"""Horn OK Please: Tara's drawings, in the medium of the age she drew them.

- crayon (5): wax that skips on the grain, wobbly lines, scribbled fills, a smiling sun;
- chalk (8): dusty broken lines, white on the dark bumper and mudflaps;
- poster paint (11-15): flat brushed colour, uneven edges, bolder shapes;
- truck art (20): crisp shapes, bold black outlines, gradients, gold and white highlights, borders.

Each motif draws into an `Art` canvas in its own box, so the same drawing goes on the truck as a decal, onto the sheet,
and (with `alive`) glows at night.

    a = Art(600, 600); sun(a, 300, 300, 200, "crayon"); img = a.rgba()
"""

from __future__ import annotations

import math

import cairo
import cv2
import numpy as np

from vidosuper.graphics.toon import darker, hexc, mix, spline

INK = hexc("#1d1620")


def _noise(h: int, w: int, scale: float, seed: int) -> np.ndarray:
    """Smooth value noise in [0, 1] at a feature size of `scale` px."""
    rng = np.random.default_rng(seed)
    gh, gw = max(2, int(h / scale) + 2), max(2, int(w / scale) + 2)
    g = rng.random((gh, gw)).astype(np.float32)
    return cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC).clip(0, 1)


class Art:
    """A canvas for drawings: cairo on top, numpy texture passes (grain, dust, glow) underneath."""

    def __init__(self, w: int, h: int, seed: int = 1):
        self.w, self.h = w, h
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        self.base = np.zeros((h, w, 4), np.float32)  # premultiplied RGBA, the finished layers
        self._layer()

    # --- layers: each medium is drawn on its own layer, textured, then merged ---
    def _layer(self):
        self.surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.w, self.h)
        self.ctx = cairo.Context(self.surf)
        self.ctx.set_antialias(cairo.ANTIALIAS_BEST)
        self.ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        self.ctx.set_line_join(cairo.LINE_JOIN_ROUND)

    def _take(self) -> np.ndarray:
        self.surf.flush()
        buf = np.ndarray((self.h, self.surf.get_stride() // 4, 4), np.uint8, self.surf.get_data())[:, :self.w].astype(np.float32)
        out = np.dstack([buf[..., 2], buf[..., 1], buf[..., 0], buf[..., 3]]) / 255.0
        self._layer()
        return out

    def merge(self, medium: str = "", grain: float = 1.0):
        """Texture what was drawn since the last merge for its medium, then lay it over the canvas."""
        lay = self._take()
        if medium == "crayon":  # wax catches the tops of the paper grain: streaky gaps along the stroke direction
            g = 0.55 * _noise(self.h, self.w, 2.2, self.seed + 11) + 0.45 * _noise(self.h, self.w, 7.0, self.seed + 12)
            k = np.clip((g - 0.26 * grain) / 0.32, 0.25, 1.0)
            lay *= k[..., None]
        elif medium == "chalk":
            g = 0.6 * _noise(self.h, self.w, 1.6, self.seed + 21) + 0.4 * _noise(self.h, self.w, 5.0, self.seed + 22)
            k = np.clip((g - 0.3 * grain) / 0.28, 0.0, 1.0)
            dust = cv2.GaussianBlur(lay, (0, 0), 3.0) * 0.18
            lay = lay * k[..., None] + dust * (1 - lay[..., 3:4])
        elif medium == "paint":  # a little density variation, as poster paint has
            g = _noise(self.h, self.w, 18.0, self.seed + 31)
            lay[..., :3] *= (0.94 + 0.1 * g)[..., None]
        a = lay[..., 3:4]
        self.base = lay + self.base * (1 - a)

    def rgba(self) -> np.ndarray:
        """Finished drawing as uint8 RGBA (straight alpha)."""
        self.merge()
        a = self.base[..., 3:4]
        rgb = np.where(a > 1e-4, self.base[..., :3] / np.maximum(a, 1e-4), 0)
        return (np.dstack([rgb, a]) * 255).clip(0, 255).astype(np.uint8)

    # --- primitives ---
    def wobble(self, pts, amp: float, freq: float = 0.02) -> np.ndarray:
        """A hand's wobble: low-frequency drift across the line."""
        pts = np.asarray(pts, float)
        if len(pts) < 2 or amp <= 0:
            return pts
        d = np.gradient(pts, axis=0)
        n = np.stack([-d[:, 1], d[:, 0]], 1) / (np.linalg.norm(d, axis=1, keepdims=True) + 1e-9)
        L = np.cumsum(np.r_[0, np.linalg.norm(np.diff(pts, axis=0), axis=1)])
        ph = self.rng.uniform(0, 2 * np.pi, 3)
        off = (np.sin(L * freq + ph[0]) + 0.5 * np.sin(L * freq * 2.3 + ph[1]) + 0.25 * np.sin(L * freq * 5.1 + ph[2])) * amp
        return pts + n * off[:, None]

    def path(self, pts, closed=False):
        c = self.ctx
        c.new_path()
        c.move_to(*pts[0])
        for p in pts[1:]:
            c.line_to(*p)
        if closed:
            c.close_path()

    def stroke(self, pts, col, w, a=1.0, closed=False):
        self.path(pts, closed)
        self.ctx.set_source_rgba(*col, a)
        self.ctx.set_line_width(w)
        self.ctx.stroke()

    def fill(self, pts, col, a=1.0):
        self.path(pts, True)
        self.ctx.set_source_rgba(*col, a)
        self.ctx.fill()

    def ellipse_pts(self, cx, cy, rx, ry, n=64, a0=0.0, a1=2 * np.pi, rot=0.0) -> np.ndarray:
        t = np.linspace(a0, a1, n)
        x, y = rx * np.cos(t), ry * np.sin(t)
        cr, sr = math.cos(rot), math.sin(rot)
        return np.stack([cx + x * cr - y * sr, cy + x * sr + y * cr], 1)

    # crayon: a wobbly line laid down in a few passes
    def crayon(self, pts, col, w, closed=False, passes=3, wob=None):
        pts = np.asarray(pts, float)
        if closed:
            pts = np.vstack([pts, pts[:1] + (pts[1:2] - pts[:1]) * 0.15])
        for k in range(passes):
            q = self.wobble(pts, w * (0.35 if wob is None else wob), 0.03) + self.rng.normal(0, w * 0.1, 2)
            self.stroke(q, col, w * (0.75 + 0.2 * k / max(1, passes - 1)), 0.55 + 0.25 * (k == 0))

    def scribble(self, shape, col, w, angle=-35.0, gap=0.85, a=0.85):
        """A crayon fill: back-and-forth strokes across the shape, clipped to it (with a little overshoot)."""
        shape = np.asarray(shape, float)
        c = self.ctx
        c.save()
        self.path(self.wobble(shape, w * 0.4), True)
        c.clip()
        cx, cy = shape.mean(0)
        R = np.abs(shape - [cx, cy]).max() * 1.5
        ang = math.radians(angle)
        u = np.array([math.cos(ang), math.sin(ang)])
        v = np.array([-u[1], u[0]])
        pts = []
        s = -R
        flip = 1
        while s < R:
            a0 = np.array([cx, cy]) + v * s - u * R * flip
            a1 = np.array([cx, cy]) + v * (s + w * gap * 0.5) + u * R * flip
            pts += [a0, a1]
            s += w * gap
            flip = -flip
        q = self.wobble(np.array(pts), w * 0.25, 0.05)
        self.stroke(q, col, w, a)
        c.restore()

    def chalk(self, pts, col, w, closed=False):
        pts = np.asarray(pts, float)
        if closed:
            pts = np.vstack([pts, pts[:1]])
        for k in range(2):
            q = self.wobble(pts, w * 0.3, 0.04) + self.rng.normal(0, w * 0.12, 2)
            self.stroke(q, col, w * (0.85 + 0.25 * k), 0.85)

    def paint(self, shape, col, edge=1.5, streaks=True, w=None):
        """Poster paint: a fill with a slightly uneven edge and a few brush streaks inside."""
        shape = np.asarray(shape, float)
        q = self.wobble(shape, edge, 0.08)
        self.fill(q, col)
        if streaks:
            c = self.ctx
            c.save()
            self.path(q, True)
            c.clip()
            cx, cy = shape.mean(0)
            R = np.abs(shape - [cx, cy]).max()
            for _ in range(5):
                a = self.rng.uniform(-0.6, 0.6)
                y0 = cy + self.rng.uniform(-R, R)
                p = np.array([[cx - R * 1.2, y0], [cx + R * 1.2, y0 + R * a]])
                self.stroke(np.linspace(p[0], p[1], 12), mix(col, (1, 1, 1), 0.12) if self.rng.random() < 0.5 else darker(col, 0.9),
                            (w or R * 0.18) * self.rng.uniform(0.4, 1.0), 0.35)
            c.restore()

    def outline(self, shape, w, col=INK, closed=True):
        self.stroke(shape, col, w, 1.0, closed)

    def radial(self, cx, cy, r, stops):
        g = cairo.RadialGradient(cx, cy, 0, cx, cy, r)
        for t, col in stops:
            g.add_color_stop_rgba(t, *col, 1.0)
        return g

    def fill_pattern(self, pts, pattern):
        self.path(pts, True)
        self.ctx.set_source(pattern)
        self.ctx.fill()


# ---------- motifs ----------


def sun(a: Art, cx, cy, r, style="crayon", eyes: float = 1.0, smile: float = 1.0):
    """Her first drawing: a sun with a face. eyes: 0 shut (a crayon line) .. 1 open; smile 0..1 (it wakes)."""
    if style == "crayon":
        yel, ora = hexc("#ffcf2a"), hexc("#f07b1e")
        disc = a.ellipse_pts(cx, cy, r * 0.56, r * 0.53, 40)
        a.scribble(disc, yel, r * 0.07, angle=-30, gap=0.8)
        a.crayon(disc, ora, r * 0.06, closed=True)
        for k in range(11):
            ang = k * 2 * np.pi / 11 + 0.2
            r0, r1 = r * 0.68, r * (0.98 if k % 2 else 0.86)
            p = np.array([[cx + math.cos(ang) * r0, cy + math.sin(ang) * r0], [cx + math.cos(ang) * r1, cy + math.sin(ang) * r1]])
            a.crayon(np.linspace(p[0], p[1], 8), ora, r * 0.055)
        a.merge("crayon")
        brown = hexc("#3a2a2a")
        for sx in (-1, 1):
            if eyes > 0.15:  # open: crayon dots, taller as they open
                a.crayon(a.ellipse_pts(cx + sx * r * 0.18, cy - r * 0.08, r * 0.045, r * 0.06 * eyes, 14), brown, r * 0.05, passes=2)
            else:  # asleep: a curved crayon line
                a.crayon(a.ellipse_pts(cx + sx * r * 0.18, cy - r * 0.1, r * 0.06, r * 0.035, 10, 0.2, np.pi - 0.2), brown, r * 0.045,
                         passes=2)
        k0 = 0.35 + 0.25 * (1 - smile)
        a.crayon(a.ellipse_pts(cx, cy + r * (0.04 + 0.06 * (1 - smile)), r * 0.26, r * 0.22 * (0.4 + 0.6 * smile), 20, k0, np.pi - k0),
                 hexc("#d62839"), r * 0.055)
        a.merge("crayon", 0.7)
        # rosy cheeks
        for sx in (-1, 1):
            a.scribble(a.ellipse_pts(cx + sx * r * 0.3, cy + r * 0.08, r * 0.07, r * 0.05, 16), hexc("#f28aa0"), r * 0.03, gap=0.9)
        a.merge("crayon", 0.8)
        return
    # truck-art sun: rays as petals, a gradient face, gold rim
    for k in range(16):
        ang = k * 2 * np.pi / 16
        tip = np.array([cx + math.cos(ang) * r, cy + math.sin(ang) * r])
        l = np.array([cx + math.cos(ang - 0.17) * r * 0.62, cy + math.sin(ang - 0.17) * r * 0.62])
        rr = np.array([cx + math.cos(ang + 0.17) * r * 0.62, cy + math.sin(ang + 0.17) * r * 0.62])
        petal = spline(np.array([l, tip, rr, [cx + math.cos(ang) * r * 0.55, cy + math.sin(ang) * r * 0.55]]), True, 5)
        a.fill(petal, hexc("#f07b1e") if k % 2 else hexc("#ffcf2a"))
        a.outline(petal, r * 0.025)
    disc = a.ellipse_pts(cx, cy, r * 0.58, r * 0.58, 64)
    a.fill_pattern(disc, a.radial(cx - r * 0.15, cy - r * 0.15, r * 0.7, [(0, hexc("#fff3a8")), (0.7, hexc("#ffcf2a")), (1, hexc("#f2a51f"))]))
    a.outline(disc, r * 0.04)
    for sx in (-1, 1):
        a.fill(a.ellipse_pts(cx + sx * r * 0.2, cy - r * 0.08, r * 0.07, r * 0.09, 20), INK)
        a.fill(a.ellipse_pts(cx + sx * r * 0.2 - r * 0.02, cy - r * 0.11, r * 0.025, r * 0.03, 12), (1, 1, 1))
    a.stroke(a.ellipse_pts(cx, cy + r * 0.05, r * 0.25, r * 0.2, 24, 0.4, np.pi - 0.4), INK, r * 0.05)


def bird(a: Art, cx, cy, r, style="crayon", flap=0.0):
    """A bird: crayon (5) or truck-art parrot-ish (20). flap 0..1 lifts the wings (alive)."""
    if style == "crayon":
        blue, ora = hexc("#2f6fd6"), hexc("#f07b1e")
        body = a.ellipse_pts(cx, cy, r * 0.5, r * 0.3, 36)
        a.scribble(body, hexc("#7fb0ff"), r * 0.06, angle=20)
        a.crayon(body, blue, r * 0.06, closed=True)
        head = a.ellipse_pts(cx + r * 0.45, cy - r * 0.22, r * 0.2, r * 0.19, 24)
        a.scribble(head, hexc("#7fb0ff"), r * 0.05)
        a.crayon(head, blue, r * 0.055, closed=True)
        a.crayon(np.array([[cx + r * 0.62, cy - r * 0.26], [cx + r * 0.85, cy - r * 0.2], [cx + r * 0.62, cy - r * 0.14]]), ora, r * 0.05)
        up = 0.35 + 0.65 * flap
        wing = np.array([[cx - r * 0.25, cy - r * 0.05], [cx - r * 0.05, cy - r * (0.2 + 0.5 * up)], [cx + r * 0.1, cy - r * 0.05],
                         [cx + r * 0.25, cy - r * (0.2 + 0.45 * up)], [cx + r * 0.38, cy - r * 0.08]])
        a.crayon(spline(wing, False, 5), blue, r * 0.055)
        for lx in (-0.08, 0.12):
            a.crayon(np.array([[cx + r * lx, cy + r * 0.28], [cx + r * lx, cy + r * 0.5], [cx + r * (lx + 0.08), cy + r * 0.52]]), ora,
                     r * 0.04)
        a.merge("crayon")
        a.fill(a.ellipse_pts(cx + r * 0.5, cy - r * 0.25, r * 0.035, r * 0.04, 12), INK)
        a.merge("crayon", 0.5)
        return
    # truck-art bird (a parrot in green and red)
    green, lime, red = hexc("#1f9e5a"), hexc("#7ed957"), hexc("#d62839")
    body = spline(np.array([[cx - r * 0.55, cy + r * 0.25], [cx - r * 0.1, cy - r * 0.2], [cx + r * 0.35, cy - r * 0.45],
                            [cx + r * 0.55, cy - r * 0.2], [cx + r * 0.25, cy + r * 0.2], [cx - r * 0.2, cy + r * 0.35]]), True, 6)
    a.fill(body, green)
    a.fill(a.ellipse_pts(cx + r * 0.25, cy - r * 0.15, r * 0.25, r * 0.15, 24), lime, 0.8)
    a.outline(body, r * 0.035)
    beak = spline(np.array([[cx + r * 0.5, cy - r * 0.42], [cx + r * 0.72, cy - r * 0.32], [cx + r * 0.55, cy - r * 0.2]]), True, 4)
    a.fill(beak, red)
    a.outline(beak, r * 0.03)
    a.fill(a.ellipse_pts(cx + r * 0.38, cy - r * 0.33, r * 0.05, r * 0.05, 12), INK)
    tail = np.array([[cx - r * 0.5, cy + r * 0.2], [cx - r * 0.95, cy + r * 0.55], [cx - r * 0.85, cy + r * 0.3]])
    a.fill(tail, hexc("#2f6fd6"))
    a.outline(tail, r * 0.03)


def dog(a: Art, cx, cy, r, style="chalk", run=0.0):
    """A dog in profile, facing right: chalk (8) or poster paint. run 0..1 stretches the legs (alive)."""
    if style == "chalk":
        w = r * 0.07
        col = (0.97, 0.96, 0.93)
        body = a.ellipse_pts(cx, cy, r * 0.5, r * 0.26, 40)
        a.chalk(body, col, w, closed=True)
        head = a.ellipse_pts(cx + r * 0.55, cy - r * 0.3, r * 0.24, r * 0.22, 30)
        a.chalk(head, col, w, closed=True)
        a.chalk(a.ellipse_pts(cx + r * 0.8, cy - r * 0.24, r * 0.15, r * 0.1, 20, -1.9, 1.9), col, w)
        ear = spline(np.array([[cx + r * 0.42, cy - r * 0.5], [cx + r * 0.3, cy - r * 0.42], [cx + r * 0.27, cy - r * 0.22],
                               [cx + r * 0.36, cy - r * 0.18], [cx + r * 0.45, cy - r * 0.42]]), True, 5)
        a.chalk(ear, col, w * 1.1, closed=True)
        legs = (-0.35, -0.15, 0.2, 0.38)
        for i, lx in enumerate(legs):
            sw = (0.18 if i % 2 else -0.18) * run
            a.chalk(np.array([[cx + r * lx, cy + r * 0.2], [cx + r * (lx + sw), cy + r * 0.55]]), col, w)
        tail = spline(np.array([[cx - r * 0.48, cy - r * 0.05], [cx - r * 0.7, cy - r * 0.35], [cx - r * 0.62, cy - r * 0.55]]), False, 5)
        a.chalk(tail, col, w)
        a.merge("chalk")
        a.fill(a.ellipse_pts(cx + r * 0.6, cy - r * 0.36, r * 0.035, r * 0.04, 12), col)
        a.fill(a.ellipse_pts(cx + r * 0.94, cy - r * 0.27, r * 0.05, r * 0.042, 12), col)
        a.chalk(a.ellipse_pts(cx + r * 0.78, cy - r * 0.2, r * 0.08, r * 0.05, 12, 0.3, 2.6), col, w * 0.7)
        for sx, sy in ((-0.1, -0.05), (0.15, 0.05)):  # spots, scribbled in
            a.chalk(a.ellipse_pts(cx + r * sx, cy + r * sy, r * 0.08, r * 0.06, 14), hexc("#e8b77a"), w * 0.9, closed=True)
        a.merge("chalk", 0.8)
        return
    brown, cream = hexc("#b5713a"), hexc("#f3dcb5")
    a.paint(a.ellipse_pts(cx, cy, r * 0.5, r * 0.27, 40), brown)
    a.paint(a.ellipse_pts(cx + r * 0.55, cy - r * 0.3, r * 0.24, r * 0.22, 30), brown)
    a.paint(a.ellipse_pts(cx + r * 0.78, cy - r * 0.24, r * 0.14, r * 0.1, 20), cream)
    for lx in (-0.35, -0.15, 0.2, 0.38):
        a.paint(np.array([[cx + r * (lx - 0.06), cy + r * 0.15], [cx + r * (lx + 0.06), cy + r * 0.15],
                          [cx + r * (lx + 0.06), cy + r * 0.56], [cx + r * (lx - 0.06), cy + r * 0.56]]), brown, streaks=False)
    a.merge("paint")


def fish(a: Art, cx, cy, r, style="paint", swim=0.0):
    """A fish facing left: poster paint (11) or truck-art. swim bends the tail (alive)."""
    ora, yel = hexc("#f07b1e"), hexc("#ffcf2a")
    body = spline(np.array([[cx - r * 0.7, cy], [cx - r * 0.2, cy - r * 0.42], [cx + r * 0.4, cy - r * 0.3], [cx + r * 0.55, cy],
                            [cx + r * 0.4, cy + r * 0.3], [cx - r * 0.2, cy + r * 0.42]]), True, 8)
    bend = swim * r * 0.25
    tail = np.array([[cx + r * 0.5, cy], [cx + r * 0.95, cy - r * 0.42 + bend], [cx + r * 0.85, cy + bend * 0.5],
                     [cx + r * 0.95, cy + r * 0.42 + bend]])
    if style == "paint":
        a.paint(tail, yel)
        a.paint(body, ora)
        a.merge("paint")
        for k in range(4):  # scales as brushed arcs
            for j in (-1, 0, 1):
                sx, sy = cx - r * 0.05 + k * r * 0.16, cy + j * r * 0.16
                a.stroke(a.ellipse_pts(sx, sy, r * 0.09, r * 0.1, 12, -1.2, 1.2), hexc("#c4531a"), r * 0.035, 0.9)
        a.fill(a.ellipse_pts(cx - r * 0.4, cy - r * 0.1, r * 0.1, r * 0.1, 20), (1, 1, 1))
        a.fill(a.ellipse_pts(cx - r * 0.42, cy - r * 0.1, r * 0.05, r * 0.06, 14), INK)
        a.stroke(a.ellipse_pts(cx - r * 0.62, cy + r * 0.08, r * 0.08, r * 0.05, 12, -0.5, 1.6), INK, r * 0.03)
        for k, (bx, by, br) in enumerate(((-0.8, -0.42, 0.07), (-0.88, -0.64, 0.05), (-0.78, -0.82, 0.04))):
            a.stroke(a.ellipse_pts(cx + r * bx, cy + r * by, r * br, r * br, 16), hexc("#9fd8ff"), r * 0.025)
        a.merge("paint")
        return
    a.fill(tail, yel)
    a.outline(tail, r * 0.03)
    a.fill_pattern(body, a.radial(cx - r * 0.2, cy - r * 0.2, r * 0.8, [(0, hexc("#ffd166")), (0.6, ora), (1, hexc("#c4531a"))]))
    a.outline(body, r * 0.04)
    for k in range(4):
        for j in (-1, 0, 1):
            a.stroke(a.ellipse_pts(cx - r * 0.05 + k * r * 0.16, cy + j * r * 0.16, r * 0.09, r * 0.1, 12, -1.2, 1.2), INK, r * 0.02)
    a.fill(a.ellipse_pts(cx - r * 0.4, cy - r * 0.1, r * 0.1, r * 0.1, 20), (1, 1, 1))
    a.fill(a.ellipse_pts(cx - r * 0.42, cy - r * 0.1, r * 0.05, r * 0.06, 14), INK)


def flower(a: Art, cx, cy, r, style="art", col="#e94f8a", centre="#ffcf2a", petals=8):
    if style == "crayon":
        for k in range(petals):
            ang = k * 2 * np.pi / petals
            a.crayon(a.ellipse_pts(cx + math.cos(ang) * r * 0.55, cy + math.sin(ang) * r * 0.55, r * 0.32, r * 0.2, 18, rot=ang),
                     hexc(col), r * 0.08, closed=True, passes=2)
        a.scribble(a.ellipse_pts(cx, cy, r * 0.25, r * 0.25, 18), hexc(centre), r * 0.07)
        a.merge("crayon")
        return
    for k in range(petals):
        ang = k * 2 * np.pi / petals
        pt = a.ellipse_pts(cx + math.cos(ang) * r * 0.55, cy + math.sin(ang) * r * 0.55, r * 0.4, r * 0.22, 24, rot=ang)
        a.fill(pt, hexc(col))
        a.fill(a.ellipse_pts(cx + math.cos(ang) * r * 0.55, cy + math.sin(ang) * r * 0.55, r * 0.22, r * 0.09, 16, rot=ang),
               mix(hexc(col), (1, 1, 1), 0.35))
        a.outline(pt, r * 0.04)
    c = a.ellipse_pts(cx, cy, r * 0.28, r * 0.28, 24)
    a.fill(c, hexc(centre))
    a.outline(c, r * 0.04)
    for k in range(6):
        ang = k * np.pi / 3
        a.fill(a.ellipse_pts(cx + math.cos(ang) * r * 0.13, cy + math.sin(ang) * r * 0.13, r * 0.04, r * 0.04, 8), darker(hexc(centre), 0.6))


def lotus(a: Art, cx, cy, r):
    pink, deep = hexc("#f28aa0"), hexc("#d6336c")
    for k, ang in enumerate((-1.1, -0.55, 0.55, 1.1, 0.0)):
        L = r * (0.75 if k < 2 or k == 2 or k == 3 else 0.95)
        tip = np.array([cx + math.sin(ang) * L, cy - math.cos(ang) * L])
        base = np.array([cx, cy + r * 0.15])
        side = np.array([math.cos(ang), math.sin(ang)]) * r * 0.22
        petal = spline(np.array([base, base + (tip - base) * 0.5 - side, tip, base + (tip - base) * 0.5 + side]), True, 6)
        a.fill(petal, pink if k != 4 else deep)
        a.fill(spline(np.array([base, base + (tip - base) * 0.5 - side * 0.4, tip, base + (tip - base) * 0.5 + side * 0.4]), True, 5),
               mix(pink, (1, 1, 1), 0.4), 0.7)
        a.outline(petal, r * 0.035)
    leaf = a.ellipse_pts(cx, cy + r * 0.25, r * 0.85, r * 0.16, 40)
    a.fill(leaf, hexc("#1f9e5a"))
    a.outline(leaf, r * 0.035)


def peacock(a: Art, cx, cy, r, spread=1.0):
    """The truck painters' favourite: a peacock with its tail fanned behind it. spread 0..1 (alive: it opens)."""
    teal, blue, green, gold = hexc("#1aa39a"), hexc("#1f4fbf"), hexc("#2f9e44"), hexc("#f2c14e")
    n = 9
    for k in range(n):  # tail feathers, back to front
        t = (k / (n - 1) - 0.5) * 2
        ang = -np.pi / 2 + t * (0.25 + 0.95 * spread)
        L = r * (0.95 - 0.08 * abs(t))
        tip = np.array([cx - r * 0.15 + math.cos(ang) * L, cy + r * 0.1 + math.sin(ang) * L])
        base = np.array([cx - r * 0.15, cy + r * 0.1])
        d = (tip - base) / (np.linalg.norm(tip - base) + 1e-9)
        nrm = np.array([-d[1], d[0]])
        feather = spline(np.array([base, base + (tip - base) * 0.55 + nrm * r * 0.1, tip, base + (tip - base) * 0.55 - nrm * r * 0.1]),
                         True, 6)
        a.fill(feather, green)
        a.outline(feather, r * 0.018)
        e = base + (tip - base) * 0.8
        for rr, col in ((0.13, gold), (0.1, teal), (0.065, blue), (0.03, INK)):
            a.fill(a.ellipse_pts(e[0], e[1], r * rr * 0.8, r * rr, 24, rot=math.atan2(d[1], d[0]) + np.pi / 2), col)
    body = spline(np.array([[cx - r * 0.35, cy + r * 0.25], [cx - r * 0.2, cy - r * 0.05], [cx + r * 0.05, cy - r * 0.35],
                            [cx + r * 0.15, cy - r * 0.62], [cx + r * 0.28, cy - r * 0.6], [cx + r * 0.22, cy - r * 0.3],
                            [cx + r * 0.25, cy + r * 0.05], [cx + r * 0.05, cy + r * 0.32]]), True, 6)
    a.fill_pattern(body, a.radial(cx, cy - r * 0.2, r * 0.6, [(0, hexc("#4fc3f7")), (0.6, blue), (1, hexc("#163a8f"))]))
    a.outline(body, r * 0.03)
    a.fill(a.ellipse_pts(cx + r * 0.24, cy - r * 0.56, r * 0.03, r * 0.035, 12), (1, 1, 1))
    a.fill(a.ellipse_pts(cx + r * 0.245, cy - r * 0.56, r * 0.015, r * 0.02, 10), INK)
    beak = np.array([[cx + r * 0.28, cy - r * 0.58], [cx + r * 0.38, cy - r * 0.54], [cx + r * 0.28, cy - r * 0.51]])
    a.fill(beak, gold)
    a.outline(beak, r * 0.02)
    for k in (-1, 0, 1):  # the crest
        top = np.array([cx + r * (0.18 + k * 0.06), cy - r * 0.8])
        a.stroke(np.array([[cx + r * 0.2, cy - r * 0.62], top]), INK, r * 0.015)
        a.fill(a.ellipse_pts(top[0], top[1], r * 0.03, r * 0.03, 10), teal)
    for lx in (-0.05, 0.12):
        a.stroke(np.array([[cx + r * lx, cy + r * 0.28], [cx + r * lx, cy + r * 0.48], [cx + r * (lx + 0.07), cy + r * 0.5]]),
                 gold, r * 0.03)


def portrait(a: Art, cx, cy, r):
    """Truck-art portrait: little Tara on Papa's shoulders, both laughing, a sun behind her - drawn the way it really
    overlaps, so it reads as one picture: her body rises behind his head (her frock showing either side of it), her
    thighs come forward over his shoulders, his head sits in front of her lap, and his hands hold her shins."""
    skin_p, skin_t, hair = hexc("#a86a45"), hexc("#c08259"), hexc("#211a1e")
    yel, red, shirt = hexc("#f2b632"), hexc("#d62839"), hexc("#86aed0")
    lw = r * 0.024

    def blob(x, y, rx, ry, col, rot=0.0, n=40, w=None):
        e = a.ellipse_pts(x, y, rx, ry, n, rot=rot)
        a.fill(e, col)
        a.outline(e, w or lw)
        return e

    def limb(pts, w0, w1, col):
        """A tapered limb along a smooth path, outlined, with a round end."""
        P = spline(np.array(pts, float), False, 8)
        d = np.gradient(P, axis=0)
        nrm = np.stack([-d[:, 1], d[:, 0]], 1) / (np.linalg.norm(d, axis=1, keepdims=True) + 1e-9)
        wd = np.linspace(w0, w1, len(P))[:, None]
        poly = np.vstack([P + nrm * wd, (P - nrm * wd)[::-1]])
        a.fill(poly, col)
        a.outline(poly, lw)
        blob(P[-1][0], P[-1][1], w1, w1, col)

    def eyes_laugh(x, y, sep, rr):  # eyes squeezed shut with laughing
        for sx in (-1, 1):
            a.stroke(a.ellipse_pts(x + sx * sep, y + rr * 0.4, rr, rr * 0.75, 14, np.pi + 0.35, 2 * np.pi - 0.35), INK, lw * 1.3)

    def open_mouth(x, y, w, h):
        m = np.vstack([[[x - w, y], [x + w, y]], a.ellipse_pts(x, y, w, h, 18, 0, np.pi)[::-1]])
        a.fill(m, hexc("#7a2230"))
        a.fill(a.ellipse_pts(x, y + h * 0.6, w * 0.5, h * 0.28, 12), hexc("#e0707f"))

    sun(a, cx, cy - r * 0.66, r * 0.56, "art")

    # 1. Tara, behind his head: arms flung up, her frock (its lower part hidden behind his head), her head
    hy = cy - r * 0.68
    sh_y = hy + r * 0.2
    for sx in (-1, 1):
        limb([(cx + sx * r * 0.13, sh_y), (cx + sx * r * 0.27, hy - r * 0.02), (cx + sx * r * 0.4, hy - r * 0.22)], r * 0.045, r * 0.038,
             skin_t)
    frock = spline(np.array([[cx - r * 0.29, cy + r * 0.12], [cx - r * 0.2, sh_y + r * 0.05], [cx - r * 0.12, sh_y - r * 0.02],
                             [cx + r * 0.12, sh_y - r * 0.02], [cx + r * 0.2, sh_y + r * 0.05], [cx + r * 0.29, cy + r * 0.12]]), True, 6)
    a.fill(frock, yel)
    a.outline(frock, lw)
    for fx, fy in ((-0.12, -0.38), (0.06, -0.42), (0.14, -0.3), (-0.2, -0.22), (0.21, -0.18), (-0.02, -0.32)):
        a.fill(a.ellipse_pts(cx + r * fx, cy + r * fy, r * 0.016, r * 0.016, 10), (1, 0.97, 0.9))
    for sx in (-1, 1):  # bunches with red bows
        blob(cx + sx * r * 0.19, hy - r * 0.03, r * 0.08, r * 0.058, hair, rot=sx * 0.35)
        blob(cx + sx * r * 0.13, hy - r * 0.06, r * 0.028, r * 0.042, red)
    blob(cx, hy, r * 0.145, r * 0.14, skin_t)
    cap = np.vstack([a.ellipse_pts(cx, hy - r * 0.01, r * 0.148, r * 0.142, 40, np.pi, 2 * np.pi),
                     [[cx + r * 0.14, hy - r * 0.03], [cx + r * 0.07, hy - r * 0.065], [cx, hy - r * 0.045], [cx - r * 0.07, hy - r * 0.065],
                      [cx - r * 0.14, hy - r * 0.03]]])
    a.fill(cap, hair)
    eyes_laugh(cx, hy + r * 0.0, r * 0.055, r * 0.03)
    for sx in (-1, 1):
        a.fill(a.ellipse_pts(cx + sx * r * 0.095, hy + r * 0.06, r * 0.024, r * 0.014, 10), hexc("#ec7f86"), 0.75)
    open_mouth(cx, hy + r * 0.07, r * 0.045, r * 0.042)

    # 2. Papa's shoulders and shirt, the checked gamcha round his neck
    shp = spline(np.array([[cx - r * 0.88, cy + r * 1.0], [cx - r * 0.8, cy + r * 0.5], [cx - r * 0.42, cy + r * 0.32],
                           [cx + r * 0.42, cy + r * 0.32], [cx + r * 0.8, cy + r * 0.5], [cx + r * 0.88, cy + r * 1.0]]), True, 6)
    a.fill(shp, shirt)
    a.outline(shp, lw)
    a.stroke(np.array([[cx, cy + r * 0.48], [cx, cy + r * 1.0]]), darker(shirt, 0.7), lw)
    for by in (0.6, 0.78, 0.94):
        a.fill(a.ellipse_pts(cx, cy + r * by, r * 0.018, r * 0.018, 10), (0.95, 0.93, 0.88))

    # 3. her legs over his shoulders: thighs from behind his head out to the knees, shins down his chest
    for sx in (-1, 1):
        limb([(cx + sx * r * 0.16, cy + r * 0.06), (cx + sx * r * 0.32, cy + r * 0.2), (cx + sx * r * 0.43, cy + r * 0.34)], r * 0.085,
             r * 0.075, skin_t)
        limb([(cx + sx * r * 0.43, cy + r * 0.34), (cx + sx * r * 0.45, cy + r * 0.55), (cx + sx * r * 0.44, cy + r * 0.74)], r * 0.07,
             r * 0.058, skin_t)
        blob(cx + sx * r * 0.45, cy + r * 0.8, r * 0.075, r * 0.045, red)  # her sandals
    # the frock's hem falls over her thighs at the knee side of his head
    for sx in (-1, 1):
        hem = spline(np.array([[cx + sx * r * 0.18, cy + r * 0.0], [cx + sx * r * 0.33, cy + r * 0.12], [cx + sx * r * 0.36, cy + r * 0.2],
                               [cx + sx * r * 0.2, cy + r * 0.14]]), True, 5)
        a.fill(hem, yel)
        a.outline(hem, lw)

    # 4. Papa: gamcha, neck, ears, head (in front of her lap), hair, face
    a.fill(np.array([[cx - r * 0.1, cy + r * 0.16], [cx + r * 0.1, cy + r * 0.16], [cx + r * 0.12, cy + r * 0.38], [cx - r * 0.12, cy + r * 0.38]]),
           darker(skin_p, 0.85))
    g = a.ellipse_pts(cx, cy + r * 0.37, r * 0.26, r * 0.075, 30)
    a.fill(g, hexc("#c43d3d"))
    a.outline(g, lw)
    for k in range(-3, 4):
        a.stroke(np.array([[cx + k * r * 0.065, cy + r * 0.31], [cx + k * r * 0.065, cy + r * 0.43]]), (0.97, 0.91, 0.83), lw * 0.7)
    for sx in (-1, 1):
        blob(cx + sx * r * 0.235, cy + r * 0.03, r * 0.045, r * 0.065, skin_p)
    blob(cx, cy, r * 0.23, r * 0.26, skin_p)
    hair_pts = [[cx - r * 0.235, cy - r * 0.02]]
    for x, y in ((-0.24, -0.16), (-0.19, -0.25), (-0.1, -0.29), (-0.02, -0.27), (0.05, -0.3), (0.14, -0.28), (0.21, -0.22), (0.245, -0.12),
                 (0.235, -0.02)):
        hair_pts.append([cx + r * x, cy + r * y])
    hair_pts += [[cx + r * 0.2, cy - r * 0.1], [cx + r * 0.1, cy - r * 0.15], [cx - r * 0.06, cy - r * 0.13], [cx - r * 0.18, cy - r * 0.08]]
    hp = spline(np.array(hair_pts), True, 5)
    a.fill(hp, hair)
    a.outline(hp, lw * 0.8)
    a.stroke(np.array([[cx - r * 0.06, cy - r * 0.13], [cx - r * 0.09, cy - r * 0.27]]), mix(hair, (1, 1, 1), 0.25), lw * 0.8)
    eyes_laugh(cx, cy - r * 0.02, r * 0.09, r * 0.045)
    for sx in (-1, 1):
        a.stroke(np.array([[cx + sx * r * 0.05, cy - r * 0.09], [cx + sx * r * 0.14, cy - r * 0.1]]), hair, lw * 1.8)
    a.stroke(a.ellipse_pts(cx + r * 0.01, cy + r * 0.07, r * 0.032, r * 0.028, 12, -0.3, 2.2), darker(skin_p, 0.55), lw)
    open_mouth(cx, cy + r * 0.145, r * 0.085, r * 0.07)
    mo = spline(np.array([[cx - r * 0.17, cy + r * 0.16], [cx - r * 0.06, cy + r * 0.1], [cx, cy + r * 0.115], [cx + r * 0.06, cy + r * 0.1],
                          [cx + r * 0.17, cy + r * 0.16], [cx + r * 0.1, cy + r * 0.152], [cx, cy + r * 0.14], [cx - r * 0.1, cy + r * 0.152]]),
                 True, 4)
    a.fill(mo, hair)
    a.outline(mo, lw * 0.7)

    # 5. his big hands round her shins
    for sx in (-1, 1):
        blob(cx + sx * r * 0.445, cy + r * 0.57, r * 0.095, r * 0.07, skin_p)
        for k in range(3):
            a.stroke(np.array([[cx + sx * r * (0.39 + 0.045 * k), cy + r * 0.545], [cx + sx * r * (0.4 + 0.045 * k), cy + r * 0.6]]),
                     darker(skin_p, 0.55), lw * 0.6)


def border(a: Art, x0, y0, x1, y1, kind="scallop", cols=("#d62839", "#ffcf2a", "#1aa39a"), w=None):
    """Truck-art borders: scallops, zigzags, dots."""
    w = w or min(x1 - x0, y1 - y0) * 0.06
    L = np.hypot(x1 - x0, y1 - y0)
    d = np.array([x1 - x0, y1 - y0]) / L
    nrm = np.array([-d[1], d[0]])
    n = max(2, int(L / (w * 1.4)))
    for i in range(n):
        p = np.array([x0, y0]) + d * (i + 0.5) * L / n
        col = hexc(cols[i % len(cols)])
        if kind == "scallop":
            arc = a.ellipse_pts(p[0], p[1], L / n * 0.5, w * 0.7, 16, 0, np.pi, rot=math.atan2(d[1], d[0]))
            a.fill(arc, col)
            a.outline(arc, w * 0.12, closed=False)
        elif kind == "zigzag":
            tri = np.array([p - d * L / n * 0.5, p + nrm * w, p + d * L / n * 0.5])
            a.fill(tri, col)
            a.outline(tri, w * 0.1)
        else:
            a.fill(a.ellipse_pts(p[0], p[1], w * 0.35, w * 0.35, 12), col)
            a.outline(a.ellipse_pts(p[0], p[1], w * 0.35, w * 0.35, 12), w * 0.08)


def glow(img: np.ndarray, strength: float = 1.0, col=None) -> np.ndarray:
    """A drawing come alive at night: its own light around it (RGBA uint8 in, RGBA float premultiplied out)."""
    f = img.astype(np.float32) / 255
    rgb, a = f[..., :3], f[..., 3:4]
    pm = rgb * a
    sat = np.clip(rgb * 1.25 - rgb.mean(-1, keepdims=True) * 0.25, 0, 1)  # the glow is the drawing's colour, richer
    halo = cv2.GaussianBlur(sat * a, (0, 0), max(2.0, img.shape[1] * 0.02)) * 1.1 * strength
    halo2 = cv2.GaussianBlur(sat * a, (0, 0), max(4.0, img.shape[1] * 0.06)) * 0.8 * strength
    lit = pm + halo + halo2
    lit = lit / (1 + 0.35 * lit)  # soft shoulder: bright, never burnt white
    al = np.clip(np.maximum(a, (halo + halo2).max(-1, keepdims=True) * 0.9), 0, 1)
    return np.dstack([np.clip(lit * 1.3, 0, 1.0), al])
