"""The Couch launcher: ties config, state machine, input mapping, renderer and backend together.

`App` is drivable without a real display (tests feed it actions and a fake backend and read back `View`s);
`main()` runs the pygame loop full-screen under cage."""
from __future__ import annotations

import json
import logging
import os
import time

from . import input as inp
from . import render as R
from . import state as S
from .config import DEFAULT_PATH, Config, Watcher, load
from .tiles import CLOSE, PAIR, RESTART, Grid, Menu

ATTRACT_AFTER = 10 * 60.0      # dim the home screen
SLEEP_AFTER = 60 * 60.0        # display off (black + DPMS hook)
DISPLAY_POWER = os.environ.get("COUCH_DISPLAY_POWER", "/opt/couch/bin/couch-display-power")
HEALTH_FILE = os.environ.get("COUCH_HEALTH_FILE", "/var/lib/couch/health.json")

STATE_TO_SCREEN = {S.IDLE: R.HOME, S.WAKING: R.WAKING, S.WAIT_SUNSHINE: R.CONNECTING, S.STREAMING: R.STREAMING,
                   S.LOST: R.LOST, S.PAIRING: R.PAIR}

log = logging.getLogger("couch.launcher")


class App:
    def __init__(self, cfg: Config, backend, now: float = 0.0, sounds=None, display_power=None):
        self.cfg = cfg
        self.b = backend
        self.m = S.Machine(backend, host=cfg.host, mac=cfg.mac, now=now)
        self.m.config_error = cfg.error
        self.grid = Grid(list(cfg.tiles))
        self.menu = Menu(pair_allowed=cfg.pair_policy == "menu")
        self.menu_open = False
        self.last_input = now
        self.dim = 0.0
        self.pads = 0
        self.sounds = sounds
        self.display_power = display_power      # callable(on: bool) or None
        self._display_off = False
        self.health = {"overall": "green", "link_bad": False}
        self.quit_requested = False

    # ---------------------------------------------------------------- helpers
    def _play(self, name: str) -> None:
        if self.sounds:
            self.sounds.play(name)

    def apply_config(self, cfg: Config) -> None:
        """Hot reload. Host/MAC changes apply to the next launch; tiles update in place."""
        self.cfg = cfg
        self.m.host, self.m.mac, self.m.config_error = cfg.host, cfg.mac, cfg.error
        if hasattr(self.b, "cfg"):
            self.b.cfg = cfg
        self.grid.replace(cfg.tiles)
        self.menu.pair_allowed = cfg.pair_policy == "menu"
        self.menu.focus = 0

    def screen(self) -> str:
        sc = STATE_TO_SCREEN[self.m.state]
        if sc == R.HOME and self.menu_open:
            return R.MENU
        return sc

    # ---------------------------------------------------------------- input
    def handle(self, actions: list[str], now: float) -> None:
        if not actions:
            return
        self.last_input = now
        if self.dim > 0:
            # The first press only wakes the screen; it never also launches something.
            self.dim = 0.0
            if self._display_off and self.display_power:
                self.display_power(True)
            self._display_off = False
            return
        for a in actions:
            if a == inp.WAKE:
                continue
            self._action(a, now)

    def _action(self, a: str, now: float) -> None:
        sc = self.screen()
        if sc == R.HOME:
            if a in (inp.UP, inp.DOWN, inp.LEFT, inp.RIGHT):
                if self.grid.move(a):
                    self._play("move")
            elif a == inp.SELECT and self.grid.selected():
                self._play("select")
                self.m.launch(self.grid.selected().app, now)
            elif a == inp.MENU:
                self.menu_open, self.menu.focus = True, 0
                self._play("select")
        elif sc == R.MENU:
            if a in (inp.UP, inp.DOWN):
                if self.menu.move(a):
                    self._play("move")
            elif a in (inp.BACK, inp.MENU):
                self.menu_open = False
                self._play("back")
            elif a == inp.SELECT:
                item = self.menu.selected()
                self.menu_open = False
                if item == RESTART:
                    self._play("select")
                    log.info("restart requested from the menu")
                    self.b.restart_box()
                elif item == PAIR:
                    self._play("select")
                    self.m.pair(now)
                elif item == CLOSE:
                    self._play("back")
        elif sc in (R.WAKING, R.CONNECTING, R.PAIR):
            if a == inp.BACK:
                self._play("back")
                self.m.back(now)
        elif sc == R.LOST:
            if a == inp.SELECT:
                self._play("select")
                self.m.retry(now)
            elif a == inp.BACK:
                self._play("back")
                self.m.back(now)
        elif sc == R.STREAMING:
            if a == inp.FORCE_QUIT:
                log.warning("guide held 3 s: force-quitting the stream")
                self.m.force_quit(now)

    # ---------------------------------------------------------------- clock
    def tick(self, now: float) -> None:
        before = self.m.state
        self.m.tick(now)
        after = self.m.state
        if before != after:
            log.info("state %s -> %s%s", before, after,
                     f" ({self.m.failure.reason}: {self.m.failure.parent})" if after == S.LOST and self.m.failure
                     else "")
            if after == S.LOST:
                self._play("error")
            elif after == S.IDLE and before in (S.STREAMING, S.PAIRING):
                self._play("ready")
                self.last_input = now
        if self.screen() in (R.HOME, R.MENU):
            idle = now - self.last_input
            if idle >= SLEEP_AFTER:
                if not self._display_off:
                    self._display_off = True
                    self.dim = 1.0
                    if self.display_power:
                        self.display_power(False)
            elif idle >= ATTRACT_AFTER:
                self.dim = 0.6
        else:
            self.last_input = max(self.last_input, now - 1)   # streaming / waiting never counts as idle
            if self.dim:
                self.dim = 0.0

    def view(self, now: float) -> R.View:
        sc = self.screen()
        host_up = self.b.host_up()
        f = self.m.failure
        return R.View(
            screen=sc,
            tiles=[R.TileView(t.name, t.art) for t in self.grid.tiles],
            cols=self.grid.cols, focus=self.grid.focus, app=self.m.app or "",
            host_state="awake" if host_up else ("asleep" if host_up is False else "unknown"),
            pads=self.pads, link_bad=bool(self.health.get("link_bad")),
            health=self.health.get("overall", "green"),
            kid=f.kid if f else "", parent=f.parent if f else "",
            pin=self.m.pin or "", menu_items=self.menu.items, menu_focus=self.menu.focus,
            elapsed=self.m.elapsed(now), t=now, dim=self.dim,
            notice=("Settings problem: " + self.cfg.error) if self.cfg.error else "",
        )

    def refresh_health(self, path: str = HEALTH_FILE) -> None:
        """couch-daemon (P3) writes the doctor's verdict here; absent file = assume green."""
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
            self.health = {"overall": d.get("overall", "green"), "link_bad": bool(d.get("link_bad", False))}
        except (OSError, ValueError):
            pass


