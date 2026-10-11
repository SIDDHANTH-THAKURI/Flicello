"""Where each person's files live, how much room they may take, and how uploads arrive safely.

Uploads come in chunks (resumable after a dropped connection) into a private temp file named by the
server; nothing the client sends is ever used as a path. A finished upload must pass ffprobe (a real
video, or a real song, within the length limits) before it joins the project; anything else is deleted.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import time
from pathlib import Path

from ..ffmpeg import ffprobe_json
from .db import DB
from .pipeline import Workdir, folder_bytes
from .settings import StudioSettings

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".3gp", ".webm"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"}
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


class UploadError(ValueError):
    """Shown to the person as is."""


def user_dir(s: StudioSettings, uid: str) -> Path:
    if not re.fullmatch(r"u[0-9a-f]{12}", uid):
        raise ValueError("bad user id")
    return s.users_dir / uid


def workdir(s: StudioSettings, uid: str, pid: str) -> Workdir:
    if not re.fullmatch(r"p[0-9a-f]{12}", pid):
        raise ValueError("bad project id")
    return Workdir(user_dir(s, uid) / "projects" / pid)


def safe_child(folder: Path, name: str) -> Path | None:
    """folder/name only if name is a plain file name that stays inside folder and exists."""
    if not SAFE.match(name or ""):
        return None
    path = (folder / name).resolve()
    try:
        path.relative_to(folder.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def free_gb(s: StudioSettings) -> float:
    return shutil.disk_usage(s.home).free / 1e9


def user_bytes(s: StudioSettings, uid: str) -> int:
    return folder_bytes(user_dir(s, uid))


def display_name(name: str) -> str:
    """A client file name, cleaned for display only (never used on disk)."""
    name = os.path.basename(name.replace("\\", "/"))
    name = "".join(ch for ch in name if ch.isprintable())[:80]
    return name or "file"


# ---------------------------------------------------------------- chunked uploads

def start_upload(db: DB, s: StudioSettings, user: dict, project: dict, kind: str, name: str, size: int) -> dict:
    lim = s.limits
    ext = Path(name).suffix.lower()
    if kind == "clip":
        if ext not in VIDEO_EXT:
            raise UploadError(f"{display_name(name)}: not a video file this tool reads ({', '.join(sorted(VIDEO_EXT))}).")
        if size > lim.clip_bytes:
            raise UploadError(f"{display_name(name)} is over {lim.clip_bytes / 1e9:.1f} GB.")
        if db.value("SELECT COUNT(*) FROM clips WHERE project_id=?", (project["id"],), 0) >= lim.clips_per_project:
            raise UploadError(f"A video can use up to {lim.clips_per_project} clips.")
    elif kind == "song":
        if ext not in AUDIO_EXT:
            raise UploadError(f"{display_name(name)}: songs can be {', '.join(sorted(AUDIO_EXT))}.")
        if size > lim.song_bytes:
            raise UploadError(f"The song is over {lim.song_bytes / 1e6:.0f} MB.")
    else:
        raise UploadError("unknown upload kind")
    if size <= 0:
        raise UploadError("That file is empty.")
    work = workdir(s, user["id"], project["id"])
    if folder_bytes(work.root) + size > lim.project_bytes:
        raise UploadError(f"A video's uploads can total {lim.project_bytes / 1e9:.1f} GB; this one would go over.")
    if user["role"] != "owner" and user_bytes(s, user["id"]) + size > lim.user_bytes:
        raise UploadError("You're out of space: delete an old video first.")
    if folder_bytes(s.users_dir) + size > lim.total_bytes or free_gb(s) - size / 1e9 < lim.min_free_gb:
        raise UploadError("The studio is short of disk space right now. Try again later.")
    uid = "up" + secrets.token_hex(8)
    now = time.time()
    db.run("INSERT INTO uploads(id, user_id, project_id, kind, name, ext, size, received, status, created, updated) "
           "VALUES (?,?,?,?,?,?,?,0,'open',?,?)", (uid, user["id"], project["id"], kind, display_name(name), ext, size, now, now))
    return {"id": uid, "chunk": lim.chunk_bytes, "received": 0, "size": size}


def _part(s: StudioSettings, upload_id: str) -> Path:
    if not re.fullmatch(r"up[0-9a-f]{16}", upload_id):
        raise UploadError("bad upload id")
    return s.uploads_dir / f"{upload_id}.part"


def write_chunk(db: DB, s: StudioSettings, up: dict, offset: int, data: bytes) -> int:
    """Append one chunk at exactly the offset the server has; a mismatch returns the server's count so the
    client resumes from there."""
    if up["status"] != "open":
        raise UploadError("This upload is closed.")
    if offset != up["received"]:
        return up["received"]
    if len(data) > s.limits.chunk_bytes or up["received"] + len(data) > up["size"]:
        raise UploadError("Chunk too large.")
    part = _part(s, up["id"])
    with open(part, "ab") as fh:
        if fh.tell() != up["received"]:  # a half-written chunk from a crash: cut back to what was confirmed
            fh.truncate(up["received"])
            fh.seek(up["received"])
        fh.write(data)
    received = up["received"] + len(data)
    db.run("UPDATE uploads SET received=?, updated=? WHERE id=?", (received, time.time(), up["id"]))
    return received


def _probe(path: Path, kind: str, s: StudioSettings) -> dict:
    try:
        info = ffprobe_json(path)
    except Exception as exc:  # noqa: BLE001 - anything ffprobe can't read is rejected
        raise UploadError("That file couldn't be read.") from exc
    streams = info.get("streams") or []
    duration = float((info.get("format") or {}).get("duration") or 0.0)
    if kind == "clip":
        video = next((st for st in streams if st.get("codec_type") == "video"
                      and st.get("disposition", {}).get("attached_pic") != 1), None)
        if video is None:
            raise UploadError("That file has no video in it.")
        if not 0.3 <= duration <= s.limits.clip_seconds:
            raise UploadError(f"Clips can be up to {s.limits.clip_seconds / 60:.0f} minutes long.")
        w, h = int(video.get("width") or 0), int(video.get("height") or 0)
        rot = 0
        for side in video.get("side_data_list") or []:
            rot = int(side.get("rotation") or 0)
        if abs(rot) % 180 == 90:
            w, h = h, w
        return {"seconds": duration, "width": w, "height": h}
    if not any(st.get("codec_type") == "audio" for st in streams):
        raise UploadError("That file has no sound in it.")
    if not 5.0 <= duration <= s.limits.song_seconds:
        raise UploadError(f"Songs can be 5 s to {s.limits.song_seconds / 60:.0f} minutes long.")
    return {"seconds": duration}


def finish_upload(db: DB, s: StudioSettings, up: dict, project: dict) -> dict:
    if up["received"] != up["size"]:
        raise UploadError("The upload isn't complete yet.")
    if project["user_id"] != up["user_id"] or project["id"] != up["project_id"]:
        raise UploadError("not your project")
    part = _part(s, up["id"])
    try:
        meta = _probe(part, up["kind"], s)
    except UploadError:
        part.unlink(missing_ok=True)
        db.run("UPDATE uploads SET status='rejected', updated=? WHERE id=?", (time.time(), up["id"]))
        raise
    work = workdir(s, up["user_id"], project["id"]).ensure()
    if up["kind"] == "clip":
        cid = "c" + secrets.token_hex(6)
        file = f"{cid}{up['ext']}"
        os.replace(part, work.clips / file)
        db.run("INSERT INTO clips(id, project_id, name, file, bytes, seconds, width, height, created) "
               "VALUES (?,?,?,?,?,?,?,?,?)", (cid, project["id"], up["name"], file, up["size"], meta["seconds"],
                                              meta["width"], meta["height"], time.time()))
        result = {"clip": cid, "name": up["name"], "seconds": round(meta["seconds"], 1)}
    else:
        for old in work.music.glob("song.*"):
            old.unlink()
        os.replace(part, work.music / f"song{up['ext']}")
        db.run("UPDATE projects SET song=?, updated=? WHERE id=?", (up["name"], time.time(), project["id"]))
        result = {"song": up["name"], "seconds": round(meta["seconds"], 1)}
    db.run("UPDATE uploads SET status='done', updated=? WHERE id=?", (time.time(), up["id"]))
    return result


def delete_clip(db: DB, s: StudioSettings, project: dict, clip_id: str) -> bool:
    row = db.one("SELECT * FROM clips WHERE id=? AND project_id=?", (clip_id, project["id"]))
    if not row:
        return False
    path = safe_child(workdir(s, project["user_id"], project["id"]).clips, row["file"])
    if path:
        path.unlink(missing_ok=True)
    db.run("DELETE FROM clips WHERE id=?", (clip_id,))
    return True


def sweep_uploads(db: DB, s: StudioSettings, older_than: float = 86400.0) -> int:
    """Abandoned uploads (no chunk for a day) and their temp files."""
    cutoff = time.time() - older_than
    stale = db.all("SELECT id FROM uploads WHERE status='open' AND updated < ?", (cutoff,))
    for row in stale:
        _part(s, row["id"]).unlink(missing_ok=True)
        db.run("UPDATE uploads SET status='abandoned' WHERE id=?", (row["id"],))
    return len(stale)
