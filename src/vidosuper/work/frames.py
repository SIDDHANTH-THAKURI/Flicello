"""What the film looks like at a moment, as a still: the work agent's eyes on its own work (`bash fl frames`).

A version is read from its rendered video. A draft has no video yet (it plays in their browser from its edit list),
so its still is made the way the player shows it: the shot's frame at that moment, framed as the edit frames it, with
every motion graphic on it drawn by the motion engine at that instant (the same blocks and values as the player and
the render)."""

from __future__ import annotations

import subprocess
from pathlib import Path

WIDE = 960  # the long side of a still: big enough to read a caption, small enough to look at quickly


def frames(studio, project: dict, which: str, times: list[float], out: Path) -> list[dict]:
    """Stills of the draft ('draft') or a version ('3') at these film seconds, as JPEGs in `out`. Returns
    [{t, file}] (a time past the end is left out)."""
    from ..schemas import EDL
    from ..studio.storage import workdir

    work = workdir(studio.s, project["user_id"], project["id"])
    shape = studio.shapes(project)[0]
    w, h = (WIDE * 9 // 16, WIDE) if shape == "9:16" else (WIDE, WIDE * 9 // 16)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.jpg"):  # only what was asked for this time
        old.unlink(missing_ok=True)
    times = sorted({round(max(0.0, float(t)), 2) for t in times})[:12]
    made = []
    if which != "draft":
        video = work.out / f"v{int(which)}-{shape.replace(':', 'x')}.mp4"
        if not video.exists():
            raise ValueError(f"Version {which} has no video here.")
        import cv2

        from ..perceive.contact_sheet import grab_frames

        for t, (_, frame) in zip(times, grab_frames(video, times)):  # in the order asked (a time past the end: none)
            path = out / f"v{which}-{t:.2f}.jpg"
            cv2.imwrite(str(path), cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 88])
            made.append({"t": t, "file": path})
        return made
    draft = studio.draft_of(project)
    if not draft:
        raise ValueError("There's no draft on screen.")
    edl = EDL.model_validate_json((work.out / draft["edl"]).read_text(encoding="utf-8"))
    fps = edl.meta.fps
    times = sorted({round(round(t * fps) / fps, 4) for t in times})  # on its frames, as it plays and is rendered
    from ..edit.timeline import build_timeline
    from ..workspace import Project

    ws = Project(work.clips, studio.cfg)
    clips = ws.clips()
    timeline = build_timeline(edl, clips)
    layers = studio.preview_of(project, "draft")["motion"]
    for t in times:
        if t >= timeline.duration:
            continue
        path = out / f"draft-{t:.2f}.jpg"
        if _picture(timeline, clips, ws.proxies, t, w, h, path):
            made.append({"t": t, "file": path})
    on = [(x, [m["t"] for m in made if x["start"] <= m["t"] < x["end"]]) for x in layers]
    _graphics([(x, ts) for x, ts in on if ts], made, w, h, out, fps)
    return made


def _picture(timeline, clips, proxies: Path, t: float, w: int, h: int, out: Path) -> bool:
    """The shot's own frame at film second `t`, framed as the edit frames it (its crop, punch-in and mirror)."""
    from ..edit.speed import source_time
    from ..ffmpeg import ffmpeg_bin

    fps = timeline.fps
    item = next((i for i in reversed(timeline.items) if i.start / fps <= t < i.end / fps), None)
    if item is None:
        return False
    seg = item.seg
    moving = min(t - item.start / fps, item.moving_frames / fps)  # a freeze holds its last frame
    src = seg.src_in + (source_time(seg, moving) if seg.ramp else moving * seg.speed)
    proxy = proxies / f"{seg.clip_id}.mp4"
    clip = clips.get(seg.clip_id)
    source = proxy if proxy.exists() else Path(clip.proxy or clip.path) if clip else None
    if source is None or not Path(source).exists():
        return False
    cx, cy = seg.frame_center
    vf = [f"crop='min(iw,ih*{w}/{h})':'min(ih,iw*{h}/{w})':'max(0,min(iw-ow,{cx}*iw-ow/2))':"
          f"'max(0,min(ih-oh,{cy}*ih-oh/2))'"]
    zoom = max(1.0, float(seg.zoom or 1.0))
    if zoom > 1.001:
        zx, zy = seg.zoom_center
        vf.append(f"crop='iw/{zoom}':'ih/{zoom}':'max(0,min(iw-ow,{zx}*iw-ow/2))':'max(0,min(ih-oh,{zy}*ih-oh/2))'")
    if seg.flip:
        vf.append("hflip")
    vf.append(f"scale={w}:{h}")
    r = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{max(0.0, src):.3f}", "-i", str(source),
                        "-frames:v", "1", "-vf", ",".join(vf), "-q:v", "3", str(out)], capture_output=True, timeout=60)
    return r.returncode == 0 and out.exists()


def _graphics(on: list[tuple[dict, list[float]]], made: list[dict], w: int, h: int, out: Path, fps: int) -> None:
    """Every graphic over the stills, drawn by the motion engine at those instants (on the graphic's own frames, as
    its render shows them), in the order the film layers them (the words said on top)."""
    from PIL import Image

    from .. import motion

    over: dict[float, list[Path]] = {}
    for k, (layer, ts) in enumerate(on):
        drawn = motion.snapshot(layer["block"], layer["vars"], w, h, layer["end"] - layer["start"],
                                [round(round((t - layer["start"]) * fps) / fps, 4) for t in ts], out / f".layer-{k}")
        for t, png in zip(ts, drawn):
            over.setdefault(t, []).append(png)
    for m in made:
        if not over.get(m["t"]):
            continue
        base = Image.open(m["file"]).convert("RGBA")
        for png in over[m["t"]]:
            base.alpha_composite(Image.open(png).convert("RGBA").resize(base.size))
        base.convert("RGB").save(m["file"], quality=88)
