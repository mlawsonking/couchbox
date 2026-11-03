"""DaemonBackend: the launcher's backend when couch-daemon is running (P3). The daemon is the allow-list gate (WoL
only to the configured MAC, stream only for listed apps, host discovery by name/ARP/cache); the launcher still runs
the moonlight-qt child itself so it stays inside the cage session."""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import threading
import time

from .backend import Child, LocalBackend
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


class DaemonBackend(LocalBackend):
    def __init__(self, cfg: Config, caller=call, poller: StatusPoller | None = None, moonlight=None,
                 state_dir: str | None = None):
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
        self.stream = Child([str(a) for a in body["argv"]], "moonlight.log")
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
