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
import re
import logging
import secrets
import time
import traceback
from dataclasses import asdict
from pathlib import Path

from .alerts import alert
from .brain import BrainError, BudgetExceeded, PersonLimit, Usage, brain_kind_for, make_brain, make_watcher
from .brain.context import look_from_note, pace_from_note
from .brain.schemas import ChangeOp, DesignPlan, Direction, StoryBeat, StoryPitch
from .db import DB, JobStopped
from .improve import maybe_start as maybe_start_improve
from . import agent, requirements
from . import spec as specs
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
    "reading": "Reading your changes",
    "confirm": "Check the changes",
    "composing": "Making your music",
    "paused": "Paused",
    "failed": "Something went wrong",
}
BUSY = {"preparing", "pitching", "sampling", "rendering", "revising", "composing", "reading"}
log = logging.getLogger("vidosuper.studio.service")
JOB_STAGE = {"prepare": "preparing", "pitch": "pitching", "sample": "sampling", "render": "rendering",
             "revise": "revising", "beat": "composing", "changes": "reading"}
PRIORITY = {"changes": 1, "draft": 1, "export": 1, "generate": 2, "effect": 2, "music_edit": 2, "strip": 2, "beat": 2, "copies": 2, "pitch": 2, "sample": 3, "learn": 4,
            "prepare": 5, "render": 6, "revise": 6}  # short jobs first
FEEDBACK_TAGS = ["too fast", "too slow", "boring start", "wrong music", "bad shots", "shaky", "too short",
                 "too long", "didn't follow my idea", "colour", "sound"]
LENGTHS = (15, 30, 45, 60, 90, 120, 180, 300, 600)  # who may ask for which: Studio.lengths_for (access levels)
MAX_SAMPLE_TRIES = 10  # a quiet safety net only
MAX_TRACKS = 5  # tracks made for one film, for everyone: after that they pick one of them or upload their own song
MAX_IDEAS = 9  # story ideas for one film, the first three included (painted looks: 12)
MAX_POLISH = 10  # times the AI may reword 'what I understood' for one film


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
    items = specs.film_spec(project, [])  # their changes by talking to their editor (spec.py)
    off = {it["id"] for it in items if not it["on"]}
    if project.get("brief"):
        b = json.loads(project["brief"])
        must = [x for k, x in enumerate(b.get("must") or []) if f"must-{k}" not in off]
        avoid = [x for k, x in enumerate(b.get("avoid") or []) if f"never-{k}" not in off]
        out = [c for c in b.get("leave_out") or [] if f"out-{c}" not in off]
        agreed = [f"The film: {b['story']}"] if b.get("story") and "goal" not in off else []
        agreed += [f"Feel: {', '.join(b['feel'])}"] if b.get("feel") and "feel" not in off else []
        agreed += [f"Must have: {'; '.join(must)}"] if must else []
        agreed += [f"Leave out: {'; '.join(avoid)}"] if avoid else []
        agreed += [f"Do not use these clips at all: {', '.join(out)}"] if out else []
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
    since = specs.ask_lines(items)
    if since:
        parts.append("# Agreed with them since, talking to their editor (this wins over everything above)\n"
                     + "\n".join(f"- {line}" for line in since))
    return "\n".join(p for p in parts if p)


KIND_WORDS = [  # 'something else': which engine can make it (the person sees the reason and can change it)
    ("voice", r"\b(voice ?(memo|note|over)|podcast|audio|narrat\w*|my voice|recording|no camera|animat\w* (of|from) my)"),
    ("talk", r"\b(me talking|talking head|to camera|vlog|explain\w*|tutorial|lesson|interview|presentation|speech|pitch)"),
    ("cartoon", r"\b(cartoons?|anime|animated (short|film|story|movie)|an animation)\b"),
]


def route_kind(describe: str) -> tuple[str, str]:
    import re

    for kind, pattern in KIND_WORDS:
        if re.search(pattern, describe, re.IGNORECASE):
            return kind, {"voice": "It sounds like it's built on a recording of a voice, so it's made as an "
                                   "animation of what is said.",
                          "talk": "It sounds like someone talking to camera, so their video stays and graphics "
                                  "land on their words.",
                          "cartoon": "It sounds like a story to animate, so it's made as a cartoon: Claude directs "
                                     "it and an animation engine makes every shot."}[kind]
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


def graphics_notes(edl_path: Path) -> list[str]:
    """What they should know about the graphics they asked for: captions with nobody heard have nothing to show."""
    from ..schemas import EDL

    edl = EDL.model_validate_json(edl_path.read_text(encoding="utf-8"))
    captions = [g for g in edl.motion if g.kind == "captions"]
    if captions and not any(g.words for g in captions):
        return ["Nobody is heard in this film (its shots play the music, not their own sound), so the captions have "
                "nothing to show"]
    return []


