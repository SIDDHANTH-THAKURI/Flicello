"""Templates: the rhythm of an edit designed on a song before any footage is chosen.

A template is a list of slots on the music's timeline - how long each shot lasts, its speed or speed
ramp, push-in, hits (flash / shake / pulse) and the transition into it. `autofill` drops reasonable
placeholder footage into every slot (best-rated moments, no car shots, no portrait clips, slow motion
only from 60 fps clips) so the user can feel the rhythm and the effects on the real song; once they
approve it, the clips are chosen with care for each slot.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from .edit.speed import source_time
from .perceive.prep import analysis_path
from .schemas import EDL, ClipAnalysis, EditMeta, Fx, MusicSpec, RampPoint, Segment, SegmentAudio, Transition
from .workspace import Project

CAR_WORDS = re.compile(r"\b(dash|dashboard|windscreen|windshield|motorway|highway|freeway|car|ute ahead|"
                       r"overtak|driv(e|ing)|bonnet|traffic|red light)\b", re.IGNORECASE)


class Slot(BaseModel):
    id: str
    start: float = Field(..., description="Timeline seconds where this shot is fully on screen (a beat/bar).")
    end: float
    want: str = Field("", description="What belongs here, for whoever picks the clip: 'wide epic reveal'.")
    energy: int = Field(3, ge=1, le=5)
    speed: float = 1.0
    ramp: list[RampPoint] = Field(default_factory=list)
    push: float = 0.0
    fx: list[Fx] = Field(default_factory=list)
    transition_in: Transition = Field(default_factory=Transition)


class Template(BaseModel):
    name: str
    idea: str = ""
    music: MusicSpec
    look: str = "natural"
    fade_in: float = 0.0
    fade_out: float = 1.0
    slots: list[Slot]


def load_template(path: Path) -> Template:
    return Template.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _moments(project: Project) -> list[dict]:
    """Every logged moment that may stand in for a slot: landscape, not from a car, with its score."""
    out = []
    for cid, clip in project.clips().items():
        path = analysis_path(project, cid)
        if not path.exists() or clip.height > clip.width:
            continue
        analysis = ClipAnalysis.model_validate_json(path.read_text(encoding="utf-8"))
        if CAR_WORDS.search(f"{analysis.summary} {analysis.scene}"):
            continue
        for m in analysis.moments:
            if CAR_WORDS.search(m.description):
                continue
            out.append({"clip": clip, "start": m.start, "end": m.end, "peak": m.peak, "kind": m.kind,
                        "score": m.hook + m.strength, "text": m.description})
    return out


def _span(slot: Slot, seconds_on_screen: float) -> float:
    """Source seconds a slot needs to fill seconds_on_screen."""
    if not slot.ramp:
        return seconds_on_screen * slot.speed
    probe = Segment(id="probe", clip_id="x", src_in=0.0, src_out=1.0, ramp=slot.ramp)
    return source_time(probe, seconds_on_screen)


def autofill(template: Template, project: Project, fps: int = 30, width: int = 1920, height: int = 1080) -> EDL:
    """An EDL with placeholder footage in every slot; transitions are centred on the slot boundaries."""
    pool = sorted(_moments(project), key=lambda m: -m["score"])
    used: set[tuple[str, float]] = set()
    slots = template.slots
    segments = []
    last_clip = None
    for k, slot in enumerate(slots):
        d_in = slot.transition_in.duration if k and slot.transition_in.type != "cut" else 0.0
        d_out = slots[k + 1].transition_in.duration if k + 1 < len(slots) and \
            slots[k + 1].transition_in.type != "cut" else 0.0
        on_screen = (slot.end - slot.start) + d_in / 2 + d_out / 2
        need = _span(slot, on_screen)
        slow = min([p.speed for p in slot.ramp] or [slot.speed]) < 0.95
        pick = None
        for m in pool:
            key = (m["clip"].clip_id, m["start"])
            if key in used or (slow and m["clip"].fps < 50) or m["clip"].duration < need:
                continue
            if m["clip"].clip_id == last_clip and len(pool) > len(slots):
                continue
            pick = m
            break
        if pick is None:
            raise ValueError(f"no footage long enough for slot {slot.id} ({need:.1f}s of source)")
        used.add((pick["clip"].clip_id, pick["start"]))
        last_clip = pick["clip"].clip_id
        start = pick["start"]
        if pick["end"] - start < need:  # widen around the moment, inside the clip
            start = max(0.0, min(pick["clip"].duration - need, pick["start"] - (need - (pick["end"] - start)) / 2))
        segments.append(Segment(
            id=slot.id, clip_id=pick["clip"].clip_id, src_in=round(start, 3), src_out=round(start + need, 3),
            speed=slot.speed, ramp=slot.ramp, push=slot.push, fx=slot.fx,
            transition_in=slot.transition_in if k else Transition(), out_sync="free",
            audio=SegmentAudio(mode="mute"), role=f"energy {slot.energy}",
            rationale=f"PLACEHOLDER for '{slot.want}': {pick['text'][:90]}"))
    meta = EditMeta(title=template.name, width=width, height=height, fps=fps, look=template.look,
                    fade_in=template.fade_in, fade_out=template.fade_out, snap_to_beats=False)
    return EDL(project_dir=str(project.clips_dir), meta=meta, segments=segments, music=template.music)


def template_markdown(template: Template) -> str:
    rows = [f"# {template.name}", "", template.idea, "", "| slot | time | energy | speed | push | hits | in | want |",
            "|---|---|---|---|---|---|---|---|"]
    for s in template.slots:
        speed = "→".join(f"{p.speed:g}x" for p in s.ramp) if s.ramp else f"{s.speed:g}x"
        hits = ", ".join(f"{f.type}@{f.at:g}" for f in s.fx) or "-"
        trans = s.transition_in.type if s.transition_in.type == "cut" else f"{s.transition_in.type} {s.transition_in.duration:g}s"
        rows.append(f"| {s.id} | {s.start:.2f}-{s.end:.2f} | {s.energy} | {speed} | {s.push:+g} | {hits} | {trans} | {s.want} |")
    return "\n".join(rows) + "\n"


def save_preview_edl(edl: EDL, path: Path) -> None:
    Path(path).write_text(json.dumps(edl.model_dump(mode="json"), indent=2), encoding="utf-8")
