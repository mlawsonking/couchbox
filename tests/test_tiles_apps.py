"""Home grid = config tiles + Steam-library tiles + a card for every app couch-daemon lists (hide list applied).
Before this, only [[tile]] blocks and library.json made tiles, so a box with neither showed Steam Big Picture and
Desktop while the daemon's /apps already listed 30 Sunshine apps (2026-11-09)."""
import json
import threading
import time

import pytest

from couch.launcher import app as A
from couch.launcher import config as C
from couch.launcher import daemon_client as DC
from couch.launcher import input as inp
from couch.launcher import state as S
from couch.launcher.tiles import merge_tiles
from fakes import FakeBackend

# What GET /apps returned on the NUC (30 entries, in Sunshine's order), trimmed to the shapes that matter.
NUC_APPS = ["BONEWORKS", "Battlefield 4", "Cyberpunk 2077", "Desktop", "GTA V", "Half-Life 2", "Half-Life: Alyx",
            "Metro 2033", "Retro", "Steam Big Picture", "SteamVR", "The Humans Collection", "XCOM 2"]
CFG = [C.Tile("Steam Big Picture", "Steam Big Picture"), C.Tile("Desktop", "Desktop")]


def names(tiles):
    return [t.app for t in tiles]


# ------------------------------------------------------------------ merge_tiles
def test_every_daemon_app_gets_a_tile_config_first_then_the_rest_sorted():
    out = merge_tiles(CFG, NUC_APPS, hide=["SteamVR"])
    assert names(out)[:2] == ["Steam Big Picture", "Desktop"]               # they keep their place
    assert names(out)[2:] == ["Battlefield 4", "BONEWORKS", "Cyberpunk 2077", "GTA V", "Half-Life 2",
                              "Half-Life: Alyx", "Metro 2033", "Retro", "The Humans Collection", "XCOM 2"]
    assert all(t.art == "" for t in out[2:])                                # generated cards: no art


def test_library_tiles_with_art_come_before_the_plain_cards_and_win_over_the_daemon_entry():
    lib = [C.Tile("Celeste", "Celeste", "1.jpg"), C.Tile("Hollow Knight", "Hollow Knight", "2.jpg")]
    out = merge_tiles(CFG + lib, ["Zelda", "Celeste", "Alpha", "Hollow Knight"])
    assert names(out) == ["Steam Big Picture", "Desktop", "Celeste", "Hollow Knight", "Alpha", "Zelda"]
    assert [t.art for t in out[:4]] == ["", "", "1.jpg", "2.jpg"]


def test_no_duplicates_case_and_spacing_insensitive():
    out = merge_tiles(CFG, ["desktop", "STEAM  BIG PICTURE", "Retro", "retro", " Retro "])
    assert names(out) == ["Steam Big Picture", "Desktop", "Retro"]


def test_hide_list_is_case_insensitive_and_only_hides_automatic_tiles():
    assert "SteamVR" not in names(merge_tiles(CFG, NUC_APPS, hide=["steamvr"]))
    assert "SteamVR" in names(merge_tiles(CFG, NUC_APPS, hide=[]))
    # an explicit [[tile]] for a hidden app is the owner's choice: it stays
    explicit = CFG + [C.Tile("VR", "SteamVR")]
    assert "SteamVR" in names(merge_tiles(explicit, NUC_APPS, hide=["SteamVR"]))


def test_junk_entries_and_long_names_are_handled():
    out = merge_tiles(CFG, ["", "   ", None, 7, "A" * 80])
    assert len(out) == 3 and out[2].app == "A" * 80 and len(out[2].name) == 40       # label trimmed, app name exact


def test_no_daemon_apps_leaves_the_configured_tiles_unchanged():
    assert merge_tiles(CFG, []) == CFG


# ------------------------------------------------------------------ config
def test_hide_defaults_to_steamvr_and_can_be_replaced_or_emptied(tmp_path):
    hdr = '[host]\nname = "gamepc.local"\nmac = "AA:BB:CC:DD:EE:FF"\n'
    none = str(tmp_path / "none.json")
    f = tmp_path / "couch.toml"
    f.write_text(hdr)
    assert C.load(str(f), library=none).hide_tiles == ["SteamVR"]
    f.write_text(hdr + '[tiles]\nhide = ["SteamVR", "Black Mesa"]\n')
    assert C.load(str(f), library=none).hide_tiles == ["SteamVR", "Black Mesa"]
    f.write_text(hdr + '[tiles]\nhide = []\n')
    assert C.load(str(f), library=none).hide_tiles == []
    f.write_text(hdr + '[tiles]\nhide = [1, 2]\n')
    cfg = C.load(str(f), library=none)
    assert cfg.hide_tiles == ["SteamVR"] and "tiles.hide" in cfg.error


