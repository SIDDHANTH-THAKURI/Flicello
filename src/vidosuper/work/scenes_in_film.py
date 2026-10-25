"""Designed scenes (motion/scenes.py) laid into the film.

A scene is designed on a stretch of the version they work on: its words, its frames, the person in it. The finished
stretch (the footage with the scene on it and its sounds) is spliced into a new version, frame for frame; the scene is
kept in the film's plan (a `scene` graphic pointing at its folder), so when the film is made again it is laid back in,
as long as the cut under it is the same (else it is left out and said, to be designed again on the new cut)."""

from __future__ import annotations

import json
import logging
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("vidosuper.work")


def _words(studio, project: dict, version: int, shape: str) -> list[tuple[float, float, str]]:
    from ..edit.timeline import build_timeline
    from ..motion.place import speech_words
    from ..perceive.prep import load_transcript
    from ..schemas import EDL
    from ..studio import versions
    from ..studio.storage import workdir
    from ..workspace import Project

    work = workdir(studio.s, project["user_id"], project["id"])
    edl = EDL.model_validate_json(versions.edl_path(work.out, version, shape).read_text(encoding="utf-8"))
    ws = Project(work.clips, studio.cfg)
    said = {cid: t for cid in {seg.clip_id for seg in edl.segments} if (t := load_transcript(ws, cid)) is not None}
    return [(round(w.s, 3), round(w.e, 3), w.w) for w in speech_words(build_timeline(edl, ws.clips()), said)]


def signature(edl_path: Path, start: float, end: float) -> list:
    """What the film is in [start, end): the shots that make it (clip, source in and out, place, speed, framing), the
    graphics and sounds over it, the music and the look. A designed scene is laid on the finished picture with its
    sound, so it is laid back in only while all of these are as they were when it was designed."""
    from ..schemas import EDL

    edl = EDL.model_validate_json(edl_path.read_text(encoding="utf-8"))

    def over(a: float, b: float) -> bool:
        return a < end and b > start

    out, t = [], 0.0
    for seg in edl.segments:
        length = (seg.src_out - seg.src_in) / max(seg.speed or 1.0, 1e-6)
        if over(t, t + length):
            out.append([seg.clip_id, round(seg.src_in, 2), round(seg.src_out, 2), round(t, 2), seg.speed, seg.zoom,
                        seg.zoom_center, seg.frame_center, seg.flip])
        t += length
    out.append(["graphics", *[[g.kind, g.text, g.sub, g.style, g.scale, g.place, g.accent, round(g.start, 2),
                               round(g.end, 2)] for g in edl.motion if over(g.start, g.end)]])
    out.append(["sounds", *[[x.source, round(x.at, 2), round(x.duration, 2), x.gain_db] for x in edl.sounds
                            if over(x.at, x.at + x.duration)]])
    out.append(["music", edl.music.model_dump(mode="json") if edl.music else None, edl.meta.look])
    return json.loads(json.dumps(out))  # as it reads back from meta.json (tuples as lists)


def splice(film: Path, scene: Path, start: float, end: float, out: Path) -> Path:
    """The film with [start, end) replaced by the scene's stretch (picture and sound), frame for frame."""
    from ..ffmpeg import ffmpeg_bin

    parts, labels = [], []
    if start > 0.02:
        parts += [f"[0:v]trim=0:{start:.4f},setpts=PTS-STARTPTS[v0]", f"[0:a]atrim=0:{start:.4f},asetpts=PTS-STARTPTS[a0]"]
        labels.append("[v0][a0]")
    parts += ["[1:v]setpts=PTS-STARTPTS[v1]", "[1:a]asetpts=PTS-STARTPTS[a1]"]
    labels.append("[v1][a1]")
    parts += [f"[0:v]trim=start={end:.4f},setpts=PTS-STARTPTS[v2]", f"[0:a]atrim=start={end:.4f},asetpts=PTS-STARTPTS[a2]"]
    labels.append("[v2][a2]")
    graph = ";".join(parts) + f";{''.join(labels)}concat=n={len(labels)}:v=1:a=1[v][a]"
    part = out.with_name(out.stem + ".part.mp4")
    done = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(film), "-i", str(scene), "-filter_complex",
                           graph, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
                           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(part)],
                          capture_output=True)
    if done.returncode != 0 or not part.exists():
        raise RuntimeError("laying the scene into the film failed: " + done.stderr.decode(errors="replace")[-600:])
    part.replace(out)
    return out


