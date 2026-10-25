"""Work sessions: a Claude Code agent doing what the person asked, in the background, on their own plan.

Each film has one session (its folder: <home>/work/<film>/). A request starts it; while it runs, a new request waits
and is handed over when it finishes; a question it asks is answered by the person and the same Claude session is
resumed with the answer, so it remembers everything about the film. Its final word is checked against a schema:
done (and what Cello should say), a question (with options), or failed (and why)."""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("vidosuper.work")

MODEL = "claude-opus-5-5"
TIMEOUT = 4 * 3600  # a session's longest single run (designing scenes takes a while)
BUDGET_USD = "60"  # Claude Code's own ceiling for one run (an API-equivalent figure; on a plan it only stops a runaway)

SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["done", "question", "failed"]},
        "say": {"type": "string", "description": "What Cello should tell the person, in one or two short spoken "
                                                 "sentences (what you did and what to look at, or why it failed)."},
        "question": {"type": "string", "description": "status question: the one thing you need to know, as Cello "
                                                      "would ask it."},
        "options": {"type": "array", "items": {"type": "string"}, "description": "status question: 2 to 4 short "
                                                                                 "answers they can tap (best first)."},
    },
    "required": ["status", "say"],
    "additionalProperties": False,
}

TABLE = """
CREATE TABLE IF NOT EXISTS work(
  id TEXT PRIMARY KEY, project_id TEXT, user_id TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'agent',
  status TEXT NOT NULL, request TEXT NOT NULL DEFAULT '', answer TEXT, question TEXT, options TEXT,
  progress TEXT, say TEXT, claude_session TEXT, key TEXT, created REAL NOT NULL, updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS work_project ON work(project_id, updated);
"""


def ensure(db) -> None:
    con = db.connect()
    try:
        con.executescript(TABLE)
    finally:
        con.close()


def brief() -> str:
    """The agent's standing instructions (who it is, how it works, its quality bar)."""
    return (Path(__file__).with_name("brief.md")).read_text(encoding="utf-8")


