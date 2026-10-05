from __future__ import annotations

import json
from datetime import datetime

import pytest

from vidosuper.config import load_config
from vidosuper.loop.review import ADVERSARY, CRITICS, aggregate, decide
from vidosuper.loop.run import Run, RunState, loop_status, record_judgement, record_review


def _critique(name, overall, issues=()):
    return {"critic": name, "scores": {"a": overall, "b": overall}, "overall": overall, "strengths": ["x"],
            "issues": [{"id": f"{name[:3].upper()}-{k}", "severity": sev, "segments": ["s01"], "problem": f"p{k}",
                        "evidence": "e", "fix": "f"} for k, sev in enumerate(issues)]}


def _write_critiques(it, overall=6.0, adversary_issues=("major",), other_issues=("minor",)):
    folder = it / "critiques"
    folder.mkdir(parents=True, exist_ok=True)
    for name in CRITICS:
        (folder / f"{name}.json").write_text(json.dumps(_critique(name, overall, other_issues)), encoding="utf-8")
    (folder / f"{ADVERSARY}.json").write_text(json.dumps(_critique(ADVERSARY, overall - 2, adversary_issues)),
                                              encoding="utf-8")
    (it / "evidence.md").write_text("evidence", encoding="utf-8")


@pytest.fixture
def run(tmp_path):
    r = Run(tmp_path / "run")
    r.dir.mkdir()
    state = RunState(run_id="t", clips_dir=".", created=datetime.now(), intent="i", platform="youtube",
                     resolution="1080p", target_seconds=60, fps=30, stage="review", iteration=1)
    r.save(state)
    r.iteration_dir(1).mkdir(parents=True)
    return r


def test_aggregate_weights_ranks_and_decides(run):
    cfg = load_config()
    it = run.iteration_dir(1)
    _write_critiques(it, overall=6.0)
    review = aggregate(it, cfg)
    assert review.score == 6.0 and review.decision == "revise"  # below the 8.5 target
    assert review.ranked[0][0] == ADVERSARY  # adversary's major ranks first
    text = (it / "review.md").read_text(encoding="utf-8")
    assert "Fix these next" in text and "Keep (what works)" in text
    _write_critiques(it, overall=9.0, adversary_issues=(), other_issues=("minor",))
    assert aggregate(it, cfg).decision == "approve"


def test_judge_needs_both_orders_to_agree(tmp_path):
    folder = tmp_path / "judge"
    folder.mkdir()
    verdict = lambda p: json.dumps({"preferred": p, "confidence": 0.8, "reasons": ["r"]})  # noqa: E731
    (folder / "ab.json").write_text(verdict("B"), encoding="utf-8")
    (folder / "ba.json").write_text(verdict("A"), encoding="utf-8")
    assert decide(folder)[0] == "new"
    (folder / "ba.json").write_text(verdict("B"), encoding="utf-8")  # position bias: always picks B
    assert decide(folder)[0] == "inconsistent"


def test_loop_status_walks_the_review_cycle(run):
    cfg = load_config()
    it = run.iteration_dir(1)
    assert "evidence" in loop_status(run, cfg)
    (it / "evidence.md").write_text("e", encoding="utf-8")
    assert "Critiques needed" in loop_status(run, cfg)
    _write_critiques(it, overall=6.0)
    record_review(run, cfg)
    status = loop_status(run, cfg)
    assert "CHECKPOINT" in status and "Revise" in status  # round 1 is a checkpoint by default
    state = run.load()
    assert state.best_iteration == 1 and state.verdicts[1] == "first"

    state.iteration, state.stage = 2, "review"  # a revision competes with the best
    run.save(state)
    it2 = run.iteration_dir(2)
    it2.mkdir()
    _write_critiques(it2, overall=7.0)
    record_review(run, cfg)
    assert "Judge needed" in loop_status(run, cfg)
    (it2 / "judge").mkdir()
    (it2 / "judge" / "ab.json").write_text(json.dumps({"preferred": "B", "confidence": 0.7, "reasons": ["r"]}), encoding="utf-8")
    (it2 / "judge" / "ba.json").write_text(json.dumps({"preferred": "A", "confidence": 0.7, "reasons": ["r"]}), encoding="utf-8")
    verdict, _ = record_judgement(run)
    assert verdict == "new" and run.load().best_iteration == 2
    assert "Revise" in loop_status(run, cfg)


def test_report_page_includes_video_scores_and_issues(run):
    from vidosuper.loop.report import iteration_report

    cfg = load_config()
    it = run.iteration_dir(1)
    _write_critiques(it, overall=6.0)
    record_review(run, cfg)
    (it / "draft.mp4").write_bytes(b"\x00")  # placeholder; the page only links to it
    page = iteration_report(run, 1).read_text(encoding="utf-8")
    assert "<video" in page and "6.00" in page and "Fix next" in page