def start(studio, user: dict, project: dict, what: str, start_s: float, end_s: float, style: str = "",
          on_progress=None) -> dict:
    """Design a scene into the film, in the background. Returns its task (a `work` row of kind 'scene')."""
    p = studio.db.project(project["id"])
    version = studio.work_version(p)
    if not version:
        raise ValueError("The film has no version yet to design on: make the first cut first.")
    if not 0 <= start_s < end_s or end_s - start_s < 0.8 or end_s - start_s > 60:
        raise ValueError("Give a stretch of the film between 0.8 and 60 seconds (start before end).")
    wid = "s" + secrets.token_hex(6)
    now = time.time()
    studio.db.run("INSERT INTO work(id, project_id, user_id, kind, status, request, key, created, updated) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", (wid, p["id"], user["id"], "scene", "running",
                                                 " ".join(what.split())[:2000], f"{start_s:.2f}-{end_s:.2f}", now, now))
    threading.Thread(target=_design, args=(studio, user, p, wid, what, start_s, end_s, style, version, on_progress),
                     daemon=True, name=f"scene-{wid}").start()
    return studio.db.one("SELECT * FROM work WHERE id=?", (wid,))


def _design(studio, user, p, wid, what, start_s, end_s, style, version, on_progress) -> None:
    from ..motion import scenes
    from ..studio import versions
    from ..studio.storage import workdir

    db = studio.db

    def progress(msg: str) -> None:
        db.run("UPDATE work SET progress=?, updated=? WHERE id=?", (" ".join(str(msg).split())[:300], time.time(), wid))
        if on_progress:
            on_progress(msg)

    try:
        shape = studio.shapes(p)[0]
        work = workdir(studio.s, p["user_id"], p["id"])
        film = work.out / f"v{version}-{shape.replace(':', 'x')}.mp4"
        if not film.exists():
            raise ValueError(f"Version {version}'s video isn't here.")
        end_s = min(end_s, _duration(film))
        folder = work.root / "scenes" / wid
        scenes.VIA = "claude_code" if studio.s.owner_brain == "claude_code" else "api"
        words = _words(studio, p, version, shape)
        result = scenes.make(film, words, start_s, end_s, what, folder, style=style, rounds=2, log=progress,
                             library=studio.s.home / "sfx")
        meta = {"id": wid, "what": what, "style": style, "start": round(start_s, 3), "end": round(end_s, 3),
                "shape": shape, "version": version, "video": str(Path(result["video"]).relative_to(work.root)),
                "signature": signature(versions.edl_path(work.out, version, shape), start_s, end_s),
                "rounds": result["rounds"]}
        (folder / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        progress("Laying the scene into the film")
        new = _new_version(studio, p, version, shape, meta, work)
        score = result["rounds"][-1]["score"] if result["rounds"] else None
        db.run("UPDATE work SET status='done', say=?, updated=? WHERE id=?",
               (f"The scene is in version {new} ({_mmss(start_s)} to {_mmss(end_s)})"
                + (f"; the reviewer scored it {score}/10" if score is not None else "") + ".", time.time(), wid))
        db.event(p["id"], f"Designed scene in version {new}: {what[:120]}", "info")
    except Exception as exc:  # noqa: BLE001 - the agent and Cello hear it plainly
        log.warning("scene %s failed", wid, exc_info=True)
        db.run("UPDATE work SET status='failed', say=?, updated=? WHERE id=?",
               (f"The scene couldn't be made: {type(exc).__name__}: {str(exc)[:300]}", time.time(), wid))


def _new_version(studio, p: dict, version: int, shape: str, meta: dict, work) -> int:
    """A new version: the one designed on, with the scene in it (its plan, shots and score carried over)."""
    from ..studio import versions
    from ..studio.brain.schemas import DesignPlan, PlanGraphic

    db = studio.db
    base = db.one("SELECT * FROM renders WHERE project_id=? AND kind='final' AND version=? ORDER BY id LIMIT 1",
                  (p["id"], version))
    plan_row = db.one("SELECT * FROM plans WHERE id=?", (base["plan_id"],))
    plan = DesignPlan.model_validate_json(plan_row["data"])
    plan.graphics = [*plan.graphics, PlanGraphic(kind="scene", scene=meta["id"], at=meta["start"], until=meta["end"],
                                                 text=meta["what"][:80])]
    plan_id = db.run("INSERT INTO plans(project_id, kind, pitch_id, data, created) VALUES (?,?,?,?,?)",
                     (p["id"], "scene", plan_row["pitch_id"], plan.model_dump_json(), time.time()))
    new = int(db.value("SELECT MAX(version) FROM renders WHERE project_id=? AND kind='final'", (p["id"],), 0)) + 1
    name = f"v{new}-{shape.replace(':', 'x')}"
    splice(work.out / f"v{version}-{shape.replace(':', 'x')}.mp4", work.root / meta["video"], meta["start"], meta["end"],
           work.out / f"{name}.mp4")
    shutil.copyfile(versions.edl_path(work.out, version, shape), versions.edl_path(work.out, new, shape))
    poster = work.out / f"v{version}-{shape.replace(':', 'x')}.jpg"
    if poster.exists():
        shutil.copyfile(poster, work.out / f"{name}.jpg")
    db.run("INSERT INTO renders(project_id, plan_id, version, kind, shape, file, seconds, render_seconds, findings, "
           "created, base, changes, shots, style) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
           (p["id"], plan_id, new, "final", shape, f"{name}.mp4", base["seconds"], 0.0, base["findings"] or "[]",
            time.time(), version, json.dumps([f"A designed scene, {_mmss(meta['start'])} to {_mmss(meta['end'])}: "
                                              f"{meta['what'][:160]}"]), base["shots"], base["style"]))
    for other in studio.shapes(p)[1:]:  # designed on one shape: the others come along as they were (and say so)
        was, now = f"v{version}-{other.replace(':', 'x')}", f"v{new}-{other.replace(':', 'x')}"
        row = db.one("SELECT * FROM renders WHERE project_id=? AND kind='final' AND version=? AND shape=? LIMIT 1",
                     (p["id"], version, other))
        if row is None or not (work.out / f"{was}.mp4").exists():
            continue
        for ext in (".mp4", ".jpg"):
            if (work.out / f"{was}{ext}").exists():
                shutil.copyfile(work.out / f"{was}{ext}", work.out / f"{now}{ext}")
        shutil.copyfile(versions.edl_path(work.out, version, other), versions.edl_path(work.out, new, other))
        db.run("INSERT INTO renders(project_id, plan_id, version, kind, shape, file, seconds, render_seconds, findings, "
               "created, base, changes, shots, style) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (p["id"], plan_id, new, "final", other, f"{now}.mp4", row["seconds"], 0.0, row["findings"] or "[]",
                time.time(), version, json.dumps([f"The designed scene is in the {shape} cut only: ask for it in this "
                                                  "one too and it is designed for this frame"]), row["shots"],
                row["style"]))
    db.run("UPDATE projects SET version=?, base=NULL, stage='done', updated=? WHERE id=?", (new, time.time(), p["id"]))
    ready = studio._info(p).get("ready") or {}
    if str(version) in ready:
        ready[str(new)] = ready[str(version)]
        studio._set_info(p, ready=ready)
    return new


