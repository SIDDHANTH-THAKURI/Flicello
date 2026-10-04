"""Pick a track for an edit: search, download a shortlist, analyse each, and score how well its
tempo and energy curve fit the director's plan (finding the best place to start the track)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..schemas import MusicBrief
from .analysis import MusicAnalysis, load_or_analyze
from .sources import MusicQuery, TrackCandidate, download


@dataclass
class ScoredTrack:
    candidate: TrackCandidate
    path: Path
    analysis: MusicAnalysis
    offset: float  # suggested music start (a bar start)
    tempo_fit: float
    arc_fit: float
    structure: float
    relevance: float
    total: float
    reasons: list[str] = field(default_factory=list)


def speed_class(bpm_min: float, bpm_max: float) -> str:
    mid = (bpm_min + bpm_max) / 2
    for limit, name in ((70, "verylow"), (95, "low"), (120, "medium"), (145, "high")):
        if mid < limit:
            return name
    return "veryhigh"


def search_plan(brief: MusicBrief) -> list[list[str]]:
    mood, genres = brief.mood or [], brief.genres or []
    plans = []
    if genres and mood:
        plans.append([genres[0], mood[0]])
    if len(genres) > 1 and mood:
        plans.append([genres[1], mood[0]])
    if len(mood) > 1:
        plans.append(mood[:2])
    if genres:
        plans.append(genres[:1])
    unique = []
    for p in plans or [["cinematic"]]:
        if p not in unique:
            unique.append(p)
    return unique[:4]


def tempo_fit(tempo: float, lo: float, hi: float) -> tuple[float, float]:
    """Best score over half/normal/double time, and the tempo it corresponds to."""
    best = (0.0, tempo)
    for t in (tempo / 2, tempo, tempo * 2):
        dist = 0.0 if lo <= t <= hi else min(abs(t - lo), abs(t - hi))
        best = max(best, (max(0.0, 1.0 - dist / 20.0), t))
    return best


def arc_fit(analysis: MusicAnalysis, target: float, arc: list[float]) -> tuple[float, float, float]:
    """(score, best start offset, structure) — slide the edit window over the track's energy curve."""
    energy = np.asarray(analysis.energy, dtype=float)
    starts = [d for d in (analysis.downbeats or [0.0]) if d + target <= analysis.duration - 0.5] or [0.0]
    want = np.asarray(arc, dtype=float) if arc else None
    best = (-1.0, 0.0, 0.0)
    for start in starts:
        window = energy[int(start):int(start + target) + 1]
        if len(window) < 4:
            continue
        if want is not None:
            sampled = np.interp(np.linspace(0, len(window) - 1, len(want)), np.arange(len(window)), window)
            score = 1.0 - float(np.mean(np.abs(sampled - want)))
        else:
            score = min(1.0, float(np.std(window)) / 0.25)  # no plan: prefer windows with real dynamics
        changes = sum(1 for s in analysis.sections if start < s.start < start + target)
        structure = min(1.0, changes / 3.0)
        if score + 0.1 * structure > best[0] + 0.1 * best[2]:
            best = (score, start, structure)
    return max(best[0], 0.0), best[1], best[2]


def relevance(candidate: TrackCandidate, brief: MusicBrief) -> tuple[float, bool]:
    text = " ".join([candidate.title, *candidate.tags]).lower()
    words = [w.lower() for w in (brief.mood + brief.genres)]
    hits = sum(1 for w in words if w in text)
    avoided = any(a.lower() in text for a in brief.avoid)
    return (hits / len(words) if words else 0.5), avoided


def find_music(brief: MusicBrief, target_seconds: float, providers: list, library: Path, analysis_cache: Path,
               *, shortlist: int = 6, log=print) -> list[ScoredTrack]:
    seen: dict[str, TrackCandidate] = {}
    for keywords in search_plan(brief):
        query = MusicQuery(keywords=keywords, instrumental=brief.instrumental, min_duration=target_seconds + 4,
                           speed=speed_class(brief.bpm_min, brief.bpm_max))
        for provider in providers:
            try:
                found = provider.search(query)
            except RuntimeError as exc:
                log(f"  {provider.name} search {keywords}: {exc}")
                continue
            log(f"  {provider.name} search {' '.join(keywords)!r}: {len(found)} usable tracks")
            for cand in found:
                seen.setdefault(cand.key, cand)
    if not seen:
        return []
    ranked = sorted(seen.values(), key=lambda c: (relevance(c, brief)[1], -relevance(c, brief)[0]))
    scored: list[ScoredTrack] = []
    for cand in ranked[:shortlist]:
        try:
            path = download(cand, library)
            analysis = load_or_analyze(path, analysis_cache)
        except Exception as exc:  # one bad download must not stop the search
            log(f"  skipped {cand.title!r}: {exc}")
            continue
        t_score, t_used = tempo_fit(analysis.tempo, brief.bpm_min, brief.bpm_max)
        a_score, offset, structure = arc_fit(analysis, target_seconds, brief.energy_arc)
        rel, avoided = relevance(cand, brief)
        total = 0.35 * a_score + 0.25 * t_score + 0.15 * structure + 0.25 * rel - (0.3 if avoided else 0.0)
        reasons = [f"{t_used:.0f} BPM ({'in' if t_score == 1 else 'outside'} {brief.bpm_min:.0f}-{brief.bpm_max:.0f})",
                   f"energy fit {a_score:.2f} starting at {offset:.1f}s", f"{structure * 3:.0f} section changes in the window",
                   f"keyword match {rel:.2f}"] + (["matches an avoid word"] if avoided else [])
        scored.append(ScoredTrack(cand, path, analysis, offset, t_score, a_score, structure, rel, total, reasons))
        log(f"  analysed {cand.title!r} by {cand.artist}: score {total:.2f}")
    return sorted(scored, key=lambda s: s.total, reverse=True)


def candidates_markdown(scored: list[ScoredTrack], chosen: int = 0) -> str:
    lines = ["# Music candidates", "", "Ranked by fit to the director's music brief. All are free for commercial use "
             "(CC0 or CC BY; CC BY needs the credit line in your video description).", ""]
    for i, s in enumerate(scored):
        c = s.candidate
        mark = " **(chosen)**" if i == chosen else ""
        lines += [f"## {i + 1}. {c.title} - {c.artist}{mark}",
                  f"- score {s.total:.2f}: " + "; ".join(s.reasons),
                  f"- {s.analysis.summary()}",
                  f"- licence {c.license.upper()} via {c.provider}; page: {c.page_url or 'n/a'}",
                  f"- credit: {c.attribution}", f"- file: {s.path}", ""]
    return "\n".join(lines)
