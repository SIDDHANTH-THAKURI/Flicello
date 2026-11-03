"""A made track measured bar by bar, and cut again on its bar lines so a film's big moments land where its story needs
them.

A music model is told where the drop goes and puts it somewhere else (ElevenLabs ignores the planned timings; a local
model drifts too). So the track is measured as it came out: every bar's loudness and how much of it is drums, the
bars where the drums come in (the drops), the runs of quiet, soft, full and peak bars. A director then picks which
bars to keep and in what order (`arrange`): the pieces are joined on bar lines with short equal-power crossfades, so
the beat never skips, and the film is timed to the music as it now is.

`motion_data` gives a film page what it needs to move with the music: per-frame loudness and kick, the beats and
the bar lines."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

SR = 22050


class ArrangeError(ValueError):
    """Said plainly to the director who asked for the cut."""


@dataclass
class Bar:
    k: int
    start: float
    end: float
    level: float  # loudness, 0..1 (the loudest bar is 1, 30 dB under it is 0)
    drums: float  # how much of it is drums, 0..1 (the most percussive bar is 1)
    label: str  # quiet | soft | full | peak


def _label(level: float, drums: float) -> str:
    if level < 0.35:
        return "quiet"
    if drums < 0.45:
        return "soft"
    if level >= 0.82 and drums >= 0.6:
        return "peak"
    return "full"


def measure(path: str | Path) -> dict:
    """The track bar by bar: {duration, bpm, beat, offset, beats, bars: [Bar...], drops: [bar k...], lifts: [bar
    k...], sections: [(label, k_from, k_to)...]}. Bars run from the first bar line to the end (a pickup before it is bar -1)."""
    import librosa

    from .analysis import analyze_music

    path = Path(path)
    y, sr = librosa.load(str(path), sr=SR, mono=True)
    duration = len(y) / sr
    grid = _grid_of(path)
    if grid is not None:  # a track cut on its bars keeps its beat exactly: measured again, it could lock to half time
        beat = float(grid["beat"])
        starts = [float(s) for s in grid["bars"] if s < duration - 0.5 * beat]
        first = float(grid["offset"])
        beats = [first + beat * k for k in range(int((duration - first) / beat) + 1)]
    else:
        a = analyze_music(path)
        beats = [float(b) for b in a.beats]
        if len(beats) < 8:
            raise ArrangeError("there's no steady beat in this track to cut it on")
        beat = float(np.median(np.diff(beats)))
        starts = [float(d) for d in a.downbeats if d < duration - 0.5 * beat]
        if len(starts) < 2:
            starts = [beats[0] + 4 * beat * k for k in range(int((duration - beats[0]) / (4 * beat)) + 1)]
    _, perc = librosa.effects.hpss(y)
    raw = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else duration
        i, j = int(s * sr), max(int(s * sr) + 1, int(e * sr))
        seg, ps = y[i:j], perc[i:j]
        energy = float(np.mean(seg ** 2)) + 1e-12
        raw.append((s, e, 10 * np.log10(energy), float(np.mean(ps ** 2)) / energy))
    loudest = max(r[2] for r in raw)
    most = max(r[3] for r in raw) or 1.0
    bars = []
    for k, (s, e, db, ratio) in enumerate(raw):
        level = float(np.clip((db - (loudest - 30.0)) / 30.0, 0, 1))
        drums = float(np.clip(ratio / most, 0, 1))
        bars.append(Bar(k, round(s, 3), round(e, 3), round(level, 3), round(drums, 3), _label(level, drums)))
    drops, lifts = [], []  # the drums come in; the music gets bigger without them
    for k in range(2, len(bars)):
        before_drums = (bars[k - 1].drums + bars[k - 2].drums) / 2
        before_level = (bars[k - 1].level + bars[k - 2].level) / 2
        if before_drums < 0.45 and bars[k].drums >= 0.6 and bars[k].level >= before_level - 0.02:
            if not drops or drops[-1] != k - 1:  # the bar after a drop is the same drop
                drops.append(k)
        elif bars[k].level - before_level >= 0.1 and bars[k].drums < 0.45:
            lifts.append(k)
    sections = []
    for b in bars:
        if sections and sections[-1][0] == b.label:
            sections[-1][2] = b.k
        else:
            sections.append([b.label, b.k, b.k])
    merged = []  # a single bar of another kind inside a run is part of the run
    for s in sections:
        if merged and s[1] == s[2] and len(merged) >= 1 and merged[-1][2] - merged[-1][1] >= 1:
            merged[-1][2] = s[2]
        elif merged and merged[-1][0] == s[0]:
            merged[-1][2] = s[2]
        else:
            merged.append(s)
    return {"duration": round(duration, 3), "bpm": round(60.0 / beat, 2), "beat": round(beat, 4),
            "offset": round(beats[0], 4), "beats": [round(b, 3) for b in beats], "bars": bars,
            "drops": drops, "lifts": lifts, "sections": [tuple(s) for s in merged]}


def describe(m: dict) -> str:
    """The measured track in words a director reads: tempo, the sections with their bars and times, the drops,
    and every bar."""
    lines = [f"{m['duration']:.2f} s, {m['bpm']:.1f} BPM (a beat is {m['beat']:.4f} s, a bar {4 * m['beat']:.3f} s; "
             f"the first beat at {m['offset']:.3f} s), {len(m['bars'])} bars."]
    lines.append("Sections (bars from-to, inclusive):")
    for label, a, b in m["sections"]:
        lines.append(f"  {label:5s} bars {a}-{b}  {m['bars'][a].start:.2f}-{m['bars'][b].end:.2f} s")
    if m["drops"]:
        lines.append("Drops (the drums come in): " + ", ".join(f"bar {k} at {m['bars'][k].start:.2f} s" for k in m["drops"]))
    else:
        lines.append("No drop: the drums never come in sharply.")
    if m.get("lifts"):
        lines.append("Lifts (bigger, no drums yet): " + ", ".join(f"bar {k} at {m['bars'][k].start:.2f} s" for k in m["lifts"]))
    lines.append("Every bar: k start level drums label")
    for b in m["bars"]:
        lines.append(f"  {b.k:3d} {b.start:7.2f} {b.level:4.2f} {b.drums:4.2f} {b.label}")
    return "\n".join(lines)


def arrange(path: str | Path, ranges: list[tuple[int, int]], out: str | Path, m: dict | None = None,
            xfade: float = 0.03, tail: float = 0.8) -> Path:
    """A new track made of these bar ranges, in this order (each [from, to] inclusive, as `measure` numbers them),
    joined on the bar lines with equal-power crossfades. When the last piece stops before the track's own end it
    fades out over `tail` seconds; when it runs to the end, the track's own ending rings out."""
    import soundfile as sf

    path, out = Path(path), Path(out)
    m = m or measure(path)
    bars = m["bars"]
    if not ranges:
        raise ArrangeError("say which bars to keep, e.g. [[0, 15], [32, 47]]")
    audio, sr = sf.read(str(path), always_2d=True)
    pieces = []
    for pair in ranges:
        try:
            a, b = int(pair[0]), int(pair[1])
        except (TypeError, ValueError, IndexError):
            raise ArrangeError(f"{pair!r} isn't a bar range like [0, 15]") from None
        if not (0 <= a <= b < len(bars)):
            raise ArrangeError(f"bars {a}-{b}: the track has bars 0-{len(bars) - 1}")
        i = 0 if a == 0 else int(round(bars[a].start * sr))  # bar 0 keeps what comes before it (a pickup, a breath)
        j = int(round(bars[b].end * sr)) if b + 1 < len(bars) else len(audio)
        pieces.append((audio[i:j].copy(), b + 1 >= len(bars), a, b, i / sr))
    n = max(1, int(xfade * sr))
    fade_in, fade_out = np.sin(np.linspace(0, np.pi / 2, n))[:, None], np.cos(np.linspace(0, np.pi / 2, n))[:, None]
    track = pieces[0][0]
    new_bars, at = [], 0.0  # the beat grid of the new track: each piece's bar lines where they now fall
    for idx, (piece, _, a, b, src) in enumerate(pieces):
        if idx:
            joined = len(track) >= n and len(piece) >= n
            at = (len(track) - (n if joined else 0)) / sr
            track = (np.concatenate([track[:-n], track[-n:] * fade_out + piece[:n] * fade_in, piece[n:]]) if joined
                     else np.concatenate([track, piece]))
        new_bars += [round(at + bars[k].start - src, 4) for k in range(a, b + 1)]
    if not pieces[-1][1]:
        k = min(len(track), int(tail * sr))
        track[-k:] *= np.linspace(1, 0, k)[:, None] ** 2
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), track.astype(np.float32), sr)
    beat = float(m["beat"])
    first = new_bars[0] - beat * int(new_bars[0] / beat + 1e-6)  # the earliest beat on the grid
    _grid_path(out).write_text(json.dumps({"beat": beat, "offset": round(first, 4), "bars": new_bars,
                                           "ranges": [[p[2], p[3]] for p in pieces], "source": path.name,
                                           "made": out.stat().st_mtime}), encoding="utf-8")
    return out


