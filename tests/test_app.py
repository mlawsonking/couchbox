import json

from couch.launcher import app as A
from couch.launcher import config as C
from couch.launcher import input as inp
from couch.launcher import render as R
from couch.launcher import state as S
from fakes import FakeBackend


def mk(**kw):
    cfg = C.Config(mac="AA:BB:CC:DD:EE:FF", tiles=[C.Tile("Steam Big Picture", "Steam Big Picture"),
                                                    C.Tile("Celeste", "Celeste")], **kw)
    b = FakeBackend()
    power = []
    return A.App(cfg, b, now=0, display_power=power.append), b, power


def test_home_select_launches_focused_tile():
    a, b, _ = mk()
    a.handle([inp.WAKE, inp.RIGHT], 1)
    a.handle([inp.WAKE, inp.SELECT], 2)
    assert a.m.state == S.WAKING and a.m.app == "Celeste" and a.screen() == R.WAKING


def test_full_happy_path_and_back_to_home():
    a, b, _ = mk()
    a.handle([inp.SELECT], 0)
    b._host_up = b._sunshine_up = True
    a.tick(1)
    a.tick(2)
    assert a.screen() == R.STREAMING
    b.stream = ("exited", 0)
    a.tick(100)
    assert a.screen() == R.HOME


def test_menu_open_navigate_pair_and_close():
    a, b, _ = mk()
    a.handle([inp.MENU], 0)
    assert a.screen() == R.MENU
    a.handle([inp.BACK], 1)
    assert a.screen() == R.HOME
    a.handle([inp.MENU], 2)
    a.handle([inp.DOWN], 3)
    a.handle([inp.SELECT], 4)
    assert a.screen() == R.PAIR and a.view(4).pin == "1234"
    a.handle([inp.BACK], 5)
    assert a.screen() == R.HOME


def test_menu_restart_calls_backend():
    a, b, _ = mk()
    a.handle([inp.MENU], 0)
    a.handle([inp.SELECT], 1)
    assert b.restarted == 1


def test_pair_hidden_when_policy_off():
    a, b, _ = mk(pair_policy="off")
    a.handle([inp.MENU], 0)
    assert "Pair with the game PC" not in a.view(0).menu_items


def test_lost_screen_retry_and_home():
    a, b, _ = mk()
    a.handle([inp.SELECT], 0)
    a.tick(S.WAKE_TIMEOUT)
    v = a.view(S.WAKE_TIMEOUT)
    assert v.screen == R.LOST and v.kid and v.parent
    a.handle([inp.SELECT], 200)
    assert a.screen() == R.WAKING
    a.handle([inp.BACK], 201)
    assert a.screen() == R.HOME


def test_streaming_ignores_everything_but_force_quit():
    a, b, _ = mk()
    b._host_up = b._sunshine_up = True
    a.handle([inp.SELECT], 0)
    a.tick(1)
    a.handle([inp.BACK, inp.MENU, inp.SELECT], 2)
    assert a.screen() == R.STREAMING
    a.handle([inp.FORCE_QUIT], 3)
    assert a.screen() == R.HOME and b.stopped == 1


def test_attract_dim_then_display_sleep_then_wake_consumes_press():
    a, b, power = mk()
    a.tick(A.ATTRACT_AFTER - 1)
    assert a.dim == 0
    a.tick(A.ATTRACT_AFTER)
    assert a.dim == 0.6
    a.tick(A.SLEEP_AFTER)
    assert a.dim == 1.0 and power == [False]
    a.handle([inp.WAKE, inp.SELECT], A.SLEEP_AFTER + 5)
    assert a.dim == 0 and power == [False, True]
    assert a.m.state == S.IDLE        # the waking press did not launch anything


def test_no_attract_while_streaming():
    a, b, _ = mk()
    b._host_up = b._sunshine_up = True
    a.handle([inp.SELECT], 0)
    a.tick(1)
    a.tick(A.SLEEP_AFTER + 10)
    assert a.dim == 0 and a.screen() == R.STREAMING


def test_hot_reload_updates_tiles_and_menu():
    a, b, _ = mk()
    a.apply_config(C.Config(tiles=[C.Tile("Hades", "Hades")], pair_policy="off", error="couch.toml: bad"))
    v = a.view(0)
    assert [t.name for t in v.tiles] == ["Hades"] and "Pair with the game PC" not in v.menu_items
    assert v.notice.startswith("Settings problem")


def test_health_file(tmp_path):
    a, b, _ = mk()
    p = tmp_path / "health.json"
    p.write_text(json.dumps({"overall": "red", "link_bad": True}))
    a.refresh_health(str(p))
    v = a.view(0)
    assert v.health == "red" and v.link_bad
    a.refresh_health(str(tmp_path / "missing.json"))   # unreadable -> keep last known
    assert a.view(0).health == "red"


def test_view_host_state():
    a, b, _ = mk()
    b._host_up = None
    assert a.view(0).host_state == "unknown"
    b._host_up = False
    assert a.view(0).host_state == "asleep"
    b._host_up = True
    assert a.view(0).host_state == "awake"


def test_main_loop_runs_headless(tmp_path):
    """The real pygame loop (SDL dummy video) starts, renders, polls config and exits cleanly."""
    import os
    import subprocess
    import sys
    from conftest import REPO
    cfg = tmp_path / "couch.toml"
    cfg.write_text('[host]\nname = "localhost"\nmac = "AA:BB:CC:DD:EE:FF"\n[[tile]]\nname = "Desktop"\n')
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", COUCH_EXIT_AFTER="2",
               COUCH_CONFIG=str(cfg), COUCH_LOG_DIR=str(tmp_path), COUCH_STATE_DIR=str(tmp_path),
               COUCH_HEALTH_FILE=str(tmp_path / "health.json"),
               COUCH_MOONLIGHT=os.path.join(REPO, "tests/fixtures/bin/fake-moonlight-qt"), PYTHONPATH=REPO)
    p = subprocess.run([sys.executable, "-m", "couch.launcher"], env=env, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    log = (tmp_path / "launcher.log").read_text()
    assert "launcher up" in log and "localhost" in log
