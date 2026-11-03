import pygame
import pytest

from couch.launcher import input as inp


def cbutton(button, down=True, iid=0):
    return pygame.event.Event(pygame.CONTROLLERBUTTONDOWN if down else pygame.CONTROLLERBUTTONUP,
                              button=button, instance_id=iid)


def caxis(axis, value, iid=0):
    return pygame.event.Event(pygame.CONTROLLERAXISMOTION, axis=axis, value=value, instance_id=iid)


@pytest.mark.parametrize("button,action", [
    (pygame.CONTROLLER_BUTTON_DPAD_UP, inp.UP), (pygame.CONTROLLER_BUTTON_DPAD_DOWN, inp.DOWN),
    (pygame.CONTROLLER_BUTTON_DPAD_LEFT, inp.LEFT), (pygame.CONTROLLER_BUTTON_DPAD_RIGHT, inp.RIGHT),
    (pygame.CONTROLLER_BUTTON_A, inp.SELECT), (pygame.CONTROLLER_BUTTON_B, inp.BACK),
    (pygame.CONTROLLER_BUTTON_START, inp.MENU)])
def test_buttons(button, action):
    assert inp.Mapper().feed(cbutton(button), 0) == [inp.WAKE, action]


def test_unmapped_buttons_only_wake():
    m = inp.Mapper()
    for b in (pygame.CONTROLLER_BUTTON_X, pygame.CONTROLLER_BUTTON_Y, pygame.CONTROLLER_BUTTON_BACK,
              pygame.CONTROLLER_BUTTON_LEFTSHOULDER):
        assert m.feed(cbutton(b), 0) == [inp.WAKE]


def test_button_up_is_silent():
    assert inp.Mapper().feed(cbutton(pygame.CONTROLLER_BUTTON_A, down=False), 0) == []


def test_stick_deadzone_and_direction():
    m = inp.Mapper()
    assert m.feed(caxis(pygame.CONTROLLER_AXIS_LEFTX, 10000), 0) == []        # inside deadzone (0.3)
    assert m.feed(caxis(pygame.CONTROLLER_AXIS_LEFTX, 30000), 0) == [inp.RIGHT]
    assert m.feed(caxis(pygame.CONTROLLER_AXIS_LEFTX, 31000), 0.01) == []     # same direction: no repeat yet
    assert m.feed(caxis(pygame.CONTROLLER_AXIS_LEFTX, 0), 0.02) == []
    assert m.feed(caxis(pygame.CONTROLLER_AXIS_LEFTY, -30000), 0.03) == [inp.UP]


def test_stick_auto_repeat():
    m = inp.Mapper()
    m.feed(caxis(pygame.CONTROLLER_AXIS_LEFTY, 32000), 0)
    assert m.tick(inp.REPEAT_DELAY - 0.01) == []
    assert m.tick(inp.REPEAT_DELAY) == [inp.DOWN]
    assert m.tick(inp.REPEAT_DELAY + inp.REPEAT_RATE) == [inp.DOWN]
    m.feed(caxis(pygame.CONTROLLER_AXIS_LEFTY, 0), 1.0)
    assert m.tick(5.0) == []


def test_right_stick_and_triggers_ignored():
    m = inp.Mapper()
    assert m.feed(caxis(pygame.CONTROLLER_AXIS_RIGHTX, 32000), 0) == []
    assert m.feed(caxis(pygame.CONTROLLER_AXIS_TRIGGERLEFT, 32000), 0) == []


def test_guide_hold_3s_force_quits_once():
    m = inp.Mapper()
    assert m.feed(cbutton(pygame.CONTROLLER_BUTTON_GUIDE), 10) == [inp.WAKE]
    assert m.tick(12.9) == []
    assert m.tick(13.0) == [inp.FORCE_QUIT]
    assert m.tick(14.0) == []


def test_guide_released_early_does_nothing():
    m = inp.Mapper()
    m.feed(cbutton(pygame.CONTROLLER_BUTTON_GUIDE), 0)
    m.feed(cbutton(pygame.CONTROLLER_BUTTON_GUIDE, down=False), 1)
    assert m.tick(5) == []


def test_two_pads_are_independent():
    m = inp.Mapper()
    assert m.feed(caxis(pygame.CONTROLLER_AXIS_LEFTX, 32000, iid=0), 0) == [inp.RIGHT]
    assert m.feed(caxis(pygame.CONTROLLER_AXIS_LEFTX, -32000, iid=1), 0) == [inp.LEFT]


def test_raw_joystick_fallback_for_unknown_pads():
    m = inp.Mapper()
    assert m.feed(pygame.event.Event(pygame.JOYBUTTONDOWN, button=0, instance_id=7), 0) == [inp.WAKE, inp.SELECT]
    assert m.feed(pygame.event.Event(pygame.JOYHATMOTION, value=(0, 1), hat=0, instance_id=7), 0) == \
        [inp.WAKE, inp.UP]
    assert m.feed(pygame.event.Event(pygame.JOYHATMOTION, value=(0, 0), hat=0, instance_id=7), 0) == []


def test_raw_events_from_game_controllers_are_not_doubled():
    m = inp.Mapper()
    m.controller_ids = {7}
    assert m.feed(pygame.event.Event(pygame.JOYBUTTONDOWN, button=0, instance_id=7), 0) == []


def test_keyboard_and_mouse_do_nothing():
    m = inp.Mapper()
    assert m.feed(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r", scancode=40), 0) == []
    assert m.feed(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(1, 1)), 0) == []


def test_synthetic_events_through_sdl_queue():
    """Events posted to the real SDL queue (dummy driver) come back mapped."""
    pygame.init()
    pygame.event.clear()
    pygame.event.post(cbutton(pygame.CONTROLLER_BUTTON_A))
    pygame.event.post(caxis(pygame.CONTROLLER_AXIS_LEFTX, -32000))
    m = inp.Mapper()
    out = []
    for ev in pygame.event.get():
        out += m.feed(ev, 0)
    assert out == [inp.WAKE, inp.SELECT, inp.LEFT]
