"""Per-frame technical measurements from a proxy: camera motion, sharpness, exposure, subject motion.

All values are sampled `analysis_fps` times per second on a frame scaled so its long edge is
`analysis_width` pixels, which keeps numbers comparable across clips of any resolution.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

import cv2
import numpy as np


@dataclass
class VideoSeries:
    t: np.ndarray  # sample times, seconds
    dx: np.ndarray  # camera translation since previous sample, fraction of frame diagonal
    dy: np.ndarray
    da: np.ndarray  # camera rotation since previous sample, radians
    valid: np.ndarray  # camera motion estimate succeeded
    motion: np.ndarray  # subject motion after cancelling camera motion, 0-1
    sharpness: np.ndarray  # log10(variance of Laplacian + 1)
    brightness: np.ndarray  # mean luma 0-255
    clip_high: np.ndarray  # fraction of pixels >= 250
    clip_low: np.ndarray  # fraction of pixels <= 5
    contrast: np.ndarray  # luma standard deviation
    saturation: np.ndarray  # mean HSV saturation 0-255
    cut: np.ndarray  # 1 - colour-histogram correlation with previous sample
    sample_fps: float

    def as_arrays(self) -> dict[str, np.ndarray]:
        return {f.name: np.asarray(getattr(self, f.name)) for f in fields(self)}


MIN_SPREAD = 0.35  # a partial consensus must span this fraction of the frame to count as "the scene"
STILL_PX = 0.5  # features moving less than this (at analysis size) are standing still
# When the camera moves, essentially no feature stays still, so even a modest still share that
# spreads across the frame means a static camera with moving subjects.
STILL_SHARE = 0.15
_IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])


def _spread(points: np.ndarray, w: int, h: int) -> tuple[float, float]:
    sx = (np.percentile(points[:, 0], 90) - np.percentile(points[:, 0], 10)) / w
    sy = (np.percentile(points[:, 1], 90) - np.percentile(points[:, 1], 10)) / h
    return float(sx), float(sy)


def _camera_motion(prev: np.ndarray, cur: np.ndarray) -> np.ndarray | None:
    """Similarity transform prev→cur describing camera motion, or None if the scene can't tell us.

    A moving subject (a person, a car, waves) must not be mistaken for the camera moving:
    1. if a good share of features stand perfectly still across the frame, the camera is static;
    2. a near-unanimous consensus is the camera;
    3. a partial consensus only counts if it spreads across the frame (not one compact subject).
    Rotation is trusted only when the agreeing features spread in both directions.
    """
    h, w = prev.shape
    pts = cv2.goodFeaturesToTrack(prev, maxCorners=300, qualityLevel=0.01, minDistance=7, blockSize=7)
    if pts is None or len(pts) < 12:
        return None
    nxt, status, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None, winSize=(21, 21), maxLevel=3)
    if nxt is None:
        return None
    good = status.reshape(-1) == 1
    if good.sum() < 12:
        return None
    src, dst = pts[good].reshape(-1, 2), nxt[good].reshape(-1, 2)

    still = np.linalg.norm(dst - src, axis=1) < STILL_PX
    if still.mean() >= STILL_SHARE and still.sum() >= 6 and max(_spread(src[still], w, h)) >= MIN_SPREAD:
        return _IDENTITY

    matrix, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=2.0,
                                                  maxIters=500)
    if matrix is None or inliers is None:
        return None
    keep = inliers.reshape(-1).astype(bool)
    ratio = keep.mean()
    if keep.sum() < 12 or ratio < 0.4:
        return None
    sx, sy = _spread(src[keep], w, h)
    if ratio < 0.8 and max(sx, sy) < MIN_SPREAD:
        return None
    if min(sx, sy) < 0.2:  # features along a line: rotation is poorly constrained, keep translation only
        shift = np.median(dst[keep] - src[keep], axis=0)
        return np.array([[1.0, 0.0, shift[0]], [0.0, 1.0, shift[1]]])
    return matrix


def measure_video(proxy: str | Path, analysis_fps: float = 10.0, long_edge: int = 320) -> VideoSeries:
    cap = cv2.VideoCapture(str(proxy))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open {proxy}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(src_fps / analysis_fps)))
    cols: dict[str, list[float]] = {k: [] for k in (
        "t", "dx", "dy", "da", "valid", "motion", "sharpness", "brightness",
        "clip_high", "clip_low", "contrast", "saturation", "cut")}
    prev_gray: np.ndarray | None = None
    prev_hist: np.ndarray | None = None
    index = 0
    try:
        while cap.grab():
            if index % step:
                index += 1
                continue
            ok, frame = cap.retrieve()
            if not ok:
                break
            h, w = frame.shape[:2]
            scale = long_edge / max(h, w)
            small = cv2.resize(frame, (max(2, round(w * scale)), max(2, round(h * scale))), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            sh, sw = gray.shape
            diag = float(np.hypot(sw, sh))

            hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
            cv2.normalize(hist, hist)

            dx = dy = da = 0.0
            valid = 0.0
            motion = 0.0
            cut = 0.0
            if prev_gray is not None:
                matrix = _camera_motion(prev_gray, gray)
                my, mx = int(sh * 0.1), int(sw * 0.1)
                if matrix is not None:
                    dx, dy = matrix[0, 2] / diag, matrix[1, 2] / diag
                    da = float(np.arctan2(matrix[1, 0], matrix[0, 0]))
                    valid = 1.0
                    aligned = cv2.warpAffine(prev_gray, matrix, (sw, sh), flags=cv2.INTER_LINEAR,
                                             borderMode=cv2.BORDER_REFLECT)
                    diff = cv2.absdiff(aligned, gray)
                else:
                    diff = cv2.absdiff(prev_gray, gray)
                motion = float(diff[my:sh - my, mx:sw - mx].mean() / 255.0)
                cut = float(1.0 - cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL))

            cols["t"].append(index / src_fps)
            cols["dx"].append(dx)
            cols["dy"].append(dy)
            cols["da"].append(da)
            cols["valid"].append(valid)
            cols["motion"].append(motion)
            cols["sharpness"].append(float(np.log10(cv2.Laplacian(gray, cv2.CV_64F).var() + 1.0)))
            cols["brightness"].append(float(gray.mean()))
            cols["clip_high"].append(float((gray >= 250).mean()))
            cols["clip_low"].append(float((gray <= 5).mean()))
            cols["contrast"].append(float(gray.std()))
            cols["saturation"].append(float(hsv[..., 1].mean()))
            cols["cut"].append(cut)
            prev_gray, prev_hist = gray, hist
            index += 1
    finally:
        cap.release()

    arrays = {k: np.asarray(v, dtype=np.float32) for k, v in cols.items()}
    arrays["valid"] = arrays["valid"] > 0.5
    return VideoSeries(**arrays, sample_fps=src_fps / step)
