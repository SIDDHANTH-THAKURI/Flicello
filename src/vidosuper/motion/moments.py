"""The moments of a talking (or voice) film, worked out from what is said, before anything is designed.

Claude reads the whole talk, word by word with its times, and what they asked for, and writes each moment that should
be seen as a contract: what is on screen as it opens (BEFORE), what changes on which spoken word and over how long
(CHANGES), and how it ends (AFTER), plus where it sits against the person and the exact words allowed on screen (none
unless they asked for words). The contract is checked against the transcript (every change on a word they really say,
at its time) and then handed, one moment at a time, to the scene designer (scenes.py), whose frames are judged against
it at the start, through each change and at the end.

Nothing here is a template: there are no layouts to fill. A moment is described in their own terms ("a blue circle;
at 'turning' it turns orange over a second; then it grows slowly for three seconds") and designed from zero.
"""

from __future__ import annotations

import hashlib
import json
import re

from pydantic import BaseModel, Field

WANTS_TEXT = re.compile(r"\b(text|caption\w*|subtitle\w*|title\w*|words? (on|in) (the )?screen|kinetic (type|text)|"
                        r"typograph\w*|label\w*|lower thirds?|write|written|spell\w* out|show the words?)\b", re.I)
NO_TEXT = re.compile(r"\b(no|without|don'?t (want|add|show)|never) (any )?(text|captions?|subtitles?|titles?|words?|"
                     r"labels?)\b", re.I)


class Change(BaseModel):
    word: str = Field(..., description="The word they say as it happens, exactly as in the transcript.")
    at: float = Field(..., description="Film seconds: when that word starts (from the transcript).")
    becomes: str = Field(..., description="What changes and how, concretely: the object, its colour, size, place, "
                                          "motion ('the blue circle turns orange', 'it splits into two halves that "
                                          "slide apart, left and right').")
    over: float = Field(0.6, description="Seconds the change takes (a pop 0.2, a turn 1, 'slowly' 2-4).")


class Moment(BaseModel):
    start: float = Field(..., description="Film seconds the moment starts (a beat before its first change).")
    end: float = Field(..., description="Film seconds it ends (after the last change has settled).")
    said: str = Field("", description="What they say in it, word for word.")
    before: str = Field(..., description="Exactly what is on screen as it opens: each object, its colour, size, place "
                                         "in the frame and against the person. 'nothing yet' is fine.")
    changes: list[Change] = Field(default_factory=list)
    after: str = Field(..., description="Exactly what is on screen as it ends.")
    where: str = Field("", description="Where it sits against the person (beside them at shoulder height, behind "
                                       "them as a world, in their open hand, above their head).")
    text: str = Field("", description="The exact words allowed on screen in this moment; empty = no words at all.")
    why: str = Field("", description="In a few words, why this moment is seen (they describe it / it is the point).")


class FilmDesign(BaseModel):
    look: str = Field(..., description="One look for the whole film, held by every moment: palette (3-5 hex), "
                                       "material, light, type if any. Their style if they named one.")
    idea: str = Field("", description="The film's treatment in one line.")
    engine: str = Field("2d", description="'2d': flat, illustrated, graphic (made in HyperFrames); '3d': real light, "
                                          "materials and depth (made in Blender).")
    world: str = Field("", description="The whole frame: the world that fills it, where the person stands in it and "
                                       "how big, and how the camera moves.")
    moments: list[Moment] = Field(default_factory=list)


def wants_text(ask: str) -> bool:
    """They asked for words on screen (captions, a title, labels, kinetic type), and didn't say no to them."""
    return bool(WANTS_TEXT.search(ask or "")) and not NO_TEXT.search(ask or "")


def _norm(word: str) -> str:
    return re.sub(r"[^\w']", "", (word or "").lower())


SLOW = {"slowly", "gradually", "gently", "softly", "steadily", "slow"}
FAST = {"suddenly", "instantly", "quickly", "snap", "snaps", "abruptly", "immediately"}


