"""Speed ramps: a shot's speed can change smoothly over its source time (0.3x into a 2x rush, a slow-mo
hit that snaps back to real time). Keyframes are (seconds after src_in, speed); speed is linear between
them and holds before the first / after the last. This module gives the output time a ramp produces
(and its inverse) and the FFmpeg setpts expression that plays it.
"""

from __future__ import annotations

import math

from ..schemas import Segment

EPS = 1e-9


def ramp_points(seg: Segment) -> list[tuple[float, float]]:
    return sorted((max(0.0, p.at), p.speed) for p in seg.ramp)


def _pieces(seg: Segment) -> list[tuple[float, float, float, float]]:
    """(source start, source end, speed at start, speed at end) covering 0..inf (last end = inf)."""
    pts = ramp_points(seg)
    if not pts:
        return [(0.0, math.inf, seg.speed, seg.speed)]
    pieces = []
    if pts[0][0] > EPS:
        pieces.append((0.0, pts[0][0], pts[0][1], pts[0][1]))
    for (a, va), (b, vb) in zip(pts, pts[1:]):
        if b - a > EPS:
            pieces.append((a, b, va, vb))
    pieces.append((pts[-1][0], math.inf, pts[-1][1], pts[-1][1]))
    return pieces


def _piece_time(a: float, b: float, va: float, vb: float, u: float) -> float:
    """Output seconds spent playing source [a, u] of a piece whose speed goes linearly va -> vb on [a, b]."""
    du = u - a
    if math.isinf(b) or abs(vb - va) < 1e-6:
        return du / va
    slope = (vb - va) / (b - a)
    return math.log((va + slope * du) / va) / slope


def out_time(seg: Segment, u: float) -> float:
    """Output seconds after playing u source seconds from src_in."""
    total = 0.0
    for a, b, va, vb in _pieces(seg):
        if u <= b:
            return total + _piece_time(a, b, va, vb, max(u, a))
        total += _piece_time(a, b, va, vb, b)
    return total


def source_time(seg: Segment, t_out: float) -> float:
    """Inverse of out_time: source seconds (after src_in) played after t_out output seconds."""
    lo, hi = 0.0, max(1.0, t_out * 10.0)
    while out_time(seg, hi) < t_out:
        hi *= 2
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if out_time(seg, mid) < t_out else (lo, mid)
    return (lo + hi) / 2


def out_duration(seg: Segment) -> float:
    return out_time(seg, seg.src_out - seg.src_in)


def speeds(seg: Segment) -> list[float]:
    return [v for _, v in ramp_points(seg)] or [seg.speed]


def end_speed(seg: Segment) -> float:
    return speeds(seg)[-1]


def setpts_expr(seg: Segment) -> str:
    """setpts filter for the shot: constant speed, or the ramp as a piecewise closed form of T."""
    if not seg.ramp:
        return f"setpts=(PTS-STARTPTS)/{seg.speed:.6f}"
    u = "(T-STARTT)"
    expr = None
    offsets = []
    acc = 0.0
    pieces = _pieces(seg)
    for a, b, va, vb in pieces:
        offsets.append(acc)
        if not math.isinf(b):
            acc += _piece_time(a, b, va, vb, b)
    for (a, b, va, vb), off in reversed(list(zip(pieces, offsets))):
        if math.isinf(b) or abs(vb - va) < 1e-6:
            body = f"{off:.6f}+({u}-{a:.6f})/{va:.6f}"
        else:
            slope = (vb - va) / (b - a)
            body = f"{off:.6f}+log(({va:.6f}+{slope:.6f}*({u}-{a:.6f}))/{va:.6f})/{slope:.6f}"
        expr = body if expr is None else f"if(lt({u},{b:.6f}),{body},{expr})"
    return f"setpts='({expr})/TB'"
