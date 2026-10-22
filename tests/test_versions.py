"""Changes to a finished film touch exactly the shots they name (versions.py): the Aura Farming film (21 Oct) took 19
versions because every change re-planned the whole film."""

from types import SimpleNamespace as NS

import pytest

from vidosuper.studio import versions
from vidosuper.studio.brain.schemas import ChangeOp, ChangeRequest, DesignPlan, PlanShot, PoolMoment, RangePlan


def _facts():
    moments = [{"start": float(a), "end": float(a + 4), "description": f"moment {k + 1}", "strength": 6, "hook": 1,
                "peak": float(a + 2) if k % 2 else None} for k, a in enumerate(range(0, 40, 5))]
    return {"c01": NS(short="c01", fps=60.0, moments=moments, clip_id="c01-x", duration=45.0),
            "c02": NS(short="c02", fps=30.0, moments=moments[:3], clip_id="c02-y", duration=20.0)}


def _ctx(drop=8):
    return NS(beats=NS(period=0.5, drop=drop, booms=[0.0, 4.0, 8.0], target=24), facts=_facts(), bans=[])


def _plan():
    """Six shots of 4 beats (one a 2x2 stutter), each pinned to the frames a version showed."""
    pool = [PoolMoment(key=f"m{k}", clip="c01", start=k * 5.0, end=k * 5.0 + 4) for k in range(6)]
    shots = [PlanShot(kind="hold", beats=4, key="m0", src_in=0.5),
             PlanShot(kind="cut", beats=4, key="m1", src_in=5.2),
             PlanShot(kind="hit", beats=4, key="m2", src_in=10.4),  # starts on the drop (beat 8)
             PlanShot(kind="cut", beats=4, key="m3", src_in=15.0),
             PlanShot(kind="stutter", beats=2, count=2, key="m4", src_in=20.0),
             PlanShot(kind="cut", beats=4, key="m5", src_in=25.0)]
    return DesignPlan(name="x", idea="x", pool=pool, plan=shots, start_at=0.11)


def _keys(plan):
    return [s.key for s in plan.plan]


def test_slot_ids_follow_the_stutter_pieces():
    assert versions.slot_ids(_plan()) == [["s01"], ["s02"], ["s03"], ["s04"], ["s05", "s06"], ["s07"]]


def test_a_version_is_pinned_to_the_frames_its_edit_list_shows():
    plan = _plan().model_copy(update={"plan": [s.model_copy(update={"src_in": None}) for s in _plan().plan],
                                      "start_at": None})
    segs = [{"id": f"s{k:02d}", "src_in": 1.0 + k, "zoom": 1.0, "zoom_center": [0.5, 0.5], "flip": k == 3,
             "frame_center": [0.5, 0.5]} for k in range(1, 8)]
    tall = {"segments": [dict(x, frame_center=[0.3, 0.5]) for x in segs]}
    out = versions.pinned(plan, {"segments": segs, "music": {"path": "song.mp3", "start": 1.82}}, tall)
    assert [s.src_in for s in out.plan] == [2.0, 3.0, 4.0, 5.0, 6.0, 8.0]  # a stutter: its first piece
    assert out.plan[2].flip and out.plan[0].frame_x == 0.3 and out.start_at == 1.82
    assert versions.pinned(plan, None) is plan


def test_untouched_shots_stay_exactly_as_they_were():
    plan, ctx = _plan(), _ctx()
    done = versions.apply(plan, ctx, [ChangeOp(op="longer", shot=4, beats=1)])
    new = done.plan
    assert versions.span(new.plan[3]) == 5 and versions.span(new.plan[4]) == 3  # the cut after 4 moved a beat
    for k in (0, 1, 2, 5):
        assert new.plan[k] == plan.plan[k]  # every field, frames included
    assert sum(versions.span(s) for s in new.plan) == sum(versions.span(s) for s in plan.plan)
    assert done.touched == {3, 4}


def test_longer_takes_from_the_next_shot_whose_later_frames_stay_in_place():
    plan, ctx = _plan(), _ctx()
    done = versions.apply(plan, ctx, [ChangeOp(op="longer", shot=1, beats=1)])
    a, b = done.plan.plan[0], done.plan.plan[1]
    assert (a.beats, a.src_in) == (5, 0.5)  # the same first frame, plays a beat longer
    assert b.beats == 3 and b.src_in == pytest.approx(5.2 + 1 * 0.5 * 1.0)  # a roll: starts a beat later in its clip
    back = versions.apply(plan, ctx, [ChangeOp(op="longer", shot=2, beats=1, side="previous")]).plan
    assert back.plan[0].beats == 3 and back.plan[1].beats == 5 and back.plan[1].src_in == pytest.approx(5.2 - 0.5)