def _pace(words: list[tuple[float, float, str]], at: float) -> str:
    """How fast they say the change happens, from its own phrase ('slowly turning orange and growing'): the word,
    the one after it, and up to four before it within the same sentence. slow, fast or ''."""
    if not words:
        return ""
    k = min(range(len(words)), key=lambda i: abs(words[i][0] - at))
    near = [words[k][2]] + ([words[k + 1][2]] if k + 1 < len(words) and not re.search(r"[.!?]$", words[k][2]) else [])
    for i in range(k - 1, max(-1, k - 5), -1):
        if re.search(r"[.!?]$", words[i][2]):
            break
        near.append(words[i][2])
    said = {_norm(w) for w in near}
    return "slow" if said & SLOW else "fast" if said & FAST else ""


SYSTEM = """You are the director of a premium motion-design studio, planning the motion graphics of one film before \
anything is designed. You read what is said, word by word with times, and decide what the viewer SEES and when.

Rules, in order of importance:
1. When they describe something to see (an object, a shape, a colour, a number of things, a motion, a change, a \
place), that is an instruction: plan exactly that. The named objects, their colours, how many, how they move, in the \
order they say it, each change landing on the word that names it. Never swap their object for a symbol of it, never \
add a different idea in its place, never show a word instead of the thing.
2. No words on screen. No captions, labels, titles, kinetic type, numbers as text or callout cards, unless they asked \
for words on screen; then only the exact words they asked for (`text`), one line at a time. Everything else has \
`text` empty.
3. Each moment is a contract a designer will be held to frame by frame, so write it as states anyone could check in a \
still: BEFORE (what is on screen as it opens), CHANGES (on which spoken word, what becomes what, over how long), \
AFTER (how it ends). Concrete: colours by name, sizes against the frame or the person ("about a third of the frame's \
width"), places ("left of their head", "filling the wall behind them"), motion ("slowly, over about 3 seconds"). \
'Slowly' is 2-4 s, 'suddenly' 0.15-0.3 s. The main subject of a moment is BIG from the moment it appears (a \
quarter to two fifths of the frame's height), even when it grows later: it grows from big to bigger, never from a \
dot. Small, timid subjects read as amateur.
4. Every change is anchored to a word they really say: `word` exactly as in the transcript, `at` its start time. A \
moment starts 0.3-0.8 s before its first change and ends 0.4-1.0 s after its last change settles.
5. Where they talk about ideas rather than things to see: a moment only where a picture truly helps and is strong; \
otherwise leave them on screen alone. Fewer, better moments. (A voice-only film has no picture of them: there every \
second needs a picture, see the film notes.)
6. The WHOLE FRAME is designed, never things pasted on top of their untouched video (that reads as stickers). \
Decide the world (`world`): what fills the frame (a deep navy space, a paper landscape, a lit studio, a void that \
changes colour...), where the person stands in it (they are cut out of their video and placed in it: which side, how \
big; their face stays clear and comfortably big), and how the camera moves (a slow push, a drift, a new angle on a \
pause). When they describe a background, the world IS that background. The world stays for the whole film; what is \
built in it can stay and grow from moment to moment.
7. The engine (`engine`): "2d" unless they ask for 3D, realism or real materials. 2d is flat, illustrated motion \
design in the spirit of the best science-animation channels: flat shapes with two-tone shading and a crisp \
highlight, glow drawn as stepped flat rings, springy motion with overshoot, parallax layers, fine grain. 3d is \
Blender: real light, glass, metal, depth of field, motion blur. One look for the whole film (`look`), true to its \
engine and to their style if they named one. Their objects stay exactly what they said (a circle is still a circle, \
in their colour), made beautifully.

Reply with JSON only, in this form:
{"look": "...", "idea": "...", "engine": "2d", "world": "...", "moments": [{"start": 1.1, "end": 6.2, "said": "...", "before": "...", \
"changes": [{"word": "turning", "at": 2.4, "becomes": "...", "over": 1.0}], "after": "...", "where": "...", \
"text": "", "why": "..."}]}"""


