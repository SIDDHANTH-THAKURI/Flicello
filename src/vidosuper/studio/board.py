"""The storyboard and the rough preview of a film from clips (docs/proposals/flicello-next.md, items 7 and 9).

Before the whole film is made, the person sees its plan as a storyboard: one frame per shot (the real still of the
moment it uses), when it plays, what it shows; and a rough preview of the whole film: those frames cut on the real
beats with the real song (seconds to make, so it costs next to nothing). They can drop a shot, move it, swap it for
another moment, or say what is wrong with it. Drops, moves and swaps are applied here, by code (free); only a shot
with a note goes back to the AI, and only that shot is changed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..ffmpeg import find_tool
from .brain.schemas import DesignPlan, PlanShot, PoolMoment

SIZE = {"9:16": (270, 480), "16:9": (480, 270)}
ROUGH = {"9:16": (360, 640), "16:9": (640, 360)}


def shot_beats(s: PlanShot) -> float:
    return s.beats * (s.count if s.kind == "stutter" else 1)


def still(source: Path, t: float, shape: str, out: Path, frame_center: tuple[float, float] = (0.5, 0.5)) -> bool:
    """One frame of `source` at `t`, cropped to the film's shape (around `frame_center`) and made small."""
    w, h = SIZE[shape]
    cx, cy = frame_center
    crop = (f"crop='min(iw,ih*{w}/{h})':'min(ih,iw*{h}/{w})':"
            f"'max(0,min(iw-ow,{cx}*iw-ow/2))':'max(0,min(ih-oh,{cy}*ih-oh/2))'")
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([find_tool("ffmpeg"), "-y", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(source),
                        "-frames:v", "1", "-vf", f"{crop},scale={w}:{h}", "-q:v", "4", str(out)],
                       capture_output=True, timeout=60)
    return r.returncode == 0 and out.exists()


def moment_label(facts: dict, short: str, start: float, end: float) -> str:
    """What the analyst wrote about the moment a shot uses (the one overlapping it most)."""
    f = facts.get(short)
    if f is None:
        return short
    best, overlap = None, 0.0
    for m in f.moments:
        o = min(end, m["end"]) - max(start, m["start"])
        if o > overlap:
            best, overlap = m, o
    return (best or {}).get("description") or short


def build(pipe, ctx, plan: DesignPlan, shape: str, name: str, alternatives: int = 18) -> dict:
    """The storyboard of `plan`: cards (one per shot, with its frame), other good moments to swap in (with frames),
    and the rough preview of the whole film. Files go to the project's stills (frames) and out (preview)."""
    period = ctx.beats.period
    by_key = {m.key: m for m in plan.pool}
    sources = {short: pipe.project.proxies / f"{f.clip_id}.mp4" for short, f in ctx.facts.items()}
    clips = pipe.project.clips()
    for short, f in ctx.facts.items():  # no proxy: the original clip
        if not sources[short].exists() and f.clip_id in clips:
            sources[short] = Path(clips[f.clip_id].path)
    cards, at, used = [], 0.0, set()
    for k, s in enumerate(plan.plan):
        m = by_key.get(s.key)
        if m is None:
            continue
        t = m.peak if m.peak is not None else (m.start + m.end) / 2
        thumb = f"sb-{name}-{k:02d}.jpg"
        ok = sources.get(m.clip) is not None and still(sources[m.clip], t, shape, pipe.work.stills / thumb)
        seconds = shot_beats(s) * period
        cards.append({"k": k, "start": round(at, 2), "end": round(at + seconds, 2), "beats": shot_beats(s),
                      "kind": s.kind, "clip": m.clip, "at": round(t, 2), "thumb": thumb if ok else "",
                      "what": moment_label(ctx.facts, m.clip, m.start, m.end), "transition": s.transition,
                      "drop": ctx.beats.drop is not None and abs(at / period - ctx.beats.drop) < 1e-6})
        used.add((m.clip, round(m.start, 1)))
        at += seconds
    others = []
    for short, f in ctx.facts.items():  # good moments the plan doesn't use, best first
        for n, m in enumerate(f.moments):
            if (short, round(m["start"], 1)) in used or m.get("strength", 0) < 4:
                continue
            others.append((m.get("hook", 0) + m.get("strength", 0), short, n, m))
    others.sort(key=lambda x: -x[0])
    alts = []
    for _, short, n, m in others[:alternatives]:
        t = m.get("peak") if m.get("peak") is not None else (m["start"] + m["end"]) / 2
        thumb = f"sb-{name}-alt-{short}-{n + 1}.jpg"
        if sources.get(short) is not None and still(sources[short], t, shape, pipe.work.stills / thumb):
            alts.append({"id": f"{short}#{n + 1}", "thumb": thumb, "what": m.get("description") or short,
                         "seconds": round(m["end"] - m["start"], 1)})
    rough = rough_cut(cards, pipe.work.stills, shape, pipe.work.out / f"{name}-rough.mp4",
                      None if pipe.no_music else Path(str(pipe.work.song_path())), ctx.beats.t0)
    return {"cards": cards, "alternatives": alts, "rough": rough.name if rough else "", "seconds": round(at, 2)}


