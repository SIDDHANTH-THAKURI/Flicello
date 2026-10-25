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
from .brain.schemas import ChangeOp, ChangeRequest, DesignPlan, PlanGraphic, PlanShot, PoolMoment, RangePlan

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
    if start is not None and plan.music is not None:  # the beat grid it was cut on, without their slide of the song
        start = float(start) + plan.music.slide
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


GRAPHIC_OPS = ("title", "captions", "lowerthird", "transition", "kinetic", "callout", "route", "accent", "thing")
TITLE_STYLES = ("rise", "slam", "mask", "split")
CAPTION_STYLES = {"pop": "the word being said lights up", "karaoke": "the words light up as they're said",
                  "box": "a colour block on the word being said"}
STYLES = {"title": TITLE_STYLES, "transition": ("panels", "iris", "bars", "flash"), "callout": ("circle", "box", "arrow"),
          "accent": ("brackets", "sweep", "leak", "lines")}
WHAT = {"title": "title", "lowerthird": "name", "transition": "transition", "kinetic": "beat words",
        "callout": "callout", "route": "route", "accent": "finish", "thing": "thing"}
BEATS = {"title": 4.0, "lowerthird": 6.0, "callout": 6.0, "route": 8.0, "accent": 4.0, "transition": 1.0, "thing": 6.0}
FOLLOW_PICTURE = ("lowerthird", "callout", "accent")  # the rest keep their place in the film


def the(what: str) -> str:
    """'boat' -> 'the boat'; 'my dog', 'the bridge' and 'Sydney' as they are."""
    w = what.strip()
    return w if not w or re.match(r"(?:a|an|the|my|our|his|her|their|this|that)", w, re.I) or w.istitle() and " " not in w         else "the " + w


def article(word: str) -> str:
    return ("an " if word[:1].lower() in "aeiou" else "a ") + word


def sized(asked: float | None, was: float = 1.0) -> float:
    """How big a graphic is asked to be against the usual, kept to what still reads and fits (0.4 to 3)."""
    return round(min(3.0, max(0.4, float(asked))), 2) if asked else was


def look_words(scale: float, place: str) -> str:
    """'bigger, at the top': a graphic's size and place as the lines they read before a change is made say them."""
    size = ("much bigger" if scale >= 1.5 else "bigger" if scale > 1.05 else "much smaller" if scale <= 0.65
            else "smaller" if scale < 0.95 else "")
    where = {"top": "at the top", "middle": "in the middle", "bottom": "at the bottom"}.get(place, "")
    return ", ".join(x for x in (size, where) if x)


def change_words(was: PlanGraphic, scale: float, place: str) -> str:
    """How a graphic there changed, as they read it before it is made: 'smaller, still at the top'."""
    size = "" if abs(scale - was.scale) < 0.01 else "back to the usual size" if abs(scale - 1.0) < 0.01 else \
        "bigger" if scale > was.scale else "smaller"
    where = {"top": "at the top", "middle": "in the middle", "bottom": "at the bottom"}.get(place, "")
    if place == was.place:
        where = f"still {where}" if where and size else ""
    elif not where:
        where = "back in its usual place"
    return ", ".join(x for x in (size, where) if x) or "as it was"


