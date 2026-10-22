"""A stretch of a made track made again, the rest kept note for note (ElevenLabs Music inpainting): the plan keeps
[0, start) and [end, length) of the stored song by its id, and writes [start, end) anew from words ("calmer",
"bigger drums"), told to follow what's around it closely, so it joins the kept parts in tempo and key."""

from __future__ import annotations

from pathlib import Path


def plan(song_id: str, length_ms: int, start_ms: int, end_ms: int, styles: list[str], avoid: list[str]) -> dict:
    start_ms = max(0, min(start_ms, length_ms - 3000))
    end_ms = max(start_ms + 3000, min(end_ms, length_ms))
    chunks: list[dict] = []
    if start_ms >= 1000:
        chunks.append({"song_id": song_id, "range": {"start_ms": 0, "end_ms": start_ms}})
    chunks.append({"text": "[Section] {instrumental}", "duration_ms": end_ms - start_ms,
                   "positive_styles": [*styles, "same tempo and key as the rest of the track", "instrumental"],
                   "negative_styles": [*avoid, "vocals", "a change of tempo"], "context_adherence": "high"})
    if length_ms - end_ms >= 1000:
        chunks.append({"song_id": song_id, "range": {"start_ms": end_ms, "end_ms": length_ms}})
    return {"chunks": chunks}


def remake(song_id: str, length_s: float, start_s: float, end_s: float, styles: list[str], avoid: list[str],
           out: Path) -> dict:
    from .compose import compose_plan

    return compose_plan(plan(song_id, int(length_s * 1000), int(start_s * 1000), int(end_s * 1000), styles, avoid), out,
                        store=True)
