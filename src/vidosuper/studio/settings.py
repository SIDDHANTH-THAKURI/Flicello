"""Studio settings: where its data lives, the limits that keep a trial cheap and safe, the brain, alerts.

Defaults < `<home>/studio.yaml` < environment variables (VIDO_STUDIO_*). Secrets never live here: the API
key comes from ANTHROPIC_API_KEY, the session secret is generated into `<home>/secret.key`.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

BrainKind = Literal["api", "claude_code", "local"]
GB = 1024 ** 3
MB = 1024 ** 2


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Tier(_Strict):
    """What one access level may do (see auth.ACCESS). `videos` None: the trial's videos_per_user."""
    videos: int | None = None
    max_seconds: int = Field(60, description="The longest film from clips a person may ask for.")
    max_recording: int = Field(180, description="The longest recording a talking or voice video may use, in seconds.")
    beats: int = Field(3, description="Tracks ElevenLabs may make for one film (each try costs about $0.15 a minute "
                                      "of music).")
    credits: int = Field(3, description="Credits each calendar month; a film uses them by its length (Pricing). The "
                                        "owner can change this on the admin page.")


def _tiers() -> dict[str, Tier]:
    return {"beta": Tier(), "special": Tier(videos=10, max_seconds=600, max_recording=600, beats=6, credits=12)}


class Pricing(_Strict):
    """What a film uses, in credits (the owner can change it on the admin page). A credit is about a minute of film:
    the AI's cost grows with the footage and the length, and long films pay their own way."""
    per_minute: int = Field(1, description="Credits per started minute of the film (or of the recording a voice or "
                                           "talking video is made from).")
    looks: int = Field(1, description="Extra credits when the video's look is painted (voice and designed talking "
                                      "videos: every scene is an AI picture).")


class Limits(_Strict):
    videos_per_user: int = Field(3, description="Finished videos a friend may make (the owner is not limited).")
    revisions_per_video: int = 2
    clips_per_project: int = 20
    clip_bytes: int = 2 * GB
    project_bytes: int = 2 * GB
    user_bytes: int = 6 * GB
    total_bytes: int = 60 * GB  # all studio data together: uploads stop above this
    clip_seconds: float = 600.0
    project_seconds: float = 1200.0  # footage per project (analysis cost grows with it)
    song_bytes: int = 60 * MB
    song_seconds: float = 480.0
    chunk_bytes: int = 8 * MB
    retention_days: int = 14  # a project untouched this long is deleted with its footage
    min_free_gb: float = 15.0  # below this much free disk, uploads and new jobs wait
    monthly_usd: float = 60.0  # spend cap per calendar month, everyone's videos together (real money only)
    video_usd: float = 6.0  # spend cap for one video, revisions included (AI, music, painted looks, cloud rendering)
    person_day_usd: float = 3.0  # what one person's videos may spend in any 24 hours (the owner is not limited)
    active_jobs_per_user: int = 1
    login_attempts: int = 8  # per 15 minutes per account and per address


class Models(_Strict):
    analyst: str = "claude-sonnet-5-5"  # logs every clip from its contact sheets
    director: str = "claude-opus-5-5"  # pitches the stories
    editor: str = "claude-opus-5-5"  # designs and revises the cut
    coach: str = "claude-haiku-4-5-20251001"  # turns feedback into style rules
    images: str = "gemini-nano-banana-2.1"  # paints the looks' previews and the backdrops (GEMINI_API_KEY)


# USD per million tokens: input, output, cache write (5 min), cache read
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.2),
    "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.1),
    "claude-haiku-4-5": (1.0, 5.0, 1.25, 0.1),
}


def price_for(model: str) -> tuple[float, float, float, float]:
    for key, value in PRICES.items():
        if model.startswith(key):
            return value
    return PRICES["claude-opus-5-5"]  # unknown model: assume the dearest, never under-count


