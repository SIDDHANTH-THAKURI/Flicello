"""Render an EDL: shots → assembled picture → mixed audio → final file (+ a JSON sidecar)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import soundfile as sf

from ..config import Config
from ..edit.timeline import build_timeline
from ..edit.validate import Issue, validate_edl
from ..ffmpeg import ffprobe_json, run_ffmpeg
from ..schemas import EDL
from ..workspace import Project, write_text_atomic
from .assemble import assemble_video
from .audio_mix import decode_music, frames_to_samples, mix_timeline, normalize, write_wav
from .segments import render_segments
from .sfx import sfx_path
from .text import render_text_png

if TYPE_CHECKING:
    from ..outro.tail import Tail


EncodedPeakCeiling = -0.5  # dBFS: decoded AAC peaks above this risk clipping in some players


def resolve_sound(source: str, clips: dict, project: Project) -> Path:
    """'clip:<id>' → that clip's 48 kHz sound; 'sfx:<name>' → built-in effect; else a file to decode."""
    if source.startswith("clip:"):
        clip = clips.get(source[5:])
        if clip is None or not clip.audio_wav:
            raise ValueError(f"sound layer {source!r}: no such clip with sound")
        return Path(clip.audio_wav)
    if source.startswith("sfx:"):
        return sfx_path(source[4:], project.root.parent.parent / "sfx")
    return decode_music(source, project.music)


def decoded_peak_db(video: Path, scratch: Path) -> float:
    run_ffmpeg(["-i", str(video), "-vn", "-c:a", "pcm_f32le", str(scratch)], what="Could not decode audio for checking")
    data, _ = sf.read(str(scratch), dtype="float32", always_2d=True)
    scratch.unlink(missing_ok=True)
    peak = float(np.abs(data).max()) if data.size else 0.0
    return 20 * np.log10(peak) if peak > 0 else -120.0


def adaptive_looks(timeline, project: Project, look: str | None) -> list[str] | None:
    """Per-shot grades for looks that adapt to the footage ('vivid'), from each shot's measured
    saturation, brightness and contrast over the source it actually uses."""
    if look != "vivid":
        return None
    from .looks import LOOKS, vivid_grade

    arrays: dict[str, dict] = {}
    out = []
    for item in timeline.items:
        cid = item.clip.clip_id
        if cid not in arrays:
            try:
                with np.load(project.signals_npz(cid)) as z:
                    arrays[cid] = {k: z[k] for k in ("t", "saturation", "brightness", "contrast")}
            except (OSError, KeyError):
                arrays[cid] = {}
        a = arrays[cid]
        if not a:
            out.append(LOOKS["vivid"])
            continue
        t0 = item.seg.src_in
        m = (a["t"] >= t0) & (a["t"] <= t0 + item.src_seconds(timeline.fps))
        if not m.any():
            m = np.abs(a["t"] - t0) <= max(0.2, float(np.min(np.abs(a["t"] - t0))))
        out.append(vivid_grade(*(float(np.nanmean(a[k][m])) for k in ("saturation", "brightness", "contrast"))))
    return out


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
               out_path: Path | None = None, on_shot: Callable[[], None] | None = None,
               tail: Tail | None = None, head: Tail | None = None) -> RenderResult:
    """tail / head: something to play after / before the film (the outro / the intro), already rendered at
    this size and fps, with its own sound at its own level."""
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

    shots = render_segments(timeline, width, height, quality, cfg, project.segments / quality, on_shot,
                            look_name=meta.look, item_looks=adaptive_looks(timeline, project, meta.look),
                            stabilize=meta.stabilize)
    texts = [(t, render_text_png(t, width, height, work / f"text_{k}.png")) for k, t in enumerate(edl.texts)]
    video_only = assemble_video(timeline, shots, meta, quality, cfg, work / "picture.mp4", work,
                                texts=texts, size=(width, height),
                                tail=(tail.video, tail.frames) if tail else None,
                                head=(head.video, head.frames) if head else None)

    music_wav = decode_music(edl.music.path, project.music) if edl.music and edl.music.path else None
    layers = [(layer, resolve_sound(layer.source, clips, project)) for layer in edl.sounds]
    mix = mix_timeline(timeline, shots, meta, edl.music if music_wav else None, music_wav,
                       cfg.render.music_duck_db, layers=layers)
    mix, measured, gain_db = normalize(mix, meta.loudness_lufs, meta.true_peak_db)
    if tail is not None:  # the outro's sound has its own level; it just follows the film
        tail_audio = np.zeros((frames_to_samples(tail.frames, timeline.fps), 2), np.float32)
        tail_audio[: len(tail.audio)] = tail.audio[: len(tail_audio)]
        mix = np.concatenate([mix, tail_audio])
    if head is not None:  # the intro's sound plays before the film's
        head_audio = np.zeros((frames_to_samples(head.frames, timeline.fps), 2), np.float32)
        head_audio[: len(head.audio)] = head.audio[: len(head_audio)]
        mix = np.concatenate([head_audio, mix])
    mix_path = work / "mix.wav"
    write_wav(mix_path, mix)

    def mux(audio: Path) -> None:
        run_ffmpeg(["-i", str(video_only), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", cfg.render.audio_bitrate, "-shortest",
                    "-movflags", "+faststart", str(out_path)], what="Could not write the final file")

    mux(mix_path)
    # AAC encoding can overshoot the limiter's ceiling on spiky material; check the decoded result.
    peak_db = decoded_peak_db(out_path, work / "check.wav")
    if peak_db > EncodedPeakCeiling:
        mix *= float(10 ** ((EncodedPeakCeiling - 0.2 - peak_db) / 20))
        write_wav(mix_path, mix)
        mux(mix_path)

    probe = ffprobe_json(out_path)
    duration = float((probe.get("format") or {}).get("duration") or 0.0)
    result = RenderResult(
        output=str(out_path), quality=quality, width=width, height=height, fps=timeline.fps,
        frames=timeline.total_frames + (tail.frames if tail else 0) + (head.frames if head else 0), duration=round(duration, 3), shots=len(shots),
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
