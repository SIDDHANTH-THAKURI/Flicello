"""Animated films (motion/animated.py) made as films of their own, like cartoons.

The film shows in their films at once while it is being made. Before any scene is built, the pitch (the story, the
scenes and their lines, the music) is put to them as one of Flicello's questions, in the film's conversation: they
tap "Make it", or say what to change and get the pitch again. When it is done, each scene is a clip of the film, the
music and the sound effects are its sound, and the finished film is version 1: from there it is a film like any
other (the timeline, styles, designed scenes, exports)."""

from __future__ import annotations

import json
import logging
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("vidosuper.work")

GO = re.compile(r"^\s*(make it|go( ahead)?|yes|yep|yeah|ok(ay)?|sure|perfect|looks? (good|great|right)|love it|"
                r"do it|build it|that'?s (it|right|great)|approved?)\b", re.I)
PITCH_WAIT = 7 * 24 * 3600  # a pitch waits a week for its answer


def is_go(text: str) -> bool:
    """Their answer to the pitch means "make it" (anything else is what to change)."""
    return bool(GO.match(text or "")) and not re.search(r"\b(but|change|instead|except|different|not)\b", text or "", re.I)


def start(studio, user: dict, brief: str, seconds: float = 60.0, shape: str = "16:9", kit: str = "",
          on_progress=None) -> dict:
    """A new film made as an animated film, in the background. Returns its task (a `work` row of kind 'animated')."""
    from ..motion import animated, kits

    brief = (brief or "").strip()
    if len(" ".join(brief.split())) < 20:
        raise ValueError("Say what the film is about (a few sentences at least).")
    if shape not in animated.SHAPES:
        raise ValueError(f"The shape must be one of {', '.join(animated.SHAPES)}.")
    if not 15 <= seconds <= 240:
        raise ValueError("An animated film is 15 seconds to 4 minutes long.")
    kit = kit or kits.from_words(brief)
    try:
        kits.folder(kit)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    shapes = shape if shape in ("9:16", "16:9") else "9:16"
    p = studio.create_project(user, "An animated film", shapes, float(seconds), " ".join(brief.split())[:900],
                              kind="montage")
    studio._set_info(p, animated={"shape": shape, "kit": kit})
    wid = "a" + secrets.token_hex(6)
    now = time.time()
    studio.db.run("INSERT INTO work(id, project_id, user_id, kind, status, request, key, created, updated) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", (wid, p["id"], user["id"], "animated", "running", brief[:2000],
                                                 f"{shape} {seconds:g}s {kit}", now, now))
    threading.Thread(target=_make, args=(studio, user, p, wid, brief, seconds, shape, kit, on_progress),
                     daemon=True, name=f"animated-{wid}").start()
    row = studio.db.one("SELECT * FROM work WHERE id=?", (wid,))
    return {**row, "film": p["id"]}


def pitch_text(pitch: dict) -> str:
    """The pitch in plain words: the story, each scene with its time and its lines, the music."""
    lines = [f"\"{pitch['title']}\": {pitch['logline']} ({pitch['seconds']:.0f} s)."]
    for s in pitch["scenes"]:
        said = " / ".join(f"\"{x}\"" for x in s["lines"])
        lines.append(f"{int(s['start']) // 60}:{int(s['start']) % 60:02d} {s['id']}: {s['what'][:220]}"
                     + (f" Words: {said}." if said else ""))
    if pitch.get("music"):
        drop = f", its big moment at {pitch['drop']:.0f} s" if pitch.get("drop") is not None else ""
        lines.append(f"Music: an original track made for it{drop} (listen: {pitch['music']}).")
    return "\n".join(lines)