def graphic_op(op: ChangeOp, marks: list[PlanGraphic], total: int, done: Applied) -> list[PlanGraphic]:
    """A graphic added, taken away, made bigger or smaller or moved (shot numbers as in the version they watched)."""
    kind = op.op
    if kind == "captions":
        kept = [g for g in marks if g.kind != "captions"]
        if op.off:
            if len(kept) == len(marks):
                done.skipped.append("there are no captions to take away")
            else:
                done.lines.append("The captions are taken away")
            return kept
        was = next((g for g in marks if g.kind == "captions"), None)
        style = op.style if op.style in CAPTION_STYLES else was.style if was and was.style in CAPTION_STYLES else "pop"
        scale, place = sized(op.scale, was.scale if was else 1.0), op.place or (was.place if was else "auto")
        look = look_words(scale, place)
        if was and op.style not in CAPTION_STYLES and (op.scale or op.place):  # the captions there, resized or moved
            done.lines.append(f"The captions are {change_words(was, scale, place)}")
        else:
            done.lines.append(f"Captions of what is said, word by word ({CAPTION_STYLES[style]}"
                              + (f"; {look}" if look else "") + ")")
        return [*kept, PlanGraphic(kind="captions", style=style, scale=scale, place=place)]
    what = WHAT[kind]
    if kind == "thing" and not op.off:
        return thing_op(op, marks, total, done)
    if op.off:
        gone = [g for g in marks if g.kind == kind and g.shot == op.shot] or [g for g in marks if g.kind == kind]
        if not gone:
            done.skipped.append(f"there is no {what} to take away")
            return marks
        done.lines.append(f"The {what}{'s are' if len(gone) > 1 else ' is'} taken away")
        return [g for g in marks if all(g is not x for x in gone)]
    if (op.scale or op.place) and not op.text.strip() and not op.want.strip():
        # bigger, smaller or somewhere else: the ones already there (on that shot, else every one of that kind)
        hit = [g for g in marks if g.kind == kind and g.shot == op.shot] or [g for g in marks if g.kind == kind]
        if not hit:
            done.skipped.append(f"there is no {what} to " + ("resize" if op.scale else "move"))
            return marks
        out = [g.model_copy(update={"scale": sized(op.scale, g.scale), "place": op.place or g.place})
               if any(g is x for x in hit) else g for g in marks]
        now = next(g for g in out if g.kind == kind and g.shot == hit[0].shot)
        done.lines.append(f"The {what}{'s are' if len(hit) > 1 else ' is'} {change_words(hit[0], now.scale, now.place)}"
                          + (f" (shot {hit[0].shot})" if len(hit) == 1 else ""))
        return out
    text = " ".join(op.text.split())[:80]
    if kind == "route":
        text = "|".join(x.strip() for x in re.split(r"\||\s+(?:to|via|then|and)\s+|,|->|\u2192", text, flags=re.I) if x.strip())
    want = " ".join(op.want.split())[:80]
    if kind in ("title", "lowerthird", "kinetic") and not text or kind == "route" and text.count("|") < 1 \
            or kind == "callout" and not (want or text):
        done.skipped.append(f"{what} on shot {op.shot}: " + ("which places?" if kind == "route" else "what should it say?"
                                                             if kind != "callout" else "what should it point at?"))
        return marks
    if not 1 <= op.shot <= total or kind == "transition" and op.shot < 2:
        done.skipped.append(f"{what} on shot {op.shot}: " + ("a transition goes into a shot after the first"
                                                             if kind == "transition" else f"there are only {total} shots"))
        return marks
    style = op.style if op.style in STYLES.get(kind, ()) else ""
    words = len(text.split())
    beats = float(op.beats) if op.beats else (float(words + 1) if kind == "kinetic" else BEATS[kind])
    said = {"title": f'A title "{text}" comes in on shot {op.shot}' + (" behind the person in it" if op.behind else ""),
            "lowerthird": f'"{text}" is named on shot {op.shot}',
            "transition": f"{article(style or 'panels').capitalize()} transition into shot {op.shot}",
            "kinetic": f'"{text}" cuts in word by word on the beat from shot {op.shot}',
            "callout": f"{article(style or 'circle').capitalize()} on {the(want or text)} in shot {op.shot}, following it"
                       + (f' ("{text}")' if text else ""),
            "route": f"A route drawing itself: {text.replace('|', ' to ')}, from shot {op.shot}",
            "accent": f"{article(style or 'brackets').capitalize()} finish on shot {op.shot}"}[kind]
    scale, place = sized(op.scale), op.place or "auto"
    look = look_words(scale, place)
    done.lines.append(said + (f" ({look})" if look else ""))
    say = " ".join(op.say.split())[:80]
    if say:
        done.lines[-1] = f'{done.lines[-1]}, as you say "{say}"'
    return [g for g in marks if not (g.kind == kind and g.shot == op.shot and g.say == say)] + [PlanGraphic(
        kind=kind, shot=op.shot, beats=beats, text=text, sub=" ".join(op.sub.split())[:60], style=style,
        want=want or (text if kind == "callout" else ""), behind=bool(op.behind) and kind == "title", say=say,
        scale=scale, place=place)]


