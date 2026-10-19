"""The Cheese Job cast: three mice and the cat, built once in 3D and drawn from any side.

Each character is a few smooth volumes (ellipsoids, a lathed body, tapered tubes, flat discs, ribbons) seen
through one camera (a turn and a small downward look), drawn as ink and paint:
- each piece one flat colour with one shade crescent away from the light and a thin rim toward it,
- a thin line only where a piece overlaps another (pieces that belong together, like skull and snout, merge),
- a soft drop shadow where a piece hangs over what is behind it,
- one thick outline around the whole figure.

    look = CAST["planner"]; img = render(look, View(yaw=35, s=400), Face(), box=(520, 560), foot=(260, 520))
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import cairo
import cv2
import numpy as np

from vidosuper.graphics.toon import darker, hexc, mix, spline

INK = hexc("#241b2b")
CAST_SHADOW = hexc("#2a1838")
LIGHT = np.array([-0.55, -0.83])  # toward the light, on screen (upper left)


# ---------- the camera ----------


@dataclass
class View:
    yaw: float = 0.0  # degrees the character is turned: 0 faces us, 90 faces screen right
    pitch: float = 6.0  # degrees the camera looks down on them
    s: float = 400.0  # pixels per unit (a mouse is about one unit tall)
    ox: float = 0.0  # screen position of the point between the feet
    oy: float = 0.0
    R: np.ndarray | None = None  # an explicit orientation (local axes -> camera axes), from a film camera
    k: float = 1.0  # squash (<1) and stretch (>1) about the feet, volume kept
    lw_cap: float = 99.0  # the thickest a line may get (px), for extreme close-ups

    @property
    def M(self) -> np.ndarray:
        if self.R is not None:
            return self.R
        a, p = math.radians(self.yaw), math.radians(self.pitch)
        Y = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
        P = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
        return P @ Y

    def cam(self, X) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, float))
        if self.k != 1.0:
            w = 1 / math.sqrt(self.k)
            X = X * np.array([w, self.k, w])
        return X @ self.M.T

    @property
    def yaw_eff(self) -> float:
        """How far the character is turned from facing us (degrees, + toward screen right)."""
        cd = self.M[2]
        return math.degrees(math.atan2(-cd[0], cd[2]))

    def __call__(self, X) -> np.ndarray:
        C = self.cam(X)
        return np.stack([self.ox + C[:, 0] * self.s, self.oy - C[:, 1] * self.s], 1)

    def depth(self, X) -> float:
        return float(self.cam(X)[:, 2].mean())

    def facing(self, n) -> float:
        """How much a surface normal faces the camera (1 straight at us, <0 away)."""
        return float((np.asarray(n, float) @ self.M.T)[2])


def rotm(rx: float = 0.0, ry: float = 0.0, rz: float = 0.0) -> np.ndarray:
    """Rotation (degrees): rx tips the forward axis down, ry turns it toward +x, rz rolls (counter-clockwise)."""
    a, b, c = np.radians([rx, ry, rz])
    Rx = np.array([[1, 0, 0], [0, math.cos(a), math.sin(a)], [0, -math.sin(a), math.cos(a)]])
    Ry = np.array([[math.cos(b), 0, math.sin(b)], [0, 1, 0], [-math.sin(b), 0, math.cos(b)]])
    Rz = np.array([[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    return v / (np.linalg.norm(v) + 1e-12)


def hull(pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, np.float64)
    o = pts.mean(0)
    k = max(1e-9, float(np.abs(pts - o).max()))
    return cv2.convexHull(((pts - o) / k).astype(np.float32))[:, 0].astype(np.float64) * k + o


def ccw(poly: np.ndarray) -> np.ndarray:
    x, y = poly[:, 0], poly[:, 1]
    area = 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    return poly if area >= 0 else poly[::-1]


# ---------- volumes -> screen polygons ----------


SPHERE = None


def _sphere(nu: int = 28, nv: int = 16) -> np.ndarray:
    global SPHERE
    if SPHERE is None:
        u, v = np.meshgrid(np.linspace(0, 2 * np.pi, nu, endpoint=False), np.linspace(-np.pi / 2, np.pi / 2, nv))
        SPHERE = np.stack([np.cos(v) * np.sin(u), np.sin(v), np.cos(v) * np.cos(u)], -1).reshape(-1, 3)
    return SPHERE


def ellipsoid(view: View, c, r, R=None) -> np.ndarray:
    R = np.eye(3) if R is None else R
    pts = np.asarray(c, float) + (_sphere() * np.asarray(r, float)) @ R.T
    return hull(view(pts))


def surf(c, r, R, d) -> tuple[np.ndarray, np.ndarray]:
    """A point on an ellipsoid's surface in direction d (local), and its normal."""
    d = unit(d)
    r = np.asarray(r, float)
    return np.asarray(c, float) + R @ (r * d), unit(R @ (d / r))


def sph(az: float, el: float) -> np.ndarray:
    a, e = math.radians(az), math.radians(el)
    return np.array([math.cos(e) * math.sin(a), math.sin(e), math.cos(e) * math.cos(a)])


def tube(view: View, pts3, radii, n: int = 6) -> list[np.ndarray]:
    """A tapered limb through 3D points: circles along the projected curve, joined by quads (one smooth union)."""
    pts3 = np.asarray(pts3, float)
    C = spline(pts3, closed=False, n=n) if len(pts3) > 2 else np.linspace(pts3[0], pts3[-1], n + 1)
    rr = np.interp(np.linspace(0, 1, len(C)), np.linspace(0, 1, len(radii)), radii) * view.s
    P = view(C)
    th = np.linspace(0, 2 * np.pi, 20, endpoint=False)
    ring = np.stack([np.cos(th), np.sin(th)], 1)
    out = [P[i] + rr[i] * ring for i in range(len(P))]
    for i in range(len(P) - 1):
        d = P[i + 1] - P[i]
        L = np.linalg.norm(d)
        if L < 1e-6:
            continue
        nrm = np.array([-d[1], d[0]]) / L
        out.append(np.array([P[i] + nrm * rr[i], P[i + 1] + nrm * rr[i + 1], P[i + 1] - nrm * rr[i + 1], P[i] - nrm * rr[i]]))
    return out


def disc(view: View, c, normal, up, radius, n: int = 48, notch: tuple[float, float, float] | None = None) -> np.ndarray:
    """A flat round shape (an ear) in the plane through c with this normal; `notch` = (angle deg, depth, width deg)."""
    nrm = unit(normal)
    v = unit(np.asarray(up, float) - nrm * np.dot(up, nrm))
    u = np.cross(v, nrm)
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    r = np.full(n, float(radius))
    if notch:
        ang, depth, width = notch
        dist = np.abs((np.degrees(t) - ang + 180) % 360 - 180)
        r *= 1 - depth * np.clip(1 - dist / width, 0, 1)
    pts = np.asarray(c, float) + (np.cos(t) * r)[:, None] * u + (np.sin(t) * r)[:, None] * v
    return view(pts)


def ribbon(view: View, pts3, width, side) -> np.ndarray:
    """A flat strip (a scarf end) along 3D points, `side` the direction its width lies in."""
    C = spline(np.asarray(pts3, float), closed=False, n=8)
    w = np.interp(np.linspace(0, 1, len(C)), np.linspace(0, 1, len(width)), width)
    S = np.asarray(side, float)
    S = S if S.ndim == 2 else np.tile(S, (len(C), 1))
    S = np.array([np.interp(np.linspace(0, 1, len(C)), np.linspace(0, 1, len(S)), S[:, k]) for k in range(3)]).T
    L = view(C + S * w[:, None] / 2)
    Rr = view(C - S * w[:, None] / 2)
    return np.vstack([L, Rr[::-1]])