def _pitcher(studio, user: dict, p: dict, wid: str):
    """The pitch put to them as a question in the film's conversation; their answer: None (make it) or what to change."""
    from ..studio import agent
    from . import asks

    rounds = {"n": 0}

    def put(key: str, question: str, options: list[str], say: str) -> dict | None:
        row = asks.ask(studio, user, p, key, question, options, say)
        if row is not None:
            try:
                agent.post(studio.db, studio.db.project(p["id"]), "editor", "text",
                           {"text": "\n".join(x for x in (say, question) if x)[:1200], "cards": [],
                            "work": row["id"], "options": options})
            except Exception:  # noqa: BLE001 - Cello still asks it
                log.warning("couldn't post the pitch to the film page", exc_info=True)
        return row

    def wait(row: dict) -> str:
        deadline = time.time() + PITCH_WAIT
        while time.time() < deadline:
            got = studio.db.one("SELECT status, answer FROM work WHERE id=?", (row["id"],))
            mine = studio.db.one("SELECT status FROM work WHERE id=?", (wid,))
            if mine and mine["status"] == "stopped":
                raise RuntimeError("stopped while waiting for the answer to the pitch")
            if got and got["status"] != "asking":
                return (got["answer"] or "").strip()
            time.sleep(2)
        raise RuntimeError("the pitch waited a week without an answer")

    def ask(pitch: dict) -> str | None:
        rounds["n"] += 1
        key = f"animated-pitch:{wid}:{rounds['n']}"
        studio.db.run("UPDATE work SET progress=?, updated=? WHERE id=?",
                      ("Waiting for your OK on the pitch (the story and the music)", time.time(), wid))
        row = put(key, "Shall I make it like this, or change something?", ["Make it", "Change something"],
                  "Here's the pitch for the animated film:\n" + pitch_text(pitch))
        if row is None:
            return None
        answer = wait(row)
        if not answer or is_go(answer):
            return None
        if re.fullmatch(r"\s*change something\s*", answer, re.I):  # a tap, without what: ask what
            row = put(key + ":what", "What should I change?", [], "")
            answer = wait(row) if row is not None else ""
            return None if not answer or is_go(answer) else answer
        return answer

    return ask


def _make(studio, user, p, wid, brief, seconds, shape, kit, on_progress) -> None:
    from ..motion import animated, scenes
    from ..studio.storage import workdir

    db = studio.db

    def progress(msg: str) -> None:
        db.run("UPDATE work SET progress=?, updated=? WHERE id=?", (" ".join(str(msg).split())[:300], time.time(), wid))
        db.run("UPDATE projects SET updated=? WHERE id=?", (time.time(), p["id"]))
        if on_progress:
            on_progress(msg)

    try:
        work = workdir(studio.s, p["user_id"], p["id"]).ensure()
        scenes.VIA = "claude_code" if studio.s.owner_brain == "claude_code" else "api"
        result = animated.make(brief, work.root / "animated" / wid, seconds, shape, kit, log=progress,
                               pitch=_pitcher(studio, user, p, wid), library=studio.s.home / "sfx")
        progress("Making it a film you can edit")
        version = register(studio, p, result, shape)
        db.run("UPDATE work SET status='done', say=?, updated=? WHERE id=?",
               (f"'{result['plan']['title']}' is made: {len(result['scenes'])} scenes, version {version} of its film.",
                time.time(), wid))
        db.event(p["id"], f"Animated film made: {result['plan']['title']}", "info")
    except Exception as exc:  # noqa: BLE001 - the agent and Cello hear it plainly
        log.warning("animated film %s failed", wid, exc_info=True)
        db.run("UPDATE work SET status='failed', say=?, updated=? WHERE id=?",
               (f"The animated film couldn't be made: {type(exc).__name__}: {str(exc)[:300]}", time.time(), wid))