def thing_op(op: ChangeOp, marks: list[PlanGraphic], total: int, done: Applied) -> list[PlanGraphic]:
    """Something they described, made real and moving over the film (a red ball dropping from their head)."""
    from ..motion.things import bounces_said, read

    want = " ".join((op.want or op.text).split())[:160]
    if not want:
        done.skipped.append(f"a thing on shot {op.shot}: what should it be?")
        return marks
    if not 1 <= op.shot <= total:
        done.skipped.append(f"a thing on shot {op.shot}: there are only {total} shots")
        return marks
    seen = read(want)
    motion = op.style if op.style in ("drop", "bounce", "pop", "fly", "float", "orbit", "spin", "shake", "burst", "rain",
                                      "grow") else seen["motion"] or "pop"
    anchor = " ".join(op.anchor.split())[:80]
    say = " ".join(op.say.split())[:80]
    beats = float(op.beats) if op.beats else (8.0 if motion in ("drop", "bounce", "rain") else BEATS["thing"])
    done.lines.append(f"{want[:1].upper()}{want[1:]}: drawn and moving ({motion})"
                      + (f" from {anchor}" if anchor else "") + (f', as you say "{say}"' if say else f" on shot {op.shot}")
                      + (f', with the words "{op.text}"' if op.text and op.text != want else ""))
    return [*marks, PlanGraphic(kind="thing", shot=op.shot, beats=beats, text=op.text if op.text != want else "",
                                style=motion, want=want, anchor=anchor, say=say, count=seen["count"],
                                bounces=bounces_said(want), accent=seen["color"])]


def placed(marks: list[PlanGraphic], slots: list, total: int, keys: dict[int, str] | None = None) -> list[PlanGraphic]:
    """The graphics on the shots as they are now. A title, a transition or a route keeps its place in the film (the
    opening stays the opening when pictures move); a name, a callout or a finish follows its picture (the moment it
    shows, `keys`: shot number -> moment), and a callout whose picture was replaced goes (what it pointed at is gone).
    Either moves up when shots before it are gone."""
    final = {x.orig: q + 1 for q, x in enumerate(slots) if x.orig is not None}
    removed = set(range(1, total + 1)) - set(final)
    now = {x.shot.key: q + 1 for q, x in reversed(list(enumerate(slots)))}  # the first shot showing each moment

    def shifted(n: int) -> int:
        return max(1, min(len(slots), n - sum(1 for r in removed if r < n)))

    out = []
    for g in marks:
        if g.kind == "captions":
            out.append(g)
            continue
        if g.kind in FOLLOW_PICTURE and keys and g.shot in keys:
            if keys[g.shot] in now:
                out.append(g.model_copy(update={"shot": now[keys[g.shot]]}))
            elif g.kind != "callout":
                out.append(g.model_copy(update={"shot": shifted(g.shot)}))
            continue
        if g.kind in FOLLOW_PICTURE and g.shot in final:
            out.append(g.model_copy(update={"shot": final[g.shot]}))
        else:
            out.append(g.model_copy(update={"shot": shifted(g.shot)}))
    return out


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
    marks = list(plan.graphics)  # numbered as in the version they watched until placed() at the end

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

    style = plan.style
    for k, op in enumerate(ops):
        if op.op == "style":
            from . import styles

            if op.want not in styles.STYLES:
                done.skipped.append(f"there is no {op.want!r} style")
                continue
            marks = styles.apply(plan.model_copy(update={"graphics": marks}), op.want, ctx.facts, ctx.beats.drop,
                                 op.text).graphics
            style = op.want
            done.lines.append(f"The film in the {styles.STYLES[op.want].name} style: {styles.STYLES[op.want].says}")
            continue
        if op.op in GRAPHIC_OPS:
            marks = graphic_op(op, marks, total, done)
            continue
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
            if getattr(ctx, "speech", False):  # a talk: what was said there goes, and the film is that much shorter
                done.lines.append(f"Cut {name(op.shot)}: the film is {beats * period:.1f} s shorter, the words around "
                                  "it join up")
                continue
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
            if getattr(ctx, "speech", False):  # a talk: the shot's own end moves (its words stay whole at its start)
                new = span(x.shot) + (beats if op.op == "longer" else -beats)
                if new < MIN_BEATS:
                    done.skipped.append(f"shorter shot {op.shot}: it would be gone; cut it instead")
                    continue
                set_beats(x, new)
                done.lines.append(f"{name(op.shot)} {'holds' if op.op == 'longer' else 'ends'} {beats * period:.1f} s "
                                  f"{'longer' if op.op == 'longer' else 'sooner'}")
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
                                        "pool": [m for k, m in pool.items() if k in used],
                                        "style": style, "graphics": placed(marks, slots, total,
                                                           {i + 1: x.key for i, x in enumerate(plan.plan)})})
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
    # graphics after the stretch move with their shots; one inside it stays as far into it as it was (or its end)
    grown = len(shots) - (hi - lo + 1)
    graphics = [g if g.kind == "captions" or g.shot <= lo else g.model_copy(update={
        "shot": g.shot + grown if g.shot > hi + 1 else lo + 1 + min(g.shot - lo - 1, max(0, len(shots) - 1))})
        for g in plan.graphics]
    return plan.model_copy(update={"plan": new, "pool": [m for k, m in pool.items() if k in used],
                                   "graphics": graphics})


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
CAPTION_WORDS = r"(?:captions?|subtitles?|subs)"
CAPTIONS_OFF = re.compile(rf"\b(?:no|remove|delete|drop|hide|turn\s+off|take\s+(?:off|away|out)|get\s+rid\s+of|without)"
                          rf"\s+(?:the\s+|all\s+the\s+|those\s+)?{CAPTION_WORDS}\b", re.I)
