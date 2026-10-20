"""Horn OK Please: the truck's paint job. Panels made from Tara's drawings (kidart.py) that go onto the truck's faces
as decals (truck.py): her childhood drawings kept in frames along the side, the portrait and the title on the back,
a peacock on the door, a sunburst crown over the windscreen, her first crayon sun on the bumper.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from vidosuper.graphics.kidart import Art, bird, border, dog, fish, flower, lotus, peacock, portrait, sun
from vidosuper.graphics.toon import hexc, spline

FONTS = Path(__file__).resolve().parents[3] / "assets" / "fonts"


def lettering(text: str, w: int, h: int, fill="#d62839", edge="#ffcf2a", shadow="#1d1620",
              font="Fraunces-SemiBold.ttf") -> np.ndarray:
    """Truck-painter's lettering: a bold face, a bright edge, a hard shadow below right (RGBA uint8)."""
    from PIL import Image, ImageDraw, ImageFont
    size = h
    while size > 8:
        f = ImageFont.truetype(str(FONTS / font), size)
        box = f.getbbox(text)
        if box[2] - box[0] <= w * 0.9 and box[3] - box[1] <= h * 0.72:
            break
        size -= 2
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    box = f.getbbox(text)
    x = (w - (box[2] - box[0])) / 2 - box[0]
    y = (h - (box[3] - box[1])) / 2 - box[1] - h * 0.04
    d.text((x, y), text, font=f, fill=255)
    core = np.array(m).astype(np.float32) / 255
    r = max(2, int(h * 0.05))
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    ring = cv2.dilate(core, ker)
    sh = np.roll(np.roll(ring, int(r * 1.3), 0), int(r * 1.3), 1)
    out = np.zeros((h, w, 4), np.float32)
    for layer, col in ((sh, shadow), (ring, edge), (core, fill)):
        c = np.array(hexc(col), np.float32)
        out[..., :3] = out[..., :3] * (1 - layer[..., None]) + c * layer[..., None]
        out[..., 3] = np.maximum(out[..., 3], layer)
    top = np.clip(core - np.roll(core, int(r * 0.9), 0), 0, 1) * 0.5  # a glint along the top of each letter
    out[..., :3] = out[..., :3] * (1 - top[..., None]) + top[..., None]
    return (out * 255).clip(0, 255).astype(np.uint8)


def paste(a: Art, img: np.ndarray, x: float, y: float, alpha: float = 1.0):
    """Lay a finished RGBA drawing onto the canvas with its top-left at (x, y)."""
    a.merge()
    h, w = img.shape[:2]
    x, y = int(round(x)), int(round(y))
    x0, y0, x1, y1 = max(0, x), max(0, y), min(a.w, x + w), min(a.h, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    f = img[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32) / 255
    f[..., 3] *= alpha
    pm = np.dstack([f[..., :3] * f[..., 3:4], f[..., 3:4]])
    a.base[y0:y1, x0:x1] = pm + a.base[y0:y1, x0:x1] * (1 - pm[..., 3:4])


def drawing(fn, size: int, *args, seed: int = 3, **kw) -> np.ndarray:
    d = Art(size, size, seed=seed)
    fn(d, size / 2, size / 2, size * 0.42, *args, **kw)
    return d.rgba()


def rect(a: Art, x0, y0, x1, y1, col):
    a.fill(np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]), hexc(col))


def frame(a: Art, x0, y0, x1, y1, bg, rim="#ffcf2a", arch=True):
    """A truck-art frame: an arched panel with a gold rim and white dots round it."""
    w, h = x1 - x0, y1 - y0
    top = y0 + (h * 0.18 if arch else 0)
    pts = [[x0, y1], [x0, top]]
    if arch:
        pts += list(a.ellipse_pts((x0 + x1) / 2, top, w / 2, h * 0.18, 30, np.pi, 2 * np.pi))
    else:
        pts += [[x0, y0], [x1, y0]]
    pts += [[x1, top], [x1, y1]]
    pts = np.array(pts, float)
    a.fill(pts, hexc(rim))
    a.outline(pts, max(2, w * 0.012))
    c = pts.mean(0)
    inner = c + (pts - c) * np.array([(w - h * 0.08) / w, (h - h * 0.08) / h])
    a.fill(inner, hexc(bg))
    a.outline(inner, max(1.5, w * 0.008))
    peri = np.vstack([pts, pts[:1]])
    L = np.cumsum(np.r_[0, np.linalg.norm(np.diff(peri, axis=0), axis=1)])
    n = max(6, int(L[-1] / (h * 0.09)))
    for k in range(n):
        d = L[-1] * (k + 0.5) / n
        i = int(np.clip(np.searchsorted(L, d) - 1, 0, len(peri) - 2))
        t = (d - L[i]) / max(1e-6, L[i + 1] - L[i])
        p = peri[i] + (peri[i + 1] - peri[i]) * t
        e = a.ellipse_pts(p[0], p[1], h * 0.02, h * 0.02, 10)
        a.fill(e, (1, 1, 1))
        a.outline(e, max(1, h * 0.005))


