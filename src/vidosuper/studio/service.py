"""The studio's rules of play, shared by the web app and the worker: what a person may do at each stage,
which job that starts, and what each job does.

A video moves through stages:

    setup -> preparing -> choose -> sampling -> review -> rendering -> done (-> revising -> done)
    choose -> pitching -> choose (another idea, or their own);  review -> sampling (with their note)

Footage films pitch stories at `choose`; voice and designed talking videos pitch LOOKS there (the world behind the
words, with a painted preview each), and the one picked paints every scene's backdrop (see explainer/backdrops.py).

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
from .brain.schemas import DesignPlan, Direction, StoryPitch
from .db import DB
from .improve import maybe_start as maybe_start_improve
from . import requirements
from .pipeline import Pipeline, UserError, remove_project_files, studio_config
from .profiles import Profile, owner_seed_rules, rules_for, scope_of
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
STYLES = ("designed", "labels")  # designer animations behind the person; clean labels over the video
TYPICAL_SECONDS = {"prepare": 240, "pitch": 70, "sample": 100, "render": 300, "revise": 180}  # first guesses, until real jobs have run
DRAW_RATE = {"designed": 20.0, "labels": 3.0}  # first guesses: seconds of drawing per second of a voice or talking video


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

    def create_project(self, user: dict, title: str, shapes: str, length: float, intent: str, pace: str = "balanced",
                       kind: str = "montage", style: str = "designed") -> dict:
        if (left := self.videos_left(user)) is not None and left <= 0:
            raise ActionError("You've used all your videos for this trial. Thank you!")
        if shapes not in ("9:16", "16:9", "both"):
            raise ActionError("Pick a shape.")
        if int(length) not in LENGTHS:
            raise ActionError("Pick a length.")
        if pace not in ("calm", "balanced", "fast", "every"):
            raise ActionError("Pick how fast it should cut.")
        pace = pace_from_note(pace, intent)
        if kind not in ("montage", "voice", "talk"):
            raise ActionError("Pick what you are making.")
        if style not in STYLES:
            raise ActionError("Pick a style for the graphics.")
        if kind == "talk" and shapes == "both":
            shapes = "9:16"  # a talking video keeps its own shape
        pid = "p" + secrets.token_hex(6)
        now = time.time()
        title = " ".join(title.split())[:60] or time.strftime("My video, %d %b")
        self.db.run("INSERT INTO projects(id, user_id, title, shapes, length, intent, stage, created, updated, pace, kind, "
                    "music, style) VALUES (?,?,?,?,?,?,'setup',?,?,?,?,?,?)",
                    (pid, user["id"], title, shapes, float(length), " ".join(intent.split())[:600], now, now, pace, kind,
                     "none" if kind != "montage" or requirements.wants_no_music(intent) else "made", style))
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

    def delete_account(self, user: dict) -> None:
        """Erase a person (their right to erasure): every project row and file, their sign-ins, passkeys, consents and
        profile, then the account. Spend rows stay for the books, with the person and project removed from them."""
        import shutil

        if self.is_owner(user):
            raise ActionError("The owner's account can't delete itself.")
        self.delete_everything(user)
        pids = [r["id"] for r in self.db.all("SELECT id FROM projects WHERE user_id=?", (user["id"],))]
        for pid in pids:
            for table in ("pitches", "plans", "renders", "feedback", "events", "uploads", "clips", "jobs"):
                self.db.run(f"DELETE FROM {table} WHERE project_id=?", (pid,))
        self.db.run("DELETE FROM projects WHERE user_id=?", (user["id"],))
        self.db.run("UPDATE spend SET user_id=NULL, project_id=NULL WHERE user_id=?", (user["id"],))
        self.db.run("UPDATE audit SET user_id=NULL, ip=NULL, detail='' WHERE user_id=?", (user["id"],))
        for table in ("sessions", "devices", "passkeys", "challenges", "consents", "jobs"):
            self.db.run(f"DELETE FROM {table} WHERE user_id=?", (user["id"],))
        if user.get("email"):
            self.db.run("DELETE FROM login_codes WHERE email=?", (user["email"],))
            self.db.run("DELETE FROM pending WHERE email=?", (user["email"],))
        self.db.run("DELETE FROM users WHERE id=?", (user["id"],))
        shutil.rmtree(user_dir(self.s, user["id"]), ignore_errors=True)
        self.db.run("INSERT INTO audit(at, action, detail) VALUES (?,?,?)", (time.time(), "account_deleted", ""))

    def export(self, user: dict) -> dict:
        """Everything Flicello keeps about a person, as data (their right of access and portability). Video files are
        downloaded from each project's page; this lists them."""
        projects = []
        for p in self.db.all("SELECT * FROM projects WHERE user_id=? AND deleted=0 ORDER BY created", (user["id"],)):
            projects.append({
                "id": p["id"], "title": p["title"], "kind": p["kind"], "created": p["created"], "updated": p["updated"],
                "request": p["intent"], "shapes": p["shapes"], "length": p["length"], "song": p["song"],
                "pitches": [json.loads(x["data"]) for x in
                            self.db.all("SELECT data FROM pitches WHERE project_id=? ORDER BY id", (p["id"],))],
                "videos": [r["file"] for r in self.db.all("SELECT file FROM renders WHERE project_id=?", (p["id"],))],
                "feedback": self.db.all("SELECT stage, version, match, liked, tags, text, created FROM feedback "
                                        "WHERE project_id=? ORDER BY id", (p["id"],)),
            })
        prof = user_dir(self.s, user["id"]) / "profile.json"
        return {
            "account": {k: user.get(k) for k in ("id", "name", "display", "email", "role", "created", "joined_by",
                                                 "marketing", "adult", "terms", "last_seen")},
            "sign_in": {"google": bool(user.get("google_sub")),
                        "passkeys": self.db.all("SELECT name, created, last_used FROM passkeys WHERE user_id=?",
                                                (user["id"],)),
                        "sessions": self.db.all("SELECT created, expires, seen FROM sessions WHERE user_id=?",
                                                (user["id"],))},
            "consents": self.db.all("SELECT kind, version, granted, at FROM consents WHERE user_id=? ORDER BY at",
                                    (user["id"],)),
            "style_profile": json.loads(prof.read_text(encoding="utf-8")) if prof.exists() else None,
            "projects": projects,
            "ai_spend_usd": round(self.db.value("SELECT SUM(usd) FROM spend WHERE user_id=?", (user["id"],), 0) or 0, 4),
            "exported": time.time(),
        }

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

    def draw_rate(self, kind: str, style: str, render: str = "final") -> float | None:
        """Seconds of drawing per second of video for voice or talking videos of this style: from the last ones drawn
        here, else a first guess."""
        rows = self.db.all("SELECT r.render_seconds AS took, r.seconds AS secs FROM renders r JOIN projects p ON "
                           "p.id = r.project_id WHERE p.kind=? AND r.style=? AND r.kind=? AND r.render_seconds > 0 "
                           "AND r.seconds > 1 ORDER BY r.id DESC LIMIT 15", (kind, style, render))
        rates = sorted(r["took"] / r["secs"] for r in rows)
        return rates[len(rates) // 2] if rates else DRAW_RATE.get(style)

    def expected_seconds(self, job: dict) -> float:
        """How long this job should take. Drawing a voice or talking video takes its length times how fast such
        videos draw here (a 2-minute designed talk takes far longer than a 30-second montage); everything else, what
        such jobs usually take."""
        from ..explainer.make import SAMPLE_SECONDS

        typical = self.typical_seconds(job["kind"])
        project = self.db.project(job["project_id"]) if job.get("project_id") else None
        if project is None or project["kind"] == "montage" or job["kind"] not in ("prepare", "render", "revise"):
            return typical
        seconds = float(json.loads(project["info"] or "{}").get("seconds") or 0)
        if job["kind"] == "prepare":  # listening, the script, then a sample
            rate = self.draw_rate(project["kind"], project["style"], "sample")
            return typical + (SAMPLE_SECONDS * rate if rate else 0.0)
        rate = self.draw_rate(project["kind"], project["style"])
        if not seconds or rate is None:
            return typical
        shapes = len(self.shapes(project)) if project["kind"] == "voice" else 1
        return seconds * rate * shapes + (90.0 if job["kind"] == "revise" else 0.0)

    def eta(self, job: dict | None) -> int | None:
        """Seconds left for the running job: what it should take, corrected by how far it has got."""
        if not job:
            return None
        typical = self.expected_seconds(job)
        if job["status"] != "running" or not job["started"]:
            return int(typical)
        elapsed = time.time() - job["started"]
        by_history = max(15.0, typical - elapsed)
        p = float(job["progress"] or 0.0)
        if p >= 0.1 and elapsed > 8:
            return int(p * (elapsed * (1 - p) / p) + (1 - p) * by_history)
        return int(by_history)

    def total_estimate(self, kind: str = "montage") -> int:
        """Start to a finished video, in seconds, for the page that asks to start."""
        steps = ("prepare", "pitch", "sample", "render") if kind == "montage" else ("prepare", "sample", "render")
        return int(sum(self.typical_seconds(k) for k in steps))

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
        if project["kind"] == "montage":
            n = self.db.value("SELECT COUNT(*) FROM clips WHERE project_id=?", (project["id"],), 0)
            if requirements.is_talking_video_request(project["intent"], n):
                self.db.run("UPDATE projects SET kind='talk', music='none', updated=? WHERE id=?", (time.time(), project["id"]))
                self.db.event(project["id"], "Switched to a talking video: the note asks for graphics for what is said.", "info")
                project = self.db.project(project["id"])
        if project["kind"] != "montage":
            self._start_explainer(project)
            return
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
        self._enqueue(project, "sample", {"pitch": pitch_id, "look": uses_looks(project)})
        if uses_looks(project):  # their taste in looks: what they picked, and what they passed over for it
            prof = self.profile(self.user(project["user_id"]))
            for p in self.pitches(project):
                if not p["rejected"]:
                    prof.note_look("picked" if p["id"] == pitch_id else "passed", p["pitch"])
            prof.save()

    def another(self, project: dict, reason: str = "", own_idea: str = "", base: int = 0) -> None:
        """One more idea: something different (with why), their own idea shaped up, or (looks only) the look `base`
        with the change in `reason`."""
        self._guard(project, "choose")
        cap = 12 if uses_looks(project) else 9
        if self.db.value("SELECT COUNT(*) FROM pitches WHERE project_id=?", (project["id"],), 0) >= cap:
            raise ActionError("That's plenty of ideas: pick one, or write your own.")
        if base and not self.db.one("SELECT id FROM pitches WHERE id=? AND project_id=?", (base, project["id"])):
            raise ActionError("Pick one of the looks to change.")
        if base and not reason.strip():
            raise ActionError("Say what to change about it.")
        if uses_looks(project) and (base or own_idea.strip()):
            prof = self.profile(self.user(project["user_id"]))
            look = next((p["pitch"] for p in self.pitches(project) if p["id"] == base), {})
            prof.note_look("changed" if base else "own", look, reason if base else own_idea)
            prof.save()
        self._enqueue(project, "pitch", {"reason": " ".join(reason.split())[:300], "own": " ".join(own_idea.split())[:600],
                                         "base": int(base or 0)})

    def sample_verdict(self, user: dict, project: dict, ok: bool, note: str = "") -> None:
        self._guard(project, "review")
        note = " ".join(note.split())[:600]
        self.db.run("INSERT INTO feedback(project_id, user_id, stage, version, liked, text, created) VALUES (?,?,?,?,?,?,?)",
                    (project["id"], user["id"], "sample", project["version"], 1 if ok else 0, note, time.time()))
        if ok:
            self._enqueue(project, "render")
            return
        if project["sample_tries"] >= MAX_SAMPLE_TRIES:
            raise ActionError("Let's try a different story instead: go back and pick another idea."
                              if project["kind"] == "montage" else
                              "That's three tries: make this version, then ask for the changes you still want.")
        if not note:
            raise ActionError("Say what to change, so the next sample is better.")
        self.db.run("UPDATE projects SET sample_tries=sample_tries+1 WHERE id=?", (project["id"],))
        self._enqueue(project, "sample", {"pitch": project["chosen"], "note": note})

    def back_to_ideas(self, project: dict) -> None:
        self._guard(project, "review")
        if project["kind"] != "montage" and not self.pitches(project):
            raise ActionError("There are no ideas to go back to for this video.")
        self.db.set_stage(project["id"], "choose")

    def rate(self, user: dict, project: dict, match: int, liked: int, tags: list[str], text: str, fix: str) -> None:
        self._guard(project, "done")
        tags = [t for t in tags if t in FEEDBACK_TAGS]
        self.db.run("INSERT INTO feedback(project_id, user_id, stage, version, match, liked, tags, text, created) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", (project["id"], user["id"], "final", project["version"],
                                                   max(1, min(5, match)), max(1, min(10, liked)), json.dumps(tags),
                                                   " ".join(text.split())[:1000], time.time()))
        self.db.enqueue("learn", project["id"], user["id"], {}, PRIORITY["learn"])
        look, _ = self._look(project)
        if look is not None:  # how the look turned out as a whole video
            prof = self.profile(user)
            prof.note_look("rated", look.model_dump(), f"{max(1, min(10, liked))}/10"
                           + (f", ticked {', '.join(tags)}" if tags else "") + (f": {text[:120]}" if text.strip() else ""))
            prof.save()
        maybe_start_improve(self)  # after every 5th rating the improvement routine looks at what people said
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
        return pipe.context(self.shapes(project), project["length"], project["intent"],
                            rules_for(self.profile(user).rules, project["kind"]),
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

    # ------------------------------------------------------------------ voice and talking videos (see ../explainer)
    def _start_explainer(self, project: dict) -> None:
        work = workdir(self.s, project["user_id"], project["id"])
        if project["kind"] == "voice":
            if work.song_path() is None:
                raise ActionError("Add your recording first.")
        else:
            clips = self.db.all("SELECT seconds FROM clips WHERE project_id=?", (project["id"],))
            if len(clips) != 1:
                raise ActionError("Add the one video of you talking.")
            if clips[0]["seconds"] and clips[0]["seconds"] > 600:
                raise ActionError("A talking video can be up to 10 minutes.")
        self._enqueue(project, "prepare")

    def _save_script(self, project: dict, script, kind: str) -> int:
        return self.db.run("INSERT INTO plans(project_id, kind, pitch_id, data, created) VALUES (?,?,?,?,?)",
                           (project["id"], kind, None, script.model_dump_json(), time.time()))

    def _script_for(self, pipe, project: dict, user: dict, speech: dict, previous=None, feedback: str = "",
                    look: Direction | None = None):
        from ..explainer.make import fit

        raw = pipe.brain.script_voice(speech["utterances"], speech["duration"], project["kind"], project["intent"],
                                      rules_for(self.profile(user).rules, project["kind"]), previous, feedback,
                                      style=project["style"], look=look)
        update = {"style": project["style"]}
        if look is not None:
            update.update(look=f"{look.title}: {look.art_style}", palette=look.palette)
        script = fit(raw.model_copy(update=update), speech)
        if previous is not None:  # places painted before keep their plates
            script = script.model_copy(update={"stage_file": previous.stage_file})
        if project["style"] == "designed" and look is None:  # with a look, props are painted first (see _paint)
            script = self._animations(pipe, project, script)
        return script

    # ------------------------------------------------------------------ looks (voice and designed talking videos)
    def _image_maker(self, project: dict, user: dict, job: dict):
        from ..explainer.backdrops import ImageMaker
        from .brain import Usage

        spend = self._spender(project, user["id"], job["id"])
        return ImageMaker(model=self.s.models.images,
                          spend=lambda task, model, usd, secs: spend(Usage(task, model, "gemini", usd=usd, seconds=secs)))

    def _make_looks(self, job, project, user, pipe, speech: dict, n: int, rnd: int, rejected=None, reason: str = "",
                    own: str = "", base: Direction | None = None) -> None:
        """Pitch `n` looks, paint a preview of each, and save them as this round's ideas."""
        from concurrent.futures import ThreadPoolExecutor

        from ..explainer.backdrops import make_preview

        shape = self.shapes(project)[0] if project["kind"] == "voice" else (
            "9:16" if (speech.get("height") or 0) > (speech.get("width") or 0) else "16:9")
        self.db.job_progress(job["id"], 0.25, "Imagining how it could look")
        profile = self.profile(user)
        looks = pipe.brain.directions(speech["utterances"], float(speech["duration"]), project["kind"], project["intent"],
                                      rules_for(profile.rules, project["kind"]), shape, n=n,
                                      rejected=rejected, reason=reason, own_idea=own, base=base, change=reason,
                                      taste=profile.taste_text())
        self.db.job_progress(job["id"], 0.6, "Painting a preview of each look")
        work = workdir(self.s, project["user_id"], project["id"]).ensure()
        maker = self._image_maker(project, user, job)
        names = [f"look-r{rnd}-{k + 1}-{secrets.token_hex(3)}.jpg" for k in range(len(looks))]
        if maker.available:
            notes: list[str] = []
            with ThreadPoolExecutor(3) as ex:
                made = list(ex.map(lambda a: make_preview(a[0].model_dump(), maker, shape, work.stills / a[1], notes.append),
                                   zip(looks, names)))
            if notes:
                self.db.event(project["id"], "looks: " + "; ".join(sorted(set(notes)))[:600], "warn")
                if not any(made):
                    alert(self.db, self.s, "images", f"Look previews failed: {notes[0][:200]}", "warning")
        else:
            made = [None] * len(looks)
            self.db.event(project["id"], "looks: no image key, so no previews or painted backdrops", "warn")
        self._save_pitches(project, [{"pitch": d.model_dump(), "stills": [nm] if m else []}
                                     for d, nm, m in zip(looks, names, made)], rnd)
        self.db.set_stage(project["id"], "choose")

    def _job_looks(self, job, project, user) -> None:
        from ..explainer import make

        work = workdir(self.s, project["user_id"], project["id"])
        speech = make.load_speech(work.root)
        if speech is None:
            raise ActionError("Start again: the recording is missing.")
        pipe = self._pipeline(job, project, user)
        old = [Direction.model_validate(p["pitch"]) for p in self.pitches(project)]
        base_id = int(job["params"].get("base") or 0)
        base = next((Direction.model_validate(p["pitch"]) for p in self.pitches(project) if p["id"] == base_id), None)
        self._make_looks(job, project, user, pipe, speech, 1, project["pitch_round"] + 1, rejected=None if base else old,
                         reason=job["params"].get("reason", ""), own=job["params"].get("own", ""), base=base)

    def _animations_where_no_prop(self, project: dict, script, job, user, window=None):
        """Stock animations, only for scenes whose painted prop could not be made (and that have none yet); for a
        sample, only the scenes inside it."""
        if project["style"] != "designed":
            return script
        bare = [k for k, s in enumerate(script.scenes) if s.visual and not s.prop_file and not s.lottie
                and (window is None or (s.end > window[0] and s.start < window[1]))]
        if not bare:
            return script
        only = script.model_copy(update={"scenes": [s if k in bare else s.model_copy(update={"visual": ""})
                                                    for k, s in enumerate(script.scenes)]})
        filled = self._animations(self._pipeline(job, project, user), project, only)
        return script.model_copy(update={"scenes": [s.model_copy(update={"lottie": f.lottie}) if k in bare else s
                                                    for k, (s, f) in enumerate(zip(script.scenes, filled.scenes))]})

    def _look(self, project: dict) -> tuple[Direction | None, "Path | None"]:
        """The look picked for this video and its preview, or (None, None)."""
        if not project.get("chosen") or not uses_looks(project):
            return None, None
        row = self.db.one("SELECT * FROM pitches WHERE id=? AND project_id=?", (project["chosen"], project["id"]))
        if row is None:
            return None, None
        try:
            look = Direction.model_validate(json.loads(row["data"]))
        except ValueError:
            return None, None
        stills = json.loads(row["stills"] or "[]")
        ref = workdir(self.s, project["user_id"], project["id"]).stills / stills[0] if stills else None
        return look, ref if ref is not None and ref.exists() else None

    def _paint(self, job, project, user, script, look: Direction | None, ref, shapes: list[str],
               window: tuple[float, float] | None = None, span: tuple[float, float] = (0.3, 0.4)):
        """Every place of the script painted for each shape (only those inside `window` for a sample), the job's
        progress moving through `span` as each picture arrives."""
        from ..explainer.backdrops import make_plates, make_props

        if look is None:
            return script
        maker = self._image_maker(project, user, job)
        if not maker.available:
            return script  # no images: the stock animations fill in after (see _explainer_render)
        work = workdir(self.s, project["user_id"], project["id"])
        notes: list[str] = []
        a, b = span
        parts = len(shapes) + 1  # each shape's backdrops, then the props
        for k, shape in enumerate(shapes):
            lo_k = a + (b - a) * k / parts
            self.db.job_progress(job["id"], lo_k, "Painting the backdrops")
            script = make_plates(script, look.model_dump(), maker, shape, work.root / "backdrops", ref, window,
                                 notes.append, project["kind"],
                                 tick=lambda f, lo_k=lo_k: self.db.job_progress(job["id"], lo_k + (b - a) / parts * f,
                                                                                "Painting the backdrops"))
        lo_p = a + (b - a) * len(shapes) / parts
        self.db.job_progress(job["id"], lo_p, "Painting the props")
        script = make_props(script, look.model_dump(), maker, work.root / "backdrops", ref, window, notes.append,
                            tick=lambda f: self.db.job_progress(job["id"], lo_p + (b - lo_p) * f, "Painting the props"))
        if notes:
            self.db.event(project["id"], "backdrops: " + "; ".join(notes)[:900], "warn")
        return script

    def _animations(self, pipe, project: dict, script):
        """Real designer animations for the moments the script asks for (see explainer/visuals.py)."""
        from ..explainer.lottie_lib import Library
        from ..explainer.visuals import assign

        work = workdir(self.s, project["user_id"], project["id"])
        try:
            script, log = assign(script, Library(), pipe.brain.pick_visuals, work.root / "visuals", pipe.brain.rethink_visuals)
        except Exception as exc:  # noqa: BLE001 - animations are a bonus: the video goes on with emoji and text
            self.db.event(project["id"], f"animations skipped: {type(exc).__name__}: {exc}"[:300], "warn")
            return script
        if log:
            self.db.event(project["id"], "animations: " + "; ".join(log)[:900], "info")
        return script

    def _explainer_render(self, job, project, user, script, plan_id: int, sample: bool, sample_start: float = 0.0,
                          lo: float = 0.4) -> None:
        """Draw the sample or the final video; the job's progress goes from `lo` (what came before in this job) to 98%."""
        from ..explainer import make

        work = workdir(self.s, project["user_id"], project["id"])
        speech = make.load_speech(work.root)
        if speech is None:
            raise ActionError("Start again: the recording is missing.")
        if project["kind"] == "voice":
            shapes = self.shapes(project)
        else:
            shapes = ["9:16" if (speech.get("height") or 0) > (speech.get("width") or 0) else "16:9"]
        bed = None
        if project["kind"] == "voice" and project["music"] == "made":
            from ..music.house import make_beat

            bed = work.root / "bed.wav"
            if not bed.exists():
                make_beat(bed, speech["duration"] + 4, "calm")
        version = project["version"] + (0 if sample else 1)
        look, ref = self._look(project)
        if look is not None:  # painting takes the first part of what is left of the bar, drawing the rest
            window = (sample_start, sample_start + make.SAMPLE_SECONDS) if sample else None
            paint_to = lo + (0.98 - lo) * (0.45 if sample else 0.3)
            script = self._paint(job, project, user, script, look, ref, shapes[:1] if sample else shapes, window,
                                 (lo, paint_to))
            script = self._animations_where_no_prop(project, script, job, user, window)
            self.db.run("UPDATE plans SET data=? WHERE id=?", (script.model_dump_json(), plan_id))
            lo = paint_to
        todo = shapes[:1] if sample else shapes
        for n, shape in enumerate(todo):
            name = f"sample-{plan_id}" if sample else f"v{version}-{shape.replace(':', 'x')}"
            out = work.out / f"{name}.mp4"
            a = lo + (0.98 - lo) * n / len(todo)  # each shape its own stretch of the bar, one after the other
            b = lo + (0.98 - lo) * (n + 1) / len(todo)
            info = make.render(script, speech, shape, out, music=bed, sample=sample, sample_start=sample_start,
                               progress=lambda f, a=a, b=b: self.db.job_progress(job["id"], a + (b - a) * f,
                                                                               "Drawing your video"),
                               workers=1 if sample else self.s.render_processes,
                               plates_dir=work.root / "backdrops" if look is not None else None)
            rows = requirements.explainer(
                project, scenes=len(script.scenes), callouts=len(script.callouts),
                cutaways=len(script.scenes) if project["kind"] == "talk" else 0, captions=script.captions,
                seconds=info["seconds"] if not sample else float(speech["duration"]), duration=float(speech["duration"]),
                music_playing=bed is not None, audio_kept=True, style=project["style"],
                animated=sum(1 for c in script.callouts if c.lottie) + sum(1 for s in script.scenes if s.lottie),
                cut_out=project["kind"] == "talk" and project["style"] == "designed" and make.cutout_available())
            self._note_requirements(project, rows, f"{shape} {'sample' if sample else 'final'}")
            self.db.run("INSERT INTO renders(project_id, plan_id, version, kind, shape, file, seconds, render_seconds, "
                        "findings, created, style) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (project["id"], plan_id, version, "sample" if sample else "final", shape, info["path"],
                         info["seconds"], info["render_seconds"], json.dumps(rows), time.time(), script.style))
        if sample:
            self.db.set_stage(project["id"], "review")
        else:
            self.db.run("UPDATE projects SET version=? WHERE id=?", (version, project["id"]))
            self.db.set_stage(project["id"], "done")

    def _job_explainer(self, job, project, user, step: str) -> None:
        from ..explainer import make
        from ..explainer.script import VoiceScript

        pipe = self._pipeline(job, project, user)
        work = workdir(self.s, project["user_id"], project["id"])
        if step == "prepare":
            speech = make.prepare(project["kind"], work.root, work.clips, work.song_path(), self.cfg,
                                  lambda f, m: self.db.job_progress(job["id"], 0.2 * f, m))
            self.db.run("UPDATE projects SET info=? WHERE id=?",
                        (json.dumps({"notes": [], "song_seconds": speech["duration"], "words": speech["words"],
                                     "seconds": speech["duration"]}), project["id"]))
            if uses_looks(project):  # first the looks, with a preview each; the script comes once one is picked
                self._make_looks(job, project, user, pipe, speech, 3, 1)
                return
            self.db.job_progress(job["id"], 0.25, "Working out what each part is about")
            script = self._script_for(pipe, project, user, speech)
            plan_id = self._save_script(project, script, "script")
            self._explainer_render(job, project, user, script, plan_id, sample=True,
                                   sample_start=make.busiest(script, float(speech["duration"])))
            return
        speech = make.load_speech(work.root)
        if step == "sample" and job["params"].get("look"):  # a look was just picked: write the script in it
            if speech is None:
                raise ActionError("Start again: the recording is missing.")
            look, _ = self._look(project)
            self.db.job_progress(job["id"], 0.05, "Writing the scenes in your look")
            script = self._script_for(pipe, project, user, speech, look=look)
            plan_id = self._save_script(project, script, "script")
            self._explainer_render(job, project, user, script, plan_id, sample=True,
                                   sample_start=make.busiest(script, float(speech["duration"])))
            return
        row = self.latest_plan(project)
        if speech is None or row is None:
            raise ActionError("There is nothing to render yet.")
        script = VoiceScript.model_validate_json(row["data"])
        feedback = job["params"].get("note") or job["params"].get("feedback") or ""
        if job["params"].get("tags"):
            feedback += "\nThey also ticked: " + ", ".join(job["params"]["tags"])
        plan_id = row["id"]
        sample_start, lines = 0.0, []
        if step in ("sample", "revise") and feedback.strip():
            from ..explainer.script import changes_between

            self.db.job_progress(job["id"], 0.2, "Working your changes in")
            old = script
            look, _ = self._look(project)
            script = self._script_for(pipe, project, user, speech, old, feedback, look=look)
            first, lines = changes_between(old, script)
            if not lines:  # the AI handed back the same thing: say so and ask once more, firmly
                script = self._script_for(pipe, project, user, speech, old, feedback +
                                          "\nYour last answer changed NOTHING visible. Change what they asked, in the scenes "
                                          "it applies to, and use a different layout or picture there.", look=look)
                first, lines = changes_between(old, script)
            plan_id = self._save_script(project, script, "revise")
            if first is not None:
                sample_start = max(0.0, first - 0.7)
            info = json.loads(project["info"] or "{}")
            info["changes"] = lines or ["I couldn't find a way to change that: try saying it differently, "
                                        "for example which part and what it should show."]
            self.db.run("UPDATE projects SET info=? WHERE id=?", (json.dumps(info), project["id"]))
        self._explainer_render(job, project, user, script, plan_id, sample=(step == "sample"), sample_start=sample_start,
                               lo=0.02 if step == "render" else 0.4)  # a plain render is all drawing

    def _job_prepare(self, job, project, user):
        if project["kind"] != "montage":
            return self._job_explainer(job, project, user, "prepare")
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
        if project["kind"] != "montage":
            return self._job_looks(job, project, user)
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
        if project["kind"] != "montage":
            return self._job_explainer(job, project, user, "sample")
        pipe = self._pipeline(job, project, user)
        ctx = self._context(pipe, project, user)
        pid, pitch = self._chosen(project, job["params"].get("pitch"))
        note = job["params"].get("note", "")
        plan = pipe.design(ctx, pitch, note)
        plan_id = self.db.run("INSERT INTO plans(project_id, kind, pitch_id, data, created) VALUES (?,?,?,?,?)",
                              (project["id"], "design", pid, plan.model_dump_json(), time.time()))
        if note:  # shown with the new sample, so a change they asked for can be checked at once
            info = json.loads(project["info"] or "{}")
            info["changes"] = [f"You said: {note}", *[x.strip() for x in plan.notes.split(";") if x.strip()][:4]]
            self.db.run("UPDATE projects SET info=? WHERE id=?", (json.dumps(info), project["id"]))
        shape = self.shapes(project)[0]
        self._note_requirements(project, self._montage_requirements(project, user, ctx, plan, None), "sample")
        info = pipe.sample(ctx, plan, shape, f"sample-{plan_id}", look=look_from_note(project["intent"], self.profile(user).look))
        self.db.run("INSERT INTO renders(project_id, plan_id, version, kind, shape, file, seconds, render_seconds, created) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", (project["id"], plan_id, project["version"], "sample", shape, info.path,
                                                   info.seconds, info.render_seconds, time.time()))
        self.db.set_stage(project["id"], "review")

    def _montage_requirements(self, project: dict, user: dict, ctx, plan: DesignPlan, seconds: float | None) -> list[dict]:
        shots = sum((s.count if s.kind == "stutter" else 1) for s in plan.plan) or 1
        mean = sum(s.beats * (s.count if s.kind == "stutter" else 1) for s in plan.plan) * ctx.beats.period / shots
        impacts = [(m_clip, t) for m_clip, f in ctx.facts.items() for t in f.impacts]
        used = sum(1 for clip, t in impacts if any(m.clip == clip and m.start - 0.05 <= t <= m.end + 0.05 for m in plan.pool))
        return requirements.montage(project, mean_shot=mean, shots=shots, hits_total=len(impacts), hits_used=used,
                                    seconds=seconds, music_playing=not (project["music"] == "none" and not project["song"]),
                                    look=look_from_note(project["intent"], self.profile(user).look))

    def _note_requirements(self, project: dict, rows: list[dict], where: str) -> None:
        bad = requirements.unmet(rows)
        if bad:
            self.db.event(project["id"], f"requirement not met ({where}): " + "; ".join(bad), "warn")

    def _render_plan(self, job, project, user, pipe: Pipeline, ctx, plan: DesignPlan, plan_id: int) -> None:
        version = project["version"] + 1
        infos = pipe.render(ctx, plan, self.shapes(project), version, look=look_from_note(project["intent"], self.profile(user).look))
        for info in infos:
            rows = self._montage_requirements(project, user, ctx, plan, info.seconds)
            info.findings.extend(rows)
            self._note_requirements(project, rows, f"{info.shape} final")
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
        if project["kind"] != "montage":
            return self._job_explainer(job, project, user, "render")
        pipe = self._pipeline(job, project, user)
        ctx = self._context(pipe, project, user)
        row = self.latest_plan(project)
        if row is None:
            raise ActionError("There is no plan to render yet.")
        self._render_plan(job, project, user, pipe, ctx, DesignPlan.model_validate_json(row["data"]), row["id"])

    def _job_revise(self, job, project, user):
        if project["kind"] != "montage":
            return self._job_explainer(job, project, user, "revise")
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
            if p["id"] == project["chosen"] and project["kind"] == "montage":
                events.append(f"Picked the story '{p['pitch']['title']}': {p['pitch']['logline']}")
            elif p["id"] == project["chosen"]:
                d = p["pitch"]
                shown = [x["pitch"]["title"] for x in self.pitches(project) if x["id"] != p["id"]]
                events.append(f"Picked the look '{d.get('title')}' ({d.get('art_style')}; {d.get('world', '')[:140]})"
                              + (f" over: {', '.join(shown[:5])}" if shown else ""))
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
        what = {"voice": "an animated explainer drawn over their voice recording (no footage: the pictures are designer "
                         "animations and text; 'bad shots' means the animations and scenes)",
                "talk": "their talking video with animations and graphics added"}.get(project["kind"],
                                                                                 "a film edited from their footage to music")
        events.insert(0, f"The video was {what}. Rules learned here apply only to this kind of video.")
        pipe = self._pipeline(job, project, user)
        profile = self.profile(user)
        update = pipe.brain.learn(rules_for(profile.rules, project["kind"]), events)
        entry = profile.apply(update, scope_of(project["kind"]))
        profile.save()
        self.db.event(project["id"], f"Style profile: {update.summary or 'no change'} "
                                     f"(+{len(entry['added'])}, -{len(entry['dropped'])})")

    # ------------------------------------------------------------------ remote renderer (see remote.py)
    def renderer_seen(self) -> float | None:
        """Seconds since a remote worker last checked in, or None if none ever has."""
        beat = self.db.one("SELECT at FROM heartbeat WHERE name='renderer'")
        return round(time.time() - beat["at"], 1) if beat else None

    def queued_note(self, job: dict | None) -> str:
        """What a waiting video says while remote rendering is on and no renderer is online."""
        if not job or job["status"] != "queued" or self.s.jobs != "remote":
            return ""
        seen = self.renderer_seen()
        if seen is not None and seen < 90:
            return ""
        return "Waiting for the video renderer to come online. Your video will start by itself; you can leave this page."

    # ------------------------------------------------------------------ admin
    def health(self) -> dict:
        from .storage import free_gb

        beat = self.db.one("SELECT * FROM heartbeat WHERE name='worker'")
        return {
            "jobs": self.s.jobs,
            "renderer_seen": self.renderer_seen(),
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


def uses_looks(project: dict) -> bool:
    """Voice videos and designed talking videos are pitched as looks, painted by an image model."""
    return project["kind"] == "voice" or (project["kind"] == "talk" and project.get("style") == "designed")


def usage_dict(u: Usage) -> dict:
    return asdict(u)