CAPTIONS_ON = re.compile(rf"\b(?:add|put|turn\s+on|show|give|want|need|with|make)\b[^.?!]*?\b{CAPTION_WORDS}\b|"
                         rf"\b{CAPTION_WORDS}\s+(?:on|please)\b|^\s*{CAPTION_WORDS}\s*[.!]?\s*$", re.I)
TITLE_OFF = re.compile(r"\b(?:no|remove|delete|drop|hide|take\s+(?:off|away|out)|get\s+rid\s+of)\s+(?:the\s+)?title\b",
                       re.I)
TITLE_ON = re.compile(r"\b(?:add|put|give\s+it|make)\s+(?:an?\s+|the\s+)?(?:(?:rise|slam|mask|split|big|bold|opening|intro)\s+)?"
                      r"title\b(?P<rest>.*)$", re.I)
QUOTED = re.compile(r"[\"\u201c\u201d'\u2018\u2019](.+?)[\"\u201c\u201d'\u2018\u2019](?:\s|$|[.,!?])")
TITLE_AT = re.compile(r"\s+(?:on|at|over|in)\s+(?:(?:the\s+)?(?:start|beginning|opening)|(?:shot|clip)\s*(\d+))\s*[.!]?\s*$",
                      re.I)


SHOT_AT = r"(?:on|at|into|before|in|over|to|for)\s+(?:shot|clip|scene)\s*(\d+)"
TRANSITION_ON = re.compile(r"\b(?:add|put|use|want|give)\b[^.?!]*?\b(?:(panels?|iris|bars?|flash|whip|wipe)\s+)?transition\b"
                           rf"[^.?!]*?{SHOT_AT}", re.I)
KINETIC_ON = re.compile(r"\b(?:kinetic\s+(?:text|type|words)|beat\s+(?:text|words)|words?\s+on\s+the\s+beats?)\b", re.I)
CALLOUT_ON = re.compile(r"\b(circle|point\s+(?:at|to)|highlight|mark|box|arrow\s+(?:at|to|on))\s+(?:the\s+|that\s+)?(.+?)\s+"
                        rf"{SHOT_AT}", re.I)
ROUTE_ON = re.compile(r"\b(?:route|map|path|journey|trip)\b[^.?!]*?\bfrom\s+(.+?)\s+to\s+(.+?)(?:\s+via\s+(.+?))?"
                      rf"(?:\s+{SHOT_AT})?\s*[.!]?$", re.I)
ACCENT_ON = re.compile(r"\b(viewfinder|brackets|camera\s+frame|light\s+leaks?|leaks?|light\s+sweep|sheen|beat\s+lines|lines\s+on"
                       rf"\s+the\s+beats?)\b(?:[^.?!]*?{SHOT_AT})?", re.I)
