import os
import time

import pytest

from couch.launcher import config as C
from couch.launcher import input as inp
from couch.launcher.tiles import Grid, Menu, PAIR
from conftest import REPO

GOOD = """
[host]
name = "gamepc.local"
mac = "AA:BB:CC:DD:EE:FF"
[stream]
display_mode = "auto"
default_rung = "720p60-10"
codec = "HEVC"
[[tile]]
name = "Celeste"
app = "Celeste"
art = "celeste.jpg"
[[tile]]
name = "Desktop"
"""


def write(tmp_path, text):
    p = tmp_path / "couch.toml"
    p.write_text(text)
    return str(p)


def test_example_config_loads_clean():
    cfg = C.load(os.path.join(REPO, "installer/files/etc/couch/couch.toml.example"))
    assert cfg.error is None
    assert [t.app for t in cfg.tiles] == ["Steam Big Picture", "Desktop"]
    assert cfg.stream_params() == ("1920x1080", 60, 15000)


def test_good_config(tmp_path):
    cfg = C.load(write(tmp_path, GOOD))
    assert cfg.error is None and cfg.mac == "AA:BB:CC:DD:EE:FF"
    assert cfg.tiles[1] == C.Tile("Desktop", "Desktop", "")
    assert cfg.stream_params() == ("1280x720", 60, 10000)
    assert cfg.stream_params("4k60-40") == ("3840x2160", 60, 40000)


@pytest.mark.parametrize("mode,expect", [("1080p60", ("1920x1080", 60, 15000)), ("4k60", ("3840x2160", 60, 40000)),
                                         ("4k120", ("3840x2160", 120, 60000))])
def test_fixed_modes(tmp_path, mode, expect):
    assert C.load(write(tmp_path, f'[stream]\ndisplay_mode = "{mode}"\n')).stream_params() == expect


def test_custom_mode(tmp_path):
    cfg = C.load(write(tmp_path, '[stream]\ndisplay_mode="custom"\ncustom_resolution="2560x1080"\n'
                                 'custom_fps=120\ncustom_bitrate_kbps=25000\n'))
    assert cfg.stream_params() == ("2560x1080", 120, 25000)


def test_missing_file_gives_defaults_and_error(tmp_path):
    cfg = C.load(str(tmp_path / "nope.toml"))
    assert "missing" in cfg.error and len(cfg.tiles) == 2


def test_syntax_error(tmp_path):
    cfg = C.load(write(tmp_path, "[host\nname=1"))
    assert "TOML syntax error" in cfg.error and cfg.host == "gamepc.local"


@pytest.mark.parametrize("text,words", [
    ('[host]\nmac = "nope"\n', "host.mac"),
    ('[host]\nname = "bad host!"\n', "host.name"),
    ('[stream]\ndisplay_mode = "8k"\n', "display_mode"),
    ('[stream]\ncodec = "VP9"\n', "codec"),
    ('[stream]\ncustom_fps = "60"\n', "custom_fps must be int"),
    ('[stream]\ncustom_bitrate_kbps = 9999999\n', "custom_bitrate_kbps"),
    ('[maintenance]\nnightly_reboot = "25:00"\n', "nightly_reboot"),
    ('[maintenance]\nntfy_url = "ftp://x"\n', "ntfy_url"),
    ('[pair]\npolicy = "always"\n', "pair.policy"),
    ('host = 5\n', "[host] must be a table"),
    ('[[tile]]\napp = "x"\n', "needs a name"),
])
def test_bad_values_are_reported_not_fatal(tmp_path, text, words):
    cfg = C.load(write(tmp_path, text))
    assert cfg.error and words in cfg.error
    assert cfg.tiles   # the launcher always has something to show


def test_ip_host_and_art_url_warn(tmp_path):
    cfg = C.load(write(tmp_path, '[host]\nname="10.0.0.5"\n[[tile]]\nname="A"\nart="https://x/y.jpg"\n'))
    assert cfg.error is None
    assert any("IP address" in w for w in cfg.warnings) and cfg.tiles[0].art == ""


def test_watcher_hot_reload(tmp_path):
    p = write(tmp_path, GOOD)
    w = C.Watcher(p)
    assert w.poll() is None
    time.sleep(0.01)
    with open(p, "a") as fh:
        fh.write('[[tile]]\nname = "Hades"\n')
    cfg = w.poll()
    assert cfg is not None and [t.name for t in cfg.tiles][-1] == "Hades"
    assert w.poll() is None


def tiles(n):
    return [C.Tile(f"T{i}", f"T{i}") for i in range(n)]


def test_grid_navigation_edges():
    g = Grid(tiles(6))                 # 4 cols: row0 = 0..3, row1 = 4..5
    assert not g.move(inp.LEFT) and not g.move(inp.UP)
    assert g.move(inp.RIGHT) and g.focus == 1
    assert g.move(inp.DOWN) and g.focus == 5
    assert not g.move(inp.RIGHT)
    assert g.move(inp.UP) and g.focus == 1
    g.focus = 3
    assert g.move(inp.DOWN) and g.focus == 5     # clamps to the last tile
    g.focus = 3
    assert not g.move(inp.RIGHT)


def test_grid_small_and_empty():
    g = Grid(tiles(2))
    assert g.cols == 2 and g.rows == 1
    e = Grid([])
    assert e.selected() is None and not e.move(inp.RIGHT)


def test_grid_replace_keeps_focus_by_app():
    g = Grid(tiles(5))
    g.focus = 3
    g.replace([C.Tile("New", "New")] + tiles(5))
    assert g.selected().app == "T3"
    g.replace(tiles(2))
    assert g.focus == 0


def test_menu_hides_pair_when_policy_off():
    assert PAIR in Menu(True).items and PAIR not in Menu(False).items
    m = Menu(True)
    assert not m.move(inp.UP) and m.move(inp.DOWN) and m.selected() == PAIR
