"""Deterministic sample views of every screen: the golden renders (tests/goldens) and docs screenshots use these."""
from __future__ import annotations

from . import render as R
from .state import REASONS

SAMPLE_TILES = ["Steam Big Picture", "Desktop", "Hollow Knight", "Celeste", "Rocket League", "Stardew Valley"]


def sample_views() -> dict[str, R.View]:
    tiles = [R.TileView(n) for n in SAMPLE_TILES]
    kid, parent = REASONS["STREAM_LOST"]
    return {
        "home": R.View(R.HOME, tiles=tiles, focus=1, host_state="asleep", pads=1, link_bad=True),
        "home-scrolled": R.View(R.HOME, tiles=tiles, focus=5, host_state="awake", pads=2, health="red"),
        "waking": R.View(R.WAKING, elapsed=12, t=0.25),
        "connecting": R.View(R.CONNECTING, app="Celeste", elapsed=3, t=0.5),
        "streaming": R.View(R.STREAMING, app="Celeste"),
        "lost": R.View(R.LOST, kid=kid, parent=parent.format(app="Celeste", code=1)),
        "pair": R.View(R.PAIR, pin="4821"),
        "menu": R.View(R.MENU, tiles=tiles, menu_items=["Restart the box", "Pair with the game PC", "Back"],
                       menu_focus=1, pads=2, host_state="awake"),
        "mapping": R.View(R.MAPPING, prompt="Press the D-pad LEFT", progress="7 of 14",
                          pad_name="Generic 2.4G Gamepad (0079:18d4)"),
        "pin": R.View(R.PIN, pin_digits=[4, 0, 0, 0], pin_cursor=1),
        "attract": R.View(R.HOME, tiles=tiles, focus=0, host_state="asleep", pads=1, dim=0.6),
    }
