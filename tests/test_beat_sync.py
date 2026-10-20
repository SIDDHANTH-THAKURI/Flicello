"""Beat sync as a person means it (the 20 Oct ferry film): bars start on the 808, the AI is told where the hits are,
and 'beat sync' said once is a rule the plan must keep."""

from types import SimpleNamespace as NS

import numpy as np
import pytest

from vidosuper.music.analysis import _clear_boom_phase, analyze_music
from vidosuper.studio.brain.context import SYNC_WORDS, BeatMap
from vidosuper.studio.brain.core import _sync_problems, plan_beats, sync_snap
from vidosuper.studio.brain.schemas import DesignPlan, PlanShot, PoolMoment


def test_a_clear_808_on_one_beat_of_four_sets_the_bar():
    levels = np.array([5, 900, 6, 4] * 6, dtype=float)  # heavy on the second beat of the grid
    assert _clear_boom_phase(levels) == 1
    assert _clear_boom_phase(np.array([800, 5, 700, 6] * 6, dtype=float)) is None  # a pop kick on 1 and 3: not clear


def test_bars_follow_the_808_not_the_pads(tmp_path):
    """A phonk-like loop: hi-hats on every beat, pads changing a beat early, an 808 on the real bar start."""
    import soundfile as sf

    sr, bpm = 22050, 100
    period = 60 / bpm
    n = int(sr * 32 * period)
    t = np.arange(n) / sr
    y = np.zeros(n)
    rng = np.random.default_rng(1)
    for k in range(32):
        a = int(k * period * sr)
        y[a:a + 600] += 0.25 * rng.standard_normal(600) * np.exp(-np.arange(600) / 120)  # hat on every beat
        if k % 4 == 1:  # the 808 on the bar start (beat 1 of the grid)
            m = int(0.5 * sr)
            y[a:a + m] += 0.9 * np.sin(2 * np.pi * 50 * np.arange(m) / sr) * np.exp(-np.arange(m) / (0.2 * sr))
        if k % 4 == 0:  # pads change chord a beat early
            seg = slice(a, min(n, a + int(4 * period * sr)))
            f = [220, 247, 262, 294][(k // 4) % 4]
            y[seg] += 0.08 * np.sin(2 * np.pi * f * t[seg])
    path = tmp_path / "loop.wav"
    sf.write(path, y / np.abs(y).max() * 0.8, sr)
    a = analyze_music(path)
    period_a = float(np.median(np.diff(a.beats)))
    booms = np.asarray(a.booms)
    assert len(booms) >= 5
    on = [np.min(np.abs(booms - d)) < 0.06 for d in a.downbeats if d > 1.0]
    assert np.mean(on) > 0.8, (a.downbeats[:6], a.booms[:6], period_a)


@pytest.mark.parametrize("text,want", [
    ("Beat sync", True), ("Beat sync was poor,", True), ("make it beat-synced", True),
    ("cuts should happen on the beats", True), ("sync it to the music", True), ("transitions on the 808s", True),
    ("No face, no internal audio, no duplicate shots", False), ("a calm sunset film", False),
])
def test_beat_sync_is_heard_in_their_words(text, want):
    assert bool(SYNC_WORDS.search(text)) is want


def _beats():
    return BeatMap(first=0, t0=0.0, period=0.6, available=60, target=48, drop=36, sections=[], strikes=[],
                   booms=[8.0, 12.0, 16.0, 20.0, 24.0, 28.0, 32.0, 36.0, 40.0, 42.0, 44.0, 46.0])


def _plan(shots):
    pool = [PoolMoment(key=f"k{i}", clip="c01", start=i * 5.0, end=i * 5.0 + 4.0) for i in range(len(shots))]
    return DesignPlan(name="x", idea="x", pool=pool, plan=[
        PlanShot(kind=k, beats=b, key=f"k{i}", transition=tr, transition_beats=1.0 if tr != "cut" else 0.0)
        for i, (k, b, tr) in enumerate(shots)])


def test_the_ferry_plan_is_caught_and_snapped_onto_the_hits(monkeypatch):
    """The plan that shipped: dissolves and cuts between the 808s. Caught, and snapped when the AI still missed."""
    import vidosuper.studio.brain.core as core

    shipped = _plan([("hold", 6, "cut"), ("cut", 2, "dissolve"), ("hold", 4, "cut"), ("hold", 6, "dissolve"),
                     ("hold", 6, "dissolve"), ("hold", 6, "cut"), ("cut", 4, "dissolve"), ("cut", 4, "cut"),
                     ("cut", 2, "dissolve"), ("hold", 8, "cut")])
    bm = _beats()
    problems = _sync_problems(shipped, bm)
    assert any("between the music's hits" in p for p in problems) and any("dissolve" in p for p in problems)

    monkeypatch.setattr(core, "check_plan", lambda p, c, taste=True: [])  # footage checks need a real catalog
    ctx = NS(beats=bm, sync=True)
    out = sync_snap(shipped, ctx)
    assert plan_beats(out) == 48 and not _sync_problems(out, bm)
    assert all(s.transition == "cut" for s in out.plan)
    assert sync_snap(shipped, NS(beats=bm, sync=False)) is shipped  # not asked: the AI's plan stands


def test_a_stutter_keeps_its_rhythm_when_snapped(monkeypatch):
    import vidosuper.studio.brain.core as core

    monkeypatch.setattr(core, "check_plan", lambda p, c, taste=True: [])
    p = _plan([("hold", 7, "cut"), ("stutter", 0.5, "cut"), ("hold", 6, "dissolve"), ("hold", 31, "cut")])
    p.plan[1] = p.plan[1].model_copy(update={"count": 8})  # 4 beats of stutter
    out = sync_snap(p, NS(beats=_beats(), sync=True))
    st = [s for s in out.plan if s.kind == "stutter"]
    assert st and st[0].count == 8 and st[0].beats == 0.5 and plan_beats(out) == 48


def test_a_808_every_bar_is_never_called_beatless():
    from vidosuper.music.analysis import MusicAnalysis

    a = MusicAnalysis(path="x", fingerprint="x", duration=32.0, tempo=100, beats=list(np.arange(0, 32, 0.6)),
                      downbeats=list(np.arange(0, 32, 2.4)), energy=[0.5] * 32, sections=[], pulse=[1.4, 1.0, 1.0, 1.5],
                      booms=list(np.arange(9.6, 24, 2.4)))
    assert a.weak_beat_stretches() == []  # the hats between the hits fooled the clarity measure once
    a.booms = []
    assert a.weak_beat_stretches() == [(8.0, 24.0)]
