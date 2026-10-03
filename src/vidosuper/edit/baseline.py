"""Non-AI baseline: each clip's technically best window, in recording order.

This exists to exercise the renderer end-to-end and as a "dumb montage" control that the
AI editor (Phase 3+) has to clearly beat.
"""

from __future__ import annotations

from ..schemas import EDL, ClipInfo, ClipSignals, EditMeta, MusicSpec, Segment, SegmentAudio, Window


def baseline_edl(
    clips: list[ClipInfo],
    signals: dict[str, ClipSignals],
    *,
    project_dir: str,
    meta: EditMeta,
    target_seconds: float = 60.0,
    shot_seconds: float = 3.0,
    min_score: float = 0.35,
    max_per_clip: int = 2,
    music: MusicSpec | None = None,
) -> EDL:
    order = {c.clip_id: i for i, c in enumerate(clips)}  # clips arrive in recording order
    candidates: list[tuple[float, ClipInfo, Window]] = []
    for clip in clips:
        sig = signals.get(clip.clip_id)
        if not clip.usable or sig is None:
            continue
        for rank, window in enumerate(sig.best_windows[:max_per_clip]):
            if window.score < min_score:
                continue
            # trim the window to the target shot length around its centre
            length = min(shot_seconds, window.end - window.start)
            mid = (window.start + window.end) / 2
            start = max(0.0, min(mid - length / 2, clip.duration - length))
            trimmed = Window(start=round(start, 2), end=round(start + length, 2), score=window.score,
                             quality=window.quality, interest=window.interest)
            candidates.append((window.score - 0.15 * rank, clip, trimmed))

    candidates.sort(key=lambda c: c[0], reverse=True)
    chosen: list[tuple[ClipInfo, Window]] = []
    total = 0.0
    for _, clip, window in candidates:
        if total >= target_seconds:
            break
        chosen.append((clip, window))
        total += window.end - window.start
    chosen.sort(key=lambda cw: (order[cw[0].clip_id], cw[1].start))

    segments = [
        Segment(
            id=f"s{i + 1:03d}",
            clip_id=clip.clip_id,
            src_in=window.start,
            src_out=window.end,
            audio=SegmentAudio(mode="original" if clip.has_audio else "mute"),
            role="baseline",
            rationale=f"best technical window (quality {window.quality:.2f}, interest {window.interest:.2f})",
        )
        for i, (clip, window) in enumerate(chosen)
    ]
    return EDL(project_dir=project_dir, meta=meta, segments=segments, music=music,
               notes="Non-AI baseline: best technical window per clip, in recording order.")
