"""Synthetic footage for tests and demos: the awkward cases real phone clips throw at us."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from .ffmpeg import ffmpeg_bin


def _ff(*args: str) -> None:
    subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _from_still(still: Path, out: Path, vf: str, seconds: float = 6.0, tone: int | None = 440) -> None:
    args = ["-loop", "1", "-framerate", "30", "-t", str(seconds), "-i", str(still)]
    if tone:
        args += ["-f", "lavfi", "-t", str(seconds), "-i", f"sine=frequency={tone}:sample_rate=48000"]
    args += ["-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if tone:
        args += ["-c:a", "aac", "-shortest"]
    _ff(*args, str(out))


def make_synthetic_clips(root: Path) -> tuple[Path, Path]:
    """Write nine test clips to root/clips and a 30 s 120 bpm track to root/music.wav."""
    clips = root / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    still = root / "still.png"
    _ff("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=1", "-frames:v", "1", str(still))

    _from_still(still, clips / "a_stable.mp4", "crop=1280:720:320:180")
    _from_still(still, clips / "b_shaky.mp4",
                "crop=1280:720:'320+25*sin(2*PI*t*7)+15*sin(2*PI*t*11)':'180+20*sin(2*PI*t*9)'")
    _from_still(still, clips / "c_pan.mp4", "crop=1280:720:'100+100*t':180")
    _from_still(still, clips / "d_blurry.mp4", "crop=1280:720:320:180,gblur=sigma=6")
    _from_still(still, clips / "e_dark.mp4", "crop=1280:720:320:180,eq=brightness=-0.45")
    _ff("-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=60:duration=5",
        "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000:duration=5",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(clips / "f_portrait60.mov"))
    _ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=4",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(clips / "g_noaudio.mp4"))
    _ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=550:sample_rate=48000:duration=4",
        "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
        "-x265-params", "log-level=error:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
        "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
        "-tag:v", "hvc1", "-c:a", "aac", "-shortest", str(clips / "h_hdr.mov"))
    plain = root / "rot_src.mp4"
    _ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000:duration=4",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(plain))
    _ff("-display_rotation", "90", "-i", str(plain), "-c", "copy", str(clips / "i_rotated.mp4"))
    plain.unlink()
    still.unlink()

    sr = 48000
    t = np.arange(30 * sr) / sr
    chord = 0.15 * np.sin(2 * np.pi * 220 * t) + 0.1 * np.sin(2 * np.pi * 277 * t)
    clicks = (np.mod(t, 0.5) < 0.03) * np.sin(2 * np.pi * 1000 * t) * 0.5
    music = root / "music.wav"
    sf.write(str(music), np.stack([chord + clicks] * 2, axis=1).astype(np.float32), sr)
    return clips, music
