"""Launcher backends: the side-effect boundary of the state machine (see state.Backend).

LocalBackend (P2) talks to the LAN and to moonlight-qt directly. When couch-daemon is installed (P3) the launcher
uses DaemonBackend instead, which asks the daemon on 127.0.0.1 for the same operations. Both keep every call
non-blocking: probes run in a background thread and calls return the latest cached answer."""
from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import threading
import time

from .. import net
from .config import Config

MOONLIGHT = os.environ.get("COUCH_MOONLIGHT", "/opt/couch/bin/moonlight-qt")
STATE_DIR = os.environ.get("COUCH_STATE_DIR", "/var/lib/couch")
LOG_DIR = os.environ.get("COUCH_LOG_DIR", "/var/log/couch")
NOT_PAIRED_MARKERS = ("not paired", "isn't paired", "is not paired", "pair with", "unpaired")


class Child:
    """A supervised child process in its own session; stop() always reaps it (no zombies)."""

    def __init__(self, argv: list[str], log_name: str):
        self.argv = argv
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
        if self.proc.poll() is None:
            for sig, wait in ((signal.SIGTERM, timeout), (signal.SIGKILL, 2.0)):
                try:
                    os.killpg(self.proc.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    self.proc.wait(wait)
                    break
                except subprocess.TimeoutExpired:
                    continue
        try:
            self.proc.wait(0.5)
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


def record_stream(app: str, started: float, code: int | None, path: str | None = None) -> None:
    """Append to the stream history couch-doctor shows (last streams, durations, exit codes)."""
    path = path or os.path.join(STATE_DIR, "streams.jsonl")
    entry = {"app": app, "start": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() -
                                                                                     (time.monotonic() - started))),
             "duration_s": round(time.monotonic() - started, 1), "exit_code": code}
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


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
        res, fps, kbps = self.cfg.stream_params()
        argv = [self.moonlight, "stream", self.cfg.host, app, "--quit-after", "--display-mode", "fullscreen",
                "--resolution", res, "--fps", str(fps), "--bitrate", str(kbps), "--video-decoder", "hardware"]
        if self.cfg.codec != "auto":
            argv += ["--video-codec", self.cfg.codec]
        return argv

    def start_stream(self, app: str) -> None:
        self.stop_stream()
        self.stream_app = app
        self.stream = Child(self.stream_argv(app), "moonlight.log")

    def stream_status(self) -> tuple[str, int | None]:
        if self.stream is None:
            return ("none", None)
        code = self.stream.poll()
        if code is None:
            return ("running", None)
        tail = self.stream.tail().lower()
        child, self.stream = self.stream, None
        child.stop()
        record_stream(self.stream_app, child.started, code)
        if code != 0 and any(m in tail for m in NOT_PAIRED_MARKERS):
            from .state import NOT_PAIRED_EXIT
            code = NOT_PAIRED_EXIT
        return ("exited", code)

    def stop_stream(self) -> bool:
        if self.stream is None:
            return True
        child, self.stream = self.stream, None
        ok = child.stop()
        record_stream(self.stream_app, child.started, child.poll())
        return ok

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
