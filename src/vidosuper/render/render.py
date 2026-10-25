"""Render an EDL: shots → assembled picture → mixed audio → final file (+ a JSON sidecar)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import soundfile as sf

from ..config import Config
from ..edit.timeline import build_timeline
from ..edit.validate import Issue, validate_edl
from ..ffmpeg import ffprobe_json, run_ffmpeg
from ..schemas import EDL, TextOverlay
from ..workspace import Project, write_text_atomic
from .assemble import assemble_video
from .audio_mix import decode_music, frames_to_samples, mix_timeline, normalize, write_wav
from .segments import render_segments
from .sfx import sfx_path
from .text import render_text_png

if TYPE_CHECKING:
    from ..outro.tail import Tail


EncodedPeakCeiling = -1.2  # dBTP: platforms ask for true peaks under -1 dB (their own re-encode clips above it)


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


def true_peak(data: np.ndarray, chunk: int = 480_000) -> float:
    """The highest peak between samples too (4x oversampled, as BS.1770 measures it): an encoded file whose samples
    stay under a ceiling can still swing over it between them, and that is what clips on a phone."""
    from scipy.signal import resample_poly

    peak = 0.0
    for c in range(data.shape[1]):
        x = data[:, c]
        for a in range(0, len(x), chunk):
            seg = x[max(0, a - 64): a + chunk + 64]
            if seg.size:
                peak = max(peak, float(np.abs(resample_poly(seg, 4, 1)).max()))
    return peak


def decoded_peak_db(video: Path, scratch: Path) -> float:
    """The true peak of the encoded file's sound, in dB."""
    run_ffmpeg(["-i", str(video), "-vn", "-c:a", "pcm_f32le", str(scratch)], what="Could not decode audio for checking")
    data, _ = sf.read(str(scratch), dtype="float32", always_2d=True)
    scratch.unlink(missing_ok=True)
    peak = true_peak(data) if data.size else 0.0
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


def motion_layers(edl: EDL, project: Project, timeline, width: int, height: int, faces: list[dict] | None = None,
                  shots: list | None = None, work: Path | None = None) -> tuple[list[tuple], list[TextOverlay], list[str]]:
    """The edit's motion graphics, rendered at this size (cached in the project's motion folder): (start, end, video)
    for each. Where the motion engine can't make one, a title or a name is drawn as still text instead and captions are
    left out, and the notes say so."""
    if not edl.motion:
        return [], [], []
    from ..motion import MotionError, render as render_block
    from ..motion.place import layers
    from ..perceive.prep import load_transcript

    said = {cid: t for cid in {s.clip_id for s in edl.segments} if (t := load_transcript(project, cid)) is not None}

    def make(layer):
        try:
            return render_block(layer.block, layer.variables, width, height, timeline.fps, layer.duration,
                                project.root / "motion"), None
        except MotionError as e:
            return None, e

    planned = layers(edl, timeline, said, faces)
    with ThreadPoolExecutor(max_workers=2) as pool:  # each starts its own Chrome: two at a time
        results = list(pool.map(make, planned))
    made, still, notes = [], [], []
    for layer, (video, e) in zip(planned, results):
        if video is not None:
            matte = None
            if layer.graphic.behind and shots is not None and work is not None:  # the person stands in front of it
                from .behind import subject_matte

                matte = subject_matte(timeline, shots, layer.start, layer.end, (width, height),
                                      work / f"matte_{len(made)}.mkv")
                if matte is None:
                    notes.append(f"the {layer.block} at {layer.start:.1f} s is in front of the person (no cut-out model)")
            made.append((layer.start, layer.end, video, matte))
        elif layer.block == "captions":
            notes.append(f"captions {layer.start:.1f}-{layer.end:.1f} s left out: {e}")
        else:
            notes.append(f"the {layer.block} at {layer.start:.1f} s is still text: {e}")
            still.append(TextOverlay(text=layer.graphic.text.replace("\n", " "), start=layer.start, end=layer.end,
                                     position="center" if layer.block == "title" else "lower-third",
                                     size="large" if layer.block == "title" else "medium", style="bold"))
    return made, still, notes