def test_library_tiles_honour_the_hide_list_too(tmp_path):
    lib = tmp_path / "library.json"
    lib.write_text(json.dumps({"games": [{"name": "SteamVR", "appid": 250820}, {"name": "Celeste", "appid": 504230}]}))
    f = tmp_path / "couch.toml"
    f.write_text('[host]\nname = "gamepc.local"\nmac = "AA:BB:CC:DD:EE:FF"\n')
    assert "SteamVR" not in names(C.load(str(f), library=str(lib)).tiles)
    assert "Celeste" in names(C.load(str(f), library=str(lib)).tiles)


# ------------------------------------------------------------------ App: refresh without a restart
def mk():
    cfg = C.Config(mac="AA:BB:CC:DD:EE:FF", tiles=list(CFG))
    return A.App(cfg, FakeBackend(), now=0)


def test_app_shows_every_listed_app_and_refreshes_on_rename_and_removal():
    a = mk()
    assert names(a.grid.tiles) == ["Steam Big Picture", "Desktop"]
    assert a.set_daemon_apps(NUC_APPS) is True
    assert "Retro" in names(a.grid.tiles) and "SteamVR" not in names(a.grid.tiles) and len(a.grid.tiles) == 12
    assert a.set_daemon_apps(NUC_APPS) is False                              # same list: nothing to do
    renamed = [x if x != "Retro" else "Retro Arcade" for x in NUC_APPS if x != "XCOM 2"]
    assert a.set_daemon_apps(renamed) is True
    got = names(a.grid.tiles)
    assert "Retro Arcade" in got and "Retro" not in got and "XCOM 2" not in got
    assert a.set_daemon_apps(renamed + ["Black Mesa"]) is True and "Black Mesa" in names(a.grid.tiles)


def test_an_empty_list_never_clears_the_tiles():
    """PC asleep -> moonlight-qt list fails -> the daemon answers an empty list: the last good list stays."""
    a = mk()
    a.set_daemon_apps(NUC_APPS)
    n = len(a.grid.tiles)
    assert a.set_daemon_apps([]) is False and len(a.grid.tiles) == n


def test_refresh_keeps_the_focus_on_the_same_app():
    a = mk()
    a.set_daemon_apps(["Zelda", "Metro 2033"])
    a.grid.focus = names(a.grid.tiles).index("Zelda")
    a.set_daemon_apps(["Alpha", "Zelda", "Metro 2033", "Beta"])             # new apps sort in front of the focused one
    assert a.grid.selected().app == "Zelda"


def test_selecting_an_automatic_tile_uses_the_normal_launch_path():
    a = mk()
    a.set_daemon_apps(["Retro"])
    a.grid.focus = names(a.grid.tiles).index("Retro")
    a.handle([inp.SELECT], 1)
    assert a.m.app == "Retro" and a.m.state == S.WAKING


def test_config_reload_keeps_the_automatic_tiles_and_applies_a_new_hide_list():
    a = mk()
    a.set_daemon_apps(NUC_APPS)
    assert "Retro" in names(a.grid.tiles)
    a.apply_config(C.Config(mac="AA:BB:CC:DD:EE:FF", tiles=list(CFG), hide_tiles=["Retro"]))
    assert "Retro" not in names(a.grid.tiles) and "XCOM 2" in names(a.grid.tiles)
    a.apply_config(C.Config(mac="AA:BB:CC:DD:EE:FF", tiles=list(CFG), hide_tiles=[]))
    assert "SteamVR" in names(a.grid.tiles)


def test_without_a_daemon_nothing_changes():
    """LocalBackend / FakeBackend have no apps(): the main loop never calls set_daemon_apps, tiles = config only."""
    from couch.launcher.backend import LocalBackend
    assert not hasattr(LocalBackend, "apps") and not hasattr(FakeBackend, "apps")
    assert names(mk().grid.tiles) == ["Steam Big Picture", "Desktop"]


# ------------------------------------------------------------------ AppsPoller
def caller_for(answers):
    it = iter(answers)

    def call(method, path, body=None, timeout=3.0):
        a = next(it)
        if isinstance(a, Exception):
            raise a
        return a
    return call


