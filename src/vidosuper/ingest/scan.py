"""Finding video files and giving each a stable identity."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_CHUNK = 1 << 20  # 1 MiB


def scan_folder(folder: Path, extensions: list[str]) -> list[Path]:
    exts = {e.lower() for e in extensions}
    found = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in exts:
            continue
        if path.name.startswith("._") or any(part.startswith(".") for part in path.relative_to(folder).parts[:-1]):
            continue
        if path.stat().st_size == 0:
            continue
        found.append(path)
    return found


def fingerprint(path: Path) -> str:
    """Cheap content identity: size + first and last MiB. Survives renames and moves."""
    size = path.stat().st_size
    digest = hashlib.sha1(str(size).encode())
    with path.open("rb") as fh:
        digest.update(fh.read(_CHUNK))
        if size > 2 * _CHUNK:
            fh.seek(-_CHUNK, 2)
            digest.update(fh.read(_CHUNK))
    return digest.hexdigest()


def make_clip_id(path: Path, fp: str, taken: set[str]) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", path.stem).strip("_")[:32] or "clip"
    for length in (4, 6, 8, 12, 40):
        candidate = f"{stem}-{fp[:length]}"
        if candidate not in taken:
            return candidate
    raise RuntimeError(f"Could not make a unique clip id for {path}")
