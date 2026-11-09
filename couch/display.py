"""Make the X server's mode match what is shown on it (the kiosk runs a bare Xorg server with no window manager).

Why this exists (NUC, 2026-11-09): without a window manager nothing honours a fullscreen request. moonlight-qt 6.2.0
(SDL3 via sdl2-compat) then falls back to a window the size of the video stream, centered: a 1920x1080 stream on the
TV's 3840x2160 X screen was drawn 1:1 in the middle, about 25% of the picture. The fix is to make the X mode equal the
stream mode: the window is then exactly the screen, Moonlight decodes at the stream size as before, and the TV does the
scaling. `base` (kiosk start-up) puts the screen on the stream's default mode so a normal stream changes nothing;
`ensure` (every stream start) follows a stream of another size and hands back what to restore afterwards.

Everything goes through the `xrandr` CLI (x11-xserver-utils, already installed). $COUCH_XRANDR overrides the binary
(tests point it at a fake; the test suite points it at a non-existent file so a developer's real display is never
touched). No DISPLAY, no xrandr or a failing xrandr = "could not change", never an exception."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

TIMEOUT = 10.0
SPEC_RE = re.compile(r"^(\d{3,5})x(\d{3,5})(?:@(\d{2,3}))?$")


@dataclass(frozen=True)
class Mode:
    width: int
    height: int
    rate: str            # exactly as xrandr prints it ("60.00"), so it can be passed back to --rate
    current: bool = False
    preferred: bool = False

    @property
    def label(self) -> str:
        return f"{self.width}x{self.height}@{self.rate}"


@dataclass
class Output:
    name: str
    connected: bool
    primary: bool = False
    modes: list[Mode] = field(default_factory=list)

    @property
    def current(self) -> Mode | None:
        return next((m for m in self.modes if m.current), None)


_OUT_RE = re.compile(r"^(\S+) (connected|disconnected)(?: (primary))?")
_MODE_RE = re.compile(r"^\s+(\d+)x(\d+)(i?)\s+(.*)$")
_RATE_RE = re.compile(r"(\d+(?:\.\d+)?)([*+]*)")


def parse_query(text: str) -> list[Output]:
    """`xrandr --query` -> outputs with their progressive modes (interlaced ones are never used for a stream)."""
    outs: list[Output] = []
    for line in text.splitlines():
        m = _OUT_RE.match(line)
        if m:
            outs.append(Output(m.group(1), m.group(2) == "connected", m.group(3) == "primary"))
            continue
        m = _MODE_RE.match(line)
        if m and outs and not m.group(3):
            for rate, flags in _RATE_RE.findall(m.group(4)):
                outs[-1].modes.append(Mode(int(m.group(1)), int(m.group(2)), rate, "*" in flags, "+" in flags))
    return outs


def pick_output(outs: list[Output]) -> Output | None:
    """The screen the TV is on: the primary connected output, else the first connected one."""
    live = [o for o in outs if o.connected and o.modes]
    return next((o for o in live if o.primary), live[0] if live else None)


def choose_mode(out: Output, width: int, height: int, fps: int) -> Mode | None:
    """The progressive mode of exactly width x height whose refresh is closest to fps (a tie goes to the higher)."""
    cands = [m for m in out.modes if (m.width, m.height) == (width, height)]
    return min(cands, key=lambda m: (abs(float(m.rate) - fps), -float(m.rate)), default=None) if cands else None


def _xrandr_bin() -> str:
    return os.environ.get("COUCH_XRANDR", "xrandr")


def _run(args: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run([_xrandr_bin(), *args], capture_output=True, text=True, timeout=TIMEOUT,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def query() -> Output | None:
    if not os.environ.get("DISPLAY"):
        return None
    rc, text = _run(["--query"])
    return pick_output(parse_query(text)) if rc == 0 else None


def _set(out: Output, mode: Mode) -> tuple[bool, str]:
    rc, text = _run(["--output", out.name, "--mode", f"{mode.width}x{mode.height}", "--rate", mode.rate])
    if rc != 0:
        return False, f"xrandr failed for {out.name} {mode.label}: {text.strip()[-200:]}"
    return True, f"{out.name} -> {mode.label}"


def ensure(width: int, height: int, fps: int) -> tuple[Mode | None, str]:
    """Put the screen on width x height @ ~fps if it is not already. Returns (previous_mode_to_restore, note);
    previous is None when nothing was changed (already right, no exact mode offered, or no display to change)."""
    out = query()
    if out is None:
        return None, "no display to change (no DISPLAY / xrandr)"
    want = choose_mode(out, width, height, fps)
    if want is None:
        return None, f"{out.name} offers no {width}x{height} mode: left at {out.current.label if out.current else '?'}"
    cur = out.current
    if cur is not None and (cur.width, cur.height) == (want.width, want.height) and abs(float(cur.rate) - float(want.rate)) < 0.5:
        return None, f"{out.name} already {cur.label}"
    ok, note = _set(out, want)
    return (cur if ok else None), note


def restore(previous: Mode | None) -> str:
    """Undo an `ensure` (previous is what it returned). Safe with None."""
    if previous is None:
        return "nothing to restore"
    out = query()
    if out is None:
        return "no display to restore"
    return _set(out, previous)[1]


def parse_spec(spec: str) -> tuple[int, int, int] | None:
    """"1920x1080@60" (the @fps is optional, default 60) -> (1920, 1080, 60); "preferred"/garbage -> None."""
    m = SPEC_RE.match((spec or "").strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 60)) if m else None


def apply_base(spec: str) -> str:
    """Kiosk start-up: put the screen on `spec` (couch.toml [display] kiosk_mode). "preferred" leaves the TV's own
    preferred mode (4K, 30 Hz on the NUC's TV) in place."""
    want = parse_spec(spec)
    if want is None:
        return f"kiosk_mode {spec!r}: screen left on the TV's preferred mode"
    return ensure(*want)[1]


def main(argv: list[str]) -> int:
    """python -m couch.display base | status   (the kiosk script calls `base` before the launcher starts)"""
    cmd = argv[0] if argv else "status"
    if cmd == "base":
        from .launcher import config as C
        try:
            spec = C.load().kiosk_mode
        except Exception as exc:          # an unreadable/broken couch.toml must not leave the screen on 4K30
            print(f"couch.toml unreadable ({exc}); using the default {C.Config().kiosk_mode}", file=sys.stderr)
            spec = C.Config().kiosk_mode
        print(apply_base(spec))
        return 0
    if cmd == "status":
        out = query()
        print("no display" if out is None else f"{out.name} {out.current.label if out.current else '?'}")
        return 0
    print("usage: python -m couch.display base|status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
