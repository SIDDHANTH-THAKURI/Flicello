"""The editor: the film on a timeline to change by hand (the editor page, frontend/src/editor), and what Cello and the
work agent change on it the same way (studio/handedit.py).

- `view`: everything the timeline shows: the shots (when each plays, the frames it shows, how far it can be trimmed
  either way before its clip runs out, its speed, sound and framing), the graphics (each with its film seconds), the
  music (the song's waveform, beats, bars and sections, and how it is edited), the words said (to snap cuts to), and
  what the player plays.
- `apply`: exact edits made on what is on screen, at once (no queue): a new draft, which plays straight away.
- undo and redo walk the drafts made since the version (Studio.draft_undo / draft_redo).
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import numpy as np

PEAKS_PER_SECOND = 50  # waveform resolution: fine enough to see a word or a kick when zoomed in
SPRITE_H = 72  # px: a filmstrip tile's height
SPRITE_COLS = 16
SPRITE_MAX = 720  # frames in one clip's sheet (a long clip gets fewer a second)
_locks: dict[str, threading.Lock] = {}


def _lock(pid: str) -> threading.Lock:
    return _locks.setdefault(pid, threading.Lock())


def peaks(audio: Path, cache: Path) -> list[int]:
    """The loudness outline of a sound file, PEAKS_PER_SECOND values 0-255 (cached beside the work)."""
    from ..ffmpeg import ffmpeg_bin

    key = f"{audio.name}:{audio.stat().st_size}:{int(audio.stat().st_mtime)}"
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("key") == key:
                return data["peaks"]
        except ValueError:
            pass
    rate = PEAKS_PER_SECOND * 80
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(audio), "-vn", "-ac", "1", "-ar", str(rate), "-f",
                          "s16le", "-"], capture_output=True, timeout=300).stdout
    x = np.abs(np.frombuffer(raw, dtype=np.int16).astype(np.float32)) / 32768.0
    n = len(x) // 80
    out: list[int] = []
    if n:
        blocks = x[: n * 80].reshape(n, 80).max(axis=1)
        ref = float(np.percentile(blocks, 99.5)) or 1.0
        out = np.clip(np.sqrt(blocks / ref) * 255, 0, 255).astype(np.uint8).tolist()  # a square-root scale reads
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"key": key, "peaks": out}), encoding="utf-8")
    return out


def sprite_plan(clip) -> dict:
    """How a clip's filmstrip sheet is laid out: frames a second, columns, a tile's size, how many tiles."""
    seconds = max(0.1, float(clip.duration or 0.1))
    fps = round(min(4.0, SPRITE_MAX / seconds), 4)
    w = max(8, int(round(SPRITE_H * (clip.width or 16) / max(1, clip.height or 9) / 2)) * 2)
    return {"fps": fps, "cols": SPRITE_COLS, "w": w, "h": SPRITE_H, "n": max(1, int(seconds * fps) + 1)}


def sprite(studio, project: dict, clip_id: str) -> Path:
    """A clip's filmstrip sheet: its frames, SPRITE_COLS to a row, from its light copy (made once, kept)."""
    from ..ffmpeg import ffmpeg_bin
    from ..workspace import Project
    from .preview import copy_path
    from .storage import workdir

    work = workdir(studio.s, project["user_id"], project["id"])
    clips = Project(work.clips, studio.cfg).clips()
    if clip_id not in clips:
        raise ValueError("No such clip in this film.")
    lay = sprite_plan(clips[clip_id])
    out = work.root / "sprites" / f"{clip_id}-{lay['fps']}-{lay['w']}.jpg"
    if out.exists():
        return out
    src = copy_path(work.root, clip_id)
    src = src if src.exists() else Path(clips[clip_id].path)
    rows = -(-lay["n"] // lay["cols"])
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_suffix(".part.jpg")
    done = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(src), "-vf",
                           f"fps={lay['fps']},scale={lay['w']}:{lay['h']},tile={lay['cols']}x{rows}", "-frames:v", "1",
                           "-q:v", "5", str(part)], capture_output=True, timeout=600)
    if done.returncode != 0 or not part.exists():
        raise ValueError("The clip's frames couldn't be read: " + done.stderr.decode(errors="replace")[-200:])
    part.replace(out)
    return out


def _on_screen(studio, project: dict):
    """What their screen shows: ('draft', its plan, its edit list) when a draft of the version they work on is up,
    else (the version's number, its plan, its edit list); the plan pinned to the frames the edit list shows."""
    from ..schemas import EDL
    from . import versions
    from .storage import workdir

    work = workdir(studio.s, project["user_id"], project["id"])
    version = studio.work_version(project)
    draft = studio.draft_of(project)
    if draft and int(draft.get("base") or 0) == version:
        path = work.out / draft["edl"]
        data = json.loads(path.read_text(encoding="utf-8"))
        tall = data if data["meta"]["height"] > data["meta"]["width"] else None
        plan = versions.pinned(studio.draft_plan(project), data, tall)
        return "draft", plan, EDL.model_validate(data)
    _, plan, data = studio._version_plan(project, version)
    if data is None:
        raise ValueError(f"Version {version}'s edit list isn't here.")
    return str(version), plan, EDL.model_validate(data)