def rough_cut(cards: list[dict], stills: Path, shape: str, out: Path, song: Path | None, start: float) -> Path | None:
    """The whole film as its frames, cut on the plan's times, with the song from where the film starts."""
    frames = [c for c in cards if c["thumb"]]
    if not frames:
        return None
    w, h = ROUGH[shape]
    listing = out.with_suffix(".txt")
    lines = []
    for c in frames:
        lines.append(f"file '{(stills / c['thumb']).as_posix()}'")
        lines.append(f"duration {max(0.04, c['end'] - c['start']):.3f}")
    lines.append(f"file '{(stills / frames[-1]['thumb']).as_posix()}'")  # the concat demuxer needs the last again
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    total = frames[-1]["end"]
    cmd = [find_tool("ffmpeg"), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing)]
    if song is not None and song.exists():
        cmd += ["-ss", f"{start:.3f}", "-i", str(song)]
    cmd += ["-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps=24,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-t", f"{total:.3f}"]
    if song is not None and song.exists():
        cmd += ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "96k", "-af", f"afade=t=out:st={max(0, total - 1.5):.2f}:d=1.5"]
    cmd += ["-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    listing.unlink(missing_ok=True)
    return out if r.returncode == 0 and out.exists() else None


def apply_edits(plan: DesignPlan, ctx, edits: dict) -> tuple[DesignPlan, list[str]]:
    """The person's changes to the storyboard, by code: `drop` (shot numbers), `move` ({k: -1 | +1}), `swap`
    ({k: "c03#2"}), `notes` ({k: "too dark"}). Returns the plan and the notes as lines for the AI (empty: none)."""
    shots = [s.model_copy() for s in plan.plan]
    pool = list(plan.pool)
    keys = {m.key for m in pool}
    period = ctx.beats.period
    starts, at = [], 0.0
    for s in shots:
        starts.append(at)
        at += shot_beats(s) * period
    notes = []
    for k, note in sorted((edits.get("notes") or {}).items(), key=lambda x: int(x[0])):
        k = int(k)
        if 0 <= k < len(shots) and str(note).strip():
            m = next((p for p in pool if p.key == shots[k].key), None)
            label = moment_label(ctx.facts, m.clip, m.start, m.end) if m else shots[k].key
            notes.append(f"Shot {k + 1} ({starts[k]:.1f}-{starts[k] + shot_beats(shots[k]) * period:.1f} s, now "
                         f"'{label}'): {' '.join(str(note).split())[:300]}")
    for k, choice in (edits.get("swap") or {}).items():
        k = int(k)
        short, _, n = str(choice).partition("#")
        f = ctx.facts.get(short)
        if not (0 <= k < len(shots)) or f is None or not n.isdigit() or not 1 <= int(n) <= len(f.moments):
            continue
        m = f.moments[int(n) - 1]
        key = f"{short}_{n}_swap"
        if key not in keys:
            pool.append(PoolMoment(key=key, clip=short, start=m["start"], end=m["end"], peak=m.get("peak")))
            keys.add(key)
        shots[k] = shots[k].model_copy(update={"key": key})
    for k, way in sorted((edits.get("move") or {}).items(), key=lambda x: int(x[0])):
        k, j = int(k), int(k) + (1 if int(way) > 0 else -1)
        if 0 <= k < len(shots) and 0 <= j < len(shots):  # the two shots trade places, each with its own length and kind
            shots[k], shots[j] = shots[j], shots[k]
    for k in sorted({int(x) for x in edits.get("drop") or []}, reverse=True):
        if not 0 <= k < len(shots) or len(shots) < 3:
            continue
        gone = shots.pop(k)
        n = k - 1 if k > 0 else 0  # its time goes to the shot before (or after, for the first)
        near = shots[n]
        total = shot_beats(near) + shot_beats(gone)
        shots[n] = near.model_copy(update={"kind": "cut" if near.kind == "stutter" else near.kind, "beats": total,
                                           "count": 1})
    used = {s.key for s in shots}
    return plan.model_copy(update={"plan": shots, "pool": [m for m in pool if m.key in used]}), notes
