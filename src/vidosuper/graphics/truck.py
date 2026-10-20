"""Horn OK Please: Papa's truck, an old long-nosed Indian goods truck built from simple solids and drawn as ink and
paint through a pinhole camera (vidosuper.graphics.toon.Camera), plain or painted with Tara's art.

Truck frame: x across (+ = the truck's left side as you face it... no: + = toward the truck's right hand), y forward
(the nose points +y), z up, ground at z = 0. Every face is a flat polygon with a colour; faces are culled when they
look away, sorted far to near, filled with three cel tones by how they face the sun, and edged in ink. Decals (her
drawings, truck-art panels) are images warped onto quads that lie on the faces.

    T = Truck(painted=True); img = T.render(Camera(...))
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cairo
import cv2
import numpy as np

from vidosuper.graphics.toon import darker, hexc, mix

INK = hexc("#1d1620")
SUN = np.array([-0.45, 0.35, 0.82])
SUN = SUN / np.linalg.norm(SUN)


@dataclass
class Face:
    pts: np.ndarray  # (n, 3) truck frame
    col: tuple
    key: str = ""
    lines: list = field(default_factory=list)  # 3D polylines drawn on the face
    bias: float = 0.0  # + drawn earlier (treated as farther)
    edge: float = 1.0  # ink line strength
    glass: bool = False
    two_sided: bool = False


@dataclass
class Decal:
    corners: np.ndarray  # (4, 3): top-left, top-right, bottom-right, bottom-left of the image on the truck
    img: np.ndarray  # RGBA uint8
    host: str  # the face it lies on (drawn right after it)
    alpha: float = 1.0


def _normal(P: np.ndarray) -> np.ndarray:
    n = np.zeros(3)
    for i in range(len(P)):
        a, b = P[i], P[(i + 1) % len(P)]
        n += np.array([(a[1] - b[1]) * (a[2] + b[2]), (a[2] - b[2]) * (a[0] + b[0]), (a[0] - b[0]) * (a[1] + b[1])])
    return n / (np.linalg.norm(n) + 1e-12)


def _orient(P, centre) -> np.ndarray:
    """Points of a face ordered so its normal points away from the solid's centre."""
    P = np.asarray(P, float)
    if np.dot(_normal(P), P.mean(0) - centre) < 0:
        P = P[::-1]
    return P


