"""The film's spec: everything agreed for one film, as one list the person and their editor both see.

It is built from what they said (the note, how they want it cut, the brief they checked on "what I understood", their
answers to its questions), their saved style rules for this kind of video, and what they added or switched off while
talking to their editor (stored on the project as `spec`: only the changes, so the list always follows the brief it
comes from). Every item says who it comes from and the words that made it. Items the studio can check carry the
check's name, and those checks are what every AI step obeys: an item switched on turns its check on, every item asking
for a check switched off turns it off (their word wins over a rule of taste). After each sample and version every
checked item is marked met or not, with the evidence: the checklist the person sees under the film.
"""

from __future__ import annotations

import json
import re

from .brain.context import NO_PEOPLE_WORDS, NO_REPEAT_WORDS, SYNC_WORDS, logged_at, people_in, place_of

# name -> (what it means, in their words; the words that ask for it). Holds as long as they say (StoryContext.hold_ok)
# stay with their notes: a length like "a 30 second reel" reads as one, which is no item for the list.
CHECKS = {
    "sync": ("Cuts land on the music's hits", SYNC_WORDS),
    "no_people": ("Nobody in shot", NO_PEOPLE_WORDS),
    "no_repeats": ("No place shown twice", NO_REPEAT_WORDS),
}
PARTS = ("goal", "deliver", "style", "music", "must", "never")
SOURCES = ("you", "style", "editor")
MAX_ADDED = 24
SHAPE_WORDS = {"9:16": "Vertical 9:16 (Reels, Shorts, TikTok)", "16:9": "Widescreen 16:9 (YouTube)",
               "both": "Vertical 9:16 and widescreen 16:9"}
PACE_WORDS = {"calm": "Calm and cinematic", "balanced": "A mix of calm and fast", "fast": "Fast, on the beat",
              "every": "A shot on every beat", "other": "Cut your own way"}


def _json(text) -> dict:
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _clean(text, n: int = 240) -> str:
    return " ".join(str(text or "").split())[:n]


def _item(iid: str, text: str, part: str, source: str, quote: str = "", check: str | None = None) -> dict:
    return {"id": iid, "text": _clean(text), "part": part, "source": source, "quote": _clean(quote), "check": check,
            "on": True, "why": "", "switchable": part in ("must", "never") or iid.startswith("rule-") or is_added(iid)}


def _span(seconds: float) -> str:
    s = float(seconds or 0)
    if s < 90:
        return f"{s:g} s"
    return f"{s / 60:g} min"


def check_in(text: str) -> str | None:
    """The check one line of theirs asks for (beat sync, nobody in shot, no place twice), if any."""
    for name in CHECKS:
        if CHECKS[name][1].search(text or ""):
            return name
    return None


def _quote(pattern, text: str, reach: int = 40) -> str:
    """The words around what they said that asks for a check, as they wrote them."""
    m = pattern.search(text or "")
    if not m:
        return ""
    a, b = max(0, m.start() - reach), min(len(text), m.end() + reach)
    return ("…" if a else "") + text[a:b].strip() + ("…" if b < len(text) else "")


def overlay(project: dict) -> dict:
    """What they changed by talking: the items they added, and the ids of items they switched off."""
    data = _json(project.get("spec"))
    added = [a for a in data.get("added") or [] if isinstance(a, dict) and a.get("id") and a.get("text")]
    return {"added": added, "off": [str(x) for x in data.get("off") or []], "rev": int(data.get("rev") or 0)}


def _music_line(project: dict, said: dict) -> str:
    if project.get("song"):
        line = f"Your song: {project['song']}"
    elif project.get("music") == "none":
        line = "No music"
    else:
        line = "Music made for this film"
    reading = _clean(said.get("music"), 200)
    return f"{line}. {reading}" if reading else line


