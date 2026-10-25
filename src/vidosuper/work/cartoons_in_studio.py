"""Cartoons (motion/cartoon.py) made as films of their own.

A cartoon starts a new film at once (it shows in their films while it is being made). When it is done, each of its
shots is a clip of that film, its edit list is those shots in order with the music, the narration and the sounds,
and the finished cartoon is version 1: from there it is a film like any other (the timeline, the music, styles,
designed scenes, exports)."""

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


def start(studio, user: dict, story: str, seconds: float = 40.0, shape: str = "9:16", style: str = "",
          narration: Path | None = None, on_progress=None) -> dict:
    """A new film made as a cartoon, in the background. Returns its task (a `work` row of kind 'cartoon')."""
    from ..motion import cartoon

    story = " ".join((story or "").split())
    if len(story) < 10 and narration is None:
        raise ValueError("Tell the story (a few sentences at least), or give a narration recording.")
    if shape not in cartoon.SHAPES:
        raise ValueError(f"The shape must be one of {', '.join(cartoon.SHAPES)}.")
    if not 8 <= seconds <= 300:
        raise ValueError("A cartoon is 8 seconds to 5 minutes long.")
    shapes = shape if shape in ("9:16", "16:9") else "9:16"
    p = studio.create_project(user, "A cartoon", shapes, float(seconds), story[:900], kind="montage")
    studio._set_info(p, cartoon={"shape": shape, "style": style})
    wid = "k" + secrets.token_hex(6)
    now = time.time()
    studio.db.run("INSERT INTO work(id, project_id, user_id, kind, status, request, key, created, updated) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", (wid, p["id"], user["id"], "cartoon", "running", story[:2000],
                                                 f"{shape} {seconds:g}s", now, now))
    threading.Thread(target=_make, args=(studio, user, p, wid, story, seconds, shape, style, narration, on_progress),
                     daemon=True, name=f"cartoon-{wid}").start()
    row = studio.db.one("SELECT * FROM work WHERE id=?", (wid,))
    return {**row, "film": p["id"]}


def _make(studio, user, p, wid, story, seconds, shape, style, narration, on_progress) -> None:
    from ..motion import cartoon, scenes
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
        result = cartoon.make(story, work.root / "cartoon" / wid, seconds, shape, style, narration,
                              library=studio.s.home / "sfx", log=progress)
        progress("Making it a film you can edit")
        version = register(studio, p, result, shape)
        scores = [s["score"] for s in result["shots"] if s.get("score") is not None]
        db.run("UPDATE work SET status='done', say=?, updated=? WHERE id=?",
               (f"'{result['board']['title']}' is made: {len(result['shots'])} shots, version {version} of its film"
                + (f"; the director scored its shots {min(scores)} to {max(scores)}/10" if scores else "") + ".",
                time.time(), wid))
        db.event(p["id"], f"Cartoon made: {result['board']['title']}", "info")
    except Exception as exc:  # noqa: BLE001 - the agent and Cello hear it plainly
        log.warning("cartoon %s failed", wid, exc_info=True)
        db.run("UPDATE work SET status='failed', say=?, updated=? WHERE id=?",
               (f"The cartoon couldn't be made: {type(exc).__name__}: {str(exc)[:300]}", time.time(), wid))


