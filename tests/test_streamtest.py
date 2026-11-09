"""couch-streamtest: the pure judgement of a held stream (the real run needs the NUC, the daemon and the TV)."""
import os
import subprocess
import sys

from conftest import REPO, load_script

ST = load_script("couch/bin/couch-streamtest", "couch_streamtest")
BASE = {"MainPID": "4242", "NRestarts": "0", "ActiveState": "active"}


def test_parse_show():
    assert ST.parse_show("MainPID=4242\nNRestarts=3\nActiveState=active\n") == \
        {"MainPID": "4242", "NRestarts": "3", "ActiveState": "active"}
    assert ST.parse_show("") == {}


def test_hold_passes_when_everything_stays_put():
    w = ST.Watch(BASE, hold=60, started_at=1000.0)
    for t in range(1001, 1060):
        assert w.step(float(t), dict(BASE), True, "") is None
        assert not w.passed(float(t))
    assert w.passed(1060.0)


def test_kiosk_restart_is_the_crash_class():
    w = ST.Watch(BASE, hold=60, started_at=1000.0)
    assert w.step(1001.0, dict(BASE), True, "") is None
    fail = w.step(1002.2, {"MainPID": "4300", "NRestarts": "1", "ActiveState": "active"}, False, "")
    assert "kiosk restarted during the stream after 2.2 s" in fail and "4242 -> 4300" in fail
    assert not w.passed(1100.0)


def test_launcher_restart_and_lost_and_early_end():
    w = ST.Watch(BASE, 60, 0.0)
    assert "launcher restarted" in w.step(3.0, dict(BASE), True, "... INFO launcher up: 3840x2160\n")
    w = ST.Watch(BASE, 60, 0.0)
    assert w.step(3.0, dict(BASE), True, "x INFO state STREAMING -> LOST (STREAM_LOST: moonlight-qt exit code 1)") \
        == "the launcher gave up: state STREAMING -> LOST (STREAM_LOST: moonlight-qt exit code 1)"
    w = ST.Watch(BASE, 60, 0.0)
    assert "ended on its own after 7.0 s" in w.step(7.0, dict(BASE), False, "")


def test_main_without_a_daemon_is_a_clean_error_not_a_traceback(monkeypatch, capsys):
    """2026-11-09: the first real run crashed in the very first call (a helper named `http` shadowed the module)."""
    monkeypatch.setattr(ST, "PORT", 1)                     # nothing listens on loopback:1
    rc = ST.main(["--hold", "1", "--json"])
    out = capsys.readouterr().out
    assert rc == 2 and '"result": "ERROR"' in out and "not reachable" in out


def test_help_runs_without_a_daemon():
    cp = subprocess.run([sys.executable, os.path.join(REPO, "couch", "bin", "couch-streamtest"), "--help"],
                        capture_output=True, text=True)
    assert cp.returncode == 0 and "--hold" in cp.stdout and "--app" in cp.stdout


XWININFO = """
xwininfo: Window id: 0x4b6 (the root window) (has no name)

  Root window id: 0x4b6 (the root window) (has no name)
  Parent window id: 0x0 (none)
     7 children:
     0xa00038 (has no name): ()  1920x1080+960+540  +960+540
     0x800004 "Qt Selection Owner for Moonlight": ()  3x3+0+0  +0+0
     0xa0002d (has no name): ()  1x1+-10+-10  +-10+-10
     0x40000d (has no name): ()  3840x2160+0+0  +0+0
"""


def test_parse_children_reads_size_and_position():
    kids = {k["id"]: k for k in ST.parse_children(XWININFO)}
    assert kids["0xa00038"] == {"id": "0xa00038", "w": 1920, "h": 1080, "x": 960, "y": 540}
    assert kids["0xa0002d"]["x"] == -10 and kids["0x40000d"]["w"] == 3840 and len(kids) == 4


def test_the_2026_11_09_picture_is_a_quarter_of_the_tv_and_is_caught():
    """1920x1080 centered on the 4K screen: exactly what the stream looked like before couch.display."""
    win = {"w": 1920, "h": 1080, "x": 960, "y": 540}
    assert not ST.fills_screen(win, (3840, 2160))
    assert ST.fills_screen({"w": 1920, "h": 1080, "x": 0, "y": 0}, (1920, 1080))
    assert not ST.fills_screen({"w": 1920, "h": 1080, "x": 0, "y": 0}, (3840, 2160))       # top-left corner is no better


def test_moonlight_window_is_matched_by_pid_and_must_be_viewable():
    calls = []

    def fake(argv, timeout=15.0):
        calls.append(argv)
        if argv[0] == "pgrep":
            return "98822 "
        if argv[:3] == ["xwininfo", "-root", "-children"]:
            return XWININFO
        if argv[0] == "xprop":
            return {"0xa00038": "_NET_WM_PID(CARDINAL) = 98822", "0x40000d": "_NET_WM_PID(CARDINAL) = 95608"}.get(
                argv[2], "_NET_WM_PID:  not found.")
        if argv[:2] == ["xwininfo", "-id"]:
            return "  Map State: IsViewable"
        return ""

    assert ST.moonlight_window(fake)["id"] == "0xa00038"                    # not the launcher (pid 95608)

    def unmapped(argv, timeout=15.0):
        return "  Map State: IsUnMapped" if argv[:2] == ["xwininfo", "-id"] else fake(argv, timeout)
    assert ST.moonlight_window(unmapped) is None


def test_moonlight_running_matches_the_versioned_path_the_real_process_has():
    """The first streamtest grepped .../current/ and could never match the real /opt/couch/moonlight/6.2.0/... process."""
    import re
    real = "/opt/couch/moonlight/6.2.0/usr/bin/moonlight stream bobbo.local Desktop --quit-after"
    assert re.search(ST.MOONLIGHT_RE, real)
    assert not re.search(ST.MOONLIGHT_RE, "/bin/bash -c pgrep -f /opt/couch/moonlight/6.2.0/usr/bin/moonlight")
    assert ST.moonlight_running(lambda argv, timeout=15.0: "98822 ") is True
    assert ST.moonlight_running(lambda argv, timeout=15.0: "") is False


def test_screen_size():
    xr = "Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 16384 x 16384"
    assert ST.screen_size(lambda argv, timeout=15.0: xr) == (1920, 1080)
    assert ST.screen_size(lambda argv, timeout=15.0: "") is None