class Studio:
    def __init__(self, s: StudioSettings):
        self.s = s.ensure()
        self.db = DB(s.db_path)
        self.cfg = studio_config(s.workspace, s.render_preset, s.models_dir)
        from concurrent.futures import ThreadPoolExecutor

        self._editors = ThreadPoolExecutor(max_workers=4, thread_name_prefix="editor")  # started on first use
        self.soon = lambda fn: self._editors.submit(fn)  # your editor's turns (tests run them at once)
        from ..work.sessions import ensure as work_table

        work_table(self.db)  # Cello's hands' table is there before anything asks; their sessions start on first use
        self._work = None

    @property
    def work(self):
        """Cello's hands: the Claude Code work sessions on this studio's films (work/sessions.py), made on first use."""
        if self._work is None:
            from ..work.sessions import Sessions

            self._work = Sessions(self)
        return self._work

    # ------------------------------------------------------------------ people
    def user(self, uid: str) -> dict | None:
        return self.db.one("SELECT * FROM users WHERE id=?", (uid,))

    @staticmethod
    def is_owner(user: dict) -> bool:
        return user["role"] == "owner"

    def set_brand(self, user: dict, name: str = "", color: str = "", font: str = "", logo: bytes | None = None,
                  drop_logo: bool = False) -> dict:
        """Their brand kit, kept with their style: a name, a colour every graphic takes, a type, and a logo shown in a
        corner of their films (a picture, made a small PNG and kept inside their profile)."""
        import base64
        import io
        import re as _re

        from PIL import Image

        prof = self.profile(user)
        brand = dict(prof.answers.get("brand") or {})
        brand["name"] = " ".join(str(name).split())[:40]
        if color and not _re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ActionError("A colour like #ff5a36.")
        brand["color"] = color.lower()
        brand["font"] = font if font in ("display", "serif", "mono") else "display"
        if drop_logo:
            brand.pop("logo", None)
        elif logo:
            try:
                img = Image.open(io.BytesIO(logo))
                img.load()
            except Exception as exc:  # noqa: BLE001 - not a picture
                raise ActionError("The logo must be a picture (PNG, JPG or WebP).") from exc
            img = img.convert("RGBA")
            img.thumbnail((512, 512))
            buf = io.BytesIO()
            img.save(buf, "PNG", optimize=True)
            if buf.tell() > 400_000:
                raise ActionError("That logo is too detailed: try a simpler PNG.")
            brand["logo"] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        prof.answers["brand"] = brand
        prof.save()
        return brand

    def profile(self, user: dict) -> Profile:
        p = Profile.load(user_dir(self.s, user["id"]) / "profile.json")
        if self.is_owner(user) and not p.rules and not p.history:
            p.rules = owner_seed_rules()
            p.history.append({"at": time.strftime("%Y-%m-%d"), "summary": "Started from your known rules.",
                              "added": [r["id"] for r in p.rules], "dropped": []})
            p.save()
        return p

    # ------------------------------------------------------------------ what a film may be: on their own machine, anything
    def tier(self, user: dict):
        """What a film may be: films as long as the studio makes them, recordings up to the project limit."""
        from .settings import Tier

        return Tier(videos=None, max_seconds=max(LENGTHS), max_recording=int(self.s.limits.project_seconds), beats=99)

    def lengths_for(self, user: dict) -> list[int]:
        return [n for n in LENGTHS if n <= self.tier(user).max_seconds]

    def _config(self, key: str, default):
        row = self.db.one("SELECT value FROM config WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def _set_config(self, key: str, value) -> None:
        self.db.run("INSERT INTO config(key, value, at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                    "value=excluded.value, at=excluded.at", (key, json.dumps(value), time.time()))

    # ------------------------------------------------------------------ money protection: caps and the off switch
    # Their own keys pay for Gemini, ElevenLabs (and the Anthropic API when it is their brain): the caps keep a loop
    # or a mistake from spending more than they meant (studio.yaml: limits.monthly_usd, video_usd, person_day_usd).
    def money(self) -> dict:
        """The spending caps in force: the settings' defaults, changed with set_money."""
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
        """The off switch: while on, no new step starts (running ones finish), and they are told why."""
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
        describe = " ".join((describe or "").split())[:600]
        if kind == "other":  # their own words: the engine that can make it, and why (shown, and changeable); no
            if len(describe) < 3:  # words at all is fine too: a film from their clips, its story found in them
                kind = "montage"
            else:
                kind, routed = route_kind(describe)
        if describe:  # what they wrote at the top is what the film is about, whichever kind they picked
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
        try:
            agent.hello(self, project, user)
        except Exception:  # noqa: BLE001 - the film is made; its editor's first words can wait for the first step
            log.warning("could not post the editor's hello for %s", pid, exc_info=True)
        return project

    def rename(self, project: dict, title: str) -> str:
        """A film's name, at any time (Cello names it once it knows what it is)."""
        title = " ".join((title or "").split())[:60]
        if not title:
            raise ActionError("Say what to call it.")
        self.db.run("UPDATE projects SET title=?, updated=? WHERE id=?", (title, time.time(), project["id"]))
        return title

    def shapes(self, project: dict) -> list[str]:
        return ["9:16", "16:9"] if project["shapes"] == "both" else [project["shapes"]]

    def delete_project(self, project: dict) -> None:
        for job in self.db.all("SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','waiting')", (project["id"],)):
            self.db.job_done(job["id"], "cancelled")
        if self.db.one("SELECT id FROM jobs WHERE project_id=? AND status='running'", (project["id"],)):
            raise ActionError("Wait a moment: this video is being worked on. Try again when the step finishes.")
        remove_project_files(workdir(self.s, project["user_id"], project["id"]), self.cfg)
        self.db.run("UPDATE projects SET deleted=1, updated=? WHERE id=?", (time.time(), project["id"]))
        self.db.run("DELETE FROM messages WHERE project_id=?", (project["id"],))
        self.db.run("DELETE FROM review_notes WHERE project_id=?", (project["id"],))
        self.db.run("DELETE FROM review_links WHERE project_id=?", (project["id"],))
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
            for table in ("pitches", "plans", "renders", "feedback", "events", "uploads", "clips", "jobs", "beats",
                          "messages"):
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
                "conversation": [{"from": m["role"], "at": m["created"], "text": m["body"].get("text", "")}
                                 for m in agent.thread(self.db, p["id"], n=2000)],
                "spec_changes": json.loads(p["spec"]) if p.get("spec") else None,
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
            raise ActionError({"confirm": "Changes are waiting for an OK on screen first: make them or drop them.",
                               "failed": "The last step didn't work: try it again first.",
                               "paused": "The film is paused: carry it on first."}.get(project["stage"],
                                                                                      "That can't be done at this step."))

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
        """The jobs a queued job waits for: those running and those queued before it."""
        rows = self.db.all("SELECT * FROM jobs WHERE status IN ('queued','running') AND id != ? AND kind != 'learn'",
                           (job["id"],))
        running = [r for r in rows if r["status"] == "running"]
        queued = [r for r in rows if r["status"] == "queued" and (r["priority"], r["id"]) < (job["priority"], job["id"])]
        return running, queued

    def eta(self, job: dict | None) -> int | None:
        """Seconds left: for a running job, what it should take corrected by how far it has got; for a queued one,
        also what is left of every job ahead of it (it said "about 10 min" while an hour-long film ran ahead)."""
        if not job:
            return None
        typical = self.expected_seconds(job)
        if job["status"] == "queued" and job.get("id"):  # a real one in the queue
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
        """Tracks can be made: by the open music model on this machine's GPU, or with an ElevenLabs key."""
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
        from ..music.compose import maker as music_maker
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
                raise ComposeError("nothing here can make music (no GPU for the music model, no ElevenLabs key)")
            if music_maker() == "elevenlabs" and not self._music_budget_ok(project):
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
                song_id, maker = made_now["song_id"], made_now.get("maker", "elevenlabs")
                if maker == "elevenlabs":  # made on this machine: nothing to pay
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
                           idea.model_dump_json(), json.dumps(plan if maker != "house" else {}), name, song_id,
                           seconds, 1 if whole else 0, facts.strip(), maker))
        return self.beat_row(project, rid)

    def _full_from_taste(self, job: dict, project: dict, user: dict, row: dict,
                         span: tuple[float, float] = (0.0, 1.0)) -> dict:
        """The full track of a long film, made once they picked its taste: the taste is its reference, so it has
        the same sound. If the taste can no longer be referred to, the same description without it; if the music
        service is down, our own simple beat (and they are told)."""
        from ..music import made
        from ..music.compose import ComposeError, compose_plan
        from ..music.compose import maker as music_maker
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
            if music_maker() == "elevenlabs" and not self._music_budget_ok(project):
                raise ComposeError("the music budget is used up")
            t0 = time.time()
            sections = made.layout(seconds, sound.bpm, purpose)
            taste = work.beats / row["file"]  # on this machine the taste itself is the reference it sounds like
            try:
                done = compose_plan(made.plan(sections, sound, purpose, reference=ref if row["song_id"] else None),
                                    work.beats / name, store=True,  # kept there: a stretch of it can be made again
                                    reference_audio=taste if taste.exists() else None)
            except ComposeError:
                if not row["song_id"]:
                    raise
                done = compose_plan(made.plan(sections, sound, purpose), work.beats / name, store=True)
                note = " (made from the same description: the taste itself could no longer be used as a reference)"
            if done.get("maker", "elevenlabs") == "elevenlabs":
                self._spend_music(project, user, job, done["seconds"], time.time() - t0)
        except ComposeError as exc:
            self.db.event(project["id"], f"music: the full track failed ({exc}); our own beat instead", "warn", job["id"])
            name = Path(row["file"]).stem + "-full.wav"
            house_beat(work.beats / name, seconds, "calm" if purpose == "bed" else "balanced")
            note = " The music service couldn't make the full track, so the film uses our own simple beat."
        self.db.run("UPDATE beats SET full_file=?, facts=?, seconds=? WHERE id=?",  # seconds: the length it's made for
                    (name, (self._beat_facts(work.beats / name, purpose) + note).strip(), seconds, row["id"]))
        if not note and "done" in locals() and done.get("song_id"):
            self.db.run("UPDATE beats SET full_song_id=? WHERE id=?", (done["song_id"], row["id"]))
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
        return MAX_IDEAS + 3 if uses_looks(project) else MAX_IDEAS

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
    STOP_TO = {"prepare": "setup", "pitch": "choose", "sample": "choose", "render": "review", "revise": "done",
               "changes": "done", "strip": "done"}

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
            self.ask_change(user, self.db.project(project["id"]), fix + (f" (They also ticked: {', '.join(tags)}.)"
                                                                         if tags else ""))

    # ------------------------------------------------------------------ versions: changes to exactly the shots named
    def _info(self, project: dict) -> dict:
        row = self.db.one("SELECT info FROM projects WHERE id=?", (project["id"],))
        return json.loads(row["info"]) if row and row["info"] else {}

    def _set_info(self, project: dict, **values) -> None:
        info = self._info(project)
        for k, v in values.items():
            if v is None:
                info.pop(k, None)
            else:
                info[k] = v
        self.db.run("UPDATE projects SET info=? WHERE id=?", (json.dumps(info), project["id"]))

    def work_version(self, project: dict) -> int:
        """The version they are working on: the one they chose to continue from, else the newest."""
        base = project.get("base")
        if base and self.db.one("SELECT id FROM renders WHERE project_id=? AND kind='final' AND version=?",
                                (project["id"], base)):
            return int(base)
        return int(project["version"] or 0)

    def strip_of(self, project: dict, version: int) -> dict | None:
        """A version's numbered shots ({"cards", "alternatives"}), once made (see versions.strip)."""
        row = self.db.one("SELECT shots FROM renders WHERE project_id=? AND kind='final' AND version=? AND shots IS NOT "
                          "NULL ORDER BY id LIMIT 1", (project["id"], version))
        return json.loads(row["shots"]) if row else None

    def versions_list(self, project: dict) -> list[dict]:
        """Every finished version, newest first: its files, how they rated it, what it was made from, what changed."""
        rated = {r["version"]: r for r in self.db.all("SELECT version, match, liked FROM feedback WHERE project_id=? "
                                                       "AND stage='final' ORDER BY id", (project["id"],))}
        out: dict[int, dict] = {}
        for r in self.db.all("SELECT * FROM renders WHERE project_id=? AND kind='final' ORDER BY version DESC, shape",
                             (project["id"],)):
            v = out.setdefault(r["version"], {"version": r["version"], "files": [], "base": r["base"],
                                              "changes": json.loads(r["changes"]) if r["changes"] else [],
                                              "liked": rated.get(r["version"], {}).get("liked"), "strip": bool(r["shots"])})
            v["files"].append({"shape": r["shape"], "file": r["file"], "seconds": r["seconds"]})
        return list(out.values())

    def _check_revisions(self, user: dict, project: dict) -> None:
        if project["revisions"] >= self.s.limits.revisions_per_video and not self.is_owner(user):
            raise ActionError("That was the last round of changes for this video.")

    def ask_change(self, user: dict, project: dict, text: str) -> None:
        """What they want changed, in their words: read into exact changes on the numbered shots, shown to them
        before anything is made (see _job_changes)."""
        self._guard(project, "done", "confirm")
        text = " ".join(str(text).split())[:1500]
        if not text:
            raise ActionError("Say what should change.")
        self._check_revisions(user, project)
        version = self.work_version(project)
        self.db.run("INSERT INTO feedback(project_id, user_id, stage, version, tags, text, created) "
                    "VALUES (?,?,?,?,?,?,?)", (project["id"], user["id"], "change", version, "[]", text, time.time()))
        self.mute_if_asked(project, text)
        if project["kind"] != "montage":  # a voice or talking video has scenes, not numbered shots: changed as before
            self.db.run("UPDATE projects SET revisions=revisions+1 WHERE id=?", (project["id"],))
            self._enqueue(project, "revise", {"feedback": text, "tags": []})
            return
        self._set_info(project, pending=None)
        self._enqueue(project, "changes", {"text": text, "base": version})

    def confirm_change(self, user: dict, project: dict, picks: list[int] | None = None) -> None:
        """Make the changes they checked (`picks`: the ones they kept ticked; None: all)."""
        self._guard(project, "confirm")
        pending = self._info(project).get("pending") or {}
        ops, said = list(pending.get("ops") or []), list(pending.get("said") or [])
        if picks is not None:
            keep = sorted({k for k in picks if 0 <= k < len(ops)})
            ops, said = [ops[k] for k in keep], [said[k] for k in keep if k < len(said)]
        if not ops:
            raise ActionError("Tick at least one change, or say what should change.")
        self._check_revisions(user, project)
        self.db.run("UPDATE projects SET revisions=revisions+1 WHERE id=?", (project["id"],))
        self._set_info(project, pending=None)
        self._enqueue(project, "revise", {"ops": ops, "base": pending.get("base") or self.work_version(project),
                                          "said": said, "text": pending.get("text", "")})

    def cancel_change(self, project: dict) -> None:
        self._guard(project, "confirm")
        self._set_info(project, pending=None)
        self.db.set_stage(project["id"], "done")

    def direct_change(self, user: dict, project: dict, form: dict) -> None:
        """Changes picked on the shot strip (remove, longer, swap, replace...): exact, so no reading by the AI."""
        from . import versions

        self._guard(project, "done")
        version = self.work_version(project)
        strip = self.strip_of(project, version)
        if not strip or not strip.get("cards"):
            raise ActionError("This version's shots aren't numbered yet: give it a few seconds.")
        cards = strip["cards"]
        ops, said = versions.direct_ops(form, len(cards), {c["n"]: c["what"] for c in cards})
        if not ops:
            raise ActionError("Pick a change on a shot first.")
        self._check_revisions(user, project)
        self.db.run("UPDATE projects SET revisions=revisions+1 WHERE id=?", (project["id"],))
        self._enqueue(project, "revise", {"ops": [o.model_dump() for o in ops], "base": version, "said": said,
                                          "text": ""})

    def continue_from(self, project: dict, version: int) -> None:
        """Work on an earlier version: the next change starts from it (nothing is deleted)."""
        self._guard(project, "done")
        if not self.db.one("SELECT id FROM renders WHERE project_id=? AND kind='final' AND version=?",
                           (project["id"], version)):
            raise ActionError(f"There is no version {version}.")
        self.db.run("UPDATE projects SET base=? WHERE id=?",
                    (None if version == project["version"] else version, project["id"]))
        if not self.strip_of(project, version):
            self.number_shots(project, version)

    def number_shots(self, project: dict, version: int | None = None) -> bool:
        """Make a version's numbered shots if it has none yet (a few seconds, no AI). False: already there or asked."""
        version = version or self.work_version(project)
        if not version or self.strip_of(project, version) or self.db.active_job(project["id"]):
            return False
        asked = self._info(project).get("strips_asked") or []
        if version in asked:
            return False
        self._set_info(project, strips_asked=(asked + [version])[-40:])
        self.db.enqueue("strip", project["id"], project["user_id"], {"version": version}, PRIORITY["strip"])
        return True

    def _version_plan(self, project: dict, version: int) -> tuple[dict, DesignPlan, dict | None]:
        """A version's plan row, the plan pinned to the frames it really showed, and its edit list."""
        from . import versions

        r = self.db.one("SELECT plan_id FROM renders WHERE project_id=? AND kind='final' AND version=? ORDER BY id LIMIT 1",
                        (project["id"], version))
        row = self.db.one("SELECT * FROM plans WHERE id=? AND project_id=?", (r["plan_id"], project["id"])) if r else None
        if row is None:
            raise ActionError(f"Version {version} can't be found.")
        plan = DesignPlan.model_validate_json(row["data"])
        work = workdir(self.s, project["user_id"], project["id"])
        edl, tall = versions.load_edl(work.out, version, self.shapes(project))
        return row, versions.pinned(plan, edl, tall), edl

    def _save_strip(self, pipe: Pipeline, ctx, project: dict, version: int) -> dict | None:
        """Number a version's shots, with a frame each and the moments it doesn't show (to swap in)."""
        from . import versions

        _, plan, edl = self._version_plan(project, version)
        if edl is None:
            return None
        shape = "9:16" if edl["meta"]["height"] > edl["meta"]["width"] else "16:9"
        sources = versions.sources_for(pipe, ctx.facts)
        pipe.work.stills.mkdir(parents=True, exist_ok=True)
        cards = versions.strip(plan, edl, ctx.facts, ctx.beats.period, ctx.beats.drop, pipe.work.stills, sources, shape,
                               f"v{version}")
        data = {"cards": cards, "alternatives": versions.alternatives(plan, ctx.facts, ctx.bans, pipe.work.stills,
                                                                      sources, shape)}
        self.db.run("UPDATE renders SET shots=? WHERE project_id=? AND kind='final' AND version=?",
                    (json.dumps(data), project["id"], version))
        return data

    def _job_strip(self, job, project, user):
        version = int(job["params"].get("version") or self.work_version(project))
        _, plan, _ = self._version_plan(project, version)
        pipe = self._pipeline(job, project, user)
        ctx = self._context(pipe, project, user, start_at=plan.start_at)
        self.db.job_progress(job["id"], 0.2, f"Numbering the shots of version {version}")
        self._save_strip(pipe, ctx, project, version)

    def _job_changes(self, job, project, user):
        """Read what they asked into exact changes on the numbered shots, and show them before anything is made."""
        from . import versions

        params = job["params"]
        version = int(params.get("base") or self.work_version(project))
        _, plan, _ = self._version_plan(project, version)
        pipe = self._pipeline(job, project, user)
        ctx = self._context(pipe, project, user, start_at=plan.start_at)
        self.db.job_progress(job["id"], 0.1, f"Looking at the shots of version {version}")
        strip = self.strip_of(project, version) or self._save_strip(pipe, ctx, project, version) or {}
        cards = strip.get("cards") or []
        if not cards:
            raise ActionError("This version's shots couldn't be read. Try again in a moment.")
        liked = {r["version"]: r["liked"] for r in self.db.all("SELECT version, liked FROM feedback WHERE project_id=? "
                                                               "AND stage='final' ORDER BY id", (project["id"],))}
        earlier, counts = [], {}
        for v in sorted({r["version"] for r in self.db.all("SELECT version FROM renders WHERE project_id=? AND "
                                                          "kind='final'", (project["id"],))} - {version}, reverse=True)[:12]:
            other = self.strip_of(project, v)
            if other and other.get("cards"):
                shots = [(c["n"], c["moment"], c["what"]) for c in other["cards"]]
            else:
                try:
                    shots = versions.labels_from_plan(self._version_plan(project, v)[1], ctx.facts)
                except ActionError:
                    continue
            earlier.append((v, liked.get(v), shots))
            counts[v] = len(shots)
        before = [r["text"] for r in self.db.all("SELECT text FROM feedback WHERE project_id=? AND stage='change' AND "
                                                 "text<>'' ORDER BY id", (project["id"],))][:-1]
        text = versions.request_text(version, cards, ctx.beats, versions.unused_moments(plan, ctx.facts, ctx.bans),
                                     earlier, ctx.bans, params["text"], before, graphics=plan.graphics)
        self.db.job_progress(job["id"], 0.3, "Reading what you asked for")
        try:
            req = pipe.brain.read_changes(text, params["text"], len(cards), ctx.facts, counts)
        except (BudgetExceeded, JobStopped):  # (and PersonLimit) the money rules and the person decide
            raise
        except Exception as exc:  # noqa: BLE001 - the AI can't read it now: the exact phrasings are still read
            self.db.event(project["id"], f"changes: the AI couldn't read them ({type(exc).__name__}: "
                          f"{' '.join(str(exc).split())[:160]}); read without the AI", "warn", job["id"])
            req = versions.read_simple(params["text"], len(cards))
        self._set_info(project, pending={"base": version, "text": params["text"],
                                         "ops": [o.model_dump() for o in req.ops], "said": req.said,
                                         "unclear": req.unclear})
        self.db.set_stage(project["id"], "confirm")

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
        pipe.speech = bool(self._info(project).get("speech"))
        # no sound from the clips: asked for in this film's words (the mix obeys, music or not), or their style says
        # "music only" and music plays (unless this film's own words ask to hear its clips)
        pipe.mute_clips = self._mutes_clips(project, user, pipe.no_music)
        pipe.brand = dict(self.profile(user).answers.get("brand") or {})
        pipe.mute_effects = project.get("sound") == "silent" or bool(
            re.search(r"\b(?:no|without)\s+(?:sound\s+)?effects?\b|\bno\s+sfx\b", ask_text(project), re.I))
        kind = brain_kind_for(self.s, owner)
        pipe.brain = make_brain(kind, self.s, self._spender(project, user["id"], job["id"]), work.brain,
                                pipe.project.load_signals)
        pipe.watcher = make_watcher(kind, self.s, self._spender(project, user["id"], job["id"]), work.brain)
        if hasattr(getattr(pipe.brain, "backend", None), "pulse"):
            pipe.brain.backend.pulse = pulse
        return pipe

    def _grade(self, project: dict, user: dict, plan: DesignPlan | None = None) -> str:
        """The film's grade: its edit style's, else their words and their style's."""
        from . import styles

        if plan is not None and plan.style in styles.STYLES:
            return styles.STYLES[plan.style].look
        return look_from_note(project["intent"], self.profile(user).look)

    def _mutes_clips(self, project: dict, user: dict, no_music: bool) -> bool:
        """No sound from the clips: asked for in this film's words (the mix obeys, music or not), or their style says
        "music only" and music plays (unless this film's own words ask to hear its clips)."""
        if self._info(project).get("speech"):  # a talking film: their voice is the film
            return False
        return project.get("sound") == "silent" or (
            project["kind"] == "montage" and not no_music and self.profile(user).answers.get("sound") == "music"
            and not requirements.wants_clip_sound(ask_text(project)))

    def _context(self, pipe: Pipeline, project: dict, user: dict, start_at: float | None = None):
        items = self.spec_items(project, user)
        rules = [r for r in rules_for(self.profile(user).rules, project["kind"]) if not specs.rule_off(items, r["id"])]
        ctx = pipe.context(self.shapes(project), project["length"], ask_text(project), rules,
                           project["pace"], start_at=start_at)
        ctx.bans = list(self._info(project).get("bans") or [])
        ctx.speech = bool(self._info(project).get("speech"))
        # what they said on earlier samples and versions of this film still holds ('beat sync' once is beat sync)
        said = [project.get("intent") or "", project.get("cut_note") or ""]
        said += [r["text"] for r in self.db.all("SELECT text FROM feedback WHERE project_id=? AND text<>''", (project["id"],))]
        if hasattr(ctx, "read_words"):
            ctx.read_words(*said)
        specs.apply(ctx, items)  # the spec is the authority: what they switched on or off there holds
        return ctx

    # ------------------------------------------------------------------ your editor (agent.py) and the film's spec
    def spec_items(self, project: dict, user: dict | None = None, extra: list[str] | None = None) -> list[dict]:
        """The film's spec as the person sees it: built from everything they said, their style, and their changes."""
        user = user or self.user(project["user_id"])
        rules = rules_for(self.profile(user).rules, project["kind"]) if user else []
        said = [r["text"] for r in self.db.all("SELECT text FROM feedback WHERE project_id=? AND text<>''", (project["id"],))]
        said += agent.own_words(agent.thread(self.db, project["id"], n=200))
        return specs.film_spec(project, rules, said + [x for x in (extra or []) if x])

    def editor_brain(self, project: dict, user: dict):
        owner = self.is_owner(user)
        work = workdir(self.s, user["id"], project["id"]).ensure()
        return make_brain(brain_kind_for(self.s, owner), self.s, self._spender(project, user["id"], None), work.brain)

    def talk(self, user: dict, project: dict, text: str, refs: list[str] | None = None) -> int:
        """Their message to their editor: kept at once, answered beside the request (agent.turn)."""
        text = " ".join(str(text or "").split())[:agent.MAX_WORDS]
        if not text:
            raise ActionError("Write something to your editor first.")
        hour = self.db.value("SELECT COUNT(*) FROM messages WHERE project_id=? AND role='you' AND created>?",
                             (project["id"], time.time() - 3600), 0)
        if hour >= agent.TURNS_PER_HOUR and not self.is_owner(user):
            raise ActionError("That's a lot of messages for one hour: give it a little while.")
        if agent.thinking(agent.thread(self.db, project["id"], n=1)):
            raise ActionError("Your editor is still answering your last message.")
        mid = self.db.post_message(project["id"], user["id"], "you", "text",
                                   {"text": text, "refs": [" ".join(str(r).split())[:40] for r in refs or []][:8],
                                    "pending": True})
        self.soon(lambda: agent.turn(self, project["id"], mid))
        return mid

    def answer_card(self, user: dict, project: dict, mid: int, k: int, choice: str) -> None:
        try:
            agent.answer(self, user, project, mid, k, choice)
        except agent.TalkError as exc:
            raise ActionError(str(exc)) from exc

    def spec_change(self, user: dict, project: dict, iid: str, op: str) -> None:
        """They switched an item of the film's spec on or off, or removed one they added by talking."""
        known = {it["id"]: it for it in self.spec_items(project, user)}
        if iid not in known:
            raise ActionError("That isn't in this film's brief any more.")
        if not known[iid]["switchable"]:
            raise ActionError("That one changes by telling your editor, not by switching it off.")
        if op == "remove":
            if not specs.is_added(iid):
                raise ActionError("This one comes from what you confirmed or your style: switch it off instead.")
            ov = specs.remove(project, iid)
        elif op in ("on", "off"):
            ov = specs.switch(project, iid, op == "on")
        else:
            raise ActionError("Switch it on or off.")
        self.db.run("UPDATE projects SET spec=?, updated=? WHERE id=?", (json.dumps(ov), time.time(), project["id"]))

    def _save_checks(self, project: dict, key: str, results: list[dict]) -> None:
        """The checklist of one sample ('sample') or version ('3'): every check the spec asks for, met or not."""
        checks = self._info(project).get("checks") or {}
        checks[key] = results
        keep = ["sample"] + sorted((k for k in checks if k != "sample"), key=lambda k: int(k) if k.isdigit() else 0)[-20:]
        self._set_info(project, checks={k: checks[k] for k in keep if k in checks})

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
            try:
                agent.checkin(self, job, project)
            except Exception:  # noqa: BLE001 - a check-in never fails the step it reports on
                log.warning("check-in after %s failed for %s", job["kind"], project["id"], exc_info=True)
            self.db.job_done(job["id"])
            agent.carry_on(self, self.db.project(project["id"]), job["kind"])  # building by itself: the next step
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
                note = (f"Paused: your account has used today's share of AI and rendering. It carries on by itself "
                        f"{when}; you can leave this page.")
                self.db.set_stage(project["id"], "paused", note=note)
                agent.trouble(self, project, note)
        except BudgetExceeded as exc:
            if self._stopped(job):
                return
            self.db.run("UPDATE jobs SET status='waiting', error=? WHERE id=?", (str(exc), job["id"]))
            if job["kind"] in JOB_STAGE:
                self.db.set_stage(project["id"], "paused", note=f"Paused: {exc}. The owner has been told.")
                agent.trouble(self, project, f"Paused: {exc}. Nothing is lost; the owner has been told.")
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
                note = ("Paused: our AI service is not answering right now. Nothing is lost: the owner has been told "
                        "and will start it again from this step, or press Try again in a while.")
                self.db.set_stage(project["id"], "paused", note=note)
                agent.trouble(self, project, note)
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
                agent.trouble(self, project, message)
            elif job["kind"] == "changes":  # reading a change broke: back to their film, which is untouched
                message = str(exc) if user_fixable else ("Your changes couldn't be read this time. Nothing was changed: "
                                                         "try again, or use the buttons on the shots.")
                self.db.set_stage(project["id"], "done", error=message[:300])
                agent.trouble(self, project, message)
            elif job["kind"] in JOB_STAGE:
                message = str(exc) if user_fixable else "Something went wrong on our side. The owner has been told; you can try again."
                self.db.set_stage(project["id"], "failed", error=message[:300])
                agent.trouble(self, project, f"That step didn't work: {message}")
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
        self._enqueue(project, "prepare")

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
        if old.get("autopilot"):
            info["autopilot"] = True
        info["speech"] = self._speech_film(project, pipe, old.get("speech"))
        fitted = False if info["speech"] else self._fit_length(project, pipe, info)
        if info["speech"]:
            fitted = self._speech_length(project, pipe)
        self.db.run("UPDATE projects SET info=? WHERE id=?", (json.dumps(info), project["id"]))
        if fitted:
            project = self.db.project(project["id"])
            if self._ensure_track(job, project, user, pipe, (0.8, 0.84)) is not None:  # music made for the old length
                project = self.db.project(project["id"])
        if info["skipped"]:
            self.db.event(project["id"], f"{len(info['skipped'])} file(s) couldn't be read and were left out.", "warn")
        self.db.job_progress(job["id"], 0.85, "Putting what I saw into words")
        ctx = self._context(pipe, project, user)
        understood = pipe.brain.understand(ctx)
        self.db.run("UPDATE projects SET understood=?, brief=NULL WHERE id=?", (understood.model_dump_json(), project["id"]))
        self.db.set_stage(project["id"], "understood")

    def _speech_film(self, project: dict, pipe, said) -> bool:
        """Someone talking to camera (they said so, or most of the footage is speech): the film is cut on what they
        say (speech_cut), not on a story of moments."""
        from . import speech_cut

        clips = pipe.project.clips()
        spoken = speech_cut.share(pipe.project, list(clips), {c: float(v.duration) for c, v in clips.items()})
        if spoken >= 0.6:  # mostly someone talking: chopping their sentences to a beat is never right
            return True
        if said is not None:
            return bool(said)
        return spoken >= 0.45

    def _speech_length(self, project: dict, pipe) -> bool:
        """A talking film is as long as what they say (pauses out): its length, and its music, follow that."""
        from ..perceive.prep import load_transcript
        from . import speech_cut

        total = 0.0
        for cid in pipe.project.clips():
            t = load_transcript(pipe.project, cid)
            if t is not None:
                total += sum(b - a + speech_cut.LEAD + speech_cut.TAIL
                             for a, b, _ in speech_cut.phrases(speech_cut._words(t)))
        if total < 1.0 or abs(total - float(project["length"])) < 1.0:
            return False
        self.db.run("UPDATE projects SET length=? WHERE id=?", (round(total, 1), project["id"]))
        return True

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
        return max(0, MAX_POLISH - int(u.get("_polished") or 0))

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
        if self._info(project).get("speech"):  # cut on what they say: one way, nothing to pick between
            pitch = StoryPitch(title="Your talk, cut clean", logline="What you say, in your order, the pauses and "
                               "fumbles cut out; graphics and music on top.", feeling="as you said it",
                               hook="Your first words.", turn="As you said it.", ending="Your last words.",
                               beats=[StoryBeat(what="What you say, cut on your words.", moments=[])], stills=[],
                               why="A talking video is cut on its words.")
            self._save_pitches(project, [{"pitch": pitch.model_dump(), "stills": []}], 1)
            self.db.set_stage(project["id"], "choose")
            return
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
        elif self._info(project).get("speech"):
            from . import speech_cut

            plan = speech_cut.plan(ctx, pipe.project)
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
        rows = self._montage_requirements(project, user, ctx, plan, None)
        self._note_requirements(project, rows, "sample")
        try:
            self._save_checks(project, "sample", specs.checklist(self.spec_items(project, user), plan, ctx, rows=rows))
        except Exception:  # noqa: BLE001 - the checklist reports; the sample is made without it
            log.warning("could not check the sample of %s", project["id"], exc_info=True)
        info = pipe.sample(ctx, plan, shape, f"sample-{plan_id}", look=self._grade(project, user, plan))
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
                                    look=self._grade(project, user, plan))

    def _note_requirements(self, project: dict, rows: list[dict], where: str) -> None:
        bad = requirements.unmet(rows)
        if bad:
            self.db.event(project["id"], f"requirement not met ({where}): " + "; ".join(bad), "warn")

    def _render_plan(self, job, project, user, pipe: Pipeline, ctx, plan: DesignPlan, plan_id: int,
                     base: int | None = None, changes: list[str] | None = None) -> None:
        version = project["version"] + 1
        infos = pipe.render(ctx, plan, self.shapes(project), version, look=self._grade(project, user, plan))
        if any(g.kind == "scene" for g in plan.graphics):  # its designed scenes, laid back in where the cut is the same
            from ..work.scenes_in_film import lay_back

            left = []
            for info in infos:
                for note in lay_back(self, project, plan, version, info.shape, pipe.work.out / info.path):
                    self.db.event(project["id"], note, "warn", job["id"])
                    left.append(note)
            changes = [*(changes or []), *dict.fromkeys(left)]  # said with the version, where they look
        for info in infos:
            rows = self._montage_requirements(project, user, ctx, plan, info.seconds)
            info.findings.extend(rows)
            self._note_requirements(project, rows, f"{info.shape} final")
            self.db.run("INSERT INTO renders(project_id, plan_id, version, kind, shape, file, seconds, render_seconds, "
                        "findings, created, base, changes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (project["id"], plan_id, version, "final", info.shape, info.path, info.seconds, info.render_seconds,
                         json.dumps(info.findings), time.time(), base, json.dumps(changes) if changes else None))
            if info.failed:
                alert(self.db, self.s, "check", f"A finished video failed a check: "
                      + "; ".join(f"{f['check']}: {f['detail'][:80]}" for f in info.failed), "warning")
        self.db.run("UPDATE projects SET version=?, base=NULL WHERE id=?", (version, project["id"]))
        draft = self.draft_of(project)
        if draft and int(draft.get("base") or 0) < version:  # made on an older version: making it now would undo this one
            self._set_info(project, draft=None, draft_back=[], draft_fwd=[])
        strip = None
        try:  # its shots, numbered as they see them, for the next change
            self.db.job_progress(job["id"], 0.97, "Numbering its shots")
            strip = self._save_strip(pipe, ctx, self.db.project(project["id"]), version)
        except Exception:  # noqa: BLE001 - the film is made; the strip can be made again later
            log.warning("could not number the shots of %s v%s", project["id"], version, exc_info=True)
        try:  # every check the spec asks for, on the version as made
            first = infos[0] if infos else None
            rows = [f for f in (first.findings if first else []) if f.get("check") == "requirement"]
            self._save_checks(project, str(version), specs.checklist(
                self.spec_items(project, user), plan, ctx, (strip or {}).get("cards"), rows,
                first.seconds if first else None, float(project["length"])))
        except Exception:  # noqa: BLE001 - the checklist reports; the film is made without it
            log.warning("could not check version %s of %s", version, project["id"], exc_info=True)
        try:  # ready to post? measured on the version as made
            self._save_ready(pipe, self.db.project(project["id"]), plan, ctx, strip, version,
                             infos[0].seconds if infos else 0.0)
        except Exception:  # noqa: BLE001 - a score to help; the film is made without it
            log.warning("could not score version %s of %s", version, project["id"], exc_info=True)
        self.db.set_stage(project["id"], "done")

    def _save_ready(self, pipe: Pipeline, project: dict, plan: DesignPlan, ctx, strip, version: int,
                    seconds: float) -> dict:
        from ..edit.timeline import build_timeline
        from ..motion.place import speech_words
        from ..perceive.prep import load_transcript
        from ..schemas import EDL
        from . import ready, versions

        shape = self.shapes(project)[0]
        edl = EDL.model_validate_json(versions.edl_path(pipe.work.out, version, shape).read_text(encoding="utf-8"))
        said = {cid: t for cid in {seg.clip_id for seg in edl.segments}
                if (t := load_transcript(pipe.project, cid)) is not None}
        speech = bool(speech_words(build_timeline(edl, pipe.project.clips()), said))
        checks = (self._info(project).get("checks") or {}).get(str(version))
        viewer = None
        if getattr(pipe, "watcher", None) is not None and hasattr(pipe.watcher, "review"):
            try:  # an AI viewer watches the version itself
                self.db.event(project["id"], "A viewer's eye: watching the version", "info")
                viewer = pipe.watcher.review(pipe.work.out / f"v{version}-{shape.replace(':', 'x')}.mp4",
                                             "Instagram Reels and TikTok" if shape == "9:16" else "YouTube")
            except Exception:  # noqa: BLE001 - the measured parts still score it
                log.warning("the viewer couldn't watch v%s of %s", version, project["id"], exc_info=True)
        result = ready.assess(plan, ctx.facts, (strip or {}).get("cards") or [], edl.motion, seconds, shape == "9:16",
                              speech, checks, viewer)
        board = self._info(project).get("ready") or {}
        board[str(version)] = result
        self._set_info(project, ready={k: board[k] for k in sorted(board, key=int)[-10:]})
        return result

    def ready_of(self, project: dict) -> dict | None:
        """The ready-to-post score of the version they work on."""
        return (self._info(project).get("ready") or {}).get(str(self.work_version(project)))

    def fix_ready(self, user: dict, project: dict, part: str) -> int:
        """One weak part of the score fixed as a draft (seconds): open on the stronger shot, a title, captions."""
        board = self.ready_of(project) or {}
        found = next((x for x in board.get("parts") or [] if x["id"] == part and x.get("fix")), None)
        if found is None:
            raise ActionError("That part has nothing to fix.")
        op = ChangeOp.model_validate({**found["fix"], **({"text": project["title"]} if found["fix"]["op"] == "title" else {})})
        return self.ask_draft(user, project, [op.model_dump()], [found["fix_says"]], found["fix_says"])

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
        if "plan_id" in job["params"]:
            return self._revise_plan(job, project, user)
        if "ops" in job["params"]:
            return self._revise_ops(job, project, user)
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

    def _revise_ops(self, job, project, user):
        """Make the changes they checked on the version they picked: by code on the plan pinned to that version's
        frames (every shot not named stays as it was), and the AI only for a stretch they want made again."""
        pipe = self._pipeline(job, project, user, 0.0, 0.3)
        version, row, ctx, new, lines = self._apply_ops(job, project, user, pipe)
        plan_id = self.db.run("INSERT INTO plans(project_id, kind, pitch_id, data, created) VALUES (?,?,?,?,?)",
                              (project["id"], "change", row["pitch_id"], new.model_dump_json(), time.time()))
        pipe.progress = lambda f, m: self.db.job_progress(job["id"], 0.3 + 0.7 * f, m)
        self._render_plan(job, project, user, pipe, ctx, new, plan_id, base=version, changes=lines)

    def _apply_ops(self, job, project, user, pipe):
        """The changes in the job made on the plan of the version they work on: (version, its plan row, context, the
        new plan, what was done in words)."""
        from . import versions
        from .brain.core import check_plan

        params = job["params"]
        version = int(params.get("base") or self.work_version(project))
        row, plan, _ = self._version_plan(project, version)
        on = params.get("on") and self.db.one("SELECT data FROM plans WHERE id=? AND project_id=?",
                                              (int(params["on"]), project["id"]))
        if on:  # made on the draft they were watching (its shots, its numbers)
            plan = DesignPlan.model_validate_json(on["data"])
        ctx = self._context(pipe, project, user, start_at=plan.start_at)
        _, pitch = self._chosen(project, row["pitch_id"])
        ops = [ChangeOp.model_validate(o) for o in params["ops"]]
        earlier = {}
        for o in ops:
            if o.op == "replace" and o.from_version and o.from_version not in earlier:
                try:
                    earlier[o.from_version] = self._version_plan(project, o.from_version)[1]
                except ActionError:
                    pass
        strip = None if on else self.strip_of(project, version)
        labels = ({c["n"]: c["what"] for c in strip["cards"]} if strip and strip.get("cards")
                  else {n: what for n, _, what in versions.labels_from_plan(plan, ctx.facts)})
        self.db.job_progress(job["id"], 0.05, f"Changing version {version}: only the shots you named")
        done = versions.apply(plan, ctx, ops, earlier, labels)
        if done.bans:
            bans = list(self._info(project).get("bans") or []) + done.bans
            self._set_info(project, bans=bans[-60:])
            ctx.bans = bans
        new = done.plan
        for lo, hi, want in reversed(done.redo):  # last first: the earlier stretches keep their places
            self.db.job_progress(job["id"], 0.15, "Making the stretch you asked for again")
            new = pipe.brain.redo_range(ctx, pitch, new, lo, hi, want)
        problems = check_plan(new, ctx, taste=False)
        if problems:
            self.db.event(project["id"], "changes left: " + "; ".join(problems)[:500], "info", job["id"])
        return version, row, ctx, new, done.lines + [f"Not done: {x}" for x in done.skipped]

    # ------------------------------------------------------------------ drafts and the instant preview (preview.py)
    def ask_draft(self, user: dict, project: dict, ops: list[dict], said: list[str], text: str = "",
                  on_draft: bool = True) -> int:
        """Their changes as a draft: the edit list only (a few seconds, no render), played at once in the browser.
        Nothing is made, and no change is used up, until they say "make it". With a draft on screen the changes are
        made on it (its shot numbers), unless `on_draft` is False (numbers from the version itself)."""
        self._guard(project, "done")
        if not ops:
            raise ActionError("Say which shots to change.")
        version = self.work_version(project)
        params = {"ops": ops, "said": said, "text": " ".join(str(text).split())[:600], "base": version}
        draft = self.draft_of(project)
        if on_draft and draft and int(draft.get("base") or 0) == version:  # watching a draft: made on it
            params["on"] = draft["plan_id"]
        return self._enqueue(project, "draft", params)

    # ------------------------------------------------------------------ the hands-on timeline (frontend timeline.js)
    def timeline_of(self, project: dict, which: str) -> dict:
        """The film as the timeline shows it: each shot (numbered as they see them) with its time, beats, kind and
        still, and the graphics on it; for the draft on screen or a version."""
        from . import versions

        data = self.preview_of(project, which)
        plan = self.draft_plan(project) if which == "draft" else self._version_plan(project, int(which))[1]
        strip = self.strip_of(project, self.work_version(project)) or {}
        thumbs = {}
        for c in strip.get("cards") or []:
            thumbs.setdefault(c.get("moment"), c.get("thumb"))
        by_key = {}
        for c, s in zip(strip.get("cards") or [], (self._version_plan(project, self.work_version(project))[1].plan
                                                   if strip.get("cards") else [])):
            by_key.setdefault(s.key, c.get("thumb"))
        shots = []
        for n, s in enumerate(plan.plan, start=1):
            pieces = [x for x in data["shots"] if x["n"] == n]
            if not pieces:
                continue
            shots.append({"n": n, "start": pieces[0]["start"], "end": pieces[-1]["end"], "beats": versions.span(s),
                          "kind": s.kind, "thumb": by_key.get(s.key) or "", "clip": pieces[0]["clip"]})
        return {"which": which, "duration": data["duration"], "shots": shots,
                "graphics": [g.model_dump(exclude={"x", "y", "accent", "by"}) for g in plan.graphics],
                "over": [o.model_dump() for o in plan.over],
                "motion": [{"block": m["block"], "start": m["start"], "end": m["end"]} for m in data.get("motion", [])],
                "style": plan.style}

    def manual_edit(self, user: dict, project: dict, ops: list[dict], said: list[str]) -> int:
        """Changes made by hand on the timeline: a draft at once, like a plain ask, and remembered as what they do by
        hand (the learning pass reads it)."""
        clean = [ChangeOp.model_validate(o).model_dump() for o in ops][:12]
        if not clean:
            raise ActionError("Nothing to change.")
        self.db.run("INSERT INTO feedback(project_id, user_id, stage, version, text, created) VALUES (?,?,?,?,?,?)",
                    (project["id"], user["id"], "manual", self.work_version(project), "; ".join(said)[:600], time.time()))
        return self.ask_draft(user, project, clean, [str(x)[:200] for x in said][:12], "; ".join(said)[:600])

    # ------------------------------------------------------------------ review links (for clients and teammates)
    def review_link(self, user: dict, project: dict) -> str:
        """A private link to the version they work on: anyone with it can watch it and leave notes at a moment. One live
        link per version (asking again gives the same one)."""
        self._guard(project, "done")
        version = self.work_version(project)
        row = self.db.one("SELECT token FROM review_links WHERE project_id=? AND version=? AND revoked=0",
                          (project["id"], version))
        if row:
            return row["token"]
        token = secrets.token_urlsafe(18)
        self.db.run("INSERT INTO review_links(token, project_id, version, created) VALUES (?,?,?,?)",
                    (token, project["id"], version, time.time()))
        return token

    def revoke_review(self, project: dict, token: str) -> None:
        self.db.run("UPDATE review_links SET revoked=1 WHERE token=? AND project_id=?", (token, project["id"]))

    def review_by_token(self, token: str) -> tuple[dict, dict] | None:
        """(link, film) for a live link (None: no such link, or switched off, or the film is gone)."""
        link = self.db.one("SELECT * FROM review_links WHERE token=? AND revoked=0", (str(token)[:64],))
        project = self.db.project(link["project_id"]) if link else None
        if not link or not project or project.get("deleted"):
            return None
        return dict(link), project

    def add_review_note(self, token: str, at: float, name: str, text: str) -> None:
        found = self.review_by_token(token)
        if not found:
            raise ActionError("This review link isn't open any more.")
        link, project = found
        text = " ".join(str(text).split())[:1000]
        if not text:
            raise ActionError("Write a note.")
        if self.db.one("SELECT COUNT(*) AS n FROM review_notes WHERE token=?", (token,))["n"] >= 300:
            raise ActionError("This link has as many notes as it can take.")
        self.db.run("INSERT INTO review_notes(token, project_id, version, at, name, text, created) VALUES (?,?,?,?,?,?,?)",
                    (token, project["id"], link["version"], max(0.0, min(3600.0, float(at))),
                     " ".join(str(name).split())[:40], text, time.time()))

    def review_notes(self, project: dict, open_only: bool = False) -> list[dict]:
        rows = self.db.all("SELECT * FROM review_notes WHERE project_id=?" + (" AND done=0" if open_only else "")
                           + " ORDER BY version DESC, at, id", (project["id"],))
        return [dict(r) for r in rows]

    def settle_note(self, project: dict, note_id: int) -> None:
        self.db.run("UPDATE review_notes SET done=1 WHERE id=? AND project_id=?", (int(note_id), project["id"]))

    # ------------------------------------------------------------------ platforms and exports (exports.py)
    def set_platform(self, user: dict, project: dict, platform: str) -> str:
        """'Make it for Instagram': the film's shape for that platform, kept for their next films; a finished film in
        another shape gets a version in this one (the same cut, laid out again). Returns what Cello says."""
        from . import exports

        spec = exports.PLATFORMS[platform]
        prof = self.profile(user)
        prof.answers["platform"] = platform
        prof.save()
        said = [f"For {spec['name']}: {spec['shape']}, and {spec['best'][0]}-{spec['best'][1]} s does best there."]
        if project["shapes"] in (spec["shape"], "both"):
            return " ".join(said + ["This film is already in that shape. Your next films start this way too."])
        old = project["shapes"]
        self.db.run("UPDATE projects SET shapes=? WHERE id=?", (spec["shape"], project["id"]))
        if project["stage"] == "done" and not self.db.active_job(project["id"]):
            row = self._version_plan(project, self.work_version(project))[0]
            self._check_revisions(user, project)
            self.db.run("UPDATE projects SET revisions=revisions+1 WHERE id=?", (project["id"],))
            self._enqueue(self.db.project(project["id"]), "revise", {
                "plan_id": row["id"], "base": self.work_version(project),
                "said": [f"The same cut in {spec['shape']} for {spec['name']} (it was {old})"], "text": spec["name"]})
            said.append(f"I'm making this film in {spec['shape']} now (a few minutes); your next films start that way too.")
        else:
            said.append("This film will be made in that shape, and your next films start that way too.")
        return " ".join(said)

    # ------------------------------------------------------------------ AI-made clips (create/clips.py)
    def generate_clip(self, user: dict, project: dict, prompt: str, shot: int | None = None, seconds: int = 5) -> int:
        """A clip made from their words by an AI video model (on this GPU, or Higgsfield with their key); with a shot
        number it takes that shot's place, as a draft."""
        from ..create import clips

        self._guard(project, "setup", "done")  # at the clips step it becomes one of their clips
        prompt = " ".join(str(prompt).split())[:600]
        if len(prompt) < 4:
            raise ActionError("Say what the clip should show.")
        if clips.maker() is None:
            raise ActionError(clips.describe(None) + ".")
        self._check_revisions(user, project)
        return self._enqueue(project, "generate", {"prompt": prompt, "shot": shot, "seconds": seconds})

    def _job_generate(self, job, project, user):
        from ..create import clips
        from .brain.backends import Usage

        params = job["params"]
        if project["stage"] == "setup":  # before the first cut: the shot joins their clips, as if they'd sent it
            return self._generate_into_clips(job, project, user, params)
        pipe = self._pipeline(job, project, user)
        k = len(list(pipe.work.clips.glob("ai-*.mp4"))) + 1
        out = pipe.work.clips / f"ai-{k:02d}.mp4"
        shape = self.shapes(project)[0]
        self.db.job_progress(job["id"], 0.1, "An AI video model is making your clip (a few minutes)")
        t0 = time.monotonic()
        try:
            made = clips.make(params["prompt"], out, seconds=int(params.get("seconds") or 5), aspect=shape,
                              log=lambda m: self.db.job_progress(job["id"], 0.3, m))
        except clips.ClipError as exc:
            raise ActionError(str(exc)) from exc
        self._spender(project, user["id"], job["id"])(Usage("generate", made["model"], made["maker"], usd=made["usd"],
                                                            seconds=round(time.monotonic() - t0, 1)))
        self.db.event(project["id"], f"AI clip ai-{k:02d} made: {params['prompt'][:120]} (${made['usd']:.2f})", "info", job["id"])
        self._swap_in(job, project, user, pipe, out, params.get("shot"), params["prompt"])

    def _generate_into_clips(self, job, project, user, params) -> None:
        import secrets

        from ..create import clips
        from .brain.backends import Usage
        from .storage import _probe, clip_thumb, workdir

        work = workdir(self.s, project["user_id"], project["id"]).ensure()
        cid = "c" + secrets.token_hex(6)
        out = work.clips / f"{cid}.mp4"
        self.db.job_progress(job["id"], 0.1, "An AI video model is making your shot (a few minutes)")
        t0 = time.monotonic()
        try:
            made = clips.make(params["prompt"], out, seconds=int(params.get("seconds") or 5),
                              aspect=self.shapes(project)[0], log=lambda m: self.db.job_progress(job["id"], 0.3, m))
        except clips.ClipError as exc:
            raise ActionError(str(exc)) from exc
        self._spender(project, user["id"], job["id"])(Usage("generate", made["model"], made["maker"], usd=made["usd"],
                                                            seconds=round(time.monotonic() - t0, 1)))
        meta = _probe(out, "clip", self.s)
        name = "Made: " + params["prompt"][:50]
        self.db.run("INSERT INTO clips(id, project_id, name, file, bytes, seconds, width, height, created) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", (cid, project["id"], name, out.name, out.stat().st_size,
                                                   meta["seconds"], meta["width"], meta["height"], time.time()))
        clip_thumb(out, work.stills, cid, meta["seconds"])
        self.db.event(project["id"], f"Shot made from words: {params['prompt'][:120]}", "info", job["id"])

    def _swap_in(self, job, project, user, pipe, out: Path, shot: int | None, what: str) -> None:
        """A new clip (made by AI or by an effect) logged, then in the place of a shot as a draft."""
        self.db.job_progress(job["id"], 0.75, "Watching the new clip")
        pipe.analyse(project["length"], project["pace"])
        params = {"shot": shot, "prompt": what}
        if params.get("shot"):
            from ..perceive.catalog import short_ids

            shorts = short_ids(pipe.project)
            cid = next((c.clip_id for c in pipe.project.clips().values() if Path(c.rel_path).name == out.name), None)
            short = shorts.get(cid) if cid else None
            if short:  # the draft is made in this same step (on the draft on screen, if any)
                project = self.db.project(project["id"])
                draft = self.draft_of(project)
                version = self.work_version(project)
                self._job_draft({**job, "params": {
                    "ops": [ChangeOp(op="replace", shot=int(params["shot"]), moment=f"{short}#1").model_dump()],
                    "said": [f"Shot {params['shot']} is your AI clip: {params['prompt'][:80]}"], "text": params["prompt"],
                    "base": version, **({"on": draft["plan_id"]} if draft and int(draft.get("base") or 0) == version else {})}},
                    project, user)

    # ------------------------------------------------------------------ effects that make a new clip (create/effects.py)
    EFFECTS = {"parallax": "a 3D photo (a slow camera move with depth)", "remove": "the object taken out",
               "upscale": "sharpened and upscaled"}

    def ask_effect(self, user: dict, project: dict, kind: str, shot: int, want: str = "") -> int:
        self._guard(project, "done")
        if kind not in self.EFFECTS:
            raise ActionError("Pick an effect.")
        if kind == "remove" and not want.strip():
            raise ActionError("Say what to take out of the shot.")
        self._check_revisions(user, project)
        return self._enqueue(project, "effect", {"kind": kind, "shot": int(shot), "want": want.strip()[:120]})

    def _job_effect(self, job, project, user):
        import cv2
        import numpy as np

        from ..create import effects
        from ..graphics.track import SceneTracker, transform_point
        from ..perceive.catalog import short_ids

        params = job["params"]
        kind, n = params["kind"], int(params["shot"])
        pipe = self._pipeline(job, project, user)
        plan = self.draft_plan(project) or self._version_plan(project, self.work_version(project))[1]
        if not 1 <= n <= len(plan.plan):
            raise ActionError(f"There is no shot {n}.")
        m = {x.key: x for x in plan.pool}[plan.plan[n - 1].key]
        cid = next((c for c, sh in short_ids(pipe.project).items() if sh == m.clip), None)
        clip = pipe.project.clips().get(cid) if cid else None
        if clip is None:
            raise ActionError(f"Shot {n}'s clip can't be found.")
        video = Path(clip.path)
        k = len(list(pipe.work.clips.glob("fx-*.mp4"))) + 1
        out = pipe.work.clips / f"fx-{k:02d}.mp4"
        start, end = float(m.start), float(m.end)
        self.db.job_progress(job["id"], 0.1, f"Making shot {n} {self.EFFECTS[kind]}")
        if kind == "parallax":
            rgb = next(effects.frames(video, start + 0.3, start + 0.4, 1920))
            effects.parallax(rgb, out, seconds=max(3.0, end - start + 0.5), fps=clip.fps or 30.0)
        elif kind == "upscale":
            effects.upscale(video, start, end, out)
        else:
            if pipe.watcher is None or not hasattr(pipe.watcher, "find_box"):
                raise ActionError("Finding things in shots needs the AI (it is off for this film).")
            first = next(effects.frames(video, start, start + 0.1, 1280))
            ok, jpeg = cv2.imencode(".jpg", first[..., ::-1])
            box = pipe.watcher.find_box(jpeg.tobytes(), params["want"]) if ok else None
            if box is None:
                raise ActionError(f"I couldn't find {params['want']} in shot {n}.")
            tracker, track, t = None, [], 0.0
            for rgb in effects.frames(video, start, end, 960):  # the box follows it as the camera moves
                if tracker is None:
                    tracker = SceneTracker(rgb.shape[1], rgb.shape[0])
                h = tracker.update(rgb)
                x, y = transform_point(h, box[0] * rgb.shape[1], box[1] * rgb.shape[0])
                track.append([round(t, 3), x / rgb.shape[1], y / rgb.shape[0]])
                t += 1.0 / (clip.fps or 30.0)
            effects.remove(video, start, end, track or [[0, box[0], box[1]]], (box[2], box[3]), out)
        self.db.event(project["id"], f"Effect: shot {n} {self.EFFECTS[kind]} ({out.name})", "info", job["id"])
        self._swap_in(job, project, user, pipe, out, n, f"shot {n} {self.EFFECTS[kind]}")

    # ------------------------------------------------------------------ a stretch of the music made again (music/sections.py)
    def edit_music(self, user: dict, project: dict, start: float | None, end: float | None, want: str) -> int:
        """'Make the ending calmer': that stretch of the film's made track written again, the rest kept note for note,
        then the film made again on it (a new version: the cut stays exactly as it was)."""
        self._guard(project, "done")
        row = self.beat_row(project, project.get("beat"))
        song_id = (row or {}).get("full_song_id") or ((row or {}).get("song_id") if (row or {}).get("whole") else None)
        if not song_id and not self._made_here(project, row):
            raise ActionError("Only music made here can be changed in one stretch; this film's track can't (yet).")
        self._check_revisions(user, project)
        return self._enqueue(project, "music_edit", {"start": start, "end": end, "want": " ".join(want.split())[:200]})

    def _made_here(self, project: dict, row: dict | None) -> bool:
        """The film's track was made by the music model on this machine (a stretch of it can be written again here)."""
        if not row or row.get("maker") != "local" or not (row["whole"] or row["full_file"]):
            return False
        return (workdir(self.s, project["user_id"], project["id"]).beats / (row["full_file"] or row["file"])).exists()

    def _job_music_edit(self, job, project, user):
        import soundfile as sf

        from ..music import maker
        from ..music.sections import remake

        params = job["params"]
        row = self.beat_row(project, project.get("beat"))
        work = workdir(self.s, project["user_id"], project["id"]).ensure()
        song_id = row.get("full_song_id") or row.get("song_id")
        current = work.beats / (row["full_file"] or row["file"])
        length = float(sf.info(str(current)).duration)
        start = float(params["start"]) if params.get("start") is not None else 0.0
        end = float(params["end"]) if params.get("end") is not None else length
        words = params["want"]
        styles = [w.strip() for w in re.split(r",|\band\b", words) if w.strip()][:6] or ["calmer"]
        k = len(list(work.beats.glob(f"{current.stem.split('-e')[0]}-e*.mp3"))) + 1
        out = work.beats / f"{current.stem.split('-e')[0]}-e{k}.mp3"
        self.db.job_progress(job["id"], 0.1, f"Writing {int(start) // 60}:{int(start) % 60:02d}-"
                             f"{int(end) // 60}:{int(end) % 60:02d} of the music again: {words}")
        t0 = time.time()
        if row.get("maker") == "local":  # written again on this machine, around the track's own description
            try:
                caption = maker.from_plan(json.loads(row.get("plan") or "{}"))["caption"]
            except (maker.MakerError, ValueError):
                caption = ", ".join(row["idea"].get("styles") or [])
            done = maker.repaint(current, start, end, ", ".join(x for x in (caption, words) if x), out)
        else:
            done = remake(song_id, length, start, end, styles, [], out)
            self._spend_music(project, user, job, max(3.0, end - start), time.time() - t0)
        self.db.run("UPDATE beats SET full_file=?, full_song_id=? WHERE id=?", (out.name, done.get("song_id") or song_id, row["id"]))
        self._choose_beat(self.db.project(project["id"]), self.beat_row(project, row["id"]))
        plan_row = self._version_plan(project, self.work_version(project))[0]
        self.db.run("UPDATE projects SET revisions=revisions+1 WHERE id=?", (project["id"],))
        self._revise_plan({**job, "params": {"plan_id": plan_row["id"], "base": self.work_version(project),
                                             "said": [f"The music from {int(start) // 60}:{int(start) % 60:02d} to "
                                                      f"{int(end) // 60}:{int(end) % 60:02d} made again: {words}"]}},
                          self.db.project(project["id"]), user)

    def ask_export(self, user: dict, project: dict, kind: str) -> int:
        from . import exports

        self._guard(project, "done")
        if kind not in exports.KINDS:
            raise ActionError("Pick a format.")
        return self._enqueue(project, "export", {"version": self.work_version(project), "kind": kind})

    def _job_export(self, job, project, user):
        from ..edit.timeline import build_timeline
        from ..motion.place import speech_words
        from ..perceive.prep import load_transcript
        from ..schemas import EDL
        from . import exports, versions

        params = job["params"]
        version, kind = int(params["version"]), params["kind"]
        pipe = self._pipeline(job, project, user)
        found = sorted(pipe.work.out.glob(f"v{version}-*x*.mp4"))
        shape = self.shapes(project)[0].replace(":", "x")
        video = pipe.work.out / f"v{version}-{shape}.mp4"
        if not video.exists():
            video = next((f for f in found if f.stem.count("-") == 1), video)
        if not video.exists():
            raise ActionError(f"Version {version}'s video isn't here.")
        edl = EDL.model_validate_json(versions.edl_path(pipe.work.out, version, video.stem.split("-", 1)[1].replace("x", ":"))
                                      .read_text(encoding="utf-8"))
        words = None
        if kind == "srt" and not any(g.kind == "captions" and g.words for g in edl.motion):
            said = {cid: t for cid in {seg.clip_id for seg in edl.segments}
                    if (t := load_transcript(pipe.project, cid)) is not None}
            words = speech_words(build_timeline(edl, pipe.project.clips()), said)
            if not words:
                raise ActionError("Nobody is heard in this film, so there are no captions to export.")
        self.db.job_progress(job["id"], 0.2, f"Making the {exports.KINDS[kind][0]}")
        out = pipe.work.out / f"{video.stem}-{kind}.{exports.KINDS[kind][1]}"
        if kind in ("fcpxml", "edl"):  # the cut for an editor's own software, on the original clips
            from . import nle

            timeline = build_timeline(edl, pipe.project.clips())
            text = (nle.fcpxml if kind == "fcpxml" else nle.cmx3600)(edl, timeline, pipe.project.clips(), project["title"])
            out.write_text(text, encoding="utf-8")
        else:
            out = exports.make(kind, video, edl, out, words)
        made = self._info(project).get("exports") or {}
        made.setdefault(str(version), {})[kind] = out.name
        self._set_info(project, exports=made)

    def exports_of(self, project: dict) -> list[dict]:
        """The formats for the version they work on: made ones with their file, the others to make."""
        from . import exports

        made = (self._info(project).get("exports") or {}).get(str(self.work_version(project))) or {}
        return [{"kind": k, "name": name, "file": made.get(k, "")} for k, (name, _) in exports.KINDS.items()]

    def try_style(self, user: dict, project: dict, style_id: str) -> int:
        """Their film in an edit style, as a draft (seconds): its grade, title move, transitions and finishes."""
        from . import styles

        style = styles.STYLES.get(style_id)
        if style is None:
            raise ActionError("Pick a style.")
        op = ChangeOp(op="style", shot=1, want=style_id, text=project["title"])
        return self.ask_draft(user, project, [op.model_dump()], [f"The film in the {style.name} style: {style.says}"],
                              f"{style.name} style")

    def style_cards(self, project: dict) -> list[dict]:
        """The edit styles for the film page: each with what its title does (for the live preview on the card), and
        which one the version they work on is in."""
        from . import styles

        try:
            current = self._version_plan(project, self.work_version(project))[1].style
        except Exception:  # noqa: BLE001 - an old film: none picked
            current = ""
        draft = self.draft_plan(project)
        return [{"id": st.id, "name": st.name, "says": st.says, "on": st.id == current,
                 "trying": bool(draft and draft.style == st.id and st.id != current),
                 "preview": {"text": (project["title"] or "Your film")[:28], "style": st.title, "accent": st.color,
                             "sub": st.name, "bar": True, "look": st.look}} for st in styles.STYLES.values()]

    def draft_plan(self, project: dict) -> DesignPlan | None:
        """The plan of the draft on screen (None: no draft)."""
        draft = self.draft_of(project)
        row = draft and self.db.one("SELECT data FROM plans WHERE id=? AND project_id=?",
                                    (int(draft["plan_id"]), project["id"]))
        return DesignPlan.model_validate_json(row["data"]) if row else None

    def _job_draft(self, job, project, user):
        pipe = self._pipeline(job, project, user)
        if "plan" in job["params"]:  # a whole plan, rewritten by the work agent (plan_draft)
            self.db.job_progress(job["id"], 0.1, "Laying out your editor's new plan")
            version, row, ctx, new, lines = self._given_plan(job, project, user, pipe)
        else:
            self.db.job_progress(job["id"], 0.1, "Changing only the shots you named")
            version, row, ctx, new, lines = self._apply_ops(job, project, user, pipe)
        plan_id = self.db.run("INSERT INTO plans(project_id, kind, pitch_id, data, created) VALUES (?,?,?,?,?)",
                              (project["id"], "draft", row["pitch_id"], new.model_dump_json(), time.time()))
        k = int(self._info(project).get("drafts") or 0) + 1
        shape = self.shapes(project)[0]
        name = f"draft-{k}-{shape.replace(':', 'x')}"
        self.db.job_progress(job["id"], 0.6, "Laying out the draft")
        path = pipe.draft(ctx, new, shape, name, look=self._grade(project, user, new))
        lines = [*lines, *graphics_notes(path), *getattr(pipe, "motion_notes", [])]
        text = job["params"].get("text", "")
        prev = self.draft_of(project)
        if job["params"].get("on") and prev and prev.get("plan_id") == job["params"]["on"]:  # all its changes so far
            lines = list(dict.fromkeys([*(x for x in prev.get("said") or [] if not x.startswith("Nobody is heard")),
                                        *lines]))
            text = " · ".join(x for x in (prev.get("text", ""), text) if x)[:1200]
        try:  # a film made before the light copies existed gets them now (a few seconds per clip)
            self.db.job_progress(job["id"], 0.8, "Making quick previews of your clips")
            pipe.make_previews()
        except Exception:  # noqa: BLE001 - the draft is made; its shots show their stills without copies
            log.warning("could not make preview copies for %s", project["id"], exc_info=True)
        self._set_info(project, drafts=k)
        self._draft_now(project, {"k": k, "base": version, "plan_id": plan_id, "edl": f"{name}.edl.json",
                                  "said": lines, "text": text, "made": time.time()})

    def _job_copies(self, job, project, user):
        """Light copies of the clips for the instant preview, and who is in the footage, for a film made before
        they existed."""
        from . import faces

        pipe = self._pipeline(job, project, user)
        self.db.job_progress(job["id"], 0.1, "Making quick previews of your clips")
        pipe.make_previews()
        if not (pipe.work.root / faces.INDEX).exists():
            self.db.job_progress(job["id"], 0.6, "Looking at who is in your clips")
            pipe.index_faces()

    # ------------------------------------------------------------------ who is in the footage (faces.py)
    def people_of(self, project: dict) -> dict | None:
        from . import faces

        return faces.load(workdir(self.s, project["user_id"], project["id"]).root / faces.INDEX)

    def find_people(self, project: dict) -> bool:
        """Start looking at who is in the clips of a film made before the face index (False: already there, or busy)."""
        if self.people_of(project) is not None or self.db.active_job(project["id"]):
            return False
        self.db.enqueue("copies", project["id"], project["user_id"], {}, PRIORITY["copies"])
        return True

    def set_person(self, user: dict, project: dict, person_id: str, op: str, name: str = "") -> dict:
        """They said who someone is: "this is me", a name, or never show them (every appearance becomes a scene no
        plan may use, and the brief says so). Returns the person."""
        from . import faces

        path = workdir(self.s, project["user_id"], project["id"]).root / faces.INDEX
        index = faces.load(path)
        person = next((p for p in (index or {}).get("people") or [] if p["id"] == person_id), None)
        if person is None:
            raise ActionError("That person isn't in this film's clips.")
        before = faces.label(person)
        if op == "me":
            for p in index["people"]:
                p["me"] = p is person
        elif op == "not_me":
            person["me"] = False
        elif op == "name":
            person["name"] = " ".join(str(name).split())[:40]
        elif op in ("never", "show"):
            person["never"] = op == "never"
            info_bans = [b for b in self._info(project).get("bans") or [] if b.get("what") != f"{before} in shot"]
            if op == "never":
                info_bans += faces.bans(person)
            self._set_info(project, bans=info_bans[-120:])
            text = f"Never show {faces.label(person) if person.get('name') or person.get('me') else before}"
            if op == "never":
                ov, _ = specs.add(project, text, "never", None, "", "you", self.spec_items(project, user))
                self.db.run("UPDATE projects SET spec=? WHERE id=?", (json.dumps(ov), project["id"]))
        else:
            raise ActionError("What should I do with them?")
        faces.save(path, index)
        return person

    def person_draft(self, user: dict, project: dict, person_id: str) -> list[int]:
        """A draft of the version they work on without the shots this person is in. Returns the shots cut."""
        from . import faces

        person = next((p for p in (self.people_of(project) or {}).get("people") or [] if p["id"] == person_id), None)
        if person is None:
            raise ActionError("That person isn't in this film's clips.")
        version = self.work_version(project)
        strip = self.strip_of(project, version) or {}
        hits = faces.shots_with(person, strip.get("cards") or [])
        if not hits:
            raise ActionError(f"{faces.label(person)} isn't close up in any shot of version {version}.")
        ops = [ChangeOp(op="remove", shot=n).model_dump() for n in hits]
        who = "you're" if person.get("me") else f"{faces.label(person)} is"
        said = [f"Shot {n} is cut ({who} in it)" for n in hits]
        self.ask_draft(user, project, ops, said, f"cut where {faces.label(person)} shows", on_draft=False)
        return hits

    def draft_of(self, project: dict) -> dict | None:
        return self._info(project).get("draft")

    def _draft_now(self, project: dict, entry: dict) -> None:
        """A new draft on screen: the one it replaces is kept to go back to (undo, when it was made on the same
        version), and nothing is left to redo."""
        info = self._info(project)
        prev = info.get("draft")
        back = list(info.get("draft_back") or []) if prev and int(prev.get("base") or 0) == int(entry["base"]) else []
        if prev and int(prev.get("base") or 0) == int(entry["base"]):
            back = [*back, prev][-40:]
        self._set_info(project, draft=entry, draft_back=back, draft_fwd=[])

    def draft_undo(self, project: dict) -> None:
        """Back to the draft before (the first one undone: the version itself)."""
        self._guard(project, "done")
        info = self._info(project)
        now = info.get("draft")
        if not now:
            raise ActionError("Nothing to undo: this is the version as it was made.")
        back = list(info.get("draft_back") or [])
        self._set_info(project, draft=back.pop() if back else None, draft_back=back,
                       draft_fwd=[*(info.get("draft_fwd") or []), now][-40:])

    def draft_redo(self, project: dict) -> None:
        self._guard(project, "done")
        info = self._info(project)
        fwd = list(info.get("draft_fwd") or [])
        if not fwd:
            raise ActionError("Nothing to redo.")
        now = info.get("draft")
        back = [*(info.get("draft_back") or []), now][-40:] if now else list(info.get("draft_back") or [])
        self._set_info(project, draft=fwd.pop(), draft_back=back, draft_fwd=fwd)

    def _hand_pipeline(self, project: dict, user: dict) -> Pipeline:
        """The pipeline for edits made by hand (studio/editing.py): no step runs, so nothing reports progress."""
        pipe = self._pipeline({"id": 0}, project, user)
        pipe.progress = lambda f, m: None
        return pipe

    def lay_out_draft(self, user: dict, project: dict, pipe: Pipeline, ctx, plan: DesignPlan, lines: list[str],
                      on_draft: bool) -> dict:
        """A plan changed by hand (studio/editing.py), laid out as the new draft at once: its edit list (a second or
        two, no queue), its changes added to the draft's own when it was made on the draft on screen, and what they
        did remembered (the learning pass reads it)."""
        version = self.work_version(project)
        row = self._version_plan(project, version)[0]
        plan_id = self.db.run("INSERT INTO plans(project_id, kind, pitch_id, data, created) VALUES (?,?,?,?,?)",
                              (project["id"], "draft", row["pitch_id"], plan.model_dump_json(), time.time()))
        k = int(self._info(project).get("drafts") or 0) + 1
        shape = self.shapes(project)[0]
        name = f"draft-{k}-{shape.replace(':', 'x')}"
        path = pipe.draft(ctx, plan, shape, name, look=self._grade(project, user, plan))
        said = [*lines, *graphics_notes(path), *getattr(pipe, "motion_notes", [])]
        prev = self.draft_of(project)
        if on_draft and prev:
            said = list(dict.fromkeys([*(prev.get("said") or []), *said]))
        entry = {"k": k, "base": version, "plan_id": plan_id, "edl": f"{name}.edl.json", "said": said[-40:],
                 "text": (prev or {}).get("text", "") if on_draft else "", "made": time.time()}
        self._set_info(project, drafts=k)
        self._draft_now(project, entry)
        self.db.run("INSERT INTO feedback(project_id, user_id, stage, version, text, created) VALUES (?,?,?,?,?,?)",
                    (project["id"], user["id"], "manual", version, "; ".join(lines)[:600], time.time()))
        return entry

    def make_draft(self, user: dict, project: dict) -> None:
        """The draft on screen becomes a real version: rendered in full quality from exactly that plan."""
        self._guard(project, "done")
        draft = self.draft_of(project)
        if not draft:
            raise ActionError("There's no draft to make.")
        self._check_revisions(user, project)
        self.db.run("UPDATE projects SET revisions=revisions+1 WHERE id=?", (project["id"],))
        self._set_info(project, draft=None, draft_back=[], draft_fwd=[])
        self._enqueue(project, "revise", {"plan_id": draft["plan_id"], "base": draft["base"], "said": draft["said"],
                                          "text": draft.get("text", "")})

    def discard_draft(self, project: dict) -> None:
        """Back to the version (the draft can be brought back with redo)."""
        draft = self.draft_of(project)
        if not draft:
            raise ActionError("There's no draft to discard.")
        info = self._info(project)
        self._set_info(project, draft=None, draft_fwd=[*(info.get("draft_fwd") or []), draft][-40:])

    # ------------------------------------------------------------------ the whole plan, for the work agent (work/)
    def _light_context(self, project: dict, user: dict, start_at: float | None):
        """The film's context without the AI (the footage's log, the beat grid, their rules and bans): enough to
        list the moments and check a plan, at once."""
        from .pipeline import Pipeline

        pipe = Pipeline(workdir(self.s, project["user_id"], project["id"]), self.cfg, None)
        pipe.speech = bool(self._info(project).get("speech"))
        return self._context(pipe, project, user, start_at=start_at)

    def _plan_ready(self, project: dict) -> None:
        """A film whose plan can be rewritten now, or why not (in words the work agent can act on)."""
        if project["kind"] != "montage":
            raise ActionError("This film is made as scenes (a voice film): change it in words (the change tool).")
        if project["stage"] == "confirm":
            raise ActionError("Changes read from their words are waiting for their OK on screen: make them (step "
                              "confirm_change) or drop them (step cancel_change) first.")
        if project["stage"] != "done":
            raise ActionError(f"The film is {project['stage']} now: its plan can be rewritten once that step is done.")

    def plan_view(self, project: dict, user: dict) -> dict:
        """What they're watching as one plan to rewrite (the draft on screen, else the version they work on), with
        every logged moment of their footage and what a plan must keep (see plan_draft)."""
        from .brain.context import people_in
        from .brain.core import plan_beats

        self._plan_ready(project)
        version = self.work_version(project)
        draft = self.draft_of(project)
        on_draft = bool(draft and int(draft.get("base") or 0) == version)
        plan = self.draft_plan(project) if on_draft else self._version_plan(project, version)[1]
        ctx = self._light_context(project, user, plan.start_at)
        used = [(m.clip, m.start, m.end) for m in plan.pool if any(s.key == m.key for s in plan.plan)]
        moments = []
        for short, f in ctx.facts.items():
            for k, m in enumerate(f.moments, start=1):
                a, b = float(m["start"]), float(m["end"])
                moments.append({
                    "id": f"{short}#{k}", "clip": short, "start": round(a, 2), "end": round(b, 2),
                    "peak": m.get("peak"), "what": m.get("description") or "", "strength": m.get("strength", 0),
                    "place": m.get("place") or "", "people": people_in(m),
                    "used": any(c == short and min(b, e) - max(a, s) > 0.25 for c, s, e in used),
                    "never": any(x["clip"] == short and min(b, x["end"]) - max(a, x["start"]) > 0.25
                                 for x in ctx.bans)})
        clips = {short: {"seconds": round(f.duration, 2), "fps": f.fps, "vertical": f.vertical,
                         "unusable": [[round(a, 2), round(b, 2)] for a, b in f.avoid]} for short, f in ctx.facts.items()}
        which = f"the draft on screen (made on version {version})" if on_draft else f"version {version}"
        rule = ("it is a talking film: as long as what is said" if ctx.speech else
                f"the shots' beats must add up to exactly {ctx.beats.target:g} (now {plan_beats(plan):g})")
        about = [
            f"The plan of {which}: {len(plan.plan)} shots, {len(plan.graphics)} graphics"
            + (f" ({', '.join(g.kind for g in plan.graphics)})" if plan.graphics else "") + ".",
            f"One beat = {ctx.beats.period:.3f} s; {rule}"
            + (f"; the music's drop is on beat {ctx.beats.drop:g}" if ctx.beats.drop is not None else "") + ".",
            "Each pool moment is a window of one clip (clip seconds), mostly inside a logged moment of it (moments "
            "list them, with `used` for those in the film now and `never` for scenes they ruled out); pool keys are "
            "unique and every shot's key is in the pool. Shots last at least 0.21 s; a shot needs its footage "
            "(a hold or slow shot needs more of the clip than its length).",
        ]
        return {"which": which, "version": version, "plan": plan.model_dump(mode="json"), "moments": moments,
                "clips": clips, "beat": round(ctx.beats.period, 4),
                "beats_total": None if ctx.speech else ctx.beats.target, "about": about}

    def plan_draft(self, user: dict, project: dict, data: dict, said: list[str], text: str = "") -> int:
        """A whole plan from the work agent (plan_view's, rewritten), checked at once like any plan (the footage, the
        beats, the shot lengths: check_plan without the rules of taste, as for their own storyboard changes) and
        laid out as a draft on their screen. Returns the draft's job."""
        from pydantic import ValidationError

        from .brain.core import check_plan

        self._plan_ready(project)
        self._guard(project, "done")
        try:
            plan = DesignPlan.model_validate(data)
        except ValidationError as exc:
            raise ActionError("That plan doesn't read: " + "; ".join(
                f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:12])) from exc
        version = self.work_version(project)
        if plan.start_at is None:  # the beat grid of the version it was made on
            plan.start_at = self._version_plan(project, version)[1].start_at
        problems = check_plan(plan, self._light_context(project, user, plan.start_at), taste=False)
        if problems:
            raise ActionError("The plan can't be made yet: " + " | ".join(problems))
        lines = [" ".join(str(x).split())[:200] for x in said if str(x).strip()][:12] or ["The edit, rearranged"]
        params = {"plan": plan.model_dump(mode="json"), "said": lines, "text": " ".join(str(text).split())[:600],
                  "base": version}
        draft = self.draft_of(project)
        if draft and int(draft.get("base") or 0) == version:  # rewritten from the draft on screen: its lines carry on
            params["on"] = draft["plan_id"]
        return self._enqueue(project, "draft", params)

    def _given_plan(self, job, project, user, pipe):
        """A whole plan from the work agent (plan_draft), checked again with the film's full context: (version, its
        plan row, context, the plan, what was done in words)."""
        from .brain.core import check_plan

        params = job["params"]
        version = int(params.get("base") or self.work_version(project))
        row, _, _ = self._version_plan(project, version)
        plan = DesignPlan.model_validate(params["plan"])
        ctx = self._context(pipe, project, user, start_at=plan.start_at)
        problems = check_plan(plan, ctx, taste=False)
        if problems:
            raise ActionError("The plan can't be made: " + " | ".join(problems))
        return version, row, ctx, plan, list(params.get("said") or [])

    def _revise_plan(self, job, project, user):
        """A version rendered from a plan already made (a draft they watched and said "make it" to)."""
        params = job["params"]
        row = self.db.one("SELECT * FROM plans WHERE id=? AND project_id=?", (int(params["plan_id"]), project["id"]))
        if row is None:
            raise ActionError("That draft is gone: make the change again.")
        plan = DesignPlan.model_validate_json(row["data"])
        pipe = self._pipeline(job, project, user)
        ctx = self._context(pipe, project, user, start_at=plan.start_at)
        self._render_plan(job, project, user, pipe, ctx, plan, row["id"], base=params.get("base"),
                          changes=list(params.get("said") or []))

    def preview_of(self, project: dict, which: str) -> dict:
        """What the browser player needs for a version ('3') or the draft ('draft'): see preview.timeline. Clips
        without their light copy yet get them made in the background (the page tries again when they're there)."""
        from ..schemas import EDL
        from . import faces, preview

        work = workdir(self.s, project["user_id"], project["id"])
        if which == "draft":
            draft = self.draft_of(project)
            if not draft:
                raise ActionError("There's no draft.")
            path = work.out / draft["edl"]
        else:
            if not str(which).isdigit():
                raise ActionError("Which version?")
            shape = self.shapes(project)[0].replace(":", "x")
            path = work.out / f"v{int(which)}-{shape}.edl.json"
            if not path.exists():
                found = sorted(work.out.glob(f"v{int(which)}-*.edl.json"))
                path = found[0] if found else path
        if not path.exists():
            raise ActionError("That version can't be previewed.")
        edl = EDL.model_validate_json(path.read_text(encoding="utf-8"))
        numbers: dict[str, int] = {}
        try:  # each segment's shot number as the strip counts them (a stutter's pieces share one)
            from . import versions

            if which == "draft":
                row = self.db.one("SELECT data FROM plans WHERE id=?", (int(draft["plan_id"]),))
                plan = DesignPlan.model_validate_json(row["data"]) if row else None
            else:
                plan = self._version_plan(project, int(which))[1]
            if plan is not None:
                numbers = {sid: n for n, ids in enumerate(versions.slot_ids(plan), start=1) for sid in ids}
        except Exception:  # noqa: BLE001 - numbers are a help; the preview plays without them
            numbers = {}
        missing = set()

        def copy_url(clip_id: str) -> str | None:
            if preview.copy_path(work.root, clip_id).exists():
                return f"/m/{project['id']}/preview/{clip_id}.mp4"
            missing.add(clip_id)
            return None

        music_url = None
        if edl.music and edl.music.path:
            name = Path(edl.music.path).name
            if (work.music / name).exists():
                music_url = f"/m/{project['id']}/music/{name}"
            elif (work.beats / name).exists():
                music_url = f"/m/{project['id']}/beats/{name}"
            elif work.song_path() is not None:
                music_url = f"/m/{project['id']}/music/{work.song_path().name}"
        user = self.user(project["user_id"])
        no_music = project["music"] == "none" and not project["song"]
        data = preview.timeline(edl, copy_url, music_url, numbers,
                                clip_sound=not (user and self._mutes_clips(project, user, no_music)),
                                seen=faces.appearances(faces.load(work.root / faces.INDEX)))
        data["missing"] = sorted(missing)
        if missing and not self.db.active_job(project["id"]):
            self.db.enqueue("copies", project["id"], project["user_id"], {}, PRIORITY["copies"])
            data["making"] = True
        return data

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
            if f["stage"] == "manual":  # what they changed by hand on the timeline
                events.append(f"Changed by hand on version {f['version']}: {f['text']}")
                continue
            if f["stage"] == "sample":
                events.append(("Approved the sample" if f["liked"] else "Rejected the sample") +
                              (f": \"{f['text']}\"" if f["text"] else ""))
            elif f["stage"] == "change":
                events.append(f"Asked for changes to version {f['version']}: \"{f['text']}\"")
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

    def queued_note(self, job: dict | None) -> str:
        """What a waiting video says while it waits."""
        if not job or job["status"] != "queued":
            return ""
        if self.paused():
            return "Flicello is paused for a moment. Your video carries on by itself when it's back; you can leave this page."
        running, queued = self.ahead(job)
        n = len(running) + len(queued)
        if not n:
            return ""
        return (f"Waiting for its turn: {n} video{'s are' if n > 1 else ' is'} being made before this one. "
                f"It starts by itself right after.")

    def find_moments(self, user: dict, project: dict, what: str) -> dict:
        """Where in the version they work on `what` happens (watched with its sound by the watcher), with the shots
        it falls in, so a change can name them ('cut where my eyes are closed')."""
        what = " ".join((what or "").split())[:200]
        if not what:
            raise ActionError("Say what to look for.")
        version = self.work_version(project)
        if not version:
            raise ActionError("There is no version to look through yet: it comes after the first cut.")
        work = workdir(self.s, project["user_id"], project["id"])
        video = work.out / f"v{version}-{self.shapes(project)[0].replace(':', 'x')}.mp4"
        if not video.exists():
            raise ActionError("That version's video isn't here to look through.")
        watcher = make_watcher(brain_kind_for(self.s, self.is_owner(user)), self.s,
                               self._spender(project, user["id"], None), work.brain)
        if watcher is None or not hasattr(watcher, "find_moments"):
            raise ActionError("Finding moments by what happens needs the AI (it is off for this film).")
        found = watcher.find_moments(video, what)
        shots = []
        if project["kind"] == "montage":
            try:
                shots = self.timeline_of(project, str(version))["shots"]
            except Exception:  # noqa: BLE001 - the times alone still say where
                shots = []
        for m in found:
            m["shots"] = [s["n"] for s in shots if min(m["end"], s["end"]) - max(m["start"], s["start"]) > 0.1]
        return {"version": version, "looked_for": what, "moments": found}

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
            "cap_usd": self.money()["monthly_usd"],
            "paused": self.paused(),
        }


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