GRAPHIC_OFF = re.compile(r"\b(?:no|remove|delete|drop|hide|take\s+(?:off|away|out)|get\s+rid\s+of)\s+(?:the\s+|all\s+the\s+|those\s+)?"
                         r"(transitions?|routes?|maps?|callouts?|circles?|brackets|viewfinder|light\s+leaks?|leaks?|beat\s+words|"
                         r"kinetic\s+(?:text|type))\b", re.I)
OFF_KIND = {"transition": "transition", "route": "route", "map": "route", "callout": "callout", "circle": "callout",
            "brackets": "accent", "viewfinder": "accent", "light": "accent", "leak": "accent", "beat": "kinetic",
            "kinetic": "kinetic"}
ACCENT_STYLE = {"viewfinder": "brackets", "brackets": "brackets", "camera": "brackets", "light leak": "leak",
                "leak": "leak", "light sweep": "sweep", "sheen": "sweep", "beat lines": "lines", "lines": "lines"}


def _quoted(text: str) -> str:
    m = QUOTED.search(text + " ")
    return m.group(1).strip() if m else ""


def _title_words(rest: str) -> tuple[str, int]:
    """What a title should say and the shot it comes in on, from what follows 'add a title' (default: shot 1)."""
    shot = 1
    if m := TITLE_AT.search(rest):
        shot = int(m.group(1)) if m.group(1) else 1
        rest = rest[:m.start()]
    if m := QUOTED.search(rest + " "):
        return m.group(1).strip(), shot
    m = re.match(r"\s*(?:saying|that\s+says|which\s+says|reading|that\s+reads|called|with|of|:)\s*(.+)$", rest, re.I)
    return (m.group(1).strip(" .!") if m else ""), shot


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
    raw = " ".join(text.split())
    from . import styles

    if sid := styles.read(raw):
        ops.append(ChangeOp(op="style", shot=1, want=sid))
        said.append(f"The film in the {styles.STYLES[sid].name} style: {styles.STYLES[sid].says}")
    if CAPTIONS_OFF.search(raw):
        ops.append(ChangeOp(op="captions", shot=1, off=True))
        said.append("The captions are taken away")
    elif CAPTIONS_ON.search(raw):
        style = "karaoke" if re.search(r"karaoke", raw, re.I) else "box" if re.search(r"\b(?:box|boxed|highlight)", raw, re.I) \
            else "pop"
        ops.append(ChangeOp(op="captions", shot=1, style=style))
        said.append(f"Captions of what is said, word by word ({CAPTION_STYLES[style]})")
    if TITLE_OFF.search(raw):
        ops.append(ChangeOp(op="title", shot=1, off=True))
        said.append("The title is taken away")
    elif m := TITLE_ON.search(raw):
        words, at_shot = _title_words(m.group("rest"))
        if words:
            style = next((st for st in TITLE_STYLES if re.search(rf"\b{st}", raw, re.I)), "")
            ops.append(ChangeOp(op="title", shot=at_shot, text=words, style=style))
            said.append(f'A title "{words}" comes in on shot {at_shot}')
    if m := TITLE_ON.search(raw):  # "... behind me": the person stands in front of it
        if ops and ops[-1].op == "title" and re.search(r"\bbehind\s+(?:me|him|her|them|us|the\s+(?:person|people|guy|girl))\b",
                                                          raw, re.I):
            ops[-1] = ops[-1].model_copy(update={"behind": True})
            said[-1] += ", behind the person in it"
    for m in GRAPHIC_OFF.finditer(raw):
        kind = OFF_KIND[m.group(1).lower().split()[0].rstrip("s")] if m.group(1).lower().split()[0].rstrip("s") in OFF_KIND \
            else OFF_KIND.get(m.group(1).lower().split()[0], "accent")
        ops.append(ChangeOp(op=kind, shot=1, off=True))
        said.append(f"The {WHAT[kind]} is taken away")
    if not GRAPHIC_OFF.search(raw):
        for m in TRANSITION_ON.finditer(raw):
            style = {"panel": "panels", "panels": "panels", "iris": "iris", "bar": "bars", "bars": "bars", "flash": "flash",
                     "whip": "panels", "wipe": "bars"}.get((m.group(1) or "panels").lower(), "panels")
            ops.append(ChangeOp(op="transition", shot=int(m.group(2)), style=style))
            said.append(f"{article(style).capitalize()} transition into shot {m.group(2)}")
        if KINETIC_ON.search(raw) and (words := _quoted(raw)):
            at = re.search(SHOT_AT, raw, re.I)
            ops.append(ChangeOp(op="kinetic", shot=int(at.group(1)) if at else 1, text=words))
            said.append(f'"{words}" cuts in word by word on the beat from shot {ops[-1].shot}')
        for m in CALLOUT_ON.finditer(raw):
            verb = m.group(1).lower()
            style = "box" if verb in ("box", "mark") else "arrow" if verb.startswith(("point", "arrow")) else "circle"
            want = m.group(2).strip(" '\"\u201c\u201d")
            label = _quoted(raw) or want[:1].upper() + want[1:]
            ops.append(ChangeOp(op="callout", shot=int(m.group(3)), want=want, text=label, style=style))
            said.append(f"{article(style).capitalize()} on {the(want)} in shot {m.group(3)}, following it")
        if m := ROUTE_ON.search(raw):
            stops = [m.group(1), *(m.group(3).split(",") if m.group(3) else []), m.group(2)]
            stops = [x.strip(" .!'\"").title() if x.strip().islower() else x.strip(" .!'\"") for x in stops if x.strip()]
            ops.append(ChangeOp(op="route", shot=int(m.group(4)) if m.group(4) else 1, text="|".join(stops)))
            said.append(f"A route drawing itself: {' to '.join(stops)}, from shot {ops[-1].shot}")
        for m in ACCENT_ON.finditer(raw):
            key = " ".join(m.group(1).lower().split())
            style = next((v for k, v in ACCENT_STYLE.items() if key.startswith(k)), "brackets")
            ops.append(ChangeOp(op="accent", shot=int(m.group(2)) if m.group(2) else 1, style=style))
            said.append(f"{article(style).capitalize()} finish on shot {ops[-1].shot}")
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
        if op.op in ("title", "lowerthird") and not op.off and not op.text.strip() and not (op.scale or op.place):
            problems.append(f"change {k}: {op.op} needs `text`, the words to show")
        if op.op == "style":
            from . import styles

            if op.want not in styles.STYLES:
                problems.append(f"change {k}: style needs `want`, one of {', '.join(styles.STYLES)}")
            continue
        if op.op in GRAPHIC_OPS:
            continue
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


