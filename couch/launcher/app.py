"""The Couch launcher: ties config, state machine, input mapping, renderer and backend together.

`App` is drivable without a real display (tests feed it actions and a fake backend and read back `View`s);
`main()` runs the pygame loop full-screen on the kiosk's X server."""
from __future__ import annotations

import json
import logging
import os
import time

from . import input as inp
from . import render as R
from . import state as S
from .config import DEFAULT_PATH, Config, Watcher, load
from . import gamepads as G
from .tiles import CLOSE, PAIR, RESTART, SETUP_PAD, Grid, Menu, PinEntry, merge_tiles

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
        self.daemon_apps: list[str] = []          # what couch-daemon lists from Sunshine (DaemonBackend only)
        self.grid = Grid(list(cfg.tiles))
        self.menu = Menu(pair_allowed=cfg.pair_policy == "menu", pad_setup_allowed=bool(cfg.setup_pin))
        self.menu_open = False
        # Gamepads: instance id -> {"guid", "name", "controller"}; the first-run mapping session, the PIN gate.
        self.devices: dict[int, dict] = {}
        self.last_device: int | None = None
        self.mapping: G.MappingSession | None = None
        self.offer_queue: list[int] = []
        self.pin_entry: PinEntry | None = None
        self.reload_controllers = False         # main loop re-inits SDL's controller subsystem when set
        self.mappings_path = G.MAPPINGS_PATH
        self.seen_path = G.SEEN_PATH
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
        self._rebuild_tiles()
        self.menu.pair_allowed = cfg.pair_policy == "menu"
        self.menu.pad_setup_allowed = bool(cfg.setup_pin)
        self.menu.focus = 0

    def _rebuild_tiles(self) -> bool:
        """Config + library tiles, then a card for every other app the daemon lists. True when the grid changed."""
        new = merge_tiles(self.cfg.tiles, self.daemon_apps, self.cfg.hide_tiles)
        if new == self.grid.tiles:
            return False
        self.grid.replace(new)               # keeps focus on the same app when it still exists
        return True

    def set_daemon_apps(self, apps: list[str]) -> bool:
        """Called about once a second with the daemon's app list. An empty list (PC asleep, listing failed) never
        clears the tiles: the last good list stays. A renamed/removed app disappears, a new one appears, no restart."""
        if not apps or list(apps) == self.daemon_apps:
            return False
        self.daemon_apps = list(apps)
        changed = self._rebuild_tiles()
        if changed:
            log.info("tiles: %d (%d apps listed by the daemon)", len(self.grid.tiles), len(self.daemon_apps))
        return changed

    def screen(self) -> str:
        sc = STATE_TO_SCREEN[self.m.state]
        if sc == R.HOME and self.mapping is not None:
            return R.MAPPING
        if sc == R.HOME and self.pin_entry is not None:
            return R.PIN
        if sc == R.HOME and self.menu_open:
            return R.MENU
        return sc

    # ---------------------------------------------------------------- gamepads (hot-plug, first-run mapping)
    def device_added(self, iid: int, guid: str, name: str, is_controller: bool, now: float) -> None:
        self.devices[iid] = {"guid": guid, "name": name, "controller": is_controller}
        self.last_device = iid
        log.info("gamepad added: %s guid %s (%s)", name, G.db_guid(guid), "mapped" if is_controller else "UNMAPPED")
        if G.needs_first_run_mapping(guid, is_controller, G.load_mappings(self.mappings_path),
                                     G.load_seen(self.seen_path)):
            self.offer_queue.append(iid)
            self._start_queued_mapping(now)

    def device_removed(self, iid: int, now: float) -> None:
        self.devices.pop(iid, None)
        if iid in self.offer_queue:
            self.offer_queue.remove(iid)
        if self.mapping is not None and self.mapping.instance_id == iid:
            # Pad switched off mid-mapping: drop the session WITHOUT marking it seen; it is offered again on return.
            log.info("gamepad removed during mapping; will offer again when it returns")
            self.mapping = None

    def _start_queued_mapping(self, now: float) -> None:
        # Never interrupt a stream, a failure screen or pairing: mapping waits for the home screen.
        if self.mapping is not None or self.m.state != S.IDLE:
            return
        while self.offer_queue:
            iid = self.offer_queue.pop(0)
            d = self.devices.get(iid)
            if d:
                self.start_mapping(iid, now)
                return

    def start_mapping(self, iid: int, now: float) -> None:
        d = self.devices[iid]
        self.menu_open = False
        self.pin_entry = None
        self.mapping = G.MappingSession(iid, d["guid"], d["name"], now)

    def raw_event(self, ev, now: float) -> bool:
        """Raw joystick events go to the mapping session while it runs. Returns True when consumed."""
        if self.mapping is None:
            return False
        self.mapping.feed(ev, now)
        self.last_input = now
        return True

    def _tick_mapping(self, now: float) -> None:
        ms = self.mapping
        if ms is None:
            self._start_queued_mapping(now)
            return
        ms.tick(now)
        if ms.done or ms.aborted:
            line = ms.mapping_line() if ms.done else None
            if line:
                try:
                    G.save_mapping(line, self.mappings_path)
                    log.info("saved gamepad mapping: %s", line)
                    self.reload_controllers = True
                    self._play("ready")
                except OSError as exc:
                    log.error("cannot write %s: %s", self.mappings_path, exc)
            else:
                log.info("gamepad mapping %s for %s; the raw-joystick fallback stays in use",
                         "incomplete" if ms.done else "abandoned", ms.guid)
            G.mark_seen(ms.guid, self.seen_path)
            self.mapping = None

    # ---------------------------------------------------------------- input
    def handle(self, actions: list[str], now: float) -> None:
        if not actions:
            return
        if self.mapping is not None:
            return                 # the mapping screen listens to raw events only (see raw_event)
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
                elif item == SETUP_PAD:
                    self._play("select")
                    self.pin_entry = PinEntry()
                elif item == CLOSE:
                    self._play("back")
        elif sc == R.PIN:
            pe = self.pin_entry
            if a in (inp.UP, inp.DOWN, inp.LEFT, inp.RIGHT):
                pe.move(a)
                self._play("move")
            elif a == inp.BACK:
                self.pin_entry = None
                self._play("back")
            elif a == inp.SELECT:
                ok = pe.check(self.cfg.setup_pin, now) and bool(self.cfg.setup_pin)
                self.pin_entry = None
                if ok and self.last_device in self.devices:
                    self.start_mapping(self.last_device, now)
                else:
                    self._play("error")
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
        self._tick_mapping(now)
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
            prompt=self.mapping.prompt if self.mapping else "",
            progress=("%d of %d" % self.mapping.progress) if self.mapping else "",
            pad_name=self.mapping.name if self.mapping else "",
            pin_digits=list(self.pin_entry.digits) if self.pin_entry else [],
            pin_cursor=self.pin_entry.cursor if self.pin_entry else 0,
        )

    def handle_pending(self, p: dict | None, now: float) -> None:
        """A request queued on couch-daemon (e.g. by remote validation over SSH) - same paths as the gamepad."""
        if not p:
            return
        action = p.get("action")
        log.info("daemon request: %s", p)
        self.last_input = now
        self.dim = 0.0
        self.menu_open = False
        if action == "stream" and isinstance(p.get("app"), str):
            self.m.launch(p["app"], now)
        elif action == "quit":
            self.m.force_quit(now)
        elif action == "pair":
            self.m.pair(now)
        elif action == "restart":
            self.b.restart_box()

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

    from .backend import LocalBackend, finalize_crashed_stream
    from .sounds import Sounds

    crashed = finalize_crashed_stream()
    if crashed:
        log.warning("the previous launcher died while streaming %r after %.1f s (recorded as %s); moonlight said: %s",
                    crashed["app"], crashed["duration_s"], crashed["outcome"], " | ".join(crashed["moonlight_tail"][-3:]))
    # The kiosk is a bare X server (couch-kiosk.service), so x11 first whenever a DISPLAY exists; the Wayland driver
    # stays as the alternative for a Wayland kiosk.
    wanted = os.environ.get("SDL_VIDEODRIVER")
    order = ["x11", "wayland"] if os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY") else \
        ["wayland", "x11"]
    for drv in ([wanted] if wanted else []) + order:
        os.environ["SDL_VIDEODRIVER"] = drv
        try:
            pygame.display.init()
            break
        except pygame.error as exc:
            log.warning("SDL video driver %s unavailable: %s", drv, exc)
    else:
        log.error("no usable SDL video driver")
        return 3
    n_maps = G.apply_to_environment()          # /etc/couch/gamepads.txt -> SDL, before controllers initialise
    pygame.init()
    controller.init()
    log.info("loaded %d saved gamepad mapping(s)", n_maps)
    pygame.mouse.set_visible(False)
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    pygame.display.set_caption("Couch")
    cfg = load(config_path)
    watcher = Watcher(config_path)
    backend = _make_backend(cfg, LocalBackend)
    app = App(cfg, backend, now=time.monotonic(), sounds=Sounds(), display_power=_display_power_hook)
    renderer = R.Renderer(screen.get_size())
    mapper = inp.Mapper()
    devices = G.Devices(G.SdlAdapter())
    clock = pygame.time.Clock()
    next_slow = 0.0
    exit_at = time.monotonic() + float(os.environ["COUCH_EXIT_AFTER"]) if os.environ.get("COUCH_EXIT_AFTER") else None
    if hasattr(backend, "apps"):
        app.set_daemon_apps(backend.apps())        # the persisted list: tiles are there on the first frame
    log.info("launcher up: %sx%s, host %s, %d tiles", *screen.get_size(), cfg.host, len(app.grid.tiles))
    try:
        while not app.quit_requested:
            now = time.monotonic()
            if exit_at is not None and now >= exit_at:      # tests only
                break
            actions: list[str] = []
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    app.quit_requested = True
                elif ev.type in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED, pygame.CONTROLLERDEVICEADDED,
                                 pygame.CONTROLLERDEVICEREMOVED):
                    devices.handle(ev, app, mapper, now)       # hot-plug and pad off/on, no restart needed
                elif ev.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP, pygame.JOYHATMOTION,
                                 pygame.JOYAXISMOTION) and app.raw_event(ev, now):
                    continue                                   # the first-run mapping screen owns raw input
                actions += mapper.feed(ev, now)
            actions += mapper.tick(now)
            app.pads = devices.count
            if app.reload_controllers:
                app.reload_controllers = False
                log.info("reloaded %d gamepad mapping(s)", devices.reload_mappings(app.mappings_path))
            app.handle(actions, now)
            app.tick(now)
            if now >= next_slow:
                next_slow = now + 1.0
                new = watcher.poll()
                if new is not None:
                    log.info("config reloaded%s", f" with problems: {new.error}" if new.error else "")
                    app.apply_config(new)
                app.refresh_health()
                if hasattr(backend, "apps"):
                    app.set_daemon_apps(backend.apps())
                if hasattr(backend, "pending"):
                    app.handle_pending(backend.pending(), now)
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
