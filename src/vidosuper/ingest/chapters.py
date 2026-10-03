"""Group clips into chapters (days / places) from recording time and GPS."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from ..schemas import ClipInfo

_FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def chronological(clips: list[ClipInfo]) -> list[ClipInfo]:
    return sorted(clips, key=lambda c: (c.creation_time or _FAR_FUTURE, c.rel_path))


def assign_chapters(clips: list[ClipInfo], gap_hours: float, distance_km: float) -> None:
    chapter = 0
    prev_time: datetime | None = None
    prev_gps: tuple[float, float] | None = None
    for clip in chronological([c for c in clips if c.usable]):
        new_chapter = chapter == 0
        if clip.creation_time and prev_time and (clip.creation_time - prev_time).total_seconds() > gap_hours * 3600:
            new_chapter = True
        if clip.gps and prev_gps and haversine_km(clip.gps, prev_gps) > distance_km:
            new_chapter = True
        if new_chapter:
            chapter += 1
        clip.chapter = chapter
        prev_time = clip.creation_time or prev_time
        prev_gps = clip.gps or prev_gps
