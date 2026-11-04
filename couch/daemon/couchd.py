"""couch-daemon (Python stdlib only). Runs as the unprivileged `couch` user.

Listens on 0.0.0.0:7777 (port from COUCH_DAEMON_PORT):
  * LAN clients may only GET /health (JSON) and / (tiny HTML page) - read-only.
  * Loopback clients may also use the allow-listed actions below. Anything else -> 404. No free-form commands.

    GET  /status            host reachability + discovery method + Sunshine port state (launcher polls this)
    GET  /apps              cached `moonlight-qt list <host>` (refreshed every 60 s)
    POST /wake              Wake-on-LAN to the MAC in /etc/couch/couch.toml (never any other MAC)
    POST /stream {"app": "<name>", "launch": bool}
                            only apps that `moonlight-qt list` returned; returns the exact moonlight argv for the
                            launcher. launch=true also queues the stream for the launcher (remote validation).
    POST /quit              queue "quit the stream" for the launcher
    POST /pair              queue "show the pairing PIN screen" for the launcher
    POST /restart           queue "reboot the box" for the launcher (it holds the logind seat session)
    POST /doctor            run couch-doctor now (with the network measurement) and return the report
    GET  /pending           (launcher) take the queued request, if any

Actions require the header `X-Couch: 1` so a web page can never trigger them via a browser on the box.
"""
from __future__ import annotations

import html
import ipaddress
import json
import logging
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import net, sdnotify
from ..launcher import config as C

log = logging.getLogger("couch.daemon")

PORT = int(os.environ.get("COUCH_DAEMON_PORT", "7777"))
BIND = os.environ.get("COUCH_DAEMON_BIND", "0.0.0.0")
CONFIG = os.environ.get("COUCH_CONFIG", "/etc/couch/couch.toml")
STATE_DIR = os.environ.get("COUCH_STATE_DIR", "/var/lib/couch")
MOONLIGHT = os.environ.get("COUCH_MOONLIGHT", "/opt/couch/bin/moonlight-qt")
DOCTOR = os.environ.get("COUCH_DOCTOR", "/opt/couch/bin/couch-doctor")
APPS_TTL = 60.0
PROBE_INTERVAL = 2.0
DOCTOR_INTERVAL = 300.0
MEASURE_INTERVAL = 3600.0
MAX_BODY = 4096

LAN_PATHS = {"/health", "/"}
GET_ACTIONS = {"/status", "/apps", "/pending"}
POST_ACTIONS = {"/wake", "/stream", "/quit", "/pair", "/restart", "/doctor"}


def is_loopback(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def parse_list(output: str) -> list[str]:
    """App names from `moonlight-qt list <host>`: one per line; tolerate a CSV form (first column) and headers."""
    apps = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("name,", "error", "warning", "qt.", "moonlight")):
            continue
        if "," in line and line.count(",") >= 2:
            line = line.split(",")[0].strip().strip('"')
        if line and line not in apps:
            apps.append(line)
    return apps


