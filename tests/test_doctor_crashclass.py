"""couch-doctor additions from the NUC field (2026-11-08): stderr chatter is not an app list, a Windows host that
drops ICMP is awake when Sunshine answers on TCP, a stream that took the kiosk down is red with moonlight's tail,
and a kiosk restart storm turns the services line yellow."""
import json
import os

from test_doctor import doctor, by_name  # noqa: F401  (fixture + helper)

PING_NONE = """PING 10.0.0.9 (10.0.0.9) 56(84) bytes of data.

--- 10.0.0.9 ping statistics ---
20 packets transmitted, 0 received, 100% packet loss, time 3821ms
"""


def write_streams(d, entries):
    os.makedirs(os.path.join(d.SYSROOT, "var/lib/couch"), exist_ok=True)
    with open(os.path.join(d.SYSROOT, "var/lib/couch/streams.jsonl"), "w") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def test_pairing_ignores_stderr_chatter(doctor):
    c = by_name(doctor("good").run_all())["pairing"]
    # the fake prints the NUC's libEGL/Qt stderr lines around the list: 2 apps, not 6
    assert c["status"] == "green" and c["detail"]["apps"] == ["Desktop", "Steam Big Picture"]
    assert "(2 apps)" in c["summary"]


def test_network_icmp_blocked_but_tcp_open_is_green(doctor):
    """BOBBO (Windows) drops ping; Sunshine answers on TCP -> awake, not 'asleep?'."""
    d = doctor("good")
    with open(os.path.join(d.SYSROOT, "ping.txt"), "w") as fh:
        fh.write(PING_NONE)
    c = by_name(d.run_all())["network"]
    assert c["status"] == "green", c
    assert "ICMP blocked" in c["summary"] and "asleep" not in c["summary"]
    assert c["detail"]["tcp"] == {47989: "open", 47984: "open"} and not c["detail"]["link_bad"]
    assert c["detail"]["rung"] == "1080p60-15"


def test_network_no_ping_no_tcp_is_asleep(doctor, monkeypatch):
    d = doctor("good")
    with open(os.path.join(d.SYSROOT, "ping.txt"), "w") as fh:
        fh.write(PING_NONE)

    def timeout(*a, **k):
        raise TimeoutError
    monkeypatch.setattr(d.socket, "create_connection", timeout)
    c = by_name(d.run_all())["network"]
    assert c["status"] == "yellow" and "asleep" in c["summary"]


def test_streams_kiosk_crash_is_red_with_tail(doctor):
    d = doctor("good")
    write_streams(d, [{"app": "Desktop", "duration_s": 600.0, "exit_code": 0},
                      {"app": "Steam Big Picture", "duration_s": 1.3, "exit_code": None, "outcome": "kiosk-crash",
                       "moonlight_tail": ["Qt Warning: The X11 connection broke (error 1)"]}])
    c = by_name(d.run_all())["streams"]
    assert c["status"] == "red"
    assert "kiosk died with it" in c["summary"] and "Steam Big Picture" in c["summary"]
    assert c["detail"]["moonlight_tail"][-1].startswith("Qt Warning: The X11 connection broke")


def test_streams_quick_nonzero_exit_is_red_too(doctor):
    d = doctor("good")
    write_streams(d, [{"app": "Desktop", "duration_s": 0.9, "exit_code": 1}])
    c = by_name(d.run_all())["streams"]
    assert c["status"] == "red" and "moonlight-qt exited 1" in c["summary"]


def test_streams_long_clean_stream_after_a_crash_is_fine(doctor):
    d = doctor("good")
    write_streams(d, [{"app": "Desktop", "duration_s": 1.1, "exit_code": None, "outcome": "kiosk-crash"},
                      {"app": "Desktop", "duration_s": 75.0, "exit_code": 0}])
    c = by_name(d.run_all())["streams"]
    assert c["status"] == "green" and c["summary"].startswith("Desktop 75.0s exit 0")


def test_streams_force_quit_after_a_real_session_is_not_a_failure(doctor):
    """2026-11-09 NUC: a /quit stops moonlight-qt with SIGTERM then SIGKILL (exit -9) after a 65 s stream."""
    d = doctor("good")
    write_streams(d, [{"app": "Desktop", "duration_s": 65.1, "exit_code": -9},
                      {"app": "Desktop", "duration_s": 61.0, "exit_code": -15}])
    c = by_name(d.run_all())["streams"]
    assert c["status"] == "green", c


def test_services_warn_on_kiosk_restart_storm(doctor):
    d = doctor("good")
    with open(os.path.join(d.SYSROOT, "systemctl.state"), "w") as fh:
        fh.write("active enabled masked 3\n")
    c = by_name(d.run_all())["services"]
    assert c["status"] == "yellow" and "restarted 3 times" in c["summary"]
    assert c["detail"]["couch-kiosk.service"]["restarts_since_boot"] == 3
    with open(os.path.join(d.SYSROOT, "systemctl.state"), "w") as fh:
        fh.write("active enabled masked 2\n")
    assert by_name(d.run_all())["services"]["status"] == "green"
