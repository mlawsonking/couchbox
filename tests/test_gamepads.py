"""Generic-receiver gamepad path: SDL GUIDs, gamepads.txt, the first-run mapping screen, PIN-gated re-mapping,
hot-plug and pad off/on - all with synthetic SDL devices/events."""
import pygame
import pytest

from couch.launcher import app as A
from couch.launcher import config as C
from couch.launcher import gamepads as G
from couch.launcher import input as inp
from couch.launcher import render as R
from couch.launcher import state as S
from couch.launcher.tiles import SETUP_PAD
from fakes import FakeBackend

X360 = G.sdl_guid(0x0003, 0x045E, 0x028E, 0x0114)       # the confirmed receiver (xpad)
GENERIC = G.sdl_guid(0x0003, 0x0079, 0x18D4, 0x0110)    # an unknown 2.4 GHz pad


def test_guid_matches_sdl_database_form():
    assert X360 == "030000005e0400008e02000014010000"     # the line SDL's gamecontrollerdb uses for xpad
    assert G.db_guid("0300b6595e0400008e02000014010000") == X360   # runtime CRC field ignored


def jb(button, down=True, iid=7):
    return pygame.event.Event(pygame.JOYBUTTONDOWN if down else pygame.JOYBUTTONUP, button=button, instance_id=iid)


def hat(v, iid=7):
    return pygame.event.Event(pygame.JOYHATMOTION, hat=0, value=v, instance_id=iid)


def ax(axis, v, iid=7):
    return pygame.event.Event(pygame.JOYAXISMOTION, axis=axis, value=v, instance_id=iid)


def run_full_session(s, t=0.0):
    i = s.instance_id
    for b in (0, 1, 2, 3):                    # A B X Y
        s.feed(jb(b, iid=i), t); s.feed(jb(b, False, iid=i), t); t += 1
    for v in ((0, 1), (0, -1), (-1, 0), (1, 0)):
        s.feed(hat(v, iid=i), t); s.feed(hat((0, 0), iid=i), t); t += 1
    for a, v in ((0, 0.9), (1, 0.9), (3, 0.9), (4, -0.9)):   # right stick Y is inverted on this pad
        s.feed(ax(a, v, iid=i), t); s.feed(ax(a, 0.0, iid=i), t); t += 1
    for b in (7, 6):                          # Start, Back
        s.feed(jb(b, iid=i), t); s.feed(jb(b, False, iid=i), t); t += 1
    return t


def test_mapping_session_full_line():
    s = G.MappingSession(7, GENERIC, "Generic, 2.4G Pad", 0)
    run_full_session(s)
    assert s.done
    line = s.mapping_line()
    assert line.startswith(GENERIC + ",Generic  2.4G Pad,")          # commas in names cannot break the format
    for tok in ("a:b0", "b:b1", "x:b2", "y:b3", "dpup:h0.1", "dpdown:h0.4", "dpleft:h0.8", "dpright:h0.2",
                "leftx:a0", "lefty:a1", "rightx:a3", "righty:a4~", "start:b7", "back:b6"):
        assert tok in line
    assert line.endswith("platform:Linux,")


def test_one_press_counts_once_and_other_pads_ignored():
    s = G.MappingSession(7, GENERIC, "pad", 0)
    s.feed(jb(0), 0)
    s.feed(jb(0), 0.1)                       # still held / repeated: not B
    s.feed(jb(5, iid=8), 0.2)                # a different pad
    assert s.step == 1 and "b" not in s.tokens
    s.feed(jb(0, False), 0.3)
    s.feed(jb(0), 0.4)                       # same control again for B: ignored
    assert s.step == 1
    s.feed(jb(1), 0.5)
    assert s.tokens["b"] == "b1"


def test_missing_controls_are_skipped_and_dead_pad_aborts():
    s = G.MappingSession(7, GENERIC, "pad", 0)
    s.feed(jb(0), 0); s.feed(jb(0, False), 0)
    s.tick(G.PROMPT_SKIP_S + 0.1)            # no B button press -> skipped
    assert s.step == 2
    s2 = G.MappingSession(7, GENERIC, "pad", 0)
    s2.tick(G.SESSION_ABORT_S)
    assert s2.aborted and s2.mapping_line() is None


