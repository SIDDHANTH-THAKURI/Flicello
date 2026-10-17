"""A remote worker: runs flicello.com's video jobs on another machine (the owner's PC now, Modal later).

    vido studio remote-worker --server https://flicello.com     (VIDO_STUDIO_WORKER_TOKEN in .env, same as the server)

It only ever calls the server (web/workerapi.py); the server never connects to it. For each job it:
  1. claims the job, with the server's money settings (brains, limits, models);
  2. mirrors that project's files from the server (clips, song, notes, results, the person's style profile);
  3. runs the job with the studio's own code (service.Studio.run_job), every database call going to the server;
  4. uploads what the job made or changed, and only then lets the job's stage change land (`hold`), so the site
     never shows a finished video whose file has not arrived.
The pipeline's working files (proxies, measurements, transcripts) stay on this machine between a project's jobs;
copies of projects the server no longer has (deleted, or past retention) are removed every few minutes.

It works in two lanes (db.LONG_KINDS), each its own process: this one renders whole films, a second one it starts and
watches does everything short (watching clips, the story, looks, samples). Without that, one hour-long film made
everybody else's video sit at "Waiting for its turn" for the whole hour.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from .db import DB
from .pipeline import remove_project_files
from .settings import Limits, Models, StudioSettings, load_settings
from .storage import user_dir, workdir
from .web.workerapi import PREFIX

log = logging.getLogger("vidosuper.studio.remote")
SAFE_TO_RETRY = (httpx.ConnectError, httpx.ConnectTimeout)  # the request never reached the server
PURGE_EVERY = 600.0


def default_home() -> Path:
    if os.environ.get("VIDO_REMOTE_HOME"):
        return Path(os.environ["VIDO_REMOTE_HOME"])
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".vidosuper")
    return Path(base) / "VidoSuper" / "remote-worker"


def _call(http: httpx.Client, method: str, url: str, tries: int = 4, **kw) -> httpx.Response:
    """One API call. Retried only when it can't have run twice: no connection, or the proxy found no server."""
    for attempt in range(tries):
        try:
            r = http.request(method, url, **kw)
            if r.status_code in (502, 503, 504) and attempt < tries - 1:
                raise httpx.ConnectError(f"server unavailable ({r.status_code})")
            r.raise_for_status()
            return r
        except SAFE_TO_RETRY:
            if attempt == tries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("unreachable")


class RemoteDB(DB):
    """The studio database, over the worker API. Stage changes and job endings can be held back (`hold`) until the
    job's files are on the server, then `release`d in order."""

    def __init__(self, http: httpx.Client):  # no local file: everything goes to the server
        self.http = http
        self.path = None
        self.hold = False
        self._held: list[tuple[str, tuple, dict]] = []

    def connect(self):
        raise RuntimeError("a remote worker has no direct database connection")

    def all(self, sql: str, args=()) -> list[dict]:
        return _call(self.http, "POST", PREFIX + "/db", json={"op": "all", "sql": sql, "args": list(args)}).json()["rows"]

    def run(self, sql: str, args=()) -> int:
        return _call(self.http, "POST", PREFIX + "/db", json={"op": "run", "sql": sql, "args": list(args)}).json()["id"]

    def set_stage(self, *args, **kw) -> None:
        if self.hold:
            self._held.append(("set_stage", args, kw))
        else:
            DB.set_stage(self, *args, **kw)

    def job_done(self, *args, **kw) -> None:
        if self.hold:
            self._held.append(("job_done", args, kw))
        else:
            DB.job_done(self, *args, **kw)

    def release(self) -> None:
        held, self._held, self.hold = self._held, [], False
        for name, args, kw in held:
            getattr(DB, name)(self, *args, **kw)

    def drop_held(self) -> None:
        self._held, self.hold = [], False


