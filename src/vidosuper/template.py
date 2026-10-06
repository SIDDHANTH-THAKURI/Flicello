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
from .music.analysis import MusicAnalysis
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
    keywords: list[str] = Field(default_factory=list, description="Words a placeholder moment should mention.")
    clip: str | None = Field(None, description="A chosen clip (catalog short id like c40, or full id) instead of a placeholder.")
    src_in: float | None = Field(None, description="Where the chosen clip starts (source seconds).")
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
    stabilize: bool = False
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


def _short_ids(project: Project) -> dict[str, str]:
    path = project.root / "catalog.json"
    if not path.exists():
        return {}
    catalog = json.loads(path.read_text(encoding="utf-8"))
    return {c["short"]: c["clip"]["clip_id"] for c in catalog.get("clips", [])}


def _span(slot: Slot, seconds_on_screen: float) -> float:
    """Source seconds a slot needs to fill seconds_on_screen."""
    if not slot.ramp:
        return seconds_on_screen * slot.speed
    probe = Segment(id="probe", clip_id="x", src_in=0.0, src_out=1.0, ramp=slot.ramp)
    return source_time(probe, seconds_on_screen)


def autofill(template: Template, project: Project, fps: int = 30, width: int = 1920, height: int = 1080) -> EDL:
    """An EDL with placeholder footage in every slot; transitions are centred on the slot boundaries."""
    pool = _moments(project)
    used: set[tuple[str, float]] = set()
    clips = project.clips()
    shorts = _short_ids(project)
    slots = template.slots
    chosen: dict[int, dict] = {}
    # the most important slots (high energy, long holds) choose first, so the best action lands on the drop
    order = sorted(range(len(slots)), key=lambda k: (-slots[k].energy, -(slots[k].end - slots[k].start)))
    for k in order:
        slot = slots[k]
        d_in = slot.transition_in.duration if k and slot.transition_in.type != "cut" else 0.0
        d_out = slots[k + 1].transition_in.duration if k + 1 < len(slots) and \
            slots[k + 1].transition_in.type != "cut" else 0.0
        on_screen = (slot.end - slot.start) + d_in / 2 + d_out / 2
        need = _span(slot, on_screen)
        slow = min([p.speed for p in slot.ramp] or [slot.speed]) < 0.95
        if slot.clip:  # chosen by hand
            clip = clips[shorts.get(slot.clip, slot.clip)]
            start = slot.src_in or 0.0
            chosen[k] = {"clip": clip, "start": start, "end": start + need, "text": f"chosen: {slot.want}", "need": need}
            used.add((clip.clip_id, start))
            continue
        def fit(m: dict) -> float:
            hits = sum(1 for w in slot.keywords if w.lower() in m["text"].lower())
            calm = m["kind"] in ("scenic", "establishing")
            return m["score"] + 3 * hits + (2 if (slot.energy <= 2) == calm else 0)

        pick = None
        neighbours = {chosen[j]["clip"].clip_id for j in (k - 1, k + 1) if j in chosen}
        for m in sorted(pool, key=fit, reverse=True):
            key = (m["clip"].clip_id, m["start"])
            if key in used or (slow and m["clip"].fps < 50) or m["clip"].duration < need:
                continue
            if m["clip"].clip_id in neighbours and len(pool) > len(slots):
                continue
            pick = m
            break
        if pick is None:
            raise ValueError(f"no footage long enough for slot {slot.id} ({need:.1f}s of source)")
        used.add((pick["clip"].clip_id, pick["start"]))
        chosen[k] = {**pick, "need": need}
    segments = []
    for k, slot in enumerate(slots):
        pick, need = chosen[k], chosen[k]["need"]
        start = pick["start"]
        if pick["end"] - start < need:  # widen around the moment, inside the clip
            start = max(0.0, min(pick["clip"].duration - need, pick["start"] - (need - (pick["end"] - start)) / 2))
        segments.append(Segment(
            id=slot.id, clip_id=pick["clip"].clip_id, src_in=round(start, 3), src_out=round(start + need, 3),
            speed=slot.speed, ramp=slot.ramp, push=slot.push, fx=slot.fx,
            transition_in=slot.transition_in if k else Transition(), out_sync="free",
            audio=SegmentAudio(mode="mute"), role=f"energy {slot.energy}",
            rationale=(pick["text"] if pick["text"].startswith("chosen") else
                       f"PLACEHOLDER for '{slot.want}': {pick['text'][:90]}")))
    meta = EditMeta(title=template.name, width=width, height=height, fps=fps, look=template.look,
                    fade_in=template.fade_in, fade_out=template.fade_out, snap_to_beats=False,
                    stabilize=template.stabilize)
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


