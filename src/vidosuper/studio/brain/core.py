"""The brain's five jobs, each checked against the footage and the song before it is accepted:

- analyse a clip  -> ClipAnalysis (moments with in/out times, problems), from its contact sheets
- pitch stories   -> three different ideas grounded in logged moments
- design the cut  -> a shot plan on the song's beats (pool + plan), turned into a `Design` for the renderer
- revise the cut  -> the same, with the person's feedback
- learn           -> new or dropped style rules for the person's profile

`Brain` asks Claude (API or headless Claude Code); `LocalBrain` does the same jobs with plain heuristics
and no AI, for tests, for trying the studio without a key, and as a fallback.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ...design import Design, Moment as PoolEntry, Shot
from ...music.analysis import MusicAnalysis
from ...schemas import ClipAnalysis, ClipInfo, ClipSignals
from ..settings import Models
from .backends import BrainError, Request, SpendFn, Usage, structured
from .context import HOLD_WORDS, NO_PEOPLE_WORDS, NO_REPEAT_WORDS, REUSE_WORDS, SYNC_WORDS, logged_at, people_in, place_of, BeatMap, ClipFacts, catalog_text, craft_rules, moment_by_id, prompt, rules_text
from ...explainer.script import VoiceScript

DESIGNED_TEXT = '# Style: DESIGNED (designer animations)\nThe person picked designer animations. talk: they are cut out of their room and stand in front of a stage; your callouts are drawn BEHIND them, above and around their head.\n- The backbone is `scene` callouts: one designer animation for what is being said right now, about every 3-6 seconds, each starting on the word that brings it. `visual` says in a few concrete words what the animation should show ("two people podcasting at a table", "an email arriving with a notification", "a ghost floating"), `topics` gives 1-3 simple search words for an animation library ("podcast", "email", "ghost"). Think like a motion designer illustrating the story, not a subtitle writer. `text` on a scene is an optional 1-3 word label.\n- `title` for the name of the thing being talked about, once, early (typed on letter by letter).\n- `stack` when a number of things is said ("10 audio clips", "3 reasons"): `count`, a `visual` for one of them ("an audio waveform") and `text` ("10 audio clips"); it flies out of the scene that is up.\n- `stat` for a rating or a figure (value as said). `pill` rarely.\n- `ambient`: one looping animation behind their head for the whole video that suits the subject ("a circular sound visualizer" for audio/music/podcasts, "soft floating particles", "film reel") with `ambient_topics`.\n- Set `punch` on the single strongest moment.\n- voice mode: give every scene a `visual` and `topics` too, so it gets a real animation instead of an emoji.\n'
from .schemas import Direction, Directions, VisualIdeas, VisualPicks
from .schemas import DesignPlan, Pitches, PlanShot, PoolMoment, ProfileUpdate, StoryBeat, StoryPitch, StyleRule
from .schemas import ChangeRequest, PlaceTags, RangePlan
from .schemas import EditorOffer, EditorReply, SpecAdd
from .schemas import BeatIdea, PolishedBrief, TalkPart, TalkUnderstanding, Term, Understanding

SLOWMO_FPS = 47.0  # real slow motion only from 50/60 fps footage


@dataclass
class StoryContext:
    """Everything the director and editor need for one video."""

    catalog: dict
    facts: dict[str, ClipFacts]
    song: MusicAnalysis
    beats: BeatMap
    shapes: list[str]  # "9:16" and/or "16:9"
    intent: str
    rules: list[dict]  # the person's style rules: {"id", "text", ...}
    allow_reuse: bool = False  # thin footage, or the person said repeats are fine: a moment may come back, made to look different
    pace: str = "balanced"  # calm | balanced | fast: how quickly the video cuts
    sync: bool = False  # they asked for beat sync (in the note, a sample note, a change or their style rules): a rule
    bans: list[dict] = field(default_factory=list)  # scenes they never want in this film: {"clip", "start", "end", "what"}
    no_people: bool = False  # they said no faces / no people: nobody clearly in view, in any shot
    no_repeats: bool = False  # they said no duplicates: one place is shown in one stretch of the film only
    hold_ok: bool = False  # they asked for holds of their own length: the pacing limits give way
    speech: bool = False  # a talking film: cut on its words (speech_cut); a cut lands on a beat where a pause allows

    def reuse_if_asked(self, *texts: str) -> None:
        if any(REUSE_WORDS.search(t or "") for t in texts):
            self.allow_reuse = True

    def sync_if_asked(self, *texts: str) -> None:
        if any(SYNC_WORDS.search(t or "") for t in texts):
            self.sync = True

    def read_words(self, *texts: str) -> None:
        """What their own words make a rule (never the AI's brief): beat sync, nobody in shot, nothing shown twice,
        holds as long as they say."""
        self.sync_if_asked(*texts)
        if any(NO_PEOPLE_WORDS.search(t or "") for t in texts):
            self.no_people = True
        if any(NO_REPEAT_WORDS.search(t or "") for t in texts):
            self.no_repeats = True
        if any(HOLD_WORDS.search(t or "") for t in texts):
            self.hold_ok = True

    def header(self) -> str:
        shape = " and ".join({"9:16": "9:16 vertical (Reels/Shorts)", "16:9": "16:9 landscape (YouTube)"}[s]
                             for s in self.shapes)
        return "\n\n".join([
            "# Craft rules (shared)\n" + craft_rules(),
            "# This person's style rules (they win over the craft rules)\n" + rules_text(self.rules),
            "# Their request\n" + (self.intent.strip() or "(nothing specific: surprise them)"),
            f"# Shape and length\n{shape}; about {self.beats.seconds:.0f} seconds.",
            "# The song, in beats\n" + self.beats.text(self.song),
            self.pace_text(),
            "# Beat edits\nStutters, hits, ramps and the shot on the drop go only on strong moments (strength 6 or more "
            "of 10). Weaker moments (a pan across a railing, the ground, empty sky) are only for linking shots, played "
            "plainly, or left out.",
            *(["# Reusing footage\nThe footage is thin or the person said repeats are fine: you may use a moment more than "
               "once (twice at most), but each time it must look different (another part of the moment, another swing or take, a different "
               "speed), with at least two other shots between, so the repeat is never obvious. Never zoom in to hide a "
               "repeat: the viewer must see the whole action."]
              if self.allow_reuse else []),
            *([f"# Beat sync: they asked for it, so it is a rule\nEvery cut lands on a heavy hit or a bar start "
               f"(beats {', '.join(f'{b:g}' for b in self.beats.hit_marks())}). Hard cuts only (a dissolve hides the "
               f"hit; at most {SYNC_SOFT} in the whole film, in a quiet part). No shot longer than {SYNC_LONGEST} beats. "
               "Slow motion is fine, but the cut into and out of it is on a hit."]
              if self.sync else []),
            "# Places\nA recognisable place (a landmark, the moments' `place`) is shown in one stretch of the film: "
            "never the Opera House at the start and again at the end. Two shots of one place side by side are one "
            "scene; the same place twice, apart, is a repeat" + (" - and they said no repeats." if self.no_repeats else "."),
            *(["# Nobody in shot\nThey don't want people in the film: use no moment with people near or a face "
               "(the moments' `people`); small far-off figures are fine."] if self.no_people else []),
            *(["# Their own lengths\nThey asked for holds of their own length: their words win over the pacing "
               "limits above (a long first shot, a long hold on the drop)."] if self.hold_ok else []),
            "# Their words win\nWhen what they asked for contradicts a craft rule or a check above, do what they "
            "asked and say so in `notes`.",
            *(["# Scenes they never want in this film (never use them, not even part of them)\n" + "\n".join(
                f"- {b['clip']} {b['start']:g}-{b['end']:g} s" + (f": {b['what']}" if b.get("what") else "")
                for b in self.bans)] if self.bans else []),
            *self.impact_text(),
        ])

    def pace_text(self) -> str:
        per_beat = self.beats.period
        return {
            "fast": f"# Pace: FAST\nThe person wants fast cuts that follow the beat. Aim for shots of 1-2 beats "
                    f"({per_beat:.2f}-{2 * per_beat:.2f} s); never more than 4 beats except one deliberate breath (a hold) "
                    "and the shot on the drop. Hard cuts only; no dissolves or whips. Average shot well under 2.5 beats.",
            "calm": "# Pace: CALM\nThe person wants a relaxed, cinematic pace: shots of 4-8 beats, long holds, dissolves "
                    "where the music breathes; fast cuts only at the drop.",
        }.get(self.pace, "# Pace: BALANCED\nA mix: mostly 2-4 beat shots, bursts of 1-beat cuts at the peak, a hold where "
                         "the music breathes.")

    def impact_text(self) -> list[str]:
        lines = []
        by_clip: dict[str, list[float]] = {}
        for short, t in hero_hits(self.facts):  # the same hits the plan is checked against
            by_clip.setdefault(short, []).append(t)
        for short, ts in by_clip.items():
            lines.append(f"- {short}: " + ", ".join(f"{t:.2f}" for t in ts) + " s")
        if not lines:
            return []
        return ["# Real hits in the footage (found from the clips' own sound, to the millisecond)\n" + "\n".join(lines) +
                "\nThese are the heroes: a bat meeting a ball, a clap, a slam, a splash. Give every hit its own shot, "
                "once (set `peak` on the pool entry to the hit's time): the tool lands each exactly on a beat and keeps its "
                "real sound. Spread them through the video and put the loudest one on the drop. Fill the beats between the "
                "hits with the clip's other action, kept short. Frame wide enough to see the whole action."]


# ---------------------------------------------------------------- checks shared by both brains

def hero_hits(facts: dict[str, ClipFacts], gap: float = 1.2) -> list[tuple[str, float]]:
    """The hits a plan must feature: inside a moment the analyst logged as usable (a clap in a stretch it threw out is
    not a hero), and hits closer than `gap` seconds count once (a ball bouncing twice is one shot, not two: the
    old rule asked for a shot per bounce and no plan could pass, so a friend's film never got made)."""
    out: list[tuple[str, float]] = []
    for short, f in facts.items():
        last = None
        for t in sorted(f.impacts):
            if f.moments and not any(m["start"] - 0.05 <= t <= m["end"] + 0.05 for m in f.moments):
                continue
            if any(a <= t <= b for a, b in f.avoid):
                continue
            if last is not None and t - last < gap:
                continue
            out.append((short, t))
            last = t
    return out


def check_understanding(u: Understanding, ctx: StoryContext) -> list[str]:
    problems = []
    known = set(ctx.facts)
    seen = {c.short for c in u.clips}
    if missing := sorted(known - seen):
        problems.append(f"`clips` must have one entry per clip in the log; missing: {', '.join(missing)}")
    if unknown := sorted(seen - known):
        problems.append(f"`clips` names clips that are not in the log: {', '.join(unknown)}")
    if len(u.questions) > 3:
        problems.append("ask at most 3 questions, only what is genuinely unclear")
    for q in u.questions:
        if not 2 <= len(q.options) <= 4:
            problems.append(f"question '{q.ask[:40]}' needs 2-4 options")
    if len(u.story.split()) < 5:
        problems.append("`story` must say what the film is about in a real sentence")
    return problems


def _mmss(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def check_talk_understanding(u: TalkUnderstanding, utterances: list[dict], duration: float) -> list[str]:
    """What is wrong with what was understood from a recording (empty: fine). Names must be copied from the
    transcript, so a correction the person makes can be found in it and put right in the captions."""
    problems = []
    if len(u.story.split()) < 5:
        problems.append("`story` must say what the recording is about in a real sentence")
    if not 1 <= len(u.parts) <= 8:
        problems.append(f"give 1-8 `parts` (the hook, the points, the ending); you gave {len(u.parts)}")
    times = [p.at for p in u.parts]
    if times != sorted(times):
        problems.append("`parts` must be in the order they are spoken")
    if any(not 0 <= t <= duration + 0.5 for t in times):
        problems.append(f"every part's `at` must be inside the recording (0-{duration:.0f} s)")
    if len(u.terms) > 12:
        problems.append("list at most 12 `terms`: only names and special words that will be on screen")
    said = " " + _norm(" ".join(x["text"] for x in utterances)) + " "
    missing = [t.heard for t in u.terms if not _norm(t.heard) or f" {_norm(t.heard)} " not in said]
    if missing:
        problems.append("these `terms` are not in the transcript as written: " + ", ".join(f"'{m}'" for m in missing[:6])
                        + ". Copy `heard` exactly as the transcript spells it (put the real name in `written`)")
    if len(u.questions) > 3:
        problems.append("ask at most 3 questions, only what is genuinely unclear")
    for q in u.questions:
        if not 2 <= len(q.options) <= 4:
            problems.append(f"question '{q.ask[:40]}' needs 2-4 options")
    return problems


VOCAL_WORDS = re.compile(r"\b(vocals?|vocalist|sing\w*|singer|lyrics?|rap|rapping|rapper|choir|a ?cappella|humm\w*)\b",
                         re.IGNORECASE)
PURPOSE = {"cut": "a film cut on the beat: a clear steady beat, a build, a drop, a breath, a last drop, a final hit",
           "bed": "music under someone speaking: soft and steady, room for the voice, no drops"}


def check_beat_idea(b: BeatIdea) -> list[str]:
    problems = []
    if not 4 <= len(b.styles) <= 12:
        problems.append(f"give 6-10 `styles` for the whole track (you gave {len(b.styles)})")
    every = [*b.styles, *b.intro, *b.build, *b.drop, *b.breath, *b.ending]
    vocal = [x for x in every if VOCAL_WORDS.search(x) and not re.search(r"\b(no|without|free|instrumental)\b", x, re.I)]
    if vocal:
        problems.append("the track is instrumental: take the voices out of the styles (" + ", ".join(vocal[:3]) + ")")
    if not 40 <= b.bpm <= 220:
        problems.append("`bpm` must be a real tempo (70-120)")
    if len(b.reading.split()) < 5:
        problems.append("`reading` must say what you will make in a real sentence")
    return problems


LOCAL_SOUNDS = [  # without an AI: their words (or the film's) matched to a sound
    (r"lo-?fi|chill|study|cozy|cosy|rain", "Lo-fi glow", 88,
     ["warm lo-fi hip hop", "soft Rhodes piano chords", "dusty boom bap drums", "vinyl crackle", "mellow round bass",
      "relaxed"]),
    (r"cinematic|epic|orchestra|trailer|dramatic|adventure", "Wide horizon", 96,
     ["cinematic orchestral", "big taiko drums", "soaring strings", "brass swells", "epic", "wide reverb"]),
    (r"house|dance|club|disco|party|techno", "Night drive", 118,
     ["deep house", "four-on-the-floor kick", "warm analog bass", "crisp claps", "uplifting chords", "club mix"]),
    (r"rock|guitar|punk|indie|skate", "Open road", 112,
     ["indie rock", "driving electric guitars", "live drums", "energetic", "bass guitar", "garage"]),
    (r"acoustic|folk|wedding|gentle|piano|calm|soft", "Quiet morning", 88,
     ["acoustic folk", "fingerpicked guitar", "soft piano", "light percussion", "warm", "intimate"]),
    (r"hip ?hop|trap|boom ?bap|street", "Street lights", 90,
     ["modern hip hop beat", "punchy kick", "crisp snare", "808 bass", "dark keys", "confident"]),
    (r"summer|happy|upbeat|travel|holiday|beach|sun", "Golden hour", 108,
     ["upbeat tropical pop", "plucked marimba", "bright synth chords", "claps", "sunny", "feel-good"]),
]


def local_beat_idea(ask: str, film: str, purpose: str, pace: str, before: dict | None = None,
                    change: str = "") -> BeatIdea:
    text = " ".join([change, ask, film]).lower()
    title, bpm, styles = "Bright run", 104, ["upbeat electronic pop", "punchy drums", "bright synth chords",
                                             "warm bass", "energetic", "modern mix"]
    for pattern, t, b, st in LOCAL_SOUNDS:
        if re.search(pattern, text):
            title, bpm, styles = t, b, st
            break
    bpm += {"calm": -8, "fast": 8, "every": 8}.get(pace, 0)
    if purpose == "bed":
        bpm = min(bpm, 92)
        styles = [*styles, "soft", "background"]
    said = f" from your words ('{ask.strip()[:60]}')" if ask.strip() else " to suit your film"
    return BeatIdea(reading=f"{title}: {', '.join(styles[:3])}, about {bpm} beats a minute{said}.", title=title,
                    bpm=bpm, styles=styles, drop=["fuller drums"], ending=["final hit"])


def local_talk_understanding(utterances: list[dict], duration: float, intent: str) -> TalkUnderstanding:
    """Without an AI: their note (or the opening words) as the story, the talk split where it pauses, and the names
    it mentions (capitalised words inside sentences) as heard."""
    text = " ".join(u["text"] for u in utterances).strip()
    note = (intent or "").split("\n")[0].strip()
    opening = " ".join(text.split()[:24])
    story = note if len(note.split()) >= 5 else (opening if len(opening.split()) >= 5 else f"A {duration:.0f}-second talk.")
    groups: list[list[dict]] = []
    target = max(4.0, duration / 5)
    for u in utterances:
        if groups and u["start"] - groups[-1][0]["start"] < target:
            groups[-1].append(u)
        else:
            groups.append([u])
    parts = [TalkPart(at=round(float(g[0]["start"]), 1),
                      what=" ".join(" ".join(x["text"] for x in g).split()[:10])) for g in groups[:8]]
    names, seen = [], set()
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for w in re.findall(r"[A-Za-z][\w'-]*", sentence)[1:]:
            if w[0].isupper() and w.lower() not in seen and w not in ("I", "I'm", "I've", "I'll", "I'd"):
                seen.add(w.lower())
                names.append(Term(heard=w, written=w))
    return TalkUnderstanding(story=story, parts=parts, terms=names[:8])



def local_understanding(ctx: StoryContext) -> Understanding:
    from .schemas import ClipRead

    clips = []
    for e in ctx.catalog["clips"]:
        a = e.get("analysis") or {}
        moments = sorted(a.get("moments") or [], key=lambda m: -m.get("strength", 0))
        best = moments[0] if moments else None
        clips.append(ClipRead(short=e["short"], sees=" ".join((a.get("summary") or a.get("scene") or "a clip").split()[:14]),
                              best=f"{best['description'][:60]} at {_mmss(best.get('peak') or best['start'])}" if best else ""))
    song = ctx.song
    drop = f", the first drop at {_mmss(song.drops[0])}" if song.drops else ""
    note = (ctx.intent or "").split("\n")[0].strip()
    return Understanding(story=note if len(note.split()) >= 5 else f"A film made from your {len(clips)} clips, cut on the beat.",
                         music=f"About {song.tempo:.0f} beats a minute, {song.duration:.0f} s long{drop}.", clips=clips)


def check_analysis(analysis: ClipAnalysis, clip: ClipInfo) -> list[str]:
    problems = []
    end = clip.duration + 0.1
    for i, m in enumerate(analysis.moments):
        where = f"moments[{i}] ({m.description[:40]!r})"
        if not 0 <= m.start < m.end <= end:
            problems.append(f"{where}: start/end {m.start}-{m.end} must satisfy 0 <= start < end <= {clip.duration:.2f}")
        elif m.peak is not None and not m.start <= m.peak <= m.end:
            problems.append(f"{where}: peak {m.peak} is outside {m.start}-{m.end}")
        elif m.end - m.start < 0.4:
            problems.append(f"{where}: only {m.end - m.start:.2f}s long; moments need at least 0.4s")
        elif m.best_at is not None and not m.start <= m.best_at <= m.end:
            problems.append(f"{where}: best_at {m.best_at} is outside {m.start}-{m.end}")
        elif m.people == "unknown":
            problems.append(f"{where}: say who is in it: people none, far, near or face")
    for i, r in enumerate(analysis.unusable):
        if not 0 <= r.start < r.end <= end:
            problems.append(f"unusable[{i}]: {r.start}-{r.end} is outside the clip (0-{clip.duration:.2f})")
    if not analysis.moments and not analysis.unusable:
        problems.append("log at least one moment, or mark the clip unusable with a reason")
    return problems


def check_pitches(p: Pitches, n: int, facts: dict[str, ClipFacts]) -> list[str]:
    problems = []
    if len(p.pitches) != n:
        problems.append(f"return exactly {n} pitch{'es' if n > 1 else ''}, not {len(p.pitches)}")
    for i, s in enumerate(p.pitches):
        ids = [m for b in s.beats for m in b.moments] + s.stills
        bad = sorted({m for m in ids if moment_by_id(facts, m) is None})
        if bad:
            problems.append(f"pitch {i + 1} ({s.title!r}) names moments that are not in the catalog: {', '.join(bad)}")
        if not 3 <= len(s.beats) <= 6:
            problems.append(f"pitch {i + 1} needs 3-6 beats, has {len(s.beats)}")
        if not 2 <= len(s.stills) <= 4:
            problems.append(f"pitch {i + 1} needs 3 or 4 stills, has {len(s.stills)}")
    return problems


def _speed(kind: str, fps: float) -> float:
    if kind in ("hold", "hit"):
        return 0.5 if fps >= SLOWMO_FPS else 1.0
    return 1.0


def source_need(shot: PlanShot, period: float, fps: float) -> float:
    """Seconds of source footage a shot plays (roughly, as the template builder will compute it)."""
    seconds = shot.beats * period
    if shot.speed is not None and shot.kind != "stutter":  # set by hand on the timeline
        return seconds * min(4.0, max(0.25, shot.speed))
    if shot.kind == "stutter":
        return seconds + 0.12 * max(0, shot.count - 1) + 0.4
    if shot.kind == "ramp":
        return seconds * (1.15 if fps >= SLOWMO_FPS else 1.0)
    return seconds * _speed(shot.kind, fps)


def plan_beats(plan: DesignPlan) -> float:
    return sum(s.beats * (s.count if s.kind == "stutter" else 1) for s in plan.plan)


def _overlap(a: PoolMoment, b: PoolMoment) -> float:
    """How much of the shorter window the two share (0-1); windows of different clips share nothing."""
    if a.clip != b.clip:
        return 0.0
    both = min(a.end, b.end) - max(a.start, b.start)
    return max(0.0, both) / max(1e-6, min(a.end - a.start, b.end - b.start))


def _overlapping_windows(plan: DesignPlan, pool: dict[str, PoolMoment], uses: dict[str, list[int]],
                         allowed: bool) -> list[str]:
    """Two different pool keys on the same stretch of footage are the same shot twice."""
    out = []
    keys = [k for k in uses if k in pool]
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if _overlap(pool[a], pool[b]) > 0.5:
                gap = min(abs(x - y) for x in uses[a] for y in uses[b])
                if not allowed:
                    out.append(f"'{a}' and '{b}' are the same stretch of {pool[a].clip}: use one of them once")
                elif gap < 3:
                    out.append(f"'{a}' and '{b}' are the same stretch of {pool[a].clip} and sit {gap} shot(s) apart: "
                               "put at least two other shots between them")
    return out


SYNC_SOFT = 1  # dissolves or fades a beat-synced film may have (a quiet part)
SYNC_LONGEST = 8  # beats: the longest shot of a beat-synced film
SYNC_MISS = 0.15  # the share of cuts that may land off the hits (a story beat between two hits)


def _sync_problems(plan: DesignPlan, beats, lo: float | None = None, hi: float | None = None,
                   hold_ok: bool = False) -> list[str]:
    """What breaks beat sync in a plan: cuts between the hits, dissolves, shots that hold too long. `lo`-`hi`: only
    the shots starting in that stretch of beats (a stretch made again; the rest is theirs)."""
    marks = beats.hit_marks()
    pos, off, soft, long_, judged = 0.0, [], [], [], 0
    for i, s in enumerate(plan.plan):
        inside = (lo is None or pos >= lo - 1e-6) and (hi is None or pos < hi - 1e-6)
        if inside:
            judged += 1
            if i and not any(abs(pos - m) < 0.01 for m in marks):
                off.append(f"shot {i + 1} at beat {pos:g}")
            if i and s.transition != "cut":
                soft.append(i + 1)
            if s.kind != "stutter" and s.beats > SYNC_LONGEST and not hold_ok:
                long_.append(i + 1)
        pos += s.beats * (s.count if s.kind == "stutter" else 1)
    out = []
    if len(off) > max(1, int(SYNC_MISS * (judged - 1))):
        out.append(f"beat sync was asked for, but {len(off)} cuts land between the music's hits ({'; '.join(off[:6])}): "
                   f"start shots on the hits or bar starts: beats {', '.join(f'{m:g}' for m in marks)}")
    if len(soft) > SYNC_SOFT:
        out.append(f"beat sync was asked for: shots {', '.join(map(str, soft))} come in with a dissolve or fade, which "
                   f"hides the hit. Hard cuts (at most {SYNC_SOFT} soft transition, in a quiet part)")
    if long_:
        out.append(f"beat sync was asked for: shots {', '.join(map(str, long_))} hold longer than {SYNC_LONGEST} beats; "
                   "split them on a hit")
    out += _dense_problems(plan, beats, lo, hi)
    return out


DENSE_GAP = 2.0  # beats: heavy hits this close (or closer) after the drop make a stretch "every beat" music
HERO_HOLDS = 1  # long holds allowed in a dense stretch besides the shot on the drop


def _dense_problems(plan: DesignPlan, beats, lo: float | None = None, hi: float | None = None) -> list[str]:
    """Beat sync after the drop, as people mean it: where the music hits on (nearly) every beat, a new picture comes
    on (nearly) every hit. The shot on the drop may hold (to the next big hit), and one more hero hold; the rest
    are cut on the hits. (Aura Farming, 21 Oct: 'after the first drop nothing is in beat sync, all spread apart'.)"""
    booms = list(getattr(beats, "booms", []) or [])
    start = float(beats.drop) if beats.drop is not None else round(beats.target * 0.6 / 4) * 4
    after = sorted(b for b in booms if start - 1e-6 <= b < beats.target)
    if len(after) < 4:
        return []
    gaps = sorted(b - a for a, b in zip(after, after[1:]))
    gap = gaps[len(gaps) // 2]
    if gap > DENSE_GAP:
        return []
    longest = max(2.0, 2 * gap)
    pos, holds = 0.0, []
    for i, s in enumerate(plan.plan):
        length = s.beats * (s.count if s.kind == "stutter" else 1)
        inside = (lo is None or pos >= lo - 1e-6) and (hi is None or pos < hi - 1e-6)
        if inside and pos > start + 1e-6 and s.kind != "stutter" and length > longest:
            holds.append(i + 1)
        pos += length
    if len(holds) <= HERO_HOLDS:
        return []
    return [f"beat sync was asked for: after the drop the music hits every {gap:g} beat{'s' if gap != 1 else ''}, but "
            f"shots {', '.join(map(str, holds))} hold longer than {longest:g} beats. Cut on its hits there (shots of "
            f"{gap:g}-{longest:g} beats, or stutters); keep at most {HERO_HOLDS} hero hold besides the shot on the drop"]


def _place_repeats(plan: DesignPlan, ctx) -> list[str]:
    """A place shown in two stretches of the film (not side by side): the Opera House at the start and again later."""
    pool = {m.key: m for m in plan.pool}
    seen: dict[str, int] = {}
    out = []
    for i, s in enumerate(plan.plan):
        m = pool.get(s.key)
        f = ctx.facts.get(m.clip) if m else None
        lm = logged_at(f, m.start, m.end) if f else None
        place = place_of(lm) if lm else ""
        if not place:
            continue
        if place in seen and seen[place] < i - 1:
            out.append(f"shot {i + 1} shows {lm.get('place')} again (shot {seen[place] + 1} already does): show each "
                       "place in one stretch only - use a moment of another place, or put the two shots side by side")
        seen[place] = i
    return out[:4]


def sync_snap(plan: DesignPlan, ctx: StoryContext, lo: float | None = None, hi: float | None = None) -> DesignPlan:
    """A plan made beat-synced by hand when the AI's last answer still missed the hits: every join a hard cut and every cut moved to the nearest heavy hit or bar start. Stutters keep their own rhythm. A shot
    squeezed to nothing is folded into the one before. If the moved plan would break something real (footage too
    short), the AI's plan stands."""
    beats = ctx.beats
    if not ctx.sync or not plan.plan or not _sync_problems(plan, beats, lo, hi, getattr(ctx, "hold_ok", False)):
        return plan
    marks = beats.hit_marks()
    total = plan_beats(plan)
    shots = list(plan.plan)
    spans = [x.beats * (x.count if x.kind == "stutter" else 1) for x in shots]
    starts = [sum(spans[:i]) for i in range(len(shots))]
    kept: list[tuple[float, object]] = [(0.0, shots[0])]
    for i in range(1, len(shots)):
        b, x = starts[i], shots[i]
        outside = (lo is not None and b <= lo + 1e-6) or (hi is not None and b >= hi - 1e-6)  # their cuts stay
        fixed = outside or x.kind == "stutter" or kept[-1][1].kind == "stutter"
        if fixed:
            at = b
        else:
            room = [m for m in marks if kept[-1][0] + 0.5 <= m <= total - 0.5
                    and (lo is None or m > lo + 1e-6) and (hi is None or m < hi - 1e-6)]
            at = min(room, key=lambda m: abs(m - b)) if room else None
        if at is None or at <= kept[-1][0] + 1e-6:
            continue  # nothing left for it: the shot before holds on
        if x.transition != "cut" and not outside:
            x = x.model_copy(update={"transition": "cut", "transition_beats": 0.0})
        kept.append((at, x))
    out = []
    for j, (at, x) in enumerate(kept):
        end = kept[j + 1][0] if j + 1 < len(kept) else total
        if x.kind == "stutter":
            out.append(x)
            continue
        length = round(end - at, 3)
        if abs(length - x.beats) < 1e-6:
            out.append(x)  # as it was (a shot outside the stretch keeps everything)
            continue
        upd = {"beats": length}
        if x.peak_beat is not None and x.peak_beat >= length:
            upd["peak_beat"] = None
        out.append(x.model_copy(update=upd))
    snapped = plan.model_copy(update={"plan": out, "notes": (plan.notes + " Cuts moved onto the music's hits.").strip()})
    if len(check_plan(snapped, ctx, taste=False)) > len(check_plan(plan, ctx, taste=False)):
        return plan
    return snapped


STRONG = 6  # moment strength (of 10) a beat edit needs: a stutter, a hit, a ramp, the drop
MAX_SHOWINGS = 2  # the most times one stretch of footage is shown, even when the footage is thin


def _window_strength(f, m: PoolMoment) -> int:
    """How strong the logged moment a pool window sits in is (the one it overlaps most)."""
    best, score = 0.0, 0
    for x in f.moments:
        o = min(x["end"], m.end) - max(x["start"], m.start)
        if o > best:
            best, score = o, int(x["strength"])
    return score


def check_plan(plan: DesignPlan, ctx: StoryContext, taste: bool = True) -> list[str]:
    """What is wrong with a shot plan. `taste` False: only what would break the film (the beats, the footage, the
    shot lengths the renderer needs), not the rules of taste (the hook, reuse, hits, the drop, pace): a person's own
    storyboard changes win over those."""
    problems: list[str] = []
    facts, beats = ctx.facts, ctx.beats
    keys = [m.key for m in plan.pool]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        problems.append(f"pool keys must be unique: {', '.join(dupes)}")
    pool = {m.key: m for m in plan.pool}
    for m in plan.pool:
        f = facts.get(m.clip)
        if f is None:
            problems.append(f"pool '{m.key}': clip {m.clip!r} is not in the catalog (use short ids like c03)")
            continue
        if not 0 <= m.start < m.end <= f.duration + 0.05:
            problems.append(f"pool '{m.key}': window {m.start}-{m.end} must lie inside {m.clip} (0-{f.duration:.2f}s)")
            continue
        if m.peak is not None and not m.start <= m.peak <= m.end:
            problems.append(f"pool '{m.key}': peak {m.peak} is outside its window {m.start}-{m.end}")
        bad = [(a, b) for a, b in f.avoid if min(b, m.end) - max(a, m.start) > 0.25] if taste else []
        if bad:
            problems.append(f"pool '{m.key}': {m.clip} {m.start}-{m.end} overlaps a range to avoid "
                            f"({', '.join(f'{a:.1f}-{b:.1f}' for a, b in bad)})")
        lm = logged_at(f, m.start, m.end)
        if getattr(ctx, "no_people", False) and lm is not None and people_in(lm) in ("near", "face"):
            problems.append(f"pool '{m.key}': {m.clip} {m.start}-{m.end} has people in view ({people_in(lm)}), and they "
                            "want nobody in the film: use another moment")
        banned = [b for b in getattr(ctx, "bans", []) if b["clip"] == m.clip
                  and min(b["end"], m.end) - max(b["start"], m.start) > 0.25]
        if banned:
            problems.append(f"pool '{m.key}': {m.clip} {m.start}-{m.end} is a scene they never want "
                            f"({banned[0].get('what') or 'removed before'}): use another moment")
        logged = max((min(x["end"], m.end) - max(x["start"], m.start) for x in f.moments), default=0.0)
        if logged < 0.5 * (m.end - m.start) and not getattr(ctx, "speech", False):  # a talk is cut on its words
            problems.append(f"pool '{m.key}': {m.clip} {m.start}-{m.end} is not inside a logged moment of that clip")
    missing = sorted({s.key for s in plan.plan} - set(pool))
    if missing:
        problems.append(f"the plan uses keys that are not in the pool: {', '.join(missing)}")
    total = plan_beats(plan)
    if abs(total - beats.target) > 1e-6 and not getattr(ctx, "speech", False):  # a talk is as long as what is said
        problems.append(f"the plan adds up to {total:g} beats; it must be exactly {beats.target} "
                        f"(add or remove {beats.target - total:+g} beats)")
    pos = 0.0
    starts = {}
    uses: dict[str, list[int]] = {}
    for i, s in enumerate(plan.plan):
        starts[round(pos, 3)] = s
        if s.beats * beats.period < 0.21:
            problems.append(f"shot {i + 1}: {s.beats} beats is only {s.beats * beats.period:.2f}s at this tempo: shots (and "
                            f"stutter pieces) must last at least 0.21s, so use at least {math.ceil(0.21 / beats.period * 2) / 2:g} "
                            "beats")
        if s.kind == "stutter" and not 2 <= s.count <= 16:
            problems.append(f"shot {i + 1}: a stutter needs 2-16 pieces")
        if s.transition != "cut" and not 0.25 <= s.transition_beats <= max(0.25, s.beats / 2):
            problems.append(f"shot {i + 1}: a {s.transition} lasts 0.25 to {max(0.25, s.beats / 2):g} beats")
        if s.frame_x is not None and not 0.0 <= s.frame_x <= 1.0:
            problems.append(f"shot {i + 1}: frame_x must be between 0 and 1")
        if s.key in pool and (f := facts.get(pool[s.key].clip)):
            need = source_need(s, beats.period, f.fps)
            if need > f.duration - 0.05:
                problems.append(f"shot {i + 1} ({s.kind} {s.beats:g} beats on {f.short}) needs {need:.1f}s of footage "
                                f"but {f.short} is only {f.duration:.1f}s long: shorten it or use a longer clip")
        uses.setdefault(s.key, []).append(i)
        pos += s.beats * (s.count if s.kind == "stutter" else 1)
    repeats = _place_repeats(plan, ctx)
    if repeats and getattr(ctx, "no_repeats", False) and not ctx.allow_reuse:
        problems += repeats  # they said no repeats: as much a must as the beats
    last = len(plan.plan) - 1
    if not taste:
        return problems
    if repeats and not getattr(ctx, "no_repeats", False) and not ctx.allow_reuse:
        problems += repeats
    if ctx.sync and plan.plan:
        problems += _sync_problems(plan, beats, hold_ok=getattr(ctx, "hold_ok", False))
    if plan.plan and not getattr(ctx, "hold_ok", False) and not getattr(ctx, "speech", False):  # a quick hook, no stalls
        first = plan.plan[0].beats * (plan.plan[0].count if plan.plan[0].kind == "stutter" else 1) * beats.period
        if first > 4.5:
            problems.append(f"the first shot lasts {first:.1f}s: the hook must land within ~2 s and the first shot "
                            f"end by 4.5 s ({int(4.5 / beats.period)} beats at most)")
        for i, s in enumerate(plan.plan):
            secs = s.beats * beats.period
            limit = 9.0 if i == last else 7.0
            if s.kind != "stutter" and secs > limit:
                problems.append(f"shot {i + 1} lasts {secs:.1f}s; keep shots under {limit:g}s "
                                f"({int(limit / beats.period)} beats) - split it or move on")
    hits = hero_hits(facts)
    if hits and plan.plan and len(hits) <= 2 * len(plan.plan):  # real hits are the heroes: don't leave them out for
        # repeats (but a talking video's claps and consonants are not heroes: more hits than shots is noise, not a cue)
        used = [(short, t) for short, t in hits if any(m.clip == short and m.start - 0.05 <= t <= m.end + 0.05 for m in plan.pool)]
        need = math.ceil(0.5 * min(len(hits), len(plan.plan)))  # most of them: a plan can have reasons to skip one
        if len(used) < need:
            left = [f"{short} at {t:.2f}" for short, t in hits if (short, t) not in used][:6]
            problems.append(f"{len(hits)} real hits were found in the footage but the plan uses only {len(used)}: give "
                            f"at least {need} their own shot (pool window around it, `peak` = its time). Unused: "
                            f"{', '.join(left)}")
    if ctx.pace == "fast" and plan.plan:
        lengths = [s.beats * (s.count if s.kind == "stutter" else 1) for s in plan.plan]
        mean = sum(lengths) / len(lengths)
        if mean > 2.6:
            problems.append(f"the pace was asked to be fast but the shots average {mean:.1f} beats: make most shots 1-2 "
                            "beats (fewer than 4 beats except one breath and the drop)")
        long_cuts = [i + 1 for i, s in enumerate(plan.plan) if s.kind == "cut" and s.beats > 4]
        if long_cuts:
            problems.append(f"fast pace: shots {', '.join(map(str, long_cuts[:6]))} last more than 4 beats; split them")
        slow = [i + 1 for i, s in enumerate(plan.plan) if s.transition not in ("cut",) and i > 0 and s.kind != "hold"]
        if len(slow) > 1:
            problems.append("fast pace: use hard cuts (no dissolves or whips) except at most one for the breath")
    # beat edits only where there is something to see: never a stutter on a railing or the ground
    strongest = max((int(x["strength"]) for f in facts.values() for x in f.moments), default=0)
    need = min(STRONG, strongest)
    drop_at = float(beats.drop) if beats.drop is not None else None
    pos2 = 0.0
    for i, s in enumerate(plan.plan):
        m = pool.get(s.key)
        f = facts.get(m.clip) if m else None
        on_drop = drop_at is not None and abs(pos2 - drop_at) < 1e-6
        if f and (s.kind in ("stutter", "hit", "ramp") or on_drop) and _window_strength(f, m) < need:
            what = "the shot on the drop" if on_drop and s.kind not in ("stutter", "hit", "ramp") else f"a {s.kind}"
            problems.append(f"shot {i + 1}: {what} needs a strong moment (strength {need}+), but '{s.key}' is a weaker one "
                            f"({_window_strength(f, m)}): put the beat edit on a strong moment and use this one plainly, "
                            "or leave it out")
        pos2 += s.beats * (s.count if s.kind == "stutter" else 1)
    # even thin footage: a stretch is shown at most twice
    for k, idx in uses.items():
        if k not in pool:
            continue
        same = [j for j in uses if j in pool and j != k and _overlap(pool[k], pool[j]) > 0.5]
        shown = len(idx) + sum(len(uses[j]) for j in same)
        if shown > MAX_SHOWINGS:
            problems.append(f"'{k}' ({pool[k].clip} {pool[k].start:g}-{pool[k].end:g}) is shown {shown} times: at most "
                            f"{MAX_SHOWINGS}. Use other moments, or hold the strong ones longer")
            break
    if ctx.allow_reuse:
        for k, idx in uses.items():
            close = [(a, b) for a, b in zip(idx, idx[1:]) if b - a < 3]
            if close:
                problems.append(f"'{k}' comes back after fewer than two other shots (shots {close[0][0] + 1} and "
                                f"{close[0][1] + 1}): put at least two shots between a moment and its reuse")
        problems += _overlapping_windows(plan, pool, uses, allowed=True)
    else:
        repeated = [k for k, idx in uses.items() if len(idx) > 1 and not (len(idx) == 2 and idx[-1] == last)]
        if repeated:
            problems.append(f"keys used more than once: {', '.join(repeated)} (each moment once; only the closing shot may "
                            "return to an earlier one)")
        problems += _overlapping_windows(plan, pool, uses, allowed=False)
    if beats.drop is not None:
        on_drop = starts.get(float(beats.drop))
        if on_drop is None:
            problems.append(f"no shot starts on the drop (beat {beats.drop}); shot boundaries are at "
                            f"{', '.join(f'{b:g}' for b in sorted(starts)[:24])}")
        elif on_drop.kind not in ("hit", "ramp"):
            problems.append(f"the shot on the drop (beat {beats.drop}) should be a hit, not a {on_drop.kind}")
    return problems


REUSE_FRAMES = ((1.14, (0.5, 0.56)), (1.26, (0.42, 0.5)), (1.08, (0.58, 0.44)), (1.2, (0.55, 0.6)), (1.32, (0.45, 0.42)))


def reuse_framing(reuses: int, action: bool = False) -> tuple[float, tuple[float, float]]:
    """How the n-th reuse of a moment is framed: a different punch-in each time (the viewer must still see the
    whole action: an action moment is cropped less)."""
    zoom, centre = REUSE_FRAMES[(reuses - 1) % len(REUSE_FRAMES)]
    return (min(zoom, 1.12) if action else zoom), centre


def to_design(plan: DesignPlan, ctx: StoryContext, song_path: str, vertical: bool, look: str = "vivid") -> Design:
    """The plan as the renderer's Design: slow motion only where the frames exist, transitions in seconds."""
    pool = {m.key: m for m in plan.pool}
    shots = []
    seen: list[PoolMoment] = []  # the windows already used, to tell a reuse
    for s in plan.plan:
        m = pool[s.key]
        f = ctx.facts[m.clip]
        kind = s.kind
        if kind == "ramp" and f.fps < SLOWMO_FPS:  # a ramp into 0.4x needs real slow-motion frames
            kind = "cut"
        speed = 1.0 if kind in ("hold", "hit") and f.fps < SLOWMO_FPS else None
        if s.speed is not None:  # set by hand on the timeline
            speed = min(4.0, max(0.25, s.speed))
        tr = None if s.transition == "cut" else (s.transition, round(max(0.25, s.transition_beats) * ctx.beats.period, 3))
        frame = (min(1.0, max(0.0, s.frame_x)), 0.5) if vertical and s.frame_x is not None else None
        # an action moment's peak lands on a beat: the one the plan names, else the shot's second beat
        peak_beat = s.peak_beat
        if kind in ("cut", "hold") and (peak_beat is not None or m.peak is not None):
            if peak_beat is None:
                peak_beat = 1.0 if s.beats >= 3 else 0.0
            peak_beat = round(max(0.0, min(peak_beat, s.beats - 0.25)) * 4) / 4
        else:
            peak_beat = None
        # the same footage again looks different: a punch-in on another part of the frame, alternating
        reuses = sum(1 for w in seen if _overlap(w, m) > 0.5)
        seen.append(m)
        zoom, center = 1.0, (0.5, 0.5)
        if s.zoom is not None:
            zoom, center = s.zoom, tuple(s.zoom_center or (0.5, 0.5))
        elif reuses:  # a new framing each time, but never so tight that an action moment's hit is lost
            zoom, center = reuse_framing(reuses, action=m.peak is not None)
        shots.append(Shot(kind=kind, beats=s.beats, key=s.key, count=s.count if kind == "stutter" else 1, speed=speed,
                          transition=tr, frame=frame, peak_beat=peak_beat, zoom=zoom, zoom_center=center,
                          flip=bool(s.flip), crack_db=s.crack_db, src_in=s.src_in,
                          push=None if s.push is None else min(0.4, max(-0.4, s.push))))
    def hero_of(m: PoolMoment) -> float | None:
        """Where the analyst said its subject is best in frame, if inside the window."""
        f = ctx.facts.get(m.clip)
        lm = logged_at(f, m.start, m.end) if f else None
        at = lm.get("best_at") if lm else None
        return float(at) if at is not None and m.start <= float(at) <= m.end else None

    return Design(name=plan.name, song=song_path, look=look, stabilize=True, intro=None, sound_design=True,
                  start_at=plan.start_at, fade_in=0.0 if ctx.pace in ("fast", "every") else 0.5,
                  vertical=vertical, pool={k: PoolEntry(clip=m.clip, start=m.start, end=m.end, peak=m.peak,
                                                        hero=hero_of(m)) for k, m in pool.items()}, plan=shots)


# ---------------------------------------------------------------- the Claude brain

def check_voice_script(sc: VoiceScript, utterances: list[dict], duration: float, mode: str, style: str) -> list[str]:
    """What is wrong with a voice/talk script, in words the model can act on (empty: fine)."""
    from ...explainer.script import fit_script

    problems = []
    fitted = fit_script(sc, duration, mode)
    if mode == "voice" and len(fitted.scenes) < max(2, int(duration // 7)):
        problems.append(f"only {len(fitted.scenes)} usable scenes for {duration:.0f} s: make one scene per point, "
                        "3-6 seconds each, inside the audio, not overlapping")
    if mode == "voice":  # nothing new on screen for long stretches while they talk = a slideshow
        marks = sorted({round(s.start, 2) for s in fitted.scenes} | {round(i.at, 2) for s in fitted.scenes for i in s.items}
                       | {round(duration, 2)})
        speech = [(u["start"], u["end"]) for u in utterances]
        still = [(a, b) for a, b in zip(marks, marks[1:]) if b - a > 6.0
                 and sum(max(0.0, min(b, e) - max(a, st)) for st, e in speech) > 4.0]
        if still:
            problems.append("nothing new appears for too long while they speak: " + ", ".join(
                f"{a:.0f}-{b:.0f} s" for a, b in still[:4]) + ": split those into shorter scenes, or add items on the "
                "words, so something new lands at least every 4 s")
    if mode == "talk" and style == "designed":
        drawn = [c for c in fitted.callouts if c.kind == "scene" and c.visual]
        if len(drawn) < max(1, int(duration // 9)):
            problems.append(f"only {len(drawn)} `scene` callouts with a `visual` for {duration:.0f} s: illustrate "
                            "what is said about every 3-6 s with a scene (visual + topics)")
        marks = sorted([c.at for c in fitted.callouts] + [s.start for s in fitted.scenes])
        speech = [(u["start"], u["end"]) for u in utterances]
        edges = [0.0, *marks, duration]
        gaps = [(a, b) for a, b in zip(edges, edges[1:]) if b - a > 10.0
                and sum(max(0.0, min(b, e) - max(a, st)) for st, e in speech) > 6.0]
        if gaps:
            problems.append("no graphics for long stretches while they speak: " + ", ".join(
                f"{a:.0f}-{b:.0f} s" for a, b in gaps[:4]) + ": add scenes there (every 3-6 s)")
    if mode == "talk":
        from ...explainer.script import NUMBER

        shown = [c.at for c in fitted.callouts if c.kind in ("stat", "stack")]
        shown += [s.start for s in fitted.scenes if s.layout == "stat"]
        for u in utterances:
            for m in NUMBER.finditer(u["text"]):
                if not any(abs(x - u["start"]) < 6 or u["start"] - 1 <= x <= u["end"] + 1 for x in shown):
                    problems.append(f"'{m.group(0)}' is said at {u['start']:.0f} s but not shown: give it a `stat` "
                                    "(value as said) or a `stack`")
                    break
    if mode == "talk":
        need = max(1, int(duration // 16))
        have = len(fitted.callouts) + len(fitted.scenes)
        if have < need:
            problems.append(f"only {have} callouts and cutaways for {duration:.0f} s: aim for one every 8-14 s "
                            f"(at least {need}), covering every number said and each new topic when it starts")
    thin = [s.headline for s in fitted.scenes if s.layout in ("list", "steps") and len(s.items) < 2]
    if thin:
        problems.append(f"list/steps scenes need 2-4 items: {', '.join(thin[:3])}")
    flow = [s.headline for s in fitted.scenes if s.layout in ("flow", "chat") and not 2 <= len(s.items) <= 5]
    if flow:
        problems.append(f"flow/chat scenes need 2-4 (flow) or 2-5 (chat) items: {', '.join(flow[:3])}")
    bad = [s.headline for s in fitted.scenes if s.layout == "compare" and len(s.items) != 2]
    if bad:
        problems.append(f"compare scenes need exactly 2 items: {', '.join(bad[:3])}")
    return problems


LOOK_TEXT = ("# The look they picked\nThe video is drawn in this look: every scene stands in front of a painted "
             "place in its world. For each scene write `backdrop`: that place as an image prompt in the look's world "
             "(what is there, where, the light), picturing what is being said (a talk about a model learning from "
             "video: the observatory's great wall of screens; about how it predicts: a corridor of glowing doorways). "
             "Neighbouring scenes may stay in one place: give them the SAME backdrop words and the camera moves on "
             "through it; change place when the topic changes, about every 6-12 seconds. Never text or letters in a "
             "backdrop. `camera` per scene: push (towards, the default), pull (away, for a reveal or an ending), left "
             "or right (travel, a new topic), rise (up, for growth or a big idea), still (a quiet beat). Set the "
             "script's `palette` to the look's palette. talk mode: `stage` is the place behind the person for the "
             "whole video. The look's world and style are already known to the painter: describe only the place.\n"
             "Each scene's `visual` is PAINTED in the look as one prop that pops in front of the backdrop: one concrete "
             "object or a small tight group that shows the point ('a paper brain clipped to a calendar page', 'a "
             "balance scale with a toggle switch on one pan'), never a whole scene, never text. Give every scene its "
             "own visual; the same words only when the same object should come back.\n")


def check_directions(d: Directions, n: int) -> list[str]:
    problems = []
    if len(d.directions) != n:
        problems.append(f"return exactly {n} look{'s' if n > 1 else ''}, not {len(d.directions)}")
    titles = [x.title.strip().lower() for x in d.directions]
    if len(set(titles)) != len(titles):
        problems.append("every look needs its own title")
    for x in d.directions:
        if not 3 <= len(x.colours) <= 5 or not all(re.fullmatch(r"#[0-9a-fA-F]{6}", c.strip()) for c in x.colours):
            problems.append(f"'{x.title}': colours must be 3-5 hex colours like #1a2b3c")
        if len(x.preview.split()) < 12:
            problems.append(f"'{x.title}': the preview must be a full image prompt (at least a sentence or two)")
    return problems


def check_backdrops(sc: VoiceScript, duration: float, mode: str) -> list[str]:
    if mode == "talk":
        return [] if sc.stage.strip() else ["talk with a look: write `stage`, the place behind the person"]
    missing = [s.headline or f"{s.start:.0f}s" for s in sc.scenes if not s.backdrop.strip()]
    problems = [f"every scene needs a `backdrop` (missing for: {', '.join(missing[:4])})"] if missing else []
    places = {s.backdrop.strip().lower() for s in sc.scenes if s.backdrop.strip()}
    most = max(3, min(14, int(duration // 5)))
    if len(places) > most:
        problems.append(f"{len(places)} different backdrops for {duration:.0f} s is too many: use at most {most}, "
                        "keeping neighbouring scenes on one topic in the same place (the same backdrop words)")
    return problems


class Brain:
    kind = "claude"

    def __init__(self, backend, models: Models, spend: SpendFn, log_dir: Path | None = None):
        self.backend = backend
        self.models = models
        self.spend = spend
        self.log_dir = log_dir

    @property
    def backend_name(self) -> str:
        return getattr(self.backend, "name", "?")

    @property
    def parallel(self) -> int:
        """Clips logged at once: the API takes several requests; each Claude Code run is a whole process."""
        return {"api": 4, "claude_code": 2}.get(self.backend_name, 1)

    def _log(self, name: str, data: dict) -> None:
        if self.log_dir is None:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        (self.log_dir / f"{name}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")

    def _ask(self, req: Request, check=None, tag: str = "", fallback=None):
        def log(name, data):
            self._log(f"{tag or req.task}-{name.split('-')[-1]}", data)

        return structured(self.backend, req, self.spend, check, log=log, fallback=fallback)

    # -- 1 analyse a clip
    def analyse_clip(self, clip: ClipInfo, brief: str, sheets: list[Path]) -> ClipAnalysis:
        text = (brief + "\n\nReturn the analysis of this clip as JSON. Use clip_id "
                f"\"{clip.clip_id}\". The clip is {clip.duration:.2f} s long.")
        req = Request("analyse", prompt("analyst"), text, ClipAnalysis, self.models.analyst, list(sheets),
                      effort="medium", max_tokens=6000)

        def check(a: ClipAnalysis) -> list[str]:
            a.clip_id = clip.clip_id
            return check_analysis(a, clip)

        return self._ask(req, check, tag=f"analyse-{clip.clip_id}")

    def understand_talk(self, utterances: list[dict], duration: float, mode: str, intent: str,
                        rules: list[dict]) -> TalkUnderstanding:
        """What it understood from a recording (the story, its parts, the names on screen), for them to correct."""
        lines = [f"[{u['start']:6.1f}-{u['end']:6.1f}] {u['text']}" for u in utterances]
        what = ("a recording only: the animation is all they see" if mode == "voice" else
                "a video of them talking: they stay on screen and the graphics land on their words")
        parts = [f"# Mode\n{mode} ({what}); the recording is {duration:.0f} seconds long.",
                 "# What the person asked for\n" + (intent.strip() or "(nothing specific)"),
                 "# This person's style rules\n" + rules_text(rules),
                 "# The transcript (seconds into the recording)\n" + "\n".join(lines)[:40000],
                 "Return what you understood as JSON."]
        req = Request("understand", prompt("understand_talk"), "\n\n".join(parts), TalkUnderstanding,
                      self.models.analyst, effort="medium", max_tokens=4000)
        return self._ask(req, lambda u: check_talk_understanding(u, utterances, duration))

    def beat_idea(self, ask: str, film: str, purpose: str, seconds: float, pace: str, before: dict | None = None,
                  change: str = "", layout: list[tuple[str, float]] | None = None) -> BeatIdea:
        """How the film's own track should sound, from their words about the music and the film. `layout`: the parts
        the tool will make and about when each starts, so the reading only promises what will be there."""
        parts = [f"# What the music is for\n{purpose}: {PURPOSE.get(purpose, purpose)}. About {seconds:.0f} seconds; "
                 f"the film is cut at a {pace} pace.",
                 "# What they said about the music\n" + (ask.strip() or "(nothing: pick what suits the film)"),
                 "# The film\n" + (film.strip() or "(nothing specific)")]
        if layout:
            names = list(dict.fromkeys(name for name, _ in layout))
            parts.append("# The parts this track will have (laid out by the tool)\n"
                         + ", ".join(f"{n} at about {int(t) // 60}:{int(t) % 60:02d}" for n, t in layout)
                         + f"\nDescribe only these in `reading`, and leave the style lists of any other part empty "
                           f"(the parts: {', '.join(names)}).")
        if before:
            parts.append("# The track they heard\n" + json.dumps(before, indent=1) + "\n\n# What they want changed\n"
                         + (change.strip() or "(they want something different: change the sound clearly)")
                         + "\n\nThey heard that track and asked for a change, so the new one must sound clearly "
                           "different in exactly what they asked: change the styles, instruments, tempo or mood that "
                           "the request points at (not just the wording), and keep only what they did not ask to "
                           "change. Start `reading` by saying in plain words what is different from the last track.")
        parts.append("Return the track as JSON.")
        req = Request("beat", prompt("beat"), "\n\n".join(parts), BeatIdea, self.models.analyst, effort="low",
                      max_tokens=1500)
        return self._ask(req, check_beat_idea)

    def polish_understanding(self, fields: dict, kind: str) -> dict:
        """Their brief on 'what I understood', reworded clearly (nothing added, nothing lost)."""
        text = (f"# The kind of video\n{kind}\n\n# Their brief\n" + json.dumps(fields, indent=1, ensure_ascii=False)
                + "\n\nReturn the polished brief as JSON.")
        req = Request("polish", prompt("polish"), text, PolishedBrief, self.models.analyst, effort="low", max_tokens=1500)
        out = self._ask(req)
        return {"story": out.story.strip(), "feel": [w.strip() for w in out.feel if w.strip()][:6],
                "must": [x.strip() for x in out.must if x.strip()][:12], "avoid": [x.strip() for x in out.avoid if x.strip()][:12]}

    # -- 2 pitch
    def understand(self, ctx: StoryContext) -> Understanding:
        """What it understood from the clips, the music and their request, in plain words for them to correct."""
        parts = [ctx.header(), "# The footage\n" + catalog_text(ctx.catalog),
                 "Return what you understood as JSON: one `clips` entry per clip in the log."]
        req = Request("understand", prompt("understand"), "\n\n".join(parts), Understanding, self.models.analyst,
                      effort="medium", max_tokens=4000)
        return self._ask(req, lambda u: check_understanding(u, ctx))

    def pitch(self, ctx: StoryContext, n: int = 3, rejected: list[StoryPitch] | None = None, reason: str = "",
              own_idea: str = "") -> list[StoryPitch]:
        parts = [ctx.header(), "# The footage\n" + catalog_text(ctx.catalog)]
        if rejected:
            parts.append("# Already pitched and turned down\n" + "\n".join(f"- {p.title}: {p.logline}" for p in rejected)
                         + (f"\nTheir reason: {reason}" if reason else "") + "\nPitch something genuinely different.")
        if own_idea:
            parts.append(f"# Their own idea\n{own_idea}\nShape it into {'a pitch' if n == 1 else 'pitches'} that the "
                         "footage can carry; keep their idea at the centre.")
        parts.append(f"Return {n} pitch{'es' if n > 1 else ''} as JSON.")
        req = Request("pitch", prompt("director"), "\n\n".join(parts), Pitches, self.models.director,
                      effort="high", max_tokens=8000)
        out = self._ask(req, lambda p: check_pitches(p, n, ctx.facts))
        return out.pitches

    # -- 3 design and 4 revise
    def _design(self, ctx: StoryContext, pitch: StoryPitch, extra: str, task: str) -> DesignPlan:
        story = json.dumps(pitch.model_dump(), indent=1)
        parts = [ctx.header(), "# The footage\n" + catalog_text(ctx.catalog), "# The chosen story\n" + story]
        if extra:
            parts.append(extra)
        parts.append(f"Return the shot plan as JSON. Its beats must add up to exactly {ctx.beats.target}.")
        req = Request(task, prompt("editor"), "\n\n".join(parts), DesignPlan, self.models.editor, effort="high",
                      max_tokens=16000)  # a whole plan after high-effort thinking
        plan = self._ask(req, lambda p: check_plan(p, ctx), fallback=lambda p: check_plan(p, ctx, taste=False))
        return sync_snap(plan, ctx)  # beat sync asked and still missed: the cuts are put on the hits by hand

    def design(self, ctx: StoryContext, pitch: StoryPitch, note: str = "") -> DesignPlan:
        ctx.reuse_if_asked(note)
        extra = f"# Their note on an earlier sample\n{note}" if note else ""
        return self._design(ctx, pitch, extra, "design")

    def revise(self, ctx: StoryContext, pitch: StoryPitch, current: DesignPlan, feedback: str) -> DesignPlan:
        ctx.reuse_if_asked(feedback)
        extra = ("# The current plan (the video they watched)\n" + json.dumps(current.model_dump(), indent=1) +
                 "\n\n# What they want changed\n" + feedback +
                 "\n\nChange what they asked for and keep what works; don't rebuild a plan they mostly liked.")
        return self._design(ctx, pitch, extra, "revise")

    # -- 1b older logs: each moment's place and who is in it, read from the log's own words (Pipeline.fill_places)
    def places(self, text: str, ids: list[str]) -> PlaceTags:
        want = set(ids)

        def check(t: PlaceTags) -> list[str]:
            got = [x.id for x in t.tags]
            missing, extra = sorted(want - set(got)), sorted(set(got) - want)
            dup = sorted({i for i in got if got.count(i) > 1})
            return ([f"tag every moment: missing {', '.join(missing[:12])}"] if missing else []) + \
                   ([f"these ids are not in the list: {', '.join(extra[:12])}"] if extra else []) + \
                   ([f"tag each moment once: {', '.join(dup[:12])}"] if dup else [])

        req = Request("places", prompt("places"), text + "\n\nReturn the tags as JSON.", PlaceTags, self.models.analyst,
                      effort="low", max_tokens=6000)
        return self._ask(req, check)

    # -- 4b a change to a finished film: read it into exact changes on its numbered shots (see versions.py)
    def read_changes(self, text: str, words: str, n: int, facts: dict, earlier: dict[int, int]) -> ChangeRequest:
        """`text`: the version and its context (versions.request_text); `words`: what they wrote."""
        from ..versions import check_request

        req = Request("changes", prompt("changes"), text, ChangeRequest, self.models.analyst, effort="medium",
                      max_tokens=6000)
        return self._ask(req, lambda r: check_request(r, n, facts, earlier))

    # -- 4c one stretch of a finished film made again; every other shot stays exactly as it is
    def redo_range(self, ctx: StoryContext, pitch: StoryPitch, plan: DesignPlan, lo: int, hi: int, want: str) -> DesignPlan:
        from ..versions import merge_range, range_beats, range_text

        start, need = range_beats(plan, lo, hi)
        parts = [ctx.header(), "# The footage\n" + catalog_text(ctx.catalog),
                 "# The chosen story\n" + json.dumps(pitch.model_dump(), indent=1),
                 "# The film now (shot number, keep or make again, beats, kind, clip window: what it shows)\n"
                 + range_text(plan, ctx, lo, hi),
                 f"# Make again ONLY shots {lo + 1}-{hi + 1}\nThey start on beat {start:g} and must last exactly "
                 f"{need:g} beats in total. What the person wants there: {want}\nEvery shot marked keep stays exactly "
                 "as it is; don't show a moment any kept shot shows. Return only the new shots for this stretch "
                 "(as many as it needs) and the moments they use."]
        req = Request("redo", prompt("editor"), "\n\n".join(parts), RangePlan, self.models.editor, effort="medium",
                      max_tokens=8000)
        others = [m for k, m in enumerate(plan.pool) if any(s.key == m.key for j, s in enumerate(plan.plan)
                                                              if not lo <= j <= hi)]

        def check(part: RangePlan, taste: bool = True) -> list[str]:
            got = sum(x.beats * (x.count if x.kind == "stutter" else 1) for x in part.plan)
            if not part.plan:
                return ["return at least one shot for the stretch"]
            if abs(got - need) > 1e-6:
                return [f"the new shots add up to {got:g} beats; the stretch is exactly {need:g} (add or remove {need - got:+g})"]
            merged = merge_range(plan, lo, hi, part)
            out = [p for p in check_plan(merged, ctx, taste=False)]
            if taste:
                if not ctx.allow_reuse:
                    for m in part.pool:
                        if any(_overlap(m, o) > 0.5 for o in others):
                            out.append(f"pool '{m.key}' ({m.clip} {m.start:g}-{m.end:g}) is already shown by a shot "
                                       "they keep: use a moment the film doesn't show yet")
                if ctx.sync:
                    out += _sync_problems(merged, ctx.beats, start, start + need, ctx.hold_ok)
            return out

        part = self._ask(req, check, fallback=lambda r: check(r, taste=False))
        merged = merge_range(plan, lo, hi, part)
        return sync_snap(merged, ctx, start, start + need)

    # -- 6a the looks of a voice or talking video, pitched before it is drawn
    def directions(self, utterances: list[dict], duration: float, mode: str, intent: str, rules: list[dict],
                   shape: str, n: int = 3, rejected: list[Direction] | None = None, reason: str = "",
                   own_idea: str = "", base: Direction | None = None, change: str = "",
                   taste: str = "") -> list[Direction]:
        lines = [f"[{u['start']:6.1f}] {u['text']}" for u in utterances]
        what = ("a recording only: the animation is all they see" if mode == "voice" else
                "a video of them talking: they are cut out and stand in front of the world")
        parts = [f"# Mode\n{mode} ({what}); {duration:.0f} seconds; shape {shape}.",
                 "# What the person asked for\n" + (intent.strip() or "(nothing specific)"),
                 "# This person's style rules\n" + rules_text(rules),
                 "# Their taste in looks so far (newest first)\n" + (taste.strip() or "(their first video: no "
                                                                      "history yet, so show them a wide range)"),
                 "# The transcript\n" + "\n".join(lines)[:24000]]
        if rejected:
            parts.append("# Looks already shown\n" + "\n".join(f"- {d.title}: {d.art_style}; {d.world[:120]}"
                                                                for d in rejected)
                         + (f"\nWhat they said: {reason}" if reason else "") + "\nPitch something clearly different.")
        if own_idea:
            parts.append(f"# Their own idea\n{own_idea}\nTurn it into a full look; keep their idea at the centre.")
        if base is not None:
            parts.append("# The look to change\n" + json.dumps(base.model_dump(), indent=1) +
                         f"\n\n# What they want changed\n{change}\nKeep the rest; make the change plain to see.")
        parts.append(f"Return {n} look{'s' if n > 1 else ''} as JSON.")
        req = Request("looks", prompt("looks"), "\n\n".join(parts), Directions, self.models.director, effort="medium",
                      max_tokens=6000)
        return self._ask(req, lambda d: check_directions(d, n)).directions

    # -- 6 the script of a voice (explainer videos)
    def script_voice(self, utterances: list[dict], duration: float, mode: str, intent: str, rules: list[dict],
                     previous: VoiceScript | None = None, feedback: str = "", style: str = "labels",
                     look: Direction | None = None) -> VoiceScript:
        lines = [f"[{u['start']:6.1f}-{u['end']:6.1f}] {u['text']}" for u in utterances]
        parts = [f"# Mode\n{mode}; the audio is {duration:.1f} seconds long.",
                 "# What the person asked for\n" + (intent.strip() or "(nothing specific: do what suits the talk)"),
                 "# This person's style rules\n" + rules_text(rules),
                 "# The transcript (seconds into the audio)\n" + "\n".join(lines)]
        if previous is not None:
            parts.append("# The current script (the video they watched)\n" + json.dumps(previous.model_dump(), indent=1) +
                         "\n\n# What they want changed\n" + feedback +
                         "\n\nChange what they asked for and keep what works.")
        if style == "designed":
            parts.insert(1, DESIGNED_TEXT)
        if look is not None:
            parts.insert(2, LOOK_TEXT + json.dumps(look.model_dump(), indent=1))
        parts.append("Return the script as JSON.")
        req = Request("script", prompt("voice"), "\n\n".join(parts), VoiceScript, self.models.editor, effort="medium",
                      max_tokens=10000)

        def check(sc: VoiceScript) -> list[str]:
            return check_voice_script(sc, utterances, duration, mode, style) + (
                check_backdrops(sc, duration, mode) if look is not None else [])

        return self._ask(req, check)

    # -- 7 animations for the moments of a script
    def rethink_visuals(self, needs: list[dict]) -> list[dict]:
        nl = chr(10)
        text = ("These moments found no good designer animation in a free animation library that is searched by simple "
                "topic words. For each, in the same order, give a simpler concrete picture that still shows the idea, and "
                "2-3 common one-word search terms that a big free animation library surely has (movie, film, calendar, "
                "notification, search, question, ghost, phone, star, money, heart, clock, idea, chat, music...)." + nl +
                nl.join(f"{k}. {n['visual']} (searched: {', '.join(n['topics']) or '-'})" for k, n in enumerate(needs)))
        req = Request("rethink", "You help a motion designer find stock animations.", text, VisualIdeas, self.models.coach,
                      effort="low", max_tokens=1500)

        def check(r: VisualIdeas) -> list[str]:
            return [] if len(r.ideas) == len(needs) else [f"give exactly {len(needs)} ideas, in order"]

        return [i.model_dump() for i in self._ask(req, check).ideas]

    def pick_visuals(self, needs: list[dict]) -> list[int]:
        nl = chr(10)
        lines = [f"Need {k}: {n['visual']} (sheet {k + 1} of the attached images; candidates 0-{n['n'] - 1})"
                 for k, n in enumerate(needs)]
        text = "# The needs" + nl + nl.join(lines) + nl + nl + "Return one pick per need as JSON."
        req = Request("visuals", prompt("visuals"), text, VisualPicks, self.models.analyst, [Path(n["sheet"]) for n in needs],
                      effort="low", max_tokens=1500)

        def check(p: VisualPicks) -> list[str]:
            bad = [x.need for x in p.picks if not 0 <= x.need < len(needs) or not -1 <= x.choice < needs[x.need]["n"]]
            return [f"picks out of range for needs {bad}"] if bad else []

        picks = self._ask(req, check)
        out = [-1] * len(needs)
        for x in picks.picks:
            out[x.need] = x.choice
        return out

    # -- 5 learn
    def learn(self, rules: list[dict], events: list[str]) -> ProfileUpdate:
        text = ("# Current rules\n" + rules_text(rules) + "\n\n# What they did and said on this video\n" +
                "\n".join(f"- {e}" for e in events) + "\n\nReturn the profile update as JSON.")
        req = Request("learn", prompt("coach"), text, ProfileUpdate, self.models.coach, effort="low", max_tokens=2000)
        ids = {r["id"] for r in rules}

        def check(u: ProfileUpdate) -> list[str]:
            bad = [d for d in u.drop if d not in ids]
            return [f"drop names unknown rule ids: {', '.join(bad)}"] if bad else []

        return self._ask(req, check)

    # -- 6 your editor answers the person (the main agent's own turn: see agent.py)
    def editor_reply(self, text: str, words: str, facts: dict, check) -> EditorReply:
        """`text`: everything the editor reads this turn (agent.view); `check`: what its answer must fit (an offer
        that can't be made now, unknown ids). An answer that still has such problems after its tries is used without
        them (agent.trim): the person always gets a reply."""
        req = Request("editor", prompt("editor_chat"), text, EditorReply, self.models.assistant, effort="high",
                      max_tokens=4000)
        return self._ask(req, check, fallback=lambda r: [] if r.say.strip() else ["say something back"])


# ---------------------------------------------------------------- the no-AI brain

class LocalBrain:
    """Every job done with plain rules over the measurements: no AI, no cost. Good enough to test the whole
    studio end to end and to keep it working without a key; not good enough to impress anyone."""

    kind = "local"
    backend_name = "local"

    def __init__(self, spend: SpendFn | None = None, signals: Callable[[str], ClipSignals | None] | None = None):
        self.spend = spend or (lambda u: None)
        self.signals = signals  # clip_id -> measured signals

    def _free(self, task: str) -> None:
        self.spend(Usage(task, "local", "local", billed=False))

    def places(self, text: str, ids: list[str]) -> PlaceTags:
        """Without the AI: no places (only the AI can tell two angles of one landmark); people from the log's words."""
        from .schemas import PlaceTag

        self._free("places")
        said = {}
        for line in text.splitlines():
            mid, _, words = line.lstrip("- ").partition(": ")
            if mid in ids:
                said[mid] = people_in({"description": words})
        return PlaceTags(tags=[PlaceTag(id=i, place="", people=said.get(i, "unknown")) for i in ids])

    def read_changes(self, text: str, words: str, n: int, facts: dict, earlier: dict[int, int]) -> ChangeRequest:
        from ..versions import read_simple

        self._free("changes")
        return read_simple(words, n)

    def redo_range(self, ctx: StoryContext, pitch: StoryPitch, plan: DesignPlan, lo: int, hi: int, want: str) -> DesignPlan:
        """Without the AI: the stretch as 2-beat cuts of the strongest moments the film doesn't show yet."""
        from ..versions import merge_range, range_beats

        self._free("redo")
        _, need = range_beats(plan, lo, hi)
        used = [m for j, s in enumerate(plan.plan) for m in plan.pool if m.key == s.key and not lo <= j <= hi]
        ranked = [r[0] for r in sorted(self._ranked(ctx), key=lambda r: -r[2])]
        picks = []
        for mid in ranked:
            m = moment_by_id(ctx.facts, mid)
            short = mid.split("#")[0]
            if m is None:
                continue
            pm = PoolMoment(key=mid.replace("#", "_"), clip=short, start=float(m["start"]), end=float(m["end"]),
                            peak=m.get("peak"))
            if any(_overlap(pm, u) > 0.5 for u in used) or any(b["clip"] == short and min(b["end"], pm.end)
                                                              - max(b["start"], pm.start) > 0.25 for b in ctx.bans):
                continue
            picks.append(pm)
        if not picks:
            picks = [m for m in plan.pool if any(s.key == m.key for s in plan.plan[lo:hi + 1])] or plan.pool[:1]
        shots, left, k = [], need, 0
        while left > 1e-6:
            n = min(2.0, left) if left - 2.0 >= 1.0 or left <= 2.0 else left
            pm = picks[k % len(picks)]
            shots.append(PlanShot(kind="cut", beats=n, key=pm.key))
            left -= n
            k += 1
        used_keys = {s.key for s in shots}
        part = RangePlan(pool=[m for m in picks if m.key in used_keys], plan=shots, notes=want)
        return merge_range(plan, lo, hi, part)

    def polish_understanding(self, fields: dict, kind: str) -> dict:
        """Without the AI: tidy spacing and capitals, drop empty and repeated lines."""
        self._free("polish")

        def tidy(x: str) -> str:
            x = " ".join(str(x).split()).strip(" -•")
            return x[:1].upper() + x[1:]

        def uniq(items) -> list[str]:
            return list(dict.fromkeys(tidy(i) for i in items if str(i).strip()))[:12]
        return {"story": tidy(fields.get("story", "")), "feel": uniq(fields.get("feel", []))[:6],
                "must": uniq(fields.get("must", [])), "avoid": uniq(fields.get("avoid", []))}

    def analyse_clip(self, clip: ClipInfo, brief: str, sheets: list[Path]) -> ClipAnalysis:
        self._free("analyse")
        sig = self.signals(clip.clip_id) if self.signals else None
        windows = sorted(sig.best_windows, key=lambda w: -w.score)[:4] if sig else []
        moments = []
        for w in sorted(windows, key=lambda w: w.start):
            start, end = max(0.0, w.start), min(clip.duration, w.end)
            if end - start < 0.4:
                continue
            hook = int(min(9, max(2, round(3 + 6 * w.interest))))
            strength = int(min(9, max(2, round(3 + 6 * w.quality))))
            kind = "action" if w.interest > 0.5 else "scenic"
            moments.append({"start": round(start, 2), "end": round(end, 2), "peak": round((start + end) / 2, 2),
                            "kind": kind, "description": f"{'Busy' if kind == 'action' else 'Calm'} stretch "
                                                         f"{start:.1f}-{end:.1f}s",
                            "hook": hook, "strength": strength, "best_use": "highlight" if hook >= 6 else "b-roll",
                            "sound": "either"})
        if not moments:
            mid = clip.duration / 2
            half = min(clip.duration / 2, 2.0)
            moments.append({"start": round(mid - half, 2), "end": round(mid + half, 2), "peak": None, "kind": "other",
                            "description": "Middle of the clip", "hook": 3, "strength": 3, "best_use": "b-roll",
                            "sound": "either"})
        unusable = [{"start": b.start, "end": b.end, "reason": b.reason or "measured problem"}
                    for b in (sig.bad_intervals if sig else []) if b.end - b.start > 2.5]
        energy = int(min(5, max(1, round(1 + 4 * (sig.motion_median * 4 if sig else 0.4)))))
        appeal = int(min(9, max(2, round(10 * (sig.quality_mean if sig else 0.5)))))
        return ClipAnalysis.model_validate({
            "clip_id": clip.clip_id, "summary": f"{clip.duration:.0f} s {clip.orientation} clip (logged without AI)",
            "scene": "unknown", "camera": "unknown", "energy": energy, "visual_appeal": appeal,
            "unusable": [u for u in unusable if not any(u["start"] <= m["start"] and m["end"] <= u["end"] for m in moments)],
            "moments": moments, "tags": []})

    @staticmethod
    def _ranked(ctx: StoryContext) -> list[tuple[str, dict, float]]:
        out = []
        for short, f in ctx.facts.items():
            for n, m in enumerate(f.moments):
                out.append((f"{short}#{n + 1}", m, m["hook"] + m["strength"]))
        return out

    def understand(self, ctx: StoryContext) -> Understanding:
        """Without an AI: the analyst's own log, said plainly (their note as the story, each clip's summary and best
        moment, the song's tempo and first drop)."""
        self._free("understand")
        return local_understanding(ctx)

    def understand_talk(self, utterances: list[dict], duration: float, mode: str, intent: str,
                        rules: list[dict]) -> TalkUnderstanding:
        self._free("understand")
        return local_talk_understanding(utterances, duration, intent)

    def beat_idea(self, ask: str, film: str, purpose: str, seconds: float, pace: str, before: dict | None = None,
                  change: str = "", layout: list[tuple[str, float]] | None = None) -> BeatIdea:
        self._free("beat")
        return local_beat_idea(ask, film, purpose, pace, before, change)

    def pitch(self, ctx: StoryContext, n: int = 3, rejected=None, reason: str = "", own_idea: str = "") -> list[StoryPitch]:
        self._free("pitch")
        ranked = self._ranked(ctx)
        if not ranked:
            raise BrainError("no logged moments to pitch from")
        by_time = [r[0] for r in ranked]
        by_score = [r[0] for r in sorted(ranked, key=lambda r: -r[2])]
        by_energy = [r[0] for r in sorted(ranked, key=lambda r: (r[1]["kind"] != "scenic", r[2]))]
        ideas = [("In order", "The day as it happened, building to its best moment.", by_time),
                 ("Best first", "Open on the strongest moment, then show how it came to be.", by_score),
                 ("Calm to rush", "Start quiet and let the energy climb into the drop.", by_energy)]
        if own_idea:
            ideas.insert(0, ("Your idea", own_idea[:200], by_score))
        taken = {p.title for p in rejected or []}
        out = []
        for title, line, order in [i for i in ideas if i[0] not in taken] + ideas:
            if len(out) == n:
                break
            if any(p.title == title for p in out):
                continue
            k = max(1, len(order) // 3)
            beats = [StoryBeat(what=w, moments=order[i * k:(i + 1) * k][:4] or order[:1], music=mu)
                     for i, (w, mu) in enumerate((("The opening", "intro"), ("The build", "build"), ("The peak", "drop")))]
            out.append(StoryPitch(title=title, logline=line, feeling="calm -> lift", hook=f"{order[0]} first",
                                  turn="the music lifts", ending="the strongest moment, held", beats=beats,
                                  stills=(order[:3] if len(order) >= 3 else (order * 3)[:3]),
                                  why="Picked from the measured moments (no AI)."))
        return out

    def design(self, ctx: StoryContext, pitch: StoryPitch, note: str = "") -> DesignPlan:
        self._free("design")
        return self._plan(ctx, pitch, calm=bool(re.search(r"\b(fast|quick|rushed|hectic)\b", note, re.I)))

    def revise(self, ctx: StoryContext, pitch: StoryPitch, current: DesignPlan, feedback: str) -> DesignPlan:
        self._free("revise")
        calm = bool(re.search(r"\b(fast|quick|rushed|hectic|calm|slower)\b", feedback, re.I))
        return self._plan(ctx, pitch, calm=calm, skip={m.clip for m in current.pool} if "different" in feedback else None)

    def _plan(self, ctx: StoryContext, pitch: StoryPitch, calm: bool = False, skip: set[str] | None = None) -> DesignPlan:
        """Story moments in pitch order, then the rest by score; holds where the music breathes, cuts in the
        build, a stutter into the drop and a hit on it, and a hold to end - sized to the target exactly."""
        beats = ctx.beats
        period = beats.period
        order = [m for b in pitch.beats for m in b.moments]
        order += [r[0] for r in sorted(self._ranked(ctx), key=lambda r: -r[2])]
        seen, pool, entries = set(), [], []
        for mid in order:
            if mid in seen:
                continue
            seen.add(mid)
            m = moment_by_id(ctx.facts, mid)
            short = mid.split("#")[0]
            if m is None or (skip and short in skip and len(seen) < len(order) - 3):
                continue
            f = ctx.facts[short]
            entries.append((mid, m, f))
        if not entries:
            raise BrainError("no usable moments for a plan")
        drop = beats.drop
        target = beats.target
        shots: list[tuple[str, float, int]] = []  # kind, beats, count
        long_ = 8 if calm else 4
        if drop is None:
            pos = 0
            while pos < target:
                n = min(long_ if pos == 0 or pos >= target - 8 else 2 if not calm else 4, target - pos)
                shots.append(("hold" if pos == 0 or pos + n >= target else "cut", n, 1))
                pos += n
        else:
            pos = 0
            while pos < drop:
                left = drop - pos
                if left == 4 and not calm:
                    shots.append(("stutter", 0.5, 8))
                    pos += 4
                    continue
                n = min(long_ if pos < 8 else 2 if not calm else 4, left)
                shots.append(("hold" if pos == 0 else "cut", n, 1))
                pos += n
            shots.append(("hit", min(8, target - drop), 1))
            pos += shots[-1][1]
            while pos < target:
                n = min(4 if calm else 2, target - pos)
                if target - pos <= 8:
                    n = target - pos
                    shots.append(("hold", n, 1))
                else:
                    shots.append(("cut", n, 1))
                pos += n
        plan, used = [], 0
        for kind, n, count in shots:
            need = PlanShot(kind=kind, beats=n, key="x", count=count)
            pick = None
            for k in range(len(entries)):
                mid, m, f = entries[(used + k) % len(entries)]
                if source_need(need, period, f.fps) <= f.duration - 0.05 and (kind != "hit" or m.get("peak") is not None
                                                                               or k == len(entries) - 1):
                    pick = (mid, m, f)
                    used = (used + k + 1) % len(entries)
                    break
            if pick is None:  # nothing long enough: shorten the shot to what the longest clip allows
                mid, m, f = max(entries, key=lambda e: e[2].duration)
                pick = (mid, m, f)
            mid, m, f = pick
            key = mid.replace("#", "_")
            if key not in {p.key for p in pool}:
                pool.append(PoolMoment(key=key, clip=f.short, start=float(m["start"]), end=float(m["end"]),
                                       peak=m.get("peak")))
            plan.append(PlanShot(kind=kind, beats=n, key=key, count=count,
                                 transition="dissolve" if kind == "hold" and plan else "cut",
                                 transition_beats=1.0 if kind == "hold" and plan else 0.0))
        # a key used twice (short footage): give the repeats their own copies so the plan stays valid
        seen_keys = {}
        for i, s in enumerate(plan):
            seen_keys[s.key] = seen_keys.get(s.key, 0) + 1
            if seen_keys[s.key] > 1:
                base = next(p for p in pool if p.key == s.key)
                new_key = f"{s.key}_{seen_keys[s.key]}"
                pool.append(base.model_copy(update={"key": new_key}))
                plan[i] = s.model_copy(update={"key": new_key})
        return DesignPlan(name=pitch.title, idea=pitch.logline, pool=pool, plan=plan,
                          notes="Laid out by rule (no AI): holds to open, cuts in the build, a hit on the drop.")

    def script_voice(self, utterances: list[dict], duration: float, mode: str, intent: str, rules: list[dict],
                     previous: VoiceScript | None = None, feedback: str = "", style: str = "labels",
                     look: Direction | None = None) -> VoiceScript:
        from ...explainer.script import local_script

        self._free("script")
        sc = local_script(utterances, duration, mode, (intent.strip()[:40] or "Your story"), designed=style == "designed")
        if look is None:
            return sc
        cams = ["push", "left", "rise", "right", "pull"]
        scenes = []
        for k, s in enumerate(sc.scenes):  # a new place every other scene; the one between stays in it
            place = f"{look.world}, the part about {s.headline.lower()}" if k % 2 == 0 or not scenes else scenes[-1].backdrop
            scenes.append(s.model_copy(update={"backdrop": place, "camera": cams[k % len(cams)]}))
        return sc.model_copy(update={"scenes": scenes, "palette": look.palette, "stage": look.world})

    def directions(self, utterances: list[dict], duration: float, mode: str, intent: str, rules: list[dict],
                   shape: str, n: int = 3, rejected=None, reason: str = "", own_idea: str = "", base=None,
                   change: str = "", taste: str = "") -> list[Direction]:
        from ...explainer.script import keywords

        self._free("looks")
        topic = " ".join(keywords(" ".join(u["text"] for u in utterances), 3)).lower() or "the idea"
        pool = [Direction(title="Night observatory", pitch="Calm and cinematic, like a documentary at night.",
                          world=f"a vast observatory at night whose screens and instruments are about {topic}",
                          art_style="painterly digital illustration, soft glow, gentle film grain",
                          colours=["#0b1026", "#1d2a5c", "#5b7cfa", "#ffd36e"], palette="midnight",
                          camera="slow push-ins", music="soft ambient pads",
                          preview=f"a wide view of a night observatory full of glowing instruments about {topic}, "
                                  "one warm lamp, deep blue shadows, stars through a high window"),
                Direction(title="Paper workshop", pitch="Bright and playful, made by hand.",
                          world=f"a sunny craft table where paper models of {topic} stand up",
                          art_style="layered paper cut-out with soft shadows and visible paper texture",
                          colours=["#2b2b3a", "#f2e6d0", "#ff6b4a", "#2f6fed"], palette="paper",
                          camera="sideways drifts", music="light plucky beat",
                          preview=f"paper cut-out models about {topic} standing on a sunny craft table, soft "
                                  "shadows, warm morning light, layered depth"),
                Direction(title="Neon grid city", pitch="Fast and bold, a little futuristic.",
                          world=f"a neon city of light lines at dusk, its towers shaped like {topic}",
                          art_style="clean neon line art on deep colour, bloom, long reflections",
                          colours=["#12051f", "#3a0f5c", "#ff2e7e", "#2ee6d6"], palette="berry",
                          camera="rising moves", music="pulsing synth",
                          preview=f"a dusk skyline of neon line towers shaped like {topic}, reflections on a wet "
                                  "street, magenta and cyan light")]
        if base is not None:
            return [base.model_copy(update={"title": (base.title + " (changed)")[:60], "pitch": change[:200] or base.pitch})]
        if own_idea:
            pool.insert(0, pool[0].model_copy(update={"title": "Your idea", "pitch": own_idea[:200],
                                                      "world": own_idea[:300], "preview": own_idea[:300]}))
        taken = {d.title for d in rejected or []}
        return ([d for d in pool if d.title not in taken] + pool)[:n]

    def rethink_visuals(self, needs: list[dict]) -> list[dict]:
        self._free("rethink")
        return [{"visual": n["visual"], "topics": [w for w in n["visual"].lower().split() if len(w) > 3][-2:]} for n in needs]

    def pick_visuals(self, needs: list[dict]) -> list[int]:
        self._free("visuals")
        return [0 if n["n"] else -1 for n in needs]

    LOCAL_CHECKS = (("sync", "Cuts land on the music's hits", "must", SYNC_WORDS),
                    ("no_people", "Nobody in shot", "never", NO_PEOPLE_WORDS),
                    ("no_repeats", "No place shown twice", "never", NO_REPEAT_WORDS))

    def editor_reply(self, text: str, words: str, facts: dict, check) -> EditorReply:
        """Without the AI: their words that switch a check on become items, and the plainest asks become the offer
        of the step they name (a story's number, 'make the film', 'swap 2 and 3')."""
        self._free("editor")
        on = {it["check"] for it in facts["items"] if it.get("on") and it.get("check")}
        add = [SpecAdd(text=label, part=part, quote=words[:200], check=name)
               for name, label, part, pattern in self.LOCAL_CHECKS if name not in on and pattern.search(words)]
        acts, w = set(facts["actions"]), words.lower()
        offer = None
        n = re.search(r"\b(?:story|idea|look|number|option|pick)\s*#?\s*(\d)\b", w)
        if "pick" in acts and n and 1 <= int(n.group(1)) <= facts["pitches"]:
            offer = EditorOffer(action="pick", why=f"A sample of idea {n.group(1)}", pitch=int(n.group(1)))
        elif "change" in acts and re.search(r"\b(swap|remove|cut out|longer|shorter|replace|move|faster|slower)\b", w):
            offer = EditorOffer(action="change", why="Your changes, read into exact edits first", text=words)
        elif "sample_ok" in acts and re.search(r"\b(looks? (?:good|right|great)|make (?:it|the film)|go ahead)\b", w):
            offer = EditorOffer(action="sample_ok", why="The whole film from this sample")
        elif "confirm_change" in acts and re.search(r"\b(yes|do it|make them|go ahead)\b", w):
            offer = EditorOffer(action="confirm_change", why="The changes as read")
        elif "confirm_understood" in acts and re.search(r"\b(yes|right|correct|spot on|looks good)\b", w):
            offer = EditorOffer(action="confirm_understood", why="What I understood is right: next step")
        elif "start" in acts and re.search(r"\b(start|go|begin|ready)\b", w):
            offer = EditorOffer(action="start", why="I watch your clips and say back what I see")
        said = [a.text.lower() for a in add]
        say = ("Got it: " + "; ".join(said) + ".") if said else "Noted."
        if offer is not None:
            why = offer.why if offer.why.startswith("I ") else offer.why[0].lower() + offer.why[1:]
            say += f" Tap the card when you're ready: {why}."
        return EditorReply(say=say, add=add, offer=offer)

    def learn(self, rules: list[dict], events: list[str]) -> ProfileUpdate:
        self._free("learn")
        text = " ".join(events).lower()
        add = []
        have = " ".join(r["text"].lower() for r in rules)
        if re.search(r"too fast|rushed|hectic", text) and "calm" not in have:
            add.append(StyleRule(text="Keep the pace calm: shots of 2 s or longer outside the drop.", why="said too fast"))
        if re.search(r"too slow|boring|dragg", text) and "faster" not in have:
            add.append(StyleRule(text="Keep it moving: shorter shots, a faster build.", why="said too slow"))
        if re.search(r"wrong music|music", text) and "music" not in have:
            add.append(StyleRule(text="Let the music lead: cut on its phrases.", why="music feedback"))
        return ProfileUpdate(add=add[:3], drop=[], summary="Noted." if add else "Nothing new to note.")


def brief_text(path: Path) -> str:
    """The clip brief without its last section (instructions for Claude Code agents, not for the API)."""
    text = Path(path).read_text(encoding="utf-8")
    return text.split("\n## Your output", 1)[0].strip()


def ceil_bar(beats: float) -> int:
    return int(math.ceil(beats / 4.0)) * 4
