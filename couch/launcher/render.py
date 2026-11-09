"""Drawing for every launcher screen. Pure function of a `View` (no state machine, no I/O except reading box art
from disk once). Cheap per frame: a screen's static layer is rendered once into a cached surface when the view's
`key` changes; afterwards only the small animated regions (focus ring, spinner, clock) are redrawn and returned as
dirty rectangles. Text surfaces are cached. The bundled pygame font is used so renders are byte-stable everywhere.

Palette: Okabe-Ito colour-blind-safe hues on a near-black background; state is never shown by colour alone
(every badge has a word and a shape)."""
from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass, field

import pygame

# ---------------------------------------------------------------- palette (10-foot, high contrast, CVD-safe)
BG = (11, 18, 32)
PANEL = (24, 34, 54)
TEXT = (245, 247, 250)
MUTED = (170, 180, 196)
FOCUS = (240, 228, 66)        # Okabe-Ito yellow: the focus ring, unmistakable at 10 ft
OK = (0, 158, 115)            # bluish green
WARN = (230, 159, 0)          # orange
BAD = (213, 94, 0)            # vermillion
INFO = (86, 180, 233)         # sky blue
CARD_HUES = [(0, 114, 178), (0, 158, 115), (204, 121, 167), (230, 159, 0), (86, 180, 233), (213, 94, 0)]

HOME, WAKING, CONNECTING, STREAMING, LOST, PAIR, MENU, MAPPING, PIN = (
    "HOME", "WAKING", "CONNECTING", "STREAMING", "LOST", "PAIR", "MENU", "MAPPING", "PIN")
SCREENS = (HOME, WAKING, CONNECTING, STREAMING, LOST, PAIR, MENU, MAPPING, PIN)


@dataclass
class TileView:
    name: str
    art: str = ""
    badge: str = ""              # "Resume": a session this box started is still running on the game PC


@dataclass
class View:
    screen: str
    tiles: list[TileView] = field(default_factory=list)
    cols: int = 4
    focus: int = 0
    app: str = ""
    host_state: str = "unknown"            # awake | asleep | unknown
    pads: int = 0
    link_bad: bool = False
    health: str = "green"                  # green | yellow | red
    kid: str = ""
    parent: str = ""
    pin: str = ""
    menu_items: list[str] = field(default_factory=list)
    menu_focus: int = 0
    elapsed: float = 0.0
    t: float = 0.0                          # animation clock
    dim: float = 0.0                        # 0..1 attract-mode dimming; 1.0 = display asleep (black)
    notice: str = ""                        # small parent line on HOME (config problems)
    prompt: str = ""                        # MAPPING: what to press now
    progress: str = ""                      # MAPPING: "3 of 14"
    pad_name: str = ""                      # MAPPING: the device being mapped
    pin_digits: list = field(default_factory=list)   # PIN entry
    pin_cursor: int = 0

    def key(self) -> tuple:
        """Everything that changes the static layer (NOT focus, t, elapsed: those are animated regions)."""
        return (self.screen, tuple((x.name, x.art, x.badge) for x in self.tiles), self.cols, self.app, self.host_state,
                self.pads, self.link_bad, self.health, self.kid, self.parent, self.pin, tuple(self.menu_items),
                self.notice, round(self.dim, 2), self.prompt, self.progress, self.pad_name,
                tuple(self.pin_digits), self.pin_cursor)


