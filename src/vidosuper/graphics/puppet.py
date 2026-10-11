"""A 3D cartoon puppet drawn flat: the lead character as a real 3D body that any camera can shoot.

The body is a skeleton (pelvis, two-piece spine, neck, head, arms, legs) posed with angles or with planted feet (IK),
dressed with simple 3D volumes - tapered tubes for limbs, stacked rings for the hoodie, ellipsoids for the head, hair
locks, hands and sneakers. For a camera, every volume is projected and outlined (convex hulls of its surface points,
or a 2D tube along the projected limb), sorted back to front and painted with cel shading and a coloured outline, so
the look stays the flat cartoon of the 2D films while the camera can move anywhere. The same volumes, projected along
the sun onto a wall or the ground, make the character's shadow - for any pose, including the shadow's own poses.

Character frame: x = his right, y = where he faces, z = up; metres; feet on z = 0. World: x right, y into the picture,
z up. yaw = 0 faces into the picture (+y), 180 faces the camera, 90 faces screen right.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields, replace

import cv2
import numpy as np

from .toon import _path, darker, hexc, mix

PAL = dict(hoodie="#E2483A", cuff="#C83C30", hood_in="#9C3026", seam="#B33A2E", string="#F7EBDD", jeans="#2F4677",
           jeans_dk="#24385F", skin="#C98A63", skin_dk="#A86E4E", blush="#E08A78", hair="#3A2A2C", hair_dk="#271B1E",
           hair_hi="#5E4648", shoe="#F4F0E8", sole="#D6CDBF", shoe_acc="#E2483A", eye="#231E26", mouth="#7A3B33",
           phone="#1B1C25", screen="#BFE3FF", case="#7DD3BD", case_dk="#4E9E8A", bezel="#17191F", lens="#22302F")

TH, SH = 0.4, 0.4  # thigh, shin
HS = 1.18  # head scale (cartoon proportions)
UA, FA = 0.28, 0.26  # upper arm, forearm
ANKLE = 0.07


# ---------------------------------------------------------------- pose

@dataclass
class Pose3:
    pos: tuple = (0.0, 0.0, 0.0)  # world ground point under the pelvis
    yaw: float = 180.0  # facing direction (0 = into the picture, 180 = toward the camera)
    hip: float = 0.895  # pelvis height above pos
    lean: float = 0.0  # whole torso pitch forward (+) about the hips
    roll: float = 0.0  # torso side lean (+ = toward his right)
    twist: float = 0.0  # upper body turned (+ = toward his left) relative to the hips
    chest: float = 0.0  # extra hunch of the upper spine
    neck: float = 0.0  # head nod forward (+)
    head_yaw: float = 0.0  # head turn (+ = toward his left)
    head_roll: float = 0.0
    # arms (r = his right): swing forward (+) / back, out to the side, elbow bend, upper-arm twist
    r_fwd: float = 4.0
    r_out: float = 5.0
    r_el: float = 10.0
    r_tw: float = 0.0
    l_fwd: float = 4.0
    l_out: float = 5.0
    l_el: float = 10.0
    l_tw: float = 0.0
    r_wrist: tuple = (0.0, 0.0, 0.0)  # hand turned at the wrist: roll about the forearm, flex, side bend (degrees)
    l_wrist: tuple = (0.0, 0.0, 0.0)
    # legs: thigh forward (+), out, knee bend, ankle pitch (+ toes up); feet: optional planted targets
    r_hip: float = 0.0
    r_hout: float = 3.0
    r_knee: float = 0.0
    r_ank: float = 0.0
    l_hip: float = 0.0
    l_hout: float = 3.0
    l_knee: float = 0.0
    l_ank: float = 0.0
    r_foot: tuple | None = None  # planted ankle (character frame), solved with IK
    l_foot: tuple | None = None
    r_toe: float = 0.0  # planted foot's toe-down pitch (push-off)
    l_toe: float = 0.0
    r_grip: float = 0.3  # fingers: 0 open .. 1 fist
    l_grip: float = 0.3
    phone: float = 0.0  # phone in his right hand (1)
    screen: float = 1.0  # phone screen light
    scroll: float = 0.0  # his thumb on the screen: 0 resting .. 1 flicked up
    feed: float = 0.0  # how far the feed on the screen has scrolled (posts)
    # face
    eyes: float = 1.0
    brows: float = 0.0
    brow_tilt: float = 0.0
    quirk: float = 0.0
    smile: float = 0.15
    mouth_open: float = 0.0
    happy: float = 0.0
    gaze: tuple = (0.0, 0.0)


def blend(a: Pose3, b: Pose3, t: float) -> Pose3:
    out = {}
    for f in fields(Pose3):
        x, y = getattr(a, f.name), getattr(b, f.name)
        if x is None or y is None:
            out[f.name] = y if t >= 0.5 else x
        elif isinstance(x, tuple):
            out[f.name] = tuple(float(p + (q - p) * t) for p, q in zip(x, y))
        else:
            out[f.name] = x + (y - x) * t
    return Pose3(**out)


def _rx(a):
    c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _ry(a):
    c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _rz(a):
    c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


DOWN = np.array([0.0, 0.0, -1.0])


def _ik(hip, target, hint):
    """Knee for a two-bone leg reaching target, bending toward hint."""
    v = target - hip
    d = float(np.linalg.norm(v))
    d = min(max(d, 0.05), TH + SH - 1e-4)
    u = v / (np.linalg.norm(v) + 1e-12)
    a = math.acos(max(-1.0, min(1.0, (TH * TH + d * d - SH * SH) / (2 * TH * d))))
    h = hint - u * float(hint @ u)
    h /= np.linalg.norm(h) + 1e-12
    knee = hip + TH * (math.cos(a) * u + math.sin(a) * h)
    ankle = hip + u * d
    return knee, ankle


def skeleton(p: Pose3) -> dict:
    """Joint positions in the character frame (x his right, y forward, z up) and the frames of the body parts."""
    J = {}
    P = np.array([0.0, 0.0, p.hip])
    Rp = _ry(p.roll) @ _rx(-p.lean)  # pelvis + lower spine (+lean = forward)
    W = P + Rp @ np.array([0, 0, 0.2])
    Ru = Rp @ _rz(p.twist) @ _rx(-p.chest)  # upper body
    C = W + Ru @ np.array([0, 0, 0.3])
    N = C + Ru @ np.array([0, 0, 0.05])
    Rh = Ru @ _rx(-p.neck) @ _rz(p.head_yaw) @ _ry(p.head_roll)
    H = N + Rh @ np.array([0, 0.018, 0.155 * HS])
    J.update(P=P, W=W, C=C, N=N, H=H, Rp=Rp, Ru=Ru, Rh=Rh)
    for side, sx in (("r", 1.0), ("l", -1.0)):
        fwd, out, el, tw = (getattr(p, f"{side}_{k}") for k in ("fwd", "out", "el", "tw"))
        S = C + Ru @ np.array([sx * 0.168, -0.005, -0.035])
        Ra = Ru @ _rx(fwd) @ _ry(-sx * out) @ _rz(sx * tw)
        E = S + Ra @ (DOWN * UA)
        Rf = Ra @ _rx(el)
        Wr = E + Rf @ (DOWN * FA)
        wr = getattr(p, f"{side}_wrist")
        Rw = Rf @ _rz(sx * wr[0]) @ _rx(wr[1]) @ _ry(sx * wr[2])  # the hand (the forearm's frame turned at the wrist)
        J.update({f"S{side}": S, f"E{side}": E, f"W{side}": Wr, f"Ra{side}": Ra, f"Rf{side}": Rf, f"Rw{side}": Rw})
        hipj = P + _ry(p.roll * 0.3) @ np.array([sx * 0.09, 0.0, -0.035])
        foot = getattr(p, f"{side}_foot")
        if foot is not None:
            target = np.array(foot, float)
            K, A = _ik(hipj, target, np.array([0.0, 1.0, 0.25]))
            shin = (A - K) / (np.linalg.norm(A - K) + 1e-9)
            toe = getattr(p, f"{side}_toe")
            fdir = _rx(toe) @ np.array([0.0, 1.0, 0.0])
        else:
            hp, ho, kn, an = (getattr(p, f"{side}_{k}") for k in ("hip", "hout", "knee", "ank"))
            Rl = _rx(hp) @ _ry(-sx * ho)
            K = hipj + Rl @ (DOWN * TH)
            Rs = Rl @ _rx(-kn)
            A = K + Rs @ (DOWN * SH)
            fdir = Rs @ _rx(an) @ np.array([0.0, 1.0, 0.0])
        J.update({f"Hip{side}": hipj, f"K{side}": K, f"A{side}": A, f"F{side}": fdir / (np.linalg.norm(fdir) + 1e-9)})
    return J


# ---------------------------------------------------------------- hands: frames, reaching, holding the phone

def hand_frame(side: str, fingers, palm) -> np.ndarray:
    """The rotation of a hand (as in J['Rw' + side]) whose fingers point along `fingers` and palm faces `palm`."""
    sx = 1.0 if side == "r" else -1.0
    hd = np.asarray(fingers, float)
    hd = hd / np.linalg.norm(hd)
    pn = np.asarray(palm, float)
    pn = pn - hd * float(pn @ hd)
    pn = pn / np.linalg.norm(pn)
    across = np.cross(hd, pn) if sx > 0 else np.cross(pn, hd)
    return np.stack([-sx * pn, across, -hd], 1)


def hand_axes(J, side: str):
    """(fingers direction, thumb side, palm normal) of a hand, character frame."""
    sx = 1.0 if side == "r" else -1.0
    Rw = J["Rw" + side]
    return Rw @ DOWN, Rw @ np.array([0.0, 1.0, 0.0]), Rw @ np.array([-sx, 0.0, 0.0])


def reach(p: Pose3, side: str, wrist, hand=None, hint=(0.4, -0.3, -1.0)) -> Pose3:
    """The arm angles that put this wrist (character frame) where asked, the elbow bending toward hint (x is mirrored
    for the left arm); with `hand` (a rotation from hand_frame) the wrist turns so the hand faces that way too."""
    sx = 1.0 if side == "r" else -1.0
    J = skeleton(p)
    S, Ru = J["S" + side], J["Ru"]
    v = np.asarray(wrist, float) - S
    d = float(np.clip(np.linalg.norm(v), 0.06, UA + FA - 1e-4))
    u = v / (np.linalg.norm(v) + 1e-12)
    a = math.acos(max(-1.0, min(1.0, (UA * UA + d * d - FA * FA) / (2 * UA * d))))
    h = np.array([sx * hint[0], hint[1], hint[2]], float)
    h = h - u * float(h @ u)
    h /= np.linalg.norm(h) + 1e-12
    E = S + UA * (math.cos(a) * u + math.sin(a) * h)
    ua = Ru.T @ ((E - S) / UA)
    fa = Ru.T @ ((S + u * d - E) / FA)
    out = math.degrees(math.asin(max(-1.0, min(1.0, sx * ua[0]))))
    fwd = math.degrees(math.atan2(ua[1], -ua[2]))
    el = math.degrees(math.acos(max(-1.0, min(1.0, float(ua @ fa)))))
    f0 = (_rx(fwd) @ _ry(-sx * out)).T @ fa
    tw = sx * math.degrees(math.atan2(-f0[0], f0[1]))
    q = replace(p, **{f"{side}_fwd": fwd, f"{side}_out": out, f"{side}_el": el, f"{side}_tw": tw})
    if hand is not None:
        Rw = skeleton(q)["Rf" + side].T @ np.asarray(hand, float)  # = Rz(a) Rx(b) Ry(c)
        b_ = math.degrees(math.asin(max(-1.0, min(1.0, Rw[2, 1]))))
        c_ = math.degrees(math.atan2(-Rw[2, 0], Rw[2, 2]))
        a_ = math.degrees(math.atan2(-Rw[0, 1], Rw[1, 1]))
        q = replace(q, **{f"{side}_wrist": (sx * a_, b_, sx * c_)})
    return q


PHONE = (0.044, 0.091, 0.0062)  # half width, half length, half thickness (m): a little big, so it reads
PHONE_GRIP = 35.0  # the right hand lies diagonally across the phone's back (degrees)
PALM_ON_PHONE = np.array([0.014, -0.026, -0.0228])  # palm centre in the phone frame, just behind its back


def grip_in_phone():
    """The right hand holding the phone, in the phone's frame (x to its right edge, y up its length, z out of the
    screen): (hand rotation, fingers direction, wrist)."""
    th = math.radians(PHONE_GRIP)
    hd = np.array([-math.cos(th), math.sin(th), 0.0])
    return hand_frame("r", hd, (0.0, 0.0, 1.0)), hd, PALM_ON_PHONE - hd * 0.045


def phone_frame(J):
    """The phone in his right hand: (centre, rotation whose columns are its across, up and screen axes), character
    frame."""
    Rhp, _, wrist = grip_in_phone()
    Rph = J["Rwr"] @ Rhp.T
    return J["Wr"] - Rph @ wrist, Rph


def hold_phone(p: Pose3, dist: float = 0.28, down: float = 22.0, roll: float = 0.0) -> Pose3:
    """Put the phone where he looks - dist metres from his eyes, down degrees below his face's forward - with the
    screen turned to him, and solve his right arm to hold it there."""
    J = skeleton(p)
    H, Rh = J["H"], J["Rh"]
    eyes = H + Rh @ (HS * np.array([0.0, 0.11, 0.008]))
    a = math.radians(down)
    look = Rh @ np.array([0.0, math.cos(a), -math.sin(a)])
    z = -look
    up = Rh @ np.array([0.0, 0.0, 1.0])
    y = up - z * float(up @ z)
    y /= np.linalg.norm(y)
    x = np.cross(y, z)
    r = math.radians(roll)
    x, y = x * math.cos(r) + y * math.sin(r), y * math.cos(r) - x * math.sin(r)
    Rph = np.stack([x, y, z], 1)
    Rhp, _, wrist = grip_in_phone()
    ctr = eyes + look * dist
    return reach(replace(p, phone=max(p.phone, 1.0)), "r", ctr + Rph @ wrist, Rph @ Rhp, hint=(0.45, -0.25, -1.0))


def rest_hand(p: Pose3, side: str = "l", along: float = 0.66, inward: float = 16.0) -> Pose3:
    """A hand lying on top of its thigh, fingers toward the knee and a little inward."""
    sx = 1.0 if side == "r" else -1.0
    J = skeleton(p)
    hipj, K = J["Hip" + side], J["K" + side]
    tdir = (K - hipj) / (np.linalg.norm(K - hipj) + 1e-9)
    n_up = np.array([0.0, 0.0, 1.0]) - tdir * tdir[2]
    n_up /= np.linalg.norm(n_up)
    a = math.radians(inward)
    hd = tdir * math.cos(a) + np.cross(n_up, tdir) * (sx * math.sin(a))
    palm_c = hipj + (K - hipj) * along + n_up * 0.098
    return reach(p, side, palm_c - hd * 0.045, hand_frame(side, hd, -n_up), hint=(0.6, -0.45, -0.5))


def pocket_hand(p: Pose3, side: str = "r", inside: float = 0.0) -> Pose3:
    """The hand at the side opening of the hoodie's front pocket, fingers going in (inside 0..1)."""
    sx = 1.0 if side == "r" else -1.0
    J = skeleton(p)
    Ru = J["Ru"]
    opening, _ = _torso_point(J, 0.1, sx * 0.11, 0.16)
    hd = Ru @ np.array([-sx, 0.15, -0.35])
    hd /= np.linalg.norm(hd)
    palm_c = opening + hd * (0.01 + 0.05 * inside)
    return reach(p, side, palm_c - hd * 0.045, hand_frame(side, hd, Ru @ np.array([0.0, -1.0, 0.0])),
                 hint=(0.5, -0.4, -0.8))


