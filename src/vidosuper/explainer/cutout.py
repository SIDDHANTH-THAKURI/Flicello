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


def providers() -> list[str]:
    """Where the model runs, best first: an NVIDIA GPU (the cloud), any Windows GPU through DirectML (the owner's PC:
    141 ms a frame on its Iris Xe against 256 ms on all its CPU cores, the same matte to 1e-5, and the cores stay free
    for drawing), else the CPU. VIDO_MATTE_DEVICE=cpu forces the CPU."""
    import onnxruntime as ort

    have = ort.get_available_providers()
    if os.environ.get("VIDO_MATTE_DEVICE", "").lower() == "cpu":
        return ["CPUExecutionProvider"]
    return [p for p in ("CUDAExecutionProvider", "DmlExecutionProvider") if p in have][:1] + ["CPUExecutionProvider"]


@lru_cache(maxsize=1)
def _session(path: str):
    import onnxruntime as ort

    if not Path(path).exists():
        raise CutoutUnavailable(f"portrait matting model missing: {path}")
    options = ort.SessionOptions()
    options.intra_op_num_threads = int(os.environ.get("VIDO_MATTE_THREADS", 0)) or max(1, (os.cpu_count() or 4) // 2)
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    chosen = providers()
    if chosen[0] == "DmlExecutionProvider":  # DirectML's own rules
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    try:
        return ort.InferenceSession(path, options, providers=chosen)
    except Exception:  # noqa: BLE001 - a GPU that won't start: the CPU always works
        return ort.InferenceSession(path, options, providers=["CPUExecutionProvider"])


def available(model: Path = MODEL) -> bool:
    return Path(model).exists()


def raw_matte(rgb: np.ndarray, model: Path = MODEL, full: bool = True) -> np.ndarray:
    """MODNet's matte for one RGB frame, 0..1: at the frame's size, or (`full` False) at the model's own size, which
    is all the Tracker needs (it works at half the frame's size)."""
    session = _session(str(model))
    h, w = rgb.shape[:2]
    scale = SHORT_SIDE / min(h, w)
    nh, nw = max(32, int(round(h * scale / 32)) * 32), max(32, int(round(w * scale / 32)) * 32)
    x = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    x = ((x - 0.5) / 0.5).transpose(2, 0, 1)[None]
    out = session.run(None, {session.get_inputs()[0].name: x})[0]
    m = np.squeeze(out).astype(np.float32)
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR) if full else m


def guided(guide: np.ndarray, src: np.ndarray, radius: int = 6, eps: float = 1e-3) -> np.ndarray:
    """He et al.'s guided filter (grey guide), run at half size: the matte follows the picture's real edges."""
    g = cv2.resize(guide, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    p = cv2.resize(src, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    q = guided_at(g, p, radius, eps)
    return cv2.resize(q, (guide.shape[1], guide.shape[0]), interpolation=cv2.INTER_LINEAR)


def guided_at(g: np.ndarray, p: np.ndarray, radius: int = 6, eps: float = 1e-3) -> np.ndarray:
    """The guided filter at the size it is given."""
    size = (2 * radius + 1, 2 * radius + 1)
    mean = lambda a: cv2.boxFilter(a, -1, size)  # noqa: E731
    mg, mp = mean(g), mean(p)
    a = (mean(g * p) - mg * mp) / (mean(g * g) - mg * mg + eps)
    b = mp - a * mg
    return mean(a) * g + mean(b)


def attached(m: np.ndarray, shrink: int = 4) -> np.ndarray:
    """Only the pieces of the matte that belong to a person in the frame: the biggest piece, and any piece that
    reaches the bottom edge (a body leaves the frame at the bottom); a lamp or ceiling floating above is dropped.
    Worked out at a quarter of the frame's size (whole pieces, not edges): `shrink` is how much smaller than `m`."""
    h, w = m.shape
    q = cv2.resize(m, (w // shrink, h // shrink), interpolation=cv2.INTER_AREA)
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
        """The frame's matte at its full size. All the work is done at half size (the guided filter's detail is half
        size anyway) and scaled up once at the end: a quarter of the cost of doing the same at full size."""
        h, w = rgb.shape[:2]
        half = (w // 2, h // 2)
        m = raw if raw is not None else raw_matte(rgb, self.model, full=False)
        p = cv2.resize(m, half, interpolation=cv2.INTER_AREA if m.shape[1] > half[0] else cv2.INTER_LINEAR)
        grey = cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), half, interpolation=cv2.INTER_AREA).astype(np.float32)
        grey *= 1.0 / 255.0
        m = np.clip(guided_at(grey, p), 0.0, 1.0)
        m = attached(m, shrink=2)
        if self.prev is not None and self.prev.shape == m.shape:
            moving = cv2.absdiff(m, self.prev)
            moving *= 3.0
            np.minimum(moving, 1.0, out=moving)  # a hand that moves follows at once
            m = cv2.blendLinear(m, 0.55 * m + 0.45 * self.prev, moving, 1.0 - moving)
        self.prev = m
        out = (m - 0.04) * (1 / 0.92)
        np.clip(out, 0.0, 1.0, out=out)
        return cv2.resize(out, (w, h), interpolation=cv2.INTER_LINEAR)


def decontaminate(rgb: np.ndarray, m: np.ndarray, radius: int = 9) -> np.ndarray:
    """The person's colours at their soft edge, without the wall behind them showing through (a light fringe around
    hair): edge pixels take the colour of the person just inside the edge. Worked out at half size (it is a blur),
    with OpenCV's own (all-core) operations, and blended in only where the edge is."""
    h, w = m.shape
    img = rgb.astype(np.float32)
    band = cv2.inRange(m, 0.0201, 0.9499)  # the soft edge: 0.02 < m < 0.95
    if not cv2.countNonZero(band):
        return img
    hs, ws = h // 2, w // 2
    small = cv2.resize(img, (ws, hs), interpolation=cv2.INTER_AREA)
    inside = cv2.threshold(cv2.resize(m, (ws, hs), interpolation=cv2.INTER_AREA), 0.95, 1.0, cv2.THRESH_BINARY)[1]
    size = (radius + 1, radius + 1)
    num = cv2.boxFilter(cv2.multiply(small, cv2.merge([inside, inside, inside])), -1, size)
    den = cv2.boxFilter(inside, -1, size)
    fg = small.copy()
    cv2.copyTo(cv2.divide(num, cv2.merge([np.maximum(den, 1e-3)] * 3)), (den > 1e-3).astype(np.uint8), fg)
    x, y, bw, bh = cv2.boundingRect(band)  # only the edge band needs the blend
    x0, y0 = max(0, x - 2), max(0, y - 2)
    x1, y1 = min(w, x + bw + 2), min(h, y + bh + 2)
    fg = cv2.resize(fg, (w, h), interpolation=cv2.INTER_LINEAR)[y0:y1, x0:x1]
    edge = band[y0:y1, x0:x1].astype(np.float32) * (0.65 / 255.0)
    img[y0:y1, x0:x1] = cv2.blendLinear(np.ascontiguousarray(img[y0:y1, x0:x1]), np.ascontiguousarray(fg), 1.0 - edge, edge)
    return img


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
        return self.pool.submit(raw_matte, rgb, self.tracker.model, False)

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
