"""Render an EDL: shots → assembled picture → mixed audio → final file (+ a JSON sidecar)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import Config
from ..edit.timeline import build_timeline
from ..edit.validate import Issue, validate_edl
from ..ffmpeg import ffprobe_json, run_ffmpeg
from ..schemas import EDL
from ..workspace import Project, write_text_atomic
from .assemble import assemble_video
from .audio_mix import decode_music, mix_timeline, normalize, write_wav
from .segments import render_segments


class EDLValidationError(ValueError):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("The edit has errors:\n" + "\n".join(f"  - {i}" for i in issues))


@dataclass
class RenderResult:
    output: str
    quality: str
    width: int
    height: int
    fps: int
    frames: int
    duration: float
    shots: int
    shots_rendered: int
    seconds: float
    loudness_measured: float | None
    loudness_gain_db: float | None
    warnings: list[str] = field(default_factory=list)


def draft_canvas(width: int, height: int, short_side: int) -> tuple[int, int]:
    scale = short_side / min(width, height)
    return max(2, round(width * scale / 2) * 2), max(2, round(height * scale / 2) * 2)


def render_edl(edl: EDL, project: Project, cfg: Config, *, quality: str = "draft",
               out_path: Path | None = None, on_shot: Callable[[], None] | None = None) -> RenderResult:
    if quality not in ("draft", "final"):
        raise ValueError("quality must be 'draft' or 'final'")
    started = time.perf_counter()
    clips = project.clips()
    signals = {cid: s for cid in clips if (s := project.load_signals(cid)) is not None}
    issues = validate_edl(edl, clips, signals)
    errors = [i for i in issues if i.level == "error"]
    if errors:
        raise EDLValidationError(errors)

    meta = edl.meta
    width, height = (meta.width, meta.height) if quality == "final" else \
        draft_canvas(meta.width, meta.height, cfg.render.draft.short_side)
    timeline = build_timeline(edl, clips)
    project.ensure()
    out_path = Path(out_path) if out_path else project.renders / f"edit_{quality}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work = project.tmp / f"render_{int(time.time() * 1000)}"
    work.mkdir(parents=True, exist_ok=True)

    shots = render_segments(timeline, width, height, quality, cfg, project.segments / quality, on_shot)
    video_only = assemble_video(timeline, shots, meta, quality, cfg, work / "picture.mp4", work)

    music_wav = decode_music(edl.music.path, project.music) if edl.music else None
    mix = mix_timeline(timeline, shots, meta, edl.music, music_wav, cfg.render.music_duck_db)
    mix, measured, gain_db = normalize(mix, meta.loudness_lufs, meta.true_peak_db)
    mix_path = work / "mix.wav"
    write_wav(mix_path, mix)

    run_ffmpeg(["-i", str(video_only), "-i", str(mix_path), "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", cfg.render.audio_bitrate, "-shortest",
                "-movflags", "+faststart", str(out_path)], what="Could not write the final file")

    probe = ffprobe_json(out_path)
    duration = float((probe.get("format") or {}).get("duration") or 0.0)
    result = RenderResult(
        output=str(out_path), quality=quality, width=width, height=height, fps=timeline.fps,
        frames=timeline.total_frames, duration=round(duration, 3), shots=len(shots),
        shots_rendered=sum(1 for s in shots if not s.cached), seconds=round(time.perf_counter() - started, 1),
        loudness_measured=None if measured is None else round(measured, 1),
        loudness_gain_db=None if gain_db is None else round(gain_db, 1),
        warnings=[str(i) for i in issues if i.level == "warning"] + timeline.notes,
    )
    sidecar = project.renders / f"{out_path.stem}.render.json"
    write_text_atomic(sidecar, json.dumps({"result": asdict(result), "edl": edl.model_dump(mode="json")}, indent=2))
    for leftover in work.iterdir():
        leftover.unlink(missing_ok=True)
    work.rmdir()
    return result