def prompt(words: list[tuple[float, float, str]], duration: float, ask: str, kind: str, width: int, height: int,
           previous: FilmDesign | None = None, feedback: str = "", problems: list[str] | None = None) -> str:
    said = " ".join(f"[{a:.2f}] {w}" for a, _, w in words) or "(nothing is said)"
    notes = (f"The film: {duration:.2f} s, {width} x {height} px. "
             + ("A VOICE-ONLY film: there is no footage, only their voice; the picture is entirely yours, so the "
                "moments run end to end with no gaps (the first starts at 0, each starts where the last ends, the "
                "last ends at the film's end), each 3-20 s, each handing over to the next (the AFTER of one is the "
                "BEFORE of the next, or turns into it)." if kind == "voice" else
                "A STRETCH OF THEIR MONTAGE (their own clips, cut to music): plan what they asked for in it. Their "
                "footage stays unless they asked for a whole-frame moment. Little or nothing is said, so each change "
                "lands on a mark of the music listed below ('bar' starts a bar, 'drop' is where it lifts, 'beat' is "
                "a beat): use that mark, exactly as listed, as the change's word." if kind == "montage" else
                "Their own video of them talking: they are cut out of it and stand in your world the whole time; "
                "the moments happen in that world around them."))
    text_note = ("They asked for words on screen: put the exact words they want in `text`, and only there."
                 if wants_text(ask) else "They did NOT ask for words on screen: every `text` is empty.")
    parts = [f"What they asked for: {ask or '(nothing beyond the recording itself)'}", notes, text_note,
             ("The music's marks and anything said, each with the second it starts:\n" if kind == "montage" else
              "What is said, each word with the second it starts:\n") + said]
    if previous is not None:
        parts.append("Your plan so far:\n" + previous.model_dump_json(indent=1))
        if feedback.strip():
            parts.append("What they said about it (this wins; change what it touches, keep the rest exactly as it "
                         f"is):\n{feedback.strip()}")
    if problems:
        parts.append("Your last answer had these problems; fix every one:\n- " + "\n- ".join(problems))
    return "\n\n".join(parts)


def check(design: FilmDesign, words: list[tuple[float, float, str]], duration: float, kind: str, ask: str) -> list[str]:
    """What is wrong with the plan, said so it can be fixed: times out of the film or overlapping, changes on words
    they don't say there, words on screen nobody asked for, a voice film with gaps."""
    problems = []
    if not design.look.strip():
        problems.append("there is no look for the film")
    if not design.moments:
        problems.append("there are no moments")
    allow_text = wants_text(ask)
    last_end = 0.0
    for k, m in enumerate(design.moments, 1):
        tag = f"moment {k} ({m.start:.2f}-{m.end:.2f} s)"
        if m.start < -0.01 or m.end > duration + 0.05 or m.end - m.start < 1.2:
            problems.append(f"{tag}: it must lie inside the film (0-{duration:.2f} s) and last at least 1.2 s")
        if m.end - m.start > 25:
            problems.append(f"{tag}: longer than 25 s; split it where the picture changes")
        if m.start < last_end - 0.01:
            problems.append(f"{tag}: overlaps the moment before it (which ends at {last_end:.2f} s)")
        if kind == "voice" and abs(m.start - last_end) > 0.05:
            problems.append(f"{tag}: a voice film runs end to end, so it must start at {last_end:.2f} s")
        last_end = max(last_end, m.end)
        if not m.before.strip() or not m.after.strip():
            problems.append(f"{tag}: write both BEFORE and AFTER")
        if m.text.strip() and not allow_text:
            problems.append(f"{tag}: has words on screen ('{m.text[:40]}') but they didn't ask for any: make `text` empty")
        for c in m.changes:
            if not m.start - 0.01 <= c.at <= m.end:
                problems.append(f"{tag}: the change on '{c.word}' at {c.at:.2f} s is outside the moment")
            near = [w for a, _, w in words if abs(a - c.at) <= 0.8]
            if _norm(c.word) and not any(_norm(c.word) in _norm(w) or _norm(w) in _norm(c.word) for w in near if _norm(w)):
                problems.append(f"{tag}: '{c.word}' isn't said near {c.at:.2f} s (said there: "
                                f"{' '.join(near[:8]) or 'nothing'}); anchor it to the word as the transcript has it")
            if not 0.05 <= c.over <= 8:
                problems.append(f"{tag}: the change on '{c.word}' takes {c.over} s; give 0.05-8 s")
            elif (pace := _pace(words, c.at)) == "slow" and c.over < 1.8:
                problems.append(f"{tag}: they say it happens slowly, but the change on '{c.word}' takes {c.over:g} s: "
                                "give it 2-4 s (end the moment later if needed)")
            elif pace == "fast" and c.over > 0.6:
                problems.append(f"{tag}: they say it happens suddenly, but the change on '{c.word}' takes {c.over:g} s: "
                                "give it 0.15-0.4 s")
            elif c.at + c.over > m.end - 0.2:
                problems.append(f"{tag}: the change on '{c.word}' runs until {c.at + c.over:.2f} s, past the moment's "
                                "end: end the moment later or make the change quicker")
    if kind == "voice" and design.moments and abs(last_end - duration) > 0.3:
        problems.append(f"a voice film runs to its end: the last moment must end at {duration:.2f} s")
    return problems