class Renderer:
    def __init__(self, size: tuple[int, int], art_root: str = "/var/lib/couch/art"):
        self.w, self.h = size
        self.s = self.h / 1080.0
        self.art_root = art_root
        self._fonts: dict[int, pygame.font.Font] = {}
        self._text: dict[tuple, pygame.Surface] = {}
        self._art: dict[tuple, pygame.Surface] = {}
        self._base: pygame.Surface | None = None
        self._base_key: tuple | None = None
        self._last_dyn: list[pygame.Rect] = []

    # ------------------------------------------------------------ primitives
    def px(self, v: float) -> int:
        return max(1, int(round(v * self.s)))

    def font(self, size: int) -> pygame.font.Font:
        sz = self.px(size)
        if sz not in self._fonts:
            self._fonts[sz] = pygame.font.Font(None, sz)
        return self._fonts[sz]

    def text(self, s: str, size: int, color=TEXT) -> pygame.Surface:
        k = (s, size, color)
        surf = self._text.get(k)
        if surf is None:
            surf = self.font(size).render(s, True, color)
            if len(self._text) > 512:
                self._text.clear()
            self._text[k] = surf
        return surf

    def wrap(self, s: str, size: int, width: int) -> list[str]:
        f = self.font(size)
        lines, cur = [], ""
        for word in s.split():
            trial = (cur + " " + word).strip()
            if f.size(trial)[0] <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines

    def blit_center(self, surf: pygame.Surface, dst: pygame.Surface, cx: int, y: int) -> pygame.Rect:
        r = surf.get_rect(midtop=(cx, y))
        dst.blit(surf, r)
        return r

    def paragraph(self, dst, s: str, size: int, color, cx: int, y: int, width: int) -> int:
        for line in self.wrap(s, size, width):
            r = self.blit_center(self.text(line, size, color), dst, cx, y)
            y = r.bottom + self.px(size * 0.25)
        return y

    # ------------------------------------------------------------ layout
    # Tiles are box-art shaped (2:3). Grid area: below the title, above the status strip; two rows visible.
    TILE_W, TILE_H, LABEL_H, ROW_GAP, COL_GAP, GRID_TOP, GRID_BOTTOM = 230, 345, 60, 20, 48, 140, 990

    def row_pitch(self) -> int:
        return self.px(self.TILE_H + self.LABEL_H + self.ROW_GAP)

    def tile_rects(self, n: int, cols: int) -> list[pygame.Rect]:
        tw, th, gap = self.px(self.TILE_W), self.px(self.TILE_H), self.px(self.COL_GAP)
        rows = max(1, (n + cols - 1) // cols)
        vis = min(rows, 2)
        block_h = vis * self.px(self.TILE_H + self.LABEL_H) + (vis - 1) * self.px(self.ROW_GAP)
        total_w = cols * tw + (cols - 1) * gap
        x0 = (self.w - total_w) // 2
        area_top, area_h = self.px(self.GRID_TOP), self.px(self.GRID_BOTTOM - self.GRID_TOP)
        y0 = area_top + max(0, (area_h - block_h) // 2)
        out = []
        for i in range(n):
            r, c = divmod(i, cols)
            out.append(pygame.Rect(x0 + c * (tw + gap), y0 + r * self.row_pitch(), tw, th))
        return out

    def scroll_offset(self, focus: int, cols: int, n: int) -> int:
        """Rows scroll so the focused row is visible (2 rows on screen)."""
        return max(0, focus // cols - 1) * self.row_pitch()

    # ------------------------------------------------------------ box art / generated cards
    def card(self, name: str, size: tuple[int, int]) -> pygame.Surface:
        k = ("card", name, size)
        if k in self._art:
            return self._art[k]
        digest = hashlib.sha256(name.encode()).digest()
        hue = CARD_HUES[digest[0] % len(CARD_HUES)]
        surf = pygame.Surface(size)
        surf.fill(hue)
        w, h = size
        # Generated title card: a big initial on a colour derived from the name (the name is printed under the tile).
        band = pygame.Rect(0, int(h * 0.72), w, int(h * 0.28))
        pygame.draw.rect(surf, tuple(int(c * 0.55) for c in hue), band)
        initial = self.font(200).render(name[:1].upper() or "?", True, TEXT)
        surf.blit(initial, initial.get_rect(center=(w // 2, int(h * 0.40))))
        self._art[k] = surf
        return surf

    def art(self, tile: TileView, size: tuple[int, int]) -> pygame.Surface:
        if tile.art:
            path = tile.art if os.path.isabs(tile.art) else os.path.join(self.art_root, tile.art)
            real = os.path.realpath(path)
            k = ("art", real, size)
            if k in self._art:
                return self._art[k]
            # Only files under the art root are read (config cannot point the launcher at arbitrary files).
            if real.startswith(os.path.realpath(self.art_root) + os.sep) and os.path.isfile(real):
                try:
                    img = pygame.image.load(real)
                    img = pygame.transform.smoothscale(img.convert() if pygame.display.get_init()
                                                       and pygame.display.get_surface() else img, size)
                    self._art[k] = img
                    return img
                except (pygame.error, ValueError):
                    pass
        return self.card(tile.name, size)

    # ------------------------------------------------------------ status strip
    def status_strip(self, dst, v: View) -> None:
        h = self.px(90)
        strip = pygame.Rect(0, self.h - h, self.w, h)
        pygame.draw.rect(dst, PANEL, strip)
        x = self.px(48)
        cy = strip.centery
        host_txt, host_col = {"awake": ("Game PC: awake", OK), "asleep": ("Game PC: asleep", MUTED)}.get(
            v.host_state, ("Game PC: checking", MUTED))
        items = [(host_txt, host_col, "dot"),
                 (f"Gamepads: {v.pads}" if v.pads else "No gamepad", OK if v.pads else WARN,
                  "dot" if v.pads else "tri")]
        if v.link_bad:
            items.append(("Wi-Fi link: slow", WARN, "tri"))
        if v.health != "green":
            items.append(("Box needs a check" if v.health == "red" else "Box: minor issue",
                          BAD if v.health == "red" else WARN, "tri"))
        for label, col, shape in items:
            r = self.px(12)
            if shape == "dot":
                pygame.draw.circle(dst, col, (x + r, cy), r)
            else:
                pygame.draw.polygon(dst, col, [(x, cy + r), (x + 2 * r, cy + r), (x + r, cy - r)])
            t = self.text(label, 34, TEXT)
            dst.blit(t, t.get_rect(midleft=(x + 2 * r + self.px(12), cy)))
            x += 2 * r + self.px(12) + t.get_width() + self.px(56)
        hint = self.text("A  Play     Start  Menu", 30, MUTED)
        dst.blit(hint, hint.get_rect(midright=(self.w - self.px(48), cy)))

    def hint(self, dst, s: str) -> None:
        self.blit_center(self.text(s, 34, MUTED), dst, self.w // 2, self.h - self.px(110))

    # ------------------------------------------------------------ static layers
    def _base_layer(self, v: View) -> pygame.Surface:
        base = pygame.Surface((self.w, self.h))
        base.fill(BG)
        cx = self.w // 2
        if v.screen in (HOME, MENU):
            base.blit(self.text("Couch", 64, TEXT), (self.px(48), self.px(40)))
            rects = self.tile_rects(len(v.tiles), v.cols)
            off = self.scroll_offset(v.focus, v.cols, len(v.tiles))
            clip = pygame.Rect(0, self.px(self.GRID_TOP - 20), self.w, self.px(self.GRID_BOTTOM - self.GRID_TOP + 20))
            base.set_clip(clip)
            for tile, r in zip(v.tiles, rects):
                rr = r.move(0, -off)
                base.blit(self.art(tile, rr.size), rr)
                if tile.badge:
                    t = self.text(tile.badge, 34, BG)
                    pill = t.get_rect().inflate(self.px(36), self.px(18))
                    pill.topleft = (rr.x + self.px(12), rr.y + self.px(12))
                    pygame.draw.rect(base, INFO, pill, border_radius=pill.height // 2)
                    base.blit(t, t.get_rect(center=pill.center))
                label = self.text(tile.name if len(tile.name) <= 28 else tile.name[:25] + "...", 36, TEXT)
                base.blit(label, label.get_rect(midtop=(rr.centerx, rr.bottom + self.px(14))))
            base.set_clip(None)
            if v.notice:
                self.blit_center(self.text(v.notice[:140], 26, WARN), base, cx, self.h - self.px(128))
            self.status_strip(base, v)
            if v.screen == MENU:
                shade = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
                shade.fill((0, 0, 0, 170))
                base.blit(shade, (0, 0))
                panel = pygame.Rect(0, 0, self.px(760), self.px(150 + 110 * len(v.menu_items)))
                panel.center = (cx, self.h // 2)
                pygame.draw.rect(base, PANEL, panel, border_radius=self.px(24))
                self.blit_center(self.text("Menu", 52, TEXT), base, cx, panel.top + self.px(30))
                for i, item in enumerate(v.menu_items):
                    t = self.text(item, 44, TEXT)
                    base.blit(t, t.get_rect(center=(cx, panel.top + self.px(150 + 110 * i))))
        elif v.screen in (WAKING, CONNECTING):
            title = "Waking up the game PC" if v.screen == WAKING else f"Starting {v.app}"
            self.paragraph(base, title, 80, TEXT, cx, self.px(300), self.w - self.px(240))
            sub = ("This can take a minute." if v.screen == WAKING else "Get your controller ready!")
            self.blit_center(self.text(sub, 44, MUTED), base, cx, self.px(470))
            self.hint(base, "B  Go back")
        elif v.screen == STREAMING:
            self.blit_center(self.text(f"Playing {v.app}", 48, MUTED), base, cx, self.h // 2)
        elif v.screen == LOST:
            pygame.draw.polygon(base, BAD, [(cx - self.px(70), self.px(300)), (cx + self.px(70), self.px(300)),
                                            (cx, self.px(180))])
            self.blit_center(self.text("!", 90, TEXT), base, cx, self.px(205))
            y = self.paragraph(base, v.kid or "Something went wrong.", 72, TEXT, cx, self.px(350),
                               self.w - self.px(240))
            self.blit_center(self.text("A  Try again        B  Home", 52, FOCUS), base, cx, y + self.px(60))
            # Parent-readable detail line, small, at the bottom.
            pygame.draw.rect(base, PANEL, pygame.Rect(0, self.h - self.px(150), self.w, self.px(150)))
            self.paragraph(base, "For grown-ups: " + (v.parent or "no details"), 28, MUTED, cx,
                           self.h - self.px(130), self.w - self.px(160))
        elif v.screen == MAPPING:
            self.blit_center(self.text("New controller!", 64, TEXT), base, cx, self.px(130))
            self.blit_center(self.text(v.pad_name[:50], 36, MUTED), base, cx, self.px(220))
            self.paragraph(base, v.prompt, 84, FOCUS, cx, self.px(380), self.w - self.px(240))
            self.blit_center(self.text(v.progress, 44, TEXT), base, cx, self.px(620))
            self.paragraph(base, "Does your controller not have that button? Just wait - it is skipped after 10 "
                                 "seconds.", 32, MUTED, cx, self.px(760), self.w - self.px(360))
        elif v.screen == PIN:
            self.blit_center(self.text("Grown-up code", 64, TEXT), base, cx, self.px(200))
            boxw, gap = self.px(140), self.px(40)
            x0 = cx - (4 * boxw + 3 * gap) // 2
            for i, d in enumerate(v.pin_digits or [0, 0, 0, 0]):
                r = pygame.Rect(x0 + i * (boxw + gap), self.px(380), boxw, self.px(180))
                pygame.draw.rect(base, PANEL, r, border_radius=self.px(16))
                pygame.draw.rect(base, FOCUS if i == v.pin_cursor else MUTED, r, width=self.px(8 if i == v.pin_cursor
                                                                                                else 3),
                                 border_radius=self.px(16))
                t = self.text(str(d), 120, TEXT)
                base.blit(t, t.get_rect(center=r.center))
            self.hint(base, "Up/Down change   Left/Right move   A  OK   B  Cancel")
        elif v.screen == PAIR:
            self.paragraph(base, "Ask a grown-up to type this PIN on the game PC", 60, TEXT, cx, self.px(170),
                           self.w - self.px(240))
            pin = self.text(" ".join(v.pin or "----"), 220, FOCUS)
            self.blit_center(pin, base, cx, self.px(380))
            self.paragraph(base, "On the game PC: open Sunshine's web page (https://localhost:47990), go to PIN, "
                                 "type the four digits and a name for this box.", 32, MUTED, cx, self.px(700),
                           self.w - self.px(360))
            self.hint(base, "B  Cancel")
        if v.dim >= 1.0:
            base.fill((0, 0, 0))
        elif v.dim > 0:
            shade = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
            shade.fill((0, 0, 0, int(200 * v.dim)))
            base.blit(shade, (0, 0))
        return base

    # ------------------------------------------------------------ animated regions
    def _dynamic(self, dst, v: View) -> list[pygame.Rect]:
        rects: list[pygame.Rect] = []
        if v.dim >= 1.0:
            return rects
        cx = self.w // 2
        if v.screen == HOME and v.tiles:
            r = self.tile_rects(len(v.tiles), v.cols)[min(v.focus, len(v.tiles) - 1)]
            r = r.move(0, -self.scroll_offset(v.focus, v.cols, len(v.tiles)))
            pad = self.px(12)
            ring = r.inflate(2 * pad, 2 * pad)
            pygame.draw.rect(dst, FOCUS, ring, width=self.px(10), border_radius=self.px(18))
            rects.append(ring.inflate(4, 4))
        elif v.screen == MENU and v.menu_items:
            panel_h = self.px(150 + 110 * len(v.menu_items))
            top = self.h // 2 - panel_h // 2
            row = pygame.Rect(0, 0, self.px(640), self.px(84))
            row.center = (cx, top + self.px(150 + 110 * v.menu_focus))
            pygame.draw.rect(dst, FOCUS, row, width=self.px(8), border_radius=self.px(14))
            rects.append(row.inflate(4, 4))
        elif v.screen in (WAKING, CONNECTING):
            c = (cx, self.px(640))
            rad = self.px(56)
            box = pygame.Rect(0, 0, 2 * rad + self.px(24), 2 * rad + self.px(24))
            box.center = c
            for i in range(8):
                a = (i / 8.0) * 2 * math.pi + v.t * 4.0
                phase = (i + int(v.t * 8)) % 8
                col = FOCUS if phase == 0 else (INFO if phase < 3 else PANEL)
                pygame.draw.circle(dst, col, (c[0] + int(rad * math.cos(a)), c[1] + int(rad * math.sin(a))),
                                   self.px(10))
            rects.append(box)
            secs = self.text(f"{int(v.elapsed)} s", 40, MUTED)
            sr = secs.get_rect(midtop=(cx, self.px(740)))
            dst.blit(secs, sr)
            rects.append(sr.inflate(self.px(160), 4))
        return rects

    def draw(self, dst: pygame.Surface, v: View) -> list[pygame.Rect]:
        """Draw `v` onto dst. Returns dirty rectangles (the whole screen when the static layer changed)."""
        k = v.key() + ((v.focus // max(1, v.cols)) if v.screen == HOME else 0,)
        if k != self._base_key or self._base is None:
            self._base = self._base_layer(v)
            self._base_key = k
            dst.blit(self._base, (0, 0))
            self._last_dyn = self._dynamic(dst, v)
            return [dst.get_rect()]
        # Restore the previous animated regions from the static layer, then draw the new ones.
        dirty = list(self._last_dyn)
        for r in self._last_dyn:
            dst.blit(self._base, r, r)
        self._last_dyn = self._dynamic(dst, v)
        return dirty + self._last_dyn
