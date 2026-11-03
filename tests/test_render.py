"""Headless golden renders of every launcher screen at 1920x1080 and 3840x2160 (SDL dummy driver), pixel-diffed
against tests/goldens, plus the per-frame budget (< 8 ms at 4K on CPU). Regenerate deliberately with
    UPDATE_GOLDENS=1 python -m pytest tests/test_render.py"""
import os
import time

import pygame
import pytest

from couch.launcher import render as R
from couch.launcher.samples import sample_views
from conftest import REPO

GOLDEN_DIR = os.path.join(REPO, "tests", "goldens")
SIZES = {"1080p": (1920, 1080), "2160p": (3840, 2160)}
TOLERANCE = 8          # per-channel difference allowed (antialiasing noise)
MAX_BAD_FRACTION = 0.001
FRAME_BUDGET_MS = 8.0


@pytest.fixture(scope="module", autouse=True)
def sdl():
    pygame.init()
    yield


def diff_count(a: pygame.Surface, b: pygame.Surface) -> int:
    """Pixels whose colour differs by more than TOLERANCE on any channel."""
    d1, d2 = a.copy(), b.copy()
    d1.blit(b, (0, 0), special_flags=pygame.BLEND_RGB_SUB)
    d2.blit(a, (0, 0), special_flags=pygame.BLEND_RGB_SUB)
    d1.blit(d2, (0, 0), special_flags=pygame.BLEND_RGB_ADD)          # |a-b| per channel
    ok = pygame.mask.from_threshold(d1, (0, 0, 0, 255), (TOLERANCE + 1, TOLERANCE + 1, TOLERANCE + 1, 255))
    return a.get_width() * a.get_height() - ok.count()


@pytest.mark.parametrize("res", list(SIZES))
@pytest.mark.parametrize("name", list(sample_views()))
def test_golden(res, name, tmp_path):
    size = SIZES[res]
    surf = pygame.Surface(size)
    R.Renderer(size, art_root=str(tmp_path)).draw(surf, sample_views()[name])
    path = os.path.join(GOLDEN_DIR, res, f"{name}.png")
    if os.environ.get("UPDATE_GOLDENS"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pygame.image.save(surf, path)
        pytest.skip("golden updated")
    assert os.path.exists(path), f"missing golden {path}; run with UPDATE_GOLDENS=1"
    golden = pygame.image.load(path)
    assert golden.get_size() == size
    bad = diff_count(surf, golden.convert(surf) if pygame.display.get_surface() else golden)
    assert bad <= MAX_BAD_FRACTION * size[0] * size[1], f"{name}@{res}: {bad} pixels differ"
    if bad:
        pygame.image.save(surf, str(tmp_path / f"{name}-{res}-actual.png"))


def test_all_seven_screens_have_goldens():
    covered = {v.screen for v in sample_views().values()}
    assert covered == set(R.SCREENS)


def test_diff_detects_a_change(tmp_path):
    size = SIZES["1080p"]
    a, b = pygame.Surface(size), pygame.Surface(size)
    r = R.Renderer(size, art_root=str(tmp_path))
    v = sample_views()["home"]
    r.draw(a, v)
    v2 = sample_views()["home"]
    v2.focus = 2
    R.Renderer(size, art_root=str(tmp_path)).draw(b, v2)
    assert diff_count(a, b) > 1000
    assert diff_count(a, a.copy()) == 0


@pytest.mark.parametrize("name", list(sample_views()))
def test_frame_budget_4k(name, tmp_path):
    """Steady-state frames (focus moving, spinner turning) must stay under 8 ms at 3840x2160 on CPU."""
    size = SIZES["2160p"]
    surf = pygame.Surface(size)
    r = R.Renderer(size, art_root=str(tmp_path))
    v = sample_views()[name]
    r.draw(surf, v)                      # first frame builds the cached static layer (not budgeted)
    times = []
    for i in range(60):
        v.t = i / 30.0
        v.elapsed = 10 + i / 30.0
        if v.screen == R.HOME:
            v.focus = [0, 1, 2, 3, 2, 1][i % 6]     # stays within the first row: no re-layout
        t0 = time.perf_counter()
        r.draw(surf, v)
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    p95 = times[int(len(times) * 0.95)]
    assert p95 < FRAME_BUDGET_MS, f"{name}: p95 {p95:.2f} ms"


def test_box_art_from_disk_and_outside_root_refused(tmp_path):
    art_root = tmp_path / "art"
    art_root.mkdir()
    img = pygame.Surface((60, 90))
    img.fill((255, 0, 255))
    pygame.image.save(img, str(art_root / "x.png"))
    pygame.image.save(img, str(tmp_path / "outside.png"))
    r = R.Renderer((1920, 1080), art_root=str(art_root))
    got = r.art(R.TileView("X", "x.png"), (230, 345))
    assert got.get_at((100, 100))[:3] == (255, 0, 255)
    card = r.art(R.TileView("Y", str(tmp_path / "outside.png")), (230, 345))
    assert card.get_at((100, 100))[:3] != (255, 0, 255)       # fell back to a generated card
    missing = r.art(R.TileView("Z", "nope.png"), (230, 345))
    assert missing.get_size() == (230, 345)
