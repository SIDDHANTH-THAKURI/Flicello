"""Versions of a finished film: each one shown shot by shot, and changes made to exactly the shots they name.

Before this, a change ("swap 9 and 10") went to the AI with the whole plan and "keep what works", and the AI planned
the film again: shots they liked changed, a good shot was traded for a worse one, and a new version was often worse
than the one before it (the Aura Farming film, 21 Oct: 19 versions; the best was v16, then 8, 3 and 4 out of 10).

Now:
- every version keeps its record: its saved edit list (out/v{N}-{shape}.edl.json) has each shot's clip and first
  frame. `pinned` writes those frames back into the plan it was made from, so a shot nobody named plays frame for
  frame as it did;
- `strip` numbers the shots the way the person sees them (a stutter is one shot, played in pieces) with a frame each;
- `apply` makes the changes by code: remove, replace (with another moment, or a shot exactly as it was in an earlier
  version), swap, move, longer, shorter, speed. Only a stretch they want made again ("fast cuts on every beat from
  shot 10") goes back to the AI, and only that stretch (`redo`, see Brain.redo_range).
The cuts stay on the beats they were on: pictures move between the shots, the rhythm stays.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..edit.timeline import build_timeline
from ..schemas import EDL
from .board import moment_label, still
from .brain.context import logged_at, people_in
from .brain.schemas import ChangeOp, ChangeRequest, DesignPlan, PlanShot, PoolMoment, RangePlan

SLOWMO_FPS = 47.0  # (as in brain.core) real slow motion only from 50/60 fps footage
MIN_BEATS = 0.5  # the shortest shot a change may leave
CONTENT = ("key", "src_in", "zoom", "zoom_center", "flip", "frame_x", "peak_beat", "crack_db")  # what a shot shows
FRESH = {k: None for k in CONTENT if k != "key"}  # a new picture: the tool picks its frames


def span(s: PlanShot) -> float:
    """Beats a shot lasts (a stutter: all its pieces)."""
    return s.beats * (s.count if s.kind == "stutter" else 1)


def slot_ids(plan: DesignPlan) -> list[list[str]]:
    """The template slot ids of each plan shot, in order: a stutter is several (s15, s16, s17)."""
    out, n = [], 0
    for s in plan.plan:
        pieces = s.count if s.kind == "stutter" else 1
        out.append([f"s{n + k + 1:02d}" for k in range(pieces)])
        n += pieces
    return out


def edl_path(out_dir: Path, version: int, shape: str) -> Path:
    return out_dir / f"v{version}-{shape.replace(':', 'x')}.edl.json"


def load_edl(out_dir: Path, version: int, shapes: list[str]) -> tuple[dict | None, dict | None]:
    """The version's edit list (the first shape that has one) and its 9:16 one when there is one (for framing)."""
    first, tall = None, None
    for shape in shapes + [x for x in ("16:9", "9:16") if x not in shapes]:
        path = edl_path(out_dir, version, shape)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            first = first or data
            if shape == "9:16":
                tall = data
    return first, tall


def pinned(plan: DesignPlan, edl: dict | None, tall: dict | None = None) -> DesignPlan:
    """The plan with every shot's frames as the version really showed them: its first frame, punch-in, mirror and (in
    9:16) where the crop sat, and the beat the film started on. Shots the edit list doesn't have (folded away as too
    short when it was made) keep the tool's own choice."""
    if edl is None:
        return plan
    segs = {s["id"]: s for s in edl.get("segments", [])}
    tall_segs = {s["id"]: s for s in (tall or {}).get("segments", [])}
    shots = []
    for shot, ids in zip(plan.plan, slot_ids(plan)):
        seg = next((segs[i] for i in ids if i in segs), None)
        if seg is None:
            shots.append(shot)
            continue
        upd = {"src_in": round(float(seg["src_in"]), 3), "zoom": float(seg.get("zoom") or 1.0),
               "zoom_center": [float(x) for x in (seg.get("zoom_center") or (0.5, 0.5))], "flip": bool(seg.get("flip"))}
        t = next((tall_segs[i] for i in ids if i in tall_segs), None)
        if t is not None and t.get("frame_center"):
            upd["frame_x"] = round(float(t["frame_center"][0]), 3)
        shots.append(shot.model_copy(update=upd))
    music = edl.get("music") or {}
    start = music.get("start") if music.get("path") else None
    return plan.model_copy(update={"plan": shots, "start_at": round(float(start), 3) if start is not None else plan.start_at})


# ---------------------------------------------------------------- the shot strip

def _moment_id(f, start: float, end: float) -> str:
    """The logged moment ('c03#2') a stretch of a clip sits in (the one it overlaps most)."""
    best, n = 0.0, 0
    for k, m in enumerate(f.moments):
        o = min(end, m["end"]) - max(start, m["start"])
        if o > best:
            best, n = o, k + 1
    return f"{f.short}#{n}" if n else f.short


