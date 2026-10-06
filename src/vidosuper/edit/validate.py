"""EDL checks. Errors block rendering; warnings are fed back to the editor as things to consider."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..schemas import EDL, ClipInfo, ClipSignals, Interval
from .speed import out_duration, speeds

BOUNDS_SLACK = 0.05  # seconds past the end of a clip we silently clamp


@dataclass
class Issue:
    level: Literal["error", "warning"]
    segment: str | None
    message: str

    def __str__(self) -> str:
        where = f"[{self.segment}] " if self.segment else ""
        return f"{self.level.upper()}: {where}{self.message}"


def validate_edl(edl: EDL, clips: dict[str, ClipInfo], signals: dict[str, ClipSignals] | None = None,
                 *, unusable: dict[str, list[Interval]] | None = None, repair: bool = True) -> list[Issue]:
    """Check the recipe. With repair=True, tiny overruns past a clip's end are clamped in place.

    `unusable` holds ranges the AI analysts marked as never-use (stricter than the automatic flags).
    """
    issues: list[Issue] = []
    if not edl.segments:
        issues.append(Issue("error", None, "the edit has no segments"))
    seen: set[str] = set()
    used: dict[str, list[tuple[float, float, str]]] = {}
    fps = edl.meta.fps
    if fps <= 0 or edl.meta.width <= 0 or edl.meta.height <= 0:
        issues.append(Issue("error", None, "meta.fps/width/height must be positive"))
    if edl.meta.width % 2 or edl.meta.height % 2:
        issues.append(Issue("error", None, "meta.width and meta.height must be even numbers"))

    for seg in edl.segments:
        sid = seg.id
        if sid in seen:
            issues.append(Issue("error", sid, "duplicate segment id"))
        seen.add(sid)
        clip = clips.get(seg.clip_id)
        if clip is None:
            issues.append(Issue("error", sid, f"unknown clip_id {seg.clip_id!r}"))
            continue
        if not clip.usable:
            issues.append(Issue("error", sid, f"clip {seg.clip_id} failed to ingest: {clip.error}"))
            continue
        if not 0.1 <= seg.speed <= 8.0:
            issues.append(Issue("error", sid, f"speed {seg.speed} outside 0.1-8"))
            continue
        if seg.src_in < 0:
            issues.append(Issue("error", sid, f"src_in {seg.src_in} is negative"))
        if seg.src_out > clip.duration:
            if repair and seg.src_out <= clip.duration + BOUNDS_SLACK:
                seg.src_out = round(clip.duration, 3)
            else:
                issues.append(Issue("error", sid, f"src_out {seg.src_out:.2f}s is past the clip end ({clip.duration:.2f}s)"))
        if seg.src_out <= seg.src_in:
            issues.append(Issue("error", sid, "src_out must be after src_in"))
            continue
        out_len = out_duration(seg)
        if out_len < 0.2:
            issues.append(Issue("error", sid, f"shot is only {out_len:.2f}s on screen (minimum 0.2s)"))
        if seg.transition_in.type != "cut" and seg.transition_in.duration <= 0:
            issues.append(Issue("warning", sid, f"{seg.transition_in.type} transition with zero duration acts as a cut"))
        slowest = min(speeds(seg))
        if slowest < 1 and clip.fps * slowest < 0.9 * fps:
            issues.append(Issue("warning", sid, f"{slowest}x slow motion from a {clip.fps:.0f} fps clip will look choppy "
                                                f"(needs {fps / slowest:.0f} fps source)"))
        if seg.ramp and seg.audio.mode == "original":
            issues.append(Issue("warning", sid, "a ramped shot plays without its own sound; lay it as a sound layer if you need it"))
        if seg.zoom > 1.0001:
            short_side = min(clip.width, clip.height) / seg.zoom
            if short_side < 0.75 * min(edl.meta.width, edl.meta.height):
                issues.append(Issue("warning", sid, f"{seg.zoom:g}x punch-in leaves {short_side:.0f}px of the "
                                                    f"{min(clip.width, clip.height)}px source; it will look soft"))
        if seg.audio.mode == "original" and not clip.has_audio:
            issues.append(Issue("warning", sid, "audio mode 'original' but the clip has no audio"))

        sig = (signals or {}).get(seg.clip_id)
        if sig is not None:
            overlap = 0.0
            reasons = set()
            for bad in sig.bad_intervals:
                o = min(seg.src_out, bad.end) - max(seg.src_in, bad.start)
                if o > 0:
                    overlap += o
                    reasons.add(bad.reason)
            if overlap / (seg.src_out - seg.src_in) > 0.3:
                issues.append(Issue("warning", sid, f"{overlap:.1f}s of this shot is {', '.join(sorted(reasons))}"))
        for bad in (unusable or {}).get(seg.clip_id, []):
            o = min(seg.src_out, bad.end) - max(seg.src_in, bad.start)
            if o > 0.2:
                issues.append(Issue("warning", sid, f"uses {o:.1f}s of a range marked unusable "
                                                    f"({bad.start:.1f}-{bad.end:.1f}s: {bad.reason})"))
        for start, end, other in used.get(seg.clip_id, []):
            o = min(seg.src_out, end) - max(seg.src_in, start)
            if o > 0.5 * min(seg.src_out - seg.src_in, end - start):
                issues.append(Issue("warning", sid, f"re-uses most of the same footage as {other}"))
        used.setdefault(seg.clip_id, []).append((seg.src_in, seg.src_out, sid))

    if edl.music is not None and not Path(edl.music.path).is_file():
        issues.append(Issue("error", None, f"music file not found: {edl.music.path}"))
    issues += _check_extras(edl, clips)
    return issues


def _approx_duration(edl: EDL) -> float:
    total = sum(out_duration(s) + s.freeze for s in edl.segments if s.speed > 0)
    return total - sum(s.transition_in.duration for s in edl.segments[1:] if s.transition_in.type != "cut")


def _check_extras(edl: EDL, clips: dict[str, ClipInfo]) -> list[Issue]:
    from ..render.looks import LOOKS
    from ..render.sfx import SFX_NAMES

    issues: list[Issue] = []
    total = _approx_duration(edl)
    for name, where in [(edl.meta.look, None)] + [(s.look, s.id) for s in edl.segments if s.look]:
        if name not in LOOKS:
            issues.append(Issue("error", where, f"unknown look {name!r}; choose from {', '.join(LOOKS)}"))
    for k, t in enumerate(edl.texts):
        if not 0 <= t.start < t.end:
            issues.append(Issue("error", f"text{k + 1}", f"text times {t.start}-{t.end} are invalid"))
        elif t.end > total + 0.05:
            issues.append(Issue("warning", f"text{k + 1}", f"ends at {t.end:.1f}s, after the video ends ({total:.1f}s)"))
        if len(t.text) > 60:
            issues.append(Issue("warning", f"text{k + 1}", "long text; on-screen words should be few and purposeful"))
    if len(edl.texts) > 3:
        issues.append(Issue("warning", None, f"{len(edl.texts)} text overlays; keep text rare (3 or fewer)"))
    for k, layer in enumerate(edl.sounds):
        where = f"sound{k + 1}"
        if layer.duration <= 0 or layer.at < 0:
            issues.append(Issue("error", where, "sound layers need at >= 0 and duration > 0"))
        if layer.source.startswith("clip:"):
            clip = clips.get(layer.source[5:])
            if clip is None or not clip.has_audio:
                issues.append(Issue("error", where, f"{layer.source}: unknown clip or the clip has no sound"))
            elif layer.src_in + layer.duration > clip.duration + 0.05:
                issues.append(Issue("warning", where, f"{layer.source} runs past the clip's end; the rest is silence"))
        elif layer.source.startswith("sfx:"):
            if layer.source[4:] not in SFX_NAMES:
                issues.append(Issue("error", where, f"unknown effect {layer.source}; available: {', '.join(SFX_NAMES)}"))
        elif not Path(layer.source).is_file():
            issues.append(Issue("error", where, f"sound file not found: {layer.source}"))
    if edl.music is not None:
        for p in edl.music.automation:
            if not -80 <= p.gain_db <= 12:
                issues.append(Issue("error", None, f"music automation gain {p.gain_db} dB at {p.t}s is out of range (-80..12)"))
        for j in edl.music.jumps:
            if j.to < 0 or j.at <= 0:
                issues.append(Issue("error", None, f"music jump at {j.at}s to {j.to}s is invalid"))
    return issues
