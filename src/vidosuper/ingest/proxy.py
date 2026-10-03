"""Proxy video (small, SDR, constant frame rate) and PCM audio extraction — one decode per clip.

Decoding 4K HEVC is the expensive part. On Intel GPUs we decode *and* shrink on the GPU
(QuickSync), then download only the small frames: about 5x faster than software decoding.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import Config
from ..ffmpeg import FFmpegError, capabilities, run_ffmpeg
from ..schemas import ClipInfo


def tonemap_filter(clip: ClipInfo) -> str:
    """HDR (HLG / PQ) → SDR BT.709. Without this iPhone HDR footage looks washed out."""
    tin = clip.color_transfer or "arib-std-b67"
    pin = clip.color_primaries or "bt2020"
    min_ = clip.color_space or "bt2020nc"
    return (
        f"zscale=tin={tin}:pin={pin}:min={min_}:t=linear:npl=100,format=gbrpf32le,"
        "zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
        "zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
    )


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def gpu_decode_available(clip: ClipInfo, cfg: Config) -> bool:
    """QuickSync path: not for rotated clips (GPU frames bypass FFmpeg's auto-rotation)."""
    if cfg.ingest.hwaccel == "none" or clip.rotation % 360:
        return False
    caps = capabilities()
    return "qsv" in caps["hwaccels"] and "scale_qsv" in caps["filters"]


def proxy_filters(clip: ClipInfo, cfg: Config, *, gpu: bool) -> tuple[list[str], str]:
    """(input options, video filter chain) for the proxy."""
    long_edge, fps = cfg.ingest.proxy_long_edge, cfg.ingest.proxy_fps
    if gpu:
        scale = long_edge / max(clip.width, clip.height)
        width, height = _even(clip.width * scale), _even(clip.height * scale)
        fmt = "p010" if clip.hdr else "nv12"  # keep 10 bits for tone mapping
        chain = [f"scale_qsv=w={width}:h={height}:format={fmt}", "hwdownload", f"format={fmt}", "setsar=1"]
        if clip.hdr:
            chain += ["format=yuv420p10le", tonemap_filter(clip)]
        chain += [f"fps={fps}", "format=yuv420p"]
        return ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"], ",".join(chain)
    chain = [tonemap_filter(clip)] if clip.hdr else []
    chain += [f"scale='if(gte(iw,ih),{long_edge},-2)':'if(gte(iw,ih),-2,{long_edge})'",
              f"fps={fps}", "format=yuv420p"]
    return [], ",".join(chain)


def _part(path: Path) -> Path:
    return path.with_name(path.stem + ".part" + path.suffix)


def build_proxy(clip: ClipInfo, video_out: Path, wav48_out: Path, wav16_out: Path, cfg: Config) -> None:
    outputs: list[tuple[Path, Path]] = [(_part(video_out), video_out)]
    audio_args: list[str] = []
    if clip.has_audio:
        af = "aresample=async=1:first_pts=0"
        outputs += [(_part(wav48_out), wav48_out), (_part(wav16_out), wav16_out)]
        audio_args += ["-map", "0:a:0", "-vn", "-af", af, "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le",
                       str(outputs[1][0])]
        audio_args += ["-map", "0:a:0", "-vn", "-af", af, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                       str(outputs[2][0])]

    attempts = [True, False] if gpu_decode_available(clip, cfg) else [False]
    last_error: FFmpegError | None = None
    for gpu in attempts:
        input_args, vf = proxy_filters(clip, cfg, gpu=gpu)
        args = [
            *input_args, "-i", clip.path,
            "-map", "0:v:0", "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-g", "15", "-keyint_min", "15", "-sc_threshold", "0",
            "-an", "-sn", "-dn", "-map_metadata", "-1", str(outputs[0][0]),
            *audio_args,
        ]
        try:
            run_ffmpeg(args, what=f"Could not make proxy for {clip.rel_path}")
            last_error = None
            break
        except FFmpegError as exc:  # GPU decode can fail on odd files; retry in software
            last_error = exc
    if last_error is not None:
        for part, _ in outputs:
            part.unlink(missing_ok=True)
        raise last_error
    for part, final in outputs:
        os.replace(part, final)
