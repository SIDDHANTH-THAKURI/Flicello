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


def make_placeholder_music(path: Path, bpm: float = 100.0, seed: int = 7) -> Path:
    """License-free placeholder track (synthesised): intro, build, drop, break, drop, outro over 32 bars.

    Not meant to be good music — it lets beat-synced editing be tested before the user picks a real track.
    """
    sr = 44100
    beat = 60.0 / bpm
    bar = 4 * beat
    rng = np.random.default_rng(seed)
    structure = [("intro", 4), ("build", 4), ("drop", 8), ("break", 4), ("drop", 8), ("outro", 4)]
    total_bars = sum(n for _, n in structure)
    out = np.zeros(int((total_bars * bar + 1.5) * sr))
    chords = [[57, 60, 64], [53, 57, 60], [48, 52, 55], [55, 59, 62]]  # Am F C G
    roots = [45, 41, 48, 43]

    def hz(note: int) -> float:
        return 440.0 * 2 ** ((note - 69) / 12)

    def env(length: int, attack: float, release: float) -> np.ndarray:
        e = np.ones(length)
        a, r = int(attack * sr), int(release * sr)
        e[:a] = np.linspace(0, 1, a)
        e[length - r:] *= np.linspace(1, 0, r)
        return e

    def pad(notes: list[int], seconds: float) -> np.ndarray:
        t = np.arange(int(seconds * sr)) / sr
        wave = sum(np.sin(2 * np.pi * k * hz(n) * t * (1 + 0.002 * j)) / k ** 1.6
                   for n in notes for k in range(1, 6) for j in (-1, 1))
        return wave * env(len(t), 0.35, 0.4)

    def kick() -> np.ndarray:
        t = np.arange(int(0.3 * sr)) / sr
        freq = 45 + 80 * np.exp(-t * 28)
        return np.sin(2 * np.pi * np.cumsum(freq) / sr) * np.exp(-t * 11)

    def snare() -> np.ndarray:
        t = np.arange(int(0.2 * sr)) / sr
        noise = np.diff(rng.standard_normal(len(t) + 1))
        return (0.6 * noise * np.exp(-t * 22) + 0.5 * np.sin(2 * np.pi * 185 * t) * np.exp(-t * 30))

    def hat() -> np.ndarray:
        t = np.arange(int(0.05 * sr)) / sr
        return np.diff(np.diff(rng.standard_normal(len(t) + 2))) * np.exp(-t * 90) * 0.25

    def bass(note: int, seconds: float) -> np.ndarray:
        t = np.arange(int(seconds * sr)) / sr
        f = hz(note)
        return (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)) * env(len(t), 0.01, 0.08)

    def put(sound: np.ndarray, at: float, gain: float) -> None:
        i = int(at * sr)
        out[i:i + len(sound)] += gain * sound[: len(out) - i]

    index = 0
    for section, bars in structure:
        for b in range(bars):
            start = index * bar
            fade = 1 - 0.85 * (b + 1) / bars if section == "outro" else 1.0
            put(pad(chords[index % 4], bar + 0.4), start, 0.05 * fade)
            if section in ("build", "drop", "break"):
                for eighth in range(8):
                    put(bass(roots[index % 4] - 12, beat / 2 * 0.9), start + eighth * beat / 2, 0.22)
            if section == "build":
                for k in range(8):
                    put(hat(), start + k * beat / 2, 0.5 + 0.5 * b / bars)
                put(kick(), start, 0.6)
                put(kick(), start + 2 * beat, 0.6)
            if section == "drop":
                for k in range(4):
                    put(kick(), start + k * beat, 0.9)
                for k in (1, 3):
                    put(snare(), start + k * beat, 0.55)
                for k in range(16):
                    put(hat(), start + k * beat / 4, 0.8 if k % 2 == 0 else 0.45)
            index += 1
    out /= np.abs(out).max() / 0.89
    stereo = np.stack([out, np.roll(out, int(0.012 * sr))], axis=1).astype(np.float32)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), stereo, sr)
    return path


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
