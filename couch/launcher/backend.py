"""Launcher backends: the side-effect boundary of the state machine (see state.Backend).

LocalBackend (P2) talks to the LAN and to moonlight-qt directly. When couch-daemon is installed (P3) the launcher
uses DaemonBackend instead, which asks the daemon on 127.0.0.1 for the same operations. Both keep every call
non-blocking: probes run in a background thread and calls return the latest cached answer."""
from __future__ import annotations

import http.client
import json
import logging
import os
import re
import secrets
import signal
import subprocess
import threading
import time

from .. import display, net
from . import logwatch
from . import resume as resume_mod
from .config import Config

log = logging.getLogger("couch.launcher.backend")

MOONLIGHT = os.environ.get("COUCH_MOONLIGHT", "/opt/couch/bin/moonlight-qt")
STATE_DIR = os.environ.get("COUCH_STATE_DIR", "/var/lib/couch")
LOG_DIR = os.environ.get("COUCH_LOG_DIR", "/var/log/couch")
NOT_PAIRED_MARKERS = ("not paired", "isn't paired", "is not paired", "pair with", "unpaired")

# Stopping a stream. moonlight-qt's own quit path IS SIGTERM (SDL turns it into "Quit event received"): it stops the
# input/audio/video/control streams within the second and then sends /cancel to Sunshine, and Sunshine answers that
# only after it has torn the session down on the PC (about 3 s on BOBBO: app exit + the display restore). The first
# build waited 3 s, so SIGKILL landed on a healthy moonlight-qt mid-/cancel (exit -9 in streams.jsonl, 2026-11-09).
# Hence: SIGTERM with a grace that covers the host's teardown, SIGKILL only as the last resort, and - because a killed
# client cannot tell Sunshine - `moonlight-qt quit <host>` after any kill. Each step is bounded, so a child that
# ignores everything is still gone (reaped) within STOP_BOUND seconds, quit included.
TERM_GRACE = 7.0                 # SIGTERM -> SIGKILL
KILL_WAIT = 2.0                  # SIGKILL -> reaped
REAP_WAIT = 0.5                  # final wait() once the group has been signalled
SUNSHINE_QUIT_TIMEOUT = 5.0      # `moonlight-qt quit <host>` after a forced kill
# Sunshine must not be left with a session. moonlight-qt only sends /cancel on a clean teardown of a stream that ran: a
# stream that failed to start ("No video received from host") exits 0 on SIGTERM without it (NUC, 2026-11-09: Desktop
# kept running on the PC after a quit that reported success). So after every user-requested stop the launcher asks
# Sunshine itself (GET /serverinfo, <currentgame>, the same unauthenticated port the daemon probes) and, if a session
# is still there after SETTLE seconds, ends it with `moonlight-qt quit`. Unreachable / unreadable = nothing to do.
SUNSHINE_PORT = 47989
PROBE_TIMEOUT = 2.0              # one GET /serverinfo
SETTLE = 3.0                     # a /cancel in progress on the PC gets this long to finish before we quit it ourselves
SESSION_CHECK = SETTLE + PROBE_TIMEOUT       # worst case of one settle loop (its last probe starts at the deadline)
STOP_BOUND = (TERM_GRACE + KILL_WAIT + REAP_WAIT + SESSION_CHECK + SUNSHINE_QUIT_TIMEOUT + KILL_WAIT + REAP_WAIT
              + PROBE_TIMEOUT)    # 24 s worst case: every stage taking its full time