def _grid_path(track: Path) -> Path:
    return track.with_name(track.name + ".grid.json")


def _grid_of(track: Path) -> dict | None:
    """The beat grid a cut track was made with (None for a track as it was made, or one written over since)."""
    p = _grid_path(track)
    if not p.exists():
        return None
    try:
        g = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return g if abs(float(g.get("made", 0)) - track.stat().st_mtime) < 2 else None


def motion_data(path: str | Path, fps: int = 30, m: dict | None = None) -> dict:
    """What a film page moves with: per-frame loudness (env) and kick (0..1), the beats, the bar lines, the beat
    length and the first beat (the kit's B(k) and BAR(k))."""
    import librosa

    path = Path(path)
    m = m or measure(path)
    y, sr = librosa.load(str(path), sr=SR, mono=True)
    hop = int(round(sr / fps))
    rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop)[0]
    env = rms / (np.percentile(rms, 98) + 1e-9)
    env = np.clip(np.convolve(env, np.ones(3) / 3, mode="same"), 0, 1)
    low = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, fmax=200, n_mels=24)
    low = low / (np.percentile(low, 99) + 1e-9)
    kick = np.zeros_like(low)
    level = 0.0
    for i, v in enumerate(np.clip(low, 0, 1)):  # a hit, then a short decay: something to pulse with
        level = max(float(v), level * 0.72)
        kick[i] = level
    return {"env": [round(float(v), 3) for v in env], "kick": [round(float(v), 3) for v in kick],
            "beats": m["beats"], "bars": [b.start for b in m["bars"]], "period": m["beat"], "offset": m["offset"],
            "drop": m["bars"][m["drops"][0]].start if m["drops"] else None, "fps": fps}


def bars_json(m: dict) -> list[dict]:
    return [asdict(b) for b in m["bars"]]
