"""Instant preview: an edit (an EDL) played straight away in the browser from light copies of the clips, so a change
is seen in a second or two instead of after a render. The full-quality film is rendered only when they say "make it".

- Every clip gets a light copy once, when its clips are watched. It is 540 px on the long side, 30 fps, with a
  keyframe every half second (seeking anywhere is quick), AAC sound and web-optimised. Copies live in the film's own
  folder (preview/<clip id>.mp4), so the server has them like every other result.
- `timeline` turns an EDL into what the player needs:
  - each shot: its copy, where it starts in the source, where it starts and ends on the timeline, its speed (a ramp
    as small steps), crop, zoom, flip and push, its transition, its hits and its sound;
  - then the music (file, offset, fades, jumps), the texts and the look.

  Frame-exact is the render's job; a preview only has to be honest about what the cut is.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..edit.speed import source_time
from ..edit.timeline import build_timeline
from ..ffmpeg import run_ffmpeg
from ..schemas import EDL

FOLDER = "preview"
LONG_EDGE = 540  # px: enough to judge a cut on a phone or a laptop, light enough to start at once
GOP = 15  # a keyframe every half second at 30 fps: a seek anywhere lands fast
STEP = 0.2  # seconds of output between the speed steps of a ramp
AUDIO_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
               ".ogg": "audio/ogg", ".flac": "audio/flac"}


def copy_path(root: Path, clip_id: str) -> Path:
    return root / FOLDER / f"{clip_id}.mp4"


def make_copy(proxy: Path, wav: Path | None, out: Path) -> Path:
    """One clip's light copy, from its proxy (already small, SDR and 30 fps) and its own sound."""
    if out.exists() and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.stem + ".part.mp4")
    sound = wav is not None and wav.exists()
    args = ["-i", str(proxy)]
    if sound:
        args += ["-i", str(wav), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-shortest"]
    else:
        args += ["-map", "0:v:0", "-an"]
    args += ["-vf", f"scale='if(gte(iw,ih),{LONG_EDGE},-2)':'if(gte(iw,ih),-2,{LONG_EDGE})'",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-g", str(GOP), "-keyint_min", str(GOP),
             "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-profile:v", "main", "-movflags", "+faststart", str(part)]
    run_ffmpeg(args, what="Could not make the clip's preview copy")
    part.replace(out)
    return out


def make_copies(project, clips, root: Path, log=None) -> int:
    """Light copies of every usable clip that doesn't have one yet; returns how many were made. A clip whose copy
    can't be made is skipped (its shots show a still in the preview), never failing the step."""
    made = 0
    for clip in clips:
        if not clip.usable:
            continue
        out = copy_path(root, clip.clip_id)
        if out.exists():
            continue
        proxy = project.proxies / f"{clip.clip_id}.mp4"
        if not proxy.exists():
            continue
        try:
            make_copy(proxy, Path(clip.audio_wav) if clip.audio_wav else None, out)
            made += 1
        except Exception as exc:  # noqa: BLE001 - a preview copy is a convenience, the film never waits on it
            if log:
                log(f"preview copy of {clip.clip_id} skipped: {type(exc).__name__}: {exc}")
    return made


def _steps(seg, moving: float) -> list[list[float]]:
    """[output seconds into the shot, play speed] from there on: one step for a steady shot, small ones for a ramp."""
    if not seg.ramp:
        return [[0.0, round(seg.speed, 4)]]
    steps, t = [], 0.0
    while t < moving - 1e-6:
        t2 = min(moving, t + STEP)
        rate = (source_time(seg, t2) - source_time(seg, t)) / max(1e-6, t2 - t)
        steps.append([round(t, 3), round(max(0.0625, min(16.0, rate)), 4)])
        t = t2
    return steps or [[0.0, round(seg.speed, 4)]]


def timeline(edl: EDL, copy_url, music_url: str | None, numbers: dict[str, int] | None = None,
             clip_sound: bool = True, seen: list[dict] | None = None) -> dict:
    """What the browser player needs to play this edit. `copy_url(clip_id)`: the clip's light copy (None: missing);
    `numbers`: each segment's shot number as the strip counts them; `clip_sound`: False when the film is music only;
    `seen`: the faces in the clips, which the motion graphics keep clear of (as in the render)."""
    from ..motion.place import layers

    tl = build_timeline(edl, defaultdict(lambda: None))
    fps = tl.fps
    shots = []
    for item in tl.items:
        seg = item.seg
        moving = item.moving_frames / fps
        shots.append({
            "id": seg.id, "n": (numbers or {}).get(seg.id, item.index + 1), "clip": seg.clip_id,
            "src": copy_url(seg.clip_id), "in": round(seg.src_in, 3),
            "start": round(item.start / fps, 3), "end": round(item.end / fps, 3), "moving": round(moving, 3),
            "steps": _steps(seg, moving), "zoom": seg.zoom, "zc": list(seg.zoom_center), "fc": list(seg.frame_center),
            "framing": seg.framing, "flip": seg.flip, "push": seg.push,
            "trans": {"type": seg.transition_in.type, "dur": round(item.trans_frames / fps, 3)},
            "fx": [{"type": f.type, "at": f.at, "dur": f.duration, "k": f.strength} for f in seg.fx],
            "mute": not clip_sound or seg.audio.mode == "mute" or bool(seg.ramp), "gain": seg.audio.gain_db,
            "look": seg.look, "keys": seg.keys,
        })
    music = None
    if edl.music and music_url:
        m = edl.music
        music = {"src": music_url, "start": m.start, "gain": m.gain_db, "fade_in": m.fade_in, "fade_out": m.fade_out,
                 "jumps": [{"at": j.at, "to": j.to} for j in m.jumps],
                 "dips": [{"t": g.t, "db": g.gain_db} for g in m.automation],
                 "duck_db": m.duck_db if m.duck_db is not None else -12.0}
    over = [{"start": round(o.at, 3), "end": round(o.until, 3), "src": copy_url(o.clip_id), "in": round(o.src_in, 3),
             "rate": o.speed, "fc": list(o.frame_center), "zoom": o.zoom, "zc": [0.5, 0.5]} for o in edl.over]
    return {"w": edl.meta.width, "h": edl.meta.height, "fps": fps, "duration": round(tl.duration, 3), "shots": shots,
            "over": over,
            "music": music, "look": edl.meta.look, "fade_in": edl.meta.fade_in, "fade_out": edl.meta.fade_out,
            "letterbox": edl.meta.letterbox, "clip_sound": clip_sound,
            "texts": [{"text": t.text, "start": t.start, "end": t.end, "position": t.position, "size": t.size,
                       "style": t.style, "fade": t.fade} for t in edl.texts],
            "motion": [layer.public(fps) for layer in layers(edl, tl, {}, seen)]}