class Child:
    """A supervised child process in its own session; stop() always reaps it (no zombies)."""

    def __init__(self, argv: list[str], log_name: str):
        self.argv = argv
        self.killed = False          # stop() had to SIGKILL it
        self.started = time.monotonic()
        self.log_path = os.path.join(LOG_DIR, log_name)
        try:
            self._log = open(self.log_path, "ab", buffering=0)
        except OSError:
            self._log = subprocess.DEVNULL
        self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=self._log, stderr=self._log,
                                     start_new_session=True)

    def poll(self) -> int | None:
        return self.proc.poll()

    def stop(self, timeout: float = 3.0) -> bool:
        """SIGTERM the group, wait `timeout`, then SIGKILL it (KILL_WAIT), then reap. Always reaps (no zombies).
        `killed` says whether SIGKILL was needed (the caller then owes Sunshine a `quit`)."""
        if self.proc.poll() is None:
            for sig, wait in ((signal.SIGTERM, timeout), (signal.SIGKILL, KILL_WAIT)):
                try:
                    os.killpg(self.proc.pid, sig)
                except ProcessLookupError:
                    break
                if sig == signal.SIGKILL:
                    self.killed = True
                try:
                    self.proc.wait(wait)
                    break
                except subprocess.TimeoutExpired:
                    continue
        try:
            self.proc.wait(REAP_WAIT)
        except subprocess.TimeoutExpired:
            return False
        if self._log not in (None, subprocess.DEVNULL):
            self._log.close()
        return True

    def tail(self, n: int = 4000) -> str:
        try:
            with open(self.log_path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - n))
                return fh.read().decode("utf-8", "replace")
        except OSError:
            return ""


INFLIGHT = "stream-inflight.json"
KIOSK_CRASH = "kiosk-crash"      # streams.jsonl outcome when the launcher itself died under a stream


def _append_stream(entry: dict, path: str) -> None:
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def record_stream(app: str, started: float, code: int | None, path: str | None = None,
                  extra: dict | None = None) -> None:
    """Append to the stream history couch-doctor shows (last streams, durations, exit codes)."""
    path = path or os.path.join(STATE_DIR, "streams.jsonl")
    entry = {"app": app, "start": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() -
                                                                                     (time.monotonic() - started))),
             "duration_s": round(time.monotonic() - started, 1), "exit_code": code}
    entry.update(extra or {})
    _append_stream(entry, path)
    try:
        os.unlink(os.path.join(os.path.dirname(path), INFLIGHT))
    except OSError:
        pass


def mark_inflight(app: str, state_dir: str | None = None, log_dir: str | None = None) -> None:
    """Written the moment a moonlight-qt child is spawned and removed when its exit is recorded. If it is still
    there when the launcher next starts, the launcher (and with it the stream) died: see finalize_crashed_stream."""
    state_dir, log_dir = state_dir or STATE_DIR, log_dir or LOG_DIR
    ml = os.path.join(log_dir, "moonlight.log")
    try:
        offset = os.path.getsize(ml)
    except OSError:
        offset = 0
    try:
        with open(os.path.join(state_dir, INFLIGHT), "w", encoding="utf-8") as fh:
            json.dump({"app": app, "start_epoch": time.time(), "moonlight_log_offset": offset}, fh)
    except OSError:
        pass


def finalize_crashed_stream(state_dir: str | None = None, log_dir: str | None = None) -> dict | None:
    """Launcher start-up: turn a leftover in-flight marker into a streams.jsonl entry (outcome kiosk-crash, with the
    moonlight.log lines written during that stream) and clear a stale `streaming` flag, so couch-doctor and the
    daemon see what happened instead of "no streams yet" / "a stream is running". The 2026-11-05/08 crash class:
    the compositor segfaulted when moonlight's window appeared and took the launcher with it, so nothing was ever
    recorded. Returns the entry, or None when the last launcher exit was clean."""
    state_dir, log_dir = state_dir or STATE_DIR, log_dir or LOG_DIR
    flag = os.path.join(state_dir, "streaming")
    marker = os.path.join(state_dir, INFLIGHT)
    try:
        os.unlink(flag)
    except OSError:
        pass
    try:
        with open(marker, encoding="utf-8") as fh:
            m = json.load(fh)
    except (OSError, ValueError):
        return None
    try:
        os.unlink(marker)
    except OSError:
        pass
    start = float(m.get("start_epoch") or time.time())
    tail: list[str] = []
    try:
        with open(os.path.join(log_dir, "moonlight.log"), "rb") as fh:
            fh.seek(int(m.get("moonlight_log_offset") or 0))
            tail = [ln for ln in fh.read().decode("utf-8", "replace").splitlines() if ln.strip()][-12:]
    except (OSError, ValueError):
        pass
    entry = {"app": m.get("app", ""), "start": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(start)),
             "duration_s": round(max(0.0, time.time() - start), 1), "exit_code": None, "outcome": KIOSK_CRASH,
             "moonlight_tail": tail}
    _append_stream(entry, os.path.join(state_dir, "streams.jsonl"))
    return entry


