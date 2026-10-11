"""Ink-and-paint drawing of the 3D puppet, so the character reads as one drawn figure instead of a kit of parts.

The figure is split into a few meaningful pieces (hoodie, each sleeve, each hand, each leg, shoes, head, hair, neck,
phone). Each piece is one smooth outline built from the puppet's 3D volumes - unions of projected ellipsoids, ring
segments and tubes, so it can be concave where a body is (waist, chin, between the fingers). Pieces are painted back
to front in flat colour; each one receives the soft drop shadows of the pieces in front of it (hair on the forehead,
arm on the body), and gets a thin inner line only where it sits over another piece. One light then shades the whole
silhouette (a shadow side and a rim) and one thicker ink line runs round the outside. Hair is ray-cast against the
head's hair cap, so the hairline is exact and smooth, and pointed locks give it a shape.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cairo
import cv2
import numpy as np

from .figure import line as line2d
from .figure import tube as tube2d
from .puppet import (HS, PAL, PHONE, Pose3, View, _ellipsoid, _hull, _ring, _torso_point, grip_in_phone, hand_axes,
                     phone_frame, skeleton, torso_volume)
from .toon import _path, darker, hexc, mix

INK = (0.2, 0.13, 0.16)
ENV = ((0.36, 0.42, 0.55), (0.08, 0.08, 0.12))  # what a dark phone screen reflects (top, bottom)
SCREEN_BG = (0.95, 0.96, 0.985)
POSTS = [((1.0, 0.66, 0.42), (0.93, 0.36, 0.45)), ((0.42, 0.74, 0.97), (0.2, 0.44, 0.82)),
         ((0.6, 0.85, 0.48), (0.28, 0.6, 0.38)), ((1.0, 0.86, 0.42), (0.96, 0.58, 0.3)),
         ((0.82, 0.64, 0.97), (0.52, 0.4, 0.86))]  # the photos in his feed (top, bottom colours)


@dataclass
class Piece:
    name: str
    poly: np.ndarray
    col: tuple
    depth: float
    outline: bool = True
    casts: bool = True
    details: list = field(default_factory=list)  # (poly, rgb, alpha)
    no_line: np.ndarray | None = None  # region where this piece's inner line is not drawn (a sleeve inside its shoulder)
    painter: object = None  # optional f(P, backlit) that paints the inside of the piece (the phone's faces)


BOUNDS = (-160.0, -160.0, 1240.0, 2080.0)  # rasterise unions only over the frame plus a margin


def smooth_union(polys, scale=2.0, pad=6, smooth=6, close=0, bounds=None):
    """One smooth outline around screen polygons (rasterised, outer contour, smoothed)."""
    polys = [np.asarray(q, float) for q in polys if q is not None and len(q) >= 3]
    if not polys:
        return np.zeros((0, 2))
    allp = np.vstack(polys)
    bx0, by0, bx1, by1 = bounds or BOUNDS
    x0, y0 = np.maximum(allp.min(0) - pad, [bx0, by0])
    x1, y1 = np.minimum(allp.max(0) + pad, [bx1, by1])
    if x1 - x0 < 3 or y1 - y0 < 3:
        return np.zeros((0, 2))
    span = max(x1 - x0, y1 - y0)
    if span * scale > 3000:
        scale = 3000.0 / span
    w, h = int((x1 - x0) * scale) + 2, int((y1 - y0) * scale) + 2
    if w < 3 or h < 3:
        return _hull(allp)
    m = np.zeros((h, w), np.uint8)
    for q in polys:
        cv2.fillPoly(m, [np.round((q - [x0, y0]) * scale * 8).astype(np.int32)], 255, lineType=cv2.LINE_AA, shift=3)
    if close:
        k = int(close * scale) * 2 + 1
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    cs, _ = cv2.findContours((m > 127).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cs:
        return _hull(allp)
    cnt = max(cs, key=cv2.contourArea)[:, 0].astype(np.float64)
    if len(cnt) > 4 * smooth:
        ext = np.vstack([cnt[-smooth:], cnt, cnt[:smooth]])
        ker = np.ones(2 * smooth + 1) / (2 * smooth + 1)
        cnt = np.stack([np.convolve(ext[:, 0], ker, "valid"), np.convolve(ext[:, 1], ker, "valid")], 1)[::2]
    return cnt / scale + [x0, y0]


def _mul(P, polys, rgb, clip=None, sigma=1.0):
    P.shadow(polys, rgb, clip=clip, sigma=sigma)


# ---------------------------------------------------------------- pieces

def pieces(p: Pose3, cam, pal=None, env=None) -> tuple[list[Piece], dict]:
    c = {k: hexc(v) for k, v in {**PAL, **(pal or {})}.items()}
    J = skeleton(p)
    V = View(cam, p)
    out: list[Piece] = []
    info = dict(J=J, V=V)
    up = np.array([0.0, 0.0, 1.0])

    def tub(chain, radii, ref, round_end=True, round_start=True, n=40):
        k = V.px(ref, 1.0)
        return tube2d(V.scr(np.array(chain)), [(pos, r * k) for pos, r in radii], n=n, fillet=1.25, round_end=round_end,
                      round_start=round_start)

    def hullp(pts3):
        return _hull(V.scr(np.asarray(pts3)))

    def stroke(pts3, w_m, ref, w1=None):
        k = V.px(ref, 1.0)
        return line2d(V.scr(np.array(pts3)), w_m * k, (w1 if w1 is not None else w_m) * k)

    # ---- legs and shoes
    for side, sx in (("r", 1.0), ("l", -1.0)):
        hipj, K, A, F = J["Hip" + side], J["K" + side], J["A" + side], J["F" + side]
        mid = (hipj + K + A) / 3
        shin = (A - K) / (np.linalg.norm(A - K) + 1e-9)
        leg = tub([hipj, K, A - 0.004 * shin], [(0, 0.1), (0.35, 0.092), (1.0, 0.074), (1.3, 0.071), (1.85, 0.061), (2.0, 0.061)],
                  mid, round_end=False)
        hem = tub([A - 0.045 * shin, A + 0.004 * shin], [(0, 0.066), (1, 0.066)], A, round_end=False, round_start=False, n=12)
        d_leg = V.depth(mid)
        details = [(hem, mix(c["jeans"], c["jeans_dk"], 0.65), 1.0)]
        kn_bend = float(np.degrees(np.arccos(np.clip(((K - hipj) / (np.linalg.norm(K - hipj) + 1e-9)) @ shin, -1, 1))))
        if kn_bend > 35:  # creases behind a bent knee
            back = np.cross(np.cross(K - hipj, shin), shin)
            back = back / (np.linalg.norm(back) + 1e-9)
            for o in (-0.02, 0.02):
                details.append((stroke([K + back * 0.03 + (K - hipj) * 0.12 + np.cross(shin, back) * o,
                                        K + back * 0.05 + shin * 0.06 + np.cross(shin, back) * o], 0.007, K), c["jeans_dk"], 0.8))
        out.append(Piece("leg_" + side, leg, c["jeans"], d_leg, details=details))
        sd = np.cross(F, up)
        sd /= np.linalg.norm(sd) + 1e-9
        fu = np.cross(sd, F)

        def foot(pts):
            pts = np.asarray(pts, float)
            return A + pts[:, :1] * F + pts[:, 1:2] * sd + pts[:, 2:3] * fu

        sole, upper = [], []
        for fv, w in ((-0.09, 0.04), (-0.05, 0.046), (0.05, 0.052), (0.15, 0.05), (0.21, 0.04), (0.24, 0.02)):
            for s_ in (-1, 1):
                sole += [(fv, s_ * w, -0.088), (fv, s_ * w, -0.058)]
                hh = 0.025 if fv < 0.0 else (0.02 if fv < 0.1 else (-0.005 if fv < 0.2 else -0.03))
                upper += [(fv * 0.98, s_ * w * 0.92, -0.06), (fv * 0.95, s_ * w * 0.8, hh)]
        sole_p, upper_p = hullp(foot(sole)), hullp(foot(upper))
        shoe = smooth_union([sole_p, upper_p], smooth=3)
        dets = [(sole_p, c["sole"], 1.0)]
        k = V.px(A, 1)
        if V.facing(foot([(0.05, sx * 0.06, -0.02)])[0], foot([(0, sx, 0)])[0] - A) > 0.05:
            dets.append((line2d(V.scr(foot([(-0.055, sx * 0.05, -0.04), (0.02, sx * 0.054, -0.022), (0.1, sx * 0.05, -0.024)])),
                                0.024 * k, 0.008 * k), c["shoe_acc"], 1.0))
        if V.facing(foot([(0.06, 0, 0.04)])[0], fu * 0.6 + F * 0.4) > 0.05:
            for i in range(3):
                dets.append((line2d(V.scr(foot([(0.0 + 0.04 * i, -0.022, 0.02 - 0.008 * i), (0.0 + 0.04 * i, 0.022, 0.02 - 0.008 * i)])),
                                    0.008 * k), c["sole"], 1.0))
        out.append(Piece("shoe_" + side, shoe, c["shoe"], min(V.depth(A + F * 0.06 - fu * 0.05), d_leg) - 0.0003, details=dets))

    # ---- seat, neck, hoodie
    seat = []
    for s_, rx, yf, yb in ((-0.12, 0.17, 0.12, 0.13), (-0.04, 0.19, 0.14, 0.14)):
        cc, R = _torso_point(J, s_)
        seat.append(_ring(cc, R, rx, yf, yb))
    out.append(Piece("seat", hullp(np.vstack(seat)), c["jeans"], V.depth(J["P"]) + 0.02, outline=False))
    N, H, Rh, Ru = J["N"], J["H"], J["Rh"], J["Ru"]
    neck = tub([N - Ru @ np.array([0, 0, 0.08]), H - Rh @ np.array([0, 0, 0.1 * HS])], [(0, 0.042), (1, 0.04)], N, n=16)
    out.append(Piece("neck", neck, mix(c["skin"], c["skin_dk"], 0.75), V.depth(N) + 0.03, outline=False, casts=False))
    rings = torso_volume(J)
    segs = [hullp(np.vstack([rings[i], rings[i + 1]])) for i in range(len(rings) - 1)]
    collar_p = hullp(np.vstack([_ring(N - Ru @ np.array([0, 0, 0.035]), Ru, 0.105, 0.09, 0.095),
                                _ring(N + Ru @ np.array([0, 0, 0.036]), Ru, 0.084, 0.08, 0.084)]))
    hb, Rb = _torso_point(J, 0.47, 0.0, -0.11)
    hood_p = hullp(_ellipsoid(hb, Rb, 0.13, 0.075, 0.085))
    shoulders = [_hull(V.scr(_ellipsoid(J["S" + sd], Ru, 0.074, 0.074, 0.074, 16, 9))) for sd in "rl"]
    body = smooth_union(segs + [collar_p] + shoulders, smooth=5)
    dets = []
    hem = hullp(np.vstack([_ring(*_torso_point(J, -0.075), 0.205, 0.15, 0.14), _ring(*_torso_point(J, -0.02), 0.205, 0.15, 0.14)]))
    dets.append((hem, c["cuff"], 1.0))
    kb = V.px(J["C"], 1)
    front_c, Rf_ = _torso_point(J, 0.1, 0.0, 0.15)
    if V.facing(front_c, Rf_ @ np.array([0, 1.0, 0])) > 0.05:
        pk = [_torso_point(J, s_, x, 0.149)[0] for s_, x in ((0.02, -0.11), (0.02, 0.11), (0.08, 0.1), (0.17, 0.085),
                                                               (0.17, -0.085), (0.08, -0.1))]
        dets.append((V.scr(np.array(pk)), darker(c["hoodie"], 0.9, 0.04), 1.0))
        for sx in (-1, 1):
            st = [_torso_point(J, s_, sx * x, d)[0] for s_, x, d in ((0.5, 0.035, 0.09), (0.44, 0.04, 0.135), (0.34, 0.038, 0.142),
                                                                    (0.27, 0.036, 0.142))]
            dets.append((line2d(V.scr(np.array(st)), 0.013 * kb, 0.011 * kb), c["string"], 1.0))
    hood_dets = []
    top = hb + Rb @ np.array([0, 0.01, 0.075])
    if V.facing(top, Rb @ np.array([0, -0.4, 1.0])) > 0.0:
        hood_dets.append((V.scr(_ring(top, Rb, 0.085, 0.035, 0.03, n=16)), c["hood_in"], 1.0))
    if V.facing(hb, Rb @ np.array([0, -1.0, 0.2])) > 0.05:
        hood_dets.append((line2d(V.scr(np.array([hb + Rb @ np.array([0, -0.075, 0.07]), hb + Rb @ np.array([0, -0.078, 0.0]),
                                                  hb + Rb @ np.array([0, -0.06, -0.06])])), 0.006 * kb), c["seam"], 1.0))
    if V.facing(N, Ru @ np.array([0, 0.5, 1.0])) > 0.0:  # the hood's opening round the neck
        dets.append((V.scr(_ring(N + Ru @ np.array([0, 0, 0.037]), Ru, 0.067, 0.064, 0.068)), c["hood_in"], 1.0))
    for sx in (-1, 1):  # a soft bunch at the waist
        a_, Ra_ = _torso_point(J, 0.05, sx * 0.185, 0.04)
        b_, _ = _torso_point(J, 0.12, sx * 0.175, 0.07)
        dets.append((line2d(V.scr(np.array([a_, b_])), 0.007 * kb, 0.003 * kb), darker(c["hoodie"], 0.82, 0.06), 0.9))
    out.append(Piece("hoodie", body, c["hoodie"], V.depth(J["W"]), details=dets))
    out.append(Piece("hood", smooth_union([hood_p], smooth=3), c["hoodie"], V.depth(hb), details=hood_dets))

    # ---- sleeves and hands
    for side, sx in (("r", 1.0), ("l", -1.0)):
        S, E, Wr = J["S" + side], J["E" + side], J["W" + side]
        fa = (Wr - E) / (np.linalg.norm(Wr - E) + 1e-9)  # the forearm (the hand may turn at the wrist)
        mid = (S + E + Wr) / 3
        d_arm = V.depth(mid) - 0.06
        sleeve = tub([S, E, Wr - 0.012 * fa], [(0, 0.072), (0.45, 0.065), (1.0, 0.058), (1.5, 0.056), (2.0, 0.054)], mid)
        shoulder_zone = _hull(V.scr(_ellipsoid(S + (E - S) * 0.12, J["Ru"], 0.082, 0.082, 0.082, 16, 9)))
        k = V.px(Wr, 1)
        dets = [(tub([Wr - 0.06 * fa, Wr], [(0, 0.06), (1, 0.06)], Wr, round_end=False, round_start=False, n=12), c["cuff"], 1.0)]
        ua = (E - S) / (np.linalg.norm(E - S) + 1e-9)
        bend = float(np.degrees(np.arccos(np.clip(ua @ fa, -1, 1))))
        if bend > 25:  # folds inside the elbow
            inside = fa - ua
            inside = inside / (np.linalg.norm(inside) + 1e-9)
            for o in (-0.018, 0.012):
                side_v = np.cross(ua, inside)
                dets.append((line2d(V.scr(np.array([E + inside * 0.035 - ua * 0.05 + side_v * o, E + inside * 0.045 + fa * 0.03 + side_v * o])),
                                    0.007 * k, 0.003 * k), darker(c["hoodie"], 0.8, 0.06), 0.9))
        to_cam = V.R.T @ (cam.pos - V.world(Wr[None])[0])
        to_cam /= np.linalg.norm(to_cam) + 1e-9
        toward = float(fa @ to_cam)  # > 0: the hand comes out of the sleeve toward us
        if toward > 0.45:  # the sleeve points at us: a dark rim of its opening shows round the wrist
            a1 = np.cross(fa, up if abs(fa[2]) < 0.9 else np.array([1.0, 0, 0]))
            a1 /= np.linalg.norm(a1)
            a2 = np.cross(fa, a1)
            ring = [Wr + 0.045 * (math.cos(t) * a1 + math.sin(t) * a2) for t in np.linspace(0, 2 * math.pi, 20, endpoint=False)]
            dets.append((V.scr(np.array(ring)), darker(c["cuff"], 0.7, 0.04), 1.0))
        out.append(Piece("sleeve_" + side, sleeve, c["hoodie"], d_arm, details=dets, no_line=shoulder_zone))
        if p.phone > 0.5 and side == "r":
            grip_pieces, light = _phone_grip(p, J, V, c, d_arm, env or ENV, toward > 0.0)
            out += grip_pieces
            info["screen_light"] = light
            continue
        hd, across, palm_n = hand_axes(J, side)
        hand, thumb, finger_lines = _hand(V, Wr, hd, across, palm_n, getattr(p, side + "_grip", 0.3))
        d_hand = d_arm - 0.002 if toward > 0.0 else d_arm + 0.002  # in front of its cuff, or behind it
        out.append(Piece("hand_" + side, hand, c["skin"], d_hand,
                         details=[(fl, darker(c["skin"], 0.8, 0.07), 0.9) for fl in finger_lines]))
        out.append(Piece("thumb_" + side, thumb, c["skin"], d_hand - 0.001))

    # ---- head and hair
    out += _head(p, J, V, c, cam)
    fn = J["Rh"] @ np.array([0.0, 0.96, -0.28])
    info["face_seen"] = V.facing(J["H"] + J["Rh"] @ (HS * np.array([0.0, 0.12, -0.04])), fn)
    return out, info


# offset across the palm, first and second segment lengths, base radius (index .. little finger)
FINGERS = ((0.0285, 0.031, 0.027, 0.0102), (0.0095, 0.034, 0.031, 0.0105), (-0.0095, 0.032, 0.029, 0.0101),
           (-0.0275, 0.026, 0.022, 0.0089))


def _hand(V, W, hd, across, n, grip):
    """A cartoon hand: palm, four tapered fingers lying together and a thumb, curled by grip (0 open .. 1 fist).
    Returns (hand, thumb, lines between the fingers)."""
    k = V.px(W, 1)
    palm = _hull(V.scr(_ellipsoid(W + hd * 0.045, np.stack([across, n, hd], 1), 0.041, 0.019, 0.047, 14, 8)))
    curl = math.radians(12 + 82 * grip)
    paths, fingers, lines = [], [], []
    for o, l1, l2, r in FINGERS:
        base = W + hd * 0.075 + across * o
        d1 = hd * math.cos(curl * 0.55) + n * math.sin(curl * 0.55)
        j1 = base + d1 * l1
        d2 = hd * math.cos(curl * 1.2) + n * math.sin(curl * 1.2)
        tip = j1 + d2 * l2
        paths.append((base, j1, tip))
        fingers.append(tube2d(V.scr(np.array([base, j1, tip])), [(0, r * k), (1, r * 0.92 * k), (2, r * 0.78 * k)], n=20))
    for a, b in zip(paths, paths[1:]):  # where two fingers lie together, from the tips back to the middle joints
        mids = [(a[1] + b[1]) / 2, (a[1] + a[2] + b[1] + b[2]) / 4, (a[2] + b[2]) / 2]
        lines.append(line2d(V.scr(np.array(mids)), 0.0032 * k, 0.0018 * k))
    hand = smooth_union([palm] + fingers, smooth=3)
    tb = W + hd * 0.02 + across * 0.032 + n * 0.008
    dt = hd * 0.5 + across * 0.75 + n * (0.28 + 0.35 * grip)
    dt /= np.linalg.norm(dt)
    t1 = tb + dt * 0.03
    dt2 = dt * math.cos(0.45 * grip) + n * math.sin(0.45 * grip)
    dt2 /= np.linalg.norm(dt2)
    thumb = tube2d(V.scr(np.array([tb, t1, t1 + dt2 * 0.027])), [(0, 0.0135 * k), (1, 0.0124 * k), (2, 0.0098 * k)], n=18)
    return hand, thumb, lines


def _truncate(path, length):
    """The first `length` metres of a polyline."""
    out = [path[0]]
    left = length
    for a, b in zip(path, path[1:]):
        seg = float(np.linalg.norm(b - a))
        if seg >= left:
            out.append(a + (b - a) * (left / max(seg, 1e-9)))
            return out
        out.append(b)
        left -= seg
    return out


def _rrect(w, h, r, n=7):
    pts = []
    for cx, cy, a0 in ((w - r, h - r, 0.0), (-w + r, h - r, 90.0), (-w + r, -h + r, 180.0), (w - r, -h + r, 270.0)):
        for a in np.radians(np.linspace(a0, a0 + 90.0, n)):
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return np.array(pts)


def _phone_grip(p, J, V, c, d_sleeve, env, toward):
    """The phone in his right hand: the palm on its back, the fingers wrapping round its left edge, the thumb on the
    screen. Pieces are layered for the side we see (screen or back) around the sleeve; plus the screen's light."""
    ctr, Rph = phone_frame(J)
    PW, PL, PT = PHONE
    Rhp, hd_p, wrist_p = grip_in_phone()
    across_p = Rhp @ np.array([0.0, 1.0, 0.0])
    n_p = np.array([0.0, 0.0, 1.0])

    def F(pts):  # phone frame -> screen
        return V.scr(ctr + np.asarray(pts, float) @ Rph.T)

    screen_side = V.facing(ctr, Rph[:, 2]) > 0.0
    k = V.px(ctr, 1.0)
    # the hand
    palm = _hull(F(_ellipsoid(wrist_p + hd_p * 0.045, np.stack([across_p, n_p, hd_p], 1), 0.041, 0.017, 0.047, 14, 8)))
    heel = _hull(F(_ellipsoid(wrist_p + hd_p * 0.012, np.stack([across_p, n_p, hd_p], 1), 0.034, 0.02, 0.026, 12, 7)))
    back_parts, front_parts, gaps = [palm, heel], [], []
    knuckles = []
    for i, (o, l1, l2, r) in enumerate(FINGERS):
        K = wrist_p + hd_p * 0.076 + across_p * o
        K[2] = -PT - r * 0.95
        s_edge = max(0.0, (-PW + 0.003 - K[0]) / hd_p[0])
        A = K + hd_p * s_edge
        B = np.array([-PW - r * 0.8, A[1] + 0.004, 0.0])
        C = np.array([-PW + 0.0095, A[1] + 0.0075, PT + r * 0.8])
        path = _truncate([K - hd_p * 0.01, K, A, B, C], l1 + l2 + 0.012)
        knuckles.append(K)
        cut = next((j for j, q in enumerate(path) if q[2] >= -1e-6), None)
        back = path if cut is None else path[:cut + 1]
        if len(back) >= 2:
            n_b = len(back) - 1
            back_parts.append(tube2d(F(back), [(0, r * k), (n_b, r * 0.92 * k)], n=24, round_start=False))
        if cut is not None and cut < len(path) - 1:
            front = path[cut:]
            front_parts.append(tube2d(F(front), [(0, r * 0.92 * k), (len(front) - 1, r * 0.8 * k)], n=16))
    for a, b in zip(knuckles, knuckles[1:]):  # the gaps between the fingers on the back of the phone
        m = (a + b) / 2
        gaps.append(line2d(F([m + hd_p * 0.012, m + hd_p * 0.04]), 0.003 * k, 0.0016 * k))
    sc = float(np.clip(p.scroll, 0.0, 1.0))
    TB = wrist_p + hd_p * 0.022 + across_p * 0.03 + n_p * 0.006
    TE = np.array([PW + 0.0045, TB[1] + 0.02, 0.0])
    TT = np.array([PW - 0.027, -0.018 + 0.036 * sc, PT + 0.0098])
    back_parts.append(tube2d(F([TB, TE]), [(0, 0.0136 * k), (1, 0.0128 * k)], n=12))
    thumb = tube2d(F([TE, (TE + TT) / 2 + np.array([0, 0, 0.003]), TT]), [(0, 0.0128 * k), (1, 0.0118 * k), (2, 0.0099 * k)], n=18)
    hand_back = smooth_union(back_parts, smooth=3)
    tips = smooth_union(front_parts, smooth=2) if front_parts else np.zeros((0, 2))
    # the phone: a rounded slab, its screen or its back towards us
    rr = _rrect(PW, PL, 0.0105)
    face_z = PT if screen_side else -PT
    slab = _hull(np.vstack([F(np.c_[rr, np.full(len(rr), PT)]), F(np.c_[rr, np.full(len(rr), -PT)])]))
    face = F(np.c_[rr, np.full(len(rr), face_z)])
    inset = 0.0034
    scr_rr = _rrect(PW - inset, PL - inset, 0.0075)
    scr = F(np.c_[scr_rr, np.full(len(scr_rr), PT + 0.0002)])
    lit = float(np.clip(p.screen, 0.0, 1.0))
    top_c, bot_c = env

    def paint_phone(P, backlit):
        dk = (lambda col: darker(col, 0.62, 0.16)) if backlit else (lambda col: col)
        if not screen_side:
            P.fill(face, dk(c["case"]))
            b2 = _rrect(0.0125, 0.0125, 0.005) + [PW - 0.019, PL - 0.02]
            bump = F(np.c_[b2, np.full(len(b2), -PT - 0.0012)])
            P.fill(bump, dk(c["case_dk"]))
            for dy in (0.0055, -0.0055):
                lens = F([(PW - 0.019 + 0.0036 * math.cos(a), PL - 0.02 + dy + 0.0036 * math.sin(a), -PT - 0.0018)
                          for a in np.linspace(0, 2 * math.pi, 14, endpoint=False)])
                P.fill(lens, dk(c["lens"]))
            return
        P.fill(face, dk(c["bezel"]))
        P.ctx.save()
        _path(P.ctx, scr)
        P.ctx.clip()
        P.fill(scr, (0.05, 0.06, 0.08))
        if lit > 0.02:
            _feed(P, F, PW - inset, PL - inset, PT + 0.0004, p.feed, lit)
        if lit < 0.98:  # a dark glass that mirrors the sky
            p0, p1 = F([(0.0, PL, PT)])[0], F([(0.0, -PL, PT)])[0]
            P.gradient(scr, [(0.0, top_c), (0.55, mix(top_c, bot_c, 0.6)), (1.0, bot_c)], p0, p1, a=0.75 * (1 - lit))
        streak = F([(-PW, 0.02, PT + 0.0004), (-PW, 0.045, PT + 0.0004), (PW, -0.01, PT + 0.0004), (PW, -0.035, PT + 0.0004)])
        P.fill(streak, (1.0, 1.0, 1.0), 0.05 + 0.12 * (1 - lit))
        P.ctx.restore()

    d = d_sleeve
    if screen_side:  # we look over his hand at the screen: palm and sleeve behind, then phone, fingertips, thumb
        depths = dict(hand_back=d - 0.0005 if toward else d + 0.002, phone=d - 0.001, tips=d - 0.0015, thumb=d - 0.002)
    else:  # we look at the back of the phone and of his hand (the hand over its cuff if it comes toward us)
        depths = dict(tips=d + 0.003, thumb=d + 0.0025, phone=d + 0.002, hand_back=d - 0.0005 if toward else d + 0.001)
    skin_line = darker(c["skin"], 0.8, 0.07)
    out = [Piece("hand_r", hand_back, c["skin"], depths["hand_back"], details=[(g, skin_line, 0.85) for g in gaps]),
           Piece("phone", slab, c["case_dk"], depths["phone"], painter=paint_phone),
           Piece("thumb_r", thumb, c["skin"], depths["thumb"])]
    if len(tips) >= 3:
        out.append(Piece("fingertips", tips, c["skin"], depths["tips"]))
    light = None
    if lit > 0.05:
        sc_px = F([(0.0, 0.01, PT)])[0]
        face_px = V.scr(J["H"] + J["Rh"] @ (HS * np.array([0.0, 0.12, -0.05])))
        reach_px = float(np.linalg.norm(face_px - sc_px))
        light = dict(c=sc_px + 0.45 * (face_px - sc_px), r=max(30.0, 1.25 * reach_px), a=0.26 * lit, col=(0.55, 0.78, 1.0),
                     screen_side=screen_side)
    return out, light


