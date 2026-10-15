"""One shot per hit of the music: cut points, hero hits on beats, footage chosen by motion, and the
pieces around it (note reading, time estimates, the made-up beat)."""

from __future__ import annotations

import numpy as np
import pytest

from vidosuper.studio.brain.context import BeatMap, ClipFacts, look_from_note, pace_from_note, read_note
from vidosuper.studio.brain.strikes import Hit, cut_points, place_heroes, plan_on_strikes

PERIOD = 0.5


def _beats(target: int = 32, strikes=(), energy: float = 0.9) -> BeatMap:
    return BeatMap(first=0, t0=0.0, period=PERIOD, available=target + 8, target=target, drop=None,
                   sections=[("peak", 0, target + 8, energy)], strikes=list(strikes))


def _clip(short: str = "c01", seconds: float = 60.0, moments=None, impacts=()) -> ClipFacts:
    moments = moments if moments is not None else [
        {"start": 0.0, "end": seconds, "strength": 7, "description": "swinging a bat", "notes": ""}]
    return ClipFacts(short, short + "-id", seconds, 30.0, True, [], moments, list(impacts))


def _signals(seconds: float = 60.0):
    t = np.arange(0, seconds, 0.1)
    motion = 0.02 + 0.03 * (np.sin(t * 2.1) > 0.4)  # bursts of action
    shake = np.zeros_like(t)
    return lambda short: (t, shake, motion)


def test_cut_points_follow_the_strikes_where_the_music_hits_hard():
    pts = cut_points(_beats(16, strikes=[1.75, 2.5, 3.0, 5.25]))
    assert pts[0] == 0.0 and pts[-1] == 16.0
    assert 1.75 in pts and 2.5 in pts  # the strikes are cuts
    assert 2.0 not in pts  # a silent beat a sixteenth away from a strike gives way to it
    assert 5.25 in pts and 5.0 not in pts
    gaps = np.diff(pts) * PERIOD
    assert gaps.min() >= 0.215 - 1e-9


def test_cut_points_are_just_the_beats_when_the_music_is_quiet_or_strikes_are_off():
    assert cut_points(_beats(8, strikes=[1.5], energy=0.4)) == [float(k) for k in range(9)]
    assert cut_points(_beats(8, strikes=[1.5]), density="beats") == [float(k) for k in range(9)]
    quiet = cut_points(_beats(8, energy=0.2))
    assert quiet == [0.0, 2.0, 4.0, 6.0, 8.0]  # every other beat


def test_each_hit_gets_a_beat_a_wind_up_and_a_follow_through():
    beats = _beats(32)
    pts = cut_points(beats)
    facts = {"c01": _clip(impacts=[10.0, 20.0, 40.0])}
    heroes = place_heroes(pts, beats, [Hit("c01", t, -10 + t / 10) for t in (10.0, 20.0, 40.0)], facts)
    assert len(heroes) == 3
    for h in heroes:
        assert pts[h["k"]] == int(pts[h["k"]])  # on a whole beat
        assert h["lead"] > 0 and h["span"] > h["lead"]
    assert [h["k"] for h in heroes] == sorted(h["k"] for h in heroes)


def test_the_plan_adds_up_has_every_hit_once_and_never_shows_a_hit_in_a_plain_shot():
    beats = _beats(32, strikes=[k + 0.75 for k in range(1, 30, 3)])
    hits = [Hit("c01", t, -10.0) for t in (12.0, 25.0, 41.0)]
    facts = {"c01": _clip(impacts=[h.t for h in hits])}
    plan = plan_on_strikes(facts, _signals(), beats, hits, "Test", "idea")
    assert abs(sum(s.beats for s in plan.plan) - beats.target) < 1e-9
    pool = {m.key: m for m in plan.pool}
    heroes = [s for s in plan.plan if s.peak_beat is not None]
    assert len(heroes) == 3 and all(pool[s.key].peak in (12.0, 25.0, 41.0) for s in heroes)
    for s in plan.plan:
        if s.peak_beat is None:
            m = pool[s.key]
            assert not any(m.start < h.t < m.end for h in hits)  # plain shots keep clear of every hit
        assert s.beats * PERIOD >= 0.215 - 1e-9
    assert all(s.zoom is not None for s in plan.plan)


def test_a_clip_that_runs_out_is_reused_with_a_new_framing_not_the_same_picture():
    beats = _beats(64)
    facts = {"c01": _clip(seconds=10.0)}  # ~64 shots from 10 s of footage
    plan = plan_on_strikes(facts, _signals(10.0), beats, [], "Tight", "idea")
    zooms = {round(s.zoom, 2) for s in plan.plan}
    assert 1.0 in zooms and len(zooms) > 2
    assert max(zooms) <= 1.32


def test_a_moment_that_mentions_the_companion_or_a_car_is_not_used():
    beats = _beats(16)
    moments = [{"start": 0.0, "end": 20.0, "strength": 9, "description": "a woman in an olive jacket", "notes": ""},
               {"start": 20.0, "end": 40.0, "strength": 5, "description": "swinging a bat", "notes": ""}]
    plan = plan_on_strikes({"c01": _clip(moments=moments, seconds=40.0)}, _signals(40.0), beats, [], "x", "y")
    assert all(m.start >= 19.9 for m in plan.pool)