def snap(design: FilmDesign, words: list[tuple[float, float, str]], duration: float) -> FilmDesign:
    """Each change on the start of its word as really said (the nearest one), each moment inside the film."""
    moments = []
    for m in design.moments:
        changes = []
        for c in m.changes:
            hits = [a for a, _, w in words if _norm(w) and (_norm(c.word) in _norm(w) or _norm(w) in _norm(c.word))
                    and abs(a - c.at) <= 0.8]
            at = min(hits, key=lambda a: abs(a - c.at)) if hits else c.at
            changes.append(c.model_copy(update={"at": round(at, 3)}))
        moments.append(m.model_copy(update={"changes": sorted(changes, key=lambda c: c.at),
                                            "start": round(max(0.0, m.start), 3),
                                            "end": round(min(duration, m.end), 3)}))
    return design.model_copy(update={"moments": moments})


def parse(text: str) -> FilmDesign | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        design = FilmDesign.model_validate(json.loads(m.group(0)))
    except ValueError:
        return None
    engine = "3d" if str(design.engine).strip().lower() in ("3d", "3-d", "blender") else "2d"
    return design.model_copy(update={"engine": engine})


def plan(words: list[tuple[float, float, str]], duration: float, ask: str, kind: str, width: int, height: int,
         previous: FilmDesign | None = None, feedback: str = "", ask_claude=None, tries: int = 3,
         log=lambda msg: None) -> FilmDesign:
    """The film's moments, from its words and their request (a revision of `previous` when `feedback` is given),
    checked against the transcript and asked again with what was wrong, up to `tries` times."""
    from . import scenes

    if ask_claude is None:
        def ask_claude(system: str, text: str) -> str:
            return scenes._ask(scenes._client(), system, [{"type": "text", "text": text}], 16000, None, "moments",
                               "high")
    problems: list[str] | None = None
    best: tuple[int, FilmDesign] | None = None
    for k in range(tries):
        design = parse(ask_claude(SYSTEM, prompt(words, duration, ask, kind, width, height, previous, feedback,
                                                 problems)))
        if design is None:
            problems = ["the reply wasn't the JSON asked for: reply with the JSON object only"]
            continue
        design = snap(design, words, duration)
        problems = check(design, words, duration, kind, ask)
        if best is None or len(problems) < best[0]:
            best = (len(problems), design)
        if not problems:
            return design
        log(f"plan {k + 1}: {len(problems)} problems; asking again")
    if best is None:
        raise ValueError("The moments couldn't be planned: the designer's replies weren't readable.")
    return _repaired(best[1], duration, kind, ask)


def _repaired(design: FilmDesign, duration: float, kind: str, ask: str) -> FilmDesign:
    """The best plan made to hold where it can be without asking again: inside the film, no overlaps, no words nobody
    asked for, a voice film end to end."""
    allow_text = wants_text(ask)
    out, last = [], 0.0
    for m in sorted(design.moments, key=lambda m: m.start):
        start = last if kind == "voice" else max(m.start, last)
        settle = max((c.at + c.over + 0.3 for c in m.changes), default=0.0)
        end = min(duration, max(m.end, start + 1.2, settle))
        if end - start < 1.2:
            continue
        out.append(m.model_copy(update={"start": round(start, 3), "end": round(end, 3),
                                        "text": m.text if allow_text else "",
                                        "changes": [c for c in m.changes if start <= c.at <= end]}))
        last = end
    if kind == "voice" and out:
        out[-1] = out[-1].model_copy(update={"end": round(duration, 3)})
    return design.model_copy(update={"moments": out})


# ---------------------------------------------------------------- one moment, handed to the designer