class Body:
    """A lathed body: horizontal elliptical slices (y, rx, rz, zoff), smoothly interpolated."""

    def __init__(self, slices, n: int = 28):
        S = spline(np.asarray(slices, float), closed=False, n=n)
        self.S = S[np.argsort(S[:, 0])]

    def at(self, y: float) -> tuple[float, float, float]:
        S = self.S
        return tuple(float(np.interp(y, S[:, 0], S[:, k])) for k in (1, 2, 3))

    def point(self, y: float, az: float, out: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
        rx, rz, zo = self.at(y)
        a = math.radians(az)
        p = np.array([(rx + out) * math.sin(a), y, zo + (rz + out) * math.cos(a)])
        return p, unit([math.sin(a) / rx, 0.0, math.cos(a) / rz])

    def ring(self, y: float, out: float = 0.0, n: int = 48) -> np.ndarray:
        rx, rz, zo = self.at(y)
        t = np.linspace(0, 2 * np.pi, n, endpoint=False)
        return np.stack([(rx + out) * np.sin(t), np.full(n, y), zo + (rz + out) * np.cos(t)], 1)

    def polys(self, view: View, y0: float | None = None, y1: float | None = None, out: float = 0.0) -> list[np.ndarray]:
        ys = self.S[:, 0]
        y0 = ys[0] if y0 is None else y0
        y1 = ys[-1] if y1 is None else y1
        grid = np.linspace(y0, y1, max(2, int((y1 - y0) / 0.012) + 1))
        rings = [view(self.ring(y, out)) for y in grid]
        return [hull(np.vstack([a, b])) for a, b in zip(rings, rings[1:])]

    def front_arc(self, view: View, y: float, out: float = 0.0, n: int = 40) -> np.ndarray:
        """The near half of the slice at y as the camera sees it, left to right on screen."""
        cd = view.M[2]
        face = math.degrees(math.atan2(cd[0], cd[2]))
        rx, rz, zo = self.at(y)
        t = np.radians(np.linspace(face - 92, face + 92, n))
        pts = np.stack([(rx + out) * np.sin(t), np.full(n, y), zo + (rz + out) * np.cos(t)], 1)
        P = view(pts)
        return P if P[0, 0] <= P[-1, 0] else P[::-1]


# ---------- pieces and painting ----------


@dataclass
class Piece:
    polys: list
    color: tuple
    depth: float
    size: float = 20.0  # px: how big the shade crescent is
    line: tuple | None = None
    shade: float = 1.0
    rim: float = 1.0
    cast: float = 0.0  # drop shadow strength on what is already drawn behind it
    detail: object = None  # f(ctx): drawn on the piece, clipped to it
    over: object = None  # f(ctx): drawn after the piece, unclipped
    lw: float | None = None
    fine: bool = False  # fine strands (whiskers): left out of the thick outline


def _path(ctx, polys) -> None:
    for p in polys:
        p = ccw(np.asarray(p, float))
        if len(p) < 3:
            continue
        ctx.move_to(*p[0])
        for q in p[1:]:
            ctx.line_to(*q)
        ctx.close_path()


def paint_pieces(ctx, pieces: list[Piece], lw: float, cast_px: float, fine: bool = True) -> None:
    ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    for pc in sorted(pieces, key=lambda p: p.depth):
        if pc.fine and not fine:
            continue
        if not pc.polys:
            if pc.over:
                pc.over(ctx)
            continue
        if pc.cast:
            ctx.save()
            ctx.set_operator(cairo.OPERATOR_ATOP)
            ctx.translate(-LIGHT[0] * cast_px, -LIGHT[1] * cast_px)
            _path(ctx, pc.polys)
            ctx.set_source_rgba(*CAST_SHADOW, pc.cast)
            ctx.fill()
            ctx.restore()
        line = pc.line if pc.line is not None else darker(pc.color, 0.55, 0.1)
        _path(ctx, pc.polys)
        ctx.set_source_rgb(*line)
        ctx.set_line_width(2 * (pc.lw if pc.lw is not None else lw))
        ctx.stroke()
        _path(ctx, pc.polys)
        ctx.set_source_rgb(*pc.color)
        ctx.fill()
        ctx.save()
        _path(ctx, pc.polys)
        ctx.clip()
        if pc.shade:
            d = 0.22 * pc.size
            ctx.push_group()
            ctx.set_source_rgba(*darker(pc.color, 0.8, 0.07), pc.shade)
            ctx.paint()
            ctx.set_operator(cairo.OPERATOR_CLEAR)
            ctx.translate(LIGHT[0] * d, LIGHT[1] * d)
            _path(ctx, pc.polys)
            ctx.fill()
            ctx.pop_group_to_source()
            ctx.paint()
        if pc.rim:
            d = 0.09 * pc.size
            ctx.push_group()
            ctx.set_source_rgba(*mix(pc.color, (1, 1, 1), 0.3), 0.45 * pc.rim)
            ctx.paint()
            ctx.set_operator(cairo.OPERATOR_CLEAR)
            ctx.translate(-LIGHT[0] * d, -LIGHT[1] * d)
            _path(ctx, pc.polys)
            ctx.fill()
            ctx.pop_group_to_source()
            ctx.paint()
        if pc.detail:
            pc.detail(ctx)
        ctx.restore()
        if pc.over:
            pc.over(ctx)


def stroke_line(ctx, pts, rgb, w0: float, w1: float | None = None, a: float = 1.0) -> None:
    """A tapered ink stroke along a polyline (w0 at the start to w1 at the end)."""
    pts = np.asarray(pts, float)
    w1 = w0 if w1 is None else w1
    if len(pts) < 2:
        return
    ctx.save()
    ctx.set_source_rgba(*rgb, a)
    if abs(w1 - w0) < 0.05:
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.set_line_width(max(0.3, w0))
        ctx.move_to(*pts[0])
        for q in pts[1:]:
            ctx.line_to(*q)
        ctx.stroke()
        ctx.restore()
        return
    # a tapered stroke: one filled shape (no darker joints), round at both ends
    d = np.gradient(pts, axis=0)
    nrm = np.stack([-d[:, 1], d[:, 0]], 1) / (np.linalg.norm(d, axis=1, keepdims=True) + 1e-9)
    w = np.linspace(w0, w1, len(pts))[:, None] / 2
    ctx.new_path()
    for q in pts + nrm * w:
        ctx.line_to(*q)
    for q in (pts - nrm * w)[::-1]:
        ctx.line_to(*q)
    ctx.close_path()
    for q, r in ((pts[0], w[0, 0]), (pts[-1], w[-1, 0])):
        ctx.new_sub_path()
        ctx.arc(q[0], q[1], max(0.15, r), 0, 2 * math.pi)
    ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
    ctx.fill()
    ctx.restore()


def fill_poly(ctx, pts, rgb, a: float = 1.0) -> None:
    _path(ctx, [pts])
    ctx.set_source_rgba(*rgb, a)
    ctx.fill()


def ell2(c, rx, ry, ang: float = 0.0, n: int = 40) -> np.ndarray:
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    p = np.stack([rx * np.cos(t), ry * np.sin(t)], 1)
    ca, sa = math.cos(ang), math.sin(ang)
    return p @ np.array([[ca, sa], [-sa, ca]]) + np.asarray(c, float)


# ---------- the designs ----------


@dataclass
class Face:
    open: float = 1.0  # eye opening (0 shut .. 1.35 wide)
    lid: float = 0.0  # upper lids lowered (0..1)
    lid_tilt: float = 0.0  # + lids slope down toward the nose (cross, sly), - (sad)
    low: float = 0.0  # lower lids raised (smug, squint)
    gaze: tuple = (0.0, 0.0)  # pupils, -1..1 (screen x, up)
    pupil: float = 1.0
    brow: float = 0.0  # brows raised (-1 .. 1)
    brow_tilt: float = 0.0  # + inner ends down (determined, cross), - inner ends up (worried)
    smile: float = 0.3
    sneer: float = 0.0  # the right corner pulled up (a smirk)
    mouth: float = 0.0  # mouth open (0..1)
    shut: str = ""  # both eyes: "happy" (closed upward arcs) or "squeeze" (> <); "wink": far eye winks
    blush: float = 0.0
    sweat: bool = False
    ears: float = 0.0  # ears drooped (0..1)
    tilt: float = 0.0  # head roll (degrees)
    nod: float = 0.0  # head tipped down (+) or back (-)
    turn: float = 0.0  # head turned toward screen right (degrees)
    teeth: float = 1.0
    nostrils: float = 0.0  # flared (the sneeze)


@dataclass
class Pose:
    arm_l: tuple = (12.0, 6.0, 25.0)  # (out from the side, forward swing, elbow bend) degrees, the screen-left arm in front view
    arm_r: tuple = (12.0, 6.0, 25.0)
    hand_in: tuple = (0.0, 0.0)  # forearm turned in toward the body (degrees), l / r
    lean: float = 0.0  # body tipped forward (degrees)
    feet: float = 1.0  # stance width
    knee: float = 4.0
    lean_side: float = 0.0  # body tipped toward screen right (degrees, front view)
    hand_l: tuple | None = None  # a wrist target (local, after the lean): the arm reaches it
    hand_r: tuple | None = None
    foot_l: tuple = (0.0, 0.0, 0.0)  # moves of the feet from where they stand (local)
    foot_r: tuple = (0.0, 0.0, 0.0)
    toe: tuple = (0.0, 0.0)  # feet tipped toe-down (degrees)
    ears: tuple = (0.0, 0.0)  # extra ear swing outward/down (degrees): the springs
    tail_wave: float = 0.0  # tail sway (units across)
    tail_phase: float = 0.0
    tail_lift: float = 0.0  # tail raised (degrees)
    grip: tuple = (0.0, 0.0)  # fingers curled (0 open .. 1 fist)
    rise: float = 0.0  # the body raised (+) or lowered (-) over its feet: bounce, crouch


@dataclass
class Look:
    name: str
    kind: str  # "mouse" or "cat"
    fur: str
    ear_in: str
    nose: str
    feet_c: str
    tail_c: str
    top: str  # clothing on the body
    legs_c: str
    sleeve: str  # sleeve colour ("" = bare fur arms)
    sleeve_len: float  # 0..1 of the arm
    slices: list  # body: (y, rx, rz, zoff)
    neck: tuple  # (y, z): where the head sits
    R: float  # skull radius
    skull: tuple = (1.0, 1.0, 1.0)  # skull shape
    snout: tuple = (1.0, 1.0, 1.0)  # (length, width, height)
    snout_drop: float = 0.25
    eye: float = 1.0
    eye_az: float = 25.0
    eye_el: float = 10.0
    ear: float = 0.75
    ear_pos: tuple = (0.62, 0.74, -0.12)
    notch: float = 0.0
    hip: tuple = (0.07, 0.36)  # leg spacing, hip height
    leg_r: tuple = (0.035, 0.028)
    foot: tuple = (0.09, 0.045, 0.03)  # half length, half width, half height
    shoulder: tuple = (0.1, 0.74)  # x, y
    arm: tuple = (0.16, 0.15)
    arm_r: tuple = (0.032, 0.027, 0.024)  # shoulder, elbow, wrist
    hand: float = 0.032
    tail: list = field(default_factory=list)
    tail_r: tuple = (0.022, 0.006)
    pose: Pose = field(default_factory=Pose)
    face: Face = field(default_factory=Face)
    extras: tuple = ()
    whisker: str = "#5b4d5e"
    brow_c: str = ""
    eye_c: str = "#1e1622"  # pupil
    iris: str = ""  # a coloured iris (the cat)


def ik(S, T, l1: float, l2: float, pole) -> np.ndarray:
    """The middle joint of a two-bone limb from S reaching for T, bending toward pole."""
    S, T = np.asarray(S, float), np.asarray(T, float)
    d = T - S
    L = float(np.linalg.norm(d))
    L = min(max(L, abs(l1 - l2) + 1e-4), l1 + l2 - 1e-4)
    u = unit(d)
    a = (l1 * l1 - l2 * l2 + L * L) / (2 * L)
    h = math.sqrt(max(0.0, l1 * l1 - a * a))
    pv = np.asarray(pole, float)
    rest = pv - u * np.dot(pv, u)
    pv = unit(rest) if np.linalg.norm(rest) > 1e-6 else unit(np.cross(u, [1.0, 0, 0]))
    return S + u * a + pv * h


def _arm_joints(L: Look, side: int, a: tuple, turn_in: float):
    """Shoulder, elbow and wrist for one arm. side -1 = screen left in front view (character's right)."""
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


def build(L: Look, view: View, face: Face | None = None, pose: Pose | None = None) -> tuple[list[Piece], float]:
    """Every piece of the character for this camera. Returns (pieces, line width px)."""
    F = face or L.face
    P = pose or L.pose
    s = view.s
    fur, top = hexc(L.fur), hexc(L.top)
    lw = min(view.lw_cap, max(1.2, 0.0042 * s * (1.0 if L.kind == "mouse" else 0.75)))
    pieces: list[Piece] = []
    body = Body(L.slices)
    lean = rotm(P.lean, 0, -P.lean_side)
    hip_y = L.hip[1]

    def B(p):  # body lean about the hips, then raised or lowered over the feet
        p = np.asarray(p, float)
        return (p - [0, hip_y, 0]) @ lean.T + [0, hip_y + P.rise, 0]

    torso_depth = view.depth(B([0, (L.slices[0][0] + L.slices[-1][0]) / 2, 0])) + 0.01

    # --- body ---
    def body_polys(out=0.0, y0=None, y1=None):
        if not P.lean:
            return body.polys(view, y0, y1, out)
        ys = body.S[:, 0]
        y0 = ys[0] if y0 is None else y0
        y1 = ys[-1] if y1 is None else y1
        grid = np.linspace(y0, y1, max(2, int((y1 - y0) / 0.012) + 1))
        rings = [view(B(body.ring(y, out))) for y in grid]
        return [hull(np.vstack([a, b])) for a, b in zip(rings, rings[1:])]

    wid = max(r[1] for r in L.slices) * 2 * s

    def body_detail(ctx):
        for ex in L.extras:
            if ex[0] == "stripes":
                _, col, y0, y1, step, frac = ex
                y = y0
                while y < y1:
                    a, b = body.front_arc(view, y + step * frac), body.front_arc(view, y)
                    a[0, 0] -= 0.05 * s
                    a[-1, 0] += 0.05 * s
                    b[0, 0] -= 0.05 * s
                    b[-1, 0] += 0.05 * s
                    fill_poly(ctx, np.vstack([a, b[::-1]]), hexc(col))
                    y += step
            elif ex[0] == "placket":
                _, col, y0, y1, buttons, bc = ex
                ys = np.linspace(y0, y1, 24)
                pts, vis = [], []
                for y in ys:
                    p, n = body.point(y, 0.0)
                    pts.append(B(p))
                    vis.append(view.facing(n) > 0.05)
                if any(vis):
                    stroke_line(ctx, view(np.array(pts))[np.array(vis)], hexc(col), lw * 1.3)
                for y in buttons:
                    p, n = body.point(y, 0.0, 0.004)
                    if view.facing(n) > 0.1:
                        c = view(B(p))[0]
                        k = view.facing(n) ** 0.6
                        fill_poly(ctx, ell2(c, 0.026 * s * k, 0.011 * s, 0), hexc(bc))
                        stroke_line(ctx, ell2(c, 0.026 * s * k, 0.011 * s, 0)[list(range(40)) + [0]], darker(hexc(bc), 0.6), lw * 0.7)
            elif ex[0] == "hem":
                _, col, y = ex
                arc = body.front_arc(view, y)
                stroke_line(ctx, arc, hexc(col), lw * 1.2)
            elif ex[0] == "straps":
                _, col, az, y0, y1, w = ex
                for sd in (-1, 1):
                    pts, vis = [], []
                    for y in np.linspace(y1, y0, 16):
                        p, n = body.point(y, sd * az, 0.003)
                        pts.append(B(p))
                        vis.append(view.facing(n) > 0.0)
                    pts = view(np.array(pts))
                    vis = np.array(vis)
                    if vis.sum() > 1:
                        stroke_line(ctx, pts[vis], darker(hexc(col), 0.6), w * s + 2 * lw)
                        stroke_line(ctx, pts[vis], hexc(col), w * s)

    pieces.append(Piece(body_polys(), top, torso_depth, size=wid * 0.55, detail=body_detail))

    # --- legs and feet ---
    J: dict = {}
    for side, off, toe in ((-1, P.foot_l, P.toe[0]), (1, P.foot_r, P.toe[1])):
        hx = side * L.hip[0] * P.feet
        Hp = B([hx, hip_y + 0.02, 0.0])
        A0 = np.array([side * L.hip[0] * P.feet * 1.15, L.foot[2] * 1.6, 0.01])
        A = A0 + np.asarray(off, float)
        rest = float(np.linalg.norm(np.array([hx, hip_y + 0.02, 0.0]) - A0)) * 1.03
        if np.linalg.norm(A - Hp) > rest * 0.99:  # straight: the slight forward knee
            K = (Hp + A) / 2 + [0, 0, 0.02 * P.knee / 4]
        else:
            K = ik(Hp, A, rest * 0.5, rest * 0.5, [0, 0.15, 1.0])
        legs = tube(view, [Hp, K, A], [L.leg_r[0], L.leg_r[1] + (L.leg_r[0] - L.leg_r[1]) * 0.35, L.leg_r[1]])
        pieces.append(Piece(legs, hexc(L.legs_c), torso_depth - 0.2 + 0.05 * view.depth(K), size=L.leg_r[0] * 2 * s))
        J["foot_l" if side < 0 else "foot_r"] = A
        fR = rotm(toe, side * 14, 0)
        fc = A + fR @ np.array([0, -L.foot[2] * 0.6, L.foot[0] * 0.6])
        foot = [ellipsoid(view, fc, (L.foot[1], L.foot[2], L.foot[0]), fR)]
        if L.kind == "mouse":  # three little toes at the front
            for k in (-1, 0, 1):
                tc = fc + fR @ np.array([k * L.foot[1] * 0.55, -L.foot[2] * 0.25, L.foot[0] * 0.9])
                foot.append(ellipsoid(view, tc, (L.foot[1] * 0.36, L.foot[2] * 0.7, L.foot[1] * 0.42), fR))
        pieces.append(Piece(foot, hexc(L.feet_c), torso_depth - 0.1 + 0.05 * view.depth(fc), size=L.foot[2] * 3 * s))

    # --- tail ---
    if L.tail:
        base = np.asarray(L.tail[0], float)
        Rl = rotm(-P.tail_lift, 0, 0)
        tp = []
        for i, p in enumerate(L.tail):
            q = base + Rl @ (np.asarray(p, float) - base)
            u = i / (len(L.tail) - 1)
            q = q + np.array([P.tail_wave * u * math.sin(P.tail_phase - 2.2 * u), 0.0, 0.0])
            tp.append(B(q))
        J["tail_tip"] = tp[-1]
        pieces.append(Piece(tube(view, tp, list(np.linspace(L.tail_r[0], L.tail_r[1], 6)), n=8), hexc(L.tail_c),
                            view.depth(tp[1]) - 0.05, size=L.tail_r[0] * 2.5 * s, cast=0.0))

    # --- arms and hands ---
    for side, a, tin, tgt, g in ((-1, P.arm_l, P.hand_in[0], P.hand_l, P.grip[0]),
                                 (1, P.arm_r, P.hand_in[1], P.hand_r, P.grip[1])):
        S, E, W, d2 = _arm_joints(L, side, a, tin)
        S, E, W = B(S), B(E), B(W)
        d2 = unit(lean @ d2)
        if tgt is not None:  # reach for a point: the elbow goes back and out
            W = np.asarray(tgt, float)
            E = ik(S, W, L.arm[0], L.arm[1], [side * 0.6, -0.3, -1.0])
            d2 = unit(W - E)
        dep = view.depth(S) + 0.15 * (view.depth(W) - view.depth(S))
        arm = tube(view, [S, E, W], list(L.arm_r))
        sl = L.sleeve
        if sl and L.sleeve_len >= 0.99:
            pieces.append(Piece(arm, hexc(sl), dep, size=L.arm_r[0] * 2 * s, cast=0.18))
            cuff = tube(view, [E + (W - E) * 0.82, W + (W - E) * 0.02], [L.arm_r[2] * 1.18, L.arm_r[2] * 1.18])
            pieces.append(Piece(cuff, darker(hexc(sl), 0.86, 0.04), dep + 0.002, size=L.arm_r[2] * 2 * s))
        else:
            pieces.append(Piece(arm, fur, dep, size=L.arm_r[1] * 2 * s, cast=0.18))
            if sl:
                mid = S + (E - S) * (L.sleeve_len * 2)
                sleeve = tube(view, [S + [0, 0.01, 0], mid], [L.arm_r[0] * 1.32, L.arm_r[0] * 1.28])
                pieces.append(Piece(sleeve, hexc(sl), dep + 0.002, size=L.arm_r[0] * 2.6 * s, cast=0.15,
                                    detail=_sleeve_band(view, S, mid, L, s)))
        # hand: palm, three fingers and a thumb
        hc = W + d2 * L.hand * 0.9
        hand = [ellipsoid(view, hc, (L.hand * 0.95, L.hand, L.hand * 0.8), np.eye(3))]
        side_v = unit(np.cross(d2, [0, 0, 1])) if abs(d2[2]) < 0.95 else np.array([1.0, 0, 0])
        palm_n = unit(np.cross(d2, side_v)) * (1 if side > 0 else -1)
        for k in (-1, 0, 1):
            base = hc + d2 * L.hand * 0.7 + side_v * k * L.hand * 0.55
            tip = (base + d2 * L.hand * (0.9 if L.kind == "mouse" else 0.7) * (1 - 0.55 * g) + np.array([0, 0, 0.3 * L.hand])
                   + palm_n * L.hand * 0.7 * g)
            hand += tube(view, [base, tip], [L.hand * 0.32, L.hand * 0.27], n=2)
        th0 = hc - side * np.array([L.hand * 0.7, 0, 0]) * 0 + np.array([0, 0, L.hand * 0.6]) - side_v * side * L.hand * 0.0
        hand += tube(view, [th0, th0 + np.array([-side * L.hand * 0.5, L.hand * 0.2, L.hand * 0.55])],
                     [L.hand * 0.3, L.hand * 0.26], n=2)
        pieces.append(Piece(hand, fur if L.kind == "mouse" else fur, dep + 0.004, size=L.hand * 2 * s, cast=0.15))
        J["hand_l" if side < 0 else "hand_r"] = hc + d2 * L.hand * 0.6

    # --- head ---
    pieces += _head(L, view, F, B, lw, P)
    for ex in L.extras:
        fn = EXTRA.get(ex[0])
        if fn:
            pieces += fn(L, view, F, B, body, ex, lw)
    Hd = Head(L, F, B)
    J["head"] = Hd.c
    J["nose"] = Hd.snout_pt(0, 4)[0]
    J["top"] = Hd.c + Hd.N([0, 1, 0]) * L.R * 1.0
    J["back"] = B([0.0, L.shoulder[1] - 0.04, -body.at(L.shoulder[1] - 0.04)[1]])
    J["chest"] = B([0.0, L.shoulder[1] - 0.08, body.at(L.shoulder[1] - 0.08)[1]])
    build.joints = J
    return pieces, lw


def _sleeve_band(view, S, mid, L, s):
    def f(ctx):
        col = None
        for ex in L.extras:
            if ex[0] == "stripes":
                col = hexc(ex[1])
        if col is None:
            return
        for k in (0.45, 0.78):
            a, b = view(S + (mid - S) * k)[0], view(S + (mid - S) * (k + 0.13))[0]
            d = b - a
            n = np.array([-d[1], d[0]]) / (np.linalg.norm(d) + 1e-9) * L.arm_r[0] * 2 * s
            fill_poly(ctx, np.array([a + n, b + n, b - n, a - n]), col)
    return f


class Head:
    """The head's frame: every head point goes through `X` (roll, nod and turn about the neck)."""

    def __init__(self, L: Look, F: Face, B):
        self.L = L
        self.pivot = B([0.0, L.neck[0], L.neck[1]])
        self.Rh = rotm(F.nod, F.turn, -F.tilt)
        self.B = B
        self.c_local = np.array([0.0, L.neck[0] + L.R * 0.88 * L.skull[1], L.neck[1]])
        sl, sw, sh = L.snout
        R = L.R
        self.skull_r = np.array([R * L.skull[0], R * L.skull[1], R * L.skull[2]])
        self.sn_local = self.c_local + np.array([0.0, -L.snout_drop * R, 0.62 * R * sl])
        self.sn_r = np.array([0.52 * R * sw, 0.47 * R * sh, 0.72 * R * sl])
        self.sn_R = rotm(12, 0, 0)

    def X(self, p):
        p = np.asarray(p, float)
        piv_local = np.array([0.0, self.L.neck[0], self.L.neck[1]])
        return self.B(piv_local + (p - piv_local) @ self.Rh.T)

    def N(self, n):
        return unit(self.Rh @ np.asarray(n, float))

    @property
    def c(self):
        return self.X(self.c_local)

    @property
    def R(self):
        return self.Rh

    def skull_pt(self, az, el, out=0.0):
        p, n = surf(self.c_local, self.skull_r + out, np.eye(3), sph(az, el))
        return self.X(p), self.N(n)

    def snout_pt(self, az, el, out=0.0):
        p, n = surf(self.sn_local, self.sn_r + out, self.sn_R, sph(az, el))
        return self.X(p), self.N(n)


def _head(L: Look, view: View, F: Face, B, lw: float, P: Pose | None = None) -> list[Piece]:
    H = Head(L, F, B)
    s = view.s
    R = L.R
    fur = hexc(L.fur)
    Rpx = R * s
    hd = view.depth(H.c) + 0.5  # the head sits over the body
    head_polys = [ellipsoid(view, H.c, H.skull_r, H.Rh)]
    sn_c = H.X(H.sn_local)
    head_polys.append(ellipsoid(view, sn_c, H.sn_r, H.Rh @ H.sn_R))
    if L.kind == "cat":  # cheek fluff
        for sd in (-1, 1):
            cc = H.X(H.c_local + np.array([sd * R * 0.62, -R * 0.42, R * 0.18]))
            head_polys.append(ellipsoid(view, cc, (R * 0.5, R * 0.36, R * 0.42), H.Rh @ rotm(0, 0, sd * 18)))
    snout_poly = head_polys[1]
    pieces: list[Piece] = []

    # ears
    for side in (-1, 1):
        ex, ey, ez = L.ear_pos
        base_local = H.c_local + np.array([side * R * ex, R * ey, R * ez])
        droop = F.ears * 55 + ((P.ears[0] if side < 0 else P.ears[1]) if P is not None else 0.0)
        Re = rotm(0, side * 20, side * droop)
        nrm = H.N(Re @ unit([side * 0.32, 0.12, 1.0]))
        up = H.N(Re @ np.array([side * 0.25, 1.0, 0.0]))
        ec = H.X(base_local + Re @ np.array([side * R * 0.1, R * L.ear * 0.55, 0.0]))
        rel = view.depth(ec) - view.depth(H.c)
        dep = hd + rel * 1.0 + (0.002 if rel > 0 else -0.002)
        if L.kind == "cat":
            ear = _cat_ear(view, ec, nrm, up, R * L.ear)
            inner = _cat_ear(view, ec + nrm * R * 0.03 - up * R * L.ear * 0.08, nrm, up, R * L.ear * 0.62)
        else:
            notch = (side * 0 + (35 if side > 0 else 145), L.notch, 26) if (L.notch and side > 0) else None
            ear = disc(view, ec, nrm, up, R * L.ear, notch=notch)
            inner = disc(view, ec + nrm * R * 0.02 - up * R * L.ear * 0.08, nrm, up, R * L.ear * 0.7, notch=notch)
        front = view.facing(nrm) > 0.05
        col_in = hexc(L.ear_in)

        def det(ctx, inner=inner, front=front, col_in=col_in):
            if front:
                fill_poly(ctx, inner, col_in)
                fill_poly(ctx, inner, darker(col_in, 0.8), 0.0)
        pieces.append(Piece([ear], fur, dep, size=R * L.ear * 1.3 * s, detail=det, cast=0.12 if rel > 0 else 0.0))

    # the face, drawn on the head (clipped to it)
    def face_detail(ctx):
        _blush(ctx, L, view, H, F, s)
        _mouth(ctx, L, view, H, F, s, lw)
        _eyes(ctx, L, view, H, F, s, lw, snout_poly)

    def face_over(ctx):
        _nose(ctx, L, view, H, F, s, lw)
        if F.sweat:
            p, n = H.skull_pt(-40 if view.yaw_eff < 45 else 40, 30, R * 0.15)
            c = view(p)[0]
            drop = np.array([[0, -1.0], [0.55, 0.15], [0.42, 0.62], [0, 0.85], [-0.42, 0.62], [-0.55, 0.15]]) * Rpx * 0.22 + c
            drop = spline(drop, True, 6)
            fill_poly(ctx, drop, hexc("#8fd0f0"))
            stroke_line(ctx, np.vstack([drop, drop[:1]]), hexc("#3c7aa6"), lw * 0.9)
            fill_poly(ctx, ell2(c + [-Rpx * 0.04, Rpx * 0.06], Rpx * 0.03, Rpx * 0.05), (1, 1, 1))

    pieces.append(Piece(head_polys, fur, hd, size=Rpx * 1.5, detail=face_detail, cast=0.22))
    pieces.append(Piece([], fur, hd + 0.3, over=face_over))
    pieces.append(Piece([], fur, hd - 0.003, fine=True, over=lambda ctx: _whiskers(ctx, L, view, H, F, s, lw, near=False)))
    pieces.append(Piece([], fur, hd + 0.29, fine=True, over=lambda ctx: _whiskers(ctx, L, view, H, F, s, lw, near=True)))
    return pieces


def _cat_ear(view, c, nrm, up, size):
    nrm = unit(nrm)
    v = unit(np.asarray(up, float) - nrm * np.dot(up, nrm))
    u = np.cross(v, nrm)
    shape = np.array([[-0.62, -0.55], [-0.5, 0.05], [-0.22, 0.7], [0.0, 1.0], [0.2, 0.72], [0.52, 0.1], [0.66, -0.55], [0.0, -0.7]])
    pts2 = spline(shape, True, 6) * size
    pts = c + pts2[:, :1] * u + pts2[:, 1:] * v
    return view(pts)


def _eye_frame(view: View, H: Head, L: Look, side: int, s: float):
    p, n = H.skull_pt(side * L.eye_az, L.eye_el)
    f = view.facing(n)
    c = view(p)[0]
    inner = view(H.X(H.c_local) * 0 + p - H.N([side * 0.02, 0, 0]))[0] - c
    return p, n, f, c, (1 if inner[0] > 0 else -1)


def _eyes(ctx, L: Look, view: View, H: Head, F: Face, s: float, lw: float, snout_poly) -> None:
    R = L.R
    eyes = []
    for side in (-1, 1):
        p, n, f, c, inner = _eye_frame(view, H, L, side, s)
        eyes.append((view.depth(p), side, p, n, f, c, inner))
    eyes.sort()
    fur = hexc(L.fur)
    for i, (dep, side, p, n, f, c, inner) in enumerate(eyes):
        if f < 0.12:
            continue
        far = i == 0 and abs(view.yaw_eff + F.turn) > 12
        k = min(1.0, f) ** 0.75
        ew = R * 0.2 * L.eye * s * k
        eh = R * 0.27 * L.eye * s * (1.0 if L.kind == "mouse" else 0.8)
        # the eye leans with the head's roll
        ang = math.radians(F.tilt) * 0.6 + (inner * math.radians(10) if L.kind == "cat" else 0.0)
        c = c + np.array([0, -eh * 0.05])
        ctx.save()
        if far:  # tucked behind the bridge of the snout
            ctx.rectangle(-1e5, -1e5, 2e5, 2e5)
            _path(ctx, [snout_poly])
            ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            ctx.clip()
            ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
        shut = F.shut if not (F.shut == "wink" and not far) else ""
        if F.shut == "wink":
            shut = "happy" if far else ""
        brow_c = hexc(L.brow_c) if L.brow_c else darker(fur, 0.5, 0.12)
        if shut == "happy":
            t = np.linspace(-1, 1, 15)
            pts = np.stack([c[0] + t * ew * 0.95, c[1] + eh * 0.1 - (1 - t ** 2) * eh * 0.45], 1)
            stroke_line(ctx, pts, INK, lw * 2.1)
        elif shut == "squeeze":
            pts = np.array([[c[0] - inner * ew * 0.9, c[1] - eh * 0.45], [c[0] + inner * ew * 0.55, c[1]],
                            [c[0] - inner * ew * 0.9, c[1] + eh * 0.45]])
            stroke_line(ctx, pts, INK, lw * 2.1)
        else:
            h = eh * F.open
            if L.kind == "cat":
                t = np.linspace(0, 2 * np.pi, 48, endpoint=False)
                shape = np.stack([np.cos(t) * ew * 1.05, np.sin(t) * h * (0.78 + 0.22 * np.cos(t) * inner)], 1)
            else:
                shape = ell2((0, 0), ew, h, 0, 48)
            ca, sa = math.cos(ang), math.sin(ang)
            shape = shape @ np.array([[ca, sa], [-sa, ca]]) + c
            ctx.save()
            _path(ctx, [shape])
            ctx.clip()
            ctx.set_source_rgb(*hexc("#fbf8f1"))
            ctx.paint()
            # pupil (follows the gaze and the way the head faces)
            nx = view.cam(n)[0, 0]
            g = np.array([F.gaze[0] * ew * 0.42 + nx * ew * 0.3, -F.gaze[1] * h * 0.32 + eh * 0.04])
            pc = c + g
            if L.iris:
                ir = ew * 0.82 * (0.85 + 0.15 * F.pupil)
                fill_poly(ctx, ell2(pc, ir * k ** 0.3, ir * 1.02), hexc(L.iris))
                fill_poly(ctx, ell2(pc + [0, ir * 0.35], ir * 0.8 * k ** 0.3, ir * 0.55), mix(hexc(L.iris), (1, 1, 0.7), 0.35), 0.6)
                slit = ir * (0.16 + 0.55 * max(0.0, F.pupil - 1.0) * 2)
                fill_poly(ctx, ell2(pc, slit * k ** 0.3, ir * 0.88), hexc(L.eye_c))
            else:
                pr = ew * 0.66 * F.pupil
                fill_poly(ctx, ell2(pc, pr * k ** 0.3, pr * 1.12), hexc(L.eye_c))
            hl = ew * 0.24
            fill_poly(ctx, ell2(pc + [-ew * 0.22, -h * 0.28], hl, hl * 1.1), (1, 1, 1))
            fill_poly(ctx, ell2(pc + [ew * 0.2, h * 0.22], hl * 0.45, hl * 0.45), (1, 1, 1), 0.9)
            # lids: the upper one comes down (tilted toward the nose), the lower one comes up
            top_y = -h * (1 - 2 * F.lid)
            lid_c = darker(fur, 0.92, 0.04)
            for sgn, amt in ((-1, F.lid), (1, F.low)):
                if amt <= 0.001 and not (sgn < 0 and F.lid_tilt):
                    continue
                yl = (-h + 2 * h * amt) if sgn < 0 else (h - 2 * h * amt)
                tl = F.lid_tilt * h * 0.55 if sgn < 0 else 0.0
                x0, x1 = -ew * 1.4, ew * 1.4
                yi = yl + tl * 1.0  # at the inner end
                yo = yl - tl * 0.6
                xi, xo = (x1, x0) if inner > 0 else (x0, x1)
                edge = np.array([[xo, yo], [xi, yi]])
                far_y = -h * 3 if sgn < 0 else h * 3
                poly = np.array([edge[0], edge[1], [xi, far_y], [xo, far_y]])
                poly = poly @ np.array([[ca, sa], [-sa, ca]]) + c
                fill_poly(ctx, poly, lid_c)
                e2 = edge @ np.array([[ca, sa], [-sa, ca]]) + c
                stroke_line(ctx, e2, INK, lw * (1.8 if sgn < 0 else 1.0))
            ctx.restore()
            stroke_line(ctx, np.vstack([shape, shape[:1]]), INK, lw * 1.25)
            if L.kind == "cat" or F.lid > 0.25:  # a lash line on top
                topi = shape[np.argsort(shape[:, 1])[:20]]
                topi = topi[np.argsort(topi[:, 0])]
                stroke_line(ctx, topi, INK, lw * 1.6)
        # brow
        pb, nb = H.skull_pt(side * (L.eye_az + 2), L.eye_el + 26 + F.brow * 7, R * 0.02)
        if view.facing(nb) > 0.1:
            cb = view(pb)[0] + np.array([0, -F.brow * eh * 0.25])
            bw = ew * 1.15
            tilt = F.brow_tilt * eh * 0.35
            pts = np.array([[cb[0] - inner * bw, cb[1] - tilt * 0.4], [cb[0], cb[1] - abs(F.brow_tilt) * eh * 0.08],
                            [cb[0] + inner * bw * 0.9, cb[1] + tilt]])
            pts = spline(pts, False, 5)
            stroke_line(ctx, pts, brow_c, lw * 1.2, lw * 2.8)
        ctx.restore()


def _mouth_curves(L: Look, H: Head, F: Face):
    R = L.R
    t = np.linspace(-1, 1, 25)
    pts, nrm = [], []
    wide = 52 if L.kind == "mouse" else 58
    for x in t:
        p, n = H.snout_pt(x * wide, -40 - (6 if L.kind == "cat" else 0))
        pts.append(p)
        nrm.append(n)
    pts = np.array(pts)
    up = H.N([0, 1, 0])
    lift = F.smile * R * 0.16 * t ** 2 + F.sneer * R * 0.14 * np.clip(t, 0, 1) ** 1.5
    if L.kind == "cat":
        lift = lift - R * 0.05 * np.sin(np.pi * np.abs(t)) * (1 - F.mouth)
    upper = pts + up * lift[:, None]
    drop = F.mouth * R * 0.42 * (1 - t ** 2) ** 0.9 * (1 + 0.2 * F.smile)
    lower = upper - up * drop[:, None]
    return t, upper, lower, np.array(nrm)


def _mouth(ctx, L: Look, view: View, H: Head, F: Face, s: float, lw: float) -> None:
    t, upper, lower, nrm = _mouth_curves(L, H, F)
    vis = np.array([view.facing(n) > -0.05 for n in nrm])
    if vis.sum() < 2:
        return
    U, Lo = view(upper)[vis], view(lower)[vis]
    tv = t[vis]
    R = L.R
    if F.mouth > 0.04:
        poly = np.vstack([U, Lo[::-1]])
        fill_poly(ctx, poly, hexc("#4a1f2c"))
        ctx.save()
        _path(ctx, [poly])
        ctx.clip()
        mid = Lo[len(Lo) // 2]
        fill_poly(ctx, ell2(mid + [0, -R * s * 0.02], R * s * 0.3, R * s * 0.15 * (0.5 + F.mouth)), hexc("#e0707f"))
        if L.kind == "mouse" and F.teeth:
            c = U[np.argmin(np.abs(tv))]
            for k in (-1, 1):
                tw, th = R * s * 0.085, R * s * 0.15
                fill_poly(ctx, np.array([[c[0] + k * 0.005 * s * 0 + (k - 1) / 2 * tw * 2 + tw * 0.04, c[1] - 2],
                                         [c[0] + (k + 1) / 2 * tw * 2 - tw * 0.04 - tw * (k > 0) * 0, c[1] - 2],
                                         [c[0] + (k + 1) / 2 * tw * 2 - tw * 0.1, c[1] + th],
                                         [c[0] + (k - 1) / 2 * tw * 2 + tw * 0.1, c[1] + th]]), (1, 0.99, 0.95))
        if L.kind == "cat":
            for k in (-1, 1):
                c = U[np.argmin(np.abs(tv - k * 0.45))]
                fill_poly(ctx, np.array([c + [-R * s * 0.05, -1], c + [R * s * 0.05, -1], c + [0, R * s * 0.13]]), (1, 0.99, 0.95))
        ctx.restore()
        stroke_line(ctx, np.vstack([poly, poly[:1]]), INK, lw * 1.3)
    else:
        stroke_line(ctx, U, INK, lw * 1.6)
        if L.kind == "mouse" and F.teeth and F.smile > -0.2:
            c = U[np.argmin(np.abs(tv))]
            tw, th = R * s * 0.075, R * s * 0.11
            for k in (-1, 1):
                x0 = c[0] + (0 if k > 0 else -tw)
                rect = np.array([[x0 + 0.5, c[1]], [x0 + tw - 0.5, c[1]], [x0 + tw - 0.8, c[1] + th], [x0 + 0.8, c[1] + th]])
                fill_poly(ctx, rect, (1, 0.99, 0.95))
                stroke_line(ctx, np.vstack([rect[1:], rect[:1]]), INK, lw * 0.8)
    # the line from the nose down to the mouth
    p0, n0 = H.snout_pt(0, -8)
    if view.facing(n0) > 0.0:
        c0 = view(p0)[0]
        c1 = U[np.argmin(np.abs(tv))]
        stroke_line(ctx, np.array([c0, c1]), INK, lw * 1.1)
    # corner dimples on a smile
    if F.smile > 0.35 and F.mouth < 0.2:
        for end in (0, -1):
            if (end == 0 and tv[0] < -0.9) or (end == -1 and tv[-1] > 0.9):
                e = U[end]
                d = U[end] - U[end + (1 if end == 0 else -1)]
                d = d / (np.linalg.norm(d) + 1e-9)
                stroke_line(ctx, np.array([e - d * 2, e + d * R * s * 0.05 + np.array([0, -R * s * 0.04])]), INK, lw * 1.1)


def _nose(ctx, L: Look, view: View, H: Head, F: Face, s: float, lw: float) -> None:
    R = L.R
    p, n = H.snout_pt(0, 4, 0.0)
    if view.facing(n) < -0.6:
        return
    c = view(p)[0]
    size = R * s * (0.14 if L.kind == "mouse" else 0.1) * (1 + 0.25 * F.nostrils)
    col = hexc(L.nose)
    if L.kind == "cat":
        k = max(0.35, view.facing(n))
        tri = spline(np.array([[-1.1 * k, -0.55], [1.1 * k, -0.55], [0, 0.6]]) * size + c, True, 6)
        fill_poly(ctx, tri, col)
        stroke_line(ctx, np.vstack([tri, tri[:1]]), INK, lw * 1.0)
        fill_poly(ctx, ell2(c + [-size * 0.3, -size * 0.25], size * 0.35, size * 0.18), mix(col, (1, 1, 1), 0.5), 0.8)
        return
    shape = ell2(c, size * 1.15, size)
    fill_poly(ctx, shape, col)
    ctx.save()
    _path(ctx, [shape])
    ctx.clip()
    fill_poly(ctx, ell2(c + [size * 0.35, size * 0.4], size * 1.0, size * 0.8), darker(col, 0.75), 0.9)
    ctx.restore()
    stroke_line(ctx, np.vstack([shape, shape[:1]]), INK, lw * 1.1)
    fill_poly(ctx, ell2(c + [-size * 0.35, -size * 0.35], size * 0.32, size * 0.22), (1, 1, 1), 0.85)


def _whiskers(ctx, L: Look, view: View, H: Head, F: Face, s: float, lw: float, near: bool) -> None:
    R = L.R
    col = hexc(L.whisker)
    for side in (-1, 1):
        p, n = H.snout_pt(side * 58, -6)
        f = view.facing(n)
        if near != (f >= 0):
            continue
        for k, (dy, ang) in enumerate(((0.06, 10), (0.0, 0), (-0.06, -12))):
            base = p + H.Rh @ np.array([0, dy * R, 0])
            d = H.N(unit([side * 1.0, math.tan(math.radians(ang + F.smile * 6)), -0.15]))
            pts = np.array([base + d * R * (0.95 if L.kind == "mouse" else 1.25) * u + H.Rh @ np.array([0, -0.08 * R * u * u, 0])
                            for u in np.linspace(0, 1, 6)])
            stroke_line(ctx, view(pts), col, max(1.0, R * s * 0.022), 0.6, 0.85)


def _blush(ctx, L: Look, view: View, H: Head, F: Face, s: float) -> None:
    if F.blush <= 0:
        return
    for side in (-1, 1):
        p, n = H.snout_pt(side * 62, 18)
        f = view.facing(n)
        if f < 0.1:
            continue
        c = view(p)[0]
        fill_poly(ctx, ell2(c, L.R * s * 0.2 * f ** 0.6, L.R * s * 0.11), hexc("#f08a9a"), 0.55 * F.blush)


# ---------- the props each character wears ----------


def _scarf(L, view, F, B, body, ex, lw):
    _, col, y = ex
    col = hexc(col)
    s = view.s
    pieces = []
    wrap = Body([(y - 0.04, 0.072, 0.07, 0.0), (y - 0.01, 0.09, 0.088, 0.004), (y + 0.02, 0.088, 0.086, 0.004),
                 (y + 0.045, 0.07, 0.07, 0.0)], n=6)
    pieces.append(Piece(wrap.polys(view), col, view.depth(B([0, y, 0])) + 0.25, size=0.06 * s, cast=0.2,
                        detail=lambda ctx: stroke_line(ctx, wrap.front_arc(view, y + 0.004), darker(col, 0.7), lw * 1.1)))
    # the hanging end, in front, over the screen-left side of the chest (character's right)
    p0 = B([-0.045, y - 0.02, 0.085])
    hang = [p0, B([-0.06, y - 0.1, 0.11]), B([-0.055, y - 0.2, 0.115]), B([-0.05, y - 0.27, 0.112])]
    rb = ribbon(view, hang, [0.05, 0.052, 0.054, 0.055], [[1.0, 0, 0.0]] * 4)
    end = view(hang[-1])[0]

    def fringe(ctx):
        for k in np.linspace(-0.022, 0.022, 5):
            a = view(B([-0.05 + k, y - 0.272, 0.113]))[0]
            stroke_line(ctx, np.array([a, a + [0, 0.025 * s]]), darker(col, 0.65), lw * 0.9)
        stroke_line(ctx, view(np.array([B([-0.075, y - 0.235, 0.115]), B([-0.025, y - 0.235, 0.115])])), darker(col, 0.6), lw)
    pieces.append(Piece([rb], col, view.depth(hang[2]) + 0.26, size=0.05 * s, cast=0.2, over=fringe))
    # the other end, thrown back over the screen-right shoulder, trailing behind
    back = [B([0.05, y + 0.01, 0.06]), B([0.09, y + 0.03, -0.02]), B([0.08, y - 0.04, -0.11]),
            B([0.07, y - 0.14, -0.16]), B([0.075, y - 0.22, -0.2])]
    rb2 = ribbon(view, back, [0.05, 0.052, 0.054, 0.054, 0.055], [[0, 0, 1.0], [0.3, 0, 1.0], [0.8, 0, 0.6], [0.7, 0, 0.7], [0.7, 0, 0.7]])
    pieces.append(Piece([rb2], darker(col, 0.92, 0.03), view.depth(back[3]) - 0.02 if view.depth(back[3]) < 0 else view.depth(back[3]) + 0.05,
                        size=0.05 * s))
    return pieces


def _specs(L, view, F, B, body, ex, lw):
    _, col = ex
    H = Head(L, F, B)
    s = view.s
    R = L.R

    def over(ctx):
        rims = []
        for side in (-1, 1):
            p, n = H.skull_pt(side * L.eye_az, L.eye_el, R * 0.05)
            f = view.facing(n)
            if f < 0.05:
                rims.append(None)
                continue
            c = view(p)[0] + [0, R * s * 0.01]
            k = f ** 0.8
            r = R * 0.28 * s
            rim = ell2(c, r * k, r, math.radians(F.tilt) * 0.6, 48)
            ctx.save()
            fill_poly(ctx, rim, hexc("#cfe6f2"), 0.22)
            ctx.restore()
            stroke_line(ctx, np.vstack([rim, rim[:1]]), hexc(col), lw * 2.1)
            # a glint
            g = np.array([c + [-r * 0.45 * k, -r * 0.1], c + [-r * 0.1 * k, -r * 0.55]])
            stroke_line(ctx, g, (1, 1, 1), lw * 1.6, lw * 0.8, 0.9)
            rims.append((c, r * k, side))
        vis = [r for r in rims if r]
        if len(vis) == 2:
            a, b = sorted(vis, key=lambda r: r[0][0])
            bridge = np.array([a[0] + [a[1], -R * s * 0.02], (a[0] + b[0]) / 2 + [0, -R * s * 0.06], b[0] + [-b[1], -R * s * 0.02]])
            stroke_line(ctx, spline(bridge, False, 5), hexc(col), lw * 1.8)
        # the arm back to the ear
        for c_r in vis:
            c, rr, side = c_r
            pe, ne = H.skull_pt(side * 80, L.eye_el + 8, R * 0.03)
            if view.facing(ne) > -0.2 and abs(view.yaw_eff) > 20:
                e = view(pe)[0]
                st = c + np.array([np.sign(e[0] - c[0]) * rr, -R * s * 0.02])
                if np.linalg.norm(e - st) > rr * 0.6:
                    stroke_line(ctx, np.array([st, e]), hexc(col), lw * 1.6)
    return [Piece([], hexc(col), view.depth(H.c) + 0.85, over=over)]


def _chalk(L, view, F, B, body, ex, lw):
    H = Head(L, F, B)
    R = L.R
    a = H.X(H.c_local + np.array([R * 0.58, R * 0.72, R * 0.2]))
    b = H.X(H.c_local + np.array([R * 1.05, R * 0.95, -R * 0.35]))
    rel = view.depth((a + b) / 2) - view.depth(H.c)
    stick = tube(view, [a, b], [0.012, 0.012], n=2)
    tip = tube(view, [a, a + (b - a) * 0.12], [0.0125, 0.0125], n=1)
    return [Piece(stick, hexc("#f4f1e8"), view.depth(H.c) + 0.5 + rel + 0.004, size=0.025 * view.s,
                  detail=lambda ctx: [fill_poly(ctx, p, hexc("#9fd3d6")) for p in tip])]


def _hood(L, view, F, B, body, ex, lw):
    _, col, y = ex
    col = hexc(col)
    s = view.s
    c = B([0, y, -0.075])
    hood = [ellipsoid(view, c, (0.115, 0.05, 0.075), rotm(-15, 0, 0))]
    for sd in (-1, 1):
        hood.append(ellipsoid(view, B([sd * 0.075, y - 0.01, 0.0]), (0.04, 0.035, 0.07), rotm(0, sd * 30, 0)))
    return [Piece(hood, col, view.depth(c) + (0.24 if view.depth(c) > 0 else 0.0) + 0.0, size=0.06 * s,
                  detail=lambda ctx: None, cast=0.15)]


def _backpack(L, view, F, B, body, ex, lw):
    _, col, c, r = ex
    col = hexc(col)
    s = view.s
    c = B(c)
    pk = [ellipsoid(view, c, r, np.eye(3))]
    top = c + [0, r[1] * 0.8, 0]
    pieces = [Piece(pk, col, -1.0 + view.depth(c) * 0.1, size=min(r[:2]) * s,
                    detail=lambda ctx: _pack_flap(ctx, view, c, r, col, lw))]
    # gadgets out of the top: an antenna with a red ball, a coil of string
    ant0 = c + [-r[0] * 0.45, r[1] * 0.75, -r[2] * 0.2]
    ant1 = ant0 + [-0.05, 0.2, -0.02]
    pieces.append(Piece(tube(view, [ant0, (ant0 + ant1) / 2 + [-0.01, 0, 0], ant1], [0.006, 0.0045], n=4), hexc("#5a5d66"),
                        -1.0 + view.depth(c) * 0.1 - 0.001, size=0.01 * s, shade=0))
    pieces.append(Piece([ellipsoid(view, ant1, (0.018, 0.018, 0.018))], hexc("#d9483b"), -1.0 + view.depth(c) * 0.1 + 0.0005, size=0.03 * s))
    coil_c = c + [r[0] * 0.35, r[1] * 0.85, -r[2] * 0.1]
    t = np.linspace(0, 2 * np.pi, 40)
    ring_out = view(coil_c + np.stack([np.cos(t) * 0.05, np.sin(t) * 0.035 + 0.02, np.zeros_like(t)], 1))
    ring_in = view(coil_c + np.stack([np.cos(t) * 0.028, np.sin(t) * 0.017 + 0.02, np.zeros_like(t)], 1))

    def coil(ctx):
        ctx.save()
        _path(ctx, [ring_out])
        ctx.new_sub_path()
        p = ring_in[::-1] if True else ring_in
        ctx.move_to(*ring_in[0])
        for q in ring_in[1:]:
            ctx.line_to(*q)
        ctx.close_path()
        ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        ctx.set_source_rgb(*hexc("#e8dcc0"))
        ctx.fill_preserve()
        ctx.set_source_rgb(*INK)
        ctx.set_line_width(lw * 1.6)
        ctx.stroke()
        ctx.restore()
        for k in (0.3, 0.6):
            stroke_line(ctx, (ring_out * (1 - k) + ring_in * k)[5:30], darker(hexc("#e8dcc0"), 0.7), lw * 0.8)
    pieces.append(Piece([], col, -1.0 + view.depth(c) * 0.1 - 0.0008, over=coil))
    return pieces


def _pack_flap(ctx, view, c, r, col, lw):
    flap = []
    for t in np.linspace(-1, 1, 15):
        flap.append(c + [t * r[0] * 0.85, r[1] * (0.55 - 0.25 * (1 - t * t)) , r[2] * 0.95 * math.sqrt(max(0, 1 - t * t * 0.7))])
    pts = view(np.array(flap))
    stroke_line(ctx, pts, darker(col, 0.6), lw * 1.3)


def _headlamp(L, view, F, B, body, ex, lw):
    _, band_c, lamp_c = ex
    H = Head(L, F, B)
    R = L.R
    s = view.s
    pieces = []
    el = 36
    pts, vis = [], []
    for az in np.linspace(-100, 100, 30):
        p, n = H.skull_pt(az, el, R * 0.02)
        pts.append(p)
        vis.append(view.facing(n) > -0.05)
    P2 = view(np.array(pts))
    vis = np.array(vis)

    def band(ctx):
        if vis.sum() > 1:
            stroke_line(ctx, P2[vis], darker(hexc(band_c), 0.6), R * s * 0.15 + lw * 2)
            stroke_line(ctx, P2[vis], hexc(band_c), R * s * 0.15)
    pieces.append(Piece([], hexc(band_c), view.depth(H.c) + 0.5 + 0.0001, over=band))
    p, n = H.skull_pt(0, el, R * 0.06)
    lamp_r = R * 0.22
    lamp = [ellipsoid(view, p + n * lamp_r * 0.3, (lamp_r, lamp_r * 0.85, lamp_r * 0.7), H.Rh)]
    glass, gn = surf(p + n * lamp_r * 0.3, (lamp_r, lamp_r * 0.85, lamp_r * 0.7), H.Rh, [0, 0, 1])
    gf = view.facing(gn)

    def lens(ctx):
        if gf > 0.1:
            c = view(glass)[0]
            fill_poly(ctx, ell2(c, lamp_r * s * 0.62 * gf ** 0.7, lamp_r * s * 0.55), hexc("#fff3b8"))
            stroke_line(ctx, np.vstack([ell2(c, lamp_r * s * 0.62 * gf ** 0.7, lamp_r * s * 0.55), ell2(c, lamp_r * s * 0.62 * gf ** 0.7, lamp_r * s * 0.55)[:1]]), INK, lw)
    pieces.append(Piece(lamp, hexc(lamp_c), view.depth(H.c) + 0.5 + 0.0002 + (view.depth(p) - view.depth(H.c)) * 0.0, size=lamp_r * 2 * s,
                        over=lens) if view.facing(n) > -0.35 else Piece([], hexc(lamp_c), 0))
    return pieces


def _collar(L, view, F, B, body, ex, lw):
    _, col, tag, y = ex
    s = view.s
    ring = Body([(y - 0.025, 0.135, 0.125, 0.0), (y + 0.025, 0.13, 0.12, 0.0)], n=3)
    pieces = [Piece(ring.polys(view), hexc(col), view.depth(B([0, y, 0])) + 0.24, size=0.05 * s)]
    p, n = ring.point(y - 0.035, 0.0, 0.01)
    if view.facing(n) > 0.0:
        c = view(B(p))[0]
        k = view.facing(n) ** 0.6
        fish = np.array([[-1.0, 0.0], [-0.55, -0.45], [0.25, -0.38], [0.62, 0.0], [1.0, -0.4], [0.95, 0.4], [0.62, 0.0],
                         [0.25, 0.38], [-0.55, 0.45]])
        fish = spline(fish, True, 4) * [0.085 * s * k, 0.06 * s] + c + [0, 0.07 * s]

        def tagd(ctx):
            fill_poly(ctx, fish, hexc(tag))
            stroke_line(ctx, np.vstack([fish, fish[:1]]), darker(hexc(tag), 0.55), lw)
            fill_poly(ctx, ell2(c + [-0.045 * s * k, 0.065 * s], 0.009 * s, 0.009 * s), darker(hexc(tag), 0.5))
            stroke_line(ctx, np.array([c + [0, 0.012 * s], c + [0, 0.028 * s]]), darker(hexc(tag), 0.6), lw)
        pieces.append(Piece([], hexc(tag), view.depth(B(p)) + 0.6, over=tagd))
    return pieces


EXTRA = {"scarf": _scarf, "specs": _specs, "chalk": _chalk, "hood": _hood, "backpack": _backpack,
         "headlamp": _headlamp, "collar": _collar}


# ---------- the cast ----------


CAST: dict[str, Look] = {
    # tall, thin, vertical: calm and precise; specs, a teal scarf, chalk behind the ear
    "planner": Look(
        name="The Planner", kind="mouse", fur="#b9b5c6", ear_in="#e9a7b2", nose="#5b3a52", feet_c="#dcb0b0",
        tail_c="#d7aeb0", top="#3b3e4c", legs_c="#2c2e38", sleeve="#3b3e4c", sleeve_len=1.0,
        slices=[(0.33, 0.1, 0.085, 0.0), (0.42, 0.098, 0.084, 0.004), (0.55, 0.105, 0.09, 0.008),
                (0.7, 0.112, 0.094, 0.006), (0.78, 0.09, 0.08, 0.0), (0.82, 0.055, 0.05, 0.0)],
        neck=(0.83, 0.0), R=0.135, skull=(0.95, 1.05, 0.98), snout=(1.42, 0.82, 0.92), snout_drop=0.22,
        eye=0.92, ear=0.72, hip=(0.055, 0.36), leg_r=(0.034, 0.026), foot=(0.085, 0.04, 0.026),
        shoulder=(0.1, 0.735), arm=(0.17, 0.16), arm_r=(0.03, 0.025, 0.022), hand=0.03,
        tail=[(0.0, 0.38, -0.08), (0.05, 0.3, -0.2), (0.16, 0.12, -0.24), (0.27, 0.06, -0.12), (0.32, 0.18, -0.05), (0.27, 0.28, -0.02)],
        tail_r=(0.02, 0.006),
        pose=Pose(arm_l=(8, -14, 30), arm_r=(10, 10, 62), hand_in=(10, 55)),
        face=Face(lid=0.32, smile=0.32, sneer=0.35, brow=0.1, brow_tilt=0.1),
        extras=(("scarf", "#1f7d7a", 0.8), ("specs", "#2a2530"), ("chalk",)),
        whisker="#6a6070",
    ),
    # big pear body, small head, thick forearms: a quiet gentle giant; striped sailor shirt, a notched ear
    "muscle": Look(
        name="The Muscle", kind="mouse", fur="#9c7a62", ear_in="#d99c94", nose="#3e2a25", feet_c="#c99487",
        tail_c="#c4928a", top="#f0e5cd", legs_c="#2e3b5e", sleeve="#f0e5cd", sleeve_len=0.42,
        slices=[(0.2, 0.2, 0.17, 0.0), (0.28, 0.25, 0.21, 0.01), (0.4, 0.29, 0.24, 0.03), (0.52, 0.28, 0.23, 0.03),
                (0.62, 0.23, 0.19, 0.01), (0.67, 0.14, 0.12, 0.0), (0.69, 0.07, 0.065, 0.0)],
        neck=(0.67, 0.02), R=0.112, skull=(1.0, 0.95, 0.98), snout=(1.05, 1.15, 1.05), snout_drop=0.3,
        eye=0.72, eye_az=27, ear=0.7, ear_pos=(0.62, 0.68, -0.12), notch=0.38, hip=(0.1, 0.22),
        leg_r=(0.06, 0.05), foot=(0.085, 0.05, 0.03), shoulder=(0.22, 0.6), arm=(0.17, 0.19),
        arm_r=(0.055, 0.075, 0.058), hand=0.05,
        tail=[(0.0, 0.24, -0.2), (0.1, 0.12, -0.32), (0.25, 0.05, -0.34), (0.38, 0.05, -0.24), (0.44, 0.12, -0.14)],
        tail_r=(0.03, 0.01),
        pose=Pose(arm_l=(22, 0, 18), arm_r=(22, 0, 18), hand_in=(25, 25), feet=1.0),
        face=Face(smile=0.42, brow=0.15, brow_tilt=0.25),
        extras=(("stripes", "#b8473a", 0.24, 0.66, 0.065, 0.42),),
        whisker="#4a3a32", brow_c="#5e4535",
    ),
    # small, round, huge ears and eyes: jittery and eager; mustard raincoat, backpack of gadgets, a headlamp
    "rookie": Look(
        name="The Rookie", kind="mouse", fur="#efe6da", ear_in="#f2a3b0", nose="#e8778e", feet_c="#f0b6b8",
        tail_c="#efb4b7", top="#e3ad35", legs_c="#efe6da", sleeve="#e3ad35", sleeve_len=1.0,
        slices=[(0.13, 0.17, 0.15, 0.0), (0.2, 0.158, 0.14, 0.005), (0.3, 0.14, 0.125, 0.01), (0.4, 0.125, 0.112, 0.008),
                (0.45, 0.1, 0.09, 0.0), (0.48, 0.06, 0.055, 0.0)],
        neck=(0.48, 0.0), R=0.158, skull=(1.0, 0.98, 0.98), snout=(0.82, 0.9, 0.9), snout_drop=0.3,
        eye=1.35, eye_az=27, eye_el=6, ear=0.98, ear_pos=(0.66, 0.66, -0.14), hip=(0.06, 0.15),
        leg_r=(0.03, 0.026), foot=(0.07, 0.038, 0.024), shoulder=(0.105, 0.42), arm=(0.1, 0.1),
        arm_r=(0.03, 0.028, 0.026), hand=0.028,
        tail=[(0.0, 0.16, -0.12), (0.08, 0.1, -0.22), (0.2, 0.04, -0.22), (0.26, 0.1, -0.12), (0.22, 0.17, -0.06),
              (0.27, 0.22, -0.02)],
        tail_r=(0.016, 0.005),
        pose=Pose(arm_l=(10, 30, 95), arm_r=(10, 30, 95), hand_in=(40, 40)),
        face=Face(open=1.1, smile=0.45, brow=0.45, brow_tilt=-0.3),
        extras=(("placket", "#b07f1c", 0.44, 0.13, (0.38, 0.3, 0.22), "#6d4b25"), ("hem", "#b07f1c", 0.14),
                ("straps", "#7a5a3a", 32, 0.27, 0.44, 0.022), ("hood", "#d9a42e", 0.47),
                ("backpack", "#727746", (0.0, 0.33, -0.17), (0.13, 0.15, 0.09)), ("headlamp", "#55593a", "#c9ccd2")),
        whisker="#a99a92",
    ),
    # long, lean, black: in charge; green eyes, tail always a question mark, a collar with a gold sardine tag
    "cat": Look(
        name="The Cat", kind="cat", fur="#2b2737", ear_in="#6e4a63", nose="#a0687f", feet_c="#2b2737",
        tail_c="#2b2737", top="#2b2737", legs_c="#2b2737", sleeve="", sleeve_len=0.0,
        slices=[(1.06, 0.21, 0.18, -0.02), (1.2, 0.28, 0.23, -0.02), (1.42, 0.26, 0.21, 0.0), (1.68, 0.21, 0.18, 0.03),
                (1.95, 0.26, 0.22, 0.05), (2.2, 0.29, 0.21, 0.03), (2.36, 0.19, 0.15, 0.0), (2.48, 0.12, 0.11, 0.0)],
        neck=(2.48, 0.02), R=0.4, skull=(1.12, 0.92, 0.95), snout=(0.62, 0.95, 0.75), snout_drop=0.38,
        eye=1.2, eye_az=28, eye_el=6, ear=0.62, ear_pos=(0.58, 0.72, -0.08), hip=(0.14, 1.16),
        leg_r=(0.155, 0.068), foot=(0.19, 0.095, 0.06), shoulder=(0.28, 2.22), arm=(0.48, 0.46),
        arm_r=(0.1, 0.075, 0.055), hand=0.072,
        tail=[(0.0, 1.18, -0.2), (0.15, 1.0, -0.42), (0.45, 1.05, -0.55), (0.7, 1.45, -0.5), (0.78, 1.95, -0.4),
              (0.62, 2.3, -0.32), (0.42, 2.28, -0.28), (0.4, 2.1, -0.26)],
        tail_r=(0.07, 0.035),
        pose=Pose(arm_l=(9, 6, 28), arm_r=(52, -6, 100), hand_in=(15, 90), knee=10),
        face=Face(lid=0.45, low=0.12, smile=0.75, sneer=0.25, brow_tilt=0.35, gaze=(0.25, 0.0)),
        extras=(("collar", "#8c2f3f", "#e6b84a", 2.45),),
        whisker="#b9b2c8", brow_c="#17141f", eye_c="#141019", iris="#9fd34a",
    ),
}


# ---------- drawing a character ----------


def render_premul(L: Look, view: View, face: Face | None = None, pose: Pose | None = None, box=(520, 560),
                  ss: int = 2, outline: float | None = None) -> np.ndarray:
    """The character on a transparent box: float32 premultiplied RGBA in [0, 1], with its thick outline.
    view.ox/oy are box coordinates."""
    w, h = box
    v2 = replace(view, s=view.s * ss, ox=view.ox * ss, oy=view.oy * ss, lw_cap=view.lw_cap * ss)
    surf_ = cairo.ImageSurface(cairo.FORMAT_ARGB32, w * ss, h * ss)
    ctx = cairo.Context(surf_)
    ctx.set_antialias(cairo.ANTIALIAS_BEST)
    pieces, lw = build(L, v2, face, pose)
    cast_px = 0.028 * v2.s * (1 if L.kind == "mouse" else 0.45)
    paint_pieces(ctx, pieces, lw, cast_px)
    surf_.flush()
    buf = np.ndarray((h * ss, surf_.get_stride() // 4, 4), np.uint8, surf_.get_data())[:, :w * ss].astype(np.float32) / 255
    solid = cairo.ImageSurface(cairo.FORMAT_ARGB32, w * ss, h * ss)  # the same figure without its fine strands
    paint_pieces(cairo.Context(solid), pieces, lw, cast_px, fine=False)
    solid.flush()
    a_solid = np.ndarray((h * ss, solid.get_stride() // 4, 4), np.uint8, solid.get_data())[:, :w * ss, 3].astype(np.float32) / 255
    a = buf[..., 3]
    k = outline if outline is not None else lw * 2.0
    r = max(1, int(round(k)))
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    a_out = cv2.dilate(cv2.GaussianBlur(a_solid, (0, 0), 0.6), ker)
    a_out = np.clip((a_out - 0.15) / 0.7, 0, 1)
    ink = np.array([INK[0], INK[1], INK[2]], np.float32)
    col = buf[..., 2::-1]
    out_rgb = col + ink * (a_out * (1 - a))[..., None]
    out_a = np.maximum(a, a_out)
    res = np.dstack([out_rgb, out_a]).clip(0, 1)
    if ss != 1:
        res = cv2.resize(res, (w, h), interpolation=cv2.INTER_AREA)
    return res


def render(L: Look, view: View, face: Face | None = None, pose: Pose | None = None, box=(520, 560),
           ss: int = 2, outline: float | None = None) -> np.ndarray:
    """The character on a transparent box (RGBA uint8, straight alpha), with its thick outline."""
    res = render_premul(L, view, face, pose, box, ss, outline)
    al = res[..., 3:4]
    rgb = np.where(al > 1e-4, res[..., :3] / np.maximum(al, 1e-4), 0)
    return (np.dstack([rgb, al]) * 255).clip(0, 255).astype(np.uint8)


# ---------- in a film: a character standing in a 3D world, seen by a pinhole camera ----------
# World: x right, y forward, z up (vidosuper.graphics.toon.Camera); one world unit = one character unit.


def frame_of(heading: float, tilt=(0.0, 0.0)) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """World directions of a character's local axes (x, up, forward) when it faces `heading` degrees
    (0 = +y, 90 = +x), tipped by tilt = (pitch forward, roll toward its local +x) degrees about its feet."""
    h = math.radians(heading)
    F = np.array([math.sin(h), math.cos(h), 0.0])
    Z = np.array([0.0, 0.0, 1.0])
    X = np.cross(-F, Z)
    p, r = (math.radians(a) for a in tilt)
    if p:
        Z, F = Z * math.cos(p) + F * math.sin(p), F * math.cos(p) - Z * math.sin(p)
    if r:
        X, Z = X * math.cos(r) - Z * math.sin(r), Z * math.cos(r) + X * math.sin(r)
    return X, Z, F


def to_world(root, heading: float, p, k: float = 1.0, tilt=(0.0, 0.0)) -> np.ndarray:
    """A local point of a character (standing at root, facing heading, squashed by k) in the world."""
    X, Z, F = frame_of(heading, tilt)
    p = np.asarray(p, float)
    w = 1 / math.sqrt(k)
    return np.asarray(root, float) + p[0] * w * X + p[1] * k * Z + p[2] * w * F


def to_local(root, heading: float, q, tilt=(0.0, 0.0)) -> np.ndarray:
    X, Z, F = frame_of(heading, tilt)
    d = np.asarray(q, float) - np.asarray(root, float)
    return np.array([d @ X, d @ Z, d @ F])


def joints(L: Look, face: Face | None = None, pose: Pose | None = None) -> dict:
    """Local positions of hands, feet, head, nose, back, chest and tail tip for this pose."""
    build(L, View(s=100.0), face, pose)
    return dict(build.joints)


def stage_view(cam, root, heading: float, k: float = 1.0, height: float = 0.6, lw_cap: float = 99.0,
               tilt=(0.0, 0.0), anchor=None) -> View:
    """The View that draws a character standing at `root` facing `heading` as the camera sees it (weak
    perspective about its middle)."""
    X, Z, F = frame_of(heading, tilt)
    Rc = cam.R
    conv = lambda v: np.array([v[0], v[1], -v[2]])  # toon camera z looks away; View z comes toward us
    M = np.stack([conv(Rc @ X), conv(Rc @ Z), conv(Rc @ F)], 1)
    a_loc = np.array([0.0, height, 0.0]) if anchor is None else np.asarray(anchor, float)
    centre = to_world(root, heading, a_loc, k, tilt)  # placed exactly by the camera; the rest drawn flat around it
    s = cam.scale(centre)
    cx, cy = cam(centre)
    v = View(s=s, R=M, k=k, lw_cap=lw_cap)
    c = v.cam(a_loc)[0]
    return replace(v, ox=float(cx - c[0] * s), oy=float(cy + c[1] * s))


def actor(L: Look, cam, root, heading: float, face: Face | None = None, pose: Pose | None = None, k: float = 1.0,
          size=(1080, 1920), ss: int = 2, lw_cap: float = 6.0, reach: float | None = None, tilt=(0.0, 0.0),
          anchor=None):
    """The character as the camera sees it: (premultiplied RGBA patch, x0, y0), or None when off screen or behind."""
    tall = reach if reach is not None else (L.neck[0] + L.R * 2.6 * max(1.0, L.ear))
    if cam.cam(to_world(root, heading, [0, tall * 0.5, 0], k, tilt))[2] < 0.2:
        return None
    v = stage_view(cam, root, heading, k, height=tall * 0.45, lw_cap=lw_cap, tilt=tilt, anchor=anchor)
    wide = max(0.8, tall * 0.75)
    corners = np.array([[x, y, z] for x in (-wide, wide) for y in (-0.3, tall * 1.4) for z in (-wide, wide)])
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


def comp(img: np.ndarray, patch, tint=(1.0, 1.0, 1.0), blur: float = 0.0, alpha: float = 1.0) -> np.ndarray:
    """Composite an actor() patch over a float RGB frame (in place), tinted by the scene's light."""
    if patch is None:
        return img
    layer, x0, y0 = patch
    if blur > 0:
        layer = cv2.GaussianBlur(layer, (0, 0), blur)
    h, w = layer.shape[:2]
    a = layer[..., 3:4] * alpha
    img[y0:y0 + h, x0:x0 + w] = img[y0:y0 + h, x0:x0 + w] * (1 - a) + layer[..., :3] * np.asarray(tint, np.float32) * alpha
    return img