def gallery(w=1900, h=580, seed=5) -> np.ndarray:
    """The truck's side: every drawing she made it, framed later in truck art by her twenty-year-old self, left to
    right as she grew: crayon bird (5), chalk dog (8), painted fish (11), parrot (15), peacock (20)."""
    a = Art(w, h, seed)
    rect(a, 0, 0, w, h, "#1f4fbf")
    border(a, 0, h * 0.05, w, h * 0.05, "scallop", ("#d62839", "#ffcf2a", "#1aa39a", "#f07b1e"), w=h * 0.07)
    border(a, w, h * 0.95, 0, h * 0.95, "zigzag", ("#ffcf2a", "#d62839"), w=h * 0.05)
    items = [(bird, ("crayon",), "#fff3d6", 11), (dog, ("chalk",), "#3d4250", 12), (fish, ("paint",), "#b9e8ff", 13),
             (bird, ("art",), "#ffd6e5", 14), (peacock, (), "#fff3d6", 15)]
    n = len(items)
    pw = w * 0.84 / n
    gap = (w - pw * n) / (n + 1)
    rims = ("#ffcf2a", "#f07b1e", "#1aa39a", "#d62839", "#ffcf2a")
    for i, (fn, args, bg, sd) in enumerate(items):
        x0 = gap + i * (pw + gap)
        frame(a, x0, h * 0.15, x0 + pw, h * 0.86, bg, rim=rims[i])
        size = int(min(pw * 0.86, h * 0.62))
        paste(a, drawing(fn, size, *args, seed=sd), x0 + (pw - size) / 2, h * 0.85 - size - h * 0.02)
        if i < n - 1:  # a lotus on its stem between frames
            cx = x0 + pw + gap / 2
            a.stroke(np.array([[cx, h * 0.86], [cx, h * 0.4]]), hexc("#1f9e5a"), gap * 0.12)
            lotus(a, cx, h * 0.38, gap * 0.4)
    return a.rgba()


def tailgate(w=1000, h=600, seed=7, portrait_u: float = 1.0, portrait=None) -> np.ndarray:
    """The back: the portrait in an arch, lotuses either side, HORN OK PLEASE underneath. portrait (or portrait_u):
    how much of the portrait is painted yet (she paints it last, top to bottom)."""
    from vidosuper.graphics.kidart import portrait as draw_portrait
    u = portrait_u if portrait is None else portrait
    a = Art(w, h, seed)
    rect(a, 0, 0, w, h, "#1aa39a")
    border(a, 0, h * 0.04, w, h * 0.04, "scallop", ("#d62839", "#ffcf2a", "#1f4fbf"), w=h * 0.06)
    frame(a, w * 0.3, h * 0.08, w * 0.7, h * 0.74, "#fff3d6", rim="#d62839")
    if u >= 1.0:
        draw_portrait(a, w * 0.5, h * 0.46, h * 0.28)
    else:
        b = Art(w, h, seed + 1)
        draw_portrait(b, w * 0.5, h * 0.46, h * 0.28)
        img = b.rgba()
        yy = np.mgrid[0:h, 0:w][0].astype(np.float32)
        top, bot = h * 0.1, h * 0.74
        m = np.clip(((top + (bot - top) * u * 1.1) - yy) / (h * 0.04), 0, 1)
        img[..., 3] = (img[..., 3] * m).astype(np.uint8)
        paste(a, img, 0, 0)
    for sx in (0.14, 0.86):
        lotus(a, w * sx, h * 0.46, h * 0.16)
        flower(a, w * sx, h * 0.19, h * 0.07, col="#f07b1e")
    paste(a, lettering("HORN OK PLEASE", int(w * 0.94), int(h * 0.21)), w * 0.03, h * 0.765)
    return a.rgba()


def door(w=560, h=360, seed=9) -> np.ndarray:
    a = Art(w, h, seed)
    frame(a, w * 0.03, h * 0.04, w * 0.97, h * 0.96, "#fff3d6", rim="#d62839", arch=False)
    peacock(a, w * 0.5, h * 0.58, h * 0.42)
    for sx in (0.12, 0.88):
        flower(a, w * sx, h * 0.24, h * 0.08, col="#1aa39a")
    return a.rgba()


