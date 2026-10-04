"""Re-time an edit to a different track (or tempo): every cut keeps its musical position.

A cut on beat 17 of the old track lands on beat 17 of the new one; shots stretch or shrink with the
tempo, and texts, sound layers and music automation move with them. Afterwards the normal beat
snapping pass makes the musical cuts frame-exact again.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from ..music import MusicAnalysis
from ..schemas import EDL, ClipInfo, GainPoint, MusicSpec
from .snap import timeline_grid
from .timeline import build_timeline


def _beat_map(grid: np.ndarray) -> tuple[Callable[[float], float], Callable[[float], float], float]:
    """(seconds → fractional beat index, beat index → seconds, period), extrapolating at both ends."""
    idx = np.arange(len(grid), dtype=float)
    period = float(np.median(np.diff(grid))) if len(grid) > 1 else 0.5

    def to_beat(t: float) -> float:
        if t <= grid[0]:
            return (t - grid[0]) / period
        if t >= grid[-1]:
            return idx[-1] + (t - grid[-1]) / period
        return float(np.interp(t, grid, idx))

    def to_time(b: float) -> float:
        if b <= 0:
            return float(grid[0] + b * period)
        if b >= idx[-1]:
            return float(grid[-1] + (b - idx[-1]) * period)
        return float(np.interp(b, idx, grid))

    return to_beat, to_time, period


def resync_edl(edl: EDL, clips: dict[str, ClipInfo], old: MusicAnalysis, new: MusicAnalysis,
               new_path: str, new_start: float) -> list[str]:
    """Modify edl in place for the new track; returns notes on anything that couldn't be kept exact."""
    if edl.music is None:
        raise ValueError("the edit has no music to re-sync from")
    fps = edl.meta.fps
    timeline = build_timeline(edl, clips)
    total = timeline.duration
    old_grid, _ = timeline_grid(edl.music, old, total + 10)
    new_spec = MusicSpec(path=new_path, start=new_start, gain_db=edl.music.gain_db,
                         fade_in=edl.music.fade_in, fade_out=edl.music.fade_out)
    new_grid, _ = timeline_grid(new_spec, new, total * 3 + 30)
    if len(old_grid) < 2 or len(new_grid) < 2:
        raise ValueError("not enough beats to re-sync")
    to_beat, _, old_period = _beat_map(old_grid)
    _, to_time, new_period = _beat_map(new_grid)

    def remap(t: float) -> float:
        return to_time(to_beat(t))

    notes = [f"tempo {60 / old_period:.0f} -> {60 / new_period:.0f} BPM; shots scale by {new_period / old_period:.2f}x"]
    items = timeline.items
    starts = [remap(it.start / fps) for it in items] + [remap(total)]
    for i, (item, seg) in enumerate(zip(items, edl.segments)):
        overlap = items[i + 1].trans_frames / fps * new_period / old_period if i + 1 < len(items) else 0.0
        on_screen = starts[i + 1] - starts[i] + overlap
        moving = max(0.3, on_screen - seg.freeze)
        new_out = seg.src_in + moving * seg.speed
        clip = clips[seg.clip_id]
        if new_out > clip.duration:
            overflow = new_out - clip.duration
            if seg.src_in - overflow >= 0:
                seg.src_in = round(seg.src_in - overflow, 4)
                notes.append(f"{seg.id}: starts {overflow:.2f}s earlier in the clip to fit the new tempo")
            else:
                notes.append(f"{seg.id}: the clip is too short for the new tempo; the shot ends early")
            new_out = clip.duration
        seg.src_out = round(new_out, 4)
        if seg.transition_in.type != "cut":
            seg.transition_in.duration = round(seg.transition_in.duration * new_period / old_period, 3)
    for text in edl.texts:
        text.start, text.end = round(remap(text.start), 3), round(remap(text.end), 3)
    for layer in edl.sounds:
        end = remap(layer.at + layer.duration)
        layer.at = round(remap(layer.at), 3)
        layer.duration = round(max(0.1, end - layer.at), 3)
    new_spec.automation = [GainPoint(t=round(remap(p.t), 3), gain_db=p.gain_db) for p in edl.music.automation]
    if edl.music.jumps:
        notes.append("music jumps were dropped (they belonged to the old track's structure)")
    edl.music = new_spec
    return notes