class Mirror:
    """Keeps this machine's copy of a project's files the same as the server's, and sends back what a job changed.
    Files are compared by size and whole-second modification time; uploads carry their time, so they match after."""

    def __init__(self, http: httpx.Client, s: StudioSettings):
        self.http, self.s = http, s

    def _files(self, uid: str, pid: str) -> dict[str, Path]:
        root = user_dir(self.s, uid)
        found = {"profile.json": root / "profile.json"} if (root / "profile.json").is_file() else {}
        project = workdir(self.s, uid, pid).root
        if project.exists():
            found.update({f.relative_to(root).as_posix(): f for f in project.rglob("*") if f.is_file()})
        return found

    def snapshot(self, uid: str, pid: str) -> dict[str, tuple[int, int]]:
        return {rel: (f.stat().st_size, int(f.stat().st_mtime)) for rel, f in self._files(uid, pid).items()}

    def down(self, uid: str, pid: str) -> dict[str, tuple[int, int]]:
        root = user_dir(self.s, uid)
        listed = _call(self.http, "GET", f"{PREFIX}/manifest/{uid}/{pid}").json()["files"]
        have = self.snapshot(uid, pid)
        for f in listed:
            if have.get(f["path"]) == (f["size"], f["mtime"]):
                continue
            target = root / Path(*f["path"].split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".download")
            with self.http.stream("GET", f"{PREFIX}/file/{uid}", params={"path": f["path"]}) as r:
                r.raise_for_status()
                with part.open("wb") as fh:
                    for chunk in r.iter_bytes(1 << 20):
                        fh.write(chunk)
            os.replace(part, target)
            os.utime(target, (f["mtime"], f["mtime"]))
        wanted = {f["path"] for f in listed}
        for rel, path in self._files(uid, pid).items():
            if rel not in wanted:  # the person deleted it on the site
                path.unlink(missing_ok=True)
        workdir(self.s, uid, pid).ensure()  # empty folders don't travel; the project's layout must be there
        return self.snapshot(uid, pid)

    def up(self, uid: str, pid: str, before: dict[str, tuple[int, int]]) -> int:
        now = self._files(uid, pid)
        sent = 0
        for rel, path in now.items():
            st = path.stat()
            if before.get(rel) == (st.st_size, int(st.st_mtime)):
                continue
            with path.open("rb") as fh:
                _call(self.http, "PUT", f"{PREFIX}/file/{uid}", params={"path": rel, "mtime": int(st.st_mtime)},
                      content=iter(lambda: fh.read(1 << 20), b""))
            sent += 1
        gone = [rel for rel in before if rel not in now]
        if gone:
            _call(self.http, "POST", f"{PREFIX}/delete/{uid}", json={"paths": gone})
        return sent


LANE_NAMES = {"all": "renderer", "long": "renderer", "quick": "renderer-quick"}


