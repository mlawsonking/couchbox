import pytest

from couch.launcher import state as S
from fakes import FakeBackend


def mk(host_up=False, sunshine_up=False):
    b = FakeBackend(host_up, sunshine_up)
    return b, S.Machine(b, host="gamepc.local", mac="AA:BB:CC:DD:EE:FF")


def test_idle_launch_asleep_goes_waking_and_sends_wol():
    b, m = mk()
    m.launch("Desktop", 0)
    assert m.state == S.WAKING and b.calls == ["wake"]


def test_wol_resent_every_10s_while_waking():
    b, m = mk()
    m.launch("Desktop", 0)
    for t in range(1, 31):
        m.tick(t)
    assert b.calls.count("wake") == 4      # t=0,10,20,30


def test_waking_to_wait_sunshine_when_host_answers():
    b, m = mk()
    m.launch("Desktop", 0)
    b._host_up = True
    m.tick(5)
    assert m.state == S.WAIT_SUNSHINE


def test_wake_timeout_lost():
    b, m = mk()
    m.launch("Desktop", 0)
    m.tick(S.WAKE_TIMEOUT - 1)
    assert m.state == S.WAKING
    m.tick(S.WAKE_TIMEOUT)
    assert m.state == S.LOST and m.failure.reason == "WAKE_TIMEOUT"
    assert "AA:BB:CC:DD:EE:FF" in m.failure.parent and m.failure.kid


def test_launch_with_host_up_skips_waking():
    b, m = mk(host_up=True)
    m.launch("Desktop", 0)
    assert m.state == S.WAIT_SUNSHINE and "wake" not in b.calls


def test_wait_sunshine_starts_stream():
    b, m = mk(True, True)
    m.launch("Celeste", 0)
    m.tick(1)
    assert m.state == S.STREAMING and ("stream", "Celeste") in b.calls


def test_sunshine_timeout_lost():
    b, m = mk(True, False)
    m.launch("Celeste", 0)
    m.tick(S.SUNSHINE_TIMEOUT)
    assert m.state == S.LOST and m.failure.reason == "SUNSHINE_TIMEOUT"


def stream(m, b, t=1):
    b._host_up = b._sunshine_up = True
    m.launch("Celeste", 0)
    m.tick(t)
    assert m.state == S.STREAMING


def test_clean_quit_returns_idle():
    b, m = mk()
    stream(m, b)
    b.stream = ("exited", 0)
    m.tick(600)
    assert m.state == S.IDLE and m.failure is None


def test_three_failed_launches_then_lost():
    b, m = mk()
    stream(m, b)
    t = 1
    for strike in (1, 2):
        b.stream = ("exited", 1)
        t += 1
        m.tick(t)
        assert m.state == S.WAIT_SUNSHINE and m.strikes == strike
        m.tick(t + 1)                      # still in the retry pause
        assert m.state == S.WAIT_SUNSHINE
        t += S.RETRY_PAUSE
        m.tick(t)
        assert m.state == S.STREAMING
    b.stream = ("exited", 1)
    m.tick(t + 1)
    assert m.state == S.LOST and m.failure.reason == "STREAM_FAILED" and m.strikes == 3
    assert sum(1 for c in b.calls if c == ("stream", "Celeste")) == 3


def test_vanished_child_counts_as_strike():
    b, m = mk()
    stream(m, b)
    b.stream = ("none", None)
    m.tick(2)
    assert m.state == S.WAIT_SUNSHINE and m.strikes == 1


def test_drop_after_grace_is_stream_lost():
    b, m = mk()
    stream(m, b)
    b.stream = ("exited", 1)
    m.tick(1 + S.LAUNCH_GRACE + 1)
    assert m.state == S.LOST and m.failure.reason == "STREAM_LOST"


def test_not_paired_exit():
    b, m = mk()
    stream(m, b)
    b.stream = ("exited", S.NOT_PAIRED_EXIT)
    m.tick(2)
    assert m.failure.reason == "NOT_PAIRED"


def test_host_gone_while_streaming_stops_stream():
    b, m = mk()
    stream(m, b)
    b._host_up = False
    m.tick(100)
    assert m.state == S.STREAMING
    m.tick(100 + S.HOST_GONE_GRACE - 1)
    assert m.state == S.STREAMING
    m.tick(100 + S.HOST_GONE_GRACE)
    assert m.state == S.LOST and m.failure.reason == "HOST_GONE" and b.stopped == 1


def test_host_blip_resets_gone_timer():
    b, m = mk()
    stream(m, b)
    b._host_up = False
    m.tick(10)
    b._host_up = True
    m.tick(20)
    b._host_up = False
    m.tick(10 + S.HOST_GONE_GRACE)
    assert m.state == S.STREAMING


def test_force_quit():
    b, m = mk()
    stream(m, b)
    m.force_quit(5)
    assert m.state == S.IDLE and b.stopped == 1


def test_force_quit_ignored_when_not_streaming():
    b, m = mk()
    m.force_quit(0)
    assert m.state == S.IDLE and b.stopped == 0


@pytest.mark.parametrize("prep", ["waking", "wait"])
def test_back_cancels(prep):
    b, m = mk(host_up=(prep == "wait"))
    m.launch("Desktop", 0)
    m.back(1)
    assert m.state == S.IDLE


