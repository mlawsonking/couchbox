"""couch-daemon on a real ThreadingHTTPServer: allow-list, loopback-only actions, read-only LAN /health,
exact WoL bytes to the configured MAC only, stream only for apps moonlight-qt listed (mock binary records argv)."""
import json
import os
import socket
import threading
import time
import http.client

import pytest

from couch import net
from couch.daemon import couchd as D
from conftest import REPO

FAKE = os.path.join(REPO, "tests", "fixtures", "bin", "fake-moonlight-qt")
CFG = '[host]\nname = "gamepc.local"\nmac = "AA:BB:CC:DD:EE:FF"\n[stream]\ncodec = "HEVC"\n'


class WolSock:
    sent = []

    def __init__(self, *a):
        pass

    def setsockopt(self, *a):
        pass

    def sendto(self, data, addr):
        WolSock.sent.append((data, addr))

    def close(self):
        pass


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    cfg = tmp_path / "couch.toml"
    cfg.write_text(CFG)
    argv_log = tmp_path / "argv.log"
    monkeypatch.setenv("MOONLIGHT_ARGV_LOG", str(argv_log))
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "list")
    monkeypatch.setenv("FAKE_MOONLIGHT_APPS", "Desktop\nSteam Big Picture\nCeleste")
    WolSock.sent = []
    core = D.Core(config_path=str(cfg), state_dir=str(tmp_path), moonlight=FAKE,
                  doctor=os.path.join(REPO, "tests", "fixtures", "bin", "fake-doctor"),
                  send_wol=lambda mac: net.send_wol(mac, sock_factory=WolSock),
                  discover=lambda host, mac, cache: ("10.9.8.7", "name"),
                  probe=lambda ip: "open")
    srv = D.serve(core, bind="0.0.0.0", port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    core.probe_once()
    yield core, srv.server_address[1], argv_log, tmp_path
    srv.shutdown()


def req(port, method, path, body=None, headers=None, host="127.0.0.1"):
    c = http.client.HTTPConnection(host, port, timeout=10)
    h = {"X-Couch": "1"}
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw.decode()


def test_wake_sends_exact_magic_packet_to_config_mac(daemon):
    core, port, *_ = daemon
    st, body = req(port, "POST", "/wake", {"mac": "02:00:00:00:00:01"})    # a body MAC is ignored
    assert st == 200 and body["ok"]
    assert WolSock.sent == [(bytes.fromhex("ff" * 6 + "aabbccddeeff" * 16), ("255.255.255.255", 9))]


def test_wake_without_mac_refuses(daemon, tmp_path):
    core, port, *_ = daemon
    core.cfg.mac = ""
    st, body = req(port, "POST", "/wake")
    assert st == 409 and not WolSock.sent


def test_apps_cached_from_moonlight_list(daemon):
    core, port, argv_log, _ = daemon
    st, body = req(port, "GET", "/apps")
    assert st == 200 and body["apps"] == ["Desktop", "Steam Big Picture", "Celeste"]
    req(port, "GET", "/apps")
    assert argv_log.read_text().splitlines() == ["list gamepc.local"]      # second call served from cache


def test_stream_only_listed_apps(daemon):
    core, port, *_ = daemon
    st, body = req(port, "POST", "/stream", {"app": "Celeste"})
    assert st == 200
    assert body["argv"][:5] == [FAKE, "stream", "gamepc.local", "Celeste", "--quit-after"]
    assert "--video-codec" in body["argv"] and body["argv"][body["argv"].index("--bitrate") + 1] == "15000"
    st, body = req(port, "POST", "/stream", {"app": "cmd.exe /c calc"})
    assert st == 403 and "not in moonlight-qt list" in body["error"]
    st, _ = req(port, "POST", "/stream", {"app": ["Celeste"]})
    assert st == 400


def test_stream_uses_measured_rung(daemon):
    core, port, _, tmp = daemon
    (tmp / "link.json").write_text(json.dumps({"rung": "720p60-10"}))
    _, body = req(port, "POST", "/stream", {"app": "Desktop"})
    a = body["argv"]
    assert a[a.index("--resolution") + 1] == "1280x720" and a[a.index("--bitrate") + 1] == "10000"


def test_stream_launch_and_pending_queue(daemon):
    core, port, *_ = daemon
    req(port, "POST", "/stream", {"app": "Desktop", "launch": True})
    st, body = req(port, "GET", "/pending")
    assert body["pending"]["action"] == "stream" and body["pending"]["app"] == "Desktop"
    assert req(port, "GET", "/pending")[1]["pending"] is None         # taken exactly once
    for action in ("quit", "pair", "restart"):
        st, body = req(port, "POST", f"/{action}")
        assert st == 202
        assert req(port, "GET", "/pending")[1]["pending"]["action"] == action


def test_stale_pending_dropped(daemon):
    core, port, *_ = daemon
    core.queue({"action": "restart"})
    core.pending["at"] -= 60
    assert req(port, "GET", "/pending")[1]["pending"] is None


@pytest.mark.parametrize("method,path", [("GET", "/shell"), ("POST", "/exec"), ("POST", "/wake/../shell"),
                                         ("GET", "/wake"), ("PUT", "/wake"), ("DELETE", "/apps"),
                                         ("POST", "/health"), ("GET", "/etc/passwd")])
def test_anything_else_is_404(daemon, method, path):
    core, port, *_ = daemon
    assert req(port, method, path)[0] == 404


def test_actions_need_header(daemon):
    core, port, *_ = daemon
    assert req(port, "POST", "/wake", headers={"X-Couch": "0"})[0] == 403
    assert not WolSock.sent


def test_oversized_and_bad_bodies(daemon):
    core, port, *_ = daemon
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", "/stream", body=b"x" * 10, headers={"X-Couch": "1", "Content-Length": "999999"})
    assert c.getresponse().status == 413
    c.close()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", "/stream", body=b"[1,2]", headers={"X-Couch": "1"})
    assert c.getresponse().status == 400
    c.close()


def test_health_json_and_html(daemon):
    core, port, *_ = daemon
    core.health = {"overall": "yellow", "generated": time.time(),
                   "checks": [{"check": "network", "status": "yellow", "summary": "jitter <b>15</b> ms"}]}
    st, body = req(port, "GET", "/health")
    assert st == 200 and body["overall"] == "yellow"
    st, page = req(port, "GET", "/")
    assert st == 200 and "couchbox: yellow" in page and "&lt;b&gt;15" in page and "<b>15" not in page


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if ip.startswith("127.") else ip


def test_lan_client_gets_only_health(daemon):
    """Connect from a real non-loopback address of this machine: actions are 404, /health works."""
    core, port, *_ = daemon
    ip = lan_ip()
    if not ip:
        pytest.skip("no non-loopback IPv4 address on this machine")
    assert req(port, "GET", "/health", host=ip)[0] == 200
    assert req(port, "GET", "/", host=ip)[0] == 200
    for method, path in [("POST", "/wake"), ("GET", "/apps"), ("POST", "/stream"), ("GET", "/pending"),
                         ("POST", "/restart"), ("GET", "/status")]:
        assert req(port, method, path, {"app": "Desktop"}, host=ip)[0] == 404, path
    assert not WolSock.sent and core.pending is None


@pytest.mark.parametrize("addr,local", [("127.0.0.1", True), ("127.8.9.1", True), ("::1", True),
                                        ("::ffff:127.0.0.1", True), ("10.0.0.5", False), ("::ffff:10.0.0.5", False),
                                        ("fe80::1", False), ("garbage", False)])
def test_is_loopback(addr, local):
    assert D.is_loopback(addr) is local


def test_status_and_target(daemon):
    core, port, *_ = daemon
    st, body = req(port, "GET", "/status")
    assert body["host_up"] and body["sunshine"] == "open" and body["target"] == "gamepc.local"
    core.status = dict(core.status, method="arp")
    assert core.stream_target() == "10.9.8.7"       # name lookup failed: moonlight gets the ARP-found IP


def test_doctor_action_writes_health_and_alerts_once(daemon, monkeypatch):
    core, port, _, tmp = daemon
    posts = []
    core._post = lambda url, body, headers: posts.append((url, body))
    core.cfg.ntfy_url = "https://ntfy.example/couch"
    monkeypatch.setenv("FAKE_DOCTOR", "red")
    st, body = req(port, "POST", "/doctor", {"measure": False})
    assert st == 200 and body["overall"] == "red"
    health = json.loads((tmp / "health.json").read_text())
    assert health["overall"] == "red" and health["link_bad"] is True
    req(port, "POST", "/doctor", {"measure": False})
    assert len(posts) == 1 and posts[0][0] == "https://ntfy.example/couch"   # alert on transition only
    monkeypatch.setenv("FAKE_DOCTOR", "green")
    req(port, "POST", "/doctor")
    monkeypatch.setenv("FAKE_DOCTOR", "red")
    req(port, "POST", "/doctor")
    assert len(posts) == 2


def test_no_ntfy_without_url(daemon, monkeypatch):
    core, port, *_ = daemon
    posts = []
    core._post = lambda *a: posts.append(a)
    monkeypatch.setenv("FAKE_DOCTOR", "red")
    req(port, "POST", "/doctor")
    assert posts == []


def test_parse_list_variants():
    assert D.parse_list("Desktop\n\nSteam Big Picture\nDesktop\n") == ["Desktop", "Steam Big Picture"]
    assert D.parse_list('Name, ID, HDR Supported, App Collection Game, Hidden\n"Celeste", 12, false, false, false\n') \
        == ["Celeste"]


def test_discover_order(tmp_path):
    cache = str(tmp_path / "c.json")
    assert net.discover("pc", "AA:BB:CC:DD:EE:FF", cache, resolve=lambda h: "10.0.0.2",
                        arp=lambda m: "10.0.0.3") == ("10.0.0.2", "name")
    assert net.discover("pc", "AA:BB:CC:DD:EE:FF", cache, resolve=lambda h: None,
                        arp=lambda m: "10.0.0.3") == ("10.0.0.3", "arp")
    assert net.discover("pc", "AA:BB:CC:DD:EE:FF", cache, resolve=lambda h: None,
                        arp=lambda m: None) == ("10.0.0.3", "cache")
    assert net.discover("other", "", cache, resolve=lambda h: None, arp=lambda m: None) == (None, "none")


def test_arp_table_parse(tmp_path):
    p = tmp_path / "arp"
    p.write_text("IP address       HW type     Flags       HW address            Mask     Device\n"
                 "10.0.0.7         0x1         0x2         aa:bb:cc:dd:ee:ff     *        eno1\n"
                 "10.0.0.8         0x1         0x0         02:00:00:00:00:02     *        eno1\n")
    assert net.arp_lookup("AA:BB:CC:DD:EE:FF", str(p)) == "10.0.0.7"
    assert net.arp_lookup("02:00:00:00:00:02", str(p)) is None      # incomplete entry


def test_sd_notify(tmp_path, monkeypatch):
    from couch import sdnotify
    path = str(tmp_path / "notify.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    s.bind(path)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    assert sdnotify.notify("WATCHDOG=1")
    assert s.recv(100) == b"WATCHDOG=1"
    s.close()
    monkeypatch.setenv("WATCHDOG_USEC", "30000000")
    monkeypatch.delenv("WATCHDOG_PID", raising=False)
    assert sdnotify.watchdog_interval() == 15.0
    monkeypatch.delenv("NOTIFY_SOCKET")
    assert sdnotify.notify("READY=1") is False
