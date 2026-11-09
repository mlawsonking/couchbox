import json
import os
import time

import pytest

from couch import net
from couch.launcher import backend as B
from couch.launcher import config as C
from couch.launcher import state as S
from conftest import REPO

FAKE = os.path.join(REPO, "tests", "fixtures", "bin", "fake-moonlight-qt")


class StaticProber:
    def __init__(self, result):
        self.result = result
        self.kicked = 0

    def is_alive(self):
        return True

    def kick(self):
        self.kicked += 1

    def stop(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(B, "STATE_DIR", str(tmp_path))
    argv_log = tmp_path / "argv.log"
    monkeypatch.setenv("MOONLIGHT_ARGV_LOG", str(argv_log))
    cfg = C.Config(host="gamepc.local", mac="AA:BB:CC:DD:EE:FF", codec="HEVC")
    return cfg, tmp_path, argv_log


def wait_exit(b, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        st = b.stream_status()
        if st[0] != "running":
            return st
        time.sleep(0.05)
    raise AssertionError("stream did not exit")


def test_probe_mapping(env):
    cfg, *_ = env
    for result, host, sun in [("open", True, True), ("refused", True, False), ("timeout", False, False),
                              ("unresolved", False, False), (None, None, None)]:
        b = B.LocalBackend(cfg, prober=StaticProber(result), moonlight=FAKE)
        assert (b.host_up(), b.sunshine_up()) == (host, sun)


def test_stream_argv_and_clean_exit(env, monkeypatch):
    cfg, tmp, argv_log = env
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "ok")
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.start_stream("Steam Big Picture")
    assert wait_exit(b) == ("exited", 0)
    line = argv_log.read_text().strip()
    assert line.startswith("stream gamepc.local Steam Big Picture --quit-after --display-mode borderless")
    assert "--resolution 1920x1080 --fps 60 --bitrate 15000" in line and "--video-codec HEVC" in line
    hist = [json.loads(x) for x in (tmp / "streams.jsonl").read_text().splitlines()]
    assert hist[-1]["app"] == "Steam Big Picture" and hist[-1]["exit_code"] == 0


def test_not_paired_is_detected(env, monkeypatch):
    cfg, *_ = env
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "notpaired")
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.start_stream("Desktop")
    assert wait_exit(b) == ("exited", S.NOT_PAIRED_EXIT)


def test_failure_exit_code(env, monkeypatch):
    cfg, *_ = env
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "fail")
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.start_stream("Desktop")
    assert wait_exit(b) == ("exited", 1)


def read_streams(tmp):
    return [json.loads(x) for x in (tmp / "streams.jsonl").read_text().splitlines()]


def calls(argv_log):
    return argv_log.read_text().splitlines()


XRANDR_FAKE = os.path.join(REPO, "tests", "fixtures", "bin", "fake-xrandr")


def use_fake_x(monkeypatch, tmp):
    monkeypatch.setenv("COUCH_XRANDR", XRANDR_FAKE)
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setenv("FAKE_XRANDR_STATE", str(tmp / "xstate"))
    monkeypatch.setenv("FAKE_XRANDR_LOG", str(tmp / "xcalls"))
    return tmp / "xstate"


