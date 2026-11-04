"""Gamepad plumbing for generic receivers: SDL GUIDs, the /etc/couch/gamepads.txt mapping file, the first-run
button-mapping session, and hot-plug bookkeeping. Pure logic (no SDL calls) so tests drive it with synthetic events.

Primary path: the kernel's generic gamepad support (xpad for XInput-class receivers such as 045E:028E, hid-generic
otherwise) + SDL2's built-in controller database. A pad SDL does not know gets ONE mapping screen the first time it is
seen; the result is an SDL_GAMECONTROLLERCONFIG line in /etc/couch/gamepads.txt, loaded on every start."""
from __future__ import annotations

import json
import os
import re

import pygame

MAPPINGS_PATH = os.environ.get("COUCH_GAMEPADS", "/etc/couch/gamepads.txt")
SEEN_PATH = os.environ.get("COUCH_GAMEPADS_SEEN", "/var/lib/couch/gamepads-seen.json")
GUID_RE = re.compile(r"^[0-9a-f]{32}$")

AXIS_PRESS = 0.6            # fraction of full scale that counts as "pushed"
AXIS_REST = 0.3
PROMPT_SKIP_S = 10.0        # no input for this long on one prompt -> that control is skipped (pad lacks it)
SESSION_ABORT_S = 60.0      # no input at all for this long -> give up, write nothing


def sdl_guid(bus: int, vendor: int, product: int, version: int) -> str:
    """SDL2's Linux joystick GUID in the form its controller database uses (name-CRC field zeroed):
    bus, 0, vendor, 0, product, 0, version, 0 as little-endian 16-bit words."""
    words = [bus, 0, vendor, 0, product, 0, version, 0]
    return "".join(f"{w & 0xFF:02x}{(w >> 8) & 0xFF:02x}" for w in words)


def db_guid(guid: str) -> str:
    """Zero the CRC field (chars 4-8) so a runtime GUID compares equal to its database form."""
    guid = guid.lower()
    return guid[:4] + "0000" + guid[8:] if len(guid) == 32 else guid