def strip(plan: DesignPlan, edl: dict, facts: dict, period: float, drop_beat: int | None, stills: Path,
          sources: dict[str, Path], shape: str, name: str) -> list[dict]:
    """One card per shot of a version, numbered as the person counts them: when it plays (film seconds), how long,
    what it shows, the frame it shows (a still from the shot itself), and whether it starts on the drop."""
    model = EDL.model_validate(edl)
    timeline = build_timeline(model, defaultdict(lambda: None))
    fps = timeline.fps
    by_seg = {item.seg.id: item for item in timeline.items}
    short_of = {f.clip_id: short for short, f in facts.items()}
    cards, pos = [], 0.0
    for n, (shot, ids) in enumerate(zip(plan.plan, slot_ids(plan)), start=1):
        items = [by_seg[i] for i in ids if i in by_seg]
        beats = span(shot)
        if not items:
            pos += beats
            continue
        first = items[0]
        seg = first.seg
        short = short_of.get(seg.clip_id, seg.clip_id.split("-")[0])
        f = facts.get(short)
        t = seg.src_in + 0.35 * max(0.0, seg.src_out - seg.src_in)
        thumb = f"{name}-shot-{n:02d}.jpg"
        lm = logged_at(f, seg.src_in, seg.src_out) if f else None
        src = sources.get(short)
        ok = src is not None and still(src, t, shape, stills / thumb, tuple(seg.frame_center))
        cards.append({
            "n": n, "start": round((first.start + first.trans_frames / 2) / fps, 2), "beats": beats, "kind": shot.kind,
            "count": shot.count if shot.kind == "stutter" else 1, "clip": short,
            "moment": _moment_id(f, seg.src_in, seg.src_out) if f else short,
            "what": moment_label(facts, short, seg.src_in, seg.src_out) if f else short,
            "thumb": thumb if ok else "", "drop": drop_beat is not None and abs(pos - drop_beat) < 1e-6,
            "slow": seg.speed < 0.99, "src": round(seg.src_in, 2),
            "place": (lm or {}).get("place") or "", "people": people_in(lm) if lm else "unknown"})
        pos += beats
    total = timeline.duration
    for k, c in enumerate(cards):
        c["end"] = cards[k + 1]["start"] if k + 1 < len(cards) else round(total, 2)
    return cards


# ---------------------------------------------------------------- changes, by code

@dataclass
class Slot:
    shot: PlanShot
    orig: int | None  # its number in the version they watched (None: added by a change)
    redo: int | None = None  # the change (index) that has the AI make this stretch again
    changed: bool = False


@dataclass
class Applied:
    plan: DesignPlan
    lines: list[str] = field(default_factory=list)  # what was done, in plain words
    redo: list[tuple[int, int, str]] = field(default_factory=list)  # (first, last) shot index of plan.plan, what they want
    bans: list[dict] = field(default_factory=list)  # scenes they never want again: clip, start, end, what
    skipped: list[str] = field(default_factory=list)  # changes that could not be made, and why
    touched: set[int] = field(default_factory=set)  # indexes in plan.plan that changed (the rest are as they were)


def play_speed(s: PlanShot, fps: float) -> float | None:
    """How fast a shot plays its clip (None: a ramp, which has no single speed)."""
    if s.kind == "ramp":
        return None
    if s.kind in ("hold", "hit"):
        return 0.5 if fps >= SLOWMO_FPS else 1.0
    return 1.0


def _fps(ctx, pool: dict, s: PlanShot) -> float:
    m = pool.get(s.key)
    f = ctx.facts.get(m.clip) if m else None
    return float(f.fps) if f else 30.0


