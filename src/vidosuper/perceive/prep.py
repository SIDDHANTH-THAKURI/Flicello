"""Phase 2 prep: for every clip, a transcript, timestamped contact sheets and a written brief,
grouped into batches that clip-analyst subagents work through."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config import Config
from ..schemas import ClipInfo, ClipSignals
from ..workspace import Project, write_model, write_text_atomic
from .contact_sheet import format_time, sheets_for_video
from .transcribe import TRANSCRIPT_VERSION, Transcript, transcribe

PREP_VERSION = 1
FRAMES_PER_SHEET = 16


@dataclass
class PreparedClip:
    clip_id: str
    duration: float
    every: float
    sheets: list[str]
    brief: str
    analysis: str  # where the analyst must write its JSON


def perception_dir(project: Project, clip_id: str) -> Path:
    return project.root / "perception" / clip_id


def transcript_path(project: Project, clip_id: str) -> Path:
    return project.root / "transcripts" / f"{clip_id}.json"


def analysis_path(project: Project, clip_id: str) -> Path:
    return perception_dir(project, clip_id) / "analysis.json"


def tasks_path(project: Project) -> Path:
    return project.root / "perception" / "tasks.json"


def sheet_spacing(duration: float, base_every: float) -> float:
    """At least ~8 frames even for short clips; one frame per `base_every` s for long ones; never under 0.5 s."""
    return round(max(0.5, min(base_every, duration / 8)), 2)


def load_transcript(project: Project, clip_id: str) -> Transcript | None:
    path = transcript_path(project, clip_id)
    if not path.exists():
        return None
    transcript = Transcript.model_validate_json(path.read_text(encoding="utf-8"))
    return transcript if transcript.version == TRANSCRIPT_VERSION else None


def ensure_transcript(project: Project, clip: ClipInfo, cfg: Config, force: bool = False) -> Transcript | None:
    if not clip.has_audio or not clip.audio_wav16k:
        return None
    if not force and (cached := load_transcript(project, clip.clip_id)) is not None:
        return cached
    transcript = transcribe(clip.clip_id, clip.audio_wav16k, cfg.analysis.whisper_model, cfg.models_root / "whisper")
    write_model(transcript_path(project, clip.clip_id), transcript)
    return transcript


def _local_time(clip: ClipInfo) -> str:
    if clip.creation_time is None:
        return "unknown"
    return clip.creation_time.astimezone().strftime("%Y-%m-%d %H:%M:%S (%z)")


def write_brief(clip: ClipInfo, signals: ClipSignals | None, transcript: Transcript | None,
                sheets: list[Path], every: float, analysis_file: Path) -> str:
    slowmo = ""
    if clip.fps >= 47:
        slowmo = f" -> real {clip.fps / 30:.0f}x slow motion possible on a 30 fps timeline"
    lines = [
        f"# Clip {clip.clip_id}",
        "",
        f"- File: {clip.rel_path} ({clip.duration:.1f} s, {clip.width}x{clip.height} {clip.orientation}, "
        f"{clip.fps:.2f} fps{slowmo})",
        f"- Recorded: {_local_time(clip)}; chapter {clip.chapter}" + (f"; device {clip.device}" if clip.device else ""),
        f"- Original audio: {'yes' if clip.has_audio else 'none'}",
        f"- Contact sheets: {len(sheets)} image(s), one frame every {every:.2f} s. The yellow label on each frame "
        "is the clip time (m:ss.s). Read them in order:",
    ]
    lines += [f"  {i + 1}. {path}" for i, path in enumerate(sheets)]
    lines += ["", "## Automatic measurements (from pixels; can be wrong, trust your eyes)"]
    if signals is None:
        lines.append("- not available")
    else:
        lines.append(f"- Technical quality {signals.quality_mean:.2f} (1 = flawless). Camera shake median "
                     f"{signals.shake_median:.3f} (0.05 noticeable, 0.25 very shaky). Brightness "
                     f"{signals.brightness_mean:.0f}/255. Subject motion median {signals.motion_median:.3f}.")
        bad = ", ".join(f"{b.start:.1f}-{b.end:.1f}s {b.reason}" for b in signals.bad_intervals) or "none"
        lines.append(f"- Problem ranges: {bad}")
        best = ", ".join(f"{w.start:.1f}-{w.end:.1f}s (quality {w.quality:.2f}, activity {w.interest:.2f})"
                         for w in signals.best_windows) or "none"
        lines.append(f"- Technically cleanest 3 s windows: {best}")
        if signals.audio_peaks:
            lines.append("- Sudden sounds (laugh, cheer, impact?) at: " + ", ".join(f"{p:.1f}s" for p in signals.audio_peaks))
        if signals.scene_changes:
            lines.append("- Big visual changes at: " + ", ".join(f"{s:.1f}s" for s in signals.scene_changes))
    lines += ["", "## Speech (Whisper, local; may mishear, lines marked unsure are probably noise)"]
    if transcript is None:
        lines.append("- no audio track")
    elif not transcript.utterances:
        lines.append("- no speech detected")
    else:
        lang = f" (language: {transcript.language})" if transcript.language else ""
        lines.append(f"- {len(transcript.utterances)} line(s){lang}:")
        lines += [f"  {line}" for line in transcript.as_lines()]
    lines += [
        "",
        "## Your output",
        f"Write the analysis JSON for this clip to: {analysis_file}",
        "Schema: run `./vido schema clip-analysis`. Times are clip seconds (convert m:ss.s labels).",
        f"Then validate: `./vido perceive check \"{Path(clip.path).parent}\" --clip {clip.clip_id}`",
    ]
    return "\n".join(lines) + "\n"


def make_batches(items: list[PreparedClip], max_sheets: int = 10, max_clips: int = 6) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    load = 0
    for item in items:
        n = len(item.sheets)
        if current and (load + n > max_sheets or len(current) >= max_clips):
            batches.append(current)
            current, load = [], 0
        current.append(item.clip_id)
        load += n
    if current:
        batches.append(current)
    return batches


def prepare_perception(project: Project, cfg: Config, *, force: bool = False,
                       on_clip: Callable[[str], None] | None = None) -> dict:
    clips = [c for c in project.clips().values() if c.usable]
    base_every = 1.0 / cfg.analysis.frames_per_second
    prepared: list[PreparedClip] = []
    for clip in clips:
        out = perception_dir(project, clip.clip_id)
        transcript = ensure_transcript(project, clip, cfg, force=force)
        every = sheet_spacing(clip.duration, base_every)
        existing = sorted(out.glob("sheet_*.jpg"))
        marker = out / "prep.json"
        stale = force or not existing or not marker.exists() or \
            json.loads(marker.read_text(encoding="utf-8")).get("version") != PREP_VERSION
        if stale:
            for old in existing:
                old.unlink()
            sheets = sheets_for_video(clip.proxy, clip.duration, out, "sheet", every=every,
                                      per_sheet=FRAMES_PER_SHEET, max_frames=96)
            write_text_atomic(marker, json.dumps({"version": PREP_VERSION, "every": every}))
        else:
            sheets = existing
        analysis_file = analysis_path(project, clip.clip_id)
        brief = out / "brief.md"
        write_text_atomic(brief, write_brief(clip, project.load_signals(clip.clip_id), transcript, sheets,
                                             every, analysis_file))
        prepared.append(PreparedClip(clip_id=clip.clip_id, duration=clip.duration, every=every,
                                     sheets=[str(s) for s in sheets], brief=str(brief), analysis=str(analysis_file)))
        if on_clip:
            on_clip(clip.rel_path)

    tasks = {
        "version": PREP_VERSION,
        "clips_dir": str(project.clips_dir),
        "clips": [asdict(p) for p in prepared],
        "batches": make_batches(prepared),
    }
    write_text_atomic(tasks_path(project), json.dumps(tasks, indent=2))
    return tasks


def format_tasks(tasks: dict) -> str:
    by_id = {c["clip_id"]: c for c in tasks["clips"]}
    out = [f"{len(tasks['clips'])} clips, {sum(len(c['sheets']) for c in tasks['clips'])} sheets, "
           f"{len(tasks['batches'])} batches"]
    for n, batch in enumerate(tasks["batches"], 1):
        sheets = sum(len(by_id[c]["sheets"]) for c in batch)
        total = sum(by_id[c]["duration"] for c in batch)
        out.append(f"batch {n}: {len(batch)} clips, {sheets} sheets, {format_time(total)} of footage")
        out += [f"  - {cid}  brief: {by_id[cid]['brief']}" for cid in batch]
    return "\n".join(out)
