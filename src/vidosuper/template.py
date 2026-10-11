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
from .schemas import (EDL, ClipAnalysis, EditMeta, Fx, Graphic, MusicSpec, RampPoint, Segment, SegmentAudio, SoundLayer,
                      Transition)
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
    frame_center: tuple[float, float] = Field((0.5, 0.5), description="Where the crop sits (keeps the subject in 9:16).")
    graphics: list[Graphic] = Field(default_factory=list, description="Motion graphics drawn into this shot.")


class IntroSpec(BaseModel):
    """A five-second cold open before the film: the crew at work at hyper-speed, "Done.", then this title."""

    title: str = "Can Claude edit?"
    header: str = "CLAUDE CODE · VIDEO EDITING TRIALS"
    take: str = "TAKE 2"


class Template(BaseModel):
    name: str
    idea: str = ""
    music: MusicSpec
    look: str = "natural"
    stabilize: bool = False
    fade_in: float = 0.0
    fade_out: float = 1.0
    vertical: bool = Field(False, description="9:16 (Reels) instead of 16:9.")
    slots: list[Slot]
    music_credit: str | None = Field(None, description="Credit line for the outro, e.g. how the music was made.")
    intro: IntroSpec | None = None
    sounds: list[SoundLayer] = Field(default_factory=list, description=(
        "Sound design on the timeline: a clip's own sound ('clip:c08' or a clip id) or a built-in effect "
        "('sfx:whoosh', 'sfx:impact', ...)."))


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


def _resolve_tiles(graphics: list[Graphic], clips: dict, shorts: dict[str, str]) -> list[Graphic]:
    """Panel tiles name clips by short id ('c40@52.6'); the renderer needs their files."""
    out = []
    for g in graphics:
        if g.kind == "panels":
            tokens = []
            for token in (t.strip() for t in g.text.split("|") if t.strip()):
                cid, _, sec = token.rpartition("@")
                clip = clips.get(shorts.get(cid, cid))
                tokens.append(f"{clip.proxy or clip.path}@{sec}" if clip else token)  # a tile needs no 4K
            g = g.model_copy(update={"text": "|".join(tokens)})
        out.append(g)
    return out


def frames_on_screen(slots: list[Slot], fps: int) -> tuple[list[int], list[int]]:
    """Output frames for each slot's shot (transitions included) and the frames of its transition in, so
    every cut lands on the frame nearest its slot boundary. Rounding each shot on its own lets the error
    add up (a 0.25 s stutter is 7.5 frames); rounding the boundaries keeps every cut within half a frame
    of its beat. A transition gets at most half of the shorter slot beside it, as the timeline allows."""
    bounds = [round(s.start * fps) for s in slots] + [round(slots[-1].end * fps)]
    trans = [0] * (len(slots) + 1)
    for k in range(1, len(slots)):
        if slots[k].transition_in.type != "cut":
            room = min(bounds[k] - bounds[k - 1], bounds[k + 1] - bounds[k]) // 2
            trans[k] = min(round(slots[k].transition_in.duration * fps), room)
    before = [t // 2 for t in trans]  # the part of each transition that plays before its boundary
    frames = [(bounds[k + 1] + trans[k + 1] - before[k + 1]) - (bounds[k] - before[k]) for k in range(len(slots))]
    return frames, trans[:-1]


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
    frames, trans = frames_on_screen(slots, fps)
    for k in order:
        slot = slots[k]
        need = _span(slot, frames[k] / fps)
        slow = min([p.speed for p in slot.ramp] or [slot.speed]) < 0.95
        if slot.clip:  # chosen by hand
            clip = clips[shorts.get(slot.clip, slot.clip)]
            start = max(0.0, min(slot.src_in or 0.0, clip.duration - need))  # a longer slot may start earlier
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
            id=slot.id, clip_id=pick["clip"].clip_id, src_in=round(start, 4), src_out=round(start + need, 4),
            framing="fill" if template.vertical else "auto",  # full-bleed 9:16; frame_center keeps the subject
            speed=slot.speed, ramp=slot.ramp, push=slot.push, fx=slot.fx, frame_center=slot.frame_center,
            graphics=_resolve_tiles(slot.graphics, clips, shorts),
            transition_in=Transition(type=slot.transition_in.type, duration=trans[k] / fps) if trans[k] else Transition(),
            out_sync="free",
            audio=SegmentAudio(mode="mute"), role=f"energy {slot.energy}",
            rationale=(pick["text"] if pick["text"].startswith("chosen") else
                       f"PLACEHOLDER for '{slot.want}': {pick['text'][:90]}")))
    meta = EditMeta(title=template.name, width=width, height=height, fps=fps, look=template.look,
                    fade_in=template.fade_in, fade_out=template.fade_out, snap_to_beats=False,
                    stabilize=template.stabilize)
    sounds = [layer.model_copy(update={"source": f"clip:{shorts.get(layer.source[5:], layer.source[5:])}"})
              if layer.source.startswith("clip:") else layer for layer in template.sounds]
    return EDL(project_dir=str(project.clips_dir), meta=meta, segments=segments, music=template.music,
               sounds=sounds)


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


