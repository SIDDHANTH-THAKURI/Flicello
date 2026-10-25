"""Render each shot to a normalised, cached intermediate (same size, fps, format for all shots).

Picture and sound are cached separately, by everything that affects them, so refinement rounds
only re-render what actually changed (a new J-cut doesn't re-encode the picture, a new look
doesn't re-cut the sound).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..edit.timeline import Timeline, TimelineItem
from ..ffmpeg import FFmpegError, run_ffmpeg
from ..ingest.proxy import gpu_decode_available
from .framing import atempo_chain, segment_video_filter, stabilize_detect_filter
from .looks import look_filter
from ..edit.speed import speeds

SEGMENT_CACHE_VERSION = 3  # 3: stabilised shots are checked for frame jumps (and redone without)
AUDIO_CACHE_VERSION = 1


@dataclass
class SegmentOutput:
    video: Path
    audio: Path | None
    cached: bool
    audio_lead: float = 0.0  # seconds of sound placed before the picture starts (J-cut actually available)


def intermediate_encoder(quality: str, cfg: Config) -> list[str]:
    if quality == "draft":
        return ["-c:v", "libx264", "-preset", cfg.render.draft.preset, "-crf", str(cfg.render.draft.crf),
                "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(cfg.render.final.intermediate_crf),
            "-pix_fmt", "yuv420p"]


def _digest(payload: dict) -> str:
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]


def uses_proxy(item: TimelineItem, quality: str) -> bool:
    """Drafts render from the 30 fps proxies, except real slow motion: a 0.5x shot from a 60 fps clip
    needs the original's frames, or the draft shows doubled frames (judder) that the final won't have."""
    if quality != "draft":
        return False
    return not (min(speeds(item.seg)) < 0.99 and item.clip.fps >= 47)


def segment_key(item: TimelineItem, width: int, height: int, fps: int, quality: str, cfg: Config,
                look: str = "") -> str:
    seg = item.seg
    payload = {
        "v": SEGMENT_CACHE_VERSION,
        "clip": item.clip.fingerprint,
        "proxy": uses_proxy(item, quality),
        "in": round(seg.src_in, 4),
        "frames": item.frames,
        "speed": round(seg.speed, 6),
        **({"ramp": [[round(p.at, 4), round(p.speed, 4)] for p in seg.ramp]} if seg.ramp else {}),
        **({"push": round(seg.push, 4)} if seg.push else {}),
        **({"fx": [f.model_dump() for f in seg.fx]} if seg.fx else {}),
        "framing": seg.framing,
        "size": [width, height],
        "fps": fps,
        "enc": intermediate_encoder(quality, cfg),
        "blur": cfg.render.blur_pad_sigma,
        "audio": seg.audio.mode == "original" and item.clip.has_audio,
    }
    # newer options only enter the key when used, so shots cached before they existed stay valid
    if seg.zoom > 1.0001:
        payload["zoom"] = [round(seg.zoom, 4), [round(v, 4) for v in seg.zoom_center]]
    if seg.flip:
        payload["flip"] = 1
    if seg.keys:  # framing keyframes, set by hand on the timeline
        payload["keys"] = [[round(float(v), 4) for v in k] for k in seg.keys]
    if tuple(seg.frame_center) != (0.5, 0.5):
        payload["frame_center"] = [round(v, 4) for v in seg.frame_center]
    if seg.freeze > 0:
        payload["freeze"] = [item.moving_frames, round(seg.freeze, 3)]
    if look:
        payload["look"] = look
    return _digest(payload)


def audio_window(item: TimelineItem, fps: int) -> tuple[float, float, float]:
    """(source start, actual lead seconds, output seconds) for a shot's sound incl. J/L-cut extensions."""
    seg, clip = item.seg, item.clip
    wanted_start = seg.src_in - seg.audio.lead * seg.speed
    start = max(0.0, wanted_start)
    lead = (seg.src_in - start) / seg.speed
    seconds = lead + item.frames / fps + seg.audio.tail
    return start, lead, seconds


def audio_key(item: TimelineItem, fps: int) -> str:
    start, lead, seconds = audio_window(item, fps)
    return _digest({"v": AUDIO_CACHE_VERSION, "clip": item.clip.fingerprint, "start": round(start, 4),
                    "seconds": round(seconds, 4), "speed": round(item.seg.speed, 6)})


def _render_audio(item: TimelineItem, fps: int, cache_dir: Path) -> tuple[Path, float, bool]:
    start, lead, seconds = audio_window(item, fps)
    out = cache_dir / f"a_{audio_key(item, fps)}.wav"
    if out.exists():
        return out, lead, True
    part = out.with_name(out.stem + ".part.wav")
    af = ",".join([*atempo_chain(item.seg.speed), "apad"])
    run_ffmpeg(["-ss", f"{start:.4f}", "-t", f"{seconds * item.seg.speed + 0.5:.4f}", "-i", str(item.clip.audio_wav),
                "-af", af, "-t", f"{seconds:.6f}", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(part)],
               what=f"Could not cut the sound of shot {item.seg.id}")
    os.replace(part, out)
    return out, lead, False


