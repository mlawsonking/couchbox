import json
import os

import pytest

import make_sysroot
from conftest import REPO, load_script

FIXBIN = os.path.join(REPO, "tests", "fixtures", "bin")


@pytest.fixture
def doctor(tmp_path, monkeypatch):
    def _make(variant):
        root = tmp_path / variant
        make_sysroot.build(str(root), variant)
        monkeypatch.setenv("COUCH_SYSROOT", str(root))
        monkeypatch.setenv("PATH", FIXBIN + os.pathsep + os.environ["PATH"])
        mod = load_script("couch/doctor/couch-doctor", "couch_doctor")
        mod.SYSROOT = str(root)
        return mod
    return _make


def by_name(report):
    return {c["check"]: c for c in report["checks"]}


def test_good_is_green(doctor):
    d = doctor("good")
    r = d.run_all()
    assert r["overall"] == "green", r
    c = by_name(r)
    hdmi = [x for x in c["display"]["detail"]["connectors"] if x["status"] == "connected"]
    assert [x["connector"] for x in hdmi] == ["card1-HDMI-A-1"] and hdmi[0]["preferred_mode"] == "3840x2160"
    assert c["decode"]["detail"]["recommended_codec"] == "HEVC"
    assert c["decode"]["detail"]["decode_ceiling"] == "4k60"
    paths = c["gamepad"]["detail"]["paths"]
    assert set(paths) == {"dongle", "bluetooth", "wired"}  # all three gamepad paths reported
    assert paths["dongle"]["adapter_plugged"] and paths["dongle"]["loaded"]
    assert paths["bluetooth"]["adapters"] == ["hci0"]
    assert paths["wired"]["driver"] == "xone_wired"
    assert c["gamepad"]["detail"]["sdl"]["status"] == "skip"


@pytest.mark.parametrize("variant,check,status,words", [
    ("nodisplay", "display", "red", "no TV detected"),
    ("nopad", "gamepad", "yellow", "turn the controller on"),
    ("dongle_nodriver", "gamepad", "red", "xone_dongle is not loaded"),
    ("h264only", "decode", "yellow", "H264"),
    ("nogpu", "decode", "red", "no GPU render node"),
])
def test_failure_variants(doctor, variant, check, status, words):
    r = doctor(variant).run_all()
    c = by_name(r)[check]
    assert c["status"] == status, c
    assert words in c["summary"]


def test_h264_only_ceiling(doctor):
    c = by_name(doctor("h264only").run_all())["decode"]["detail"]
    assert c["decode_ceiling"] == "1080p60" and c["recommended_codec"] == "H.264"


def test_services_red_when_kiosk_down(doctor, tmp_path):
    d = doctor("good")
    with open(os.path.join(d.SYSROOT, "systemctl.state"), "w") as fh:
        fh.write("failed enabled enabled\n")
    c = by_name(d.run_all())["services"]
    assert c["status"] == "red"
    assert "getty@tty1 not masked" in c["summary"]


def test_firstboot_pending_is_red(doctor):
    d = doctor("good")
    os.makedirs(os.path.join(d.SYSROOT, "var/lib/couch"), exist_ok=True)
    open(os.path.join(d.SYSROOT, "var/lib/couch/firstboot-pending"), "w").close()
    assert by_name(d.run_all())["services"]["status"] == "red"


def test_crashing_check_does_not_hide_others(doctor, monkeypatch):
    d = doctor("good")

    def boom():
        raise RuntimeError("kaboom")
    monkeypatch.setattr(d, "CHECKS", [boom, d.check_display])
    r = d.run_all()
    assert r["overall"] == "red"
    assert by_name(r)["display"]["status"] == "green"


def test_main_json_and_exit_code(doctor, capsys):
    d = doctor("nodisplay")
    rc = d.main(["--json", "--sysroot", d.SYSROOT])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["overall"] == "red"


def test_parse_vainfo_ignores_encode_only():
    d = load_script("couch/doctor/couch-doctor", "couch_doctor2")
    info = d.parse_vainfo("      VAProfileHEVCMain : VAEntrypointEncSlice\n      VAProfileH264High : VAEntrypointVLD\n")
    assert info["caps"]["hevc"] is False and info["caps"]["h264"] is True