def register(studio, p: dict, result: dict, shape: str) -> int:
    """The made film as its first version: its scenes as the film's clips, its edit list, its plan."""
    import shutil

    from ..ffmpeg import ffmpeg_bin
    from ..ingest import ingest_folder
    from ..motion import animated
    from ..schemas import EDL, EditMeta, MusicSpec, Segment, SegmentAudio, SoundLayer
    from ..studio import versions
    from ..studio.brain.schemas import DesignPlan, PlanShot, PoolMoment
    from ..studio.storage import workdir

    db = studio.db
    work = workdir(studio.s, p["user_id"], p["id"]).ensure()
    plan = result["plan"]
    slug = re.sub(r"[^a-z0-9]+", "-", plan["title"].lower()).strip("-")[:40] or "animated"
    width, height = animated.SHAPES[shape]
    named: dict[str, dict] = {}
    for k, s in enumerate(result["scenes"], 1):
        name = f"{slug}-{k:02d}-{s['id']}.mp4"
        dst = work.clips / name
        if not dst.exists():
            try:
                dst.hardlink_to(s["picture"])
            except OSError:
                shutil.copyfile(s["picture"], dst)
        named[name] = {**s, "n": k}
        db.run("INSERT INTO clips(id, project_id, name, file, bytes, seconds, width, height, created) "
               "VALUES (?,?,?,?,?,?,?,?,?)", ("c" + secrets.token_hex(6), p["id"], f"Scene {k}: {s['id']}", name,
                                              dst.stat().st_size, round(s["end"] - s["start"], 3), width, height,
                                              time.time()))
    project, clips = ingest_folder(work.clips, studio.cfg)
    by_file = {Path(c.rel_path).name: c.clip_id for c in clips}
    segments = [Segment(id=f"s{s['n']:02d}", clip_id=by_file[name], src_in=0.0, src_out=round(s["end"] - s["start"], 3),
                        audio=SegmentAudio(mode="mute"), framing="fill")
                for name, s in sorted(named.items(), key=lambda kv: kv[1]["n"])]
    total = float(result["timing"]["seconds"])
    sounds = [SoundLayer(source=str(result["effects"]), at=0.0, duration=total, gain_db=0.0)] \
        if result.get("effects") and Path(result["effects"]).exists() else []
    edl = EDL(project_dir=str(work.clips), segments=segments,
              meta=EditMeta(title=plan["title"], platform="tiktok" if height > width else "youtube", width=width,
                            height=height, fps=animated.FPS, snap_to_beats=False),
              music=MusicSpec(path=str(result["music"]), fade_out=0.5) if result.get("music") else None,
              sounds=sounds, notes=f"An animated film: {plan.get('logline', '')}")
    short = {cid: f"c{k + 1:02d}" for k, cid in enumerate(c.clip_id for c in clips if c.usable)}
    bpm = float(plan["music"].get("bpm") or 100)
    design = DesignPlan(name=plan["title"], idea=plan.get("logline", "") or plan["title"],
                        pool=[PoolMoment(key=f"scene{seg.id[1:]}", clip=short.get(seg.clip_id, seg.clip_id), start=0.0,
                                         end=seg.src_out) for seg in segments],
                        plan=[PlanShot(kind="hold", beats=round(seg.src_out * bpm / 60.0, 3), key=f"scene{seg.id[1:]}")
                              for seg in segments],
                        notes="The animated film's scenes, as its director timed them on the music.")
    plan_id = db.run("INSERT INTO plans(project_id, kind, pitch_id, data, created) VALUES (?,?,?,?,?)",
                     (p["id"], "animated", None, design.model_dump_json(), time.time()))
    version = int(db.value("SELECT MAX(version) FROM renders WHERE project_id=? AND kind='final'", (p["id"],), 0) or 0) + 1
    name = f"v{version}-{shape.replace(':', 'x')}"
    shutil.copyfile(result["video"], work.out / f"{name}.mp4")
    versions.edl_path(work.out, version, shape).write_text(edl.model_dump_json(indent=1), encoding="utf-8")
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{min(2.0, total / 3):.2f}", "-i",
                    str(work.out / f"{name}.mp4"), "-frames:v", "1", "-q:v", "3", str(work.out / f"{name}.jpg")],
                   capture_output=True)
    db.run("INSERT INTO renders(project_id, plan_id, version, kind, shape, file, seconds, render_seconds, findings, "
           "created, base, changes, shots, style) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
           (p["id"], plan_id, version, "final", shape, f"{name}.mp4", total, 0.0, "[]", time.time(), None,
            json.dumps([f"The animated film '{plan['title']}', {len(segments)} scenes"]), None, None))
    db.run("UPDATE projects SET version=?, base=NULL, stage='done', title=?, updated=? WHERE id=?",
           (version, plan["title"][:60], time.time(), p["id"]))
    return version
