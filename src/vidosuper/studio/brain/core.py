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
from dataclasses import dataclass
from pathlib import Path

from ...design import Design, Moment as PoolEntry, Shot
from ...music.analysis import MusicAnalysis
from ...schemas import ClipAnalysis, ClipInfo, ClipSignals
from ..settings import Models
from .backends import BrainError, Request, SpendFn, Usage, structured
from .context import REUSE_WORDS, BeatMap, ClipFacts, catalog_text, craft_rules, moment_by_id, prompt, rules_text
from ...explainer.script import VoiceScript
from .schemas import DesignPlan, Pitches, PlanShot, PoolMoment, ProfileUpdate, StoryBeat, StoryPitch, StyleRule

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

    def reuse_if_asked(self, *texts: str) -> None:
        if any(REUSE_WORDS.search(t or "") for t in texts):
            self.allow_reuse = True

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
            *(["# Reusing footage\nThe footage is thin or the person said repeats are fine: you may use a moment more than "
               "once, but each time it must look different (another part of the moment, another swing or take, a different "
               "speed), with at least two other shots between, so the repeat is never obvious. Never zoom in to hide a "
               "repeat: the viewer must see the whole action."]
              if self.allow_reuse else []),
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
        for short, f in self.facts.items():
            if f.impacts:
                lines.append(f"- {short}: " + ", ".join(f"{t:.2f}" for t in f.impacts) + " s")
        if not lines:
            return []
        return ["# Real hits in the footage (found from the clips' own sound, to the millisecond)\n" + "\n".join(lines) +
                "\nThese are the heroes: a bat meeting a ball, a clap, a slam, a splash. Give every hit its own shot, "
                "once (set `peak` on the pool entry to the hit's time): the tool lands each exactly on a beat and keeps its "
                "real sound. Spread them through the video and put the loudest one on the drop. Fill the beats between the "
                "hits with the clip's other action, kept short. Frame wide enough to see the whole action."]


# ---------------------------------------------------------------- checks shared by both brains

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


def check_plan(plan: DesignPlan, ctx: StoryContext) -> list[str]:
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
        bad = [(a, b) for a, b in f.avoid if min(b, m.end) - max(a, m.start) > 0.25]
        if bad:
            problems.append(f"pool '{m.key}': {m.clip} {m.start}-{m.end} overlaps a range to avoid "
                            f"({', '.join(f'{a:.1f}-{b:.1f}' for a, b in bad)})")
        logged = max((min(x["end"], m.end) - max(x["start"], m.start) for x in f.moments), default=0.0)
        if logged < 0.5 * (m.end - m.start):
            problems.append(f"pool '{m.key}': {m.clip} {m.start}-{m.end} is not inside a logged moment of that clip")
    missing = sorted({s.key for s in plan.plan} - set(pool))
    if missing:
        problems.append(f"the plan uses keys that are not in the pool: {', '.join(missing)}")
    total = plan_beats(plan)
    if abs(total - beats.target) > 1e-6:
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
    last = len(plan.plan) - 1
    if plan.plan:  # pacing a short video needs: a quick hook and no stalls
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
    hits = [(short, t) for short, f in facts.items() for t in f.impacts]
    if hits and plan.plan:  # real hits are the heroes: don't leave them out for repeats
        used = [(short, t) for short, t in hits if any(m.clip == short and m.start - 0.05 <= t <= m.end + 0.05 for m in plan.pool)]
        need = math.ceil(0.8 * min(len(hits), len(plan.plan)))
        if len(used) < need:
            left = [f"{short} at {t:.2f}" for short, t in hits if (short, t) not in used][:6]
            problems.append(f"{len(hits)} real hits were found in the footage but the plan uses only {len(used)}: give "
                            f"each hit its own shot (pool window around it, `peak` = its time). Unused: {', '.join(left)}")
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
                          flip=bool(s.flip), crack_db=s.crack_db))
    return Design(name=plan.name, song=song_path, look=look, stabilize=True, intro=None, sound_design=True,
                  fade_in=0.0 if ctx.pace in ("fast", "every") else 0.5,
                  vertical=vertical, pool={k: PoolEntry(clip=m.clip, start=m.start, end=m.end, peak=m.peak)
                                           for k, m in pool.items()}, plan=shots)


# ---------------------------------------------------------------- the Claude brain

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

    def _ask(self, req: Request, check=None, tag: str = ""):
        def log(name, data):
            self._log(f"{tag or req.task}-{name.split('-')[-1]}", data)

        return structured(self.backend, req, self.spend, check, log=log)

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

    # -- 2 pitch
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
                      max_tokens=12000)
        return self._ask(req, lambda p: check_plan(p, ctx))

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

    # -- 6 the script of a voice (explainer videos)
    def script_voice(self, utterances: list[dict], duration: float, mode: str, intent: str, rules: list[dict],
                     previous: VoiceScript | None = None, feedback: str = "") -> VoiceScript:
        lines = [f"[{u['start']:6.1f}-{u['end']:6.1f}] {u['text']}" for u in utterances]
        parts = [f"# Mode\n{mode}; the audio is {duration:.1f} seconds long.",
                 "# What the person asked for\n" + (intent.strip() or "(nothing specific: do what suits the talk)"),
                 "# This person's style rules\n" + rules_text(rules),
                 "# The transcript (seconds into the audio)\n" + "\n".join(lines)]
        if previous is not None:
            parts.append("# The current script (the video they watched)\n" + json.dumps(previous.model_dump(), indent=1) +
                         "\n\n# What they want changed\n" + feedback +
                         "\n\nChange what they asked for and keep what works.")
        parts.append("Return the script as JSON.")
        req = Request("script", prompt("voice"), "\n\n".join(parts), VoiceScript, self.models.editor, effort="medium",
                      max_tokens=8000)

        def check(sc: VoiceScript) -> list[str]:
            from ...explainer.script import fit_script

            problems = []
            fitted = fit_script(sc, duration, mode)
            if mode == "voice" and len(fitted.scenes) < max(2, int(duration // 14)):
                problems.append(f"only {len(fitted.scenes)} usable scenes for {duration:.0f} s: make one scene per point, "
                                "4-10 seconds each, inside the audio, not overlapping")
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

        return self._ask(req, check)

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
                     previous: VoiceScript | None = None, feedback: str = "") -> VoiceScript:
        from ...explainer.script import local_script

        self._free("script")
        return local_script(utterances, duration, mode, (intent.strip()[:40] or "Your story"))

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
