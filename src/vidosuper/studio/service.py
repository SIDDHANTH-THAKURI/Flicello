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
import logging
import secrets
import time
import traceback
from dataclasses import asdict
from pathlib import Path

from .alerts import alert
from .brain import BrainError, BudgetExceeded, PersonLimit, Usage, brain_kind_for, make_brain
from .brain.context import look_from_note, pace_from_note
from .brain.schemas import DesignPlan, Direction, StoryPitch
from .db import DB, JobStopped
from .improve import maybe_start as maybe_start_improve
from . import requirements
from .pipeline import Pipeline, UserError, remove_project_files, studio_config
from .profiles import Profile, owner_seed_rules, rules_for, scope_of
from .settings import StudioSettings
from .storage import UploadError, user_dir, workdir

STAGES = {
    "setup": "Add your clips and a song",
    "preparing": "Watching your clips",
    "understood": "Check what I understood",
    "choose": "Pick a story",
    "pitching": "Thinking up story ideas",
    "sampling": "Cutting a sample",
    "review": "Check the sample",
    "rendering": "Making your video",
    "done": "Your video is ready",
    "revising": "Making your changes",
    "composing": "Making your music",
    "paused": "Paused",
    "failed": "Something went wrong",
}
BUSY = {"preparing", "pitching", "sampling", "rendering", "revising", "composing"}
log = logging.getLogger("vidosuper.studio.service")
JOB_STAGE = {"prepare": "preparing", "pitch": "pitching", "sample": "sampling", "render": "rendering",
             "revise": "revising", "beat": "composing"}
PRIORITY = {"beat": 2, "pitch": 2, "sample": 3, "learn": 4, "prepare": 5, "render": 6, "revise": 6}  # short jobs first
FEEDBACK_TAGS = ["too fast", "too slow", "boring start", "wrong music", "bad shots", "shaky", "too short",
                 "too long", "didn't follow my idea", "colour", "sound"]
LENGTHS = (15, 30, 45, 60, 90, 120, 180, 300, 600)  # who may ask for which: Studio.lengths_for (access levels)
MAX_SAMPLE_TRIES = 10  # a quiet safety net only: samples will be paid for with credits
MAX_TRACKS = 5  # tracks made for one film, for everyone: after that they pick one of them or upload their own song
MAX_IDEAS = 6  # story ideas (or looks) for one film, the first three included; the owner gets more
MAX_POLISH = 3  # times the AI may reword 'what I understood' for one film


IMPROVISE = [  # what the AI is told for each part of the slider (the person sees the left-hand words)
    (20, "Follow their words closely. Add nothing they did not ask for."),
    (50, "Follow their idea, adding your own touches where they clearly help."),
    (80, "Take real creative liberties: an unexpected structure, bold transitions, play with the rhythm. Keep "
         "everything they said they must have."),
    (100, "Go wild: the boldest, most surprising version the footage can carry. Keep only their must-haves."),
]


def improvise_text(level: int) -> str:
    return next(text for top, text in IMPROVISE if int(level) <= top)


def ask_text(project: dict) -> str:
    """Everything the person asked for, as the AI reads it: their note, how they want it cut in their own words, how
    far it may go beyond them (the slider), and the brief they checked and corrected (it wins over the rest)."""
    parts = [(project.get("intent") or "").strip()]
    if (project.get("cut_note") or "").strip():
        parts.append(f"How they want it cut, in their words: {project['cut_note'].strip()}")
    parts.append(f"How much to improvise ({int(project.get('improvise') or 35)}/100): "
                 f"{improvise_text(project.get('improvise') or 35)}")
    if project.get("brief"):
        b = json.loads(project["brief"])
        agreed = [f"The film: {b['story']}"] if b.get("story") else []
        agreed += [f"Feel: {', '.join(b['feel'])}"] if b.get("feel") else []
        agreed += [f"Must have: {'; '.join(b['must'])}"] if b.get("must") else []
        agreed += [f"Leave out: {'; '.join(b['avoid'])}"] if b.get("avoid") else []
        agreed += [f"Do not use these clips at all: {', '.join(b['leave_out'])}"] if b.get("leave_out") else []
        agreed += [f"About the music: {b['music']}"] if b.get("music") else []
        if b.get("parts"):
            agreed.append("The parts, in order (each becomes its own scenes): " + "; ".join(
                (f"{int(x['at']) // 60}:{int(x['at']) % 60:02d} " if x.get("at") is not None else "") + x["what"]
                for x in b["parts"]))
        if b.get("terms"):
            agreed.append("Write these names exactly like this, on screen and in the captions: " + "; ".join(
                f"'{t['heard']}' is '{t['written']}'" if t["heard"] != t["written"] else f"'{t['written']}'"
                for t in b["terms"]))
        agreed += [f"Asked '{x['ask']}', they answered: {x['answer']}" for x in b.get("answers") or []]
        if agreed:
            parts.append("# What we agreed with them (they checked and corrected this: it wins over anything above)\n"
                         + "\n".join(f"- {line}" for line in agreed))
    return "\n".join(p for p in parts if p)


KIND_WORDS = [  # 'something else': which engine can make it (the person sees the reason and can change it)
    ("voice", r"\b(voice ?(memo|note|over)|podcast|audio|narrat\w*|my voice|recording|no camera|animat\w* (of|from) my)"),
    ("talk", r"\b(me talking|talking head|to camera|vlog|explain\w*|tutorial|lesson|interview|presentation|speech|pitch)"),
]


def route_kind(describe: str) -> tuple[str, str]:
    import re

    for kind, pattern in KIND_WORDS:
        if re.search(pattern, describe, re.IGNORECASE):
            return kind, {"voice": "It sounds like it's built on a recording of a voice, so it's made as an "
                                   "animation of what is said.",
                          "talk": "It sounds like someone talking to camera, so their video stays and graphics "
                                  "land on their words."}[kind]
    return "montage", "It sounds like it's made from your clips, so they're cut together into a story on the beat."
STYLES = ("designed", "labels")  # designer animations behind the person; clean labels over the video
TYPICAL_SECONDS = {"prepare": 240, "pitch": 70, "sample": 100, "render": 300, "revise": 180, "beat": 45}  # first guesses, until real jobs have run
DRAW_RATE = {"designed": 20.0, "labels": 3.0}  # first guesses: seconds of drawing per second of a voice or talking video


class ActionError(ValueError):
    """Shown to the person as is."""


