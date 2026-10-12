"""Start the improvement routine (docs/improvement.md) once enough new ratings have come in.

The trigger is the number of finished-video ratings since the last digest. At `improve_every` (5) the studio starts
scripts/improve.ps1 in the background, which reads the digest and works in its own git worktree on a branch: the live
studio's files are never touched, nothing is pushed to main, and the owner is told what it did. Off unless
`improve_auto: true` is in studio.yaml.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

from ..config import REPO_ROOT

log = logging.getLogger("vidosuper.studio")
LOCK_HOURS = 3.0  # a run that long ago is dead or stuck: a new one may start


def due(db) -> int:
    """Final ratings since the last digest (the digest moves the mark)."""
    mark = db.value("SELECT at FROM heartbeat WHERE name='digest'", (), 0.0) or 0.0
    return int(db.value("SELECT COUNT(*) FROM feedback WHERE stage='final' AND created > ?", (mark,), 0))


def lock_path(home: Path) -> Path:
    return Path(home) / "improve" / "running.lock"


def running(home: Path) -> bool:
    lock = lock_path(home)
    return lock.exists() and time.time() - lock.stat().st_mtime < LOCK_HOURS * 3600


def maybe_start(studio) -> bool:
    """Called after every rating. True when a run was started. Never raises: a rating must never fail because of this."""
    try:
        s = studio.s
        if not s.improve_auto or os.name != "nt":
            return False
        count = due(studio.db)
        if count < s.improve_every or running(s.home):
            return False
        script = REPO_ROOT / "scripts" / "improve.ps1"
        if not script.is_file():
            return False
        lock = lock_path(s.home)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        log_file = lock.parent / f"run-{time.strftime('%Y-%m-%d-%H%M')}.log"
        with log_file.open("w", encoding="utf-8") as out:
            subprocess.Popen(  # noqa: S603 - our own script, a fixed command
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                cwd=str(REPO_ROOT), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        from .alerts import alert

        alert(studio.db, s, "improve", f"{count} new ratings: the improvement routine started (log: {log_file.name}).", "info")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("could not start the improvement routine: %s", exc)
        return False