def test_dpad_as_axes_and_minimum_viable_mapping():
    s = G.MappingSession(7, GENERIC, "pad", 0)
    s.feed(jb(0), 0); s.feed(jb(0, False), 0)
    s.feed(jb(1), 1); s.feed(jb(1, False), 1)
    t = 2
    while s.step < 4:
        s.tick(t + G.PROMPT_SKIP_S); t += G.PROMPT_SKIP_S + 0.1
    for v in (-1.0, 1.0):                    # d-pad up/down reported on axis 7
        s.feed(ax(7, v), t); s.feed(ax(7, 0), t); t += 1
    assert s.tokens["dpup"] == "-a7" and s.tokens["dpdown"] == "+a7"
    assert s.mapping_line() is None          # no left/right yet: not enough to navigate


def test_mappings_file_roundtrip(tmp_path):
    p = str(tmp_path / "gamepads.txt")
    G.save_mapping(GENERIC + ",Pad,a:b0,b:b1,dpup:h0.1,dpdown:h0.4,dpleft:h0.8,dpright:h0.2,platform:Linux,", p)
    G.save_mapping(GENERIC + ",Pad,a:b1,b:b0,dpup:h0.1,dpdown:h0.4,dpleft:h0.8,dpright:h0.2,platform:Linux,", p)
    maps = G.load_mappings(p)
    assert list(maps) == [GENERIC] and ",a:b1," in maps[GENERIC]          # replaced, not duplicated
    with open(p, "a") as fh:
        fh.write("garbage line\nzz,missing\n")
    assert len(G.load_mappings(p)) == 1
    assert G.apply_to_environment(p) == 1
    import os
    assert os.environ["SDL_GAMECONTROLLERCONFIG"].startswith(GENERIC)


@pytest.fixture
def app(tmp_path):
    cfg = C.Config(tiles=[C.Tile("Desktop", "Desktop")], setup_pin="2468")
    a = A.App(cfg, FakeBackend(host_up=True, sunshine_up=True), now=0)
    a.mappings_path = str(tmp_path / "gamepads.txt")
    a.seen_path = str(tmp_path / "seen.json")
    return a


def test_known_pad_gets_no_mapping_screen(app):
    app.device_added(3, X360, "Xbox 360 Controller", True, 1)
    app.tick(2)
    assert app.screen() == R.HOME and app.mapping is None


def test_unknown_pad_first_run_mapping_then_never_again(app):
    app.device_added(7, GENERIC, "Generic Pad", False, 1)
    assert app.screen() == R.MAPPING
    app.handle([inp.SELECT], 1.5)                     # normal actions are blocked while mapping
    assert app.m.state == S.IDLE
    t = 2.0
    for ev in [jb(0), jb(0, False), jb(1), jb(1, False)]:
        app.raw_event(ev, t)
    while app.mapping and not app.mapping.done:
        t += G.PROMPT_SKIP_S + 0.1
        app.tick(t)
    # only A/B given, no way to move -> nothing written, but it is marked seen (no nagging)
    app.tick(t + 1)
    assert app.mapping is None and G.load_mappings(app.mappings_path) == {}
    app.device_removed(7, t + 2)
    app.device_added(8, GENERIC, "Generic Pad", False, t + 3)
    assert app.screen() == R.HOME


def test_completed_mapping_is_saved_and_reloaded(app):
    app.device_added(7, GENERIC, "Generic Pad", False, 1)
    t = run_full_session(app.mapping, 2)
    app.tick(t)
    assert app.mapping is None and app.reload_controllers
    assert GENERIC in G.load_mappings(app.mappings_path)
    assert app.screen() == R.HOME


def test_pad_switched_off_mid_mapping_is_offered_again(app):
    app.device_added(7, GENERIC, "Generic Pad", False, 1)
    app.device_removed(7, 2)
    assert app.mapping is None and app.screen() == R.HOME
    app.device_added(9, GENERIC, "Generic Pad", False, 3)            # off/on: new instance id, same GUID
    assert app.screen() == R.MAPPING


def test_mapping_waits_while_streaming(app):
    app.handle([inp.SELECT], 0)
    app.tick(1)
    assert app.m.state == S.STREAMING
    app.device_added(7, GENERIC, "Generic Pad", False, 2)
    app.tick(3)
    assert app.screen() == R.STREAMING
    app.b.stream = ("exited", 0)
    app.tick(100)
    assert app.screen() == R.MAPPING


