"""Join rendered shots into one video: hard cuts via concat, transitions via xfade, global fades."""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..edit.timeline import Timeline
from ..ffmpeg import run_ffmpeg
from ..schemas import EditMeta, TextOverlay
from .segments import SegmentOutput


def output_encoder(quality: str, cfg: Config) -> list[str]:
    if quality == "draft":
        return ["-c:v", "libx264", "-preset", cfg.render.draft.preset, "-crf", str(cfg.render.draft.crf),
                "-pix_fmt", "yuv420p"]
    final = cfg.render.final
    if final.encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "veryslow", "-global_quality", str(final.crf), "-pix_fmt", "nv12"]
    return ["-c:v", "libx264", "-preset", final.preset, "-crf", str(final.crf), "-profile:v", "high",
            "-pix_fmt", "yuv420p"]


# Editors mean a smooth crossfade by "dissolve"; FFmpeg's xfade "dissolve" is a speckled pixel dissolve.
XFADE_NAMES = {"dissolve": "fade"}


def letterbox_filter(width: int, height: int, aspect: float | None) -> str | None:
    if not aspect or width / height >= aspect:
        return None
    bar = int(round((height - width / aspect) / 2 / 2)) * 2
    if bar <= 0:
        return None
    return (f"drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill,"
            f"drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color=black:t=fill")


def assembly_graph(timeline: Timeline, meta: EditMeta, texts: list[tuple[TextOverlay, Path]] | None = None,
                   size: tuple[int, int] | None = None, tail: bool = False, head: bool = False,
                   graphics: list[tuple] | None = None) -> str:
    """Text overlays are extra inputs after the shots: input index len(timeline.items) + k. Then the motion graphics
    (start, end, a video with transparency, and a matte of the person when it goes behind them), laid on in order,
    then the mattes, then a tail (the outro), played after the film's own fade-out, and a head (the intro), played
    before its fade-in."""
    fps = timeline.fps
    runs: list[list[int]] = []
    for item in timeline.items:
        if not runs or item.trans_frames > 0:
            runs.append([item.index])
        else:
            runs[-1].append(item.index)

    lines = []
    for k, run in enumerate(runs):
        labels = "".join(f"[{i}:v]" for i in run)
        joined = f"concat=n={len(run)}:v=1:a=0," if len(run) > 1 else ""
        lines.append(f"{labels}{joined}settb=AVTB,fps={fps}[r{k}]")
    current = "r0"
    for k in range(1, len(runs)):
        first = timeline.items[runs[k][0]]
        kind = XFADE_NAMES.get(first.seg.transition_in.type, first.seg.transition_in.type)
        lines.append(
            f"[{current}][r{k}]xfade=transition={kind}:"
            f"duration={first.trans_frames / fps:.6f}:offset={first.start / fps:.6f}[x{k}]"
        )
        current = f"x{k}"

    if size and (bars := letterbox_filter(size[0], size[1], meta.letterbox)):
        lines.append(f"[{current}]{bars}[lb]")
        current = "lb"
    first_text = len(timeline.items)
    for k, (overlay, _) in enumerate(texts or []):
        f = max(0.0, min(overlay.fade, (overlay.end - overlay.start) / 2))
        alpha = f"fade=t=in:st={overlay.start:.3f}:d={f:.3f}:alpha=1,fade=t=out:st={overlay.end - f:.3f}:d={f:.3f}:alpha=1" \
            if f > 0 else "null"
        lines.append(f"[{first_text + k}:v]format=rgba,{alpha}[t{k}]")
        lines.append(f"[{current}][t{k}]overlay=0:0:enable='between(t,{overlay.start:.3f},{overlay.end:.3f})'[o{k}]")
        current = f"o{k}"
    first_graphic = first_text + len(texts or [])
    first_matte = first_graphic + len(graphics or [])
    mattes = 0
    for k, (start, end, _, *rest) in enumerate(graphics or []):
        matte = rest[0] if rest else None
        lines.append(f"[{first_graphic + k}:v]setpts=PTS-STARTPTS+{start:.6f}/TB[g{k}]")
        if matte is not None:  # the person back over the graphic, cut out with their matte
            lines.append(f"[{current}]split[pa{k}][pb{k}]")
            lines.append(f"[pb{k}]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS,format=rgba[ps{k}]")
            lines.append(f"[{first_matte + mattes}:v]setpts=PTS-STARTPTS,format=gray[mt{k}]")
            lines.append(f"[ps{k}][mt{k}]alphamerge,setpts=PTS+{start:.6f}/TB[sb{k}]")
            lines.append(f"[pa{k}][g{k}]overlay=0:0:eof_action=pass:format=auto:"
                         f"enable='between(t,{start:.6f},{end:.6f})'[mg{k}]")
            lines.append(f"[mg{k}][sb{k}]overlay=0:0:eof_action=pass:format=auto:"
                         f"enable='between(t,{start:.6f},{end:.6f})'[m{k}]")
            mattes += 1
        else:
            lines.append(f"[{current}][g{k}]overlay=0:0:eof_action=pass:format=auto:"
                         f"enable='between(t,{start:.6f},{end:.6f})'[m{k}]")
        current = f"m{k}"

    total = timeline.total_frames / fps
    post = []
    if meta.fade_in > 0:
        post.append(f"fade=t=in:st=0:d={min(meta.fade_in, total / 2):.3f}")
    if meta.fade_out > 0:
        d = min(meta.fade_out, total / 2)
        post.append(f"fade=t=out:st={total - d:.6f}:d={d:.3f}")
    post.append("format=yuv420p")
    if not tail and not head:
        lines.append(f"[{current}]{','.join(post)}[vout]")
        return ";\n".join(lines)
    # end the film exactly on its last frame: a looped text image would otherwise keep the overlay (and
    # so the film) running forever, and the tail would never be reached
    lines.append(f"[{current}]{','.join(post)},trim=end_frame={timeline.total_frames},settb=AVTB,setsar=1[vfilm]")
    extra = first_matte + mattes
    parts = []
    if head:
        lines.append(f"[{extra + (1 if tail else 0)}:v]settb=AVTB,fps={fps},format=yuv420p,setsar=1[vhead]")
        parts.append("[vhead]")
    parts.append("[vfilm]")
    if tail:
        lines.append(f"[{extra}:v]settb=AVTB,fps={fps},format=yuv420p,setsar=1[vtail]")
        parts.append("[vtail]")
    lines.append(f"{''.join(parts)}concat=n={len(parts)}:v=1:a=0[vout]")
    return ";\n".join(lines)


