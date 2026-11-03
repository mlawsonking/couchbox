import json
import os
import collections

import pytest

import make_sysroot
from conftest import REPO, load_script

Usage = collections.namedtuple("Usage", "total used free")
FIXBIN = os.path.join(REPO, "tests", "fixtures", "bin")


@pytest.fixture
def doctor(tmp_path, monkeypatch):
    def _make(variant):
        root = tmp_path / variant
        make_sysroot.build(str(root), variant)
        monkeypatch.setenv("COUCH_SYSROOT", str(root))
        monkeypatch.setenv("COUCH_CONFIG", str(root / "etc/couch/couch.toml"))
        monkeypatch.setenv("COUCH_MOONLIGHT", os.path.join(FIXBIN, "fake-moonlight-qt"))
        monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "list")
        monkeypatch.setenv("FAKE_MOONLIGHT_APPS", "Desktop\nSteam Big Picture")
        monkeypatch.setenv("PATH", FIXBIN + os.pathsep + os.environ["PATH"])
        mod = load_script("couch/doctor/couch-doctor", "couch_doctor")
        mod.SYSROOT = str(root)

        class Conn:                      # Sunshine "listening" on the fake workstation
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        monkeypatch.setattr(mod.socket, "create_connection", lambda *a, **k: Conn())
        monkeypatch.setattr(mod.shutil, "disk_usage", lambda path: Usage(100_000_000_000, 40_000_000_000, 60_000_000_000))
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


def test_v1_matrix_has_every_check(doctor):
    r = doctor("good").run_all()
    assert [c["check"] for c in r["checks"]] == ["display", "gamepad", "decode", "network", "sunshine", "pairing",
                                                 "services", "drivers", "disk", "streams"]


def test_network_good_wired(doctor):
    c = by_name(doctor("good").run_all())["network"]
    assert c["status"] == "green", c
    d = c["detail"]
    assert d["path"] == "wired" and d["ping"]["loss_pct"] == 0 and d["ping"]["jitter_ms"] == 0.37
    assert d["rung"] == "1080p60-15" and not d["link_bad"]


def test_network_jittery_link_flags_badge(doctor):
    c = by_name(doctor("jittery").run_all())["network"]
    assert c["status"] == "yellow" and c["detail"]["link_bad"] and "shaky" in c["summary"]


def test_network_down(doctor):
    assert by_name(doctor("nonet").run_all())["network"]["status"] == "red"


def test_measure_records_rung(doctor, monkeypatch):
    d = doctor("good")
    monkeypatch.setenv("FAKE_IPERF_MBPS", "70")
    d.MEASURE = True
    c = by_name(d.run_all())["network"]
    assert c["detail"]["throughput_mbps"] == 70.0 and c["detail"]["rung"] == "1440p60-30"
    link = json.load(open(os.path.join(d.SYSROOT, "var/lib/couch/link.json")))
    assert link["rung"] == "1440p60-30"


@pytest.mark.parametrize("mbps,jitter,loss,rung", [
    (None, None, None, "1080p60-15"), (200, 2, 0, "4k60-40"), (45, 2, 0, "1080p60-20"), (31, 2, 0, "1080p60-15"),
    (12, 2, 0, "720p60-10"), (200, 25, 0, "1440p60-30"), (25, 2, 3.0, "720p60-10")])
def test_recommend_rung(mbps, jitter, loss, rung):
    d = load_script("couch/doctor/couch-doctor", "couch_doctor3")
    assert d.recommend_rung(mbps, jitter, loss) == rung


def test_pairing_states(doctor, monkeypatch):
    d = doctor("good")
    assert by_name(d.run_all())["pairing"]["status"] == "green"
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "notpaired")
    assert by_name(d.run_all())["pairing"]["status"] == "red"


def test_sunshine_down_but_host_up(doctor, monkeypatch):
    d = doctor("good")

    def refused(*a, **k):
        raise ConnectionRefusedError
    monkeypatch.setattr(d.socket, "create_connection", refused)
    assert by_name(d.run_all())["sunshine"]["status"] == "red"


def test_xone_status_and_streams(doctor):
    d = doctor("good")
    os.makedirs(os.path.join(d.SYSROOT, "var/lib/couch"), exist_ok=True)
    with open(os.path.join(d.SYSROOT, "var/lib/couch/xone-status.json"), "w") as fh:
        json.dump({"ok": False, "kernel": "6.8.0-99-generic", "detail": "xone not built"}, fh)
    with open(os.path.join(d.SYSROOT, "var/lib/couch/streams.jsonl"), "w") as fh:
        for i, code in enumerate([0, 0, 1, 0]):
            fh.write(json.dumps({"app": f"A{i}", "duration_s": 60 * i, "exit_code": code}) + "\n")
    c = by_name(d.run_all())
    assert c["drivers"]["status"] == "red" and "6.8.0-99" in c["drivers"]["summary"]
    assert len(c["streams"]["detail"]["last"]) == 3 and c["streams"]["summary"].startswith("A3")


def test_parse_ping_math():
    d = load_script("couch/doctor/couch-doctor", "couch_doctor4")
    import make_sysroot as m
    pg = d.parse_ping(m.PING_BAD)
    assert pg["sent"] == 5 and pg["received"] == 4 and pg["loss_pct"] == 20.0
    assert pg["jitter_ms"] == round((44 + 43 + 56) / 3, 2)


@pytest.mark.parametrize("free,status", [(60_000_000_000, "green"), (10_000_000_000, "yellow"),
                                         (900_000_000, "red")])
def test_disk_thresholds(doctor, monkeypatch, free, status):
    d = doctor("good")
    monkeypatch.setattr(d.shutil, "disk_usage", lambda path: Usage(100_000_000_000, 100_000_000_000 - free, free))
    assert by_name(d.run_all())["disk"]["status"] == status