def render_segment(item: TimelineItem, width: int, height: int, fps: int, quality: str,
                   cfg: Config, cache_dir: Path, look_name: str | None = None,
                   look_override: str | None = None, stabilize: bool = False) -> SegmentOutput:
    clip, seg = item.clip, item.seg
    look = look_override if look_override is not None and not seg.look else look_filter(seg.look or look_name)
    stabilize = stabilize or seg.stabilize
    key = segment_key(item, width, height, fps, quality, cfg, look + ("|stab" if stabilize else ""))
    video = cache_dir / f"{key}.mp4"
    want_audio = seg.audio.mode == "original" and clip.has_audio and bool(clip.audio_wav) and not seg.ramp
    audio, lead, audio_cached = (_render_audio(item, fps, cache_dir) if want_audio else (None, 0.0, True))
    if video.exists():
        return SegmentOutput(video, audio, cached=audio_cached, audio_lead=lead)

    from_proxy = uses_proxy(item, quality)
    source = clip.proxy if from_proxy else clip.path
    if not source or not Path(source).exists():
        raise FileNotFoundError(f"Source for {clip.clip_id} is missing: {source}")
    src_seconds = item.src_seconds(fps)
    window = ["-ss", f"{seg.src_in:.4f}", "-t", f"{src_seconds + 0.5:.4f}"]
    video_part = video.with_name(video.stem + ".part.mp4")

    trf = cache_dir / f"{key}.trf"

    def detect(gpu: bool) -> list[str]:
        vf = stabilize_detect_filter(clip, seg, width, height, from_proxy=from_proxy, gpu=gpu, trf=str(trf))
        hw = ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"] if gpu else []
        return [*hw, *window, "-i", str(source), "-map", "0:v:0", "-vf", vf, "-f", "null", "-"]

    def command(gpu: bool, stab: bool = True) -> list[str]:
        vf = segment_video_filter(clip, seg, width, height, fps, from_proxy=from_proxy,
                                  blur_sigma=cfg.render.blur_pad_sigma, gpu=gpu,
                                  moving_frames=item.moving_frames, look=look,
                                  stab_trf=str(trf) if stabilize and stab else None)
        hw = ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"] if gpu else []
        return [*hw, *window, "-i", str(source), "-map", "0:v:0", "-vf", vf, "-frames:v", str(item.frames),
                *intermediate_encoder(quality, cfg), "-an", str(video_part)]

    # Originals (4K HEVC etc.) decode on the GPU when possible; proxies are cheap to decode in software.
    attempts = [True, False] if not from_proxy and gpu_decode_available(clip, cfg) else [False]
    error: FFmpegError | None = None
    used_gpu = False
    for gpu in attempts:
        try:
            if stabilize:
                run_ffmpeg(detect(gpu), what=f"Could not analyse shake in shot {seg.id} ({clip.rel_path})")
            run_ffmpeg(command(gpu), what=f"Could not render shot {seg.id} ({clip.rel_path})")
            error, used_gpu = None, gpu
            break
        except FFmpegError as exc:
            error = exc
    if error is not None:
        video_part.unlink(missing_ok=True)
        raise error
    if stabilize and stabiliser_jumps(video_part, fps, seg):
        # a bad motion estimate threw frames across the screen: the shot is better unstabilised
        run_ffmpeg(command(used_gpu, stab=False), what=f"Could not render shot {seg.id} ({clip.rel_path})")
    os.replace(video_part, video)
    return SegmentOutput(video, audio, cached=False, audio_lead=lead)


def stabiliser_jumps(video: Path, fps: int, seg) -> list[float]:
    """Seconds into a stabilised shot where a frame leaps from the one before far more than the shot's own
    motion explains (vidstab occasionally misreads flat sky, water or a passing object, and some library
    versions more than others). Frames an effect changes on purpose (flash, pulse, shake) are skipped, and
    brightness is levelled first so a flash or an exposure change never counts."""
    import cv2
    import numpy as np

    skip: set[int] = set()
    for f in seg.fx:
        first = int(f.at * fps)
        last = int((f.at + f.duration) * fps) + 1 if f.type in ("flash", "shake") else first + 2
        skip.update(range(first - 1, last + 1))
    cap = cv2.VideoCapture(str(video))
    diffs: list[float] = []
    prev = None
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            g = cv2.cvtColor(cv2.resize(frame, (96, max(1, round(96 * h / w))), interpolation=cv2.INTER_AREA),
                             cv2.COLOR_BGR2GRAY).astype(np.float32)
            g -= g.mean()
            if prev is not None:
                diffs.append(float(np.abs(g - prev).mean()))
            prev = g
    finally:
        cap.release()
    if len(diffs) < 3:
        return []
    limit = max(12.0, 6.0 * float(np.median(diffs)))
    return [round((i + 1) / fps, 3) for i, d in enumerate(diffs) if d > limit and (i + 1) not in skip]


def render_segments(timeline: Timeline, width: int, height: int, quality: str, cfg: Config,
                    cache_dir: Path, on_done: Callable[[], None] | None = None,
                    look_name: str | None = None, item_looks: list[str] | None = None,
                    stabilize: bool = False) -> list[SegmentOutput]:
    cache_dir.mkdir(parents=True, exist_ok=True)

    def work(item: TimelineItem) -> SegmentOutput:
        out = render_segment(item, width, height, timeline.fps, quality, cfg, cache_dir, look_name,
                             item_looks[item.index] if item_looks else None, stabilize)
        if item.seg.graphics:  # motion graphics drawn into the rendered shot
            from ..graphics.apply import apply_graphics

            out = SegmentOutput(apply_graphics(out.video, item.seg.graphics, timeline.fps, cache_dir, quality, cfg),
                                out.audio, cached=out.cached, audio_lead=out.audio_lead)
        if on_done:
            on_done()
        return out

    with ThreadPoolExecutor(max_workers=max(1, cfg.render.workers)) as pool:
        return list(pool.map(work, timeline.items))