def test_graceful_quit_is_exit_0_without_sigkill_and_without_a_sunshine_quit(env, monkeypatch):
    """moonlight-qt's own quit path (SIGTERM): it stops the stream and /cancels Sunshine itself, taking ~3 s on the
    real PC. The grace covers that, so there is no SIGKILL (exit -9) and nothing for us to clean up on Sunshine."""
    cfg, tmp, argv_log = env
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "graceful")
    monkeypatch.setenv("FAKE_TERM_DELAY", "1.0")
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.start_stream("Desktop")
    time.sleep(0.3)
    pid = b.stream.proc.pid
    t0 = time.monotonic()
    assert b.stop_stream() is True
    took = time.monotonic() - t0
    assert 0.9 < took < B.TERM_GRACE                       # it really waited for moonlight's teardown
    rec = read_streams(tmp)[-1]
    assert rec["exit_code"] == 0 and "killed" not in rec and "sunshine_quit" not in rec
    assert not any(c.startswith("quit") for c in calls(argv_log))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_a_grace_shorter_than_the_hosts_teardown_reproduces_the_exit_minus_9(env, monkeypatch):
    """The 2026-11-09 bug in miniature: moonlight-qt needs 1.0 s after SIGTERM, the grace is 0.3 s -> SIGKILL (-9). The
    session is then still ended on Sunshine by `moonlight-qt quit`."""
    cfg, tmp, argv_log = env
    monkeypatch.setattr(B, "TERM_GRACE", 0.3)
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "graceful")
    monkeypatch.setenv("FAKE_TERM_DELAY", "1.0")
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.start_stream("Desktop")
    time.sleep(0.3)
    assert b.stop_stream() is True
    rec = read_streams(tmp)[-1]
    assert rec["exit_code"] == -9 and rec["killed"] is True and rec["sunshine_quit"] == "ok"
    assert calls(argv_log)[-1] == "quit gamepc.local"


def test_grace_is_longer_than_the_host_teardown_that_got_moonlight_killed():
    """2026-11-09: Sunshine answered /cancel ~3 s after SIGTERM and the old 3 s grace SIGKILLed moonlight-qt mid-reply."""
    assert B.TERM_GRACE >= 6.0


def test_hung_child_is_killed_and_reaped_within_the_bound_and_sunshine_is_told(env, monkeypatch):
    cfg, tmp, argv_log = env
    monkeypatch.setattr(B, "TERM_GRACE", 0.5)              # the fake ignores SIGTERM; do not wait the real 7 s
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "hang")      # ignores SIGTERM
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.start_stream("Desktop")
    time.sleep(0.2)
    pid = b.stream.proc.pid
    t0 = time.monotonic()
    assert b.stop_stream() is True
    assert time.monotonic() - t0 < B.STOP_BOUND
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)                                    # gone and reaped: no zombie left behind
    assert b.stream_status() == ("none", None)
    rec = read_streams(tmp)[-1]
    assert rec["exit_code"] == -9 and rec["killed"] is True and rec["sunshine_quit"] == "ok"
    lines = calls(argv_log)                                # the stream first, then `quit <the host moonlight was given>`
    assert lines[0].startswith("stream gamepc.local") and lines[-1] == "quit gamepc.local"


def test_sunshine_quit_failure_is_recorded_and_does_not_block_the_return(env, monkeypatch):
    cfg, tmp, _ = env
    monkeypatch.setattr(B, "TERM_GRACE", 0.3)
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "hang")
    monkeypatch.setenv("FAKE_QUIT_MODE", "fail")
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.start_stream("Desktop")
    time.sleep(0.2)
    assert b.stop_stream() is True
    assert read_streams(tmp)[-1]["sunshine_quit"] == "rc=1"


def test_a_hung_sunshine_quit_is_itself_killed_within_the_bound(env, monkeypatch):
    cfg, tmp, _ = env
    monkeypatch.setattr(B, "TERM_GRACE", 0.3)
    monkeypatch.setattr(B, "SUNSHINE_QUIT_TIMEOUT", 0.5)
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "hang")
    monkeypatch.setenv("FAKE_QUIT_MODE", "hang")           # `moonlight-qt quit` ignores SIGTERM too
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.start_stream("Desktop")
    time.sleep(0.2)
    t0 = time.monotonic()
    assert b.stop_stream() is True
    assert time.monotonic() - t0 < B.STOP_BOUND
    assert read_streams(tmp)[-1]["sunshine_quit"] == "timeout"


def test_the_documented_bound_is_the_real_bound():
    total = B.TERM_GRACE + B.KILL_WAIT + B.REAP_WAIT + B.SUNSHINE_QUIT_TIMEOUT + B.KILL_WAIT + B.REAP_WAIT
    assert B.STOP_BOUND == total
    readme = open(os.path.join(REPO, "README.md"), encoding="utf-8").read()
    assert f"within {B.STOP_BOUND:.0f} s" in readme, "README's hung-child figure is out of date"


