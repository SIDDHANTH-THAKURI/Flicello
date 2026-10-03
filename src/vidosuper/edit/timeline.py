"""Frame-exact timeline math for an EDL. Video and audio both follow these numbers, so they stay in sync."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ..schemas import EDL, ClipInfo, Segment

STANDARD_FPS = (24, 25, 30)


@dataclass
class TimelineItem:
    index: int
    seg: Segment
    clip: ClipInfo
    frames: int  # output frames this shot occupies
    start: int  # first output frame on the timeline
    trans_frames: int  # overlap with the previous shot (0 for a hard cut)

    @property
    def end(self) -> int:
        return self.start + self.frames

    def src_seconds(self, fps: int) -> float:
        """Source seconds actually consumed (frame-quantised)."""
        return self.frames * self.seg.speed / fps


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
        frames = max(1, round((seg.src_out - seg.src_in) / seg.speed * fps))
        trans = 0
        if index > 0 and seg.transition_in.type != "cut":
            trans = round(seg.transition_in.duration * fps)
            limit = min(items[-1].frames, frames) // 2
            if trans > limit:
                notes.append(f"{seg.id}: transition shortened from {trans} to {limit} frames (shots too short)")
                trans = limit
        start = 0 if index == 0 else cursor - trans
        items.append(TimelineItem(index=index, seg=seg, clip=clip, frames=frames, start=start, trans_frames=trans))
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
