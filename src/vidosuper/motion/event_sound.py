"""A film's sound effects made from what happens in it: the kit logs each event (a page folding up, a page turn, a
snip, a stamp, someone typing, the drop) with its time, and each becomes its sound here, made in code, levelled under
the music. A sound described in words (`say`) is made by the sound model when there is one (scenes.sound_for).

    render(events, seconds, out_wav, library)   the sound-effects track
    mix(picture, music, effects, out, seconds)   the finished film: music + effects, -14 LUFS, true peak -1 dB"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

SR = 48000
C_MAJ = [523.25, 587.33, 659.26, 783.99, 880.0, 1046.5, 1174.66, 1318.5, 1568.0, 1760.0, 2093.0]


class _Synth:
    """The sounds, made from noise and tones (seeded, so a film always sounds the same)."""

    def __init__(self, seed: int = 11):
        self.rng = np.random.default_rng(seed)

    @staticmethod
    def n(sec: float) -> int:
        return max(16, int(sec * SR))

    @staticmethod
    def env(n: int, a: float = 0.002, d: float = 0.2) -> np.ndarray:
        t = np.arange(n) / SR
        return np.minimum(1, t / max(a, 1e-4)) * np.exp(-t / max(d, 1e-4))

    def band(self, n: int, lo: float, hi: float) -> np.ndarray:
        f = np.fft.rfft(self.rng.standard_normal(n))
        fr = np.fft.rfftfreq(n, 1 / SR)
        f[(fr < lo) | (fr > hi)] = 0
        y = np.fft.irfft(f, n)
        return y / (np.abs(y).max() + 1e-9)

    def tone(self, freq: float, dur: float, decay: float, a: float = 0.003, glide: float | None = None, vib: float = 0.0):
        n = self.n(dur)
        t = np.arange(n) / SR
        f = np.full(n, freq) if glide is None else freq * (glide / freq) ** (t / dur)
        f = f * (1 + vib * np.sin(2 * np.pi * 6 * t))
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * self.env(n, a, decay)

    @staticmethod
    def mix(*parts) -> np.ndarray:
        n = max(len(x) for x in parts)
        return sum(np.pad(x, (0, n - len(x))) for x in parts)

    @staticmethod
    def delay(x: np.ndarray, sec: float) -> np.ndarray:
        return np.pad(x, (int(sec * SR), 0))

    # ---- the sounds
    def flap(self, big=False):
        n = self.n(0.12)
        s = self.mix(self.band(n, 900, 7000) * self.env(n, 0.001, 0.028),
                     self.band(self.n(0.06), 200, 900) * self.env(self.n(0.06), 0.001, 0.02) * 0.5)
        return self.mix(s, self.tone(110, 0.16, 0.05, glide=80) * 0.7) if big else s

    def flick(self):
        n = self.n(0.06)
        return self.mix(self.band(n, 2500, 9500) * self.env(n, 0.0005, 0.012),
                        self.delay(self.band(self.n(0.04), 1200, 5000) * self.env(self.n(0.04), 0.0005, 0.01) * 0.6, 0.03))

    def turn(self, dur=0.8):
        n = self.n(dur)
        t = np.linspace(0, 1, n)
        body = self.band(n, 400, 7500) * np.sin(np.pi * t) ** 2 * (0.65 + 0.35 * np.sin(2 * np.pi * 13 * t * dur))
        return self.mix(body * 0.8, self.delay(self.flap(True), dur * 0.82))

    def snip(self):
        c = lambda: self.band(self.n(0.014), 3000, 11000) * self.env(self.n(0.014), 0.0003, 0.004)  # noqa: E731
        return self.mix(c(), self.tone(4200, 0.09, 0.022) * 0.22, self.tone(6100, 0.07, 0.016) * 0.12, self.delay(c() * 0.8, 0.035))

    def stamp(self):
        return self.mix(self.tone(72, 0.3, 0.08, glide=46), self.band(self.n(0.07), 300, 3500) * self.env(self.n(0.07), 0.0005, 0.016) * 0.8)

    def tick(self, f=2200):
        return self.mix(self.tone(f, 0.03, 0.006), self.band(self.n(0.01), 2000, 8000) * self.env(self.n(0.01), 0.0002, 0.002) * 0.4)

    def key(self):
        f = 1500 + self.rng.random() * 900
        return self.mix(self.band(self.n(0.022), 1500, 6500) * self.env(self.n(0.022), 0.0003, 0.005), self.tone(f, 0.016, 0.003) * 0.25)

    def pop(self, f=700):
        return self.mix(self.tone(f, 0.12, 0.035, glide=f * 1.7), self.band(self.n(0.012), 1000, 6000) * self.env(self.n(0.012), 0.0003, 0.003) * 0.3)

    def pling(self, f):
        return self.mix(self.tone(f, 0.7, 0.22, a=0.002), self.tone(f * 2, 0.5, 0.12) * 0.25, self.tone(f * 3.01, 0.3, 0.06) * 0.08)

    def chime(self, freqs=(523.25, 659.26, 783.99, 1046.5), dur=1.8, decay=0.6):
        return self.mix(*[self.mix(self.tone(f, dur, decay, a=0.004), self.tone(f * 2.76, dur * 0.6, decay * 0.4) * 0.18) * (0.9 ** i)
                          for i, f in enumerate(freqs)])

    def whoosh(self, dur=0.6, lo=300, hi=6000, rise=True):
        n = self.n(dur)
        t = np.linspace(0, 1, n)
        return self.band(n, lo, hi) * (t ** 2 if rise else (1 - t) ** 2) * np.sin(np.pi * t) ** 0.5

    def swish(self, dur=0.35, lo=600, hi=7000):
        n = self.n(dur)
        t = np.linspace(0, 1, n)
        return self.band(n, lo, hi) * np.sin(np.pi * t) ** 2

    def boom(self):
        return self.mix(self.tone(54, 2.4, 0.7, a=0.004, glide=36), self.band(self.n(0.45), 40, 220) * self.env(self.n(0.45), 0.002, 0.16) * 0.8)

    def shimmer(self, dur=0.6, f0=1600, k=9):
        m = self.n(dur)
        t = np.arange(m) / SR
        s = np.zeros(m)
        for i in range(k):
            f = f0 * (1.5 ** (i % 4)) * (1 + 0.01 * i)
            s += np.sin(2 * np.pi * f * t + self.rng.random() * 6) * (0.5 + 0.5 * np.sin(2 * np.pi * (5 + i) * t + i))
        return s / k * np.minimum(1, t / (dur * 0.9)) ** 2 * np.exp(-np.maximum(0, t - dur * 0.92) / 0.05)

    def splash(self):
        n = self.n(0.7)
        t = np.arange(n) / SR
        s = self.band(n, 400, 9000) * self.env(n, 0.003, 0.18) * (0.7 + 0.3 * np.sin(2 * np.pi * 23 * t))
        for _ in range(12):
            s = self.mix(s, self.delay(self.tone(1200 + self.rng.random() * 2200, 0.05, 0.015, glide=2600 + self.rng.random() * 1500) * 0.25, self.rng.random() * 0.5))
        return s

    def spark(self):
        s = np.zeros(self.n(0.18))
        for _ in range(14):
            i = int(self.rng.random() * (len(s) - self.n(0.006)))
            s[i:i + self.n(0.006)] += self.band(self.n(0.006), 2500, 12000) * self.rng.random()
        return s

    def clack(self):
        return self.mix(self.tone(2600, 0.12, 0.025), self.tone(3900, 0.1, 0.018) * 0.6,
                        self.band(self.n(0.025), 2000, 9000) * self.env(self.n(0.025), 0.0003, 0.006), self.tone(180, 0.1, 0.03) * 0.6)

    def thump(self):
        return self.mix(self.tone(62, 0.5, 0.14, glide=40), self.band(self.n(0.14), 200, 4000) * self.env(self.n(0.14), 0.001, 0.03) * 0.9, self.flap(True) * 0.6)

    def plop(self):
        return self.mix(self.tone(330, 0.14, 0.04, glide=150), self.band(self.n(0.05), 250, 2600) * self.env(self.n(0.05), 0.0008, 0.014) * 0.5)

    def hit(self):
        return self.mix(self.boom(), self.chime((523.25, 659.26, 783.99, 1046.5, 1318.5), 3.0, 1.0) * 0.5,
                        self.band(self.n(0.6), 1500, 9000) * self.env(self.n(0.6), 0.002, 0.15) * 0.25)

    def chirp(self):
        return self.mix(self.tone(2600, 0.06, 0.03, glide=4200), self.delay(self.tone(3000, 0.05, 0.025, glide=4400) * 0.8, 0.08))

    def creak(self):
        return self.tone(620, 0.45, 0.35, a=0.04, glide=980, vib=0.03) * 0.6

    def scribble(self, dur=0.5):
        n = self.n(dur)
        t = np.arange(n) / SR
        return self.band(n, 2500, 7000) * (0.5 + 0.5 * np.sin(2 * np.pi * 14 * t)) ** 2 * np.sin(np.pi * t / dur)

    def rumble(self, dur):
        n = self.n(dur)
        return self.band(n, 30, 170) * np.linspace(0, 1, n) ** 1.5

    def step(self):
        return self.band(self.n(0.05), 120, 2400) * self.env(self.n(0.05), 0.001, 0.018)

    def hum(self, dur):
        n = self.n(dur)
        t = np.arange(n) / SR
        s = np.sin(2 * np.pi * 60 * t) * 0.5 + np.sin(2 * np.pi * 120 * t) * 0.3 + np.sin(2 * np.pi * 180 * t) * 0.12 + self.band(n, 80, 400) * 0.15
        return s * np.minimum(1, t / 0.8) * np.minimum(1, (t[-1] - t) / 0.6)


BASE_GAIN = {"flap": -25, "flick": -25, "turn": -19, "snip": -18, "stamp": -19, "tick": -30, "keys": -33, "ticks": -31,
             "steps": -34, "step": -34, "pop": -27, "pling": -25, "chime": -24, "whoosh": -24, "swish": -28, "boom": -10,
             "hit": -10, "drop": -24, "plop": -24, "shimmer": -28, "splash": -22, "spark": -30, "clack": -18, "thump": -15,
             "chirp": -34, "creak": -30, "scribble": -32, "rumble": -24, "hum": -34, "say": -18}
KINDS = frozenset(BASE_GAIN)  # the sounds a scene can ask for by name; anything else is 'say' with {sound: '...'}
UNDER = -25.0  # dB: where the effects sit under the music as a whole (The Paper Studio's sat at -25.3; rated 19/20)
CEILING = -4.0  # dB: no stretch of effects comes closer to the music than this (a pile of sounds is pulled down)
WINDOW, HOP = 0.4, 0.05  # seconds: the stretch a level is measured over, and how often
CROWD = 0.1  # seconds: more than PER_CROWD sounds of one kind this close together and the rest are left out (a pile of folds is one rustle)
PER_CROWD = 4


def _thin(events: list[dict]) -> list[dict]:
    out, recent = [], {}
    for e in sorted(events, key=lambda e: float(e.get("t", 0))):
        kind = str(e.get("type", ""))
        times = [t for t in recent.get(kind, []) if float(e["t"]) - t < CROWD]
        if len(times) >= PER_CROWD:
            continue
        recent[kind] = times + [float(e["t"])]
        out.append(e)
    return out


def sounds_for(e: dict, sy: _Synth, library: Path | None = None) -> list[tuple[float, np.ndarray]]:
    """An event's sound(s): [(at, mono)]; a range (keys, ticks, steps) is many sounds."""
    kind, t = str(e.get("type", "")), float(e.get("t", 0))
    until = float(e.get("until", t + 1.0))
    if kind == "flap":
        return [(t, sy.flap(bool(e.get("big"))))]
    if kind in ("flick", "snip", "stamp", "boom", "splash", "spark", "clack", "thump", "chirp", "creak", "step", "plop", "hit"):
        return [(t, getattr(sy, kind)())]
    if kind == "drop":  # something small dropping into place (the film's big moment is 'hit')
        return [(t, sy.plop())]
    if kind == "turn":
        return [(t, sy.turn(float(e.get("dur", 0.8))))]
    if kind == "tick":
        return [(t, sy.tick(2200 + 200 * int(e.get("pitch", 0))))]
    if kind == "pop":
        p = int(e.get("pitch", 3))
        return [(t, sy.pop(C_MAJ[p % len(C_MAJ)] * 0.6))]
    if kind == "pling":
        return [(t, sy.pling(C_MAJ[int(e.get("pitch", 4)) % len(C_MAJ)]))]
    if kind == "chime":
        return [(t, sy.chime())]
    if kind == "whoosh":
        d = float(e.get("dur", 0.6))
        return [(t, sy.whoosh(d, rise=e.get("rise", True) is not False))]
    if kind == "swish":
        return [(t, sy.swish(float(e.get("dur", 0.35))))]
    if kind == "shimmer":
        return [(t, sy.shimmer(float(e.get("dur", 0.6))))]
    if kind == "scribble":
        return [(t, sy.scribble(float(e.get("dur", 0.5))))]
    if kind in ("rumble", "hum"):
        d = max(0.2, until - t)
        return [(t, getattr(sy, kind)(d))]
    if kind in ("keys", "ticks", "steps"):
        out, k, at = [], 0, t
        every = float(e.get("every", {"keys": 0.12, "ticks": 0.25, "steps": 1 / 3}[kind]))
        while at < until:
            snd = sy.key() if kind == "keys" else sy.tick(2300 if k % 2 else 1900) if kind == "ticks" else sy.step()
            out.append((at, snd))
            k += 1
            at += every * (0.6 + sy.rng.random() * 0.8) if kind == "keys" else every
        return out
    if kind == "say" and e.get("sound") and library is not None:
        from . import scenes

        made = scenes.sound_for(str(e["sound"]), float(e.get("dur", 1.2)), library)
        if made is not None:
            import soundfile as sf

            x, sr = sf.read(str(made), always_2d=True)
            mono = x.mean(axis=1)
            if sr != SR:
                mono = np.interp(np.linspace(0, len(mono) - 1, int(len(mono) * SR / sr)), np.arange(len(mono)), mono)
            return [(t, mono / (np.abs(mono).max() + 1e-9))]
    return []