def apply(plan: DesignPlan, ctx, ops: list[ChangeOp], earlier: dict[int, DesignPlan] | None = None,
          labels: dict[int, str] | None = None) -> Applied:
    """The changes, made by code on a (pinned) plan. Shot numbers are those of the version they watched, whatever
    the changes before did. A shot no change names keeps its picture, its frames and its beats."""
    earlier = earlier or {}
    labels = labels or {}
    period = ctx.beats.period
    pool = {m.key: m for m in plan.pool}
    slots = [Slot(s, i + 1) for i, s in enumerate(plan.plan)]
    done = Applied(plan)
    total = len(slots)

    def name(n: int) -> str:
        what = labels.get(n, "")
        return f"shot {n}" + (f" ({what[:60]})" if what else "")

    def at(n: int | None) -> int | None:
        return next((i for i, x in enumerate(slots) if x.orig == n), None) if n is not None else None

    def on_drop() -> int | None:
        """The index of the shot that starts on the drop now (None: no drop, or no shot starts there)."""
        if ctx.beats.drop is None:
            return None
        pos = 0.0
        for q, x in enumerate(slots):
            if abs(pos - ctx.beats.drop) < 1e-6:
                return q
            pos += span(x.shot)
        return None

    def speed_of(x: Slot) -> float | None:
        return play_speed(x.shot, _fps(ctx, pool, x.shot))

    def set_beats(x: Slot, beats: float) -> None:
        s = x.shot
        if s.kind == "stutter":
            pieces = beats / s.beats
            if abs(pieces - round(pieces)) < 1e-6 and round(pieces) >= 2:
                x.shot = s.model_copy(update={"count": int(round(pieces))})
            else:  # no longer a whole number of pieces: a plain shot of that length
                x.shot = s.model_copy(update={"kind": "cut", "beats": beats, "count": 1})
        else:
            x.shot = s.model_copy(update={"beats": beats})
        x.changed = True

    def move_head(x: Slot, beats: float) -> None:
        """The shot starts `beats` later (negative: earlier); its later frames stay where they were in the music."""
        sp = speed_of(x)
        if x.shot.src_in is None:
            return
        if sp is None:  # a ramp: the tool finds its start again
            x.shot = x.shot.model_copy(update={"src_in": None})
            return
        x.shot = x.shot.model_copy(update={"src_in": round(max(0.0, x.shot.src_in + beats * period * sp), 3)})

    def ban(x: Slot, why: str) -> None:
        m = pool.get(x.shot.key)
        f = ctx.facts.get(m.clip) if m else None
        if m is None or f is None:
            return
        a, b = m.start, m.end
        for lm in f.moments:  # the whole logged moment it comes from
            if min(b, lm["end"]) - max(a, lm["start"]) > 0.5 * (b - a):
                a, b = min(a, lm["start"]), max(b, lm["end"])
        done.bans.append({"clip": m.clip, "start": round(a, 2), "end": round(b, 2),
                          "what": (labels.get(x.orig or 0) or why)[:120]})

    def new_key(base: str) -> str:
        key, k = base, 2
        while key in pool:
            key, k = f"{base}_{k}", k + 1
        return key

    for k, op in enumerate(ops):
        i = at(op.shot)
        if i is None:
            done.skipped.append(f"{op.op} shot {op.shot}: " + (f"there are only {total} shots" if not 1 <= op.shot <= total
                                                               else "that shot was already changed by an earlier change"))
            continue
        x = slots[i]
        if op.op == "remove":
            if len(slots) <= 1:
                done.skipped.append(f"remove shot {op.shot}: it is the only shot")
                continue
            gone = slots.pop(i)
            beats = span(gone.shot)
            if op.never_again:
                ban(gone, "removed")
            prev = (op.side or "previous") == "previous" and i > 0 or i >= len(slots)
            nb = slots[i - 1] if prev else slots[i]
            set_beats(nb, span(nb.shot) + beats)
            if not prev:
                move_head(nb, -beats)
            done.lines.append(f"Removed {name(op.shot)}" + (" (it won't come back)" if op.never_again else "")
                              + f": shot {nb.orig} holds on {beats:g} beat{'s' if beats != 1 else ''} longer")
        elif op.op in ("longer", "shorter"):
            beats = round(float(op.beats or 1.0) * 2) / 2
            if beats <= 0:
                done.skipped.append(f"{op.op} shot {op.shot}: give a number of beats")
                continue
            side = op.side or "next"
            j = i + 1 if side == "next" else i - 1
            if not 0 <= j < len(slots):
                side = "previous" if side == "next" else "next"
                j = i + 1 if side == "next" else i - 1
            if not 0 <= j < len(slots):
                done.skipped.append(f"{op.op} shot {op.shot}: it has no neighbour to take the time from")
                continue
            if op.op == "longer":
                left, eaten = beats, []
                while left > 1e-6 and 0 <= j < len(slots):
                    nb = slots[j]
                    room = span(nb.shot) - MIN_BEATS
                    if room >= left:
                        set_beats(nb, span(nb.shot) - left)
                        if side == "next":
                            move_head(nb, left)
                        left = 0.0
                        break
                    take = span(nb.shot)  # too short to give that much: it goes, and the next one gives the rest
                    eaten.append(nb.orig)
                    slots.pop(j)
                    left -= take
                    if side == "previous":
                        i -= 1
                        j = i - 1
                    x = slots[i]
                got = beats - max(0.0, left)
                set_beats(x, span(x.shot) + got)
                if side == "previous":
                    move_head(x, -got)
                done.lines.append(f"{name(op.shot)[:1].upper()}{name(op.shot)[1:]} lasts {got:g} beat{'s' if got != 1 else ''}"
                                  f" longer (the cut {'after' if side == 'next' else 'before'} it moves)"
                                  + (f"; shot{'s' if len(eaten) > 1 else ''} {', '.join(map(str, eaten))} made room"
                                     if eaten else ""))
            else:
                beats = min(beats, span(x.shot) - MIN_BEATS)
                if beats <= 0:
                    done.skipped.append(f"shorter shot {op.shot}: it is as short as a shot can be")
                    continue
                set_beats(x, span(x.shot) - beats)
                nb = slots[j]
                set_beats(nb, span(nb.shot) + beats)
                if side == "next":
                    move_head(nb, -beats)
                else:
                    move_head(x, beats)
                done.lines.append(f"{name(op.shot)[:1].upper()}{name(op.shot)[1:]} is {beats:g} beat{'s' if beats != 1 else ''}"
                                  f" shorter; shot {nb.orig} takes the time")
        elif op.op == "swap":
            j = at(op.other)
            if j is None or j == i:
                done.skipped.append(f"swap shot {op.shot}: no other shot {op.other} to swap with")
                continue
            d = on_drop()
            if d in (i, j):  # the drop stays the music's impact: only the pictures trade places
                a = {c: getattr(slots[i].shot, c) for c in CONTENT}
                b = {c: getattr(slots[j].shot, c) for c in CONTENT}
                slots[i].shot, slots[j].shot = slots[i].shot.model_copy(update=b), slots[j].shot.model_copy(update=a)
                slots[i].changed = slots[j].changed = True
                done.lines.append(f"Shots {op.shot} and {op.other} trade pictures; the drop stays an impact on the drop")
            else:  # the two shots trade places, each with its own length and look
                slots[i], slots[j] = slots[j], slots[i]
                slots[i].changed = slots[j].changed = True
                done.lines.append(f"Shots {op.shot} and {op.other} trade places, each with its own length and look")
        elif op.op == "move":
            j = at(op.other)
            if j is None or j == i:
                done.skipped.append(f"move shot {op.shot}: there is no shot {op.other} to move it to")
                continue
            d = on_drop()
            if d is not None and min(i, j) <= d <= max(i, j):  # the drop stays put: the pictures move along it
                pictures = [{c: getattr(q.shot, c) for c in CONTENT} for q in slots]
                pictures.insert(j, pictures.pop(i))
                for q in range(min(i, j), max(i, j) + 1):
                    slots[q].shot = slots[q].shot.model_copy(update=pictures[q])
                    slots[q].changed = True
                done.lines.append(f"The picture of {name(op.shot)} moves to where shot {op.other} was; the pictures "
                                  f"between move one place {'earlier' if j > i else 'later'} (the drop stays on the drop)")
            else:
                moved = slots.pop(i)
                k2 = at(op.other)
                slots.insert(k2 + 1 if j > i else k2, moved)
                moved.changed = True
                done.lines.append(f"{name(op.shot)[:1].upper()}{name(op.shot)[1:]} moves "
                                  f"{'after' if j > i else 'before'} shot {op.other}, with its own length and look")
        elif op.op == "replace":
            old = labels.get(op.shot, "")
            if op.from_version is not None:
                ev = earlier.get(op.from_version)
                if ev is None or not op.from_shot or not 1 <= op.from_shot <= len(ev.plan):
                    done.skipped.append(f"replace shot {op.shot}: version {op.from_version} has no shot {op.from_shot}")
                    continue
                src = ev.plan[op.from_shot - 1]
                m = {p.key: p for p in ev.pool}.get(src.key)
                if m is None:
                    done.skipped.append(f"replace shot {op.shot}: that shot of version {op.from_version} can't be found")
                    continue
                key = new_key(f"{m.key}_v{op.from_version}")
                pool[key] = m.model_copy(update={"key": key})
                pic = {c: getattr(src, c) for c in CONTENT}
                pic["key"] = key
                if op.never_again:
                    ban(x, "replaced")
                x.shot = x.shot.model_copy(update=pic)
                x.changed = True
                done.lines.append(f"Shot {op.shot} shows shot {op.from_shot} of version {op.from_version} again, "
                                  f"exactly as it was" + (f" (instead of {old[:50]})" if old else ""))
            elif op.moment:
                short, _, num = op.moment.partition("#")
                f = ctx.facts.get(short)
                if f is None or not num.isdigit() or not 1 <= int(num) <= len(f.moments):
                    done.skipped.append(f"replace shot {op.shot}: there is no moment {op.moment}")
                    continue
                lm = f.moments[int(num) - 1]
                key = new_key(f"{short}_{num}")
                pool[key] = PoolMoment(key=key, clip=short, start=float(lm["start"]), end=float(lm["end"]),
                                       peak=lm.get("peak"))
                if op.never_again:
                    ban(x, "replaced")
                kind = x.shot.kind
                if kind in ("hit", "ramp") and lm.get("peak") is None:
                    kind = "cut"  # those need an action peak
                x.shot = x.shot.model_copy(update={**FRESH, "key": key, "kind": kind})
                x.changed = True
                done.lines.append(f"Shot {op.shot} now shows {lm.get('description') or op.moment}"[:200]
                                  + (f" (instead of {old[:50]})" if old else ""))
            else:  # what they want there, not which moment: the editor picks, for this shot only
                if op.never_again:
                    ban(x, "replaced")
                x.redo = k
                done.lines.append(f"Shot {op.shot}: something else" + (f": {op.want}" if op.want else "")
                                  + (f" (instead of {old[:50]})" if old else ""))
        elif op.op == "speed":
            if op.kind is None:
                done.skipped.append(f"speed shot {op.shot}: say how it should play")
                continue
            s, length = x.shot, span(x.shot)
            if op.kind == "stutter":
                count = max(2, int(round(length / 0.5)))
                x.shot = s.model_copy(update={"kind": "stutter", "count": count, "beats": round(length / count, 4)})
            else:
                upd = {"kind": op.kind, "beats": length, "count": 1}
                if op.kind == "ramp":
                    upd["src_in"] = None  # a ramp needs its own lead-in to the action
                x.shot = s.model_copy(update=upd)
            x.changed = True
            how = {"hold": "in slow motion", "cut": "at normal speed", "ramp": "as a speed ramp (rushes in, then slows)",
                   "hit": "as an impact (slow motion, flash on the beat)", "stutter": "as a stutter (quick repeats)"}
            done.lines.append(f"{name(op.shot)[:1].upper()}{name(op.shot)[1:]} plays {how[op.kind]}")
        elif op.op == "redo":
            j = at(op.other) if op.other is not None else i
            if j is None:
                j = i
            lo, hi = min(i, j), max(i, j)
            for q in range(lo, hi + 1):
                slots[q].redo = k
            a, b = slots[lo].orig, slots[hi].orig
            done.lines.append((f"Shots {a}-{b}" if a != b else f"Shot {a}") + " made again" + (f": {op.want}" if op.want else ""))
    # the stretches the AI makes again, in plan order
    groups: list[tuple[int, int, str]] = []
    for i, x in enumerate(slots):
        if x.redo is None:
            continue
        want = ops[x.redo].want or "something better that fits the story"
        if groups and groups[-1][1] == i - 1 and groups[-1][2] == want:
            groups[-1] = (groups[-1][0], i, want)
        else:
            groups.append((i, i, want))
    used = {x.shot.key for x in slots}
    done.plan = plan.model_copy(update={"plan": [x.shot for x in slots],
                                        "pool": [m for k, m in pool.items() if k in used]})
    done.redo = groups
    done.touched = {i for i, x in enumerate(slots) if x.changed or x.redo is not None or x.orig is None}
    return done


