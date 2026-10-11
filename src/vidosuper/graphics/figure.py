"""A cartoon person for the animated shorts: a 2D rig drawn from the side, the front or behind.

Units are metres in the figure's own plane: u forward (the way the figure faces in profile; its right when seen from
behind), v up, feet on v = 0. A Pose holds joint angles for the side view; a BackPose holds the body for the front and
back views. `profile`, `front` and `back` turn a pose into parts back-to-front (name, polygon, colour, clip); names
starting with "~" are flat details (eyes, pockets, laces) drawn without shading or outline. `draw` paints parts with
cel shading and a coloured outline, and `outline` gives the solid shapes for casting the figure's shadow.

Limbs are smooth tapered tubes (`tube`) bent through rounded joints, so arms and legs read as soft cloth and skin
rather than separate blocks.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields, replace

import numpy as np

from .toon import darker, ellipse, hexc, mix, spline

TH, SH, TORSO, NECK, UA, FA = 0.43, 0.43, 0.50, 0.07, 0.28, 0.26

PAL = dict(hoodie="#E2483A", cuff="#C83C30", hood_in="#8E2A20", string="#F7EBDD", jeans="#2F4677",
           jeans_dk="#24385F", stitch="#D9B36A", skin="#C98A63", skin_dk="#A86E4E", blush="#E08A78",
           hair="#2A2129", hair_hi="#4E3D49", shoe="#F4F0E8", sole="#D6CDBF", shoe_acc="#E2483A", eye="#231E26",
           mouth="#7A3B33", phone="#1B1C25")


# ---------------------------------------------------------------- poses

@dataclass
class Pose:
    """The figure from the side, facing +u."""
    root: tuple = (0.0, 0.93)  # pelvis
    spine: float = 0.0  # torso lean from vertical, + = forward
    neck: float = 0.0  # head bend relative to the torso, + = forward/down
    head: float = 0.0  # extra head tilt, + = chin down
    sh_n: float = 4.0  # near upper arm from straight down, + = forward
    el_n: float = 12.0  # near elbow bend, + = forearm forward/up
    sh_f: float = -4.0
    el_f: float = 12.0
    hp_n: float = 0.0  # near thigh from straight down, + = forward
    kn_n: float = 0.0  # near knee bend, + = shin back
    an_n: float = 0.0  # near foot pitch, + = toes up
    hp_f: float = 0.0
    kn_f: float = 0.0
    an_f: float = 0.0
    phone: float = 0.0  # > 0.5 = phone in the near hand
    turn: float = 1.0  # head facing: 1 forward, -1 looking back over the shoulder
    eyes: float = 1.0  # 0 shut, 1 open, 1.35 wide
    brows: float = 0.0  # -1 low/frown .. +1 raised
    brow_tilt: float = 0.0  # + inner ends up (worried), - inner ends down (cross)
    quirk: float = 0.0  # one brow up, the other down ("huh?")
    smile: float = 0.1  # -1 frown .. 1 big smile
    mouth_open: float = 0.0
    happy: float = 0.0  # 1 = eyes closed in happy arcs
    gaze: tuple = (0.0, 0.0)  # pupils' shift (-1..1)


def blend(a, b, t: float):
    """Pose (or BackPose) part way from a to b (t may overshoot for snappy moves)."""
    out = {}
    for f in fields(type(a)):
        x, y = getattr(a, f.name), getattr(b, f.name)
        out[f.name] = tuple(float(p + (q - p) * t) for p, q in zip(x, y)) if isinstance(x, tuple) else x + (y - x) * t
    return type(a)(**out)


def _dn(a: float) -> np.ndarray:
    r = math.radians(a)
    return np.array([math.sin(r), -math.cos(r)])


def _up(a: float) -> np.ndarray:
    r = math.radians(a)
    return np.array([math.sin(r), math.cos(r)])


def _frame(origin, up_vec, fwd_vec):
    o, d, n = (np.asarray(v, np.float64) for v in (origin, up_vec, fwd_vec))
    return lambda pts: o + np.asarray(pts, np.float64)[:, :1] * d + np.asarray(pts, np.float64)[:, 1:2] * n


def _xy(origin, x_vec, y_vec):
    o, x, y = (np.asarray(v, np.float64) for v in (origin, x_vec, y_vec))
    return lambda pts: o + np.asarray(pts, np.float64)[:, :1] * x + np.asarray(pts, np.float64)[:, 1:2] * y


def joints(p: Pose) -> dict:
    P = np.asarray(p.root, np.float64)
    d = _up(p.spine)
    n = np.array([d[1], -d[0]])
    N = P + TORSO * d
    S = P + (TORSO - 0.085) * d - 0.015 * n
    H0 = N + NECK * _up(p.spine + p.neck)
    ha = p.spine + p.neck + p.head
    J = dict(P=P, d=d, n=n, N=N, S=S, H0=H0, Hc=H0 + 0.105 * _up(ha), ha=ha)
    for s in "nf":
        sh, el = getattr(p, "sh_" + s), getattr(p, "el_" + s)
        E = S + UA * _dn(sh)
        J["E" + s], J["W" + s], J["wa" + s] = E, E + FA * _dn(sh + el), sh + el
        hp, kn, an = getattr(p, "hp_" + s), getattr(p, "kn_" + s), getattr(p, "an_" + s)
        K = P + TH * _dn(hp)
        b = hp - kn
        J["K" + s], J["A" + s] = K, K + SH * _dn(b)
        J["fd" + s] = np.array([math.cos(math.radians(b + an)), math.sin(math.radians(b + an))])
    return J


# ---------------------------------------------------------------- shapes

def tube(pts, radii, n: int = 44, fillet: float = 1.25, round_start: bool = True, round_end: bool = True) -> np.ndarray:
    """Outline of a soft limb through joints pts with radius keys [(position, r)]: position = joint index plus a
    fraction along the next segment (0 = first joint, 1 = second, 1.5 = half way to the third). Bends are rounded
    with a fillet wider than the limb so the inside of an elbow or knee never folds over."""
    P = [np.asarray(q, np.float64) for q in pts]
    keys = sorted(radii)

    def r_at(pos):
        xs = [k for k, _ in keys]
        ys = [v for _, v in keys]
        return float(np.interp(pos, xs, ys))

    path, joint_idx = [P[0]], [0]
    for i in range(1, len(P) - 1):
        a, b = P[i] - P[i - 1], P[i + 1] - P[i]
        la, lb = np.linalg.norm(a), np.linalg.norm(b)
        if la < 1e-6 or lb < 1e-6:
            continue
        a, b = a / la, b / lb
        cross = a[0] * b[1] - a[1] * b[0]
        phi = math.acos(max(-1.0, min(1.0, float(a @ b))))
        if phi < 0.03:
            path.append(P[i])
            joint_idx.append(len(path) - 1)
            continue
        R = fillet * r_at(i)
        T = R * math.tan(phi / 2)
        T = min(T, 0.45 * min(la, lb))
        R = T / math.tan(phi / 2)
        A, B = P[i] - a * T, P[i] + b * T
        sgn = 1.0 if cross > 0 else -1.0
        C = A + R * np.array([-a[1], a[0]]) * sgn
        a0 = math.atan2(A[1] - C[1], A[0] - C[0])
        m = max(4, int(phi / 0.1))
        arc = [C + R * np.array([math.cos(a0 + sgn * phi * k / m), math.sin(a0 + sgn * phi * k / m)]) for k in range(m + 1)]
        path += arc
        joint_idx.append(len(path) - 1 - m // 2)
    path.append(P[-1])
    joint_idx.append(len(path) - 1)
    path = np.array(path)
    seg = np.linalg.norm(np.diff(path, axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)])
    js = s[joint_idx]
    total = s[-1]
    u = np.linspace(0, total, n)
    c = np.stack([np.interp(u, s, path[:, 0]), np.interp(u, s, path[:, 1])], 1)
    pos = np.interp(u, js, np.arange(len(js), dtype=np.float64))
    r = np.array([r_at(q) for q in pos])
    t = np.gradient(c, axis=0)
    t /= np.linalg.norm(t, axis=1, keepdims=True) + 1e-12
    nr = np.stack([-t[:, 1], t[:, 0]], 1)
    left, right = c + nr * r[:, None], c - nr * r[:, None]
    an_e = math.atan2(nr[-1, 1], nr[-1, 0])
    an_s = math.atan2(nr[0, 1], nr[0, 0])
    cap_e = [c[-1] + r[-1] * np.array([math.cos(a), math.sin(a)]) for a in np.linspace(an_e, an_e - math.pi, 9)[1:-1]] \
        if round_end else np.zeros((0, 2))
    cap_s = [c[0] + r[0] * np.array([math.cos(a), math.sin(a)]) for a in np.linspace(an_s - math.pi, an_s - 2 * math.pi, 9)[1:-1]] \
        if round_start else np.zeros((0, 2))
    return np.vstack([left, cap_e, right[::-1], cap_s])


def line(pts, w0: float, w1: float | None = None, n: int = 12) -> np.ndarray:
    """A tapered stroke (brows, laces, creases) as a polygon."""
    w1 = w0 if w1 is None else w1
    return tube(pts, [(0, w0 / 2), (len(pts) - 1, w1 / 2)], n=n, fillet=1.0)


def _cols(pal) -> dict:
    return {k: hexc(v) for k, v in (pal or PAL).items()}


def _hand(W, hd, c, tone, name):
    """Mitten hand at the wrist pointing along hd, thumb on the +side."""
    side = np.array([-hd[1], hd[0]])
    F = _xy(W, hd, side)
    palm = spline(F([(0.0, -0.031), (0.035, -0.04), (0.075, -0.037), (0.1, -0.022), (0.108, 0.0), (0.1, 0.021),
                     (0.07, 0.033), (0.03, 0.035), (0.0, 0.03)]), n=6)
    thumb = tube(F([(0.018, 0.026), (0.05, 0.05), (0.065, 0.052)]), [(0, 0.016), (2, 0.013)], n=16)
    split = [line(F([(0.072, -0.012), (0.1, -0.009)]), 0.004), line(F([(0.07, 0.008), (0.098, 0.008)]), 0.004)]
    return [("hand_" + name, palm, tone(c["skin"]), None), ("thumb_" + name, thumb, tone(c["skin"]), None)] + \
        [("~fingers", sp, tone(c["skin_dk"]), None) for sp in split]


def _sleeve(S, E, W, c, tone, name, thick=1.0):
    hd = W - E
    hd = hd / (np.linalg.norm(hd) + 1e-9)
    arm = tube([S, E, W - 0.02 * hd], [(0, 0.071 * thick), (0.45, 0.064 * thick), (1.0, 0.057 * thick),
                                       (1.5, 0.055 * thick), (2.0, 0.052 * thick)])
    cuff = tube([W - 0.055 * hd, W - 0.012 * hd], [(0, 0.054), (1, 0.054)], n=12)
    return [("arm_" + name, arm, tone(c["hoodie"]), None), ("cuff_" + name, cuff, tone(c["cuff"]), None)] + \
        _hand(W, hd, c, tone, name)


def _shoe(A, fd, c, tone, name):
    fu = np.array([-fd[1], fd[0]])
    F = _xy(A, fd, fu)
    upper = spline(F([(-0.088, -0.05), (-0.084, 0.0), (-0.07, 0.04), (-0.02, 0.062), (0.035, 0.066), (0.08, 0.042),
                      (0.14, 0.012), (0.2, -0.008), (0.236, -0.036), (0.22, -0.056), (0.1, -0.06)]), n=6)
    sole = spline(F([(-0.095, -0.058), (0.1, -0.064), (0.232, -0.06), (0.248, -0.072), (0.232, -0.088),
                     (0.1, -0.09), (-0.088, -0.088), (-0.1, -0.074)]), n=5)
    stripe = line(F([(-0.05, -0.03), (0.02, -0.012), (0.09, -0.01)]), 0.022, 0.008)
    laces = [line(F([(0.03 + 0.03 * k, 0.05 - 0.017 * k), (0.045 + 0.03 * k, 0.035 - 0.017 * k)]), 0.007) for k in range(3)]
    collar = line(F([(-0.075, 0.035), (-0.02, 0.055)]), 0.012)
    return [("shoe_" + name, upper, tone(c["shoe"]), None), ("sole_" + name, sole, tone(c["sole"]), None),
            ("~stripe", stripe, tone(c["shoe_acc"]), None), ("~collar", collar, tone(c["sole"]), None)] + \
        [("~lace", l_, tone(c["sole"]), None) for l_ in laces]


def _leg(J, s, c, tone):
    P, K, A = J["P"], J["K" + s], J["A" + s]
    shin = (A - K) / (np.linalg.norm(A - K) + 1e-9)
    leg = tube([P, K, A + 0.01 * shin], [(0, 0.1), (0.35, 0.092), (1.0, 0.073), (1.3, 0.071), (1.85, 0.06), (2.0, 0.06)])
    hem = tube([A - 0.07 * shin, A + 0.005 * shin], [(0, 0.064), (1, 0.064)], n=12)
    return [("leg_" + s, leg, tone(c["jeans"]), None), ("hem_" + s, hem, tone(c["jeans_dk"]), None)] + \
        _shoe(A, J["fd" + s], c, tone, s)


def _eye(center, rx, ry, p, c, angle=0.0):
    """An eye: open oval with a highlight, or a happy arc when p.happy, squashed shut when p.eyes ~ 0."""
    cx, cy = center
    if p.happy > 0.5 or p.eyes < 0.12:
        k = 1 if p.happy > 0.5 else -0.15
        arc = [(cx - rx * 1.1, cy - k * ry * 0.2), (cx, cy + k * ry * 0.55), (cx + rx * 1.1, cy - k * ry * 0.2)]
        return [("~eye", line(np.array(arc), rx * 0.5, rx * 0.5), c["eye"], None)]
    ryy = ry * min(1.35, max(0.15, p.eyes))
    gx, gy = p.gaze
    eye = ellipse((cx + gx * rx * 0.25, cy + gy * ry * 0.2), rx * (1 + 0.15 * max(0.0, p.eyes - 1)), ryy, angle, 20)
    hi = ellipse((cx + gx * rx * 0.25 + rx * 0.38, cy + gy * ry * 0.2 + ryy * 0.38), rx * 0.32, rx * 0.32, 0, 10)
    return [("~eye", eye, c["eye"], None), ("~eye_hi", hi, (1.0, 1.0, 1.0), None)]


# ---------------------------------------------------------------- side view

def _head_profile(J, p, c):
    hd = _up(J["ha"])
    hn = np.array([hd[1], -hd[0]]) * (1 if p.turn >= 0 else -1)
    H = _frame(J["Hc"], hd, hn)
    face = spline(H([(0.135, -0.01), (0.125, 0.055), (0.095, 0.105), (0.055, 0.125), (0.025, 0.128), (0.0, 0.132),
                     (-0.018, 0.152), (-0.035, 0.133), (-0.052, 0.128), (-0.066, 0.126), (-0.082, 0.12),
                     (-0.104, 0.106), (-0.122, 0.07), (-0.118, 0.02), (-0.085, -0.045), (-0.05, -0.1), (0.0, -0.132),
                     (0.07, -0.125), (0.115, -0.075)]), n=6)
    ang = math.degrees(math.atan2(hd[1], hd[0])) - 90
    ear = ellipse(H(np.array([[-0.005, -0.03]]))[0], 0.025, 0.034, ang, 18)
    ear_in = ellipse(H(np.array([[-0.005, -0.026]]))[0], 0.011, 0.018, ang, 12)
    hair = spline(H([(-0.07, -0.118), (-0.02, -0.148), (0.06, -0.155), (0.13, -0.115), (0.172, -0.045), (0.188, 0.02),
                     (0.18, 0.08), (0.158, 0.13), (0.128, 0.158), (0.112, 0.14), (0.1, 0.112), (0.085, 0.09),
                     (0.07, 0.07), (0.05, 0.055), (0.03, 0.05), (0.005, 0.045), (0.0, 0.03), (0.025, 0.012),
                     (0.04, -0.02), (0.03, -0.06), (-0.01, -0.085), (-0.045, -0.098)]), n=6)
    shine = line(H(np.array([(0.12, -0.08), (0.16, -0.02), (0.168, 0.04)])), 0.016, 0.006)
    out = [("ear", ear, darker(c["skin"], 0.93, 0.03), None), ("~ear_in", ear_in, c["skin_dk"], None),
           ("head", face, c["skin"], None)]
    eye_c = H(np.array([[0.022 + 0.004 * p.brows, 0.1]]))[0]
    out += _eye(eye_c, 0.011, 0.018, p, c, ang)
    by = 0.058 + 0.014 * p.brows + 0.012 * p.quirk
    brow = line(H(np.array([(by - 0.004 + 0.006 * p.brow_tilt, 0.078), (by + 0.004, 0.1), (by - 0.002 - 0.006 * p.brow_tilt, 0.124)])),
                0.007, 0.011)
    out.append(("~brow", brow, c["hair"], None))
    sm = p.smile
    if p.mouth_open > 0.15:
        mo = ellipse(H(np.array([[-0.066, 0.118]]))[0], 0.01 + 0.006 * p.mouth_open, 0.012 * p.mouth_open + 0.004, ang, 14)
        out.append(("~mouth", mo, c["mouth"], None))
    else:
        mouth = line(H(np.array([(-0.064 + 0.006 * sm, 0.108), (-0.068, 0.118), (-0.066 + 0.004 * sm, 0.127)])), 0.005)
        out.append(("~mouth", mouth, c["mouth"], None))
    out.append(("~blush", ellipse(H(np.array([[-0.03, 0.08]]))[0], 0.022, 0.014, ang, 14), mix(c["skin"], c["blush"], 0.45), None))
    out += [("hair", hair, c["hair"], None), ("~shine", shine, c["hair_hi"], None)]
    return out


def profile(p: Pose, pal=None) -> list:
    """Parts of the figure seen from its side, facing +u, back to front: (name, poly, rgb, clip)."""
    J, c = joints(p), _cols(pal)

    def far(col):
        return darker(col, 0.74, 0.1)

    def same(col):
        return col

    d, n, P, N = J["d"], J["n"], J["P"], J["N"]
    T = _frame(P, d, n)
    torso = spline(T([(-0.06, -0.128), (0.08, -0.15), (0.25, -0.155), (0.39, -0.135), (0.47, -0.095), (0.515, -0.03),
                      (0.515, 0.045), (0.46, 0.11), (0.36, 0.135), (0.22, 0.14), (0.1, 0.15), (0.0, 0.14),
                      (-0.06, 0.09), (-0.072, 0.0)]), n=6)
    hem = T([(-0.2, -0.3), (-0.025, -0.3), (-0.025, 0.3), (-0.2, 0.3)])
    pocket = spline(T([(0.0, 0.095), (0.17, 0.112), (0.205, 0.17), (0.0, 0.17)]), n=4)
    seat = spline(T([(-0.11, -0.13), (0.06, -0.14), (0.1, 0.06), (0.0, 0.13), (-0.11, 0.07)]), n=6)
    hood = spline(T([(0.36, -0.12), (0.43, -0.175), (0.51, -0.17), (0.56, -0.11), (0.565, -0.04), (0.53, -0.01),
                     (0.47, -0.04), (0.41, -0.08)]), n=6)
    hood_in = spline(T([(0.46, -0.13), (0.52, -0.135), (0.55, -0.08), (0.52, -0.05), (0.48, -0.07)]), n=5)
    strings = [line(T(np.array([(0.48, 0.09 - 0.01 * k), (0.41, 0.11 - 0.008 * k), (0.34 - 0.02 * k, 0.122 - 0.006 * k)])), 0.012)
               for k in range(2)]
    neck = tube([N - 0.07 * d, J["H0"] + 0.02 * _up(J["ha"])], [(0, 0.047), (1, 0.044)], n=12)
    parts = _sleeve(J["S"], J["Ef"], J["Wf"], c, far, "f") + _leg(J, "f", c, far)
    parts.append(("seat", seat, c["jeans"], None))
    parts += _leg(J, "n", c, same)
    parts.append(("neck", neck, c["skin_dk"], None))
    parts += [("torso", torso, c["hoodie"], None), ("~hem", hem, c["cuff"], torso),
              ("~pocket", pocket, darker(c["hoodie"], 0.9, 0.04), torso)]
    parts += [("~string", s_, c["string"], None) for s_ in strings]
    parts += [("hood", hood, c["hoodie"], None), ("~hood_in", hood_in, c["hood_in"], hood)]
    parts += _head_profile(J, p, c)
    parts += _sleeve(J["S"], J["En"], J["Wn"], c, same, "n")
    if p.phone > 0.5:
        W, hd = J["Wn"], _dn(J["wan"])
        side = np.array([-hd[1], hd[0]])
        ctr = W + 0.05 * hd + 0.02 * side
        a, b = 0.078 * side, 0.011 * hd
        parts.append(("phone", np.array([ctr - a - b, ctr + a - b, ctr + a + b, ctr - a + b]), c["phone"], None))
        parts += [q for q in _hand(W, hd, c, same, "n") if q[0] == "thumb_n"]
    return parts


# ---------------------------------------------------------------- front and back views

@dataclass
class BackPose:
    """The figure facing us or facing away: sitting on the ground or standing / walking."""
    root: tuple = (0.0, 0.1)  # pelvis centre
    lean: float = 0.0  # torso roll, + = toward the figure's right (+u)
    hunch: float = 0.0  # 0 upright .. 1 curled forward
    head_roll: float = 0.0
    head_turn: float = 0.0  # -1 .. 1, which way the face is turned
    arm_l: float = 8.0  # upper arm angle out from straight down
    arm_r: float = 8.0
    el_l: float = 0.0  # elbow bend, + carries the forearm further out / up
    el_r: float = 0.0
    sit: float = 1.0  # 1 sitting on the ground, 0 standing
    lift_l: float = 0.0  # standing: how high each foot is raised (walking)
    lift_r: float = 0.0
    cross: float = 1.0  # sitting: 1 cross-legged (knees out), 0 knees up and hugged
    eyes: float = 1.0
    brows: float = 0.0
    brow_tilt: float = 0.0
    quirk: float = 0.0
    smile: float = 0.1
    mouth_open: float = 0.0
    happy: float = 0.0
    gaze: tuple = (0.0, 0.0)


def _body(p: BackPose, c, facing_us: bool):
    """Shared front/back body: legs, shoes, seat, torso, arms, neck. Returns (parts, head frame, extras)."""
    P = np.asarray(p.root, np.float64)
    up = _up(p.lean)
    rt = np.array([up[1], -up[0]])
    T = _frame(P, up, rt)
    ht = 0.53 - 0.06 * p.hunch
    sit = min(1.0, max(0.0, p.sit))

    def sym(half, n=6):
        right = list(half)
        left = [(a_, -s_) for a_, s_ in reversed(right) if s_ > 1e-9]
        return spline(T(np.array(left + right)), n=n)

    parts = []
    for side, sx, lift in (("l", -1, p.lift_l), ("r", 1, p.lift_r)):
        hip = P + np.array([sx * 0.088, -0.03])
        k_sit = p.cross * np.array([sx * 0.34, P[1] + 0.03]) + (1 - p.cross) * np.array([sx * 0.11, P[1] + 0.3])
        a_sit = p.cross * np.array([sx * 0.14, P[1] - 0.04]) + (1 - p.cross) * np.array([sx * 0.1, P[1] - 0.06])
        knee = (1 - sit) * np.array([sx * 0.1, P[1] - 0.43 + 0.6 * lift]) + sit * k_sit
        ankle = (1 - sit) * np.array([sx * 0.1, 0.07 + lift]) + sit * a_sit
        leg = tube([hip, knee, ankle], [(0, 0.098), (0.4, 0.09), (1.0, 0.073), (1.3, 0.07), (2.0, 0.06)])
        parts.append(("leg_" + side, leg, darker(c["jeans"], 0.95, 0.03), None))
        if sit < 0.5:
            parts.append(("hem_" + side, tube([ankle + [0, 0.07], ankle + [0, 0.005]], [(0, 0.064), (1, 0.064)], n=12),
                          c["jeans_dk"], None))
        foot = ankle + np.array([sx * 0.05 * sit * p.cross, -0.048 * (1 - sit) - 0.01 * sit])
        if facing_us and sit < 0.5:
            toe = ellipse(foot + [0, 0.004], 0.066, 0.046, 0, 22)
            parts += [("shoe_" + side, toe, c["shoe"], None),
                      ("sole_" + side, ellipse(foot - [0, 0.03], 0.068, 0.016, 0, 16), c["sole"], None),
                      ("~lace", line(np.array([foot + [-0.02, 0.03], foot + [0.02, 0.03]]), 0.007), c["sole"], None)]
        else:
            parts.append(("shoe_" + side, ellipse(foot, 0.058 + 0.02 * sit, 0.048, 0, 20), c["shoe"], None))
            if lift > 0.02 and sit < 0.5:
                parts.append(("~sole", ellipse(foot - [0, 0.012], 0.05, 0.03 * min(1.0, lift / 0.08), 0, 16), c["sole"], None))
    seat = sym([(-0.135, 0.0), (-0.12, 0.12), (-0.05, 0.18), (0.04, 0.17)])
    parts.append(("seat", seat, c["jeans"], None))
    if not facing_us:
        for sx in (-1, 1):
            parts.append(("~back_pocket", spline(T([(-0.02, sx * 0.04), (-0.02, sx * 0.14), (-0.1, sx * 0.13), (-0.11, sx * 0.05)]), n=4),
                          c["jeans_dk"], seat))
    else:
        parts.append(("~fly", line(T(np.array([(0.0, 0.0), (-0.09, 0.005), (-0.12, 0.03)])), 0.007), c["jeans_dk"], None))
    torso = sym([(-0.065, 0.0), (-0.06, 0.185), (0.04, 0.198), (0.18, 0.2), (0.34, 0.208), (ht - 0.1, 0.205),
                 (ht - 0.04, 0.17), (ht - 0.005, 0.1), (ht + 0.005, 0.05), (ht + 0.005, 0.0)])
    hem = T([(-0.2, -0.4), (-0.015, -0.4), (-0.015, 0.4), (-0.2, 0.4)])
    neck_top = P + (ht + 0.1 - 0.07 * p.hunch) * up
    neck = tube([neck_top - 0.13 * up, neck_top + 0.03 * up], [(0, 0.05), (1, 0.046)], n=12)
    if facing_us:
        collar = sym([(ht - 0.05, 0.0), (ht - 0.03, 0.075), (ht + 0.01, 0.12), (ht + 0.035, 0.1), (ht + 0.04, 0.0)])
        pocket = sym([(0.015, 0.0), (0.02, 0.112), (0.075, 0.106), (0.165, 0.086), (0.17, 0.0)], n=4)
        strings = [line(T(np.array([(ht - 0.04, sx * 0.035), (ht - 0.12, sx * 0.04), (ht - 0.2, sx * 0.036)])), 0.012)
                   for sx in (-1, 1)]
        parts += [("neck", neck, c["skin_dk"], None), ("torso", torso, c["hoodie"], None), ("~hem", hem, c["cuff"], torso),
                  ("~pocket", pocket, darker(c["hoodie"], 0.9, 0.04), torso), ("collar", collar, c["hoodie"], None),
                  ("~collar_in", sym([(ht - 0.03, 0.0), (ht - 0.015, 0.06), (ht + 0.02, 0.085), (ht + 0.03, 0.0)], n=4),
                   c["hood_in"], None)]
        parts += [("~string", s_, c["string"], None) for s_ in strings]
    else:
        hood = sym([(ht - 0.15, 0.0), (ht - 0.13, 0.1), (ht - 0.05, 0.152), (ht + 0.025, 0.125), (ht + 0.04, 0.0)])
        fold = sym([(ht - 0.06, 0.0), (ht - 0.04, 0.072), (ht + 0.012, 0.092), (ht + 0.025, 0.0)])
        parts += [("neck", neck, c["skin_dk"], None), ("torso", torso, c["hoodie"], None), ("~hem", hem, c["cuff"], torso),
                  ("hood", hood, c["hoodie"], None), ("~hood_in", fold, c["hood_in"], hood)]
    arms = []
    for side, sx, a_up, a_el in (("l", -1, p.arm_l, p.el_l), ("r", 1, p.arm_r, p.el_r)):
        sh = T(np.array([[ht - 0.09, sx * 0.165]]))[0]
        el = sh + 0.28 * _dn(sx * a_up + p.lean)
        hd = _dn(sx * (a_up + a_el) + p.lean)
        wr = el + 0.25 * hd
        arm = tube([sh, el, wr - 0.02 * hd], [(0, 0.072), (0.45, 0.065), (1.0, 0.058), (1.5, 0.055), (2.0, 0.053)])
        cuff = tube([wr - 0.055 * hd, wr - 0.012 * hd], [(0, 0.055), (1, 0.055)], n=12)
        side_v = np.array([-hd[1], hd[0]]) * (-sx if facing_us else sx)
        F = _xy(wr, hd, side_v)
        hand = spline(F([(0.0, -0.03), (0.04, -0.038), (0.085, -0.03), (0.1, 0.0), (0.085, 0.03), (0.04, 0.036), (0.0, 0.03)]), n=6)
        thumb = tube(F([(0.015, 0.028), (0.045, 0.045), (0.058, 0.044)]), [(0, 0.015), (2, 0.012)], n=14)
        arms += [("arm_" + side, arm, c["hoodie"], None), ("cuff_" + side, cuff, c["cuff"], None),
                 ("hand_" + side, hand, darker(c["skin"], 0.96), None), ("thumb_" + side, thumb, darker(c["skin"], 0.96), None)]
    hup = _up(p.lean + p.head_roll)
    hrt = np.array([hup[1], -hup[0]])
    Hc = neck_top + 0.1 * hup
    return parts, arms, (Hc, hup, hrt)


def back(p: BackPose, pal=None) -> list:
    """Parts of the figure seen from behind, back to front."""
    c = _cols(pal)
    parts, arms, (Hc, hup, hrt) = _body(p, c, facing_us=False)
    H = _xy(Hc, hrt, hup)
    ang = math.degrees(math.atan2(hup[1], hup[0])) - 90
    head = ellipse(Hc, 0.13, 0.142, ang, 32)
    ears = [ellipse(H(np.array([[sx * (0.128 - 0.016 * max(0.0, sx * p.head_turn)), -0.012]]))[0], 0.025, 0.036, ang, 16)
            for sx in (-1, 1)]
    hair = spline(H([(-0.145, 0.0), (-0.142, 0.09), (-0.08, 0.168), (0.0, 0.188), (0.085, 0.17), (0.142, 0.095),
                     (0.148, 0.0), (0.128, -0.06), (0.09, -0.1), (0.045, -0.108), (0.015, -0.13), (-0.02, -0.11),
                     (-0.06, -0.118), (-0.1, -0.096), (-0.13, -0.058)]), n=6)
    shine = line(H(np.array([(-0.06, 0.15), (0.02, 0.17), (0.08, 0.15)])), 0.012, 0.006)
    return parts + [("ear_l", ears[0], darker(c["skin"], 0.92), None), ("ear_r", ears[1], darker(c["skin"], 0.92), None),
                    ("head", head, c["skin"], None), ("hair", hair, c["hair"], None),
                    ("~shine", shine, c["hair_hi"], None)] + arms


def front(p: BackPose, pal=None) -> list:
    """Parts of the figure facing us, back to front, with the face."""
    c = _cols(pal)
    parts, arms, (Hc, hup, hrt) = _body(p, c, facing_us=True)
    H = _xy(Hc, hrt, hup)
    ang = math.degrees(math.atan2(hup[1], hup[0])) - 90
    tx = 0.03 * p.head_turn

    def half(pts):
        right = [(x, y) for x, y in pts]
        left = [(-x, y) for x, y in reversed(pts) if x > 1e-9]
        return np.array(left + right)

    face = spline(H(half([(0.0, -0.15), (0.045, -0.142), (0.085, -0.115), (0.115, -0.07), (0.128, -0.02), (0.13, 0.04),
                          (0.112, 0.098), (0.07, 0.132), (0.0, 0.142)]) + [tx, 0]), n=6)
    ears = [ellipse(H(np.array([[sx * 0.128 + tx * (0.5 if sx * p.head_turn < 0 else 1.5), -0.01]]))[0], 0.026, 0.036, ang, 16)
            for sx in (-1, 1)]
    hair = spline(H(np.array([(-0.14, 0.0), (-0.148, 0.07), (-0.125, 0.135), (-0.07, 0.178), (0.0, 0.192),
                              (0.075, 0.185), (0.13, 0.15), (0.152, 0.085), (0.145, 0.0), (0.132, 0.02), (0.128, 0.07),
                              (0.108, 0.098), (0.07, 0.104), (0.035, 0.088), (0.0, 0.072), (-0.04, 0.046),
                              (-0.056, 0.078), (-0.095, 0.1), (-0.118, 0.085), (-0.13, 0.03)]) + [tx, 0]), n=6)
    shine = line(H(np.array([(0.03, 0.17), (0.09, 0.158), (0.125, 0.12)]) + [tx, 0]), 0.014, 0.006)
    out = parts + [("ear_l", ears[0], darker(c["skin"], 0.93), None), ("ear_r", ears[1], darker(c["skin"], 0.93), None),
                   ("head", face, c["skin"], None)]
    for sx in (-1, 1):
        out.append(("~blush", ellipse(H(np.array([[sx * 0.078 + tx, -0.045]]))[0], 0.026, 0.016, ang, 14),
                    mix(c["skin"], c["blush"], 0.45), None))
        out += _eye(H(np.array([[sx * 0.048 + tx * 1.3, 0.008]]))[0], 0.017, 0.026, p, c, ang)
        by = 0.06 + 0.016 * p.brows + 0.016 * p.quirk * sx
        inner, outer = by + 0.012 * p.brow_tilt, by - 0.004 * p.brow_tilt
        brow = line(H(np.array([(sx * 0.024 + tx, inner), (sx * 0.05 + tx, (inner + outer) / 2 + 0.006),
                                (sx * 0.074 + tx, outer)])), 0.012, 0.007)
        out.append(("~brow", brow, c["hair"], None))
    nose = line(H(np.array([(0.004 + tx * 1.2, -0.012), (0.012 + tx * 1.2, -0.04), (0.0 + tx * 1.2, -0.046)])), 0.008, 0.006)
    out.append(("~nose", nose, c["skin_dk"], None))
    sm = p.smile
    if p.mouth_open > 0.15:
        mo = ellipse(H(np.array([[tx, -0.088]]))[0], 0.022 + 0.008 * p.mouth_open, 0.008 + 0.022 * p.mouth_open, ang, 18)
        out += [("~mouth", mo, c["mouth"], None),
                ("~tongue", ellipse(H(np.array([[tx, -0.088 - 0.012 * p.mouth_open]]))[0], 0.012, 0.007 * p.mouth_open + 0.002, ang, 12),
                 mix(c["mouth"], c["blush"], 0.6), None)]
    else:
        mouth = line(H(np.array([(-0.026 + tx, -0.08 + 0.012 * sm), (tx, -0.088 - 0.008 * sm), (0.026 + tx, -0.08 + 0.012 * sm)])),
                     0.008, 0.008)
        out.append(("~mouth", mouth, c["mouth"], None))
    out += [("hair", hair, c["hair"], None), ("~shine", shine, c["hair_hi"], None)]
    return out + arms


# ---------------------------------------------------------------- drawing and shadows

def draw(P, parts, xf, light=(-1.0, -0.35), lw: float = 2.0, backlit: bool = False, line_k: float = 0.55, s=None, r=None):
    """Paint parts through xf (figure metres -> screen pixels): cel shade + rim, a coloured outline around solid
    parts, flat details on top. lw is the outline width in pixels (0 = none)."""
    from .toon import _path
    s = 7.0 if s is None else s
    r = 3.0 if r is None else r
    for name, poly, col, clip in parts:
        scr = xf(poly)
        if clip is not None:
            P.ctx.save()
            _path(P.ctx, xf(clip))
            P.ctx.clip()
        if name.startswith("~"):
            P.fill(scr, darker(col, 0.62, 0.16) if backlit else col)
        elif backlit:
            P.cel(scr, darker(col, 0.62, 0.16), darker(col, 0.48, 0.22), mix(col, (1.0, 0.78, 0.48), 0.75), light=light, s=s, r=r)
            if lw:
                P.stroke(scr, darker(col, 0.35, 0.25), lw)
        else:
            P.cel(scr, col, darker(col, 0.8, 0.1), mix(col, (1.0, 0.93, 0.8), 0.45), light=light, s=s, r=r)
            if lw:
                P.stroke(scr, darker(col, line_k, 0.2), lw)
        if clip is not None:
            P.ctx.restore()


def outline(parts) -> list[np.ndarray]:
    """The solid shapes of a figure (for casting its shadow)."""
    return [poly for name, poly, col, clip in parts if clip is None and not name.startswith("~")]


# ---------------------------------------------------------------- motion

def leg_ik(hip, ankle) -> tuple[float, float]:
    """(thigh angle, knee bend) that put the ankle on the target with the knee pointing forward."""
    v = np.asarray(ankle, np.float64) - np.asarray(hip, np.float64)
    d = min(max(float(np.hypot(*v)), 0.05), TH + SH - 1e-4)
    base = math.degrees(math.atan2(v[0], -v[1]))
    alpha = math.degrees(math.acos(max(-1.0, min(1.0, (TH * TH + d * d - SH * SH) / (2 * TH * d)))))
    beta = math.degrees(math.acos(max(-1.0, min(1.0, (TH * TH + SH * SH - d * d) / (2 * TH * SH)))))
    return base + alpha, 180.0 - beta


def plant(p: Pose, ankle_n, ankle_f, an_n: float | None = None, an_f: float | None = None) -> Pose:
    """p with both legs solved so the ankles sit on the targets (figure coords); feet flat unless pitched."""
    hn, kn = leg_ik(p.root, ankle_n)
    hf, kf = leg_ik(p.root, ankle_f)
    return replace(p, hp_n=hn, kn_n=kn, an_n=-(hn - kn) + (an_n or 0.0), hp_f=hf, kn_f=kf, an_f=-(hf - kf) + (an_f or 0.0))


def gait(t: float, speed: float = 1.3, step: float = 0.65, duty: float = 0.6, lift: float = 0.09,
         hip: float = 0.885, arm: float = 20.0, elbow: float = 14.0, lean: float = 3.0,
         base: Pose | None = None) -> tuple[Pose, float]:
    """A walk (duty ~0.6) or run (duty ~0.35) seen from the side at time t: (pose, distance travelled). The stance
    foot stays planted on the ground while the hip passes over it; the swing foot arcs forward."""
    period = 2 * step / speed
    ph = (t / period) % 1.0
    reach = step * duty
    ankles, pitch, planted = [], [], []
    for off in (0.0, 0.5):
        q = (ph - off) % 1.0
        if q < duty:
            sv = q / duty
            u, v, pt = reach * (1 - 2 * sv), 0.07, 0.0
            if sv > 0.75:
                k = (sv - 0.75) / 0.25
                v += 0.05 * k
                pt = -35 * k
            planted.append(u)
        else:
            sv = (q - duty) / (1 - duty)
            e = 0.5 - 0.5 * math.cos(math.pi * sv)
            u = -reach + 2 * reach * e
            v = 0.07 + lift * math.sin(math.pi * sv) ** 0.8
            pt = -25 * (1 - sv) + 12 * sv
        ankles.append((u, v))
        pitch.append(pt)
    h = hip
    for u in planted:
        h = min(h, 0.07 + math.sqrt(max(0.0, ((TH + SH) * 0.995) ** 2 - u * u)))
    swing = math.cos(2 * math.pi * ph)
    pose = replace(base or Pose(), root=(0.0, h), spine=lean + 1.2 * math.sin(4 * math.pi * ph),
                   sh_n=-arm * swing, el_n=elbow + 0.5 * max(0.0, -arm * swing),
                   sh_f=arm * swing, el_f=elbow + 0.5 * max(0.0, arm * swing))
    pose = plant(pose, ankles[0], ankles[1], pitch[0], pitch[1])
    return pose, speed * t


def walk(t: float, speed: float = 1.3, base: Pose | None = None) -> tuple[Pose, float]:
    return gait(t, speed=speed, step=0.62 * speed / 1.3, base=base)


def run(t: float, speed: float = 3.4, base: Pose | None = None) -> tuple[Pose, float]:
    return gait(t, speed=speed, step=1.05, duty=0.36, lift=0.24, hip=0.9, arm=42.0, elbow=88.0, lean=13.0, base=base)


def back_walk(t: float, speed: float = 1.2, step: float = 0.6, base: BackPose | None = None) -> tuple[BackPose, float]:
    """Walking away from the camera: feet lift in turn, hips sway, arms swing. (pose, distance travelled)."""
    period = 2 * step / speed
    ph = (t / period) % 1.0
    lift_l = 0.11 * max(0.0, math.sin(2 * math.pi * ph)) ** 1.2
    lift_r = 0.11 * max(0.0, -math.sin(2 * math.pi * ph)) ** 1.2
    sway = 3.0 * math.sin(2 * math.pi * ph)
    b = base or BackPose()
    pose = replace(b, root=(0.02 * math.sin(2 * math.pi * ph), 0.9 + 0.02 * abs(math.cos(2 * math.pi * ph))),
                   sit=0.0, lean=b.lean + sway * 0.5, lift_l=lift_l, lift_r=lift_r,
                   arm_l=8 + 6 * math.sin(2 * math.pi * ph), arm_r=8 - 6 * math.sin(2 * math.pi * ph), el_l=6, el_r=6)
    return pose, speed * t
