"""Locating and running FFmpeg / ffprobe."""

from __future__ import annotations

import functools
import glob
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    def __init__(self, message: str, cmd: list[str], stderr: str = ""):
        tail = "\n".join(stderr.strip().splitlines()[-15:])
        super().__init__(f"{message}\n  command: {' '.join(cmd)[:600]}\n  ffmpeg said:\n{tail}")
        self.cmd = cmd
        self.stderr = stderr


def _candidates(name: str) -> list[str]:
    exe = f"{name}.exe" if os.name == "nt" else name
    found: list[str] = []
    env_dir = os.environ.get("VIDO_FFMPEG_DIR")
    if env_dir:
        found.append(str(Path(env_dir) / exe))
    on_path = shutil.which(name)
    if on_path:
        found.append(on_path)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        found.append(str(Path(local) / "Microsoft" / "WinGet" / "Links" / exe))
        pattern = str(Path(local) / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg*" / "*" / "bin" / exe)
        found.extend(sorted(glob.glob(pattern), reverse=True))
    found.append(str(Path("C:/ProgramData/chocolatey/bin") / exe))
    return found


@functools.lru_cache(maxsize=None)
def find_tool(name: str) -> str:
    for cand in _candidates(name):
        if cand and Path(cand).is_file():
            return cand
    raise FileNotFoundError(
        f"Could not find {name}. Install FFmpeg (winget install Gyan.FFmpeg) "
        f"or set VIDO_FFMPEG_DIR to the folder containing {name}."
    )


def ffmpeg_bin() -> str:
    return find_tool("ffmpeg")


def ffprobe_bin() -> str:
    return find_tool("ffprobe")


def run_ffmpeg(args: list[str], *, what: str = "ffmpeg failed", timeout: float | None = None) -> str:
    """Run ffmpeg with standard flags. Returns stderr (ffmpeg logs there)."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    stderr = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(what, cmd, stderr)
    return stderr


def ffprobe_json(path: str | Path, timeout: float | None = None) -> dict:
    """timeout: seconds before giving up on the file (for untrusted uploads; raises FFmpegError)."""
    cmd = [
        ffprobe_bin(), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"ffprobe took longer than {timeout:.0f}s on {path}", cmd) from exc
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe could not read {path}", cmd, proc.stderr.decode("utf-8", errors="replace"))
    return json.loads(proc.stdout.decode("utf-8", errors="replace") or "{}")


def _list_output(flag: str) -> str:
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", flag], capture_output=True)
    return proc.stdout.decode("utf-8", errors="replace")


_ENCODER_LINE = re.compile(r"^\s*[VAS][.A-Z]{5}\s+(\S+)")
_FILTER_LINE = re.compile(r"^\s*\S+\s+(\S+)\s+\S*->\S*")
_VERSION = re.compile(r"version\s+n?(\d+)\.(\d+)")


@functools.lru_cache(maxsize=1)
def capabilities() -> dict:
    """Which encoders/filters/hwaccels this FFmpeg build offers."""
    version_text = _list_output("-version")
    version_line = version_text.splitlines()[0] if version_text else "unknown"
    match = _VERSION.search(version_line)
    encoders = {m.group(1) for line in _list_output("-encoders").splitlines() if (m := _ENCODER_LINE.match(line))}
    filters = {m.group(1) for line in _list_output("-filters").splitlines() if (m := _FILTER_LINE.match(line))}
    hw_lines = _list_output("-hwaccels").splitlines()
    hwaccels = {line.strip() for line in hw_lines[1:] if line.strip()}
    return {
        "version": version_line,
        "major": int(match.group(1)) if match else 0,
        "encoders": encoders,
        "filters": filters,
        "hwaccels": hwaccels,
    }