def merge_range(plan: DesignPlan, lo: int, hi: int, part: RangePlan) -> DesignPlan:
    """The plan with shots lo..hi (indexes) replaced by `part`'s shots; their moments join the pool (a key that is
    already taken gets a new name)."""
    pool = {m.key: m for m in plan.pool}
    rename = {}
    for m in part.pool:
        key, k = m.key, 2
        while key in pool and pool[key] != m:
            key, k = f"{m.key}_{k}", k + 1
        rename[m.key] = key
        pool[key] = m.model_copy(update={"key": key})
    shots = [s.model_copy(update={"key": rename.get(s.key, s.key), "src_in": None}) for s in part.plan]
    new = plan.plan[:lo] + shots + plan.plan[hi + 1:]
    used = {s.key for s in new}
    return plan.model_copy(update={"plan": new, "pool": [m for k, m in pool.items() if k in used]})


def range_text(plan: DesignPlan, ctx, lo: int, hi: int) -> str:
    """The film around a stretch to make again, as the editor reads it: every shot with its number, beats and what it
    shows; the stretch marked."""
    pool = {m.key: m for m in plan.pool}
    lines, pos = [], 0.0
    for i, s in enumerate(plan.plan):
        m = pool.get(s.key)
        tag = "MAKE AGAIN" if lo <= i <= hi else "keep"
        head = f"{i + 1}. [{tag}] beat {pos:g}-{pos + span(s):g} {s.kind}" + (f" x{s.count}" if s.kind == "stutter" else "")
        if m is not None:
            head += f" {m.clip} {m.start:g}-{m.end:g}: {moment_label(ctx.facts, m.clip, m.start, m.end)[:90]}"
        lines.append(head)
        pos += span(s)
    return "\n".join(lines)


