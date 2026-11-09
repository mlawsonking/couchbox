"""(1) A failed start (moonlight-qt keeps running behind its error dialog) is detected from its log and handled as a
failed launch; (2) a Sunshine session this box started and lost track of is offered for resume for a while, then ended
once. Both were found on the real TV on 2026-11-09."""
import json
import os
import time

import pytest

from couch.launcher import app as A
from couch.launcher import backend as B
from couch.launcher import config as C
from couch.launcher import input as inp
from couch.launcher import logwatch as LW
from couch.launcher import resume as RS
from couch.launcher import state as S
from fakes import FakeBackend
from test_backend import FAKE, FakeSunshine, StaticProber  # noqa: F401  (fixtures below reuse them)

# ---- lines captured from the NUC's /var/log/couch/moonlight.log (2026-11-09) -----------------------------------
NO_VIDEO = ("00:00:17 - SDL Error (0): Connection terminated: -100\n"
            "00:00:17 - Qt Critical: No video received from host.\n\n"
            "Check your firewall and port forwarding rules for port(s): UDP 47998, UDP 48000\n")
HEALTHY_NOISE = """00:00:01 - SDL Error (0): pl_vk_inst_create() failed
00:00:01 - SDL Critical (0): libplacebo: Failed initializing vulkan instance
00:00:01 - SDL Critical (0): libplacebo: Failed creating instance: VK_ERROR_INCOMPATIBLE_DRIVER
00:00:01 - SDL Warn (0): Failed to create test window with platform flags: Installed Vulkan doesn't implement the VK_KHR_surface extension
00:00:01 - SDL Warn (0): SDL_CreateWindow() failed with platform flags: Installed Vulkan doesn't implement the VK_KHR_surface extension
00:00:07 - SDL Warn (0): Unable to set render thread to high priority: setpriority() failed
00:00:07 - SDL Info (0): Sending RFI request for unrecoverable frame 23252
00:00:07 - SDL Info (0): Invalidate reference frame request sent (23252 to 23252)
00:00:07 - SDL Info (0): Waiting for RFI frame
00:00:08 - SDL Info (0): Unable to recover audio data block 63444 to 63447 (2+0=2 received < 4 needed)
00:00:08 - SDL Info (0): Network dropped audio data (expected 63446, but received 63448)
00:01:02 - SDL Info (0): Control stream connection failed: 4
00:00:02 - Qt Warning: Could not find the Qt platform plugin "wayland" in ""
00:00:05 - Qt Warning: qrc:/gui/main.qml:13:1: QML ApplicationWindow: ToolTip must be attached to an Item
00:00:00 - Qt Warning: QQmlEngine::setContextForObject(): Object already has a QQmlContext
00:04:00 - Qt Warning: The X11 connection broke: I/O error (code 1)
"""
LAUNCH = ('00:00:02 - Qt Info: Executing request: "https://gamepc.local:47984/launch?uniqueid=0123456789abcdef&'
          'uuid=0b79&appid=881448767&mode=1920x1080x60&additionalStates=1&sops=1&rikey=REDACTED"\n')


# ------------------------------------------------------------------ logwatch
def test_the_real_no_video_failure_is_found_with_its_explanation():
    got = LW.find_failure(NO_VIDEO)
    assert got.startswith("Connection terminated: -100")
    only = LW.find_failure("00:00:17 - Qt Critical: No video received from host.\n\nCheck your firewall and port forwarding "
                           "rules for port(s): UDP 47998, UDP 48000\n00:00:17 - SDL Info (0): Quit event received\n")
    assert only == ("No video received from host. Check your firewall and port forwarding rules for port(s): "
                    "UDP 47998, UDP 48000")


@pytest.mark.parametrize("line", [
    "00:00:09 - SDL Error (0): Starting RTSP handshake failed: Error -1",
    "00:00:09 - Qt Warning: Failed to connect to host",
    "00:00:09 - SDL Error (0): Unable to connect to the host",
    "00:00:09 - Qt Critical: Host is not reachable",
    "00:00:09 - Qt Warning: Connection timed out",
    "00:00:09 - Qt Critical: Unable to initialize video decoder",
])
def test_other_fatal_connection_messages_are_found(line):
    assert LW.find_failure(line + "\n")


def test_a_healthy_streams_error_level_lines_are_never_a_failure():
    """The Vulkan renderer fails first on this NUC and EGL takes over; every quit logs 'Control stream connection
    failed'; loss/RFI lines are normal. None of that may count (the strings are only read in the first seconds, and
    only these specific messages count)."""
    assert LW.find_failure(HEALTHY_NOISE) is None
    for line in HEALTHY_NOISE.splitlines():
        assert LW.find_failure(line + "\n") is None, line