def film_spec(project: dict, rules: list[dict], said: list[str] | None = None) -> list[dict]:
    """Every item of the film's spec, in order: the goal, what to deliver, the style, the music, then what must be in
    it and what must not (theirs, their style's, and the words of theirs that switch a check on), then what they added
    by talking. `rules`: their style rules for this kind of video; `said`: their other words on this film (what they
    wrote on samples and versions)."""
    brief = _json(project.get("brief"))
    understood = _json(project.get("understood"))
    agreed = bool(brief)  # after they checked "what I understood", its lines are theirs; before, the editor's reading
    src = brief if agreed else understood
    who = "you" if agreed else "editor"
    items: list[dict] = []
    story = _clean(src.get("story"), 600)
    if story:
        items.append(_item("goal", story, "goal", who))
    elif (project.get("intent") or "").strip():
        items.append(_item("goal", project["intent"], "goal", "you", project["intent"]))
    if src.get("feel"):
        items.append(_item("feel", "Feel: " + ", ".join(map(str, src["feel"])), "goal", who))
    items.append(_item("deliver", f"{SHAPE_WORDS.get(project.get('shapes'), project.get('shapes'))}, about "
                       f"{_span(project.get('length') or 0)}", "deliver", "you"))
    if project.get("kind", "montage") == "montage":
        cut = _clean(project.get("cut_note"))
        pace = PACE_WORDS.get(project.get("pace") or "balanced", "A mix of calm and fast")
        items.append(_item("pace", f"{pace}: {cut}" if cut else pace, "style", "you", cut))
        items.append(_item("music", _music_line(project, src), "music", "you"))
    for k, line in enumerate(src.get("must") or []):
        items.append(_item(f"must-{k}", line, "must", who, line, check_in(line)))
    for k, line in enumerate(src.get("avoid") or []):
        items.append(_item(f"never-{k}", line, "never", who, line, check_in(line)))
    for short in brief.get("leave_out") or []:
        items.append(_item(f"out-{short}", f"Leave out clip {short}", "never", "you", check="clips_out"))
    for k, a in enumerate(brief.get("answers") or []):
        if a.get("answer"):
            items.append(_item(f"answer-{k}", a["answer"], "must", "you", a.get("ask", ""), check_in(a["answer"])))
    for r in rules:
        if r.get("id") and r.get("text"):
            rule = _item(f"rule-{r['id']}", r["text"], "style", "style", "", check_in(r["text"]))
            why = _clean(r.get("why"))
            rule["why"] = "from your first answers" if why.lower() == "your answer" else why
            items.append(rule)
    ov = overlay(project)
    added = [_item(a["id"], a["text"], a.get("part") if a.get("part") in PARTS else "must",
                   a.get("source") if a.get("source") in SOURCES else "you", a.get("quote", ""),
                   a.get("check") if a.get("check") in CHECKS or a.get("check") == "clips_out" else None)
             for a in ov["added"]]
    texts = [project.get("intent") or "", project.get("cut_note") or "", *(said or [])]
    for name in CHECKS:  # their own words that make a check a rule, unless a line of theirs already asks for it
        if any(it["check"] == name and it["source"] == "you" for it in items + added):
            continue
        hit = next((t for t in texts if CHECKS[name][1].search(t or "")), None)
        if hit is not None:
            items.append(_item(f"word-{name}", CHECKS[name][0], "never" if name in ("no_people", "no_repeats") else "must",
                               "you", _quote(CHECKS[name][1], hit), name))
    items += added
    off = set(ov["off"])
    for it in items:
        it["on"] = it["id"] not in off
    return items


def flags(items: list[dict]) -> dict[str, bool]:
    """The checks for this film: on when an item asking for it is on, off when every item asking for it was switched
    off. A check nothing asks for is left out (it stays as their words set it)."""
    out: dict[str, bool] = {}
    for name in CHECKS:  # the editor's own reading of their words counts once they confirmed it (then it is theirs)
        mine = [it for it in items if it.get("check") == name and it.get("source") != "editor"]
        if mine:
            out[name] = any(it["on"] for it in mine)
    return out


def apply(ctx, items: list[dict]) -> None:
    """Make the checks every AI step obeys the spec's: beat sync, nobody in shot, no place twice."""
    for name, on in flags(items).items():
        if hasattr(ctx, name):
            setattr(ctx, name, on)


def rule_off(items: list[dict], rule_id: str) -> bool:
    return any(it["id"] == f"rule-{rule_id}" and not it["on"] for it in items)


def off_lines(items: list[dict], prefix: str) -> set[int]:
    """Indexes of the brief's lines ('must' / 'never') they switched off: the AI no longer reads them."""
    out = set()
    for it in items:
        if not it["on"] and it["id"].startswith(prefix + "-") and it["id"].split("-", 1)[1].isdigit():
            out.add(int(it["id"].split("-", 1)[1]))
    return out


