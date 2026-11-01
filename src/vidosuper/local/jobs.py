"""Who does each job on this machine: the Claude model for each kind of thinking, and which painter, video model,
cartoon engine and music maker. Chosen on the Connect page (Models), kept in studio.yaml under `jobs:`, and applied
as the environment variables the code reads each time it starts a job, so a change takes effect on the next job
without a restart. "auto" means: the best that can run here (the code's own choice)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

CLAUDE = (("claude-opus-5-5", "Opus", "the strongest work; slowest, uses the most of your plan"),
          ("claude-sonnet-5-5", "Sonnet", "quicker and lighter on your plan"),
          ("claude-haiku-5-5", "Haiku", "fastest and lightest; for simple work"))


@dataclass(frozen=True)
class Job:
    id: str
    label: str
    says: str
    env: str
    choices: tuple[tuple[str, str, str], ...]
    default: str
    note: str = ""
    extra: dict = field(default_factory=dict)


JOBS: tuple[Job, ...] = (
    Job("talk", "Talking with you", "Cello in conversation: quick replies, real work goes to its hands",
        "VIDO_CELLO_MODEL", (CLAUDE[2], CLAUDE[1], CLAUDE[0]), "claude-haiku-5-5", "restart Cello to hear it"),
    Job("hands", "Making your edits", "Cello's hands: the sessions that change a film, write captions, plan cuts",
        "VIDO_HANDS_MODEL", CLAUDE, "claude-opus-5-5"),
    Job("design", "Designing motion graphics", "the worlds, scenes and cartoons it designs and directs",
        "VIDO_DESIGN_MODEL", CLAUDE, "claude-opus-5-5"),
    Job("eyes", "Watching your footage", "finding moments and checking drafts (when Gemini isn't watching)",
        "VIDO_WATCH_MODEL", CLAUDE, "claude-opus-5-5"),
    Job("painter", "Painting", "the pictures inside designed scenes",
        "VIDO_PAINTER", (("auto", "Best here", "this machine when it can, else Gemini"),
                         ("local", "This machine", "Qwen-Image-Edit on your GPU"),
                         ("gemini", "Gemini", "Google's image model, with your key")), "auto"),
    Job("clips", "AI shots from words", "shots made from a description when your footage has none",
        "VIDO_AI_CLIPS", (("auto", "Best here", "this machine when it can, else Higgsfield"),
                          ("local", "This machine", "Wan 2.2 on your GPU"),
                          ("higgsfield", "Higgsfield", "online, with your key, paid per shot")), "auto"),
    Job("cartoons", "Cartoons", "how a cartoon's characters are animated",
        "VIDO_CARTOON_ENGINE", (("auto", "Best here", "the video model when it can run, else drawn in code"),
                                ("cartoon", "Wan 2.2", "a video model on your GPU"),
                                ("anime", "AniSora", "an anime video model on your GPU"),
                                ("rig", "Blender rigs", "characters built and animated in 3D"),
                                ("drawn", "Drawn in code", "any machine; flat drawn animation")), "auto"),
    Job("music", "Music", "the score made for a film",
        "VIDO_MUSIC", (("auto", "Best here", "this machine when it can, else ElevenLabs"),
                       ("local", "This machine", "ACE-Step on your GPU"),
                       ("elevenlabs", "ElevenLabs", "online, with your key")), "auto"),
)
BY_ID = {j.id: j for j in JOBS}


def current(job: Job) -> str:
    """What does the job now: its environment variable, else its default."""
    v = os.environ.get(job.env, "").strip()
    return v if v in {c[0] for c in job.choices} else job.default


def ready(job: Job, choice: str) -> str:
    """Why this choice can't run here, or "" when it can (a choice that can't is still kept, and said)."""
    try:
        if choice == "local" and job.id == "painter":
            from ..motion import painter
            return "" if painter.available() else "needs an NVIDIA GPU or Apple Silicon with the paint extra"
        if choice in ("local", "cartoon", "anime") and job.id in ("clips", "cartoons"):
            from ..motion import videogen
            return "" if videogen.available() else "needs an NVIDIA GPU with the video extra"
        if choice == "local" and job.id == "music":
            from ..music import maker
            return "" if maker.can_make_music() else "needs a GPU with the sound extra"
        if choice == "rig":
            from ..motion import blender
            return "" if blender.find() else "needs Blender (Connect, Tools)"
    except Exception:  # noqa: BLE001 - a check that breaks says nothing rather than blocking the choice
        return ""
    key = {"gemini": "GEMINI_API_KEY", "higgsfield": "HIGGSFIELD_API_KEY", "elevenlabs": "ELEVENLABS_API_KEY"}.get(choice)
    if key and not os.environ.get(key, "").strip():  # (saved keys are loaded into the environment at start)
        return "needs its key (Connect, Keys)"
    return ""


def put(job: Job, choice: str) -> None:
    """Make the choice live in this process: "auto" clears the variable, so the code picks."""
    if choice == "auto":
        os.environ.pop(job.env, None)
    else:
        os.environ[job.env] = choice
    if job.id == "talk":
        from . import voice
        voice.MODEL = choice


def apply(chosen: dict | None) -> None:
    """At start: the choices kept in studio.yaml, unless the environment already says otherwise."""
    for jid, choice in (chosen or {}).items():
        job = BY_ID.get(jid)
        if job and choice in {c[0] for c in job.choices} and not os.environ.get(job.env):
            put(job, choice)


def listing() -> list[dict]:
    """Every job with its choices, what does it now, and why a choice can't run here."""
    out = []
    for job in JOBS:
        now = current(job)
        out.append({"id": job.id, "label": job.label, "says": job.says, "now": now, "note": job.note,
                    "choices": [{"id": c, "name": n, "says": d, "why_not": ready(job, c)} for c, n, d in job.choices]})
    return out
