"""See the screen: one DPI-aware screenshot of every monitor, via call-hooks/screenshot.ps1."""

from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image

from .config import REPO_ROOT

SCREENSHOT_SCRIPT = REPO_ROOT / "call-hooks" / "screenshot.ps1"


class ScreenCaptureError(RuntimeError):
    pass


def capture_screen(out_path: Path | None = None, timeout: float = 30.0) -> Path:
    """Run the screenshot script and return the path of the PNG it wrote (a fresh temp file by default)."""
    if not SCREENSHOT_SCRIPT.exists():
        raise ScreenCaptureError(f"screenshot script missing: {SCREENSHOT_SCRIPT}")
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCREENSHOT_SCRIPT)]
    if out_path is not None:
        cmd.append(str(Path(out_path).resolve()))
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScreenCaptureError(f"screenshot failed to run: {exc}") from exc
    lines = [line.strip() for line in run.stdout.splitlines() if line.strip()]
    if run.returncode != 0 or not lines:
        raise ScreenCaptureError(f"screenshot failed (exit {run.returncode}): {(run.stderr or run.stdout).strip()}")
    png = Path(lines[-1])
    if not png.exists():
        raise ScreenCaptureError(f"screenshot script printed {png} but no file is there")
    return png


def screen_size(png: Path) -> tuple[int, int]:
    """Pixel size of a capture; it is the whole virtual screen in physical pixels, so click coordinates match it."""
    with Image.open(png) as im:
        return im.size
