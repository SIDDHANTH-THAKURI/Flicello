from __future__ import annotations

import json

import pytest

from vidosuper.devtools import make_placeholder_music
from vidosuper.ffmpeg import ffprobe_json
from vidosuper.loop import RunError, finalize, revise, start_run, submit_brief, submit_edl
from vidosuper.perceive.prep import analysis_path


def _analyse_everything(project, clips):
    for clip in clips.values():
        data = {"clip_id": clip.clip_id, "summary": f"Test clip {clip.rel_path}.", "scene": "test card",
                "camera": "static", "energy": 3, "visual_appeal": 5,
                "unusable": [{"start": 0.0, "end": 0.3, "reason": "camera settling"}],
                "moments": [{"start": 0.5, "end": min(3.5, clip.duration - 0.1), "kind": "detail",
                             "description": "colour bars", "hook": 4, "strength": 5, "best_use": "b-roll"}]}
        path = analysis_path(project, clip.clip_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture(scope="module")
def run_setup(ingested, cfg, tmp_path_factory):
    project, clips, _ = ingested
    _analyse_everything(project, clips)
    music = make_placeholder_music(tmp_path_factory.mktemp("m") / "track.wav", bpm=100)
    run = start_run(project, cfg, intent="test montage", platform="youtube", target_seconds=8, music=music)
    return project, clips, run


def test_run_refuses_unanalysed_footage(ingested, cfg):
    project, clips, _ = ingested
    path = analysis_path(project, clips["e_dark"].clip_id)
    backup = path.read_text(encoding="utf-8") if path.exists() else None
    path.unlink(missing_ok=True)
    try:
        with pytest.raises(RunError, match="not analysed"):
            start_run(project, cfg, intent="x", platform="youtube", target_seconds=8)
    finally:
        if backup is not None:
            path.write_text(backup, encoding="utf-8")


def test_brief_is_validated(run_setup):
    project, _, run = run_setup
    brief = {"title": "T", "logline": "L", "target_seconds": 8, "hook": "bars", "ending": "fade",
             "structure": [{"name": "all", "purpose": "p", "start": 0, "end": 8, "energy": 3,
                            "shot_length": "2 beats", "moments": ["c01#1", "c99#4"]}]}
    run.brief_path.write_text(json.dumps(brief), encoding="utf-8")
    problems = submit_brief(run, project)
    assert problems and "c99#4" in problems[0]
    brief["structure"][0]["moments"] = ["c01#1", "c02#1"]
    run.brief_path.write_text(json.dumps(brief), encoding="utf-8")
    assert submit_brief(run, project) == []
    assert run.load().stage == "edit"


def test_edl_with_short_ids_is_snapped_rendered_and_finalised(run_setup, cfg, monkeypatch, tmp_path):
    project, clips, run = run_setup
    it = run.iteration_dir(1)
    edl = {"segments": [
        {"id": "s1", "clip_id": "c01", "src_in": 0.5, "src_out": 2.93},  # cut at 2.43s -> beat 2.4
        {"id": "s2", "clip_id": "c03", "src_in": 1.0, "src_out": 3.2},
        {"id": "s3", "clip_id": "c05", "src_in": 0.0, "src_out": 3.0},  # uses all of 'camera settling'
    ], "music": {"start": 0.0, "gain_db": -3}, "meta": {"fade_in": 0.3, "fade_out": 0.8}}
    (it / "edl.json").write_text(json.dumps(edl), encoding="utf-8")
    result = submit_edl(run, project, cfg)
    assert not result.problems, result.problems
    assert result.draft.exists() and result.sheets
    assert any("moved" in n for n in result.notes)
    assert any("marked unusable" in w for w in result.warnings)
    final = json.loads((it / "edl.final.json").read_text(encoding="utf-8"))
    assert final["segments"][0]["clip_id"] == clips["a_stable"].clip_id  # short id resolved
    assert "| s1 |" in (it / "edl.md").read_text(encoding="utf-8")
    assert run.load().stage == "review"

    nxt = revise(run)
    assert (nxt / "edl.json").exists() and run.load().stage == "edit"
    assert submit_edl(run, project, cfg).problems == []

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    out = finalize(run, project, cfg)
    video = next(s for s in ffprobe_json(out)["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1920, 1080) and run.load().stage == "done"
