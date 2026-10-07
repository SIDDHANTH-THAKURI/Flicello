"""Behind-the-scenes numbers for the outro, all read from the run and the project - nothing invented."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import REPO_ROOT
from ..edit.timeline import build_timeline
from ..perceive.prep import analysis_path
from ..schemas import EDL
from ..workspace import Project


@dataclass
class RunStats:
    clips: int = 0
    raw_seconds: float = 0.0
    uhd: bool = False  # the footage is 4K
    source_frames: int = 0
    measured_frames: int = 0  # frames the measuring pass looked at (shake, blur, light, motion)
    moments: int = 0  # moments the clip analysts logged
    acts: int = 0  # sections in the director's plan
    drafts: int = 0
    reviewed: int = 0  # drafts the critic panel actually reviewed
    shots: int = 0
    final_seconds: float = 0.0
    critic_notes: int = 0
    adversary_notes: int = 0
    verdicts: int = 0  # judge comparisons
    score_trail: list[float] = field(default_factory=list)  # the critics' score per reviewed draft
    panel: list[tuple[str, str]] = field(default_factory=list)  # (rubric, score) for the best draft
    code_lines: int = 0
    music_credit: str | None = None


def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def count_code_lines(root: Path = REPO_ROOT) -> int:
    """Lines of Python in the tool and its tests - every one written by Claude."""
    total = 0
    for folder in (root / "src", root / "tests"):
        for path in folder.rglob("*.py"):
            with path.open(encoding="utf-8", errors="ignore") as fh:
                total += sum(1 for _ in fh)
    return total


def short_credit(credit: str | None) -> str | None:
    """'"Song" by Artist is licensed under CC BY 3.0. To view...' -> 'Music: "Song" by Artist · CC BY 3.0'."""
    if not credit:
        return None
    m = re.match(r'\s*"(.+?)" by (.+?)(?: \([^)]*\))? is licensed under (CC[^.]*?(?:\d\.\d)?)\.', credit)
    if m:
        return f"Music: “{m.group(1)}” by {m.group(2)} · {m.group(3)}"
    return "Music: " + credit.split(". ")[0].strip().rstrip(".")


def _fmt_score(x: float) -> str:
    return f"{x:.0f}" if abs(x - round(x)) < 0.05 else f"{x:.1f}"


def footage_stats(project: Project) -> RunStats:
    """What the footage passes did: clips watched, raw length, frames measured, moments logged."""
    s = RunStats()
    clips = project.clips()
    s.clips = len(clips)
    s.raw_seconds = sum(c.duration for c in clips.values())
    s.uhd = bool(clips) and min(min(c.width, c.height) for c in clips.values()) >= 2160
    s.source_frames = sum(c.frame_count or round(c.duration * c.fps) for c in clips.values())
    for cid in clips:
        if (signals := project.load_signals(cid)) is not None:
            s.measured_frames += round(signals.duration * signals.analysis_fps)
        s.moments += len(_json(analysis_path(project, cid)).get("moments") or [])
    return s


def collect_stats(run_dir: Path, project: Project, iteration: int) -> RunStats:
    s = footage_stats(project)
    state = _json(run_dir / "run.json")
    clips = project.clips()
    s.acts = len(_json(run_dir / "brief.json").get("structure") or [])

    iterations = sorted(p for p in (run_dir / "iterations").glob("[0-9][0-9]") if p.is_dir())
    s.drafts = sum(1 for p in iterations if (p / "draft.mp4").exists())
    final = run_dir / "iterations" / f"{iteration:02d}" / "edl.final.json"
    if final.exists():
        edl = EDL.model_validate_json(final.read_text(encoding="utf-8"))
        s.shots = len(edl.segments)
        s.final_seconds = build_timeline(edl, clips).duration

    for it in iterations:
        critiques = sorted((it / "critiques").glob("*.json"))
        s.reviewed += bool(critiques)
        for path in critiques:
            issues = len(_json(path).get("issues") or [])
            if path.stem == "adversary":
                s.adversary_notes += issues
            else:
                s.critic_notes += issues
        s.verdicts += sum(1 for _ in (it / "judge").glob("*.json"))
    scores = {int(k): float(v) for k, v in (state.get("scores") or {}).items()}
    s.score_trail = [scores[k] for k in sorted(scores)]
    best = state.get("best_iteration") or (max(scores, key=scores.get) if scores else None)
    if best:
        for path in sorted((run_dir / "iterations" / f"{int(best):02d}" / "critiques").glob("*.json")):
            overall = _json(path).get("overall")
            if path.stem != "adversary" and isinstance(overall, (int, float)):
                s.panel.append((path.stem, _fmt_score(float(overall))))
    order = ["story", "rhythm", "hook", "craft", "sound"]
    s.panel.sort(key=lambda p: order.index(p[0]) if p[0] in order else len(order))
    s.code_lines = count_code_lines()
    s.music_credit = state.get("music_credit")
    return s
