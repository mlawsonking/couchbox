import json
import os
import threading
import time

import pytest

from couch import net
from couch.daemon import couchd as D
from couch.launcher import app as A
from couch.launcher import config as C
from couch.launcher import daemon_client as DC
from couch.launcher import input as inp
from couch.launcher import state as S
from conftest import REPO
from fakes import FakeBackend

FAKE = os.path.join(REPO, "tests", "fixtures", "bin", "fake-moonlight-qt")


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A real daemon on a random loopback port + a DaemonBackend talking to it."""
    cfg_path = tmp_path / "couch.toml"
    cfg_path.write_text('[host]\nname = "gamepc.local"\nmac = "AA:BB:CC:DD:EE:FF"\n')
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "list")
    monkeypatch.setenv("FAKE_LIST_MODE", "list")
    monkeypatch.setenv("FAKE_MOONLIGHT_APPS", "Desktop\nCeleste")
    monkeypatch.setenv("MOONLIGHT_ARGV_LOG", str(tmp_path / "argv.log"))
    wol = []
    core = D.Core(config_path=str(cfg_path), state_dir=str(tmp_path), moonlight=FAKE, doctor="/bin/false",
                  send_wol=wol.append, discover=lambda h, m, c: ("10.0.0.9", "name"), probe=lambda ip: "open")
    core.probe_once()
    srv = D.serve(core, bind="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(DC, "DAEMON_PORT", srv.server_address[1])
    import couch.launcher.backend as B
    monkeypatch.setattr(B, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(B, "STATE_DIR", str(tmp_path))
    poller = DC.StatusPoller(interval=0.05)
    b = DC.DaemonBackend(C.load(str(cfg_path)), poller=poller, moonlight=FAKE, state_dir=str(tmp_path))
    time.sleep(0.2)
    yield core, b, wol, tmp_path
    b.close()
    srv.shutdown()


def test_available_and_status(live):
    core, b, wol, _ = live
    assert DC.daemon_available()
    assert b.host_up() is True and b.sunshine_up() is True


def test_wake_goes_through_daemon(live):
    core, b, wol, _ = live
    b.wake()
    assert wol == ["AA:BB:CC:DD:EE:FF"]


def test_stream_runs_daemon_argv_and_flags_streaming(live, monkeypatch):
    core, b, wol, tmp = live
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "hang")
    b.start_stream("Celeste")
    assert (tmp / "streaming").exists()
    assert b.stream_status() == ("running", None)
    deadline = time.time() + 5
    while "stream" not in (tmp / "argv.log").read_text() and time.time() < deadline:
        time.sleep(0.05)
    assert b.stop_stream()
    assert not (tmp / "streaming").exists()
    assert "stream gamepc.local Celeste --quit-after" in (tmp / "argv.log").read_text()


def test_unlisted_app_is_a_failed_launch_without_a_child(live):
    core, b, wol, tmp = live
    b.start_stream("NotAGame")
    assert b.stream is None and "not in moonlight-qt list" in b.last_error
    assert b.stream_status() == ("exited", 1)


def test_daemon_down_means_unknown(monkeypatch):
    monkeypatch.setattr(DC, "DAEMON_PORT", 1)
    assert DC.daemon_available() is False
    p = DC.StatusPoller(interval=0.05)
    p.start()
    time.sleep(0.1)
    p.stop()
    assert p.status is None


def test_remote_validation_drives_the_state_machine(live):
    """Fable over SSH: POST /stream {launch:true} -> the launcher picks it up and runs the normal flow."""
    core, b, wol, tmp = live
    a = A.App(C.load(str(tmp / "couch.toml")), b, now=0)
    D_req = core.stream("Desktop", launch=True)
    assert D_req[0] == 200
    a.handle_pending(b.pending(), 1)
    assert a.m.state == S.WAIT_SUNSHINE and a.m.app == "Desktop"


def test_handle_pending_actions():
    cfg = C.Config(tiles=[C.Tile("Desktop", "Desktop")])
    fb = FakeBackend(host_up=True, sunshine_up=True)
    a = A.App(cfg, fb, now=0)
    a.handle_pending({"action": "pair"}, 1)
    assert a.m.state == S.PAIRING
    a.handle([inp.BACK], 2)
    a.handle_pending({"action": "stream", "app": "Desktop"}, 3)
    a.tick(4)
    assert a.m.state == S.STREAMING
    a.handle_pending({"action": "quit"}, 5)
    assert a.m.state == S.IDLE and fb.stopped == 1
    a.handle_pending({"action": "restart"}, 6)
    assert fb.restarted == 1
    a.handle_pending({"action": "rm -rf /"}, 7)
    a.handle_pending(None, 8)
    assert a.m.state == S.IDLE


def test_library_tiles(tmp_path):
    art = tmp_path / "art"
    art.mkdir()
    (art / "504230.jpg").write_bytes(b"x")
    lib = tmp_path / "library.json"
    lib.write_text(json.dumps({"games": [
        {"appid": 504230, "name": "Celeste", "installed": True},
        {"appid": 367520, "name": "Hollow Knight", "installed": True},
        {"appid": 1, "name": "Uninstalled", "installed": False},
        {"appid": "x", "name": "Bad id"}, {"name": ""}, "junk"]}))
    tiles = C.library_tiles(str(lib), str(art))
    assert tiles == [C.Tile("Celeste", "Celeste", "504230.jpg"), C.Tile("Hollow Knight", "Hollow Knight", "")]
    cfgp = tmp_path / "couch.toml"
    cfgp.write_text('[[tile]]\nname = "Celeste"\n')
    cfg = C.load(str(cfgp), str(lib))
    assert [t.name for t in cfg.tiles] == ["Celeste", "Hollow Knight"]     # no duplicate of a configured tile
    assert C.library_tiles(str(tmp_path / "none.json")) == []
