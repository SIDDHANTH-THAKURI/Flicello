import numpy as np

from vidosuper.graphics.sketch import (InkCanvas, front_mask, gull_flying, gull_perched, horizon_row, illustrate,
                                       paper, schedule, watercolour)


def coast(w: int = 270, h: int = 480) -> np.ndarray:
    """Sky over sea, a dark tree on the right, a lawn at the bottom left."""
    rng = np.random.default_rng(0)
    img = np.zeros((h, w, 3), np.uint8)
    hy = int(0.55 * h)
    img[:hy] = (150, 190, 235)
    img[hy:] = (40, 80, 130)
    img[hy:] += rng.integers(0, 25, (h - hy, w, 1)).astype(np.uint8)  # ripples
    yy, xx = np.mgrid[0:h, 0:w]
    img[((xx - 0.8 * w) ** 2 + (yy - 0.4 * h) ** 2) < (0.12 * w) ** 2] = (20, 30, 20)  # the tree
    img[(yy > 0.8 * h) & (xx < 0.5 * w)] = (60, 150, 50)  # lawn
    return img


def test_illustrate_lays_in_the_scene_in_phrases():
    img = coast()
    strokes, hy = illustrate(img)
    assert abs(hy - 0.55 * img.shape[0]) < 6
    phrases = {s.phrase for s in strokes}
    assert {0, 3, 4} <= phrases  # horizon/tree, water hatching, grass
    assert all(len(s.pts) >= 2 for s in strokes)


def test_strokes_are_scheduled_and_drawn_progressively():
    img = coast()
    strokes, _ = illustrate(img)
    schedule(strokes, {p: (0.1 * p, 0.1 * p + 0.5) for p in range(5)})
    assert all(s.t1 > s.t0 >= 0 for s in strokes)
    canvas = InkCanvas(img.shape[1], img.shape[0], strokes)
    assert canvas.advance(0.0) >= 0
    early = canvas.alpha().sum()
    assert canvas.advance(0.3) > 0
    mid = canvas.alpha().sum()
    canvas.advance(5.0)
    assert early <= mid < canvas.alpha().sum()
    assert canvas.advance(6.0) == 0  # everything is drawn


def test_wet_front_and_watercolour():
    level = np.linspace(0, 1, 100, dtype=np.float32)[None].repeat(10, 0)
    assert front_mask(level, 0.0)[0].max() == 0
    assert front_mask(level, 1.3)[0].min() == 1
    page = paper(60, 90, (244, 239, 230))
    wash = watercolour(coast(60, 90), page)
    assert wash.shape == page.shape and 0 <= wash.min() and wash.max() <= 255


def test_gulls_have_ink_and_colour():
    for ink, full in (gull_flying(1.0, 0.5, 120), gull_flying(1.0, -0.8, 120), gull_perched(1.0, 100, blink=True)):
        a_ink = np.asarray(ink)[..., 3]
        a_full = np.asarray(full)[..., 3]
        assert a_ink.max() > 200 and a_full.max() > 200
        assert (a_full > 128).sum() > (a_ink > 128).sum()  # the finished gull is filled in


def test_horizon_row_finds_the_sea():
    img = coast()
    assert abs(horizon_row(img) - 0.55 * img.shape[0]) < 6
