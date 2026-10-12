"""The studio's rules of play, shared by the web app and the worker: what a person may do at each stage,
which job that starts, and what each job does.

A video moves through stages:

    setup -> preparing -> choose -> sampling -> review -> rendering -> done (-> revising -> done)
    choose -> pitching -> choose (another idea, or their own);  review -> sampling (with their note)

and `paused` (an AI budget is used up: the owner is told) or `failed` (they can try again). Each busy
stage is one job in the queue; the worker runs them one at a time.
"""

from __future__ import annotations

import json
import secrets
import time
import traceback
from dataclasses import asdict

from .alerts import alert
from .brain import BrainError, BudgetExceeded, Usage, brain_kind_for, make_brain
from .brain.context import look_from_note, pace_from_note
from .brain.schemas import DesignPlan, StoryPitch
from .db import DB
from .pipeline import Pipeline, UserError, remove_project_files, studio_config
from .profiles import Profile, owner_seed_rules
from .settings import StudioSettings
from .storage import UploadError, user_dir, workdir

STAGES = {
    "setup": "Add your clips and a song",
    "preparing": "Watching your clips",
    "choose": "Pick a story",
    "pitching": "Thinking of another idea",
    "sampling": "Cutting a sample",
    "review": "Check the sample",
    "rendering": "Making your video",
    "done": "Your video is ready",
    "revising": "Making your changes",
    "paused": "Paused",
    "failed": "Something went wrong",
}
BUSY = {"preparing", "pitching", "sampling", "rendering", "revising"}
JOB_STAGE = {"prepare": "preparing", "pitch": "pitching", "sample": "sampling", "render": "rendering",
             "revise": "revising"}
PRIORITY = {"pitch": 2, "sample": 3, "learn": 4, "prepare": 5, "render": 6, "revise": 6}  # short jobs first
FEEDBACK_TAGS = ["too fast", "too slow", "boring start", "wrong music", "bad shots", "shaky", "too short",
                 "too long", "didn't follow my idea", "colour", "sound"]
LENGTHS = (15, 30, 45, 60)
MAX_SAMPLE_TRIES = 3
TYPICAL_SECONDS = {"prepare": 240, "pitch": 70, "sample": 100, "render": 300, "revise": 180}  # first guesses, until real jobs have run


class ActionError(ValueError):
    """Shown to the person as is."""