def main_drop(music: MusicAnalysis, start: float = 0.0, end: float | None = None) -> float | None:
    """The drop into the song's full-energy part: the first one (between start and end) followed by 8 s at
    peak energy, else the one into the loudest stretch (a song may also 'drop' from its intro into a build)."""
    import numpy as np

    energy = np.asarray(music.energy)
    drops = [d for d in music.drops if start <= d <= (music.duration if end is None else end)]
    if not drops:
        return None
    after = {d: float(energy[int(d):int(d) + 8].mean()) if int(d) < len(energy) else 0.0 for d in drops}
    return next((d for d in drops if after[d] >= 0.7), max(drops, key=lambda d: after[d]))


def audible_end(music: MusicAnalysis, floor: float = 0.1) -> float:
    """Where the song has died away (energy per second under the floor from there on)."""
    loud = [i for i, e in enumerate(music.energy) if e >= floor]
    return min(music.duration, loud[-1] + 1.0) if loud else music.duration


def retarget(template: Template, old: MusicAnalysis, new: MusicAnalysis, new_path: str,
             anchor: str | None = None) -> tuple[Template, list[str]]:
    """The same edit on another song. Slot boundaries are counted in beats from the anchor slot (default:
    the one on the old song's drop), which lands on the new song's main drop. Before the drop the slots
    keep their beat counts when the song has room, else squeeze into the beats there are; after it they
    stretch or squeeze (0.75-1.5x) to end with the song. Every cut stays on a whole beat and hits move
    with their slot; slots past the new song's end are shortened or dropped."""
    import numpy as np

    old_period = float(np.median(np.diff(old.beats)))
    new_beats = np.asarray(new.beats, dtype=float)
    new_period = float(np.median(np.diff(new_beats)))
    slots = template.slots
    if anchor is not None:
        anchor_slot = next((s for s in slots if s.id == anchor), None)
        if anchor_slot is None:
            raise ValueError(f"no slot {anchor!r} in the template")
    else:
        old_drop = main_drop(old, template.music.start, template.music.start + slots[-1].end)
        if old_drop is None:
            raise ValueError("the old song has no drop to line up; name an anchor slot")
        anchor_slot = min(slots, key=lambda s: abs(s.start - (old_drop - template.music.start)))
    drop = main_drop(new)
    if drop is None:
        raise ValueError("the new song has no drop to line up with")
    drop_beat = int(np.argmin(np.abs(new_beats - drop)))

    rel = [(t - anchor_slot.start) / old_period for t in [s.start for s in slots] + [slots[-1].end]]
    pre, post = max(-rel[0], 0.0), max(rel[-1], 0.0)
    end_beat = int(np.searchsorted(new_beats, audible_end(new), side="right")) - 1
    pre_scale = min(1.0, drop_beat / pre) if pre else 1.0
    post_scale = min(1.5, max(0.75, (end_beat - drop_beat) / post)) if post else 1.0
    idx = [drop_beat + int(np.floor(r * (pre_scale if r < 0 else post_scale) + 0.5)) for r in rel]
    for k in range(1, len(idx)):  # every slot keeps at least one beat
        idx[k] = max(idx[k], idx[k - 1] + 1)

    def beat_time(i: int) -> float:
        if i >= len(new_beats):
            return float(new_beats[-1] + (i - len(new_beats) + 1) * new_period)
        return float(new_beats[i])

    times = [beat_time(i) for i in idx]
    start, end = times[0], new.duration - times[0]
    notes, moved = [], []
    for k, slot in enumerate(slots):
        s0, full = times[k] - start, times[k + 1] - start
        s1 = min(full, end)
        if s1 < full - 0.01:
            if s1 - s0 < 1.0:
                notes.append(f"{slot.id}: dropped, the new song ends at {end:.1f}s")
                if moved:
                    moved[-1] = moved[-1].model_copy(update={"end": round(end, 3)})
                continue
            notes.append(f"{slot.id}: shortened to end with the song ({s1 - s0:.1f}s)")
        ratio = (idx[k + 1] - idx[k]) / max(rel[k + 1] - rel[k], 1e-6)
        fx = [f.model_copy(update={"at": round(int(np.floor(f.at / old_period * ratio + 0.5)) * new_period, 3)})
              for f in slot.fx]
        moved.append(slot.model_copy(update={"start": round(s0, 3), "end": round(s1, 3), "fx": fx}))
    music = template.music.model_copy(update={"path": new_path, "start": round(start, 3)})
    notes.insert(0, f"{anchor_slot.id} on the drop at {drop:.2f}s; the song starts at {start:.2f}s; tempo "
                    f"{60 / old_period:.1f} -> {60 / new_period:.1f} BPM; before the drop x{pre_scale:.2f}, "
                    f"after x{post_scale:.2f}")
    return template.model_copy(update={"music": music, "slots": moved}), notes
