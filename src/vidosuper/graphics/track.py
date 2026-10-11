"""Scene tracking, so graphics stick to the world: how the shot's first frame maps onto each later frame
(a similarity transform - pan, tilt, zoom, roll), estimated from background features with KLT optical
flow and RANSAC. The person in the shot (when a matte is given) is left out of the features."""

from __future__ import annotations

import numpy as np


class SceneTracker:
    def __init__(self, width: int, height: int, work: int = 640):
        self.scale = work / max(width, height)
        self.size = (max(2, round(width * self.scale)), max(2, round(height * self.scale)))
        self.prev: np.ndarray | None = None
        self.pts: np.ndarray | None = None
        self.acc = np.eye(3)

    def _gray(self, rgb: np.ndarray) -> np.ndarray:
        import cv2

        return cv2.cvtColor(cv2.resize(rgb, self.size, interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)

    def _detect(self, gray: np.ndarray, exclude: np.ndarray | None) -> np.ndarray | None:
        import cv2

        mask = None
        if exclude is not None:
            m = cv2.resize(exclude.astype(np.float32), self.size)
            mask = cv2.erode((m < 0.25).astype(np.uint8) * 255, np.ones((9, 9), np.uint8))
        return cv2.goodFeaturesToTrack(gray, maxCorners=400, qualityLevel=0.01, minDistance=8, mask=mask)

    def update(self, rgb: np.ndarray, exclude: np.ndarray | None = None) -> np.ndarray:
        """3x3 transform taking the first frame's pixel coordinates to this frame's."""
        import cv2

        gray = self._gray(rgb)
        if self.prev is None:
            self.prev, self.pts = gray, self._detect(gray, exclude)
            return np.eye(3)
        if self.pts is not None and len(self.pts) >= 8:
            nxt, status, _ = cv2.calcOpticalFlowPyrLK(self.prev, gray, self.pts, None, winSize=(21, 21), maxLevel=3)
            ok = status.ravel() == 1
            p0, p1 = self.pts[ok], nxt[ok]
            if len(p0) >= 8:
                m, inliers = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC, ransacReprojThreshold=2.0)
                if m is not None:
                    self.acc = np.vstack([m, [0.0, 0.0, 1.0]]) @ self.acc
                    keep = inliers.ravel() == 1 if inliers is not None else np.ones(len(p1), bool)
                    self.pts = p1[keep].reshape(-1, 1, 2)
        if self.pts is None or len(self.pts) < 120:
            self.pts = self._detect(gray, exclude)
        self.prev = gray
        s = np.diag([self.scale, self.scale, 1.0])
        return np.linalg.inv(s) @ self.acc @ s


def transform_point(h: np.ndarray, x: float, y: float) -> tuple[float, float]:
    v = h @ np.array([x, y, 1.0])
    return float(v[0] / v[2]), float(v[1] / v[2])


def transform_scale(h: np.ndarray) -> float:
    """How much the transform has zoomed (1 = not at all)."""
    return float(np.sqrt(abs(np.linalg.det(h[:2, :2]))))
