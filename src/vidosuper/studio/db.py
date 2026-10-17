"""The studio's database (SQLite in WAL mode, safe for the web process and the worker at once).

Tokens (sessions, invites) are stored only as SHA-256 hashes, so a copied database opens no doors.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path

# A renderer works in two lanes, each its own process: whole films (an hour for a long designed talk on a busy PC) in
# one, everything short (watching clips, the story, looks, a sample) in the other, so one long film never makes
# everyone else wait for it. "all" is one process doing both (the server's own worker, an older renderer).
LONG_KINDS = ("render", "revise")
LANES = ("all", "long", "quick")


def lane_of(kind: str) -> str:
    return "long" if kind in LONG_KINDS else "quick"


def _lane_sql(lane: str) -> str:
    marks = ",".join("?" * len(LONG_KINDS))
    return {"all": "", "long": f" AND kind IN ({marks})", "quick": f" AND kind NOT IN ({marks})"}[lane]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, role TEXT NOT NULL, pw TEXT NOT NULL,
  created REAL NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, videos_allowed INTEGER, onboarded INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS invites(
  token TEXT PRIMARY KEY, label TEXT, role TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
  used_by TEXT, used REAL);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
  seen REAL, ip TEXT);
CREATE TABLE IF NOT EXISTS attempts(key TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS devices(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, shapes TEXT NOT NULL, length REAL NOT NULL,
  intent TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
  updated REAL NOT NULL, version INTEGER NOT NULL DEFAULT 0, revisions INTEGER NOT NULL DEFAULT 0,
  sample_tries INTEGER NOT NULL DEFAULT 0, pitch_round INTEGER NOT NULL DEFAULT 0, chosen INTEGER,
  song TEXT, info TEXT, error TEXT, deleted INTEGER NOT NULL DEFAULT 0, song_seconds REAL,
  pace TEXT NOT NULL DEFAULT 'balanced', music TEXT NOT NULL DEFAULT 'made',
  kind TEXT NOT NULL DEFAULT 'montage', style TEXT NOT NULL DEFAULT 'designed');
CREATE TABLE IF NOT EXISTS clips(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, file TEXT NOT NULL, bytes INTEGER NOT NULL,
  seconds REAL, width INTEGER, height INTEGER, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS uploads(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
  ext TEXT NOT NULL, size INTEGER NOT NULL, received INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, user_id TEXT, kind TEXT NOT NULL, params TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT, priority INTEGER NOT NULL DEFAULT 5, created REAL NOT NULL, started REAL, finished REAL, after REAL,
  moved REAL, worker TEXT);
CREATE TABLE IF NOT EXISTS pitches(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, round INTEGER NOT NULL, data TEXT NOT NULL,
  stills TEXT NOT NULL, created REAL NOT NULL, rejected INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS plans(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, kind TEXT NOT NULL, pitch_id INTEGER,
  data TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS renders(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, plan_id INTEGER, version INTEGER NOT NULL,
  kind TEXT NOT NULL, shape TEXT NOT NULL, file TEXT NOT NULL, seconds REAL, render_seconds REAL,
  findings TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL, style TEXT);
CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, user_id TEXT NOT NULL, stage TEXT NOT NULL,
  version INTEGER, match INTEGER, liked INTEGER, tags TEXT NOT NULL DEFAULT '[]', text TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS spend(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, user_id TEXT, project_id TEXT, job_id INTEGER,
  task TEXT, model TEXT, backend TEXT, tin INTEGER, tout INTEGER, cw INTEGER, cr INTEGER, usd REAL NOT NULL,
  billed INTEGER NOT NULL, seconds REAL);
CREATE TABLE IF NOT EXISTS steps(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL, at REAL NOT NULL, message TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS steps_job ON steps(job_id);
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, user_id TEXT, project_id TEXT, job_id INTEGER,
  note TEXT NOT NULL, context TEXT NOT NULL, resolved REAL);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, project_id TEXT, job_id INTEGER, level TEXT NOT NULL,
  message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, level TEXT NOT NULL, kind TEXT NOT NULL,
  message TEXT NOT NULL, seen INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS heartbeat(name TEXT PRIMARY KEY, at REAL NOT NULL, info TEXT);
CREATE TABLE IF NOT EXISTS login_codes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, code TEXT NOT NULL, created REAL NOT NULL,
  expires REAL NOT NULL, tries INTEGER NOT NULL DEFAULT 0, used REAL);
CREATE TABLE IF NOT EXISTS pending(
  token TEXT PRIMARY KEY, email TEXT, google_sub TEXT, name TEXT, created REAL NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS oauth(
  state TEXT PRIMARY KEY, nonce TEXT NOT NULL, verifier TEXT NOT NULL, created REAL NOT NULL, next TEXT);
CREATE TABLE IF NOT EXISTS passkeys(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, public_key BLOB NOT NULL, sign_count INTEGER NOT NULL DEFAULT 0,
  transports TEXT, name TEXT, created REAL NOT NULL, last_used REAL);
CREATE TABLE IF NOT EXISTS challenges(
  token TEXT PRIMARY KEY, challenge BLOB NOT NULL, user_id TEXT, kind TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS consents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, kind TEXT NOT NULL, version TEXT NOT NULL,
  granted INTEGER NOT NULL, at REAL NOT NULL, ip TEXT);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, user_id TEXT, action TEXT NOT NULL, detail TEXT, ip TEXT);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, priority, id);
CREATE INDEX IF NOT EXISTS projects_user ON projects(user_id, deleted);
CREATE INDEX IF NOT EXISTS spend_at ON spend(at);
CREATE INDEX IF NOT EXISTS attempts_key ON attempts(key, at);
CREATE INDEX IF NOT EXISTS events_project ON events(project_id, id);
CREATE INDEX IF NOT EXISTS codes_email ON login_codes(email, created);
CREATE INDEX IF NOT EXISTS passkeys_user ON passkeys(user_id);
CREATE INDEX IF NOT EXISTS consents_user ON consents(user_id, kind, at);
CREATE INDEX IF NOT EXISTS audit_at ON audit(at);
"""


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(SCHEMA)
            self._migrate(con)

    @staticmethod
    def _migrate(con: sqlite3.Connection) -> None:
        """Columns added after the first release (CREATE TABLE IF NOT EXISTS leaves older databases as they were)."""
        have = {r[1] for r in con.execute("PRAGMA table_info(projects)")}
        if "song_seconds" not in have:
            con.execute("ALTER TABLE projects ADD COLUMN song_seconds REAL")
        if "style" not in have:
            con.execute("ALTER TABLE projects ADD COLUMN style TEXT NOT NULL DEFAULT 'designed'")
        if "kind" not in have:
            con.execute("ALTER TABLE projects ADD COLUMN kind TEXT NOT NULL DEFAULT 'montage'")
        if "music" not in have:
            con.execute("ALTER TABLE projects ADD COLUMN music TEXT NOT NULL DEFAULT 'made'")
        if "pace" not in have:
            con.execute("ALTER TABLE projects ADD COLUMN pace TEXT NOT NULL DEFAULT 'balanced'")
        # step 3a: their own words for the cut, how far the AI may go beyond them, and with no music, which sound
        for col, kind in (("cut_note", "TEXT NOT NULL DEFAULT ''"), ("improvise", "INTEGER NOT NULL DEFAULT 35"),
                          ("sound", "TEXT NOT NULL DEFAULT 'silent'"), ("routed", "TEXT NOT NULL DEFAULT ''"),
                          # step 3c: what the AI understood, and what the person agreed to after correcting it
                          ("understood", "TEXT"), ("brief", "TEXT")):
            if col not in have:
                con.execute(f"ALTER TABLE projects ADD COLUMN {col} {kind}")
        if "moved" not in {r[1] for r in con.execute("PRAGMA table_info(jobs)")}:
            con.execute("ALTER TABLE jobs ADD COLUMN moved REAL")
        if "worker" not in {r[1] for r in con.execute("PRAGMA table_info(jobs)")}:  # which renderer lane runs it
            con.execute("ALTER TABLE jobs ADD COLUMN worker TEXT")
        if "abort" not in {r[1] for r in con.execute("PRAGMA table_info(jobs)")}:  # the watchdog took it back
            con.execute("ALTER TABLE jobs ADD COLUMN abort REAL")
        if "style" not in {r[1] for r in con.execute("PRAGMA table_info(renders)")}:  # how a voice/talk video was drawn
            con.execute("ALTER TABLE renders ADD COLUMN style TEXT")
        # public sign-up: people are found by email or Google account; `name` stays the unique handle
        users = {r[1] for r in con.execute("PRAGMA table_info(users)")}
        for col, kind in (("email", "TEXT"), ("google_sub", "TEXT"), ("display", "TEXT"), ("last_seen", "REAL"),
                          ("marketing", "INTEGER NOT NULL DEFAULT 0"), ("adult", "REAL"), ("terms", "TEXT"),
                          ("joined_by", "TEXT"), ("access", "TEXT NOT NULL DEFAULT 'beta'"), ("request_note", "TEXT"),
                          ("requested", "REAL"), ("decided", "REAL")):
            if col not in users:
                con.execute(f"ALTER TABLE users ADD COLUMN {col} {kind}")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email ON users(email) WHERE email IS NOT NULL")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_google ON users(google_sub) WHERE google_sub IS NOT NULL")

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=15, isolation_level=None, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=15000")
        return con

    # small helpers: one short-lived connection per call keeps threads and processes out of each other's way
    def all(self, sql: str, args: Iterable = ()) -> list[dict]:
        con = self.connect()
        try:
            return [dict(r) for r in con.execute(sql, tuple(args)).fetchall()]
        finally:
            con.close()

    def one(self, sql: str, args: Iterable = ()) -> dict | None:
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def run(self, sql: str, args: Iterable = ()) -> int:
        con = self.connect()
        try:
            cur = con.execute(sql, tuple(args))
            return cur.lastrowid or cur.rowcount
        finally:
            con.close()

    def value(self, sql: str, args: Iterable = (), default=None):
        row = self.one(sql, args)
        return next(iter(row.values())) if row else default

    # ------------------------------------------------------------------ jobs
    def enqueue(self, kind: str, project_id: str | None, user_id: str | None, params: dict | None = None,
                priority: int = 5) -> int:
        return self.run("INSERT INTO jobs(project_id, user_id, kind, params, status, priority, created) "
                        "VALUES (?,?,?,?, 'queued', ?, ?)",
                        (project_id, user_id, kind, json.dumps(params or {}), priority, time.time()))

    def claim(self, lane: str = "all", worker: str | None = None) -> dict | None:
        """The next queued job of this lane (lowest priority number first, then oldest), marked running atomically
        and as `worker`'s (so a worker starting again puts back only its own jobs, not another lane's)."""
        if lane not in LANES:
            raise ValueError(f"unknown lane {lane!r}")
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM jobs WHERE status='queued' AND (after IS NULL OR after <= ?)"
                              + _lane_sql(lane) + " ORDER BY priority, id LIMIT 1",
                              (time.time(), *(LONG_KINDS if lane != "all" else ()))).fetchone()
            if row is None:
                con.execute("COMMIT")
                return None
            now = time.time()
            con.execute("UPDATE jobs SET status='running', started=?, moved=?, attempts=attempts+1, progress=0, message='', "
                        "worker=?, abort=NULL WHERE id=?", (now, now, worker, row["id"]))
            con.execute("COMMIT")
            job = dict(row)
            job["attempts"] += 1
            job["params"] = json.loads(job["params"] or "{}")
            return job
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def requeue(self, job_id: int, delay: float = 0.0, error: str | None = None) -> None:
        self.run("UPDATE jobs SET status='queued', after=?, error=? WHERE id=?", (time.time() + delay, error, job_id))

    def job_progress(self, job_id: int, progress: float, message: str) -> None:
        """`moved` is when the progress or the message last changed: a step that stops moving may be stuck, however
        long it is (a long video simply takes longer)."""
        p, msg = max(0.0, min(1.0, progress)), message[:200]
        # never backwards: a step that reports its own 0-100% (a second shape, a retry inside the job) must not make
        # the bar jump back to the start; a job claimed again starts at 0 (see claim)
        self.run("UPDATE jobs SET moved=CASE WHEN ABS(progress - ?) > 1e-6 OR message != ? THEN ? ELSE moved END, "
                 "progress=MAX(progress, ?), message=? WHERE id=?", (p, msg, time.time(), p, msg, job_id))
        if msg:
            self.step(job_id, msg)

    def step(self, job_id: int, message: str) -> None:
        """The job's step log, shown live under the bar: a new line when the step changes; a counter that moves
        ("Watched 3 of 7 clips") updates its own line instead of adding one."""
        cache = self.__dict__.setdefault("_steps", {})
        if cache.get(job_id) == message:
            return
        last = self.one("SELECT id, message FROM steps WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,))
        if last and last["message"] == message:
            cache[job_id] = message
            return
        if last and re.sub(r"\d+", "#", last["message"]) == re.sub(r"\d+", "#", message):
            self.run("UPDATE steps SET message=? WHERE id=?", (message[:200], last["id"]))
        else:
            self.run("INSERT INTO steps(job_id, at, message) VALUES (?,?,?)", (job_id, time.time(), message[:200]))
        cache[job_id] = message

    def steps(self, job_id: int, n: int = 8) -> list[dict]:
        rows = self.all("SELECT at, message FROM steps WHERE job_id=? ORDER BY id DESC LIMIT ?", (job_id, n))
        return rows[::-1]

    def take_back(self, job_id: int, why: str, delay: float = 0.0) -> None:
        """Put a running job back in the queue and tell whoever runs it to drop it (remote.py checks `abort`)."""
        self.run("UPDATE jobs SET status='queued', after=?, error=?, abort=? WHERE id=?",
                 (time.time() + delay, why[:500], time.time(), job_id))
        self.__dict__.setdefault("_steps", {}).pop(job_id, None)
        self.run("INSERT INTO steps(job_id, at, message) VALUES (?,?,?)", (job_id, time.time(), why[:200]))

    def job_done(self, job_id: int, status: str = "done", error: str | None = None) -> None:
        self.run("UPDATE jobs SET status=?, error=?, finished=?, progress=CASE WHEN ?='done' THEN 1 ELSE progress END "
                 "WHERE id=?", (status, error, time.time(), status, job_id))

    def active_job(self, project_id: str) -> dict | None:
        """The job that moves this video along, if one is queued or running (style learning doesn't count)."""
        return self.one("SELECT * FROM jobs WHERE project_id=? AND status IN ('queued','running','waiting') "
                        "AND kind != 'learn' ORDER BY id DESC LIMIT 1", (project_id,))

    # ------------------------------------------------------------------ projects and events
    def project(self, pid: str) -> dict | None:
        return self.one("SELECT * FROM projects WHERE id=? AND deleted=0", (pid,))

    def set_stage(self, pid: str, stage: str, error: str | None = None, note: str | None = None) -> None:
        sets, args = ["stage=?", "updated=?", "error=?"], [stage, time.time(), error]
        if note is not None:
            sets.append("note=?")
            args.append(note)
        self.run(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", (*args, pid))

    def touch(self, pid: str) -> None:
        self.run("UPDATE projects SET updated=? WHERE id=?", (time.time(), pid))

    def event(self, project_id: str | None, message: str, level: str = "info", job_id: int | None = None) -> None:
        self.run("INSERT INTO events(at, project_id, job_id, level, message) VALUES (?,?,?,?,?)",
                 (time.time(), project_id, job_id, level, message[:2000]))

    # ------------------------------------------------------------------ money
    def month_spend(self, billed_only: bool = True) -> float:
        start = time.mktime(time.localtime()[:2] + (1, 0, 0, 0, 0, 0, -1))
        sql = "SELECT COALESCE(SUM(usd), 0) AS s FROM spend WHERE at >= ?" + (" AND billed=1" if billed_only else "")
        return float(self.value(sql, (start,), 0.0))

    def project_spend(self, pid: str, billed_only: bool = True) -> float:
        sql = "SELECT COALESCE(SUM(usd), 0) AS s FROM spend WHERE project_id=?" + (" AND billed=1" if billed_only else "")
        return float(self.value(sql, (pid,), 0.0))

    def heartbeat(self, name: str, info: dict | None = None) -> None:
        self.run("INSERT INTO heartbeat(name, at, info) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET at=excluded.at, "
                 "info=excluded.info", (name, time.time(), json.dumps(info or {})))
