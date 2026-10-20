"""Horn OK Please: the people (Papa, and Tara at four ages), built once in 3D and drawn from any side.

The same ink-and-paint rules as cast.py (one flat colour per piece, a shade crescent away from the light and a thin
rim toward it, a line only where a piece overlaps another, one thick outline around the figure), plus what people
need and mice don't:
- a human head: cranium, cheeks and chin, a nose that shows in profile, ears;
- hair as a shell over the skull cut at a designed hairline (fringe, parting), plus pigtails, braids or a bun;
- a moustache, brows, lashes, tears;
- clothes: a shirt with collar and pocket, a frock with a skirt, a kurta, dungarees;
- hands with four fingers and a thumb, each finger its own piece so it reads.

Units are metres; a character stands on y = 0 facing +z.

    img = render(PEOPLE["papa"], View(yaw=35, s=420, ox=260, oy=900), box=(520, 960))
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import cairo
import cv2
import numpy as np

from vidosuper.graphics.cast import (INK, Body, Piece, View, _path, ell2, ellipsoid, fill_poly, ik, paint_pieces, ribbon,
                                     rotm, stroke_line, tube, unit)
from vidosuper.graphics.toon import darker, hexc, mix, spline

# ---------- what a person can do ----------


@dataclass
class Face:
    open: float = 1.0  # eye opening (0 shut .. 1.35 wide)
    lid: float = 0.0  # upper lids lowered (0..1)
    lid_tilt: float = 0.0  # + lids slope down toward the nose (cross), - (sad)
    low: float = 0.0  # lower lids raised (a squint, a warm smile)
    gaze: tuple = (0.0, 0.0)  # pupils, -1..1 (screen x, up)
    brow: float = 0.0  # brows raised (-1 .. 1)
    brow_tilt: float = 0.0  # + inner ends down (cross, determined), - inner ends up (worried, sad)
    smile: float = 0.3
    mouth: float = 0.0  # open (0..1)
    wide: float = 0.0  # mouth stretched wide (a grin)
    shut: str = ""  # "happy" (arcs up), "soft" (relaxed, down), "squeeze" (> <), "wink" (the far eye)
    blush: float = 0.0
    tongue: float = 0.0  # tip of the tongue out of the screen-right corner (concentrating)
    teeth: float = 0.0  # upper teeth when the mouth is open
    tear: float = 0.0  # a tear on the near cheek (0..1 down the cheek)
    glisten: float = 0.0  # wet eyes
    tilt: float = 0.0  # head roll (degrees)
    nod: float = 0.0  # head tipped down (+) or back (-)
    turn: float = 0.0  # head turned toward screen right (degrees)


@dataclass
class Pose:
    arm_l: tuple = (9.0, 4.0, 14.0)  # (out from the side, forward swing, elbow bend) degrees; l = screen left in front view
    arm_r: tuple = (9.0, 4.0, 14.0)
    hand_in: tuple = (0.0, 0.0)  # palms turned back (degrees)
    hand_l: tuple | None = None  # a wrist target (local): the arm reaches it
    hand_r: tuple | None = None
    grip: tuple = (0.35, 0.35)  # fingers curled (0 open .. 1 fist)
    lean: float = 0.0  # body tipped forward (degrees)
    lean_side: float = 0.0
    feet: float = 1.0  # stance width
    knee: float = 4.0
    foot_l: tuple = (0.0, 0.0, 0.0)
    foot_r: tuple = (0.0, 0.0, 0.0)
    toe: tuple = (0.0, 0.0)
    rise: float = 0.0
    hold: str = ""  # what the screen-left hand (their right) holds: "crayon", "chalk", "brush", "pot"
    tip: tuple | None = None  # where the held tool's tip touches (local): the arm reaches so it lands there
    hold_r: str = ""  # what the other hand holds ("pot")


@dataclass
class Person:
    name: str
    skin: str
    hair: str
    top: str  # the main garment
    bottom: str  # trousers, salwar or leggings ("" = bare legs)
    slices: list  # torso (y, rx, rz, zoff)
    neck: tuple  # (y, z) where the head pivots
    R: float  # cranium radius
    head_up: float = 1.0  # cranium centre above the pivot (x R)
    skull: tuple = (0.93, 1.0, 1.0)
    jaw: tuple = (0.78, 0.6, 0.8)  # radii (x R)
    jaw_at: tuple = (0.0, -0.6, 0.16)  # centre from the cranium centre (x R)
    chin: tuple | None = None  # (y, z, r) an extra chin (a square jaw)
    nose: tuple = (0.16, 0.18, 0.2)  # radii (x R)
    nose_at: tuple = (0.0, -0.36, 0.95)
    eye: float = 1.0
    eye_az: float = 30.0
    eye_el: float = -8.0
    eye_round: float = 1.0  # height / width
    iris: str = "#4a2a1a"
    brow_w: float = 1.0
    lash: float = 0.0
    ear: float = 0.3  # (x R)
    ear_el: float = -12.0
    mouth_el: float = -34.0  # on the jaw
    mouth_w: float = 34.0  # half width (degrees on the jaw)
    moustache: float = 0.0
    hair_style: str = "short"
    grey: float = 0.0  # hair gone grey
    hip: tuple = (0.09, 0.88)  # leg spacing, hip height
    leg_r: tuple = (0.07, 0.05)
    foot: tuple = (0.12, 0.045, 0.032)  # half length, half width, half height
    shoe: str = "#6b4630"
    shoe_kind: str = "sandal"  # "sandal", "chappal", "sneaker"
    shoulder: tuple = (0.18, 1.42)
    arm: tuple = (0.29, 0.27)
    arm_r: tuple = (0.045, 0.037, 0.03)
    hand: float = 0.045
    sleeve: str = ""  # sleeve colour ("" = bare arms)
    sleeve_len: float = 0.45  # 0..1 of the upper arm; >= 1 to the wrist
    sleeve_kind: str = "rolled"  # "rolled", "puff", "plain"
    skirt: list | None = None  # a frock's or kurta's skirt (y, rx, rz, zoff)
    skirt_c: str = ""
    pant_hem: float = 0.08  # where the trousers end
    pose: Pose = field(default_factory=Pose)
    face: Face = field(default_factory=Face)
    extras: tuple = ()
    line_k: float = 1.0

    @property
    def hair_c(self) -> tuple:
        return mix(hexc(self.hair), hexc("#a7a6ad"), self.grey)

    @property
    def height(self) -> float:
        return self.neck[0] + self.R * (self.head_up + self.skull[1] * 1.12)


# ---------- surfaces ----------


def sph_v(az, el) -> np.ndarray:
    a, e = np.radians(az), np.radians(el)
    return np.stack([np.cos(e) * np.sin(a), np.sin(e), np.cos(e) * np.cos(a)], -1)


def on_ellipsoid(c, r, az, el, out: float = 0.0):
    """Points and normals on an axis-aligned ellipsoid at these azimuths / elevations (degrees)."""
    d = sph_v(az, el)
    r = np.asarray(r, float) + out
    p = np.asarray(c, float) + d * r
    n = d / r
    return p, n / np.linalg.norm(n, axis=-1, keepdims=True)


class Head:
    """The head's frame: every head point goes through `X` (roll, nod and turn about the neck, then the body)."""

    def __init__(self, L: Person, F: Face, B, lean):
        self.L, self.B = L, B
        R = L.R
        self.piv = np.array([0.0, L.neck[0], L.neck[1]])
        self.Rh = rotm(-F.nod, F.turn, -F.tilt)  # + nod tips the face down, as Face says
        self.Rw = lean @ self.Rh  # how the head is turned in the world
        self.c = self.piv + np.array([0.0, R * L.head_up, R * 0.02])
        self.cr = np.array(L.skull) * R
        self.jc = self.c + np.array(L.jaw_at) * R
        self.jr = np.array(L.jaw) * R
        self.nc = self.c + np.array(L.nose_at) * R
        self.nr = np.array(L.nose) * R

    def X(self, p) -> np.ndarray:
        p = np.asarray(p, float)
        return self.B(self.piv + (p - self.piv) @ self.Rh.T)

    def N(self, n) -> np.ndarray:
        n = np.asarray(n, float) @ self.Rw.T
        return n / np.linalg.norm(n, axis=-1, keepdims=True)

    def cran(self, az, el, out=0.0):
        p, n = on_ellipsoid(self.c, self.cr, az, el, out)
        return self.X(p), self.N(n)

    def jaw_pt(self, az, el, out=0.0):
        p, n = on_ellipsoid(self.jc, self.jr, az, el, out)
        return self.X(p), self.N(n)

    @property
    def centre(self) -> np.ndarray:
        return self.X(self.c)


# ---------- the build ----------


def _rel(view: View, p, ref) -> float:
    return view.depth(p) - view.depth(ref)


