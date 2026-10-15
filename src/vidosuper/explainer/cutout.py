"""The person cut out of their own video, so graphics can go behind them.

MODNet (portrait matting, Apache-2.0; models/modnet.onnx) gives a soft matte with real hair and headphone edges. A guided
filter snaps the matte's edge to the picture's own edges, pieces that are not attached to the person are dropped (a
ceiling, a lamp), and the matte is smoothed over time so the edge does not shimmer from frame to frame. No fixed head area
or other guess about where the person is: they may move anywhere in the frame.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

MODEL = Path(os.environ.get("VIDO_MODELS_DIR") or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VidoSuper" / "models") / "modnet.onnx"
SHORT_SIDE = 448  # MODNet's own size is 512; 448 is half again faster and looks the same on a phone


class CutoutUnavailable(RuntimeError):
    """The matting model is not installed."""


@lru_cache(maxsize=1)
def _session(path: str):
    import onnxruntime as ort

    if not Path(path).exists():
        raise CutoutUnavailable(f"portrait matting model missing: {path}")
    options = ort.SessionOptions()
    options.intra_op_num_threads = int(os.environ.get("VIDO_MATTE_THREADS", 0)) or max(1, (os.cpu_count() or 4) // 2)
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(path, options, providers=["CPUExecutionProvider"])


def available(model: Path = MODEL) -> bool:
    return Path(model).exists()


def raw_matte(rgb: np.ndarray, model: Path = MODEL) -> np.ndarray:
    """MODNet's matte for one RGB frame, 0..1, at the frame's size."""
    session = _session(str(model))
    h, w = rgb.shape[:2]
    scale = SHORT_SIDE / min(h, w)
    nh, nw = max(32, int(round(h * scale / 32)) * 32), max(32, int(round(w * scale / 32)) * 32)
    x = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    x = ((x - 0.5) / 0.5).transpose(2, 0, 1)[None]
    out = session.run(None, {session.get_inputs()[0].name: x})[0]
    m = np.squeeze(out).astype(np.float32)
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)


def guided(guide: np.ndarray, src: np.ndarray, radius: int = 6, eps: float = 1e-3) -> np.ndarray:
    """He et al.'s guided filter (grey guide), run at half size: the matte follows the picture's real edges."""
    g = cv2.resize(guide, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    p = cv2.resize(src, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    size = (2 * radius + 1, 2 * radius + 1)
    mean = lambda a: cv2.boxFilter(a, -1, size)  # noqa: E731
    mg, mp = mean(g), mean(p)
    a = (mean(g * p) - mg * mp) / (mean(g * g) - mg * mg + eps)
    b = mp - a * mg
    q = mean(a) * g + mean(b)
    return cv2.resize(q, (guide.shape[1], guide.shape[0]), interpolation=cv2.INTER_LINEAR)


def attached(m: np.ndarray) -> np.ndarray:
    """Only the pieces of the matte that belong to a person in the frame: the biggest piece, and any piece that
    reaches the bottom edge (a body leaves the frame at the bottom); a lamp or ceiling floating above is dropped.
    Worked out at quarter size (whole pieces, not edges)."""
    h, w = m.shape
    q = cv2.resize(m, (w // 4, h // 4), interpolation=cv2.INTER_AREA)
    hard = (q > 0.5).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(hard, 8)
    if n <= 2:
        return m
    qh, qw = hard.shape
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    keep = np.zeros_like(hard)
    for c in range(1, n):
        x, y, cw, ch, area = stats[c]
        if c == biggest or (y + ch >= qh - 1 and area > 0.004 * qh * qw):
            keep[lab == c] = 1
    keep = cv2.GaussianBlur(cv2.dilate(keep, np.ones((7, 7), np.uint8)).astype(np.float32), (0, 0), 1.5)
    return m * cv2.resize(keep, (w, h), interpolation=cv2.INTER_LINEAR)


class Tracker:
    """Mattes for consecutive frames of one video: smoothed over time where the person is still, quick where they move."""

    def __init__(self, model: Path = MODEL):
        self.model = model
        self.prev: np.ndarray | None = None

    def __call__(self, rgb: np.ndarray, raw: np.ndarray | None = None) -> np.ndarray:
        m = raw if raw is not None else raw_matte(rgb, self.model)
        grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        m = np.clip(guided(grey, m), 0.0, 1.0)
        m = attached(m)
        if self.prev is not None:
            moving = np.clip(np.abs(m - self.prev) * 3.0, 0.0, 1.0)  # a hand that moves follows at once
            m = moving * m + (1 - moving) * (0.55 * m + 0.45 * self.prev)
        self.prev = m
        return np.clip((m - 0.04) / 0.92, 0.0, 1.0).astype(np.float32)


def decontaminate(rgb: np.ndarray, m: np.ndarray, radius: int = 9) -> np.ndarray:
    """The person's colours at their soft edge, without the wall behind them showing through (a light fringe around
    hair): edge pixels take the colour of the person just inside the edge. Worked out at half size (it is a blur)."""
    h, w = m.shape
    img = rgb.astype(np.float32)
    edge = ((m > 0.02) & (m < 0.95)).astype(np.float32) * 0.65
    if not edge.any():
        return img
    hs, ws = h // 2, w // 2
    small = cv2.resize(img, (ws, hs), interpolation=cv2.INTER_AREA)
    inside = (cv2.resize(m, (ws, hs), interpolation=cv2.INTER_AREA) > 0.95).astype(np.float32)
    size = (radius + 1, radius + 1)
    num = cv2.boxFilter(small * inside[..., None], -1, size)
    den = cv2.boxFilter(inside, -1, size)[..., None]
    fg = np.where(den > 1e-3, num / np.maximum(den, 1e-3), small).astype(np.float32)
    fg = cv2.resize(fg, (w, h), interpolation=cv2.INTER_LINEAR)
    return cv2.blendLinear(img, fg, 1.0 - edge, edge)


def box(m: np.ndarray, at: float = 0.5) -> tuple[float, float, float, float] | None:
    """The person's bounding box as fractions of the frame (left, top, right, bottom), or None when nobody is there."""
    small = m[::8, ::8] > at
    if small.sum() < 20:
        return None
    rows, cols = np.where(small.any(axis=1))[0], np.where(small.any(axis=0))[0]
    h, w = small.shape
    return cols[0] / w, rows[0] / h, (cols[-1] + 1) / w, (rows[-1] + 1) / h


class Stream:
    """Mattes for a stream of frames, computed one frame ahead on another thread while the caller draws the current
    one (the model releases Python's lock, so the two overlap)."""

    def __init__(self, model: Path = MODEL):
        from concurrent.futures import ThreadPoolExecutor

        self.tracker = Tracker(model)
        self.pool = ThreadPoolExecutor(1)
        self.pending = None

    def raw(self, rgb: np.ndarray):
        return self.pool.submit(raw_matte, rgb, self.tracker.model)

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