def range_beats(plan: DesignPlan, lo: int, hi: int) -> tuple[float, float]:
    """(the beat the stretch starts on, how many beats it lasts)."""
    start = sum(span(s) for s in plan.plan[:lo])
    return start, sum(span(s) for s in plan.plan[lo:hi + 1])


# ---------------------------------------------------------------- reading a change without the AI

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8, "a": 1, "half": 0.5}
NUM = r"(\d+)(?:\s?(?:st|nd|rd|th)\b)?"  # "9th", and "9 th" as people type it
SHOT = r"(?:shot|clip|scene|cut)?\s*(?:no\.?|number|#)?\s*"


def read_simple(text: str, n: int) -> ChangeRequest:
    """The common, exact phrasings ('remove 11 and 12', 'swap 9 and 10', 'extend 8 by one beat', 'replace 3'), for
    when the AI can't read the request (and for the free brain). Anything else is left as a question."""
    ops, said = [], []
    t = " " + " ".join(text.lower().split()) + " "
    for m in re.finditer(rf"\bswap\s+{SHOT}{NUM}\s*(?:and|with|&|,)\s*{SHOT}{NUM}", t):
        a, b = int(m.group(1)), int(m.group(2))
        ops.append(ChangeOp(op="swap", shot=a, other=b))
        said.append(f"Shots {a} and {b} trade places, each with its own length and look")
    for m in re.finditer(rf"\b(?:remove|delete|drop|cut out|take out)\s+((?:{SHOT}{NUM}(?:\s*(?:,|and|&)\s*)?)+)", t):
        for num in re.findall(r"\d+", m.group(1)):
            ops.append(ChangeOp(op="remove", shot=int(num)))
            said.append(f"Shot {num} is removed")
    for m in re.finditer(rf"\b(?:extend|lengthen|longer|hold)\s+{SHOT}{NUM}(?:\s+(?:by|for)\s+(\d+(?:\.5)?|one|two|three|four|a|half)(?:\s+more)?\s+beats?)?", t):
        beats = m.group(2)
        k = float(WORDS.get(beats, beats)) if beats else 1.0
        ops.append(ChangeOp(op="longer", shot=int(m.group(1)), beats=k))
        said.append(f"Shot {m.group(1)} lasts {k:g} beat{'s' if k != 1 else ''} longer")
    for m in re.finditer(rf"\b(?:shorten|shorter|trim)\s+{SHOT}{NUM}(?:\s+(?:by)\s+(\d+(?:\.5)?|one|two|a|half)\s+beats?)?", t):
        beats = m.group(2)
        k = float(WORDS.get(beats, beats)) if beats else 1.0
        ops.append(ChangeOp(op="shorter", shot=int(m.group(1)), beats=k))
        said.append(f"Shot {m.group(1)} is {k:g} beat{'s' if k != 1 else ''} shorter")
    for m in re.finditer(rf"\breplace\s+((?:{SHOT}{NUM}(?:\s*(?:,|and|&)\s*)?)+)", t):
        for num in re.findall(r"\d+", m.group(1)):
            ops.append(ChangeOp(op="replace", shot=int(num), want="something else that fits"))
            said.append(f"Shot {num} shows something else")
    keep = [(o, s) for o, s in zip(ops, said) if 1 <= o.shot <= n and (o.other is None or 1 <= o.other <= n)]
    unclear = "" if keep else ("I couldn't tell which shots to change: name them by number, like 'swap 9 and 10' or "
                               "'remove 11'.")
    return ChangeRequest(ops=[o for o, _ in keep], said=[s for _, s in keep], unclear=unclear)


