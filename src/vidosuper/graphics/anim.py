"""Keyframes and easing for the animated shorts."""
from __future__ import annotations

import math
from bisect import bisect_right


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def ramp(t: float, t0: float, t1: float) -> float:
    """0 before t0, 1 after t1, linear between."""
    return clamp01((t - t0) / (t1 - t0)) if t1 > t0 else float(t >= t1)


def linear(x: float) -> float:
    return clamp01(x)


def smooth(x: float) -> float:
    x = clamp01(x)
    return x * x * (3 - 2 * x)


def ease_in(x: float) -> float:
    return clamp01(x) ** 3


def ease_out(x: float) -> float:
    return 1 - (1 - clamp01(x)) ** 3


def ease_in_out(x: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * clamp01(x))


def back_out(x: float, s: float = 1.70158) -> float:
    """Overshoots past 1 and settles: the cartoon 'snap into place'."""
    x = clamp01(x) - 1
    return x * x * ((s + 1) * x + s) + 1


def keys(t: float, frames, ease=smooth, lerp=None):
    """Value at time t from keyframes [(time, value) or (time, value, ease_into_this_key)], eased between
    neighbours. Tuples interpolate element-wise; anything else needs lerp(a, b, u)."""
    ts = [k[0] for k in frames]
    if t <= ts[0]:
        return frames[0][1]
    if t >= ts[-1]:
        return frames[-1][1]
    i = bisect_right(ts, t) - 1
    a, b = frames[i], frames[i + 1]
    e = b[2] if len(b) > 2 else ease
    u = e((t - a[0]) / (b[0] - a[0]))
    if lerp is not None:
        return lerp(a[1], b[1], u)
    if isinstance(a[1], tuple):
        return tuple(x + (y - x) * u for x, y in zip(a[1], b[1]))
    return a[1] + (b[1] - a[1]) * u
