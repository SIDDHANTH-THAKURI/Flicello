"""Loudness over time and sudden sound events (laughs, cheers, splashes) from the 16 kHz track."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.ndimage import median_filter
from scipy.signal import butter, find_peaks, sosfiltfilt

HOP_SECONDS = 0.1


@dataclass
class AudioSeries:
    t: np.ndarray  # window centres, seconds
    rms_db: np.ndarray  # dBFS per 100 ms window


def measure_audio(wav16k: str | Path) -> AudioSeries:
    data, sr = sf.read(str(wav16k), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    hop = max(1, int(sr * HOP_SECONDS))
    count = len(mono) // hop
    if count == 0:
        return AudioSeries(t=np.zeros(0, np.float32), rms_db=np.zeros(0, np.float32))
    frames = mono[: count * hop].reshape(count, hop)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1))
    rms_db = (20 * np.log10(rms + 1e-9)).astype(np.float32)
    t = ((np.arange(count) + 0.5) * HOP_SECONDS).astype(np.float32)
    return AudioSeries(t=t, rms_db=rms_db)


def find_impacts(wav: str | Path, min_sharpness: float = 12.0, within_db: float = 14.0, spacing: float = 0.15,
                 floor_db: float = -38.0) -> list[tuple[float, float]]:
    """Sharp, loud sounds to the millisecond: a bat or racket meeting a ball, a clap, a door, a splash, a drum hit.
    (seconds, level in dB) of each. An impact is a burst in the high band (above 1.5 kHz) that is at least
    `min_sharpness` times louder than the 150 ms before it and within `within_db` of the clip's loudest such burst,
    so a clip's own steady noises (a fan, a net rattling, footsteps) are left out and only its real hits stay.
    Works on any sample rate of 16 kHz or more (the 48 kHz track gives the exact millisecond)."""
    data, sr = sf.read(str(wav), dtype="float32", always_2d=True)
    if sr < 16000 or len(data) < sr:
        return []
    mono = data.mean(axis=1)
    high = sosfiltfilt(butter(4, 1500, "highpass", fs=sr, output="sos"), mono)
    hop = sr // 1000
    n = len(high) // hop
    if n < 400:
        return []
    env = np.sqrt((high[: n * hop].reshape(n, hop) ** 2).mean(axis=1))
    db = 20 * np.log10(env + 1e-9)
    csum = np.concatenate([[0.0], np.cumsum(env)])
    idx = np.arange(n)
    lo, hi = np.maximum(0, idx - 150), np.maximum(1, idx - 5)
    before = (csum[hi] - csum[lo]) / np.maximum(1, hi - lo)  # mean of the 150 ms before, leaving out the last 5 ms
    sharp = env / (before + 1e-6)
    peaks, _ = find_peaks(env, distance=max(1, int(spacing * 1000)))
    cand = [i for i in peaks if sharp[i] >= min_sharpness and db[i] >= floor_db]
    if not cand:
        return []
    loudest = max(db[i] for i in cand)
    return [(round(float(i) / 1000.0, 3), round(float(db[i]), 1)) for i in cand if db[i] >= loudest - within_db]


def audio_peaks(series: AudioSeries, jump_db: float = 10.0, floor_db: float = -35.0) -> list[float]:
    """Times where the sound jumps well above its recent level (reactions, impacts, cheers)."""
    if len(series.rms_db) < 5:
        return []
    baseline = median_filter(series.rms_db, size=21, mode="nearest")
    hot = (series.rms_db - baseline > jump_db) & (series.rms_db > floor_db)
    peaks: list[float] = []
    i = 0
    while i < len(hot):
        if hot[i]:
            j = i
            while j + 1 < len(hot) and hot[j + 1]:
                j += 1
            k = i + int(np.argmax(series.rms_db[i:j + 1]))
            if not peaks or series.t[k] - peaks[-1] > 0.5:
                peaks.append(round(float(series.t[k]), 2))
            i = j + 1
        else:
            i += 1
    return peaks
