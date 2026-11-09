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