def graphics_text(graphics: list[PlanGraphic]) -> list[str]:
    """The film's graphics, one line each, as the change reader and the editor read them."""
    out = []
    for g in graphics:
        look = look_words(g.scale, g.place)
        if g.kind == "captions":
            out.append(f"- captions of what is said ({g.style or 'pop'}" + (f", {look}" if look else "") + ")")
        else:
            out.append(f"- {WHAT.get(g.kind, g.kind)} on shot {g.shot} for {g.beats:g} beats: "
                       f"\"{g.text}\"" + (f" / \"{g.sub}\"" if g.sub else "") + (f" ({g.style})" if g.style else "")
                       + (f" on {g.want}" if g.kind == "callout" and g.want else "") + (" behind the person" if g.behind else "")
                       + (f", {look}" if look else ""))
    return out


def request_text(version: int, cards: list[dict], beats, unused: list[dict], earlier: list[tuple[int, int | None, list]],
                 bans: list[dict], text: str, said_before: list[str], graphics: list[PlanGraphic] | None = None) -> str:
    """Everything the change reader needs: the version shot by shot (and its graphics), the music's hits, what else
    there is to show, what earlier versions showed, and their words."""
    period = beats.period
    lines = [f"# The version they watched: version {version}, {clock(cards[-1]['end']) if cards else '?'} long, "
             f"{len(cards)} shots, {60 / period:.0f} beats a minute (one beat = {period:.3f} s)"]
    for c in cards:
        lines.append(f"{c['n']}. {clock(c['start'])}-{clock(c['end'])} · {c['beats']:g} beats · {how_it_plays(c)} · "
                     f"{c['what'][:110]} [{c['moment']}]" + (f" · place: {c['place']}" if c.get("place") else "")
                     + (f" · people: {c['people']}" if c.get("people") not in (None, "none", "unknown") else "")
                     + (" · starts on the drop" if c.get("drop") else ""))
    lines += ["", "# Its graphics", *(graphics_text(graphics or []) or ["- none"])]
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