def test_remap_only_through_pin_gated_menu(app, tmp_path):
    app.device_added(3, X360, "Xbox 360 Controller", True, 0)
    app.handle([inp.MENU], 1)
    assert SETUP_PAD in app.menu.items
    while app.menu.selected() != SETUP_PAD:
        app.handle([inp.DOWN], 1)
    app.handle([inp.SELECT], 2)
    assert app.screen() == R.PIN
    app.handle([inp.SELECT], 3)                           # 0000: wrong
    assert app.screen() == R.HOME and app.mapping is None
    app.handle([inp.MENU], 4)
    while app.menu.selected() != SETUP_PAD:
        app.handle([inp.DOWN], 4)
    app.handle([inp.SELECT], 5)
    for a in [inp.UP, inp.UP, inp.RIGHT, inp.UP, inp.UP, inp.UP, inp.UP, inp.RIGHT] + [inp.UP] * 6 + \
             [inp.RIGHT] + [inp.UP] * 8:
        app.handle([a], 6)
    assert app.view(6).pin_digits == [2, 4, 6, 8]
    app.handle([inp.SELECT], 7)
    assert app.screen() == R.MAPPING and app.mapping.instance_id == 3


def test_pin_lockout_after_three_wrong(app):
    from couch.launcher.tiles import PinEntry
    pe = PinEntry()
    for t in range(3):
        assert not pe.check("2468", t)
    pe.digits = [2, 4, 6, 8]
    assert not pe.check("2468", 10)                       # locked for 5 minutes
    pe.digits = [2, 4, 6, 8]
    assert pe.check("2468", 400)


def test_menu_item_hidden_without_setup_pin():
    a = A.App(C.Config(), FakeBackend(), now=0)
    assert SETUP_PAD not in a.menu.items


class FakeJoy:
    def __init__(self, iid, guid, name):
        self.iid, self.guid, self.name = iid, guid, name

    def get_instance_id(self):
        return self.iid

    def get_guid(self):
        return self.guid

    def get_name(self):
        return self.name


class FakeSdl:
    def __init__(self):
        self.devices = {}        # index -> (FakeJoy, mapped)
        self.reinits = 0

    def open_joystick(self, index):
        return self.devices[index][0]

    def is_controller(self, index):
        return self.devices[index][1]

    def open_controller(self, index):
        return self.devices[index][0]

    def controller_instance_id(self, c):
        return c.iid

    def joystick_count(self):
        return len(self.devices)

    def reinit_controllers(self):
        self.reinits += 1


def test_hotplug_and_off_on_without_restart(app):
    sdl = FakeSdl()
    dev = G.Devices(sdl)
    mapper = inp.Mapper()
    sdl.devices[0] = (FakeJoy(10, X360, "Xbox 360 Controller"), True)
    dev.handle(pygame.event.Event(pygame.JOYDEVICEADDED, device_index=0), app, mapper, 1)
    dev.handle(pygame.event.Event(pygame.CONTROLLERDEVICEADDED, device_index=0), app, mapper, 1)
    assert dev.count == 1 and mapper.controller_ids == {10}
    dev.handle(pygame.event.Event(pygame.JOYDEVICEREMOVED, instance_id=10), app, mapper, 2)   # pad off
    assert dev.count == 0 and mapper.controller_ids == set()
    sdl.devices[0] = (FakeJoy(11, X360, "Xbox 360 Controller"), True)                         # pad on
    dev.handle(pygame.event.Event(pygame.JOYDEVICEADDED, device_index=0), app, mapper, 3)
    assert dev.count == 1 and mapper.controller_ids == {11} and app.screen() == R.HOME
    # an unknown pad joins, gets mapped, the mapping is applied without a restart
    sdl.devices[1] = (FakeJoy(12, GENERIC, "Generic Pad"), False)
    dev.handle(pygame.event.Event(pygame.JOYDEVICEADDED, device_index=1), app, mapper, 4)
    assert app.screen() == R.MAPPING
    t = run_full_session(app.mapping, 5)
    app.tick(t)
    sdl.devices[1] = (sdl.devices[1][0], True)           # SDL now knows it
    assert dev.reload_mappings(app.mappings_path) == 1
    assert sdl.reinits == 1 and mapper.controller_ids | set(dev.controllers) >= {11, 12}