def view(studio, project: dict, user: dict) -> dict:
    """Everything the editor shows of the film on screen (see the module's words)."""
    from ..edit.timeline import build_timeline
    from ..motion.place import speech_words
    from ..music import load_or_analyze
    from ..perceive.prep import load_transcript
    from ..workspace import Project
    from . import versions
    from .brain.context import clip_facts, load_catalog
    from .storage import workdir

    if project["kind"] != "montage":
        raise ValueError("This film is made as scenes (a voice film): change it by talking to Cello.")
    which, plan, edl = _on_screen(studio, project)
    work = workdir(studio.s, project["user_id"], project["id"])
    ws = Project(work.clips, studio.cfg)
    clips = ws.clips()
    timeline = build_timeline(edl, clips)
    fps = timeline.fps
    play = studio.preview_of(project, which)
    facts = clip_facts(load_catalog(ws.root))
    short_of = {f.clip_id: short for short, f in facts.items()}
    seg_at = {item.seg.id: item for item in timeline.items}
    period = None
    shots = []
    for n, (s, ids) in enumerate(zip(plan.plan, versions.slot_ids(plan)), start=1):
        items = [seg_at[i] for i in ids if i in seg_at]
        if not items:
            continue
        first, last = items[0], items[-1]
        seg = first.seg
        start, end = first.start / fps, last.end / fps
        f = facts.get(next((m.clip for m in plan.pool if m.key == s.key), ""))
        rate = seg.speed if not seg.ramp else None
        if period is None and s.beats > 0 and s.kind != "stutter" and len(items) == 1:
            period = (end - start) / s.beats
        played = (end - start) * (rate or 1.0)
        clip_len = clips[seg.clip_id].duration if seg.clip_id in clips else (f.duration if f else played)
        shots.append({
            "n": n, "start": round(start, 3), "end": round(end, 3), "kind": s.kind, "count": s.count,
            "beats": round(s.beats, 4), "clip": seg.clip_id, "short": short_of.get(seg.clip_id, seg.clip_id),
            "src_in": round(seg.src_in, 3), "rate": None if rate is None else round(rate, 4),
            "room_start": round(seg.src_in / rate, 3) if rate else 0.0,
            "room_end": round(max(0.0, clip_len - seg.src_in - played) / rate, 3) if rate else 0.0,
            "speed": s.speed, "gain_db": s.gain_db, "mute": bool(s.mute), "frame_x": s.frame_x, "zoom": s.zoom or 1.0,
            "zoom_center": s.zoom_center or [0.5, 0.5], "flip": bool(s.flip), "push": s.push if s.push is not None else seg.push,
            "blend": s.transition, "blend_seconds": round(first.trans_frames / fps, 3), "keys": s.keys or [],
            "copy": f"/m/{project['id']}/preview/{seg.clip_id}.mp4", "fps": f.fps if f else fps,
            "sound": seg.audio.mode != "mute",
        })
    total = timeline.duration
    # the graphics, each with the film seconds it really plays (its pieces in the edit list), in the plan's order
    spans: dict[int, list[float]] = {}
    for g in edl.motion:
        if g.ref is not None:
            a, b = spans.get(g.ref, [g.start, g.end])
            spans[g.ref] = [min(a, g.start), max(b, g.end)]
    graphics = []
    for k, g in enumerate(plan.graphics):
        if g.kind == "scene":
            a, b = g.at or 0.0, g.until or 0.0
        elif k in spans:
            a, b = spans[k]
        elif g.at is not None:
            a, b = g.at, g.until or g.at + 2.0
        else:
            sh = next((x for x in shots if x["n"] == g.shot), shots[0] if shots else {"start": 0.0})
            a = sh["start"]
            b = min(total, a + max(1.0, g.beats * (period or 0.5)))
        graphics.append({"index": k, "kind": g.kind, "text": g.text, "sub": g.sub, "style": g.style, "scale": g.scale,
                         "place": g.place, "shot": g.shot, "hand": g.at is not None, "start": round(a, 3),
                         "end": round(min(total, b), 3), "drawn": k in spans or g.kind == "scene"})
    # the words said, at their film seconds (cuts snap to them)
    said = {cid: t for cid in {seg.clip_id for seg in edl.segments} if (t := load_transcript(ws, cid)) is not None}
    words = [{"w": w.w, "s": round(w.s, 3), "e": round(w.e, 3)} for w in speech_words(timeline, said)]
    # the music: the song's shape and how the film plays it
    music = None
    if edl.music and edl.music.path and play.get("music"):
        song = Path(edl.music.path)
        song = song if song.exists() else (work.song_path() or song)
        a = load_or_analyze(song, ws.music)
        if len(a.beats) > 2:  # the beat as the film was cut on it
            period = float(np.median(np.diff(np.asarray(a.beats, dtype=float))))
        m = edl.music
        music = {
            "src": play["music"]["src"], "duration": round(a.duration, 3), "tempo": round(a.tempo, 2),
            "beats": [round(b, 3) for b in a.beats], "bars": [round(b, 3) for b in a.downbeats],
            "sections": [{"label": x.label, "start": round(x.start, 3), "end": round(x.end, 3), "energy": round(x.energy, 2)}
                         for x in a.sections],
            "drops": [round(d, 3) for d in a.drops],
            "start": m.start, "jumps": [{"at": j.at, "to": j.to} for j in m.jumps], "gain_db": m.gain_db,
            "fade_in": m.fade_in, "fade_out": m.fade_out,
            "duck_db": m.duck_db if m.duck_db is not None else studio.cfg.render.music_duck_db,
            "points": [{"t": p.t, "db": p.gain_db} for p in m.automation],
            "edit": plan.music.model_dump() if plan.music else None,
            "remake": _remakable(studio, project),
            "peaks": peaks(song, work.root / "peaks" / f"song-{song.stem}.json"),
        }
    from .preview import copy_path

    sounds = {}  # each clip's own sound, as a waveform (from its light copy, which carries it; else the clip itself)
    for cid in {x["clip"] for x in shots}:
        for src in (copy_path(work.root, cid), Path(clips[cid].path) if cid in clips else None):
            if src is None or not src.exists():
                continue
            try:
                found = peaks(src, work.root / "peaks" / f"clip-{cid}.json")
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
            if found:
                sounds[cid] = found
                break
    ids = {short: f.clip_id for short, f in facts.items()}
    over = [{"index": k, "start": o.at, "end": o.until, "short": o.clip, "clip": ids.get(o.clip, ""), "src_in": o.src_in,
             "rate": o.speed, "frame_x": o.frame_x, "zoom": o.zoom,
             "copy": f"/m/{project['id']}/preview/{ids.get(o.clip, '')}.mp4"} for k, o in enumerate(plan.over)]
    footage = [{"short": short, "clip": f.clip_id, "seconds": round(f.duration, 2), "fps": f.fps,
                "moments": [{"start": round(float(m["start"]), 2), "end": round(float(m["end"]), 2),
                             "what": m.get("description") or "", "strength": m.get("strength", 0)} for m in f.moments]}
               for short, f in facts.items()]
    sprites = {cid: {**sprite_plan(clips[cid]), "src": f"/api/p/{project['id']}/sprite/{cid}"}
               for cid in {x["clip"] for x in shots} | {f["clip"] for f in footage} if cid in clips}
    draft = studio.draft_of(project) if which == "draft" else None
    info = studio._info(project)
    return {
        "which": which, "version": studio.work_version(project), "duration": round(total, 3), "fps": fps,
        "w": edl.meta.width, "h": edl.meta.height, "period": round(period or 0.5, 4), "shots": shots,
        "graphics": graphics, "words": words, "music": music, "sounds": sounds, "peaks_per_second": PEAKS_PER_SECOND,
        "sprites": sprites, "over": over, "footage": footage,
        "play": play, "said": (draft or {}).get("said") or [],
        "undo": bool(draft) or bool(info.get("draft_back")), "redo": bool(info.get("draft_fwd")),
        "busy": bool(studio.db.active_job(project["id"])), "stage": project["stage"],
    }


