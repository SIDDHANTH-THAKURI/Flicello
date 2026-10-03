"""Audio for the edit: shot audio on the timeline, music bed with ducking, loudness + true-peak limiting."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
import pyloudnorm
import soundfile as sf
from scipy.ndimage import maximum_filter1d, minimum_filter1d, uniform_filter1d
from scipy.signal import resample_poly

from ..edit.timeline import Timeline
from ..ffmpeg import run_ffmpeg
from ..schemas import EditMeta, MusicSpec
from .segments import SegmentOutput

SR = 48000
CLICK_GUARD = int(0.005 * SR)  # 5 ms fades at hard cuts so audio never clicks
ENV_HOP = 0.01  # envelope resolution for ducking, seconds


def frames_to_samples(frames: int, fps: int) -> int:
    return int(round(frames * SR / fps))


def db_to_gain(db: float | np.ndarray) -> float | np.ndarray:
    return 10.0 ** (np.asarray(db) / 20.0)


def load_wav(path: str | Path, length: int, offset: int = 0) -> np.ndarray:
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if sr != SR:
        raise ValueError(f"{path} is {sr} Hz, expected {SR}")
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    data = data[offset:, :2]
    out = np.zeros((length, 2), np.float32)
    out[: min(length, len(data))] = data[:length]
    return out


def _ramp_in(n: int) -> np.ndarray:
    return np.sin((np.arange(n, dtype=np.float32) + 0.5) / n * (np.pi / 2))  # equal-power


def _ramp_out(n: int) -> np.ndarray:
    return _ramp_in(n)[::-1]


def decode_music(path: str | Path, cache_dir: Path) -> Path:
    src = Path(path)
    stat = src.stat()
    key = hashlib.sha1(f"{src.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:16]
    out = cache_dir / f"{key}.wav"
    if not out.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        part = out.with_name(out.stem + ".part.wav")
        run_ffmpeg(["-i", str(src), "-vn", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_f32le", str(part)],
                   what=f"Could not decode music {src.name}")
        os.replace(part, out)
    return out


def _activity(signal: np.ndarray, threshold_db: float = -45.0) -> np.ndarray:
    """Per-envelope-hop flag: is there audible shot audio (speech, laughter, ambience)?"""
    hop = int(ENV_HOP * SR)
    n = len(signal) // hop
    if n == 0:
        return np.zeros(0, bool)
    frames = signal[: n * hop].reshape(n, hop, -1)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=(1, 2)))
    return 20 * np.log10(rms + 1e-9) > threshold_db


def duck_envelope(mask_hops: np.ndarray, total: int, attack: float = 0.15, release: float = 0.4) -> np.ndarray:
    """0..1 per sample: how far to pull the music down. Dips slightly before speech, recovers after."""
    if len(mask_hops) == 0 or not mask_hops.any():
        return np.zeros(total, np.float32)
    pre, post = int(attack / ENV_HOP), int(release / ENV_HOP)
    size = pre + post + 1
    # output[i] = max(mask[i - post : i + pre]); a positive origin shifts the window into the past
    widened = maximum_filter1d(mask_hops.astype(np.float32), size=size, origin=(post - pre) // 2)
    smooth = uniform_filter1d(widened, size=max(3, int(0.12 / ENV_HOP)))
    hop = int(ENV_HOP * SR)
    times = (np.arange(len(smooth)) + 0.5) * hop
    return np.interp(np.arange(total), times, smooth).astype(np.float32)


def mix_timeline(timeline: Timeline, shots: list[SegmentOutput], meta: EditMeta,
                 music: MusicSpec | None, music_wav: Path | None, duck_db: float) -> np.ndarray:
    fps = timeline.fps
    total = frames_to_samples(timeline.total_frames, fps)
    dialog = np.zeros((total, 2), np.float32)
    duck_mask = np.zeros(total, bool)
    items = timeline.items
    for i, (item, shot) in enumerate(zip(items, shots)):
        if shot.audio is None or item.seg.audio.mode == "mute":
            continue
        start = frames_to_samples(item.start, fps)
        n = min(frames_to_samples(item.frames, fps), total - start)
        if n <= 0:
            continue
        x = load_wav(shot.audio, n) * np.float32(db_to_gain(item.seg.audio.gain_db))
        nxt = items[i + 1] if i + 1 < len(items) else None
        fade_in = frames_to_samples(item.trans_frames, fps) if item.trans_frames else CLICK_GUARD
        fade_out = frames_to_samples(nxt.trans_frames, fps) if nxt is not None and nxt.trans_frames else CLICK_GUARD
        fade_in, fade_out = min(fade_in, n // 2), min(fade_out, n // 2)
        if fade_in:
            x[:fade_in] *= _ramp_in(fade_in)[:, None]
        if fade_out:
            x[n - fade_out:] *= _ramp_out(fade_out)[:, None]
        dialog[start:start + n] += x
        if item.seg.audio.duck_music:
            duck_mask[start:start + n] = True

    out = dialog.copy()
    if music is not None and music_wav is not None:
        bed = load_wav(music_wav, total, offset=int(round(music.start * SR)))
        fi, fo = min(int(music.fade_in * SR), total // 2), min(int(music.fade_out * SR), total // 2)
        if fi:
            bed[:fi] *= np.linspace(0, 1, fi, dtype=np.float32)[:, None]
        if fo:
            bed[total - fo:] *= np.linspace(1, 0, fo, dtype=np.float32)[:, None]
        hop = int(ENV_HOP * SR)
        speaking = _activity(dialog)
        if len(speaking):
            speaking &= duck_mask[: len(speaking) * hop: hop]
        env = duck_envelope(speaking, total)
        out += bed * db_to_gain(music.gain_db + duck_db * env).astype(np.float32)[:, None]

    # picture fades are mirrored in the sound
    fi, fo = min(int(meta.fade_in * SR), total // 2), min(int(meta.fade_out * SR), total // 2)
    if fi:
        out[:fi] *= np.linspace(0, 1, fi, dtype=np.float32)[:, None]
    if fo:
        out[total - fo:] *= np.linspace(1, 0, fo, dtype=np.float32)[:, None]
    return out


def _true_peaks(x: np.ndarray, chunk: int = SR * 20, pad: int = 64) -> np.ndarray:
    """Per-sample peak of the 4x-oversampled signal (catches inter-sample overs)."""
    n = len(x)
    peaks = np.empty(n, np.float32)
    for start in range(0, n, chunk):
        lo, hi = max(0, start - pad), min(n, start + chunk + pad)
        over = resample_poly(x[lo:hi], 4, 1, axis=0)
        per = np.abs(over).max(axis=1)
        per = per[: (hi - lo) * 4].reshape(hi - lo, 4).max(axis=1)
        end = min(n, start + chunk)
        peaks[start:end] = per[start - lo: end - lo]
    return peaks


def true_peak_limit(x: np.ndarray, ceiling_db: float, lookahead: float = 0.005, release: float = 0.05) -> np.ndarray:
    """Look-ahead peak limiter: gain dips just before each over and holds ~50 ms before recovering."""
    ceiling = float(db_to_gain(ceiling_db))
    if len(x) == 0:
        return x
    peaks = _true_peaks(x)
    if peaks.max() <= ceiling:
        return x
    g = np.minimum(1.0, ceiling / np.maximum(peaks, 1e-12)).astype(np.float32)
    ahead, hold = int(lookahead * SR), int(release * SR)
    # held[i] = min(g[i - hold : i + ahead]); smoothing over ±ahead then never exceeds g at a peak
    held = minimum_filter1d(g, size=ahead + hold + 1, origin=(hold - ahead) // 2)
    g = uniform_filter1d(held, size=2 * ahead + 1)
    y = x * g[:, None]
    residual = _true_peaks(y).max()
    if residual > ceiling:
        y *= ceiling / residual
    return y


def normalize(x: np.ndarray, target_lufs: float, ceiling_db: float) -> tuple[np.ndarray, float | None, float | None]:
    """Bring integrated loudness to target (EBU R128 / BS.1770) and keep true peaks under the ceiling.

    Limiting shaves loudness off peaky material, so we re-measure and add make-up gain a few times.
    """
    if len(x) < SR // 2:
        return x, None, None
    meter = pyloudnorm.Meter(SR)
    loudness = meter.integrated_loudness(x.astype(np.float64))
    if not np.isfinite(loudness) or loudness < -70:
        return x, None, None
    total_gain = target_lufs - loudness
    y = true_peak_limit((x * np.float32(db_to_gain(total_gain))).astype(np.float32), ceiling_db)
    for _ in range(4):
        error = target_lufs - meter.integrated_loudness(y.astype(np.float64))
        if abs(error) < 0.2:
            break
        total_gain += error
        y = true_peak_limit((y * np.float32(db_to_gain(error))).astype(np.float32), ceiling_db)
    return y, float(loudness), float(total_gain)


def write_wav(path: Path, audio: np.ndarray) -> None:
    sf.write(str(path), np.clip(audio, -1.0, 1.0), SR, subtype="FLOAT")