class Prober(threading.Thread):
    """Every `interval` s: resolve the host name and probe Sunshine's port. Results are read without blocking."""

    def __init__(self, host: str, interval: float = 2.0, resolve=net.resolve4, probe=net.tcp_probe):
        super().__init__(daemon=True, name="couch-prober")
        self.host, self.interval, self._resolve, self._probe = host, interval, resolve, probe
        self.ip: str | None = None
        self.result: str | None = None      # open | refused | timeout | unresolved
        self._stop = threading.Event()
        self._kick = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            ip = self._resolve(self.host) or self.ip      # keep the last known IP if the name lookup blips
            if ip:
                self.ip = ip
                self.result = self._probe(ip)
            else:
                self.result = "unresolved"
            self._kick.wait(self.interval)
            self._kick.clear()

    def kick(self) -> None:
        self._kick.set()

    def stop(self) -> None:
        self._stop.set()
        self._kick.set()


class LocalBackend:
    # class-level defaults: DaemonBackend subclasses this with its own __init__
    stream_target = ""                           # the host name moonlight-qt was given (for `quit` after a kill)
    _display_prev: display.Mode | None = None   # what to put the X screen back to when the stream ends
    _scanner: logwatch.LogScanner | None = None  # moonlight.log from this stream's start (fatal messages, app id)
    _learned = False

    def __init__(self, cfg: Config, prober: Prober | None = None, moonlight: str = MOONLIGHT):
        self.cfg = cfg
        self.moonlight = moonlight
        self.prober = prober or Prober(cfg.host)
        if not self.prober.is_alive():
            self.prober.start()
        self.stream: Child | None = None
        self.stream_app = ""
        self.pairing: Child | None = None

    # -- probes
    def host_up(self) -> bool | None:
        r = self.prober.result
        if r is None:
            return None
        return r in ("open", "refused")

    def sunshine_up(self) -> bool | None:
        r = self.prober.result
        return None if r is None else r == "open"

    def wake(self) -> None:
        if self.cfg.mac:
            try:
                net.send_wol(self.cfg.mac)
            except (OSError, ValueError):
                pass
        self.prober.kick()

    # -- streaming
    def stream_argv(self, app: str) -> list[str]:
        return self.cfg.stream_argv(self.moonlight, self.cfg.host, app)

    def spawn_stream(self, argv: list[str]) -> Child:
        """The one place a moonlight-qt stream child is created (LocalBackend and DaemonBackend). The X screen is put
        on the stream's mode first (no window manager: the stream window is only as big as the stream, see
        couch.display) and restored when the stream ends."""
        mark_inflight(self.stream_app)
        ml_log = os.path.join(LOG_DIR, "moonlight.log")
        self._scanner, self._learned = logwatch.LogScanner(ml_log, logwatch.size(ml_log)), False
        self.stream_target = argv[argv.index("stream") + 1] if "stream" in argv[:-1] else self.cfg.host
        self._fit_display(argv)
        try:
            return Child(argv, "moonlight.log")
        except BaseException:
            self._restore_display()
            raise

    def _fit_display(self, argv: list[str]) -> None:
        def opt(name: str) -> str:
            return argv[argv.index(name) + 1] if name in argv[:-1] else ""
        m = re.match(r"^(\d+)x(\d+)$", opt("--resolution"))
        if not m:
            return
        fps = int(opt("--fps")) if opt("--fps").isdigit() else 60
        self._display_prev, note = display.ensure(int(m.group(1)), int(m.group(2)), fps)
        log.info("stream display: %s", note)

    def _restore_display(self) -> None:
        prev, self._display_prev = self._display_prev, None
        if prev is not None:
            log.info("stream display restored: %s", display.restore(prev))

    def start_stream(self, app: str) -> None:
        self.stop_stream()
        self.stream_app = app
        self.stream = self.spawn_stream(self.stream_argv(app))

    def stream_status(self) -> tuple[str, int | None]:
        if self.stream is None:
            return ("none", None)
        code = self.stream.poll()
        if code is None:
            return ("running", None)
        tail = self.stream.tail().lower()
        child, self.stream = self.stream, None
        child.stop()
        self._learn_app_id()
        record_stream(self.stream_app, child.started, code)
        self._restore_display()
        if code != 0 and any(m in tail for m in NOT_PAIRED_MARKERS):
            from .state import NOT_PAIRED_EXIT
            code = NOT_PAIRED_EXIT
        return ("exited", code)

    def stop_stream(self) -> bool:
        """Quit the stream through moonlight-qt's own quit path (SIGTERM: it ends the stream and /cancels the Sunshine
        session itself), SIGKILL only if it does not finish within TERM_GRACE, and after a kill end the Sunshine
        session ourselves. Reaped within STOP_BOUND seconds whatever the child does."""
        if self.stream is None:
            return True
        child, self.stream = self.stream, None
        ok = child.stop(TERM_GRACE)
        extra: dict = {}
        self._learn_app_id()
        if self._scanner is not None and self._scanner.failure:
            extra["failure"] = self._scanner.failure              # a failed start: why (see stream_failure)
        target = self.stream_target or self.cfg.host
        if child.killed:
            extra["killed"] = True
            extra["sunshine_quit"] = self.sunshine_quit(target)
        else:
            left = self.sunshine_game(settle=SETTLE)            # a clean teardown leaves 0 at once: one probe
            if left:
                extra["sunshine_leftover"] = left
                extra["sunshine_quit"] = self.sunshine_quit(target)
        if "sunshine_quit" in extra:
            extra["sunshine_game_after"] = self.sunshine_game()     # verify by effect: None = could not be read
        record_stream(self.stream_app, child.started, child.poll(), extra=extra)
        self._restore_display()
        return ok

    def host_ip(self) -> str | None:
        return getattr(self.prober, "ip", None)

    # -- failed starts and stranded sessions
    def _learn_app_id(self) -> None:
        """Sunshine's numeric id for the app being streamed, from moonlight-qt's own launch line: how a later
        <currentgame> is recognised as one of ours (resume.py)."""
        sc = self._scanner
        if sc is None or self._learned:
            return
        sc.poll()
        if sc.app_id and self.stream_app:
            self._learned = True
            resume_mod.learn_id(STATE_DIR, sc.app_id, self.stream_app)

    def stream_failure(self) -> str | None:
        """While a stream is starting: moonlight-qt's fatal connection message from its log, or None. The caller
        (state.Machine) only asks inside state.FAIL_WATCH."""
        if self.stream is None or self.stream.poll() is not None or self._scanner is None:
            return None
        self._learn_app_id()
        self._scanner.poll()
        return self._scanner.failure

    def owned_game_app(self, game: int | None) -> str | None:
        """Our app name for Sunshine's current app id when the session is one this box started (the id belongs to the
        app of our last stream), else None: a phone's Moonlight, or anything unknown, is never ours to end."""
        if not game:
            return None
        name = resume_mod.load_ids(STATE_DIR).get(str(game))
        return name if name and name == resume_mod.last_stream_app(STATE_DIR) else None

    def end_stranded(self, app: str, age_s: float) -> str:
        """End a stranded session once (`moonlight-qt quit`), verify, and record it in streams.jsonl. Synchronous and
        bounded (SUNSHINE_QUIT_TIMEOUT + PROBE_TIMEOUT): only called when no stream of ours is running or starting."""
        target = self.cfg.host
        result = self.sunshine_quit(target)
        after = self.sunshine_game()
        entry = {"app": app, "start": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - age_s)),
                 "duration_s": round(age_s, 1), "exit_code": None, "outcome": "stranded-ended",
                 "age_s": round(age_s, 1), "sunshine_quit": result, "sunshine_game_after": after}
        _append_stream(entry, os.path.join(STATE_DIR, "streams.jsonl"))
        log.info("ended the stranded Sunshine session of %r (seen %.0f s ago): %s, session after: %s",
                 app, age_s, result, after)
        return result

    def sunshine_game(self, settle: float = 0.0) -> int | None:
        """Sunshine's <currentgame> (0 = no session) from GET /serverinfo on the game PC, waiting up to `settle`
        seconds for a non-zero value to drop to 0. None = the PC could not be asked (no IP yet, unreachable, odd reply):
        the caller then does nothing. Bounded by settle + PROBE_TIMEOUT."""
        ip = self.host_ip()
        if not ip:
            return None
        deadline = time.monotonic() + settle
        while True:
            game = None
            try:
                c = http.client.HTTPConnection(ip, SUNSHINE_PORT, timeout=PROBE_TIMEOUT)
                try:
                    c.request("GET", "/serverinfo")
                    m = re.search(r"<currentgame>(\d+)</currentgame>", c.getresponse().read().decode("utf-8", "replace"))
                finally:
                    c.close()
                game = int(m.group(1)) if m else None
            except (OSError, http.client.HTTPException):
                game = None
            if not game or time.monotonic() >= deadline:
                return game
            time.sleep(0.25)

    def sunshine_quit(self, target: str) -> str:
        """`moonlight-qt quit <host>`: ends the running app (the session) on Sunshine. Used when moonlight-qt had to
        be killed, or exited without sending /cancel itself. Bounded; "ok", "rc=N", "timeout" or "error: ..."."""
        try:
            q = Child([self.moonlight, "quit", target], "moonlight.log")
        except OSError as exc:
            log.warning("moonlight-qt quit %s could not start: %s", target, exc)
            return f"error: {exc}"
        try:
            rc = q.proc.wait(SUNSHINE_QUIT_TIMEOUT)
            result = "ok" if rc == 0 else f"rc={rc}"
        except subprocess.TimeoutExpired:
            result = "timeout"
        q.stop(0.0)
        log.info("moonlight-qt quit %s (the stream left a Sunshine session): %s", target, result)
        return result

    # -- pairing
    def pair_start(self) -> str:
        pin = f"{secrets.randbelow(10000):04d}"
        self.pair_cancel()
        self.pairing = Child([self.moonlight, "pair", self.cfg.host, "--pin", pin], "moonlight-pair.log")
        return pin

    def pair_status(self) -> tuple[str, str]:
        if self.pairing is None:
            return ("failed", "pairing was not started")
        code = self.pairing.poll()
        if code is None:
            return ("waiting", "")
        detail = self.pairing.tail(400).strip().splitlines()[-1:] or [f"exit code {code}"]
        self.pairing.stop()
        self.pairing = None
        return ("paired", "") if code == 0 else ("failed", detail[0])

    def pair_cancel(self) -> None:
        if self.pairing is not None:
            self.pairing.stop()
            self.pairing = None

    # -- box
    def restart_box(self) -> None:
        """Without the daemon (P3) the launcher cannot reboot; exiting makes systemd restart the kiosk."""
        raise SystemExit(0)

    def close(self) -> None:
        self.stop_stream()
        self.pair_cancel()
        self.prober.stop()
