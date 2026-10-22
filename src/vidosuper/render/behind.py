"""Graphics behind the person in the shot: the people's matte for the stretch of the film a graphic covers, from the
shots as rendered, written as a grey video. The assembly lays the graphic on, then the people, cut out with this matte,
back over it.

The matte: u2net's human segmentation (graphics/matte.py), which finds whole people however small and from any side
(MODNet, made for portraits, misses a couple walking away down a street), sharpened to the picture's real edges with
the cut-out's guided filter and steadied over time. Without u2net, MODNet."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from ..edit.timeline import Timeline
from ..ffmpeg import ffmpeg_bin


def people_matte():
    """A function rgb -> 0..1 matte of the people in a frame (None: no model here)."""
    import cv2

    from ..explainer import cutout
    from ..graphics import matte as u2

    if u2.MODEL.exists():
        def find(rgb: np.ndarray) -> np.ndarray:
            m = u2.person_matte(rgb)
            grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
            return np.clip(cutout.guided(grey, m.astype(np.float32)), 0.0, 1.0)
        return find
    if cutout.available():
        return lambda rgb: cutout.raw_matte(rgb, full=True)
    return None


def subject_matte(timeline: Timeline, shots: list, start: float, end: float, size: tuple[int, int], out: Path) -> Path | None:
    """The people's matte for film seconds [start, end) at the film's size (None: no matte model here)."""
    import cv2

    find = people_matte()
    if find is None:
        return None
    fps = timeline.fps
    w, h = size
    f0, f1 = int(round(start * fps)), int(round(end * fps))
    out.parent.mkdir(parents=True, exist_ok=True)
    enc = subprocess.Popen([ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-f", "rawvideo",
                            "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "ffv1",
                            "-pix_fmt", "gray", str(out)], stdin=subprocess.PIPE)
    caps: dict[int, list] = {}  # shot index -> [capture, next frame it reads]
    prev = None
    try:
        for f in range(f0, f1):
            item = next((i for i in reversed(timeline.items) if i.start <= f < i.end), None)
            frame = None
            if item is not None:
                cap = caps.get(item.index)
                if cap is None:
                    cap = caps[item.index] = [cv2.VideoCapture(str(shots[item.index].video)), 0]
                local = f - item.start
                if cap[1] != local:
                    cap[0].set(cv2.CAP_PROP_POS_FRAMES, local)
                ok, bgr = cap[0].read()
                cap[1] = local + 1
                if ok:
                    frame = bgr
            if frame is None or frame.shape[1] != w or frame.shape[0] != h:
                m = np.zeros((h, w), np.uint8)
            else:
                m = find(np.ascontiguousarray(frame[..., ::-1]))
                if prev is not None:  # steady where still, quick where moving (as the cut-out does)
                    moving = np.minimum(np.abs(m - prev) * 3.0, 1.0)
                    m = moving * m + (1.0 - moving) * (0.55 * m + 0.45 * prev)
                prev = m
                m = (m * 255.0 + 0.5).astype(np.uint8)
            enc.stdin.write(m.tobytes())
    finally:
        for cap, _ in caps.values():
            cap.release()
        enc.stdin.close()
        enc.wait()
    return out if enc.returncode == 0 and out.exists() else None
