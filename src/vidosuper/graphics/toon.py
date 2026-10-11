"""Flat cartoon rendering for VidoSuper's animated shorts.

The world is 3D (x right, y into the picture, z up) seen through a pinhole `Camera`, so props recede properly and
shadows are real projections of each caster's outline along the sun direction onto the ground and walls. Shapes are
drawn with cairo as flat colour with a shade crescent on the side away from the light and a thin rim toward it;
`finish` adds bloom, vignette and film grain to the numpy frame.
"""
from __future__ import annotations

import math

import cairo
import cv2
import numpy as np

# ---------- colour ----------


def hexc(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


def mix(a, b, t: float) -> tuple[float, float, float]:
    return tuple(float(x + (y - x) * t) for x, y in zip(a, b))


def darker(c, k: float = 0.8, cool: float = 0.08) -> tuple[float, float, float]:
    """Cel shade of a colour: darker and nudged toward blue, the way shadow sides look in warm light."""
    r, g, b = c
    return (r * k * (1 - cool), g * k * (1 - cool / 2), min(1.0, b * k * (1 + cool)))


# ---------- 2D shapes (polygons are (n, 2) float arrays, any units) ----------


def spline(pts, closed: bool = True, n: int = 8) -> np.ndarray:
    """Catmull-Rom curve through the points: a smooth outline from a few control points."""
    p = np.asarray(pts, np.float64)
    p = np.vstack([p[-1:], p, p[:2]]) if closed else np.vstack([p[:1], p, p[-1:]])
    t = np.linspace(0, 1, n, endpoint=False)[:, None]
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        out.append(0.5 * (2 * p1 + (p2 - p0) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t ** 2
                          + (3 * p1 - p0 - 3 * p2 + p3) * t ** 3))
    if not closed:
        out.append(p[-2:-1])
    return np.vstack(out)


def capsule(a, b, ra: float, rb: float, n: int = 20) -> np.ndarray:
    """Outline of a limb: the hull of a circle of radius ra at a and one of radius rb at b."""
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ring = np.stack([np.cos(th), np.sin(th)], 1)
    pts = np.vstack([np.asarray(a, np.float64) + ra * ring, np.asarray(b, np.float64) + rb * ring])
    scale = max(1e-9, float(np.abs(pts).max()))
    hull = cv2.convexHull((pts / scale).astype(np.float32))[:, 0]
    return hull.astype(np.float64) * scale


def ellipse(c, rx: float, ry: float | None = None, angle: float = 0.0, n: int = 36) -> np.ndarray:
    ry = rx if ry is None else ry
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ca, sa = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    x, y = rx * np.cos(th), ry * np.sin(th)
    return np.stack([c[0] + x * ca - y * sa, c[1] + x * sa + y * ca], 1)


def rect(x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float64)


def rrect(x0: float, y0: float, x1: float, y1: float, r: float, n: int = 6) -> np.ndarray:
    r = min(r, abs(x1 - x0) / 2, abs(y1 - y0) / 2)
    out = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for a in np.linspace(a0, a0 + 90, n):
            out.append((cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))))
    return np.array(out)


def rotate(pts, deg: float, c=(0.0, 0.0)) -> np.ndarray:
    ca, sa = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    p = np.asarray(pts, np.float64) - c
    return np.stack([p[:, 0] * ca - p[:, 1] * sa, p[:, 0] * sa + p[:, 1] * ca], 1) + c


# ---------- 3D ----------


