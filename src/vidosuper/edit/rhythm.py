"""Rhythmic sound layers (ticks, footsteps, a clock) have to sit on the music's beat grid.

A steady layer at a slightly different tempo drifts in and out of time with the track, which viewers
hear as "the beat isn't syncing" - it happened: an indicator tick-tock every 0.70 s against a 0.667 s
beat, louder than the music in the opening.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import soundfile as sf

from ..schemas import EDL
from .snap import timeline_grid

MIN_ONSETS = 6
MAX_IOI_CV = 0.25  # onsets this regular are heard as a rhythm
DRIFT_MS = 40.0  # median distance from the beat/half-beat grid that reads as off
AUDIBLE_MUSIC_DB = -30.0  # below this the music is effectively off, so nothing to clash with


def _music_gain_db(edl: EDL, t: np.ndarray) -> np.ndarray:
    points = sorted(edl.music.automation, key=lambda p: p.t) if edl.music else []
    if not points:
        return np.zeros_like(t)
    return np.interp(t, [p.t for p in points], [p.gain_db for p in points])


def rhythm_warnings(edl: EDL, analysis, total: float, resolve: Callable[[str], Path]) -> list[str]:
    """One warning per steady sound layer that sits off the music's beat while the music is audible."""
    if edl.music is None or not edl.sounds:
        return []
    import librosa

    beats, _ = timeline_grid(edl.music, analysis, total + 5.0)
    if len(beats) < 2:
        return []
    grid = np.sort(np.concatenate([beats, beats[:-1] + np.diff(beats) / 2]))
    warnings = []
    for k, layer in enumerate(edl.sounds, 1):
        if layer.source.startswith("sfx:"):
            continue
        try:
            path = resolve(layer.source)
            rate = sf.info(str(path)).samplerate
            y, sr = sf.read(str(path), start=int(layer.src_in * rate), frames=int(layer.duration * rate),
                            dtype="float32", always_2d=True)
        except Exception:  # noqa: BLE001 - a layer we can't read is reported by validation, not here
            continue
        if len(y) < sr:
            continue
        onsets = librosa.onset.onset_detect(y=y.mean(axis=1), sr=sr, units="time", hop_length=256)
        if len(onsets) < MIN_ONSETS:
            continue
        gaps = np.diff(onsets)
        if np.std(gaps) / max(float(np.mean(gaps)), 1e-6) > MAX_IOI_CV:
            continue  # irregular: ambience, not a rhythm
        times = layer.at + onsets
        times = times[_music_gain_db(edl, times) + edl.music.gain_db > AUDIBLE_MUSIC_DB]
        if len(times) < MIN_ONSETS:
            continue
        off_ms = np.array([abs(t - grid[np.argmin(np.abs(grid - t))]) for t in times]) * 1000
        if np.median(off_ms) > DRIFT_MS:
            warnings.append(
                f"sound layer {k} ({Path(layer.source).name}) is a steady rhythm (every {np.median(gaps):.2f} s) "
                f"sitting {np.median(off_ms):.0f} ms off the music's beat on average, so it drifts against the "
                "track: re-lay it on the beat grid or drop it")
    return warnings