def _feed(P, F, sw, sh, z, feed, alpha):
    """A social feed on the screen: posts (an avatar, a photo, a like) that scroll up as feed grows."""
    pitch, photo_h = 0.086, 0.058
    top = sh - 0.01
    P.fill(F([(-sw, sh, z), (sw, sh, z), (sw, -sh, z), (-sw, -sh, z)]), SCREEN_BG, alpha)
    first = int(math.floor(feed)) - 1
    for j in range(first, first + 4):
        y0 = top - (j - feed) * pitch  # this post's top edge
        if y0 - pitch > sh or y0 < -sh - 0.01:
            continue
        cols = POSTS[j % len(POSTS)]
        av = F([(-sw + 0.009 + 0.0042 * math.cos(a), y0 - 0.0065 + 0.0042 * math.sin(a), z) for a in np.linspace(0, 2 * math.pi, 14, endpoint=False)])
        P.fill(av, mix(cols[0], cols[1], 0.5), alpha)
        P.fill(F([(-sw + 0.017, y0 - 0.0052, z), (-sw + 0.044, y0 - 0.0052, z), (-sw + 0.044, y0 - 0.0082, z), (-sw + 0.017, y0 - 0.0082, z)]),
               (0.78, 0.8, 0.84), alpha)
        y1, y2 = y0 - 0.0135, y0 - 0.0135 - photo_h
        photo = F([(-sw, y1, z), (sw, y1, z), (sw, y2, z), (-sw, y2, z)])
        P.gradient(photo, [(0.0, cols[0]), (1.0, cols[1])], F([(0, y1, z)])[0], F([(0, y2, z)])[0], a=alpha)
        hill = F([(-sw, y2 + 0.016, z)] + [(x, y2 + 0.012 + 0.012 * math.sin((x / sw + j) * 2.2), z) for x in np.linspace(-sw, sw, 9)]
                 + [(sw, y2, z), (-sw, y2, z)])
        P.fill(hill, darker(cols[1], 0.75, 0.05), alpha * 0.55)
        hy = y2 - 0.0072
        heart = F([(-sw + 0.0085 + 0.0034 * 16 * math.sin(a) ** 3 / 16, hy + 0.0034 * (13 * math.cos(a) - 5 * math.cos(2 * a)
                                                                                      - 2 * math.cos(3 * a) - math.cos(4 * a)) / 16, z)
                   for a in np.linspace(0, 2 * math.pi, 24, endpoint=False)])
        P.fill(heart, (0.93, 0.3, 0.38) if j % 2 == 0 else (0.72, 0.74, 0.78), alpha)
        P.fill(F([(-sw + 0.016, hy + 0.0015, z), (-sw + 0.05, hy + 0.0015, z), (-sw + 0.05, hy - 0.0013, z), (-sw + 0.016, hy - 0.0013, z)]),
               (0.8, 0.82, 0.86), alpha)
    P.fill(F([(-0.012, -sh + 0.004, z), (0.012, -sh + 0.004, z), (0.012, -sh + 0.0058, z), (-0.012, -sh + 0.0058, z)]),
           (0.25, 0.27, 0.3), alpha * 0.6)


