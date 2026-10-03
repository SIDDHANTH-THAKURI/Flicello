"""Join rendered shots into one video: hard cuts via concat, transitions via xfade, global fades."""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..edit.timeline import Timeline
from ..ffmpeg import run_ffmpeg
from ..schemas import EditMeta
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


def assembly_graph(timeline: Timeline, meta: EditMeta) -> str:
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

    total = timeline.total_frames / fps
    post = []
    if meta.fade_in > 0:
        post.append(f"fade=t=in:st=0:d={min(meta.fade_in, total / 2):.3f}")
    if meta.fade_out > 0:
        d = min(meta.fade_out, total / 2)
        post.append(f"fade=t=out:st={total - d:.6f}:d={d:.3f}")
    post.append("format=yuv420p")
    lines.append(f"[{current}]{','.join(post)}[vout]")
    return ";\n".join(lines)


def assemble_video(timeline: Timeline, shots: list[SegmentOutput], meta: EditMeta, quality: str,
                   cfg: Config, out_path: Path, work_dir: Path) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    script = work_dir / f"{out_path.stem}.filter.txt"
    script.write_text(assembly_graph(timeline, meta), encoding="utf-8")
    args: list[str] = []
    for shot in shots:
        args += ["-i", str(shot.video)]
    args += ["-/filter_complex", str(script), "-map", "[vout]", "-frames:v", str(timeline.total_frames),
             *output_encoder(quality, cfg), "-r", str(timeline.fps), "-an", "-movflags", "+faststart", str(out_path)]
    run_ffmpeg(args, what="Could not assemble the video")
    return out_path
