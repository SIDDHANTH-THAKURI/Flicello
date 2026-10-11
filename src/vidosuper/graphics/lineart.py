"""Artist line art of a real frame, drawn on over time.

The lines come from the Informative Drawings "opensketch" model (Chan, Durand, Isola 2022; MIT licence; ONNX
at %LOCALAPPDATA%/VidoSuper/models/lineart_opensketch.onnx): a pen sketch of the frame that reads as that place at
a glance, and that sits exactly on the footage when the page turns real.

The drawing animation follows the lines themselves: every connected group of strokes is drawn outward from
where it starts (a breadth-first walk along the ink, on a coarse grid), the big structural groups first and the
small detail strokes after, so the sketch builds up the way a hand would draw it - not as a wipe.
"""

from __future__ import annotations

import os
from collections import deque
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

MODEL = Path(os.environ.get("LOCALAPPDATA", ".")) / "VidoSuper" / "models" / "lineart_opensketch.onnx"


@lru_cache(maxsize=1)
def _session():
    import onnxruntime as ort

    return ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])


def lineart(rgb: np.ndarray, work: tuple[int, int] = (1600, 900), contrast: float = 1.25) -> np.ndarray:
    """Ink strength 0..1 (1 = full ink) of an artist's sketch of the frame, at the frame's size."""
    h, w = rgb.shape[:2]
    x = cv2.resize(rgb, work, interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    y = _session().run(None, {"input": x.transpose(2, 0, 1)[None]})[0][0, 0]
    ink = np.clip((1.0 - y) * contrast, 0, 1)
    ink[ink < 0.08] = 0  # paper stays clean
    return cv2.resize(ink, (w, h), interpolation=cv2.INTER_CUBIC).clip(0, 1)


def strokes_ink(strokes, w: int, h: int, ss: int = 2) -> np.ndarray:
    """Vector strokes (e.g. cloud tops) as ink strength in the same pen style, to add to the line art."""
    canvas = np.zeros((h * ss, w * ss), np.uint8)
    for s in strokes:
        pts = np.round(s.pts * ss).astype(np.int32)
        th = max(1, int(round(s.width * ss * 0.8)))
        cv2.polylines(canvas, [pts.reshape(-1, 1, 2)], False, 255, th, cv2.LINE_AA)
    return cv2.resize(canvas, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0 * 0.85


class RasterDrawing:
    """When each inked pixel appears, so the sketch draws itself between start and end."""

    def __init__(self, ink: np.ndarray, start: float, end: float, seed: int = 3, grid: int = 3,
                 order: np.ndarray | None = None):
        h, w = ink.shape
        self.ink = ink
        gh, gw = h // grid, w // grid
        small = cv2.resize(ink, (gw, gh), interpolation=cv2.INTER_AREA)
        mask = cv2.dilate((small > 0.06).astype(np.uint8), np.ones((3, 3), np.uint8))
        n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
        rng = np.random.default_rng(seed)
        dist = np.full((gh, gw), -1.0, np.float32)
        lengths = np.zeros(n, np.float32)
        for c in range(1, n):  # breadth-first along the ink from the group's left/top end
            x0, y0, ww, hh, area = stats[c]
            ys, xs = np.nonzero(labels[y0:y0 + hh, x0:x0 + ww] == c)
            if not len(ys):
                continue
            k = int(np.argmin(xs + 0.6 * ys))
            sy, sx = ys[k] + y0, xs[k] + x0
            dist[sy, sx] = 0
            q = deque([(sy, sx)])
            far = 0.0
            while q:
                yy, xx = q.popleft()
                d0 = dist[yy, xx]
                for dy in (-1, 0, 1):
                    ny = yy + dy
                    if ny < 0 or ny >= gh:
                        continue
                    for dx in (-1, 0, 1):
                        nx = xx + dx
                        if (dy or dx) and 0 <= nx < gw and labels[ny, nx] == c and dist[ny, nx] < 0:
                            dist[ny, nx] = d0 + (1.4142 if dy and dx else 1.0)
                            far = max(far, dist[ny, nx])
                            q.append((ny, nx))
            lengths[c] = max(far, 1.0)
        span = max(1e-3, end - start)
        sizes = stats[:, cv2.CC_STAT_AREA].astype(np.float32)
        rank = np.zeros(n, np.float32)
        if order is not None and len(order) == n:
            rank = order
        else:  # big structure first, then the detail, sweeping left to right inside each band
            by_size = np.argsort(-sizes[1:]) + 1
            r = np.empty(n, np.float32)
            r[by_size] = np.arange(len(by_size), dtype=np.float32) / max(1, len(by_size) - 1)
            sweep = cents[:, 0] / max(1, gw)
            rank = np.clip(0.75 * r ** 0.6 + 0.25 * sweep + rng.uniform(-0.03, 0.03, n), 0, 1)
        speed = lengths.max() / (0.5 * span) if n > 1 else 1.0  # the biggest group takes half the time
        t0 = start + 0.82 * span * rank  # strokes keep coming until the end of the window
        dur = np.minimum(end - t0, np.maximum(0.1, lengths / speed))
        when = np.full((gh, gw), 1e6, np.float32)  # never
        lab_ok = (labels > 0) & (dist >= 0)
        cl = labels[lab_ok]
        when[lab_ok] = t0[cl] + dur[cl] * (dist[lab_ok] / lengths[cl])
        when = cv2.resize(when, (w, h), interpolation=cv2.INTER_NEAREST)
        when = cv2.erode(when, np.ones((3, 3), np.uint8))  # cover the full-res ink just outside the coarse cells
        self.when = when
        self.start, self.end = start, end
        self._last = -1.0

    def alpha(self, t: float, soft: float = 0.04) -> np.ndarray:
        if t >= self.end + soft:
            return self.ink
        return self.ink * np.clip((t - self.when) / soft + 1.0, 0, 1)

    def activity(self, t0: float, t1: float) -> float:
        """How much ink went down between t0 and t1 (drives the pen-scratch sound)."""
        sel = (self.when > t0) & (self.when <= t1)
        return float(self.ink[sel].sum())
