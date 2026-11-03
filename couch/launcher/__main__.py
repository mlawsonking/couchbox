import os
import sys


def selftest() -> int:
    """Render every screen headless at 1080p and 2160p with the installed pygame-ce (install/doctor smoke test)."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    import pygame

    from . import render as R
    from .samples import sample_views
    pygame.init()
    for size in ((1920, 1080), (3840, 2160)):
        surf = pygame.Surface(size)
        r = R.Renderer(size)
        for name, v in sample_views().items():
            r.draw(surf, v)
    print(f"couch launcher selftest ok: pygame-ce {pygame.version.ver}, SDL {'.'.join(map(str, pygame.get_sdl_version()))}")
    return 0


if "--selftest" in sys.argv[1:]:
    sys.exit(selftest())

from .app import main  # noqa: E402

sys.exit(main(os.environ.get("COUCH_CONFIG", "/etc/couch/couch.toml")))
