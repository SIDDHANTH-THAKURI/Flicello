"""Studio settings: where its data lives, the limits that keep its spending and its disk in check, the brain,
alerts.

Defaults < `<home>/studio.yaml` < environment variables (VIDO_STUDIO_*). Keys never live here: they are kept in the
operating system's keychain (local/keys.py, the Connect page), or come from the environment.
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
    """What a film may be (service.tier). `videos` None: no limit."""
    videos: int | None = None
    max_seconds: int = Field(60, description="The longest film from clips.")
    max_recording: int = Field(180, description="The longest recording a talking or voice video may use, in seconds.")
    beats: int = Field(3, description="Tracks ElevenLabs may make for one film.")


class Limits(_Strict):
    videos_per_user: int = Field(3, description="Finished videos a friend may make (the owner is not limited).")
    revisions_per_video: int = 2
    clips_per_project: int = 20
    clip_bytes: int = 5 * GB
    project_bytes: int = 5 * GB
    user_bytes: int = 15 * GB  # three full films' footage
    total_bytes: int = 60 * GB  # all studio data together: uploads stop above this
    clip_seconds: float = 600.0
    project_seconds: float = 1200.0  # footage per project (analysis cost grows with it)
    song_bytes: int = 60 * MB
    song_seconds: float = 480.0
    chunk_bytes: int = 8 * MB
    retention_days: int = 14  # a project untouched this long is deleted with its footage
    min_free_gb: float = 15.0  # below this much free disk, uploads and new jobs wait
    monthly_usd: float = 60.0  # spend cap per calendar month on their own keys (the Claude plan isn't counted)
    video_usd: float = 6.0  # spend cap for one video, revisions included (AI, music, painted looks)
    person_day_usd: float = 3.0  # what may be spent in any 24 hours
    active_jobs_per_user: int = 1


class Models(_Strict):
    analyst: str = "claude-sonnet-5-5"  # logs every clip from its contact sheets
    director: str = "claude-opus-5-5"  # pitches the stories
    editor: str = "claude-opus-5-5"  # designs and revises the cut
    coach: str = "claude-haiku-4-5-20251001"  # turns feedback into style rules
    assistant: str = "claude-opus-5-5"  # your editor: talks with the person through the whole film (agent.py)
    watcher: str = "gemini-pro-latest"  # watches each clip with its sound (brain/gemini.py); "" = contact sheets only
    images: str = "gemini-nano-banana-2.1"  # paints the looks' previews and the backdrops (GEMINI_API_KEY)


# USD per million tokens: input, output, cache write (5 min), cache read
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.2),
    "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.1),
    "claude-haiku-4-5": (1.0, 5.0, 1.25, 0.1),
    # Gemini (approximate list prices; video and audio count as input tokens)
    "gemini-pro": (2.0, 12.0, 0.0, 0.0),
    "gemini-3.1-pro": (2.0, 12.0, 0.0, 0.0),
    "gemini-3.8-flash": (0.5, 3.0, 0.0, 0.0),
    "gemini-3.7-flash": (0.5, 3.0, 0.0, 0.0),
}


def price_for(model: str) -> tuple[float, float, float, float]:
    for key, value in PRICES.items():
        if model.startswith(key):
            return value
    return PRICES["claude-opus-5-5"]  # unknown model: assume the dearest, never under-count


class StudioSettings(_Strict):
    home: Path
    name: str = "Flicello"
    brain: BrainKind = Field("claude_code", description="The brain for steps that don't name their own.")
    owner_brain: BrainKind = Field("claude_code", description="The brain: their Claude Code plan (claude_code), or the "
                                                              "Anthropic API with their key (api).")
    models: Models = Field(default_factory=Models)
    limits: Limits = Field(default_factory=Limits)
    host: str = "127.0.0.1"
    port: int = 8765
    workers: int = Field(1, description="Jobs run at once.")
    render_preset: str = "medium"  # x264 preset for finished videos (slow is ~2x slower for a little quality)
    alert_url: str | None = Field(None, description="Alerts are POSTed here as plain text (e.g. an ntfy.sh topic). "
                                                    "Only short messages, never footage or names of files.")
    session_days: int = Field(60, description="The browser's session lasts this long and is renewed as it is used.")
    session_max_days: int = Field(365, description="However active, a session ends after this long.")
    improve_every: int = Field(5, description="Start the improvement routine after this many new ratings.")
    render_processes: int = Field(4, description="Processes that draw a designed talking video at once (each "
                                                 "takes a CPU core and a share of the GPU).")
    improve_auto: bool = Field(False, description="Let the studio start the improvement routine (scripts/improve.ps1) "
                                                  "by itself when improve_every ratings have come in.")
    blender_path: str = Field("", description="Blender's program, for 3D scenes (local; empty: looked for).")

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
    "VIDO_STUDIO_WORKERS": "workers",
    "VIDO_STUDIO_ALERT_URL": "alert_url",
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
    if os.environ.get("VIDO_STUDIO_MONTHLY_USD"):
        data.setdefault("limits", {})["monthly_usd"] = float(os.environ["VIDO_STUDIO_MONTHLY_USD"])
    data.update(overrides or {})
    data["home"] = home
    gone = [k for k in data if k not in StudioSettings.model_fields]  # settings of the old hosted studio
    for k in gone:
        data.pop(k)
    if isinstance(data.get("limits"), dict):
        data["limits"] = {k: v for k, v in data["limits"].items() if k in Limits.model_fields}
    return StudioSettings.model_validate(data)


@lru_cache(maxsize=1)
def settings() -> StudioSettings:
    return load_settings().ensure()
