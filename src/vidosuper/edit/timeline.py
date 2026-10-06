"""Frame-exact timeline math for an EDL. Video and audio both follow these numbers, so they stay in sync."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ..schemas import EDL, ClipInfo, Segment
from .speed import out_duration, source_time

STANDARD_FPS = (24, 25, 30)


@dataclass
class TimelineItem:
    index: int
    seg: Segment
    clip: ClipInfo
    frames: int  # output frames this shot occupies (moving + frozen)
    start: int  # first output frame on the timeline
    trans_frames: int  # overlap with the previous shot (0 for a hard cut)
    moving_frames: int = 0  # frames of actual motion before any freeze

    @property
    def end(self) -> int:
        return self.start + self.frames

    def src_seconds(self, fps: int) -> float:
        """Source seconds actually consumed (frame-quantised)."""
        return source_time(self.seg, self.moving_frames / fps) if self.seg.ramp else self.moving_frames * self.seg.speed / fps


@dataclass
class Timeline:
    items: list[TimelineItem]
    fps: int
    total_frames: int
    notes: list[str]

    @property
    def duration(self) -> float:
        return self.total_frames / self.fps


def build_timeline(edl: EDL, clips: dict[str, ClipInfo]) -> Timeline:
    fps = edl.meta.fps
    items: list[TimelineItem] = []
    notes: list[str] = []
    cursor = 0
    for index, seg in enumerate(edl.segments):
        clip = clips[seg.clip_id]
        moving = max(1, round(out_duration(seg) * fps))
        frames = moving + round(seg.freeze * fps)
        trans = 0
        if index > 0 and seg.transition_in.type != "cut":
            trans = round(seg.transition_in.duration * fps)
            limit = min(items[-1].frames, frames) // 2
            if trans > limit:
                notes.append(f"{seg.id}: transition shortened from {trans} to {limit} frames (shots too short)")
                trans = limit
        start = 0 if index == 0 else cursor - trans
        items.append(TimelineItem(index=index, seg=seg, clip=clip, frames=frames, start=start, trans_frames=trans,
                                  moving_frames=moving))
        cursor = start + frames
    return Timeline(items=items, fps=fps, total_frames=cursor, notes=notes)


def standard_fps(rate: float) -> int:
    while rate >= 47:  # 48/50/60/120/240 fps footage plays back at half (or less) on a normal timeline
        rate /= 2
    return min(STANDARD_FPS, key=lambda f: abs(f - rate))


def choose_fps(clips: list[ClipInfo]) -> int:
    """Pick the output frame rate that matches most of the footage (weighted by duration)."""
    votes: Counter[int] = Counter()
    for clip in clips:
        if clip.usable and clip.fps > 0:
            votes[standard_fps(clip.fps)] += clip.duration
    return votes.most_common(1)[0][0] if votes else 30
