from __future__ import annotations

import subprocess

import numpy as np
import pytest

from vidosuper.config import load_config
from vidosuper.ffmpeg import ffmpeg_bin, ffprobe_json
from vidosuper.graphics.apply import apply_graphics
from vidosuper.graphics.layers import render_layer
from vidosuper.graphics.matte import MODEL, person_matte
from vidosuper.graphics.track import SceneTracker, transform_point
from vidosuper.schemas import Graphic


def _texture(w: int = 640, h: int = 360, seed: int = 4) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, size=(h // 8, w // 8, 3), dtype=np.uint8)
    import cv2

    return cv2.resize(base, (w * 2, h * 2), interpolation=cv2.INTER_NEAREST)  # room to pan over


def test_tracker_recovers_a_pan():
    big = _texture()
    tracker = SceneTracker(640, 360)
    h = np.eye(3)
    for k in range(12):  # the camera pans right 6 px a frame: the scene moves left
        h = tracker.update(np.ascontiguousarray(big[100:460, 100 + 6 * k:740 + 6 * k]))
    x, y = transform_point(h, 320, 180)
    assert abs(x - (320 - 66)) < 3 and abs(y - 180) < 3


@pytest.mark.parametrize("g", [
    Graphic(kind="title", text="THE\nEDGE", at=0.2),
    Graphic(kind="words", text="ONE|TWO", at=0.2, beats=[0.0, 0.4]),
    Graphic(kind="label", text="Wollongong Head", at=0.2),
    Graphic(kind="doodle", style="circle", at=0.2),
    Graphic(kind="doodle", style="arrow", points=[(0.2, 0.2), (0.6, 0.6)], at=0.2),
    Graphic(kind="doodle", style="burst", at=0.2),
    Graphic(kind="doodle", style="spiral", at=0.2),
    Graphic(kind="doodle", style="stars", at=0.2),
    Graphic(kind="doodle", style="line", points=[(0.2, 0.2), (0.6, 0.7)], at=0.2),
    Graphic(kind="route", text="Sydney|Wollongong|Minnamurra", anchor="screen", at=0.2),
    Graphic(kind="crew", at=0.2),
])
def test_every_kind_draws_after_it_starts(g):
    size = (360, 640)
    assert render_layer(g, 0.1, size, np.eye(3)) is None  # not yet
    layer = render_layer(g, 0.55, size, np.eye(3))  # mid-animation: a burst is gone by 0.9 s
    assert layer is not None and np.asarray(layer)[..., 3].max() > 0
    if g.kind not in ("route",):  # a world graphic moves with the camera
        moved = render_layer(g, 0.55, size, np.array([[1, 0, 40], [0, 1, 0], [0, 0, 1]], float))
        assert not np.array_equal(np.asarray(moved), np.asarray(layer))


def test_graphics_pass_keeps_every_frame(tmp_path):
    src = tmp_path / "shot.mp4"
    frames = [_texture(320, 180)[60:240, 60 + 4 * k:380 + 4 * k] for k in range(24)]
    proc = subprocess.Popen([ffmpeg_bin(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "320x180",
                             "-r", "24", "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], stdin=subprocess.PIPE)
    proc.communicate(b"".join(np.ascontiguousarray(f).tobytes() for f in frames))
    out = apply_graphics(src, [Graphic(kind="title", text="HI", at=0.1), Graphic(kind="doodle", style="stars", at=0.2)],
                         24, tmp_path, "draft", load_config())
    video = next(s for s in ffprobe_json(out)["streams"] if s["codec_type"] == "video")
    assert out != src and int(video["nb_frames"]) == 24


@pytest.mark.skipif(not MODEL.exists(), reason="person matte model not downloaded")
def test_person_matte_is_a_soft_mask():
    m = person_matte(_texture(320, 180)[:180, :320])
    assert m.shape == (180, 320) and 0.0 <= float(m.min()) and float(m.max()) <= 1.0