def register(studio, p: dict, result: dict, shape: str) -> int:
    """The made cartoon as the film's first version: its shots as the film's clips, its edit list, its plan."""
    from ..ingest import ingest_folder
    from ..motion import cartoon
    from ..schemas import EDL, EditMeta, MusicSpec, Segment, SegmentAudio, SoundLayer
    from ..studio import versions
    from ..studio.brain.schemas import DesignPlan, PlanShot, PoolMoment
    from ..studio.storage import workdir

    db = studio.db
    work = workdir(studio.s, p["user_id"], p["id"]).ensure()
    board = result["board"]
    slug = re.sub(r"[^a-z0-9]+", "-", board["title"].lower()).strip("-")[:40] or "cartoon"
    width, height = cartoon.SHAPES[shape]
    named: dict[str, dict] = {}
    for s in result["shots"]:
        name = f"{slug}-shot{s['n']:02d}.mp4"
        dst = work.clips / name
        if not dst.exists():
            try:
                dst.hardlink_to(s["picture"])
            except OSError:
                import shutil

                shutil.copyfile(s["picture"], dst)
        named[name] = s
        cid = "c" + secrets.token_hex(6)
        db.run("INSERT INTO clips(id, project_id, name, file, bytes, seconds, width, height, created) "
               "VALUES (?,?,?,?,?,?,?,?,?)", (cid, p["id"], f"Shot {s['n']}", name, dst.stat().st_size,
                                              round(s["end"] - s["start"], 3), width, height, time.time()))
    project, clips = ingest_folder(work.clips, studio.cfg)
    by_file = {Path(c.rel_path).name: c.clip_id for c in clips}
    segments = [Segment(id=f"s{s['n']:02d}", clip_id=by_file[name], src_in=0.0, src_out=round(s["end"] - s["start"], 3),
                        audio=SegmentAudio(mode="mute"), framing="fill")
                for name, s in sorted(named.items(), key=lambda kv: kv[1]["n"])]
    sounds = [SoundLayer(source=x["file"], at=x["at"], duration=x["seconds"], gain_db=x["gain"])
              for x in result.get("sounds") or []]
    total = float(board["seconds"])
    if result.get("narration"):
        sounds.insert(0, SoundLayer(source=str(result["narration"]), at=0.0, duration=total, gain_db=0.0,
                                    fade_in=0.0, fade_out=0.3))
    edl = EDL(project_dir=str(work.clips), segments=segments,
              meta=EditMeta(title=board["title"], platform="tiktok" if height > width else "youtube", width=width,
                            height=height, fps=cartoon.FPS, snap_to_beats=False),
              music=MusicSpec(path=str(result["music"]), fade_out=2.0) if result.get("music") else None,
              sounds=sounds, notes=f"A cartoon: {board.get('logline', '')}")
    short = {cid: f"c{k + 1:02d}" for k, cid in enumerate(c.clip_id for c in clips if c.usable)}
    bpm = float(board["music"].get("bpm") or 100)
    plan = DesignPlan(name=board["title"], idea=board.get("logline", "") or board["title"],
                      pool=[PoolMoment(key=f"shot{seg.id[1:]}", clip=short.get(seg.clip_id, seg.clip_id), start=0.0,
                                       end=seg.src_out) for seg in segments],
                      plan=[PlanShot(kind="hold", beats=round(seg.src_out * bpm / 60.0, 3), key=f"shot{seg.id[1:]}")
                            for seg in segments],
                      notes="The cartoon's shots, as its board cut them.")
    plan_id = db.run("INSERT INTO plans(project_id, kind, pitch_id, data, created) VALUES (?,?,?,?,?)",
                     (p["id"], "cartoon", None, plan.model_dump_json(), time.time()))
    version = int(db.value("SELECT MAX(version) FROM renders WHERE project_id=? AND kind='final'", (p["id"],), 0) or 0) + 1
    name = f"v{version}-{shape.replace(':', 'x')}"
    import shutil

    shutil.copyfile(result["video"], work.out / f"{name}.mp4")
    versions.edl_path(work.out, version, shape).write_text(edl.model_dump_json(indent=1), encoding="utf-8")
    from ..ffmpeg import ffmpeg_bin

    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{min(1.0, total / 3):.2f}", "-i",
                    str(work.out / f"{name}.mp4"), "-frames:v", "1", "-q:v", "3", str(work.out / f"{name}.jpg")],
                   capture_output=True)
    db.run("INSERT INTO renders(project_id, plan_id, version, kind, shape, file, seconds, render_seconds, findings, "
           "created, base, changes, shots, style) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
           (p["id"], plan_id, version, "final", shape, f"{name}.mp4", total, 0.0, "[]", time.time(), None,
            json.dumps([f"The cartoon '{board['title']}', {len(segments)} shots"]), None, None))  # its strip: when edited
    db.run("UPDATE projects SET version=?, base=NULL, stage='done', title=?, updated=? WHERE id=?",
           (version, board["title"][:60], time.time(), p["id"]))
    return version
