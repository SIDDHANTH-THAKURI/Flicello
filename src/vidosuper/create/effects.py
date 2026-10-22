"""Visual effects that make a new clip from a shot (the studio swaps it in as a draft):
- **3D photo** (parallax): a frame's depth (Depth Anything V2 Small, Apache-2.0) turns a still into a slow camera move
  where near things slide past far ones;
- **remove** an object: the region is filled in, frame by frame, by LaMa (Apache-2.0) inside a box that follows the
  object as the camera moves;
- **upscale**: Real-ESRGAN x4plus (BSD-3) in tiles, for low-resolution shots, brought to the film's size.

Models live in %LOCALAPPDATA%/VidoSuper/models (VIDO_MODELS_DIR): depth/, inpaint/, upscale/."""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..ffmpeg import ffmpeg_bin


def models_dir() -> Path:
    return Path(os.environ.get("VIDO_MODELS_DIR") or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VidoSuper" / "models")


DEPTH = "depth/depth_anything_v2_small.onnx"
LAMA = "inpaint/lama.onnx"
ESRGAN = "upscale/real_esrgan_x4plus-onnx-float/real_esrgan_x4plus.onnx"


def available(name: str) -> bool:
    return (models_dir() / name).exists()


@lru_cache(maxsize=3)
def _session(name: str):
    import onnxruntime as ort

    from ..cpus import usable_cpus

    path = models_dir() / name
    if not path.exists():
        raise FileNotFoundError(f"model missing: {path}")
    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, usable_cpus() - 1)
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