def build(L: Person, view: View, face: Face | None = None, pose: Pose | None = None) -> tuple[list[Piece], float]:
    """Every piece of the person for this camera. Returns (pieces, line width px)."""
    F = face or L.face
    P = pose or L.pose
    s = view.s
    lw = min(view.lw_cap, max(0.9, 0.0036 * s * L.line_k))
    skin = hexc(L.skin)
    top = hexc(L.top)
    pieces: list[Piece] = []
    body = Body(L.slices)
    lean = rotm(P.lean, 0, -P.lean_side)
    hip_y = L.hip[1]

    def B(p):
        p = np.asarray(p, float)
        return (p - [0, hip_y, 0]) @ lean.T + [0, hip_y + P.rise, 0]

    mid_y = (L.slices[0][0] + L.slices[-1][0]) / 2
    centre = B([0, mid_y, 0])
    torso_depth = view.depth(centre) + 0.01

    def lathe_polys(bd: Body, out=0.0):
        ys = bd.S[:, 0]
        grid = np.linspace(ys[0], ys[-1], max(2, int((ys[-1] - ys[0]) / 0.008) + 1))
        rings = [view(B(bd.ring(y, out))) for y in grid]
        from vidosuper.graphics.cast import hull
        return [hull(np.vstack([a, b])) for a, b in zip(rings, rings[1:])]

    wid = max(r[1] for r in L.slices) * 2 * s

    # --- torso ---
    pieces.append(Piece(lathe_polys(body), top, torso_depth, size=wid * 0.5,
                        detail=_garment_detail(L, view, B, body, lw, "top")))

    # --- skirt (frock, kurta) ---
    if L.skirt:
        sk = Body(L.skirt)
        sc = hexc(L.skirt_c or L.top)
        pieces.append(Piece(lathe_polys(sk), sc, torso_depth + 0.004, size=max(r[1] for r in L.skirt) * s,
                            detail=_garment_detail(L, view, B, sk, lw, "skirt")))

    # --- legs and feet ---
    J: dict = {}
    for side, off, toe in ((-1, P.foot_l, P.toe[0]), (1, P.foot_r, P.toe[1])):
        hx = side * L.hip[0] * P.feet
        Hp = B([hx, hip_y + 0.03, 0.0])
        A0 = np.array([side * L.hip[0] * P.feet * 1.1, L.foot[2] * 2.2, -0.01])
        A = A0 + np.asarray(off, float)
        rest = float(np.linalg.norm(np.array([hx, hip_y + 0.03, 0.0]) - A0)) * 1.02
        if np.linalg.norm(A - Hp) > rest * 0.995:
            K = (Hp + A) / 2 + [0, 0, 0.012 * P.knee / 4]
        else:
            K = ik(Hp, A, rest * 0.5, rest * 0.5, [0, 0.15, 1.0])
        r0, r1 = L.leg_r
        leg_len = float(np.linalg.norm(Hp - K) + np.linalg.norm(K - A))

        def part_dep(p):
            """Behind the torso, unless this part of the leg comes out in front of it (a crouch, a kneel)."""
            rel = _rel(view, p, B([0.0, float(np.asarray(p)[1]), 0.0]))
            base = torso_depth + 0.012 if rel > 0.16 * leg_len else torso_depth - 0.2
            return base + 0.25 * view.depth(p)

        u1, u2 = unit(K - Hp), unit(A - K)
        bent = float(np.degrees(np.arccos(np.clip(np.dot(u1, u2), -1, 1)))) > 28
        col_leg = hexc(L.bottom) if L.bottom else skin
        end = (A + (K - A) * 0.04) if L.bottom else A
        rad = [r0, r0 * 0.86, r1 * 1.05] if L.bottom else [r0, r0 * 0.8, r1]
        if not bent:  # one smooth leg
            legs = flat_end_tube(view, [Hp, K, end], rad) if L.bottom else tube(view, [Hp, K, end], rad)
            dl = part_dep((Hp + end) / 2)
            pieces.append(Piece(legs, col_leg, dl, size=r0 * 2 * s, rim=0.6,
                                detail=_trouser_detail(view, Hp, K, end, r1, col_leg, lw) if L.bottom else None))
        else:  # thigh and shin apart: each in its own depth, so a folded leg hides what is behind it
            thigh = tube(view, [Hp, K + u1 * rad[1] * 0.3], [rad[0], rad[1]], n=3)
            shin = (flat_end_tube(view, [K, end], [rad[1], rad[2]]) if L.bottom else tube(view, [K, end], [rad[1], rad[2]], n=3))
            dl = part_dep((K + end) / 2)
            pieces.append(Piece(shin, col_leg, dl, size=r0 * 2 * s, rim=0.6))
            pieces.append(Piece(thigh, col_leg, part_dep((Hp + K) / 2), size=r0 * 2 * s, rim=0.6))
        J["foot_l" if side < 0 else "foot_r"] = A
        fd = part_dep(A + np.array([0, 0, L.foot[0] * 0.5]))
        pieces += _foot(L, view, A, side, toe, fd if bent else max(fd, dl + 0.005), lw)

    # --- arms and hands ---
    for side, a, tin, tgt, g in ((-1, P.arm_l, P.hand_in[0], P.hand_l, P.grip[0]),
                                 (1, P.arm_r, P.hand_in[1], P.hand_r, P.grip[1])):
        S, E, W, d2 = _arm_joints(L, side, a, tin)
        S, E, W = B(S), B(E), B(W)
        d2 = unit(lean @ d2)
        tool = P.hold if (side < 0 and P.hold in TOOLS) else ""
        if tool and P.tip is not None:  # aim the wrist so the tool's tip touches the point
            Tp = np.asarray(P.tip, float)
            Wg = Tp - unit(Tp - S) * (L.hand * 2.0 + TOOLS[tool][0] * 0.35)
            for _ in range(4):
                Eg = ik(S, Wg, L.arm[0], L.arm[1], [side * 0.6, -0.3, -1.0])
                tip_now = _tool_geom(L, _hand_frame(L, Wg, unit(Wg - Eg), side, tin), tool)[0]
                Wg = Wg + (Tp - tip_now)
            tgt = tuple(Wg)
        if tgt is not None:
            W = np.asarray(tgt, float)
            E = ik(S, W, L.arm[0], L.arm[1], [side * 0.6, -0.3, -1.0])
            d2 = unit(W - E)
        ref = B([0.0, L.shoulder[1], 0.0])
        ru = _rel(view, (S + E) / 2, B([0.0, (S[1] + E[1]) / 2, 0.0]))
        rf = _rel(view, (E + W) / 2, B([0.0, (E[1] + W[1]) / 2, 0.0]))
        dep = torso_depth + 0.5 * (ru + rf) + (0.02 if max(ru, rf) > 0.03 else -0.012)
        if abs(ru - rf) < 0.04:  # one smooth limb
            arm = tube(view, [S, E, W], list(L.arm_r))
            pieces.append(Piece(arm, skin, dep, size=L.arm_r[1] * 2 * s, cast=0.16, rim=0.6))
            dep_u = dep_f = dep
        else:  # the forearm crosses in front of (or behind) the body: each part finds its own place
            dep_u = torso_depth + ru + (0.02 if ru > 0.03 else -0.012)
            dep_f = torso_depth + rf + (0.02 if rf > 0.03 else -0.012)
            up = tube(view, [S, E + (E - S) * 0.02], [L.arm_r[0], L.arm_r[1]], n=3)
            fo = tube(view, [E, W], [L.arm_r[1], L.arm_r[2]], n=3)
            pieces.append(Piece(up, skin, dep_u, size=L.arm_r[1] * 2 * s, cast=0.16, rim=0.6))
            pieces.append(Piece(fo, skin, dep_f, size=L.arm_r[1] * 2 * s, cast=0.16, rim=0.6))
        if L.sleeve:
            pieces += _sleeve(L, view, S, E, W, side, dep_u, lw)
        dep = dep_f
        hand_pieces, grip_pt, frame = _hand(L, view, W, d2, side, tin, g, dep + 0.004, lw, skin, pencil=bool(tool))
        pieces += hand_pieces
        J["hand_l" if side < 0 else "hand_r"] = grip_pt
        J["frame_l" if side < 0 else "frame_r"] = frame
        for ex in L.extras:
            if ex[0] == "kada" and ex[1] == side:
                pieces.append(_band(view, W - d2 * L.hand * 0.32, d2, L.arm_r[2] * 1.16, L.hand * 0.2, hexc(ex[2]), dep + 0.003, lw,
                                    shine=True))
            if ex[0] == "wristband" and ex[1] == side:
                pieces += _wrist_ribbon(view, W - d2 * L.hand * 0.4, d2, L.arm_r[2] * 1.15, hexc(ex[2]), dep + 0.003, lw, side)
        if side < 0 and P.hold:
            pieces += _held(P.hold, view, grip_pt, frame, dep + 0.004, lw, L)
        if side > 0 and P.hold_r:
            pieces += _held(P.hold_r, view, grip_pt, frame, dep + 0.004, lw, L)

    # --- neck ---
    nk0 = B([0.0, L.slices[-1][0] - 0.012, L.neck[1] * 0.5])
    nk1 = B([0.0, L.neck[0] + L.R * 0.15, L.neck[1]])
    nr = L.slices[-1][1] * 0.95
    pieces.append(Piece(tube(view, [nk0, nk1], [nr, nr * 0.95], n=3), skin, torso_depth + 0.012, size=nr * 2 * s, shade=0.7,
                        rim=0.3, detail=lambda ctx: None))

    # --- head, hair, face ---
    H = Head(L, F, B, lean)
    hd = view.depth(H.centre) + 0.5
    pieces += _head(L, view, F, H, hd, lw)
    pieces += _hair(L, view, F, H, hd, lw, torso_depth, B)

    # --- what they wear on top ---
    for ex in L.extras:
        fn = EXTRA.get(ex[0])
        if fn:
            pieces += fn(L, view, F, B, body, ex, lw, torso_depth, H, hd)

    J["head"] = H.centre
    J["top"] = H.X(H.c + [0, H.cr[1] * 1.1, 0])
    J["chest"] = B([0.0, L.shoulder[1] - 0.1, body.at(L.shoulder[1] - 0.1)[1]])
    build.joints = J
    return pieces, lw


# ---------- clothes ----------


def cyl(view: View, a, b, r: float, n: int = 28) -> list:
    """A cylinder from a to b as the camera sees it: its two end circles and the sides between (one convex shape)."""
    from vidosuper.graphics.cast import hull
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = unit(b - a)
    x = unit(np.cross(d, [0, 0, 1.0])) if abs(d[2]) < 0.95 else unit(np.cross(d, [1.0, 0, 0]))
    y = np.cross(d, x)
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ring = np.cos(t)[:, None] * x + np.sin(t)[:, None] * y
    return [hull(view(np.vstack([a + ring * r, b + ring * r])))]


def flat_end_tube(view: View, pts, radii) -> list:
    """A tapered limb whose far end is cut flat (a sleeve, a trouser hem) instead of rounded."""
    pts = [np.asarray(p, float) for p in pts]
    r_end = radii[-1]
    d = unit(pts[-1] - pts[-2])
    cut = pts[-1] - d * r_end
    return tube(view, pts[:-1] + [cut], list(radii[:-1]) + [r_end]) + cyl(view, cut, pts[-1], r_end)


def _garment_detail(L, view, B, body: Body, lw, which):
    def f(ctx):
        for ex in L.extras:
            k = ex[0]
            if k == "placket" and which == "top":
                _, col, y0, y1, buttons, bc = ex
                _placket(ctx, view, B, body, col, y0, y1, buttons, bc, lw)
            elif k == "pocket" and which == "top":
                _, col, az, y, w, h, pen = ex
                _pocket(ctx, view, B, body, hexc(col), az, y, w, h, pen, lw)
            elif k == "print" and which in ex[4]:
                _, col, step, size, _w = ex
                _print(ctx, view, B, body, hexc(col), step, size, lw)
            elif k == "hemline" and which == ex[2]:
                _, col, _w, y = ex
                arc = body.front_arc(view, y)
                stroke_line(ctx, view(B(_lift_arc(body, y, view))), hexc(col), lw * 2.4)
            elif k == "bib" and which == "top":
                _, col, y0, y1, az = ex
                _bib(ctx, view, B, body, hexc(col), y0, y1, az, lw)
            elif k == "splat" and which in ex[2]:
                _, cols, _w, seed = ex
                _splats(ctx, view, B, body, cols, seed, lw)
            elif k == "neckline" and which == "top":
                pass
    return f


def _lift_arc(body: Body, y: float, view: View, n: int = 40) -> np.ndarray:
    cd = view.M[2]
    face = math.degrees(math.atan2(cd[0], cd[2]))
    rx, rz, zo = body.at(y)
    t = np.radians(np.linspace(face - 90, face + 90, n))
    return np.stack([rx * np.sin(t), np.full(n, y), zo + rz * np.cos(t)], 1)


def _visible_pts(view, B, body, ys, az, out=0.003):
    pts, vis = [], []
    for y in ys:
        p, n = body.point(y, az, out)
        pts.append(B(p))
        vis.append(view.facing(n) > 0.05)
    return np.array(pts), np.array(vis)


def _placket(ctx, view, B, body, col, y0, y1, buttons, bc, lw):
    pts, vis = _visible_pts(view, B, body, np.linspace(y0, y1, 24), 0.0)
    if vis.sum() > 1:
        stroke_line(ctx, view(pts)[vis], darker(hexc(col), 0.75), lw * 1.1)
    s = view.s
    for y in buttons:
        p, n = body.point(y, 0.0, 0.004)
        if view.facing(n) > 0.15:
            c = view(B(p))[0] + [0.012 * s * view.facing(n), 0]
            k = view.facing(n) ** 0.6
            fill_poly(ctx, ell2(c, 0.009 * s * k, 0.009 * s), hexc(bc))
            stroke_line(ctx, np.vstack([ell2(c, 0.009 * s * k, 0.009 * s), ell2(c, 0.009 * s * k, 0.009 * s)[:1]]),
                        darker(hexc(bc), 0.6), lw * 0.6)


def _patch(view, B, body, az0, az1, y0, y1, out=0.004, n=10):
    """A patch on the body surface (azimuth range x height range), as a screen polygon, if it faces us."""
    ring = []
    for az in np.linspace(az0, az1, n):
        ring.append(body.point(y1, az, out))
    for y in np.linspace(y1, y0, n):
        ring.append(body.point(y, az1, out))
    for az in np.linspace(az1, az0, n):
        ring.append(body.point(y0, az, out))
    for y in np.linspace(y0, y1, n):
        ring.append(body.point(y, az0, out))
    fac = np.mean([view.facing(nn) for _, nn in ring])
    pts = view(B(np.array([p for p, _ in ring])))
    return pts, fac


def _pocket(ctx, view, B, body, col, az, y, w, h, pen, lw):
    pts, fac = _patch(view, B, body, az - w, az + w, y - h, y)
    if fac < 0.15:
        return
    fill_poly(ctx, pts, darker(col, 0.93, 0.03))
    stroke_line(ctx, np.vstack([pts, pts[:1]]), darker(col, 0.62), lw * 1.0)
    if pen:  # a blue pen clipped in the pocket
        p0, _ = body.point(y + 0.035, az + w * 0.45, 0.008)
        p1, _ = body.point(y - h * 0.15, az + w * 0.45, 0.008)
        a, b = view(B(p0))[0], view(B(p1))[0]
        stroke_line(ctx, np.array([a, b]), INK, lw * 2.6 + 0.012 * view.s * 0.0)
        stroke_line(ctx, np.array([a, b]), hexc("#2f5fa8"), max(1.0, 0.009 * view.s))
        stroke_line(ctx, np.array([a + (b - a) * 0.08, a + (b - a) * 0.08 + [0, 0]]), (1, 1, 1), max(1.0, 0.006 * view.s))


def _print(ctx, view, B, body, col, step, size, lw):
    """Little five-petal flowers scattered over the fabric."""
    s = view.s
    ys = body.S[:, 0]
    rng = np.random.default_rng(7)
    for y in np.arange(ys[0] + step * 0.5, ys[-1] - step * 0.2, step):
        for az in np.arange(-180, 180, 32):
            az2 = az + (16 if int(round(y / step)) % 2 else 0) + rng.uniform(-4, 4)
            p, n = body.point(y, az2, 0.002)
            f = view.facing(n)
            if f < 0.2:
                continue
            c = view(B(p))[0]
            r = size * s
            k = f ** 0.7
            for i in range(5):
                a = i * 2 * math.pi / 5 + y * 3
                fill_poly(ctx, ell2(c + [math.cos(a) * r * 0.9 * k, math.sin(a) * r * 0.9], r * 0.62 * k, r * 0.62), col)
            fill_poly(ctx, ell2(c, r * 0.42 * k, r * 0.42), hexc("#e2553f"))


def _bib(ctx, view, B, body, col, y0, y1, az, lw):
    pts, fac = _patch(view, B, body, -az, az, y0, y1, out=0.003)
    if fac < 0.05:
        return
    fill_poly(ctx, pts, col)
    stroke_line(ctx, np.vstack([pts, pts[:1]]), darker(col, 0.65), lw * 1.0)
    # the stitched edge and the chest pocket
    pts2, fac2 = _patch(view, B, body, -az * 0.55, az * 0.55, y1 - (y1 - y0) * 0.55, y1 - (y1 - y0) * 0.12, out=0.004)
    if fac2 > 0.1:
        stroke_line(ctx, np.vstack([pts2, pts2[:1]]), mix(col, (1, 0.85, 0.5), 0.5), lw * 0.8)
    for sd in (-1, 1):  # buttons where the straps meet
        p, n = body.point(y1 - 0.012, sd * az * 0.78, 0.006)
        if view.facing(n) > 0.1:
            c = view(B(p))[0]
            r = 0.011 * view.s
            fill_poly(ctx, ell2(c, r, r), hexc("#d8d2c4"))
            stroke_line(ctx, np.vstack([ell2(c, r, r), ell2(c, r, r)[:1]]), darker(col, 0.5), lw * 0.7)