# ---------------------------------------------------------------- volumes

def _ellipsoid(c, R, rx, ry, rz, nu=14, nv=9):
    u = np.linspace(0, 2 * np.pi, nu, endpoint=False)
    v = np.linspace(-np.pi / 2, np.pi / 2, nv)
    uu, vv = np.meshgrid(u, v)
    pts = np.stack([rx * np.cos(vv) * np.cos(uu), ry * np.cos(vv) * np.sin(uu), rz * np.sin(vv)], -1).reshape(-1, 3)
    return c + pts @ R.T


def _ring(c, R, rx, ry_front, ry_back, n=18):
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    y = np.where(np.sin(a) >= 0, ry_front, ry_back) * np.sin(a)
    pts = np.stack([rx * np.cos(a), y, np.zeros_like(a)], -1)
    return c + pts @ R.T


TORSO_RINGS = [(-0.065, 0.196, 0.142, 0.132), (0.05, 0.2, 0.148, 0.138), (0.2, 0.202, 0.146, 0.14),
               (0.36, 0.208, 0.14, 0.146), (0.45, 0.198, 0.122, 0.135), (0.505, 0.13, 0.085, 0.095)]


def _torso_point(J, s, side=0.0, depth=0.0):
    """A point on/in the torso at spine height s (0 = pelvis, 0.5 = shoulders)."""
    if s <= 0.2:
        base = J["P"] + J["Rp"] @ np.array([0, 0, s])
        R = J["Rp"]
    else:
        base = J["W"] + J["Ru"] @ np.array([0, 0, s - 0.2])
        R = J["Ru"]
    return base + R @ np.array([side, depth, 0.0]), R