def test_quit_uses_the_host_the_daemon_gave_not_the_config(env, monkeypatch):
    cfg, tmp, argv_log = env
    monkeypatch.setattr(B, "TERM_GRACE", 0.3)
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "hang")
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.stream_app = "Desktop"
    b.stream = b.spawn_stream([FAKE, "stream", "otherpc.local", "Desktop", "--resolution", "1920x1080"])
    time.sleep(0.2)
    b.stop_stream()
    assert calls(argv_log)[-1] == "quit otherpc.local"


def test_stream_follows_the_x_mode_and_restores_it_when_it_ends(env, monkeypatch):
    """No window manager: the stream window is as big as the stream, so the X screen must be too (and back after)."""
    cfg, tmp, _ = env
    state = use_fake_x(monkeypatch, tmp)
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "slow")      # runs ~1 s then exits 0
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.start_stream("Desktop")
    assert state.read_text().strip() == "1920x1080 60.00"  # the stream is 1920x1080x60: screen matches while it runs
    assert wait_exit(b) == ("exited", 0)
    assert state.read_text().strip() == "3840x2160 30.00"  # and goes back when the stream ends by itself


def test_forced_stop_restores_the_x_mode_too(env, monkeypatch):
    cfg, tmp, _ = env
    state = use_fake_x(monkeypatch, tmp)
    monkeypatch.setattr(B, "TERM_GRACE", 0.3)
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "hang")
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.start_stream("Desktop")
    time.sleep(0.2)
    assert state.read_text().strip() == "1920x1080 60.00"
    assert b.stop_stream() is True
    assert state.read_text().strip() == "3840x2160 30.00"


def test_a_screen_already_on_the_stream_mode_is_not_touched(env, monkeypatch):
    cfg, tmp, _ = env
    state = use_fake_x(monkeypatch, tmp)
    state.write_text("1920x1080 60.00\n")
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "ok")
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.start_stream("Desktop")
    assert wait_exit(b) == ("exited", 0)
    log = tmp / "xcalls"
    assert not any("--mode" in ln for ln in log.read_text().splitlines())   # no mode set = no TV re-sync per stream


def test_pairing(env, monkeypatch):
    cfg, tmp, argv_log = env
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "slow")
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    pin = b.pair_start()
    assert len(pin) == 4 and pin.isdigit()
    assert b.pair_status() == ("waiting", "")
    time.sleep(1.3)
    assert b.pair_status() == ("paired", "")
    assert f"pair gamepc.local --pin {pin}" in argv_log.read_text()


def test_pairing_failure_detail(env, monkeypatch):
    cfg, *_ = env
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "fail")
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.pair_start()
    time.sleep(0.3)
    st, detail = b.pair_status()
    assert st == "failed" and "stream failed" in detail


def test_wake_sends_to_config_mac(env, monkeypatch):
    cfg, *_ = env
    sent = []
    monkeypatch.setattr(net, "send_wol", lambda mac: sent.append(mac))
    p = StaticProber("timeout")
    b = B.LocalBackend(cfg, prober=p, moonlight=FAKE)
    b.wake()
    assert sent == ["AA:BB:CC:DD:EE:FF"] and p.kicked == 1
    b.cfg = C.Config(mac="")
    b.wake()
    assert sent == ["AA:BB:CC:DD:EE:FF"]


def test_prober_keeps_last_ip_on_dns_blip():
    answers = iter(["10.1.2.3", None])
    p = B.Prober("gamepc.local", interval=0.01, resolve=lambda h: next(answers, None),
                 probe=lambda ip: "open" if ip == "10.1.2.3" else "timeout")
    p.start()
    time.sleep(0.15)
    p.stop()
    assert p.ip == "10.1.2.3" and p.result == "open"