def check_request(req: ChangeRequest, n: int, facts: dict, earlier: dict[int, int]) -> list[str]:
    """What is wrong with a read change request (for the AI to fix): shots that don't exist, moments that don't, two
    changes that fight over one shot. `earlier`: version -> its number of shots."""
    problems = []
    if len(req.said) != len(req.ops):
        problems.append(f"`said` needs one line per change: {len(req.ops)} changes, {len(req.said)} lines")
    seen: dict[int, str] = {}
    for k, op in enumerate(req.ops, start=1):
        if not 1 <= op.shot <= n:
            problems.append(f"change {k}: there is no shot {op.shot} (the version has {n})")
        if op.op in ("swap", "move") and (op.other is None or not 1 <= op.other <= n):
            problems.append(f"change {k}: {op.op} needs `other`, a shot from 1 to {n}")
        if op.op == "redo" and op.other is not None and not 1 <= op.other <= n:
            problems.append(f"change {k}: redo's `other` must be a shot from 1 to {n}")
        if op.op in ("longer", "shorter") and (op.beats is None or op.beats <= 0 or op.beats > 32):
            problems.append(f"change {k}: {op.op} needs `beats` (0.5 to 32)")
        if op.op == "speed" and op.kind is None:
            problems.append(f"change {k}: speed needs `kind`")
        if op.op == "replace" and op.moment:
            short, _, num = op.moment.partition("#")
            if short not in facts or not num.isdigit() or not 1 <= int(num) <= len(facts[short].moments):
                problems.append(f"change {k}: moment {op.moment!r} doesn't exist (ids look like 'c02#5')")
        if op.op == "replace" and op.from_version is not None:
            if op.from_version not in earlier:
                problems.append(f"change {k}: there is no earlier version {op.from_version}")
            elif not op.from_shot or not 1 <= op.from_shot <= earlier[op.from_version]:
                problems.append(f"change {k}: version {op.from_version} has shots 1 to {earlier[op.from_version]}")
        if op.op in ("remove", "replace") and seen.get(op.shot) in ("remove", "replace"):
            problems.append(f"change {k}: shot {op.shot} is already {seen[op.shot]}d by an earlier change")
        seen.setdefault(op.shot, op.op)
    return problems


