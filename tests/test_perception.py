from __future__ import annotations

import json

import pytest

from vidosuper.perceive import prep as prep_module
from vidosuper.perceive.catalog import build_catalog, check_analysis
from vidosuper.perceive.prep import PreparedClip, analysis_path, make_batches, prepare_perception, sheet_spacing
from vidosuper.perceive.transcribe import Transcript, Utterance


def test_sheet_spacing_scales_with_duration():
    assert sheet_spacing(5.0, 2.0) == 0.62
    assert sheet_spacing(75.0, 2.0) == 2.0
    assert sheet_spacing(2.0, 2.0) == 0.5


def test_batches_respect_sheet_and_clip_limits():
    items = [PreparedClip(f"c{i}", 10.0, 1.0, ["s"] * n, "", "") for i, n in enumerate([3, 3, 3, 3, 1, 1, 1, 1, 1, 1, 1])]
    batches = make_batches(items, max_sheets=10, max_clips=6)
    assert batches[0] == ["c0", "c1", "c2"]  # a fourth 3-sheet clip would exceed 10 sheets
    assert all(len(b) <= 6 for b in batches)
    assert [c for b in batches for c in b] == [f"c{i}" for i in range(11)]


@pytest.fixture(scope="module")
def prepared(ingested, cfg):
    project, clips, _ = ingested

    def fake_transcribe(clip_id, wav16k, model_name, download_root):
        lines = [Utterance(start=1.0, end=2.0, text="look at that!")] if clip_id.startswith("a_stable") else []
        return Transcript(clip_id=clip_id, model="fake", language="en", utterances=lines)

    patch = pytest.MonkeyPatch()
    patch.setattr(prep_module, "transcribe", fake_transcribe)
    try:
        tasks = prepare_perception(project, cfg)
    finally:
        patch.undo()
    return project, clips, tasks


def test_prep_writes_sheets_briefs_and_batches(prepared):
    project, clips, tasks = prepared
    assert len(tasks["clips"]) == len(clips)
    by_id = {c["clip_id"]: c for c in tasks["clips"]}
    stable = by_id[clips["a_stable"].clip_id]
    assert stable["sheets"] and all(path.endswith(".jpg") for path in stable["sheets"])
    brief = open(stable["brief"], encoding="utf-8").read()
    assert "look at that!" in brief and "Problem ranges" in brief and stable["analysis"] in brief
    shaky_brief = open(by_id[clips["b_shaky"].clip_id]["brief"], encoding="utf-8").read()
    assert "shaky" in shaky_brief
    assert sorted(c for b in tasks["batches"] for c in b) == sorted(by_id)


def _write_analysis(project, clip, **overrides):
    data = {
        "clip_id": clip.clip_id, "summary": "Test pattern.", "scene": "test card", "camera": "static",
        "energy": 2, "visual_appeal": 3,
        "moments": [{"start": 0.5, "end": 2.5, "peak": 1.0, "kind": "detail", "description": "colour bars",
                     "hook": 2, "strength": 3, "best_use": "b-roll"}],
    }
    data.update(overrides)
    path = analysis_path(project, clip.clip_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_check_accepts_valid_and_explains_invalid(prepared):
    project, clips, _ = prepared
    clip = clips["c_pan"]
    _write_analysis(project, clip)
    assert check_analysis(project, clip)[1] == []

    _write_analysis(project, clip, moments=[{"start": 4.0, "end": 99.0, "kind": "detail", "description": "too long",
                                             "hook": 2, "strength": 3, "best_use": "b-roll"}])
    problems = check_analysis(project, clip)[1]
    assert problems and "start/end" in problems[0]

    _write_analysis(project, clip, energy=9)
    assert any("energy" in p for p in check_analysis(project, clip)[1])

    _write_analysis(project, clip, clip_id="someone-else")
    assert any("expected" in p for p in check_analysis(project, clip)[1])
    _write_analysis(project, clip)  # leave a valid one behind for the catalog test


def test_catalog_lists_analysed_and_missing_clips(prepared):
    project, clips, _ = prepared
    _write_analysis(project, clips["a_stable"])
    catalog = build_catalog(project)
    entries = {e["clip"]["clip_id"]: e for e in catalog["clips"]}
    assert entries[clips["a_stable"].clip_id]["analysis"]["summary"] == "Test pattern."
    assert entries[clips["d_blurry"].clip_id]["analysis"] is None
    html = (project.root / "catalog.html").read_text(encoding="utf-8")
    assert "colour bars" in html and "Not analysed yet" in html
