"""Music structure for editing: tempo, beats, bar starts, energy over time, sections and drops."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field
from scipy.ndimage import uniform_filter1d
from scipy.signal import find_peaks

MUSIC_VERSION = 8  # 8: booms, and bars phased on a clear kick/808; 7: strikes; 2: beat clarity (pulse); 3: kick-band clarity too; 4: grid on the kick drum; 6: beat level re-checked;
#                   5: drops on the beat where the kick comes in, bars phased from the main drop
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
    strikes: list[float] = Field(default_factory=list, description=(
        "Seconds of the clear hits (kick, snare, claps, stabs), each on the beat grid's sixteenth notes: what a cut "
        "can land on. Wider than the beats: dance tracks hit about 2-3 times a second."))
    booms: list[float] = Field(default_factory=list, description=(
        "Seconds of the beats with a heavy low hit (the kick or the 808): the hits a beat-synced cut lands on"))
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
        """Track time ranges where the beat grid has no clear accent (merged PULSE_CHUNK pieces). A piece with heavy
        hits on its beats (an 808 every bar) has a clear beat, however loud the hi-hats between them are."""
        out: list[tuple[float, float]] = []
        for k, d in enumerate(self.pulse):
            a, b = k * PULSE_CHUNK, min(self.duration, (k + 1) * PULSE_CHUNK)
            if d < below and sum(1 for t in self.booms if a <= t < b) < 2:
                if out and abs(out[-1][1] - a) < 1e-6:
                    out[-1] = (out[-1][0], b)
                else:
                    out.append((a, b))
        return out


def fingerprint_file(path: Path) -> str:
    stat = path.stat()
    return hashlib.sha1(f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:16]


BOOM_CLEAR = 2.0  # one beat of the bar this much heavier in the low end than any other: that beat starts the bar


def _boom_levels(beat_frames: np.ndarray, low_lin: np.ndarray) -> np.ndarray:
    """How hard the low end (kick, 808) hits on each beat: the peak just after it."""
    return np.array([low_lin[max(0, f - 1):f + 6].max() if f < len(low_lin) else 0.0 for f in beat_frames])


def _clear_boom_phase(levels: np.ndarray) -> int | None:
    """The bar phase when the kick or 808 clearly marks one beat of four (trap, phonk, most dance music), else None
    (a pop kick on 1 and 3 is not clear: the chords decide)."""
    if len(levels) < 8:
        return None
    means = np.array([levels[p::4].mean() for p in range(4)])
    order = np.argsort(means)
    if means[order[-1]] <= 0 or means[order[-1]] < BOOM_CLEAR * max(means[order[-2]], 1e-9):
        return None
    return int(order[-1])


def _downbeat_phase(beat_frames: np.ndarray, chroma: np.ndarray, low_env: np.ndarray,
                    booms: np.ndarray | None = None) -> int:
    """Which of the 4 beat phases starts the bar. A kick or 808 that clearly marks one beat decides (pads changing
    chord a beat early once put every cut of a phonk film one beat before its 808s); otherwise chords usually change
    on the bar start and kick drums favour it, so score each phase by harmonic change + low-frequency accent."""
    if len(beat_frames) < 8:
        return 0
    if booms is not None and (clear := _clear_boom_phase(booms)) is not None:
        return clear
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


def _refine_drop(t: float, beats: np.ndarray, level: np.ndarray) -> float:
    """The beat a drop really lands on: within a bar either side of the bar start found, the first beat
    whose low-band level already matches what follows (the kick comes in) right after a quieter one."""
    i0 = int(np.argmin(np.abs(beats - t)))
    for i in range(max(1, i0 - 4), min(len(beats) - 8, i0 + 5)):
        after = float(np.median(level[i:i + 8]))
        if level[i] >= after - 3 and level[i - 1] < after - 6:
            return float(beats[i])
    return t


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


def _best_period(onset: np.ndarray, duration: float, period: float) -> float:
    """The beat period whose grid the onsets follow best, among the detected one and its usual mistakes
    (4/3, 3/4, 2, 1/2, 3/2, 2/3 of it). Another level wins only when its grid clearly hits the drums
    (a real pulse, at least twice the average onset) and clearly beats the tracker's own; tempos between
    90 and 140 BPM get a small edge, as in most songs. Music without a beat keeps what it had."""
    spread = np.maximum(onset, np.maximum(np.roll(onset, 1), np.roll(onset, -1)))
    mean = float(onset.mean()) + 1e-9

    def contrast(p: float) -> float:  # fine-searched: a candidate a hair off would drift over the song
        periods = p * (1 + np.linspace(-0.02, 0.02, 41))
        phases = np.linspace(0, p, 32, endpoint=False)
        return float(_grid_score(spread, periods, phases, duration).max()) / mean

    own = contrast(period)
    best, best_score = period, own * (1.12 if 90 <= 60 / period <= 140 else 1.0)
    for ratio in (4 / 3, 3 / 4, 2.0, 0.5, 1.5, 2 / 3):
        p = period * ratio
        if not 60 / 180 <= p <= 1.0:
            continue
        raw = contrast(p)
        score = raw * (1.12 if 90 <= 60 / p <= 140 else 1.0)
        if raw >= 2.0 and raw >= 1.25 * own and score > best_score:
            best, best_score = p, score
    return best


def _kick_on_the_beat(beats: np.ndarray, kick_env: np.ndarray, duration: float,
                      onset: np.ndarray | None = None) -> np.ndarray:
    """Dance music puts loud open hi-hats on the off-beats, and the grid fit can lock onto them. The
    beat is where the kick drum is: if the kick band hits clearly harder half a beat later than on the
    grid, move the grid there. When the kick can't tell (a busy low end), the song's biggest hits - the
    opening hit, the drop, the crashes - land on beats: follow them."""
    if len(beats) < 8:
        return beats
    half = float(np.median(np.diff(beats))) / 2
    on, off = _strength_at(kick_env, beats), _strength_at(kick_env, beats + half)
    shift = off > 1.3 * on
    if not shift and off > on / 1.3 and onset is not None:
        shift = _accents_off_grid(beats, onset)
    if not shift:
        return beats
    shifted = np.concatenate([beats - half, [beats[-1] + half]])
    return shifted[(shifted >= 0) & (shifted < duration)]


def _accents_off_grid(beats: np.ndarray, onset: np.ndarray, top: int = 8) -> bool:
    """True when the strongest onsets (at least a second apart) sit nearer the half-beats than the beats."""
    times = np.arange(len(onset)) * HOP / SR
    order = np.argsort(onset)[::-1]
    picked: list[float] = []
    for i in order:
        t = float(times[i])
        if all(abs(t - u) >= 1.0 for u in picked):
            picked.append(t)
        if len(picked) == top:
            break
    period = float(np.median(np.diff(beats)))
    phase = np.array([((t - beats[0]) / period) % 1.0 for t in picked])
    on_beat = np.minimum(phase, 1 - phase)  # 0 = on a beat, 0.5 = on a half-beat
    return float(np.mean(on_beat)) > 0.32


def _strength_at(onset_env: np.ndarray, points: np.ndarray) -> float:
    """Mean onset strength at these times; onsets may sit up to ~35 ms before the grid (attack vs
    perceived beat)."""
    times = np.arange(len(onset_env)) * HOP / SR
    idx = np.searchsorted(times, points).clip(0, len(onset_env) - 1)
    lo = max(1, int(round(0.035 * SR / HOP)))
    return float(np.mean([onset_env[max(0, i - lo): i + 2].max() for i in idx])) if len(idx) else 0.0


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
    if len(detected) >= 8:  # the tracker can lock onto a 3-against-4 or a double/half pulse: re-check the level
        period0 = float(np.median(np.diff(detected)))
        period = _best_period(onset_env / (onset_env.max() + 1e-9), duration, period0)
        if abs(period - period0) / period0 > 0.05:
            detected = np.arange(detected[0] % period, duration, period)
    beats = _beat_grid(detected, duration, onset_env / (onset_env.max() + 1e-9))
    # the kick drum alone: low band of the percussive part (bass lines and pads removed)
    low_band = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP, fmax=200, n_mels=24)
    kick_env = librosa.onset.onset_strength(y=librosa.effects.percussive(y, margin=2.0), sr=sr, hop_length=HOP,
                                            fmax=200, n_mels=24)
    beats = _kick_on_the_beat(beats, kick_env, duration, onset_env)
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
    boom_level = _boom_levels(np.asarray(grid_frames, dtype=int), spec[freqs < 150].sum(axis=0))
    boom_phase = _clear_boom_phase(boom_level)
    phase = _downbeat_phase(np.asarray(grid_frames, dtype=int), chroma, low_env, boom_level)
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
    # low-band level on each beat (dB), to put each drop on the beat where the kick comes in
    low_db = 20 * np.log10(spec[freqs < 150].sum(axis=0) + 1e-9)
    frames = np.asarray(grid_frames, dtype=int)
    beat_level = np.array([low_db[max(0, f - 1):f + 6].max() if f < len(low_db) else -120.0 for f in frames])
    if len(bar_energy) >= 8:
        w = 2
        novelty = np.zeros(len(bar_energy))
        for i in range(w, len(bar_energy) - w + 1):
            novelty[i] = bar_energy[i:i + w].mean() - bar_energy[i - w:i].mean()
        peaks, _ = find_peaks(np.abs(novelty), height=0.12, distance=4)
        biggest: tuple[float, float | None] = (0.0, None)
        for p in peaks:
            t = float(bars[p])
            if novelty[p] > 0.2:
                t = _refine_drop(t, beats, beat_level)
                drops.append(round(t, 2))
                if novelty[p] > biggest[0]:
                    biggest = (float(novelty[p]), t)
            bounds.append(t)
        if biggest[1] is not None and boom_phase is None:  # drops land on bar starts: the main drop sets the phase
            downbeats = beats[int(np.argmin(np.abs(beats - biggest[1]))) % 4::4]
    bounds.append(duration)
    sections = _label_sections(sorted(set(bounds)), energy, duration)

    return MusicAnalysis(
        path=str(path), fingerprint=fingerprint_file(path), duration=round(duration, 3), tempo=round(tempo, 2),
        beats=[round(float(b), 3) for b in beats], downbeats=[round(float(b), 3) for b in downbeats],
        energy=[round(float(e), 3) for e in energy], sections=sections, drops=drops,
        strikes=find_strikes(y, sr, beats),
        booms=[round(float(b), 3) for b, lv in zip(beats, boom_level)
               if len(boom_level) and lv >= 0.35 * float(np.percentile(boom_level, 95))],
        # dance music puts loud hi-hats on the off-beats and busy bass lines in the low band, so also
        # listen to the low band and to the kick drum alone
        pulse=[max(a, b, c) for a, b, c in zip(
            beat_dominance(onset_env, beats, duration), beat_dominance(low_band, beats, duration),
            beat_dominance(kick_env, beats, duration))],
    )


def find_strikes(y: np.ndarray, sr: int, beats: np.ndarray, min_gap: float = 0.2, strength: float = 0.5) -> list[float]:
    """The clear hits of the track, each snapped to the sixteenth-note grid the beats make (a hit that isn't on it is
    noise or a swung fill: skipped). Spectral flux on a fine hop; the hit's strength is relative to the loud ones."""
    import librosa

    if len(beats) < 4:
        return []
    hop = 128
    spec = np.abs(librosa.stft(y, n_fft=1024, hop_length=hop)) ** 2
    mel = librosa.power_to_db(librosa.feature.melspectrogram(S=spec, sr=sr, n_mels=40, fmax=min(12000, sr // 2)))
    flux = np.maximum(0.0, np.diff(mel, axis=1, prepend=mel[:, :1])).sum(axis=0)
    flux = flux / (np.percentile(flux, 99) + 1e-9)
    peaks, _ = find_peaks(flux, height=strength, distance=max(1, int(min_gap * sr / hop / 2)))
    period = float(np.median(np.diff(beats)))
    out: dict[float, float] = {}
    for k in peaks:
        t = float(k * hop / sr) - 0.5 * 1024 / sr * 0.5  # the flux frame peaks a little after the attack
        i = int(np.argmin(np.abs(beats - t)))
        x = (t - beats[i]) / period
        q = round(x * 4) / 4
        if abs(x - q) * period > 0.045:
            continue
        at = float(beats[i] + q * period)
        out[at] = max(out.get(at, 0.0), float(flux[k]))
    times = sorted(out)
    kept: list[float] = []
    for t in times:  # two hits closer than min_gap are one attack: keep the stronger
        if kept and t - kept[-1] < min_gap - 1e-6:
            if out[t] > out[kept[-1]]:
                kept[-1] = t
            continue
        kept.append(t)
    return [round(t, 3) for t in kept if t >= 0]


def beat_dominance(onset_env: np.ndarray, beats: np.ndarray, duration: float) -> list[float]:
    """Per PULSE_CHUNK seconds: onset strength on the beats over onset strength on the half-beats.
    ~1 or less means no audible beat on the grid (pads, or accents on the off-beat), so cuts placed
    "on the beat" there don't feel on the music; 1.4+ is a clear beat."""
    if len(beats) < 4:
        return []
    half = float(np.median(np.diff(beats))) / 2
    out = []
    for c0 in np.arange(0.0, duration, PULSE_CHUNK):
        b = beats[(beats >= c0) & (beats < c0 + PULSE_CHUNK)]
        out.append(round(_strength_at(onset_env, b) / max(_strength_at(onset_env, b + half), 1e-9), 2)
                   if len(b) >= 4 else 1.0)
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