def parse_mapping_lines(text: str) -> dict[str, str]:
    """guid -> full mapping line, for valid lines only (comments and junk ignored)."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) >= 3 and GUID_RE.match(parts[0].lower()) and parts[1].strip():
            out[db_guid(parts[0])] = line
    return out


def load_mappings(path: str = MAPPINGS_PATH) -> dict[str, str]:
    try:
        with open(path, encoding="utf-8") as fh:
            return parse_mapping_lines(fh.read())
    except OSError:
        return {}


def save_mapping(line: str, path: str = MAPPINGS_PATH) -> None:
    """Add or replace the line for its GUID. Written in place (the couch user owns the file, not the directory)."""
    lines = load_mappings(path)
    guid = db_guid(line.split(",", 1)[0])
    lines[guid] = line
    header = "# couchbox gamepad mappings (SDL_GAMECONTROLLERCONFIG lines), written by the first-run mapping screen\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + "".join(v + "\n" for v in lines.values()))


def apply_to_environment(path: str = MAPPINGS_PATH) -> int:
    """Hand the saved mappings to SDL. Must run before the GameController subsystem (re)initialises."""
    maps = load_mappings(path)
    if maps:
        os.environ["SDL_GAMECONTROLLERCONFIG"] = "\n".join(maps.values())
    else:
        os.environ.pop("SDL_GAMECONTROLLERCONFIG", None)
    return len(maps)


def load_seen(path: str = SEEN_PATH) -> set[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            v = json.load(fh)
        return {db_guid(g) for g in v if isinstance(g, str)}
    except (OSError, ValueError, TypeError):
        return set()


def mark_seen(guid: str, path: str = SEEN_PATH) -> None:
    seen = load_seen(path) | {db_guid(guid)}
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(sorted(seen), fh)
    except OSError:
        pass


def needs_first_run_mapping(guid: str, is_controller: bool, mappings: dict, seen: set) -> bool:
    """Only pads SDL cannot map, that have no saved line, and that were never offered the screen before."""
    g = db_guid(guid)
    return not is_controller and g not in mappings and g not in seen


# ------------------------------------------------------------------ the mapping session
PROMPTS = [
    ("a", "Press A (the bottom face button)", "button"),
    ("b", "Press B (the right face button)", "button"),
    ("x", "Press X (the left face button)", "button"),
    ("y", "Press Y (the top face button)", "button"),
    ("dpup", "Press the D-pad UP", "button"),
    ("dpdown", "Press the D-pad DOWN", "button"),
    ("dpleft", "Press the D-pad LEFT", "button"),
    ("dpright", "Press the D-pad RIGHT", "button"),
    ("leftx", "Push the LEFT stick to the RIGHT", "axis"),
    ("lefty", "Push the LEFT stick DOWN", "axis"),
    ("rightx", "Push the RIGHT stick to the RIGHT", "axis"),
    ("righty", "Push the RIGHT stick DOWN", "axis"),
    ("start", "Press START (the menu button)", "button"),
    ("back", "Press BACK (the view/select button)", "button"),
]
HAT_BITS = {(0, 1): 1, (1, 0): 2, (0, -1): 4, (-1, 0): 8}


class MappingSession:
    """Feed raw SDL joystick events for ONE device; collects SDL mapping tokens prompt by prompt."""

    def __init__(self, instance_id: int, guid: str, name: str, now: float):
        self.instance_id = instance_id
        self.guid = db_guid(guid)
        self.name = re.sub(r"[,\n\r]", " ", name or "Gamepad").strip()[:60] or "Gamepad"
        self.step = 0
        self.tokens: dict[str, str] = {}
        self.used: set[str] = set()
        self.prompt_started = now
        self.last_input = now
        self.armed = True             # requires release/centre between prompts so one press is not counted twice
        self.done = False
        self.aborted = False

    @property
    def prompt(self) -> str:
        return PROMPTS[self.step][1] if self.step < len(PROMPTS) else "All done!"

    @property
    def progress(self) -> tuple[int, int]:
        return min(self.step, len(PROMPTS)), len(PROMPTS)

    def _record(self, token: str, now: float) -> None:
        if token in self.used:
            return                    # same control twice: ignore, wait for a different one
        key, _, kind = PROMPTS[self.step]
        self.tokens[key] = token
        self.used.add(token)
        self._next(now)

    def _next(self, now: float) -> None:
        self.step += 1
        self.prompt_started = now
        self.armed = False
        if self.step >= len(PROMPTS):
            self.done = True

    def feed(self, ev, now: float) -> None:
        if self.done or self.aborted or getattr(ev, "instance_id", None) != self.instance_id:
            return
        kind = PROMPTS[self.step][2]
        t = ev.type
        if t == pygame.JOYBUTTONUP or (t == pygame.JOYHATMOTION and tuple(ev.value) == (0, 0)):
            self.armed = True
            return
        if t == pygame.JOYAXISMOTION:
            v = ev.value if abs(ev.value) <= 1.0 else ev.value / 32767.0
            if abs(v) < AXIS_REST:
                self.armed = True
                return
            if abs(v) < AXIS_PRESS or not self.armed:
                return
            self.last_input = now
            if kind == "axis":
                # Prompts push RIGHT/DOWN (the positive direction); a negative push means the axis is inverted.
                self._record(f"a{ev.axis}" + ("" if v > 0 else "~"), now)
            else:
                self._record(("+" if v > 0 else "-") + f"a{ev.axis}", now)   # half-axis (triggers/d-pads as axes)
            return
        if not self.armed:
            return
        if t == pygame.JOYBUTTONDOWN and kind == "button":
            self.last_input = now
            self._record(f"b{ev.button}", now)
        elif t == pygame.JOYHATMOTION and kind == "button":
            bit = HAT_BITS.get(tuple(ev.value))
            if bit:
                self.last_input = now
                self._record(f"h{ev.hat}.{bit}", now)

    def tick(self, now: float) -> None:
        if self.done or self.aborted:
            return
        if now - self.last_input >= SESSION_ABORT_S:
            self.aborted = True
        elif now - self.prompt_started >= PROMPT_SKIP_S:
            self._next(now)            # this pad lacks the control: skip it
            self.armed = True

    def mapping_line(self) -> str | None:
        """The SDL_GAMECONTROLLERCONFIG line, or None if the essentials (A, B and a way to move) are missing."""
        moves = {"dpup", "dpdown", "dpleft", "dpright"} <= set(self.tokens) or {"leftx", "lefty"} <= set(self.tokens)
        if not ({"a", "b"} <= set(self.tokens) and moves):
            return None
        body = ",".join(f"{k}:{v}" for k, v in self.tokens.items())
        return f"{self.guid},{self.name},{body},platform:Linux,"


# ------------------------------------------------------------------ hot-plug bookkeeping
class SdlAdapter:
    """The few SDL calls Devices needs (real pygame). Tests pass a fake with the same methods."""

    def __init__(self):
        from pygame._sdl2 import controller
        self._c = controller

    def open_joystick(self, index: int):
        return pygame.joystick.Joystick(index)

    def is_controller(self, index: int) -> bool:
        return bool(self._c.is_controller(index))

    def open_controller(self, index: int):
        return self._c.Controller(index)

    def controller_instance_id(self, ctrl) -> int:
        return ctrl.as_joystick().get_instance_id()

    def joystick_count(self) -> int:
        return pygame.joystick.get_count()

    def reinit_controllers(self) -> None:
        # SDL reads SDL_GAMECONTROLLERCONFIG when the GameController subsystem starts.
        self._c.quit()
        self._c.init()


class Devices:
    """Tracks every joystick (mapped or not) and every GameController across hot-plug and pad off/on."""

    def __init__(self, sdl):
        self.sdl = sdl
        self.joysticks: dict[int, object] = {}      # instance id -> joystick (kept open so raw events flow)
        self.controllers: dict[int, object] = {}    # instance id -> GameController

    @property
    def count(self) -> int:
        return len(self.joysticks)

    def _open_controller(self, index: int) -> None:
        try:
            c = self.sdl.open_controller(index)
            self.controllers[self.sdl.controller_instance_id(c)] = c
        except pygame.error:
            pass

    def handle(self, ev, app, mapper, now: float) -> None:
        t = ev.type
        if t == pygame.JOYDEVICEADDED:
            try:
                j = self.sdl.open_joystick(ev.device_index)
            except pygame.error:
                return
            iid = j.get_instance_id()
            self.joysticks[iid] = j
            mapped = self.sdl.is_controller(ev.device_index)
            if mapped:
                self._open_controller(ev.device_index)
            app.device_added(iid, j.get_guid(), j.get_name(), mapped, now)
        elif t == pygame.JOYDEVICEREMOVED:
            self.joysticks.pop(ev.instance_id, None)
            self.controllers.pop(ev.instance_id, None)
            app.device_removed(ev.instance_id, now)
        elif t == pygame.CONTROLLERDEVICEADDED:
            self._open_controller(ev.device_index)
        elif t == pygame.CONTROLLERDEVICEREMOVED:
            self.controllers.pop(ev.instance_id, None)
        mapper.controller_ids = set(self.controllers)

    def reload_mappings(self, path: str = MAPPINGS_PATH) -> int:
        """After the mapping screen saved a line: hand all lines to SDL and re-open controllers."""
        n = apply_to_environment(path)
        self.controllers.clear()
        self.sdl.reinit_controllers()
        for index in range(self.sdl.joystick_count()):
            if self.sdl.is_controller(index):
                self._open_controller(index)
        return n