def lotus_panel(w=600, h=330, bg="#d62839", seed=10) -> np.ndarray:
    a = Art(w, h, seed)
    rect(a, 0, 0, w, h, bg)
    border(a, 0, h * 0.07, w, h * 0.07, "dots", ("#ffcf2a", "#ffffff"), w=h * 0.08)
    vine = np.array([[w * 0.03, h * 0.78], [w * 0.25, h * 0.58], [w * 0.5, h * 0.82], [w * 0.75, h * 0.58], [w * 0.97, h * 0.78]])
    a.stroke(spline(vine, False, 10), hexc("#1f9e5a"), h * 0.035)
    lotus(a, w * 0.5, h * 0.56, h * 0.34)
    for sx in (0.18, 0.82):
        flower(a, w * sx, h * 0.52, h * 0.12, col="#ffcf2a", centre="#d62839")
    return a.rgba()


def bumper(w=1160, h=170, painted=True, seed=12) -> np.ndarray:
    """The front bumper: painter's stripes, and her very first crayon sun kept at the end, faded by the road."""
    a = Art(w, h, seed)
    if painted:
        k, x = 0, -h
        while x < w + h:
            a.fill(np.array([[x, h], [x + h * 0.5, 0], [x + h, 0], [x + h * 0.5, h]]), hexc("#1d1620") if k % 2 else hexc("#ffcf2a"))
            x += h * 0.5
            k += 1
        box = np.array([[w * 0.77, h * 0.05], [w * 0.99, h * 0.05], [w * 0.99, h * 0.95], [w * 0.77, h * 0.95]])
        a.fill(box, hexc("#fff3d6"))
        a.outline(box, h * 0.03)
    s = drawing(sun, int(h * 0.98), "crayon", seed=1)
    paste(a, s, w * 0.88 - h * 0.49, h * 0.01, alpha=0.7 if painted else 1.0)
    return a.rgba()


def crown(w=1120, h=230, seed=14) -> np.ndarray:
    """The crown over the windscreen: a sunburst among mirror-work."""
    a = Art(w, h, seed)
    rect(a, 0, 0, w, h, "#d62839")
    for k in range(14):
        x = w * (k + 0.5) / 14
        if abs(x - w / 2) < h * 0.62:
            continue
        for y, col in ((h * 0.3, "#ffcf2a"), (h * 0.72, "#1aa39a")):
            e = a.ellipse_pts(x, y, h * 0.1, h * 0.1, 16)
            a.fill(e, hexc(col))
            a.fill(a.ellipse_pts(x, y, h * 0.05, h * 0.05, 12), (0.9, 0.94, 1.0))
            a.fill(a.ellipse_pts(x - h * 0.017, y - h * 0.017, h * 0.016, h * 0.016, 8), (1, 1, 1))
            a.outline(e, h * 0.012)
    sun(a, w / 2, h * 0.56, h * 0.5, "art")
    return a.rgba()


def headboard(w=1200, h=320, seed=16) -> np.ndarray:
    """The cargo's front crown over the cab: a rising sun between two parrots."""
    a = Art(w, h, seed)
    rect(a, 0, 0, w, h, "#1f4fbf")
    sun(a, w / 2, h * 0.64, h * 0.5, "art")
    for sx, flip in ((0.22, 1), (0.78, -1)):
        img = drawing(bird, int(h * 0.9), "art", seed=int(sx * 100))
        if flip < 0:
            img = img[:, ::-1].copy()
        paste(a, img, w * sx - h * 0.45, h * 0.1)
    border(a, 0, h * 0.95, w, h * 0.95, "dots", ("#ffcf2a", "#d62839", "#ffffff"), w=h * 0.08)
    return a.rgba()


def flap(w=270, h=370, seed=18) -> np.ndarray:
    a = Art(w, h, seed)
    rect(a, 0, 0, w, h, "#232226")
    border(a, 0, h * 0.06, w, h * 0.06, "zigzag", ("#ffffff",), w=w * 0.09)
    border(a, w, h * 0.94, 0, h * 0.94, "zigzag", ("#ffffff",), w=w * 0.09)
    flower(a, w * 0.5, h * 0.42, w * 0.3, col="#ffffff", centre="#d62839")
    e = a.ellipse_pts(w * 0.5, h * 0.78, w * 0.12, w * 0.12, 24)
    a.fill(e, hexc("#d62839"))
    a.fill(a.ellipse_pts(w * 0.47, h * 0.765, w * 0.04, w * 0.04, 12), (1, 0.8, 0.8))
    return a.rgba()
