"""Describe camera and subject motion over a stretch of a clip, from the stored measurements.

Lets the editor plan match cuts on movement (a pan left into a pan left), deliberate collisions
(left into right) and stillness-vs-motion contrasts, without spending any AI usage.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

STILL = 0.02  # camera speed (frame diagonal per second) below which it reads as static
FAST = 0.15


def describe_motion(npz_path: Path, start: float, end: float) -> str:
    if not npz_path.exists():
        return ""
    data = np.load(npz_path)
    t = data["t"]
    if len(t) < 3:
        return ""
    fs = 1.0 / float(np.median(np.diff(t)))
    sel = (t >= start) & (t <= end)
    if sel.sum() < 2:
        return ""
    pan = -float(np.mean(data["dx"][sel])) * fs  # content moves left when the camera pans right
    tilt = float(np.mean(data["dy"][sel])) * fs  # content moves down when the camera tilts up
    speed = float(np.hypot(pan, tilt))
    if speed < STILL:
        camera = "camera still"
    else:
        moves = []
        if abs(pan) >= STILL:
            moves.append(f"pans {'right' if pan > 0 else 'left'}")
        if abs(tilt) >= STILL:
            moves.append(f"tilts {'up' if tilt > 0 else 'down'}")
        camera = f"camera {' + '.join(moves)} ({'fast' if speed > FAST else 'slow'})"
    subject = float(np.median(data["motion"][sel]))
    scene = "busy scene" if subject > 0.04 else "calm scene" if subject < 0.012 else "some movement"
    return f"{camera}, {scene}"