class Camera:
    """Pinhole camera looking from pos at target; f is the focal length in pixels."""

    def __init__(self, pos, target, f: float, size=(1080, 1920), roll: float = 0.0, center=None):
        self.pos = np.asarray(pos, np.float64)
        fwd = np.asarray(target, np.float64) - self.pos
        fwd /= np.linalg.norm(fwd)
        right = np.cross(fwd, [0.0, 0.0, 1.0])
        right /= np.linalg.norm(right)
        up = np.cross(right, fwd)
        if roll:
            r = math.radians(roll)
            right, up = right * math.cos(r) + up * math.sin(r), up * math.cos(r) - right * math.sin(r)
        self.R = np.stack([right, up, fwd])
        self.f = float(f)
        self.w, self.h = size
        self.cx, self.cy = center if center is not None else (self.w / 2, self.h / 2)

    def cam(self, X) -> np.ndarray:
        return (np.asarray(X, np.float64) - self.pos) @ self.R.T

    def __call__(self, X) -> np.ndarray:
        c = self.cam(X)
        z = np.maximum(c[..., 2], 1e-3)
        return np.stack([self.cx + self.f * c[..., 0] / z, self.cy - self.f * c[..., 1] / z], -1)

    def scale(self, X) -> float:
        """Pixels per metre at a world point."""
        return float(self.f / max(1e-3, self.cam(X)[..., 2]))

    def poly(self, X, near: float = 0.05) -> np.ndarray:
        """Project a 3D polygon, clipped against the near plane first (for ground, walls and sea that reach behind
        the camera). Returns an empty array when nothing is in front."""
        Y = self.clip3d(X, near)
        return self(Y) if len(Y) >= 3 else np.zeros((0, 2))

    def clip3d(self, X, near: float = 0.05) -> np.ndarray:
        """The part of a 3D polygon in front of the camera (still in 3D)."""
        X = np.asarray(X, np.float64)
        Z = self.cam(X)[:, 2]
        out = []
        n = len(X)
        for i in range(n):
            a, b = X[i], X[(i + 1) % n]
            za, zb = Z[i], Z[(i + 1) % n]
            if za >= near:
                out.append(a)
            if (za >= near) != (zb >= near):
                t = (near - za) / (zb - za)
                out.append(a + t * (b - a))
        if len(out) < 3:
            return np.zeros((0, 3))
        return np.array(out)

    def rays(self, w: int, h: int) -> np.ndarray:
        """World-space view directions for a w x h grid covering the frame (for sky shading)."""
        xs = (np.arange(w) + 0.5) * self.w / w
        ys = (np.arange(h) + 0.5) * self.h / h
        xx, yy = np.meshgrid(xs, ys)
        d = np.stack([(xx - self.cx) / self.f, -(yy - self.cy) / self.f, np.ones_like(xx)], -1)
        d = d @ self.R
        return d / np.linalg.norm(d, axis=-1, keepdims=True)


def sky_dome(cam, sun_dir, stops_sun, stops_away, glow=(1.0, 0.9, 0.7), glow_k=6.0, glow_a=0.3, res=8) -> np.ndarray:
    """A sky that is right from any camera. stops_* = [(elevation in degrees, rgb), ...] - the colour bands looking
    toward the sun and away from it; in between they blend by azimuth. Plus a soft halo around the sun.
    Returns float RGB for the whole frame (the ground is drawn over it)."""
    w, h = max(8, cam.w // res), max(8, cam.h // res)
    d = cam.rays(w, h)
    sdir = np.asarray(sun_dir, np.float64)
    sdir = sdir / np.linalg.norm(sdir)
    el = np.degrees(np.arcsin(np.clip(d[..., 2], -1, 1)))

    def bands(stops):
        xs = np.array([e for e, _ in stops], np.float64)
        cs = np.array([c for _, c in stops], np.float64)
        return np.stack([np.interp(el, xs, cs[:, k]) for k in range(3)], -1)

    flat = np.array([sdir[0], sdir[1], 0.0])
    flat = flat / (np.linalg.norm(flat) + 1e-9)
    dh = d.copy()
    dh[..., 2] = 0
    dh /= np.linalg.norm(dh, axis=-1, keepdims=True) + 1e-9
    toward = np.clip((dh @ flat + 1) / 2, 0, 1)[..., None] ** 1.6
    img = bands(stops_away) * (1 - toward) + bands(stops_sun) * toward
    cosg = np.clip(d @ sdir, -1, 1)
    img = img + np.exp(-(1 - cosg) * glow_k)[..., None] * glow_a * np.asarray(glow, np.float64)
    img = cv2.resize(img.astype(np.float32), (cam.w, cam.h), interpolation=cv2.INTER_CUBIC)
    return np.clip(img, 0, 1.2)


def sun(azimuth: float, elevation: float) -> np.ndarray:
    """Direction the sunlight travels. azimuth: degrees from +y (into the picture) toward +x; elevation above the
    horizon. A sun behind the camera's left shoulder is azimuth ~ +40, elevation ~ 15."""
    a, e = math.radians(azimuth), math.radians(elevation)
    return np.array([math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), -math.sin(e)])