def torso_volume(J):
    rings = []
    for s, rx, yf, yb in TORSO_RINGS:
        c, R = _torso_point(J, s)
        rings.append(_ring(c, R, rx, yf, yb))
    return rings


@dataclass
class Item:
    """One drawable piece: screen polygon, colour, depth, kind ('solid' cel+outline, 'flat' detail, 'line' stroke)."""
    poly: np.ndarray
    col: tuple
    depth: float
    kind: str = "solid"
    clip: np.ndarray | None = None
    name: str = ""


class View:
    """Projection helper for one camera and one placed character."""

    def __init__(self, cam, p: Pose3):
        self.cam = cam
        a = math.radians(p.yaw)
        fwd = np.array([math.sin(a), math.cos(a), 0.0])
        right = np.array([math.cos(a), -math.sin(a), 0.0])
        self.R = np.stack([right, fwd, [0.0, 0.0, 1.0]], 1)  # character -> world rotation
        self.o = np.asarray(p.pos, float)

    def world(self, X):
        return self.o + np.asarray(X, float) @ self.R.T

    def scr(self, X):
        return self.cam(self.world(X))

    def depth(self, X):
        return float(np.mean(self.cam.cam(self.world(np.atleast_2d(X)))[:, 2]))

    def px(self, X, metres):
        """Pixels per metre at a point."""
        return self.cam.f / max(1e-3, float(self.cam.cam(self.world(np.atleast_2d(X)))[0, 2])) * metres

    def facing(self, X, normal):
        """How much a surface normal (character frame) faces the camera: > 0 visible."""
        Xw = self.world(np.atleast_2d(X))[0]
        n = self.R @ np.asarray(normal, float)
        to_cam = self.cam.pos - Xw
        return float(n @ to_cam / (np.linalg.norm(to_cam) + 1e-9))


def _hull(pts2d):
    if len(pts2d) < 3:
        return pts2d
    s = max(1.0, float(np.abs(pts2d).max()))
    return cv2.convexHull((pts2d / s).astype(np.float32))[:, 0].astype(np.float64) * s