class StudioSettings(_Strict):
    home: Path
    name: str = "Flicello"
    brain: BrainKind = Field("api", description="The brain for invited people's videos.")
    owner_brain: BrainKind = Field("claude_code", description="The brain for the owner's own videos (their Claude plan).")
    models: Models = Field(default_factory=Models)
    limits: Limits = Field(default_factory=Limits)
    host: str = "127.0.0.1"
    port: int = 8765
    https: bool = Field(False, description="Served over HTTPS (e.g. tailscale serve): cookies are marked Secure.")
    proxy_net: str | None = Field(None, description="Network our own reverse proxy connects from (e.g. the Docker "
                                  "network of Caddy on the cloud server): its X-Forwarded-For is believed too.")
    workers: int = Field(1, description="Jobs run at once. Renders are heavy: keep 1 on a laptop.")
    jobs: Literal["local", "remote"] = Field("local", description="Who runs the video jobs: this machine's worker, or "
                                                                  "remote workers (the owner's PC, later Modal) that "
                                                                  "claim them over /api/worker (see remote.py).")
    render_preset: str = "medium"  # x264 preset for finished videos (slow is ~2x slower for a little quality)
    alert_url: str | None = Field(None, description="Alerts are POSTed here as plain text (e.g. an ntfy.sh topic). "
                                                    "Only short messages, never footage or names of files.")
    session_days: int = Field(60, description="A sign-in lasts this long and is renewed whenever the person uses the "
                                               "site, so people who come back stay signed in.")
    session_max_days: int = Field(365, description="However active, a sign-in ends after this long.")
    invite_days: int = 7
    public_signup: bool = Field(False, description="Anyone may make an account (Google, email code); otherwise only "
                                                   "invite links work.")
    beta: bool = Field(False, description="Closed beta: anyone may make an account, but it can only make videos once "
                                          "the owner approves its request (admin page). Invited and allow-listed "
                                          "people are approved at once.")
    tiers: dict[str, Tier] = Field(default_factory=_tiers, description="What each access level may do: 'beta' (an "
                                                                       "approved person), 'special' (long videos).")
    pricing: Pricing = Field(default_factory=Pricing)
    site_url: str | None = Field(None, description="The public address (https://flicello.com): Google sign-in's "
                                                   "return address, passkeys' domain, links in emails.")
    allowed_emails: list[str] = Field(default_factory=list, description=(
        "When set, only these emails may sign in or make an account with Google or an email code (plus the owner and "
        "people the owner invited); everyone else is told it is by invitation. Env: comma-separated."))
    signups_per_day: int = Field(100, description="New accounts a day; past it, people join a waiting list.")
    mail_from: str = "Flicello <hello@flicello.com>"
    mail_outbox: bool = Field(False, description="Write emails to <home>/outbox instead of sending them (local tests).")
    improve_every: int = Field(5, description="Start the improvement routine after this many new ratings.")
    render_processes: int = Field(2, description="Processes that draw a designed talking video at once. Measured on "
                                                 "the owner's laptop (i5-13500H, Iris Xe, cut-out on the GPU): 1 -> 3.3, "
                                                 "2 -> 4.8, 3 -> 2.3 frames/s (three fight over the GPU and the cores).")
    modal_url: str | None = Field(None, description="The cloud renderer's wake address: the `wake` endpoint of "
                                                    "deploy/modal_app.py (Modal). Unset: no cloud renderer.")
    modal_mode: Literal["off", "auto", "always"] = Field("auto", description=(
        "auto: the cloud takes what the PC can't (everything while the PC is offline, a step that has waited "
        "`modal_wait` seconds while it is busy); always: every step the cloud can run goes there at once; off."))
    modal_wait: int = Field(45, description="auto: how long a step waits for a busy PC before the cloud takes it.")
    modal_max: int = Field(3, description="Cloud renderers at once (each costs about $0.50 an hour while it works).")
    improve_deploy: bool = Field(False, description="Let a finished improvement branch go live by itself when the tests "
                                                    "pass, nothing is running and it touches no protected file (deploy.py); "
                                                    "undone by itself if the site does not come back.")
    improve_auto: bool = Field(False, description="Let the studio start the improvement routine (scripts/improve.ps1) "
                                                  "by itself when improve_every ratings have come in.")

    @property
    def db_path(self) -> Path:
        return self.home / "studio.db"

    @property
    def workspace(self) -> Path:
        """The pipeline's working files (proxies, measurements, renders) for every studio project."""
        return self.home / "workspace"

    @property
    def models_dir(self) -> Path:
        """Downloaded models (Whisper): shared with the command line's on this PC, else in the studio home."""
        if env := os.environ.get("VIDO_MODELS_DIR"):
            return Path(env)
        if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
            return Path(os.environ["LOCALAPPDATA"]) / "VidoSuper" / "models"
        return self.home / "models"

    @property
    def users_dir(self) -> Path:
        return self.home / "users"

    @property
    def uploads_dir(self) -> Path:
        return self.home / "uploads"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    def secret_key(self) -> bytes:
        """A random key made once and kept in the data folder (never in the repository)."""
        path = self.home / "secret.key"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(secrets.token_hex(32), encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return bytes.fromhex(path.read_text(encoding="utf-8").strip())

    def ensure(self) -> "StudioSettings":
        for folder in (self.home, self.workspace, self.users_dir, self.uploads_dir, self.logs_dir):
            folder.mkdir(parents=True, exist_ok=True)
        return self


def default_home() -> Path:
    if env := os.environ.get("VIDO_STUDIO_HOME"):
        return Path(env).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "VidoSuper" / "studio"
    return Path.home() / ".vidosuper" / "studio"


_ENV = {  # environment variable -> setting (strings are parsed by pydantic)
    "VIDO_STUDIO_BRAIN": "brain",
    "VIDO_STUDIO_OWNER_BRAIN": "owner_brain",
    "VIDO_STUDIO_HOST": "host",
    "VIDO_STUDIO_PORT": "port",
    "VIDO_STUDIO_HTTPS": "https",
    "VIDO_STUDIO_PROXY_NET": "proxy_net",
    "VIDO_STUDIO_PUBLIC_SIGNUP": "public_signup",
    "VIDO_STUDIO_BETA": "beta",
    "VIDO_STUDIO_SITE_URL": "site_url",
    "VIDO_STUDIO_MAIL_OUTBOX": "mail_outbox",
    "VIDO_STUDIO_WORKERS": "workers",
    "VIDO_STUDIO_JOBS": "jobs",
    "VIDO_STUDIO_ALERT_URL": "alert_url",
    "VIDO_STUDIO_MODAL_URL": "modal_url",
    "VIDO_STUDIO_MODAL": "modal_mode",
    "VIDO_STUDIO_RENDER_PROCESSES": "render_processes",
}


def load_settings(home: Path | None = None, overrides: dict | None = None) -> StudioSettings:
    from ..music.sources import _load_dotenv

    _load_dotenv()  # the repo's .env (git-ignored): ANTHROPIC_API_KEY, VIDO_STUDIO_* ...; real environment wins
    home = Path(home) if home else default_home()
    data: dict = {}
    path = home / "studio.yaml"
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"{path} must contain a mapping")
        data.update(loaded)
    for env, key in _ENV.items():
        if os.environ.get(env):
            data[key] = os.environ[env]
    if os.environ.get("VIDO_STUDIO_ALLOWED_EMAILS"):
        data["allowed_emails"] = [e.strip().lower() for e in os.environ["VIDO_STUDIO_ALLOWED_EMAILS"].split(",")
                                  if e.strip()]
    if os.environ.get("VIDO_STUDIO_MONTHLY_USD"):
        data.setdefault("limits", {})["monthly_usd"] = float(os.environ["VIDO_STUDIO_MONTHLY_USD"])
    data.update(overrides or {})
    data["home"] = home
    return StudioSettings.model_validate(data)


@lru_cache(maxsize=1)
def settings() -> StudioSettings:
    return load_settings().ensure()