def assemble_video(timeline: Timeline, shots: list[SegmentOutput], meta: EditMeta, quality: str,
                   cfg: Config, out_path: Path, work_dir: Path,
                   texts: list[tuple[TextOverlay, Path]] | None = None, size: tuple[int, int] | None = None,
                   tail: tuple[Path, int] | None = None, head: tuple[Path, int] | None = None,
                   graphics: list[tuple] | None = None) -> Path:
    """tail / head: (video, frames) played after / before the film, e.g. the outro and the intro. graphics: (start,
    end, video with transparency) laid over the film."""
    work_dir.mkdir(parents=True, exist_ok=True)
    script = work_dir / f"{out_path.stem}.filter.txt"
    script.write_text(assembly_graph(timeline, meta, texts, size, tail=tail is not None, head=head is not None,
                                     graphics=graphics), encoding="utf-8")
    args: list[str] = []
    for shot in shots:
        args += ["-i", str(shot.video)]
    for _, png in texts or []:  # a still looped for exactly the film's length, never forever
        args += ["-loop", "1", "-framerate", str(timeline.fps), "-t", f"{timeline.total_frames / timeline.fps:.3f}",
                 "-i", str(png)]
    for _, _, video, *_ in graphics or []:
        args += ["-i", str(video)]
    for _, _, _, *rest in graphics or []:
        if rest and rest[0] is not None:
            args += ["-i", str(rest[0])]
    if tail is not None:
        args += ["-i", str(tail[0])]
    if head is not None:
        args += ["-i", str(head[0])]
    frames = timeline.total_frames + (tail[1] if tail else 0) + (head[1] if head else 0)
    args += ["-/filter_complex", str(script), "-map", "[vout]", "-frames:v", str(frames),
             *output_encoder(quality, cfg), "-r", str(timeline.fps), "-an", "-movflags", "+faststart", str(out_path)]
    run_ffmpeg(args, what="Could not assemble the video")
    return out_path