def test_poll_replaces_the_list_only_with_a_usable_answer(tmp_path):
    cache = tmp_path / DC.APPS_CACHE
    p = DC.AppsPoller(caller=caller_for([(200, {"apps": ["Desktop", "Retro"]}), (200, {"apps": []}),
                                         (500, {"error": "x"}), OSError("down"), (200, {"apps": ["Desktop"]}),
                                         (200, {"apps": "nope"})]), cache_path=str(cache))
    assert p.apps == []
    assert p.poll_once() is True and p.apps == ["Desktop", "Retro"]
    assert p.poll_once() is False and p.apps == ["Desktop", "Retro"]         # empty list (PC asleep)
    assert p.poll_once() is False and p.apps == ["Desktop", "Retro"]         # daemon error
    assert p.poll_once() is False and p.apps == ["Desktop", "Retro"]         # daemon down
    assert p.poll_once() is True and p.apps == ["Desktop"]                   # a real change (Retro was removed)
    assert p.poll_once() is False and p.apps == ["Desktop"]                  # garbage
    assert json.loads(cache.read_text())["apps"] == ["Desktop"]


def test_the_last_good_list_survives_a_restart_with_the_pc_asleep(tmp_path):
    """The nightly reboot wipes the daemon's in-memory list and the PC is asleep: tiles must still be there."""
    cache = tmp_path / DC.APPS_CACHE
    first = DC.AppsPoller(caller=caller_for([(200, {"apps": NUC_APPS})]), cache_path=str(cache))
    first.poll_once()
    after_reboot = DC.AppsPoller(caller=caller_for([(200, {"apps": []})]), cache_path=str(cache))
    assert after_reboot.apps == NUC_APPS                                     # loaded before any poll
    assert after_reboot.poll_once() is False and after_reboot.apps == NUC_APPS


def test_a_broken_or_missing_cache_file_is_just_empty(tmp_path):
    bad = tmp_path / DC.APPS_CACHE
    bad.write_text("{not json")
    assert DC.AppsPoller(caller=caller_for([]), cache_path=str(bad)).apps == []
    assert DC.AppsPoller(caller=caller_for([]), cache_path=str(tmp_path / "missing.json")).apps == []
    bad.write_text(json.dumps({"apps": ["ok", 3, None, "  "]}))
    assert DC.AppsPoller(caller=caller_for([]), cache_path=str(bad)).apps == ["ok"]


def test_the_poller_thread_survives_a_caller_that_raises_anything():
    p = DC.AppsPoller(caller=caller_for([ValueError("x"), TypeError("y"), (200, {"apps": ["A"]})]), interval=0.01,
                      retry=0.01)
    p.start()
    deadline = time.time() + 3
    while p.apps != ["A"] and time.time() < deadline:
        time.sleep(0.02)
    p.stop()
    assert p.apps == ["A"]


# ------------------------------------------------------------------ end to end against a real daemon
def test_daemon_backend_lists_the_daemons_apps_and_the_grid_shows_them(live_apps):
    b, tmp = live_apps
    deadline = time.time() + 5
    while not b.apps() and time.time() < deadline:
        time.sleep(0.05)
    assert b.apps() == ["Desktop", "Celeste", "Retro"]
    a = A.App(C.Config(mac="AA:BB:CC:DD:EE:FF", tiles=list(CFG)), b, now=0)
    a.set_daemon_apps(b.apps())
    assert names(a.grid.tiles) == ["Steam Big Picture", "Desktop", "Celeste", "Retro"]
    assert json.loads((tmp / DC.APPS_CACHE).read_text())["apps"] == ["Desktop", "Celeste", "Retro"]


@pytest.fixture
def live_apps(tmp_path, monkeypatch):
    import os
    from couch.daemon import couchd as D
    import couch.launcher.backend as B
    fake = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "bin", "fake-moonlight-qt")
    cfg_path = tmp_path / "couch.toml"
    cfg_path.write_text('[host]\nname = "gamepc.local"\nmac = "AA:BB:CC:DD:EE:FF"\n')
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "list")
    monkeypatch.setenv("FAKE_LIST_MODE", "list")
    monkeypatch.setenv("FAKE_MOONLIGHT_APPS", "Desktop\nCeleste\nRetro")
    core = D.Core(config_path=str(cfg_path), state_dir=str(tmp_path), moonlight=fake, doctor="/bin/false",
                  send_wol=lambda m: None, discover=lambda h, m, c: ("10.0.0.9", "name"), probe=lambda ip: "open")
    core.probe_once()
    srv = D.serve(core, bind="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(DC, "DAEMON_PORT", srv.server_address[1])
    monkeypatch.setattr(B, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(B, "STATE_DIR", str(tmp_path))
    b = DC.DaemonBackend(C.load(str(cfg_path)), poller=DC.StatusPoller(interval=0.05), moonlight=fake,
                         state_dir=str(tmp_path))
    yield b, tmp_path
    b.close()
    srv.shutdown()
