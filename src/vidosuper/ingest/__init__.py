"""Ingest: scan a clips folder, probe every file, build proxies + audio, group into chapters."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..config import Config
from ..schemas import ClipInfo
from ..workspace import Project
from .chapters import assign_chapters, chronological
from .probe import probe_clip
from .proxy import build_proxy
from .scan import fingerprint, make_clip_id, scan_folder

ProgressFn = Callable[[str], None]


def _outputs(project: Project, clip_id: str) -> tuple[Path, Path, Path]:
    return (
        project.proxies / f"{clip_id}.mp4",
        project.audio / f"{clip_id}.48k.wav",
        project.audio / f"{clip_id}.16k.wav",
    )


def _is_complete(clip: ClipInfo) -> bool:
    if clip.error or not clip.proxy or not Path(clip.proxy).exists():
        return False
    return not clip.has_audio or bool(clip.audio_wav and Path(clip.audio_wav).exists())


def _name_rank(path: Path) -> tuple[bool, int, str]:
    """Among identical files, prefer one not named like a copy, then the shortest name."""
    return "copy" in path.stem.lower(), len(path.name), path.name


def ingest_folder(
    clips_dir: str | Path,
    cfg: Config,
    *,
    force: bool = False,
    on_clip: ProgressFn | None = None,
) -> tuple[Project, list[ClipInfo]]:
    project = Project(clips_dir, cfg).ensure()
    if not project.clips_dir.is_dir():
        raise FileNotFoundError(f"Clips folder not found: {project.clips_dir}")
    files = scan_folder(project.clips_dir, cfg.ingest.extensions)

    manifest = project.load_manifest()
    previous = {c.fingerprint: c for c in (manifest.clips if manifest else [])}
    taken: set[str] = set()
    ready: list[ClipInfo] = []
    todo: list[tuple[Path, str, str]] = []

    # the same footage twice (e.g. "clip - Copy.MP4") is one clip: keep the original-looking name
    prints = {path: fingerprint(path) for path in files}
    chosen: dict[str, Path] = {}
    for path, fp in prints.items():
        if fp not in chosen or _name_rank(path) < _name_rank(chosen[fp]):
            chosen[fp] = path

    for path in files:
        fp = prints[path]
        if chosen[fp] != path:
            continue
        rel = str(path.relative_to(project.clips_dir))
        old = previous.get(fp)
        if old and not force and _is_complete(old):
            old.path, old.rel_path = str(path), rel  # the file may have been moved or renamed
            taken.add(old.clip_id)
            ready.append(old)
            continue
        clip_id = old.clip_id if old else make_clip_id(path, fp, taken)
        taken.add(clip_id)
        todo.append((path, fp, clip_id))

    def work(item: tuple[Path, str, str]) -> ClipInfo:
        path, fp, clip_id = item
        rel = str(path.relative_to(project.clips_dir))
        try:
            clip = probe_clip(path, clip_id=clip_id, rel_path=rel, fp=fp)
            video_out, wav48, wav16 = _outputs(project, clip_id)
            # outputs are only renamed into place once complete, so existing ones survive an interrupted run
            finished = video_out.exists() and (not clip.has_audio or (wav48.exists() and wav16.exists()))
            if force or not finished:
                build_proxy(clip, video_out, wav48, wav16, cfg)
            clip.proxy = str(video_out)
            if clip.has_audio:
                clip.audio_wav, clip.audio_wav16k = str(wav48), str(wav16)
        except Exception as exc:  # one bad file must not stop the whole trip
            clip = ClipInfo(clip_id=clip_id, path=str(path), rel_path=rel, fingerprint=fp,
                            error=f"{type(exc).__name__}: {str(exc).strip().splitlines()[0][:300]}")
        if on_clip:
            on_clip(clip.rel_path)
        return clip

    with ThreadPoolExecutor(max_workers=max(1, cfg.ingest.workers)) as pool:
        ready.extend(pool.map(work, todo))

    assign_chapters(ready, cfg.ingest.chapter_gap_hours, cfg.ingest.chapter_distance_km)
    clips = chronological([c for c in ready if c.usable]) + [c for c in ready if not c.usable]
    project.save_manifest(clips)
    return project, clips


__all__ = ["ingest_folder", "scan_folder", "probe_clip", "build_proxy", "assign_chapters"]
