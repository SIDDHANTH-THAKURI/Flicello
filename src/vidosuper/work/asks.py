"""Flicello's own questions to the person, asked by Cello and shown as a card of options beside it (the same card as
the work agent's questions), and the person's lasting choices, kept visible beside the film.

The first one: once the clips are in, the film's shape. Flicello looks at the clips (mostly vertical, mostly wide)
and asks with its recommendation first."""

from __future__ import annotations

import json
import re
import secrets
import time

SHAPES = {"9:16": "Vertical 9:16 (Reels, TikTok, Shorts)", "16:9": "Landscape 16:9 (YouTube)",
          "both": "Both: vertical and landscape"}


def choose(studio, project: dict, what: str, value: str) -> list[dict]:
    """Keep one of their choices (the latest value of each `what` wins)."""
    info = studio._info(project)
    kept = [c for c in info.get("choices") or [] if c.get("what") != what]
    kept.append({"what": " ".join(what.split())[:40], "value": " ".join(value.split())[:120], "at": time.time()})
    studio._set_info(project, choices=kept[-24:])
    return kept


def choices(studio, project: dict | None) -> list[dict]:
    """Their choices for the film: the film's own settings, then what they decided along the way."""
    if project is None:
        return []
    p = studio.db.project(project["id"])
    out = [{"what": "Shape", "value": SHAPES.get(p["shapes"], p["shapes"])}]
    if p["kind"] == "montage" and studio._info(p).get("speech"):
        out.append({"what": "Kind", "value": "A talk, cut on your words"})
    elif p["kind"] != "montage":
        out.append({"what": "Kind", "value": {"talk": "Talking video", "voice": "Voice to animation"}.get(p["kind"], p["kind"])})
    out.append({"what": "Length", "value": f"{float(p['length']):g} s"})
    if p.get("music"):
        out.append({"what": "Music", "value": {"made": "Made for this film", "none": "None"}.get(p["music"], p["music"])})
    out += [{"what": c["what"], "value": c["value"]} for c in studio._info(p).get("choices") or []
            if c.get("what") not in ("Shape", "Kind", "Length", "Music")]
    return out


def ask(studio, user: dict, project: dict, key: str, question: str, options: list[str], say: str = "") -> dict | None:
    """Put one of Flicello's questions to them (once per film and key). None: already asked."""
    if studio.db.one("SELECT id FROM work WHERE project_id=? AND kind='system' AND key=?", (project["id"], key)):
        return None
    wid = "w" + secrets.token_hex(6)
    now = time.time()
    studio.db.run("INSERT INTO work(id, project_id, user_id, kind, status, key, question, options, say, created, updated) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (wid, project["id"], user["id"], "system", "asking", key, question,
                                                    json.dumps(options), say, now, now))
    return studio.db.one("SELECT * FROM work WHERE id=?", (wid,))


def answer(studio, user: dict, row: dict, text: str) -> dict:
    """Their answer to one of Flicello's questions, carried out."""
    project = studio.db.project(row["project_id"])
    said = ""
    if row["key"] == "shape":
        low = text.lower()
        shape = ("both" if re.search(r"\bboth\b", low) else "16:9" if re.search(r"16:9|landscape|wide|horizontal|youtube", low)
                 else "9:16" if re.search(r"9:16|vertical|portrait|reel|tiktok|short", low) else None)
        if shape is None:
            raise ValueError("Vertical, landscape, or both?")
        if project["stage"] == "setup":
            studio.set_settings(project, shape, None)
        choose(studio, project, "Shape", SHAPES[shape])
        said = f"{SHAPES[shape].split(' (')[0]} it is."
    studio.db.run("UPDATE work SET status='done', answer=?, question=NULL, options=NULL, say=?, updated=? WHERE id=?",
                  (text, said, time.time(), row["id"]))
    return studio.db.one("SELECT * FROM work WHERE id=?", (row["id"],))


def after_upload(studio, user: dict, project: dict) -> None:
    """The clips are in: ask the shape, recommending what most of them are."""
    project = studio.db.project(project["id"])
    if project is None or project["stage"] != "setup" or project["kind"] == "voice":
        return
    clips = studio.db.all("SELECT width, height, seconds FROM clips WHERE project_id=?", (project["id"],))
    if not clips:
        return
    tall = sum(float(c["seconds"] or 1) for c in clips if (c["height"] or 0) > (c["width"] or 0))
    wide = sum(float(c["seconds"] or 1) for c in clips if (c["width"] or 0) >= (c["height"] or 0))
    first = "9:16" if tall >= wide else "16:9"
    order = [first, "16:9" if first == "9:16" else "9:16", "both"]
    what = "mostly vertical" if tall >= wide else "mostly landscape"
    ask(studio, user, project, "shape", "Which shape should the film be?", [SHAPES[s] for s in order],
        f"Your clips are {what}.")
