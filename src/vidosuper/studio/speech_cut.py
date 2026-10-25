"""A talking film cut the way an editor cuts one: on what is said. Every phrase they speak becomes a shot, in the order
they said it, cut a breath before the first word and a beat after the last; the long pauses, the false starts and the
"um"s between go. Each shot is pinned to its words (src_in), so music, beat sync, graphics and every later change
work on it like on any film, and no cut ever lands inside a word.
"""

from __future__ import annotations

import re

from .brain.schemas import DesignPlan, PlanShot, PoolMoment

PAUSE = 0.55  # a gap longer than this between words ends a phrase
LONG = 6.5  # a phrase longer than this is split at its best pause (a comma, the widest gap)
LEAD, TAIL = 0.1, 0.16  # kept before the first word and after the last
FILLERS = {"um", "uh", "erm", "er", "uhm", "hmm", "mm", "ah"}


def _words(transcript) -> list[tuple[float, float, str]]:
    out = []
    for u in transcript.utterances:
        if not u.confident:
            continue
        for w in u.words or []:
            out.append((float(w.start), float(w.end), w.text.strip()))
        if not u.words:
            out.append((float(u.start), float(u.end), u.text.strip()))
    return sorted(out)


def phrases(words: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """The words grouped into phrases: (start, end, text), fillers on their own dropped."""
    groups: list[list[tuple[float, float, str]]] = []
    for w in words:
        if groups and w[0] - groups[-1][-1][1] <= PAUSE and not re.search(r"[.!?]$", groups[-1][-1][2]):
            groups[-1].append(w)
        else:
            groups.append([w])
    out: list[list[tuple[float, float, str]]] = []
    for g in groups:  # too long to sit in one shot: split where they paused most (after a comma, if any)
        stack = [g]
        while stack:
            part = stack.pop(0)
            if part[-1][1] - part[0][0] <= LONG or len(part) < 4:
                out.append(part)
                continue
            best = max(range(1, len(part) - 1), key=lambda k: (part[k][0] - part[k - 1][1])
                       + (0.3 if part[k - 1][2].endswith(",") else 0.0) - 0.02 * abs(k - len(part) / 2))
            stack[:0] = [part[:best], part[best:]]
    kept = []
    for part in out:
        bare = [re.sub(r"[^a-z]", "", w[2].lower()) for w in part]
        if all(b in FILLERS or not b for b in bare):
            continue
        kept.append((part[0][0], part[-1][1], " ".join(w[2] for w in part)))
    return kept


def share(project, clip_ids: list[str], durations: dict[str, float]) -> float:
    """How much of the footage is someone speaking (0-1)."""
    from ..perceive.prep import load_transcript

    spoken = total = 0.0
    for cid in clip_ids:
        total += durations.get(cid, 0.0)
        t = load_transcript(project, cid)
        if t is not None:
            spoken += sum(u.end - u.start for u in t.utterances if u.confident)
    return spoken / total if total > 0 else 0.0


def plan(ctx, project, name: str = "Your talk, cut clean") -> DesignPlan:
    """The film as its phrases, clip by clip in the order they were filmed, pinned to their words."""
    from ..perceive.prep import load_transcript

    period = ctx.beats.period or 0.5
    pool: list[PoolMoment] = []
    shots: list[PlanShot] = []
    sync = bool(getattr(ctx, "sync", False))
    booms = set(getattr(ctx.beats, "booms", []) or [])
    pos = 0.0  # where the next shot starts, in beats
    for short, f in ctx.facts.items():
        t = load_transcript(project, f.clip_id)
        if t is None:
            continue
        said = phrases(_words(t))
        for k, (a, b, _) in enumerate(said):
            prev_end = said[k - 1][1] if k else 0.0
            nxt = said[k + 1][0] if k + 1 < len(said) else f.duration
            start = max(0.0, prev_end + 0.02 if k else 0.0, a - LEAD)
            end = min(f.duration, b + TAIL, nxt - 0.02 if k + 1 < len(said) else f.duration)
            if any(s <= (a + b) / 2 <= e for s, e in f.avoid):  # the analyst marked it unusable
                continue
            if end - start < 0.35:
                continue
            if sync:  # the cut after this phrase onto a beat, inside the pause after its last word
                lo = (b + 0.05 - start) / period
                hi = (min(f.duration, nxt - 0.02 if k + 1 < len(said) else f.duration, b + 0.7) - start) / period
                beats_in = [m for m in range(int(pos + lo), int(pos + hi) + 2) if pos + lo <= m <= pos + hi]
                if beats_in:  # a heavy hit if one falls there, else the nearest beat
                    best = min(beats_in, key=lambda m: (m not in booms, abs(m - (pos + (end - start) / period))))
                    end = start + (best - pos) * period
            key = f"{short}s{k + 1}"
            span = round((end - start) / period, 4)
            pool.append(PoolMoment(key=key, clip=short, start=round(start, 3), end=round(end, 3)))
            shots.append(PlanShot(kind="cut", beats=span, key=key, src_in=round(start, 3)))
            pos += span
    if not shots:
        raise ValueError("No speech was found to cut on.")
    return DesignPlan(name=name, idea="What they say, cut clean on their words, in the order they said it.",
                      pool=pool, plan=shots, notes="Cut on the phrases; pauses and fillers removed.")
