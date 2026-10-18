"""What people ask for that Flicello can't do yet (docs/proposals/flicello-next.md, item 10).

Every request (the note, the words for the cut, a film described in their words, what they agreed after "what I
understood") is read for abilities the engine doesn't have. The person is told plainly, with the closest thing that
works today, and may ask to be told when it's ready. The owner sees what is asked for most on the admin page and
decides what gets built (nothing is built or shipped without the owner's OK).

Rules, not AI: free, instant, and the same answer every time. Keep GAPS true: remove an entry the day its ability
ships (and tell the people waiting from the admin page).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Gap:
    id: str
    name: str  # what it is, in the person's words
    pattern: str  # how a request asks for it (case-insensitive)
    closest: str  # what works today instead
    kinds: tuple[str, ...] = ("montage", "talk", "voice")  # where it is missing


GAPS = [
    Gap("lyrics", "the song's words on screen", r"\blyrics?\b|words of the song|karaoke",
        "a title with the song's name at the start"),
    Gap("split", "split screen", r"split[- ]?screen|side[- ]by[- ]side|two (clips|videos|shots) at (once|the same time)",
        "quick cuts between the two"),
    Gap("pip", "a picture in the picture", r"picture[- ]in[- ]picture|\bpip\b|reaction video|(small|little) (video|window) in the corner",
        "cutting between the two"),
    Gap("reverse", "playing a shot backwards",
        r"\bplay(ed|ing|s)? (it |them |this )?(in )?reverse\b|\breverse(d)? (the |a |my )?(clip|shot|video|footage|motion)|\bbackwards\b",
        "a speed ramp: a rush, then slow motion"),
    Gap("logo", "your logo or branding", r"\b(logo|watermark|branding|my brand|lower[- ]third|end card)\b",
        "a title in the film's own style"),
    Gap("subtitles", "subtitles for what people say in the clips", r"\b(subtitles?|captions?)\b",
        "a talking video, which has captions", ("montage",)),
    Gap("map", "a map or a travel route", r"\b(map|route animation|travel route|flight path|show the route)\b",
        "a title with the place's name"),
    Gap("photos", "photos in the film", r"\b(my|these|the|some|our) (photos|pictures|images)\b|\bslideshow\b|"
        r"\bphoto ?montage\b|\bstill (photos|images)\b",
        "short clips (a phone's live photos work)", ("montage",)),
    Gap("stock", "footage from the internet", r"\bstock (footage|video|clips?)\b|\bb-?roll (from|off) (the )?(internet|web)\b",
        "your own clips"),
    Gap("generated", "AI-generated shots", r"\b(ai[- ]generated|generate (a|the|some|new) (shot|scene|clip|video)s?|"
        r"text[- ]to[- ]video|(turn|make) (me|it|this|us) (into )?(an? )?(anime|cartoon|pixar)|generate footage)\b",
        "painted looks in a voice or talking video"),
    Gap("voiceover", "a voice that reads a script", r"\b(ai voice|voice ?over|narrat(e|ion|or)|read (it|this|my script) (out|aloud))\b",
        "recording your own voice: 'Just my voice' makes an animation from it", ("montage",)),
    Gap("medley", "more than one song", r"\b(two|2|three|3|multiple|several|different) songs\b|\bmedley\b|\bmash-?up\b",
        "one song, extended to fit the film"),
    Gap("countdown", "a countdown, timer or progress bar", r"\bcount ?down\b|\btimer\b|\bprogress bar\b",
        "a title card"),
    Gap("blur", "blurring a face or a number plate", r"\bblur (the |his |her |their |my )?(face|faces|plate|number|people)\b|"
        r"\bhide (the |his |her |their )?face",
        "leaving those clips out (on 'what I understood')"),
    Gap("background", "changing the background of your clips", r"\b(green ?screen|change the background|replace the "
        r"background|remove the background)\b", "a talking video, where you stand in front of a painted world", ("montage",)),
    Gap("trend", "an app's trend template", r"\b(capcut|tiktok trend|trending template|instagram template)\b",
        "a fast cut on the beat"),
]
BY_ID = {g.id: g for g in GAPS}


def find(text: str, kind: str) -> list[Gap]:
    """The missing abilities a request asks for (each once)."""
    text = " ".join((text or "").split())
    return [g for g in GAPS if kind in g.kinds and re.search(g.pattern, text, re.IGNORECASE)]


def note(db, project: dict, text: str) -> list[str]:
    """Keep what this project asks for that is missing (once per project and ability); returns the new ids."""
    new = []
    for g in find(text, project["kind"]):
        if db.one("SELECT id FROM gaps WHERE project_id=? AND ability=?", (project["id"], g.id)):
            continue
        db.run("INSERT INTO gaps(at, user_id, project_id, ability, words) VALUES (?,?,?,?,?)",
               (time.time(), project["user_id"], project["id"], g.id, " ".join((text or "").split())[:300]))
        new.append(g.id)
    return new


def for_project(db, project: dict) -> list[dict]:
    rows = db.all("SELECT * FROM gaps WHERE project_id=? ORDER BY id", (project["id"],))
    return [{**r, "name": BY_ID[r["ability"]].name, "closest": BY_ID[r["ability"]].closest}
            for r in rows if r["ability"] in BY_ID]


def summary(db) -> list[dict]:
    """For the owner: what is asked for most, by how many people, how many wait to hear, and an example request."""
    out = []
    for r in db.all("SELECT ability, COUNT(DISTINCT user_id) AS people, COUNT(*) AS asks, MAX(at) AS last, "
                    "SUM(CASE WHEN notify=1 AND told IS NULL THEN 1 ELSE 0 END) AS waiting FROM gaps GROUP BY ability "
                    "ORDER BY people DESC, asks DESC"):
        example = db.one("SELECT words FROM gaps WHERE ability=? ORDER BY id DESC LIMIT 1", (r["ability"],))
        g = BY_ID.get(r["ability"])
        out.append({**r, "name": g.name if g else r["ability"], "example": example["words"] if example else "",
                    "shipped": g is None})
    return out
