"""Built-in sound effects, synthesised locally (no licences involved): whooshes for whip pans,
risers before a drop, impacts on a reveal, sub drops, swells, ticks and a camera shutter."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

SR = 48000
SFX_NAMES = ("whoosh", "whoosh_long", "riser", "impact", "subdrop", "swell", "tick", "shutter", "pop", "snip",
             "boing", "chime")


def _noise(seconds: float, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal(int(seconds * SR))


def _bandpass_sweep(x: np.ndarray, f0: float, f1: float, steps: int = 24) -> np.ndarray:
    """Noise through a band-pass whose centre glides from f0 to f1 (block-wise)."""
    out = np.zeros_like(x)
    edges = np.linspace(0, len(x), steps + 1).astype(int)
    for k in range(steps):
        fc = f0 * (f1 / f0) ** (k / max(1, steps - 1))
        sos = butter(2, [max(30, fc / 1.6), min(SR / 2 - 100, fc * 1.6)], btype="band", fs=SR, output="sos")
        a, b = max(0, edges[k] - 512), edges[k + 1]
        out[edges[k]:b] = sosfilt(sos, x[a:b])[edges[k] - a:]
    return out


def _env(n: int, attack: float, release: float, curve: float = 2.0) -> np.ndarray:
    t = np.linspace(0, 1, n)
    peak = attack / (attack + release)
    rise = np.clip(t / max(peak, 1e-6), 0, 1) ** curve
    fall = np.clip((1 - t) / max(1 - peak, 1e-6), 0, 1) ** curve
    return np.minimum(rise, fall)


def synthesize(name: str) -> np.ndarray:
    if name == "whoosh":
        x = _bandpass_sweep(_noise(0.7, 1), 300, 4000)
        y = x * _env(len(x), 0.45, 0.25)
    elif name == "whoosh_long":
        x = _bandpass_sweep(_noise(1.6, 2), 200, 6000)
        y = x * _env(len(x), 1.1, 0.5)
    elif name == "riser":
        t = np.arange(int(3.0 * SR)) / SR
        tone = np.sin(2 * np.pi * np.cumsum(np.linspace(180, 1400, len(t))) / SR)
        y = (0.5 * _bandpass_sweep(_noise(3.0, 3), 400, 9000) + 0.35 * tone) * (t / t[-1]) ** 2.2
    elif name == "impact":
        t = np.arange(int(1.4 * SR)) / SR
        boom = np.sin(2 * np.pi * np.cumsum(45 + 70 * np.exp(-t * 18)) / SR) * np.exp(-t * 3.2)
        crack = _bandpass_sweep(_noise(1.4, 4), 2500, 800) * np.exp(-t * 14)
        y = boom + 0.5 * crack
    elif name == "subdrop":
        t = np.arange(int(1.8 * SR)) / SR
        y = np.sin(2 * np.pi * np.cumsum(70 * np.exp(-t * 1.4) + 28) / SR) * np.exp(-t * 1.6)
    elif name == "swell":
        x = _bandpass_sweep(_noise(1.8, 5), 1500, 7000)
        t = np.linspace(0, 1, len(x))
        y = x * t ** 3 * (1 - np.clip((t - 0.97) / 0.03, 0, 1))  # reverse-cymbal style, hard stop
    elif name == "tick":
        t = np.arange(int(0.06 * SR)) / SR
        y = np.sin(2 * np.pi * 2200 * t) * np.exp(-t * 90)
    elif name == "shutter":
        a = _bandpass_sweep(_noise(0.05, 6), 3000, 5000) * np.exp(-np.arange(int(0.05 * SR)) / SR * 80)
        y = np.concatenate([a, np.zeros(int(0.07 * SR)), 0.8 * a])
    elif name == "pop":  # a bubbly bloop: a quick upward glide that settles (the crew appearing)
        t = np.arange(int(0.22 * SR)) / SR
        f = 520 * (0.7 + 0.3 * (1 - np.exp(-t * 55)))
        y = (np.sin(2 * np.pi * np.cumsum(f) / SR) + 0.18 * np.sin(4 * np.pi * np.cumsum(f) / SR)) *             np.minimum(1, t / 0.003) * np.exp(-t * 22)
    elif name == "snip":  # scissors: two short metallic clicks with a brushed blade between
        n = int(0.05 * SR)
        t = np.arange(n) / SR
        click = (np.sin(2 * np.pi * 4200 * t) * 0.6 + _noise(0.05, 7) * 0.4) * np.exp(-t * 160)
        blade = _bandpass_sweep(_noise(0.09, 8), 5000, 2500) * 0.25
        y = np.concatenate([click, blade, click * 0.85])
    elif name == "boing":  # a cartoon jump: a springy rising tone with vibrato
        t = np.arange(int(0.45 * SR)) / SR
        f = 180 + 520 * (1 - np.exp(-t * 9)) + 25 * np.sin(2 * np.pi * 18 * t)
        y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.minimum(1, t / 0.005) * np.exp(-t * 5.5)
    elif name == "chime":  # a small bell, two notes a fifth apart
        t = np.arange(int(1.8 * SR)) / SR
        y = np.zeros_like(t)
        for start, base in ((0.0, 1046.5), (0.09, 1567.98)):
            k = int(start * SR)
            tt = t[: len(t) - k]
            y[k:] += sum(a * np.sin(2 * np.pi * base * r * tt) * np.exp(-tt * d)
                         for r, a, d in ((1, 1.0, 2.2), (2.76, 0.35, 4.5), (5.4, 0.12, 8)))
    else:
        raise ValueError(f"unknown sound effect {name!r}; available: {', '.join(SFX_NAMES)}")
    y = y / (np.abs(y).max() + 1e-9) * 0.5  # -6 dBFS peak; the mix sets the final level
    return np.stack([y, y], axis=1).astype(np.float32)


# The pro library: each effect made once by ElevenLabs' sound generation from a sound designer's description, kept and
# reused for every film (a few cents once). Without a key, or before it's made, the synthesized effect plays.
PRO = {
    "whoosh": ("a fast clean cinematic whoosh passing left to right, airy, crisp, no music, no voice", 0.8),
    "whoosh_long": ("a long smooth cinematic swoosh sweeping past, airy and wide, no music, no voice", 1.6),
    "riser": ("a short tense cinematic riser building up to a hit, rising pitch, no music, no voice", 1.5),
    "impact": ("a deep punchy cinematic impact hit with a short sub boom, tight, no music, no voice", 1.2),
    "subdrop": ("a deep sub bass drop, cinematic, no music, no voice", 1.5),
    "swell": ("a soft warm reverse cymbal swell rising into a reveal, no music, no voice", 1.5),
    "tick": ("a single crisp clean UI tick, very short, no music", 0.5),
    "pop": ("a soft satisfying bubbly pop, short, no music", 0.5),
    "shutter": ("a single camera shutter click, mechanical, no music", 0.5),
    "chime": ("a short bright glassy chime, magical shimmer, no music", 1.0),
}


def pro_path(name: str, library: Path) -> Path | None:
    """The generated (pro) version of an effect, made now if there's a key and it isn't kept yet (None: none)."""
    import json
    import os
    import urllib.request

    if name not in PRO:
        return None
    path = library / "pro" / f"{name}.wav"  # 48 kHz stereo, as the mix reads it
    if path.exists() and path.stat().st_size > 1000:
        return path
    from ..music.compose import _load_dotenv

    _load_dotenv()
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        return None
    text, seconds = PRO[name]
    req = urllib.request.Request("https://api.elevenlabs.io/v1/sound-generation",
                                 data=json.dumps({"text": text, "duration_seconds": seconds, "prompt_influence": 0.6}).encode(),
                                 headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            audio = r.read()
    except Exception:  # noqa: BLE001 - the synthesized effect plays instead
        return None
    if len(audio) < 1000:
        return None
    import subprocess

    from ..ffmpeg import ffmpeg_bin

    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.with_suffix(".mp3")
    raw.write_bytes(audio)
    part = path.with_name(path.stem + ".part.wav")
    done = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(raw), "-ar", str(SR), "-ac", "2",
                           "-c:a", "pcm_f32le", str(part)], capture_output=True)
    raw.unlink(missing_ok=True)
    if done.returncode != 0 or not part.exists():
        return None
    part.replace(path)
    return path


def sfx_path(name: str, library: Path, pro: bool = True) -> Path:
    """An effect's file: the pro (generated) one when there is one, else the synthesized one."""
    if pro and (made := pro_path(name, library)) is not None:
        return made
    path = library / f"{name}.wav"
    if not path.exists():
        library.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), synthesize(name), SR, subtype="FLOAT")
    return path