def _splats(ctx, view, B, body, cols, seed, lw):
    rng = np.random.default_rng(seed)
    s = view.s
    ys = body.S[:, 0]
    for _ in range(26):
        y = rng.uniform(ys[0], ys[-1])
        az = rng.uniform(-70, 70)
        p, n = body.point(y, az, 0.003)
        f = view.facing(n)
        if f < 0.2:
            continue
        c = view(B(p))[0]
        col = hexc(cols[rng.integers(len(cols))])
        r = rng.uniform(0.004, 0.011) * s
        blob = ell2(c, r * f ** 0.6, r * rng.uniform(0.7, 1.1), rng.uniform(0, 3))
        fill_poly(ctx, blob, col)
        for _k in range(rng.integers(0, 3)):
            d = rng.uniform(1.4, 2.4) * r
            a = rng.uniform(0, 2 * math.pi)
            fill_poly(ctx, ell2(c + [math.cos(a) * d, math.sin(a) * d], r * 0.3, r * 0.3), col)


def _trouser_detail(view, Hp, K, hem, r1, col, lw):
    def f(ctx):
        # a crease down the front and the turned-up hem
        a = view(np.array([Hp + [0, -0.05, 0.03], K + [0, 0, 0.03], hem + [0, 0.02, 0.03]]))
        stroke_line(ctx, spline(a, False, 6), darker(col, 0.82), lw * 0.9)
    return f


def _sleeve(L, view, S, E, W, side, dep, lw):
    s = view.s
    col = hexc(L.sleeve)
    pieces = []
    if L.sleeve_len >= 0.99:  # to the wrist
        sl = flat_end_tube(view, [S + [0, 0.01, 0], E, W - (W - E) * 0.05], [L.arm_r[0] * 1.2, L.arm_r[1] * 1.25, L.arm_r[2] * 1.35])
        pieces.append(Piece(sl, col, dep + 0.001, size=L.arm_r[0] * 2.4 * s, cast=0.15))
        return pieces
    end = S + (E - S) * L.sleeve_len
    d = unit(E - S)
    if L.sleeve_kind == "puff":
        c = S + (E - S) * 0.3
        pieces.append(Piece([ellipsoid(view, c, (L.arm_r[0] * 1.85, L.arm[0] * 0.34, L.arm_r[0] * 1.85),
                                       _frame_from(d))], col, dep + 0.002, size=L.arm_r[0] * 3.5 * s, cast=0.15,
                            detail=_puff_detail(view, c, d, L, col, lw)))
        return pieces
    top = S + d * L.arm_r[0] * 0.15
    r_s = L.arm_r[0] * 1.08
    sl = flat_end_tube(view, [top, end], [r_s, r_s * 1.04])
    pieces.append(Piece(sl, col, dep + 0.001, size=L.arm_r[0] * 2.4 * s, cast=0.15))
    if L.sleeve_kind == "rolled":  # the turned-up cuff
        cuff = cyl(view, end - d * L.arm[0] * 0.085, end + d * L.arm[0] * 0.01, r_s * 1.1)
        pieces.append(Piece(cuff, darker(col, 0.92, 0.03), dep + 0.0015, size=L.arm_r[0] * 2.4 * s))
    return pieces


def _frame_from(d) -> np.ndarray:
    """A rotation whose y axis is d (for ellipsoids along a limb)."""
    d = unit(d)
    x = unit(np.cross(d, [0, 0, 1.0])) if abs(d[2]) < 0.95 else np.array([1.0, 0, 0])
    z = np.cross(x, d)
    return np.stack([x, d, z], 1)


def _puff_detail(view, c, d, L, col, lw):
    def f(ctx):
        # gathers at the top of a puff sleeve and its elastic band
        x = unit(np.cross(d, [0, 0, 1.0]))
        z = np.cross(x, d)
        r = L.arm_r[0] * 1.9
        band = [c + d * L.arm[0] * 0.3 + (x * math.cos(t) + z * math.sin(t)) * r * 0.62 for t in np.linspace(0, 2 * np.pi, 40)]
        P2 = view(np.array(band))
        stroke_line(ctx, P2[P2[:, 1] >= np.median(P2[:, 1]) - 1e9], darker(col, 0.78), lw * 1.2)
    return f


def _band(view, c, d, r, h, col, dep, lw, shine=False) -> Piece:
    """A bangle or cuff: a short wide tube around a limb."""
    poly = cyl(view, c - d * h / 2, c + d * h / 2, r)

    def det(ctx):
        if shine:
            a, b = view(c - d * h * 0.1)[0], view(c + d * h * 0.1)[0]
            m = (a + b) / 2
            stroke_line(ctx, np.array([m + [-r * view.s * 0.6, -r * view.s * 0.2], m + [-r * view.s * 0.1, -r * view.s * 0.35]]),
                        (1, 1, 1), lw * 1.4, lw * 0.5, 0.85)
    return Piece(poly, col, dep, size=r * 2 * view.s, detail=det, line=darker(col, 0.5))


def _wrist_ribbon(view, c, d, r, col, dep, lw, side):
    pieces = [_band(view, c, d, r, 0.012, col, dep, lw)]
    x = unit(np.cross(d, [0, 0, 1.0])) if abs(d[2]) < 0.9 else np.array([1.0, 0, 0])
    knot = c + x * r * 0.9 * (-side)
    tails = []
    for k in (-1, 1):
        tails.append(ribbon(view, [knot, knot + d * 0.008 * k + x * 0.006 * (-side) + [0, -0.014, 0.0],
                                   knot + d * 0.012 * k + x * 0.008 * (-side) + [0, -0.026, 0.004]],
                            [0.008, 0.007, 0.008], [unit(d)] * 3))
    pieces.append(Piece(tails, col, dep + 0.001, size=0.02 * view.s, line=darker(col, 0.55)))
    return pieces


# ---------- limbs ----------


def _arm_joints(L: Person, side: int, a: tuple, turn_in: float):
    out, fwd, bend = a
    S = np.array([side * L.shoulder[0], L.shoulder[1], 0.0])
    o, f = math.radians(out), math.radians(fwd)
    d1 = unit([side * math.sin(o), -math.cos(o) * math.cos(f), math.cos(o) * math.sin(f)])
    E = S + d1 * L.arm[0]
    ti = math.radians(turn_in)
    tgt = unit(np.array([-side * math.sin(ti), -0.15 * math.sin(ti), math.cos(ti) + 0.05]))
    perp = unit(tgt - d1 * np.dot(tgt, d1))
    b = math.radians(bend)
    d2 = unit(d1 * math.cos(b) + perp * math.sin(b))
    W = E + d2 * L.arm[1]
    return S, E, W, d2


def _rot_about(v, axis, deg) -> np.ndarray:
    axis = unit(axis)
    a = math.radians(deg)
    return v * math.cos(a) + np.cross(axis, v) * math.sin(a) + axis * np.dot(axis, v) * (1 - math.cos(a))


TOOLS = {  # length, radius at the grip, colour, tip colour
    "crayon": (0.085, 0.0075, "#f07b1e", "#e0601a"),
    "chalk": (0.07, 0.0065, "#f4f1e8", "#ffffff"),
    "brush": (0.2, 0.0045, "#c0472f", "#2a9d8f"),
}


def _hand_frame(L: Person, W, d2, side, turn_in):
    """The hand's frame at wrist W along forearm d2: (palm centre, along, palm normal, across, thumb side)."""
    h = L.hand
    d2 = unit(d2)
    ref = np.array([-side * 1.0, 0, 0])
    pn = unit(ref - d2 * np.dot(ref, d2))  # the palm faces the body
    pn = _rot_about(pn, d2, -side * turn_in)
    ac = unit(np.cross(d2, pn))
    return W + d2 * h * 0.9, d2, pn, ac, -side * ac


def _tool_geom(L: Person, frame, kind):
    """A pencil grip: the tool pinched between thumb and index, lying back over the web of the thumb, its tip past the
    fingertips. Returns (tip, back end, axis from back to tip)."""
    hc, d2, pn, ac, ts = frame
    h = L.hand
    length = TOOLS[kind][0]
    axis = unit(d2 * 0.62 + pn * 0.62 - ts * 0.48)  # tip ahead and down to the surface; the back end over the thumb's web
    pinch = hc + d2 * h * 1.0 + pn * h * 0.4 + ts * h * 0.42
    return pinch + axis * length * 0.42, pinch - axis * length * 0.58, axis


def _hand(L: Person, view: View, W, d2, side, turn_in, g, dep, lw, skin, pencil=False):
    """Palm, four fingers and a thumb (each finger its own piece). pencil: the grip that holds a crayon or brush.
    Returns (pieces, grip point, frame)."""
    h = L.hand
    s = view.s
    hc, d2, pn, ac, thumb_side = _hand_frame(L, W, d2, side, turn_in)
    Rm = np.stack([ac, d2, pn], 1)
    pieces = [Piece([ellipsoid(view, hc, (h * 0.78, h * 0.95, h * 0.36), Rm)], skin, dep, size=h * 1.8 * s, cast=0.12, rim=0.5)]
    lengths = (0.74, 0.8, 0.74, 0.6)
    curls = (0.55, 0.62, 1.0, 1.05) if pencil else (g, g, g, g)
    for i, frac in enumerate((-1.5, -0.5, 0.5, 1.5)):  # index (thumb side) to little finger
        off = thumb_side * (-frac) * h * 0.38
        base = hc + d2 * h * 0.78 + off + pn * h * 0.02
        L1 = h * lengths[i] * 0.55
        L2 = h * lengths[i] * 0.5
        curl = curls[i] * (48 + 6 * i) if pencil else curls[i] * (42 + 6 * i)
        dir1 = unit(d2 * math.cos(math.radians(curl)) + pn * math.sin(math.radians(curl)))
        dir2 = unit(d2 * math.cos(math.radians(curl * 1.7)) + pn * math.sin(math.radians(curl * 1.7)))
        dir1 = unit(dir1 + thumb_side * (-frac) * 0.06)
        k1 = base + dir1 * L1
        tip = k1 + dir2 * L2
        r = h * (0.2 if i < 3 else 0.17)
        fp = tube(view, [base, k1, tip], [r, r * 0.95, r * 0.86], n=3)
        front = view.depth(tip) > view.depth(hc)
        if pencil and i < 2:  # index and middle wrap over the tool
            fd = dep + 0.0012 + 0.0004 * i
        else:
            fd = dep + 0.0002 * (1 + i) + (0.0003 if front else -0.002)
        pieces.append(Piece(fp, skin, fd, size=r * 2 * s, shade=0.6, rim=0.3, line=darker(skin, 0.55, 0.1)))
    tb = hc + thumb_side * h * 0.55 - d2 * h * 0.15 + pn * h * 0.15
    if pencil:  # the thumb comes round to pinch the tool against the side of the index finger
        tdir = unit(d2 * 0.8 + thumb_side * 0.25 + pn * 0.45)
        t1 = tb + tdir * h * 0.48
        t2 = t1 + unit(d2 * 0.75 + pn * 0.35 - thumb_side * 0.15) * h * 0.36
        tdep = dep + 0.0016
    else:
        tdir = unit(d2 * 0.78 + thumb_side * 0.3 + pn * (0.3 + 0.45 * g))
        t1 = tb + tdir * h * 0.42
        t2 = t1 + unit(tdir + pn * 0.6 * g + d2 * 0.2) * h * 0.36
        tdep = dep + (0.003 if view.depth(t1) > view.depth(hc) else -0.003)
    pieces.append(Piece(tube(view, [tb, t1, t2], [h * 0.25, h * 0.22, h * 0.19], n=3), skin, tdep, size=h * 0.5 * s, shade=0.6,
                        rim=0.3, line=darker(skin, 0.55, 0.1)))
    grip_pt = hc + d2 * h * 0.55 + pn * h * 0.55
    return pieces, grip_pt, (hc, d2, pn, ac, thumb_side)


