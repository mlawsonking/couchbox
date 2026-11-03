"""Tile grid and Start-menu models (pure; no SDL)."""
from __future__ import annotations

from dataclasses import dataclass

from . import input as inp
from .config import Tile

COLS_MAX = 4


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


@dataclass
class Menu:
    pair_allowed: bool = True
    focus: int = 0

    @property
    def items(self) -> list[str]:
        return [RESTART, PAIR, CLOSE] if self.pair_allowed else [RESTART, CLOSE]

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