class RemoteWorker:
    def __init__(self, server: str = "", token: str = "", home: Path | None = None, http: httpx.Client | None = None,
                 name: str = "", lane: str = "all"):
        from .service import Studio

        s = load_settings(home or default_home())  # also reads the repo's .env (the token)
        token = token or os.environ.get("VIDO_STUDIO_WORKER_TOKEN", "")
        if http is None:
            if not server or len(token) < 32:
                raise SystemExit("Set VIDO_STUDIO_WORKER_TOKEN (the server's value) and give --server.")
            http = httpx.Client(base_url=server.rstrip("/"), timeout=httpx.Timeout(30.0, read=600.0),
                                headers={"User-Agent": f"flicello-worker/{platform.node()}"})
        if token:
            http.headers["Authorization"] = f"Bearer {token}"
        self.http, self.lane, self.name = http, lane, name or LANE_NAMES[lane]
        self.studio = Studio(s)
        self.db = RemoteDB(http)
        self.studio.db = self.db
        self.mirror = Mirror(http, self.studio.s)
        self._last_purge = 0.0
        self.current: int | None = None  # the job running now (the pulse checks the server still wants it)
        from ..music.compose import available as music_available

        self.music = music_available()  # this machine can have music made (an ElevenLabs key): the site offers it
        self.on_abort = lambda: os._exit(3)  # a lane process is started again by its supervisor (run_split)

    def _apply(self, settings: dict) -> None:
        """The server decides what costs money; this machine decides how hard it works (render processes)."""
        s = self.studio.s
        self.studio.s = s.model_copy(update={"brain": settings["brain"], "owner_brain": settings["owner_brain"],
                                             "limits": Limits.model_validate(settings["limits"]),
                                             "models": Models.model_validate(settings["models"])})
        self.mirror.s = self.studio.s

    def purge(self) -> int:
        """Delete this machine's copies of projects the server no longer has."""
        s = self.studio.s
        local = [(u.name, p.name) for u in s.users_dir.glob("u*") for p in (u / "projects").glob("p*") if p.is_dir()]
        if not local:
            return 0
        alive = set(_call(self.http, "POST", PREFIX + "/alive", json={"ids": [p for _, p in local]}).json()["alive"])
        gone = 0
        for uid, pid in local:
            if pid not in alive:
                remove_project_files(workdir(s, uid, pid), self.studio.cfg)
                gone += 1
        return gone

    def recover(self) -> int:
        return _call(self.http, "POST", PREFIX + "/recover", json={"worker": self.name}).json()["requeued"]

    def tick(self) -> bool:
        """One round: heartbeat, tidy up when due, and at most one job. True if a job ran."""
        self.db.heartbeat(self.name, {"at": time.time(), "host": platform.node(), "music": self.music})
        if time.time() - self._last_purge > PURGE_EVERY:
            self._last_purge = time.time()
            removed = self.purge()
            if removed:
                log.info("removed %d project copies the server no longer has", removed)
        data = _call(self.http, "POST", PREFIX + "/claim", json={"lane": self.lane, "worker": self.name}).json()
        job = data["job"]
        if job is None:
            return False
        self._apply(data["settings"])
        uid, pid = job["user_id"], job["project_id"]
        log.info("job %s %s (project %s, try %s)", job["id"], job["kind"], pid, job["attempts"])
        started = time.monotonic()
        try:
            before = self.mirror.down(uid, pid) if uid and pid else {}
        except Exception:
            self.db.requeue(job["id"], delay=60.0, error="the renderer couldn't fetch the files")
            raise
        self.db.hold = True
        self.current = job["id"]
        try:
            self.studio.run_job(job)
        finally:
            self.current = None
        try:
            if uid and pid:
                sent = self.mirror.up(uid, pid, before)
                log.info("job %s: sent back %d file(s)", job["id"], sent)
        except Exception:
            self.db.drop_held()  # the result never arrived: run the job again rather than show a missing video
            self.db.requeue(job["id"], delay=60.0, error="the renderer couldn't send the result back")
            raise
        self.db.release()
        log.info("job %s finished in %.0fs", job["id"], time.monotonic() - started)
        return True

    def check(self) -> None:
        """Check in, and drop the running job if the server took it back (its watchdog found it stuck): a stuck step
        can't be stopped from inside, so the whole lane process ends and its supervisor starts it again."""
        self.db.heartbeat(self.name, {"at": time.time(), "host": platform.node(), "music": self.music})
        job = self.current
        if job is None:
            return
        row = self.db.one("SELECT abort FROM jobs WHERE id=?", (job,))
        if row and row["abort"] and self.current == job:
            log.error("the server took job %s back (stuck): dropping it and starting this lane again", job)
            self.on_abort()

    def _pulse(self, stop) -> None:
        """Check in every 20 s, also in the middle of a long render: the site tells waiting people the renderer is
        offline only when it really is."""
        while not stop.wait(20.0):
            try:
                self.check()
            except Exception:  # noqa: BLE001 - the next pulse tries again
                pass

    def run(self, stop=None) -> None:
        stop = stop or threading.Event()
        threading.Thread(target=self._pulse, args=(stop,), name="renderer-pulse", daemon=True).start()
        requeued = self.recover()
        if requeued:
            log.warning("put %d interrupted job(s) back in the queue", requeued)
        while not stop.is_set():
            try:
                busy = self.tick()
            except httpx.HTTPError as exc:
                log.warning("server unreachable or refused: %s", exc)
                busy = False
                time.sleep(15.0)
            except Exception:  # noqa: BLE001 - the loop itself must survive anything
                log.exception("remote worker error")
                busy = False
            if not busy:
                time.sleep(3.0)


def _alive(pid: int) -> bool:
    """Is process `pid` still running? (Windows: os.kill would end it, so ask the system instead.)"""
    if os.name == "nt":
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return k32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT: not ended
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def follow_parent(pid: int, every: float = 5.0) -> None:
    """A lane started by another process ends the moment that process does (killed, window closed): a lane left
    running would keep its job while the next one, under the same name, put that job back in the queue."""
    def watch():
        while _alive(pid):
            time.sleep(every)
        log.warning("the main renderer stopped: this lane stops too")
        os._exit(0)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def run_split(server: str, token: str = "", stop: threading.Event | None = None, every: float = 5.0) -> None:
    """The renderer as a supervisor of two lane processes: whole films in one, everything short in the other. Each is
    started again when it stops (a crash, or dropping a job the server took back) without touching the other's job;
    both end with this process."""
    stop = stop or threading.Event()
    env = dict(os.environ)
    if token:
        env["VIDO_STUDIO_WORKER_TOKEN"] = token  # never on the command line, where other programs can read it
    lanes: dict[str, subprocess.Popen | None] = {"long": None, "quick": None}
    try:
        while not stop.is_set():
            for lane, proc in lanes.items():
                if proc is None or proc.poll() is not None:
                    if proc is not None:
                        log.warning("the %s lane stopped (code %s): starting it again", lane, proc.returncode)
                    lanes[lane] = subprocess.Popen(
                        [sys.executable, "-m", "vidosuper", "studio", "remote-worker", "--server", server,
                         "--lane", lane, "--parent", str(os.getpid())], env=env)
            stop.wait(every)
    finally:
        for proc in lanes.values():
            if proc is not None and proc.poll() is None:
                proc.terminate()