def retarget(template: Template, old: MusicAnalysis, new: MusicAnalysis, new_path: str,
             anchor: str | None = None) -> tuple[Template, list[str]]:
    """The same edit on another song. Every slot keeps its place in beats counted from the anchor slot
    (default: the one that hits the old song's drop), and the anchor lands on the new song's drop, so
    the shots, speeds and hits stay where they were in the music. Hits are rescaled to the new tempo;
    slots past the new song's end are shortened or dropped."""
    import numpy as np

    old_period = float(np.median(np.diff(old.beats)))
    new_beats = np.asarray(new.beats, dtype=float)
    new_period = float(np.median(np.diff(new_beats)))
    if anchor is not None:
        anchor_slot = next((s for s in template.slots if s.id == anchor), None)
        if anchor_slot is None:
            raise ValueError(f"no slot {anchor!r} in the template")
    else:
        if not old.drops:
            raise ValueError("the old song has no drop to line up; name an anchor slot")
        old_drop = old.drops[0] - template.music.start
        anchor_slot = min(template.slots, key=lambda s: abs(s.start - old_drop))
    if not new.drops:
        raise ValueError("the new song has no drop to line up with")
    drop_beat = int(np.argmin(np.abs(new_beats - new.drops[0])))

    def music_time(t: float) -> float:
        """Timeline seconds on the template -> seconds into the new song, by beats from the anchor."""
        pos = drop_beat + round((t - anchor_slot.start) / old_period * 2) / 2
        if pos <= 0:
            return float(new_beats[0] + pos * new_period)
        if pos >= len(new_beats) - 1:
            return float(new_beats[-1] + (pos - len(new_beats) + 1) * new_period)
        return float(np.interp(pos, np.arange(len(new_beats)), new_beats))

    start = music_time(template.slots[0].start)
    if start < 0:
        raise ValueError(f"the new song reaches its drop {-start:.1f}s too early for this template")
    end = new.duration - start
    scale = new_period / old_period
    notes, slots = [], []
    for slot in template.slots:
        full = music_time(slot.end) - start
        s0, s1 = music_time(slot.start) - start, min(full, end)
        if s1 < full - 0.01:
            if s1 - s0 < 1.0:
                notes.append(f"{slot.id}: dropped, the new song ends at {end:.1f}s")
                if slots:
                    slots[-1] = slots[-1].model_copy(update={"end": round(end, 3)})
                continue
            notes.append(f"{slot.id}: shortened to end with the song ({s1 - s0:.1f}s)")
        fx = [f.model_copy(update={"at": round(f.at * scale, 3)}) for f in slot.fx]
        slots.append(slot.model_copy(update={"start": round(s0, 3), "end": round(s1, 3), "fx": fx}))
    music = template.music.model_copy(update={"path": new_path, "start": round(start, 3)})
    notes.insert(0, f"{anchor_slot.id} on the drop at {new.drops[0]:.2f}s; the song starts at {start:.2f}s; "
                    f"tempo {60 / old_period:.1f} -> {60 / new_period:.1f} BPM")
    return template.model_copy(update={"music": music, "slots": slots}), notes
