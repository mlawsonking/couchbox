"""The launcher's state machine. Pure logic: time comes from `now` arguments and every side effect goes through a
small Backend interface, so tests drive every transition and timeout without SDL, sockets or processes.

    IDLE -> WAKING -> WAIT_SUNSHINE -> STREAMING -> IDLE        (normal; quit in the game returns to IDLE)
                                                 -> LOST         (stream dropped / 3 failed launches / timeouts)
    IDLE -> PAIRING -> IDLE | LOST
    LOST -> (Retry) WAKING ... | (Back) IDLE

Every non-IDLE state has a timeout. Backend calls must be fast and non-blocking (implementations probe in a
background thread and return the latest cached answer).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

IDLE, WAKING, WAIT_SUNSHINE, STREAMING, LOST, PAIRING = (
    "IDLE", "WAKING", "WAIT_SUNSHINE", "STREAMING", "LOST", "PAIRING")
STATES = (IDLE, WAKING, WAIT_SUNSHINE, STREAMING, LOST, PAIRING)

# Timeouts / cadences in seconds (acceptance: asleep -> stream <= 90 s; up -> stream <= 10 s).
WAKE_TIMEOUT = 120.0
WOL_RESEND = 10.0
SUNSHINE_TIMEOUT = 60.0
LAUNCH_GRACE = 15.0          # a moonlight exit != 0 within this window counts as a failed launch (strike)
MAX_STRIKES = 3
RETRY_PAUSE = 2.0            # between failed launches
FAIL_WATCH = 25.0            # seconds after the child starts in which moonlight.log is watched for fatal connection
                             # messages (the "no video" timeout fires ~17 s in); never matched after that - an
                             # established stream logs RFI/invalidate-frame lines all the time, and they are normal
WATCH_EVERY = 0.5            # how often the log is looked at inside that window
HOST_GONE_GRACE = 30.0       # host unreachable this long while streaming -> stop + LOST (moonlight hung)
PAIR_TIMEOUT = 180.0
# stop_stream's bound is backend.STOP_BOUND (SIGTERM grace + SIGKILL + reap + Sunshine quit); a normal quit ~3-4 s.

# Failure reasons -> (kid words, parent detail template). Also rendered into docs/FAILURE-SCREENS.md.
REASONS = {
    "WAKE_TIMEOUT": ("The game PC is still asleep.",
                     "No answer from {host} {secs:.0f}s after Wake-on-LAN to {mac}. Check the PC is plugged in, "
                     "WoL is on (NIC + BIOS) and Fast Startup is off."),
    "SUNSHINE_TIMEOUT": ("The game PC is awake but not ready to play.",
                         "{host} answers but Sunshine did not respond within {secs:.0f}s. Is the Sunshine service "
                         "running on the PC?"),
    "STREAM_FAILED": ("The game would not start.",
                      "moonlight-qt failed {strikes} times in a row starting {app!r}. Last problem: {detail}."),
    "STREAM_LOST": ("We lost the game PC.",
                    "The stream of {app!r} stopped unexpectedly (moonlight-qt exit code {code}). Network or PC "
                    "problem."),
    "HOST_GONE": ("We lost the game PC.",
                  "{host} stopped answering for {secs:.0f}s during the stream; the stream was closed."),
    "NOT_PAIRED": ("This box needs a grown-up to connect it to the game PC.",
                   "moonlight-qt is not paired with {host}. Open the Start menu > Pair and type the PIN into "
                   "Sunshine's web page on the PC."),
    "PAIR_TIMEOUT": ("Pairing took too long.",
                     "No PIN was entered in Sunshine on {host} within {secs:.0f}s."),
    "PAIR_FAILED": ("Pairing did not work.",
                    "moonlight-qt pair {host} failed: {detail}"),
    "NO_HOST": ("This box does not know which game PC to use.",
                "/etc/couch/couch.toml: {detail}"),
}

NOT_PAIRED_EXIT = 3          # fake/real adapters map "not paired" to this exit code (see backend)


class Backend(Protocol):
    def wake(self) -> None: ...
    def host_up(self) -> bool | None: ...
    def sunshine_up(self) -> bool | None: ...
    def start_stream(self, app: str) -> None: ...
    def stream_status(self) -> tuple[str, int | None]: ...   # ("none"|"running"|"exited", exit_code)
    def stop_stream(self) -> bool: ...                        # True when the child is gone (reaped)
    def pair_start(self) -> str: ...                          # returns the PIN to show on the TV
    def pair_status(self) -> tuple[str, str]: ...             # ("waiting"|"paired"|"failed", detail)
    def pair_cancel(self) -> None: ...


@dataclass
class Failure:
    reason: str
    kid: str
    parent: str


class Machine:
    def __init__(self, backend: Backend, host: str = "gamepc.local", mac: str = "", now: float = 0.0):
        self.b = backend
        self.host = host
        self.mac = mac
        self.state = IDLE
        self.entered = now
        self.app: str | None = None
        self.strikes = 0
        self.last_wol = -1e9
        self.stream_started = 0.0
        self.host_down_since: float | None = None
        self.retry_at: float | None = None
        self.failure: Failure | None = None
        self.pin: str | None = None
        self.history: list[tuple[str, str]] = []     # (from, to) for tests / logs
        self.config_error: str | None = None
        self.last_watch = -1e9

    # -------------------------------------------------------------- helpers
    def _go(self, state: str, now: float) -> None:
        self.history.append((self.state, state))
        self.state = state
        self.entered = now

    def _fail(self, reason: str, now: float, **kw) -> None:
        kid, tpl = REASONS[reason]
        fields = {"host": self.host, "mac": self.mac or "(no MAC configured)", "app": self.app,
                  "strikes": self.strikes, "code": None, "secs": now - self.entered, "detail": ""}
        fields.update(kw)
        self.failure = Failure(reason, kid, tpl.format(**fields))
        self._go(LOST, now)

    def elapsed(self, now: float) -> float:
        return now - self.entered

    # -------------------------------------------------------------- user actions
    def launch(self, app: str, now: float) -> None:
        """A tile was pressed (or Retry on LOST)."""
        if self.state not in (IDLE, LOST):
            return
        if self.config_error and not self.mac and not self.host:
            self._fail("NO_HOST", now, detail=self.config_error)
            return
        self.app = app
        self.strikes = 0
        self.failure = None
        self.retry_at = None
        if self.b.host_up():
            self._go(WAIT_SUNSHINE, now)
        else:
            self._go(WAKING, now)
            self._send_wol(now)

    def retry(self, now: float) -> None:
        if self.state == LOST and self.app:
            self.launch(self.app, now)

    def back(self, now: float) -> None:
        """B button: cancel whatever is in progress and go home."""
        if self.state == PAIRING:
            self.b.pair_cancel()
            self._go(IDLE, now)
        elif self.state in (WAKING, WAIT_SUNSHINE, LOST):
            self.failure = None
            self._go(IDLE, now)

    def force_quit(self, now: float) -> None:
        """Guide held 3 s while streaming (only needed if moonlight's own quit chord fails)."""
        if self.state == STREAMING:
            self.b.stop_stream()
            self._go(IDLE, now)

    def pair(self, now: float) -> None:
        if self.state != IDLE:
            return
        self.pin = self.b.pair_start()
        self._go(PAIRING, now)

    # -------------------------------------------------------------- clock
    def _send_wol(self, now: float) -> None:
        self.b.wake()
        self.last_wol = now

    def tick(self, now: float) -> None:
        s = self.state
        if s == WAKING:
            if self.b.host_up():
                self._go(WAIT_SUNSHINE, now)
            elif self.elapsed(now) >= WAKE_TIMEOUT:
                self._fail("WAKE_TIMEOUT", now)
            elif now - self.last_wol >= WOL_RESEND:
                self._send_wol(now)
        elif s == WAIT_SUNSHINE:
            if self.retry_at is not None:
                if now >= self.retry_at:
                    self.retry_at = None
                    self._start(now)
            elif self.b.sunshine_up():
                self._start(now)
            elif self.elapsed(now) >= SUNSHINE_TIMEOUT:
                self._fail("SUNSHINE_TIMEOUT", now)
        elif s == STREAMING:
            self._tick_stream(now)
        elif s == PAIRING:
            st, detail = self.b.pair_status()
            if st == "paired":
                self.pin = None
                self._go(IDLE, now)
            elif st == "failed":
                self.pin = None
                self._fail("PAIR_FAILED", now, detail=detail or "unknown error")
            elif self.elapsed(now) >= PAIR_TIMEOUT:
                self.b.pair_cancel()
                self.pin = None
                self._fail("PAIR_TIMEOUT", now)

    def _start(self, now: float) -> None:
        assert self.app is not None
        self.b.start_stream(self.app)
        self.stream_started = now
        self.last_watch = -1e9
        self.host_down_since = None
        self._go(STREAMING, now)

    def _tick_stream(self, now: float) -> None:
        st, code = self.b.stream_status()
        ran = now - self.stream_started
        if st == "running":
            if ran < FAIL_WATCH and now - self.last_watch >= WATCH_EVERY and hasattr(self.b, "stream_failure"):
                self.last_watch = now
                why = self.b.stream_failure()
                if why:
                    # moonlight-qt keeps running behind an error dialog when the connection fails ("No video received
                    # from host"): treat it as a failed launch. The normal stop path also ends the Sunshine session.
                    self.b.stop_stream()
                    self._launch_failed(now, why)
                    return
            up = self.b.host_up()
            if up is False:
                if self.host_down_since is None:
                    self.host_down_since = now
                elif now - self.host_down_since >= HOST_GONE_GRACE:
                    self.b.stop_stream()
                    self._fail("HOST_GONE", now, secs=now - self.host_down_since)
            else:
                self.host_down_since = None
            return
        if st == "none":       # child vanished without a status: treat as a failed launch
            code = None
        if code == 0:
            self.strikes = 0
            self._go(IDLE, now)             # the player quit the game: back to the launcher
            return
        if code == NOT_PAIRED_EXIT:
            self._fail("NOT_PAIRED", now, code=code)
            return
        if ran >= LAUNCH_GRACE:
            self._fail("STREAM_LOST", now, code=code)
            return
        self._launch_failed(now, f"exit code {code}", code=code)

    def _launch_failed(self, now: float, detail: str, code: int | None = None) -> None:
        """A strike: retry after RETRY_PAUSE, or on the MAX_STRIKES-th the STREAM_FAILED screen with the real reason."""
        self.strikes += 1
        if self.strikes >= MAX_STRIKES:
            self._fail("STREAM_FAILED", now, code=code, detail=detail)
            return
        # Retry after a short pause, back through WAIT_SUNSHINE (which still has its own timeout).
        self._go(WAIT_SUNSHINE, now)
        self.retry_at = now + RETRY_PAUSE
