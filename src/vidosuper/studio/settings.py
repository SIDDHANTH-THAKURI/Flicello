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
    monthly_usd: float = 20.0  # AI spend cap per calendar month (real API money only)
    video_usd: float = 4.0  # AI spend cap for one video, revisions included
    active_jobs_per_user: int = 1
    login_attempts: int = 8  # per 15 minutes per account and per address


class Models(_Strict):
    analyst: str = "claude-sonnet-5-5"  # logs every clip from its contact sheets
    director: str = "claude-opus-5-5"  # pitches the stories
    editor: str = "claude-opus-5-5"  # designs and revises the cut
    coach: str = "claude-haiku-4-5-20251001"  # turns feedback into style rules


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
    name: str = "VidoSuper Studio"
    brain: BrainKind = Field("api", description="The brain for invited people's videos.")
    owner_brain: BrainKind = Field("claude_code", description="The brain for the owner's own videos (their Claude plan).")
    models: Models = Field(default_factory=Models)
    limits: Limits = Field(default_factory=Limits)
    host: str = "127.0.0.1"
    port: int = 8765
    https: bool = Field(False, description="Served over HTTPS (e.g. tailscale serve): cookies are marked Secure.")
    workers: int = Field(1, description="Jobs run at once. Renders are heavy: keep 1 on a laptop.")
    render_preset: str = "medium"  # x264 preset for finished videos (slow is ~2x slower for a little quality)
    alert_url: str | None = Field(None, description="Alerts are POSTed here as plain text (e.g. an ntfy.sh topic). "
                                                    "Only short messages, never footage or names of files.")
    session_days: int = 14
    invite_days: int = 7

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
    "VIDO_STUDIO_WORKERS": "workers",
    "VIDO_STUDIO_ALERT_URL": "alert_url",
}


def load_settings(home: Path | None = None, overrides: dict | None = None) -> StudioSettings:
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
    return StudioSettings.model_validate(data)


@lru_cache(maxsize=1)
def settings() -> StudioSettings:
    return load_settings().ensure()
