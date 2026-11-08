"""The kiosk-crash class (2026-11-05/08): the launcher died under a stream, so nothing recorded it. The in-flight
marker written at spawn time lets the next launcher start record it with moonlight's last words."""
import json
import os
import time

import pytest

from couch.launcher import backend as B
from couch.launcher import config as C
from conftest import REPO
from test_backend import StaticProber, wait_exit

FAKE = os.path.join(REPO, "tests", "fixtures", "bin", "fake-moonlight-qt")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(B, "STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MOONLIGHT_ARGV_LOG", str(tmp_path / "argv.log"))
    return C.Config(host="gamepc.local", mac="AA:BB:CC:DD:EE:FF", codec="HEVC"), tmp_path


def test_inflight_marker_written_then_cleared(env, monkeypatch):
    cfg, tmp = env
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "slow")
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.start_stream("Desktop")
    m = json.loads((tmp / B.INFLIGHT).read_text())
    assert m["app"] == "Desktop" and m["start_epoch"] > 0 and m["moonlight_log_offset"] >= 0
    assert wait_exit(b) == ("exited", 0)
    assert not (tmp / B.INFLIGHT).exists()                 # a recorded exit removes the marker
    assert B.finalize_crashed_stream(str(tmp), str(tmp)) is None


def test_finalize_turns_a_leftover_marker_into_a_kiosk_crash_entry(env):
    cfg, tmp = env
    (tmp / "moonlight.log").write_text("old line from an earlier stream\n")
    off = (tmp / "moonlight.log").stat().st_size
    (tmp / B.INFLIGHT).write_text(json.dumps({"app": "Steam Big Picture", "start_epoch": time.time() - 1.4,
                                              "moonlight_log_offset": off}))
    (tmp / "streaming").write_text("1")
    with (tmp / "moonlight.log").open("a") as fh:
        fh.write("00:00:01 - SDL Info (0): Using VAAPI accelerated renderer on x11\n")
        fh.write("00:00:01 - Qt Warning: The X11 connection broke (error 1). Did the X11 server die?\n")
    e = B.finalize_crashed_stream(str(tmp), str(tmp))
    assert e["outcome"] == B.KIOSK_CRASH and e["app"] == "Steam Big Picture" and e["exit_code"] is None
    assert 1.0 <= e["duration_s"] < 10 and e["moonlight_tail"][-1].endswith("Did the X11 server die?")
    assert all("old line" not in ln for ln in e["moonlight_tail"])     # only what moonlight wrote during that stream
    hist = [json.loads(x) for x in (tmp / "streams.jsonl").read_text().splitlines()]
    assert hist[-1] == e
    assert not (tmp / B.INFLIGHT).exists() and not (tmp / "streaming").exists()   # marker + stale flag cleared
    assert B.finalize_crashed_stream(str(tmp), str(tmp)) is None                  # idempotent


def test_finalize_without_marker_only_clears_the_stale_flag(env):
    _, tmp = env
    (tmp / "streaming").write_text("1")
    assert B.finalize_crashed_stream(str(tmp), str(tmp)) is None
    assert not (tmp / "streaming").exists() and not (tmp / "streams.jsonl").exists()
