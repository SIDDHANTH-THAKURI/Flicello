"""The film's small sounds, made here (so every hit has one), all kept soft: low-passed (no bright noise that stings,
the lesson of the 5/20 cut), short, and peaking at -6 dBFS (the plan sets each one's level under the voice).

    python story/tools/sfx.py        -> public/sfx/*.wav
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare import PUBLIC  # noqa: E402

SR = 48000
SFX = PUBLIC / "sfx"
LIB = PUBLIC.parent.parent / "sfx"  # %LOCALAPPDATA%/VidoSuper/sfx: the older set
rng = np.random.default_rng(7)


def t(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * SR)) / SR


def lowpass(x: np.ndarray, hz: float, order: int = 4) -> np.ndarray:
    return sosfilt(butter(order, hz, "low", fs=SR, output="sos"), x)


def band(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return sosfilt(butter(2, [lo, hi], "band", fs=SR, output="sos"), x)


def save(name: str, x: np.ndarray, top: float = 6000) -> None:
    x = lowpass(np.asarray(x, dtype=np.float64), top)
    fade = min(len(x) // 4, int(0.004 * SR))
    if fade:
        x[-fade:] *= np.linspace(1, 0, fade)
    x = x / (np.abs(x).max() + 1e-9) * 10 ** (-6 / 20)
    SFX.mkdir(parents=True, exist_ok=True)
    sf.write(str(SFX / f"{name}.wav"), np.stack([x, x], axis=1).astype(np.float32), SR)


def main() -> None:
    # a dry tick: a ring's notch, a click of a mouse
    s = t(0.03)
    save("tick", band(rng.standard_normal(len(s)), 900, 3200) * np.exp(-s / 0.006) + 0.4 * np.sin(2 * np.pi * 1300 * s) * np.exp(-s / 0.01))
    # ten blips rising in pitch: the attachments arriving
    for k in range(10):
        s = t(0.07)
        f = 520 * 2 ** (k / 12)
        save(f"blip-{k + 1:02d}", np.sin(2 * np.pi * f * s) * np.minimum(1, s / 0.003) * np.exp(-s / 0.025))
    # a riser that stops dead (for a hard cut): noise opening up and a sweep, growing
    s = t(2.6)
    p = s / s[-1]
    noise = rng.standard_normal(len(s))
    swept = np.concatenate([band(noise[i:i + 4800], 200 + 2600 * p[i] ** 2, 400 + 3400 * p[i] ** 2) for i in range(0, len(s), 4800)])[: len(s)]
    sweep = np.sin(2 * np.pi * np.cumsum(70 + 330 * p ** 2) / SR)
    save("riser", (0.6 * swept + 0.5 * sweep) * p ** 2.2, top=5000)
    # soft static: a played clip that isn't quite a voice
    s = t(0.32)
    crackle = (rng.random(len(s)) > 0.995) * rng.standard_normal(len(s)) * 3
    gate = (np.sin(2 * np.pi * 23 * s) > -0.2).astype(float)
    save("static", (band(rng.standard_normal(len(s)), 300, 3200) * gate + crackle) * np.exp(-s / 0.2), top=4500)
    # a neon sign's hum, flickering on with the picture (30 fps frames: on, off, on, off, off, on, half, on)
    s = t(0.9)
    hum = sum(np.sin(2 * np.pi * 100 * h * s) / h for h in range(1, 7))
    frames = [1, 0, 0.8, 0, 0, 1, 0.4, 1]
    gate = np.array([frames[min(len(frames) - 1, int(x * 30))] for x in s]) * np.where(s > len(frames) / 30, 0.55, 1)
    save("buzz", hum * gate * np.minimum(1, (s[-1] - s) / 0.25), top=3000)
    # a light switch: a click and a small thump
    s = t(0.12)
    save("switch", 0.7 * band(rng.standard_normal(len(s)), 1200, 4000) * np.exp(-s / 0.002) + np.sin(2 * np.pi * 85 * s) * np.exp(-s / 0.035))
    # the older set's whoosh, backwards: a return
    w, sr = sf.read(str(LIB / "whoosh.wav"), always_2d=True)
    save("whoosh-back", w.mean(axis=1)[::-1] if sr == SR else np.interp(np.arange(int(len(w) * SR / sr)) * sr / SR, np.arange(len(w)), w.mean(axis=1))[::-1])
    # a notification gone wrong: the chime, lower and detuned against itself
    c, sr = sf.read(str(LIB / "chime.wav"), always_2d=True)
    c = c.mean(axis=1)
    pos = np.arange(int(len(c) * SR / sr / 0.82)) * 0.82 * sr / SR
    low = np.interp(pos, np.arange(len(c)), c)
    det = np.interp(pos * 1.03, np.arange(len(c)), c, right=0)
    save("chime-dark", low + 0.7 * det, top=4500)
    print("made:", ", ".join(sorted(p.stem for p in SFX.glob("*.wav"))))


if __name__ == "__main__":
    main()