def is_added(iid: str) -> bool:
    return len(iid) > 1 and iid[0] == "a" and iid[1:].isdigit()


def ask_lines(items: list[dict]) -> list[str]:
    """For the AI: what they added while talking to their editor (it wins over the brief), and the words of theirs
    they took back."""
    lines = []
    lead = {"goal": "The film, as they now put it", "deliver": "Deliver", "style": "How to cut it", "music": "Music",
            "must": "Must have", "never": "Leave out"}
    for it in items:
        if is_added(it["id"]) and it["on"]:
            lines.append(f"{lead.get(it['part'], 'Must have')}: {it['text']}")
        elif it["id"].startswith("word-") and not it["on"]:
            lines.append(f"They took this back, it is not wanted for this film: {it['text'].lower()}")
    return lines


# ---------------------------------------------------------------- changes made by talking

def add(project: dict, text: str, part: str, check: str | None = None, quote: str = "", source: str = "you",
        items: list[dict] | None = None) -> tuple[dict, str | None]:
    """The overlay with one item added (None as the id: it is already there, or the list is full)."""
    ov = overlay(project)
    text = _clean(text)
    if not text or part not in PARTS:
        return ov, None
    have = {it["text"].lower() for it in (items or [])} | {a["text"].lower() for a in ov["added"]}
    if text.lower() in have or len(ov["added"]) >= MAX_ADDED:
        return ov, None
    if check not in CHECKS and check != "clips_out":
        check = check_in(text) if part in ("must", "never", "style") else None
    n = max([int(a["id"][1:]) for a in ov["added"] if is_added(a["id"])] + [0]) + 1
    iid = f"a{n}"
    ov["added"].append({"id": iid, "text": text, "part": part, "source": source if source in SOURCES else "you",
                        "quote": _clean(quote), "check": check})
    ov["rev"] += 1
    return ov, iid


def switch(project: dict, iid: str, on: bool) -> dict:
    ov = overlay(project)
    off = [x for x in ov["off"] if x != iid]
    if not on:
        off.append(iid)
    ov["off"] = off
    ov["rev"] += 1
    return ov


def remove(project: dict, iid: str) -> dict:
    """An item they added by talking goes; anything else can only be switched off (it comes from the brief or their
    style, which keep it)."""
    ov = overlay(project)
    ov["added"] = [a for a in ov["added"] if a["id"] != iid]
    ov["off"] = [x for x in ov["off"] if x != iid]
    ov["rev"] += 1
    return ov


# ---------------------------------------------------------------- the checklist on a sample or a version

def _span_beats(s) -> float:
    return s.beats * (s.count if s.kind == "stutter" else 1)


def _shot_moments(plan, ctx) -> list[tuple[int, dict | None]]:
    pool = {m.key: m for m in plan.pool}
    out = []
    for i, s in enumerate(plan.plan):
        m = pool.get(s.key)
        f = ctx.facts.get(m.clip) if m else None
        out.append((i + 1, logged_at(f, m.start, m.end) if f else None))
    return out


def _sync_line(plan, ctx) -> tuple[bool, str]:
    from .brain.core import _sync_problems

    marks = ctx.beats.hit_marks()
    pos, on, cuts = 0.0, 0, 0
    for i, s in enumerate(plan.plan):
        if i:
            cuts += 1
            on += any(abs(pos - m) < 0.01 for m in marks)
        pos += _span_beats(s)
    problems = _sync_problems(plan, ctx.beats, hold_ok=getattr(ctx, "hold_ok", False))
    said = sync_said(problems)
    return not problems, f"{on} of {cuts} cuts on the hits" + (f"; {said}" if said else "")


def _shots(numbers: str) -> str:
    n = [x.strip() for x in numbers.split(",") if x.strip()]
    return f"shot {n[0]}" if len(n) == 1 else "shots " + ", ".join(n[:-1]) + " and " + n[-1]


def _verb(numbers: str, verb: str) -> str:
    return verb if "," in numbers else verb + "s"


