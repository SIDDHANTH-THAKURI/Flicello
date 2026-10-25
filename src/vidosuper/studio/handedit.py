"""Edits by hand on the timeline (the editor page, Cello's `timeline` tool, the work agent): exact changes, in film
seconds, to the plan of what is on screen, made at once as a draft.

- trim: a shot's start or end, earlier or later. Ripple: everything after it moves with it. Roll: the neighbour takes up
  the difference, so the film keeps its length.
- split: one shot becomes two at a moment. delete: a shot goes (ripple: the film closes up; lift: the shot before holds
  on). move: a shot goes to another place. duplicate: it plays twice.
- speed: how fast a shot plays its frames (ripple: the same frames, so it gets longer or shorter). frame: where the
  picture sits in a vertical crop, its punch-in, mirror and an animated zoom. keys: framing keyframes (zoom and the
  point at the centre, at moments of the shot, eased between). volume: its own sound. blend: how it comes in.
- graphic: a graphic's words, style, size, place and film seconds; or it goes.
- over: a shot laid over the picture for a stretch (B-roll, a cutaway): added, moved, trimmed, reframed or taken away;
  the film's own sound goes on under it.
- music: the song's slide against the picture, its level, fades, dips under speech, level points and cuts.

Shots are numbered as on screen (1 = the first). Every shot keeps its exact frames (the plan is pinned to the edit list
it was made from), so an edit changes only what it names; graphics placed by hand and the music's edits keep their
moment when the film closes up or opens out after an edit."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from .brain.schemas import DesignPlan, MusicEdit, OverShot, PlanGraphic, PlanShot

SLOWMO_FPS = 47.0  # (as in brain.core) real slow motion only from 50/60 fps footage
MIN_SHOT = 0.25  # seconds: shorter shots don't read (and the renderer folds them away)
BLENDS = {"cut": "a cut", "dissolve": "a dissolve", "fadeblack": "a dip to black", "fadewhite": "a dip to white",
          "smoothleft": "a slide to the left", "smoothright": "a slide to the right", "zoomin": "a zoom through"}
FOLLOWS = ("lowerthird", "callout", "accent", "thing")  # graphics that belong to their picture (the rest to the moment)
WHAT = {"title": "title", "captions": "captions", "lowerthird": "name", "transition": "transition",
        "kinetic": "beat words", "callout": "callout", "route": "route", "accent": "finish", "thing": "drawn thing",
        "scene": "designed scene"}
STYLES = {"title": ("rise", "slam", "mask", "split"), "captions": ("pop", "karaoke", "box"),
          "transition": ("panels", "iris", "bars", "flash"), "callout": ("circle", "box", "arrow"),
          "accent": ("brackets", "sweep", "leak", "lines")}


class EditError(ValueError):
    """An edit that can't be made, said so they can do it another way."""


class EditOp(BaseModel):
    op: Literal["trim", "split", "delete", "move", "duplicate", "speed", "frame", "keys", "volume", "blend", "graphic",
                "over", "music"]
    shot: int = Field(0, description="The shot, numbered as on screen (1 = the first).")
    edge: Literal["start", "end"] = Field("end", description="trim: which end of the shot.")
    seconds: float = Field(0.0, description=(
        "trim: how much longer (+) or shorter (-) at that end; split: seconds into the shot; blend: its length."))
    ripple: bool = Field(True, description=(
        "trim / delete / speed: what comes after moves with it (false: the neighbour takes it up, the film keeps its "
        "length; delete: the shot before holds on)."))
    to: int = Field(0, description="move: the place it goes to, as a shot number.")
    factor: float | None = Field(None, description="speed: how fast, 0.25 to 4 (1 = as filmed).")
    frame_x: float | None = Field(None, description="frame: where the subject sits across a vertical crop, 0-1.")
    zoom: float | None = Field(None, description="frame: punch-in, 1 to 2.5.")
    zoom_center: list[float] | None = Field(None, description="frame: the punch-in's centre [x, y], 0-1.")
    flip: bool | None = Field(None, description="frame: mirrored.")
    push: float | None = Field(None, description="frame: an animated zoom over the shot, -0.4 (pulls out) to 0.4 (pushes in).")
    keys: list[list[float]] | None = Field(None, description=(
        "keys: all of the shot's framing keyframes, as they should be now: [seconds into the shot, zoom 1-3, x, y "
        "(the point of the picture at the centre, 0-1)]; empty: none."))
    gain_db: float | None = Field(None, description="volume: its own sound, dB (-30 to 12; 0 = as filmed).")
    mute: bool | None = Field(None, description="volume: its own sound off.")
    style: str = Field("", description=(
        "blend: cut, dissolve, fadeblack, fadewhite, smoothleft, smoothright or zoomin; graphic: its style."))
    index: int = Field(-1, description="graphic / over: which one, by its index on the timeline (over: -1 adds one).")
    clip: str | None = Field(None, description="over: the clip it shows, by its short id ('c03').")
    src_in: float | None = Field(None, description="over: the clip second it starts at.")
    text: str | None = Field(None, description="graphic: its words.")
    sub: str | None = Field(None, description="graphic: the line under them.")
    scale: float | None = Field(None, description="graphic: how big against the usual, 0.4 to 3.")
    place: Literal["auto", "top", "middle", "bottom"] | None = Field(None, description="graphic: where on the frame.")
    at: float | None = Field(None, description="graphic / over: the film second it comes in.")
    until: float | None = Field(None, description="graphic / over: the film second it goes.")
    remove: bool = Field(False, description="graphic / over: take it away.")
    music: MusicEdit | None = Field(None, description="music: the whole of how the music is edited, as it should be now.")