class Builder:
    def __init__(self):
        self.faces: list[Face] = []

    def add(self, pts, col, centre=None, **kw):
        pts = np.asarray(pts, float)
        if centre is not None:
            pts = _orient(pts, np.asarray(centre, float))
        self.faces.append(Face(pts, col, **kw))
        return self.faces[-1]

    def box(self, x0, x1, y0, y1, z0, z1, col, key="", skip=(), sides=None, **kw):
        c = np.array([(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2])
        quads = {
            "left": [[x0, y0, z0], [x0, y1, z0], [x0, y1, z1], [x0, y0, z1]],
            "right": [[x1, y0, z0], [x1, y1, z0], [x1, y1, z1], [x1, y0, z1]],
            "back": [[x0, y0, z0], [x1, y0, z0], [x1, y0, z1], [x0, y0, z1]],
            "front": [[x0, y1, z0], [x1, y1, z0], [x1, y1, z1], [x0, y1, z1]],
            "bottom": [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0]],
            "top": [[x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]],
        }
        out = {}
        for name, q in quads.items():
            if name in skip:
                continue
            col_f = (sides or {}).get(name, col)
            out[name] = self.add(q, col_f, c, key=f"{key}.{name}" if key else "", **kw)
        return out

    def prism_x(self, profile_yz, x0, x1, col, key="", cap_col=None, **kw):
        """A solid extruded across the truck: profile (y, z) points, from x0 to x1."""
        prof = np.asarray(profile_yz, float)
        c = np.array([(x0 + x1) / 2, prof[:, 0].mean(), prof[:, 1].mean()])
        out = {}
        out["capL"] = self.add([[x0, y, z] for y, z in prof], cap_col or col, c, key=f"{key}.capL" if key else "", **kw)
        out["capR"] = self.add([[x1, y, z] for y, z in prof], cap_col or col, c, key=f"{key}.capR" if key else "", **kw)
        n = len(prof)
        for i in range(n):
            (ya, za), (yb, zb) = prof[i], prof[(i + 1) % n]
            q = [[x0, ya, za], [x0, yb, zb], [x1, yb, zb], [x1, ya, za]]
            mid = np.array([(x0 + x1) / 2, (ya + yb) / 2, (za + zb) / 2])
            # sides of a non-convex profile: orient by the profile's own winding instead of the centre
            e = np.array([0, yb - ya, zb - za])
            nrm = np.cross(np.array([1.0, 0, 0]), e)
            if _ccw2(prof) < 0:
                nrm = -nrm
            P = np.asarray(q, float)
            if np.dot(_normal(P), -nrm) < 0:
                P = P[::-1]
            self.faces.append(Face(P, col, key=f"{key}.s{i}" if key else "", **kw))
        return out

    def prism_y(self, profile_xz, y0, y1, col, key="", cap_col=None, **kw):
        """A solid extruded along the truck: profile (x, z) points, from y0 to y1."""
        prof = np.asarray(profile_xz, float)
        c = np.array([prof[:, 0].mean(), (y0 + y1) / 2, prof[:, 1].mean()])
        out = {}
        out["back"] = self.add([[x, y0, z] for x, z in prof], cap_col or col, c, key=f"{key}.back" if key else "", **kw)
        out["front"] = self.add([[x, y1, z] for x, z in prof], cap_col or col, c, key=f"{key}.front" if key else "", **kw)
        n = len(prof)
        for i in range(n):
            (xa, za), (xb, zb) = prof[i], prof[(i + 1) % n]
            q = [[xa, y0, za], [xb, y0, zb], [xb, y1, zb], [xa, y1, za]]
            out[f"s{i}"] = self.add(q, col, c, key=f"{key}.s{i}" if key else "", **kw)
        return out

    def cylinder(self, c, axis, r, h, col, cap_col=None, n=20, key="", **kw):
        c = np.asarray(c, float)
        ax = np.asarray(axis, float) / np.linalg.norm(axis)
        u = np.cross(ax, [0, 0, 1.0]) if abs(ax[2]) < 0.9 else np.cross(ax, [1.0, 0, 0])
        u /= np.linalg.norm(u)
        v = np.cross(ax, u)
        t = np.linspace(0, 2 * np.pi, n, endpoint=False)
        ring = np.cos(t)[:, None] * u + np.sin(t)[:, None] * v
        a, b = c - ax * h / 2, c + ax * h / 2
        out = {"capA": self.add(a + ring * r, cap_col or col, c, key=f"{key}.capA" if key else "", **kw),
               "capB": self.add(b + ring * r, cap_col or col, c, key=f"{key}.capB" if key else "", **kw)}
        for i in range(n):
            j = (i + 1) % n
            self.add([a + ring[i] * r, a + ring[j] * r, b + ring[j] * r, b + ring[i] * r], col, c, key=f"{key}.side" if key else "",
                     edge=0.0, **kw)
        return out


