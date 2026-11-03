"""UI sounds synthesised at start-up (no third-party assets): short, soft sine blips. `python -m
couch.launcher.sounds DIR` also writes them as WAV files for inspection."""
from __future__ import annotations

import io
import math
import struct
import sys
import wave

RATE = 22050

# name -> list of (frequency Hz, duration s); quiet and short so they never annoy at 10 ft.
SPECS = {
    "move": [(660, 0.035)],
    "select": [(523, 0.05), (784, 0.07)],
    "back": [(523, 0.05), (392, 0.07)],
    "error": [(330, 0.12), (262, 0.18)],
    "ready": [(523, 0.06), (659, 0.06), (784, 0.10)],
}
VOLUME = 0.25


def pcm16(spec: list[tuple[float, float]]) -> bytes:
    out = bytearray()
    for freq, dur in spec:
        n = int(RATE * dur)
        fade = max(1, int(RATE * 0.008))
        for i in range(n):
            env = min(1.0, i / fade, (n - i) / fade)
            v = int(32767 * VOLUME * env * math.sin(2 * math.pi * freq * i / RATE))
            out += struct.pack("<h", v)
    return bytes(out)


def wav_bytes(name: str) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm16(SPECS[name]))
    return buf.getvalue()


class Sounds:
    """Loads all sounds into pygame.mixer if audio works; otherwise every play() is a silent no-op."""

    def __init__(self) -> None:
        self._s = {}
        try:
            import pygame
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=RATE, size=-16, channels=1)
            for name in SPECS:
                self._s[name] = pygame.mixer.Sound(file=io.BytesIO(wav_bytes(name)))
        except Exception:   # no sound card / HDMI audio not ready: the launcher must still work
            self._s = {}

    def play(self, name: str) -> None:
        s = self._s.get(name)
        if s is not None:
            try:
                s.play()
            except Exception:
                pass


if __name__ == "__main__":
    import os
    d = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(d, exist_ok=True)
    for name in SPECS:
        with open(os.path.join(d, f"{name}.wav"), "wb") as fh:
            fh.write(wav_bytes(name))
        print(os.path.join(d, f"{name}.wav"))