def test_the_note_is_read_for_pace_look_and_what_to_show_back():
    assert pace_from_note("balanced", "one shot per beat please") == "every"
    assert pace_from_note("balanced", "Fast, punchy and bright") == "fast"
    assert pace_from_note("calm", "fast") == "calm"  # a deliberate pick wins
    assert pace_from_note("balanced", "Every time he hits the ball, it lands on a beat") == "balanced"
    assert look_from_note("bright and sunny", "vivid") == "bright"
    assert look_from_note("bright and sunny", "warm") == "warm"
    lines = read_note("Fast and bright. The bat's crack must land on a beat. Reuse shots. Fill the full length.", "fast")
    text = " ".join(lines)
    assert "Fast cuts" in text and "lands exactly on a beat" in text and "framed differently" in text
    assert "bright" in text and "full length" in text


def test_the_beat_made_for_a_video_is_a_clean_readable_beat(tmp_path):
    import soundfile as sf

    from vidosuper.music.analysis import analyze_music
    from vidosuper.music.house import make_beat

    info = make_beat(tmp_path / "b.wav", 20, "balanced")
    x, sr = sf.read(tmp_path / "b.wav")
    assert x.shape[1] == 2 and info["seconds"] >= 20 and float(np.abs(x).max()) <= 0.75
    analysis = analyze_music(tmp_path / "b.wav")
    assert abs(analysis.tempo - info["bpm"]) < 1.5 and analysis.strikes


def test_hit_led_every_cut_shows_a_real_hit_on_the_cut_and_repeats_look_different():
    beats = _beats(48, strikes=[k + 0.75 for k in range(1, 46, 3)])
    hits = [Hit("c01", t, -10.0) for t in (12.0, 25.0, 41.0)]
    facts = {"c01": _clip(impacts=[h.t for h in hits])}
    plan = plan_on_strikes(facts, _signals(), beats, hits, "Hits", "idea", hit_led=True)
    assert abs(sum(s.beats for s in plan.plan) - beats.target) < 1e-9
    pool = {m.key: m for m in plan.pool}
    assert all(pool[s.key].peak in (12.0, 25.0, 41.0) for s in plan.plan)  # every shot is a hit
    plain = [s for s in plan.plan if s.peak_beat == 0.0]
    assert len(plain) > 20 and all(s.crack_db is not None for s in plain)  # the contact is on the cut
    shown = {}
    for s in plain:
        shown.setdefault(pool[s.key].peak, []).append((s.zoom, tuple(s.zoom_center), s.flip))
    for variants in shown.values():
        assert len(set(variants)) > 3 and any(v[2] for v in variants)  # framed or mirrored differently each time
        assert all(v[0] <= 1.3 for v in variants)
    peaks = [pool[s.key].peak for s in plan.plan]
    assert all(a != b for a, b in zip(peaks, peaks[1:]) if a is not None)  # never the same hit twice running
    assert plan.plan[0].zoom == 1.0


def test_the_plan_schema_has_nothing_claude_code_refuses():
    """Claude Code's --json-schema is strict: tuples (prefixItems) made every video fail at the design step."""
    import json

    from vidosuper.studio.brain.backends import model_schema
    from vidosuper.explainer.script import VoiceScript
    from vidosuper.studio.brain.schemas import DesignPlan, Pitches, ProfileUpdate
    from vidosuper.studio.brain.core import ClipAnalysis

    for model in (DesignPlan, Pitches, ProfileUpdate, ClipAnalysis, VoiceScript):
        text = json.dumps(model_schema(model))
        assert "prefixItems" not in text, model.__name__


def test_deploy_gates_refuse_protected_files_and_off_switch(tmp_path):
    from vidosuper.studio.deploy import DeployRefused, deploy, protected_hits

    assert protected_hits(["src/vidosuper/studio/storage.py", "src/vidosuper/design.py", "scripts/improve.ps1"]) == [
        "src/vidosuper/studio/storage.py", "scripts/improve.ps1"]
    assert protected_hits(["src/vidosuper/explainer/draw.py", "tests/test_studio.py"]) == []

    class S:
        improve_deploy = False

    class Studio:
        s = S()

    with pytest.raises(DeployRefused, match="off"):
        deploy(Studio(), tmp_path, "improve/x", tmp_path, "python")


def test_requirements_are_read_from_the_note_and_checked_against_what_was_made():
    from vidosuper.studio import requirements as rq

    project = {"intent": "No music please. Every time he hits the ball it lands on a beat. Fill the full length. Bright.",
               "pace": "every", "length": 60.0, "kind": "montage"}
    assert rq.wants_no_music(project["intent"]) and not rq.wants_no_music("music is great")
    good = rq.montage(project, mean_shot=0.43, shots=139, hits_total=9, hits_used=9, seconds=59.8, music_playing=False,
                      look="bright")
    assert rq.unmet(good) == [] and len(good) == 5
    bad = rq.montage(project, mean_shot=2.2, shots=20, hits_total=9, hits_used=3, seconds=40.0, music_playing=True,
                     look="natural")
    assert len(rq.unmet(bad)) == 5
    talk = rq.explainer({"intent": "motion graphics, don't add music", "kind": "talk"}, scenes=3, callouts=10, cutaways=3,
                        captions=True, seconds=120, duration=124, music_playing=False, audio_kept=True)
    assert rq.unmet(talk) == []
    voice = rq.explainer({"intent": "", "kind": "voice"}, scenes=1, callouts=0, cutaways=0, captions=True, seconds=20,
                         duration=63, music_playing=False, audio_kept=True)
    assert len(rq.unmet(voice)) == 2


def test_a_talking_video_picked_in_the_montage_form_is_routed_to_talk_mode():
    from vidosuper.studio.requirements import is_talking_video_request as route

    note = "Its about me explaining undertone movie review Make it Instagram ready with motiongraphics in the back as i speak"
    assert route(note, 1) and not route(note, 3) and not route("", 1)
    assert route("Add motion graphics while I am talking", 1) and not route("fast cuts on the beat", 1)