def test_longer_than_the_next_shot_has_eats_it():
    plan, ctx = _plan(), _ctx()
    done = versions.apply(plan, ctx, [ChangeOp(op="longer", shot=1, beats=6)])
    assert _keys(done.plan) == ["m0", "m2", "m3", "m4", "m5"] and done.plan.plan[0].beats == 10
    assert done.plan.plan[1].beats == 2 and "made room" in done.lines[0]


def test_remove_gives_its_time_to_the_shot_before_and_never_again_rules_the_scene_out():
    plan, ctx = _plan(), _ctx()
    done = versions.apply(plan, ctx, [ChangeOp(op="remove", shot=4, never_again=True)], labels={4: "the man in black"})
    assert _keys(done.plan) == ["m0", "m1", "m2", "m4", "m5"] and done.plan.plan[2].beats == 8
    assert done.bans and done.bans[0]["clip"] == "c01" and "man in black" in done.bans[0]["what"]
    first = versions.apply(plan, ctx, [ChangeOp(op="remove", shot=1)]).plan  # the first: the next one starts earlier
    assert first.plan[0].key == "m1" and first.plan[0].beats == 8 and first.plan[0].src_in == pytest.approx(5.2 - 2.0)


def test_swap_trades_whole_shots_but_the_drop_keeps_its_impact():
    plan, ctx = _plan(), _ctx()
    done = versions.apply(plan, ctx, [ChangeOp(op="swap", shot=1, other=2)]).plan
    assert done.plan[0] == plan.plan[1] and done.plan[1] == plan.plan[0]  # each with its own length and look
    drop = versions.apply(plan, ctx, [ChangeOp(op="swap", shot=3, other=4)]).plan  # shot 3 is on the drop
    assert drop.plan[2].kind == "hit" and drop.plan[2].key == "m3" and drop.plan[2].src_in == 15.0
    assert drop.plan[3].kind == "cut" and drop.plan[3].key == "m2"


def test_move_puts_a_shot_after_another_and_numbers_mean_the_version_they_watched():
    plan, ctx = _plan(), _ctx(drop=None)
    done = versions.apply(plan, ctx, [ChangeOp(op="move", shot=1, other=3)]).plan
    assert _keys(done) == ["m1", "m2", "m0", "m3", "m4", "m5"]
    two = versions.apply(plan, ctx, [ChangeOp(op="remove", shot=2), ChangeOp(op="swap", shot=5, other=6)]).plan
    assert _keys(two) == ["m0", "m2", "m3", "m5", "m4"]  # 5 and 6 are still the shots they saw as 5 and 6


def test_replace_with_a_moment_or_a_shot_from_an_earlier_version():
    plan, ctx = _plan(), _ctx()
    done = versions.apply(plan, ctx, [ChangeOp(op="replace", shot=2, moment="c02#2")])
    s = done.plan.plan[1]
    pool = {m.key: m for m in done.plan.pool}
    assert pool[s.key].clip == "c02" and pool[s.key].start == 5.0 and s.src_in is None and s.beats == 4
    old = DesignPlan(name="v3", idea="x", pool=[PoolMoment(key="opera", clip="c01", start=30, end=34)],
                     plan=[PlanShot(kind="hold", beats=6, key="opera", src_in=31.25, zoom=1.0)])
    back = versions.apply(plan, ctx, [ChangeOp(op="replace", shot=2, from_version=3, from_shot=1)], {3: old})
    s = back.plan.plan[1]
    assert {m.key: m for m in back.plan.pool}[s.key].start == 30 and s.src_in == 31.25 and s.beats == 4
    assert "exactly as it was" in back.lines[0]
    gone = versions.apply(plan, ctx, [ChangeOp(op="replace", shot=2, from_version=9, from_shot=1)], {3: old})
    assert gone.skipped and gone.plan.plan == plan.plan


def test_a_wish_without_a_moment_and_redo_go_to_the_editor_for_those_shots_only():
    plan, ctx = _plan(), _ctx()
    done = versions.apply(plan, ctx, [ChangeOp(op="redo", shot=4, other=6, want="fast cuts on every beat"),
                                      ChangeOp(op="replace", shot=1, want="the bridge, far away")])
    assert done.redo == [(0, 0, "the bridge, far away"), (3, 5, "fast cuts on every beat")]
    assert done.plan.plan[1:3] == plan.plan[1:3]


def test_speed_changes_how_a_shot_plays():
    plan, ctx = _plan(), _ctx()
    done = versions.apply(plan, ctx, [ChangeOp(op="speed", shot=2, kind="stutter"),
                                      ChangeOp(op="speed", shot=5, kind="cut"), ChangeOp(op="speed", shot=6, kind="ramp")])
    a, b, c = done.plan.plan[1], done.plan.plan[4], done.plan.plan[5]
    assert a.kind == "stutter" and a.count * a.beats == 4 and a.src_in == 5.2
    assert b.kind == "cut" and b.beats == 4 and b.count == 1 and c.kind == "ramp" and c.src_in is None