def _foot(L: Person, view: View, A, side, toe, dep, lw) -> list[Piece]:
    s = view.s
    fl, fw, fh = L.foot
    fR = rotm(toe, side * 9, 0)
    fc = A + fR @ np.array([0, -fh * 0.55, fl * 0.55])
    skin = hexc(L.skin)
    shoe = hexc(L.shoe)
    pieces = []
    if L.shoe_kind == "sneaker":
        body = [ellipsoid(view, fc + fR @ np.array([0, fh * 0.15, 0]), (fw * 1.08, fh * 1.25, fl * 1.05), fR)]
        pieces.append(Piece(body, shoe, dep, size=fh * 3 * s, detail=_sneaker_detail(view, fc, fR, L)))
        sole = [ellipsoid(view, fc + fR @ np.array([0, -fh * 0.75, 0.0]), (fw * 1.12, fh * 0.42, fl * 1.08), fR)]
        pieces.append(Piece(sole, hexc("#f1ece2"), dep + 0.0005, size=fh * 2 * s, shade=0.4))
        return pieces
    # a sandal: the sole under a bare foot, a strap over it
    sole = [ellipsoid(view, fc + fR @ np.array([0, -fh * 0.95, fl * 0.02]), (fw * 1.18, fh * 0.32, fl * 1.12), fR)]
    pieces.append(Piece(sole, shoe, dep - 0.001, size=fh * 2 * s, shade=0.5))
    foot = [ellipsoid(view, fc, (fw * 0.95, fh, fl * 0.98), fR)]
    for k in range(4):  # toes peep out at the front
        tc = fc + fR @ np.array([(k - 1.2) * fw * 0.42 * (-side), -fh * 0.25, fl * (0.86 - 0.05 * abs(k - 1.2))])
        foot.append(ellipsoid(view, tc, (fw * 0.24, fh * 0.55, fw * 0.3), fR))
    pieces.append(Piece(foot, skin, dep, size=fh * 3 * s, rim=0.5))
    strap_c = shoe if L.shoe_kind == "sandal" else darker(shoe, 0.8)
    if L.shoe_kind == "chappal":  # a toe post: a V over the foot
        a = fc + fR @ np.array([0, fh * 0.2, fl * 0.62])
        pts = [fc + fR @ np.array([-fw * 0.95, -fh * 0.3, fl * 0.05]), a, fc + fR @ np.array([fw * 0.95, -fh * 0.3, fl * 0.05])]
        strap = tube(view, pts, [fh * 0.32, fh * 0.3, fh * 0.32], n=4)
    else:
        pts = [fc + fR @ np.array([-fw * 0.98, -fh * 0.2, fl * 0.25]), fc + fR @ np.array([0, fh * 0.75, fl * 0.3]),
               fc + fR @ np.array([fw * 0.98, -fh * 0.2, fl * 0.25])]
        strap = tube(view, pts, [fh * 0.36, fh * 0.34, fh * 0.36], n=4)
    pieces.append(Piece(strap, strap_c, dep + 0.002, size=fh * s, shade=0.4))
    return pieces


def _sneaker_detail(view, fc, fR, L):
    def f(ctx):
        fl, fw, fh = L.foot
        lace = [fc + fR @ np.array([x, fh * 0.95, fl * z]) for x, z in ((-fw * 0.4, 0.1), (fw * 0.4, 0.25), (-fw * 0.4, 0.4),
                                                                       (fw * 0.4, 0.55))]
        P = view(np.array(lace))
        stroke_line(ctx, P, (0.97, 0.95, 0.9), max(1.0, 0.006 * view.s))
        for i, col in enumerate(("#e2553f", "#2a9d8f", "#f2b632")):  # paint drips
            c = view(fc + fR @ np.array([fw * (0.6 - 0.5 * i), fh * 0.5, fl * (-0.3 + 0.35 * i)]))[0]
            fill_poly(ctx, ell2(c, 0.006 * view.s, 0.005 * view.s), hexc(col))
    return f


# ---------- held things ----------


def _held(kind, view, g, frame, dep, lw, L) -> list[Piece]:
    """The tool in the hand: crayon, chalk or brush in a pencil grip (drawn between the fingers that wrap it and
    the ones behind), or a paint pot held in the palm."""
    s = view.s
    if kind in TOOLS:
        length, r, col, tip_c = TOOLS[kind]
        tip, back, axis = _tool_geom(L, frame, kind)
        col, tip_c = hexc(col), hexc(tip_c)
        pieces = []
        if kind == "brush":  # wooden handle, a metal ferrule, the wet tip
            f0 = tip - axis * length * 0.28
            f1 = tip - axis * length * 0.18
            pieces.append(Piece(tube(view, [back, f0], [r * 0.8, r * 1.0], n=2), col, dep + 0.0008, size=r * 2 * s))
            pieces.append(Piece(tube(view, [f0, f1], [r * 1.1, r * 1.1], n=2), hexc("#c9ccd2"), dep + 0.0009, size=r * 2 * s))
            pieces.append(Piece(tube(view, [f1, tip], [r * 1.15, r * 0.25], n=3), tip_c, dep + 0.001, size=r * 2 * s))
        else:  # a stick with a worn point; crayons wear a paper wrapper
            body_end = tip - axis * length * 0.18
            pieces.append(Piece(tube(view, [back, body_end], [r, r], n=2) + tube(view, [body_end, tip], [r, r * 0.45], n=2), col,
                                dep + 0.0008, size=r * 2 * s, line=darker(col, 0.55)))
            if kind == "crayon":
                w0, w1 = back + axis * length * 0.12, back + axis * length * 0.62
                pieces.append(Piece(cyl(view, w0, w1, r * 1.06), hexc("#f6e7c8"), dep + 0.00085, size=r * 2 * s, line=darker(col, 0.55)))
        return pieces
    if kind == "pot":  # a small paint tin in the palm
        hc, d2, pn, ac, ts = frame
        c = hc + pn * 0.035 + d2 * 0.01
        return [Piece(cyl(view, c - np.array([0, 0.035, 0]), c + np.array([0, 0.035, 0]), 0.04), hexc("#d9dde3"), dep + 0.002,
                      size=0.08 * s, line=darker(hexc("#d9dde3"), 0.5))]
    return []


# ---------- the head ----------


def _head(L: Person, view: View, F: Face, H: Head, hd: float, lw: float) -> list[Piece]:
    s = view.s
    R = L.R
    skin = hexc(L.skin)
    polys = [ellipsoid(view, H.centre, H.cr, H.Rw), ellipsoid(view, H.X(H.jc), H.jr, H.Rw)]
    if L.chin:
        cy, cz, cr = L.chin
        polys.append(ellipsoid(view, H.X(H.c + np.array([0, cy, cz]) * R), (cr * R * 1.25, cr * R, cr * R), H.Rw))
    nose_poly = ellipsoid(view, H.X(H.nc), H.nr, H.Rw @ rotm(14, 0, 0))
    polys.append(nose_poly)
    pieces = []

    # ears
    for side in (-1, 1):
        p, n = H.cran(side * 93, L.ear_el, -R * 0.06)
        rel = view.depth(p) - view.depth(H.centre)
        dep = hd + rel + (0.002 if rel > 0 else -0.002)
        size = L.ear * R
        nrm = H.N(unit([side * 1.0, 0.05, 0.25]))
        up = H.N(np.array([0, 1.0, -0.1]))
        v = unit(up - nrm * np.dot(up, nrm))
        u = np.cross(v, nrm)
        shape = np.array([[0.05, 1.0], [0.6, 0.78], [0.72, 0.2], [0.5, -0.55], [0.12, -0.95], [-0.32, -0.62], [-0.42, 0.2],
                          [-0.3, 0.8]])
        sh = spline(shape, True, 6) * size
        c = p + nrm * size * 0.25 + u * size * 0.15 * side * 0
        ear3 = c + sh[:, :1] * u * (-side) + sh[:, 1:] * v
        ear2 = view(ear3)
        front = view.facing(nrm) > 0.0
        inner3 = c + (spline(shape, True, 6) * size * 0.52)[:, :1] * u * (-side) + (spline(shape, True, 6) * size * 0.52)[:, 1:] * v \
            + nrm * size * 0.05

        def det(ctx, inner3=inner3, front=front):
            if front:
                P2 = view(inner3)
                stroke_line(ctx, P2[2:int(len(P2) * 0.72)], darker(skin, 0.6, 0.1), lw * 1.2)
        pieces.append(Piece([ear2], skin, dep, size=size * 1.2 * s, detail=det, cast=0.1 if rel > 0 else 0.0, rim=0.4))

    def face_detail(ctx):
        _blush(ctx, L, view, H, F, s)
        _mouth(ctx, L, view, H, F, s, lw)
        _eyes(ctx, L, view, H, F, s, lw, nose_poly)
        _brows(ctx, L, view, H, F, s, lw)

    def face_over(ctx):
        _nose_line(ctx, L, view, H, F, s, lw, nose_poly)
        if L.moustache:
            _moustache(ctx, L, view, H, F, s, lw)
        if F.tear > 0:
            _tear(ctx, L, view, H, F, s, lw)

    pieces.append(Piece(polys, skin, hd, size=R * 1.4 * s, detail=face_detail, cast=0.2, rim=0.7))
    pieces.append(Piece([], skin, hd + 0.3, over=face_over))
    return pieces


def _eyes(ctx, L: Person, view: View, H: Head, F: Face, s: float, lw: float, nose_poly) -> None:
    R = L.R
    eyes = []
    for side in (-1, 1):
        p, n = H.cran(side * L.eye_az, L.eye_el)
        eyes.append((view.depth(p), side, p, n))
    eyes.sort(key=lambda e: e[0])
    skin = hexc(L.skin)
    for i, (dep, side, p, n) in enumerate(eyes):
        f = view.facing(n)
        if f < 0.1:
            continue
        far = i == 0 and abs(view.yaw_eff + F.turn) > 14
        k = min(1.0, f) ** 0.8
        ew = R * 0.19 * L.eye * s * k
        eh = R * 0.19 * L.eye * L.eye_round * s
        c = view(p)[0]
        inner = 1 if (view(p - H.N([side * 0.02, 0, 0]))[0] - c)[0] > 0 else -1
        ang = math.radians(F.tilt) * 0.7
        ctx.save()
        if far:
            ctx.rectangle(-1e5, -1e5, 2e5, 2e5)
            _path(ctx, [nose_poly])
            ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            ctx.clip()
            ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
        shut = F.shut
        if F.shut == "wink":
            shut = "happy" if far else ""
        ca, sa = math.cos(ang), math.sin(ang)
        rot = np.array([[ca, sa], [-sa, ca]])
        if shut in ("happy", "soft"):
            t = np.linspace(-1, 1, 17)
            sgn = 1 if shut == "happy" else -0.55
            pts = np.stack([t * ew * 1.0, eh * 0.15 - sgn * (1 - t ** 2) * eh * 0.5], 1) @ rot + c
            stroke_line(ctx, pts, INK, lw * 2.4)
            if L.lash:
                o = pts[-1] if inner < 0 else pts[0]
                d = np.array([-inner * 1.0, -0.6]) * eh * 0.35
                stroke_line(ctx, np.array([o, o + d]), INK, lw * 2.0, lw * 0.6)
        elif shut == "squeeze":
            pts = np.array([[-inner * ew * 0.9, -eh * 0.45], [inner * ew * 0.5, 0], [-inner * ew * 0.9, eh * 0.45]]) @ rot + c
            stroke_line(ctx, pts, INK, lw * 2.3)
        else:
            h = eh * F.open
            t = np.linspace(0, 2 * np.pi, 56, endpoint=False)
            # a soft almond: fuller on top, the outer corner a little lower
            shape = np.stack([np.cos(t) * ew, np.sin(t) * h * np.where(np.sin(t) < 0, 1.0, 0.86)], 1)
            shape[:, 1] += -inner * np.cos(t) * h * 0.06
            shape = shape @ rot + c
            ctx.save()
            _path(ctx, [shape])
            ctx.clip()
            ctx.set_source_rgb(*hexc("#fbf7ef"))
            ctx.paint()
            nx = view.cam(n)[0, 0]
            g = np.array([F.gaze[0] * ew * 0.4 + nx * ew * 0.28, -F.gaze[1] * h * 0.3 + eh * 0.05])
            pc = c + g
            ir = min(ew, eh) * 0.82 if L.eye_round >= 0.9 else eh * 0.95
            iris = hexc(L.iris)
            fill_poly(ctx, ell2(pc, ir * k ** 0.35, ir), iris)
            fill_poly(ctx, ell2(pc + [0, ir * 0.42], ir * 0.72 * k ** 0.35, ir * 0.42), mix(iris, (1.0, 0.8, 0.5), 0.35), 0.75)
            fill_poly(ctx, ell2(pc, ir * 0.5 * k ** 0.35, ir * 0.5), hexc("#140c10"))
            hl = ir * 0.34
            fill_poly(ctx, ell2(pc + [-ir * 0.36, -ir * 0.36], hl, hl * 1.05), (1, 1, 1))
            fill_poly(ctx, ell2(pc + [ir * 0.34, ir * 0.3], hl * 0.42, hl * 0.42), (1, 1, 1), 0.9)
            if F.glisten:
                fill_poly(ctx, ell2(pc + [ir * 0.05, ir * 0.6], ir * 0.7, ir * 0.18), (1, 1, 1), 0.55 * F.glisten)
            # lids
            lid_c = darker(skin, 0.93, 0.03)
            for sgn, amt in ((-1, F.lid), (1, F.low)):
                if amt <= 0.001 and not (sgn < 0 and F.lid_tilt):
                    continue
                yl = (-h + 2 * h * amt) if sgn < 0 else (h - 2 * h * amt)
                tl = F.lid_tilt * h * 0.55 if sgn < 0 else 0.0
                x0, x1 = -ew * 1.5, ew * 1.5
                xi, xo = (x1, x0) if inner > 0 else (x0, x1)
                edge = np.array([[xo, yl - tl * 0.6], [xi, yl + tl]])
                far_y = -h * 3 if sgn < 0 else h * 3
                poly = np.array([edge[0], edge[1], [xi, far_y], [xo, far_y]]) @ rot + c
                fill_poly(ctx, poly, lid_c)
                stroke_line(ctx, edge @ rot + c, INK, lw * (2.2 if sgn < 0 else 1.0))
            ctx.restore()
            # the upper lid line (lashes) is heavy, the lower one light and short
            top = shape[np.argsort(shape[:, 1])[:30]]
            top = top[np.argsort(top[:, 0])]
            stroke_line(ctx, top, INK, lw * 2.4)
            bot = shape[np.argsort(-shape[:, 1])[:16]]
            bot = bot[np.argsort(bot[:, 0])]
            stroke_line(ctx, bot, darker(skin, 0.5, 0.1), lw * 0.9)
            if L.lash:  # a flick at the outer corner
                o = top[0] if inner > 0 else top[-1]
                d = np.array([-inner * 1.0, -0.75]) * eh * 0.36
                stroke_line(ctx, np.array([o, o + d]) @ np.eye(2), INK, lw * 2.2, lw * 0.6)
            if F.glisten:
                stroke_line(ctx, bot + [0, 1.0], (0.85, 0.95, 1.0), lw * 1.2, lw * 1.2, 0.8 * F.glisten)
        ctx.restore()


