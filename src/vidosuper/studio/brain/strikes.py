"""One shot per hit of the music.

For people who want the cut to follow the song's drums ("fast cuts, one shot per beat"), the plan is not left to a
language model: the song is analysed for its hits (beats plus the clear off-beat strikes), every gap between two cuts
becomes a shot, and the footage that fills them is chosen by what the footage does: the real impacts in it (a bat
meeting a ball, a clap, a splash) are placed on a beat with their wind-up and follow-through, the rest of the shots
are the most active stretches of the clips, in step with how hard the music hits there, never the same stretch twice
until the footage runs out (then a reused stretch is framed differently).
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from ...qa import CAR_WORDS, COMPANION
from .context import BeatMap, ClipFacts
from .core import reuse_framing
from .schemas import DesignPlan, PlanShot, PoolMoment

MIN_SHOT = 0.215  # seconds: a shot shorter than this cannot be read (and the renderer refuses 0.21 and under)
STEP = 0.1  # the clips' motion signals are sampled every 0.1 s
SHAKE_LIMIT = 0.11
HERO_EVERY = 6.0  # beats of room one hero hit needs

Signals = Callable[[str], tuple[np.ndarray, np.ndarray, np.ndarray]]  # short id -> (t, shake, motion)


def _energy_at(beats: BeatMap, pos: float) -> float:
    for _label, a, b, energy in beats.sections:
        if a <= pos < b:
            return energy
    return 0.5


def cut_points(beats: BeatMap, density: str = "strikes") -> list[float]:
    """Where the cuts fall, in beats from the start. Where the music hits hard, every clear strike is a cut (what the
    ear counts as the pulse: dance tracks syncopate, so these are often more than one a beat) and the beats between them
    that no strike is near; elsewhere every beat, and every other beat where it is quiet. 0 and the target are the ends."""
    gap = MIN_SHOT / beats.period  # the closest two cuts may be, in beats
    loud = [s for s in beats.strikes if density == "strikes" and _energy_at(beats, s) >= 0.5]
    keep: list[float] = []
    for s in sorted(loud):  # two strikes too close: the one on a whole beat wins, else the first
        if keep and s - keep[-1] < gap - 1e-9:
            if s == int(s) and keep[-1] != int(keep[-1]):
                keep[-1] = s
            continue
        keep.append(s)
    pts = set(keep)
    for k in range(1, beats.target):
        quiet = _energy_at(beats, k) < 0.3 and k % 2
        if not quiet and all(abs(k - x) >= gap - 1e-9 for x in keep):
            pts.add(float(k))
    pts = sorted(x for x in pts if 0 < x < beats.target)
    return [0.0, *[x for x in pts if x >= gap], float(beats.target)]


@dataclass
class Hit:
    short: str
    t: float
    db: float = 0.0


@dataclass
class _Clip:
    short: str
    duration: float
    ok: np.ndarray  # per sample: usable
    rank: np.ndarray  # per sample: how active the picture is, 0-1 among this clip's usable samples
    bonus: np.ndarray  # per sample: the analyst's regard for the moment it sits in
    uses: np.ndarray  # per sample: how often a shot has already shown it


def _prepare(facts: dict[str, ClipFacts], signals: Signals) -> dict[str, _Clip]:
    out = {}
    for short, f in facts.items():
        t, shake, motion = signals(short)
        n = len(t)
        ok = np.ones(n, dtype=bool)
        bonus = np.zeros(n)
        for a, b in f.avoid:
            ok[(t >= a) & (t <= b)] = False
        for m in f.moments:
            seg = (t >= m["start"]) & (t <= m["end"])
            text = f"{m.get('description', '')} {m.get('notes') or ''}"
            if COMPANION.search(text) or CAR_WORDS.search(text):
                ok[seg] = False
            else:
                bonus[seg] = np.maximum(bonus[seg], 0.12 * (float(m.get("strength", 5)) - 5) / 5)
        ok &= shake <= SHAKE_LIMIT
        rank = np.zeros(n)
        if ok.sum() > 3:
            order = np.argsort(np.argsort(motion[ok]))
            rank[ok] = order / max(1, len(order) - 1)
        out[short] = _Clip(short, f.duration, ok, rank, bonus, np.zeros(n, dtype=int))
    return out


def _window_arrays(c: _Clip, n: int):
    """For windows of n samples: whether each start is fully usable, its activity score and its reuse count."""
    size = len(c.ok) - n + 1
    if size <= 0:
        return None
    view = np.lib.stride_tricks.sliding_window_view
    usable = view(c.ok.astype(np.int8), n).min(axis=1) == 1
    rank = view(c.rank, n)
    score = 0.6 * rank.mean(axis=1) + 0.4 * rank.max(axis=1) + view(c.bonus, n).mean(axis=1)
    return usable, score, view(c.uses, n).max(axis=1)


def place_heroes(pts: list[float], beats: BeatMap, hits: list[Hit], facts: dict[str, ClipFacts]) -> list[dict]:
    """Each real hit lands on a beat with a wind-up before it and a follow-through after: the shot spans the cut
    before the hit's beat to the second cut after it. The loudest hit gets the drop. Spread over the video."""
    room = max(1, int(beats.target // HERO_EVERY))
    chosen = sorted(sorted(hits, key=lambda h: -h.db)[:room], key=lambda h: h.t)
    if not chosen:
        return []
    eligible = [k for k in range(1, len(pts) - 3) if pts[k] == int(pts[k]) and pts[k] >= 2.0]
    slots = [(j + 0.5) / len(chosen) * beats.target for j in range(len(chosen))]
    order = list(range(len(chosen)))
    if beats.drop is not None and len(chosen) > 1:  # the loudest hit takes the slot nearest the drop
        loud = max(range(len(chosen)), key=lambda i: chosen[i].db)
        near = min(range(len(slots)), key=lambda i: abs(slots[i] - beats.drop))
        order[loud], order[near] = order[near], order[loud]
    taken: list[int] = []
    out = []
    for slot, hit_index in zip(slots, order):
        hit = chosen[hit_index]
        f = facts[hit.short]
        for k in sorted(eligible, key=lambda k: abs(pts[k] - slot)):
            if any(abs(k - o) < 4 for o in taken):
                continue
            lead = pts[k] - pts[k - 1]
            after = pts[k + 2] - pts[k]
            if hit.t - lead * beats.period < 0.0 or hit.t + after * beats.period > f.duration - 0.05:
                continue
            taken.append(k)
            out.append({"k": k, "hit": hit, "lead": lead, "span": pts[k + 2] - pts[k - 1]})
            break
    return sorted(out, key=lambda h: h["k"])


def plan_on_strikes(facts: dict[str, ClipFacts], signals: Signals, beats: BeatMap, hits: list[Hit], name: str,
                    idea: str, seed: int = 0, density: str = "strikes", max_zoom: float = 1.3) -> DesignPlan:
    rng = np.random.default_rng(seed)
    period = beats.period
    pts = cut_points(beats, density)
    heroes = place_heroes(pts, beats, hits, facts)
    hero_at = {h["k"] - 1: h for h in heroes}  # shot index in `pts` terms: starts at pts[k - 1]
    clips = _prepare(facts, signals)

    # the timeline: (from beat, to beat, hero or None)
    shots: list[tuple[float, float, dict | None]] = []
    i = 0
    while i < len(pts) - 1:
        if i in hero_at:
            h = hero_at[i]
            shots.append((pts[i], pts[i] + h["span"], h))
            i += 3
        else:
            shots.append((pts[i], pts[i + 1], None))
            i += 1
    for hit in hits:  # a real hit only plays as a hero shot, on a beat: never inside a plain shot (off the beat)
        c = clips[hit.short]
        t = np.arange(len(c.ok)) * STEP
        c.ok[(t >= hit.t - 0.7) & (t <= hit.t + 0.7)] = False

    chosen: dict[int, tuple[str, float, int]] = {}  # shot -> clip, start, how often that stretch was seen before
    energy = [_energy_at(beats, a) for a, _b, _h in shots]
    for si in sorted((x for x in range(len(shots)) if shots[x][2] is None), key=lambda x: -energy[x]):
        a, b, _ = shots[si]
        seconds = (b - a) * period
        n = max(2, int(math.ceil(seconds / STEP)) + 1)
        target = 0.5 + 0.5 * energy[si]
        best, best_value = None, -1e9
        near = [chosen[x] for x in (si - 1, si + 1) if x in chosen]
        for short, c in clips.items():
            arrays = _window_arrays(c, n)
            if arrays is None:
                continue
            usable, score, uses = arrays
            value = -np.abs(score - target) - 1.2 * (uses > 0) - 0.15 * np.minimum(uses, 4) + rng.normal(0, 0.015, len(score))
            for other, start, _r in near:  # not the same stretch as the shot beside it
                if other == short:
                    idx = np.arange(len(score)) * STEP
                    value -= 0.5 * (np.abs(idx - start) < 1.5)
            value = np.where(usable, value, -1e9)
            k = int(np.argmax(value))
            if value[k] > best_value:
                best, best_value = (short, k, int(uses[k])), float(value[k])
        if best is None:
            continue
        short, k, seen = best
        chosen[si] = (short, round(k * STEP, 2), seen)
        clips[short].uses[k:k + n] += 1

    pool, plan, reused = [], [], 0
    for si, (a, b, hero) in enumerate(shots):
        key = f"w{si + 1:03d}"
        if hero is not None:
            hit = hero["hit"]
            f = facts[hit.short]
            lead_s, after_s = hero["lead"] * period, (hero["span"] - hero["lead"]) * period
            pool.append(PoolMoment(key=key, clip=hit.short, start=round(max(0.0, hit.t - lead_s - 0.1), 2),
                                   end=round(min(f.duration, hit.t + after_s + 0.1), 2), peak=round(hit.t, 3)))
            plan.append(PlanShot(kind="cut", beats=hero["span"], key=key, peak_beat=hero["lead"], zoom=1.0,
                                 zoom_center=(0.5, 0.5)))
            continue
        if si not in chosen:
            continue
        short, start, seen = chosen[si]
        f = facts[short]
        seconds = (b - a) * period
        zoom, centre = 1.0, (0.5, 0.5)
        if seen:
            reused += 1
            zoom, centre = reuse_framing(seen)
            zoom = min(zoom, max_zoom)
        pool.append(PoolMoment(key=key, clip=short, start=start,
                               end=round(min(f.duration, start + seconds + 0.2), 2), peak=None))
        plan.append(PlanShot(kind="cut", beats=b - a, key=key, zoom=zoom, zoom_center=centre))
    lengths = [s.beats * period for s in plan]
    notes = (f"One shot per hit of the music: {len(plan)} shots, {sum(lengths) / len(lengths):.2f} s on average. "
             f"{len(heroes)} real hit{'s' if len(heroes) != 1 else ''} in the footage, each on a beat with its own "
             f"wind-up and follow-through; {reused} shot{'s' if reused != 1 else ''} reuse footage with a new framing.")
    return DesignPlan(name=name, idea=idea, pool=pool, plan=plan, notes=notes)