def test_app_id_is_read_from_the_launch_line_only():
    assert LW.find_app_id(LAUNCH) == "881448767"
    assert LW.find_app_id("00:00:02 - Qt Info: something appid=5 but not a launch\n") is None


def test_scanner_reads_complete_lines_once_and_from_its_start_offset(tmp_path):
    log = tmp_path / "moonlight.log"
    log.write_text("old noise\n00:00:01 - Qt Critical: from an earlier stream\n")
    sc = LW.LogScanner(str(log), LW.size(str(log)))
    sc.poll()
    assert sc.failure is None and sc.app_id is None                 # an earlier stream's lines are never read
    with open(log, "a") as fh:
        fh.write(LAUNCH + "00:00:17 - Qt Critical: No video rec")      # a half-written line
    sc.poll()
    assert sc.app_id == "881448767" and sc.failure is None
    with open(log, "a") as fh:
        fh.write("eived from host.\n")
    sc.poll()
    assert sc.failure == "No video received from host."


# ------------------------------------------------------------------ Machine: a failed start returns to the tiles
class WatchedBackend(FakeBackend):
    """FakeBackend that reports a fatal connection message from `fail_at` seconds after the stream starts."""

    def __init__(self, clock, fail_at=3.0, why="No video received from host."):
        super().__init__(host_up=True, sunshine_up=True)
        self.clock, self.fail_at, self.why, self.started, self.asked = clock, fail_at, why, 0.0, 0

    def start_stream(self, app):
        super().start_stream(app)
        self.started = self.clock()

    def stream_failure(self):
        self.asked += 1
        return self.why if self.clock() - self.started >= self.fail_at else None


def run_machine(b, m, until, step=0.25, t0=0.0):
    t = t0
    while t < until:
        m.tick(t)
        t += step
    return t


def test_a_failed_start_is_stopped_counted_retried_and_on_the_third_strike_shown_with_the_real_reason():
    clock = {"t": 0.0}
    b = WatchedBackend(lambda: clock["t"])
    m = S.Machine(b, host="bobbo.local", mac="AA:BB:CC:DD:EE:FF")
    m.launch("Desktop", 0.0)
    starts = []
    t = 0.0
    while t < 120 and m.state != S.LOST:
        clock["t"] = t
        before = len([c for c in b.calls if c == ("stream", "Desktop")])
        m.tick(t)
        if len([c for c in b.calls if c == ("stream", "Desktop")]) > before:
            starts.append(t)
        t += 0.25
    assert m.state == S.LOST and m.failure.reason == "STREAM_FAILED" and m.strikes == S.MAX_STRIKES
    assert "No video received from host." in m.failure.parent and "Desktop" in m.failure.parent
    assert len(starts) == 3 and b.stopped == 3                        # each failed attempt went through the stop path
    gaps = [b2 - a for a, b2 in zip(starts, starts[1:])]
    assert all(3.0 <= g <= 3.0 + S.RETRY_PAUSE + 1.0 for g in gaps)  # detected at ~3 s, then RETRY_PAUSE, then again


def test_the_log_is_never_consulted_after_the_stream_is_established():
    clock = {"t": 0.0}
    b = WatchedBackend(lambda: clock["t"], fail_at=S.FAIL_WATCH + 5)   # a "failure" line that appears late
    m = S.Machine(b, host="bobbo.local")
    m.launch("Desktop", 0.0)
    t = 0.0
    while t < S.FAIL_WATCH + 40:
        clock["t"] = t
        m.tick(t)
        t += 0.25
    assert m.state == S.STREAMING and b.stopped == 0 and m.strikes == 0
    asked_at_cutoff = b.asked
    clock["t"] = S.FAIL_WATCH + 60
    m.tick(S.FAIL_WATCH + 60)
    assert b.asked == asked_at_cutoff                                  # not asked any more
    assert asked_at_cutoff <= S.FAIL_WATCH / S.WATCH_EVERY + 2         # and never more often than WATCH_EVERY


def test_a_stream_that_exits_non_zero_still_gives_the_exit_code_as_the_reason():
    b = FakeBackend(host_up=True, sunshine_up=True)
    m = S.Machine(b, host="bobbo.local")
    m.launch("Desktop", 0.0)
    t = 0.0
    for _ in range(3):
        m.tick(t)
        b.stream = ("exited", 1)
        t += 1
        m.tick(t)
        t += S.RETRY_PAUSE + 0.1
        m.tick(t)
    assert m.state == S.LOST and "exit code 1" in m.failure.parent