def _levels(x: np.ndarray, hops: int) -> np.ndarray:
    """dB of x over a WINDOW around every HOP."""
    h, k = int(HOP * SR), int(WINDOW * SR)
    c = np.concatenate([[0.0], np.cumsum(x.astype(np.float64) ** 2)])
    mid = np.arange(hops) * h
    a, b = np.clip(mid - k // 2, 0, len(x)), np.clip(mid + k // 2, 0, len(x))
    return 10 * np.log10((c[b] - c[a]) / np.maximum(1, b - a) + 1e-12)


def _mono_at_sr(path: Path, n: int) -> np.ndarray:
    import soundfile as sf

    x, sr = sf.read(str(path), always_2d=True)
    m = x.mean(axis=1)
    if sr != SR:
        m = np.interp(np.arange(int(len(m) * SR / sr)) * (sr / SR), np.arange(len(m)), m)
    return np.pad(m, (0, max(0, n - len(m))))[:n]


def balance(effects: np.ndarray, music: np.ndarray) -> dict:
    """Where the effects sit against the music: the difference (dB) over every stretch where effects sound, as its
    median and its loudest stretches. The film's music bar by bar decides how loud a sound may be, not a fixed level."""
    hops = len(effects) // int(HOP * SR) + 1
    ef, mu = _levels(effects, hops), _levels(music, hops)
    sounding = ef > ef.max() - 40
    d = ef[sounding] - mu[sounding]
    return {"median": round(float(np.median(d)), 1), "p95": round(float(np.percentile(d, 95)), 1),
            "max": round(float(d.max()), 1)} if len(d) else {"median": None, "p95": None, "max": None}


def under(track: np.ndarray, music: np.ndarray) -> tuple[np.ndarray, dict]:
    """The effects levelled against the music under them: as a whole they sit UNDER dB below it, and no stretch comes
    closer than CEILING (a pile of sounds is pulled down; a quiet bar still lets a small sound through)."""
    fx = track.mean(axis=1)
    h = int(HOP * SR)
    hops = len(fx) // h + 1
    ef, mu = _levels(fx, hops), _levels(music, hops)
    sounding = ef > ef.max() - 40
    if not sounding.any() or ef.max() < -90:
        return track, {"shift": 0.0, "held": 0.0}
    playing = mu > mu.max() - 45
    floor = float(np.median(mu[playing])) - 12 if playing.any() else -60.0
    mu = np.maximum(mu, floor)  # where the music is near silent, the effects are held to a quiet bar's level
    shift = min(6.0, UNDER - float(np.median(ef[sounding] - mu[sounding])))  # too quiet to hear is raised, only so far
    over = np.where(sounding, np.maximum(0, ef + shift - (mu + CEILING)), 0.0)
    r = max(1, int(WINDOW / HOP / 2))  # held across the whole loud stretch, then eased in and out
    held = np.lib.stride_tricks.sliding_window_view(np.pad(over, r, mode="edge"), 2 * r + 1).max(axis=1)
    held = np.convolve(np.pad(held, 2, mode="edge"), np.ones(5) / 5, mode="valid")
    gain = 10 ** ((shift - held) / 20)
    curve = np.interp(np.arange(len(fx)), np.arange(hops) * h, gain)
    return (track * curve[:, None]).astype(np.float32), {"shift": round(shift, 1), "held": round(float(held.max()), 1)}


def render(events: list[dict], seconds: float, out: Path, library: Path | None = None, seed: int = 11,
           music: Path | None = None) -> dict:
    """The effects track for these events (a float wav at 48 kHz, stereo), levelled under `music` when there is
    one. Returns {sounds, peak, shift, held, balance}."""
    import soundfile as sf

    sy = _Synth(seed)
    track = np.zeros((int((seconds + 2) * SR), 2), np.float32)
    laid = 0
    for e in _thin(events):
        gain = BASE_GAIN.get(str(e.get("type")), -26) + float(e.get("gain", 0))
        pan = float(np.clip(e.get("pan", 0.0), -1, 1))
        for at, mono in sounds_for(e, sy, library):
            i = int(at * SR)
            if i < 0 or i >= len(track):
                continue
            seg = mono[: len(track) - i] * 10 ** (gain / 20)
            track[i:i + len(seg), 0] += seg * min(1, 1 - pan)
            track[i:i + len(seg), 1] += seg * min(1, 1 + pan)
            laid += 1
    track = track[: int(seconds * SR)]
    moved = {"shift": 0.0, "held": 0.0}
    bed = _mono_at_sr(Path(music), len(track)) if music is not None and Path(music).exists() and len(track) else None
    if bed is not None:
        track, moved = under(track, bed)
    peak = float(np.abs(track).max()) if len(track) else 0.0
    if peak > 0.95:
        track *= 0.95 / peak
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), track, SR, subtype="FLOAT")
    return {"sounds": laid, "peak": round(min(peak, 0.95), 3), **moved,
            "balance": balance(track.mean(axis=1), bed) if bed is not None else None}