def _ccw2(P) -> float:
    x, y = P[:, 0], P[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def _arc(cy, cz, r, a0, a1, n=12):
    t = np.radians(np.linspace(a0, a1, n))
    return [(cy + r * math.cos(a), cz + r * math.sin(a)) for a in t]


PLAIN = dict(cab="#6f8597", nose="#6f8597", fender="#5f7486", trim="#4a4f57", chassis="#2b2a2e", tyre="#2a2729",
             hub="#7b8189", glass="#9fc2d6", wood="#8a6a48", wood_dark="#6e5238", tarp="#4f6d7a", chrome="#c9ccd2",
             lamp="#f4f1e0", tank="#5c6066", flap="#232226", visor="#5f7486", bumper="#4a4f57", head="#8a6a48")
PAINTED = dict(cab="#f2b632", nose="#d62839", fender="#1f9e5a", trim="#1f4fbf", chassis="#2b2a2e", tyre="#2a2729",
               hub="#d62839", glass="#9fc2d6", wood="#f07b1e", wood_dark="#c4531a", tarp="#1aa39a", chrome="#dfe2e6",
               lamp="#fff6d0", tank="#1f4fbf", flap="#232226", visor="#d62839", bumper="#f2b632", head="#1f4fbf")


class Truck:
    def __init__(self, painted: bool = False, decals: list[Decal] | None = None, tassels: bool | None = None):
        self.painted = painted
        self.pal = {k: hexc(v) for k, v in (PAINTED if painted else PLAIN).items()}
        self.decals = decals or []
        self.tassels = painted if tassels is None else tassels
        self._build()

    # ---------- the solids ----------
    def _build(self):
        P = self.pal
        b = Builder()
        # wheels: front single, rear dual
        self.wheels = []
        for x in (-0.98, 0.98):
            self.wheels.append((x, 2.45))
        for x in (-0.98, -0.7, 0.7, 0.98):
            self.wheels.append((x, -2.3))
        for x, y in self.wheels:
            side = 1 if x > 0 else -1
            cy = b.cylinder([x, y, 0.5], [1, 0, 0], 0.5, 0.26, P["tyre"], key="wheel", bias=0.6, n=22)
            # hub on the outer face
            if abs(x) > 0.8:
                hub = [np.array([x + side * 0.132, y + 0.24 * math.cos(t), 0.5 + 0.24 * math.sin(t)])
                       for t in np.linspace(0, 2 * np.pi, 20, endpoint=False)]
                f = b.add(hub, P["hub"], [x, y, 0.5], key="hub", bias=0.55)
                f.lines = [[np.array([x + side * 0.134, y + 0.15 * math.cos(t), 0.5 + 0.15 * math.sin(t)]) for t in
                            np.linspace(0, 2 * np.pi, 21)]]
        # chassis
        b.box(-0.48, 0.48, -4.1, 3.2, 0.42, 0.72, P["chassis"], key="chassis", bias=0.8)
        # front bumper
        b.box(-1.16, 1.16, 3.2, 3.38, 0.48, 0.82, P["bumper"], key="bumper")
        # bonnet: a long nose with chamfered top edges
        nose = [(-0.72, 0.76), (0.72, 0.76), (0.72, 1.42), (0.56, 1.57), (-0.56, 1.57), (-0.72, 1.42)]
        bn = b.prism_y(nose, 2.02, 3.2, P["nose"], key="bonnet")
        # grille and its bars
        g = b.box(-0.48, 0.48, 3.2, 3.25, 0.86, 1.44, P["chrome"], key="grille", skip=("back",))
        g["front"].lines = [[np.array([x, 3.252, 0.9]), np.array([x, 3.252, 1.4])] for x in np.linspace(-0.4, 0.4, 9)]
        # front mudguards: arches over the wheels, a flat lamp shelf at the front
        for side in (-1, 1):
            x0, x1 = (0.72, 1.17) if side > 0 else (-1.17, -0.72)
            outer = _arc(2.45, 0.5, 0.66, 168, 12, 14)
            inner = _arc(2.45, 0.5, 0.57, 12, 168, 14)
            prof = outer + [(3.2, 0.62), (3.2, 0.98)] + [(3.15, 1.05)] + inner[:1] + inner
            prof = outer + inner
            b.prism_x(prof, x0, x1, P["fender"], key=f"fender{side}")
            # lamp shelf
            b.box(min(x0, x1), max(x0, x1), 2.85, 3.2, 0.95, 1.02, P["fender"], key=f"shelf{side}")
            # headlight: chrome rim cylinder, glass face
            hc = [side * 0.94, 3.12, 1.2]
            h = b.cylinder(hc, [0, 1, 0], 0.15, 0.14, P["chrome"], key=f"lamp{side}", n=18)
            h["capB"].col = P["lamp"]
            h["capB"].lines = [[np.array([hc[0] + 0.1 * math.cos(t), 3.192, 1.2 + 0.1 * math.sin(t)]) for t in np.linspace(2.2, 3.4, 6)]]
            b.box(side * 0.94 - 0.03, side * 0.94 + 0.03, 3.0, 3.06, 1.0, 1.1, P["chrome"], key="lampbracket")
        # cab
        cab = b.box(-1.12, 1.12, 0.72, 2.02, 0.95, 2.55, P["cab"], key="cab")
        # windscreen (two panes) and the door windows
        for xa, xb in ((-1.0, -0.04), (0.04, 1.0)):
            b.add([[xa, 2.024, 1.74], [xb, 2.024, 1.74], [xb, 2.024, 2.4], [xa, 2.024, 2.4]], P["glass"], [0, 1.0, 1.8],
                  key="windscreen", glass=True)
        for side in (-1, 1):
            x = side * 1.122
            b.add([[x, 0.9, 1.78], [x, 1.84, 1.78], [x, 1.84, 2.4], [x, 0.9, 2.4]], P["glass"], [0, 1.4, 1.8], key=f"window{side}",
                  glass=True)
            door = cab["right" if side > 0 else "left"]
            door.lines = [[np.array([x * 1.0005, y, z]) for y, z in ((0.84, 1.0), (1.96, 1.0), (1.96, 2.47), (0.84, 2.47), (0.84, 1.0))],
                          [np.array([x * 1.0007, 1.72, 1.6]), np.array([x * 1.0007, 1.86, 1.6])]]
            # step and mirror
            b.box(min(side * 1.0, side * 1.18), max(side * 1.0, side * 1.18), 1.1, 1.62, 0.74, 0.8, P["trim"], key="step")
            b.box(min(side * 1.12, side * 1.42), max(side * 1.12, side * 1.42), 1.98, 2.02, 2.2, 2.24, P["trim"], key="mirrorarm")
            b.box(min(side * 1.4, side * 1.47), max(side * 1.4, side * 1.47), 1.94, 2.1, 1.92, 2.42, P["trim"], key="mirror")
        if self.painted:  # the crown (taj) over the windscreen: a board with an arched, scalloped top
            crown = [(-1.13, 2.53), (1.13, 2.53), (1.13, 2.78), (0.9, 2.86), (0.64, 2.83), (0.38, 2.94), (0.0, 3.04),
                     (-0.38, 2.94), (-0.64, 2.83), (-0.9, 2.86), (-1.13, 2.78)]
            b.prism_y(crown, 1.98, 2.1, P["visor"], key="taj")
        else:  # a plain sun visor
            vis = [(1.95, 2.5), (2.3, 2.66), (2.3, 2.84), (1.95, 2.6)]
            b.prism_x(vis, -1.14, 1.14, P["visor"], key="visor")
        # fuel tank on the near (left) side
        b.cylinder([-0.86, 1.15, 0.66], [0, 1, 0], 0.22, 0.72, P["tank"], key="tank", n=18, bias=0.2)
        # cargo body: sides, floor, headboard with an arched crown, tarp over the load
        cb = b.box(-1.2, 1.2, -4.15, 0.62, 1.0, 2.45, P["wood"], key="body", skip=("top",))
        for side in ("left", "right"):
            if self.painted:
                break
            x = -1.2 if side == "left" else 1.2
            cb[side].lines = [[np.array([x * 1.001, -4.15, z]), np.array([x * 1.001, 0.62, z])] for z in np.arange(1.22, 2.42, 0.24)]
            cb[side].lines += [[np.array([x * 1.001, y, 1.0]), np.array([x * 1.001, y, 2.45])] for y in np.linspace(-3.6, 0.1, 6)]
        if not self.painted:
            cb["back"].lines = [[np.array([x, -4.152, 1.0]), np.array([x, -4.152, 2.45])] for x in (-0.4, 0.4)]
            cb["back"].lines += [[np.array([-1.2, -4.152, z]), np.array([1.2, -4.152, z])] for z in (1.4, 1.95)]
        head = [(-1.2, 2.45), (1.2, 2.45), (1.2, 2.78), (0.92, 2.96), (0.0, 3.08), (-0.92, 2.96), (-1.2, 2.78)]
        b.prism_y(head, 0.58, 0.68, P["head"], key="headboard")
        tarp = [(-1.18, 2.45), (1.18, 2.45), (1.16, 2.62), (0.85, 2.76), (0.0, 2.82), (-0.85, 2.76), (-1.16, 2.62)]
        tp = b.prism_y(tarp, -4.1, 0.58, P["tarp"], key="tarp")
        for k in tp:
            if k.startswith("s"):
                F = tp[k]
                F.lines = [[F.pts[0] + (F.pts[3] - F.pts[0]) * 0 + (F.pts[1] - F.pts[0]) * 0 + (F.pts[3] - F.pts[0]) * t,
                            F.pts[1] + (F.pts[2] - F.pts[1]) * t] for t in np.linspace(0.1, 0.9, 5)]
        # rear: bumper, tail lamps, mudflaps
        b.box(-1.0, 1.0, -4.22, -4.08, 0.38, 0.52, P["trim"], key="rearbumper")
        for side in (-1, 1):
            b.box(min(side * 0.82, side * 1.05), max(side * 0.82, side * 1.05), -4.2, -4.15, 0.62, 0.8, hexc("#c0392b"), key="taillamp")
            x0, x1 = sorted((side * 0.58, side * 1.12))
            b.box(x0, x1, -2.94, -2.9, 0.12, 0.86, P["flap"], key=f"flap{side}", bias=0.1)
        self.faces = b.faces

    # ---------- drawing ----------
    def render(self, cam, size=None, root=(0.0, 0.0, 0.0), heading: float = 0.0, lw: float | None = None,
               bg=None, holes=(), lights_on: float = 0.0) -> np.ndarray:
        """The truck seen by a toon Camera (world: x right, y forward, z up), standing at root, nose toward heading
        degrees (0 = +y). Returns premultiplied float RGBA (h, w, 4). holes: keys of glass faces left open (to see
        the cab's inside through them); their screen polygons are left in self.holes. lights_on: headlamps lit 0..1."""
        W, H = size or (cam.w, cam.h)
        h = math.radians(heading)
        Rz = np.array([[math.cos(h), math.sin(h), 0], [-math.sin(h), math.cos(h), 0], [0, 0, 1.0]])
        root = np.asarray(root, float)

        def world(P):
            return np.asarray(P, float) @ Rz.T + root

        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
        ctx = cairo.Context(surf)
        ctx.set_antialias(cairo.ANTIALIAS_BEST)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ppm = cam.scale(world(np.array([[0, 0, 1.5]]))[0])
        lw = lw or min(max(1.0, ppm * 0.012), 1.0 + 4.0 * W / 1080)  # thin far away, never fat up close
        sun_w = SUN @ Rz.T
        items = []
        self.holes = []
        for f in self.faces:
            Pw = world(f.pts)
            if f.key in holes:
                if np.dot(_normal(Pw), cam.pos - Pw.mean(0)) > 0:
                    self.holes.append(cam(Pw))
                continue
            c = Pw.mean(0)
            n = _normal(Pw)
            to_cam = cam.pos - c
            if np.dot(n, to_cam) <= 0 and not f.two_sided:
                continue
            d = np.linalg.norm(to_cam) + f.bias
            items.append((d, f, Pw, n))
        items = _order(items, cam)
        by_host: dict[str, list[Decal]] = {}
        for dc in self.decals:
            by_host.setdefault(dc.host, []).append(dc)
        for d, f, Pw, n in items:
            near = cam.cam(Pw)[:, 2] < 0.05
            if near.all():
                continue
            P2 = cam.poly(Pw) if near.any() else cam(Pw)  # a face reaching behind the camera is clipped, never flipped
            if len(P2) < 3:
                continue
            clipped = bool(near.any())
            lam = float(np.dot(n, sun_w))
            col = f.col if lam > 0.35 else (darker(f.col, 0.86, 0.06) if lam > -0.1 else darker(f.col, 0.72, 0.1))
            if f.glass:
                col = mix(f.col, (0.12, 0.16, 0.24), 0.25)
            if lights_on and f.col == self.pal["lamp"]:
                col = mix(f.col, (1.0, 0.97, 0.85), lights_on)
            _poly(ctx, P2)
            ctx.set_source_rgb(*col)
            ctx.fill_preserve()
            if f.edge:
                ctx.set_source_rgba(*INK, 0.9 * f.edge)
                ctx.set_line_width(lw)
                ctx.stroke()
            else:
                ctx.new_path()
            if f.glass:  # a sky reflection band across the glass
                ctx.save()
                _poly(ctx, P2)
                ctx.clip()
                x0, y0 = P2.min(0)
                x1, y1 = P2.max(0)
                ctx.move_to(x0 + (x1 - x0) * 0.2, y1)
                ctx.line_to(x0 + (x1 - x0) * 0.45, y1)
                ctx.line_to(x0 + (x1 - x0) * 0.75, y0)
                ctx.line_to(x0 + (x1 - x0) * 0.5, y0)
                ctx.close_path()
                ctx.set_source_rgba(1, 1, 1, 0.28)
                ctx.fill()
                ctx.restore()
            for dc in ([] if clipped else by_host.get(f.key, [])):
                _decal(ctx, cam, world(dc.corners), dc.img, P2, W, H, dc.alpha)
            for line in f.lines:
                Lw = world(np.array(line))
                if (cam.cam(Lw)[:, 2] < 0.05).any():
                    continue
                L2 = cam(Lw)
                ctx.move_to(*L2[0])
                for q in L2[1:]:
                    ctx.line_to(*q)
                ctx.set_source_rgba(*darker(f.col, 0.55, 0.1), 0.85)
                ctx.set_line_width(lw * 0.8)
                ctx.stroke()
        if self.holes:  # cut the windows open through the cab behind them, and edge them in ink
            ctx.save()
            ctx.set_operator(cairo.OPERATOR_CLEAR)
            for q in self.holes:
                _poly(ctx, q)
                ctx.fill()
            ctx.restore()
            for q in self.holes:
                _poly(ctx, q)
                ctx.set_source_rgb(*INK)
                ctx.set_line_width(lw * 1.1)
                ctx.stroke()
        if self.tassels and np.dot(cam.pos - world([0, 3.3, 0.6])[0] if False else cam.pos - world(np.array([[0, 3.3, 0.6]]))[0],
                                   np.array([0, 1.0, 0]) @ Rz.T) > 0:
            _tassels(ctx, cam, world, lw)
        surf.flush()
        buf = np.ndarray((H, surf.get_stride() // 4, 4), np.uint8, surf.get_data())[:, :W].astype(np.float32) / 255
        out = np.dstack([buf[..., 2], buf[..., 1], buf[..., 0], buf[..., 3]])
        # one thick ink outline round the whole truck
        a = out[..., 3]
        r = max(1, int(round(lw * 1.6)))
        ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        a_out = np.clip((cv2.dilate(cv2.GaussianBlur(a, (0, 0), 0.6), ker) - 0.15) / 0.7, 0, 1)
        rgb = out[..., :3] + np.array(INK, np.float32) * (a_out * (1 - a))[..., None]
        return np.dstack([rgb, np.maximum(a, a_out)]).clip(0, 1)


def _order(items, cam):
    """Far to near, the way a painter must: when two faces overlap on screen, the one lying wholly behind the
    other's plane goes first (Newell); only faces that cross each other fall back to distance."""
    n = len(items)
    P2 = [cam(it[2]) for it in items]
    lo = np.array([p.min(0) for p in P2])
    hi = np.array([p.max(0) for p in P2])
    planes = [(it[3], it[2].mean(0)) for it in items]
    eps = 1e-4
    after = [set() for _ in range(n)]  # after[i]: faces that must be drawn after i
    indeg = np.zeros(n, int)
    for i in range(n):
        for j in range(i + 1, n):
            if (lo[i] >= hi[j]).any() or (lo[j] >= hi[i]).any():
                continue
            (ni, ci), (nj, cj) = planes[i], planes[j]
            Pi, Pj = items[i][2], items[j][2]
            si = (Pj - ci) @ ni  # j's points against i's plane (+ = on the camera side, as every face here faces us)
            sj = (Pi - cj) @ nj
            if (sj <= eps).all() and not (sj >= -eps).all():
                first, second = i, j  # i lies behind j's plane
            elif (si >= -eps).all() and not (si <= eps).all():
                first, second = i, j  # j lies in front of i's plane
            elif (si <= eps).all() and not (si >= -eps).all():
                first, second = j, i
            elif (sj >= -eps).all() and not (sj <= eps).all():
                first, second = j, i
            else:
                continue
            if second not in after[first]:
                after[first].add(second)
                indeg[second] += 1
    import heapq
    heap = [(-items[k][0], k) for k in range(n) if indeg[k] == 0]
    heapq.heapify(heap)
    done = np.zeros(n, bool)
    order = []
    while len(order) < n:
        if not heap:  # a cycle: take the farthest face left
            k = max((k for k in range(n) if not done[k]), key=lambda k: items[k][0])
            indeg[k] = 0
            heap = [(-items[k][0], k)]
        _, k = heapq.heappop(heap)
        if done[k]:
            continue
        done[k] = True
        order.append(items[k])
        for m in after[k]:
            indeg[m] -= 1
            if indeg[m] == 0 and not done[m]:
                heapq.heappush(heap, (-items[m][0], m))
    return order


def _poly(ctx, P2):
    ctx.new_path()
    ctx.move_to(*P2[0])
    for q in P2[1:]:
        ctx.line_to(*q)
    ctx.close_path()


def _decal(ctx, cam, corners_w, img, clip2, W, H, alpha):
    """Warp a decal image onto its quad (perspective-correct) and paint it clipped to its host face."""
    if (cam.cam(corners_w)[:, 2] < 0.05).any():
        return
    dst = cam(corners_w).astype(np.float32)
    h, w = img.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    x0, y0 = np.floor(dst.min(0)).astype(int) - 2
    x1, y1 = np.ceil(dst.max(0)).astype(int) + 2
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    M = cv2.getPerspectiveTransform(src, dst - np.float32([x0, y0]))
    f = img.astype(np.float32) / 255
    pm = np.dstack([f[..., :3] * f[..., 3:4], f[..., 3:4]])
    warped = cv2.warpPerspective(pm, M, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    bgra = np.ascontiguousarray((np.dstack([warped[..., 2], warped[..., 1], warped[..., 0], warped[..., 3]]) * 255 * alpha)
                                .clip(0, 255).astype(np.uint8))
    s = cairo.ImageSurface.create_for_data(bgra, cairo.FORMAT_ARGB32, x1 - x0, y1 - y0, bgra.strides[0])
    ctx.save()
    _poly(ctx, clip2)
    ctx.clip()
    ctx.set_source_surface(s, x0, y0)
    ctx.paint()
    ctx.restore()


def _tassels(ctx, cam, world, lw):
    """Black and red pompoms on strings along the front bumper, as every decorated truck has."""
    for k, x in enumerate(np.linspace(-1.05, 1.05, 9)):
        top = world(np.array([[x, 3.385, 0.6]]))[0]
        bot = world(np.array([[x, 3.4, 0.38 - 0.03 * (k % 2)]]))[0]
        a, b2 = cam(np.array([top, bot]))
        ctx.move_to(*a)
        ctx.line_to(*b2)
        ctx.set_source_rgb(*INK)
        ctx.set_line_width(lw * 0.7)
        ctx.stroke()
        r = cam.scale(bot) * 0.045
        ctx.arc(b2[0], b2[1] + r * 0.6, r, 0, 2 * math.pi)
        ctx.set_source_rgb(*(hexc("#d62839") if k % 2 else hexc("#1d1620")))
        ctx.fill_preserve()
        ctx.set_source_rgb(*INK)
        ctx.set_line_width(lw * 0.6)
        ctx.stroke()


def view_camera(view: str = "front34", size=(1800, 1000), dist: float = 22.0, fill: float = 0.9):
    """A long-lens camera for showing the whole truck on a sheet."""
    from vidosuper.graphics.toon import Camera
    target = np.array([0.0, -0.4, 1.25])
    dirs = {"front34": (-0.62, 0.78, 0.22), "rear34": (-0.6, -0.8, 0.2), "side": (-1.0, 0.0, 0.08), "front": (0.0, 1.0, 0.1)}
    d = np.array(dirs[view], float)
    d /= np.linalg.norm(d)
    pos = target + d * dist
    f = size[0] * fill * dist / 8.6
    return Camera(pos, target, f, size=size)


def _q(*pts):
    return np.array(pts, float)


def paint_job(painted: bool = True) -> list[Decal]:
    """Where her art sits on the truck. Plain: only her first crayon sun on the bumper. Painted: everything.
    (Made once each and kept: the panels take seconds to paint.)"""
    return list(_paint_job_cached(bool(painted)))


from functools import lru_cache  # noqa: E402


@lru_cache(maxsize=2)
def _paint_job_cached(painted: bool):
    return tuple(_paint_job_build(painted))


@lru_cache(maxsize=8)
def _drawing_cached(kind: str):
    from vidosuper.graphics import kidart as K
    from vidosuper.graphics import truckart as A
    if kind == "dog":
        return A.drawing(K.dog, 360, "chalk", seed=12)
    if kind == "fish":
        return A.drawing(K.fish, 360, "paint", seed=13)
    return A.drawing(K.peacock, 420, seed=14)


@lru_cache(maxsize=160)
def _tailgate_cached(u: float):
    from vidosuper.graphics import truckart as A
    return A.tailgate(portrait=u)


def _paint_job_build(painted: bool = True) -> list[Decal]:
    from vidosuper.graphics import truckart as A
    if not painted:
        return [Decal(_q((1.16, 3.381, 0.82), (-1.16, 3.381, 0.82), (-1.16, 3.381, 0.48), (1.16, 3.381, 0.48)),
                      A.bumper(painted=False), "bumper.front")]
    g = A.gallery()
    return [
        Decal(_q((-1.2005, 0.62, 2.45), (-1.2005, -4.15, 2.45), (-1.2005, -4.15, 1.0), (-1.2005, 0.62, 1.0)), g, "body.left"),
        Decal(_q((1.2005, -4.15, 2.45), (1.2005, 0.62, 2.45), (1.2005, 0.62, 1.0), (1.2005, -4.15, 1.0)), g, "body.right"),
        Decal(_q((-1.2, -4.1505, 2.45), (1.2, -4.1505, 2.45), (1.2, -4.1505, 1.0), (-1.2, -4.1505, 1.0)), A.tailgate(), "body.back"),
        Decal(_q((-1.1205, 1.96, 1.72), (-1.1205, 0.84, 1.72), (-1.1205, 0.84, 1.02), (-1.1205, 1.96, 1.02)), A.door(), "cab.left"),
        Decal(_q((1.1205, 0.84, 1.72), (1.1205, 1.96, 1.72), (1.1205, 1.96, 1.02), (1.1205, 0.84, 1.02)), A.door(), "cab.right"),
        Decal(_q((-0.7205, 3.2, 1.42), (-0.7205, 2.02, 1.42), (-0.7205, 2.02, 0.76), (-0.7205, 3.2, 0.76)), A.lotus_panel(),
              "bonnet.s5"),
        Decal(_q((0.7205, 2.02, 1.42), (0.7205, 3.2, 1.42), (0.7205, 3.2, 0.76), (0.7205, 2.02, 0.76)), A.lotus_panel(), "bonnet.s1"),
        Decal(_q((0.56, 2.02, 1.5705), (-0.56, 2.02, 1.5705), (-0.56, 3.2, 1.5705), (0.56, 3.2, 1.5705)),
              A.lotus_panel(600, 600, bg="#ffcf2a"), "bonnet.s3"),
        Decal(_q((1.16, 3.381, 0.82), (-1.16, 3.381, 0.82), (-1.16, 3.381, 0.48), (1.16, 3.381, 0.48)), A.bumper(), "bumper.front"),
        Decal(_q((1.13, 2.1005, 3.04), (-1.13, 2.1005, 3.04), (-1.13, 2.1005, 2.53), (1.13, 2.1005, 2.53)), A.crown(), "taj.front"),
        Decal(_q((1.2, 0.6805, 3.08), (-1.2, 0.6805, 3.08), (-1.2, 0.6805, 2.45), (1.2, 0.6805, 2.45)), A.headboard(),
              "headboard.front"),
        Decal(_q((-1.12, -2.9405, 0.86), (-0.58, -2.9405, 0.86), (-0.58, -2.9405, 0.12), (-1.12, -2.9405, 0.12)), A.flap(), "flap-1.back"),
        Decal(_q((0.58, -2.9405, 0.86), (1.12, -2.9405, 0.86), (1.12, -2.9405, 0.12), (0.58, -2.9405, 0.12)), A.flap(), "flap1.back"),
    ]


STAGES = ("plain", "sun", "dog", "fish", "peacock", "full")


def reveal(img: np.ndarray, u: float, centre=None) -> np.ndarray:
    """A drawing partly made: a clockwise sweep round `centre` (fractions of the image), or a wipe left to right."""
    if u >= 1.0:
        return img
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    if centre is not None:
        cx, cy = centre[0] * w, centre[1] * h
        ang = ((np.arctan2(yy - cy, xx - cx) + np.pi / 2) % (2 * np.pi)) / (2 * np.pi)
        m = np.clip((u * 1.06 - ang) / 0.06, 0, 1)
    else:
        m = np.clip((u * 1.12 - xx / w) / 0.1, 0, 1)
    out = img.copy()
    out[..., 3] = (out[..., 3] * m).astype(np.uint8)
    return out


def paint_stage(stage: str, reveal_u: float = 1.0) -> tuple[bool, list[Decal]]:
    """The truck as the years go by: plain -> her crayon sun on the bumper (5) -> a chalk dog on the mudflap (8) ->
    a painted fish on the fuel tank (11) -> a peacock on the door (15) -> the whole truck in truck art (20).
    reveal_u: how much of this stage's newest drawing is done yet. Returns (painted colours?, decals)."""
    from vidosuper.graphics import kidart as K
    from vidosuper.graphics import truckart as A
    k = STAGES.index(stage)
    if stage == "full":
        dec = paint_job(True)
        if reveal_u < 1.0:
            dec = [d for d in dec if d.host != "body.back"]
            dec.append(Decal(_q((-1.2, -4.1505, 2.45), (1.2, -4.1505, 2.45), (1.2, -4.1505, 1.0), (-1.2, -4.1505, 1.0)),
                             _tailgate_cached(round(reveal_u, 2)), "body.back"))
        return True, dec
    out: list[Decal] = []
    if k >= 1:
        b = paint_job(False)[0]
        if k == 1 and reveal_u < 1.0:
            b = Decal(b.corners, reveal(b.img, reveal_u, centre=(0.88, 0.5)), b.host)
        out.append(b)
    if k >= 2:
        dog = _drawing_cached("dog")
        if k == 2:
            dog = reveal(dog, reveal_u)
        out.append(Decal(_q((-1.12, -2.9405, 0.8), (-0.58, -2.9405, 0.8), (-0.58, -2.9405, 0.26), (-1.12, -2.9405, 0.26)), dog,
                         "flap-1.back"))
    if k >= 3:
        fish = _drawing_cached("fish")
        if k == 3:
            fish = reveal(fish, reveal_u)
        out.append(Decal(_q((-1.081, 1.51, 0.9), (-1.081, 0.79, 0.9), (-1.081, 0.79, 0.42), (-1.081, 1.51, 0.42)), fish, "tank.side"))
    if k >= 4:
        pea = _drawing_cached("peacock")
        if k == 4:
            pea = reveal(pea, reveal_u)
        out.append(Decal(_q((-1.1205, 1.86, 1.74), (-1.1205, 0.96, 1.74), (-1.1205, 0.96, 1.04), (-1.1205, 1.86, 1.04)), pea, "cab.left"))
    return False, out
