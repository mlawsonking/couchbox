"""couch.display: the X screen must equal the stream's mode (no window manager runs in the kiosk)."""
import os

import pytest

from couch import display as D
from couch.launcher import config as C
from conftest import REPO

FAKE = os.path.join(REPO, "tests", "fixtures", "bin", "fake-xrandr")

# `xrandr --query` on the NUC's TV (Samsung, DP-1), captured 2026-11-09: 4K is offered only at 30 Hz.
NUC_QUERY = """Screen 0: minimum 320 x 200, current 3840 x 2160, maximum 16384 x 16384
DP-1 connected primary 3840x2160+0+0 (normal left inverted right x axis y axis) 1872mm x 1053mm
   3840x2160     30.00*+  25.00    24.00    29.97    23.98
   2560x1440     59.95
   1920x1080     60.00    50.00    59.94    30.00    25.00    24.00    29.97    23.98
   1920x1080i    60.00    50.00    59.94
   1280x720      60.00    50.00    59.94
DP-2 disconnected (normal left inverted right x axis y axis)
HDMI-1 disconnected (normal left inverted right x axis y axis)
"""


@pytest.fixture
def xr(tmp_path, monkeypatch):
    monkeypatch.setenv("COUCH_XRANDR", FAKE)
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setenv("FAKE_XRANDR_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("FAKE_XRANDR_LOG", str(tmp_path / "calls"))
    return tmp_path


def current(tmp):
    return (tmp / "state").read_text().strip() if (tmp / "state").exists() else "3840x2160 30.00"


def sets(tmp):
    log = tmp / "calls"
    return [ln for ln in (log.read_text().splitlines() if log.exists() else []) if "--mode" in ln]


def test_parse_real_nuc_output():
    outs = D.parse_query(NUC_QUERY)
    assert [o.name for o in outs] == ["DP-1", "DP-2", "HDMI-1"]
    out = D.pick_output(outs)
    assert out.name == "DP-1" and out.primary
    assert out.current.label == "3840x2160@30.00"
    assert [m.label for m in out.modes if m.preferred] == ["3840x2160@30.00"]
    assert not any(m.width == 1920 and m.rate == "60.00" and m.current for m in out.modes)


def test_choose_mode_exact_size_closest_rate_never_interlaced():
    out = D.pick_output(D.parse_query(NUC_QUERY))
    assert D.choose_mode(out, 1920, 1080, 60).label == "1920x1080@60.00"      # not 59.94, not the interlaced 60
    assert D.choose_mode(out, 1920, 1080, 50).rate == "50.00"
    assert D.choose_mode(out, 3840, 2160, 60).rate == "30.00"                 # all the TV has at 4K
    assert D.choose_mode(out, 1600, 900, 60) is None


def test_ensure_switches_to_the_stream_mode_and_restore_undoes_it(xr):
    prev, note = D.ensure(1920, 1080, 60)
    assert prev.label == "3840x2160@30.00" and current(xr) == "1920x1080 60.00"
    assert "DP-1" in note
    assert D.restore(prev) and current(xr) == "3840x2160 30.00"


def test_ensure_is_a_no_op_when_the_screen_already_matches(xr):
    D.ensure(1920, 1080, 60)
    before = sets(xr)
    prev, note = D.ensure(1920, 1080, 60)
    assert prev is None and "already" in note and sets(xr) == before         # no second mode set = no TV re-sync


def test_ensure_leaves_the_screen_when_the_mode_is_not_offered(xr):
    prev, note = D.ensure(1600, 900, 60)
    assert prev is None and "no 1600x900" in note and sets(xr) == []


def test_xrandr_failure_is_reported_not_raised(xr, monkeypatch):
    monkeypatch.setenv("FAKE_XRANDR_FAIL", "1")
    prev, note = D.ensure(1920, 1080, 60)
    assert prev is None and "failed" in note and current(xr) == "3840x2160 30.00"


def test_no_display_or_no_xrandr_changes_nothing(xr, monkeypatch):
    monkeypatch.delenv("DISPLAY")
    assert D.ensure(1920, 1080, 60)[0] is None and sets(xr) == []
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setenv("COUCH_XRANDR", "/nonexistent/xrandr")
    assert D.ensure(1920, 1080, 60)[0] is None
    assert D.restore(None) == "nothing to restore"


def test_spec_parsing_and_kiosk_base(xr):
    assert D.parse_spec("1920x1080@60") == (1920, 1080, 60)
    assert D.parse_spec("1280x720") == (1280, 720, 60)
    assert D.parse_spec("preferred") is None and D.parse_spec("") is None and D.parse_spec("big") is None
    assert "preferred mode" in D.apply_base("preferred") and sets(xr) == []
    D.apply_base("1920x1080@60")
    assert current(xr) == "1920x1080 60.00"


def test_kiosk_mode_config_default_and_validation(tmp_path):
    none = str(tmp_path / "none.json")
    hdr = '[host]\nname = "gamepc.local"\nmac = "AA:BB:CC:DD:EE:FF"\n'
    assert C.Config().kiosk_mode == "1920x1080@60"
    f = tmp_path / "couch.toml"
    f.write_text(hdr + '[display]\nkiosk_mode = "preferred"\n')
    cfg = C.load(str(f), library=none)
    assert cfg.kiosk_mode == "preferred" and cfg.error is None
    f.write_text(hdr + '[display]\nkiosk_mode = "huge"\n')
    cfg = C.load(str(f), library=none)
    assert cfg.kiosk_mode == "1920x1080@60" and "display.kiosk_mode" in cfg.error
    f.write_text(hdr)
    assert C.load(str(f), library=none).kiosk_mode == "1920x1080@60"
