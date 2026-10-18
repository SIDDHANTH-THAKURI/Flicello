"""Leftovers on the owner's PC from before flicello.com moved to a server (the PC-hosted studio, 12-16 Oct 2026), kept
to the same promise as the site: other people's footage is deleted once untouched for the retention time. The PC
renderer runs this with its tidy-up; on any other machine there is nothing here and it does nothing.

What goes, once untouched that long (newest file older than the retention time):
- each friend's project in the old studio (users/<uid>/projects/<pid>), with the old studio's working copy of it
  (workspace/projects/clips-*, linked by its clips folder), and the friend's folder once it holds no projects;
- the story engine's working copies of a talk (%LOCALAPPDATA%/VidoSuper/story), once the talk they were made from is
  gone or untouched that long (story/work/source.json names it; tools/prepare.py touches it while it is in use);
- the earlier attempts' scratch copies of that talk (%TEMP%/wow: app, story, shots, designed-*, the samples).
The owner's own projects stay: the owner decides about those.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)
DAY = 86400.0


def local_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "VidoSuper"


def newest(path: Path) -> float:
    """The newest modification time of the files at or under `path` (0 when there are none). Files only: a folder's
    own time changes when something in it is deleted, which is not someone using it."""
    try:
        if path.is_file():
            return path.stat().st_mtime
    except OSError:
        return 0.0
    latest = 0.0
    if path.is_dir():
        for f in path.rglob("*"):
            try:
                if f.is_file():
                    latest = max(latest, f.stat().st_mtime)
            except OSError:
                pass
    return latest


def _owners(db: Path) -> set[str] | None:
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            return {r[0] for r in con.execute("SELECT id FROM users WHERE role='owner'")}
        finally:
            con.close()
    except sqlite3.Error:
        return None


def _gone(path: Path, removed: list[str], label: str) -> None:
    shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)
    removed.append(label)


def sweep(root: Path | None = None, days: int = 14, now: float | None = None, scratch: Path | None = None) -> list[str]:
    """Delete what is past the retention time (see above). Returns what went, for the log."""
    root = root or local_root()
    now = time.time() if now is None else now
    old = now - days * DAY
    removed: list[str] = []
    studio = root / "studio"
    owners = _owners(studio / "studio.db") if (studio / "studio.db").is_file() else None
    if owners:  # (without knowing whose is whose, nothing is touched)
        workspaces = {}
        for ws in (studio / "workspace" / "projects").glob("clips-*"):
            try:
                clips = json.loads((ws / "project.json").read_text(encoding="utf-8")).get("clips_dir") or ""
            except (OSError, ValueError):
                continue
            workspaces[Path(clips).parent.name] = ws
        for user in (studio / "users").glob("u*"):
            if user.name in owners or not user.is_dir():
                continue
            for project in (user / "projects").glob("p*"):
                ws = workspaces.get(project.name)
                if max(newest(project), newest(ws) if ws else 0.0) < old:
                    if ws:
                        _gone(ws, removed, f"old studio workspace {ws.name}")
                    _gone(project, removed, f"old studio project {user.name}/{project.name}")
            if not any((user / "projects").glob("p*")) and newest(user) < old:
                _gone(user, removed, f"old studio account folder {user.name}")
    story = root / "story"
    try:
        source = json.loads((story / "work" / "source.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        source = None
    if source:
        talk = Path(source.get("talk", ""))
        if newest(talk) < old and newest(story / "public") < old:
            for name in ("narrator.mp4", "narrator-alpha.webm", "voice.wav", "heard.json", "words.json",
                         "levels.json", "cuts.json"):
                if (story / "public" / name).exists():
                    _gone(story / "public" / name, removed, f"story copy {name}")
            for folder in ("work", "out"):
                if (story / folder).exists():
                    _gone(story / folder, removed, f"story {folder}")
            scratch = scratch or Path(tempfile.gettempdir()) / "wow"
            if scratch.is_dir() and newest(scratch) < old:
                for item in [*(scratch / n for n in ("app", "story", "shots")), *scratch.glob("designed-*"),
                             *scratch.glob("*.mp4")]:
                    if item.exists():
                        _gone(item, removed, f"scratch {item.name}")
    for what in removed:
        log.info("retention: deleted %s", what)
    return removed
