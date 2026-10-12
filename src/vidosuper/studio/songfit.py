"""Make a short song long enough: repeat its strongest bars, on the beat, and keep its real ending.

A 16 s song under a 60 s video is the most common way a video comes out shorter than asked. This finds the song's
loudest full section, picks a loop of 8, 4 or 2 whole bars from its end (cut on bar lines found by the music
analysis), repeats that loop as often as needed with a short equal-power crossfade at each join (the crossfade
blends the loop's end into the audio that originally came just before the loop starts, so the join is as smooth
as the song itself), and then plays the song's own ending. The result is a WAV the studio uses instead of the
upload; the original stays beside it.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import correlate

from ..music.analysis import MusicAnalysis, load_or_analyze
from ..render.audio_mix import SR, decode_music

CROSSFADE = 0.04  # seconds
HEADROOM = 2.0  # the extended song runs this much past the length asked, so the video's last beats have music under them


class SongTooShort(ValueError):
    """The song has too few bars to repeat cleanly."""


def pick_loop(song: MusicAnalysis) -> tuple[float, float, int]:
    """(start, end, bars) of the loop: whole bars at the end of the song's strongest section."""
    downs = [float(d) for d in song.downbeats]
    if len(downs) < 3:
        raise SongTooShort("This song is too short to repeat cleanly: add a longer one.")
    best = max(song.sections, key=lambda s: s.energy) if song.sections else None
    inside = [d for d in downs if best is None or best.start - 0.05 <= d <= best.end + 0.05]
    if len(inside) < 3:
        inside = downs
    bars = next((n for n in (8, 4, 2) if len(inside) - 1 >= n), len(inside) - 1)
    end = inside[-1]
    return inside[-1 - bars], end, bars


def _envelope(mono: np.ndarray, sr: int) -> np.ndarray:
    """Loudness shape at 1 kHz (the beats' attacks), for lining two stretches of the song up."""
    hop = sr // 1000
    n = len(mono) // hop
    return np.abs(mono[: n * hop]).reshape(n, hop).mean(axis=1)


def refine_end(mono: np.ndarray, sr: int, i0: int, i1: int, run_up: float = 1.0, reach: float = 0.08) -> int:
    """Nudge the loop's end (by at most `reach` s) so the second before it has the same rhythm as the second before
    the loop's start: the join is then on the beat. The analysis' tempo is only good to ~0.1 BPM, which would
    otherwise slip a loop by ~20 ms; many repeats would drift audibly."""
    hop = sr // 1000
    n_run, n_reach = int(run_up * 1000), int(reach * 1000)
    e0, e1 = i0 // hop, i1 // hop
    if e0 - n_run < 0 or e1 + n_reach > len(mono) // hop:
        return i1
    env = _envelope(mono, sr)
    a = env[e0 - n_run:e0]
    b = env[e1 - n_run - n_reach:e1 + n_reach]
    if a.std() < 1e-6 or b.std() < 1e-6:
        return i1
    a = a - a.mean()
    score = correlate(b - b.mean(), a, mode="valid", method="fft")  # one value per shift, -reach .. +reach
    shifts = np.arange(-n_reach, n_reach + 1)
    best = int(np.argmax(score))
    if score[best] < 1.15 * float(np.mean(np.abs(score))):  # no clear rhythm to lock onto: leave the analysis' bar line
        return i1
    return i1 + int(shifts[best]) * hop


def extend_song(src: Path, dst: Path, target_seconds: float, cache_dir: Path) -> dict:
    """Write dst (WAV) as src stretched to at least target_seconds + HEADROOM; returns what was done."""
    song = load_or_analyze(src, cache_dir)
    if song.duration >= target_seconds + HEADROOM:
        raise ValueError("The song is already long enough.")
    start, end, bars = pick_loop(song)
    audio, sr = sf.read(str(decode_music(src, cache_dir)), dtype="float32", always_2d=True)
    assert sr == SR
    i0, i1, xf = int(round(start * sr)), int(round(end * sr)), int(CROSSFADE * sr)
    i1 = refine_end(audio.mean(axis=1), sr, i0, i1)
    if i0 - xf < 0 or i1 - i0 < 4 * xf:
        raise SongTooShort("This song is too short to repeat cleanly: add a longer one.")
    body_seconds = (i1 - i0) / sr
    need = target_seconds + HEADROOM - song.duration
    copies = max(1, math.ceil(need / body_seconds))
    fade_out = np.cos(np.linspace(0, math.pi / 2, xf, dtype=np.float32))[:, None]
    fade_in = np.sin(np.linspace(0, math.pi / 2, xf, dtype=np.float32))[:, None]
    pieces = [audio[:i1].copy()]
    for _ in range(copies):
        tail = pieces[-1]
        tail[-xf:] = tail[-xf:] * fade_out + audio[i0 - xf:i0] * fade_in  # the loop's end melts into its own run-up
        pieces.append(audio[i0:i1].copy())
    pieces.append(audio[i1:])  # the song's own ending, unchanged
    out = np.concatenate(pieces)
    peak = float(np.abs(out).max())
    if peak > 0.99:  # crossfades of correlated audio can add up
        out = out * (0.99 / peak)
    dst.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dst), out, sr, subtype="PCM_16")
    return {"loop_start": round(start, 2), "loop_bars": bars, "loop_seconds": round(body_seconds, 2), "repeats": copies,
            "seconds": round(len(out) / sr, 1), "tempo": round(song.tempo, 1)}