def mix(picture: Path, music: Path | None, effects: Path | None, out: Path, seconds: float, crf: int = 18) -> Path:
    """The finished film: the music and the effects together (no clipping), loudness -14 LUFS, true peak -1 dB,
    the picture encoded for posting (H.264, yuv420p, fast start)."""
    from ..ffmpeg import ffmpeg_bin

    ff = ffmpeg_bin()
    inputs = [x for x in (music, effects) if x is not None and Path(x).exists()]
    raw = out.with_name(out.stem + ".mix.wav")
    if inputs:
        args = [ff, "-y", "-loglevel", "error"]
        for x in inputs:
            args += ["-i", str(x)]
        chains = "".join(f"[{k}:a]aresample=48000,atrim=0:{seconds:.3f},apad=whole_dur={seconds:.3f}[a{k}];" for k in range(len(inputs)))
        labels = "".join(f"[a{k}]" for k in range(len(inputs)))
        args += ["-filter_complex", chains + f"{labels}amix=inputs={len(inputs)}:normalize=0:duration=longest,"
                 f"alimiter=limit=0.9:level=false[o]", "-map", "[o]", "-c:a", "pcm_f32le", str(raw)]
        done = subprocess.run(args, capture_output=True)
        if done.returncode != 0:
            raise RuntimeError("the sound couldn't be mixed: " + done.stderr.decode(errors="replace")[-600:])
        probe = subprocess.run([ff, "-i", str(raw), "-af", "loudnorm=I=-14:TP=-1.0:LRA=11:print_format=json", "-f", "null", "-"],
                               capture_output=True)
        text = probe.stderr.decode(errors="replace")
        try:
            js = json.loads(text[text.rindex("{"):text.rindex("}") + 1])
            norm = (f"loudnorm=I=-14:TP=-1.0:LRA=11:measured_I={js['input_i']}:measured_TP={js['input_tp']}:"
                    f"measured_LRA={js['input_lra']}:measured_thresh={js['input_thresh']}:offset={js['target_offset']}:linear=true")
        except (ValueError, KeyError):
            norm = "loudnorm=I=-14:TP=-1.0:LRA=11"
        args = [ff, "-y", "-loglevel", "error", "-i", str(picture), "-i", str(raw), "-map", "0:v", "-map", "1:a",
                "-af", norm + ",aresample=48000", "-c:v", "libx264", "-crf", str(crf), "-preset", "slow", "-pix_fmt",
                "yuv420p", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", "-shortest", str(out)]
    else:
        args = [ff, "-y", "-loglevel", "error", "-i", str(picture), "-c:v", "libx264", "-crf", str(crf), "-preset", "slow",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    done = subprocess.run(args, capture_output=True)
    raw.unlink(missing_ok=True)
    if done.returncode != 0:
        raise RuntimeError("the film couldn't be finished: " + done.stderr.decode(errors="replace")[-600:])
    return out