class Studio:
    def __init__(self, s: StudioSettings):
        self.s = s.ensure()
        self.db = DB(s.db_path)
        self.cfg = studio_config(s.workspace, s.render_preset, s.models_dir)

    # ------------------------------------------------------------------ people
    def user(self, uid: str) -> dict | None:
        return self.db.one("SELECT * FROM users WHERE id=?", (uid,))

    @staticmethod
    def is_owner(user: dict) -> bool:
        return user["role"] == "owner"

    def profile(self, user: dict) -> Profile:
        p = Profile.load(user_dir(self.s, user["id"]) / "profile.json")
        if self.is_owner(user) and not p.rules and not p.history:
            p.rules = owner_seed_rules()
            p.history.append({"at": time.strftime("%Y-%m-%d"), "summary": "Started from your known rules.",
                              "added": [r["id"] for r in p.rules], "dropped": []})
            p.save()
        return p

    def videos_left(self, user: dict) -> int | None:
        if self.is_owner(user):
            return None
        allowed = user["videos_allowed"] if user["videos_allowed"] is not None else self.s.limits.videos_per_user
        # a video counts once it exists; deleting an unfinished one gives the place back, a finished one doesn't
        made = self.db.value("SELECT COUNT(*) FROM projects WHERE user_id=? AND (deleted=0 OR version>=1)",
                             (user["id"],), 0)
        return max(0, allowed - made)

    # ------------------------------------------------------------------ projects
    def projects(self, user: dict) -> list[dict]:
        return self.db.all("SELECT * FROM projects WHERE user_id=? AND deleted=0 ORDER BY created DESC", (user["id"],))

    def owned(self, user: dict, pid: str) -> dict:
        p = self.db.project(pid)
        if p is None or p["user_id"] != user["id"]:
            raise ActionError("No such video.")
        return p

    def create_project(self, user: dict, title: str, shapes: str, length: float, intent: str, pace: str = "balanced") -> dict:
        if (left := self.videos_left(user)) is not None and left <= 0:
            raise ActionError("You've used all your videos for this trial. Thank you!")
        if shapes not in ("9:16", "16:9", "both"):
            raise ActionError("Pick a shape.")
        if int(length) not in LENGTHS:
            raise ActionError("Pick a length.")
        if pace not in ("calm", "balanced", "fast", "every"):
            raise ActionError("Pick how fast it should cut.")
        pace = pace_from_note(pace, intent)
        pid = "p" + secrets.token_hex(6)
        now = time.time()
        title = " ".join(title.split())[:60] or time.strftime("My video, %d %b")
        self.db.run("INSERT INTO projects(id, user_id, title, shapes, length, intent, stage, created, updated, pace) "
                    "VALUES (?,?,?,?,?,?,'setup',?,?,?)", (pid, user["id"], title, shapes, float(length),
                                                           " ".join(intent.split())[:600], now, now, pace))
        workdir(self.s, user["id"], pid).ensure()
        return self.db.project(pid)

    def shapes(self, project: dict) -> list[str]:
        return ["9:16", "16:9"] if project["shapes"] == "both" else [project["shapes"]]

    def delete_project(self, project: dict) -> None:
        for job in self.db.all("SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','waiting')", (project["id"],)):
            self.db.job_done(job["id"], "cancelled")
        if self.db.one("SELECT id FROM jobs WHERE project_id=? AND status='running'", (project["id"],)):
            raise ActionError("Wait a moment: this video is being worked on. Try again when the step finishes.")
        remove_project_files(workdir(self.s, project["user_id"], project["id"]), self.cfg)
        self.db.run("UPDATE projects SET deleted=1, updated=? WHERE id=?", (time.time(), project["id"]))
        self.db.run("DELETE FROM clips WHERE project_id=?", (project["id"],))

    def delete_everything(self, user: dict) -> None:
        """Every project and file of this person (their account stays, so they can still sign in)."""
        for p in self.projects(user):
            self.delete_project(p)
        prof = user_dir(self.s, user["id"]) / "profile.json"
        prof.unlink(missing_ok=True)

    # ------------------------------------------------------------------ actions
    def _guard(self, project: dict, *stages: str) -> None:
        if self.db.active_job(project["id"]):
            raise ActionError("Still working on the last step; give it a moment.")
        if stages and project["stage"] not in stages:
            raise ActionError("That can't be done at this step.")

    def _enqueue(self, project: dict, kind: str, params: dict | None = None) -> int:
        user = self.user(project["user_id"])
        if kind in JOB_STAGE and user and not self.is_owner(user):
            busy = self.db.value("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status IN ('queued','running','waiting') "
                                 "AND kind != 'learn'", (user["id"],), 0)
            if busy >= self.s.limits.active_jobs_per_user:
                raise ActionError("One video at a time: wait for your other video's step to finish.")
        job = self.db.enqueue(kind, project["id"], project["user_id"], params, PRIORITY.get(kind, 5))
        if kind in JOB_STAGE:
            self.db.set_stage(project["id"], JOB_STAGE[kind])
        return job

    def extend_song(self, project: dict) -> dict:
        """Repeat the strongest bars of a song that is shorter than the video asked for (see songfit.py). The upload is
        kept next to the new file. Runs inside the web request (a few seconds for a short song)."""
        from ..music import load_or_analyze  # noqa: F401 - analysis is cached under the project's workspace
        from .pipeline import Pipeline
        from .songfit import SongTooShort, extend_song

        self._guard(project, "setup")
        work = workdir(self.s, project["user_id"], project["id"]).ensure()
        song = work.song_path()
        if song is None:
            raise ActionError("Add a song first.")
        music_cache = Pipeline(work, self.cfg, None).project.music
        if song.stem != "song" or song.suffix == ".part":
            raise ActionError("That song can't be extended.")
        original = work.music / f"original-song{song.suffix}"
        target = work.music / "extended.wav"
        try:
            info = extend_song(song, target, project["length"], music_cache)
        except SongTooShort as exc:
            raise ActionError(str(exc)) from exc
        except ValueError as exc:
            raise ActionError(str(exc)) from exc
        if not original.exists():
            song.replace(original)  # keep the upload (the song glob only matches song.*)
        else:
            song.unlink()
        target.replace(work.music / "song.wav")
        name = (project["song"] or "song").rsplit(" (extended)", 1)[0] + " (extended)"
        self.db.run("UPDATE projects SET song=?, song_seconds=?, updated=? WHERE id=?",
                    (name, info["seconds"], time.time(), project["id"]))
        return info

    def typical_seconds(self, kind: str) -> float:
        """How long this kind of job usually takes here: the median of the last finished ones, else a first guess."""
        rows = self.db.all("SELECT finished - started AS d FROM jobs WHERE kind=? AND status='done' AND started IS NOT NULL "
                           "AND finished IS NOT NULL ORDER BY id DESC LIMIT 15", (kind,))
        spent = sorted(r["d"] for r in rows if r["d"] and r["d"] > 5)
        return float(spent[len(spent) // 2]) if spent else float(TYPICAL_SECONDS.get(kind, 120))

    def eta(self, job: dict | None) -> int | None:
        """Seconds left for the running job: what such jobs usually take, corrected by how far this one has got."""
        if not job:
            return None
        typical = self.typical_seconds(job["kind"])
        if job["status"] != "running" or not job["started"]:
            return int(typical)
        elapsed = time.time() - job["started"]
        by_history = max(15.0, typical - elapsed)
        p = float(job["progress"] or 0.0)
        if p >= 0.1 and elapsed > 8:
            return int(p * (elapsed * (1 - p) / p) + (1 - p) * by_history)
        return int(by_history)

    def total_estimate(self) -> int:
        """Start to a finished video, in seconds, for the page that asks to start."""
        return int(sum(self.typical_seconds(k) for k in ("prepare", "pitch", "sample", "render")))

    def set_music(self, project: dict, choice: str) -> None:
        """With no song uploaded: a beat made for the video ('made'), or no music at all ('none')."""
        self._guard(project, "setup")
        if choice not in ("made", "none"):
            raise ActionError("Pick a beat made for you, or no music.")
        self.db.run("UPDATE projects SET music=?, updated=? WHERE id=?", (choice, time.time(), project["id"]))

    def set_length(self, project: dict, length: int) -> None:
        self._guard(project, "setup")
        if int(length) not in LENGTHS:
            raise ActionError("Pick one of the lengths.")
        self.db.run("UPDATE projects SET length=?, updated=? WHERE id=?", (float(length), time.time(), project["id"]))

    def start(self, project: dict) -> None:
        self._guard(project, "setup")
        if not self.db.value("SELECT COUNT(*) FROM clips WHERE project_id=?", (project["id"],), 0):
            raise ActionError("Add at least one clip first.")
        footage = float(self.db.value("SELECT COALESCE(SUM(seconds), 0) FROM clips WHERE project_id=?", (project["id"],), 0.0))
        if footage < 0.6 * project["length"]:
            raise ActionError(f"That's {footage:.0f} s of footage for a {project['length']:.0f} s video: add more clips, "
                              "or pick a shorter length.")
        if footage > self.s.limits.project_seconds:
            raise ActionError(f"That's {footage / 60:.0f} minutes of footage; the most a video can use is "
                              f"{self.s.limits.project_seconds / 60:.0f}. Remove some clips.")
        self._enqueue(project, "prepare")

    def choose(self, project: dict, pitch_id: int) -> None:
        self._guard(project, "choose")
        row = self.db.one("SELECT id FROM pitches WHERE id=? AND project_id=?", (pitch_id, project["id"]))
        if not row:
            raise ActionError("Pick one of the ideas.")
        self.db.run("UPDATE projects SET chosen=?, sample_tries=0 WHERE id=?", (pitch_id, project["id"]))
        self._enqueue(project, "sample", {"pitch": pitch_id})

    def another(self, project: dict, reason: str = "", own_idea: str = "") -> None:
        self._guard(project, "choose")
        if self.db.value("SELECT COUNT(*) FROM pitches WHERE project_id=?", (project["id"],), 0) >= 9:
            raise ActionError("That's plenty of ideas: pick one, or write your own.")
        self._enqueue(project, "pitch", {"reason": " ".join(reason.split())[:300], "own": " ".join(own_idea.split())[:600]})

    def sample_verdict(self, user: dict, project: dict, ok: bool, note: str = "") -> None:
        self._guard(project, "review")
        note = " ".join(note.split())[:600]
        self.db.run("INSERT INTO feedback(project_id, user_id, stage, version, liked, text, created) VALUES (?,?,?,?,?,?,?)",
                    (project["id"], user["id"], "sample", project["version"], 1 if ok else 0, note, time.time()))
        if ok:
            self._enqueue(project, "render")
            return
        if project["sample_tries"] >= MAX_SAMPLE_TRIES:
            raise ActionError("Let's try a different story instead: go back and pick another idea.")
        if not note:
            raise ActionError("Say what to change, so the next sample is better.")
        self.db.run("UPDATE projects SET sample_tries=sample_tries+1 WHERE id=?", (project["id"],))
        self._enqueue(project, "sample", {"pitch": project["chosen"], "note": note})

    def back_to_ideas(self, project: dict) -> None:
        self._guard(project, "review")
        self.db.set_stage(project["id"], "choose")

    def rate(self, user: dict, project: dict, match: int, liked: int, tags: list[str], text: str, fix: str) -> None:
        self._guard(project, "done")
        tags = [t for t in tags if t in FEEDBACK_TAGS]
        self.db.run("INSERT INTO feedback(project_id, user_id, stage, version, match, liked, tags, text, created) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", (project["id"], user["id"], "final", project["version"],
                                                   max(1, min(5, match)), max(1, min(10, liked)), json.dumps(tags),
                                                   " ".join(text.split())[:1000], time.time()))
        self.db.enqueue("learn", project["id"], user["id"], {}, PRIORITY["learn"])
        fix = " ".join(fix.split())[:1000]
        if fix:
            if project["revisions"] >= self.s.limits.revisions_per_video and not self.is_owner(user):
                raise ActionError("Thanks! That was the last round of changes for this video.")
            self.db.run("UPDATE projects SET revisions=revisions+1 WHERE id=?", (project["id"],))
            self._enqueue(project, "revise", {"feedback": fix, "tags": tags})

    def retry(self, project: dict) -> None:
        if project["stage"] not in ("failed", "paused"):
            raise ActionError("Nothing to retry.")
        job = self.db.one("SELECT * FROM jobs WHERE project_id=? AND status IN ('failed','waiting') ORDER BY id DESC LIMIT 1",
                          (project["id"],))
        if not job:
            raise ActionError("Nothing to retry.")
        if self.db.active_job(project["id"]) and job["status"] != "waiting":
            raise ActionError("Still working on the last step; give it a moment.")
        self.db.run("UPDATE jobs SET attempts=0 WHERE id=?", (job["id"],))
        self.db.requeue(job["id"])
        self.db.set_stage(project["id"], JOB_STAGE.get(job["kind"], project["stage"]))

    # ------------------------------------------------------------------ reading state for the pages
    def pitches(self, project: dict) -> list[dict]:
        rows = self.db.all("SELECT * FROM pitches WHERE project_id=? ORDER BY id", (project["id"],))
        return [{"id": r["id"], "round": r["round"], "rejected": r["rejected"], "pitch": json.loads(r["data"]),
                 "stills": json.loads(r["stills"])} for r in rows]

    def latest_plan(self, project: dict) -> dict | None:
        return self.db.one("SELECT * FROM plans WHERE project_id=? ORDER BY id DESC LIMIT 1", (project["id"],))

    def renders(self, project: dict, kind: str) -> list[dict]:
        return self.db.all("SELECT * FROM renders WHERE project_id=? AND kind=? ORDER BY id DESC", (project["id"], kind))

    # ------------------------------------------------------------------ jobs
    def _spender(self, project: dict | None, user_id: str | None, job_id: int):
        def spend(u: Usage) -> None:
            self.db.run("INSERT INTO spend(at, user_id, project_id, job_id, task, model, backend, tin, tout, cw, cr, usd, "
                        "billed, seconds) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (time.time(), user_id, project["id"] if project else None, job_id, u.task, u.model, u.backend,
                         u.input_tokens, u.output_tokens, u.cache_write, u.cache_read, u.usd, int(u.billed), u.seconds))
            if not u.billed:
                return
            month = self.db.month_spend()
            cap = self.s.limits.monthly_usd
            if month >= 0.8 * cap:
                alert(self.db, self.s, "budget", f"AI spend this month is ${month:.2f} of the ${cap:.2f} cap.", "warning")
            if month >= cap:
                raise BudgetExceeded(f"the monthly AI budget (${cap:.2f}) is used up")
            if project and self.db.project_spend(project["id"]) >= self.s.limits.video_usd:
                raise BudgetExceeded(f"this video reached its AI budget (${self.s.limits.video_usd:.2f})")
        return spend

    def _pipeline(self, job: dict, project: dict, user: dict, lo: float = 0.0, hi: float = 1.0) -> Pipeline:
        owner = self.is_owner(user)
        work = workdir(self.s, user["id"], project["id"])

        def progress(f: float, msg: str) -> None:
            self.db.job_progress(job["id"], lo + (hi - lo) * f, msg)

        pipe = Pipeline(work, self.cfg, None, progress, owner=owner)
        pipe.no_music = project["music"] == "none" and not project["song"]
        kind = brain_kind_for(self.s, owner)
        pipe.brain = make_brain(kind, self.s, self._spender(project, user["id"], job["id"]), work.brain,
                                pipe.project.load_signals)
        return pipe

    def _context(self, pipe: Pipeline, project: dict, user: dict):
        return pipe.context(self.shapes(project), project["length"], project["intent"], self.profile(user).rules,
                            project["pace"])

    def run_job(self, job: dict) -> None:
        project = self.db.project(job["project_id"]) if job["project_id"] else None
        user = self.user(job["user_id"]) if job["user_id"] else None
        if project is None or user is None:
            self.db.job_done(job["id"], "cancelled", "project or person gone")
            return
        handler = getattr(self, f"_job_{job['kind']}")
        try:
            if job["kind"] in JOB_STAGE and self.s.limits.monthly_usd <= self.db.month_spend() \
                    and brain_kind_for(self.s, self.is_owner(user)) == "api":
                raise BudgetExceeded("the monthly AI budget is used up")
            handler(job, project, user)
            self.db.job_done(job["id"])
        except BudgetExceeded as exc:
            self.db.run("UPDATE jobs SET status='waiting', error=? WHERE id=?", (str(exc), job["id"]))
            if job["kind"] in JOB_STAGE:
                self.db.set_stage(project["id"], "paused", note=f"Paused: {exc}. The owner has been told.")
            alert(self.db, self.s, "budget", f"A job is waiting: {exc}.")
        except Exception as exc:  # noqa: BLE001 - every failure is recorded, retried once, then shown
            detail = traceback.format_exc()
            self.db.event(project["id"], f"{job['kind']} failed (try {job['attempts']}): {type(exc).__name__}: "
                          f"{' '.join(str(exc).split())[:200]}\n{detail[-3000:]}", "error", job["id"])
            user_fixable = isinstance(exc, (UploadError, ActionError, UserError))  # only these have words made for people
            if job["attempts"] < 2 and not user_fixable:
                self.db.requeue(job["id"], delay=20.0, error=str(exc)[:500])
                return
            self.db.job_done(job["id"], "failed", str(exc)[:500])
            if job["kind"] in JOB_STAGE:
                message = str(exc) if user_fixable else "Something went wrong on our side. The owner has been told; you can try again."
                self.db.set_stage(project["id"], "failed", error=message[:300])
            alert(self.db, self.s, "job", f"{job['kind']} failed for a video: {type(exc).__name__}: {str(exc)[:200]}")

    # the job handlers -------------------------------------------------
    def _save_pitches(self, project: dict, pitches: list[dict], rnd: int) -> None:
        for p in pitches:
            self.db.run("INSERT INTO pitches(project_id, round, data, stills, created) VALUES (?,?,?,?,?)",
                        (project["id"], rnd, json.dumps(p["pitch"]), json.dumps(p["stills"]), time.time()))
        self.db.run("UPDATE projects SET pitch_round=? WHERE id=?", (rnd, project["id"]))

    def _job_prepare(self, job, project, user):
        pipe = self._pipeline(job, project, user, 0.0, 0.8)
        info = pipe.analyse(project["length"], project["pace"])
        self.db.run("UPDATE projects SET info=? WHERE id=?", (json.dumps(info), project["id"]))
        if info["skipped"]:
            self.db.event(project["id"], f"{len(info['skipped'])} file(s) couldn't be read and were left out.", "warn")
        pipe.progress = lambda f, m: self.db.job_progress(job["id"], 0.8 + 0.2 * f, m)
        ctx = self._context(pipe, project, user)
        self._save_pitches(project, pipe.pitch(ctx, n=3, tag="r1"), 1)
        self.db.set_stage(project["id"], "choose")

    def _job_pitch(self, job, project, user):
        pipe = self._pipeline(job, project, user)
        ctx = self._context(pipe, project, user)
        old = [StoryPitch.model_validate(p["pitch"]) for p in self.pitches(project)]
        rnd = project["pitch_round"] + 1
        own = job["params"].get("own", "")
        self._save_pitches(project, pipe.pitch(ctx, n=1, rejected=old, reason=job["params"].get("reason", ""),
                                               own_idea=own, tag=f"r{rnd}"), rnd)
        self.db.set_stage(project["id"], "choose")

    def _chosen(self, project: dict, pitch_id: int | None) -> tuple[int, StoryPitch]:
        row = self.db.one("SELECT * FROM pitches WHERE id=? AND project_id=?", (pitch_id or project["chosen"], project["id"]))
        if row is None:
            raise ActionError("Pick a story first.")
        return row["id"], StoryPitch.model_validate(json.loads(row["data"]))

    def _job_sample(self, job, project, user):
        pipe = self._pipeline(job, project, user)
        ctx = self._context(pipe, project, user)
        pid, pitch = self._chosen(project, job["params"].get("pitch"))
        plan = pipe.design(ctx, pitch, job["params"].get("note", ""))
        plan_id = self.db.run("INSERT INTO plans(project_id, kind, pitch_id, data, created) VALUES (?,?,?,?,?)",
                              (project["id"], "design", pid, plan.model_dump_json(), time.time()))
        shape = self.shapes(project)[0]
        info = pipe.sample(ctx, plan, shape, f"sample-{plan_id}", look=look_from_note(project["intent"], self.profile(user).look))
        self.db.run("INSERT INTO renders(project_id, plan_id, version, kind, shape, file, seconds, render_seconds, created) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", (project["id"], plan_id, project["version"], "sample", shape, info.path,
                                                   info.seconds, info.render_seconds, time.time()))
        self.db.set_stage(project["id"], "review")

    def _render_plan(self, job, project, user, pipe: Pipeline, ctx, plan: DesignPlan, plan_id: int) -> None:
        version = project["version"] + 1
        infos = pipe.render(ctx, plan, self.shapes(project), version, look=look_from_note(project["intent"], self.profile(user).look))
        for info in infos:
            self.db.run("INSERT INTO renders(project_id, plan_id, version, kind, shape, file, seconds, render_seconds, "
                        "findings, created) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (project["id"], plan_id, version, "final", info.shape, info.path, info.seconds, info.render_seconds,
                         json.dumps(info.findings), time.time()))
            if info.failed:
                alert(self.db, self.s, "check", f"A finished video failed a check: "
                      + "; ".join(f"{f['check']}: {f['detail'][:80]}" for f in info.failed), "warning")
        self.db.run("UPDATE projects SET version=? WHERE id=?", (version, project["id"]))
        self.db.set_stage(project["id"], "done")

    def _job_render(self, job, project, user):
        pipe = self._pipeline(job, project, user)
        ctx = self._context(pipe, project, user)
        row = self.latest_plan(project)
        if row is None:
            raise ActionError("There is no plan to render yet.")
        self._render_plan(job, project, user, pipe, ctx, DesignPlan.model_validate_json(row["data"]), row["id"])

    def _job_revise(self, job, project, user):
        pipe = self._pipeline(job, project, user, 0.0, 0.25)
        ctx = self._context(pipe, project, user)
        row = self.latest_plan(project)
        if row is None:
            raise ActionError("There is no plan to change yet.")
        _, pitch = self._chosen(project, row["pitch_id"])
        feedback = job["params"].get("feedback", "")
        if job["params"].get("tags"):
            feedback += "\nThey also ticked: " + ", ".join(job["params"]["tags"])
        plan = pipe.revise(ctx, pitch, DesignPlan.model_validate_json(row["data"]), feedback)
        plan_id = self.db.run("INSERT INTO plans(project_id, kind, pitch_id, data, created) VALUES (?,?,?,?,?)",
                              (project["id"], "revise", row["pitch_id"], plan.model_dump_json(), time.time()))
        pipe.progress = lambda f, m: self.db.job_progress(job["id"], 0.25 + 0.75 * f, m)
        self._render_plan(job, project, user, pipe, ctx, plan, plan_id)

    def _job_learn(self, job, project, user):
        events = []
        for p in self.pitches(project):
            if p["id"] == project["chosen"]:
                events.append(f"Picked the story '{p['pitch']['title']}': {p['pitch']['logline']}")
        for f in self.db.all("SELECT * FROM feedback WHERE project_id=? ORDER BY id", (project["id"],)):
            if f["stage"] == "sample":
                events.append(("Approved the sample" if f["liked"] else "Rejected the sample") +
                              (f": \"{f['text']}\"" if f["text"] else ""))
            else:
                tags = json.loads(f["tags"] or "[]")
                events.append(f"Rated version {f['version']}: matched their ask {f['match']}/5, liked it {f['liked']}/10"
                              + (f"; ticked: {', '.join(tags)}" if tags else "") + (f"; said: \"{f['text']}\"" if f["text"] else ""))
        if not events:
            return
        pipe = self._pipeline(job, project, user)
        profile = self.profile(user)
        update = pipe.brain.learn(profile.rules, events)
        entry = profile.apply(update)
        profile.save()
        self.db.event(project["id"], f"Style profile: {update.summary or 'no change'} "
                                     f"(+{len(entry['added'])}, -{len(entry['dropped'])})")

    # ------------------------------------------------------------------ admin
    def health(self) -> dict:
        from .storage import free_gb

        beat = self.db.one("SELECT * FROM heartbeat WHERE name='worker'")
        return {
            "worker_seen": round(time.time() - beat["at"], 1) if beat else None,
            "queued": self.db.value("SELECT COUNT(*) FROM jobs WHERE status='queued'", (), 0),
            "running": self.db.value("SELECT COUNT(*) FROM jobs WHERE status='running'", (), 0),
            "waiting": self.db.value("SELECT COUNT(*) FROM jobs WHERE status='waiting'", (), 0),
            "failed_24h": self.db.value("SELECT COUNT(*) FROM jobs WHERE status='failed' AND finished > ?",
                                        (time.time() - 86400,), 0),
            "free_gb": round(free_gb(self.s), 1),
            "month_usd": round(self.db.month_spend(), 3),
            "month_usd_all": round(self.db.month_spend(billed_only=False), 3),
            "cap_usd": self.s.limits.monthly_usd,
        }


def stage_label(stage: str) -> str:
    return STAGES.get(stage, stage)


def usage_dict(u: Usage) -> dict:
    return asdict(u)
