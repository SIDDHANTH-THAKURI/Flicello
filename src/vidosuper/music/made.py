"""An original track made for one film by ElevenLabs Music, from what the person asked for (studio: the `beat` job).

The code owns the structure and the AI owns the sound. The structure is whole bars at the track's tempo, laid out
for what the music is for:

- `cut` (a film cut on the beat): an intro, a build, the drop, a breath, the last drop, and a two-bar ending whose
  first beat is a final hit that rings out, so the film can end on it;
- `bed` (music under a voice): a soft intro, a steady groove that changes a little every so often and leaves room
  for the voice, and the same kind of ending.

The sound (genre, instruments, mood, tempo, what each part adds) is a `Sound` the brain writes from their words.
Every try of a short track is the whole track, so what they hear is what they get; a long one is first a 30-second
taste, and the full track is made once, after they pick it, with the taste as its reference so it sounds the same.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

PRICE_PER_MINUTE = 0.15  # USD, ElevenLabs Music over the API (pricing page, October 2026)
MODEL = "music_v2_5"
WHOLE_UP_TO = 75.0  # seconds: a track up to this long is made whole on every try; longer ones start as a taste
TASTE_BARS = (2, 9, 2)  # intro, the main groove, the ending: about 30 s at 96 BPM
MAX_CHUNK_MS = 120_000
ALWAYS_AVOID = ["vocals", "singing", "lyrics", "spoken word", "rap", "choir", "humming", "vocal chops"]


@dataclass
class Sound:
    """How the track should sound (the brain's reading of what they asked for)."""

    title: str
    bpm: int
    styles: list[str]  # global: genre, instruments with how they are played, mood, production
    avoid: list[str] = field(default_factory=list)
    parts: dict[str, list[str]] = field(default_factory=dict)  # what each part adds: intro, build, drop, breath, ending


@dataclass
class Section:
    name: str  # intro | build | drop | breath | lift | groove | ending
    bars: int


def bar_seconds(bpm: int) -> float:
    return 240.0 / bpm


def layout(seconds: float, bpm: int, purpose: str = "cut") -> list[Section]:
    """Whole bars adding up to about `seconds` (never under 4 bars), the ending last."""
    bar = bar_seconds(bpm)
    n = max(4, int(round(seconds / bar)))
    if purpose == "bed":
        intro = 2 if n >= 8 else 1
        body = n - intro - 2
        out = [Section("intro", intro)]
        k = 0
        while body > 0:  # the groove changes a little every 8-16 bars, so a long bed never loops by ear
            take = min(body, 16 if body > 20 else body)
            out.append(Section("groove" if k % 2 == 0 else "lift", take))
            body -= take
            k += 1
        return out + [Section("ending", 2)]
    if n < 10:  # a very short film: in, the drop, out
        intro = 1 if n < 7 else 2
        return [Section("intro", intro), Section("drop", n - intro - 2), Section("ending", 2)]
    ending = 2
    intro = 2 if n < 24 else 4
    rest = n - intro - ending
    build = max(2, int(round(rest * 0.22)))
    drop = max(4, int(round(rest * 0.36)))
    breath = max(2, int(round(rest * 0.14))) if rest >= 16 else 0
    last = rest - build - drop - breath
    if last < 2:  # too little left for a last drop: give it to the drop
        drop += last
        last = 0
    out = [Section("intro", intro), Section("build", build), Section("drop", drop)]
    if breath:
        out.append(Section("breath", breath))
    if last:
        out.append(Section("lift" if breath else "drop", last))
    return out + [Section("ending", ending)]


def taste_layout() -> list[Section]:
    intro, groove, ending = TASTE_BARS
    return [Section("intro", intro), Section("drop", groove), Section("ending", ending)]


PART_WORDS = {  # what each part is, in the words the music model reads (added to the sound's own words for it)
    "intro": ["short intro"],
    "build": ["building energy", "drums get busier", "rising tension"],
    "drop": ["full groove", "the strongest part", "punchy kick and bass"],
    "breath": ["breakdown", "drums drop out", "space to breathe"],
    "lift": ["the full groove returns", "a little bigger than before"],
    "groove": ["steady groove", "even dynamics"],
    "ending": ["a final hit on the first beat that rings out", "clean ending", "no fade-out"],
}
BED_WORDS = ["leaves room for a speaking voice", "no lead melody", "soft and steady", "low and gentle in the mix"]


def plan(sections: list[Section], sound: Sound, purpose: str = "cut", reference: dict | None = None) -> dict:
    """The composition plan (music_v2 chunks), each section one chunk (a long one split at 120 s on whole bars).
    `reference`: a stored song's {song_id, range} the whole track should sound like (the taste they picked)."""
    bar_ms = bar_seconds(sound.bpm) * 1000
    avoid = list(dict.fromkeys([*ALWAYS_AVOID, *sound.avoid]))[:50]
    base = list(dict.fromkeys([*sound.styles, f"{sound.bpm} bpm", "instrumental", "steady tempo"]))
    chunks = []
    for k, s in enumerate(sections):
        words = [*PART_WORDS.get(s.name, []), *sound.parts.get(s.name, [])]
        if purpose == "bed" and s.name != "ending":
            words += BED_WORDS
        per = max(1, int(MAX_CHUNK_MS // bar_ms))
        left = s.bars
        while left > 0:
            take = min(left, per)
            ms = int(round(take * bar_ms))
            if ms < 3000:  # the model's shortest chunk
                ms = 3000
            chunks.append({"text": f"[{s.name.title()}] {{instrumental}}", "duration_ms": ms,
                           "positive_styles": list(dict.fromkeys([*base, *words]))[:50],
                           "negative_styles": avoid})
            left -= take
    if reference and chunks:  # the first chunk sets the sound of the whole track
        chunks[0]["conditioning_ref"] = reference
        chunks[0]["condition_strength"] = "high"
    return {"chunks": chunks}


def plan_seconds(p: dict) -> float:
    return sum(c.get("duration_ms", 0) for c in p["chunks"]) / 1000.0


def cost(seconds: float) -> float:
    return round(seconds / 60.0 * PRICE_PER_MINUTE, 4)


def section_times(sections: list[Section], bpm: int) -> list[tuple[str, float]]:
    """Where each section starts, in seconds (as planned: the real track is measured after)."""
    bar = bar_seconds(bpm)
    out, t = [], 0.0
    for s in sections:
        out.append((s.name, round(t, 2)))
        t += s.bars * bar
    return out


def whole(seconds: float) -> bool:
    return seconds <= WHOLE_UP_TO


def fit_bpm(bpm: int, purpose: str) -> int:
    """A tempo the beat tracker reads well (it halves anything much over 120) and that suits the use."""
    lo, hi = (70, 120) if purpose == "cut" else (60, 110)
    bpm = int(bpm or 96)
    while bpm > hi:
        bpm = int(math.ceil(bpm / 2)) if bpm / 2 >= lo else hi
    return max(lo, min(hi, bpm))
