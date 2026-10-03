"""Contact sheets: grids of frames with timestamps burned in. This is how Claude "watches" a clip."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES = ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def format_time(seconds: float) -> str:
    minutes, sec = divmod(max(0.0, seconds), 60)
    return f"{int(minutes)}:{sec:04.1f}"


def grab_frames(video: str | Path, times: list[float]) -> list[tuple[float, np.ndarray]]:
    """Frames nearest to each requested time, read sequentially (fast on short-GOP proxies)."""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    wanted = sorted(times)
    out: list[tuple[float, np.ndarray]] = []
    index, k = 0, 0
    try:
        if wanted and wanted[0] > 2.0:  # jump close to the first frame we need
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(wanted[0] * fps) - 2))
            index = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
        while k < len(wanted) and cap.grab():
            t = index / fps
            if t + 0.5 / fps >= wanted[k]:
                ok, frame = cap.retrieve()
                if ok:
                    while k < len(wanted) and t + 0.5 / fps >= wanted[k]:
                        out.append((t, frame))
                        k += 1
            index += 1
    finally:
        cap.release()
    return out


def build_sheet(frames: list[tuple[float, np.ndarray]], *, tile_long_edge: int = 480, columns: int | None = None,
                label_prefix: str = "", labels: list[str] | None = None) -> Image.Image:
    if not frames:
        raise ValueError("no frames to lay out")
    h, w = frames[0][1].shape[:2]
    scale = tile_long_edge / max(h, w)
    tw, th = int(round(w * scale)), int(round(h * scale))
    cols = columns or (4 if w >= h else 6)
    rows = (len(frames) + cols - 1) // cols
    gap = 4
    sheet = Image.new("RGB", (cols * tw + (cols - 1) * gap, rows * th + (rows - 1) * gap), "white")
    draw = ImageDraw.Draw(sheet)
    font = _font(max(14, th // 12))
    for i, (t, frame) in enumerate(frames):
        tile = Image.fromarray(cv2.cvtColor(cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA),
                                            cv2.COLOR_BGR2RGB))
        x, y = (i % cols) * (tw + gap), (i // cols) * (th + gap)
        sheet.paste(tile, (x, y))
        label = labels[i] if labels else f"{label_prefix}{format_time(t)}"
        box = draw.textbbox((x + 6, y + 4), label, font=font)
        draw.rectangle((box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 3), fill=(0, 0, 0))
        draw.text((x + 6, y + 4), label, fill=(255, 255, 0), font=font)
    return sheet


def uniform_times(duration: float, every: float, max_frames: int) -> list[float]:
    count = max(1, min(max_frames, int(duration / every) + 1))
    if count == 1:
        return [duration / 2]
    step = duration / count
    return [round(step * (i + 0.5), 3) for i in range(count)]


def sheets_for_video(video: str | Path, duration: float, out_dir: Path, stem: str, *, every: float = 2.0,
                     per_sheet: int = 16, max_frames: int = 96, tile_long_edge: int = 480) -> list[Path]:
    """Uniformly sampled frames → one or more sheet images (JPEG). Returns their paths."""
    times = uniform_times(duration, every, max_frames)
    frames = grab_frames(video, times)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for n, start in enumerate(range(0, len(frames), per_sheet)):
        sheet = build_sheet(frames[start:start + per_sheet], tile_long_edge=tile_long_edge)
        path = out_dir / f"{stem}_{n + 1:02d}.jpg"
        sheet.save(path, quality=88)
        paths.append(path)
    return paths