# -------------------------------------------------------------------- real loop
def _display_power_hook(on: bool) -> None:
    import subprocess
    if os.access(DISPLAY_POWER, os.X_OK):
        subprocess.run([DISPLAY_POWER, "on" if on else "off"], check=False, timeout=10)


def main(config_path: str = DEFAULT_PATH) -> int:
    os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")   # see the Guide button while streaming
    os.environ.setdefault("SDL_GAMECONTROLLER_ALLOW_STEAM_VIRTUAL_GAMEPAD", "0")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        filename=os.path.join(os.environ.get("COUCH_LOG_DIR", "/var/log/couch"), "launcher.log"))
    import pygame
    from pygame._sdl2 import controller

    from .backend import LocalBackend
    from .sounds import Sounds

    # Under cage the Wayland backend is preferred; XWayland (x11) is the fallback if SDL cannot use Wayland.
    wanted = os.environ.get("SDL_VIDEODRIVER")
    for drv in ([wanted] if wanted else []) + ["wayland", "x11"]:
        os.environ["SDL_VIDEODRIVER"] = drv
        try:
            pygame.display.init()
            break
        except pygame.error as exc:
            log.warning("SDL video driver %s unavailable: %s", drv, exc)
    else:
        log.error("no usable SDL video driver")
        return 3
    pygame.init()
    controller.init()
    pygame.mouse.set_visible(False)
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    pygame.display.set_caption("Couch")
    cfg = load(config_path)
    watcher = Watcher(config_path)
    backend = _make_backend(cfg, LocalBackend)
    app = App(cfg, backend, now=time.monotonic(), sounds=Sounds(), display_power=_display_power_hook)
    renderer = R.Renderer(screen.get_size())
    mapper = inp.Mapper()
    pads: dict[int, object] = {}
    clock = pygame.time.Clock()
    next_slow = 0.0
    exit_at = time.monotonic() + float(os.environ["COUCH_EXIT_AFTER"]) if os.environ.get("COUCH_EXIT_AFTER") else None
    log.info("launcher up: %sx%s, host %s, %d tiles", *screen.get_size(), cfg.host, len(cfg.tiles))
    try:
        while not app.quit_requested:
            now = time.monotonic()
            if exit_at is not None and now >= exit_at:      # tests only
                break
            actions: list[str] = []
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    app.quit_requested = True
                elif ev.type == pygame.CONTROLLERDEVICEADDED:
                    try:
                        c = controller.Controller(ev.device_index)
                        pads[c.as_joystick().get_instance_id()] = c
                        mapper.controller_ids = set(pads)
                    except pygame.error:
                        pass
                elif ev.type == pygame.CONTROLLERDEVICEREMOVED:
                    pads.pop(ev.instance_id, None)
                    mapper.controller_ids = set(pads)
                actions += mapper.feed(ev, now)
            actions += mapper.tick(now)
            app.pads = max(len(pads), pygame.joystick.get_count())
            app.handle(actions, now)
            app.tick(now)
            if now >= next_slow:
                next_slow = now + 1.0
                new = watcher.poll()
                if new is not None:
                    log.info("config reloaded%s", f" with problems: {new.error}" if new.error else "")
                    app.apply_config(new)
                app.refresh_health()
            dirty = renderer.draw(screen, app.view(now))
            pygame.display.update(dirty)
            clock.tick(10 if app.m.state == S.STREAMING else 30)
    finally:
        backend.close()
        pygame.quit()
    return 0


def _make_backend(cfg: Config, local_cls):
    """Prefer couch-daemon (P3) when present; otherwise talk to the LAN/moonlight directly."""
    try:
        from .daemon_client import DaemonBackend, daemon_available
        if daemon_available():
            return DaemonBackend(cfg)
    except ImportError:
        pass
    return local_cls(cfg)
