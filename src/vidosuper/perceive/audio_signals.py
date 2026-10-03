"""Loudness over time and sudden sound events (laughs, cheers, splashes) from the 16 kHz track."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.ndimage import median_filter

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
