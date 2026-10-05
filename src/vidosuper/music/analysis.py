"""Music structure for editing: tempo, beats, bar starts, energy over time, sections and drops."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field
from scipy.ndimage import uniform_filter1d
from scipy.signal import find_peaks

MUSIC_VERSION = 2  # 2: beat clarity (pulse)
SR = 22050
HOP = 512
PULSE_CHUNK = 8.0


class MusicSection(BaseModel):
    start: float
    end: float
    energy: float  # 0-1, relative to the loudest part of the track
    label: str  # intro / build / peak / break / outro (heuristic)


class MusicAnalysis(BaseModel):
    path: str
    fingerprint: str
    version: int = MUSIC_VERSION
    duration: float
    tempo: float
    beats: list[float]
    downbeats: list[float]  # bar starts (every 4th beat, phase estimated)
    energy: list[float] = Field(description="0-1 loudness/intensity per second")
    sections: list[MusicSection]
    drops: list[float] = Field(default_factory=list, description="Bar starts where intensity jumps up sharply")
    pulse: list[float] = Field(default_factory=list, description=(
        f"Beat clarity per {PULSE_CHUNK:.0f} s: onset strength on the beats / on the half-beats (1.4+ clear, "
        "~1 or less: no audible beat on the grid there)"))

    def beats_between(self, start: float, end: float) -> list[float]:
        return [b for b in self.beats if start <= b <= end]

    def summary(self) -> str:
        secs = ", ".join(f"{s.label} {s.start:.1f}-{s.end:.1f}s (energy {s.energy:.2f})" for s in self.sections)
        drops = ", ".join(f"{d:.1f}s" for d in self.drops) or "none"
        weak = self.weak_beat_stretches()
        weak_txt = (" No audible beat on the grid (cut free there, not on the beat): "
                    + ", ".join(f"{a:.0f}-{b:.0f}s" for a, b in weak) + ".") if weak else ""
        return (f"{Path(self.path).name}: {self.duration:.1f}s, {self.tempo:.0f} BPM "
                f"(beat every {60 / self.tempo:.3f}s, bar every {240 / self.tempo:.2f}s), "
                f"{len(self.beats)} beats. Sections: {secs}. Drops: {drops}.{weak_txt}")

    def weak_beat_stretches(self, below: float = 1.15) -> list[tuple[float, float]]:
        """Track time ranges where the beat grid has no clear accent (merged PULSE_CHUNK pieces)."""
        out: list[tuple[float, float]] = []
        for k, d in enumerate(self.pulse):
            if d < below:
                a, b = k * PULSE_CHUNK, min(self.duration, (k + 1) * PULSE_CHUNK)
                if out and abs(out[-1][1] - a) < 1e-6:
                    out[-1] = (out[-1][0], b)
                else:
                    out.append((a, b))
        return out


def fingerprint_file(path: Path) -> str:
    stat = path.stat()
    return hashlib.sha1(f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:16]


def _downbeat_phase(beat_frames: np.ndarray, chroma: np.ndarray, low_env: np.ndarray) -> int:
    """Which of the 4 beat phases starts the bar. Chords usually change on the bar start and kick
    drums favour it, so score each phase by harmonic change + low-frequency accent."""
    if len(beat_frames) < 8:
        return 0
    bounds = np.concatenate([beat_frames, [chroma.shape[1]]])
    sync = np.stack([chroma[:, a:max(a + 1, b)].mean(axis=1) for a, b in zip(bounds[:-1], bounds[1:])])
    sync /= np.linalg.norm(sync, axis=1, keepdims=True) + 1e-9
    change = np.concatenate([[0.0], 1.0 - (sync[1:] * sync[:-1]).sum(axis=1)])
    change /= change.max() + 1e-9
    accent = low_env[np.minimum(beat_frames, len(low_env) - 1)]
    scores = [float(change[p::4].mean() + 0.5 * accent[p::4].mean()) for p in range(4)]
    return int(np.argmax(scores))


def _grid_score(onset: np.ndarray, periods: np.ndarray, phases: np.ndarray, duration: float) -> np.ndarray:
    """Mean onset strength under each (period, phase) grid; onset is sampled at HOP/SR resolution."""
    fps = SR / HOP
    k = np.arange(int(duration / periods.min()) + 2)
    scores = np.empty((len(periods), len(phases)))
    for i, period in enumerate(periods):
        grid = phases[:, None] + period * k[None, :]
        valid = grid < duration
        idx = np.minimum(np.round(grid * fps).astype(int), len(onset) - 1)
        scores[i] = (onset[idx] * valid).sum(axis=1) / np.maximum(valid.sum(axis=1), 1)
    return scores


def _beat_grid(beats: np.ndarray, duration: float, onset: np.ndarray) -> np.ndarray:
    """Steady-tempo songs: a regular grid over the whole track, fitted to the actual drum hits.

    librosa's tracker skips quiet intros and its tempo can be ~1% off, which drifts by half a beat
    over a minute; searching period and phase against the onset envelope fixes both.
    """
    if len(beats) < 8:
        return beats
    ibi = np.diff(beats)
    period = float(np.median(ibi))
    if np.std(ibi) / period > 0.08:  # tempo drifts (live music): keep detections, just extend the ends
        before = np.arange(beats[0] - period, -1e-6, -period)[::-1]
        after = np.arange(beats[-1] + period, duration, period)
        return np.concatenate([before, beats, after])
    # tolerate one frame of quantisation when scoring
    spread = np.maximum(onset, np.maximum(np.roll(onset, 1), np.roll(onset, -1)))
    periods = period * (1 + np.linspace(-0.02, 0.02, 161))
    phases = np.linspace(0, period, 48, endpoint=False)
    scores = _grid_score(spread, periods, phases, duration)
    i, j = np.unravel_index(int(np.argmax(scores)), scores.shape)
    best_period = float(periods[i])
    fine_phases = phases[j] + np.linspace(-period / 48, period / 48, 25)
    fine = _grid_score(spread, np.array([best_period]), fine_phases, duration)[0]
    phase = float(fine_phases[int(np.argmax(fine))]) % best_period
    return phase + best_period * np.arange(int((duration - phase) / best_period) + 1)


def _label_sections(bounds: list[float], energy: np.ndarray, duration: float) -> list[MusicSection]:
    sections = []
    for i, (start, end) in enumerate(zip(bounds[:-1], bounds[1:])):
        e = float(energy[int(start):max(int(start) + 1, int(end))].mean())
        sections.append(MusicSection(start=round(start, 2), end=round(min(end, duration), 2), energy=round(e, 2), label=""))
    for i, sec in enumerate(sections):
        prev_e = sections[i - 1].energy if i else None
        if sec.energy >= 0.7:
            sec.label = "peak"
        elif i == 0:
            sec.label = "intro"
        elif i == len(sections) - 1 and prev_e is not None and sec.energy < prev_e:
            sec.label = "outro"
        elif prev_e is not None and sec.energy < prev_e - 0.15:
            sec.label = "break"
        else:
            sec.label = "build"
    return sections


def _align_to_attacks(y: np.ndarray, sr: int, beats: np.ndarray) -> np.ndarray:
    """Shift the grid onto the actual attacks: onset envelopes peak a frame or two after a drum hit."""
    import librosa

    hop = 64  # ~3 ms
    rms = librosa.feature.rms(y=y, frame_length=256, hop_length=hop)[0]
    rise = np.diff(rms, prepend=rms[0])
    offsets, strengths = [], []
    for b in beats:
        lo, hi = int((b - 0.09) * sr / hop), int((b + 0.03) * sr / hop)
        if lo < 0 or hi >= len(rise):
            continue
        k = lo + int(np.argmax(rise[lo:hi]))
        offsets.append(k * hop / sr - b)
        strengths.append(rise[k])
    if len(offsets) < 8:
        return beats
    offsets, strengths = np.array(offsets), np.array(strengths)
    strong = strengths >= np.percentile(strengths, 50)  # only clear hits vote
    return beats + float(np.median(offsets[strong]))


def analyze_music(path: str | Path) -> MusicAnalysis:
    import librosa

    path = Path(path)
    y, sr = librosa.load(str(path), sr=SR, mono=True)
    duration = len(y) / sr
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=HOP)
    detected = librosa.frames_to_time(np.asarray(beat_frames, dtype=int), sr=sr, hop_length=HOP)
    beats = _beat_grid(detected, duration, onset_env / (onset_env.max() + 1e-9))
    beats = _align_to_attacks(y, sr, beats)
    beats = beats[(beats >= 0) & (beats < duration)]
    if len(beats) >= 2:
        tempo = 60.0 / float(np.median(np.diff(beats)))
    tempo = float(np.atleast_1d(tempo)[0])

    # bar starts: harmonic change + kick accents, scored on the full beat grid
    spec = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    low_env = spec[freqs < 150].mean(axis=0)
    low_env = low_env / (low_env.max() + 1e-9)
    chroma = librosa.feature.chroma_stft(S=spec ** 2, sr=sr, hop_length=HOP)
    grid_frames = librosa.time_to_frames(beats, sr=sr, hop_length=HOP)
    phase = _downbeat_phase(np.asarray(grid_frames, dtype=int), chroma, low_env)
    downbeats = beats[phase::4]
    onset_norm = onset_env / (onset_env.max() + 1e-9)

    # intensity per second: loudness (RMS) blended with rhythmic activity (onset density)
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    frames_per_sec = sr / HOP
    seconds = max(1, int(np.ceil(duration)))
    per_sec = np.array([rms[int(s * frames_per_sec):int((s + 1) * frames_per_sec)].mean() if
                        int(s * frames_per_sec) < len(rms) else 0.0 for s in range(seconds)])
    act = np.array([onset_norm[int(s * frames_per_sec):int((s + 1) * frames_per_sec)].mean() if
                    int(s * frames_per_sec) < len(onset_norm) else 0.0 for s in range(seconds)])
    raw = 0.7 * per_sec / (np.percentile(per_sec, 95) + 1e-9) + 0.3 * act / (np.percentile(act, 95) + 1e-9)
    energy = np.clip(uniform_filter1d(raw, size=3, mode="nearest"), 0, 1)

    # section boundaries: big changes in intensity, measured bar against bar
    bars = downbeats if len(downbeats) >= 4 else beats[::4]
    bar_energy = np.array([energy[int(b):max(int(b) + 1, int(b + 240 / max(tempo, 1)))].mean() for b in bars]) \
        if len(bars) else np.zeros(0)
    bounds = [0.0]
    drops: list[float] = []
    if len(bar_energy) >= 8:
        w = 2
        novelty = np.zeros(len(bar_energy))
        for i in range(w, len(bar_energy) - w + 1):
            novelty[i] = bar_energy[i:i + w].mean() - bar_energy[i - w:i].mean()
        peaks, _ = find_peaks(np.abs(novelty), height=0.12, distance=4)
        for p in peaks:
            bounds.append(float(bars[p]))
            if novelty[p] > 0.2:
                drops.append(round(float(bars[p]), 2))
    bounds.append(duration)
    sections = _label_sections(sorted(set(bounds)), energy, duration)

    return MusicAnalysis(
        path=str(path), fingerprint=fingerprint_file(path), duration=round(duration, 3), tempo=round(tempo, 2),
        beats=[round(float(b), 3) for b in beats], downbeats=[round(float(b), 3) for b in downbeats],
        energy=[round(float(e), 3) for e in energy], sections=sections, drops=drops,
        pulse=beat_dominance(onset_env, beats, duration),
    )


def beat_dominance(onset_env: np.ndarray, beats: np.ndarray, duration: float) -> list[float]:
    """Per PULSE_CHUNK seconds: onset strength on the beats over onset strength on the half-beats.
    ~1 or less means no audible beat on the grid (pads, or accents on the off-beat), so cuts placed
    "on the beat" there don't feel on the music; 1.4+ is a clear beat. Onsets may sit up to ~35 ms
    before the grid (attack vs perceived beat)."""
    if len(beats) < 4:
        return []
    times = np.arange(len(onset_env)) * HOP / SR
    half = float(np.median(np.diff(beats))) / 2

    def strength(points: np.ndarray) -> float:
        idx = np.searchsorted(times, points).clip(0, len(onset_env) - 1)
        lo = max(1, int(round(0.035 * SR / HOP)))
        return float(np.mean([onset_env[max(0, i - lo): i + 2].max() for i in idx]))

    out = []
    for c0 in np.arange(0.0, duration, PULSE_CHUNK):
        b = beats[(beats >= c0) & (beats < c0 + PULSE_CHUNK)]
        out.append(round(strength(b) / max(strength(b + half), 1e-9), 2) if len(b) >= 4 else 1.0)
    return out


def pulse_score(analysis: "MusicAnalysis", start: float, length: float) -> float:
    """0-1 for the stretch an edit would use: mostly the average beat clarity, partly its weakest chunk."""
    if not analysis.pulse:
        return 0.5  # analysed before this existed: neutral
    first, last = int(start // PULSE_CHUNK), int((start + length) // PULSE_CHUNK)
    chunk = [min(1.0, max(0.0, (d - 1.0) / 0.5)) for d in analysis.pulse[first:last + 1]]
    return 0.7 * float(np.mean(chunk)) + 0.3 * min(chunk) if chunk else 0.5


def load_or_analyze(path: str | Path, cache_dir: Path) -> MusicAnalysis:
    path = Path(path)
    cache = cache_dir / f"{fingerprint_file(path)}.analysis.json"
    if cache.exists():
        cached = MusicAnalysis.model_validate_json(cache.read_text(encoding="utf-8"))
        if cached.version == MUSIC_VERSION:
            return cached
    result = analyze_music(path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(result.model_dump_json(indent=1), encoding="utf-8")
    return result
