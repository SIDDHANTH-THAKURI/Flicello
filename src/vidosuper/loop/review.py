"""The review loop: critics' JSON → weighted score, ranked issues, a decision; the judge's pairwise
verdict decides whether a new iteration replaces the best so far.

Kept separate from the run state machine so the critic panel can evolve without touching drafting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from ..config import Config
from ..schemas import Critique, Judgement
from ..workspace import write_text_atomic

CRITICS = ("story", "rhythm", "hook", "craft", "sound")
ADVERSARY = "adversary"
SEVERITY_RANK = {"blocker": 0, "major": 1, "minor": 2}


def critiques_dir(iteration_dir: Path) -> Path:
    return iteration_dir / "critiques"


def load_critique(path: Path) -> tuple[Critique | None, list[str]]:
    if not path.exists():
        return None, [f"missing {path}"]
    try:
        critique = Critique.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        return None, [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:10]]
    except ValueError as exc:
        return None, [f"not valid JSON: {exc}"]
    problems = [f"score {k}={v} is outside 0-10" for k, v in critique.scores.items() if not 0 <= v <= 10]
    if not critique.scores:
        problems.append("scores is empty: score every criterion in your rubric")
    for issue in critique.issues:
        if not issue.fix.strip():
            problems.append(f"issue {issue.id} has no fix")
    return critique, problems


def missing_critiques(iteration_dir: Path) -> dict[str, list[str]]:
    out = {}
    for name in (*CRITICS, ADVERSARY):
        _, problems = load_critique(critiques_dir(iteration_dir) / f"{name}.json")
        if problems:
            out[name] = problems
    return out


@dataclass
class Review:
    score: float
    blockers: int
    majors: int
    decision: str  # approve | revise
    ranked: list[tuple[str, dict]]  # (critic, issue)


def aggregate(iteration_dir: Path, cfg: Config) -> Review:
    weights = {k: v for k, v in cfg.critic_weights.items() if k in CRITICS}
    total_w = sum(weights.values()) or 1.0
    critiques = {name: load_critique(critiques_dir(iteration_dir) / f"{name}.json")[0] for name in (*CRITICS, ADVERSARY)}
    score = sum(weights.get(n, 0) * c.overall for n, c in critiques.items() if c and n in CRITICS) / total_w
    issues: list[tuple[str, dict]] = []
    for name, critique in critiques.items():
        for issue in (critique.issues if critique else []):
            issues.append((name, issue.model_dump()))
    rank = {name: i for i, name in enumerate(sorted(weights, key=weights.get, reverse=True))}
    rank[ADVERSARY] = -1  # the adversary's findings go first within a severity
    issues.sort(key=lambda ci: (SEVERITY_RANK[ci[1]["severity"]], rank.get(ci[0], 99)))
    blockers = sum(1 for _, i in issues if i["severity"] == "blocker")
    majors = sum(1 for _, i in issues if i["severity"] == "major")
    approve = score >= cfg.loop.target_score and blockers <= cfg.loop.max_blockers and majors <= cfg.loop.max_major_issues
    review = Review(score=round(score, 2), blockers=blockers, majors=majors,
                    decision="approve" if approve else "revise", ranked=issues)
    _write_review(iteration_dir, review, critiques, cfg)
    return review


def _write_review(iteration_dir: Path, review: Review, critiques: dict[str, Critique | None], cfg: Config) -> None:
    lines = ["# Review", "",
             f"Weighted score **{review.score:.2f}/10** (target {cfg.loop.target_score}); {review.blockers} blocker(s), "
             f"{review.majors} major issue(s). Decision: **{review.decision}**.", "",
             "| critic | overall | scores |", "|---|---|---|"]
    for name, c in critiques.items():
        if c:
            lines.append(f"| {name} | {c.overall:.1f} | " + ", ".join(f"{k} {v:g}" for k, v in c.scores.items()) + " |")
    top = review.ranked[: cfg.loop.issues_per_round]
    lines += ["", f"## Fix these next ({len(top)} of {len(review.ranked)} issues, most important first)"]
    for critic, issue in top:
        where = ", ".join(issue["segments"]) or (f"{issue['at']:.1f}s" if issue.get("at") is not None else "whole edit")
        lines += [f"- **{issue['id']}** ({critic}, {issue['severity']}, {where}): {issue['problem']}",
                  f"  - evidence: {issue['evidence']}", f"  - fix: {issue['fix']}"]
    rest = review.ranked[cfg.loop.issues_per_round:]
    if rest:
        lines += ["", "## Also noted"] + [f"- {i['id']} ({c}, {i['severity']}): {i['problem']}" for c, i in rest]
    strengths = [f"- ({n}) {s}" for n, c in critiques.items() if c for s in c.strengths]
    if strengths:
        lines += ["", "## Keep (what works)"] + strengths
    write_text_atomic(iteration_dir / "review.md", "\n".join(lines) + "\n")
    write_text_atomic(iteration_dir / "review.json", json.dumps({
        "score": review.score, "blockers": review.blockers, "majors": review.majors, "decision": review.decision,
        "top_issues": [{"critic": c, **i} for c, i in top]}, indent=1))


def load_judgement(path: Path) -> tuple[Judgement | None, list[str]]:
    if not path.exists():
        return None, [f"missing {path}"]
    try:
        return Judgement.model_validate_json(path.read_text(encoding="utf-8")), []
    except (ValidationError, ValueError) as exc:
        return None, [str(exc).splitlines()[0]]


def decide(judge_dir: Path) -> tuple[str | None, list[str]]:
    """Two verdicts with the order swapped: ab.json (A = best so far, B = new), ba.json (A = new, B = best).
    The new cut wins only if it wins both; anything else keeps the best (position bias cancels out)."""
    ab, p1 = load_judgement(judge_dir / "ab.json")
    ba, p2 = load_judgement(judge_dir / "ba.json")
    if p1 or p2:
        return None, p1 + p2
    new_wins = ab.preferred == "B" and ba.preferred == "A"
    best_wins = ab.preferred == "A" and ba.preferred == "B"
    if new_wins:
        return "new", ab.reasons + ba.reasons
    if best_wins:
        return "best", ab.reasons + ba.reasons
    return "inconsistent", ab.reasons + ba.reasons