def sync_said(problems: list[str]) -> str:
    """What breaks beat sync, in a few words for the person."""
    out = []
    for p in problems:
        if m := re.search(r"but (\d+) cuts land between", p):
            out.append(f"{m.group(1)} cuts fall between the hits")
        elif m := re.search(r"shots ([\d, ]+) come in with a dissolve", p):
            out.append(f"{_shots(m.group(1))} {_verb(m.group(1), 'fade')} in instead of cutting")
        elif m := re.search(r"after the drop .*?but shots ([\d, ]+) hold longer", p):
            out.append(f"after the drop, {_shots(m.group(1))} {_verb(m.group(1), 'hold')} past the hits")
        elif m := re.search(r"shots ([\d, ]+) hold longer than", p):
            out.append(f"{_shots(m.group(1))} {_verb(m.group(1), 'hold')} too long")
    return "; ".join(out)


def _people_line(plan, ctx, cards: list[dict] | None) -> tuple[bool, str]:
    seen = [(c["n"], c.get("people", "unknown")) for c in cards] if cards else \
        [(n, people_in(lm) if lm else "unknown") for n, lm in _shot_moments(plan, ctx)]
    near = [n for n, who in seen if who in ("near", "face")]
    if not near:
        far = [n for n, who in seen if who == "far"]
        return True, "nobody close in any shot" + (f" (small far-off figures in shot {', '.join(map(str, far))})" if far else "")
    return False, f"someone is in shot {', '.join(map(str, near))}"


def _places_line(plan, ctx, cards: list[dict] | None) -> tuple[bool, str]:
    shots = [(c["n"], " ".join(str(c.get("place") or "").lower().replace("the ", " ").split()), c.get("place") or "")
             for c in cards] if cards else \
        [(n, place_of(lm) if lm else "", (lm or {}).get("place") or "") for n, lm in _shot_moments(plan, ctx)]
    last: dict[str, int] = {}
    again = []
    for n, key, name in shots:
        if not key:
            continue
        if key in last and last[key] < n - 1:
            again.append(f"{name} in shot {last[key]} and {n}")
        last[key] = n
    return (not again), ("every place once" if not again else "; ".join(again[:3]))


def _clips_line(plan, left_out: set[str]) -> tuple[bool, str]:
    used = {m.clip for m in plan.pool if any(s.key == m.key for s in plan.plan)}
    bad = sorted(used & left_out)
    return (not bad), ("none of the clips you left out" if not bad else f"uses {', '.join(bad)}")


def checklist(items: list[dict], plan, ctx, cards: list[dict] | None = None, rows: list[dict] | None = None,
              seconds: float | None = None, length: float | None = None) -> list[dict]:
    """Each check the spec asks for, met or not, with the evidence, for one sample or version; then the requirement
    counts (pace, hits, music, length). One line per check, naming the items that asked for it."""
    out = []
    left_out = {it["id"][4:] for it in items if it["on"] and it.get("check") == "clips_out"}
    for name in ("sync", "no_people", "no_repeats", "clips_out"):
        asked = [it for it in items if it["on"] and it.get("check") == name]
        if not asked:
            continue
        if name == "sync":
            ok, detail = _sync_line(plan, ctx)
        elif name == "no_people":
            ok, detail = _people_line(plan, ctx, cards)
        elif name == "no_repeats":
            ok, detail = _places_line(plan, ctx, cards)
        else:
            ok, detail = _clips_line(plan, left_out)
        label = CHECKS[name][0] if name in CHECKS else "Clips you left out stay out"
        out.append({"check": name, "text": label, "ok": ok, "detail": detail, "items": [it["id"] for it in asked]})
    if seconds and length:
        ok = abs(seconds - length) <= max(1.5, 0.1 * length)
        out.append({"check": "length", "text": f"About {_span(length)}", "ok": ok, "detail": f"{seconds:.1f} s",
                    "items": ["deliver"]})
    for r in rows or []:
        what, _, detail = str(r.get("detail", "")).partition(": ")
        out.append({"check": "requirement", "text": what[:1].upper() + what[1:], "ok": r.get("level") == "ok",
                    "detail": detail, "items": []})
    return out


def summary(results: list[dict]) -> str:
    """'4 of 5 met' for a check-in."""
    if not results:
        return ""
    met = sum(1 for r in results if r["ok"])
    return f"{met} of {len(results)} checks met" if met < len(results) else f"all {len(results)} checks met"
