"""Configuration: config/default.yaml + optional user overrides + per-run overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "default.yaml"
USER_CONFIG = Path.home() / ".vidosuper" / "config.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentsCfg(_Strict):
    clip_analyst: str = "sonnet"
    director: str = "opus"
    editor: str = "opus"
    critics: str = "sonnet"
    adversary: str = "opus"
    judge: str = "opus"


class LoopCfg(_Strict):
    max_rounds: int = 5
    target_score: float = 8.5
    max_blockers: int = 0
    max_major_issues: int = 1
    stop_after_no_gain: int = 2
    issues_per_round: int = 6
    checkpoints: list[int] = Field(default_factory=lambda: [1])


class LimitsCfg(_Strict):
    max_minutes: float = 180
    max_usd: float = 15


class OutputCfg(_Strict):
    platform: Literal["youtube", "reels", "shorts", "tiktok", "square"] = "youtube"
    resolution: Literal["1080p", "4k"] = "1080p"
    fps: Literal["auto", 24, 25, 30, 50, 60] = "auto"
    loudness_lufs: float = -14.0
    true_peak_db: float = -1.0
    captions: Literal["auto", "on", "off"] = "auto"


class IngestCfg(_Strict):
    extensions: list[str] = Field(
        default_factory=lambda: [".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".3gp", ".webm"]
    )
    proxy_long_edge: int = 640
    proxy_fps: int = 30
    workers: int = 2
    hwaccel: Literal["auto", "none"] = "auto"  # auto = Intel QuickSync when available, else software
    chapter_gap_hours: float = 3.0
    chapter_distance_km: float = 15.0


class MeasureCfg(_Strict):
    analysis_fps: float = 10.0
    analysis_width: int = 320
    workers: int = 4
    window_seconds: float = 3.0


class DraftCfg(_Strict):
    short_side: int = 540
    preset: str = "ultrafast"
    crf: int = 23


class FinalCfg(_Strict):
    encoder: Literal["libx264", "h264_qsv"] = "libx264"
    preset: str = "slow"
    crf: int = 18
    intermediate_crf: int = 12


class RenderCfg(_Strict):
    workers: int = 3
    draft: DraftCfg = Field(default_factory=DraftCfg)
    final: FinalCfg = Field(default_factory=FinalCfg)
    audio_bitrate: str = "256k"
    blur_pad_sigma: float = 30.0
    music_duck_db: float = -12.0


class AnalysisCfg(_Strict):
    frames_per_second: float = 0.5
    whisper_model: str = "small"


class Config(_Strict):
    mode: Literal["claude_code", "api"] = "claude_code"
    workspace: str = "auto"
    agents: AgentsCfg = Field(default_factory=AgentsCfg)
    loop: LoopCfg = Field(default_factory=LoopCfg)
    limits: LimitsCfg = Field(default_factory=LimitsCfg)
    critic_weights: dict[str, float] = Field(default_factory=dict)
    output: OutputCfg = Field(default_factory=OutputCfg)
    ingest: IngestCfg = Field(default_factory=IngestCfg)
    measure: MeasureCfg = Field(default_factory=MeasureCfg)
    render: RenderCfg = Field(default_factory=RenderCfg)
    analysis: AnalysisCfg = Field(default_factory=AnalysisCfg)

    @property
    def workspace_root(self) -> Path:
        if self.workspace != "auto":
            return Path(self.workspace).expanduser()
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "VidoSuper"


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    return data


def load_config(overrides: dict[str, Any] | None = None) -> Config:
    """Load defaults, then the user's personal config, then per-run overrides."""
    path = Path(os.environ.get("VIDO_CONFIG", DEFAULT_CONFIG))
    data = _read_yaml(path)
    data = _deep_merge(data, _read_yaml(USER_CONFIG))
    if overrides:
        data = _deep_merge(data, overrides)
    return Config.model_validate(data)