class Core:
    """Daemon state; every side effect is behind an injectable callable so tests run it without a LAN."""

    def __init__(self, config_path: str = CONFIG, state_dir: str = STATE_DIR, moonlight: str = MOONLIGHT,
                 doctor: str = DOCTOR, send_wol=None, discover=None, probe=None, run=None, post=None):
        self.config_path = config_path
        self.state_dir = state_dir
        self.moonlight = moonlight
        self.doctor = doctor
        self._send_wol = send_wol or net.send_wol
        self._discover = discover or net.discover
        self._probe = probe or net.tcp_probe
        self._run = run or self._default_run
        self._post = post or self._default_post
        self.lock = threading.Lock()
        self.cfg = C.load(config_path)
        self._cfg_mtime = self._mtime()
        self.status = {"host": self.cfg.host, "ip": None, "method": "none", "sunshine": None, "host_up": None,
                       "checked": None}
        self.apps: list[str] = []
        self.apps_at = 0.0
        self.apps_error: str | None = None
        self.pending: dict | None = None
        self.health: dict = {"overall": "unknown", "checks": [], "generated": None}
        self.last_overall = None
        self.wol_count = 0

    # ---------------------------------------------------------------- plumbing
    @staticmethod
    def _default_run(argv, timeout):
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return 124, f"{argv[0]} timed out after {timeout}s"
        except OSError as exc:
            return 127, str(exc)
        return p.returncode, p.stdout + p.stderr

    @staticmethod
    def _default_post(url: str, body: bytes, headers: dict) -> None:
        import urllib.request
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=10).close()

    def _mtime(self):
        try:
            return os.stat(self.config_path).st_mtime_ns
        except OSError:
            return None

    def reload_config(self) -> None:
        m = self._mtime()
        if m != self._cfg_mtime:
            self._cfg_mtime = m
            self.cfg = C.load(self.config_path)
            with self.lock:
                self.apps, self.apps_at = [], 0.0
            log.info("config reloaded (host %s)", self.cfg.host)

    # ---------------------------------------------------------------- probes
    def probe_once(self) -> dict:
        ip, method = self._discover(self.cfg.host, self.cfg.mac, os.path.join(self.state_dir, "host-cache.json"))
        sun = self._probe(ip) if ip else "unresolved"
        st = {"host": self.cfg.host, "ip": ip, "method": method, "sunshine": sun,
              "host_up": sun in ("open", "refused"), "checked": time.time()}
        with self.lock:
            self.status = st
        return st

    def stream_target(self) -> str:
        """What to hand moonlight-qt: the name when it resolves, else the IP found via ARP/cache."""
        st = self.status
        if st.get("method") == "name" or not st.get("ip"):
            return self.cfg.host
        return st["ip"]

    def refresh_apps(self, force: bool = False) -> list[str]:
        if not force and self.apps and time.monotonic() - self.apps_at < APPS_TTL:
            return self.apps
        rc, out = self._run([self.moonlight, "list", self.stream_target()], 30)
        with self.lock:
            if rc == 0:
                self.apps, self.apps_at, self.apps_error = parse_list(out), time.monotonic(), None
            else:
                self.apps_error = f"moonlight-qt list exit {rc}: {out.strip()[-200:]}"
        return self.apps

    # ---------------------------------------------------------------- actions
    def wake(self) -> dict:
        if not self.cfg.mac:
            return {"ok": False, "error": "no MAC configured in couch.toml"}
        self._send_wol(self.cfg.mac)        # only ever the configured MAC
        self.wol_count += 1
        return {"ok": True, "sent": True}

    def stream(self, app: str, launch: bool) -> tuple[int, dict]:
        if not isinstance(app, str) or not app:
            return 400, {"ok": False, "error": "app required"}
        apps = self.refresh_apps()
        if app not in apps:
            apps = self.refresh_apps(force=True)
        if app not in apps:
            return 403, {"ok": False, "error": "app is not in moonlight-qt list", "apps": apps,
                         "list_error": self.apps_error}
        argv = self.cfg.stream_argv(self.moonlight, self.stream_target(), app, self.measured_rung())
        if launch:
            self.queue({"action": "stream", "app": app})
        return 200, {"ok": True, "argv": argv}

    def measured_rung(self) -> str | None:
        try:
            with open(os.path.join(self.state_dir, "link.json"), encoding="utf-8") as fh:
                return json.load(fh).get("rung")
        except (OSError, ValueError):
            return None

    def queue(self, item: dict) -> None:
        with self.lock:
            self.pending = dict(item, at=time.time())

    def take_pending(self) -> dict | None:
        with self.lock:
            p, self.pending = self.pending, None
        if p and time.time() - p["at"] > 30:     # stale requests are dropped, never executed late
            return None
        return p

    def run_doctor(self, measure: bool = False) -> dict:
        argv = [self.doctor, "--json"] + (["--measure"] if measure else [])
        rc, out = self._run(argv, 120)
        try:
            report = json.loads(out[out.index("{"):])
        except ValueError:
            report = {"overall": "red", "checks": [{"check": "doctor", "status": "red",
                                                     "summary": f"couch-doctor failed (rc {rc})"}]}
        report["generated"] = time.time()
        self.health = report
        self._write_health(report)
        self._maybe_alert(report)
        return report

    def _write_health(self, report: dict) -> None:
        link = next((c for c in report.get("checks", []) if c.get("check") == "network"), {})
        slim = {"overall": report.get("overall"), "generated": report.get("generated"),
                "link_bad": bool(link.get("detail", {}).get("link_bad"))}
        path = os.path.join(self.state_dir, "health.json")
        try:
            with open(path + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(slim, fh)
            os.replace(path + ".tmp", path)
        except OSError:
            pass

    def _maybe_alert(self, report: dict) -> None:
        overall = report.get("overall")
        url = self.cfg.ntfy_url
        if overall == "red" and self.last_overall != "red" and url:
            bad = [f"{c['check']}: {c['summary']}" for c in report.get("checks", []) if c.get("status") == "red"]
            try:
                self._post(url, ("couchbox is red\n" + "\n".join(bad)).encode()[:3000],
                           {"Title": "couchbox needs a check", "Priority": "default", "Tags": "tv"})
            except Exception as exc:      # alerting must never take the daemon down
                log.warning("ntfy POST failed: %s", exc)
        self.last_overall = overall

    # ---------------------------------------------------------------- loops
    def loops(self, stop: threading.Event) -> None:
        next_doctor, next_measure = time.monotonic() + 20, time.monotonic() + 120
        while not stop.is_set():
            try:
                self.reload_config()
                self.probe_once()
                now = time.monotonic()
                if now >= next_measure and not self.streaming():
                    next_measure = now + MEASURE_INTERVAL
                    next_doctor = now + DOCTOR_INTERVAL
                    self.run_doctor(measure=True)
                elif now >= next_doctor:
                    next_doctor = now + DOCTOR_INTERVAL
                    self.run_doctor()
            except Exception:
                log.exception("background loop error")
            stop.wait(PROBE_INTERVAL)

    def streaming(self) -> bool:
        return os.path.exists(os.path.join(self.state_dir, "streaming"))


# -------------------------------------------------------------------- HTTP
def make_handler(core: Core):
    class Handler(BaseHTTPRequestHandler):
        server_version = "couch-daemon"
        sys_version = ""

        def log_message(self, fmt, *args):
            log.info("%s %s", self.client_address[0], fmt % args)

        def _send(self, code: int, body, ctype: str = "application/json") -> None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _local(self) -> bool:
            return is_loopback(self.client_address[0])

        def _path(self) -> str:
            return self.path.split("?", 1)[0]

        def do_GET(self):
            p = self._path()
            if p == "/health":
                return self._send(200, core.health)
            if p == "/":
                return self._send(200, render_html(core.health).encode(), "text/html; charset=utf-8")
            if p in GET_ACTIONS and self._local():
                if p == "/status":
                    return self._send(200, dict(core.status, target=core.stream_target()))
                if p == "/apps":
                    return self._send(200, {"apps": core.refresh_apps(), "error": core.apps_error})
                if p == "/pending":
                    return self._send(200, {"pending": core.take_pending()})
            return self._send(404, {"error": "not found"})

        do_HEAD = do_GET

        def do_POST(self):
            p = self._path()
            if p not in POST_ACTIONS or not self._local():
                return self._send(404, {"error": "not found"})
            if self.headers.get("X-Couch") != "1":
                return self._send(403, {"error": "missing X-Couch header"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                return self._send(413, {"error": "body too large"})
            body = {}
            if n:
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError
                except ValueError:
                    return self._send(400, {"error": "body must be a JSON object"})
            if p == "/wake":
                r = core.wake()
                return self._send(200 if r["ok"] else 409, r)
            if p == "/stream":
                code, r = core.stream(body.get("app"), bool(body.get("launch")))
                return self._send(code, r)
            if p in ("/quit", "/pair", "/restart"):
                core.queue({"action": p[1:]})
                return self._send(202, {"ok": True, "queued": p[1:]})
            if p == "/doctor":
                return self._send(200, core.run_doctor(measure=bool(body.get("measure", True))))
            return self._send(404, {"error": "not found"})

        def do_PUT(self):
            self._send(404, {"error": "not found"})

        do_DELETE = do_PATCH = do_OPTIONS = do_PUT

    return Handler


def render_html(health: dict) -> str:
    rows = "".join(
        f"<tr><td>{html.escape(str(c.get('check')))}</td><td class={html.escape(str(c.get('status')))}>"
        f"{html.escape(str(c.get('status')))}</td><td>{html.escape(str(c.get('summary')))}</td></tr>"
        for c in health.get("checks", []))
    gen = health.get("generated")
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(gen)) if gen else "not yet"
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            "<title>couchbox health</title><style>body{font:16px sans-serif;margin:1em}td{padding:4px 8px}"
            ".green{color:#007a55}.yellow{color:#a86b00}.red{color:#b23c00;font-weight:bold}</style>"
            f"<h1>couchbox: {html.escape(str(health.get('overall')))}</h1><p>checked {when}"
            f" &middot; <a href=/health>JSON</a></p><table>{rows}</table>")


def serve(core: Core, bind: str = BIND, port: int = PORT) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((bind, port), make_handler(core))
    srv.daemon_threads = True
    return srv


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(levelname)s %(message)s")
    core = Core()
    srv = serve(core)
    stop = threading.Event()
    t_http = threading.Thread(target=srv.serve_forever, name="http", daemon=True)
    t_loop = threading.Thread(target=core.loops, args=(stop,), name="loops", daemon=True)
    t_http.start()
    t_loop.start()
    sdnotify.notify("READY=1\nSTATUS=serving on :%d" % PORT)
    interval = sdnotify.watchdog_interval() or 5.0
    try:
        while True:
            time.sleep(interval)
            # Only pet the watchdog while both threads are alive: a wedged daemon gets restarted by systemd.
            if t_http.is_alive() and t_loop.is_alive():
                sdnotify.notify("WATCHDOG=1")
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        srv.shutdown()
    return 0