def test_bad_numbers_are_skipped_not_guessed():
    plan, ctx = _plan(), _ctx()
    done = versions.apply(plan, ctx, [ChangeOp(op="remove", shot=9), ChangeOp(op="swap", shot=1, other=12),
                                      ChangeOp(op="remove", shot=2), ChangeOp(op="longer", shot=2, beats=1)])
    assert len(done.skipped) == 3 and _keys(done.plan) == ["m0", "m2", "m3", "m4", "m5"]


def test_merge_range_puts_new_shots_in_and_clears_their_pins():
    plan = _plan()
    part = RangePlan(pool=[PoolMoment(key="m1", clip="c02", start=1, end=5), PoolMoment(key="new", clip="c02", start=6, end=9)],
                     plan=[PlanShot(kind="cut", beats=2, key="m1", src_in=3.0), PlanShot(kind="cut", beats=2, key="new")])
    out = versions.merge_range(plan, 3, 3, part)
    keys = _keys(out)
    assert keys[:3] == ["m0", "m1", "m2"] and keys[3] != "m1" and keys[4] == "new" and keys[5:] == ["m4", "m5"]
    assert out.plan[3].src_in is None and out.plan[0] == plan.plan[0]
    assert versions.range_beats(plan, 3, 4) == (12, 8)


@pytest.mark.parametrize("text,want", [
    ("Swap 9 th and 10th clip", [("swap", 9, 10)]),
    ("Remove 11th and 12th its repetetive", [("remove", 11, None), ("remove", 12, None)]),
    ("Extend clip 8 to one more beat", [("longer", 8, None)]),
    ("replace 3rd and 5th clip", [("replace", 3, None), ("replace", 5, None)]),
    ("make it better", []),
])
def test_the_exact_phrasings_are_read_without_the_ai(text, want):
    r = versions.read_simple(text, 21)
    assert [(o.op, o.shot, o.other) for o in r.ops] == want
    assert bool(r.unclear) is (not want)


def test_a_read_request_is_checked_before_anyone_sees_it():
    facts = _facts()
    bad = ChangeRequest(ops=[ChangeOp(op="remove", shot=30), ChangeOp(op="swap", shot=2),
                             ChangeOp(op="replace", shot=3, moment="c09#1"), ChangeOp(op="longer", shot=4),
                             ChangeOp(op="replace", shot=5, from_version=7, from_shot=40)], said=["a"])
    problems = versions.check_request(bad, 21, facts, {7: 20})
    assert len(problems) == 6
    ok = ChangeRequest(ops=[ChangeOp(op="replace", shot=3, moment="c01#2"), ChangeOp(op="longer", shot=4, beats=2)],
                       said=["x", "y"])
    assert versions.check_request(ok, 21, facts, {}) == []


def test_changes_picked_on_the_shot_strip():
    form = {"remove_3": "1", "never_3": "1", "longer_2": "1", "swap_5": "6", "replace_1": "c02#1", "speed_4": "hold",
            "move_6": "x", "shorter_6": "abc", "replace_2": "any"}
    ops, said = versions.direct_ops(form, 6, {3: "the guy"})
    got = [(o.op, o.shot, o.other, o.beats, o.moment, o.never_again) for o in ops]
    assert ("remove", 3, None, None, None, True) in got and ("longer", 2, None, 1.0, None, False) in got
    assert ("swap", 5, 6, None, None, False) in got and ("replace", 1, None, None, "c02#1", False) in got
    assert ("speed", 4, None, None, None, False) in got and len(ops) == 6 and len(said) == 6
    assert any(o.op == "replace" and o.shot == 2 and o.want for o in ops)


def test_the_change_reader_is_told_the_shots_hits_and_what_else_there_is():
    cards = [{"n": 1, "start": 0.0, "end": 2.0, "beats": 4, "kind": "hold", "count": 1, "what": "towers", "moment": "c01#1",
              "slow": True, "drop": False},
             {"n": 2, "start": 2.0, "end": 4.0, "beats": 4, "kind": "stutter", "count": 4, "what": "wake", "moment": "c01#2",
              "drop": True}]
    text = versions.request_text(16, cards, NS(period=0.5, booms=[0, 4], drop=4), [{"id": "c02#1", "seconds": 4, "what": "bridge"}],
                                 [(15, 8, [(1, "c01#3", "opera centred")])], [{"clip": "c01", "start": 5, "end": 9, "what": "guy"}],
                                 "swap 1 and 2", ["hold the drop"])
    for bit in ("1. 0:00.0-0:02.0 · 4 beats · slow look in slow motion · towers [c01#1]", "stutter x4", "starts on the drop",
                "c02#1 (4 s): bridge", "version 15 (they liked it 8/10): 1 [c01#3] opera centred", "guy", "swap 1 and 2"):
        assert bit in text, bit