class Sessions:
    """The running work sessions of one studio (a thread each; one per film at a time)."""

    def __init__(self, studio):
        self.studio = studio
        self.tokens: dict[str, dict] = {}  # agent token -> {work, user_id, project_id}
        self.procs: dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()
        self.screen: list[tuple[float, str, dict]] = []  # what the agent did on their screen: (when, person, ui)
        self.recovered = self._recover()

    def _recover(self) -> int:
        """Work that was running when Flicello last stopped can't carry on (its process and threads are gone): it is
        marked failed, plainly, so the film's session is free again; what was waiting behind it is set aside too
        (they ask again). A question still open stays open: its answer resumes the same Claude session."""
        db, now = self.studio.db, time.time()
        cut = db.all("SELECT id FROM work WHERE status IN ('running','queued')")
        db.run("UPDATE work SET status='failed', say=?, updated=? WHERE status='running'",
               ("Flicello was restarted while I was on this, so it stopped part way. Ask me again and I'll pick it up.",
                now))
        db.run("UPDATE work SET status='stopped', say=?, updated=? WHERE status='queued'",
               ("Not started: Flicello was restarted before it could.", now))
        return len(cut)

    # ------------------------------------------------------------ the person's side
    def start(self, user: dict, project: dict | None, request: str) -> dict:
        """Hand them a request. Returns the work row (queued when the film's session is busy)."""
        request = " ".join((request or "").split())[:4000]
        if not request:
            raise ValueError("Say what to do.")
        pid = project["id"] if project else None
        db = self.studio.db
        # a question of its own still open, and they ask for something else: the new request wins (the same Claude
        # session carries on, so nothing it knew is lost)
        db.run("UPDATE work SET status='stopped', say='Set aside for what they asked next.', updated=? "
               "WHERE project_id IS ? AND kind='agent' AND status='asking'", (time.time(), pid))
        busy = db.one("SELECT * FROM work WHERE project_id IS ? AND kind='agent' AND status='running' "
                      "ORDER BY created DESC LIMIT 1", (pid,))
        wid = "w" + secrets.token_hex(6)
        now = time.time()
        db.run("INSERT INTO work(id, project_id, user_id, status, request, created, updated) VALUES (?,?,?,?,?,?,?)",
               (wid, pid, user["id"], "queued" if busy else "running", request, now, now))
        if not busy:
            self._launch(wid)
        return db.one("SELECT * FROM work WHERE id=?", (wid,))

    def answer(self, user: dict, wid: str, text: str) -> dict:
        """Their answer to a question: the same Claude session carries on with it."""
        db = self.studio.db
        row = db.one("SELECT * FROM work WHERE id=? AND user_id=?", (wid, user["id"]))
        if row is None:
            raise ValueError("That question isn't open any more.")
        text = " ".join((text or "").split())[:2000]
        if row["status"] != "asking" or not text:
            raise ValueError("There's no open question to answer.")
        if row["kind"] == "system":  # one of Flicello's own questions (asks.py)
            from . import asks

            return asks.answer(self.studio, user, row, text)
        db.run("UPDATE work SET status='running', answer=?, question=NULL, options=NULL, updated=? WHERE id=?",
               (text, time.time(), wid))
        self._launch(wid, f"My answer to your question: {text}")
        return db.one("SELECT * FROM work WHERE id=?", (wid,))

    def stop(self, user: dict, wid: str) -> None:
        db = self.studio.db
        row = db.one("SELECT * FROM work WHERE id=? AND user_id=?", (wid, user["id"]))
        if row is None:
            raise ValueError("No such work.")
        proc = self.procs.get(wid)
        db.run("UPDATE work SET status='stopped', updated=? WHERE id=?", (time.time(), wid))
        if proc and proc.poll() is None:
            proc.terminate()  # its run ends and hands over to what waits (_run)
        elif row["status"] == "asking":  # nothing running: what waits behind it starts now
            self._next(row)

    # ------------------------------------------------------------ the agent's side
    def by_token(self, token: str | None) -> dict | None:
        return self.tokens.get(token or "") if token else None

    def show(self, user_id: str, ui: dict, pid: str | None = None) -> None:
        """Something the agent did that their screen should show (a film opened, a draft on its way), as Cello's own
        tools show it: their Cello shell picks it up with its next look at the film's events."""
        with self.lock:
            self.screen = [*self.screen[-49:], (time.time(), user_id, {**ui, **({"pid": pid} if pid else {})})]

    def shown_since(self, user_id: str, since: float) -> list[dict]:
        with self.lock:
            return [ui for t, uid, ui in self.screen if uid == user_id and t > since]

    def progress(self, wid: str, text: str) -> None:
        self.studio.db.run("UPDATE work SET progress=?, updated=? WHERE id=?", (" ".join(text.split())[:300], time.time(), wid))

    # ------------------------------------------------------------ running it
    def folder(self, pid: str | None, user_id: str) -> Path:
        return self.studio.s.home / "work" / (pid or f"home-{user_id}")

    def _launch(self, wid: str, message: str | None = None) -> None:
        threading.Thread(target=self._run, args=(wid, message), daemon=True, name=f"work-{wid}").start()

    def _run(self, wid: str, message: str | None) -> None:
        db = self.studio.db
        row = db.one("SELECT * FROM work WHERE id=?", (wid,))
        if row is None:
            return
        folder = self.folder(row["project_id"], row["user_id"])
        folder.mkdir(parents=True, exist_ok=True)
        self._tools(folder)
        token = secrets.token_urlsafe(24)
        with self.lock:
            self.tokens[token] = {"work": wid, "user_id": row["user_id"], "project_id": row["project_id"]}
        session = self._last_session(row)
        prompt = message or self._prompt(row)
        (folder / "system.md").write_text(brief(), encoding="utf-8")
        from ..local import claude_exe, health, plan_env

        cmd = [claude_exe(), "-p", "--output-format", "json", "--model", MODEL, "--system-prompt-file",
               str(folder / "system.md"), "--setting-sources", "", "--strict-mcp-config", "--max-budget-usd", BUDGET_USD,
               "--json-schema", json.dumps(SCHEMA), "--tools", "Read,Write,Edit,Bash",
               "--allowedTools", "Read(./**)", "Write(./**)", "Edit(./**)", "Bash(bash fl:*)", "Bash(bash look:*)",
               "Bash(bash paint:*)"]
        if session:
            cmd += ["--resume", session]

        env = {**plan_env(), "FLICELLO_URL": f"http://127.0.0.1:{self.studio.s.port}", "FLICELLO_AGENT": token,
               "FLICELLO_WORK": wid, "FLICELLO_FILM": row["project_id"] or "", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "64000",
               "PYTHONIOENCODING": "utf-8"}
        out_path, err_path = folder / f"{wid}.out.json", folder / f"{wid}.err.log"
        t0 = time.time()
        if db.one("SELECT status FROM work WHERE id=?", (wid,))["status"] == "stopped":  # stopped as it was starting
            with self.lock:
                self.tokens.pop(token, None)
            self._next(row)
            return
        try:
            given = folder / f"{wid}.prompt.txt"  # read as its stdin from a file: see local.run_claude
            given.write_text(prompt, encoding="utf-8")
            with open(out_path, "wb") as out, open(err_path, "ab") as err, open(given, "rb") as stdin:
                proc = subprocess.Popen(cmd, stdin=stdin, stdout=out, stderr=err, cwd=str(folder), env=env)
                self.procs[wid] = proc
                proc.wait(timeout=TIMEOUT)
            result = json.loads(out_path.read_text(encoding="utf-8") or "{}")
        except subprocess.TimeoutExpired:
            proc.kill()
            result = {"is_error": True, "result": f"it ran past {TIMEOUT // 3600} hours"}
        except (OSError, ValueError) as exc:
            result = {"is_error": True, "result": f"{type(exc).__name__}: {exc}"}
        finally:
            with self.lock:
                self.tokens.pop(token, None)
            self.procs.pop(wid, None)
        if db.one("SELECT status FROM work WHERE id=?", (wid,))["status"] == "stopped":
            self._next(row)
            return
        said = result.get("structured_output") if isinstance(result.get("structured_output"), dict) else {}
        text = str(result.get("result") or "")
        if result.get("is_error") or not said:
            health.failed("claude_code", text)
            status, say = "failed", ("I couldn't finish that: " + (health.explain(text)[0] if text else "it stopped"))
        else:
            health.worked("claude_code")
            status, say = said.get("status", "done"), said.get("say", "")
        question = said.get("question") if status == "question" else None
        options = json.dumps(said.get("options") or [])[:2000] if status == "question" else None
        db.run("UPDATE work SET status=?, say=?, question=?, options=?, claude_session=?, updated=? WHERE id=?",
               ("asking" if status == "question" else status, " ".join(say.split())[:1000], question, options,
                result.get("session_id") or session, time.time(), wid))
        log.info("work %s %s in %.0fs", wid, status, time.time() - t0)
        if status != "question":
            self._next(row)

    def _next(self, row: dict) -> None:
        """The next request waiting for this film's session."""
        db = self.studio.db
        nxt = db.one("SELECT id FROM work WHERE project_id IS ? AND status='queued' ORDER BY created LIMIT 1",
                     (row["project_id"],))
        if nxt:
            db.run("UPDATE work SET status='running', updated=? WHERE id=?", (time.time(), nxt["id"]))
            self._launch(nxt["id"])

    def _last_session(self, row: dict) -> str | None:
        """The film's Claude session, so it remembers everything said and done about it."""
        if row["claude_session"]:
            return row["claude_session"]
        prev = self.studio.db.one("SELECT claude_session FROM work WHERE project_id IS ? AND user_id=? AND "
                                  "claude_session IS NOT NULL ORDER BY updated DESC LIMIT 1",
                                  (row["project_id"], row["user_id"]))
        return prev["claude_session"] if prev else None

    def _prompt(self, row: dict) -> str:
        film = self.studio.db.project(row["project_id"]) if row["project_id"] else None
        where = f'the film "{film["title"]}" ({film["stage"]})' if film else "their home screen (no film open)"
        return (f"They are on {where}. They said to Cello: \"{row['request']}\"\n"
                "Do it. Start with `bash fl state` (what is on screen and what can be done next). `bash fl help` "
                "lists every tool with its arguments; tools are added as Flicello grows, so check it whenever you're "
                "unsure how to do something, and never tell them something can't be done before you have.")

    def _tools(self, folder: Path) -> None:
        """The agent's commands in its folder: `bash fl ...` (Flicello), `bash look`/`bash paint` (scene desks make
        their own)."""
        py = Path(sys.executable).as_posix()
        (folder / "fl").write_text(f'#!/bin/bash\n"{py}" -m vidosuper.work.fl "$@"\n', encoding="utf-8")
        for name in ("look", "paint"):  # only meaningful inside a scene desk; harmless here
            path = folder / name
            if not path.exists():
                path.write_text('#!/bin/bash\necho "Use bash fl scene to design a scene; look and paint work at its desk."\n',
                                encoding="utf-8")


def available() -> bool:
    return shutil.which("claude") is not None and bool(os.environ.get("PATH"))