HAIR_LOCKS = [  # small tufts that break the outline (head coords, unscaled): path, base radius, tip radius
    ([(0.112, -0.01, 0.1), (0.13, 0.0, 0.07), (0.126, 0.008, 0.046)], 0.022, 0.008),
    ([(-0.112, -0.01, 0.1), (-0.13, 0.0, 0.07), (-0.126, 0.008, 0.046)], 0.022, 0.008),
]
# the hair: a cap plus a quiff at the front-top (centre, radii in head coords, unscaled)
HAIR_VOLUMES = [((0.0, -0.012, 0.022), (0.138, 0.15, 0.148)), ((0.02, 0.05, 0.1), (0.112, 0.1, 0.085))]


def hairline(x, y):
    """Height (head coords) above which the skull is hair: forehead high, nape low, soft bangs swept to his right,
    sideburns over the ears and two little points at the nape."""
    base = 0.012 + 0.62 * y - 0.9 * np.maximum(0, -y - 0.05)
    front = np.clip((y - 0.05) / 0.06, 0, 1)
    bangs = (0.042 * np.exp(-((x - 0.05) / 0.022) ** 2) + 0.05 * np.exp(-((x + 0.004) / 0.024) ** 2)
             + 0.03 * np.exp(-((x + 0.058) / 0.018) ** 2))
    side = np.clip((np.abs(x) - 0.095) / 0.02, 0, 1) * np.clip(1 - np.abs(y - 0.012) / 0.045, 0, 1)
    back = np.clip((-y - 0.08) / 0.04, 0, 1)
    nape = 0.028 * np.exp(-((x - 0.042) / 0.02) ** 2) + 0.028 * np.exp(-((x + 0.042) / 0.02) ** 2)
    return base - front * bangs - side * 0.04 - back * nape