def _brows(ctx, L: Person, view: View, H: Head, F: Face, s: float, lw: float) -> None:
    """Each brow a filled sweep on the skull: square and thick at the inner end, tapering to the outer end."""
    R = L.R
    col = darker(L.hair_c, 0.9, 0.05)
    t = np.linspace(0, 1, 15)  # inner -> outer
    base = L.eye_el + 18 + F.brow * 6.5
    tilt = -F.brow_tilt * 7.0 * (1 - t)  # + tilt: inner end pulled down
    arch = 3.2 * np.sin(np.pi * (0.15 + 0.85 * t)) * (1 - 0.6 * abs(F.brow_tilt))
    thick = (3.0 + 4.2 * L.brow_w) * (1 - 0.72 * t ** 1.4)
    for side in (-1, 1):
        azs = side * (L.eye_az - 17 + 36 * t)
        lo = base + tilt + arch
        p0, n0 = H.cran(azs, lo, R * 0.02)
        p1, _ = H.cran(azs, lo + thick, R * 0.025)
        f = np.array([view.facing(nn) for nn in n0])
        vis = f > 0.12
        if vis.sum() < 4:
            continue
        a, b = view(p0)[vis], view(p1)[vis]
        poly = np.vstack([a, b[::-1]])
        fill_poly(ctx, spline(poly, True, 3), col)


def _mouth_curves(L: Person, H: Head, F: Face):
    R = L.R
    t = np.linspace(-1, 1, 25)
    w = L.mouth_w * (1 + 0.35 * F.wide + 0.12 * max(0.0, F.smile))
    p, n = H.jaw_pt(t * w, np.full_like(t, L.mouth_el))
    up = H.N(np.array([0, 1.0, 0]))
    lift = F.smile * R * 0.13 * t ** 2 - F.smile * R * 0.02
    upper = p + up * lift[:, None]
    drop = F.mouth * R * 0.42 * (1 - t ** 2) ** 0.8 * (1 + 0.25 * F.smile)
    lower = upper - up * drop[:, None]
    return t, upper, lower, n