# ------------------------------------------------------------------ Backend + the fake moonlight-qt
@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(B, "STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MOONLIGHT_ARGV_LOG", str(tmp_path / "argv.log"))
    monkeypatch.setenv("FAKE_APPID", "881448767")
    cfg = C.Config(host="gamepc.local", mac="AA:BB:CC:DD:EE:FF", codec="HEVC")
    return cfg, tmp_path


def streams(tmp):
    return [json.loads(x) for x in (tmp / "streams.jsonl").read_text().splitlines()]


def wait_for(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_a_connection_that_failed_behind_moonlights_error_dialog_is_detected_and_stopped(env, monkeypatch):
    cfg, tmp = env
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "connfail")
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.start_stream("Desktop")
    why = wait_for(b.stream_failure)
    assert why.startswith("Connection terminated: -100")
    assert b.stream.poll() is None                                     # it is still running behind its dialog
    assert b.stop_stream() is True                                     # the normal stop path
    rec = streams(tmp)[-1]
    assert rec["exit_code"] == 0 and "killed" not in rec and rec["failure"].startswith("Connection terminated")
    assert RS.load_ids(str(tmp)) == {"881448767": "Desktop"}           # the id was learned on the way


def test_a_healthy_start_with_noisy_error_level_lines_is_left_alone(env, monkeypatch):
    cfg, tmp = env
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "noisy")
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.start_stream("Desktop")
    deadline = time.time() + 1.5
    while time.time() < deadline:
        assert b.stream_failure() is None
        time.sleep(0.1)
    assert b.stop_stream() is True and "failure" not in streams(tmp)[-1]


def test_end_to_end_three_failed_starts_end_on_the_failure_screen_with_the_real_reason(env, monkeypatch):
    cfg, tmp = env
    monkeypatch.setattr(S, "RETRY_PAUSE", 0.1)
    monkeypatch.setattr(S, "WATCH_EVERY", 0.05)
    monkeypatch.setenv("FAKE_MOONLIGHT_MODE", "connfail")
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    b.prober.result = "open"
    m = S.Machine(b, host="gamepc.local", mac=cfg.mac)
    m.launch("Desktop", time.monotonic())
    end = time.time() + 20
    while m.state != S.LOST and time.time() < end:
        m.tick(time.monotonic())
        time.sleep(0.02)
    assert m.state == S.LOST and m.failure.reason == "STREAM_FAILED"
    assert "Connection terminated: -100" in m.failure.parent
    assert len([s for s in streams(tmp) if s.get("failure")]) == 3     # three attempts, each stopped cleanly
    assert b.stream is None


# ------------------------------------------------------------------ Resume policy (pure)
def test_a_stranded_session_is_confirmed_badged_for_the_window_then_due():
    r = RS.Resume(window_s=900)
    r.observe(5, "Desktop", 100.0)
    assert r.badge(100.0) is None                                      # seen once: a /cancel may still be finishing
    r.observe(5, "Desktop", 100.0 + RS.CONFIRM_S)
    assert r.badge(106.0) == "Desktop"
    assert not r.due(106.0, idle=True)
    assert r.badge(100 + 899) == "Desktop" and r.badge(100 + 900) is None
    assert not r.due(100 + 899, idle=True)
    assert r.due(100 + 900, idle=True) and not r.due(100 + 900, idle=False)    # never while a stream runs/starts
    assert r.age(100 + 900) == 900


def test_window_zero_ends_it_as_soon_as_it_is_confirmed():
    r = RS.Resume(window_s=0)
    r.observe(5, "Desktop", 0.0)
    assert not r.due(1.0, idle=True)                                   # still needs the second sighting
    r.observe(5, "Desktop", RS.CONFIRM_S)
    assert r.badge(RS.CONFIRM_S) is None and r.due(RS.CONFIRM_S, idle=True)


def test_ended_once_never_twice_and_a_different_session_starts_over():
    r = RS.Resume(window_s=10)
    r.observe(5, "Desktop", 0.0)
    r.observe(5, "Desktop", 6.0)
    assert r.due(11.0, idle=True)
    r.mark_ended()
    r.observe(5, "Desktop", 12.0)                                      # a stale answer still says 5: not tracked again
    r.observe(5, "Desktop", 30.0)
    assert not r.due(60.0, idle=True) and r.badge(30.0) is None
    r.observe(9, "Retro", 100.0)
    assert r.game == 9 and not r.confirmed


