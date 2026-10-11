"""Drawn -> real: one frame of the footage turned into an ink drawing and a watercolour of itself, so a
shot can be drawn on a page, painted, and then come alive.

The drawing is built from what is in the frame, the way an illustrator would draw it, not a filter: the
horizon first, then the breakwater, boats and posts (their real edges), the clouds (outlines of the cloud
shapes), the water (hatching laid on the real ripples, denser near the camera), the grass and flowers.
Every stroke is a polyline drawn over time, so the page fills in like a speed drawing, and every line
sits exactly where the real thing is when the picture comes alive under it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageDraw

INK = np.array([30, 34, 60], np.float32)


@dataclass
class Stroke:
    pts: np.ndarray  # (n, 2) pixels, in drawing order
    width: float  # pixels at output size
    phrase: int
    t0: float = 0.0
    t1: float = 0.0


def paper(w: int, h: int, cream: tuple[int, int, int], seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = np.ones((h, w, 3), np.float32) * np.array(cream, np.float32)
    grain = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 1.1) * 4.0
    fibre = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 10) * 9.0
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    vignette = 1 - 0.06 * (((xx / w - 0.5) ** 2 + (yy / h - 0.5) ** 2) * 2.2)
    return np.clip((base + (grain + fibre)[..., None]) * vignette[..., None], 0, 255)


def noise_field(w: int, h: int, seed: int, cell: int = 48) -> np.ndarray:
    """Smooth 0..1 noise (a few octaves), for wet edges and ink fronts."""
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w), np.float32)
    amp, total = 1.0, 0.0
    for octave in range(3):
        c = max(4, cell >> octave)
        small = rng.random((h // c + 2, w // c + 2)).astype(np.float32)
        out += amp * cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
        total += amp
        amp *= 0.5
    out /= total
    return (out - out.min()) / max(1e-6, out.max() - out.min())


def horizon_row(rgb: np.ndarray) -> int:
    h = rgb.shape[0]
    luma = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32).mean(axis=1)
    best, row = -1e9, h // 2
    for y in range(int(0.35 * h), int(0.85 * h)):
        d = luma[y - 8:y].mean() - luma[y:y + 8].mean()  # bright sky above, darker sea below
        if d > best:
            best, row = d, y
    return row


def _resample(pts: np.ndarray, step: float, closed: bool) -> np.ndarray:
    p = pts.astype(np.float32)
    if closed:
        p = np.vstack([p, p[:1]])
    seg = np.hypot(*np.diff(p, axis=0).T)
    s = np.concatenate([[0], np.cumsum(seg)])
    if s[-1] < step:
        return p
    t = np.arange(0, s[-1], step)
    return np.stack([np.interp(t, s, p[:, 0]), np.interp(t, s, p[:, 1])], axis=1)


def _smooth(pts: np.ndarray, sigma: float, closed: bool) -> np.ndarray:
    if len(pts) < 5 or sigma <= 0:
        return pts
    k = int(3 * sigma) | 1
    kernel = cv2.getGaussianKernel(2 * k + 1, sigma).ravel()
    mode = "wrap" if closed else "edge"
    out = np.empty_like(pts)
    for c in range(2):
        padded = np.pad(pts[:, c], k, mode=mode)
        out[:, c] = np.convolve(padded, kernel, mode="same")[k:-k]
    return out


def _wobble(pts: np.ndarray, amp: float, rng: np.random.Generator) -> np.ndarray:
    if amp <= 0 or len(pts) < 3:
        return pts
    n = len(pts)
    phase, freq = rng.uniform(0, 2 * math.pi), rng.uniform(1.5, 3.5)
    t = np.linspace(0, 1, n)
    d = np.gradient(pts, axis=0)
    nrm = np.stack([-d[:, 1], d[:, 0]], axis=1)
    nrm /= np.maximum(1e-6, np.hypot(*nrm.T))[:, None]
    return pts + nrm * (amp * np.sin(2 * math.pi * freq * t + phase))[:, None]


def scene_strokes(rgb: np.ndarray, seed: int = 7) -> tuple[list[Stroke], int]:
    """The strokes an illustrator would draw for this frame, grouped in phrases: 0 horizon, 1 the band on
    the horizon (breakwater, boats, posts), 2 clouds, 3 water, 4 grass and flowers."""
    rng = np.random.default_rng(seed)
    h, w = rgb.shape[:2]
    hy = horizon_row(rgb)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    hue, sat, val = hsv[..., 0].astype(np.int32), hsv[..., 1].astype(np.float32), hsv[..., 2].astype(np.float32)
    luma = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    strokes: list[Stroke] = []

    # 0: the horizon, one sweep across the page
    xs = np.linspace(-12, w + 12, 160)
    strokes.append(Stroke(_wobble(np.stack([xs, np.full_like(xs, hy)], 1), 0.7, rng), 2.6, 0))

    # 1: the band on the horizon - real edges of the breakwater, the boats, masts and posts
    y0, y1 = int(hy - 0.09 * h), int(hy + 0.065 * h)
    edges = cv2.Canny(cv2.GaussianBlur(luma, (0, 0), 1.0), 45, 120)
    band = np.zeros_like(edges)
    band[y0:y1] = edges[y0:y1]
    objects = (((val > 185) & (sat < 95)) | ((sat > 110) & ((hue < 10) | (hue > 170)))).astype(np.uint8)
    n_obj, labels, stats, _ = cv2.connectedComponentsWithStats(objects, connectivity=8)
    big = np.zeros(n_obj, bool)
    big[1:] = stats[1:, cv2.CC_STAT_AREA] >= 120  # boats and posts, not sun glints on the ripples
    near = cv2.dilate(big[labels].astype(np.uint8), np.ones((15, 15), np.uint8)) > 0
    water = np.zeros_like(near)
    water[hy + 4:] = True
    band[water & ~near] = 0  # below the horizon only the boats keep their edges, not the ripples
    contours, _ = cv2.findContours(band, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    for c in sorted(contours, key=lambda c: float(c[:, 0, 0].mean())):
        if len(c) >= 10:
            strokes.append(Stroke(_smooth(c[:, 0, :].astype(np.float32), 0.8, False), 1.9, 1))

    # breakwater: the dark silhouette standing on the horizon, its top edge column by column
    sky_ref = float(np.median(luma[int(hy - 0.06 * h):int(hy - 0.035 * h)]))
    top = np.full(w, -1)
    for x in range(w):
        col = luma[int(hy - 0.03 * h):hy + 2, x].astype(np.float32)
        dark = np.nonzero(col < sky_ref - 45)[0]
        if len(dark):
            top[x] = int(hy - 0.03 * h) + dark[0]
    x = 0
    while x < w:
        if top[x] < 0:
            x += 1
            continue
        end = x
        while end < w and top[end] >= 0:
            end += 1
        if end - x >= 18:
            ys = _smooth(np.stack([np.arange(x, end, dtype=np.float32), top[x:end].astype(np.float32)], 1), 2.0, False)
            outline = np.vstack([[[x, hy]], ys, [[end - 1, hy]]]).astype(np.float32)
            strokes.append(Stroke(_resample(outline, 3.0, False), 2.4, 0))
        x = end

    # 2: clouds - the tops of the cloud shapes (the way clouds are sketched), against the local sky so the
    # haze over the horizon stays clear
    whiteness = (val / 255.0) * (1 - sat / 255.0)
    whiteness = cv2.GaussianBlur(whiteness, (0, 0), 4)
    local = whiteness - cv2.GaussianBlur(whiteness, (0, 0), 70)
    sky = np.zeros((h, w), bool)
    sky[: int(hy - 0.06 * h)] = True
    mask = ((local > 0.035) & sky).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    soft = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 3)
    grad_x, grad_y = cv2.Sobel(soft, cv2.CV_32F, 1, 0), cv2.Sobel(soft, cv2.CV_32F, 0, 1)
    cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cs = sorted([c for c in cs if cv2.contourArea(c) > 1500], key=lambda c: -cv2.contourArea(c))[:40]
    for c in cs:
        pts = _smooth(_resample(c[:, 0, :], 4.0, True), 2.5, True)
        ix = np.clip(pts[:, 0].astype(int), 0, w - 1)
        iy = np.clip(pts[:, 1].astype(int), 0, h - 1)
        gx, gyv = grad_x[iy, ix], grad_y[iy, ix]
        up = gyv / (np.hypot(gx, gyv) + 1e-6) > 0.2  # the mask grows downward here: a cloud top
        if up.all() or not up.any():
            continue
        start = int(np.argmin(up))  # begin the walk on a non-top point so runs don't wrap
        pts, up = np.roll(pts, -start, axis=0), np.roll(up, -start)
        width = 2.5 if cv2.contourArea(c) > 15000 else 1.9
        k = 0
        while k < len(up):
            if not up[k]:
                k += 1
                continue
            e = k
            while e < len(up) and up[e]:
                e += 1
            if e - k >= 26:  # short ticks read as birds
                run = pts[k:e]
                if run[0, 0] > run[-1, 0]:
                    run = run[::-1]  # draw left to right
                strokes.append(Stroke(_wobble(run, 0.7, rng), width, 2))
            k = e

    # 3: water - hatching laid on the real ripples, sparse at the horizon, bolder near the camera
    grass = (((hue >= 30) & (hue <= 90) & (sat > 70) & (val > 50)) |
             ((sat > 120) & ((hue < 8) | (hue > 172)) & (val > 90)))
    grass[: int(hy + 0.08 * h)] = False
    grass_d = cv2.dilate(grass.astype(np.uint8), np.ones((25, 25), np.uint8)) > 0
    blur = cv2.GaussianBlur(luma, (0, 0), 1.5).astype(np.float32)
    y = hy + 0.02 * h
    while y < h - 4:
        u = (y - hy) / max(1, h - hy)
        spacing, length = 7 + 24 * u, 22 + 150 * u
        row = blur[int(y)]
        mean = cv2.GaussianBlur(row[None, :], (0, 0), 25)[0]
        dark = (row < mean - 1.0).astype(np.int8)
        x = 0
        while x < w:
            if dark[x] and not grass_d[int(y), x]:
                end = x
                while end < w and dark[end] and end - x < length:
                    end += 1
                if end - x >= max(10, length * 0.15) and rng.random() < 0.75:
                    xs = np.linspace(x, end, max(4, int((end - x) / 8)))
                    ys = y + np.sin(np.linspace(0, math.pi * rng.uniform(0.6, 1.4), len(xs))) * (0.5 + 2.2 * u) * rng.choice([-1, 1])
                    strokes.append(Stroke(np.stack([xs, ys], 1), 1.1 + 1.5 * u, 3))
                x = end + int(length * rng.uniform(0.25, 0.9))
            else:
                x += 2
        y += spacing * rng.uniform(0.8, 1.2)

    # 4: grass ticks and flower outlines in the foreground
    green = grass & ~((sat > 120) & ((hue < 8) | (hue > 172)))
    gy, gx = np.nonzero(green)
    if len(gy) > 1500:
        pick = rng.choice(len(gy), size=min(150, len(gy) // 350), replace=False)
        for i in sorted(pick, key=lambda i: gx[i]):
            x0, y0g = float(gx[i]), float(gy[i])
            ln, lean = rng.uniform(10, 30), rng.uniform(-6, 6)
            t = np.linspace(0, 1, 6)
            strokes.append(Stroke(np.stack([x0 + lean * t ** 2, y0g - ln * t], 1), 1.6, 4))
    red = (((sat > 120) & ((hue < 8) | (hue > 172)) & (val > 90)).astype(np.uint8))
    red[: int(hy + 0.08 * h)] = 0
    cs, _ = cv2.findContours(red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    for c in cs:
        if cv2.contourArea(c) > 25:
            strokes.append(Stroke(_smooth(_resample(c[:, 0, :], 2.5, True), 1.2, True), 1.6, 4))
    return strokes, hy


def schedule(strokes: list[Stroke], windows: dict[int, tuple[float, float]], speed: float = 2600.0,
             order_y: tuple[int, ...] = (3,)) -> None:
    """Give every stroke its time: strokes of a phrase start one after another across the phrase
    window and each takes as long as a fast pen needs for its length."""
    for phrase, (a, b) in windows.items():
        group = [s for s in strokes if s.phrase == phrase]
        if phrase in order_y:
            group.sort(key=lambda s: (float(s.pts[:, 1].mean()), float(s.pts[:, 0].mean())))
        n = len(group)
        for k, s in enumerate(group):
            start = a + (b - a) * 0.8 * (k / max(1, n - 1)) if n > 1 else a
            length = float(np.hypot(*np.diff(s.pts, axis=0).T).sum()) if len(s.pts) > 1 else 1.0
            s.t0 = start
            s.t1 = min(b, start + max(0.06, length / speed))
            if s.t1 <= s.t0:
                s.t1 = s.t0 + 0.04


class InkCanvas:
    """Strokes drawn progressively on a supersampled canvas (antialiased, tapered)."""

    def __init__(self, w: int, h: int, strokes: list[Stroke], ss: int = 2):
        self.w, self.h, self.ss = w, h, ss
        self.canvas = np.zeros((h * ss, w * ss), np.uint8)
        self.strokes = strokes
        self.done = [0] * len(strokes)

    def advance(self, t: float) -> int:
        """Draw everything due by t; returns how many segments were added (pen activity)."""
        added = 0
        ss = self.ss
        for i, s in enumerate(self.strokes):
            if t < s.t0 or self.done[i] >= len(s.pts):
                continue
            p = min(1.0, (t - s.t0) / max(1e-6, s.t1 - s.t0))
            target = max(1, int(round(p * len(s.pts))))
            n = len(s.pts)
            for k in range(max(1, self.done[i]), target):
                q = k / max(1, n - 1)
                taper = 0.45 + 0.55 * math.sin(math.pi * min(1.0, max(0.0, q)))
                th = max(1, int(round(s.width * ss * taper)))
                a = (int(round(s.pts[k - 1, 0] * ss)), int(round(s.pts[k - 1, 1] * ss)))
                b = (int(round(s.pts[k, 0] * ss)), int(round(s.pts[k, 1] * ss)))
                cv2.line(self.canvas, a, b, 255, th, cv2.LINE_AA)
                added += 1
            self.done[i] = max(self.done[i], target)
        return added

    def alpha(self) -> np.ndarray:
        return cv2.resize(self.canvas, (self.w, self.h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0


def watercolour(rgb: np.ndarray, page: np.ndarray, seed: int = 5) -> np.ndarray:
    """The frame as a transparent wash on the paper: flattened shapes, granulation, pigment pooled at
    the shape edges, the paper's whites kept for the clouds."""
    h, w = rgb.shape[:2]
    small = cv2.resize(rgb, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    base = cv2.pyrMeanShiftFiltering(small, 10, 22)
    base = cv2.edgePreservingFilter(base, flags=1, sigma_s=30, sigma_r=0.3)
    base = cv2.resize(base, (w, h), interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(base, cv2.COLOR_RGB2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * 1.22, 0, 255)
    hsv[..., 2] = np.clip(hsv[..., 2] * 1.05, 0, 255)
    base = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB).astype(np.float32) / 255.0
    rng = np.random.default_rng(seed)
    gran = 1 + cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 1.5) * 0.07
    glaze = 1 - 0.88 * (1 - base) * gran[..., None]
    gray = cv2.GaussianBlur(cv2.cvtColor((base * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY), (0, 0), 3).astype(np.float32)
    edge = np.hypot(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1))
    pool = 1 - np.clip(edge / 90, 0, 1) * 0.16
    return np.clip(page * glaze * pool[..., None], 0, 255)


