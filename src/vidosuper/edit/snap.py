"""Snap hard cuts onto the music: to a beat, to a bar start, or not at all.

Not every cut should land on the beat: cutting on motion or on a sound against the music is part of
making an edit breathe. The editor marks each cut (`out_sync`); this pass makes the musical ones
frame-exact without ever extending a shot into footage marked bad.
"""

from __future__ import annotations

import numpy as np

from ..music import MusicAnalysis
from ..schemas import EDL, ClipInfo, Interval, MusicSpec
from .timeline import build_timeline


def _overlaps(start: float, end: float, ranges: list[Interval]) -> bool:
    return any(min(end, r.end) - max(start, r.start) > 1e-6 for r in ranges)


def music_pieces(music: MusicSpec, total: float) -> list[tuple[float, float, float]]:
    """(timeline start, timeline end, music time at timeline start) for each stretch between jumps."""
    starts = [(0.0, music.start)] + [(j.at, j.to) for j in sorted(music.jumps, key=lambda j: j.at) if 0 < j.at < total]
    return [(t0, starts[k + 1][0] if k + 1 < len(starts) else total, m0) for k, (t0, m0) in enumerate(starts)]


def timeline_grid(music: MusicSpec, analysis: MusicAnalysis, total: float) -> tuple[np.ndarray, np.ndarray]:
    """Beats and bar starts expressed in timeline seconds (following music.start and any jumps)."""
    beats, downs = [], []
    for t0, t1, m0 in music_pieces(music, total):
        m1 = m0 + (t1 - t0)
        beats += [t0 + b - m0 for b in analysis.beats if m0 <= b < m1]
        downs += [t0 + d - m0 for d in analysis.downbeats if m0 <= d < m1]
    return np.array(beats), np.array(downs)


def snap_to_beats(edl: EDL, clips: dict[str, ClipInfo], analysis: MusicAnalysis, *, tolerance: float = 0.15,
                  bar_tolerance: float = 0.35, min_shot: float = 0.5,
                  avoid: dict[str, list[Interval]] | None = None) -> list[str]:
    """Modify edl in place; returns one note per cut (moved / kept / couldn't)."""
    if edl.music is None or not edl.meta.snap_to_beats:
        return []
    fps = edl.meta.fps
    avoid = avoid or {}
    total = build_timeline(edl, clips).duration + 5.0
    beats, downs = timeline_grid(edl.music, analysis, total)
    notes: list[str] = []
    for i in range(len(edl.segments) - 1):
        seg, nxt = edl.segments[i], edl.segments[i + 1]
        if nxt.transition_in.type != "cut":
            continue
        if seg.out_sync == "free":
            notes.append(f"{seg.id}->{nxt.id}: free cut (timed by the editor, not the music)")
            continue
        grid, tol, unit = (downs, bar_tolerance, "bar start") if seg.out_sync == "downbeat" else (beats, tolerance, "beat")
        if len(grid) == 0:
            continue
        cut = build_timeline(edl, clips).items[i + 1].start / fps
        j = int(np.argmin(np.abs(grid - cut)))
        delta = float(grid[j] - cut)
        if abs(delta) < 0.5 / fps:
            notes.append(f"{seg.id}->{nxt.id}: already on the {unit} at {cut:.2f}s")
            continue
        if abs(delta) > tol:
            notes.append(f"{seg.id}->{nxt.id}: cut at {cut:.2f}s is {delta * 1000:+.0f} ms from the nearest {unit}; "
                         "left alone (too far)")
            continue
        clip = clips[seg.clip_id]
        new_out = seg.src_out + delta * seg.speed
        reason = ""
        if new_out > clip.duration:
            reason = "clip ends"
        elif (new_out - seg.src_in) / seg.speed < min_shot:
            reason = "shot would get too short"
        elif delta > 0 and _overlaps(seg.src_out, new_out, avoid.get(seg.clip_id, [])):
            reason = "would extend into bad footage"
        if reason:
            notes.append(f"{seg.id}->{nxt.id}: could not move the cut onto the {unit} ({reason})")
            continue
        seg.src_out = round(new_out, 4)
        notes.append(f"{seg.id}->{nxt.id}: cut moved {delta * 1000:+.0f} ms onto the {unit} at {grid[j]:.2f}s")
    return notes


def beat_alignment(edl: EDL, clips: dict[str, ClipInfo], analysis: MusicAnalysis) -> tuple[int, int, int]:
    """(musical cuts within one frame of their grid, musical cuts, free cuts) for reporting."""
    if edl.music is None:
        return 0, 0, 0
    fps = edl.meta.fps
    timeline = build_timeline(edl, clips)
    beats, downs = timeline_grid(edl.music, analysis, timeline.duration + 5.0)
    on = musical = free = 0
    for prev, item in zip(timeline.items, timeline.items[1:]):
        if item.seg.transition_in.type != "cut":
            continue
        if prev.seg.out_sync == "free":
            free += 1
            continue
        musical += 1
        grid = downs if prev.seg.out_sync == "downbeat" else beats
        if len(grid) and np.min(np.abs(grid - item.start / fps)) <= 1.0 / fps:
            on += 1
    return on, musical, free
