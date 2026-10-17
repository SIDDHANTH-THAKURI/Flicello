"""The API remote workers use to run the studio's video jobs (the owner's PC now, Modal later; see ../remote.py).

A worker holds the token in VIDO_STUDIO_WORKER_TOKEN, the same value as on the server. It claims a job, mirrors that
project's files, runs the job with the studio's own code, writes through /db and uploads what changed. The server
never connects to a worker. The token is as powerful as the database: keep it out of git and chat, and change it on
both sides if it ever leaks. Without a token set (or with jobs run locally) every route answers 404.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path, PurePosixPath

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response

from ..db import LANES
from ..storage import user_dir, workdir

PREFIX = "/api/worker"


def manifest(root: Path, pid: str) -> list[dict]:
    """The files a job may need, relative to the person's folder: their style profile and the project's folder."""
    out = []
    prof = root / "profile.json"
    candidates = [prof] if prof.is_file() else []
    project = root / "projects" / pid
    if project.exists():
        candidates += [f for f in project.rglob("*") if f.is_file()]
    for f in candidates:
        st = f.stat()
        out.append({"path": f.relative_to(root).as_posix(), "size": st.st_size, "mtime": int(st.st_mtime)})
    return out


def safe_rel(root: Path, rel: str) -> Path:
    """root/rel, only for the style profile or a file inside one of the person's projects."""
    parts = PurePosixPath(rel or "").parts
    ok = rel == "profile.json" or (len(parts) >= 3 and parts[0] == "projects")
    if not ok or "\\" in rel or ":" in rel or any(p in ("..", ".", "") for p in parts) or rel.startswith("/"):
        raise HTTPException(400, "Bad path.")
    path = (root / rel).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(400, "Bad path.") from exc
    return path


def mount(app, studio) -> None:
    s, db = studio.s, studio.db

    def authorised(request: Request) -> None:
        token = os.environ.get("VIDO_STUDIO_WORKER_TOKEN", "")
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if s.jobs != "remote" or len(token) < 32 or not hmac.compare_digest(
                hashlib.sha256(given.encode()).digest(), hashlib.sha256(token.encode()).digest()):
            raise HTTPException(404, "Not found.")

    def json_response(data) -> Response:
        return Response(json.dumps(data, default=lambda v: None), media_type="application/json")

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001 - an older renderer sends nothing
            return {}
        return data if isinstance(data, dict) else {}

    @app.post(PREFIX + "/claim")
    async def claim(request: Request):
        authorised(request)
        data = await body(request)
        lane = data.get("lane") if data.get("lane") in LANES else "all"
        job = db.claim(lane, str(data.get("worker") or "")[:40] or None)
        return json_response({"job": job, "settings": {
            "brain": s.brain, "owner_brain": s.owner_brain,  # money decisions stay the server's
            "limits": s.limits.model_dump(), "models": s.models.model_dump()}})

    @app.post(PREFIX + "/recover")
    async def recover(request: Request):
        """A worker starting up puts the jobs it was running back in the queue (their steps are safe to redo); only its
        own, so a lane starting again never takes the other lane's job away mid-render."""
        authorised(request)
        worker = str((await body(request)).get("worker") or "")[:40]
        # (the main lane, "renderer", also owns jobs an older renderer claimed without a name)
        rows = (db.all("SELECT id FROM jobs WHERE status='running' AND (worker=? OR (worker IS NULL AND ?='renderer'))",
                       (worker, worker)) if worker
                else db.all("SELECT id FROM jobs WHERE status='running'"))  # an older renderer: all of them
        for row in rows:
            db.requeue(row["id"])
        return json_response({"requeued": len(rows)})

    @app.post(PREFIX + "/db")
    async def run_sql(request: Request):
        authorised(request)
        body = await request.json()
        op, sql, args = body.get("op"), str(body.get("sql", "")), list(body.get("args") or [])
        if op == "all":
            return json_response({"rows": db.all(sql, args)})
        if op == "run":
            return json_response({"id": db.run(sql, args)})
        raise HTTPException(400, "Unknown operation.")

    @app.get(PREFIX + "/manifest/{uid}/{pid}")
    def files(request: Request, uid: str, pid: str):
        authorised(request)
        workdir(s, uid, pid)  # checks both ids
        return json_response({"files": manifest(user_dir(s, uid), pid)})

    @app.get(PREFIX + "/file/{uid}")
    def download(request: Request, uid: str, path: str):
        authorised(request)
        target = safe_rel(user_dir(s, uid), path)
        if not target.is_file():
            raise HTTPException(404, "Not found.")
        return FileResponse(target, media_type="application/octet-stream")

    @app.put(PREFIX + "/file/{uid}")
    async def upload(request: Request, uid: str, path: str, mtime: int = 0):
        authorised(request)
        target = safe_rel(user_dir(s, uid), path)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".incoming")
        try:
            with part.open("wb") as fh:
                async for chunk in request.stream():
                    fh.write(chunk)
            os.replace(part, target)
        finally:
            part.unlink(missing_ok=True)
        if mtime:
            os.utime(target, (mtime, mtime))
        return json_response({"ok": True, "size": target.stat().st_size})

    @app.post(PREFIX + "/delete/{uid}")
    async def delete(request: Request, uid: str):
        authorised(request)
        root = user_dir(s, uid)
        gone = 0
        for rel in (await request.json()).get("paths", []):
            target = safe_rel(root, rel)
            if target.is_file():
                target.unlink()
                gone += 1
        return json_response({"deleted": gone})

    @app.post(PREFIX + "/alive")
    async def alive(request: Request):
        """Which of these projects still exist: a worker deletes its copies of the rest (retention, deletions)."""
        authorised(request)
        ids = [str(i) for i in (await request.json()).get("ids", [])][:500]
        rows = db.all(f"SELECT id FROM projects WHERE deleted=0 AND id IN ({','.join('?' * len(ids))})", ids) if ids else []
        return json_response({"alive": [r["id"] for r in rows], "at": time.time()})