def test_lost_retry_and_back():
    b, m = mk()
    m.launch("Desktop", 0)
    m.tick(S.WAKE_TIMEOUT)
    assert m.state == S.LOST
    m.retry(200)
    assert m.state == S.WAKING and m.app == "Desktop" and m.failure is None
    m.tick(200 + S.WAKE_TIMEOUT)
    m.back(400)
    assert m.state == S.IDLE and m.failure is None


def test_launch_ignored_while_busy():
    b, m = mk()
    m.launch("Desktop", 0)
    m.launch("Celeste", 1)
    assert m.app == "Desktop"


def test_pairing_success():
    b, m = mk()
    m.pair(0)
    assert m.state == S.PAIRING and m.pin == "1234"
    m.tick(5)
    assert m.state == S.PAIRING
    b.pair = ("paired", "")
    m.tick(6)
    assert m.state == S.IDLE and m.pin is None


def test_pairing_failed():
    b, m = mk()
    m.pair(0)
    b.pair = ("failed", "wrong PIN")
    m.tick(1)
    assert m.state == S.LOST and m.failure.reason == "PAIR_FAILED" and "wrong PIN" in m.failure.parent


def test_pairing_timeout_cancels():
    b, m = mk()
    m.pair(0)
    m.tick(S.PAIR_TIMEOUT)
    assert m.state == S.LOST and m.failure.reason == "PAIR_TIMEOUT" and "pair_cancel" in b.calls


def test_pairing_back_cancels():
    b, m = mk()
    m.pair(0)
    m.back(1)
    assert m.state == S.IDLE and "pair_cancel" in b.calls


def test_pair_only_from_idle():
    b, m = mk()
    m.launch("Desktop", 0)
    m.pair(1)
    assert m.state == S.WAKING


def test_every_reason_has_kid_and_parent_text():
    for reason, (kid, parent) in S.REASONS.items():
        assert kid and parent and len(kid) < 70, reason


def test_every_state_reached_and_every_edge_covered():
    """Union of the transitions exercised in this module's scenarios must cover the documented graph."""
    expected = {(S.IDLE, S.WAKING), (S.IDLE, S.WAIT_SUNSHINE), (S.WAKING, S.WAIT_SUNSHINE), (S.WAKING, S.LOST),
                (S.WAKING, S.IDLE), (S.WAIT_SUNSHINE, S.STREAMING), (S.WAIT_SUNSHINE, S.LOST),
                (S.WAIT_SUNSHINE, S.IDLE), (S.STREAMING, S.IDLE), (S.STREAMING, S.LOST),
                (S.STREAMING, S.WAIT_SUNSHINE), (S.LOST, S.WAKING), (S.LOST, S.IDLE), (S.IDLE, S.PAIRING),
                (S.PAIRING, S.IDLE), (S.PAIRING, S.LOST)}
    seen = set()
    for fn in [test_idle_launch_asleep_goes_waking_and_sends_wol, test_waking_to_wait_sunshine_when_host_answers,
               test_wake_timeout_lost, test_launch_with_host_up_skips_waking, test_wait_sunshine_starts_stream,
               test_sunshine_timeout_lost, test_clean_quit_returns_idle, test_three_failed_launches_then_lost,
               test_drop_after_grace_is_stream_lost, test_lost_retry_and_back, test_pairing_success,
               test_pairing_failed]:
        orig = S.Machine._go

        def spy(self, st, now, _orig=orig):
            seen.add((self.state, st))
            _orig(self, st, now)
        S.Machine._go = spy
        try:
            fn()
        finally:
            S.Machine._go = orig
    for prep in ("waking", "wait"):
        b, m = mk(host_up=(prep == "wait"))
        m.launch("x", 0)
        seen.add((m.state, S.IDLE))
    assert expected <= seen, expected - seen


@pytest.mark.parametrize("how", ["moonlight_exits", "moonlight_hangs"])
def test_host_vanishing_mid_stream_is_a_normal_path(how):
    """facts: the workstation has bugchecked ~10 times since July. A host that disappears mid-stream must end on the
    kid-readable LOST screen (never a crash or a hang), and Retry must walk the normal wake path until it is back."""
    b, m = mk()
    stream(m, b)                                   # STREAMING since t=1
    b._host_up = b._sunshine_up = False            # bugcheck: PC gone
    if how == "moonlight_exits":
        b.stream = ("exited", 1)                   # moonlight notices first (after > LAUNCH_GRACE)
        m.tick(60)
        assert m.state == S.LOST and m.failure.reason == "STREAM_LOST"
    else:
        m.tick(60)                                 # moonlight still "running", host silent
        m.tick(60 + S.HOST_GONE_GRACE)
        assert m.state == S.LOST and m.failure.reason == "HOST_GONE" and b.stopped == 1
    assert m.failure.kid == "We lost the game PC." and m.app == "Celeste"
    # Retry while the PC is still rebooting: normal WAKING with WoL, no strikes carried over.
    m.retry(200)
    assert m.state == S.WAKING and m.strikes == 0 and b.calls.count("wake") >= 1
    m.tick(230)
    b._host_up = True                              # Windows is back up, Sunshine not yet
    m.tick(240)
    assert m.state == S.WAIT_SUNSHINE
    b._sunshine_up = True
    m.tick(250)
    assert m.state == S.STREAMING and b.calls.count(("stream", "Celeste")) == 2
    b.stream = ("exited", 0)
    m.tick(900)
    assert m.state == S.IDLE and m.failure is None
