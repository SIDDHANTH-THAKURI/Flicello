"""Turn the outro into something the renderer can append: a video file (same size and fps as the film)
plus its sound - the film's track carrying on softly under the crew, and small synthesised cues."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from ..config import Config
from ..edit.snap import music_pieces
from ..ffmpeg import FFmpegError, ffmpeg_bin
from ..render.audio_mix import SR, frames_to_samples, normalize
from ..render.segments import intermediate_encoder
from ..render.sfx import synthesize
from ..schemas import MusicSpec
from .card import Outro

OUTRO_VERSION = 1
OUTRO_LUFS = -20.0  # a little quieter than the film: credits, not a second video
MUSIC_FADE_IN = 1.0
MUSIC_FADE_OUT = 1.6


@dataclass
class Tail:
    video: Path  # picture only, same size and fps as the film
    frames: int
    audio: np.ndarray  # (samples, 2) float32 at 48 kHz, already at its final level


def outro_key(outro: Outro, quality: str) -> str:
    payload = {"v": OUTRO_VERSION, "stats": asdict(outro.stats), "size": [outro.W, outro.H], "fps": outro.fps,
               "style": outro.style, "quality": quality}
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:20]


def render_outro_video(outro: Outro, cache_dir: Path, quality: str, cfg: Config) -> Path:
    """Frames are piped straight into FFmpeg; the result is cached by everything that shapes it."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"outro_{outro_key(outro, quality)}.mp4"
    if out.exists():
        return out
    part = out.with_name(out.stem + ".part.mp4")
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{outro.W}x{outro.H}", "-r", str(outro.fps), "-i", "-",
           "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
           *intermediate_encoder(quality, cfg),
           "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
           "-an", str(part)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert proc.stdin is not None
        for i in range(outro.frames):
            proc.stdin.write(outro.frame_at(i).tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
    if proc.wait() != 0:
        part.unlink(missing_ok=True)
        raise FFmpegError("Could not render the outro", cmd, err)
    part.replace(out)
    return out


def music_time_at_end(music: MusicSpec, film_seconds: float) -> float:
    """Where the film's track would be when the film ends (following music.start and any jumps)."""
    t0, _, m0 = music_pieces(music, film_seconds)[-1]
    return m0 + (film_seconds - t0)


# ---- sound ----------------------------------------------------------------------------------------

def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    return np.minimum(1.0, t / max(attack, 1e-4)) * np.exp(-t * decay)


def _pop(freq: float) -> np.ndarray:
    """A bubbly 'bloop': a quick upward glide that settles on the note."""
    n = int(0.2 * SR)
    t = np.arange(n) / SR
    f = freq * (0.72 + 0.28 * (1 - np.exp(-t * 55)))
    phase = 2 * np.pi * np.cumsum(f) / SR
    return (np.sin(phase) + 0.18 * np.sin(2 * phase)) * _env(n, 0.003, 24)


def _pluck(freq: float) -> np.ndarray:
    n = int(1.4 * SR)
    t = np.arange(n) / SR
    y = sum(a * np.sin(2 * np.pi * freq * h * t) * np.exp(-t * d) for h, a, d in ((1, 1.0, 3.2), (2, 0.35, 6), (3, 0.12, 9)))
    return y * np.minimum(1.0, t / 0.004)


def _chime() -> np.ndarray:
    """A small bell: inharmonic partials with long, separate decays, two notes a fifth apart."""
    n = int(2.2 * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for start, base in ((0.0, 1046.5), (0.09, 1567.98)):
        k = int(start * SR)
        tt = t[: n - k]
        out[k:] += sum(a * np.sin(2 * np.pi * base * r * tt) * np.exp(-tt * d)
                       for r, a, d in ((1, 1.0, 2.2), (2.76, 0.35, 4.5), (5.4, 0.12, 8)))
    return out * np.minimum(1.0, t / 0.002)


def _tick() -> np.ndarray:
    n = int(0.03 * SR)
    t = np.arange(n) / SR
    return np.sin(2 * np.pi * 3100 * t) * np.exp(-t * 260)


def _cue_sound(kind: str, pitch: float) -> tuple[np.ndarray, float]:
    """(mono samples, offset in seconds relative to the cue time)."""
    if kind == "pop":
        return _pop(pitch), 0.0
    if kind == "pluck":
        return _pluck(pitch), 0.0
    if kind == "chime":
        return _chime(), 0.0
    if kind == "tick":
        return _tick(), 0.0
    if kind == "swell_end":  # a reverse swell that ends exactly on the cue
        y = synthesize("swell")[:, 0].astype(np.float64)
        return y, -len(y) / SR
    raise ValueError(f"unknown outro sound {kind!r}")


def outro_audio(outro: Outro, music_wav: Path | None = None, music_from: float = 0.0) -> np.ndarray:
    n = frames_to_samples(outro.frames, outro.fps)
    mono = np.zeros(n)
    for cue in outro.cues:
        y, offset = _cue_sound(cue.kind, cue.pitch)
        y = y / (np.abs(y).max() + 1e-9) * cue.gain
        start = int(round((cue.t + offset) * SR))
        a, b = max(0, start), min(n, start + len(y))
        if b > a:
            mono[a:b] += y[a - start: b - start]
    mix = np.stack([mono, mono], axis=1) * 0.5
    if music_wav is not None:
        data, sr = sf.read(str(music_wav), dtype="float32", always_2d=True)
        if sr != SR:
            raise ValueError(f"{music_wav} is {sr} Hz, expected {SR}")
        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        start = int(round(max(0.0, music_from) * SR))
        bed = np.zeros((n, 2), np.float32)
        chunk = data[start: start + n, :2]
        bed[: len(chunk)] = chunk
        t = np.arange(n) / SR
        crew_start = outro.win["crew"][0]  # type: ignore[index]
        env = np.clip((t - crew_start) / MUSIC_FADE_IN, 0, 1) * np.clip((outro.duration - t) / MUSIC_FADE_OUT, 0, 1)
        mix += bed * env[:, None] * 0.6
        mix, _, _ = normalize(mix.astype(np.float32), OUTRO_LUFS, -1.5)
    else:
        peak = np.abs(mix).max()
        if peak > 0:
            mix *= 10 ** (-12 / 20) / peak  # cues alone: modest, no loudness target to chase
    return mix.astype(np.float32)


def build_tail(outro: Outro, cache_dir: Path, quality: str, cfg: Config, music_wav: Path | None = None,
               music_from: float = 0.0) -> Tail:
    return Tail(video=render_outro_video(outro, cache_dir, quality, cfg), frames=outro.frames,
                audio=outro_audio(outro, music_wav, music_from))
