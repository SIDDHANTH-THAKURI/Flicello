"""The improvement digest: everything people said about their videos and everything that went wrong, since the last
digest, on one page for whoever works on the studio next (the owner, or Claude on its daily routine).

It is plain counting and quoting: no AI call, no cost. It says where to look (a tag maps to the part of the code that
decides that thing); the reading and the fixing are the routine's job (docs/improvement.md). Reports are written under
the studio home, next to the data they quote (people's own words and names), never into the repository.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter
from pathlib import Path

from .db import DB

# What a complaint usually means in code: where the routine starts looking.
TAG_HINTS = {
    "too slow": "pace: brain/strikes.py cut_points, pace_text/check_plan (brain/core.py), prompts/editor.md",
    "too fast": "pace: density 'beats' in strikes.py, check_plan pace rules",
    "bad shots": "choice of footage: strikes.py scoring, perceive/ moments, enrich_with_impacts (pipeline.py)",
    "didn't follow my idea": "reading the note: brain/context.py read_note/pace_from_note, the pitch and design prompts",
    "music didn't fit": "music: music/analysis.py beats, strikes, drops; music/house.py",
    "wrong music": "music: music/analysis.py, songfit.py",
    "boring": "story: pitch prompt (prompts/director.md), strikes.py variety",
    "too much": "pace/effects: design.py bar pulses, fx",
}
FAILED_STAGES = ("failed", "paused")


def _since(db: DB, days: float | None, since: float | None) -> float:
    if since is not None:
        return since
    if days is not None:
        return time.time() - days * 86400
    last = db.value("SELECT at FROM heartbeat WHERE name='digest'", (), None)
    return float(last) if last else time.time() - 7 * 86400


def _first_line(text: str, n: int = 160) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()[:n]
    return ""


def build_digest(db: DB, days: float | None = None, since: float | None = None,
                 rules: dict[str, list[str]] | None = None) -> dict:
    t0 = _since(db, days, since)
    names = {u["id"]: u["name"] for u in db.all("SELECT id, name FROM users")}
    projects = {p["id"]: p for p in db.all("SELECT * FROM projects WHERE deleted=0")}
    out: dict = {"since": t0, "now": time.time()}

    rows = db.all("SELECT * FROM feedback WHERE created > ? AND stage='final' ORDER BY created", (t0,))
    ratings, tags, notes = [], Counter(), []
    for f in rows:
        p = projects.get(f["project_id"], {})
        ftags = json.loads(f["tags"] or "[]")
        tags.update(ftags)
        ratings.append({"user": names.get(f["user_id"], "?"), "project": f["project_id"], "version": f["version"],
                        "match": f["match"], "liked": f["liked"], "tags": ftags, "text": f["text"],
                        "pace": p.get("pace"), "length": p.get("length"), "intent": p.get("intent", ""),
                        "plan": _plan_summary(db, f["project_id"])})
        if f["text"]:
            notes.append(f["text"])
    out["ratings"] = ratings
    out["tags"] = dict(tags.most_common())
    out["avg_liked"] = round(statistics.mean(r["liked"] for r in ratings if r["liked"]), 2) if any(r["liked"] for r in ratings) else None
    out["avg_match"] = round(statistics.mean(r["match"] for r in ratings if r["match"]), 2) if any(r["match"] for r in ratings) else None
    out["sample_verdicts"] = [dict(project=f["project_id"], liked=f["liked"], text=f["text"])
                              for f in db.all("SELECT * FROM feedback WHERE created > ? AND stage='sample' AND liked=0", (t0,))]

    jobs = db.all("SELECT * FROM jobs WHERE created > ? AND kind != 'learn' ORDER BY id", (t0,))
    out["jobs"] = {"total": len(jobs), "failed": sum(j["status"] == "failed" for j in jobs)}
    took: dict[str, list[float]] = {}
    for j in jobs:
        if j["status"] == "done" and j["started"] and j["finished"]:
            took.setdefault(j["kind"], []).append(j["finished"] - j["started"])
    out["median_seconds"] = {k: round(statistics.median(v)) for k, v in took.items()}
    failures = Counter(_first_line(j["error"]) for j in jobs if j["status"] == "failed")
    errors = Counter(_first_line(e["message"], 200) for e in db.all("SELECT * FROM events WHERE at > ? AND level='error'", (t0,)))
    out["failures"] = dict(failures.most_common(8))
    out["error_events"] = dict(errors.most_common(8))
    out["stuck"] = [{"project": p["id"], "title": p["title"], "user": names.get(p["user_id"], "?"), "stage": p["stage"],
                     "error": _first_line(p["error"])} for p in projects.values()
                    if p["stage"] in FAILED_STAGES and p["updated"] > t0]

    warn = Counter()
    for r in db.all("SELECT findings FROM renders WHERE created > ? AND kind='final'", (t0,)):
        for f in json.loads(r["findings"] or "[]"):
            if f.get("level") in ("warn", "fail"):
                warn[f"{f['level']} {f.get('check')}"] += 1
    out["check_findings"] = dict(warn.most_common(8))

    finished = [p for p in projects.values() if p["stage"] == "done" and p["updated"] > t0]
    rated = {f["project_id"] for f in db.all("SELECT project_id FROM feedback WHERE stage='final'")}
    out["finished"] = len(finished)
    out["unrated"] = [{"project": p["id"], "title": p["title"], "user": names.get(p["user_id"], "?")}
                      for p in finished if p["id"] not in rated]
    out["videos_started"] = sum(1 for p in projects.values() if p["created"] > t0)
    out["spend_usd"] = round(float(db.value("SELECT COALESCE(SUM(usd),0) FROM spend WHERE at > ?", (t0,), 0.0) or 0.0), 3)
    out["month_usd"] = round(db.month_spend(), 3)
    out["rules"] = rules or {}
    out["hints"] = {t: TAG_HINTS[t] for t in tags if t in TAG_HINTS}
    return out


def _plan_summary(db: DB, project_id: str) -> str:
    row = db.one("SELECT data FROM plans WHERE project_id=? ORDER BY id DESC LIMIT 1", (project_id,))
    if not row:
        return ""
    plan = json.loads(row["data"])
    shots = plan.get("plan") or []
    return f"{len(shots)} shots; {_first_line(plan.get('notes', ''), 220)}"


def to_markdown(d: dict) -> str:
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(d["now"]))
    since = time.strftime("%Y-%m-%d %H:%M", time.localtime(d["since"]))
    lines = [f"# Flicello improvement digest, {when}", f"Covers {since} to now.", ""]
    lines += [f"- Videos started {d['videos_started']}, finished {d['finished']}, rated {len(d['ratings'])}; "
              f"jobs {d['jobs']['total']} ({d['jobs']['failed']} failed)",
              f"- Average liked {d['avg_liked']}, average match {d['avg_match']} (liked 1-10, match 1-5)",
              f"- Typical seconds per job: {d['median_seconds'] or 'no jobs yet'}",
              f"- AI spend: ${d['spend_usd']} in the window, ${d['month_usd']} this month", ""]
    if d["tags"]:
        lines += ["## What people ticked", *[f"- {t}: {n}" for t, n in d["tags"].items()], ""]
        if d["hints"]:
            lines += ["Where to look:", *[f"- {t} -> {h}" for t, h in d["hints"].items()], ""]
    if d["ratings"]:
        lines += ["## Each rating"]
        for r in d["ratings"]:
            lines += [f"- **{r['user']}**, {r['project']} v{r['version']}: liked {r['liked']}, match {r['match']}, "
                      f"tags {r['tags'] or '-'}, pace {r['pace']}, {r['length']:.0f} s",
                      f"  - their note: {r['intent'] or '(none)'}",
                      f"  - what they said: {r['text'] or '(nothing)'}",
                      f"  - the plan: {r['plan'] or '?'}"]
        lines.append("")
    if d["sample_verdicts"]:
        lines += ["## Samples they turned down", *[f"- {s['project']}: {s['text'] or '(no reason)'}" for s in d["sample_verdicts"]], ""]
    if d["stuck"] or d["failures"] or d["error_events"]:
        lines += ["## Things that broke"]
        lines += [f"- {n}x job failed: {m}" for m, n in d["failures"].items()]
        lines += [f"- {n}x error: {m}" for m, n in d["error_events"].items()]
        lines += [f"- STUCK {s['user']}'s '{s['title']}' ({s['project']}) at {s['stage']}: {s['error']}" for s in d["stuck"]]
        lines.append("")
    if d["check_findings"]:
        lines += ["## Our own checks flagged", *[f"- {n}x {k}" for k, n in d["check_findings"].items()], ""]
    if d["unrated"]:
        lines += ["## Finished but not rated (ask for a rating: it is the only way to learn)",
                  *[f"- {u['user']}: '{u['title']}' ({u['project']})" for u in d["unrated"]], ""]
    if d["rules"]:
        lines += ["## What the studio has learned about each person", *[f"- {u}: " + "; ".join(r) for u, r in d["rules"].items() if r], ""]
    return "\n".join(lines)


def write_digest(db: DB, home: Path, days: float | None = None,
                 rules: dict[str, list[str]] | None = None) -> tuple[Path, dict]:
    """Build, save under <home>/improve/ and move the 'since' mark on."""
    d = build_digest(db, days=days, rules=rules)
    folder = Path(home) / "improve"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"digest-{time.strftime('%Y-%m-%d-%H%M', time.localtime(d['now']))}.md"
    path.write_text(to_markdown(d), encoding="utf-8")
    with (folder / "metrics.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": d["now"], "avg_liked": d["avg_liked"], "avg_match": d["avg_match"], "rated": len(d["ratings"]),
                             "finished": d["finished"], "failed_jobs": d["jobs"]["failed"], "tags": d["tags"]}) + "\n")
    db.run("INSERT INTO heartbeat(name, at, info) VALUES ('digest', ?, '') ON CONFLICT(name) DO UPDATE SET at=excluded.at",
           (d["now"],))
    return path, d
