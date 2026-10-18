"""Look and listen before showing anyone: a sheet of frames (every `--every` seconds, time on each) and the sound's
numbers (loudness, true peak, the loudest moment, how far the voice stands above the music where there is music).

    python story/tools/check.py <film.mp4> [--every 0.5] [--cols 8] [--from 0 --to 30]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare import tool  # noqa: E402


def sheet(video: Path, every: float, cols: int, start: float, stop: float | None, width: int = 216) -> Path:
    from PIL import Image, ImageDraw

    dur = float(subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(video)], capture_output=True, text=True, check=True).stdout.strip())
    stop = min(dur, stop or dur)
    times = list(np.arange(start, stop, every))
    height = int(width * 16 / 9)
    raw = subprocess.run([tool(), "-v", "error", "-ss", str(start), "-to", str(stop), "-i", str(video), "-vf",
                          f"fps=1/{every},scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    n = len(raw) // (width * height * 3)
    rows = (n + cols - 1) // cols
    out = Image.new("RGB", (cols * (width + 6), rows * (height + 26)), "black")
    draw = ImageDraw.Draw(out)
    for k in range(n):
        im = Image.frombytes("RGB", (width, height), raw[k * width * height * 3:(k + 1) * width * height * 3])
        x, y = (k % cols) * (width + 6), (k // cols) * (height + 26)
        out.paste(im, (x, y))
        draw.text((x + 4, y + height + 6), f"{start + k * every:.1f}s", fill="white")
    path = video.with_name(video.stem + f"-sheet-{int(start)}.jpg")
    out.save(path, quality=88)
    return path


def sound(video: Path) -> dict:
    import pyloudnorm as pyln
    from scipy.signal import resample_poly

    raw = subprocess.run([tool(), "-v", "error", "-i", str(video), "-vn", "-ac", "2", "-ar", "48000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32).reshape(-1, 2).astype(np.float64)
    meter = pyln.Meter(48000)
    tp = max(np.abs(resample_poly(x[:, c], 4, 1)).max() for c in range(2))
    short = [meter.integrated_loudness(x[i:i + 144000]) for i in range(0, max(1, len(x) - 144000), 48000)]
    return {"lufs": round(meter.integrated_loudness(x), 1), "true_peak_db": round(20 * np.log10(tp + 1e-12), 2),
            "loudest_3s_lufs": round(max(short), 1) if short else None, "seconds": round(len(x) / 48000, 2)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--every", type=float, default=0.5)
    ap.add_argument("--cols", type=int, default=10)
    ap.add_argument("--from", dest="start", type=float, default=0.0)
    ap.add_argument("--to", dest="stop", type=float)
    a = ap.parse_args()
    print("sound:", sound(a.video))
    print("sheet:", sheet(a.video, a.every, a.cols, a.start, a.stop))


if __name__ == "__main__":
    main()
