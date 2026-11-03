"""Gamepad -> launcher actions. Only the gamepad exists on the couch: D-pad / left stick navigate, A select, B back,
Start = menu, Guide held 3 s while streaming = force-quit. Uses SDL's GameController API (standard Xbox layout from
SDL's controller DB), with a raw-joystick fallback (hat + axes + buttons 0/1/7) for pads SDL does not know.

Pure mapping: `Mapper.feed(event, now)` returns a list of actions; `Mapper.tick(now)` emits stick auto-repeat and
the Guide-hold action. Tests drive it with synthetic pygame events."""
from __future__ import annotations

import pygame

UP, DOWN, LEFT, RIGHT, SELECT, BACK, MENU, FORCE_QUIT, WAKE = (
    "UP", "DOWN", "LEFT", "RIGHT", "SELECT", "BACK", "MENU", "FORCE_QUIT", "WAKE")
ACTIONS = (UP, DOWN, LEFT, RIGHT, SELECT, BACK, MENU, FORCE_QUIT)

DEADZONE = 0.55            # of full scale; generous so a drifting stick never navigates by itself
REPEAT_DELAY = 0.40
REPEAT_RATE = 0.12
GUIDE_HOLD = 3.0
AXIS_MAX = 32767.0

_BUTTONS = {
    pygame.CONTROLLER_BUTTON_DPAD_UP: UP,
    pygame.CONTROLLER_BUTTON_DPAD_DOWN: DOWN,
    pygame.CONTROLLER_BUTTON_DPAD_LEFT: LEFT,
    pygame.CONTROLLER_BUTTON_DPAD_RIGHT: RIGHT,
    pygame.CONTROLLER_BUTTON_A: SELECT,
    pygame.CONTROLLER_BUTTON_B: BACK,
    pygame.CONTROLLER_BUTTON_START: MENU,
}
# Raw joystick fallback (xpad/xone/xpadneo all expose A=0, B=1, Start=7 on Xbox pads).
_JOY_BUTTONS = {0: SELECT, 1: BACK, 7: MENU}
_HATS = {(0, 1): UP, (0, -1): DOWN, (-1, 0): LEFT, (1, 0): RIGHT}


class Mapper:
    def __init__(self) -> None:
        self.stick_dir: dict[int, str | None] = {}     # instance id -> current stick direction
        self.stick_next: dict[int, float] = {}
        self.guide_down_at: float | None = None
        self.guide_fired = False
        self.controller_ids: set[int] = set()            # joysticks also opened as GameControllers
        self._axes: dict[tuple[str, int], float] = {}

    @staticmethod
    def _axis_dir(x: float, y: float) -> str | None:
        if max(abs(x), abs(y)) < DEADZONE:
            return None
        if abs(x) > abs(y):
            return RIGHT if x > 0 else LEFT
        return DOWN if y > 0 else UP

    def _stick(self, jid: int, x: float, y: float, now: float) -> list[str]:
        d = self._axis_dir(x, y)
        prev = self.stick_dir.get(jid)
        self.stick_dir[jid] = d
        if d is None or d == prev:
            return []
        self.stick_next[jid] = now + REPEAT_DELAY
        return [d]

    def feed(self, ev, now: float) -> list[str]:
        t = ev.type
        if t == pygame.CONTROLLERBUTTONDOWN:
            if ev.button == pygame.CONTROLLER_BUTTON_GUIDE:
                self.guide_down_at, self.guide_fired = now, False
                return [WAKE]
            a = _BUTTONS.get(ev.button)
            return [WAKE, a] if a else [WAKE]
        if t == pygame.CONTROLLERBUTTONUP:
            if ev.button == pygame.CONTROLLER_BUTTON_GUIDE:
                self.guide_down_at = None
            return []
        if t == pygame.CONTROLLERAXISMOTION:
            if ev.axis in (pygame.CONTROLLER_AXIS_LEFTX, pygame.CONTROLLER_AXIS_LEFTY):
                key = ("x", ev.instance_id) if ev.axis == pygame.CONTROLLER_AXIS_LEFTX else ("y", ev.instance_id)
                self._axes[key] = ev.value / AXIS_MAX
                return self._stick(ev.instance_id, self._axes.get(("x", ev.instance_id), 0.0),
                                   self._axes.get(("y", ev.instance_id), 0.0), now)
            return []
        # Raw joystick fallback, ignored for devices SDL also exposes as a GameController (no double input).
        if t == pygame.JOYBUTTONDOWN and getattr(ev, "instance_id", None) not in self.controller_ids:
            a = _JOY_BUTTONS.get(ev.button)
            return [WAKE, a] if a else [WAKE]
        if t == pygame.JOYHATMOTION and getattr(ev, "instance_id", None) not in self.controller_ids:
            a = _HATS.get(tuple(ev.value))
            return [WAKE, a] if a else []
        return []

    def tick(self, now: float) -> list[str]:
        out = []
        for jid, d in self.stick_dir.items():
            if d is not None and now >= self.stick_next.get(jid, now + 1):
                out.append(d)
                self.stick_next[jid] = now + REPEAT_RATE
        if self.guide_down_at is not None and not self.guide_fired and now - self.guide_down_at >= GUIDE_HOLD:
            self.guide_fired = True
            out.append(FORCE_QUIT)
        return out
