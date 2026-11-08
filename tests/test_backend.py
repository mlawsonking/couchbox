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


def test_hung_child_is_killed_and_reaped_within_5s(env, monkeypatch):
    cfg, *_ = env
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "hang")      # ignores SIGTERM
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.start_stream("Desktop")
    time.sleep(0.2)
    pid = b.stream.proc.pid
    t0 = time.monotonic()
    assert b.stop_stream() is True
    assert time.monotonic() - t0 < S.QUIT_TIMEOUT + 1
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)                                    # gone and reaped: no zombie left behind
    assert b.stream_status() == ("none", None)


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
