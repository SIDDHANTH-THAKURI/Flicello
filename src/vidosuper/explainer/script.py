"""What a voice means, scene by scene: the script that the animations are drawn from.

A script is made from the transcript (words with their times) by the AI brain, or by `local_script` when no AI is
available. Either way `fit_script` repairs it against the real audio (times inside it, scenes in order, nothing
overlapping, cutaways kept short), so a script the model got slightly wrong still renders and the person never sees an
error.

Two modes share it:
- "voice": only a recording. Scenes fill the whole time; each is a full-screen animated layout.
- "talk": a video of someone talking. Their picture and sound run untouched; `callouts` pop over it at the right
  moments and a few short `scenes` cut away to an animated layout while the voice carries on.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

Layout = Literal["title", "keyword", "list", "steps", "stat", "compare", "quote", "flow", "chat"]
PALETTES = ("midnight", "sunrise", "mint", "paper", "berry")
CUTAWAY_SHARE = 0.34  # of a talking video's length, at most, may be animation instead of the person
MIN_SCENE = 1.2
STOP = set("""a an and are as at be been but by can could did do does doing for from get got had has have he her here him his
how i if in into is it its just like me more most my no not of on one or our out over so some such than that the their them
then there these they this those to too up us very was we were what when where which who why will with would you your
actually basically really kind sort lot thing things going gonna want need also about because""".split())

# a few hundred words people say often, to a picture that says it: the no-AI fallback and a safety net for the AI
EMOJI = {
    "money": "💰", "cash": "💵", "price": "🏷️", "cost": "💸", "pay": "💳", "buy": "🛒", "sell": "🏪", "business": "💼",
    "work": "💼", "job": "💼", "idea": "💡", "think": "🤔", "brain": "🧠", "learn": "📚", "book": "📖", "school": "🎓",
    "time": "⏰", "clock": "⏰", "fast": "⚡", "quick": "⚡", "slow": "🐢", "growth": "📈", "grow": "📈", "rise": "📈",
    "fall": "📉", "drop": "📉", "love": "❤️", "heart": "❤️", "friend": "🤝", "friends": "🤝", "team": "👥",
    "people": "👥", "family": "👨‍👩‍👧", "home": "🏠", "house": "🏠", "city": "🏙️", "world": "🌍", "travel": "✈️",
    "trip": "🧳", "food": "🍽️", "eat": "🍽️", "coffee": "☕", "health": "🩺", "sleep": "😴", "run": "🏃", "fit": "💪",
    "strong": "💪", "phone": "📱", "app": "📱", "computer": "💻", "laptop": "💻", "internet": "🌐", "online": "🌐",
    "video": "🎬", "movie": "🎬", "film": "🎞️", "camera": "📷", "photo": "📷", "music": "🎵", "song": "🎵",
    "sound": "🔊", "audio": "🎧", "voice": "🎙️", "podcast": "🎙️", "listen": "👂", "email": "📧", "message": "💬",
    "talk": "💬", "story": "📖", "horror": "👻", "scary": "😱", "terrify": "😱", "fear": "😨", "ghost": "👻",
    "dark": "🌑", "night": "🌙", "light": "☀️", "sun": "☀️", "star": "⭐", "stars": "⭐", "rating": "⭐", "rate": "⭐",
    "win": "🏆", "winner": "🏆", "game": "🎮", "play": "▶️", "goal": "🎯", "target": "🎯", "plan": "🗺️", "map": "🗺️",
    "secret": "🤫", "lock": "🔒", "safe": "🛡️", "danger": "⚠️", "warning": "⚠️", "question": "❓", "answer": "✅",
    "yes": "✅", "no": "❌", "wrong": "❌", "right": "✅", "start": "🚀", "launch": "🚀", "new": "✨", "magic": "✨",
    "fire": "🔥", "hot": "🔥", "cold": "❄️", "water": "💧", "tree": "🌳", "nature": "🌿", "animal": "🐾", "dog": "🐶",
    "cat": "🐱", "car": "🚗", "build": "🛠️", "fix": "🔧", "tool": "🛠️", "code": "👨‍💻", "data": "📊", "chart": "📊",
    "number": "🔢", "science": "🔬", "space": "🪐", "power": "🔋", "energy": "⚡", "search": "🔍", "find": "🔍",
    "review": "📝", "write": "✍️", "news": "📰", "free": "🆓", "gift": "🎁", "party": "🎉", "smile": "😊", "happy": "😊",
    "sad": "😢", "angry": "😠", "surprise": "😲", "shock": "😲", "crazy": "🤯", "mind": "🤯", "eye": "👀", "watch": "👀",
    "see": "👀", "look": "👀", "hand": "✋", "stop": "🛑", "mystery": "🕵️", "anonymous": "🕵️", "clip": "📎",
    "clips": "🎞️", "audience": "👥", "director": "🎬", "genre": "🏷️", "concept": "💡", "climax": "🎭", "ending": "🏁",
}
FALLBACK_EMOJI = ("✨", "💡", "🎯", "📌", "🔔")
NUMBER = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:out of|/)\s*(\d+)\b|\b(\d+(?:\.\d+)?)\s*(%|percent|k|million|billion|x)\b", re.I)


class Item(BaseModel):
    at: float = Field(..., description="Seconds into the AUDIO when this element appears (when its word is spoken).")
    text: str = Field("", description="1-4 words shown with it. Real words from what is said, never filler.")
    emoji: str = Field("", description="One emoji that pictures it.")


class Scene(BaseModel):
    start: float = Field(..., description="Seconds into the audio.")
    end: float
    layout: Layout
    headline: str = Field("", description="The point of the scene in 2-6 words (title/keyword/quote: the main text).")
    emoji: str = Field("", description="One emoji for the scene's hero picture.")
    items: list[Item] = Field(default_factory=list, description="list/steps: 2-4 items; compare: exactly 2; others: empty.")
    value: str = Field("", description="stat: the number as spoken, e.g. '7.5/10', '40%', '3x'.")
    palette: str = Field("", description="midnight, sunrise, mint, paper or berry; empty = the script's.")
    visual: str = Field("", description="The designer animation that would show this scene best, in a few words "
                                        "('two people podcasting at a table'). Empty = the emoji is enough.")
    topics: list[str] = Field(default_factory=list, description="1-3 simple words to search an animation library for "
                                                                "it: 'podcast', 'microphone'.")
    lottie: str = Field("", description="Leave empty: filled in by the tool.")


class Callout(BaseModel):
    at: float = Field(..., description="Seconds into the video when it pops up (as the topic is spoken).")
    until: float = Field(..., description="Seconds when it goes (2-4 s after `at`; a `scene` may stay up to 6 s).")
    kind: Literal["pill", "stat", "title", "scene", "stack"] = Field("pill", description=(
        "scene: a designer animation of what is being said, big, behind the person (one at a time); "
        "stack: `count` small copies of an animation flying out of the current scene (10 audio clips, 3 reasons); "
        "pill: emoji + 1-4 words; stat: a number; title: a name, typed on."))
    text: str = ""
    emoji: str = ""
    value: str = ""
    visual: str = Field("", description="scene/stack: the animation that shows it, in a few words.")
    topics: list[str] = Field(default_factory=list, description="scene/stack: 1-3 simple words to search an animation "
                                                                "library for it.")
    count: int = Field(0, description="stack: how many.")
    punch: bool = Field(False, description="A quick zoom on the person as it appears (for the strongest moment only).")
    lottie: str = Field("", description="Leave empty: filled in by the tool.")


class Respell(BaseModel):
    heard: str = Field(..., description="A word as the transcript has it ('Jeff').")
    written: str = Field(..., description="What the person really said ('JEPA').")


class VoiceScript(BaseModel):
    name: str
    idea: str = Field("", description="The animation's idea in one line.")
    palette: str = "midnight"
    captions: bool = Field(True, description="Word-by-word captions along the bottom.")
    style: Literal["designed", "labels"] = Field("designed", description="Leave as given.")
    ambient: str = Field("", description="talk: a looping animation behind the person's head the whole time, in a few "
                                         "words ('a circular sound visualizer'); empty for none.")
    ambient_topics: list[str] = Field(default_factory=list, description="1-2 words to search the library for it.")
    ambient_lottie: str = Field("", description="Leave empty: filled in by the tool.")
    scenes: list[Scene] = Field(default_factory=list)
    callouts: list[Callout] = Field(default_factory=list)
    respell: list[Respell] = Field(default_factory=list, description="Names the transcript misheard, so the captions "
                                   "say them right: one entry per spelling heard ('Jeff' -> 'JEPA', 'Jeb' -> 'JEPA').")
    notes: str = ""


def _bare(text: str) -> str:
    return re.sub(r"(?:'s|’s)$", "", re.sub(r"^\W+|\W+$", "", text.strip()).lower())


def respell_words(words: list[dict], fixes: list[Respell]) -> list[dict]:
    """The words with misheard names put right (keeping their punctuation and a possessive 's), for the captions."""
    table = {_bare(f.heard): f.written.strip() for f in fixes if _bare(f.heard) and f.written.strip()}
    if not table:
        return words
    out = []
    for w in words:
        text = w["text"]
        key = _bare(text)
        if key in table:
            m = re.match(r"^(\W*)(.*?)((?:'s|’s)?\W*)$", text.strip())
            text = (m.group(1) + table[key] + m.group(3)) if m else table[key]
            w = {**w, "text": text}
        out.append(w)
    return out


def _desc(s: Scene) -> str:
    items = ", ".join(i.text for i in s.items if i.text)
    return f"{s.layout} '{s.headline}'" + (f" ({items})" if items else "") + (f" {s.value}" if s.value else "")


def _clock(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def changes_between(old: VoiceScript, new: VoiceScript) -> tuple[float | None, list[str]]:
    """What a revision changed, in words the person can check: (where the first change is, lines). Scenes are matched
    by where they start."""
    lines: list[str] = []
    first: float | None = None
    olds = {round(s.start): s for s in old.scenes}
    for s in new.scenes:
        before = olds.get(round(s.start))
        if before is None or _desc(before) != _desc(s) or before.emoji != s.emoji:
            lines.append(f"{_clock(s.start)}: " + (f"{_desc(before)} became {_desc(s)}" if before else f"new {_desc(s)}"))
            first = s.start if first is None else min(first, s.start)
    gone = [s for s in old.scenes if round(s.start) not in {round(x.start) for x in new.scenes}]
    for s in gone:
        lines.append(f"{_clock(s.start)}: removed {_desc(s)}")
        first = s.start if first is None else min(first, s.start)
    oc, nc = [(round(c.at), c.text) for c in old.callouts], [(round(c.at), c.text) for c in new.callouts]
    added = [c for c in new.callouts if (round(c.at), c.text) not in oc]
    removed = [c for c in old.callouts if (round(c.at), c.text) not in nc]
    for c in added[:4]:
        lines.append(f"{_clock(c.at)}: added the callout '{c.text or c.value}'")
        first = c.at if first is None else min(first, c.at)
    if removed:
        lines.append(f"{len(removed)} callout{'s' if len(removed) != 1 else ''} removed")
    if old.palette != new.palette:
        lines.append(f"colours: {old.palette} became {new.palette}")
    if old.captions != new.captions:
        lines.append("captions " + ("on" if new.captions else "off"))
    return first, lines[:8]


def valid_emoji(text: str) -> bool:
    """A string the emoji font can draw as one colour picture (so a made-up one never shows as an empty box)."""
    from .draw import emoji_sprite

    return bool(text) and emoji_sprite(text, 48) is not None


def keywords(text: str, limit: int = 4) -> list[str]:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", text) if w.lower() not in STOP]
    seen, out = set(), []
    for w in sorted(words, key=lambda x: -len(x)):
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out[:limit]


def pick_emoji(text: str, k: int = 0) -> str:
    for w in re.findall(r"[a-z]+", text.lower()):
        for form in (w, w.rstrip("s"), w[:-3] if w.endswith("ing") else w, w[:-2] if w.endswith("ed") else w):
            if form in EMOJI:
                return EMOJI[form]
    return FALLBACK_EMOJI[k % len(FALLBACK_EMOJI)]


def _words(utterances: list[dict]) -> list[dict]:
    return [w for u in utterances for w in u.get("words", [])]


def _at(words: list[dict], key: str, after: float, before: float) -> float:
    for w in words:
        if after <= w["start"] < before and re.sub(r"[^a-z]", "", w["text"].lower()) == key.lower():
            return float(w["start"])
    return after


def local_script(utterances: list[dict], duration: float, mode: str = "voice", name: str = "Your story",
                 designed: bool = False) -> VoiceScript:
    """A usable script without AI: sentences grouped into scenes, keywords and emoji from what is said."""
    words = _words(utterances)
    scenes: list[Scene] = []
    callouts: list[Callout] = []
    chunk: list[dict] = []
    groups: list[list[dict]] = []
    for u in utterances:
        chunk.append(u)
        if chunk[-1]["end"] - chunk[0]["start"] >= 4.5:
            groups.append(chunk)
            chunk = []
    if chunk:
        groups.append(chunk)
    layouts = ["keyword", "list", "stat", "keyword", "steps"]
    for i, g in enumerate(groups):
        text = " ".join(u["text"] for u in g)
        a, b = float(g[0]["start"]), float(g[-1]["end"])
        keys = keywords(text, 4)
        number = NUMBER.search(text)
        if i == 0:
            layout = "title"
        elif number:
            layout = "stat"
        else:
            layout = layouts[i % len(layouts)] if len(keys) >= 3 or layouts[i % len(layouts)] == "keyword" else "keyword"
        items = [Item(at=_at(words, k, a, b), text=k.title(), emoji=pick_emoji(k, j)) for j, k in enumerate(keys[:4])]
        value = ""
        if number:
            value = f"{number.group(1)}/{number.group(2)}" if number.group(1) else f"{number.group(3)}{number.group(4)}"
        scenes.append(Scene(start=a, end=b, layout=layout, headline=(keys[0].title() if keys else "…"),
                            emoji=pick_emoji(text, i), items=items if layout in ("list", "steps") else [], value=value))
        if designed and keys:
            scenes[-1] = scenes[-1].model_copy(update={"visual": keys[0].lower(), "topics": [keys[0].lower()]})
        if mode == "talk" and keys and designed:
            t0 = _at(words, keys[0], a, b)
            callouts.append(Callout(at=t0, until=min(duration, t0 + 4.0), kind="scene", text=keys[0].title(),
                                    emoji=pick_emoji(text, i), visual=keys[0].lower(), topics=[keys[0].lower()]))
        elif mode == "talk" and keys:
            callouts.append(Callout(at=_at(words, keys[0], a, b), until=min(duration, _at(words, keys[0], a, b) + 3.2),
                                    kind="stat" if number else "pill", text=keys[0].title(), emoji=pick_emoji(text, i),
                                    value=scenes[-1].value))
    if mode == "talk" and designed:
        scenes = []  # the person stays on screen; the animations go behind them
    return fit_script(VoiceScript(name=name, idea="", scenes=scenes, callouts=callouts,
                                  style="designed" if designed else "labels"), duration, mode)


def fit_script(script: VoiceScript, duration: float, mode: str) -> VoiceScript:
    """Repair a script against the real audio so it always renders: times clamped and ordered, scenes that overlap
    trimmed, voice mode covered end to end, talk mode with limited cutaways and callouts that never sit on one."""
    pal = script.palette if script.palette in PALETTES else "midnight"
    scenes = []
    for s in sorted(script.scenes, key=lambda s: s.start):
        a, b = max(0.0, min(s.start, duration)), max(0.0, min(s.end, duration))
        if scenes and a < scenes[-1].end:
            a = scenes[-1].end
        if b - a < MIN_SCENE and mode == "talk":
            continue
        if b - a < 0.4:
            continue
        items = [i.model_copy(update={"at": min(max(i.at, a), max(a, b - 0.3)),
                                      "emoji": i.emoji if valid_emoji(i.emoji) else ""}) for i in s.items][:4]
        scenes.append(s.model_copy(update={"start": a, "end": b, "items": items,
                                           "emoji": s.emoji if valid_emoji(s.emoji) else pick_emoji(s.headline),
                                           "palette": s.palette if s.palette in PALETTES else ""}))
    if mode == "voice":
        if not scenes:
            scenes = [Scene(start=0.0, end=duration, layout="keyword", headline=script.name, emoji="🎙️")]
        scenes[0] = scenes[0].model_copy(update={"start": 0.0})
        for k in range(len(scenes) - 1):  # no gaps: a scene holds until the next begins
            scenes[k] = scenes[k].model_copy(update={"end": scenes[k + 1].start})
        scenes[-1] = scenes[-1].model_copy(update={"end": duration})
    else:
        budget, kept = CUTAWAY_SHARE * duration, []
        for s in scenes:
            if s.start < 2.0 or s.end > duration - 1.0:
                continue
            span = min(s.end - s.start, 4.5)
            s = s.model_copy(update={"end": s.start + span})
            if sum(x.end - x.start for x in kept) + span <= budget:
                kept.append(s)
        scenes = kept
    callouts: list[Callout] = []
    ordered = sorted(script.callouts, key=lambda c: c.at)
    visual = [c for c in ordered if c.kind == "scene"]
    for c in ordered:
        a = max(0.0, min(c.at, duration - 1.0))
        if mode == "talk" and any(s.start - 0.2 <= a <= s.end for s in scenes):
            continue
        if c.kind == "scene":  # an animation holds until the next one starts (6 s at most, 1.5 s at least)
            nxt = next((x.at for x in visual if x.at > c.at + 0.05), duration)
            b = max(a + 1.5, min(c.until if c.until > a + 1.5 else a + 6.0, nxt - 0.05, a + 6.0, duration))
            if any(x.kind == "scene" and a < x.until - 0.05 for x in callouts):
                continue
        else:
            b = max(a + 1.2, min(c.until, a + 4.5, duration))
            if any(x.kind not in ("scene",) and a < x.until + 0.3 and x.kind != "stack" for x in callouts) and c.kind != "stack":
                continue
        count = max(0, min(c.count, 12)) if c.kind == "stack" else 0
        callouts.append(c.model_copy(update={"at": a, "until": b, "count": count,
                                             "emoji": c.emoji if valid_emoji(c.emoji) else ""}))
    for k, c in enumerate(callouts):  # a scene stays up while a stack flies out of it
        if c.kind == "scene":
            kids = [x for x in callouts if x.kind == "stack" and c.at <= x.at <= c.until + 0.6]
            nxt = next((x.at for x in callouts if x.kind == "scene" and x.at > c.at + 0.05), duration)
            if kids:
                callouts[k] = c.model_copy(update={"until": min(max(c.until, *(x.until for x in kids)), nxt - 0.05)})
    return script.model_copy(update={"palette": pal, "scenes": scenes, "callouts": callouts if mode == "talk" else []})