# ---------------------------------------------------------------- what the change reader is told

def clock(t: float) -> str:
    return f"{int(t) // 60}:{t % 60:04.1f}"


def how_it_plays(c: dict) -> str:
    if c["kind"] == "stutter":
        return f"stutter x{c['count']} (one picture repeated quickly)"
    return {"hold": "slow look" + (" in slow motion" if c.get("slow") else ""), "cut": "plain shot",
            "hit": "impact (flash" + (", slow motion" if c.get("slow") else "") + ")",
            "ramp": "speed ramp"}.get(c["kind"], c["kind"])


def labels_from_plan(plan: DesignPlan, facts: dict) -> list[tuple[int, str, str]]:
    """(number, moment id, what it shows) per shot of a version that has no shot strip (made before strips)."""
    pool = {m.key: m for m in plan.pool}
    out = []
    for n, s in enumerate(plan.plan, start=1):
        m = pool.get(s.key)
        f = facts.get(m.clip) if m else None
        out.append((n, _moment_id(f, m.start, m.end) if f else (m.clip if m else "?"),
                    moment_label(facts, m.clip, m.start, m.end) if m else s.key))
    return out


def unused_moments(plan: DesignPlan, facts: dict, bans: list[dict], limit: int = 30) -> list[dict]:
    """Good logged moments the version doesn't show (and they haven't ruled out), best first."""
    used = [(m.clip, m.start, m.end) for m in plan.pool if any(s.key == m.key for s in plan.plan)]
    out = []
    for short, f in facts.items():
        for k, m in enumerate(f.moments, start=1):
            a, b = float(m["start"]), float(m["end"])
            if m.get("strength", 0) < 4 or any(c == short and min(b, e) - max(a, s) > 0.5 * (b - a) for c, s, e in used):
                continue
            if any(x["clip"] == short and min(b, x["end"]) - max(a, x["start"]) > 0.25 for x in bans):
                continue
            out.append({"id": f"{short}#{k}", "seconds": round(b - a, 1), "what": m.get("description") or short,
                        "place": m.get("place") or "", "people": people_in(m),
                        "score": m.get("strength", 0) + m.get("hook", 0), "clip": short,
                        "at": m.get("peak") if m.get("peak") is not None else (a + b) / 2})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def request_text(version: int, cards: list[dict], beats, unused: list[dict], earlier: list[tuple[int, int | None, list]],
                 bans: list[dict], text: str, said_before: list[str]) -> str:
    """Everything the change reader needs: the version shot by shot, the music's hits, what else there is to show,
    what earlier versions showed, and their words."""
    period = beats.period
    lines = [f"# The version they watched: version {version}, {clock(cards[-1]['end']) if cards else '?'} long, "
             f"{len(cards)} shots, {60 / period:.0f} beats a minute (one beat = {period:.3f} s)"]
    for c in cards:
        lines.append(f"{c['n']}. {clock(c['start'])}-{clock(c['end'])} · {c['beats']:g} beats · {how_it_plays(c)} · "
                     f"{c['what'][:110]} [{c['moment']}]" + (f" · place: {c['place']}" if c.get("place") else "")
                     + (f" · people: {c['people']}" if c.get("people") not in (None, "none", "unknown") else "")
                     + (" · starts on the drop" if c.get("drop") else ""))
    hits = [b * period for b in beats.booms]
    if hits:
        lines += ["", "# The music's heavy hits (seconds into the film)", ", ".join(clock(t) for t in hits[:80])]
    if beats.drop is not None:
        lines.append(f"The drop: {clock(beats.drop * period)} (beat {beats.drop}).")
    if unused:
        lines += ["", "# Moments this version doesn't show (best first): id (seconds): what it shows"]
        lines += [f"{u['id']} ({u['seconds']:g} s): {u['what'][:110]}" + (f" · place: {u['place']}" if u.get("place") else "")
                  + (f" · people: {u['people']}" if u.get("people") not in (None, "none", "unknown") else "") for u in unused]
    if earlier:
        lines += ["", "# Earlier versions of this film (shot: [moment] what it showed)"]
        for v, liked, shots in earlier:
            lines.append(f"version {v}" + (f" (they liked it {liked}/10)" if liked else "") + ": "
                         + " · ".join(f"{n} [{mid}] {what[:40]}" for n, mid, what in shots))
    if bans:
        lines += ["", "# Scenes they never want in this film"]
        lines += [f"- {b['clip']} {b['start']:g}-{b['end']:g} s: {b.get('what', '')}" for b in bans]
    if said_before:
        lines += ["", "# What they asked for in earlier changes (for context; it's done)"]
        lines += [f"- {x[:300]}" for x in said_before[-6:]]
    lines += ["", "# What they want changed now", text.strip(), "", "Return the changes as JSON."]
    return "\n".join(lines)


