import datetime as dt
import json
import os

import pytest

from conftest import load_script

M = load_script("couch/bin/couch-maint", "couch_maint")
U = load_script("couch/bin/couch-update", "couch_update")


def at(h, m):
    return dt.datetime(2026, 11, 4, h, m)


@pytest.mark.parametrize("now,uptime,stream_age,due,why", [
    (at(4, 30), 86400, None, True, "due"),
    (at(4, 44), 86400, None, True, "due"),
    (at(4, 45), 86400, None, False, "outside the window"),
    (at(4, 29), 86400, None, False, "outside the window"),
    (at(4, 31), 600, None, False, "booted recently"),
    (at(4, 31), 86400, 120, False, "a stream is running"),
    (at(4, 31), 86400, 7 * 3600, True, "due"),            # stale flag (crashed launcher) does not block
])
def test_reboot_window(now, uptime, stream_age, due, why):
    assert M.reboot_due(now, "04:30", uptime, stream_age) == (due, why)


def test_reboot_disabled_and_bad():
    assert M.reboot_due(at(4, 30), "", 1e6, None)[0] is False
    assert M.reboot_due(at(4, 30), "4h30", 1e6, None)[0] is False


def fake_dkms(installed: set):
    def runner(argv):
        if argv[0] == "dkms":
            mod = argv[argv.index("-m") + 1]
            return (0, f"{mod}/x, 6.8: installed" if mod in installed else "")
        return (0, "filename: xone_dongle.ko")
    return runner


def test_driver_check_after_kernel_update():
    vers = {"xone": "0.5.8", "hid-xpadneo": "0.10.5"}
    ok = M.check_drivers("6.8.0-99-generic", runner=fake_dkms({"xone", "hid-xpadneo"}), versions=vers)
    assert ok["ok"] and ok["detail"] == "built"
    bad = M.check_drivers("6.8.0-99-generic", runner=fake_dkms({"hid-xpadneo"}), versions=vers)
    assert not bad["ok"] and "xone 0.5.8" in bad["detail"]
    none = M.check_drivers("6.8.0-99-generic", runner=fake_dkms(set()), versions={})
    assert none["ok"] and "not installed" in none["detail"]


def test_write_status(tmp_path):
    M.write_status({"ok": False, "kernel": "k", "detail": "d"}, state=str(tmp_path))
    d = json.loads((tmp_path / "xone-status.json").read_text())
    assert d["ok"] is False and "checked" in d
    assert oct(os.stat(tmp_path / "xone-status.json").st_mode & 0o777) == "0o644"


@pytest.fixture
def opt(tmp_path, monkeypatch):
    o = tmp_path / "opt"
    (o / "src" / "installer").mkdir(parents=True)
    (o / "src" / "installer" / "install.sh").write_text("old")
    monkeypatch.setattr(U, "OPT", str(o))
    monkeypatch.setattr(U, "SRC", str(o / "src"))
    monkeypatch.setattr(U, "ROLLBACK", str(o / "rollback" / "src"))
    new = tmp_path / "new"
    (new / "installer").mkdir(parents=True)
    (new / "installer" / "install.sh").write_text("new")
    return o, new


class Runner:
    def __init__(self, fail_on=()):
        self.calls, self.fail_on = [], fail_on

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        key = open(argv[0]).read() if argv[0].endswith("install.sh") else os.path.basename(argv[0])
        return 1 if key in self.fail_on else 0


def test_update_success_saves_rollback_point(opt):
    o, new = opt
    r = Runner()
    assert U.update_from(str(new), runner=r) == 0
    assert (o / "rollback" / "src" / "installer" / "install.sh").read_text() == "old"
    assert ["systemctl", "try-restart", "couch-kiosk.service"] in r.calls


def test_update_failure_rolls_back(opt):
    o, new = opt
    r = Runner(fail_on={"new"})
    assert U.update_from(str(new), runner=r) == 1
    ran = [c[0] for c in r.calls if c[0].endswith("install.sh")]
    assert len(ran) == 2 and ran[0].endswith("new/installer/install.sh") and "couch-rollback-" in ran[1]


def test_update_verify_failure_rolls_back(opt):
    o, new = opt
    r = Runner(fail_on={"moonlight-qt"})          # new moonlight does not even print --version
    assert U.update_from(str(new), runner=r) == 3   # rollback's verify fails too in this fake: reported, not hidden


def test_update_rejects_non_checkout(opt, tmp_path):
    assert U.update_from(str(tmp_path / "nothing"), runner=Runner()) == 2


def test_import_library(tmp_path):
    src = tmp_path / "export"
    (src / "art").mkdir(parents=True)
    (src / "steam-library.json").write_text(json.dumps({"games": [
        {"appid": 504230, "name": "Celeste", "installed": True},
        {"appid": "../../etc", "name": "evil"},
        {"appid": 367520, "name": "Hollow Knight", "installed": False}]}))
    (src / "art" / "504230.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 100)
    (src / "art" / "367520.jpg").write_bytes(b"<html>not a jpeg</html>")
    state = tmp_path / "state"
    assert U.import_library(str(src), state=str(state)) == 0
    lib = json.loads((state / "library.json").read_text())["games"]
    assert [g["appid"] for g in lib] == [504230, 367520]
    assert (state / "art" / "504230.jpg").exists() and not (state / "art" / "367520.jpg").exists()


def test_import_rejects_garbage(tmp_path):
    src = tmp_path / "export"
    src.mkdir()
    assert U.import_library(str(src), state=str(tmp_path / "s")) == 2
    (src / "steam-library.json").write_text("not json")
    assert U.import_library(str(src), state=str(tmp_path / "s")) == 2