def over_layers(edl: EDL, clips: dict, fps: int, width: int, height: int, quality: str, cfg: Config,
                project: Project, look: str) -> list[tuple]:
    """The shots laid over the picture (B-roll), rendered like the film's own shots (framed, graded, cached) and laid
    on before the motion graphics, which stay on top: (start, end, video) each."""
    from ..edit.timeline import TimelineItem
    from ..schemas import Segment, SegmentAudio
    from .segments import render_segment

    out = []
    for k, o in enumerate(edl.over):
        clip = clips.get(o.clip_id)
        seconds = o.until - o.at
        if clip is None or seconds < 0.2:
            continue
        seg = Segment(id=f"over{k + 1:02d}", clip_id=o.clip_id, src_in=o.src_in,
                      src_out=min(clip.duration, o.src_in + seconds * o.speed), speed=o.speed,
                      frame_center=o.frame_center, zoom=o.zoom, audio=SegmentAudio(mode="mute"))
        frames = max(1, round(seconds * fps))
        item = TimelineItem(index=10_000 + k, seg=seg, clip=clip, frames=frames, start=0, trans_frames=0,
                            moving_frames=frames)
        made = render_segment(item, width, height, fps, quality, cfg, project.segments / quality, look)
        out.append((round(o.at, 6), round(o.until, 6), made.video))
    return out


def draft_canvas(width: int, height: int, short_side: int) -> tuple[int, int]:
    scale = short_side / min(width, height)
    return max(2, round(width * scale / 2) * 2), max(2, round(height * scale / 2) * 2)


def render_edl(edl: EDL, project: Project, cfg: Config, *, quality: str = "draft",
               out_path: Path | None = None, on_shot: Callable[[], None] | None = None,
               tail: Tail | None = None, head: Tail | None = None, faces: list[dict] | None = None) -> RenderResult:
    """tail / head: something to play after / before the film (the outro / the intro), already rendered at
    this size and fps, with its own sound at its own level. faces: where people's faces are in the clips (the face
    index's appearances), which the motion graphics keep clear of."""
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
    graphics, still, motion_notes = motion_layers(edl, project, timeline, width, height, faces, shots, work)
    graphics = [*over_layers(edl, clips, timeline.fps, width, height, quality, cfg, project, meta.look), *graphics]
    texts = [(t, render_text_png(t, width, height, work / f"text_{k}.png")) for k, t in enumerate([*edl.texts, *still])]
    video_only = assemble_video(timeline, shots, meta, quality, cfg, work / "picture.mp4", work,
                                texts=texts, size=(width, height),
                                tail=(tail.video, tail.frames) if tail else None,
                                head=(head.video, head.frames) if head else None, graphics=graphics)

    music_wav = decode_music(edl.music.path, project.music) if edl.music and edl.music.path else None
    layers = [(layer, resolve_sound(layer.source, clips, project)) for layer in edl.sounds]
    duck = edl.music.duck_db if edl.music and edl.music.duck_db is not None else cfg.render.music_duck_db
    mix = mix_timeline(timeline, shots, meta, edl.music if music_wav else None, music_wav, duck, layers=layers)
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
    # AAC encoding can overshoot the limiter's ceiling on spiky material (a beat's sharp kicks): check the decoded
    # result's true peak, and bring the whole mix down until it is under the ceiling (twice at most).
    for _ in range(2):
        peak_db = decoded_peak_db(out_path, work / "check.wav")
        if peak_db <= EncodedPeakCeiling:
            break
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
        warnings=[str(i) for i in issues if i.level == "warning"] + timeline.notes + motion_notes,
    )
    sidecar = project.renders / f"{out_path.stem}.render.json"
    write_text_atomic(sidecar, json.dumps({"result": asdict(result), "edl": edl.model_dump(mode="json")}, indent=2))
    for leftover in work.iterdir():
        leftover.unlink(missing_ok=True)
    work.rmdir()
    return result
