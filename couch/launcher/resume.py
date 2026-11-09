"""A Sunshine session this box started and lost track of: offer to resume it for a while, then end it once.

How a session gets stranded: the launcher or X server dies under a stream (a kiosk restart, a crash), so nothing ever
sends /cancel and the app keeps running on the game PC (NUC, 2026-11-09: Steam Big Picture ran for hours until the next
stream resumed it). Resuming is valuable (the game is still there), leaving it forever is not.

Policy (Michael, 2026-11-09): at launcher start and whenever the launcher returns to its tiles, if Sunshine reports a
running session that this box started and no stream of ours is running, show that app's tile with a "Resume" badge for
`[stream] resume_window_min` minutes (default 15; 0 = end at once). A tile press in the window resumes it as a normal
stream. When the window passes with no stream started, end it ONCE with `moonlight-qt quit` and log it
(streams.jsonl outcome "stranded-ended"). Never while a stream of ours is running or starting.

"That this box started": the session's app id (Sunshine's <currentgame>) must be the id of the app of our last stream
(ids are learned from moonlight-qt's own launch line, see backend). A session started by anything else - a phone
running Moonlight, someone at the PC - is never touched.

Everything here is pure (time is an argument) except SessionWatcher, the thread that asks Sunshine without blocking
the UI."""
from __future__ import annotations

import json
import os
import threading
import time

CONFIRM_S = 5.0            # a session must be seen twice this far apart: a /cancel still finishing is not "stranded"


class Resume:
    def __init__(self, window_s: float = 15 * 60.0):
        self.window_s = window_s
        self.game: int | None = None
        self.app: str | None = None
        self.first = 0.0
        self.confirmed = False
        self.ended: set[int] = set()

    def reset(self) -> None:
        self.game, self.app, self.confirmed = None, None, False

    def observe(self, game: int | None, app: str | None, now: float) -> None:
        """`game` = Sunshine's current app id (0/None = no session); `app` = our app name for it, None when the session
        is not one of ours (then it is ignored). Called about once a second with the latest answer."""
        if not game or not app:
            self.reset()
            return
        if game != self.game:
            self.game, self.app, self.first, self.confirmed = game, app, now, False
        elif not self.confirmed and now - self.first >= CONFIRM_S:
            self.confirmed = True

    def badge(self, now: float) -> str | None:
        """The app whose tile shows "Resume": a confirmed stranded session, window still open."""
        if self.confirmed and self.game not in self.ended and self.window_s > 0 and now - self.first < self.window_s:
            return self.app
        return None

    def due(self, now: float, idle: bool) -> bool:
        """True when the stranded session must be ended now: confirmed, we are idle (no stream running or starting)
        and the window has passed (immediately with window 0)."""
        return (self.confirmed and idle and self.game not in self.ended
                and (self.window_s <= 0 or now - self.first >= self.window_s))

    def age(self, now: float) -> float:
        return max(0.0, now - self.first)

    def mark_ended(self) -> None:
        if self.game is not None:
            self.ended.add(self.game)
        self.reset()


# ------------------------------------------------------------------ app ids (Sunshine's numeric ids -> our app names)
APP_IDS = "app-ids.json"


def load_ids(state_dir: str) -> dict[str, str]:
    try:
        with open(os.path.join(state_dir, APP_IDS), encoding="utf-8") as fh:
            d = json.load(fh)
        return {str(k): v for k, v in d.items() if isinstance(v, str)} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def learn_id(state_dir: str, game_id: str, app: str) -> bool:
    """Remember that Sunshine's app id `game_id` is our app `app`. True when the map changed."""
    ids = load_ids(state_dir)
    if ids.get(game_id) == app:
        return False
    ids[game_id] = app
    path = os.path.join(state_dir, APP_IDS)
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(ids, fh)
        os.replace(path + ".tmp", path)
    except OSError:
        pass
    return True


def last_stream_app(state_dir: str) -> str | None:
    """The app of the newest streams.jsonl entry (a stream that ended, or a crashed one recorded at the next start)."""
    try:
        with open(os.path.join(state_dir, "streams.jsonl"), "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 65536))
            lines = [ln for ln in fh.read().decode("utf-8", "replace").splitlines() if ln.strip()]
    except OSError:
        return None
    for ln in reversed(lines):
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if e.get("outcome") == "stranded-ended":      # bookkeeping, not a stream of ours
            continue
        return e.get("app") if isinstance(e.get("app"), str) else None
    return None


class SessionWatcher(threading.Thread):
    """Asks Sunshine for its current app id: once at start, on every kick (the launcher came back to its tiles) and,
    while something is being tracked, every `track_every` s to confirm it / notice it ended. Not while a stream of ours
    runs. The probe can take seconds (it is an HTTP GET with a timeout): it never runs on the UI thread."""

    def __init__(self, probe, active, track_every: float = 5.0):
        super().__init__(daemon=True, name="couch-session-watch")
        self._probe, self._active, self.track_every = probe, active, track_every
        self.game: int | None = None
        self.stamp = 0.0
        self._kick = threading.Event()
        self._stop_evt = threading.Event()

    def kick(self) -> None:
        self._kick.set()

    def stop(self) -> None:
        self._stop_evt.set()
        self._kick.set()

    def run(self) -> None:
        while not self._stop_evt.is_set():
            self._kick.clear()
            if self._active():
                try:
                    self.game = self._probe()
                except Exception:                # a watcher thread must never die
                    self.game = None
                self.stamp = time.monotonic()
            else:
                self.game = None
            # idle + nothing seen: sleep until the next kick; something seen: look again soon to confirm / drop it
            self._kick.wait(self.track_every if self.game else None)