def test_no_session_or_not_ours_is_ignored_and_resets():
    r = RS.Resume(window_s=10)
    r.observe(5, "Desktop", 0.0)
    r.observe(0, None, 1.0)
    assert r.game is None
    r.observe(5, None, 2.0)                                            # a session of some other client: never tracked
    assert r.game is None and not r.due(999.0, idle=True)


def test_id_map_and_last_stream_app(tmp_path):
    d = str(tmp_path)
    assert RS.load_ids(d) == {} and RS.last_stream_app(d) is None
    assert RS.learn_id(d, "881448767", "Desktop") is True and RS.learn_id(d, "881448767", "Desktop") is False
    RS.learn_id(d, "469093302", "Retro")
    assert RS.load_ids(d) == {"881448767": "Desktop", "469093302": "Retro"}
    (tmp_path / "streams.jsonl").write_text(json.dumps({"app": "Desktop", "exit_code": 0}) + "\n" +
                                            json.dumps({"app": "Retro", "outcome": "kiosk-crash"}) + "\n" +
                                            json.dumps({"app": "Desktop", "outcome": "stranded-ended"}) + "\n")
    assert RS.last_stream_app(d) == "Retro"                            # a crash entry counts, bookkeeping does not
    (tmp_path / "apps.json").write_text("{not json")
    (tmp_path / RS.APP_IDS).write_text("[1,2]")
    assert RS.load_ids(d) == {}


# ------------------------------------------------------------------ App: badge, resume, end
class StrandedBackend(FakeBackend):
    def __init__(self, owner="Desktop"):
        super().__init__(host_up=True, sunshine_up=True)
        self.owner, self.ended = owner, []

    def owned_game_app(self, game):
        return self.owner if game == 5 else None

    def end_stranded(self, app, age_s):
        self.ended.append((app, round(age_s)))
        return "ok"


def mk_app(window_min=15, owner="Desktop"):
    cfg = C.Config(mac="AA:BB:CC:DD:EE:FF", resume_window_min=window_min,
                   tiles=[C.Tile("Steam Big Picture", "Steam Big Picture"), C.Tile("Desktop", "Desktop")])
    b = StrandedBackend(owner)
    return A.App(cfg, b, now=0), b


def badges(a, now):
    return {t.name: t.badge for t in a.view(now).tiles}


def test_the_tile_of_a_stranded_session_shows_resume_and_a_press_resumes_it_as_a_normal_stream():
    a, b = mk_app()
    a.resume_tick(0.0, 5)
    a.resume_tick(6.0, 5)
    assert badges(a, 6.0) == {"Steam Big Picture": "", "Desktop": "Resume"}
    a.grid.focus = 1
    a.handle([inp.SELECT], 7.0)                                        # the normal launch path
    assert a.m.state != S.IDLE and a.m.app == "Desktop"
    assert a.resume_tick(7.5, 5) is False and badges(a, 7.5)["Desktop"] == ""   # our stream owns it now
    assert a.resume_tick(2000.0, 5) is False and b.ended == []                  # and it is never ended under us


def test_the_stranded_session_is_ended_once_when_the_window_passes_with_no_stream_started():
    a, b = mk_app(window_min=15)
    for t in range(0, 905, 5):
        a.resume_tick(float(t), 5)
    assert b.ended == [("Desktop", 900)]                               # once, with the app and its age
    a.resume_tick(1000.0, 5)
    a.resume_tick(1100.0, 5)
    assert len(b.ended) == 1


def test_a_stream_being_started_at_the_end_of_the_window_is_not_killed():
    a, b = mk_app(window_min=1)
    a.resume_tick(0.0, 5)
    a.resume_tick(6.0, 5)
    a.handle([inp.SELECT], 30.0)                                       # the player presses a tile just before the end
    assert a.m.state != S.IDLE
    assert a.resume_tick(61.0, 5) is False and a.resume_tick(500.0, 5) is False and b.ended == []


def test_window_zero_in_the_config_ends_it_at_once_and_a_config_reload_changes_the_window():
    a, b = mk_app(window_min=0)
    a.resume_tick(0.0, 5)
    assert a.resume_tick(6.0, 5) is True and b.ended == [("Desktop", 6)]
    a2, b2 = mk_app(window_min=15)
    a2.resume_tick(0.0, 5)
    a2.resume_tick(6.0, 5)
    a2.apply_config(C.Config(mac="AA:BB:CC:DD:EE:FF", resume_window_min=1, tiles=list(a2.cfg.tiles)))
    assert a2.resume_tick(61.0, 5) is True and b2.ended == [("Desktop", 61)]