@dataclass
class Edited:
    plan: DesignPlan
    lines: list[str] = field(default_factory=list)


def span(s: PlanShot) -> float:
    return s.beats * (s.count if s.kind == "stutter" else 1)


def _value(keys: list[list[float]], t: float) -> list[float]:
    """The framing [zoom, x, y] the keys give at `t` (the half-cosine ease the render and the preview use)."""
    import math

    pts = sorted(keys, key=lambda k: k[0])
    if t <= pts[0][0]:
        return list(pts[0][1:4])
    for a, b in zip(pts, pts[1:]):
        if t <= b[0]:
            u = (t - a[0]) / max(1e-3, b[0] - a[0])
            e = 0.5 - 0.5 * math.cos(math.pi * u)
            return [a[i] + (b[i] - a[i]) * e for i in (1, 2, 3)]
    return list(pts[-1][1:4])


def keys_within(keys: list[list[float]] | None, lo: float, hi: float, shift: float = 0.0, scale: float = 1.0):
    """The keyframes of the stretch [lo, hi] of a shot, as a shot of their own: the framing at its ends kept (a key
    added there when one falls outside), times moved by `shift` and stretched by `scale`."""
    if not keys:
        return keys
    inside = [k for k in keys if lo - 1e-6 <= k[0] <= hi + 1e-6]
    out = [[lo, *_value(keys, lo)]] if not any(abs(k[0] - lo) < 1e-3 for k in inside) and any(k[0] < lo for k in keys) else []
    out += [list(k) for k in inside]
    if any(k[0] > hi for k in keys) and not any(abs(k[0] - hi) < 1e-3 for k in inside):
        out.append([hi, *_value(keys, hi)])
    return [[round((k[0] + shift) * scale, 3), *[round(x, 4) for x in k[1:4]]] for k in out] or None


def _mmss(t: float) -> str:
    return f"{int(t) // 60}:{t % 60:04.1f}"


