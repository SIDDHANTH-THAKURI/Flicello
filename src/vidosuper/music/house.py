"""A beat made on the spot, for videos whose maker brought no music.

Fully synthesised here (drums, a bass line and a short chord stab from plain waveforms), so it is original, owes
nothing to anyone and can be posted anywhere. It is built for cutting: a clear kick on every beat, a clap on the 2 and
4, a syncopated stab that gives the cut something to land on between the beats, a short build into a drop, a breath
and a last drop, then a closing hit that rings out. Tempo follows the cutting pace: slower for calm videos.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

SR = 44100
TEMPO = {"calm": 96, "balanced": 108, "fast": 118, "every": 120}  # the beat tracker reads 120 and under reliably; above it halves the tempo
# the stab rhythm in sixteenths over a bar (3 + 3 + 2, twice): the syncopation dance music cuts to
STABS = (0, 3, 6, 8, 11, 14)
CHORD = (220.0, 261.63, 329.63, 392.0)  # A minor 7


def _kick(n: int) -> np.ndarray:
    t = np.arange(n) / SR
    freq = 45 + 110 * np.exp(-t * 28)
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 7)
    click = np.exp(-t * 400) * 0.5
    return (body + click) * 0.95


def _clap(n: int, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n) / SR
    noise = rng.standard_normal(n)
    noise = np.convolve(noise, [1, -0.95])[:n]  # a gentle high-pass
    env = np.exp(-t * 22) + 0.6 * np.exp(-((t - 0.012) ** 2) * 9000)
    return noise * env * 0.45


def _hat(n: int, rng: np.random.Generator, open_: bool = False) -> np.ndarray:
    t = np.arange(n) / SR
    noise = np.diff(rng.standard_normal(n + 1))
    return noise * np.exp(-t * (14 if open_ else 90)) * (0.2 if open_ else 0.16)


def _bass(n: int, freq: float) -> np.ndarray:
    t = np.arange(n) / SR
    wave = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(2 * np.pi * 2 * freq * t)
    return wave * np.exp(-t * 3.2) * 0.4


def _stab(n: int) -> np.ndarray:
    t = np.arange(n) / SR
    out = np.zeros(n)
    for f in CHORD:
        for det in (0.996, 1.004):
            out += (2 * ((f * det * t) % 1.0) - 1.0) * 0.07  # saws a little apart
    return out * np.exp(-t * 9) * (1 - np.exp(-t * 700))


def _put(track: np.ndarray, sound: np.ndarray, at: float, gain: float = 1.0) -> None:
    i = int(at * SR)
    if i >= len(track):
        return
    j = min(len(track), i + len(sound))
    track[i:j] += sound[:j - i] * gain


def make_beat(path: Path, seconds: float, pace: str = "balanced", seed: int = 7) -> dict:
    """Write a WAV of about `seconds` (whole bars) to `path`; returns {"seconds", "bpm", "drop_bar"}."""
    import soundfile as sf

    bpm = TEMPO.get(pace, 108)
    beat = 60.0 / bpm
    bar = 4 * beat
    bars = max(8, int(np.ceil(seconds / bar)) + 1)
    rng = np.random.default_rng(seed)
    total = int((bars * bar + 2.5) * SR)
    drums, bass, stab = np.zeros(total), np.zeros(total), np.zeros(total)
    kick, clap = _kick(int(0.4 * SR)), _clap(int(0.3 * SR), rng)
    hat, open_hat = _hat(int(0.06 * SR), rng), _hat(int(0.25 * SR), rng, True)
    bass_note, stab_hit = _bass(int(beat * 1.9 * SR), 55.0), _stab(int(0.5 * SR))
    drop = max(2, int(bars * 0.25))  # the first drop; a breath later, then the last drop
    breath = int(bars * 0.62)
    last = bars - 2
    for b in range(bars):
        t0 = b * bar
        building = b < drop
        silent = breath <= b < breath + 2
        full = (drop <= b < breath) or b >= breath + 2
        for k in range(4):  # beats
            at = t0 + k * beat
            if (full and b < last) or (b >= last):
                _put(drums, kick, at)
                _put(bass, bass_note, at + beat / 2, 0.8)
            elif building and b >= drop - 2:
                _put(drums, kick, at, 0.7)
            if k in (1, 3) and (full or (building and b >= 1)):
                _put(drums, clap, at, 0.8 if full else 0.5)
            if not silent:
                _put(drums, open_hat, at + beat / 2, 0.8)
            for h in range(4):  # sixteenth hats
                if not silent and (full or b >= 1) and h % 2 == 1:
                    _put(drums, hat, at + h * beat / 4, 0.7 if full else 0.45)
        if full or b == drop - 1:
            for s in STABS:
                _put(stab, stab_hit, t0 + s * beat / 4, 0.9 if full else 0.5)
        if b == drop - 1:  # the build's last beat: a roll of claps into the drop
            for r in range(8):
                _put(drums, clap, t0 + 3 * beat + r * beat / 8, 0.35 + 0.08 * r)
    mix = drums + bass + stab
    end = int((bars - 1) * bar * SR)
    mix[end:] *= np.linspace(1.0, 0.0, len(mix) - end) ** 2  # rings out through the last bar
    # the track as a whole ducks a little under each kick so the bass and stabs stay out of its way
    peak = float(np.abs(mix).max()) or 1.0
    mix = np.tanh(mix / peak * 1.4) / np.tanh(1.4) * 0.7
    out = np.stack([mix, np.roll(mix, int(0.0004 * SR))], axis=1).astype(np.float32)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), out, SR)
    return {"seconds": round(len(mix) / SR, 2), "bpm": bpm, "drop_bar": drop}
