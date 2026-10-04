"""Per-shot video filters: speed, frame rate, HDR→SDR, and fitting the shot into the output canvas."""

from __future__ import annotations

import math

from ..ingest.proxy import tonemap_filter
from ..schemas import ClipInfo, Segment


def resolve_framing(mode: str, src_w: int, src_h: int, dst_w: int, dst_h: int) -> str:
    """auto: fill when shapes roughly match, otherwise blurred-background fill (vertical clip in 16:9 etc.)."""
    if mode != "auto":
        return mode
    if not src_w or not src_h:
        return "fill"
    ratio = (src_w / src_h) / (dst_w / dst_h)
    return "fill" if abs(math.log(ratio)) <= math.log(1.2) else "blur_pad"


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def framing_filter(mode: str, width: int, height: int, blur_sigma: float) -> str:
    if mode == "fill":
        return (f"scale={width}:{height}:force_original_aspect_ratio=increase:force_divisible_by=2,"
                f"crop={width}:{height}")
    if mode == "fit":
        return (f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black")
    if mode == "blur_pad":
        # Blur at quarter size (much faster than full-size gblur), then scale back up.
        bw, bh = _even(width / 4), _even(height / 4)
        sigma = max(1.0, blur_sigma / 4)
        return (
            "split=2[vbg][vfg];"
            f"[vbg]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"gblur=sigma={sigma:.1f},eq=brightness=-0.06:saturation=1.1,scale={width}:{height}[vbgb];"
            f"[vfg]scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2[vfgs];"
            "[vbgb][vfgs]overlay=(W-w)/2:(H-h)/2"
        )
    raise ValueError(f"unknown framing mode {mode!r}")


def zoom_filter(seg: Segment) -> str | None:
    """Crop for a punch-in, centred on seg.zoom_center and kept inside the frame (even dimensions)."""
    if seg.zoom <= 1.0001:
        return None
    z = seg.zoom
    cx, cy = (min(1.0, max(0.0, v)) for v in seg.zoom_center)
    return (f"crop=w='trunc(iw/{z:.4f}/2)*2':h='trunc(ih/{z:.4f}/2)*2':"
            f"x='max(0,min(iw-ow,{cx:.4f}*iw-ow/2))':y='max(0,min(ih-oh,{cy:.4f}*ih-oh/2))'")


def gpu_prefilter(clip: ClipInfo, width: int, height: int, zoom: float = 1.0) -> list[str]:
    """Shrink on the GPU (QuickSync) to just cover the canvas (times any punch-in), then download."""
    scale = min(1.0, max(width / clip.width, height / clip.height) * zoom)
    tw, th = _even(clip.width * scale), _even(clip.height * scale)
    fmt = "p010" if clip.hdr else "nv12"
    chain = [f"scale_qsv=w={tw}:h={th}:format={fmt}", "hwdownload", f"format={fmt}", "setsar=1"]
    if clip.hdr:
        chain += ["format=yuv420p10le", tonemap_filter(clip)]
    return chain


def segment_video_filter(clip: ClipInfo, seg: Segment, width: int, height: int, fps: int,
                         *, from_proxy: bool, blur_sigma: float, gpu: bool = False,
                         moving_frames: int | None = None, look: str = "") -> str:
    """moving_frames: frames of motion to keep before an optional freeze (the last one is then held)."""
    pre = []
    if gpu:
        pre += gpu_prefilter(clip, width, height, seg.zoom)
    elif clip.hdr and not from_proxy:  # proxies are already tone-mapped
        pre.append(tonemap_filter(clip))
    if (crop := zoom_filter(seg)) is not None:
        pre.append(crop)
    pre.append(f"setpts=(PTS-STARTPTS)/{seg.speed:.6f}")
    pre.append(f"fps={fps}")
    if moving_frames is not None and seg.freeze > 0:
        pre.append(f"trim=end_frame={moving_frames},setpts=PTS-STARTPTS")  # freeze holds *this* last frame
    mode = resolve_framing(seg.framing, clip.width, clip.height, width, height)
    post = ([look] if look else []) + [
        "setsar=1", "format=yuv420p", f"tpad=stop_mode=clone:stop_duration={seg.freeze + 2:.3f}"]
    return ",".join(pre) + "," + framing_filter(mode, width, height, blur_sigma) + "," + ",".join(post)


def atempo_chain(speed: float) -> list[str]:
    """Audio speed change without pitch shift; atempo handles 0.5-2.0 per stage."""
    if abs(speed - 1.0) < 1e-6:
        return []
    stages = []
    remaining = speed
    while remaining > 2.0:
        stages.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        stages.append("atempo=0.5")
        remaining /= 0.5
    stages.append(f"atempo={remaining:.6f}")
    return stages