def apply(plan: DesignPlan, ctx, ops: list[EditOp]) -> Edited:
    """The edits, in order, on a plan pinned to its frames. Raises EditError for one that can't be made."""
    period = ctx.beats.period
    shots: list[PlanShot] = list(plan.plan)
    pool = {m.key: m for m in plan.pool}
    graphics: list[PlanGraphic] = list(plan.graphics)
    over: list[OverShot] = [o.model_copy() for o in plan.over]
    music: MusicEdit | None = plan.music.model_copy(deep=True) if plan.music else None
    lines: list[str] = []

    def facts(s: PlanShot):
        m = pool.get(s.key)
        return ctx.facts.get(m.clip) if m else None

    def rate(s: PlanShot) -> float | None:
        """Source seconds per film second (None: a ramp, which has no single speed)."""
        if s.speed is not None:
            return min(4.0, max(0.25, s.speed))
        if s.kind == "ramp":
            return None
        if s.kind in ("hold", "hit"):
            f = facts(s)
            return 0.5 if f is not None and f.fps >= SLOWMO_FPS else 1.0
        return 1.0

    def length(s: PlanShot) -> float:
        return span(s) * period

    def starts() -> list[float]:
        out, t = [], 0.0
        for s in shots:
            out.append(t)
            t += length(s)
        return out

    def index(n: int) -> int:
        if not 1 <= n <= len(shots):
            raise EditError(f"There's no shot {n}: the film has {len(shots)}.")
        return n - 1

    def fits(s: PlanShot, n: int, film_seconds: float) -> None:
        """The clip has the frames the shot needs."""
        f, r = facts(s), rate(s)
        if f is None or r is None or s.src_in is None:
            return
        if s.src_in < -0.01:
            raise EditError(f"Shot {n} can't start earlier: its clip begins there.")
        if s.src_in + film_seconds * r > f.duration + 0.02:
            room = max(0.0, (f.duration - s.src_in) / r)
            raise EditError(f"Shot {n} can't be that long: its clip has {room:.2f} s from there.")

    def shift(after: float, by: float) -> None:
        """Graphics placed by hand and the music's edits keep their moment when what comes before them moves."""
        nonlocal music
        if abs(by) < 1e-6:
            return

        def moved(t: float | None) -> float | None:
            if t is None:
                return t
            if by < 0 and after + by - 1e-6 <= t < after - 1e-6:  # in what was taken out: where the film closed up
                return round(after + by, 3)
            if t < after - 1e-6:
                return t
            return round(max(0.0, t + by), 3)

        for k, g in enumerate(graphics):
            if g.at is not None:
                graphics[k] = g.model_copy(update={"at": moved(g.at), "until": moved(g.until)})
        for k, o in enumerate(over):  # what is laid over the picture stays with the moment it covers
            over[k] = o.model_copy(update={"at": moved(o.at), "until": moved(o.until)})
        if music is not None:
            music.points = [p.model_copy(update={"t": moved(p.t)}) for p in music.points]
            music.jumps = [j.model_copy(update={"at": moved(j.at)}) for j in music.jumps]

    def renumber(fn) -> None:
        """Shot-anchored graphics, to their shots' new numbers (`fn`: old number -> new number, or None: it goes)."""
        keep = []
        for g in graphics:
            if g.kind == "captions" or g.at is not None:
                keep.append(g)
                continue
            n = fn(g)
            if n is not None:
                keep.append(g.model_copy(update={"shot": max(1, min(len(shots), n))}))
        graphics[:] = keep

    for op in ops:
        if op.op == "music":
            if op.music is None:
                raise EditError("Say how the music should be.")
            total = sum(length(s) for s in shots)
            m = op.music
            music = MusicEdit(
                slide=round(min(30.0, max(-30.0, m.slide)), 3), gain_db=round(min(12.0, max(-30.0, m.gain_db)), 1),
                fade_in=None if m.fade_in is None else round(min(10.0, max(0.0, m.fade_in)), 2),
                fade_out=None if m.fade_out is None else round(min(10.0, max(0.0, m.fade_out)), 2),
                duck_db=None if m.duck_db is None else round(min(0.0, max(-30.0, m.duck_db)), 1),
                points=sorted((p.model_copy(update={"t": round(min(total, max(0.0, p.t)), 3),
                                                    "db": round(min(12.0, max(-60.0, p.db)), 1)}) for p in m.points),
                              key=lambda p: p.t)[:64],
                jumps=sorted((j.model_copy(update={"at": round(min(total, max(0.0, j.at)), 3),
                                                   "to": round(max(0.0, j.to), 3)}) for j in m.jumps),
                             key=lambda j: j.at)[:32])
            lines.append(music_words(plan.music, music))
            continue
        if op.op == "over":
            total = sum(length(s) for s in shots)
            if op.index >= len(over):
                raise EditError("That laid-over shot isn't there any more.")
            if op.remove:
                if op.index < 0:
                    raise EditError("Which laid-over shot?")
                gone = over.pop(op.index)
                lines.append(f"The shot laid over {_mmss(gone.at)} to {_mmss(gone.until)} is taken away")
                continue
            was = over[op.index] if op.index >= 0 else None
            clip = op.clip or (was.clip if was else None)
            f = ctx.facts.get(clip) if clip else None
            if f is None:
                raise EditError("Pick a clip of this film to lay over it.")
            at = op.at if op.at is not None else (was.at if was else 0.0)
            until = op.until if op.until is not None else (was.until if was else at + 2.0)
            at = round(min(max(0.0, at), max(0.0, total - 0.3)), 3)
            until = round(min(total, max(at + 0.3, until)), 3)
            src = op.src_in if op.src_in is not None else (was.src_in if was else 0.0)
            speed = round(min(4.0, max(0.25, op.factor if op.factor else (was.speed if was else 1.0))), 3)
            src = round(min(max(0.0, src), max(0.0, f.duration - (until - at) * speed)), 3)
            if src + (until - at) * speed > f.duration + 0.02:
                raise EditError(f"{clip} is only {f.duration:.1f} s long: lay it over a shorter stretch.")
            new = OverShot(at=at, until=until, clip=clip, src_in=src, speed=speed,
                           frame_x=op.frame_x if op.frame_x is not None else (was.frame_x if was else None),
                           zoom=round(min(2.5, max(1.0, op.zoom if op.zoom is not None else (was.zoom if was else 1.0))), 3))
            if was is None:
                over.append(new)
                lines.append(f"{clip} is laid over {_mmss(at)} to {_mmss(until)} (the sound goes on under it)")
            else:
                over[op.index] = new
                lines.append(f"The shot laid over is now {_mmss(at)} to {_mmss(until)}" + (f" ({clip})" if clip != was.clip else ""))
            over.sort(key=lambda o: o.at)
            continue
        if op.op == "graphic":
            if not 0 <= op.index < len(graphics):
                raise EditError("That graphic isn't there any more.")
            g = graphics[op.index]
            what = WHAT.get(g.kind, g.kind)
            if op.remove:
                graphics.pop(op.index)
                lines.append(f"The {what} is taken away" + (f' ("{g.text}")' if g.text else ""))
                continue
            upd: dict = {}
            said = []
            if op.text is not None and op.text.strip() != g.text:
                upd["text"] = " ".join(op.text.split())[:80] if g.kind != "title" else op.text.strip()[:80]
                said.append(f'says "{upd["text"]}"')
            if op.sub is not None and op.sub.strip() != g.sub:
                upd["sub"] = " ".join(op.sub.split())[:60]
                said.append(f'has "{upd["sub"]}" under it' if upd["sub"] else "has no line under it")
            if op.style and op.style != g.style:
                if op.style not in STYLES.get(g.kind, (op.style,)):
                    raise EditError(f"A {what} can be {', '.join(STYLES[g.kind])}.")
                upd["style"] = op.style
                said.append(f"is in the {op.style} style")
            if op.scale is not None and abs(op.scale - g.scale) > 1e-3:
                upd["scale"] = round(min(3.0, max(0.4, op.scale)), 2)
                said.append("is bigger" if upd["scale"] > g.scale else "is smaller")
            if op.place is not None and op.place != g.place:
                upd["place"] = op.place
                said.append({"top": "sits at the top", "middle": "sits in the middle", "bottom": "sits at the bottom",
                             "auto": "finds its own place"}[op.place])
            if op.at is not None or op.until is not None:
                total = sum(length(s) for s in shots)
                at = round(min(max(0.0, op.at if op.at is not None else (g.at or 0.0)), max(0.0, total - 0.3)), 3)
                until = op.until if op.until is not None else g.until
                if until is None:
                    until = at + max(1.0, g.beats * period)
                until = round(min(total, max(at + 0.3, until)), 3)
                upd.update(at=at, until=until)
                said.append(f"plays {_mmss(at)} to {_mmss(until)}")
            if upd:
                graphics[op.index] = g.model_copy(update=upd)
                lines.append(f"The {what} " + ", ".join(said))
            continue
        i = index(op.shot)
        s = shots[i]
        n = i + 1
        if op.op == "trim":
            d = round(float(op.seconds), 3)
            if abs(d) < 1e-3:
                continue
            if s.kind == "stutter":
                raise EditError(f"Shot {n} repeats in quick pieces: give it a plain speed first, then trim it.")
            r = rate(s)
            if op.edge == "start" and r is None:
                raise EditError(f"Shot {n} speeds up and slows down: give it a plain speed first, then trim its start.")
            new = length(s) + d
            if new < MIN_SHOT:
                raise EditError(f"Shot {n} would be shorter than {MIN_SHOT:g} s: delete it instead.")
            end_before = starts()[i] + length(s)
            if op.edge == "start":
                upd = {"beats": new / period, "peak_beat": None}
                if s.src_in is not None:
                    upd["src_in"] = round(s.src_in - d * r, 3)
                if s.keys:  # they stay on their frames: later by what was added at the start
                    upd["keys"] = keys_within(s.keys, max(0.0, -d), length(s) + 1e3, shift=d)
                s = s.model_copy(update=upd)
            else:
                s = s.model_copy(update={"beats": new / period,
                                         "keys": keys_within(s.keys, 0.0, new) if s.keys and d < 0 else s.keys})
            fits(s, n, new)
            shots[i] = s
            said = (f"Shot {n} is {abs(d):.2f} s {'longer' if d > 0 else 'shorter'} at its "
                    f"{'start' if op.edge == 'start' else 'end'}")
            j = i + 1 if op.edge == "end" else i - 1
            if not op.ripple and 0 <= j < len(shots):  # roll: the neighbour takes up the difference
                nb = shots[j]
                if nb.kind == "stutter":
                    raise EditError(f"Shot {j + 1} next to it repeats in quick pieces: trim with ripple instead.")
                left = length(nb) - d
                if left < MIN_SHOT:
                    raise EditError(f"Shot {j + 1} next to it would be too short: delete it, or trim with ripple.")
                upd = {"beats": left / period}
                if op.edge == "end" and nb.src_in is not None:  # its start moves: later (or earlier) frames
                    nr = rate(nb)
                    upd["src_in"] = None if nr is None else round(nb.src_in + d * nr, 3)
                    upd["peak_beat"] = None
                shots[j] = nb.model_copy(update=upd)
                fits(shots[j], j + 1, left)
                if op.edge == "end":
                    said += f"; shot {j + 1} starts {'later' if d > 0 else 'earlier'}"
                else:
                    said += f"; shot {j + 1} ends {'earlier' if d > 0 else 'later'}"
            else:
                shift(end_before, d)
            lines.append(said)
        elif op.op == "split":
            t = round(float(op.seconds), 3)
            if s.kind in ("stutter", "ramp") and s.speed is None:
                raise EditError(f"Shot {n} {'repeats in quick pieces' if s.kind == 'stutter' else 'speeds up and slows down'}: "
                                "give it a plain speed first, then split it.")
            whole = length(s)
            if not MIN_SHOT <= t <= whole - MIN_SHOT:
                raise EditError(f"Split shot {n} at least {MIN_SHOT:g} s from its ends (it is {whole:.2f} s long).")
            r = rate(s) or 1.0
            peak = s.peak_beat * period if s.peak_beat is not None else None
            first = s.model_copy(update={"beats": t / period, "keys": keys_within(s.keys, 0.0, t),
                                         "peak_beat": s.peak_beat if peak is not None and peak < t else None})
            second = s.model_copy(update={
                "beats": (whole - t) / period, "transition": "cut", "transition_beats": 0.0,
                "src_in": None if s.src_in is None else round(s.src_in + t * r, 3),
                "keys": keys_within(s.keys, t, whole, shift=-t),
                "peak_beat": (peak - t) / period if peak is not None and peak > t + 0.05 else None})
            shots[i:i + 1] = [first, second]
            renumber(lambda g: g.shot + 1 if g.shot > n else g.shot)
            lines.append(f"Shot {n} is split at {t:.2f} s into shots {n} and {n + 1}")
        elif op.op == "delete":
            if len(shots) == 1:
                raise EditError("That's the only shot: a film needs one.")
            at = starts()[i]
            gone = length(s)
            shots.pop(i)
            if op.ripple:
                renumber(lambda g: None if g.shot == n and g.kind in FOLLOWS else g.shot - 1 if g.shot > n else g.shot)
                shift(at + gone, -gone)
                lines.append(f"Shot {n} is deleted; the film closes up")
            else:  # lift: the shot before holds on (the first one: the next one starts earlier)
                k = i - 1 if i > 0 else 0
                nb = shots[k]
                if nb.kind == "stutter":
                    nb = nb.model_copy(update={"kind": "cut", "beats": span(nb), "count": 1})
                upd = {"beats": nb.beats + span(s)}
                if i == 0 and nb.src_in is not None and (nr := rate(nb)) is not None:
                    upd["src_in"] = round(max(0.0, nb.src_in - gone * nr), 3)
                shots[k] = nb.model_copy(update=upd)
                fits(shots[k], k + 1, length(shots[k]))
                kept = []  # its words stay at their moment (placed there by hand); what showed its picture goes
                for g in graphics:
                    if g.kind == "captions" or g.at is not None or g.shot < n:
                        kept.append(g)
                    elif g.shot > n:
                        kept.append(g.model_copy(update={"shot": g.shot - 1}))
                    elif g.kind not in FOLLOWS:
                        kept.append(g.model_copy(update={"at": round(at, 3), "until": round(at + max(1.0, g.beats * period), 3)}))
                graphics[:] = kept
                lines.append(f"Shot {n} is deleted; shot {k + 1} {'holds on' if i > 0 else 'starts earlier'} in its place")
        elif op.op == "move":
            j = max(0, min(len(shots) - 1, op.to - 1))
            if j == i:
                continue
            order = list(range(len(shots)))
            order.insert(j, order.pop(i))
            shots[:] = [shots[k] for k in order]
            new_place = {old + 1: pos + 1 for pos, old in enumerate(order)}
            renumber(lambda g: new_place[g.shot] if g.kind in FOLLOWS and g.shot in new_place else g.shot)
            if shots[0].transition != "cut":  # nothing before the first shot to blend from
                shots[0] = shots[0].model_copy(update={"transition": "cut", "transition_beats": 0.0})
            lines.append(f"Shot {n} moves to place {j + 1}")
        elif op.op == "duplicate":
            end_before = starts()[i] + length(s)
            shots.insert(i + 1, s.model_copy(update={"transition": "cut", "transition_beats": 0.0}))
            renumber(lambda g: g.shot + 1 if g.shot > n else g.shot)
            shift(end_before, length(s))
            lines.append(f"Shot {n} plays again right after itself (shot {n + 1})")
        elif op.op == "speed":
            if op.factor is None:
                raise EditError("Say how fast: 0.25 to 4 times.")
            f = round(min(4.0, max(0.25, float(op.factor))), 3)
            if s.kind == "stutter":
                raise EditError(f"Shot {n} repeats in quick pieces: it has no single speed to change.")
            old_rate = rate(s) or 1.0
            whole = length(s)
            upd = {"speed": f, "push": s.push}
            if s.kind == "ramp":
                upd["kind"] = "cut"  # one speed instead of the speed-up and slow-down
            new = whole * old_rate / f if op.ripple else whole
            if new < MIN_SHOT:
                raise EditError(f"At {f:g}x shot {n} would be shorter than {MIN_SHOT:g} s.")
            upd["beats"] = new / period
            if s.keys and op.ripple:
                upd["keys"] = keys_within(s.keys, 0.0, whole, scale=new / whole)
            end_before = starts()[i] + whole
            shots[i] = s.model_copy(update=upd)
            fits(shots[i], n, new)
            if op.ripple:
                shift(end_before, new - whole)
            lines.append(f"Shot {n} plays at {f:g}x" + (f" ({new:.2f} s now)" if op.ripple and abs(new - whole) > 0.01 else ""))
        elif op.op == "frame":
            upd, said = {}, []
            if op.frame_x is not None:
                upd["frame_x"] = round(min(1.0, max(0.0, op.frame_x)), 3)
                said.append("reframed")
            if op.zoom is not None:
                upd["zoom"] = round(min(2.5, max(1.0, op.zoom)), 3)
                said.append(f"punched in {upd['zoom']:g}x" if upd["zoom"] > 1.001 else "not punched in")
            if op.zoom_center is not None and len(op.zoom_center) >= 2:
                upd["zoom_center"] = [round(min(1.0, max(0.0, float(x))), 3) for x in op.zoom_center[:2]]
                if "reframed" not in said:
                    said.append("reframed")
            if op.flip is not None and bool(op.flip) != bool(s.flip):
                upd["flip"] = bool(op.flip)
                said.append("mirrored" if op.flip else "not mirrored")
            if op.push is not None:
                upd["push"] = round(min(0.4, max(-0.4, op.push)), 3)
                said.append("pushes in slowly" if upd["push"] > 0.005 else "pulls out slowly" if upd["push"] < -0.005
                            else "holds still")
            if upd:
                shots[i] = s.model_copy(update=upd)
                lines.append(f"Shot {n} is " + ", ".join(said))
        elif op.op == "keys":
            whole = length(s)
            keys = []
            for k in (op.keys or [])[:16]:
                if len(k) < 4:
                    raise EditError("A keyframe is [seconds into the shot, zoom, x, y].")
                keys.append([round(min(whole, max(0.0, float(k[0]))), 3), round(min(3.0, max(1.0, float(k[1]))), 3),
                             round(min(1.0, max(0.0, float(k[2]))), 3), round(min(1.0, max(0.0, float(k[3]))), 3)])
            keys.sort(key=lambda k: k[0])
            shots[i] = s.model_copy(update={"keys": keys or None})
            moving = len(keys) > 1 and any(abs(a[1] - b[1]) > 0.01 or abs(a[2] - b[2]) > 0.005 or abs(a[3] - b[3]) > 0.005
                                           for a, b in zip(keys, keys[1:]))
            lines.append(f"Shot {n} has no keyframes" if not keys else
                         f"Shot {n} moves through {len(keys)} keyframes" if moving else
                         f"Shot {n} is framed at {keys[0][1]:g}x")
        elif op.op == "volume":
            upd = {}
            if op.mute is not None:
                upd["mute"] = bool(op.mute)
            if op.gain_db is not None:
                upd["gain_db"] = round(min(12.0, max(-30.0, op.gain_db)), 1)
                upd.setdefault("mute", False)
            if upd:
                shots[i] = s.model_copy(update=upd)
                level = upd.get("gain_db") or 0.0
                if upd.get("mute"):
                    lines.append(f"Shot {n}'s own sound is off")
                else:
                    lines.append(f"Shot {n}'s own sound is " + (f"{level:+g} dB" if level else "as filmed"))
        elif op.op == "blend":
            style = op.style or "dissolve"
            if style not in BLENDS:
                raise EditError(f"A shot can come in with {', '.join(BLENDS)}.")
            if i == 0 and style != "cut":
                raise EditError("The first shot has nothing before it to blend from.")
            seconds = op.seconds if op.seconds > 0 else 0.5
            beats = round(min(max(0.25, seconds / period), max(0.25, span(s) / 2)), 3)
            shots[i] = s.model_copy(update={"transition": style, "transition_beats": 0.0 if style == "cut" else beats})
            lines.append(f"Shot {n} comes in with {BLENDS[style]}" + ("" if style == "cut" else f" ({beats * period:.2f} s)"))
    return Edited(plan.model_copy(update={"plan": shots, "pool": list(pool.values()), "graphics": graphics,
                                          "music": music, "over": over}), lines)