def union(polys, pad=4, scale=2.0):
    """One outline around a group of screen polygons (rasterised at 2x, outer contour, lightly smoothed)."""
    polys = [np.asarray(q, float) for q in polys if q is not None and len(q) >= 3]
    if not polys:
        return np.zeros((0, 2))
    allp = np.vstack(polys)
    x0, y0 = allp.min(0) - pad
    x1, y1 = allp.max(0) + pad
    w, h = int((x1 - x0) * scale) + 2, int((y1 - y0) * scale) + 2
    if w * h > 6e6 or w < 3 or h < 3:
        return _hull(allp)
    m = np.zeros((h, w), np.uint8)
    for q in polys:
        cv2.fillPoly(m, [np.round((q - [x0, y0]) * scale * 8).astype(np.int32)], 255, lineType=cv2.LINE_AA, shift=3)
    cs, _ = cv2.findContours((m > 127).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cs:
        return _hull(allp)
    cnt = max(cs, key=cv2.contourArea)[:, 0].astype(np.float64)
    if len(cnt) > 12:
        k = 7
        ext = np.vstack([cnt[-k:], cnt, cnt[:k]])
        cnt = np.stack([np.convolve(ext[:, 0], np.ones(2 * k + 1) / (2 * k + 1), "valid"),
                        np.convolve(ext[:, 1], np.ones(2 * k + 1) / (2 * k + 1), "valid")], 1)
        cnt = cnt[::2]
    return cnt / scale + [x0, y0]


def _tube2d(pts, radii, n=40, fillet=1.25, round_end=True, round_start=True):
    from .figure import tube
    return tube(pts, radii, n=n, fillet=fillet, round_end=round_end, round_start=round_start)


def _stroke2d(pts, w0, w1=None):
    from .figure import line
    return line(np.asarray(pts, float), w0, w1)


# ---------------------------------------------------------------- building the drawing

def build(p: Pose3, cam, pal=None) -> list[Item]:
    """Every piece of the character for this camera, unsorted (call `paint` to draw)."""
    c = {k: hexc(v) for k, v in (pal or PAL).items()}
    J = skeleton(p)
    V = View(cam, p)
    items: list[Item] = []
    to_cam_char = V.R.T @ (cam.pos - V.world(J["C"][None])[0])  # camera direction in the character frame

    def add(poly, col, X, kind="solid", clip=None, bias=0.0, name=""):
        items.append(Item(np.asarray(poly, float), col, V.depth(X) + bias, kind, clip, name))

    def limb(chain, radii, X_depth, col, bias=0.0, name="", round_end=True):
        scr = V.scr(np.array(chain))
        k = V.px(X_depth, 1.0)
        poly = _tube2d(scr, [(pos, r * k) for pos, r in radii], round_end=round_end)
        add(poly, col, X_depth, bias=bias, name=name)
        return poly

    # legs and sneakers
    for side, sx in (("r", 1.0), ("l", -1.0)):
        hipj, K, A, F = J["Hip" + side], J["K" + side], J["A" + side], J["F" + side]
        mid = (hipj + K + A) / 3
        shin = (A - K) / (np.linalg.norm(A - K) + 1e-9)
        limb([hipj, K, A - 0.005 * shin], [(0, 0.1), (0.35, 0.092), (1.0, 0.073), (1.3, 0.071), (1.85, 0.06), (2.0, 0.06)],
             mid, c["jeans"], name="leg", round_end=False)
        leg_depth = V.depth(mid)
        hem_s = V.scr(np.array([A - 0.035 * shin, A + 0.004 * shin]))
        items.append(Item(_tube2d(hem_s, [(0, 0.062 * V.px(A, 1)), (1, 0.062 * V.px(A, 1))], n=12, round_end=False, round_start=False),
                          mix(c["jeans"], c["jeans_dk"], 0.6), leg_depth - 0.0001, "solid"))
        up = np.array([0.0, 0.0, 1.0])
        sd = np.cross(F, up)
        sd /= np.linalg.norm(sd) + 1e-9
        fu = np.cross(sd, F)

        def foot(pts):
            pts = np.asarray(pts, float)
            return A + pts[:, :1] * F + pts[:, 1:2] * sd + pts[:, 2:3] * fu

        sole = []
        upper = []
        for fv, w in ((-0.09, 0.04), (-0.05, 0.046), (0.05, 0.052), (0.15, 0.05), (0.21, 0.04), (0.24, 0.02)):
            for s_ in (-1, 1):
                sole += [(fv, s_ * w, -0.088), (fv, s_ * w, -0.058)]
                h = 0.025 if fv < 0.0 else (0.02 if fv < 0.1 else (-0.005 if fv < 0.2 else -0.03))
                upper += [(fv * 0.98, s_ * w * 0.92, -0.06), (fv * 0.95, s_ * w * 0.8, h)]
        shoe_d = min(V.depth(A + F * 0.06 - fu * 0.05), leg_depth) - 0.0002
        items.append(Item(_hull(V.scr(foot(sole))), c["sole"], shoe_d, "solid"))
        items.append(Item(_hull(V.scr(foot(upper))), c["shoe"], shoe_d - 0.0001, "solid"))
        outer = foot([(0.05, sx * 0.06, -0.02)])[0]
        if V.facing(outer, foot([(0, sx, 0)])[0] - A) > 0.05:
            stripe = V.scr(foot([(-0.055, sx * 0.05, -0.04), (0.02, sx * 0.054, -0.022), (0.1, sx * 0.05, -0.024)]))
            k = V.px(A, 1)
            items.append(Item(_stroke2d(stripe, 0.024 * k, 0.008 * k), c["shoe_acc"], shoe_d - 0.0002, "flat"))
        if V.facing(foot([(0.06, 0, 0.04)])[0], fu * 0.6 + F * 0.4) > 0.05:
            k = V.px(A, 1)
            for i in range(3):
                lace = V.scr(foot([(0.0 + 0.04 * i, -0.022, 0.02 - 0.008 * i), (0.0 + 0.04 * i, 0.022, 0.02 - 0.008 * i)]))
                items.append(Item(_stroke2d(lace, 0.008 * k), c["sole"], shoe_d - 0.0003, "flat"))

    # seat and torso
    seat = []
    for s, rx, yf, yb in ((-0.12, 0.17, 0.12, 0.13), (-0.04, 0.19, 0.14, 0.14)):
        cc, R = _torso_point(J, s)
        seat.append(_ring(cc, R, rx, yf, yb))
    add(_hull(V.scr(np.vstack(seat))), c["jeans"], J["P"], bias=0.02)
    rings = torso_volume(J)
    torso = _hull(V.scr(np.vstack(rings)))
    add(torso, c["hoodie"], J["W"], name="torso")
    hem = _hull(V.scr(np.vstack([_ring(*_torso_point(J, -0.07), 0.2, 0.146, 0.136), _ring(*_torso_point(J, -0.015), 0.2, 0.146, 0.136)])))
    add(hem, c["cuff"], J["W"], kind="flat", clip=torso, bias=-0.001)
    front_c, Rf_ = _torso_point(J, 0.1, 0.0, 0.15)
    if V.facing(front_c, Rf_ @ np.array([0, 1.0, 0])) > 0.05:  # kangaroo pocket and drawstrings
        pk = [_torso_point(J, s, x, 0.149)[0] for s, x in ((0.02, -0.11), (0.02, 0.11), (0.08, 0.1), (0.17, 0.085),
                                                             (0.17, -0.085), (0.08, -0.1))]
        add(V.scr(np.array(pk)), darker(c["hoodie"], 0.9, 0.04), J["W"], kind="flat", clip=torso, bias=-0.002)
        k = V.px(J["C"], 1)
        for sx in (-1, 1):
            st = [_torso_point(J, s, sx * x, d)[0] for s, x, d in ((0.5, 0.035, 0.09), (0.44, 0.04, 0.135), (0.34, 0.038, 0.142),
                                                                  (0.27, 0.036, 0.142))]
            add(_stroke2d(V.scr(np.array(st)), 0.013 * k), c["string"], J["W"], kind="flat", bias=-0.003)
    # hood lying on the back, with its opening facing up and a centre seam
    hb, Rb = _torso_point(J, 0.47, 0.0, -0.11)
    hood = _ellipsoid(hb, Rb, 0.13, 0.075, 0.085)
    hood_poly = _hull(V.scr(hood))
    hood_item_depth = hb
    add(hood_poly, c["hoodie"], hood_item_depth, bias=0.0, name="hood")
    top = hb + Rb @ np.array([0, 0.01, 0.075])
    if V.facing(top, Rb @ np.array([0, -0.4, 1.0])) > 0.0:
        ring = _ring(top, Rb, 0.085, 0.035, 0.03, n=16)
        add(V.scr(ring), c["hood_in"], hood_item_depth, kind="flat", clip=hood_poly, bias=-0.001)
    if V.facing(hb, Rb @ np.array([0, -1.0, 0.2])) > 0.05:
        seam = V.scr(np.array([hb + Rb @ np.array([0, -0.075, 0.07]), hb + Rb @ np.array([0, -0.078, 0.0]),
                               hb + Rb @ np.array([0, -0.06, -0.06])]))
        add(_stroke2d(seam, 0.006 * V.px(hb, 1)), c["seam"], hood_item_depth, kind="flat", bias=-0.002)
    # neck and collar
    N, H, Rh = J["N"], J["H"], J["Rh"]
    limb([N - J["Ru"] @ np.array([0, 0, 0.08]), H - Rh @ np.array([0, 0, 0.1 * HS])], [(0, 0.047), (1, 0.045)], N, c["skin_dk"],
         bias=0.03)
    collar = _hull(V.scr(np.vstack([_ring(N - J["Ru"] @ np.array([0, 0, 0.035]), J["Ru"], 0.1, 0.085, 0.09),
                                    _ring(N + J["Ru"] @ np.array([0, 0, 0.01]), J["Ru"], 0.08, 0.07, 0.075)])))
    add(collar, c["hoodie"], N, bias=0.01, name="collar")
    if V.facing(N, J["Ru"] @ np.array([0, 0.5, 1.0])) > 0.0:
        add(V.scr(_ring(N + J["Ru"] @ np.array([0, 0, 0.012]), J["Ru"], 0.066, 0.058, 0.062)), c["hood_in"], N, kind="flat",
            clip=collar, bias=0.009)

    items += _head(p, J, V, c)

    # arms
    for side, sx in (("r", 1.0), ("l", -1.0)):
        S, E, Wr = J["S" + side], J["E" + side], J["W" + side]
        hd = (Wr - E) / (np.linalg.norm(Wr - E) + 1e-9)
        mid = (S + E + Wr) / 3
        bias = -0.06
        limb([S, E, Wr - 0.02 * hd], [(0, 0.071), (0.45, 0.064), (1.0, 0.057), (1.5, 0.055), (2.0, 0.052)], mid, c["hoodie"],
             bias=bias, name="arm")
        k = V.px(Wr, 1)
        add(_tube2d(V.scr(np.array([Wr - 0.055 * hd, Wr - 0.012 * hd])), [(0, 0.055 * k), (1, 0.055 * k)], n=12), c["cuff"], mid,
            bias=bias - 0.001)
        Ra = J["Rf" + side]
        across = Ra @ np.array([1.0, 0, 0])
        palm_n = Ra @ np.array([0, sx * 1.0, 0])
        hand = _ellipsoid(Wr + hd * 0.05, np.stack([across, np.cross(hd, across), hd], 1), 0.042, 0.022, 0.055, 12, 7)
        add(_hull(V.scr(hand)), c["skin"], mid, bias=bias - 0.002, name="hand")
        thumb = V.scr(np.array([Wr + hd * 0.02 + across * 0.03, Wr + hd * 0.06 + across * 0.045]))
        if p.phone > 0.5 and side == "r":
            items += _phone(p, J, V, c, Wr, hd, across, mid, bias - 0.003)
        add(_tube2d(thumb, [(0, 0.016 * k), (1, 0.013 * k)], n=12), c["skin"], mid, bias=bias - 0.004)
    return items


def _phone(p, J, V, c, Wr, hd, across, mid, bias):
    ctr = Wr + hd * 0.07
    face_n = J["H"] - ctr
    face_n /= np.linalg.norm(face_n) + 1e-9
    long_ax = np.cross(face_n, across)
    long_ax /= np.linalg.norm(long_ax) + 1e-9
    wide = np.cross(long_ax, face_n)
    corners = [ctr + a * long_ax * 0.075 + b * wide * 0.036 + e * face_n * 0.005 for a in (-1, 1) for b in (-1, 1) for e in (-1, 1)]
    body = _hull(V.scr(np.array(corners)))
    out = [Item(body, c["phone"], V.depth(mid) + bias, "solid", None, "phone")]
    if V.facing(ctr, face_n) > 0.0:
        scr = [ctr + a * long_ax * 0.067 + b * wide * 0.03 + face_n * 0.006 for a, b in ((-1, -1), (-1, 1), (1, 1), (1, -1))]
        out.append(Item(V.scr(np.array(scr)), mix(c["phone"], c["screen"], p.screen), V.depth(mid) + bias - 0.0005, "flat",
                        None, "screen"))
    return out


def _head(p, J, V, c):
    H, Rh = J["H"], J["Rh"]
    out = []

    def P_(x, y, z):
        return H + Rh @ (HS * np.array([x, y, z]))

    depth_h = V.depth(H)
    skull = _ellipsoid(H, Rh, 0.122 * HS, 0.13 * HS, 0.14 * HS, 18, 11)
    jaw = _ellipsoid(P_(0, 0.03, -0.068), Rh, 0.092 * HS, 0.088 * HS, 0.078 * HS, 14, 8)
    head = _hull(V.scr(np.vstack([skull, jaw])))
    k = V.px(H, 1) * HS
    # ears: the far one behind the head, the near one on top
    for sx in (-1, 1):
        ear = _ellipsoid(P_(sx * 0.118, -0.028, -0.016), Rh, 0.014 * HS, 0.022 * HS, 0.032 * HS, 10, 7)
        side_on = V.facing(P_(sx * 0.12, -0.02, 0), Rh @ np.array([sx, 0, 0]))
        near = side_on > 0.55
        ear_poly = _hull(V.scr(ear))
        out.append(Item(ear_poly, darker(c["skin"], 0.95, 0.02), depth_h + (-0.0005 if near else 0.0005), "solid", None, "ear"))
        if near:
            inner = _ellipsoid(P_(sx * 0.126, -0.028, -0.016), Rh, 0.006 * HS, 0.011 * HS, 0.018 * HS, 8, 6)
            out.append(Item(_hull(V.scr(inner)), c["skin_dk"], depth_h - 0.00055, "flat", ear_poly, "ear_in"))
    out.append(Item(head, c["skin"], depth_h, "solid", None, "head"))
    fwd = Rh @ np.array([0, 1.0, 0])
    face_vis = V.facing(P_(0, 0.13, 0), fwd)
    if face_vis > -0.05:
        out += _face(p, P_, Rh, V, c, depth_h, k, head)
    # hair: the whole volume behind the head (its outline shows round the skull), then the visible cap above the
    # hairline and the locks in front, merged into one shape so it reads as hair, not a helmet or a pile of balls
    nu, nv = 44, 26
    uu, vv = np.meshgrid(np.linspace(0, 2 * np.pi, nu + 1), np.linspace(-np.pi / 2, np.pi / 2, nv))
    local = np.stack([0.138 * np.cos(vv) * np.cos(uu), 0.15 * np.cos(vv) * np.sin(uu), 0.148 * np.sin(vv)], -1)
    local += np.array([0, -0.012, 0.022])
    hairy = local[..., 2] > 0.012 + 0.62 * local[..., 1] - 0.9 * np.maximum(0, -local[..., 1] - 0.05)
    world_pts = H + (HS * local.reshape(-1, 3)) @ Rh.T
    scr = V.scr(world_pts).reshape(nv, nu + 1, 2)
    normals = (local - np.array([0, -0.012, 0.022])) / np.array([0.138, 0.15, 0.148]) ** 2
    nw = normals.reshape(-1, 3) @ Rh.T
    to_cam = V.cam.pos - V.world(world_pts)
    vis = (np.sum((nw @ V.R.T) * to_cam, 1) > 0).reshape(nv, nu + 1)
    locks = [((0.035, 0.112, 0.108), (0.075, 0.042, 0.042), (0, 0, -22)),  # fringe swoop
             ((-0.05, 0.1, 0.11), (0.05, 0.04, 0.04), (0, 0, 15)),
             ((0.02, 0.05, 0.155), (0.05, 0.055, 0.026), (-14, 0, 10)),  # top flick
             ((0.05, -0.125, -0.045), (0.042, 0.03, 0.045), (0, 0, 0)),  # nape locks
             ((-0.05, -0.125, -0.05), (0.042, 0.03, 0.045), (0, 0, 0)),
             ((0.0, -0.14, 0.0), (0.06, 0.03, 0.06), (0, 0, 0)),
             ((0.11, -0.04, 0.06), (0.03, 0.06, 0.05), (0, 0, 0)), ((-0.11, -0.04, 0.06), (0.03, 0.06, 0.05), (0, 0, 0))]
    lock_polys_front, lock_polys_all = [], []
    for (x, y, z), (rx, ry, rz), (ax, ay, az) in locks:
        R = Rh @ _rx(ax) @ _ry(ay) @ _rz(az)
        ctr = P_(x, y, z)
        poly = _hull(V.scr(_ellipsoid(ctr, R, rx * HS, ry * HS, rz * HS, 12, 7)))
        lock_polys_all.append(poly)
        if V.depth(ctr) < depth_h + 0.02:
            lock_polys_front.append(poly)
    back_hair = union([_hull(scr[hairy])] + lock_polys_all)
    out.append(Item(back_hair, c["hair"], depth_h + 0.0008, "solid", None, "hair_back"))
    quads = []
    for i in range(nv - 1):
        for j in range(nu):
            if hairy[i, j] and hairy[i + 1, j] and hairy[i, j + 1] and hairy[i + 1, j + 1] and                     (vis[i, j] or vis[i + 1, j] or vis[i, j + 1] or vis[i + 1, j + 1]):
                quads.append(np.array([scr[i, j], scr[i, j + 1], scr[i + 1, j + 1], scr[i + 1, j]]))
    front_hair = union(quads + lock_polys_front)
    if len(front_hair) >= 3:
        out.append(Item(front_hair, c["hair"], depth_h - 0.0004, "solid", head, "hair"))
    # a shine on top and a whorl at the crown (seen from behind)
    shine = V.scr(np.array([P_(0.07, 0.02, 0.15), P_(0.02, 0.06, 0.155), P_(-0.04, 0.08, 0.14)]))
    if V.facing(P_(0, 0.03, 0.15), Rh @ np.array([0, 0.3, 1])) > 0 and len(front_hair) >= 3:
        out.append(Item(_stroke2d(shine, 0.016 * k, 0.006 * k), c["hair_hi"], depth_h - 0.0006, "flat", front_hair))
    if V.facing(P_(0, -0.09, 0.11), Rh @ np.array([0, -1, 0.6])) > 0.1:
        a = np.linspace(0, 4.2, 14)
        whorl = np.stack([0.025 * a / 4.2 * np.cos(a), -0.075 + 0.025 * a / 4.2 * np.sin(a), 0.135 + 0 * a], 1)
        out.append(Item(_stroke2d(V.scr(np.array([P_(*q) for q in whorl])), 0.006 * k), c["hair_dk"], depth_h - 0.0007,
                        "flat", None))
        for dx in (-0.045, 0.0, 0.045):
            strand = V.scr(np.array([P_(dx, -0.13, 0.06), P_(dx * 1.1, -0.145, -0.01), P_(dx * 1.2, -0.13, -0.06)]))
            out.append(Item(_stroke2d(strand, 0.005 * k, 0.002 * k), c["hair_dk"], depth_h - 0.0007, "flat", None))
    return out


def _face(p, P_, Rh, V, c, depth, k, head):
    out = []

    def on(x, z):
        """A point on the face surface at (x, z), pushed onto the skull/jaw front."""
        y = 0.13 * math.sqrt(max(0.0, 1 - (x / 0.122) ** 2 - (z / 0.14) ** 2)) if z > -0.05 else \
            0.03 + 0.088 * math.sqrt(max(0.0, 1 - (x / 0.092) ** 2 - ((z + 0.068) / 0.078) ** 2))
        return P_(x, y, z)

    def vis(x, z):
        n = Rh @ np.array([x / 0.122 ** 2, 0.13 / 0.13 ** 2, z / 0.14 ** 2])
        return V.facing(on(x, z), n)

    gx, gy = p.gaze
    for sx in (-1, 1):
        ex, ez = sx * 0.048, 0.008
        if vis(ex, ez) < 0.08:
            continue
        if p.happy > 0.5 or p.eyes < 0.12:
            kk = 1 if p.happy > 0.5 else -0.15
            arc = V.scr(np.array([on(ex - 0.019, ez - kk * 0.005), on(ex, ez + kk * 0.014), on(ex + 0.019, ez - kk * 0.005)]))
            out.append(Item(_stroke2d(arc, 0.008 * k), c["eye"], depth - 0.0002, "flat", head))
        else:
            open_ = min(1.35, max(0.15, p.eyes))
            cx, cz = ex + gx * 0.004, ez + gy * 0.005
            ring = [on(cx + 0.017 * (1 + 0.15 * max(0, p.eyes - 1)) * math.cos(a), cz + 0.026 * open_ * math.sin(a))
                    for a in np.linspace(0, 2 * np.pi, 18, endpoint=False)]
            out.append(Item(V.scr(np.array(ring)), c["eye"], depth - 0.0002, "flat", head))
            hi = [on(cx + 0.007 + 0.006 * math.cos(a), cz + 0.01 * open_ + 0.006 * math.sin(a)) for a in np.linspace(0, 2 * np.pi, 10, endpoint=False)]
            out.append(Item(V.scr(np.array(hi)), (1.0, 1.0, 1.0), depth - 0.0003, "flat", head))
        by = 0.062 + 0.016 * p.brows + 0.016 * p.quirk * sx
        inner, outer = by + 0.012 * p.brow_tilt, by - 0.004 * p.brow_tilt
        brow = V.scr(np.array([on(sx * 0.024, inner), on(sx * 0.05, (inner + outer) / 2 + 0.006), on(sx * 0.076, outer)]))
        out.append(Item(_stroke2d(brow, 0.013 * k, 0.007 * k), c["hair_dk"], depth - 0.0002, "flat", head))
        if vis(sx * 0.078, -0.045) > 0.1:
            bl = [on(sx * 0.078 + 0.024 * math.cos(a), -0.045 + 0.014 * math.sin(a)) for a in np.linspace(0, 2 * np.pi, 14, endpoint=False)]
            out.append(Item(V.scr(np.array(bl)), mix(c["skin"], c["blush"], 0.45), depth - 0.0001, "flat", head))
    # nose: a little volume that pokes past the cheek in profile
    fwd_h = Rh @ np.array([0, 1.0, 0])
    fwd_h = V.R @ np.array([fwd_h[0], fwd_h[1], 0.0])
    to_c = V.cam.pos - V.world(P_(0, 0.13, -0.03)[None])[0]
    to_c[2] = 0.0
    front_on = float(fwd_h @ to_c / ((np.linalg.norm(fwd_h) + 1e-9) * (np.linalg.norm(to_c) + 1e-9)))
    if front_on > 0.8:
        mark = V.scr(np.array([on(0.004, -0.012), on(0.012, -0.04), on(0.0, -0.047)]))
        out.append(Item(_stroke2d(mark, 0.008 * k, 0.006 * k), c["skin_dk"], depth - 0.0004, "flat", head))
    elif front_on > -0.3:
        nose = _ellipsoid(P_(0, 0.13, -0.03), Rh, 0.012 * HS, 0.017 * HS, 0.019 * HS, 10, 7)
        profile_ = front_on < 0.35
        out.append(Item(_hull(V.scr(nose)), darker(c["skin"], 0.97 if profile_ else 0.9, 0.02), depth - 0.0004,
                        "solid" if profile_ else "flat", None if profile_ else head, "nose"))
    if vis(0.0, -0.088) > 0.0:
        sm = p.smile
        if p.mouth_open > 0.15:
            ring = [on(0.022 * (1 + 0.35 * p.mouth_open) * math.cos(a), -0.09 + (0.008 + 0.022 * p.mouth_open) * math.sin(a))
                    for a in np.linspace(0, 2 * np.pi, 18, endpoint=False)]
            out.append(Item(V.scr(np.array(ring)), c["mouth"], depth - 0.0002, "flat", head))
        else:
            m = V.scr(np.array([on(-0.026, -0.082 + 0.012 * sm), on(0.0, -0.09 - 0.008 * sm), on(0.026, -0.082 + 0.012 * sm)]))
            out.append(Item(_stroke2d(m, 0.008 * k), c["mouth"], depth - 0.0002, "flat", head))
    return out


# ---------------------------------------------------------------- painting and shadows

def paint(P, items, light=(-1.0, -0.4), lw=2.0, backlit=False, s=None, r=None, line_k=0.55):
    """Draw the pieces back to front: cel shading and an outline for solids, flat fills for details."""
    s = 7.0 if s is None else s
    r = 3.0 if r is None else r
    for it in sorted(items, key=lambda q: -q.depth):
        if len(it.poly) < 3:
            continue
        if it.clip is not None:
            P.ctx.save()
            _path(P.ctx, it.clip)
            P.ctx.clip()
        col = it.col
        if it.kind == "flat":
            P.fill(it.poly, darker(col, 0.62, 0.16) if backlit else col)
        elif backlit:
            P.cel(it.poly, darker(col, 0.62, 0.16), darker(col, 0.48, 0.22), mix(col, (1.0, 0.78, 0.48), 0.75), light=light,
                  s=s, r=r)
            if lw:
                P.stroke(it.poly, darker(col, 0.32, 0.25), lw)
        else:
            P.cel(it.poly, col, darker(col, 0.8, 0.1), mix(col, (1.0, 0.93, 0.8), 0.45), light=light, s=s, r=r)
            if lw:
                P.stroke(it.poly, darker(col, line_k, 0.2), lw)
        if it.clip is not None:
            P.ctx.restore()


def volumes(p: Pose3) -> list[np.ndarray]:
    """The character's solid volumes in WORLD space as point clouds (each one convex), for casting shadows."""
    J = skeleton(p)
    a = math.radians(p.yaw)
    R = np.stack([[math.cos(a), -math.sin(a), 0.0], [math.sin(a), math.cos(a), 0.0], [0.0, 0.0, 1.0]], 1)
    o = np.asarray(p.pos, float)
    out = []

    def tube3(chain, radii, steps=4):
        chain = [np.asarray(q, float) for q in chain]
        for i in range(len(chain) - 1):
            a0, a1 = chain[i], chain[i + 1]
            ax = a1 - a0
            L = np.linalg.norm(ax) + 1e-9
            ax /= L
            ref = np.array([0, 0, 1.0]) if abs(ax[2]) < 0.9 else np.array([1.0, 0, 0])
            u = np.cross(ax, ref)
            u /= np.linalg.norm(u)
            v = np.cross(ax, u)
            for j in range(steps):
                pts = []
                for t in (j / steps, (j + 1) / steps):
                    q = a0 + ax * L * t
                    rr = float(np.interp(i + t, [k for k, _ in radii], [r for _, r in radii]))
                    ang = np.linspace(0, 2 * np.pi, 10, endpoint=False)
                    pts.append(q + rr * (np.cos(ang)[:, None] * u + np.sin(ang)[:, None] * v))
                out.append(np.vstack(pts))

    for side in "rl":
        tube3([J["Hip" + side], J["K" + side], J["A" + side]], [(0, 0.1), (1, 0.073), (2, 0.06)])
        A, F = J["A" + side], J["F" + side]
        out.append(np.array([A + F * f + np.array([0, 0, z]) + np.cross(F, [0, 0, 1]) * w for f in (-0.09, 0.24)
                             for z in (-0.088, 0.03) for w in (-0.05, 0.05)]))
        tube3([J["S" + side], J["E" + side], J["W" + side]], [(0, 0.071), (1, 0.057), (2, 0.052)], steps=3)
        hd, ac, pn = hand_axes(J, side)
        out.append(_ellipsoid(J["W" + side] + hd * 0.05, np.stack([ac, pn, hd], 1), 0.045, 0.026, 0.062, 8, 5))
    rings = torso_volume(J)
    for i in range(len(rings) - 1):
        out.append(np.vstack([rings[i], rings[i + 1]]))
    hb, Rb = _torso_point(J, 0.47, 0.0, -0.11)
    out.append(_ellipsoid(hb, Rb, 0.13, 0.075, 0.085, 10, 6))
    out.append(np.vstack([_ellipsoid(J["H"] + J["Rh"] @ np.array([0, -0.012 * HS, 0.022 * HS]), J["Rh"], 0.14 * HS,
                                     0.152 * HS, 0.15 * HS, 12, 7),
                          _ellipsoid(J["H"] + J["Rh"] @ (HS * np.array([0, 0.03, -0.068])), J["Rh"], 0.092 * HS, 0.088 * HS,
                                     0.078 * HS, 8, 5)]))
    out.append(np.vstack([_ring(J["N"], J["Ru"], 0.05, 0.05, 0.05), _ring(J["H"] - J["Rh"] @ np.array([0, 0, 0.1 * HS]), J["Rh"], 0.05, 0.05, 0.05)]))
    Hh, Rh = J["H"], J["Rh"]

    def hp(x, y, z):
        return Hh + Rh @ (HS * np.array([x, y, z]))
    out.append(_ellipsoid(hp(0.02, 0.05, 0.1), Rh, 0.112 * HS, 0.1 * HS, 0.085 * HS, 10, 6))  # the quiff
    out.append(_ellipsoid(hp(0.0, 0.135, -0.03), Rh, 0.014 * HS, 0.026 * HS, 0.02 * HS, 6, 4))  # the nose
    if p.phone > 0.5:
        ctr, Rph = phone_frame(J)
        w, l, t = PHONE
        out.append(np.array([ctr + Rph @ np.array([a * w, b * l, c * t]) for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)]))
    return [o + v @ R.T for v in out]


