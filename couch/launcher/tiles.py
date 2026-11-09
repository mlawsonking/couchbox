"""Tile grid and Start-menu models (pure; no SDL)."""
from __future__ import annotations

from dataclasses import dataclass

from . import input as inp
from .config import Tile, is_hidden

COLS_MAX = 4


def merge_tiles(base: list[Tile], daemon_apps: list[str], hide=()) -> list[Tile]:
    """The home grid: the configured tiles ([[tile]] blocks, then the Steam-library tiles with art, in that order -
    Desktop and Steam Big Picture keep their place), then one generated card for every other app couch-daemon lists
    from Sunshine, sorted by name. Never a duplicate (same app name, case/space-insensitive), never a hidden app.
    The daemon only lists what its allow-list will launch, so no tile here makes anything new playable."""
    def key(app: str) -> str:
        return " ".join(app.casefold().split())
    seen = {key(t.app) for t in base}
    extras: dict[str, Tile] = {}
    for app in daemon_apps:
        if not isinstance(app, str) or not app.strip() or is_hidden(app, hide):
            continue
        k = key(app)
        if k not in seen and k not in extras:
            extras[k] = Tile(app.strip()[:40], app.strip())
    return list(base) + sorted(extras.values(), key=lambda t: (key(t.app), t.app))


@dataclass
class Grid:
    tiles: list[Tile]
    cols: int = COLS_MAX
    focus: int = 0

    def __post_init__(self) -> None:
        self.cols = max(1, min(self.cols, len(self.tiles) or 1))
        self.focus = min(self.focus, max(0, len(self.tiles) - 1))

    @property
    def rows(self) -> int:
        return (len(self.tiles) + self.cols - 1) // self.cols if self.tiles else 0

    def pos(self, i: int | None = None) -> tuple[int, int]:
        i = self.focus if i is None else i
        return divmod(i, self.cols)

    def move(self, action: str) -> bool:
        """Move focus; returns True if it changed. Edges stop (no wrap: predictable for small kids)."""
        if not self.tiles:
            return False
        r, c = self.pos()
        n = len(self.tiles)
        if action == inp.LEFT and c > 0:
            new = self.focus - 1
        elif action == inp.RIGHT and c < self.cols - 1 and self.focus + 1 < n:
            new = self.focus + 1
        elif action == inp.UP and r > 0:
            new = self.focus - self.cols
        elif action == inp.DOWN and r < self.rows - 1:
            new = min(self.focus + self.cols, n - 1)
        else:
            return False
        self.focus = new
        return True

    def selected(self) -> Tile | None:
        return self.tiles[self.focus] if self.tiles else None

    def replace(self, tiles: list[Tile]) -> None:
        """Hot-reload: keep focus on the same app name if it still exists."""
        cur = self.selected()
        self.tiles = list(tiles)
        self.cols = max(1, min(COLS_MAX, len(self.tiles) or 1))
        names = [t.app for t in self.tiles]
        self.focus = names.index(cur.app) if cur and cur.app in names else 0


RESTART, PAIR, CLOSE = "Restart the box", "Pair with the game PC", "Back"
SETUP_PAD = "Set up a controller"


@dataclass
class Menu:
    pair_allowed: bool = True
    focus: int = 0
    pad_setup_allowed: bool = False      # only when couch.toml [gamepad] setup_pin is set (PIN-gated)

    @property
    def items(self) -> list[str]:
        items = [RESTART] + ([PAIR] if self.pair_allowed else []) + ([SETUP_PAD] if self.pad_setup_allowed else [])
        return items + [CLOSE]

    def move(self, action: str) -> bool:
        if action == inp.UP and self.focus > 0:
            self.focus -= 1
            return True
        if action == inp.DOWN and self.focus < len(self.items) - 1:
            self.focus += 1
            return True
        return False

    def selected(self) -> str:
        return self.items[min(self.focus, len(self.items) - 1)]


@dataclass
class PinEntry:
    """Four digits entered with the D-pad: up/down change the digit, left/right move, A checks, B cancels.
    Three wrong tries lock it for 5 minutes (a kid can't brute-force it from the couch)."""
    digits: list = None
    cursor: int = 0
    tries: int = 0
    locked_until: float = 0.0

    def __post_init__(self) -> None:
        self.digits = [0, 0, 0, 0]

    def reset(self) -> None:
        self.digits, self.cursor = [0, 0, 0, 0], 0

    def move(self, action: str) -> None:
        if action == inp.UP:
            self.digits[self.cursor] = (self.digits[self.cursor] + 1) % 10
        elif action == inp.DOWN:
            self.digits[self.cursor] = (self.digits[self.cursor] - 1) % 10
        elif action == inp.LEFT and self.cursor > 0:
            self.cursor -= 1
        elif action == inp.RIGHT and self.cursor < 3:
            self.cursor += 1

    def check(self, pin: str, now: float) -> bool:
        if now < self.locked_until:
            return False
        ok = "".join(map(str, self.digits)) == pin
        if ok:
            self.tries = 0
        else:
            self.tries += 1
            if self.tries >= 3:
                self.tries, self.locked_until = 0, now + 300
        self.reset()
        return ok