def service_refused(exc: BaseException) -> bool:
    """The AI account itself said no (a refused key, no permission, no credit left): no retry can help until the
    owner fixes it."""
    name, text = type(exc).__name__, str(exc).lower()
    return name in ("AuthenticationError", "PermissionDeniedError") or "invalid x-api-key" in text \
        or "credit balance is too low" in text


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

    # ------------------------------------------------------------------ access (closed beta; see auth.ACCESS)
    def tier(self, user: dict):
        """What this person's access level allows; the owner gets everything special people get, and more."""
        from .settings import Tier

        if self.is_owner(user):
            top = self.s.tiers.get("special") or Tier()
            return Tier(videos=None, max_seconds=max(LENGTHS), max_recording=max(top.max_recording,
                                                                                 int(self.s.limits.project_seconds)),
                        beats=99)
        return self.s.tiers.get("special" if user.get("access") == "special" else "beta") or Tier()

    def lengths_for(self, user: dict) -> list[int]:
        return [n for n in LENGTHS if n <= self.tier(user).max_seconds]

    def request_access(self, user: dict, note: str) -> bool:
        """Someone waiting for the beta says what they would make. True the first time (the owner is told then)."""
        note = " ".join((note or "").split())[:600]
        if len(note) < 3:
            raise ActionError("Tell us in a few words what you'd like to make.")
        first = not (user.get("request_note") or "")
        self.db.run("UPDATE users SET request_note=?, requested=COALESCE(requested, ?) WHERE id=?",
                    (note, time.time(), user["id"]))
        if first:
            from . import mail

            who = user.get("display") or user["name"]
            alert(self.db, self.s, "access", f"{who} asked for access: {note[:200]}", "warn")
            if mail.available(self.s):
                site = (self.s.site_url or "").rstrip("/")
                for owner in [{"email": e} for e in self.s.alert_emails]:
                    try:
                        mail.send(self.s, owner["email"], *mail.notice_email(self.s, f"{who} asked for access", [
                            f"{who} ({user.get('email') or 'no email'}) would like to use Flicello.",
                            f"What they'd make: {note}",
                            f"Approve or decline on the admin page: {site}/admin"]))
                    except Exception:  # noqa: BLE001 - the request is saved and on the admin page either way
                        log.warning("could not email the owner about an access request")
        return first

    def decide_access(self, target: dict, access: str) -> None:
        """The owner approves (beta or special), declines, or puts someone back to waiting. An approval is emailed."""
        from .auth import ACCESS

        if access not in ACCESS or target["role"] == "owner":
            raise ActionError("Not changed.")
        was = target.get("access") or "beta"
        self.db.run("UPDATE users SET access=?, decided=? WHERE id=?", (access, time.time(), target["id"]))
        if access in ("beta", "special") and was in ("requested", "declined") and target.get("email"):
            from . import mail

            if mail.available(self.s):
                site = (self.s.site_url or "").rstrip("/")
                monthly = self.tier_credits().get(access, 0)
                p = self.pricing()
                lines = ["Your Flicello access is approved. You can make your first video now.",
                         f"You get {monthly} credits every month. A film uses {p['per_minute']} for each minute it "
                         "lasts (we show it before anything starts), so you can try things freely.",
                         f"Sign in here: {site}/signin"]
                if access == "special":
                    lines.insert(1, "You also have special access: films up to 10 minutes long.")
                try:
                    mail.send(self.s, target["email"], *mail.notice_email(self.s, "You're in", lines))
                except Exception:  # noqa: BLE001 - they can sign in anyway; the owner sees no email went
                    log.warning("could not email an approval")

    # ------------------------------------------------------------------ problems people report
    def report_problem(self, user: dict, project: dict, note: str) -> int:
        """Someone says something is wrong with a video. Kept with everything needed to look into it (the stage, the
        step and its log, the recent events), and the owner is told at once."""
        note = " ".join((note or "").split())[:1500]
        if len(note) < 3:
            raise ActionError("Tell us in a few words what went wrong.")
        if self.db.value("SELECT COUNT(*) FROM reports WHERE user_id=? AND at > ?", (user["id"], time.time() - 86400),
                         0) >= 3:
            raise ActionError("We've got your reports from today and are on them. Thank you!")
        if self.db.value("SELECT COUNT(*) FROM reports WHERE user_id=? AND project_id=? AND at > ?",
                         (user["id"], project["id"], time.time() - 3600), 0) >= 1:
            raise ActionError("We've got your report about this video and are on it. You can add more in an hour.")
        job = self.db.active_job(project["id"]) or self.db.one(
            "SELECT * FROM jobs WHERE project_id=? ORDER BY id DESC LIMIT 1", (project["id"],))
        context = {
            "stage": project["stage"], "error": project.get("error"), "kind": project["kind"],
            "style": project.get("style"), "length": project["length"], "shapes": project["shapes"],
            "job": {k: job.get(k) for k in ("id", "kind", "status", "progress", "message", "attempts", "error",
                                            "worker", "started", "moved")} if job else None,
            "steps": self.db.steps(job["id"], 30) if job else [],
            "events": [{"at": e["at"], "level": e["level"], "message": e["message"][:600]} for e in self.db.all(
                "SELECT at, level, message FROM events WHERE project_id=? ORDER BY id DESC LIMIT 25", (project["id"],))],
            "renderer_seen": self.renderer_seen(),
        }
        rid = self.db.run("INSERT INTO reports(at, user_id, project_id, job_id, note, context) VALUES (?,?,?,?,?,?)",
                          (time.time(), user["id"], project["id"], job["id"] if job else None, note,
                           json.dumps(context, default=str)))
        who = user.get("display") or user["name"]
        alert(self.db, self.s, "report", f"{who} reported a problem with a video ({project['stage']}): {note[:200]}")
        from . import mail

        if mail.available(self.s):
            site = (self.s.site_url or "").rstrip("/")
            for owner in [{"email": e} for e in self.s.alert_emails]:
                try:
                    mail.send(self.s, owner["email"], *mail.notice_email(self.s, f"{who} reported a problem", [
                        f"{who} ({user.get('email') or 'no email'}) says: {note}",
                        f"The video is at the '{project['stage']}' stage"
                        + (f"; the last step: {job['message'] or job['kind']} ({job['status']})." if job else "."),
                        f"Everything about it is on the admin page: {site}/admin#reports"]))
                except Exception:  # noqa: BLE001 - the report is saved either way
                    log.warning("could not email the owner about a report")
        return rid

    # ------------------------------------------------------------------ credits: a film uses them by its length
    # Each access level gives credits every calendar month (UTC); the owner can give anyone extra credits, which
    # never run out. A film's credits are taken when it starts (this month's first, then extra ones), the difference
    # when it starts again after a change, and all of them come back if it is deleted before it is finished.
    def _config(self, key: str, default):
        row = self.db.one("SELECT value FROM config WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def _set_config(self, key: str, value) -> None:
        self.db.run("INSERT INTO config(key, value, at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                    "value=excluded.value, at=excluded.at", (key, json.dumps(value), time.time()))

    def pricing(self) -> dict:
        return {**self.s.pricing.model_dump(), **self._config("pricing", {})}

    # ------------------------------------------------------------------ money protection: caps and the off switch
    def money(self) -> dict:
        """The spending caps in force: the settings' defaults, changed by the owner on the admin page."""
        lim = self.s.limits
        base = {"monthly_usd": lim.monthly_usd, "video_usd": lim.video_usd, "person_day_usd": lim.person_day_usd}
        return {**base, **{k: float(v) for k, v in self._config("money", {}).items() if k in base}}

    def set_money(self, monthly: float, video: float, person_day: float) -> None:
        for v in (monthly, video, person_day):
            if not 0 < float(v) <= 10000:
                raise ActionError("Use amounts between $0.01 and $10,000.")
        if float(video) > float(monthly):
            raise ActionError("One video's cap can't be more than the whole month's.")
        self._set_config("money", {"monthly_usd": round(float(monthly), 2), "video_usd": round(float(video), 2),
                                   "person_day_usd": round(float(person_day), 2)})

    def paused(self) -> dict | None:
        """The owner's off switch: while on, no new step starts (running ones finish) and people are told why."""
        return self._config("paused", None)

    def set_paused(self, on: bool, why: str = "") -> None:
        if on:
            self._set_config("paused", {"at": time.time(), "why": " ".join((why or "").split())[:200]})
            alert(self.db, self.s, "paused", "Everything is paused: no new step starts until you resume.", "warning")
        else:
            self.db.run("DELETE FROM config WHERE key='paused'")

    def person_spend(self, user_id: str, hours: float = 24.0) -> float:
        return float(self.db.value("SELECT COALESCE(SUM(usd), 0) FROM spend WHERE user_id=? AND billed=1 AND at > ?",
                                   (user_id, time.time() - hours * 3600), 0) or 0)

    def flags(self, user: dict) -> list[str]:
        """Why an account may need a look (the admin page shows them; switching it off is one click there)."""
        if user["role"] == "owner":
            return []
        out = []
        day = time.time() - 86400
        spent = self.person_spend(user["id"])
        if spent >= self.money()["person_day_usd"] * 2 / 3:
            out.append(f"spent ${spent:.2f} in a day")
        failed = self.db.value("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status='failed' AND finished > ?",
                               (user["id"], day), 0) or 0
        if failed >= 4:
            out.append(f"{failed} failed steps in a day")
        films = self.db.value("SELECT COUNT(*) FROM projects WHERE user_id=? AND created > ?", (user["id"], day), 0) or 0
        if films >= 6:
            out.append(f"{films} films started in a day")
        gb = (self.db.value("SELECT COALESCE(SUM(size), 0) FROM uploads WHERE user_id=? AND created > ? AND status='done'",
                            (user["id"], day), 0) or 0) / 1e9
        if gb >= 5:
            out.append(f"{gb:.1f} GB uploaded in a day")
        return out

    def tier_credits(self) -> dict[str, int]:
        return {**{name: t.credits for name, t in self.s.tiers.items()}, **self._config("tier_credits", {})}

    def set_pricing(self, per_minute: int, looks: int, beta: int, special: int) -> None:
        """The owner's numbers: what a film uses, and what each access level gets a month."""
        for n in (per_minute, looks, beta, special):
            if not 0 <= int(n) <= 1000:
                raise ActionError("Use whole numbers from 0 to 1000.")
        if int(per_minute) < 1:
            raise ActionError("A minute of film must use at least 1 credit.")
        self._set_config("pricing", {"per_minute": int(per_minute), "looks": int(looks)})
        self._set_config("tier_credits", {"beta": int(beta), "special": int(special)})

    @staticmethod
    def month_key(at: float | None = None) -> str:
        return time.strftime("%Y-%m", time.gmtime(at if at is not None else time.time()))

    @staticmethod
    def next_month_label(at: float | None = None) -> str:
        t = time.gmtime(at if at is not None else time.time())
        year, month = (t.tm_year + 1, 1) if t.tm_mon == 12 else (t.tm_year, t.tm_mon + 1)
        return f"1 {['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][month - 1]}"

    def credits(self, user: dict) -> dict | None:
        """What they can spend now: this month's allowance left, and extra credits. None: the owner (no limit)."""
        if self.is_owner(user):
            return None
        allowance = int(self.tier_credits().get("special" if user.get("access") == "special" else "beta", 0))
        used = -int(self.db.value("SELECT COALESCE(SUM(monthly), 0) FROM credits WHERE user_id=? AND month=?",
                                  (user["id"], self.month_key()), 0) or 0)
        extra = max(0, int(self.db.value("SELECT COALESCE(SUM(extra), 0) FROM credits WHERE user_id=?",
                                         (user["id"],), 0) or 0))
        left = max(0, allowance - used)
        return {"monthly": allowance, "monthly_left": left, "extra": extra, "total": left + extra,
                "resets": self.next_month_label()}

    def cost_of(self, kind: str, seconds: float, style: str = "designed") -> int:
        """Credits for a film: per started minute (a few frames over the minute don't count), and painted looks."""
        import math

        p = self.pricing()
        minutes = max(1, math.ceil(max(0.0, float(seconds) - 1.0) / 60.0))
        looks = kind == "voice" or (kind == "talk" and style == "designed")
        return minutes * int(p["per_minute"]) + (int(p["looks"]) if looks else 0)

    def film_seconds(self, project: dict) -> float | None:
        """How long the film is, for its credits: the length asked for; a voice or talking video, its recording."""
        if project["kind"] == "montage":
            return float(project["length"])
        if project["kind"] == "voice":
            return float(project["song_seconds"]) if project.get("song_seconds") else None
        secs = self.db.value("SELECT SUM(seconds) FROM clips WHERE project_id=?", (project["id"],), None)
        return float(secs) if secs else None

    def film_cost(self, project: dict) -> int | None:
        seconds = self.film_seconds(project)
        return None if seconds is None else self.cost_of(project["kind"], seconds, project.get("style") or "designed")

    def credit_history(self, user: dict, n: int = 20) -> list[dict]:
        return self.db.all("SELECT c.*, p.title FROM credits c LEFT JOIN projects p ON p.id=c.project_id "
                           "WHERE c.user_id=? ORDER BY c.id DESC LIMIT ?", (user["id"], n))

    def grant_credits(self, target: dict, n: int, note: str = "") -> None:
        """The owner gives someone extra credits (they never run out), or takes back unused extra ones."""
        n = int(n)
        if not -1000 <= n <= 1000 or n == 0:
            raise ActionError("Give between 1 and 1000 credits (or take some back with a minus).")
        have = self.credits(target)
        if have is not None and n < 0 and -n > have["extra"]:
            raise ActionError(f"They have {have['extra']} extra credits to take back.")
        self.db.run("INSERT INTO credits(user_id, at, month, monthly, extra, kind, note) VALUES (?,?,?,?,?,?,?)",
                    (target["id"], time.time(), self.month_key(), 0, n, "grant", " ".join((note or "").split())[:200]))

    def _pay_for(self, project: dict, check: bool = False) -> None:
        """Take the credits a film uses as it starts: the difference from what it already used (it may have been
        started before, then changed). Not enough: nothing starts, and they are told how many they need. `check`:
        only make sure there are enough (before the step is queued: a step that can't be queued takes nothing)."""
        user = self.user(project["user_id"])
        cost = self.film_cost(project)
        if user is None or cost is None or self.is_owner(user):
            return
        need = cost - int(project.get("credits") or 0)
        if need > 0:
            have = self.credits(user)
            if need > have["total"]:
                raise ActionError(
                    f"This film uses {cost} credit{'s' if cost != 1 else ''}"
                    + (f" ({need} more than it has used)" if need != cost else "")
                    + f"; you have {have['total']}. A shorter film uses fewer"
                    + (f", and your monthly credits come back on {have['resets']}." if have["monthly"] else "."))
            if check:
                return
            take_m = min(need, have["monthly_left"])
            self.db.run("INSERT INTO credits(user_id, at, month, monthly, extra, kind, project_id, note) "
                        "VALUES (?,?,?,?,?,?,?,?)", (user["id"], time.time(), self.month_key(), -take_m,
                                                    -(need - take_m), "film", project["id"], project["title"][:120]))
        elif need < 0 and not check:
            self._refund(user, project, -need, "made shorter")
        if not check:
            self.db.run("UPDATE projects SET credits=? WHERE id=?", (cost, project["id"]))

    def _refund(self, user: dict, project: dict, n: int, why: str) -> None:
        """Credits back: to this month's allowance as far as this film used it this month, the rest as extra ones
        (credits from an earlier month are never lost)."""
        if n <= 0:
            return
        used_now = -int(self.db.value("SELECT COALESCE(SUM(monthly), 0) FROM credits WHERE project_id=? AND month=?",
                                      (project["id"], self.month_key()), 0) or 0)
        back_m = max(0, min(n, used_now))
        self.db.run("INSERT INTO credits(user_id, at, month, monthly, extra, kind, project_id, note) VALUES "
                    "(?,?,?,?,?,?,?,?)", (user["id"], time.time(), self.month_key(), back_m, n - back_m, "refund",
                                          project["id"], f"{project['title'][:100]} ({why})"))

    # ------------------------------------------------------------------ projects
    def projects(self, user: dict) -> list[dict]:
        return self.db.all("SELECT * FROM projects WHERE user_id=? AND deleted=0 ORDER BY created DESC", (user["id"],))

    def owned(self, user: dict, pid: str) -> dict:
        p = self.db.project(pid)
        if p is None or p["user_id"] != user["id"]:
            raise ActionError("No such video.")
        return p

    def length_ok(self, user: dict, length: float) -> None:
        """Any length from 5 seconds up to what their access allows (the chips are only quick picks)."""
        top = self.tier(user).max_seconds
        if not 5 <= float(length) <= top:
            raise ActionError(f"Pick a length between 5 seconds and {top // 60} minutes." if top >= 120 else
                              f"Pick a length between 5 and {top} seconds."
                              + (" Longer films come with special access." if top < max(LENGTHS) else ""))

    def create_project(self, user: dict, title: str, shapes: str, length: float, intent: str, pace: str = "balanced",
                       kind: str = "montage", style: str = "designed", cut_note: str = "", improvise: int = 35,
                       describe: str = "") -> dict:
        if shapes not in ("9:16", "16:9", "both"):
            raise ActionError("Pick a shape.")
        routed = ""
        if kind == "other":  # their own kind of film: the engine that can make it, and why (shown, and changeable)
            describe = " ".join((describe or "").split())[:600]
            if len(describe) < 3:
                raise ActionError("Describe the film you have in mind.")
            kind, routed = route_kind(describe)
            intent = (describe + (" " + intent if intent.strip() else "")).strip()
        self.length_ok(user, length)
        if pace not in ("calm", "balanced", "fast", "every", "other"):
            raise ActionError("Pick how fast it should cut.")
        if pace == "other":  # their own way of cutting: their words decide (every step reads them), no keyword guess
            if len(" ".join((cut_note or "").split())) < 3:
                raise ActionError("Describe how it should cut, in the box under the choices.")
            pace = "balanced"
        else:  # left at the default, the note may say what they want (one shot per beat, fast cuts)
            pace = pace_from_note(pace, " ".join(x for x in (intent, cut_note or "") if x))
        if kind not in ("montage", "voice", "talk"):
            raise ActionError("Pick what you are making.")
        if style not in STYLES:
            raise ActionError("Pick a style for the graphics.")
        if kind == "talk" and shapes == "both":
            shapes = "9:16"  # a talking video keeps its own shape
        pid = "p" + secrets.token_hex(6)
        now = time.time()
        title = " ".join(title.split())[:60] or time.strftime("My video, %d %b")
        mute = kind == "montage" and requirements.wants_no_clip_sound(" ".join([intent, cut_note or "", describe or ""]))
        self.db.run("INSERT INTO projects(id, user_id, title, shapes, length, intent, stage, created, updated, pace, kind, "
                    "music, style, cut_note, improvise, sound, routed) VALUES (?,?,?,?,?,?,'setup',?,?,?,?,?,?,?,?,?,?)",
                    (pid, user["id"], title, shapes, round(float(length), 1), " ".join(intent.split())[:900], now, now,
                     pace, kind, "none" if kind != "montage" or requirements.wants_no_music(intent) else "made", style,
                     " ".join((cut_note or "").split())[:400], max(0, min(100, int(improvise))),
                     "silent" if mute else "natural", routed))
        workdir(self.s, user["id"], pid).ensure()
        project = self.db.project(pid)
        from . import abilities

        abilities.note(self.db, project, " ".join([intent, cut_note or "", describe or ""]))
        return project

    def shapes(self, project: dict) -> list[str]:
        return ["9:16", "16:9"] if project["shapes"] == "both" else [project["shapes"]]

    def delete_project(self, project: dict) -> None:
        for job in self.db.all("SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','waiting')", (project["id"],)):
            self.db.job_done(job["id"], "cancelled")
        if self.db.one("SELECT id FROM jobs WHERE project_id=? AND status='running'", (project["id"],)):
            raise ActionError("Wait a moment: this video is being worked on. Try again when the step finishes.")
        remove_project_files(workdir(self.s, project["user_id"], project["id"]), self.cfg)
        self.db.run("UPDATE projects SET deleted=1, updated=? WHERE id=?", (time.time(), project["id"]))
        owner = self.user(project["user_id"])
        if owner and int(project.get("credits") or 0) > 0 and not project["version"]:  # never finished
            self._refund(owner, project, int(project["credits"]), "deleted before it was finished")
            self.db.run("UPDATE projects SET credits=0 WHERE id=?", (project["id"],))
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
            for table in ("pitches", "plans", "renders", "feedback", "events", "uploads", "clips", "jobs", "beats"):
                self.db.run(f"DELETE FROM {table} WHERE project_id=?", (pid,))
        self.db.run("DELETE FROM projects WHERE user_id=?", (user["id"],))
        self.db.run("UPDATE spend SET user_id=NULL, project_id=NULL WHERE user_id=?", (user["id"],))
        self.db.run("UPDATE audit SET user_id=NULL, ip=NULL, detail='' WHERE user_id=?", (user["id"],))
        for table in ("sessions", "devices", "passkeys", "challenges", "consents", "jobs", "credits", "gaps"):
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
            "credits": self.db.all("SELECT at, kind, monthly, extra, project_id, note FROM credits WHERE user_id=? "
                                   "ORDER BY id", (user["id"],)),
            "asked_for_not_possible_yet": self.db.all("SELECT at, ability, words, notify FROM gaps WHERE user_id=? "
                                                      "ORDER BY id", (user["id"],)),
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
        if self.paused() and user and not self.is_owner(user):
            raise ActionError("Flicello is paused for a moment. Try again a little later: nothing you've made is lost.")
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
        such jobs usually take (watching or listening, ideas, a sample)."""
        typical = self.typical_seconds(job["kind"])
        project = self.db.project(job["project_id"]) if job.get("project_id") else None
        if project is None or project["kind"] == "montage" or job["kind"] not in ("render", "revise"):
            return typical
        seconds = float(json.loads(project["info"] or "{}").get("seconds") or 0)
        rate = self.draw_rate(project["kind"], project["style"])
        if not seconds or rate is None:
            return typical
        shapes = len(self.shapes(project)) if project["kind"] == "voice" else 1
        return seconds * rate * shapes + (90.0 if job["kind"] == "revise" else 0.0)

    def ahead(self, job: dict) -> tuple[list[dict], list[dict]]:
        """The jobs a queued job waits for: those running and those queued before it, in its own lane (with the
        renderer's quick lane online, a short step never waits for a whole film; see db.LONG_KINDS)."""
        from .db import lane_of

        quick = self.db.one("SELECT at FROM heartbeat WHERE name='renderer-quick'")
        split = self.s.jobs == "remote" and quick is not None and time.time() - quick["at"] < 90
        rows = self.db.all("SELECT * FROM jobs WHERE status IN ('queued','running') AND id != ? AND kind != 'learn'",
                           (job["id"],))
        same = [r for r in rows if not split or lane_of(r["kind"]) == lane_of(job["kind"])]
        running = [r for r in same if r["status"] == "running"]
        queued = [r for r in same if r["status"] == "queued" and (r["priority"], r["id"]) < (job["priority"], job["id"])]
        return running, queued

    def eta(self, job: dict | None) -> int | None:
        """Seconds left: for a running job, what it should take corrected by how far it has got; for a queued one,
        also what is left of every job ahead of it (it said "about 10 min" while an hour-long film ran ahead)."""
        if not job:
            return None
        typical = self.expected_seconds(job)
        if job["status"] == "queued" and job.get("id"):  # a real one in the queue
            from . import cloud

            if cloud.can_take(self, job):  # a cloud renderer starts for it if the PC doesn't take it soon
                return int(typical + 60)
            running, queued = self.ahead(job)
            return int(typical + sum(self.eta(r) or 0 for r in running)
                       + sum(self.expected_seconds(r) for r in queued))
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
        """Music made for the film ('made'), or none ('none'). For a film from clips, 'no music' also takes away a
        song they uploaded (they can upload it again) and a made track (it stays on the list to pick again)."""
        self._guard(project, "setup")
        if choice not in ("made", "none"):
            raise ActionError("Pick music made for you, or no music.")
        self.db.run("UPDATE projects SET music=?, updated=? WHERE id=?", (choice, time.time(), project["id"]))
        if project["kind"] != "montage":
            return  # a voice video's music/ folder holds the recording itself
        work = workdir(self.s, project["user_id"], project["id"])
        if choice == "none" or project.get("song"):
            for old in work.music.glob("song.*"):
                old.unlink()
            self.db.run("UPDATE projects SET song=NULL, song_seconds=NULL WHERE id=?", (project["id"],))
        if choice == "made" and project.get("beat"):
            row = self.beat_row(project, project["beat"])
            if row:
                self._choose_beat(self.db.project(project["id"]), row)

    def mute_if_asked(self, project: dict, text: str) -> bool:
        """Their words asked for no sound from the clips: the film is switched to 'no clip sound', which the mix obeys
        for every shot whatever the plan says (see Pipeline.mute_clips). Said on screen in the step's log."""
        if project.get("kind") != "montage" or project.get("sound") == "silent" \
                or not requirements.wants_no_clip_sound(text):
            return False
        self.db.run("UPDATE projects SET sound='silent', updated=? WHERE id=?", (time.time(), project["id"]))
        project["sound"] = "silent"
        self.db.event(project["id"], "Asked for no sound from the clips: every shot is muted (music only).", "info")
        return True

    def set_sound(self, project: dict, sound: str) -> None:
        """With no music: keep the clips' own sound, or a silent film."""
        self._guard(project, "setup")
        if sound not in ("natural", "silent"):
            raise ActionError("Pick the clips' own sound, or silence.")
        self.db.run("UPDATE projects SET sound=?, updated=? WHERE id=?", (sound, time.time(), project["id"]))

    def set_kind(self, project: dict, kind: str) -> None:
        """Before starting, they can move a film to another engine (when the routing of 'something else' was wrong)."""
        self._guard(project, "setup")
        if kind not in ("montage", "voice", "talk"):
            raise ActionError("Pick what you are making.")
        self.db.run("UPDATE projects SET kind=?, routed='', music=?, updated=? WHERE id=?",
                    (kind, "made" if kind == "montage" else "none", time.time(), project["id"]))

    def set_length(self, project: dict, length: int) -> None:
        self._guard(project, "setup")
        self.length_ok(self.user(project["user_id"]), length)
        self.db.run("UPDATE projects SET length=?, updated=? WHERE id=?", (float(length), time.time(), project["id"]))

    def set_settings(self, project: dict, shapes: str, length: float | None) -> str:
        """Change the shape and the length after the film was made (at the clips step; later, go back to it first).
        Returns what changed, in words, with what it means for their music."""
        self._guard(project, "setup")
        if shapes not in ("9:16", "16:9", "both"):
            raise ActionError("Pick a shape.")
        if project["kind"] == "talk" and shapes == "both":
            shapes = "9:16"
        said = [{"9:16": "vertical 9:16", "16:9": "widescreen 16:9", "both": "both shapes"}[shapes]]
        tail = ""
        if project["kind"] == "montage" and length is not None:
            self.length_ok(self.user(project["user_id"]), length)
            length = round(float(length), 1)
            self.db.run("UPDATE projects SET length=? WHERE id=?", (length, project["id"]))
            info = json.loads(project["info"]) if project.get("info") else {}
            if info.get("fit") and length > float(info["fit"]["to"]):  # they want it longer than the footage carries
                info["fit_declined"] = True
                self.db.run("UPDATE projects SET info=? WHERE id=?", (json.dumps(info), project["id"]))
            said.append(f"{length:g} seconds" if length < 120 else f"{length / 60:g} minutes")
            row = self.beat_row(project, project.get("beat")) if project.get("music") == "made" else None
            if project.get("song") and project.get("song_seconds") and float(project["song_seconds"]) < length - 0.5:
                tail = (f" Your song is {float(project['song_seconds']):.0f} seconds: the film stops when it ends, so "
                        "extend it below or pick a shorter length.")
            elif row and not self._track_fits({**project, "length": length}, row):
                tail = " Your music was made for the old length: it's made again for this one when the film starts."
        self.db.run("UPDATE projects SET shapes=?, updated=? WHERE id=?", (shapes, time.time(), project["id"]))
        return f"Saved: {', '.join(said)}.{tail}"

    def add_note(self, project: dict, note: str) -> None:
        """Anything else they want, in their words, added to the film's request before it starts."""
        self._guard(project, "setup")
        note = " ".join((note or "").split())[:400]
        if len(note) < 3:
            raise ActionError("Write what you'd like in a few words.")
        intent = " ".join(f"{project.get('intent') or ''} {note}".split())[:900]
        self.db.run("UPDATE projects SET intent=?, updated=? WHERE id=?", (intent, time.time(), project["id"]))
        self.mute_if_asked(project, note)
        from . import abilities

        abilities.note(self.db, {**project, "intent": intent}, note)

    # ------------------------------------------------------------------ music made for the film (music/made.py)
    def can_compose(self) -> bool:
        """Tracks can be made: the renderer says it has an ElevenLabs key (or, with local jobs, this machine has)."""
        if self.s.jobs == "remote":
            row = self.db.one("SELECT info FROM heartbeat WHERE name IN ('renderer', 'renderer-quick') AND info IS NOT "
                              "NULL ORDER BY at DESC LIMIT 1")
            try:
                return bool(json.loads(row["info"]).get("music")) if row else False
            except (TypeError, ValueError):
                return False
        from ..music.compose import available

        return available()

    def beats(self, project: dict) -> list[dict]:
        """Every track made for this film, newest first."""
        rows = self.db.all("SELECT * FROM beats WHERE project_id=? ORDER BY id DESC", (project["id"],))
        for r in rows:
            r["idea"] = json.loads(r["idea"])
        return rows

    def beat_row(self, project: dict, beat_id) -> dict | None:
        if not beat_id:
            return None
        row = self.db.one("SELECT * FROM beats WHERE id=? AND project_id=?", (int(beat_id), project["id"]))
        if row:
            row["idea"] = json.loads(row["idea"])
        return row

    def beats_left(self, project: dict) -> int:
        """Tracks the music service may still make for this film (their access level's allowance)."""
        tier = self.tier(self.user(project["user_id"]))
        made = self.db.value("SELECT COUNT(*) FROM beats WHERE project_id=? AND maker='elevenlabs'", (project["id"],), 0)
        return max(0, min(tier.beats, MAX_TRACKS) - made)

    @staticmethod
    def track_seconds(project: dict) -> float:
        """How long the film's own track is: the film and its ending (a cut), or the recording and a breath (a bed)."""
        if project["kind"] == "voice":
            return float(project.get("song_seconds") or 60.0) + 4.0
        return float(project["length"]) + 2.0

    def make_beat(self, project: dict, ask: str = "", base: int = 0, change: str = "") -> None:
        """They asked for music in their words (or a change to a track they heard). The renderer reads it, makes it
        (a short film's whole track, or a 30-second taste of a long one) and they listen before going on."""
        self._guard(project, "setup", "understood")
        if project["kind"] == "talk":
            raise ActionError("A talking video keeps its own sound.")
        if project["kind"] == "voice" and not project.get("song_seconds"):
            raise ActionError("Add your recording first: the music is made to its length.")
        if not self.can_compose():
            raise ActionError("Music can't be made right now. Your film gets our own simple beat instead; you can "
                              "also upload a song.")
        if self.beats_left(project) <= 0:
            raise ActionError(f"That's {MAX_TRACKS} tracks for this film, the most it can have made: pick one of "
                              "them, or upload your own song.")
        ask = " ".join((ask or "").split())[:400]
        change = " ".join((change or "").split())[:300]
        if base and not self.beat_row(project, base):
            raise ActionError("Pick one of the tracks to change.")
        if base and not change:
            raise ActionError("Say what to change about it.")
        if project["kind"] == "montage" and project.get("song"):  # music made for them instead of their song
            self.set_music(project, "made")
            project = self.db.project(project["id"])
        self.db.run("UPDATE projects SET music='made', updated=? WHERE id=?", (time.time(), project["id"]))
        self._enqueue(project, "beat", {"ask": ask, "base": int(base or 0), "change": change, "back": project["stage"]})

    def use_beat(self, project: dict, beat_id: int) -> None:
        """An earlier track is the one. At 'what I understood' a film from clips has its music measured again first
        (the cut follows the song's beats), so that is a short step on the renderer."""
        self._guard(project, "setup", "understood")
        row = self.beat_row(project, beat_id)
        if row is None:
            raise ActionError("Pick one of the tracks.")
        if project["stage"] == "understood" and project["kind"] == "montage":
            self._enqueue(project, "beat", {"use": int(beat_id), "back": "understood"})
            return
        self._choose_beat(project, row)

    def _choose_beat(self, project: dict, row: dict) -> None:
        """This track is the film's music. For a film from clips a whole track (or the full one made from a taste)
        goes where an uploaded song would; a taste waits until its full track is made (when the film starts)."""
        import shutil

        self.db.run("UPDATE projects SET beat=?, music='made', song=NULL, song_seconds=NULL, updated=? WHERE id=?"
                    if project["kind"] == "montage" else "UPDATE projects SET beat=?, music='made', updated=? WHERE id=?",
                    (row["id"], time.time(), project["id"]))
        if project["kind"] != "montage":
            return  # a voice video's music bed is picked up when it is drawn
        work = workdir(self.s, project["user_id"], project["id"]).ensure()
        for old in work.music.glob("song.*"):
            old.unlink()
        name = row["full_file"] or (row["file"] if row["whole"] else None)
        if name and (work.beats / name).exists():
            shutil.copy2(work.beats / name, work.music / f"song{Path(name).suffix}")

    def _music_budget_ok(self, project: dict) -> bool:
        cap = self.s.limits
        return (self.db.month_spend() < cap.monthly_usd * 0.98
                and self.db.project_spend(project["id"]) < cap.video_usd * 0.98)

    def _spend_music(self, project: dict, user: dict, job: dict, seconds: float, took: float) -> None:
        """What a track cost (ElevenLabs bills by the minute of music), on the same books as the AI."""
        from ..music import made

        try:
            self._spender(project, user["id"], job["id"])(Usage("beat", made.MODEL, "elevenlabs",
                                                                usd=made.cost(seconds), seconds=took))
        except BudgetExceeded as exc:  # the track is made and paid for: the owner hears about the cap, the film goes on
            alert(self.db, self.s, "budget", f"Music for a film went over a budget: {exc}.", "warning")

    @staticmethod
    def _sound(idea: dict, purpose: str):
        from ..music import made

        return made.Sound(title=str(idea.get("title") or "Your track")[:60], bpm=made.fit_bpm(idea.get("bpm") or 96, purpose),
                          styles=list(idea.get("styles") or [])[:12], avoid=list(idea.get("avoid") or [])[:8],
                          parts={"intro": idea.get("intro") or [], "build": idea.get("build") or [],
                                 "drop": idea.get("drop") or [], "lift": idea.get("drop") or [],
                                 "breath": idea.get("breath") or [], "ending": idea.get("ending") or []})

    def _beat_facts(self, path: Path, purpose: str, taste_of: float | None = None) -> str:
        """What the made track really is, measured (the model does not always put the drop where it was asked)."""
        from ..music import analyze_music

        try:
            a = analyze_music(path)
        except Exception:  # noqa: BLE001 - the track is fine to use; we just can't describe it
            return ""
        clock = lambda t: f"{int(t) // 60}:{int(t) % 60:02d}"  # noqa: E731
        words = f"{a.tempo:.0f} beats a minute"
        if taste_of:
            return (f"A {a.duration:.0f}-second taste, {words}. The full {clock(taste_of)} track is made in the same "
                    "sound when your film starts.")
        words += f", {clock(a.duration)} long"
        if purpose == "cut" and a.drops:
            words += f"; the drop lands at {clock(a.drops[0])}"
        return words + "."

    def _film_for_music(self, project: dict) -> str:
        """What the music maker is told about the film: everything they asked for, and a picked look's music."""
        film = ask_text(project)
        look, _ = self._look(project)
        if look is not None:
            film += f"\nThe look they picked: {look.title} ({look.art_style}). Its music: {look.music or 'not said'}."
        return film

    def _compose(self, job: dict, project: dict, user: dict, pipe, ask: str = "", base: dict | None = None,
                 change: str = "", span: tuple[float, float] = (0.0, 1.0)) -> dict:
        """Read what they asked for, lay the track out, make it (whole, or a taste of a long one), measure it and
        keep it. When the music service can't make it, our own simple beat stands in and they are told. The job's
        bar moves through `span`."""
        from ..music import made
        from ..music.compose import BadPlan, ComposeError, available, compose_plan
        from ..music.house import make_beat as house_beat

        def prog(f: float, msg: str) -> None:
            self.db.job_progress(job["id"], span[0] + (span[1] - span[0]) * f, msg)

        purpose = "bed" if project["kind"] == "voice" else "cut"
        seconds = self.track_seconds(project)
        work = workdir(self.s, project["user_id"], project["id"]).ensure()
        work.beats.mkdir(parents=True, exist_ok=True)
        prog(0.05, "Reading what you asked for" if ask or change else "Choosing a sound for your film")
        whole = made.whole(seconds)
        shape = [({"lift": "last drop (the drop's styles again)", "groove": "steady groove"}.get(n, n), t) for n, t in
                 made.section_times(made.layout(seconds, 96, purpose) if whole else made.taste_layout(), 96)]
        try:
            idea = pipe.brain.beat_idea(ask, self._film_for_music(project), purpose, seconds, project["pace"],
                                        base["idea"] if base else None, change, shape)
        except BudgetExceeded:  # (and PersonLimit) the money rules decide, not a fallback
            raise
        except Exception as exc:  # noqa: BLE001 - the AI can't read it now (down, refused key): read it simply here
            from .brain.core import local_beat_idea

            self.db.event(project["id"], f"music: the AI couldn't read the request ({type(exc).__name__}: "
                          f"{' '.join(str(exc).split())[:160]}); read it without the AI", "warn", job["id"])
            idea = local_beat_idea(ask, self._film_for_music(project), purpose, project["pace"],
                                   base["idea"] if base else None, change)
        name = f"beat-{secrets.token_hex(4)}.mp3"
        maker, note, song_id, took = "elevenlabs", "", "", 0.0
        try:
            if not available():
                raise ComposeError("no ElevenLabs key on this machine")
            if not self._music_budget_ok(project):
                raise ComposeError("the music budget is used up")
            for attempt in range(2):
                sound = self._sound(idea.model_dump(), purpose)
                sections = made.layout(seconds, sound.bpm, purpose) if whole else made.taste_layout()
                plan = made.plan(sections, sound, purpose)
                prog(0.2, f"Composing {'your track' if whole else 'a 30-second taste'}: {idea.title}")
                t0 = time.time()
                try:
                    made_now = compose_plan(plan, work.beats / name, store=not whole)
                except BadPlan as exc:  # a style it refused (a name, usually): described again without it, once
                    if attempt:
                        raise
                    self.db.step(job["id"], "Describing the sound again in other words")
                    idea = pipe.brain.beat_idea(ask, self._film_for_music(project), purpose, seconds, project["pace"],
                                                base["idea"] if base else None,
                                                (change + ". " if change else "") + "The music model refused the last "
                                                f"styles ({exc.detail[:200]}): describe the sound with no names at all.",
                                                shape)
                    continue
                took = time.time() - t0
                song_id = made_now["song_id"]
                self._spend_music(project, user, job, made_now["seconds"], took)
                break
        except (BudgetExceeded, JobStopped):  # (and PersonLimit) the money rules and the person decide
            raise
        except Exception as exc:  # noqa: BLE001 - the music service or describing it again broke: never the film
            log.warning("a track could not be made: %s", exc, exc_info=not isinstance(exc, ComposeError))
            self.db.event(project["id"], f"music: {exc}; our own beat instead", "warn", job["id"])
            maker, whole, name = "house", True, name.replace(".mp3", ".wav")
            note = " The music service couldn't make it just now, so this is our own simple beat."
            house_beat(work.beats / name, seconds, {"calm": "calm", "fast": "fast", "every": "every"}.get(
                project["pace"], "balanced") if purpose == "cut" else "calm")
        prog(0.85, "Listening to it: the tempo and the drop")
        facts = self._beat_facts(work.beats / name, purpose, None if whole else seconds) + note
        rid = self.db.run("INSERT INTO beats(project_id, created, ask, idea, plan, file, song_id, seconds, whole, facts, "
                          "maker) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (project["id"], time.time(), " ".join(x for x in (ask, change) if x)[:500],
                           idea.model_dump_json(), json.dumps(plan if maker == "elevenlabs" else {}), name, song_id,
                           seconds, 1 if whole else 0, facts.strip(), maker))
        return self.beat_row(project, rid)

    def _full_from_taste(self, job: dict, project: dict, user: dict, row: dict,
                         span: tuple[float, float] = (0.0, 1.0)) -> dict:
        """The full track of a long film, made once they picked its taste: the taste is its reference, so it has
        the same sound. If the taste can no longer be referred to, the same description without it; if the music
        service is down, our own simple beat (and they are told)."""
        from ..music import made
        from ..music.compose import ComposeError, compose_plan
        from ..music.house import make_beat as house_beat

        purpose = "bed" if project["kind"] == "voice" else "cut"
        seconds = self.track_seconds(project)
        sound = self._sound(row["idea"], purpose)
        bar = made.bar_seconds(sound.bpm)
        intro, groove, _ = made.TASTE_BARS
        ref = {"song_id": row["song_id"], "range": {"start_ms": int(intro * bar * 1000),
                                                     "end_ms": int(min((intro + groove) * bar, intro * bar + 30) * 1000)}}
        work = workdir(self.s, project["user_id"], project["id"]).ensure()
        name = Path(row["file"]).stem + "-full.mp3"
        what = "again, for the film's new length" if row["whole"] or row["full_file"] else "from the taste you picked"
        self.db.job_progress(job["id"], span[0] + (span[1] - span[0]) * 0.1,
                             f"Making the full {int(seconds) // 60}:{int(seconds) % 60:02d} track {what}")
        note = ""
        try:
            if not self._music_budget_ok(project):
                raise ComposeError("the music budget is used up")
            t0 = time.time()
            sections = made.layout(seconds, sound.bpm, purpose)
            try:
                done = compose_plan(made.plan(sections, sound, purpose, reference=ref if row["song_id"] else None),
                                    work.beats / name)
            except ComposeError:
                if not row["song_id"]:
                    raise
                done = compose_plan(made.plan(sections, sound, purpose), work.beats / name)
                note = " (made from the same description: the taste itself could no longer be used as a reference)"
            self._spend_music(project, user, job, done["seconds"], time.time() - t0)
        except ComposeError as exc:
            self.db.event(project["id"], f"music: the full track failed ({exc}); our own beat instead", "warn", job["id"])
            name = Path(row["file"]).stem + "-full.wav"
            house_beat(work.beats / name, seconds, "calm" if purpose == "bed" else "balanced")
            note = " The music service couldn't make the full track, so the film uses our own simple beat."
        self.db.run("UPDATE beats SET full_file=?, facts=?, seconds=? WHERE id=?",  # seconds: the length it's made for
                    (name, (self._beat_facts(work.beats / name, purpose) + note).strip(), seconds, row["id"]))
        return self.beat_row(project, row["id"])

    def _track_fits(self, project: dict, row: dict) -> bool:
        """A made track still fits the film: made for about its length now. The length can change after the track
        was made; too short and the film would stop with the music, much too long and its drop and ending would come
        after the film is over."""
        need, made_for = self.track_seconds(project), float(row.get("seconds") or 0)
        return not made_for or need - 1.0 <= made_for <= need * 1.3 + 3.0

    def _ensure_track(self, job: dict, project: dict, user: dict, pipe,
                      span: tuple[float, float] = (0.0, 1.0)) -> Path | None:
        """The made track a film uses, ready at its full length: the one they picked (its full track made now if it
        was a taste), else one made from their note when music can be made here. None: our own simple beat (the
        pipeline's for a film from clips, the bed's for a voice video) or no music."""
        from ..music.compose import available

        if project["music"] != "made" or project["kind"] == "talk" or (project["kind"] == "montage" and project.get("song")):
            return None
        row = self.beat_row(project, project.get("beat"))
        if row is None:
            if not available() or self.beats_left(project) <= 0:
                return None
            row = self._compose(job, project, user, pipe, span=span)
        if (not row["whole"] and not row["full_file"]) or not self._track_fits(project, row):
            row = self._full_from_taste(job, project, user, row, span)
        self._choose_beat(self.db.project(project["id"]), row)
        work = workdir(self.s, project["user_id"], project["id"])
        return work.beats / (row["full_file"] or row["file"])

    def _job_beat(self, job, project, user) -> None:
        params = job["params"]
        back = params.get("back") or "setup"
        pipe = self._pipeline(job, project, user)
        if params.get("use"):  # an earlier track, picked at 'what I understood'
            row = self.beat_row(project, params["use"])
            if row is not None:
                self._choose_beat(project, row)
        else:
            row = self._compose(job, project, user, pipe, params.get("ask", ""),
                                self.beat_row(project, params.get("base")), params.get("change", ""))
            self._choose_beat(project, row)
        project = self.db.project(project["id"])
        if back == "understood" and project["kind"] == "montage":  # the cut follows this music now: measure it
            self._ensure_track(job, project, user, pipe)
            self._music_line(pipe, self.db.project(project["id"]), job)
        self.db.set_stage(project["id"], back)

    def _music_line(self, pipe, project: dict, job: dict) -> None:
        """What 'what I understood' says about the music, after the music changed there (measured, no AI)."""
        u = self.understanding(project)
        if not u:
            return
        self.db.job_progress(job["id"], 0.9, "Listening to the new music")
        song = pipe.song()
        clock = lambda t: f"{int(t) // 60}:{int(t) % 60:02d}"  # noqa: E731
        u["music"] = (f"Your new track: about {song.tempo:.0f} beats a minute, {clock(song.duration)} long"
                      + (f", the drop at {clock(song.drops[0])}" if song.drops else "") + ".")
        self.db.run("UPDATE projects SET understood=? WHERE id=?", (json.dumps(u), project["id"]))

    def start(self, project: dict) -> None:
        self._guard(project, "setup")
        if project["kind"] == "montage":
            n = self.db.value("SELECT COUNT(*) FROM clips WHERE project_id=?", (project["id"],), 0)
            if requirements.is_talking_video_request(project["intent"], n):
                self.db.run("UPDATE projects SET kind='talk', music='none', updated=? WHERE id=?", (time.time(), project["id"]))
                self.db.event(project["id"], "Switched to a talking video: the note asks for graphics for what is said.", "info")
                project = self.db.project(project["id"])
        if project["kind"] != "montage":
            tier = self.tier(self.user(project["user_id"]))
            took = float(project.get("song_seconds") or 0.0) if project["kind"] == "voice" else float(
                self.db.value("SELECT COALESCE(SUM(seconds), 0) FROM clips WHERE project_id=?", (project["id"],), 0.0))
            if took > tier.max_recording + 1:
                raise ActionError(f"That recording is {took / 60:.1f} minutes; with your access a video can use up to "
                                  f"{tier.max_recording / 60:.0f}. Trim it and upload it again.")
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
        self._pay_for(project, check=True)
        self._enqueue(project, "prepare")
        self._pay_for(project)

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

    def another(self, project: dict, reason: str = "", own_idea: str = "", base: int = 0,
                improvise: int | None = None) -> None:
        """One more idea: something different (with why), their own idea shaped up, or (looks only) the look `base`
        with the change in `reason`."""
        self._guard(project, "choose")
        if self.ideas_left(project) <= 0:
            raise ActionError(f"That's {self.idea_cap(project)} ideas for this film, the most it can have: pick one of "
                              "them. To start over, go back to the clips and settings.")
        if base and not self.db.one("SELECT id FROM pitches WHERE id=? AND project_id=?", (base, project["id"])):
            raise ActionError("Pick one of the looks to change.")
        if base and not reason.strip():
            raise ActionError("Say what to change about it.")
        if uses_looks(project) and (base or own_idea.strip()):
            prof = self.profile(self.user(project["user_id"]))
            look = next((p["pitch"] for p in self.pitches(project) if p["id"] == base), {})
            prof.note_look("changed" if base else "own", look, reason if base else own_idea)
            prof.save()
        if improvise is not None and not reason.strip() and not own_idea.strip():
            reason = "Improvise: take it somewhere they would not have thought of"
        self._enqueue(project, "pitch", {"reason": " ".join(reason.split())[:300], "own": " ".join(own_idea.split())[:600],
                                         **({"improvise": int(improvise)} if improvise is not None else {}),
                                         "base": int(base or 0)})

    def idea_cap(self, project: dict) -> int:
        user = self.user(project["user_id"])
        return (12 if uses_looks(project) else 9) if user and self.is_owner(user) else MAX_IDEAS

    def ideas_left(self, project: dict) -> int:
        """More ideas (or looks) this film may still have: another, their own, improvised, changed."""
        made = self.db.value("SELECT COUNT(*) FROM pitches WHERE project_id=?", (project["id"],), 0)
        return max(0, self.idea_cap(project) - made)

    @staticmethod
    def sample_tries_left(project: dict) -> int:
        return max(0, MAX_SAMPLE_TRIES - int(project.get("sample_tries") or 0))

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
                              "That's all the samples for this film: make this version, then ask for the changes you "
                              "still want.")
        if not note:
            raise ActionError("Say what to change, so the next sample is better.")
        self.db.run("UPDATE projects SET sample_tries=sample_tries+1 WHERE id=?", (project["id"],))
        self.mute_if_asked(project, note)
        self._enqueue(project, "sample", {"pitch": project["chosen"], "note": note})

    # ------------------------------------------------------------------ going back, stopping a step
    BACK_FROM = {"setup": ("understood", "choose", "review", "failed", "paused"), "understood": ("choose", "review"),
                 "choose": ("review",)}
    STOP_TO = {"prepare": "setup", "pitch": "choose", "sample": "choose", "render": "review", "revise": "done"}

    def back_targets(self, project: dict) -> list[str]:
        """The earlier stages this video can go back to now (none while a step runs)."""
        if self.db.active_job(project["id"]):
            return []
        out = [to for to, froms in self.BACK_FROM.items() if project["stage"] in froms]
        if "choose" in out and not self.pitches(project):
            out.remove("choose")  # an explainer without looks has no ideas to go back to
        if "understood" in out and not project.get("understood"):
            out.remove("understood")
        return out

    def go_back(self, project: dict, to: str) -> None:
        """Back to an earlier stage to change something. To the clips and settings: their uploads stay, the ideas
        made from the old set go (they would be about clips that may change), and starting again re-uses everything
        already measured. To the ideas: the sample stays as it was."""
        if to not in self.back_targets(project):
            raise ActionError("This video can't go back there right now.")
        if to == "choose":
            self.back_to_ideas(project)
            return
        if to == "understood":  # change what was agreed: the ideas made from it go
            self.db.run("DELETE FROM pitches WHERE project_id=?", (project["id"],))
            self.db.run("UPDATE projects SET chosen=NULL, pitch_round=0, updated=? WHERE id=?", (time.time(), project["id"]))
            self.db.set_stage(project["id"], "understood")
            return
        self.db.run("DELETE FROM pitches WHERE project_id=?", (project["id"],))
        self.db.run("UPDATE projects SET chosen=NULL, pitch_round=0, sample_tries=0, error=NULL, note='', understood=NULL, "
                    "brief=NULL, updated=? WHERE id=?", (time.time(), project["id"]))
        self.db.set_stage(project["id"], "setup")
        self.db.event(project["id"], f"Went back to the setup from '{project['stage']}'.", "info")

    def stop(self, project: dict) -> None:
        """Stop the step that is running or waiting, and go back to where they were before it. A step already
        running on the renderer is dropped there (the lane starts again), so nothing it was making lands later."""
        job = self.db.active_job(project["id"])
        if job is None:
            raise ActionError("Nothing is running for this video.")
        to = self.STOP_TO.get(job["kind"], "setup")
        if job["kind"] == "beat":
            to = (json.loads(job["params"] or "{}") if isinstance(job["params"], str) else job["params"]).get("back") or "setup"
        if job["kind"] in ("sample", "pitch") and not self.pitches(project):
            to = "understood" if project.get("understood") else "setup"
        self.db.run("UPDATE jobs SET status='cancelled', finished=?, abort=CASE WHEN status='running' THEN ? ELSE abort "
                    "END, error=? WHERE id=?", (time.time(), time.time(), f"stopped by the person; back to {to}", job["id"]))
        self.db.step(job["id"], "Stopped.")
        if to == "setup":
            self.db.run("DELETE FROM pitches WHERE project_id=?", (project["id"],))
            self.db.run("UPDATE projects SET chosen=NULL, pitch_round=0, sample_tries=0, understood=NULL, brief=NULL "
                        "WHERE id=?", (project["id"],))
        self.db.set_stage(project["id"], to)
        self.db.event(project["id"], f"Stopped the {job['kind']} step; back to '{to}'.", "info", job["id"])

    def edit_board(self, project: dict, form: dict) -> None:
        """Their changes to the storyboard (drop, move, swap, a note on a shot): a new sample with them."""
        self._guard(project, "review")
        if project["kind"] != "montage":
            raise ActionError("That can't be done for this video.")
        edits: dict = {"drop": [], "move": {}, "swap": {}, "notes": {}}
        for key, value in form.items():
            kind, _, k = key.partition("_")
            if not k.isdigit() or value in (None, "", "0"):
                continue
            if kind == "drop":
                edits["drop"].append(int(k))
            elif kind == "move" and str(value) in ("-1", "1"):
                edits["move"][k] = int(value)
            elif kind == "swap" and "#" in str(value):
                edits["swap"][k] = str(value)[:20]
            elif kind == "note" and str(value).strip():
                edits["notes"][k] = " ".join(str(value).split())[:300]
        if not any(edits.values()):
            raise ActionError("Mark at least one shot to change.")
        # a note here is about its own shot (the AI fixes that shot, its sound too): it never mutes the whole film
        if edits["notes"]:  # a note costs an AI call: the same limit as notes on the sample
            if project["sample_tries"] >= MAX_SAMPLE_TRIES:
                raise ActionError("Let's try a different story instead: go back and pick another idea.")
            self.db.run("UPDATE projects SET sample_tries=sample_tries+1 WHERE id=?", (project["id"],))
        self._enqueue(project, "sample", {"pitch": project["chosen"], "edits": edits})

    def want_gap(self, project: dict, ability: str) -> None:
        """They want to hear when an ability they asked for exists (the owner tells them from the admin page)."""
        if not self.db.one("SELECT id FROM gaps WHERE project_id=? AND ability=?", (project["id"], ability)):
            raise ActionError("That wasn't something this film asked for.")
        self.db.run("UPDATE gaps SET notify=1 WHERE project_id=? AND ability=?", (project["id"], ability))

    def tell_gap(self, ability: str) -> int:
        """The owner says an ability has shipped: everyone who asked to hear is emailed (once). Returns how many."""
        from . import mail
        from .abilities import BY_ID

        rows = self.db.all("SELECT g.id, u.email, COALESCE(u.display, u.name) AS who FROM gaps g JOIN users u ON "
                           "u.id = g.user_id WHERE g.ability=? AND g.notify=1 AND g.told IS NULL", (ability,))
        name = BY_ID[ability].name if ability in BY_ID else ability
        sent = 0
        for r in rows:
            if r["email"] and mail.available(self.s):
                site = (self.s.site_url or "").rstrip("/")
                try:
                    mail.send(self.s, r["email"], *mail.notice_email(self.s, "Flicello can do it now", [
                        f"You asked Flicello for {name}. It can do that now.", f"Make a film with it: {site}/new"]))
                    sent += 1
                except Exception:  # noqa: BLE001 - marked as told anyway; the owner sees the count
                    log.warning("could not email about a new ability")
            self.db.run("UPDATE gaps SET told=? WHERE id=?", (time.time(), r["id"]))
        return sent

    def board(self, project: dict, plan_id: int | None) -> dict | None:
        row = self.db.one("SELECT board FROM plans WHERE id=? AND project_id=?", (plan_id, project["id"])) if plan_id else None
        return json.loads(row["board"]) if row and row["board"] else None

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
            self.mute_if_asked(project, fix)
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
            money = self.money()
            month = self.db.month_spend()
            cap = money["monthly_usd"]
            if month >= 0.8 * cap:
                alert(self.db, self.s, "budget", f"Spend this month is ${month:.2f} of the ${cap:.2f} cap.", "warning")
            if month >= cap:
                raise BudgetExceeded(f"the monthly budget (${cap:.2f}) is used up")
            if project and self.db.project_spend(project["id"]) >= money["video_usd"]:
                raise BudgetExceeded(f"this video reached its budget (${money['video_usd']:.2f})")
            person = self.user(user_id) if user_id else None
            if person and not self.is_owner(person):
                window = time.time() - 86400
                spent = self.person_spend(user_id)
                if spent >= money["person_day_usd"]:
                    # back once enough of the last day's spending is more than a day old
                    rows = self.db.all("SELECT at, usd FROM spend WHERE user_id=? AND billed=1 AND at > ? ORDER BY at",
                                       (user_id, window))
                    over, resume = spent - money["person_day_usd"] * 0.8, time.time() + 3600
                    for r in rows:
                        over -= r["usd"]
                        if over <= 0:
                            resume = r["at"] + 86400
                            break
                    raise PersonLimit(f"this account's daily share (${money['person_day_usd']:.2f}) is used", resume)
        return spend

    def _pipeline(self, job: dict, project: dict, user: dict, lo: float = 0.0, hi: float = 1.0) -> Pipeline:
        owner = self.is_owner(user)
        work = workdir(self.s, user["id"], project["id"])

        now = {"f": 0.0, "msg": "", "t": time.monotonic()}

        def progress(f: float, msg: str) -> None:
            now.update(f=f, msg=msg, t=time.monotonic())
            self.db.job_progress(job["id"], lo + (hi - lo) * f, msg)

        def pulse() -> None:  # the AI is still writing: say how long, so the step is seen to be alive
            mins = int((time.monotonic() - now["t"]) // 60)
            if mins >= 1 and now["msg"]:
                self.db.job_progress(job["id"], lo + (hi - lo) * now["f"], f"{now['msg']} ({mins} min)")

        pipe = Pipeline(work, self.cfg, None, progress, owner=owner)
        pipe.no_music = project["music"] == "none" and not project["song"]
        pipe.natural_sound = project.get("sound") == "natural"
        # no sound from the clips: asked for in this film's words (the mix obeys, music or not), or their style says
        # "music only" and music plays (unless this film's own words ask to hear its clips)
        pipe.mute_clips = project.get("sound") == "silent" or (
            project["kind"] == "montage" and not pipe.no_music and self.profile(user).answers.get("sound") == "music"
            and not requirements.wants_clip_sound(ask_text(project)))
        kind = brain_kind_for(self.s, owner)
        pipe.brain = make_brain(kind, self.s, self._spender(project, user["id"], job["id"]), work.brain,
                                pipe.project.load_signals)
        if hasattr(getattr(pipe.brain, "backend", None), "pulse"):
            pipe.brain.backend.pulse = pulse
        return pipe

    def _context(self, pipe: Pipeline, project: dict, user: dict):
        ctx = pipe.context(self.shapes(project), project["length"], ask_text(project),
                           rules_for(self.profile(user).rules, project["kind"]),
                           project["pace"])
        # what they said on earlier samples and versions of this film still holds ('beat sync' once is beat sync)
        said = [project.get("intent") or "", project.get("cut_note") or ""]
        said += [r["text"] for r in self.db.all("SELECT text FROM feedback WHERE project_id=? AND text<>''", (project["id"],))]
        if hasattr(ctx, "sync_if_asked"):
            ctx.sync_if_asked(*said)
        return ctx

    def run_job(self, job: dict) -> None:
        project = self.db.project(job["project_id"]) if job["project_id"] else None
        user = self.user(job["user_id"]) if job["user_id"] else None
        if project is None or user is None:
            self.db.job_done(job["id"], "cancelled", "project or person gone")
            return
        handler = getattr(self, f"_job_{job['kind']}")
        if project["stage"] == "paused" and job["kind"] in JOB_STAGE:  # carrying on after a wait
            self.db.set_stage(project["id"], JOB_STAGE[job["kind"]])
        try:
            if job["kind"] in JOB_STAGE and self.money()["monthly_usd"] <= self.db.month_spend() \
                    and brain_kind_for(self.s, self.is_owner(user)) == "api":
                raise BudgetExceeded("the monthly budget is used up")
            handler(job, project, user)
            if self._stopped(job):
                return  # stopped by the person as it finished: what it made is not used
            self._posters(project)
            self.db.job_done(job["id"])
        except JobStopped:
            log.info("job %s stopped by the person", job["id"])
            self._stopped(job)
            return
        except PersonLimit as exc:
            if self._stopped(job):  # stopped by the person meanwhile: it stays stopped
                return
            wait = max(60.0, exc.resume_at - time.time())
            self.db.requeue(job["id"], delay=wait, error=str(exc))
            self.db.step(job["id"], "Waiting: this account's share for today is used")
            if job["kind"] in JOB_STAGE:
                hours = wait / 3600
                when = f"in about {hours:.0f} hour{'s' if round(hours) != 1 else ''}" if hours >= 1.5 else \
                    f"in about {max(1, int(wait // 60))} minutes"
                self.db.set_stage(project["id"], "paused", note=(
                    f"Paused: your account has used today's share of AI and rendering. It carries on by itself {when}; "
                    "you can leave this page."))
        except BudgetExceeded as exc:
            if self._stopped(job):
                return
            self.db.run("UPDATE jobs SET status='waiting', error=? WHERE id=?", (str(exc), job["id"]))
            if job["kind"] in JOB_STAGE:
                self.db.set_stage(project["id"], "paused", note=f"Paused: {exc}. The owner has been told.")
            alert(self.db, self.s, "budget", f"A job is waiting: {exc}.")
        except Exception as exc:  # noqa: BLE001 - every failure is recorded, retried once, then shown
            if self._stopped(job):  # stopped by the person, then it broke: nothing to retry or show them
                log.info("job %s stopped by the person (it then failed: %s)", job["id"], exc)
                return
            detail = traceback.format_exc()
            self.db.event(project["id"], f"{job['kind']} failed (try {job['attempts']}): {type(exc).__name__}: "
                          f"{' '.join(str(exc).split())[:200]}\n{detail[-3000:]}", "error", job["id"])
            user_fixable = isinstance(exc, (UploadError, ActionError, UserError))  # only these have words made for people
            if service_refused(exc) and job["kind"] in JOB_STAGE:
                # our AI account said no (key refused, no credit left): not their film's fault, and trying again
                # can't help until the owner fixes it. The film waits (nothing lost) instead of failing.
                self.db.run("UPDATE jobs SET status='waiting', error=? WHERE id=?", (str(exc)[:500], job["id"]))
                self.db.set_stage(project["id"], "paused", note=(
                    "Paused: our AI service is not answering right now. Nothing is lost: the owner has been told and "
                    "will start it again from this step, or press Try again in a while."))
                alert(self.db, self.s, "key", f"The AI account refused a request ({type(exc).__name__}: "
                      f"{str(exc)[:160]}): videos are paused until the key or credit is fixed in .env, the server's "
                      ".env.studio and the Modal secret; then retry them from /admin.")
                return
            if job["attempts"] < 2 and not user_fixable:
                self.db.requeue(job["id"], delay=20.0, error=str(exc)[:500])
                return
            self.db.job_done(job["id"], "failed", str(exc)[:500])
            if job["kind"] == "beat":
                # music is one choice inside a step, not the video: back to where they asked for it (setup or 'what
                # I understood'), told why, free to ask again, pick another track or upload a song. A 'failed' video
                # could only retry the same thing or go back to the start and lose what was agreed.
                params = job["params"] if isinstance(job["params"], dict) else json.loads(job["params"] or "{}")
                message = str(exc) if user_fixable else ("The music couldn't be made this time. Try again, describe "
                                                         "it differently, or upload a song.")
                self.db.set_stage(project["id"], params.get("back") or "setup", error=message[:300])
            elif job["kind"] in JOB_STAGE:
                message = str(exc) if user_fixable else "Something went wrong on our side. The owner has been told; you can try again."
                self.db.set_stage(project["id"], "failed", error=message[:300])
            if type(exc).__name__ == "AuthenticationError" or "invalid x-api-key" in str(exc):
                alert(self.db, self.s, "key", "The AI key was refused (401, invalid x-api-key): people's videos can't "
                      "use the AI until it is replaced in .env, the server's .env.studio and the Modal secret.")
            else:
                alert(self.db, self.s, "job", f"{job['kind']} failed for a video: {type(exc).__name__}: {str(exc)[:200]}")

    def _posters(self, project: dict) -> None:
        """Every video of this film has its still for the player (see pipeline.poster_for)."""
        from .pipeline import poster_for

        try:
            out = workdir(self.s, project["user_id"], project["id"]).out
            for video in sorted(out.glob("*.mp4")) if out.exists() else []:
                if not video.with_suffix(".jpg").exists():
                    poster_for(video)
        except Exception:  # noqa: BLE001 - a missing still never fails a step
            log.warning("could not make the stills for %s", project["id"], exc_info=True)

    def _stopped(self, job: dict) -> bool:
        """The person stopped this step (Studio.stop): it is put back at the stage the stop chose, even if the step
        moved the video on as it ended."""
        row = self.db.one("SELECT status, error FROM jobs WHERE id=?", (job["id"],))
        if not row or row["status"] != "cancelled":
            return False
        to = (row["error"] or "").rpartition("back to ")[2].strip()
        if job["project_id"] and to in STAGES:
            self.db.set_stage(job["project_id"], to)
        return True

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
        self._pay_for(project, check=True)
        self._enqueue(project, "prepare")
        self._pay_for(project)

    def _save_script(self, project: dict, script, kind: str) -> int:
        return self.db.run("INSERT INTO plans(project_id, kind, pitch_id, data, created) VALUES (?,?,?,?,?)",
                           (project["id"], kind, None, script.model_dump_json(), time.time()))

    def _script_for(self, pipe, project: dict, user: dict, speech: dict, previous=None, feedback: str = "",
                    look: Direction | None = None):
        from ..explainer.make import fit

        raw = pipe.brain.script_voice(speech["utterances"], speech["duration"], project["kind"], ask_text(project),
                                      rules_for(self.profile(user).rules, project["kind"]), previous, feedback,
                                      style=project["style"], look=look)
        update = {"style": project["style"]}
        if look is not None:
            update.update(look=f"{look.title}: {look.art_style}", palette=look.palette)
        script = fit(raw.model_copy(update=update), speech)
        if spell := self.spellings(project):  # the names as they corrected them, on screen and in the captions
            from ..explainer.script import respell_text

            script = respell_text(script, spell)
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
        looks = pipe.brain.directions(speech["utterances"], float(speech["duration"]), project["kind"], ask_text(project),
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
        if job["params"].get("first"):  # the first looks, from the brief they agreed
            self._make_looks(job, project, user, pipe, speech, 3, 1)
            return
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
            bed = self._ensure_track(job, project, user, self._pipeline(job, project, user), (lo, lo + 0.03))
            if bed is None:
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
            self.db.job_progress(job["id"], 0.6, "Putting what I heard into words")
            understood = pipe.brain.understand_talk(speech["utterances"], float(speech["duration"]), project["kind"],
                                                    ask_text(project), rules_for(self.profile(user).rules, project["kind"]))
            self.db.run("UPDATE projects SET understood=?, brief=NULL WHERE id=?",
                        (understood.model_dump_json(), project["id"]))
            self.db.set_stage(project["id"], "understood")
            return
        speech = make.load_speech(work.root)
        if step == "sample" and job["params"].get("first"):  # clean labels: they agreed the brief, so the script now
            if speech is None:
                raise ActionError("Start again: the recording is missing.")
            self.db.job_progress(job["id"], 0.05, "Working out what each part is about")
            script = self._script_for(pipe, project, user, speech)
            plan_id = self._save_script(project, script, "script")
            self._explainer_render(job, project, user, script, plan_id, sample=True,
                                   sample_start=make.busiest(script, float(speech["duration"])))
            return
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
        if self._ensure_track(job, project, user, pipe, (0.0, 0.06)) is not None:
            project = self.db.project(project["id"])
            pipe.no_music = False
        info = pipe.analyse(project["length"], project["pace"])
        old = json.loads(project["info"]) if project.get("info") else {}
        if old.get("fit_declined"):
            info["fit_declined"] = True
        fitted = self._fit_length(project, pipe, info)
        self.db.run("UPDATE projects SET info=? WHERE id=?", (json.dumps(info), project["id"]))
        if fitted:
            project = self.db.project(project["id"])
            self._pay_for(project)  # a shorter film uses fewer credits: the difference comes back
            if self._ensure_track(job, project, user, pipe, (0.8, 0.84)) is not None:  # music made for the old length
                project = self.db.project(project["id"])
        if info["skipped"]:
            self.db.event(project["id"], f"{len(info['skipped'])} file(s) couldn't be read and were left out.", "warn")
        self.db.job_progress(job["id"], 0.85, "Putting what I saw into words")
        ctx = self._context(pipe, project, user)
        understood = pipe.brain.understand(ctx)
        self.db.run("UPDATE projects SET understood=?, brief=NULL WHERE id=?", (understood.model_dump_json(), project["id"]))
        self.db.set_stage(project["id"], "understood")

    FIT_SLACK = 1.15  # a film up to this much longer than its footage carries is left alone

    def _fit_length(self, project: dict, pipe, info: dict) -> bool:
        """A film much longer than its footage would have to show the same shots again and again (and stretch weak
        moments to fill the time): make it as long as the footage carries, and say so. Not when they said repeats are
        fine, or set the length back up after being told."""
        from .brain.context import REUSE_WORDS

        if project["kind"] != "montage" or info.get("fit_declined") or REUSE_WORDS.search(project.get("intent") or ""):
            return False
        carries = pipe.supported_seconds()
        length = float(project["length"])
        target = max(15.0, 5 * (carries // 5))
        if length <= max(target * self.FIT_SLACK, target + 5):
            return False
        info["fit"] = {"from": length, "to": target, "carries": round(carries, 1)}
        self.db.run("UPDATE projects SET length=?, updated=? WHERE id=?", (target, time.time(), project["id"]))
        raw = sum(float(c["seconds"] or 0) for c in self.db.all("SELECT seconds FROM clips WHERE project_id=?", (project["id"],)))
        self.db.event(project["id"], f"Your footage is {raw:.0f} seconds, with about {carries:.0f} seconds of strong moments, so "
                      f"I made the film {target:g} seconds instead of {length:g}: at {length:g} it would show the same shots "
                      "again and again. Want it longer anyway? Go back to the clips and settings and pick the length.", "warn")
        return True

    def understanding(self, project: dict) -> dict | None:
        return json.loads(project["understood"]) if project.get("understood") else None

    @staticmethod
    def spellings(project: dict) -> list[tuple[str, str]]:
        """The names they corrected on 'what I understood' (heard -> written), for every text and caption."""
        brief = json.loads(project["brief"]) if project.get("brief") else {}
        return [(t["heard"], t["written"]) for t in brief.get("terms") or [] if t["heard"] != t["written"]]

    @staticmethod
    def _parts_from(text: str) -> list[dict]:
        """'0:12 Why it works' per line (the time is optional) -> [{"at": 12.0, "what": "Why it works"}]."""
        import re

        out = []
        for line in str(text or "").splitlines()[:12]:
            line = line.strip(" -•\t")
            if not line:
                continue
            m = re.match(r"^(?:(\d{1,2}):(\d{2})|(\d+(?:\.\d+)?)\s*s)\b\s*[-–—:·.]?\s*(.*)$", line)
            at = None
            if m and m.group(4).strip():
                at = float(m.group(3)) if m.group(3) else int(m.group(1)) * 60 + int(m.group(2))
                line = m.group(4).strip()
            out.append({"at": at, "what": line[:160]})
        return out

    def polish_left(self, project: dict) -> int:
        u = self.understanding(project) or {}
        user = self.user(project["user_id"])
        cap = 10 if user and self.is_owner(user) else MAX_POLISH
        return max(0, cap - int(u.get("_polished") or 0))

    @staticmethod
    def _their_words(u: dict, brief: dict) -> str:
        """What they typed or changed on 'what I understood': their answers, and every line that isn't the AI's own
        wording (which only repeats what they asked for, already checked when the film was made)."""
        norm = lambda x: " ".join(str(x).split()).lower()  # noqa: E731
        said = [x["answer"] for x in brief.get("answers") or []]
        if norm(brief.get("story", "")) != norm(u.get("story", "")):
            said.append(brief["story"])
        for key in ("must", "avoid"):
            before = {norm(x) for x in u.get(key) or []}
            said += [x for x in brief.get(key) or [] if norm(x) not in before]
        if brief.get("music") and norm(brief["music"]) != norm(u.get("music", "")):
            said.append(brief["music"])
        return " ".join(said)

    def polish_understanding(self, project: dict, form: dict) -> None:
        """The AI rewords what they wrote on 'what I understood' (clearer, nothing added or lost). The version before
        is kept, so they can go back to it."""
        self._guard(project, "understood")
        u = self.understanding(project)
        if not u:
            raise ActionError("Nothing to polish yet.")
        if self.polish_left(project) <= 0:
            raise ActionError("That's all the polishing for this film: edit the words yourself, or carry on.")

        def lines(name: str) -> list[str]:
            return [x.strip(" -•\t")[:200] for x in str(form.get(name, "")).splitlines() if x.strip(" -•\t")][:12]

        current = {"story": " ".join(str(form.get("story") or u.get("story", "")).split())[:600],
                   "feel": [w.strip() for w in str(form.get("feel", "")).split(",") if w.strip()][:6] or u.get("feel", []),
                   "must": lines("must") if "must" in form else u.get("must", []),
                   "avoid": lines("avoid") if "avoid" in form else u.get("avoid", [])}
        self.mute_if_asked(project, self._their_words(u, current))  # what they typed counts, polished or not
        user = self.user(project["user_id"])
        owner = self.is_owner(user)
        work = workdir(self.s, user["id"], project["id"]).ensure()
        brain = make_brain(brain_kind_for(self.s, owner), self.s, self._spender(project, user["id"], None), work.brain)
        try:
            out = brain.polish_understanding(current, project["kind"])
        except BudgetExceeded as exc:
            raise ActionError(f"It can't be polished right now ({exc}). Edit the words yourself, or carry on.") from exc
        except Exception as exc:  # noqa: BLE001 - their words stay as they were
            log.warning("polishing failed: %s", exc)
            raise ActionError("The AI couldn't polish it just now; your words are as you left them. Try again.") from exc
        history = (u.get("_history") or [])[-4:] + [current]
        u.update(story=out["story"] or current["story"], feel=out["feel"] or current["feel"],
                 must=out["must"] or current["must"], avoid=out["avoid"] or current["avoid"],
                 _history=history, _polished=int(u.get("_polished") or 0) + 1)
        self.db.run("UPDATE projects SET understood=?, updated=? WHERE id=?", (json.dumps(u), time.time(), project["id"]))

    def undo_understanding(self, project: dict) -> None:
        """Back to the words as they were before the last polish."""
        self._guard(project, "understood")
        u = self.understanding(project) or {}
        history = u.get("_history") or []
        if not history:
            raise ActionError("There's no earlier version to go back to.")
        last = history.pop()
        u.update({k: last.get(k, u.get(k)) for k in ("story", "feel", "must", "avoid")}, _history=history)
        self.db.run("UPDATE projects SET understood=?, updated=? WHERE id=?", (json.dumps(u), time.time(), project["id"]))

    def confirm_understanding(self, project: dict, form: dict) -> None:
        """They checked (and corrected) what was understood: that becomes the brief every later step follows, and the
        stories are pitched from it."""
        self._guard(project, "understood")
        u = self.understanding(project) or {}

        def lines(name: str) -> list[str]:
            return [x.strip(" -•\t")[:200] for x in str(form.get(name, "")).splitlines() if x.strip(" -•\t")][:12]

        answers = []
        for k, q in enumerate(u.get("questions") or []):
            a = " ".join(str(form.get(f"q{k}_other") or form.get(f"q{k}") or "").split())[:200]
            if a:
                answers.append({"ask": q["ask"], "answer": a})
        brief = {
            "story": " ".join(str(form.get("story", "")).split())[:600] or u.get("story", ""),
            "feel": [w.strip() for w in str(form.get("feel", "")).split(",") if w.strip()][:6],
            "must": lines("must"), "avoid": lines("avoid"), "answers": answers,
        }
        if project["kind"] == "montage":
            known = {c["short"] for c in u.get("clips") or []}
            brief["music"] = " ".join(str(form.get("music", "")).split())[:300]
            brief["leave_out"] = [c for c in form.get("leave_out", []) if c in known] \
                if isinstance(form.get("leave_out"), list) else []
        else:  # a recording: its parts as they see them, and the names spelt the way they say them
            brief["parts"] = self._parts_from(form.get("parts", "")) if "parts" in form else [
                {"at": x["at"], "what": x["what"]} for x in u.get("parts") or []]
            terms = []
            for k, t in enumerate(u.get("terms") or []):
                written = " ".join(str(form.get(f"term{k}", t["written"]) or "").split())[:80] or t["heard"]
                terms.append({"heard": t["heard"], "written": written})
            for line in str(form.get("terms_more", "")).splitlines()[:12]:
                import re

                pair = [x.strip() for x in re.split(r"\s*(?:=|->|→)\s*", line.strip(), maxsplit=1)]
                if pair and pair[0]:
                    heard, written = (pair + [pair[0]])[:2] if len(pair) == 1 else pair[:2]
                    terms.append({"heard": heard[:80], "written": (written or heard)[:80]})
            brief["terms"] = terms[:24]
        self.db.run("UPDATE projects SET brief=?, updated=? WHERE id=?", (json.dumps(brief), time.time(), project["id"]))
        self.mute_if_asked(project, self._their_words(u, brief))
        from . import abilities

        abilities.note(self.db, project, " ".join([brief["story"], *brief["must"], brief.get("music", "")]))
        self.db.run("DELETE FROM pitches WHERE project_id=?", (project["id"],))
        self.db.run("UPDATE projects SET chosen=NULL, pitch_round=0 WHERE id=?", (project["id"],))
        if project["kind"] == "montage" or uses_looks(project):  # stories, or looks to pick from
            self._enqueue(self.db.project(project["id"]), "pitch", {"first": True})
        else:  # clean labels: the script and its sample come next
            self._enqueue(self.db.project(project["id"]), "sample", {"first": True})

    def _job_pitch(self, job, project, user):
        if project["kind"] != "montage":
            return self._job_looks(job, project, user)
        pipe = self._pipeline(job, project, user)
        if job["params"].get("improvise") is not None:  # 'Improvise one': this idea at the strength they set
            project = {**project, "improvise": int(job["params"]["improvise"])}
        ctx = self._context(pipe, project, user)
        if job["params"].get("first"):  # the first ideas, from the brief they agreed
            self.db.job_progress(job["id"], 0.1, "Thinking up story ideas from what we agreed")
            self._save_pitches(project, pipe.pitch(ctx, n=3, tag="r1"), 1)
            self.db.set_stage(project["id"], "choose")
            return
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
        from . import board
        from .brain.core import check_plan

        pipe = self._pipeline(job, project, user)
        ctx = self._context(pipe, project, user)
        pid, pitch = self._chosen(project, job["params"].get("pitch"))
        note = job["params"].get("note", "")
        edits = job["params"].get("edits")
        changes: list[str] = []
        row = self.latest_plan(project) if edits else None
        if edits and row is not None:  # their storyboard changes: by code, and the AI only for the shots with a note
            plan, notes = board.apply_edits(DesignPlan.model_validate_json(row["data"]), ctx, edits)
            changes = edit_lines(edits)
            problems = check_plan(plan, ctx, taste=False)  # their choices win over the rules of taste
            if problems:
                self.db.event(project["id"], "storyboard: their changes left " + "; ".join(problems)[:500], "info", job["id"])
            if notes or problems:
                self.db.job_progress(job["id"], 0.1, "Changing the shots you marked")
                plan = pipe.revise(ctx, pitch, plan, "\n".join(
                    ["The person changed the storyboard themselves; keep their changes."] + notes
                    + [f"Fix only this, which their changes left: {p}" for p in problems]
                    + ["Keep every other shot exactly as it is."]))
                changes += [f"Shot change you asked for: {n.split(': ', 1)[-1]}" for n in notes]
        else:
            plan = pipe.design(ctx, pitch, note)
        plan_id = self.db.run("INSERT INTO plans(project_id, kind, pitch_id, data, created) VALUES (?,?,?,?,?)",
                              (project["id"], "board" if edits else "design", pid, plan.model_dump_json(), time.time()))
        if note:  # shown with the new sample, so a change they asked for can be checked at once
            changes = [f"You said: {note}", *[x.strip() for x in plan.notes.split(";") if x.strip()][:4]]
        if changes:
            info = json.loads(project["info"] or "{}")
            info["changes"] = changes
            self.db.run("UPDATE projects SET info=? WHERE id=?", (json.dumps(info), project["id"]))
        shape = self.shapes(project)[0]
        self.db.job_progress(job["id"], 0.2, "Drawing the storyboard and a rough cut of the whole film")
        try:
            sb = board.build(pipe, ctx, plan, shape, f"p{plan_id}")
            self.db.run("UPDATE plans SET board=? WHERE id=?", (json.dumps(sb), plan_id))
        except Exception as exc:  # noqa: BLE001 - the storyboard helps; the sample still comes
            self.db.event(project["id"], f"storyboard skipped: {type(exc).__name__}: {exc}"[:300], "warn", job["id"])
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
        """Seconds since a remote worker (either lane) last checked in, or None if none ever has."""
        at = self.db.value("SELECT MAX(at) FROM heartbeat WHERE name IN ('renderer', 'renderer-quick') OR name LIKE "
                           "'modal-%'", (), None)
        return round(time.time() - at, 1) if at else None

    def queued_note(self, job: dict | None) -> str:
        """What a waiting video says while remote rendering is on and no renderer is online."""
        if not job or job["status"] != "queued":
            return ""
        if self.paused():
            return "Flicello is paused for a moment. Your video carries on by itself when it's back; you can leave this page."
        if self.s.jobs != "remote":
            return ""
        from . import cloud

        if cloud.can_take(self, job) and (self.s.modal_mode == "always" or not cloud.pc_online(self.db)):
            return "Starting a cloud renderer for it: it begins in about a minute. You can leave this page."
        seen = self.renderer_seen()
        if seen is None or seen >= 90:
            return "Waiting for the video renderer to come online. Your video will start by itself; you can leave this page."
        running, queued = self.ahead(job)
        n = len(running) + len(queued)
        if not n:
            return ""
        return (f"Waiting for its turn: {n} video{'s are' if n > 1 else ' is'} being made before yours. "
                f"Yours starts by itself right after.")

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
            "cap_usd": self.money()["monthly_usd"],
            "paused": self.paused(),
            "cloud": cloud_status(self),
        }


def cloud_status(studio) -> dict:
    from .cloud import status

    return status(studio)


def edit_lines(edits: dict) -> list[str]:
    """What they changed on the storyboard, in their words (shown with the new sample)."""
    lines = [f"Dropped shot {int(k) + 1}" for k in sorted(edits.get("drop") or [])]
    lines += [f"Moved shot {int(k) + 1} {'later' if int(v) > 0 else 'earlier'}" for k, v in (edits.get("move") or {}).items()]
    lines += [f"Shot {int(k) + 1} now uses {v}" for k, v in (edits.get("swap") or {}).items()]
    return lines


def stage_label(stage: str) -> str:
    return STAGES.get(stage, stage)


def uses_looks(project: dict) -> bool:
    """Voice videos and designed talking videos are pitched as looks, painted by an image model."""
    return project["kind"] == "voice" or (project["kind"] == "talk" and project.get("style") == "designed")


def usage_dict(u: Usage) -> dict:
    return asdict(u)