# ---------------------------------------------------------------- changes picked on the shot strip (no AI)

def direct_ops(form: dict, n: int, labels: dict[int, str] | None = None) -> tuple[list[ChangeOp], list[str]]:
    """The changes picked on a version's shot strip: per shot `remove_N`, `never_N`, `longer_N` / `shorter_N`
    (beats), `swap_N` / `move_N` (another shot's number), `replace_N` (a moment id, or 'any'), `speed_N` (a kind)."""
    labels = labels or {}
    ops, said = [], []

    def get(key: str) -> str:
        return str(form.get(key) or "").strip()

    def num(value: str) -> int | None:
        return int(value) if value.isdigit() and 1 <= int(value) <= n else None

    def beats(value: str) -> float | None:
        try:
            b = round(float(value) * 2) / 2
        except ValueError:
            return None
        return b if 0 < b <= 32 else None

    for k in range(1, n + 1):
        never = get(f"never_{k}") == "1"
        what = labels.get(k, "")
        if (m := get(f"replace_{k}")):
            if m == "any":
                ops.append(ChangeOp(op="replace", shot=k, want="something the film doesn't show yet that fits",
                                    never_again=never))
                said.append(f"Shot {k} shows something else" + (" (and that scene never comes back)" if never else ""))
            elif re.fullmatch(r"c\d+#\d+", m):
                ops.append(ChangeOp(op="replace", shot=k, moment=m, never_again=never))
                said.append(f"Shot {k} shows {m} instead" + (" (and the old scene never comes back)" if never else ""))
        if (kind := get(f"speed_{k}")) in ("hold", "cut", "hit", "ramp", "stutter"):
            ops.append(ChangeOp(op="speed", shot=k, kind=kind))
            said.append(f"Shot {k} plays " + {"hold": "in slow motion", "cut": "at normal speed", "hit": "as an impact",
                                              "ramp": "as a speed ramp", "stutter": "as a stutter"}[kind])
        for op in ("longer", "shorter"):
            if (b := beats(get(f"{op}_{k}"))) is not None:
                ops.append(ChangeOp(op=op, shot=k, beats=b))
                said.append(f"Shot {k} {'lasts' if op == 'longer' else 'is'} {b:g} beat{'s' if b != 1 else ''} {op}")
        if (o := num(get(f"swap_{k}"))) is not None and o != k:
            ops.append(ChangeOp(op="swap", shot=k, other=o))
            said.append(f"Shots {k} and {o} trade places")
        if (o := num(get(f"move_{k}"))) is not None and o != k:
            ops.append(ChangeOp(op="move", shot=k, other=o))
            said.append(f"Shot {k} moves to where shot {o} is")
        if get(f"remove_{k}") == "1":
            ops.append(ChangeOp(op="remove", shot=k, never_again=never))
            said.append(f"Shot {k}{f' ({what[:50]})' if what else ''} is removed"
                        + (" and never comes back" if never else ""))
        elif never and not get(f"replace_{k}"):
            ops.append(ChangeOp(op="replace", shot=k, want="something the film doesn't show yet that fits",
                                never_again=True))
            said.append(f"Shot {k}'s scene never comes back: something else shows there")
    return ops, said


def sources_for(pipe, facts: dict) -> dict[str, Path]:
    """Where to take a still of each clip from: its proxy, else the original."""
    clips = pipe.project.clips()
    out = {}
    for short, f in facts.items():
        proxy = pipe.project.proxies / f"{f.clip_id}.mp4"
        if proxy.exists():
            out[short] = proxy
        elif f.clip_id in clips:
            out[short] = Path(clips[f.clip_id].path)
    return out


def alternatives(plan: DesignPlan, facts: dict, bans: list[dict], stills: Path, sources: dict[str, Path], shape: str,
                 limit: int = 18) -> list[dict]:
    """Good moments a version doesn't show, with a frame each (shared between versions), to swap in."""
    out = []
    for u in unused_moments(plan, facts, bans, limit):
        short, _, k = u["id"].partition("#")
        thumb = f"alt-{short}-{k}-{shape.replace(':', 'x')}.jpg"
        ok = (stills / thumb).exists() or (sources.get(short) is not None
                                           and still(sources[short], float(u["at"]), shape, stills / thumb))
        out.append({"id": u["id"], "what": u["what"], "seconds": u["seconds"], "thumb": thumb if ok else ""})
    return out
