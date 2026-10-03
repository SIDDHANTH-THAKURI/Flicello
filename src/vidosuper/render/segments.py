"""Render each shot to a normalised, cached intermediate (same size, fps, format for all shots).

Shots are cached by everything that affects their pixels, so refinement rounds only re-render
the shots that actually changed.
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
from .framing import atempo_chain, segment_video_filter

SEGMENT_CACHE_VERSION = 2


@dataclass
class SegmentOutput:
    video: Path
    audio: Path | None
    cached: bool


def intermediate_encoder(quality: str, cfg: Config) -> list[str]:
    if quality == "draft":
        return ["-c:v", "libx264", "-preset", cfg.render.draft.preset, "-crf", str(cfg.render.draft.crf),
                "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(cfg.render.final.intermediate_crf),
            "-pix_fmt", "yuv420p"]


def segment_key(item: TimelineItem, width: int, height: int, fps: int, quality: str, cfg: Config) -> str:
    seg = item.seg
    payload = {
        "v": SEGMENT_CACHE_VERSION,
        "clip": item.clip.fingerprint,
        "proxy": quality == "draft",
        "in": round(seg.src_in, 4),
        "frames": item.frames,
        "speed": round(seg.speed, 6),
        "framing": seg.framing,
        "size": [width, height],
        "fps": fps,
        "enc": intermediate_encoder(quality, cfg),
        "blur": cfg.render.blur_pad_sigma,
        "audio": seg.audio.mode == "original" and item.clip.has_audio,
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]


def render_segment(item: TimelineItem, width: int, height: int, fps: int, quality: str,
                   cfg: Config, cache_dir: Path) -> SegmentOutput:
    clip, seg = item.clip, item.seg
    key = segment_key(item, width, height, fps, quality, cfg)
    video = cache_dir / f"{key}.mp4"
    want_audio = seg.audio.mode == "original" and clip.has_audio and bool(clip.audio_wav)
    audio = cache_dir / f"{key}.wav" if want_audio else None
    if video.exists() and (audio is None or audio.exists()):
        return SegmentOutput(video, audio, cached=True)

    from_proxy = quality == "draft"
    source = clip.proxy if from_proxy else clip.path
    if not source or not Path(source).exists():
        raise FileNotFoundError(f"Source for {clip.clip_id} is missing: {source}")
    src_seconds = item.frames * seg.speed / fps
    window = ["-ss", f"{seg.src_in:.4f}", "-t", f"{src_seconds + 0.5:.4f}"]
    video_part = video.with_name(video.stem + ".part.mp4")
    audio_part = audio.with_name(audio.stem + ".part.wav") if audio else None

    def command(gpu: bool) -> list[str]:
        vf = segment_video_filter(clip, seg, width, height, fps, from_proxy=from_proxy,
                                  blur_sigma=cfg.render.blur_pad_sigma, gpu=gpu)
        hw = ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"] if gpu else []
        args = [*hw, *window, "-i", str(source)]
        if audio_part:
            args += [*window, "-i", str(clip.audio_wav)]
        args += ["-map", "0:v:0", "-vf", vf, "-frames:v", str(item.frames),
                 *intermediate_encoder(quality, cfg), "-an", str(video_part)]
        if audio_part:
            af = ",".join([*atempo_chain(seg.speed), "apad"])
            args += ["-map", "1:a:0", "-af", af, "-t", f"{item.frames / fps:.6f}",
                     "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(audio_part)]
        return args

    # Originals (4K HEVC etc.) decode on the GPU when possible; proxies are cheap to decode in software.
    attempts = [True, False] if not from_proxy and gpu_decode_available(clip, cfg) else [False]
    error: FFmpegError | None = None
    for gpu in attempts:
        try:
            run_ffmpeg(command(gpu), what=f"Could not render shot {seg.id} ({clip.rel_path})")
            error = None
            break
        except FFmpegError as exc:
            error = exc
    if error is not None:
        video_part.unlink(missing_ok=True)
        if audio_part:
            audio_part.unlink(missing_ok=True)
        raise error
    os.replace(video_part, video)
    if audio_part and audio:
        os.replace(audio_part, audio)
    return SegmentOutput(video, audio, cached=False)


def render_segments(timeline: Timeline, width: int, height: int, quality: str, cfg: Config,
                    cache_dir: Path, on_done: Callable[[], None] | None = None) -> list[SegmentOutput]:
    cache_dir.mkdir(parents=True, exist_ok=True)

    def work(item: TimelineItem) -> SegmentOutput:
        out = render_segment(item, width, height, timeline.fps, quality, cfg, cache_dir)
        if on_done:
            on_done()
        return out

    with ThreadPoolExecutor(max_workers=max(1, cfg.render.workers)) as pool:
        return list(pool.map(work, timeline.items))