def on_plane(X, light, normal, d: float) -> np.ndarray:
    """Where each point's shadow lands on the plane normal . X = d, travelling along the light direction."""
    X = np.asarray(X, np.float64)
    n = np.asarray(normal, np.float64)
    t = (d - X @ n) / float(np.dot(n, light))
    return X + t[..., None] * light


def lift(pts2d, base, e1=(1.0, 0.0, 0.0), e2=(0.0, 0.0, 1.0)) -> np.ndarray:
    """A flat shape (u, v) standing in the world at base, spanned by e1 (u) and e2 (v)."""
    p = np.asarray(pts2d, np.float64)
    return np.asarray(base, np.float64) + p[:, :1] * np.asarray(e1) + p[:, 1:2] * np.asarray(e2)


def box_faces(x0, x1, y0, y1, z0, z1) -> list[tuple[str, np.ndarray]]:
    """Faces of an axis-aligned box as (name, 4x3 corners)."""
    return [
        ("top", np.array([[x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], float)),
        ("bottom", np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0]], float)),
        ("front", np.array([[x0, y0, z0], [x1, y0, z0], [x1, y0, z1], [x0, y0, z1]], float)),
        ("back", np.array([[x0, y1, z0], [x1, y1, z0], [x1, y1, z1], [x0, y1, z1]], float)),
        ("left", np.array([[x0, y0, z0], [x0, y1, z0], [x0, y1, z1], [x0, y0, z1]], float)),
        ("right", np.array([[x1, y0, z0], [x1, y1, z0], [x1, y1, z1], [x1, y0, z1]], float)),
    ]


# ---------- drawing ----------


def _path(ctx, pts) -> None:
    ctx.move_to(float(pts[0][0]), float(pts[0][1]))
    for x, y in pts[1:]:
        ctx.line_to(float(x), float(y))
    ctx.close_path()