def shadow_polys(clouds, light, normal, d, cam):
    """Each convex volume's shadow on the plane normal . X = d, as a screen polygon."""
    n = np.asarray(normal, float)
    n /= np.linalg.norm(n)
    ref = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = np.cross(n, ref)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    out = []
    for X in clouds:
        t = (d - X @ n) / float(n @ light)
        Y = X + t[:, None] * light
        uv = np.stack([Y @ e1, Y @ e2], 1)
        h = _hull(uv)
        Yh = h[:, :1] * e1 + h[:, 1:2] * e2 + d * n
        if np.min(cam.cam(Yh)[:, 2]) < 0.05:
            continue
        out.append(cam(Yh))
    return out


# ---------------------------------------------------------------- motion

def plant(p: Pose3, r_ankle, l_ankle, r_toe=0.0, l_toe=0.0) -> Pose3:
    return replace(p, r_foot=tuple(r_ankle), l_foot=tuple(l_ankle), r_toe=r_toe, l_toe=l_toe)


def gait(t: float, speed: float = 1.3, step: float = 0.65, duty: float = 0.6, lift: float = 0.09, hip: float = 0.885,
         arm: float = 22.0, elbow: float = 16.0, lean: float = 3.0, base: Pose3 | None = None) -> tuple[Pose3, float]:
    """Walk (duty ~0.6) or run (~0.35) in the character frame at time t: (pose, distance travelled). Stance feet stay
    planted while the hip passes over them; the swing foot arcs forward."""
    period = 2 * step / speed
    ph = (t / period) % 1.0
    reach = step * duty
    feet, toes, planted = {}, {}, []
    for side, off, sx in (("r", 0.0, 1.0), ("l", 0.5, -1.0)):
        q = (ph - off) % 1.0
        if q < duty:
            sv = q / duty
            y, z, toe = reach * (1 - 2 * sv), ANKLE, 0.0
            if sv > 0.75:
                kk = (sv - 0.75) / 0.25
                z += 0.05 * kk
                toe = -35 * kk
            planted.append(y)
        else:
            sv = (q - duty) / (1 - duty)
            e = 0.5 - 0.5 * math.cos(math.pi * sv)
            y = -reach + 2 * reach * e
            z = ANKLE + lift * math.sin(math.pi * sv) ** 0.8
            toe = -25 * (1 - sv) + 12 * sv
        feet[side] = (sx * 0.1, y, z)
        toes[side] = toe
    h = hip
    for y in planted:
        h = min(h, ANKLE + 0.035 + math.sqrt(max(0.0, ((TH + SH) * 0.99) ** 2 - y * y)))
    swing = math.cos(2 * math.pi * ph)
    b = base or Pose3()
    pose = replace(b, hip=h, lean=lean + 1.2 * math.sin(4 * math.pi * ph), roll=1.5 * math.sin(2 * math.pi * ph),
                   r_fwd=-arm * swing, r_el=elbow + 0.5 * max(0.0, -arm * swing), l_fwd=arm * swing,
                   l_el=elbow + 0.5 * max(0.0, arm * swing), r_out=8, l_out=8)
    return plant(pose, feet["r"], feet["l"], toes["r"], toes["l"]), speed * t


def walk(t: float, speed: float = 1.3, base: Pose3 | None = None) -> tuple[Pose3, float]:
    return gait(t, speed=speed, step=0.62 * speed / 1.3, base=base)


def run(t: float, speed: float = 3.4, base: Pose3 | None = None) -> tuple[Pose3, float]:
    b = base or Pose3()
    if b.r_grip == 0.3 and b.l_grip == 0.3:  # runners' hands: loose fists, not open palms
        b = replace(b, r_grip=0.68, l_grip=0.68)
    return gait(t, speed=speed, step=1.05, duty=0.36, lift=0.24, hip=0.9, arm=45.0, elbow=90.0, lean=13.0, base=b)


def at(p: Pose3, pos, yaw=None) -> Pose3:
    """The same pose placed somewhere else (and facing yaw)."""
    return replace(p, pos=tuple(pos), yaw=p.yaw if yaw is None else yaw)


def forward(yaw: float) -> np.ndarray:
    a = math.radians(yaw)
    return np.array([math.sin(a), math.cos(a), 0.0])
