"""The cut for an editor's own software: the shots as they are in the film, pointing at the original clips with their
exact in and out points, so an editor can finish it in Premiere, Resolve, Final Cut or Avid.
- FCPXML 1.10 (Final Cut Pro, DaVinci Resolve, Premiere through its importer): one asset per clip, the shots on the
  spine with their offsets, speed changes as retimes, and the graphics as markers.
- CMX 3600 EDL (Premiere, Avid, Resolve): one event per shot, with the clip as its reel and a comment naming the file.
Music and graphics stay with the film's own render; the EDL carries the picture cut."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from xml.sax.saxutils import quoteattr

from ..edit.timeline import Timeline
from ..schemas import EDL


def _r(seconds: float, fps: int) -> str:
    """Seconds as an FCPXML rational time on the frame grid."""
    frames = round(seconds * fps)
    f = Fraction(frames, fps)
    return f"{f.numerator}/{f.denominator}s" if f.denominator != 1 else f"{f.numerator}s"


def fcpxml(edl: EDL, timeline: Timeline, clips: dict, title: str) -> str:
    fps = timeline.fps
    w, h = edl.meta.width, edl.meta.height
    used = list(dict.fromkeys(item.seg.clip_id for item in timeline.items))
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<!DOCTYPE fcpxml>', '<fcpxml version="1.10">', '<resources>',
             f'<format id="r0" name="FFVideoFormat{h}p{fps}" frameDuration="1/{fps}s" width="{w}" height="{h}"/>']
    ids = {}
    for k, cid in enumerate(used, start=1):
        c = clips[cid]
        ids[cid] = f"r{k}"
        src = Path(c.path).resolve().as_uri()
        lines.append(f'<asset id="r{k}" name={quoteattr(Path(c.path).name)} start="0s" duration="{_r(c.duration, fps)}" '
                     f'hasVideo="1" hasAudio="{1 if c.has_audio else 0}" format="r0">'
                     f'<media-rep kind="original-media" src={quoteattr(src)}/></asset>')
    lines += ['</resources>', '<library>', f'<event name={quoteattr(title)}>', f'<project name={quoteattr(title)}>',
              f'<sequence format="r0" duration="{_r(timeline.duration, fps)}" tcStart="0s" tcFormat="NDF">', '<spine>']
    for item in timeline.items:
        seg = item.seg
        offset, dur = item.start / fps, item.frames / fps
        src_dur = item.src_seconds(fps)
        retime = ""
        if not seg.ramp and abs(seg.speed - 1.0) > 1e-3:
            retime = (f'<timeMap><timept time="0s" value="{_r(seg.src_in, fps)}" interp="smooth2"/>'
                      f'<timept time="{_r(dur, fps)}" value="{_r(seg.src_in + src_dur, fps)}" interp="smooth2"/></timeMap>')
        lines.append(f'<asset-clip ref="{ids[seg.clip_id]}" name={quoteattr(seg.id)} offset="{_r(offset, fps)}" '
                     f'start="{_r(seg.src_in, fps)}" duration="{_r(dur, fps)}">{retime}</asset-clip>')
    lines.append('</spine>')
    for g in edl.motion:  # the graphics, where they are, for the editor to rebuild in their own way
        what = g.text or g.style or g.kind
        lines.append(f'<marker start="{_r(g.start, fps)}" duration="{_r(max(1 / fps, g.end - g.start), fps)}" '
                     f'value={quoteattr(f"{g.kind}: {what}"[:120])}/>')
    lines += ['</sequence>', '</project>', '</event>', '</library>', '</fcpxml>']
    return "\n".join(lines) + "\n"


def _tc(seconds: float, fps: int) -> str:
    f = int(round(seconds * fps))
    return f"{f // (3600 * fps):02d}:{f // (60 * fps) % 60:02d}:{f // fps % 60:02d}:{f % fps:02d}"


def cmx3600(edl: EDL, timeline: Timeline, clips: dict, title: str) -> str:
    fps = timeline.fps
    out = [f"TITLE: {title[:60]}", "FCM: NON-DROP FRAME", ""]
    for k, item in enumerate(timeline.items, start=1):
        seg = item.seg
        reel = f"C{(list(dict.fromkeys(i.seg.clip_id for i in timeline.items)).index(seg.clip_id) + 1):03d}"
        rec_in, rec_out = item.start / fps, item.end / fps
        src_out = seg.src_in + item.src_seconds(fps)
        out.append(f"{k:03d}  {reel:<8} V     C        {_tc(seg.src_in, fps)} {_tc(src_out, fps)} "
                   f"{_tc(rec_in, fps)} {_tc(rec_out, fps)}")
        if not seg.ramp and abs(seg.speed - 1.0) > 1e-3:
            out.append(f"M2   {reel:<8}       {seg.speed * fps:05.1f}                {_tc(seg.src_in, fps)}")
        out.append(f"* FROM CLIP NAME: {Path(clips[seg.clip_id].path).name}")
        out.append("")
    return "\n".join(out)