class Painter:
    """A frame being drawn: a cairo ARGB surface with helpers for polygons in screen pixels."""

    def __init__(self, w: int, h: int, bg=(0.0, 0.0, 0.0)):
        """bg=None leaves the surface transparent (a layer to blur or composite later)."""
        self.w, self.h = w, h
        self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        self.ctx = cairo.Context(self.surface)
        if bg is not None:
            self.ctx.set_source_rgb(*bg)
            self.ctx.paint()

    def fill(self, pts, rgb, a: float = 1.0) -> None:
        if len(pts) < 3:
            return
        _path(self.ctx, pts)
        self.ctx.set_source_rgba(*rgb, a)
        self.ctx.fill()

    def cel(self, pts, base, shade=None, rim=None, light=(-1.0, -1.0), s: float = 6.0, r: float = 2.5,
            a: float = 1.0) -> None:
        """Flat part with a shade crescent away from the light and a rim crescent toward it. light points from the
        part toward the light in screen space; s and r are the crescent widths in pixels."""
        if len(pts) < 3:
            return
        pts = np.asarray(pts, np.float64)
        lv = np.asarray(light, np.float64)
        lv = lv / max(1e-9, float(np.hypot(*lv)))
        c = self.ctx
        c.save()
        _path(c, pts)
        c.clip()
        c.set_source_rgba(*base, a)
        c.paint()
        c.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        for col, off in ((shade, lv * s), (rim, -lv * r)):
            if col is None:
                continue
            c.rectangle(-1e5, -1e5, 2e5, 2e5)
            _path(c, pts + off)
            c.set_source_rgba(*col, a)
            c.fill()
        c.restore()

    def stroke(self, pts, rgb, width: float, a: float = 1.0, closed: bool = True) -> None:
        """Draw a line along pts (an outline around a part, or an open stroke)."""
        if len(pts) < 2:
            return
        c = self.ctx
        c.move_to(float(pts[0][0]), float(pts[0][1]))
        for x, y in pts[1:]:
            c.line_to(float(x), float(y))
        if closed:
            c.close_path()
        c.set_line_width(width)
        c.set_line_join(cairo.LINE_JOIN_ROUND)
        c.set_line_cap(cairo.LINE_CAP_ROUND)
        c.set_source_rgba(*rgb, a)
        c.stroke()

    def gradient(self, pts, stops, p0, p1, a: float = 1.0) -> None:
        """Fill with a linear gradient from screen point p0 to p1; stops = [(offset, rgb), ...]."""
        if len(pts) < 3:
            return
        g = cairo.LinearGradient(float(p0[0]), float(p0[1]), float(p1[0]), float(p1[1]))
        for o, col in stops:
            g.add_color_stop_rgba(o, *col, a)
        _path(self.ctx, pts)
        self.ctx.set_source(g)
        self.ctx.fill()

    def glow(self, c, r: float, rgb, a: float = 1.0, pts=None, inner: float = 0.0, screen: bool = False) -> None:
        """Radial light that fades from rgb at the centre to nothing at radius r (clipped to pts if given);
        screen=True brightens like light instead of painting over."""
        g = cairo.RadialGradient(float(c[0]), float(c[1]), float(inner), float(c[0]), float(c[1]), float(r))
        g.add_color_stop_rgba(0, *rgb, a)
        g.add_color_stop_rgba(0.35, *rgb, a * 0.45)
        g.add_color_stop_rgba(1, *rgb, 0)
        self.ctx.save()
        if pts is not None:
            _path(self.ctx, pts)
            self.ctx.clip()
        if screen:
            self.ctx.set_operator(cairo.OPERATOR_SCREEN)
        self.ctx.set_source(g)
        self.ctx.paint()
        self.ctx.restore()

    def shadow(self, polys, rgb, clip=None, sigma: float = 1.2, strength: float = 1.0, close: int = 0) -> None:
        """Multiply a cast shadow onto what is drawn so far: the union of polys, softened by sigma pixels and
        clipped to the receiving surface's outline."""
        polys = [p for p in polys if p is not None and len(p) >= 3]
        if not polys:
            return
        stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_A8, self.w)
        buf = np.zeros((self.h, stride), np.uint8)
        m = cairo.ImageSurface.create_for_data(memoryview(buf), cairo.FORMAT_A8, self.w, self.h, stride)
        mc = cairo.Context(m)
        mc.set_source_rgba(0, 0, 0, 1)
        for p in polys:
            _path(mc, p)
            mc.fill()
        m.flush()
        if close > 0:  # join a figure's shadow into one soft shape (arms to body, head to neck)
            k = 2 * int(close) + 1
            buf[:, :self.w] = cv2.morphologyEx(buf[:, :self.w], cv2.MORPH_CLOSE,
                                               cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
            m.mark_dirty()
        if sigma > 0 or strength < 1:
            a = buf[:, :self.w].astype(np.float32)
            if sigma > 0:
                a = cv2.GaussianBlur(a, (0, 0), sigma)
            buf[:, :self.w] = np.clip(a * strength, 0, 255).astype(np.uint8)
            m.mark_dirty()
        c = self.ctx
        if clip is not None and len(clip) < 3:
            return
        c.save()
        if clip is not None:
            _path(c, clip)
            c.clip()
        c.set_operator(cairo.OPERATOR_MULTIPLY)
        c.set_source_rgb(*rgb)
        c.mask_surface(m, 0, 0)
        c.restore()

    def blit(self, img: np.ndarray) -> None:
        """Replace the frame with an RGB image (float 0-1), e.g. a shaded sky to draw the world over."""
        self.surface.flush()
        a = np.ndarray((self.h, self.surface.get_stride() // 4, 4), np.uint8, self.surface.get_data())[:, :self.w]
        a[..., :3] = (np.clip(img[..., ::-1], 0, 1) * 255 + 0.5).astype(np.uint8)
        a[..., 3] = 255
        self.surface.mark_dirty()

    def modulate(self, pts, tex: np.ndarray) -> None:
        """Multiply what is drawn so far inside pts by (1 + tex), tex being a texture already warped to the frame
        (for plaster, stone and other surface grain that must stay on its surface)."""
        self.surface.flush()
        a = np.ndarray((self.h, self.surface.get_stride() // 4, 4), np.uint8, self.surface.get_data())[:, :self.w]
        mask = np.zeros((self.h, self.w), np.uint8)
        cv2.fillPoly(mask, [np.round(np.clip(np.asarray(pts), -1e5, 1e5)).astype(np.int32)], 1)
        m = mask.astype(bool)
        sub = a[m].astype(np.float32)
        sub[:, :3] *= (1 + tex[m])[:, None]
        a[m] = np.clip(sub, 0, 255).astype(np.uint8)
        self.surface.mark_dirty()

    def image(self) -> np.ndarray:
        """The frame as float32 RGB in [0, 1]."""
        self.surface.flush()
        a = np.ndarray((self.h, self.surface.get_stride() // 4, 4), np.uint8, self.surface.get_data())[:, :self.w]
        return a[..., 2::-1].astype(np.float32) / 255

    def rgba(self) -> np.ndarray:
        """The layer as float32 premultiplied RGBA in [0, 1]."""
        self.surface.flush()
        a = np.ndarray((self.h, self.surface.get_stride() // 4, 4), np.uint8, self.surface.get_data())[:, :self.w]
        return a[..., [2, 1, 0, 3]].astype(np.float32) / 255


def over(img: np.ndarray, layer: np.ndarray, blur: float = 0.0) -> np.ndarray:
    """Composite a premultiplied RGBA layer over an RGB image, optionally defocused by blur pixels."""
    if blur > 0:
        layer = cv2.GaussianBlur(layer, (0, 0), blur)
    return img * (1 - layer[..., 3:4]) + layer[..., :3]


# ---------- finishing ----------

_VIGNETTE: dict = {}


def finish(img: np.ndarray, seed: int = 0, bloom: float = 0.35, knee: float = 0.72, vignette: float = 0.22,
           grain: float = 0.016) -> np.ndarray:
    """Bloom on the bright parts, a soft vignette and fine film grain."""
    h, w = img.shape[:2]
    hi = np.clip(img - knee, 0, None)
    small = cv2.resize(hi, (w // 4, h // 4), interpolation=cv2.INTER_AREA)
    halo = cv2.GaussianBlur(small, (0, 0), 5) + 0.7 * cv2.GaussianBlur(small, (0, 0), 20)
    img = img + bloom * cv2.resize(halo, (w, h), interpolation=cv2.INTER_LINEAR)
    key = (h, w)
    if key not in _VIGNETTE:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r2 = ((xx - w / 2) / (w / 2)) ** 2 * 0.55 + ((yy - h / 2) / (h / 2)) ** 2 * 0.45
        _VIGNETTE[key] = np.clip(r2, 0, 1.6)[..., None]
    img = img * (1 - vignette * _VIGNETTE[key])
    if grain:
        g = np.random.default_rng(seed).standard_normal((h // 2, w // 2)).astype(np.float32)
        img = img + grain * cv2.resize(g, (w, h), interpolation=cv2.INTER_LINEAR)[..., None]
    return np.clip(img, 0, 1)


def to_bgr8(img: np.ndarray) -> np.ndarray:
    return (np.clip(img, 0, 1)[..., ::-1] * 255 + 0.5).astype(np.uint8)