def _remakable(studio, project: dict) -> bool:
    """Whether a stretch of this film's music can be written again (only music made here: Studio.edit_music)."""
    row = studio.beat_row(project, project.get("beat")) if project.get("beat") else None
    return bool(row and (row.get("full_song_id") or (row.get("song_id") and row.get("whole"))
                         or studio._made_here(project, row)))


def apply(studio, user: dict, project: dict, ops: list[dict], said: list[str] | None = None) -> list[str]:
    """Exact edits on what is on screen, made at once as the new draft. Returns what was done, in words."""
    from . import handedit
    from .brain.schemas import DesignPlan

    edits = [handedit.EditOp.model_validate(o) for o in ops][:40]
    if not edits:
        raise ValueError("Nothing to change.")
    with _lock(project["id"]):
        project = studio.db.project(project["id"])
        studio._guard(project, "done")
        if project["kind"] != "montage":
            raise ValueError("This film is made as scenes (a voice film): change it by talking to Cello.")
        which, plan, _ = _on_screen(studio, project)
        pipe = studio._hand_pipeline(project, user)
        ctx = studio._context(pipe, project, user, start_at=plan.start_at)
        try:
            done = handedit.apply(plan, ctx, edits)
        except handedit.EditError as exc:
            raise ValueError(str(exc)) from exc
        lines = [" ".join(str(x).split())[:200] for x in (said or []) if str(x).strip()][:12] or done.lines
        new = DesignPlan.model_validate(done.plan.model_dump())
        studio.lay_out_draft(user, project, pipe, ctx, new, lines, on_draft=which == "draft")
        return done.lines
