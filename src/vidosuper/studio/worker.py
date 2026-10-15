"""The worker: takes jobs off the queue one at a time and keeps the studio healthy.

Every few seconds it beats a heartbeat (the admin page shows how long ago), and every few minutes it looks
after the house: jobs stuck "running" after a crash go back in the queue, abandoned uploads are swept,
projects past the retention time are deleted with their footage, and low disk or a nearly spent AI budget
raises an alert.
"""

from __future__ import annotations

import logging
import threading
import time

from .alerts import alert
from .pipeline import remove_project_files
from .service import Studio
from .storage import free_gb, sweep_uploads, workdir

log = logging.getLogger("vidosuper.studio")
STUCK_SECONDS = 3 * 3600


def recover(studio: Studio) -> int:
    """Jobs left running by a stopped worker go back in the queue (their steps are safe to redo)."""
    rows = studio.db.all("SELECT id FROM jobs WHERE status='running'")
    for row in rows:
        studio.db.requeue(row["id"])
    return len(rows)


def watch_stages(studio: Studio) -> None:
    """Looks at every stage the way the owner would: a step that is taking far longer than such steps usually do, a
    video that failed, a person who rejected a sample twice. Each problem is noted once, and starts the improvement
    routine (which looks at it, fixes what it can in a branch, and deploys if the checks pass)."""
    from .improve import maybe_start

    db, s = studio.db, studio.s
    now = time.time()
    seen = {r["message"] for r in db.all("SELECT message FROM alerts WHERE kind='watch' AND at > ?", (now - 7 * 86400,))}
    problems: list[tuple[str, str]] = []
    for job in db.all("SELECT * FROM jobs WHERE status='running' AND started IS NOT NULL"):
        typical = studio.typical_seconds(job["kind"])
        took = now - job["started"]
        if took > max(900.0, 4 * typical):
            problems.append((f"slow job {job['id']}", f"A {job['kind']} step has run for {took / 60:.0f} minutes "
                                                      f"(usually {typical / 60:.0f}); it may be stuck."))
    for job in db.all("SELECT * FROM jobs WHERE status='failed' AND finished > ? AND kind != 'learn'", (now - 86400,)):
        problems.append((f"failed job {job['id']}", f"A {job['kind']} step failed: {(job['error'] or '')[:160]}"))
    for p in db.all("SELECT * FROM projects WHERE deleted=0 AND stage='review' AND sample_tries >= 2 AND updated > ?",
                    (now - 86400,)):
        problems.append((f"unhappy {p['id']}", f"Someone rejected the sample of video {p['id']} {p['sample_tries']} times: "
                                               "what they asked for is not getting through."))
    for r in db.all("SELECT id, project_id, findings FROM renders WHERE kind='final' AND created > ?", (now - 86400,)):
        import json as _json

        bad = [f["detail"] for f in _json.loads(r["findings"] or "[]") if f.get("check") == "requirement" and f.get("level") != "ok"]
        if bad:
            problems.append((f"requirement {r['id']}", f"Video {r['project_id']} did not do what was asked: " + "; ".join(bad)[:200]))
    fresh = [(k, m) for k, m in problems if f"[{k}] {m}" not in seen]
    for k, m in fresh:
        alert(db, s, "watch", f"[{k}] {m}", "warning")
    if fresh:
        maybe_start(studio, reason="; ".join(m for _, m in fresh)[:200])


def maintain(studio: Studio) -> None:
    db, s = studio.db, studio.s
    now = time.time()
    for job in db.all("SELECT id, project_id FROM jobs WHERE status='running' AND started < ?", (now - STUCK_SECONDS,)):
        db.job_done(job["id"], "failed", "ran for more than 3 hours")
        if job["project_id"]:
            db.set_stage(job["project_id"], "failed", error="This step took far too long. The owner has been told.")
        alert(db, s, "stuck", "A job ran for more than 3 hours and was stopped.")
    sweep_uploads(db, s)
    cutoff = now - s.limits.retention_days * 86400
    old = db.all("SELECT p.* FROM projects p JOIN users u ON u.id = p.user_id WHERE p.deleted=0 AND p.updated < ? "
                 "AND u.role != 'owner'", (cutoff,))
    for p in old:
        if db.active_job(p["id"]):
            continue
        remove_project_files(workdir(s, p["user_id"], p["id"]), studio.cfg)
        db.run("UPDATE projects SET deleted=1 WHERE id=?", (p["id"],))
        db.run("DELETE FROM clips WHERE project_id=?", (p["id"],))
        log.info("retention: deleted project %s", p["id"])
    free = free_gb(s)
    if free < s.limits.min_free_gb:
        alert(db, s, "disk", f"Only {free:.0f} GB of disk left: new uploads and jobs wait until there is room.")


class Worker:
    def __init__(self, studio: Studio, name: str = "worker"):
        self.studio = studio
        self.name = name
        self.stop = threading.Event()
        self._last_maintenance = 0.0

    def tick(self) -> bool:
        """One round: heartbeat, maintenance when due, and at most one job. True if a job ran."""
        db, s = self.studio.db, self.studio.s
        db.heartbeat(self.name, {"at": time.time()})
        if time.time() - self._last_maintenance > 300:
            self._last_maintenance = time.time()
            try:
                maintain(self.studio)
                watch_stages(self.studio)
            except Exception as exc:  # noqa: BLE001 - housekeeping must never stop the queue
                log.exception("maintenance failed")
                alert(db, s, "maintenance", f"Housekeeping failed: {exc}", "warning")
        if free_gb(s) < s.limits.min_free_gb * 0.5:
            return False  # nearly out of disk: renders would fail half-way; wait (the alert is out)
        job = db.claim()
        if job is None:
            return False
        log.info("job %s %s (project %s, try %s)", job["id"], job["kind"], job["project_id"], job["attempts"])
        started = time.monotonic()
        self.studio.run_job(job)
        log.info("job %s finished in %.0fs", job["id"], time.monotonic() - started)
        return True

    def run(self) -> None:
        recovered = recover(self.studio)
        if recovered:
            log.warning("put %d interrupted job(s) back in the queue", recovered)
        while not self.stop.is_set():
            try:
                busy = self.tick()
            except Exception:  # noqa: BLE001 - the loop itself must survive anything
                log.exception("worker loop error")
                busy = False
            if not busy:
                self.stop.wait(2.0)

    def start_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name=self.name, daemon=True)
        t.start()
        return t