def music_words(before: MusicEdit | None, now: MusicEdit) -> str:
    """The music's edit, said: what changed since the last time."""
    was = before or MusicEdit()
    said = []
    if abs(now.slide - was.slide) > 1e-3:
        said.append(f"starts {abs(now.slide):.2f} s {'later' if now.slide > 0 else 'earlier'}" if now.slide else
                    "starts where it was cut to")
    if abs(now.gain_db - was.gain_db) > 0.05:
        said.append(f"is {abs(now.gain_db):g} dB {'louder' if now.gain_db > 0 else 'softer'}" if now.gain_db else
                    "is at its usual level")
    if now.fade_in != was.fade_in and now.fade_in is not None:
        said.append(f"fades in over {now.fade_in:g} s")
    if now.fade_out != was.fade_out and now.fade_out is not None:
        said.append(f"fades out over {now.fade_out:g} s")
    if now.duck_db != was.duck_db:
        said.append("doesn't dip under the voice" if now.duck_db == 0 else
                    f"dips {abs(now.duck_db):g} dB under the voice" if now.duck_db is not None else "dips as usual under the voice")
    if [p.model_dump() for p in now.points] != [p.model_dump() for p in was.points]:
        said.append(f"has its level shaped at {len(now.points)} point{'s' if len(now.points) != 1 else ''}"
                    if now.points else "has no level changes")
    if [j.model_dump() for j in now.jumps] != [j.model_dump() for j in was.jumps]:
        said.append(", ".join(f"jumps at {_mmss(j.at)} to {_mmss(j.to)} of the song" for j in now.jumps[:3])
                    if now.jumps else "plays straight through")
    return "The music " + ("; ".join(said) if said else "is as it was")