def brief_text(m: Moment, design: FilmDesign, ask: str, kind: str) -> str:
    """The moment as the designer reads it: the contract, in scene seconds."""
    lines = ["Design this moment EXACTLY as written below. It was worked out from their own words; follow it, don't "
             "reinterpret it. Invent everything around it (the look in detail, light, depth, secondary motion, sound), "
             "never instead of it.",
             f"BEFORE (as the scene opens, 0.00 s): {m.before}"]
    if m.changes:
        lines.append("CHANGES (each lands on its spoken word; the time is seconds into the scene):")
        for c in m.changes:
            lines.append(f"- on \"{c.word}\" at {c.at - m.start:.2f} s, over about {c.over:g} s: {c.becomes}")
    lines.append(f"AFTER (by the end, {m.end - m.start:.2f} s): {m.after}")
    if m.where:
        lines.append(f"WHERE: {m.where}")
    lines.append(f"TEXT ON SCREEN: exactly \"{m.text}\", nothing else written anywhere." if m.text.strip() else
                 "TEXT ON SCREEN: none at all. No captions, labels, titles, kinetic words, numbers or letters anywhere.")
    lines.append(f"The film's look (every moment holds it): {design.look}")
    if kind == "voice":
        lines.append("This is a voice-only film: the shot is an empty frame, there is no person, and the whole picture "
                     "is yours, edge to edge.")
    if ask:
        lines.append(f"What they asked for the whole film: {ask}")
    return "\n".join(lines)


def as_prompt(design: FilmDesign) -> str:
    """The whole plan as one prompt anyone can read, change and paste anywhere."""
    out = [f"Look ({'3D' if design.engine == '3d' else '2D'}): {design.look}"] + (
        [f"Idea: {design.idea}"] if design.idea else []) + ([f"World: {design.world}"] if design.world else [])
    for k, m in enumerate(design.moments, 1):
        out.append("")
        out.append(f"Moment {k} ({m.start:.1f}-{m.end:.1f} s)" + (f": {m.said}" if m.said else ""))
        out.append(f"  Starts: {m.before}")
        out += [f"  On \"{c.word}\" ({c.at:.1f} s, over {c.over:g} s): {c.becomes}" for c in m.changes]
        out.append(f"  Ends: {m.after}")
        if m.where:
            out.append(f"  Where: {m.where}")
        out.append(f"  Words on screen: {m.text}" if m.text.strip() else "  Words on screen: none")
    return "\n".join(out)


def checkpoints(m: Moment) -> list[dict]:
    """The moments in the scene a still must match the contract (scene seconds): the opening state, each change
    halfway and once it has landed, and the end."""
    dur = m.end - m.start
    last = round(max(0.1, dur - 0.08), 2)  # the closing state: the latest still, after everything
    first = (m.changes[0].at - m.start) if m.changes else dur / 2
    out = [{"t": round(max(0.1, min(first - 0.25, 0.4 * dur)), 2), "expect": f"BEFORE: {m.before}"}]
    for c in m.changes:
        at = c.at - m.start
        if c.over >= 0.5:
            out.append({"t": round(min(last - 0.3, at + c.over / 2), 2),
                        "expect": f"halfway through the change on \"{c.word}\": {c.becomes}"})
        landed = round(at + c.over + 0.2, 2)
        if landed < last - 0.25:  # landing right at the end: the closing state checks it
            out.append({"t": landed, "expect": f"after \"{c.word}\": {c.becomes} (done)"})
    out = [p for p in out if p["t"] < last]
    out.append({"t": last, "expect": f"AFTER: {m.after}"})
    seen, unique = set(), []
    for p in sorted(out, key=lambda p: p["t"]):
        if p["t"] not in seen:
            seen.add(p["t"])
            unique.append(p)
    if len(unique) > 10:  # the opening and closing states always, and an even spread of the rest
        inner = unique[1:-1]
        unique = [unique[0], *[inner[round(k * (len(inner) - 1) / 7)] for k in range(8)], unique[-1]]
    return unique


def digest(m: Moment, design: FilmDesign, base: str) -> str:
    """What makes a designed moment reusable: its contract, the film's look and the picture under it."""
    raw = json.dumps([m.model_dump(exclude={"why"}), design.look, base], sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]
