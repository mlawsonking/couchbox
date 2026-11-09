"""DaemonBackend: the launcher's backend when couch-daemon is running (P3). The daemon is the allow-list gate (WoL
only to the configured MAC, stream only for listed apps, host discovery by name/ARP/cache); the launcher still runs
the moonlight-qt child itself so it stays inside the kiosk's X session."""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import threading
import time

from .backend import LocalBackend
from .config import Config

DAEMON_PORT = int(os.environ.get("COUCH_DAEMON_PORT", "7777"))


def call(method: str, path: str, body: dict | None = None, timeout: float = 3.0) -> tuple[int, dict]:
    c = http.client.HTTPConnection("127.0.0.1", DAEMON_PORT, timeout=timeout)
    try:
        data = json.dumps(body).encode() if body is not None else None
        c.request(method, path, body=data, headers={"X-Couch": "1", "Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")
    finally:
        c.close()


def daemon_available() -> bool:
    try:
        return call("GET", "/status", timeout=1.0)[0] == 200
    except (OSError, ValueError):
        return False


class StatusPoller(threading.Thread):
    """Polls GET /status every second so host_up()/sunshine_up() never block the UI."""

    def __init__(self, interval: float = 1.0, caller=call):
        super().__init__(daemon=True, name="couch-daemon-poll")
        self.interval, self._call = interval, caller
        self.status: dict | None = None
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                code, st = self._call("GET", "/status")
                self.status = st if code == 200 else None
            except (OSError, ValueError):
                self.status = None
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()


APPS_CACHE = "apps-cache.json"


class AppsPoller(threading.Thread):
    """Keeps the list of Sunshine apps the daemon will launch (GET /apps) for the launcher's tile grid.

    The daemon only knows the list while the game PC answers `moonlight-qt list`, and forgets it on every restart
    (nightly reboot, couch-update) - but the PC is usually asleep at that moment. So the last good list is persisted in
    the state dir and loaded at start: the tiles are there on the first frame, PC asleep or not. An empty or failed
    answer never replaces a good list. The poll runs in this thread: /apps can take seconds (it runs `moonlight-qt
    list`), the UI must never wait for it."""

    def __init__(self, caller=call, cache_path: str | None = None, interval: float = 20.0, retry: float = 60.0):
        super().__init__(daemon=True, name="couch-apps-poll")
        self._call, self.cache_path, self.interval, self.retry = caller, cache_path, interval, retry
        self._stop_evt = threading.Event()
        self.apps: list[str] = self._load()

    @staticmethod
    def _clean(raw) -> list[str]:
        return [a.strip() for a in raw if isinstance(a, str) and a.strip()] if isinstance(raw, list) else []

    def _load(self) -> list[str]:
        try:
            with open(self.cache_path or "", encoding="utf-8") as fh:
                return self._clean(json.load(fh).get("apps"))
        except (OSError, ValueError, AttributeError):
            return []

    def _save(self) -> None:
        if not self.cache_path:
            return
        tmp = self.cache_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"apps": self.apps, "at": time.time()}, fh)
            os.replace(tmp, self.cache_path)
        except OSError:
            pass

    def poll_once(self) -> bool:
        """One GET /apps. True when it returned a usable (non-empty) list; the list is replaced only then."""
        try:
            code, body = self._call("GET", "/apps", timeout=45.0)
        except Exception:                     # a poller thread must never die: the tiles would freeze silently
            return False
        apps = self._clean(body.get("apps")) if code == 200 and isinstance(body, dict) else []
        if not apps:
            return False
        if apps != self.apps:
            self.apps = apps
            self._save()
        return True

    def run(self) -> None:
        while not self._stop_evt.is_set():
            self._stop_evt.wait(self.interval if self.poll_once() else self.retry)

    def stop(self) -> None:
        self._stop_evt.set()


class DaemonBackend(LocalBackend):
    def __init__(self, cfg: Config, caller=call, poller: StatusPoller | None = None, moonlight=None,
                 state_dir: str | None = None, apps_poller: AppsPoller | None = None):
        self.cfg = cfg
        self._call = caller
        self.poller = poller or StatusPoller(caller=caller)
        if not self.poller.is_alive():
            self.poller.start()
        self.moonlight = moonlight or os.environ.get("COUCH_MOONLIGHT", "/opt/couch/bin/moonlight-qt")
        self.state_dir = state_dir or os.environ.get("COUCH_STATE_DIR", "/var/lib/couch")
        self.stream: Child | None = None
        self.stream_app = ""
        self.pairing: Child | None = None
        self.last_error: str | None = None
        self.prober = self.poller            # LocalBackend.close() stops it
        self.apps_poller = apps_poller or AppsPoller(caller=caller, cache_path=os.path.join(self.state_dir, APPS_CACHE))
        if not self.apps_poller.is_alive():
            self.apps_poller.start()

    def apps(self) -> list[str]:
        """Every app the daemon will launch (its allow-list), as last listed - the launcher's tile grid."""
        return list(self.apps_poller.apps)

    def close(self) -> None:
        self.apps_poller.stop()
        super().close()

    def host_up(self) -> bool | None:
        st = self.poller.status
        return None if st is None else bool(st.get("host_up"))

    def sunshine_up(self) -> bool | None:
        st = self.poller.status
        return None if st is None else st.get("sunshine") == "open"

    def wake(self) -> None:
        try:
            self._call("POST", "/wake")
        except (OSError, ValueError):
            pass

    def start_stream(self, app: str) -> None:
        self.stop_stream()
        self.stream_app = app
        try:
            code, body = self._call("POST", "/stream", {"app": app}, timeout=40.0)
        except (OSError, ValueError) as exc:
            code, body = 0, {"error": str(exc)}
        if code != 200 or not isinstance(body.get("argv"), list):
            # Not on the allow-list (or daemon trouble): report it as a failed launch, no child is started.
            self.last_error = body.get("error", f"daemon refused ({code})")
            self.stream = None
            self._refused = True
            return
        self._refused = False
        self.stream = self.spawn_stream([str(a) for a in body["argv"]])
        self._flag(True)

    def stream_status(self) -> tuple[str, int | None]:
        if getattr(self, "_refused", False):
            self._refused = False
            return ("exited", 1)
        st = super().stream_status()
        if st[0] != "running":
            self._flag(False)
        return st

    def stop_stream(self) -> bool:
        ok = super().stop_stream()
        self._flag(False)
        return ok

    def _flag(self, on: bool) -> None:
        """/var/lib/couch/streaming tells the daemon not to run the 10 s throughput probe mid-game."""
        path = os.path.join(self.state_dir, "streaming")
        try:
            if on:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(str(time.time()))
            elif os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass

    def pending(self) -> dict | None:
        try:
            code, body = self._call("GET", "/pending")
            return body.get("pending") if code == 200 else None
        except (OSError, ValueError):
            return None

    def restart_box(self) -> None:
        """The launcher holds the active logind session on seat0, so polkit lets it reboot."""
        subprocess.run(["systemctl", "reboot"], check=False, timeout=30)
