"""Per-shot video filters: speed, frame rate, HDR→SDR, and fitting the shot into the output canvas."""

from __future__ import annotations

import math

from ..ingest.proxy import tonemap_filter
from ..edit.speed import setpts_expr
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


def framing_filter(mode: str, width: int, height: int, blur_sigma: float,
                   center: tuple[float, float] = (0.5, 0.5)) -> str:
    """center: where a fill crop sits in the scaled source (fractions), e.g. on the subject in 9:16."""
    if mode == "fill":
        cx, cy = (min(1.0, max(0.0, v)) for v in center)
        crop = f"crop={width}:{height}" if (cx, cy) == (0.5, 0.5) else \
            f"crop={width}:{height}:x='(iw-{width})*{cx:.4f}':y='(ih-{height})*{cy:.4f}'"
        return f"scale={width}:{height}:force_original_aspect_ratio=increase:force_divisible_by=2,{crop}"
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


def source_prefilter(clip: ClipInfo, seg: Segment, width: int, height: int, *, from_proxy: bool,
                     gpu: bool) -> list[str]:
    """Decode-side steps shared by rendering and stabilisation analysis (so both see the same frames)."""
    if gpu:
        return gpu_prefilter(clip, width, height, seg.zoom)
    if clip.hdr and not from_proxy:  # proxies are already tone-mapped
        return [tonemap_filter(clip)]
    return []


def filter_path(path: str) -> str:
    """A file path written safely inside an FFmpeg filter argument."""
    return "'" + str(path).replace("\\", "/").replace(":", "\\:") + "'"


def stabilize_detect_filter(clip: ClipInfo, seg: Segment, width: int, height: int, *, from_proxy: bool,
                            gpu: bool, trf: str) -> str:
    pre = source_prefilter(clip, seg, width, height, from_proxy=from_proxy, gpu=gpu)
    return ",".join([*pre, f"vidstabdetect=shakiness=6:accuracy=12:stepsize=6:result={filter_path(trf)}"])


def segment_video_filter(clip: ClipInfo, seg: Segment, width: int, height: int, fps: int,
                         *, from_proxy: bool, blur_sigma: float, gpu: bool = False,
                         moving_frames: int | None = None, look: str = "", stab_trf: str | None = None) -> str:
    """moving_frames: frames of motion to keep before an optional freeze (the last one is then held).
    stab_trf: vidstab motion file for this shot; smooths handheld wobble (optimal zoom hides the edges)."""
    pre = source_prefilter(clip, seg, width, height, from_proxy=from_proxy, gpu=gpu)
    if stab_trf:
        pre.append(f"vidstabtransform=input={filter_path(stab_trf)}:smoothing=24:optzoom=1:zoomspeed=0.25"
                   ":interpol=bicubic")
    if (crop := zoom_filter(seg)) is not None:
        pre.append(crop)
    pre.append(setpts_expr(seg))
    pre.append(f"fps={fps}")
    if moving_frames is not None and seg.freeze > 0:
        pre.append(f"trim=end_frame={moving_frames},setpts=PTS-STARTPTS")  # freeze holds *this* last frame
    mode = resolve_framing(seg.framing, clip.width, clip.height, width, height)
    motion = motion_filter(seg, width, height, (moving_frames or 0) / fps)
    post = ([motion] if motion else []) + ([look] if look else []) + [
        "setsar=1", "format=yuv420p", f"tpad=stop_mode=clone:stop_duration={seg.freeze + 2:.3f}"]
    return ",".join(pre) + "," + framing_filter(mode, width, height, blur_sigma, seg.frame_center) + "," + ",".join(post)


def motion_filter(seg: Segment, width: int, height: int, seconds: float) -> str | None:
    """Push-in/out over the shot plus its hits, as one per-frame scale+crop (and a brightness lift for
    flashes). t is seconds into the shot. A pulse is a quick zoom bump that decays; a shake jitters the
    frame inside a few percent of zoom margin; a flash lifts brightness and decays."""
    zoom_terms, dx_terms, dy_terms, flash_terms = [], [], [], []
    if seg.push and seconds > 0:
        ease = f"(0.5-0.5*cos(PI*min(1,t/{seconds:.4f})))"
        zoom_terms.append(f"(1+{seg.push:.4f}*{ease})" if seg.push > 0 else f"(1+{-seg.push:.4f}*(1-{ease}))")
    for f in seg.fx:
        a, d, s = f.at, f.duration, f.strength
        env = f"if(between(t,{a:.4f},{a + d:.4f}),1-(t-{a:.4f})/{d:.4f},0)"
        if f.type == "pulse":
            zoom_terms.append(f"(1+{0.07 * s:.4f}*if(gte(t,{a:.4f}),exp(-(t-{a:.4f})/{max(d / 3, 0.02):.4f}),0))")
        elif f.type == "shake":
            amp = 0.012 * s * width
            zoom_terms.append(f"(1+{0.03 * s:.4f}*{env})")
            dx_terms.append(f"{amp:.2f}*sin(2*PI*17*(t-{a:.4f}))*{env}")
            dy_terms.append(f"{amp * 0.8:.2f}*cos(2*PI*13*(t-{a:.4f}))*{env}")
        else:  # flash
            flash_terms.append(f"{0.75 * s:.4f}*{env}")
    if not (zoom_terms or flash_terms):
        return None
    parts = []
    if zoom_terms:
        z = "*".join(zoom_terms)
        dx = "+".join(dx_terms) or "0"
        dy = "+".join(dy_terms) or "0"
        parts.append(f"scale=w='2*trunc({width}*{z}/2)':h='2*trunc({height}*{z}/2)':eval=frame:flags=bicubic")
        parts.append(f"crop=w={width}:h={height}:x='max(0,min(iw-{width},(iw-{width})/2+{dx}))'"
                     f":y='max(0,min(ih-{height},(ih-{height})/2+{dy}))'")
    if flash_terms:
        parts.append(f"eq=brightness='min(1,{'+'.join(flash_terms)})':eval=frame")
    return ",".join(parts)


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
