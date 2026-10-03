"""Per-project working folders (outside OneDrive) and JSON persistence helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from .config import Config
from .schemas import ClipInfo, ClipManifest, ClipSignals


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_model(path: Path, model: BaseModel) -> None:
    write_text_atomic(path, model.model_dump_json(indent=2))


class Project:
    """All derived files for one clips folder live under <workspace>/projects/<slug>-<hash>/."""

    def __init__(self, clips_dir: str | Path, cfg: Config):
        self.clips_dir = Path(clips_dir).expanduser().resolve()
        self.cfg = cfg
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", self.clips_dir.name).strip("-")[:40] or "clips"
        digest = hashlib.sha1(str(self.clips_dir).lower().encode("utf-8")).hexdigest()[:8]
        self.root = cfg.workspace_root / "projects" / f"{slug}-{digest}"

    # folders -----------------------------------------------------------------
    @property
    def proxies(self) -> Path:
        return self.root / "proxies"

    @property
    def audio(self) -> Path:
        return self.root / "audio"

    @property
    def signals(self) -> Path:
        return self.root / "signals"

    @property
    def segments(self) -> Path:
        return self.root / "segments"

    @property
    def renders(self) -> Path:
        return self.root / "renders"

    @property
    def edls(self) -> Path:
        return self.root / "edl"

    @property
    def music(self) -> Path:
        return self.root / "music"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def manifest_path(self) -> Path:
        return self.root / "clips.json"

    def ensure(self) -> "Project":
        for folder in (self.proxies, self.audio, self.signals, self.segments,
                       self.renders, self.edls, self.music, self.tmp):
            folder.mkdir(parents=True, exist_ok=True)
        info = self.root / "project.json"
        if not info.exists():
            write_text_atomic(info, json.dumps({"clips_dir": str(self.clips_dir)}, indent=2))
        return self

    # manifest ----------------------------------------------------------------
    def load_manifest(self) -> ClipManifest | None:
        if not self.manifest_path.exists():
            return None
        return ClipManifest.model_validate_json(self.manifest_path.read_text(encoding="utf-8"))

    def save_manifest(self, clips: list[ClipInfo]) -> ClipManifest:
        manifest = ClipManifest(clips_dir=str(self.clips_dir), updated=datetime.now(timezone.utc), clips=clips)
        write_model(self.manifest_path, manifest)
        return manifest

    def clips(self) -> dict[str, ClipInfo]:
        manifest = self.load_manifest()
        if manifest is None:
            raise FileNotFoundError(f"No clips ingested yet for {self.clips_dir}. Run: vido analyze \"{self.clips_dir}\"")
        return {c.clip_id: c for c in manifest.clips}

    # measurements --------------------------------------------------------------
    def signals_json(self, clip_id: str) -> Path:
        return self.signals / f"{clip_id}.json"

    def signals_npz(self, clip_id: str) -> Path:
        return self.signals / f"{clip_id}.npz"

    def load_signals(self, clip_id: str) -> ClipSignals | None:
        path = self.signals_json(clip_id)
        if not path.exists():
            return None
        return ClipSignals.model_validate_json(path.read_text(encoding="utf-8"))