def _hair_mask_poly(J, V, cam, extra_polys):
    """The visible hair: camera rays hit the hair volumes (cap + quiff) where they are above the hairline; plus
    the side tufts. Exact and smooth at any angle."""
    H, Rh = J["H"], J["Rh"]
    Rw = V.R @ Rh
    vols = []
    pts = []
    for off, rad in HAIR_VOLUMES:
        off, radii = np.array(off), HS * np.array(rad)
        Cc = H + Rh @ (HS * off)
        vols.append((off, radii, V.world(Cc[None])[0]))
        pts.append(V.scr(_ellipsoid(Cc, Rh, *radii, 16, 10)))
    allp = np.vstack(pts + [q for q in extra_polys if len(q)])
    x0, y0 = np.floor(allp.min(0)) - 4
    x1, y1 = np.ceil(allp.max(0)) + 4
    x0, y0 = max(x0, -160), max(y0, -160)
    x1, y1 = min(x1, cam.w + 160), min(y1, cam.h + 160)
    span = max(x1 - x0, y1 - y0)
    if span < 4 or x1 - x0 < 4 or y1 - y0 < 4:
        return np.zeros((0, 2))
    step = 1 if span < 360 else (2 if span < 900 else 3)
    xs = np.arange(x0, x1, step) + step / 2
    ys = np.arange(y0, y1, step) + step / 2
    if len(xs) < 3 or len(ys) < 3:
        return np.zeros((0, 2))
    uu, vv = np.meshgrid(xs, ys)
    d = np.stack([(uu - cam.cx) / cam.f, -(vv - cam.cy) / cam.f, np.ones_like(uu)], -1) @ cam.R
    d /= np.linalg.norm(d, axis=-1, keepdims=True)
    mask = np.zeros(uu.shape, bool)
    for off, radii, Cw in vols:
        o_l = (Rw.T @ (cam.pos - Cw)) / radii
        d_l = (d @ Rw) / radii
        a = np.sum(d_l * d_l, -1)
        b = 2 * (d_l @ o_l)
        cc = float(o_l @ o_l) - 1
        disc = b * b - 4 * a * cc
        t = (-b - np.sqrt(np.maximum(disc, 0))) / (2 * a)
        rel = (o_l + t[..., None] * d_l) * radii / HS + off
        mask |= (disc > 0) & (t > 0) & (rel[..., 2] > hairline(rel[..., 0], rel[..., 1]))
    m = mask.astype(np.uint8) * 255
    res = 2 if span < 360 else 1
    big = cv2.resize(m, (int(m.shape[1] * step * res), int(m.shape[0] * step * res)), interpolation=cv2.INTER_LINEAR)
    big = cv2.GaussianBlur(big, (0, 0), 1.2 * res)
    for q in extra_polys:
        if len(q) >= 3:
            cv2.fillPoly(big, [np.round((q - [x0, y0]) * res * 8).astype(np.int32)], 255, lineType=cv2.LINE_AA, shift=3)
    cs, _ = cv2.findContours((big > 127).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cs:
        return np.zeros((0, 2))
    cnt = max(cs, key=cv2.contourArea)[:, 0].astype(np.float64)
    sm = 3
    if len(cnt) > 4 * sm:
        ext = np.vstack([cnt[-sm:], cnt, cnt[:sm]])
        ker = np.ones(2 * sm + 1) / (2 * sm + 1)
        cnt = np.stack([np.convolve(ext[:, 0], ker, "valid"), np.convolve(ext[:, 1], ker, "valid")], 1)
    return cnt / res + [x0, y0]


def _head(p, J, V, c, cam):
    H, Rh = J["H"], J["Rh"]

    def P_(x, y, z):
        return H + Rh @ (HS * np.array([x, y, z]))

    depth_h = V.depth(H)
    k = V.px(H, 1) * HS
    nu = int(np.clip(k * 0.12, 20, 72))
    skull = _hull(V.scr(_ellipsoid(H, Rh, 0.122 * HS, 0.13 * HS, 0.14 * HS, nu, nu // 2 + 1)))
    jaw = _hull(V.scr(_ellipsoid(P_(0, 0.03, -0.068), Rh, 0.092 * HS, 0.088 * HS, 0.078 * HS, nu, nu // 2 + 1)))
    parts = [skull, jaw]
    ear_det = []
    for sx in (-1, 1):
        ear = _hull(V.scr(_ellipsoid(P_(sx * 0.118, -0.028, -0.016), Rh, 0.014 * HS, 0.022 * HS, 0.032 * HS, 10, 7)))
        side_on = V.facing(P_(sx * 0.12, -0.02, 0), Rh @ np.array([sx, 0, 0]))
        if side_on > -0.25:
            parts.append(ear)
        if side_on > 0.35:
            inner = _hull(V.scr(_ellipsoid(P_(sx * 0.126, -0.028, -0.016), Rh, 0.006 * HS, 0.011 * HS, 0.018 * HS, 8, 6)))
            ear_det.append((inner, darker(c["skin"], 0.84, 0.06), 1.0))
    fwd_h = Rh @ np.array([0, 1.0, 0])
    fwd_h = V.R @ np.array([fwd_h[0], fwd_h[1], 0.0])
    to_c = V.cam.pos - V.world(P_(0, 0.13, -0.03)[None])[0]
    to_c[2] = 0.0
    front_on = float(fwd_h @ to_c / ((np.linalg.norm(fwd_h) + 1e-9) * (np.linalg.norm(to_c) + 1e-9)))
    if front_on < 0.45:  # in profile the nose is part of the head's outline
        parts.append(_hull(V.scr(_ellipsoid(P_(0, 0.132, -0.03), Rh, 0.013 * HS, 0.02 * HS, 0.02 * HS, 10, 7))))
    head = smooth_union(parts, smooth=5)
    dets = list(ear_det) + _face(p, P_, Rh, V, c, k, front_on)
    out = [Piece("head", head, c["skin"], depth_h, details=dets)]
    locks = []
    kl = V.px(H, 1) * HS
    for path, r, tip in HAIR_LOCKS:
        pts3 = np.array([P_(*q) for q in path])
        if V.depth(pts3[-1]) < depth_h + 0.04:
            locks.append(tube2d(V.scr(pts3), [(0, r * kl), (1, r * 0.8 * kl), (2, tip * kl)], n=18))
    hair = _hair_mask_poly(J, V, cam, locks)
    if len(hair) >= 3:
        shine = []
        if V.facing(P_(0, 0.03, 0.15), Rh @ np.array([0, 0.3, 1])) > 0:
            shine.append((line2d(V.scr(np.array([P_(0.07, 0.03, 0.152), P_(0.02, 0.07, 0.156), P_(-0.045, 0.085, 0.142)])),
                                 0.014 * kl, 0.005 * kl), c["hair_hi"], 1.0))
        if V.facing(P_(0, -0.09, 0.11), Rh @ np.array([0, -1, 0.6])) > 0.1:
            for dx in (-0.045, 0.0, 0.045):
                shine.append((line2d(V.scr(np.array([P_(dx, -0.13, 0.06), P_(dx * 1.1, -0.145, -0.01), P_(dx * 1.2, -0.13, -0.06)])),
                                     0.005 * kl, 0.002 * kl), c["hair_dk"], 1.0))
        out.append(Piece("hair", hair, c["hair"], depth_h - 0.0005, details=shine))
    return out


def _face(p, P_, Rh, V, c, k, front_on):
    out = []

    def on(x, z):
        y = 0.13 * math.sqrt(max(0.0, 1 - (x / 0.122) ** 2 - (z / 0.14) ** 2)) if z > -0.05 else \
            0.03 + 0.088 * math.sqrt(max(0.0, 1 - (x / 0.092) ** 2 - ((z + 0.068) / 0.078) ** 2))
        return P_(x, y, z)

    def vis(x, z):
        n = Rh @ np.array([x / 0.122 ** 2, 0.13 / 0.13 ** 2, z / 0.14 ** 2])
        return V.facing(on(x, z), n)

    def cosv(x, z):  # how squarely a point of the face looks at the camera (1 = straight on, 0 = on the outline)
        n = Rh @ np.array([x / 0.122 ** 2, 0.13 / 0.13 ** 2, z / 0.14 ** 2])
        return V.facing(on(x, z), n / np.linalg.norm(n))

    gx, gy = p.gaze
    for sx in (-1, 1):
        ex, ez = sx * 0.048, 0.008
        if cosv(ex, ez) < 0.22:  # an eye right on the outline reads as a mark on the cheek: leave it out
            continue
        if p.happy > 0.5 or p.eyes < 0.12:
            kk = 1 if p.happy > 0.5 else -0.15
            arc = V.scr(np.array([on(ex - 0.019, ez - kk * 0.005), on(ex, ez + kk * 0.014), on(ex + 0.019, ez - kk * 0.005)]))
            out.append((line2d(arc, 0.008 * k), c["eye"], 1.0))
        else:
            open_ = min(1.35, max(0.15, p.eyes))
            cx, cz = ex + gx * 0.004, ez + gy * 0.005
            ring = [on(cx + 0.017 * (1 + 0.15 * max(0, p.eyes - 1)) * math.cos(a), cz + 0.026 * open_ * math.sin(a))
                    for a in np.linspace(0, 2 * np.pi, 20, endpoint=False)]
            out.append((V.scr(np.array(ring)), c["eye"], 1.0))
            hi = [on(cx + 0.007 + 0.006 * math.cos(a), cz + 0.01 * open_ + 0.006 * math.sin(a)) for a in np.linspace(0, 2 * np.pi, 10, endpoint=False)]
            out.append((V.scr(np.array(hi)), (1.0, 1.0, 1.0), 1.0))
        by = 0.062 + 0.016 * p.brows + 0.016 * p.quirk * sx
        inner, outer = by + 0.012 * p.brow_tilt, by - 0.004 * p.brow_tilt
        brow = V.scr(np.array([on(sx * 0.024, inner), on(sx * 0.05, (inner + outer) / 2 + 0.006), on(sx * 0.076, outer)]))
        out.append((line2d(brow, 0.013 * k, 0.007 * k), c["hair_dk"], 1.0))
        if vis(sx * 0.078, -0.045) > 0.15:
            bl = [on(sx * 0.078 + 0.024 * math.cos(a), -0.045 + 0.013 * math.sin(a)) for a in np.linspace(0, 2 * np.pi, 16, endpoint=False)]
            out.append((V.scr(np.array(bl)), mix(c["skin"], c["blush"], 0.6), 0.45))
    if front_on >= 0.45:  # nose: a small soft hook from the front and three-quarter
        m = V.scr(np.array([on(0.006, -0.026), on(0.012, -0.041), on(0.0, -0.047)]))
        out.append((line2d(m, 0.006 * k, 0.005 * k), darker(c["skin"], 0.86, 0.05), 0.9))
    if vis(0.0, -0.088) > 0.0:
        sm = p.smile
        if p.mouth_open > 0.15:
            ring = [on(0.022 * (1 + 0.35 * p.mouth_open) * math.cos(a), -0.09 + (0.008 + 0.022 * p.mouth_open) * math.sin(a))
                    for a in np.linspace(0, 2 * np.pi, 18, endpoint=False)]
            out.append((V.scr(np.array(ring)), c["mouth"], 1.0))
        else:
            mpts = V.scr(np.array([on(-0.026, -0.082 + 0.012 * sm), on(0.0, -0.09 - 0.008 * sm), on(0.026, -0.082 + 0.012 * sm)]))
            out.append((line2d(mpts, 0.008 * k), c["mouth"], 1.0))
    return out


# ---------------------------------------------------------------- painting

def draw(P, cam, p: Pose3, light=(-1.0, -0.4), lw=None, backlit=False, pal=None, rim=True, env=None):
    """Paint the character as one drawn figure (see the module docstring). env = (top, bottom) colours a dark phone
    screen reflects."""
    ps, info = pieces(p, cam, pal, env)
    glow = info.get("screen_light")
    ps = [q for q in ps if q.poly is not None and len(q.poly) >= 3]
    if not ps:
        return
    order = sorted(ps, key=lambda q: -q.depth)
    J, V = info["J"], info["V"]
    k = V.px(J["H"], 1)
    lw_out = lw if lw is not None else float(np.clip(k / 230.0, 1.3, 5.0))
    lw_in = lw_out * 0.45
    L = np.asarray(light, float)
    L = L / (np.linalg.norm(L) + 1e-9)
    off = -L * float(np.clip(k * 0.03, 1.5, 22.0))
    shade = (0.8, 0.76, 0.9) if not backlit else (0.86, 0.8, 0.9)
    for i, q in enumerate(order):
        base = q.col if not backlit else darker(q.col, 0.6, 0.18)
        P.fill(q.poly, base)
        if q.painter is not None:
            P.ctx.save()
            _path(P.ctx, q.poly)
            P.ctx.clip()
            q.painter(P, backlit)
            P.ctx.restore()
        front = [r.poly + off for r in order[i + 1:] if r.casts and r.name != q.name]
        if front:
            _mul(P, front, shade, clip=q.poly, sigma=max(0.8, lw_out * 0.6))
        if glow is not None and q.name in ("head", "thumb_r", "fingertips") and (q.name != "head" or info.get("face_seen", 0) > 0):
            a = glow["a"] * (1.0 if q.name == "head" else 0.7) * (1.0 if q.name != "head" else min(1.0, info["face_seen"] * 2.5))
            P.glow(glow["c"], glow["r"], glow["col"], a, pts=q.poly, screen=True)
        if q.details:
            P.ctx.save()
            _path(P.ctx, q.poly)
            P.ctx.clip()
            for poly, rgb, a in q.details:
                if poly is not None and len(poly) >= 3:
                    P.fill(poly, darker(rgb, 0.62, 0.16) if backlit else rgb, a)
            P.ctx.restore()
        if q.outline:
            if q.no_line is not None and len(q.no_line) >= 3:
                P.ctx.save()
                P.ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
                P.ctx.rectangle(-1e5, -1e5, 2e5, 2e5)
                _path(P.ctx, q.no_line)
                P.ctx.clip()
                P.stroke(q.poly, darker(base, 0.5, 0.15), lw_in)
                P.ctx.restore()
            else:
                P.stroke(q.poly, darker(base, 0.5, 0.15), lw_in)
    sil = smooth_union([q.poly for q in order], smooth=3)
    if len(sil) >= 3:
        s_px = float(np.clip(k * 0.05, 2.0, 30.0))
        c = P.ctx
        c.save()
        _path(c, sil)
        c.clip()
        c.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        c.rectangle(-1e5, -1e5, 2e5, 2e5)
        _path(c, sil + L * s_px)
        c.set_operator(cairo.OPERATOR_MULTIPLY)
        c.set_source_rgb(*((0.82, 0.78, 0.9) if not backlit else (0.9, 0.85, 0.92)))
        c.fill()
        if rim:
            c.set_operator(cairo.OPERATOR_SCREEN)
            c.rectangle(-1e5, -1e5, 2e5, 2e5)
            _path(c, sil - L * s_px * (0.35 if not backlit else 0.5))
            col = (0.45, 0.38, 0.28) if not backlit else (0.85, 0.55, 0.3)
            c.set_source_rgb(*col)
            c.fill()
        c.restore()
        P.stroke(sil, INK if not backlit else (0.18, 0.1, 0.12), lw_out)