def _mouth(ctx, L: Person, view: View, H: Head, F: Face, s: float, lw: float) -> None:
    t, upper, lower, nrm = _mouth_curves(L, H, F)
    vis = np.array([view.facing(nn) > -0.05 for nn in nrm])
    if vis.sum() < 2:
        return
    U, Lo = view(upper)[vis], view(lower)[vis]
    tv = t[vis]
    R = L.R
    skin = hexc(L.skin)
    if F.mouth > 0.04:
        poly = np.vstack([U, Lo[::-1]])
        fill_poly(ctx, poly, hexc("#5a1f2a"))
        ctx.save()
        _path(ctx, [poly])
        ctx.clip()
        mid = Lo[len(Lo) // 2]
        fill_poly(ctx, ell2(mid + [0, -R * s * 0.01], R * s * 0.26, R * s * 0.12 * (0.5 + F.mouth)), hexc("#e0707f"))
        if F.teeth:
            band = np.vstack([U, U[::-1] + [0, R * s * 0.075 * F.teeth]])
            fill_poly(ctx, band, (1.0, 0.99, 0.95))
        ctx.restore()
        stroke_line(ctx, np.vstack([poly, poly[:1]]), INK, lw * 1.3)
    else:
        stroke_line(ctx, U, INK, lw * 1.7, lw * 1.7)
        # a soft lower lip line under the middle
        m = len(U) // 2
        a = U[max(0, m - 3):m + 4] + [0, R * s * 0.055]
        if len(a) > 1 and not L.moustache:
            stroke_line(ctx, a, darker(skin, 0.72, 0.08), lw * 1.1)
    if F.tongue > 0:  # the tip of the tongue at the screen-right corner
        e = U[-1] if U[-1][0] > U[0][0] else U[0]
        r = R * s * 0.06 * F.tongue
        tg = ell2(e + [r * 0.3, r * 0.5], r * 0.85, r * 0.7, 0.4)
        fill_poly(ctx, tg, hexc("#e8707e"))
        stroke_line(ctx, np.vstack([tg, tg[:1]]), INK, lw * 1.1)
    # a moustache hides the mouth, so a smile shows in the cheeks: a crease from the nose round each end
    if L.moustache and F.smile > 0.3:
        W = L.mouth_w * 1.3 * L.moustache
        for side in (-1, 1):
            p, n = H.jaw_pt(side * (W * 0.7 + np.linspace(0, 9, 9) ** 1.1), L.mouth_el + np.linspace(27, 2, 9), R * 0.01)
            f = np.array([view.facing(nn) for nn in n])
            if (f > 0.15).sum() > 3:
                stroke_line(ctx, view(p)[f > 0.15], darker(skin, 0.58, 0.1), lw * 0.7, lw * 2.0, min(1.0, F.smile * 1.2))
    # dimples at the corners of a smile
    if F.smile > 0.4 and F.mouth < 0.25 and not L.moustache:
        for end in (0, -1):
            if (end == 0 and tv[0] < -0.9) or (end == -1 and tv[-1] > 0.9):
                e = U[end]
                d = U[end] - U[end + (1 if end == 0 else -1)]
                d = d / (np.linalg.norm(d) + 1e-9)
                stroke_line(ctx, np.array([e - d * 1.5, e + d * R * s * 0.04 + np.array([0, -R * s * 0.035])]),
                            darker(skin, 0.6, 0.1), lw * 1.1)


def _arc_by_angle(poly, c, a0: float, a1: float) -> np.ndarray:
    """The part of a closed outline between two screen angles round c (0 = right, 90 = down), in order."""
    d = poly - c
    ang = np.degrees(np.arctan2(d[:, 1], d[:, 0]))
    ang = (ang - a0) % 360
    sel = ang <= (a1 - a0) % 360
    pts, a = poly[sel], ang[sel]
    return pts[np.argsort(a)]


def _nose_line(ctx, L: Person, view: View, H: Head, F: Face, s: float, lw: float, nose_poly) -> None:
    """The nose as a cartoonist draws it: a hook round the shadow side of the tip, a nostril, and in 3/4 the far
    side of the nose as one line; in profile the outline of the head already draws it."""
    skin = hexc(L.skin)
    ink = darker(skin, 0.42, 0.12)
    c = nose_poly.mean(0)
    nrm = H.N(np.array([0, 0, 1.0]))
    if view.facing(nrm) < -0.3:
        return
    turned = float(view.cam(nrm)[0, 0])  # + the face looks toward screen right
    if abs(turned) < 0.22:  # facing us: a hook under the tip on the shadow side (screen right)
        arc = _arc_by_angle(nose_poly, c, 25, 128)
        if len(arc) > 2:
            stroke_line(ctx, arc[::-1], ink, lw * 1.7, lw * 0.5)
        # soft shade on the shadow half of the tip
        sh = _arc_by_angle(nose_poly, c, -70, 110)
        if len(sh) > 2:
            fill_poly(ctx, np.vstack([sh, [c + [H.nr[0] * s * 0.05, 0]]]), darker(skin, 0.86, 0.06), 0.55)
    elif abs(turned) < 0.85:  # 3/4: the far side of the nose and round under its tip
        sg = 1 if turned > 0 else -1
        arc = _arc_by_angle(nose_poly, c, -75, 130) if sg > 0 else _arc_by_angle(nose_poly, c, 50, 255)
        if sg < 0:
            arc = arc[::-1]
        if len(arc) > 2:
            stroke_line(ctx, arc, ink, lw * 0.7, lw * 1.6)
    # the nostril on the side we see best
    sd = 1 if turned >= 0 else -1
    if view.facing(H.N(np.array([-sd * 0.4, -0.5, 0.8]))) > 0.1:
        p = H.X(H.nc + np.array([-sd * H.nr[0] * 0.45, -H.nr[1] * 0.62, H.nr[2] * 0.35]))
        q = view(p)[0]
        stroke_line(ctx, np.array([q + [-H.nr[0] * s * 0.25, -H.nr[1] * s * 0.05], q, q + [H.nr[0] * s * 0.18, H.nr[1] * s * 0.1]]),
                    ink, lw * 1.1, lw * 0.5, 0.8)


def _moustache(ctx, L: Person, view: View, H: Head, F: Face, s: float, lw: float) -> None:
    R = L.R
    col = L.hair_c
    W = L.mouth_w * 1.3 * L.moustache * (1 + 0.15 * F.wide)
    azs = np.linspace(-W, W, 41)
    u = azs / W
    lift = F.smile * 3.0 * u ** 2
    mid = L.mouth_el + 9.5 - 7.5 * u ** 2 + lift  # the ends droop past the corners of the mouth
    T = 15.0 * L.moustache * (1 - u ** 2) ** 0.55 + 1.2  # thick in the middle, tapering to points
    notch = 2.2 * np.exp(-(u / 0.1) ** 2)  # the dip under the nose
    scallop = 1.1 * np.abs(np.sin(u * np.pi * 3.5)) * (1 - u ** 2)
    top_el = mid + T * 0.55 - notch
    bot_el = mid - T * 0.45 - scallop
    pt, nt = H.jaw_pt(azs, top_el, R * 0.03)
    pb, nb = H.jaw_pt(azs, bot_el, R * 0.06)
    vis = np.array([view.facing(a) > -0.1 and view.facing(b) > -0.1 for a, b in zip(nt, nb)])
    if vis.sum() < 3:
        return
    idx = np.where(vis)[0]
    T, Bt = view(pt)[idx], view(pb)[idx]
    poly = np.vstack([T, Bt[::-1]])
    poly = spline(poly[::2], True, 3) if len(poly) > 8 else poly
    fill_poly(ctx, poly, col)
    ctx.save()
    _path(ctx, [poly])
    ctx.clip()
    # light along the top (the light is above), darker toward the lip, a few combed strokes
    fill_poly(ctx, np.vstack([T, T[::-1] + [0, R * s * 0.05]]), mix(col, (1, 1, 1), 0.14), 0.7)
    n = len(T)
    for k in range(2, n - 2, 3):
        o = 1 if k > n // 2 else -1
        j = int(np.clip(k + o * 2, 0, n - 1))
        a, b = T[k], Bt[j]
        stroke_line(ctx, np.array([a + (b - a) * 0.25, a + (b - a) * 0.8]), darker(col, 0.72, 0.05), lw * 0.7, lw * 0.25, 0.45)
    ctx.restore()
    stroke_line(ctx, np.vstack([poly, poly[:1]]), INK, lw * 1.3)


def _tear(ctx, L: Person, view: View, H: Head, F: Face, s: float, lw: float) -> None:
    R = L.R
    for side in (-1, 1):
        p, n = H.cran(side * (L.eye_az + 4), L.eye_el - 14 - 22 * F.tear, R * 0.03)
        if view.facing(n) < 0.35 or abs(view.yaw_eff) > 10 and side * view.yaw_eff > 0:
            continue
        c = view(p)[0]
        r = R * s * 0.055
        drop = np.array([[0, -1.25], [0.6, 0.15], [0.45, 0.65], [0, 0.82], [-0.45, 0.65], [-0.6, 0.15]]) * r + c
        drop = spline(drop, True, 6)
        fill_poly(ctx, drop, hexc("#bfe6fb"))
        stroke_line(ctx, np.vstack([drop, drop[:1]]), hexc("#3c7aa6"), lw * 0.9)
        fill_poly(ctx, ell2(c + [-r * 0.2, r * 0.15], r * 0.16, r * 0.24), (1, 1, 1))
        break


def _blush(ctx, L: Person, view: View, H: Head, F: Face, s: float) -> None:
    if F.blush <= 0:
        return
    for side in (-1, 1):
        p, n = H.jaw_pt(side * 50, 8)
        f = view.facing(n)
        if f < 0.1:
            continue
        c = view(p)[0]
        fill_poly(ctx, ell2(c, L.R * s * 0.17 * f ** 0.6, L.R * s * 0.1), hexc("#ec7f86"), 0.4 * F.blush)


# ---------- hair ----------


def _hairline_fn(style: str):
    """The hairline: elevation (degrees) on the skull at each azimuth."""
    if style == "short":  # a side parting, temples a little high, neat round the ears, down to the nape
        pts = [(0, 34), (25, 30), (50, 22), (70, 16), (85, 10), (100, 4), (120, -22), (150, -42), (180, -48)]
    elif style == "fringe":  # a little girl's fringe
        pts = [(0, 12), (30, 13), (50, 10), (70, 4), (90, -6), (110, -18), (140, -40), (180, -55)]
    elif style == "parted":  # pulled back from a middle parting
        pts = [(0, 30), (25, 27), (50, 20), (70, 12), (90, 2), (110, -14), (140, -38), (180, -52)]
    else:
        pts = [(0, 30), (60, 18), (90, 4), (180, -50)]
    xs = np.array([p[0] for p in pts], float)
    ys = np.array([p[1] for p in pts], float)

    def f(az):
        a = np.abs(((np.asarray(az, float) + 180) % 360) - 180)
        el = np.interp(a, xs, ys)
        if style == "fringe":  # a cut fringe: little scallops across the front
            front = np.clip(1 - a / 62, 0, 1)
            el = el - front * 3.2 * np.abs(np.sin(np.radians(np.asarray(az, float)) * 9.0))
        return el
    return f


def _shell(view: View, H: Head, hl, k=(1.07, 1.06, 1.07), nu: int = 90, nv: int = 10, lift: float = 0.0):
    azs = np.linspace(-180, 180, nu + 1)
    el0 = hl(azs)
    ts = np.linspace(0, 1, nv + 1) ** 0.85
    AZ = np.tile(azs, (nv + 1, 1))
    EL = el0[None, :] + (89.0 - el0[None, :]) * ts[:, None]
    r = H.cr * np.array(k)
    c = H.c + np.array([0, lift * H.cr[1], 0])
    p, n = on_ellipsoid(c, r, AZ, EL)
    Pw = H.X(p.reshape(-1, 3))
    Nw = H.N(n.reshape(-1, 3))
    P2 = view(Pw).reshape(nv + 1, nu + 1, 2)
    fac = (Nw @ view.M.T)[:, 2].reshape(nv + 1, nu + 1)
    front, back = [], []
    for i in range(nv):
        for j in range(nu):
            q = np.array([P2[i, j], P2[i, j + 1], P2[i + 1, j + 1], P2[i + 1, j]])
            f = fac[i, j] + fac[i, j + 1] + fac[i + 1, j] + fac[i + 1, j + 1]
            (front if f > 0 else back).append(q)
    return front, back, P2[0]


def _hair(L: Person, view: View, F: Face, H: Head, hd: float, lw: float, torso_depth: float, B) -> list[Piece]:
    s = view.s
    R = L.R
    col = L.hair_c
    style = L.hair_style
    pieces: list[Piece] = []
    shell_style = {"short": "short", "pigtails": "fringe", "braids": "parted", "braid": "parted", "bun": "parted"}[style]
    hl = _hairline_fn(shell_style)
    front, back, edge = _shell(view, H, hl)
    extra_front = []
    if style == "short":  # volume on top, swept from a parting on the screen-left side
        for (az, el, rr, kk) in ((18, 62, 0.62, (1.0, 0.55, 0.9)), (-30, 58, 0.5, (0.9, 0.5, 0.85)), (40, 45, 0.5, (0.8, 0.5, 0.9))):
            p, _ = on_ellipsoid(H.c, H.cr * 0.98, az, el)
            extra_front.append(ellipsoid(view, H.X(p), np.array(kk) * R * rr, H.Rw @ rotm(-20, az * 0.4, 0)))

    def shine(ctx):
        # one soft band of light across the crown, on the side toward the light, thinning at its ends
        azs = np.linspace(-85, 35, 30)
        u = np.linspace(-1, 1, len(azs))
        w = 6.5 * (1 - u ** 2) ** 0.7
        p0, n0 = on_ellipsoid(H.c, H.cr * 1.076, azs, 50.0 - w / 2)
        p1, _ = on_ellipsoid(H.c, H.cr * 1.076, azs, 50.0 + w / 2)
        f = np.array([view.facing(nn) for nn in H.N(n0)])
        vis = f > 0.12
        if vis.sum() > 4:
            a, b = view(H.X(p0))[vis], view(H.X(p1))[vis]
            fill_poly(ctx, np.vstack([a, b[::-1]]), mix(col, (0.72, 0.8, 1.0), 0.3), 0.75)
        # a few long combed strokes sweeping back from the front
        for az0 in np.linspace(-50, 60, 4):
            azs2 = az0 + np.linspace(0, 40, 6) * (1 if az0 >= -20 else -1)
            els2 = np.linspace(hl(np.array([az0]))[0] + 10, 70, 6)
            pp, nn = on_ellipsoid(H.c, H.cr * 1.075, azs2, els2)
            fv = np.array([view.facing(q) for q in H.N(nn)])
            if fv.min() > 0.25:
                stroke_line(ctx, view(H.X(pp)), mix(col, (1, 1, 1), 0.12), lw * 0.4, lw * 0.9, 0.5)

    pieces.append(Piece(back, col, torso_depth - 0.03, size=R * 1.3 * s, shade=0.8))
    pieces.append(Piece(front + extra_front, col, hd + 0.02, size=R * 1.3 * s, detail=shine, cast=0.25))

    red = hexc("#d62839")
    if style == "pigtails":  # two bunches high on the sides, tied with red ribbon bows
        for side in (-1, 1):
            base, _ = on_ellipsoid(H.c, H.cr, side * 98, 16)
            outd = np.array([side * 1.0, 0.06, -0.25])
            pts = [H.X(base), H.X(base + outd * R * 0.4), H.X(base + outd * R * 0.78 + [0, -R * 0.06, 0]),
                   H.X(base + outd * R * 0.98 + [0, -R * 0.2, 0])]
            rel = _rel(view, pts[1], H.centre)
            dep = hd + rel - 0.004
            bunch = tube(view, pts, [R * 0.2, R * 0.37, R * 0.33, R * 0.15], n=6)
            pieces.append(Piece(bunch, col, dep, size=R * 0.7 * s, cast=0.15,
                                detail=_strands(view, pts, col, lw)))
            pieces += _bow(view, H.X(base + outd * R * 0.18), H, side, red, dep + 0.002, R, lw)
    elif style in ("braids", "braid"):
        sides = (-1, 1) if style == "braids" else (0,)
        for side in sides:
            if side:
                base, _ = on_ellipsoid(H.c, H.cr, side * 112, -20)
                path = [base, base + [side * R * 0.18, -R * 0.55, -R * 0.15], base + [side * R * 0.22, -R * 1.25, R * 0.05],
                        base + [side * R * 0.2, -R * 1.9, R * 0.12]]
            else:
                base, _ = on_ellipsoid(H.c, H.cr, 180, -25)
                path = [base, base + [0.0, -R * 0.6, -R * 0.3], base + [0.0, -R * 1.5, -R * 0.42], base + [0.0, -R * 2.6, -R * 0.4]]
            P3 = spline(np.array(path), False, 6)
            P3w = np.array([H.X(p) for p in P3])
            pieces += _braid(view, P3w, R * 0.2, col, torso_depth, B, H, hd, lw)
            end = P3w[-1]
            rel_end = _rel(view, end, B([0.0, end[1], 0.0]))
            pieces += _bow(view, end, H, side if side else 1, red, torso_depth + rel_end + (0.035 if rel_end > 0 else -0.025),
                           R * 0.75, lw, braid=True)
    elif style == "bun":  # a messy bun on the back of the crown, two brushes stuck through
        bc, _ = on_ellipsoid(H.c, H.cr, 180, 38)
        c = H.X(bc + [0, R * 0.12, -R * 0.12])
        rel = _rel(view, c, H.centre)
        dep = hd + rel + (0.025 if rel > 0 else -0.004)
        bun = [ellipsoid(view, c, (R * 0.48, R * 0.42, R * 0.44), H.Rw)]
        for k, (dx, dy) in enumerate(((-0.3, 0.25), (0.32, 0.18), (0.05, 0.38))):
            bun.append(ellipsoid(view, H.X(bc + [dx * R, (0.12 + dy) * R, -R * 0.12]), (R * 0.22, R * 0.2, R * 0.22), H.Rw))
        pieces.append(Piece(bun, col, dep, size=R * 0.8 * s, cast=0.15, detail=_bun_detail(view, c, R, col, lw)))
        for k, (dx, colh, colt) in enumerate(((-0.35, "#c0472f", "#f2b632"), (0.3, "#2f5fa8", "#d62839"))):
            a = H.X(bc + [dx * R * 0.8, R * 0.1, -R * 0.2])
            b = H.X(bc + [dx * R * 1.5, R * 0.42, -R * 0.95])
            d = unit(b - a)
            handle = tube(view, [a, b], [R * 0.045, R * 0.035], n=2)
            ferr = tube(view, [b, b + d * R * 0.12], [R * 0.05, R * 0.05], n=2)
            tip = tube(view, [b + d * R * 0.12, b + d * R * 0.3], [R * 0.05, R * 0.012], n=3)
            rb = _rel(view, (a + b) / 2, H.centre)
            dd = hd + rb + (0.03 if rb > 0 else -0.006)
            pieces += [Piece(handle, hexc(colh), dd, size=R * 0.1 * s), Piece(ferr, hexc("#c9ccd2"), dd + 0.0004, size=R * 0.1 * s),
                       Piece(tip, hexc(colt), dd + 0.0006, size=R * 0.1 * s)]
    return pieces


def _strands(view, pts, col, lw):
    def f(ctx):
        P2 = view(spline(np.array(pts), False, 6))
        for off in (-0.25, 0.1):
            q = P2 + np.array([0, off * 10])
            stroke_line(ctx, q[2:-3], darker(col, 0.7, 0.05), lw * 0.8, lw * 0.3, 0.6)
    return f


def _bun_detail(view, c, R, col, lw):
    def f(ctx):
        q = view(c)[0]
        r = R * view.s
        for k in range(3):
            a = np.linspace(0.3 + k * 1.9, 1.9 + k * 1.9, 12)
            arc = np.stack([q[0] + np.cos(a) * r * (0.3 - 0.06 * k), q[1] + np.sin(a) * r * (0.24 - 0.05 * k)], 1)
            stroke_line(ctx, arc, darker(col, 0.65, 0.05), lw * 1.0, lw * 0.4, 0.8)
    return f


def _braid(view, P3w, r, col, torso_depth, B, H, hd, lw) -> list[Piece]:
    """A plait: overlapping lobes that alternate side to side down the path, each its own piece so the weave shows."""
    pieces = []
    n = len(P3w)
    lobes = 9
    for i in range(lobes):
        u = i / (lobes - 1)
        k = u * (n - 1)
        a = P3w[int(math.floor(k))]
        b = P3w[min(n - 1, int(math.floor(k)) + 1)]
        p = a + (b - a) * (k - math.floor(k))
        d = unit(P3w[min(n - 1, int(k) + 1)] - P3w[max(0, int(k) - 1)])
        side = np.cross(d, [0, 0, 1.0])
        side = unit(side) if np.linalg.norm(side) > 1e-6 else np.array([1.0, 0, 0])
        rr = r * (1.0 - 0.45 * u)
        c = p + side * rr * 0.28 * (1 if i % 2 else -1)
        rel = _rel(view, c, H.centre)
        dep = (hd + rel - 0.004) if i < 2 else (torso_depth + rel * 1.0 + (0.03 if rel > 0 else -0.03)) + 0.0005 * i
        pieces.append(Piece([ellipsoid(view, c, (rr * 0.9, rr * 1.35, rr * 0.8), _frame_from(d) @ rotm(0, 0, 25 if i % 2 else -25))],
                            col, dep, size=rr * 1.6 * view.s, line=darker(col, 0.55, 0.05)))
    return pieces


def _bow(view, at, H, side, col, dep, R, lw, braid=False) -> list[Piece]:
    """A ribbon bow: two loops, a knot, two tails."""
    up = H.N(np.array([0, 1.0, 0]))
    fw = H.N(np.array([0, 0, 1.0]))
    x = np.cross(up, fw)
    sz = R * (0.85 if not braid else 0.7)
    loops = []
    for k in (-1, 1):
        c = at + up * sz * 0.25 * k + x * sz * 0.05 * side + fw * sz * 0.08
        loops.append(ellipsoid(view, c, (sz * 0.18, sz * 0.32, sz * 0.18), H.Rw @ rotm(0, 0, 25 * k)))
    tails = []
    for k in (-1, 1):
        tails.append(ribbon(view, [at, at + up * sz * 0.05 * k - fw * sz * 0.02 + x * side * sz * 0.12 + [0, -sz * 0.25, 0],
                                   at + up * sz * 0.1 * k + x * side * sz * 0.2 + [0, -sz * 0.5, 0]],
                            [sz * 0.14, sz * 0.13, sz * 0.15], [fw] * 3))
    knot = [ellipsoid(view, at + fw * sz * 0.1, (sz * 0.1, sz * 0.1, sz * 0.09), H.Rw)]
    line = darker(col, 0.5)
    return [Piece(tails, darker(col, 0.92, 0.03), dep - 0.0004, size=sz * 0.3 * view.s, line=line),
            Piece(loops, col, dep, size=sz * 0.4 * view.s, line=line),
            Piece(knot, darker(col, 0.9), dep + 0.0004, size=sz * 0.2 * view.s, line=line)]


# ---------- extras worn on top ----------


def _gamcha(L, view, F, B, body, ex, lw, torso_depth, H, hd):
    """A checked cotton towel round the neck: one end down the chest, one thrown over the shoulder."""
    _, base, check, y = ex
    col = hexc(base)
    chk = hexc(check)
    s = view.s
    pieces = []
    rx, rz, zo = body.at(y)
    wrap = Body([(y - 0.035, rx * 1.02 + 0.012, rz * 1.02 + 0.012, zo), (y, rx * 1.05 + 0.016, rz * 1.05 + 0.016, zo + 0.004),
                 (y + 0.03, rx * 0.98 + 0.012, rz * 0.98 + 0.012, zo), (y + 0.05, rx * 0.8 + 0.008, rz * 0.8 + 0.008, zo)], n=5)
    from vidosuper.graphics.cast import hull
    rings = [view(B(wrap.ring(yy))) for yy in np.linspace(y - 0.035, y + 0.05, 8)]
    wrap_polys = [hull(np.vstack([a, b])) for a, b in zip(rings, rings[1:])]

    def wrap_det(ctx):
        arc = view(B(_lift_arc(wrap, y + 0.004, view)))
        stroke_line(ctx, arc, chk, lw * 2.2)
        arc2 = view(B(_lift_arc(wrap, y - 0.018, view)))
        stroke_line(ctx, arc2, chk, lw * 1.2)
    pieces.append(Piece(wrap_polys, col, torso_depth + 0.03, size=0.05 * s, cast=0.2, detail=wrap_det))
    # the front end, down the chest on the screen-right side (their left)
    p0 = B([0.06, y - 0.02, rz + zo + 0.01])
    hang = [p0, B([0.075, y - 0.12, body.at(y - 0.12)[1] + 0.03]), B([0.08, y - 0.24, body.at(y - 0.24)[1] + 0.035]),
            B([0.078, y - 0.33, body.at(y - 0.33)[1] + 0.035])]
    w = [0.075, 0.08, 0.082, 0.082]
    rb = ribbon(view, hang, w, [[1.0, 0, 0.0]] * 4)
    pieces.append(Piece([rb], col, torso_depth + 0.035 + 0.5 * _rel(view, hang[2], B([0, y - 0.24, 0])), size=0.06 * s, cast=0.2,
                        detail=_checks(view, hang, w, [[1.0, 0, 0.0]] * 4, chk, lw, fringe=True, col=col)))
    # the other end over the screen-left shoulder, down the back
    def on_back(yy, k=1.0, out=0.02):  # a point on the body's surface behind, at height yy
        rxx, rzz, zz = body.at(yy)
        return -rzz * k - out + zz

    back = [B([-0.06, y + 0.006, rz * 0.75 + zo]), B([-0.112, y - 0.008, 0.012]), B([-0.122, y - 0.055, on_back(y - 0.055, 0.9)]),
            B([-0.11, y - 0.16, on_back(y - 0.16, 1.0, 0.012)]), B([-0.1, y - 0.26, on_back(y - 0.26, 1.0, 0.012)])]
    side = [[0, 0, 1.0], [0.4, 0, 1.0], [1.0, 0, 0.2], [1.0, 0, 0.0], [1.0, 0, 0.0]]
    rb2 = ribbon(view, back, [0.07, 0.075, 0.078, 0.08, 0.08], side)
    rel = _rel(view, back[3], B([0, y - 0.16, 0]))
    pieces.append(Piece([rb2], darker(col, 0.94, 0.03), torso_depth + rel * 1.2 + (0.03 if rel > 0 else -0.02), size=0.06 * s,
                        detail=_checks(view, back, [0.07, 0.075, 0.078, 0.08, 0.08], side, chk, lw)))
    return pieces


def _checks(view, pts3, width, side, chk, lw, fringe=False, col=None):
    """Checks on a cotton towel: lines along and across the strip."""
    def f(ctx):
        C = spline(np.asarray(pts3, float), closed=False, n=8)
        w = np.interp(np.linspace(0, 1, len(C)), np.linspace(0, 1, len(width)), width)
        S = np.asarray(side, float)
        S = np.array([np.interp(np.linspace(0, 1, len(C)), np.linspace(0, 1, len(S)), S[:, k]) for k in range(3)]).T
        wpx = np.linalg.norm(view(C + S * w[:, None] / 2) - view(C - S * w[:, None] / 2), axis=1)
        k = np.clip((wpx / (lw * 10.0)) - 0.2, 0, 1)
        for fr in (-0.3, 0.05, 0.38):  # along the strip, only where it shows its face
            P2 = view(C + S * (w * fr)[:, None])
            on = k > 0.35
            start = None
            for i in range(len(P2) + 1):
                if i < len(P2) and on[i]:
                    start = i if start is None else start
                elif start is not None:
                    if i - start > 1:
                        stroke_line(ctx, P2[start:i], chk, lw * 1.6)
                    start = None
        L = np.cumsum(np.r_[0, np.linalg.norm(np.diff(C, axis=0), axis=1)])
        for d in np.arange(0.02, L[-1], 0.045):
            i = int(np.searchsorted(L, d))
            i = min(i, len(C) - 1)
            if k[i] < 0.1:
                continue
            a = C[i] + S[i] * w[i] / 2
            b = C[i] - S[i] * w[i] / 2
            stroke_line(ctx, view(np.array([a, b])), chk, lw * 1.3 * k[i])
        if fringe:
            end = C[-1]
            for k in np.linspace(-0.45, 0.45, 7):
                a = end + S[-1] * w[-1] * k
                q = view(np.array([a, a + [0, -0.018, 0]]))
                stroke_line(ctx, q, chk, lw * 1.0)
    return f


def _collar(L, view, F, B, body, ex, lw, torso_depth, H, hd):
    """A shirt collar: two flaps lying open over the chest."""
    _, col, y = ex
    col = hexc(col)
    s = view.s
    pieces = []
    rx, rz, zo = body.at(y)
    for side in (-1, 1):
        pts = [B([side * 0.012, y + 0.01, rz + zo + 0.004]), B([side * rx * 0.7, y + 0.03, rz * 0.6]),
               B([side * rx * 0.62, y - 0.035, rz * 0.9 + 0.01]), B([side * 0.03, y - 0.075, body.at(y - 0.075)[1] + 0.006])]
        poly = view(np.array(pts))
        nrm = B([0, 0, 1.0]) - B([0, 0, 0])
        if view.facing(nrm) < -0.4:
            continue
        rel = _rel(view, pts[2], B([0, y, 0]))
        pieces.append(Piece([spline(poly, True, 4)], col, torso_depth + 0.025 + rel * 0.2, size=0.04 * s, shade=0.5))
    return pieces


def _pcollar(L, view, F, B, body, ex, lw, torso_depth, H, hd):
    """A round (Peter Pan) collar: two flaps lying on the yoke either side of the neck."""
    _, col, y_top, h, az_w = ex
    col = hexc(col)
    pieces = []
    for side in (-1, 1):
        ring = []
        for t in np.linspace(0, 2 * np.pi, 36, endpoint=False):
            az = side * (5 + az_w * 0.5 * (1 + math.cos(t)))
            y = y_top - h * 0.5 * (1 - math.sin(t))
            y = min(y, y_top)
            ring.append(body.point(y, az, 0.006))
        nf = np.mean([view.facing(n) for _, n in ring])
        if nf < -0.1:
            continue
        P2 = view(B(np.array([p for p, _ in ring])))
        rel = _rel(view, B(ring[0][0]), B([0, y_top, 0]))
        pieces.append(Piece([P2], col, torso_depth + 0.02 + rel * 0.1, size=h * view.s, shade=0.4, line=darker(col, 0.62)))
    return pieces


def _specs(L, view, F, B, body, ex, lw, torso_depth, H, hd):
    _, col = ex
    s = view.s
    R = L.R

    def over(ctx):
        rims = []
        for side in (-1, 1):
            p, n = H.cran(side * L.eye_az, L.eye_el, R * 0.07)
            f = view.facing(n)
            if f < 0.05:
                continue
            c = view(p)[0]
            k = f ** 0.8
            r = R * 0.27 * s
            rim = ell2(c, r * k, r * 0.82, math.radians(F.tilt) * 0.6, 48)
            fill_poly(ctx, rim, hexc("#d9eef7"), 0.18)
            stroke_line(ctx, np.vstack([rim, rim[:1]]), hexc(col), lw * 1.8)
            g = np.array([c + [-r * 0.45 * k, -r * 0.05], c + [-r * 0.1 * k, -r * 0.5]])
            stroke_line(ctx, g, (1, 1, 1), lw * 1.4, lw * 0.6, 0.85)
            rims.append((c, r * k, side))
        if len(rims) == 2:
            a, b = sorted(rims, key=lambda r: r[0][0])
            bridge = np.array([a[0] + [a[1], -R * s * 0.03], (a[0] + b[0]) / 2 + [0, -R * s * 0.07], b[0] + [-b[1], -R * s * 0.03]])
            stroke_line(ctx, spline(bridge, False, 5), hexc(col), lw * 1.6)
        for c, rr, side in rims:
            pe, ne = H.cran(side * 88, L.eye_el + 6, R * 0.04)
            if view.facing(ne) > -0.2 and abs(view.yaw_eff) > 20:
                e = view(pe)[0]
                st = c + np.array([np.sign(e[0] - c[0]) * rr, -R * s * 0.02])
                if np.linalg.norm(e - st) > rr * 0.6:
                    stroke_line(ctx, np.array([st, e]), hexc(col), lw * 1.4)
    return [Piece([], hexc(col), hd + 0.85, over=over)]


def _straps(L, view, F, B, body, ex, lw, torso_depth, H, hd):
    """Dungaree straps over the shoulders, from the bib down the back."""
    _, col, y_front, y_back, x = ex
    col = hexc(col)
    pieces = []
    for side in (-1, 1):
        pts = []
        for y, z in ((y_front, 1.0), (L.shoulder[1] - 0.015, 0.75), (L.slices[-2][0] + 0.005, 0.0), (L.shoulder[1] - 0.04, -0.85),
                     (y_back, -1.0)):
            rx, rz, zo = body.at(min(y, L.slices[-1][0]))
            pts.append(B([side * x, y, zo + (rz + 0.006) * z]))
        pts = np.array(pts)
        # front half and back half apart: seen square on, a strap folded over the shoulder would cancel itself out
        fwd = B([0, 0, 1.0]) - B([0, 0, 0.0])  # the way her chest faces
        for part, idx in (("front", [0, 1, 2]), ("back", [2, 3, 4])):
            q = pts[idx]
            facing = view.facing(fwd if part == "front" else -fwd)
            if facing < -0.05:  # that side of her is turned away: its strap is hidden behind her, so not drawn
                continue
            rb = ribbon(view, spline(q, False, 5), [0.042] * 3, [[1.0, 0, 0]] * 3)
            rel = _rel(view, q[1], B([0, q[1][1], 0]))
            pieces.append(Piece([rb], col, torso_depth + rel * 0.5 + (0.004 if rel > 0 else -0.03), size=0.03 * view.s, shade=0.5,
                                line=darker(col, 0.55)))
    return pieces


EXTRA = {"gamcha": _gamcha, "collar": _collar, "specs": _specs, "straps": _straps, "pcollar": _pcollar}


# ---------- the people ----------


SKIN_PAPA = "#a86a45"
SKIN_TARA = "#c08259"
HAIR = "#211a1e"

PAPA_POSE = Pose(arm_l=(7, -2, 12), arm_r=(9, 8, 24), grip=(0.4, 0.5), lean_side=1.5, foot_r=(0.03, 0.0, 0.05),
                 toe=(0.0, 0.0))

PEOPLE: dict[str, Person] = {
    # a gentle bear: broad shoulders and a soft middle from years at the wheel, big working hands, a big kind
    # moustache; sky-blue shirt with the sleeves rolled, a checked gamcha, a steel kada, chappals
    "papa": Person(
        name="Papa", skin=SKIN_PAPA, hair=HAIR, top="#86aed0", bottom="#6d6250",
        slices=[(0.86, 0.186, 0.14, 0.012), (0.97, 0.19, 0.146, 0.02), (1.08, 0.196, 0.15, 0.026), (1.2, 0.206, 0.142, 0.016),
                (1.28, 0.218, 0.132, 0.004), (1.33, 0.222, 0.122, -0.004), (1.37, 0.212, 0.11, -0.008),
                (1.4, 0.18, 0.096, -0.008), (1.425, 0.13, 0.08, -0.006), (1.445, 0.09, 0.068, -0.002), (1.46, 0.07, 0.062, 0.0)],
        neck=(1.515, 0.016), R=0.118, head_up=1.0, skull=(0.93, 1.0, 1.0), jaw=(0.86, 0.66, 0.84), jaw_at=(0.0, -0.55, 0.15),
        chin=(-1.04, 0.3, 0.36), nose=(0.22, 0.24, 0.26), nose_at=(0.0, -0.3, 0.97), eye=1.14, eye_az=30, eye_el=-4,
        eye_round=0.8, iris="#3b2316", brow_w=1.55, ear=0.32, ear_el=-10, mouth_el=-19, mouth_w=30, moustache=1.0,
        hair_style="short", hip=(0.095, 0.88), leg_r=(0.082, 0.058), foot=(0.125, 0.048, 0.034), shoe="#6b4630",
        shoe_kind="chappal", shoulder=(0.205, 1.31), arm=(0.29, 0.27), arm_r=(0.054, 0.046, 0.037), hand=0.056,
        sleeve="#86aed0", sleeve_len=0.66, sleeve_kind="rolled", pose=PAPA_POSE, face=Face(smile=0.4, low=0.12),
        extras=(("placket", "#86aed0", 1.44, 0.87, (1.34, 1.22, 1.1, 0.98), "#f1ece0"),
                ("pocket", "#86aed0", 28, 1.31, 14, 0.1, True), ("gamcha", "#c43d3d", "#f6e9d4", 1.44), ("kada", -1, "#c9ccd2")),
    ),
}
PEOPLE["papa_old"] = replace(
    PEOPLE["papa"], name="Papa, later", grey=0.85, top="#7f9fbd", sleeve="#7f9fbd",
    slices=[(0.86, 0.19, 0.146, 0.016), (0.97, 0.196, 0.156, 0.03), (1.08, 0.2, 0.16, 0.036), (1.2, 0.206, 0.146, 0.02),
            (1.28, 0.214, 0.13, 0.004), (1.33, 0.21, 0.12, -0.006), (1.37, 0.19, 0.108, -0.01),
            (1.4, 0.146, 0.092, -0.01), (1.43, 0.096, 0.074, -0.006), (1.45, 0.07, 0.062, 0.0)],
    neck=(1.505, 0.03), pose=replace(PAPA_POSE, lean=3.0), face=Face(smile=0.5, low=0.24, lid=0.12),
    extras=PEOPLE["papa"].extras + (("specs", "#5b4636"),),
)

PEOPLE["tara5"] = Person(
    # five: a big round head, a fringe and two bunches in red ribbons; a marigold frock with little white flowers;
    # always a crayon in her fist
    name="Tara, 5", skin=SKIN_TARA, hair=HAIR, top="#f2b632", bottom="",
    slices=[(0.47, 0.112, 0.092, 0.0), (0.55, 0.108, 0.088, 0.004), (0.63, 0.116, 0.086, 0.004), (0.68, 0.11, 0.08, -0.002),
            (0.71, 0.088, 0.066, -0.002), (0.732, 0.054, 0.046, 0.0), (0.745, 0.036, 0.034, 0.0)],
    neck=(0.785, 0.01), R=0.122, head_up=1.0, skull=(1.0, 0.98, 0.98), jaw=(0.95, 0.68, 0.86), jaw_at=(0.0, -0.45, 0.12),
    nose=(0.11, 0.1, 0.12), nose_at=(0.0, -0.4, 0.93), eye=1.38, eye_az=31, eye_el=-14, eye_round=1.08, iris="#3a2216",
    brow_w=0.75, lash=1.0, ear=0.27, ear_el=-16, mouth_el=-21, mouth_w=24, hair_style="pigtails", hip=(0.052, 0.43),
    leg_r=(0.034, 0.026), foot=(0.062, 0.03, 0.022), shoe="#c23a3a", shoe_kind="sandal", shoulder=(0.104, 0.69),
    arm=(0.155, 0.145), arm_r=(0.028, 0.024, 0.021), hand=0.03, sleeve="#f2b632", sleeve_len=0.55, sleeve_kind="puff",
    skirt=[(0.33, 0.19, 0.17, 0.0), (0.4, 0.16, 0.14, 0.004), (0.48, 0.125, 0.104, 0.004), (0.53, 0.114, 0.094, 0.002)],
    skirt_c="#f2b632",
    pose=Pose(arm_l=(10, 18, 70), arm_r=(14, 2, 12), hand_in=(70, 0), grip=(0.85, 0.35), hold="crayon"),
    face=Face(smile=0.55, blush=0.5),
    extras=(("print", "#fff7e6", 0.06, 0.008, ("top", "skirt")), ("hemline", "#fff7e6", "skirt", 0.345),
            ("pcollar", "#fff7e6", 0.744, 0.05, 62)),
    line_k=1.15,
)
PEOPLE["tara10"] = Person(
    name="Tara, 10", skin=SKIN_TARA, hair=HAIR, top="#2a9d8f", bottom="#efe6d6",
    slices=[(0.68, 0.13, 0.098, 0.0), (0.76, 0.118, 0.088, 0.004), (0.86, 0.128, 0.09, 0.004), (0.95, 0.132, 0.088, 0.0),
            (0.99, 0.118, 0.08, -0.002), (1.02, 0.088, 0.064, 0.0), (1.045, 0.05, 0.042, 0.0), (1.055, 0.036, 0.032, 0.0)],
    neck=(1.09, 0.01), R=0.112, skull=(0.97, 1.0, 0.98), jaw=(0.84, 0.64, 0.82), jaw_at=(0.0, -0.54, 0.14),
    nose=(0.12, 0.12, 0.14), nose_at=(0.0, -0.38, 0.95), eye=1.2, eye_az=31, eye_el=-11, eye_round=1.0, iris="#3a2216",
    brow_w=0.8, lash=1.0, ear=0.28, mouth_el=-22, mouth_w=26, hair_style="braids", hip=(0.06, 0.64),
    leg_r=(0.045, 0.034), foot=(0.085, 0.036, 0.026), shoe="#3b3f58", shoe_kind="sandal", shoulder=(0.125, 0.98),
    arm=(0.21, 0.19), arm_r=(0.033, 0.028, 0.024), hand=0.036, sleeve="#2a9d8f", sleeve_len=1.0,
    skirt=[(0.5, 0.17, 0.14, 0.0), (0.58, 0.152, 0.122, 0.004), (0.68, 0.13, 0.1, 0.004), (0.72, 0.124, 0.094, 0.0)],
    skirt_c="#2a9d8f", pose=Pose(arm_l=(9, 4, 14), arm_r=(9, 4, 14), grip=(0.35, 0.35)), face=Face(smile=0.5),
    extras=(("hemline", "#f2b632", "skirt", 0.515),), line_k=1.05,
)
PEOPLE["tara15"] = Person(
    name="Tara, 15", skin=SKIN_TARA, hair=HAIR, top="#a8443a", bottom="#3b3f58",
    slices=[(0.8, 0.14, 0.104, 0.0), (0.9, 0.128, 0.094, 0.004), (1.04, 0.136, 0.094, 0.006), (1.14, 0.136, 0.088, -0.002),
            (1.19, 0.1, 0.07, 0.0), (1.22, 0.044, 0.04, 0.0)],
    neck=(1.27, 0.012), R=0.108, skull=(0.95, 1.02, 0.99), jaw=(0.8, 0.64, 0.82), jaw_at=(0.0, -0.56, 0.15),
    nose=(0.13, 0.14, 0.16), nose_at=(0.0, -0.36, 0.96), eye=1.08, eye_az=30, eye_el=-9, eye_round=0.92, iris="#3a2216",
    brow_w=0.85, lash=1.0, ear=0.28, mouth_el=-22, mouth_w=27, hair_style="braid", hip=(0.065, 0.78),
    leg_r=(0.055, 0.04), foot=(0.1, 0.04, 0.028), shoe="#6b4630", shoe_kind="chappal", shoulder=(0.14, 1.14),
    arm=(0.25, 0.22), arm_r=(0.036, 0.03, 0.026), hand=0.04, sleeve="#a8443a", sleeve_len=0.75, sleeve_kind="plain",
    skirt=[(0.56, 0.19, 0.15, 0.0), (0.66, 0.17, 0.13, 0.004), (0.78, 0.15, 0.112, 0.004), (0.84, 0.142, 0.106, 0.0)],
    skirt_c="#a8443a", pose=Pose(arm_l=(9, 4, 14), arm_r=(9, 10, 40), grip=(0.4, 0.75), hold=""), face=Face(smile=0.4),
    extras=(("splat", ("#f2b632", "#2a9d8f", "#f7f1e3"), ("skirt",), 4), ("hemline", "#f2b632", "skirt", 0.575)), line_k=1.0,
)
PEOPLE["tara20"] = Person(
    # twenty: a truck artist. Messy bun with brushes through it, denim dungarees splashed with paint over a white tee,
    # canvas sneakers; the red ribbon from her pigtails now tied round her wrist
    name="Tara, 20", skin=SKIN_TARA, hair=HAIR, top="#f4efe6", bottom="#4d6a96",
    slices=[(0.82, 0.148, 0.108, 0.0), (0.93, 0.134, 0.098, 0.004), (1.08, 0.138, 0.096, 0.006), (1.2, 0.142, 0.09, -0.002),
            (1.26, 0.104, 0.072, 0.0), (1.29, 0.046, 0.042, 0.0)],
    neck=(1.335, 0.012), R=0.106, skull=(0.94, 1.03, 0.99), jaw=(0.78, 0.64, 0.82), jaw_at=(0.0, -0.57, 0.15),
    nose=(0.13, 0.15, 0.17), nose_at=(0.0, -0.35, 0.96), eye=1.02, eye_az=30, eye_el=-8, eye_round=0.88, iris="#3a2216",
    brow_w=0.9, lash=1.0, ear=0.28, mouth_el=-21, mouth_w=28, hair_style="bun", hip=(0.07, 0.83),
    leg_r=(0.06, 0.044), foot=(0.105, 0.042, 0.03), shoe="#f1ebe0", shoe_kind="sneaker", shoulder=(0.145, 1.2),
    arm=(0.26, 0.23), arm_r=(0.036, 0.03, 0.026), hand=0.041, sleeve="#f4efe6", sleeve_len=0.5, sleeve_kind="rolled",
    pose=Pose(arm_l=(9, 18, 62), arm_r=(9, 4, 14), hand_in=(40, 0), grip=(0.78, 0.35), hold="brush"),
    face=Face(smile=0.55, low=0.1),
    extras=(("bib", "#4d6a96", 0.86, 1.16, 46), ("straps", "#4d6a96", 1.15, 0.95, 0.088),
            ("splat", ("#f2b632", "#d62839", "#2a9d8f", "#f07b1e"), ("top",), 11), ("wristband", 1, "#d62839")),
    line_k=1.0,
)


# ---------- drawing a person ----------


def render_premul(L: Person, view: View, face: Face | None = None, pose: Pose | None = None, box=(520, 960),
                  ss: int = 2, outline: float | None = None) -> np.ndarray:
    """The person on a transparent box: float32 premultiplied RGBA in [0, 1], with the thick outline."""
    w, h = box
    v2 = replace(view, s=view.s * ss, ox=view.ox * ss, oy=view.oy * ss, lw_cap=view.lw_cap * ss)
    surf_ = cairo.ImageSurface(cairo.FORMAT_ARGB32, w * ss, h * ss)
    ctx = cairo.Context(surf_)
    ctx.set_antialias(cairo.ANTIALIAS_BEST)
    pieces, lw = build(L, v2, face, pose)
    cast_px = 0.012 * v2.s
    paint_pieces(ctx, pieces, lw, cast_px)
    surf_.flush()
    buf = np.ndarray((h * ss, surf_.get_stride() // 4, 4), np.uint8, surf_.get_data())[:, :w * ss].astype(np.float32) / 255
    a = buf[..., 3]
    k = outline if outline is not None else lw * 2.0
    r = max(1, int(round(k)))
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    a_out = cv2.dilate(cv2.GaussianBlur(a, (0, 0), 0.6), ker)
    a_out = np.clip((a_out - 0.15) / 0.7, 0, 1)
    ink = np.array([INK[0], INK[1], INK[2]], np.float32)
    col = buf[..., 2::-1]
    out_rgb = col + ink * (a_out * (1 - a))[..., None]
    out_a = np.maximum(a, a_out)
    res = np.dstack([out_rgb, out_a]).clip(0, 1)
    if ss != 1:
        res = cv2.resize(res, (w, h), interpolation=cv2.INTER_AREA)
    return res


def render(L: Person, view: View, face: Face | None = None, pose: Pose | None = None, box=(520, 960), ss: int = 2,
           outline: float | None = None) -> np.ndarray:
    """The person on a transparent box (RGBA uint8, straight alpha), with the thick outline."""
    res = render_premul(L, view, face, pose, box, ss, outline)
    al = res[..., 3:4]
    rgb = np.where(al > 1e-4, res[..., :3] / np.maximum(al, 1e-4), 0)
    return (np.dstack([rgb, al]) * 255).clip(0, 255).astype(np.uint8)


# ---------- in a film: a person standing in a 3D world, seen by a pinhole camera ----------
# World: x right, y forward, z up (vidosuper.graphics.toon.Camera); one world unit = one metre.


def actor(L: Person, cam, root, heading: float, face: Face | None = None, pose: Pose | None = None, k: float = 1.0,
          size=(1080, 1920), ss: int = 2, lw_cap: float = 6.0, tilt=(0.0, 0.0), anchor=None):
    """The person as the camera sees it: (premultiplied RGBA patch, x0, y0), or None when off screen or behind."""
    from vidosuper.graphics.cast import stage_view, to_world
    tall = L.height + 0.12
    if cam.cam(to_world(root, heading, [0, tall * 0.5, 0], k, tilt))[2] < 0.15:
        return None
    v = stage_view(cam, root, heading, k, height=tall * 0.5, lw_cap=lw_cap, tilt=tilt, anchor=anchor)
    wide = max(0.7, tall * 0.62)
    corners = np.array([[x, y, z] for x in (-wide, wide) for y in (-0.25, tall * 1.15) for z in (-wide, wide)])
    P = v(corners)
    x0, y0 = np.floor(P.min(0)).astype(int) - 8
    x1, y1 = np.ceil(P.max(0)).astype(int) + 8
    W, H = size
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    if (x1 - x0) * (y1 - y0) > 3_000_000:
        ss = 1
    v = replace(v, ox=v.ox - x0, oy=v.oy - y0)
    return render_premul(L, v, face, pose, box=(x1 - x0, y1 - y0), ss=ss), x0, y0