def lay_back(studio, project: dict, plan, version: int, shape: str, film: Path) -> list[str]:
    """After the film is made again: lay each of its designed scenes back in, where the cut under it is unchanged.
    Returns what was left out, said plainly."""
    from ..studio import versions
    from ..studio.storage import workdir

    work = workdir(studio.s, project["user_id"], project["id"])
    notes = []
    for g in [g for g in plan.graphics if g.kind == "scene" and g.scene]:
        meta_path = work.root / "scenes" / g.scene / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta["shape"] != shape:
            continue
        now = signature(versions.edl_path(work.out, version, shape), meta["start"], meta["end"])
        if now != meta["signature"]:
            notes.append(f"The designed scene at {_mmss(meta['start'])} was left out: the cut under it changed. "
                         "Ask Cello to design it again on this version.")
            continue
        splice(film, work.root / meta["video"], meta["start"], meta["end"], film.with_name(film.stem + ".scene.mp4"))
        film.with_name(film.stem + ".scene.mp4").replace(film)
    return notes


def _duration(video: Path) -> float:
    import cv2

    cap = cv2.VideoCapture(str(video))
    n, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT), cap.get(cv2.CAP_PROP_FPS) or 30
    cap.release()
    return n / fps if n else 1e9


def _mmss(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"