class Writer:
    """Frames (RGB uint8) into an H.264 clip."""

    def __init__(self, out: Path, size: tuple[int, int], fps: float):
        out.parent.mkdir(parents=True, exist_ok=True)
        self.out = out
        self.proc = subprocess.Popen([ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-f", "rawvideo",
                                      "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", f"{fps:.4f}", "-i", "-",
                                      "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
                                      "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)

    def write(self, rgb: np.ndarray) -> None:
        self.proc.stdin.write(np.ascontiguousarray(rgb).tobytes())

    def close(self) -> Path:
        self.proc.stdin.close()
        self.proc.wait()
        if self.proc.returncode != 0:
            raise RuntimeError(f"could not write {self.out.name}")
        return self.out


def frames(video: Path, start: float, end: float, long_edge: int | None = None):
    """RGB frames of [start, end) of a video, scaled so the long edge is `long_edge` (None: as it is)."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
    n = int(round((end - start) * fps))
    try:
        for _ in range(n):
            ok, bgr = cap.read()
            if not ok:
                break
            if long_edge:
                h, w = bgr.shape[:2]
                k = long_edge / max(h, w)
                if k < 1:
                    bgr = cv2.resize(bgr, (round(w * k / 2) * 2, round(h * k / 2) * 2), interpolation=cv2.INTER_AREA)
            yield np.ascontiguousarray(bgr[..., ::-1])
    finally:
        cap.release()


def depth(rgb: np.ndarray) -> np.ndarray:
    """Relative depth 0..1 (1 = near) at the frame's size."""
    import cv2

    h, w = rgb.shape[:2]
    k = 518 / max(h, w)
    nh, nw = max(14, int(h * k / 14) * 14), max(14, int(w * k / 14) * 14)
    x = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    out = _session(DEPTH).run(None, {"pixel_values": x.transpose(2, 0, 1)[None]})[0][0]
    out = cv2.resize(out.astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
    lo, hi = np.percentile(out, 2), np.percentile(out, 98)
    return np.clip((out - lo) / max(1e-6, hi - lo), 0.0, 1.0)


def parallax(rgb: np.ndarray, out: Path, seconds: float = 4.0, fps: float = 30.0, strength: float = 0.035,
             move: str = "dolly") -> Path:
    """A still as a slow camera move with depth: near things slide more than far ones. `move`: dolly (a push in with
    a drift), orbit (a sideways arc), rise (a crane up)."""
    import cv2

    h, w = rgb.shape[:2]
    d = cv2.GaussianBlur(depth(rgb), (0, 0), 2.0)  # soft edges: fewer tears at the outlines
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w / 2, h / 2
    n = int(round(seconds * fps))
    writer = Writer(out, (w // 2 * 2, h // 2 * 2), fps)
    try:
        for i in range(n):
            t = i / max(1, n - 1)
            e = t * t * (3 - 2 * t)  # ease in and out
            amp = strength * max(w, h)
            if move == "orbit":
                dx, dy, zoom = (e - 0.5) * 2 * amp, 0.0, 1.06
            elif move == "rise":
                dx, dy, zoom = 0.0, (0.5 - e) * 2 * amp, 1.06
            else:
                dx, dy, zoom = (e - 0.5) * amp * 0.6, 0.0, 1.04 + 0.08 * e
            # near pixels (d=1) move the most; everything is scaled about the centre for the push
            mx = (xx - cx) / zoom + cx - dx * (d - 0.5) * (1 + 0.8 * e)
            my = (yy - cy) / zoom + cy - dy * (d - 0.5) - (d - 0.5) * (zoom - 1.0) * (yy - cy) * 0.6
            frame = cv2.remap(rgb, mx, my, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            writer.write(frame[: h // 2 * 2, : w // 2 * 2])
    finally:
        writer.close()
    return out


def _lama(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """LaMa on a 512x512 patch: the masked part filled in."""
    x = rgb.astype(np.float32).transpose(2, 0, 1)[None] / 255.0
    m = (mask > 0.5).astype(np.float32)[None, None]
    out = _session(LAMA).run(None, {"image": x, "mask": m})[0][0]
    if out.max() <= 1.5:  # some exports answer 0..1, others 0..255
        out = out * 255.0
    return np.clip(out.transpose(1, 2, 0), 0, 255).astype(np.uint8)


def remove(video: Path, start: float, end: float, track: list[list[float]], box: tuple[float, float], out: Path,
           long_edge: int = 1280, every: int = 4) -> Path:
    """[start, end) of a video with an object filled in: `track` [[seconds from start, x, y], ...] is where its centre
    is (shares of the frame), `box` its width and height (shares). LaMa fills every `every`-th frame; the frames between
    carry that fill, moved with the box (the background behind a still object moves with the camera, as the box does)."""
    import cv2

    writer, kept = None, None
    try:
        for i, rgb in enumerate(frames(video, start, end, long_edge)):
            h, w = rgb.shape[:2]
            if writer is None:
                import cv2 as _cv

                cap = _cv.VideoCapture(str(video))
                fps = cap.get(_cv.CAP_PROP_FPS) or 30.0
                cap.release()
                writer = Writer(out, (w // 2 * 2, h // 2 * 2), fps)
            t = i / fps
            k = max(0, min(len(track) - 1, int(np.searchsorted([p[0] for p in track], t, side="right")) - 1))
            if k + 1 < len(track) and track[k + 1][0] > track[k][0]:
                u = (t - track[k][0]) / (track[k + 1][0] - track[k][0])
                x = track[k][1] + (track[k + 1][1] - track[k][1]) * u
                y = track[k][2] + (track[k + 1][2] - track[k][2]) * u
            else:
                x, y = track[k][1], track[k][2]
            bw, bh = box[0] * w * 1.25, box[1] * h * 1.25  # a margin round it: no outline left behind
            side = int(min(max(bw, bh) * 2.2, min(w, h)))  # the patch: the hole and enough around it to fill from
            x0 = int(min(max(0, x * w - side / 2), w - side))
            y0 = int(min(max(0, y * h - side / 2), h - side))
            patch = rgb[y0:y0 + side, x0:x0 + side]
            m = np.zeros((side, side), np.float32)
            cx, cy = x * w - x0, y * h - y0
            m[int(max(0, cy - bh / 2)):int(cy + bh / 2), int(max(0, cx - bw / 2)):int(cx + bw / 2)] = 1.0
            if i % every == 0 or kept is None or kept.shape[0] != side:  # a fresh fill; between, it moves with the box
                small = cv2.resize(patch, (512, 512), interpolation=cv2.INTER_AREA)
                kept = cv2.resize(_lama(small, cv2.resize(m, (512, 512), interpolation=cv2.INTER_NEAREST)), (side, side),
                                  interpolation=cv2.INTER_CUBIC)
            filled = kept
            soft = cv2.GaussianBlur(m, (0, 0), max(1.0, side * 0.01))[..., None]
            frame = rgb.copy()
            frame[y0:y0 + side, x0:x0 + side] = (patch * (1 - soft) + filled * soft).astype(np.uint8)
            writer.write(frame[: h // 2 * 2, : w // 2 * 2])
    finally:
        if writer is not None:
            writer.close()
    return out


def upscale_frame(rgb: np.ndarray, tile: int = 128, pad: int = 12) -> np.ndarray:
    """A frame four times larger, by Real-ESRGAN in overlapping 128-pixel tiles."""
    h, w = rgb.shape[:2]
    out = np.zeros((h * 4, w * 4, 3), np.float32)
    session = _session(ESRGAN)
    step = tile - 2 * pad
    padded = np.pad(rgb, ((pad, pad + tile), (pad, pad + tile), (0, 0)), mode="reflect")
    for y in range(0, h, step):
        for x in range(0, w, step):
            t = padded[y:y + tile, x:x + tile].astype(np.float32).transpose(2, 0, 1)[None] / 255.0
            o = session.run(None, {"image": t})[0][0].transpose(1, 2, 0)
            ch, cw = min(step, h - y), min(step, w - x)
            out[y * 4:(y + ch) * 4, x * 4:(x + cw) * 4] = o[pad * 4:(pad + ch) * 4, pad * 4:(pad + cw) * 4]
    return np.clip(out * 255.0, 0, 255).astype(np.uint8)


def upscale(video: Path, start: float, end: float, out: Path, target_long: int = 1920) -> Path:
    """[start, end) of a low-resolution video brought up to `target_long` on its long edge with Real-ESRGAN."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()
    writer = None
    try:
        for rgb in frames(video, start, end):
            big = upscale_frame(rgb)
            h, w = big.shape[:2]
            k = target_long / max(h, w)
            big = cv2.resize(big, (round(w * k / 2) * 2, round(h * k / 2) * 2), interpolation=cv2.INTER_AREA)
            if writer is None:
                writer = Writer(out, (big.shape[1], big.shape[0]), fps)
            writer.write(big)
    finally:
        if writer is not None:
            writer.close()
    return out
