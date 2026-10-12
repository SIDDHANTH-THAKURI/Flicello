"""Replay: plan a finished video again with today's code and measure the plan, with no AI call and no render.

This is how the daily routine tells whether a change helps: every real project the studio has made (its clips, song,
note and pace are all still on disk) is planned again, and the numbers that make a cut good or bad are written next to
the git commit and next to what the person thought of the old version. A change that makes the numbers worse on
videos people disliked, or on videos they liked, is not an improvement.

Clip analysis is reused from the project (it was made by the AI at the time), so a replay costs nothing. Plans for
paces the AI designs (calm, balanced, fast) use the no-AI brain here: they show the structure, not the AI's taste.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import time
from pathlib import Path

from .brain import make_brain
from .brain.schemas import StoryPitch
from .pipeline import Pipeline, studio_config
from .storage import workdir


def commit_id(repo: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "?"
    except Exception:  # noqa: BLE001 - no git: the numbers still stand
        return "?"


def replay(studio, project: dict, pace: str | None = None) -> dict:
    """Plan `project` again; returns the measurements."""
    user = studio.user(project["user_id"])
    work = workdir(studio.s, user["id"], project["id"])
    pipe = Pipeline(work, studio_config(studio.s.workspace, studio.s.render_preset, studio.s.models_dir), None,
                    lambda f, m: None, owner=studio.is_owner(user))
    pipe.brain = make_brain("local", studio.s, lambda u: None, work.brain, pipe.project.load_signals)
    pace = pace or project["pace"]
    ctx = pipe.context(studio.shapes(project)[:1], project["length"], project["intent"], studio.profile(user).rules, pace)
    pitch = StoryPitch(title="replay", logline="replay", feeling="-", hook="-", turn="-", ending="-",
                       beats=[], stills=[], why="-")
    try:
        pitch_row = studio.pitches(project)[0]["pitch"] if studio.pitches(project) else None
        if pitch_row:
            pitch = StoryPitch.model_validate(pitch_row)
    except Exception:  # noqa: BLE001 - a missing or old pitch only changes the title
        pass
    plan = pipe.design(ctx, pitch)
    shape = studio.shapes(project)[0]
    _design, template = pipe._template(ctx, plan, shape, "vivid")
    lens = [s.end - s.start for s in template.slots]
    keys = [(m.clip, round(m.start, 1)) for m in plan.pool]
    return {
        "project": project["id"], "user": user["name"], "pace": pace, "length": project["length"],
        "shots": len(lens), "mean_shot": round(statistics.mean(lens), 3), "shortest": round(min(lens), 3),
        "longest": round(max(lens), 3), "under_half_second": round(sum(x < 0.5 for x in lens) / len(lens), 3),
        "distinct_windows": len(set(keys)), "mirrored": sum(bool(getattr(s, "flip", False)) for s in template.slots),
        "max_zoom": round(max(s.zoom for s in template.slots), 2), "hits": template.idea,
        "folded": len(plan.plan) - len(lens), "notes": plan.notes[:240],
    }


def run_bench(studio, repo: Path, only: str | None = None) -> list[dict]:
    """Replay every finished project (or one) and append to <home>/improve/bench.jsonl with the commit and the rating
    people gave the original."""
    folder = studio.s.home / "improve"
    folder.mkdir(parents=True, exist_ok=True)
    commit = commit_id(repo)
    results = []
    for p in studio.db.all("SELECT * FROM projects WHERE deleted=0 AND stage='done' ORDER BY created"):
        if only and p["id"] != only:
            continue
        try:
            row = replay(studio, p)
        except Exception as exc:  # noqa: BLE001 - one project that can't be replayed must not stop the rest
            row = {"project": p["id"], "error": f"{type(exc).__name__}: {exc}"[:200]}
        fb = studio.db.one("SELECT liked, match, tags FROM feedback WHERE project_id=? AND stage='final' "
                           "ORDER BY id DESC LIMIT 1", (p["id"],))
        row.update(commit=commit, at=time.time(), rated_liked=fb["liked"] if fb else None,
                   rated_tags=json.loads(fb["tags"]) if fb else None)
        results.append(row)
    with (folder / "bench.jsonl").open("a", encoding="utf-8") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in results)
    return results
