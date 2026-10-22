"""Edit styles: whole looks a person picks instead of explaining. Each is a recipe on top of the cut:
- the grade;
- how the title moves and what colour the graphics take;
- whether transitions mark the film's changes of place, and which;
- what happens on the drop (words on the beat, beat lines, a flash);
- the finish (viewfinder corners, light leaks, a sweep of light);
- place names or a route between the places.

A style is applied by code to the plan of the version they're on, as a draft: they see their own film in the style in
seconds, next to the version they had, and pick. Graphics they asked for themselves stay; only the last style's own
graphics are replaced. The film's place names come from the clips' log; the drop from the music."""

from __future__ import annotations

from dataclasses import dataclass

from .brain.context import logged_at, place_of
from .brain.schemas import DesignPlan, PlanGraphic


@dataclass(frozen=True)
class Style:
    id: str
    name: str
    says: str  # one line, for the card
    look: str  # EditMeta.look
    title: str  # the title's move
    color: str  # the graphics' accent
    transition: str = ""  # on changes of place ("" = cuts only)
    transitions: int = 0  # how many at most
    drop: tuple[str, ...] = ()  # on the drop: "kinetic", "lines", "flash"
    accent: str = ""  # the finish over the opening ("" = none); "all": over the whole film
    accent_all: bool = False
    names: bool = False  # place names as lower thirds where the place changes
    route: bool = False  # a route between the film's places


STYLES: dict[str, Style] = {s.id: s for s in (
    Style("cinematic", "Clean cinematic", "Film grade, a title wiped in slowly, clean cuts, a sheen of light.",
          look="film", title="mask", color="#f4efe6", accent="sweep"),
    Style("hype", "Hype", "Punchy grade, a title that slams in, a flash into the drop and words on its beats.",
          look="punchy", title="slam", color="#ff5a36", transition="panels", transitions=2,
          drop=("flash", "kinetic", "lines")),
    Style("travel", "Travel diary", "Vivid grade, letters rising in, a route between your places and their names.",
          look="vivid", title="rise", color="#ffd38a", transition="panels", transitions=3, accent="brackets",
          names=True, route=True),
    Style("soft", "Soft and warm", "Warm grade, halves of the title meeting, gentle iris turns, light leaks.",
          look="warm", title="split", color="#ffc7a8", transition="iris", transitions=2, accent="leak"),
    Style("documentary", "Documentary", "Natural grade, places named as you reach them, nothing else in the way.",
          look="natural", title="mask", color="#9fd3c7", names=True),
    Style("camcorder", "Camcorder", "Faded grade, a viewfinder and REC over the whole film, bar wipes.",
          look="faded", title="rise", color="#ff3b3b", transition="bars", transitions=3, accent="brackets",
          accent_all=True),
)}

WORDS = {"cinematic": "cinematic", "cinema": "cinematic", "film": "cinematic", "hype": "hype", "energetic": "hype",
         "sports": "hype", "travel": "travel", "vlog": "travel", "diary": "travel", "soft": "soft", "warm": "soft",
         "dreamy": "soft", "documentary": "documentary", "doc": "documentary", "camcorder": "camcorder",
         "retro": "camcorder", "vhs": "camcorder", "home video": "camcorder"}


def places(plan: DesignPlan, facts: dict) -> list[str]:
    """The place each shot shows, from the clips' log ("" where none)."""
    pool = {m.key: m for m in plan.pool}
    out = []
    for s in plan.plan:
        m = pool.get(s.key)
        f = facts.get(m.clip) if m else None
        lm = logged_at(f, m.start, m.end) if f and m else None
        out.append(str((lm or {}).get("place") or "").strip())
    return out


def graphics(style: Style, plan: DesignPlan, facts: dict, drop: int | None, title: str) -> list[PlanGraphic]:
    """The style's own graphics for this plan (shot numbers from 1). `drop`: the index of the shot on the drop."""
    from .versions import span

    n = len(plan.plan)
    where = places(plan, facts)
    keys = [place_of({"place": p}) for p in where]
    # the shots where the film arrives somewhere new (the first shot of each run of a named place)
    changes = [k for k in range(1, n) if keys[k] and keys[k] != keys[k - 1] and keys[k] not in keys[:k]]
    out: list[PlanGraphic] = []
    by = dict(by="style", accent=style.color)
    if title:
        out.append(PlanGraphic(kind="title", shot=1, beats=min(8.0, max(4.0, span(plan.plan[0]) + 2)), text=title,
                               style=style.title, **by))
    if style.transition and style.transitions:
        picks = [k for k in changes if k != (drop or -1)][:style.transitions]
        out += [PlanGraphic(kind="transition", shot=k + 1, style=style.transition, **by) for k in picks]
    if drop is not None and drop > 0:
        if "flash" in style.drop:
            out.append(PlanGraphic(kind="transition", shot=drop + 1, style="flash", **by))
        if "kinetic" in style.drop and title:
            words = " ".join(title.split()[:4])
            out.append(PlanGraphic(kind="kinetic", shot=drop + 1, beats=len(words.split()) + 1, text=words, **by))
        if "lines" in style.drop:
            out.append(PlanGraphic(kind="accent", shot=drop + 1, beats=8, style="lines", **by))
    if style.accent:
        total = sum(span(s) for s in plan.plan)
        out.append(PlanGraphic(kind="accent", shot=1, beats=total if style.accent_all else 6.0, style=style.accent, **by))
    if style.route:
        stops = list(dict.fromkeys(p for p in where if p))
        if len(stops) >= 2:
            at = changes[0] + 1 if changes else 2
            out.append(PlanGraphic(kind="route", shot=min(n, at), beats=8.0, text="|".join(stops[:4]), **by))
    if style.names:
        for k in ([0] if keys and keys[0] and not title else []) + changes[:3]:  # the opening has the title
            if not (style.route and k == (changes[0] if changes else -1)):
                out.append(PlanGraphic(kind="lowerthird", shot=k + 1, beats=6.0, text=where[k][:40], **by))
    return out


def apply(plan: DesignPlan, style_id: str, facts: dict, drop_beat: int | None, title: str) -> DesignPlan:
    """The plan in a style: its look, and the style's graphics in place of the last style's (theirs stay)."""
    from .versions import span

    style = STYLES[style_id]
    drop, pos = None, 0.0
    for k, s in enumerate(plan.plan):
        if drop_beat is not None and abs(pos - drop_beat) < 1e-6:
            drop = k
        pos += span(s)
    mine = [g for g in plan.graphics if g.by != "style"]
    taken = {(g.kind, g.shot) for g in mine}
    new = [g for g in graphics(style, plan, facts, drop, title) if (g.kind, g.shot) not in taken
           and not (g.kind == "title" and any(x.kind == "title" for x in mine))]
    return plan.model_copy(update={"style": style_id, "graphics": [*mine, *new]})


def read(text: str) -> str | None:
    """The style named in their words ('make it cinematic', 'camcorder style'), or None."""
    import re

    low = " ".join(text.lower().split())
    for word, sid in sorted(WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(word)}\b", low) and re.search(r"\b(style|look|vibe|feel|make it|like a|go|try|it up)\b", low):
            return sid
    return None