def front_mask(level: np.ndarray, progress: float, soft: float = 0.07) -> tuple[np.ndarray, np.ndarray]:
    """A wet front: where `level` is below the advancing progress; also the thin rim at the front."""
    d = progress - level
    mask = np.clip(d / soft, 0, 1)
    rim = np.exp(-((d - soft * 0.5) / (soft * 0.45)) ** 2)
    return mask, rim


def gull_layers(scale: float, flap: float, s0: float) -> tuple[Image.Image, Image.Image]:
    """A gull drawn in ink, wings at `flap` (-1 down .. 1 up): (outline layer, fill layer), RGBA, centred."""
    ss = 3
    span = s0 * scale * ss
    size = int(span * 1.3) + 8
    c = size / 2
    outline = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    fill = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    do, df = ImageDraw.Draw(outline), ImageDraw.Draw(fill)
    lift = flap * 0.32 * span
    wings = []
    for side in (-1, 1):
        shoulder = (c + side * 0.04 * span, c - 0.01 * span)
        elbow = (c + side * 0.24 * span, c - 0.07 * span - 0.55 * lift)
        tip = (c + side * 0.5 * span, c + 0.02 * span - lift)
        trail = (c + side * 0.22 * span, c + 0.085 * span - 0.35 * lift)
        wings.append((shoulder, elbow, tip, trail))
    ink = tuple(int(v) for v in INK) + (255,)
    lw = max(2, int(round(2.4 * ss * max(0.8, scale ** 0.5))))
    for shoulder, elbow, tip, trail in wings:
        poly = [shoulder, elbow, tip, trail, (c, c + 0.05 * span)]
        df.polygon(poly, fill=(236, 238, 242, 255))
        tip_poly = [(elbow[0] * 0.35 + tip[0] * 0.65, elbow[1] * 0.35 + tip[1] * 0.65), tip,
                    (trail[0] * 0.3 + tip[0] * 0.7, trail[1] * 0.3 + tip[1] * 0.7)]  # black wingtips
        df.polygon(tip_poly, fill=(44, 46, 58, 255))
        do.line([shoulder, elbow, tip, trail, (c, c + 0.05 * span)], fill=ink, width=lw, joint="curve")
    body = [c - 0.11 * span, c - 0.045 * span, c + 0.11 * span, c + 0.05 * span]
    df.ellipse(body, fill=(250, 250, 252, 255))
    do.ellipse(body, outline=ink, width=lw)
    head = [c + 0.07 * span, c - 0.07 * span, c + 0.15 * span, c + 0.0 * span]
    df.ellipse(head, fill=(250, 250, 252, 255))
    do.ellipse(head, outline=ink, width=lw)
    beak = [(c + 0.148 * span, c - 0.04 * span), (c + 0.2 * span, c - 0.025 * span), (c + 0.148 * span, c - 0.015 * span)]
    df.polygon(beak, fill=(242, 170, 40, 255))
    do.line(beak, fill=ink, width=max(1, lw // 2))
    eye = (c + 0.118 * span, c - 0.045 * span)
    do.ellipse([eye[0] - lw * 0.6, eye[1] - lw * 0.6, eye[0] + lw * 0.6, eye[1] + lw * 0.6], fill=ink)
    out = (size // ss, size // ss)
    return outline.resize(out, Image.LANCZOS), fill.resize(out, Image.LANCZOS)


def _runs(ok: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    out, k = [], 0
    while k < len(ok):
        if not ok[k]:
            k += 1
            continue
        e = k
        while e < len(ok) and ok[e]:
            e += 1
        if e - k >= min_len:
            out.append((k, e))
        k = e
    return out


def _outline_strokes(mask: np.ndarray, min_area: float, width: float, phrase: int, rng: np.random.Generator,
                     step: float = 4.0) -> list[Stroke]:
    """Outlines of the shapes in a mask, leaving out the parts that run along the frame edge."""
    h, w = mask.shape
    cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    out = []
    for c in sorted(cs, key=lambda c: -cv2.contourArea(c)):
        if cv2.contourArea(c) < min_area:
            continue
        pts = _smooth(_resample(c[:, 0, :], step, True), 2.0, True)
        ok = (pts[:, 0] > 4) & (pts[:, 0] < w - 5) & (pts[:, 1] > 4) & (pts[:, 1] < h - 5)
        if ok.all():
            out.append(Stroke(_wobble(pts, 0.6, rng), width, phrase))
            continue
        start = int(np.argmin(ok))
        pts, ok = np.roll(pts, -start, axis=0), np.roll(ok, -start)
        for a, b in _runs(ok, 12):
            out.append(Stroke(_wobble(pts[a:b], 0.6, rng), width, phrase))
    return out


def illustrate(rgb: np.ndarray, person: np.ndarray | None = None, seed: int = 7,
               max_details: int = 260) -> tuple[list[Stroke], int]:
    """An ink drawing of any coastal frame, laid in the way an illustrator would: 0 the horizon and the big
    dark shapes (trees, bushes, headlands), 1 the person and the details (branches, rocks, houses, boats),
    2 cloud tops, 3 water hatching on the real ripples, 4 grass ticks and flowers."""
    rng = np.random.default_rng(seed)
    h, w = rgb.shape[:2]
    hy = horizon_row(rgb)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    hue, sat, val = hsv[..., 0].astype(np.int32), hsv[..., 1].astype(np.float32), hsv[..., 2].astype(np.float32)
    luma = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    strokes: list[Stroke] = []
    k9 = np.ones((9, 9), np.uint8)

    upper = luma[max(0, hy - 10):hy - 2].astype(np.float32).mean(axis=0)
    lower = luma[hy + 2:hy + 10].astype(np.float32).mean(axis=0)
    if np.mean(upper - lower > 10) > 0.55:  # a straight sea horizon runs across the frame
        xs = np.linspace(-12, w + 12, 160)
        strokes.append(Stroke(_wobble(np.stack([xs, np.full_like(xs, hy)], 1), 0.7, rng), 2.6, 0))

    thr = min(85.0, float(np.percentile(luma, 22)))
    dark = (cv2.GaussianBlur(luma, (0, 0), 2) < thr).astype(np.uint8)
    dark = cv2.morphologyEx(cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)), cv2.MORPH_CLOSE, k9)
    person_m = np.zeros((h, w), bool) if person is None else person > 0.5
    dark[person_m] = 0  # the person gets their own outline
    strokes += _outline_strokes(dark, 0.004 * w * h, 2.6, 0, rng)
    dark_d = cv2.dilate(dark, np.ones((35, 35), np.uint8)) > 0
    if person is not None:
        pm = cv2.morphologyEx(person_m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        strokes += _outline_strokes(pm, 400, 3.0, 1, rng, 3.0)
    person_d = cv2.dilate(person_m.astype(np.uint8), np.ones((25, 25), np.uint8)) > 0

    green = (hue >= 30) & (hue <= 90) & (sat > 60) & (val > 40)
    green[:hy] = False
    green_d = cv2.dilate(green.astype(np.uint8), np.ones((15, 15), np.uint8)) > 0
    brownish = (hue >= 5) & (hue <= 32) & (sat > 45)
    water = np.zeros((h, w), bool)
    water[hy + 3:] = True
    water &= ~(dark > 0) & ~green_d & ~brownish & ~person_d
    water = cv2.morphologyEx(water.astype(np.uint8), cv2.MORPH_OPEN, k9) > 0
    n_w, lab_w, st_w, _ = cv2.connectedComponentsWithStats(water.astype(np.uint8), connectivity=4)
    keep_w = np.zeros(n_w, bool)
    keep_w[1:] = st_w[1:, cv2.CC_STAT_AREA] > 0.01 * w * h
    water = keep_w[lab_w]

    sky = np.zeros((h, w), bool)
    sky[: max(0, int(hy - 0.04 * h))] = True
    sky &= ~dark_d & ~person_d

    # 1: details - edges of the smoothed picture, outside the sky, the water and the lawn
    bil = cv2.bilateralFilter(rgb, 9, 40, 7)
    edges = cv2.Canny(cv2.cvtColor(bil, cv2.COLOR_RGB2GRAY), 50, 130)
    edges[sky | water | (green & ~person_d)] = 0
    raw_edges = cv2.Canny(cv2.GaussianBlur(luma, (0, 0), 1.0), 50, 130)
    density = cv2.blur((raw_edges > 0).astype(np.float32), (41, 41))  # rock and foliage texture: few lines there
    cs, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    plain, busy, branches = [], [], []
    for c in cs:
        if len(c) < 24:
            continue
        cy, cx = int(c[:, 0, 1].mean()), int(c[:, 0, 0].mean())
        if person_m[cy, cx]:
            plain.append(c)
        elif dark_d[cy, cx] and cy < hy:
            branches.append(c)  # inside trees and bushes: a few branch lines, not every leaf
        elif density[cy, cx] > 0.06:
            continue  # rock texture: shaded with hatching below instead of traced crack by crack
        elif len(c) >= 30:
            plain.append(c)
    cs = sorted(plain, key=len, reverse=True)[:max_details] + sorted(branches, key=len, reverse=True)[:60]
    texture = (density > 0.06) & ~person_d & ~water & ~green
    texture[:hy] = False
    texture = cv2.morphologyEx(texture.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    texture = cv2.morphologyEx(texture, cv2.MORPH_OPEN, np.ones((15, 15), np.uint8))
    n_t, lab_t, st_t, _ = cv2.connectedComponentsWithStats(texture, connectivity=8)
    ground = np.zeros(n_t, bool)  # only the ground we stand on (it reaches the bottom of the frame)
    ground[1:] = (st_t[1:, cv2.CC_STAT_TOP] + st_t[1:, cv2.CC_STAT_HEIGHT] >= h - 0.05 * h) & \
        (st_t[1:, cv2.CC_STAT_AREA] > 0.03 * w * h)
    texture = ground[lab_t].astype(np.uint8)
    strokes += _outline_strokes(texture, 0.03 * w * h, 2.3, 1, rng, 5.0)  # the shape of the ledge
    shade = cv2.GaussianBlur(cv2.cvtColor(bil, cv2.COLOR_RGB2GRAY), (0, 0), 2).astype(np.float32)
    shadow = (shade < cv2.GaussianBlur(shade, (0, 0), 30) - 10) & (texture > 0)
    sy, sx = np.nonzero(shadow)
    if len(sy):
        for i in rng.choice(len(sy), size=min(110, len(sy) // 400 + 1), replace=False):
            u = (sy[i] - hy) / max(1, h - hy)
            ln = (10 + 16 * u) * rng.uniform(0.8, 1.2)
            for k in range(int(rng.integers(2, 4))):  # a little group of parallel hatch lines
                ox, oy = sx[i] + k * 5.0, sy[i] + k * 2.0
                strokes.append(Stroke(np.float32([[ox, oy], [ox - ln * 0.55, oy + ln * 0.83]]), 1.2 + 0.4 * u, 4))
    for c in sorted(cs, key=lambda c: float(c[:, 0, 0].mean())):
        pts = _smooth(c[:, 0, :].astype(np.float32), 0.9, False)
        on_person = bool(person_m[int(pts[:, 1].mean()), int(pts[:, 0].mean())])
        strokes.append(Stroke(pts, 1.9 if on_person else 1.6, 1))

    # 2: cloud tops
    whiteness = cv2.GaussianBlur((val / 255.0) * (1 - sat / 255.0), (0, 0), 4)
    local = whiteness - cv2.GaussianBlur(whiteness, (0, 0), 70)
    mask = ((local > 0.03) & sky).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    soft = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 3)
    grad_x, grad_y = cv2.Sobel(soft, cv2.CV_32F, 1, 0), cv2.Sobel(soft, cv2.CV_32F, 0, 1)
    cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    for c in sorted([c for c in cs if cv2.contourArea(c) > 1500], key=lambda c: -cv2.contourArea(c))[:40]:
        pts = _smooth(_resample(c[:, 0, :], 4.0, True), 2.5, True)
        ix = np.clip(pts[:, 0].astype(int), 0, w - 1)
        iy = np.clip(pts[:, 1].astype(int), 0, h - 1)
        up = grad_y[iy, ix] / (np.hypot(grad_x[iy, ix], grad_y[iy, ix]) + 1e-6) > 0.2
        up &= sky[iy, ix] & (pts[:, 0] > 4) & (pts[:, 0] < w - 5) & (pts[:, 1] > 4)
        if not up.any() or up.all():
            continue
        start = int(np.argmin(up))
        pts, up = np.roll(pts, -start, axis=0), np.roll(up, -start)
        width = 2.5 if cv2.contourArea(c) > 15000 else 1.9
        for a, b in _runs(up, 26):
            run = pts[a:b] if pts[a, 0] <= pts[b - 1, 0] else pts[a:b][::-1]
            strokes.append(Stroke(_wobble(run, 0.7, rng), width, 2))

    # 3: water hatching on the real ripples
    blur = cv2.GaussianBlur(luma, (0, 0), 1.5).astype(np.float32)
    y = hy + 0.015 * h
    while y < h - 4:
        u = (y - hy) / max(1, h - hy)
        spacing, length = 7 + 24 * u, 22 + 150 * u
        row = blur[int(y)]
        mean = cv2.GaussianBlur(row[None, :], (0, 0), 25)[0]
        dk = row < mean - 1.0
        x = 0
        while x < w:
            if dk[x] and water[int(y), x]:
                end = x
                while end < w and dk[end] and water[int(y), end] and end - x < length:
                    end += 1
                if end - x >= max(10, length * 0.15) and rng.random() < 0.75:
                    xs = np.linspace(x, end, max(4, int((end - x) / 8)))
                    ys = y + np.sin(np.linspace(0, math.pi * rng.uniform(0.6, 1.4), len(xs))) * (0.5 + 2.2 * u) * rng.choice([-1, 1])
                    strokes.append(Stroke(np.stack([xs, ys], 1), 1.1 + 1.5 * u, 3))
                x = end + int(length * rng.uniform(0.25, 0.9))
            else:
                x += 2
        y += spacing * rng.uniform(0.8, 1.2)

    # 4: grass ticks (smaller toward the horizon) and flowers
    gy, gx = np.nonzero(green & ~person_d)
    if len(gy) > 1500:
        pick = rng.choice(len(gy), size=min(80, len(gy) // 600), replace=False)
        for i in sorted(pick, key=lambda i: gx[i]):
            u = (gy[i] - hy) / max(1, h - hy)
            ln, lean = (5 + 20 * u) * rng.uniform(0.7, 1.2), rng.uniform(-6, 6) * (0.4 + u)
            t = np.linspace(0, 1, 6)
            strokes.append(Stroke(np.stack([gx[i] + lean * t ** 2, gy[i] - ln * t], 1).astype(np.float32), 1.2 + 0.8 * u, 4))
    red = (((sat > 120) & ((hue < 8) | (hue > 172)) & (val > 90)).astype(np.uint8))
    red[:hy] = 0
    cs, _ = cv2.findContours(red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    for c in cs:
        if cv2.contourArea(c) > 25:
            strokes.append(Stroke(_smooth(_resample(c[:, 0, :], 2.5, True), 1.2, True), 1.6, 4))
    return strokes, hy


def _spline(points: list[tuple[float, float]], closed: bool, n: int = 10) -> list[tuple[float, float]]:
    """Catmull-Rom through the points: soft illustrated curves from a few control points."""
    p = [p0 for p0 in points]
    p = [p[-1]] + p + [p[0], p[1]] if closed else [p[0]] + p + [p[-1]]
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = (np.array(v, np.float64) for v in (p[i - 1], p[i], p[i + 1], p[i + 2]))
        for s in np.linspace(0, 1, n, endpoint=False):
            q = 0.5 * ((2 * p1) + (-p0 + p2) * s + (2 * p0 - 5 * p1 + 4 * p2 - p3) * s * s + (-p0 + 3 * p1 - 3 * p2 + p3) * s ** 3)
            out.append((float(q[0]), float(q[1])))
    if not closed:
        out.append((float(p[-2][0]), float(p[-2][1])))
    return out


class _Both:
    """Ink drawn on the outline layer and, in painting order, on the full sprite: the full sprite covers
    hidden lines (no wing outline across the body) while the outline layer keeps every line for drawing in."""

    def __init__(self, ink: ImageDraw.ImageDraw, full: ImageDraw.ImageDraw):
        self.ink, self.full = ink, full

    def line(self, *a, **k):
        self.ink.line(*a, **k)
        self.full.line(*a, **k)

    def ellipse(self, *a, **k):
        self.ink.ellipse(*a, **k)
        self.full.ellipse(*a, **k)


GULL_WHITE, GULL_GREY, GULL_BLACK = (250, 250, 252, 255), (178, 186, 200, 255), (38, 40, 52, 255)
GULL_BEAK, GULL_LEG = (246, 186, 48, 255), (232, 150, 120, 255)


def gull_flying(scale: float, flap: float, s0: float) -> tuple[Image.Image, Image.Image]:
    """A herring gull in flight drawn in ink, wings at `flap` (-1 down .. 1 up), heading right:
    (outlines only, the finished gull), RGBA, centred on the body; s0 is the wingspan in pixels at scale 1."""
    ss = 3
    span = s0 * scale * ss
    size = int(span * 1.25) + 8
    c = size / 2

    def P(x: float, y: float) -> tuple[float, float]:
        return (c + x * span, c + y * span)

    ink_l = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    col_l = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    di, dc = _Both(ImageDraw.Draw(ink_l), ImageDraw.Draw(col_l)), ImageDraw.Draw(col_l)
    ink = tuple(int(v) for v in INK) + (255,)
    lw = max(2, int(round(2.3 * ss * max(0.8, scale ** 0.5))))
    lift = 0.30 * flap
    for side in (-1, 1):
        lead = [P(side * 0.03, -0.02), P(side * 0.2, -0.09 - 0.6 * lift), P(side * 0.36, -0.07 - 0.9 * lift),
                P(side * 0.5, -0.02 - lift)]
        trail = [P(side * 0.44, 0.01 - 0.95 * lift), P(side * 0.4, 0.035 - 0.85 * lift), P(side * 0.34, 0.03 - 0.75 * lift),
                 P(side * 0.28, 0.055 - 0.6 * lift), P(side * 0.16, 0.07 - 0.35 * lift), P(side * 0.04, 0.05)]
        wing = _spline(lead, False, 8) + _spline(trail, False, 6)
        dc.polygon(wing, fill=GULL_GREY)
        tip = _spline([P(side * 0.36, -0.07 - 0.9 * lift), P(side * 0.5, -0.02 - lift)], False, 6) + \
            [P(side * 0.44, 0.01 - 0.95 * lift), P(side * 0.37, 0.02 - 0.85 * lift)]
        dc.polygon(tip, fill=GULL_BLACK)  # black wingtips with a white mirror spot
        sp, r = P(side * 0.44, -0.025 - 0.95 * lift), 0.012 * span
        dc.ellipse([sp[0] - r, sp[1] - r, sp[0] + r, sp[1] + r], fill=GULL_WHITE)
        di.line(wing + [wing[0]], fill=ink, width=lw, joint="curve")
    tail = [P(-0.17, 0.02), P(-0.27, -0.005), P(-0.28, 0.05), P(-0.17, 0.045)]
    dc.polygon(tail, fill=GULL_WHITE)
    di.line(tail + [tail[0]], fill=ink, width=lw, joint="curve")
    body = _spline([P(-0.2, 0.03), P(-0.08, -0.03), P(0.08, -0.035), P(0.17, -0.01), P(0.1, 0.035), P(-0.06, 0.045)], True, 8)
    dc.polygon(body, fill=GULL_WHITE)
    di.line(body + [body[0]], fill=ink, width=lw, joint="curve")
    head = [c + 0.13 * span, c - 0.065 * span, c + 0.215 * span, c + 0.015 * span]
    dc.ellipse(head, fill=GULL_WHITE)
    di.ellipse(head, outline=ink, width=lw)
    beak = [P(0.207, -0.035), P(0.27, -0.02), P(0.262, -0.008), P(0.205, -0.012)]
    dc.polygon(beak, fill=GULL_BEAK)
    di.line(beak + [beak[0]], fill=ink, width=max(1, lw // 2))
    e = P(0.185, -0.035)
    di.ellipse([e[0] - lw * 0.7, e[1] - lw * 0.7, e[0] + lw * 0.7, e[1] + lw * 0.7], fill=ink)
    out = (size // ss, size // ss)
    return ink_l.resize(out, Image.LANCZOS), col_l.resize(out, Image.LANCZOS)


def gull_perched(scale: float, s0: float, blink: bool = False, look: float = 0.0) -> tuple[Image.Image, Image.Image]:
    """The same gull standing, side view, facing right, feet at the bottom centre of the sprite:
    (outlines only, the finished gull); s0 is its height in pixels at scale 1."""
    ss = 3
    hgt = s0 * scale * ss
    size_w, size_h = int(hgt * 1.25) + 8, int(hgt * 1.05) + 8
    cx, foot = size_w / 2, size_h - 4

    def P(x: float, y: float) -> tuple[float, float]:
        return (cx + x * hgt, foot + (y - 1.0) * hgt)

    ink_l = Image.new("RGBA", (size_w, size_h), (0, 0, 0, 0))
    col_l = Image.new("RGBA", (size_w, size_h), (0, 0, 0, 0))
    di, dc = _Both(ImageDraw.Draw(ink_l), ImageDraw.Draw(col_l)), ImageDraw.Draw(col_l)
    ink = tuple(int(v) for v in INK) + (255,)
    lw = max(2, int(round(2.3 * ss * max(0.8, scale ** 0.5))))
    for lx in (-0.02, 0.06):  # legs and feet
        di.line([P(lx, 0.62), P(lx + 0.01, 0.98)], fill=GULL_LEG, width=lw + 2)
        di.line([P(lx - 0.04, 0.99), P(lx + 0.07, 0.99)], fill=GULL_LEG, width=lw + 1)
    body = _spline([P(-0.42, 0.42), P(-0.2, 0.36), P(0.08, 0.34), P(0.24, 0.42), P(0.22, 0.58), P(0.02, 0.66),
                    P(-0.22, 0.6)], True, 8)
    dc.polygon(body, fill=GULL_WHITE)
    di.line(body + [body[0]], fill=ink, width=lw, joint="curve")
    wing = _spline([P(0.14, 0.4), P(-0.08, 0.37), P(-0.34, 0.42), P(-0.5, 0.47), P(-0.3, 0.55), P(-0.04, 0.55),
                    P(0.12, 0.5)], True, 8)
    dc.polygon(wing, fill=GULL_GREY)
    dc.polygon(_spline([P(-0.3, 0.43), P(-0.5, 0.47), P(-0.33, 0.53)], False, 6), fill=GULL_BLACK)
    for sx in (-0.38, -0.44):
        sp, r = P(sx, 0.47), 0.012 * hgt
        dc.ellipse([sp[0] - r, sp[1] - r, sp[0] + r, sp[1] + r], fill=GULL_WHITE)
    di.line(wing + [wing[0]], fill=ink, width=lw, joint="curve")
    nx = 0.03 * look
    neck = _spline([P(0.12, 0.38), P(0.16 + nx, 0.24), P(0.24 + nx, 0.22), P(0.26, 0.4)], False, 6)
    dc.polygon(neck + [P(0.2, 0.42)], fill=GULL_WHITE)
    head = [P(0.13 + nx, 0.1)[0], P(0.13 + nx, 0.1)[1], P(0.33 + nx, 0.3)[0], P(0.33 + nx, 0.3)[1]]
    dc.ellipse(head, fill=GULL_WHITE)
    di.ellipse(head, outline=ink, width=lw)
    di.line(neck[:3], fill=ink, width=lw)
    beak = [P(0.315 + nx, 0.18), P(0.45 + nx, 0.2), P(0.44 + nx, 0.235), P(0.315 + nx, 0.23)]
    dc.polygon(beak, fill=GULL_BEAK)
    spot, r = P(0.41 + nx, 0.225), 0.012 * hgt
    dc.ellipse([spot[0] - r, spot[1] - r, spot[0] + r, spot[1] + r], fill=(220, 60, 50, 255))
    di.line(beak + [beak[0]], fill=ink, width=max(1, lw // 2))
    e = P(0.27 + nx, 0.17)
    if blink:
        di.line([(e[0] - lw, e[1]), (e[0] + lw, e[1])], fill=ink, width=max(1, lw // 2))
    else:
        di.ellipse([e[0] - lw * 0.75, e[1] - lw * 0.75, e[0] + lw * 0.75, e[1] + lw * 0.75], fill=ink)
    out = (size_w // ss, size_h // ss)
    return ink_l.resize(out, Image.LANCZOS), col_l.resize(out, Image.LANCZOS)
