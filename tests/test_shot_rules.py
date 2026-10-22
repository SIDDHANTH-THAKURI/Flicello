"""What the Aura Farming film (21 Oct, 19 versions) taught: one place in one stretch of the film, nobody in shot when
they said so, beat sync as cuts on the hits after the drop, a landmark cut whole, and their own lengths over the
pacing limits."""

from types import SimpleNamespace as NS

import pytest

from vidosuper.studio.brain.context import BeatMap, ClipFacts, people_in, place_of
from vidosuper.studio.brain.core import StoryContext, _dense_problems, check_plan
from vidosuper.studio.brain.schemas import DesignPlan, PlanShot, PoolMoment

PLACES = {"c01": "Sydney Opera House", "c02": "Sydney Harbour Bridge", "c03": "", "c04": "Sydney Opera House",
          "c05": "the wharf"}
PEOPLE = {"c01": "none", "c02": "none", "c03": "near", "c04": "none", "c05": "far"}


def _ctx(**flags) -> StoryContext:
    facts = {}
    for short, place in PLACES.items():
        moments = [{"start": 0.5, "end": 5.5, "peak": None, "kind": "scenic", "description": f"{short} view", "hook": 6,
                    "strength": 7, "best_use": "highlight", "sound": "either", "place": place, "people": PEOPLE[short],
                    "best_at": 3.0}]
        facts[short] = ClipFacts(short, f"{short}-x", 8.0, 30.0, False, [], moments)
    beats = BeatMap(first=0, t0=0.0, period=0.5, available=64, target=32, drop=16, sections=[],
                    booms=[float(b) for b in range(0, 33)])  # an 808 on every beat
    ctx = StoryContext(catalog={"clips": []}, facts=facts, song=None, beats=beats, shapes=["16:9"], intent="", rules=[])
    for k, v in flags.items():
        setattr(ctx, k, v)
    return ctx


def _plan(keys_beats, kinds=None) -> DesignPlan:
    pool = [PoolMoment(key=k, clip=k, start=1.0, end=5.0) for k in PLACES]
    kinds = kinds or {}
    return DesignPlan(name="t", idea="t", pool=pool, plan=[
        PlanShot(kind=kinds.get(i, "cut"), beats=b, key=k) for i, (k, b) in enumerate(keys_beats)])


def test_their_words_become_rules():
    ctx = _ctx()
    ctx.read_words("No internal audio, no faces, no duplicate scenes cuts", "hold that first beat for like 5-6 sec")
    assert ctx.no_people and ctx.no_repeats and ctx.hold_ok and not ctx.sync
    quiet = _ctx()
    quiet.read_words("a calm sunset film with friends")
    assert not (quiet.no_people or quiet.no_repeats or quiet.hold_ok)


def test_the_same_place_twice_apart_is_a_repeat_side_by_side_is_a_scene():
    apart = _plan([("c01", 8), ("c02", 8), ("c03", 8), ("c04", 8)])  # the Opera House, then again two shots later
    side = _plan([("c01", 8), ("c04", 8), ("c02", 8), ("c03", 8)])
    assert any("Sydney Opera House again" in p for p in check_plan(apart, _ctx()))
    assert not any("again" in p for p in check_plan(side, _ctx()))
    assert any("again" in p for p in check_plan(apart, _ctx(no_repeats=True), taste=False))  # they said so: a must
    assert not any("again" in p for p in check_plan(apart, _ctx(), taste=False))  # otherwise a matter of taste
    assert not any("again" in p for p in check_plan(apart, _ctx(allow_reuse=True)))


def test_nobody_in_shot_when_they_said_no_faces():
    plan = _plan([("c01", 8), ("c03", 8), ("c05", 8), ("c02", 8)])
    assert any("has people in view" in p and "c03" in p for p in check_plan(plan, _ctx(no_people=True), taste=False))
    assert not any("people in view" in p for p in check_plan(plan, _ctx(), taste=False))
    assert not any("'c05'" in p and "people" in p for p in check_plan(plan, _ctx(no_people=True)))  # far-off figures


def test_logs_from_before_are_read_for_people_and_places():
    assert people_in({"description": "A man in a black t-shirt leans on the rail"}) == "near"
    assert people_in({"description": "Tiny distant figures on the quay"}) == "far"
    assert people_in({"description": "The skyline at dusk"}) == "unknown"
    assert people_in({"people": "face", "description": "x"}) == "face"
    assert place_of({"place": "The Sydney Opera House"}) == place_of({"place": "sydney  opera house"})


def test_their_own_lengths_win_over_the_pacing_limits():
    long_first = _plan([("c01", 12), ("c02", 4), ("c03", 8), ("c05", 8)])
    assert any("first shot lasts" in p for p in check_plan(long_first, _ctx()))
    assert not any("first shot lasts" in p or "keep shots under" in p for p in check_plan(long_first, _ctx(hold_ok=True)))


def test_after_the_drop_beat_sync_means_a_cut_on_the_hits():
    beats = _ctx().beats  # an 808 on every beat; the drop at beat 16
    spread = _plan([("c01", 8), ("c02", 8), ("c03", 4), ("c05", 4), ("c04", 4), ("c01", 4)])  # 4-beat shots after it
    assert _dense_problems(spread, beats)
    cut = _plan([("c01", 8), ("c02", 8), ("c03", 6)] + [("c05", 1)] * 10)  # the drop held, then a cut per hit
    assert not _dense_problems(cut, beats)
    every_bar = NS(drop=16, target=32, booms=[16.0, 20.0, 24.0, 28.0])  # an 808 every bar: a cut every bar is in sync
    assert not _dense_problems(spread, every_bar)


def test_a_landmark_shot_is_cut_around_where_it_is_whole():
    from vidosuper.design import Moment, _Footage

    class Footage(_Footage):
        def __init__(self):
            pass

        def clip(self, short):
            return NS(duration=20.0)

        def peak_of(self, m, refine=False):
            return m.peak if m.peak is not None else 9.9  # (the busiest frame, which a scenic shot must not follow)

        def steadiest(self, short, lo, hi, need):
            return lo

    f = Footage()
    pan = Moment(clip="c02", start=4.0, end=10.0, hero=8.0)
    assert f.source_in(pan, 2.0, None) == pytest.approx(8.0 - 0.9)  # the bridge whole a little before the middle
    assert f.source_in(pan, 2.0, 0.15) == pytest.approx(7.1)  # a short cut: the same, not the busiest frame
    action = Moment(clip="c02", start=4.0, end=10.0, peak=6.0, hero=8.0)
    assert f.source_in(action, 2.0, 0.15) == pytest.approx(5.85)  # a real action peak still leads
    assert f.source_in(Moment(clip="c02", start=4.0, end=10.0), 2.0, None) == 4.0