def test_a_session_that_is_not_ours_is_never_touched():
    a, b = mk_app(owner=None)
    for t in range(0, 2000, 5):
        a.resume_tick(float(t), 5)
    assert b.ended == [] and set(badges(a, 1000.0).values()) == {""}


def test_a_session_that_ended_by_itself_drops_the_badge():
    a, b = mk_app()
    a.resume_tick(0.0, 5)
    a.resume_tick(6.0, 5)
    assert badges(a, 6.0)["Desktop"] == "Resume"
    a.resume_tick(10.0, 0)                                             # Sunshine reports no session any more
    assert badges(a, 10.0)["Desktop"] == ""


def test_config_resume_window_default_and_validation(tmp_path):
    hdr = '[host]\nname = "gamepc.local"\nmac = "AA:BB:CC:DD:EE:FF"\n[stream]\n'
    none = str(tmp_path / "none.json")
    f = tmp_path / "couch.toml"
    assert C.Config().resume_window_min == 15
    f.write_text(hdr + "resume_window_min = 0\n")
    assert C.load(str(f), library=none).resume_window_min == 0
    f.write_text(hdr + "resume_window_min = 5000\n")
    cfg = C.load(str(f), library=none)
    assert cfg.resume_window_min == 15 and "resume_window_min" in cfg.error


# ------------------------------------------------------------------ Backend: ownership + ending, against a fake Sunshine
def test_owned_game_app_requires_the_id_to_be_the_one_of_our_last_stream(env):
    cfg, tmp = env
    RS.learn_id(str(tmp), "881448767", "Desktop")
    RS.learn_id(str(tmp), "469093302", "Retro")
    (tmp / "streams.jsonl").write_text(json.dumps({"app": "Desktop", "exit_code": None, "outcome": "kiosk-crash"}) + "\n")
    b = B.LocalBackend(cfg, prober=StaticProber("open"), moonlight=FAKE)
    assert b.owned_game_app(881448767) == "Desktop"
    assert b.owned_game_app(469093302) is None                         # known, but not what we last streamed
    assert b.owned_game_app(12345) is None and b.owned_game_app(0) is None and b.owned_game_app(None) is None


def test_end_stranded_quits_verifies_and_records_the_outcome(env, monkeypatch):
    cfg, tmp = env
    state = {"game": 881448767}
    srv = FakeSunshine(lambda: state["game"])
    try:
        monkeypatch.setattr(B, "SUNSHINE_PORT", srv.port)
        p = StaticProber("open")
        p.ip = "127.0.0.1"
        b = B.LocalBackend(cfg, prober=p, moonlight=FAKE)
        orig = b.sunshine_quit
        b.sunshine_quit = lambda t: (state.update(game=0), orig(t))[1]
        assert b.end_stranded("Desktop", 905.0) == "ok"
    finally:
        srv.close()
    e = streams(tmp)[-1]
    assert e["outcome"] == "stranded-ended" and e["app"] == "Desktop" and e["age_s"] == 905.0
    assert e["sunshine_quit"] == "ok" and e["sunshine_game_after"] == 0 and e["exit_code"] is None
    assert open(tmp / "argv.log").read().splitlines()[-1] == "quit gamepc.local"


# ------------------------------------------------------------------ the watcher thread
def test_session_watcher_probes_at_start_on_kick_and_only_while_active():
    seen = []
    state = {"active": True, "game": 7}

    def probe():
        seen.append(time.monotonic())
        return state["game"]

    w = RS.SessionWatcher(probe, lambda: state["active"], track_every=0.05)
    w.start()
    try:
        wait_for(lambda: w.game == 7)
        n = len(seen)
        assert n >= 1
        state["game"] = 0
        wait_for(lambda: w.game == 0)
        before = len(seen)
        time.sleep(0.3)
        assert len(seen) == before                                     # nothing seen: it sleeps until kicked
        state["game"], state["active"] = 8, False
        w.kick()
        wait_for(lambda: w.game is None)                               # not while a stream of ours runs: no probe
        state["active"] = True
        w.kick()
        wait_for(lambda: w.game == 8)
    finally:
        w.stop()


def test_session_watcher_survives_a_probe_that_raises():
    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return 3

    w = RS.SessionWatcher(probe, lambda: True, track_every=0.05)
    w.start()
    try:
        w.kick()
        wait_for(lambda: w.game == 3)
    finally:
        w.stop()
