"""Snap hard cuts onto music beats by nudging the outgoing shot's end point.

Cuts that land exactly on the beat are a big part of why an edit "feels" edited. The editor picks
shots and rough lengths; this pass makes the timing frame-exact without ever extending a shot into
footage marked bad.
"""

from __future__ import annotations

import numpy as np

from ..schemas import EDL, ClipInfo, Interval
from .timeline import build_timeline


def _overlaps(start: float, end: float, ranges: list[Interval]) -> bool:
    return any(min(end, r.end) - max(start, r.start) > 1e-6 for r in ranges)


def snap_to_beats(edl: EDL, clips: dict[str, ClipInfo], music_beats: list[float], *, tolerance: float = 0.15,
                  min_shot: float = 0.5, avoid: dict[str, list[Interval]] | None = None) -> list[str]:
    """Modify edl in place; returns one note per cut (moved / kept / couldn't). Beats are music time."""
    if edl.music is None or not music_beats or not edl.meta.snap_to_beats:
        return []
    fps = edl.meta.fps
    beats = np.array([b - edl.music.start for b in music_beats if b - edl.music.start >= 0])
    avoid = avoid or {}
    notes: list[str] = []
    for i in range(len(edl.segments) - 1):
        seg, nxt = edl.segments[i], edl.segments[i + 1]
        if nxt.transition_in.type != "cut":
            continue
        timeline = build_timeline(edl, clips)
        cut = timeline.items[i + 1].start / fps
        j = int(np.argmin(np.abs(beats - cut)))
        delta = float(beats[j] - cut)
        if abs(delta) < 0.5 / fps:
            notes.append(f"{seg.id}->{nxt.id}: already on the beat at {cut:.2f}s")
            continue
        if abs(delta) > tolerance:
            notes.append(f"{seg.id}->{nxt.id}: cut at {cut:.2f}s is {delta * 1000:+.0f} ms from the nearest beat; "
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
            notes.append(f"{seg.id}->{nxt.id}: could not move the cut onto the beat ({reason})")
            continue
        seg.src_out = round(new_out, 4)
        notes.append(f"{seg.id}->{nxt.id}: cut moved {delta * 1000:+.0f} ms onto the beat at {beats[j]:.2f}s")
    return notes


def beat_alignment(edl: EDL, clips: dict[str, ClipInfo], music_beats: list[float]) -> tuple[int, int]:
    """(cuts within one frame of a beat, total hard cuts) for reporting."""
    if edl.music is None or not music_beats:
        return 0, 0
    fps = edl.meta.fps
    beats = np.array([b - edl.music.start for b in music_beats])
    timeline = build_timeline(edl, clips)
    cuts = [it.start / fps for it in timeline.items[1:] if it.seg.transition_in.type == "cut"]
    on_beat = sum(1 for c in cuts if np.min(np.abs(beats - c)) <= 1.0 / fps)
    return on_beat, len(cuts)
