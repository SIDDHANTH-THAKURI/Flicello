"""The editor (studio/editing.py, studio/handedit.py): exact edits by hand in film seconds, made at once as drafts that
play straight away and render the same; undo and redo; the music edited by hand; framing keyframes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vidosuper.render.framing import motion_filter
from vidosuper.schemas import EDL, Segment
from vidosuper.studio import handedit
from vidosuper.studio.brain.schemas import DesignPlan, PlanGraphic, PlanShot, PoolMoment

from test_faces import _drain_all, studio_film  # noqa: F401 - a finished film to work on (a fixture)

PERIOD = 0.5


def _ctx():
    facts = {c: SimpleNamespace(fps=30.0, duration=20.0) for c in ("c1", "c2")}
    return SimpleNamespace(beats=SimpleNamespace(period=PERIOD), facts=facts)


def _plan(*lengths, graphics=()):
    pool = [PoolMoment(key="a", clip="c1", start=0, end=20), PoolMoment(key="b", clip="c2", start=0, end=20)]
    shots = [PlanShot(kind="cut", beats=n / PERIOD, key="ab"[i % 2], src_in=2.0 + i) for i, n in enumerate(lengths)]
    return DesignPlan(name="x", idea="x", pool=pool, plan=shots, graphics=list(graphics))


def _run(plan, *ops):
    return handedit.apply(plan, _ctx(), [handedit.EditOp.model_validate(o) for o in ops])


def _secs(plan):
    return [round(s.beats * PERIOD, 3) for s in plan.plan]


def test_a_trim_ripples_or_rolls_and_never_reaches_past_the_clip():
    p = _plan(2, 3, 2)
    assert _secs(_run(p, {"op": "trim", "shot": 1, "edge": "end", "seconds": -0.5}).plan) == [1.5, 3, 2]  # closes up
    rolled = _run(p, {"op": "trim", "shot": 1, "edge": "end", "seconds": 0.5, "ripple": False}).plan
    assert _secs(rolled) == [2.5, 2.5, 2] and rolled.plan[1].src_in == 3.5  # the next one starts later in its clip
    head = _run(p, {"op": "trim", "shot": 2, "edge": "start", "seconds": 1.0}).plan
    assert _secs(head)[1] == 4 and head.plan[1].src_in == 2.0  # a second earlier in its clip
    with pytest.raises(handedit.EditError, match="can't start earlier"):
        _run(p, {"op": "trim", "shot": 1, "edge": "start", "seconds": 5})
    with pytest.raises(handedit.EditError, match="shorter than"):
        _run(p, {"op": "trim", "shot": 1, "edge": "end", "seconds": -1.9})
    with pytest.raises(handedit.EditError, match="can't be that long"):
        _run(p, {"op": "trim", "shot": 1, "edge": "end", "seconds": 30})


def test_a_split_makes_two_shots_of_the_same_frames_and_the_keyframes_carry_on():
    p = _plan(2, 4, 2, graphics=[PlanGraphic(kind="title", shot=3, text="End"),
                                 PlanGraphic(kind="lowerthird", shot=2, text="Ana")])
    p.plan[1] = p.plan[1].model_copy(update={"keys": [[0, 1, .5, .5], [4, 2, .3, .3]]})
    done = _run(p, {"op": "split", "shot": 2, "seconds": 1.5})
    a, b = done.plan.plan[1], done.plan.plan[2]
    assert _secs(done.plan) == [2, 1.5, 2.5, 2] and b.src_in == a.src_in + 1.5 and b.transition == "cut"
    assert [g.shot for g in done.plan.graphics] == [4, 2]
    assert a.keys[0] == [0, 1, .5, .5] and a.keys[-1][0] == 1.5 and b.keys[0][0] == 0 and b.keys[-1][0] == 2.5
    assert abs(a.keys[-1][1] - b.keys[0][1]) < 1e-3  # the framing at the split carries straight on
    assert done.lines == ["Shot 2 is split at 1.50 s into shots 2 and 3"]


def test_a_delete_closes_up_or_lets_the_shot_before_hold_on():
    p = _plan(2, 3, 2, graphics=[PlanGraphic(kind="title", shot=2, text="Hi"),
                                 PlanGraphic(kind="callout", shot=2, want="boat", text="boat")])
    done = _run(p, {"op": "delete", "shot": 2})
    assert _secs(done.plan) == [2, 2] and [(g.kind, g.shot) for g in done.plan.graphics] == [("title", 2)]
    lifted = _run(p, {"op": "delete", "shot": 2, "ripple": False}).plan
    assert _secs(lifted) == [5, 2] and lifted.graphics[0].at == 2.0  # its words keep their moment


def test_a_moved_shot_takes_its_name_along_and_the_title_keeps_its_place():
    p = _plan(1, 2, 3, graphics=[PlanGraphic(kind="title", shot=1, text="Open"),
                                 PlanGraphic(kind="lowerthird", shot=1, text="Ana")])
    done = _run(p, {"op": "move", "shot": 1, "to": 3})
    assert _secs(done.plan) == [2, 3, 1] and {g.kind: g.shot for g in done.plan.graphics} == {"title": 1, "lowerthird": 3}


def test_speed_plays_the_same_frames_faster_or_slower():
    p = _plan(2, 2)
    done = _run(p, {"op": "speed", "shot": 1, "factor": 2})
    assert _secs(done.plan) == [1, 2] and done.plan.plan[0].speed == 2 and done.lines == ["Shot 1 plays at 2x (1.00 s now)"]
    assert _secs(_run(p, {"op": "speed", "shot": 1, "factor": 0.5, "ripple": False}).plan) == [2, 2]


def test_the_music_edit_stays_in_bounds_and_its_cuts_keep_their_moment():
    done = _run(_plan(2, 2, 2), {"op": "music", "music": {"slide": 99, "gain_db": -50, "points": [{"t": 9, "db": -99}],
                                                         "jumps": [{"at": 5, "to": 30}]}})
    m = done.plan.music
    assert (m.slide, m.gain_db, m.points[0].t, m.points[0].db, m.jumps[0].at) == (30, -30, 6, -60, 5)
    assert _run(done.plan, {"op": "delete", "shot": 1}).plan.music.jumps[0].at == 3  # the film closed up before it


def test_a_graphic_is_placed_restyled_and_taken_away_by_hand():
    p = _plan(2, 2, graphics=[PlanGraphic(kind="title", shot=1, text="Hi", style="rise")])
    g = _run(p, {"op": "graphic", "index": 0, "text": "Hello", "style": "slam", "scale": 1.5, "place": "top",
                 "at": 1, "until": 3}).plan.graphics[0]
    assert (g.text, g.style, g.scale, g.place, g.at, g.until) == ("Hello", "slam", 1.5, "top", 1, 3)
    with pytest.raises(handedit.EditError, match="A title can be"):
        _run(p, {"op": "graphic", "index": 0, "style": "karaoke"})
    assert _run(p, {"op": "graphic", "index": 0, "remove": True}).plan.graphics == []


def test_a_shot_laid_over_the_picture_fits_its_clip_and_keeps_its_moment():
    p = _plan(2, 2, 2)
    done = _run(p, {"op": "over", "index": -1, "clip": "c2", "src_in": 19.0, "at": 1, "until": 3})
    o = done.plan.over[0]
    assert (o.clip, o.at, o.until, o.src_in) == ("c2", 1, 3, 18.0)  # pulled back so its 2 s fit inside the clip
    closed = _run(done.plan, {"op": "delete", "shot": 1}).plan.over[0]
    assert (closed.at, closed.until) == (0, 1)  # the stretch it covered in the deleted shot goes with it
    moved = _run(done.plan, {"op": "over", "index": 0, "at": 2.5, "until": 4}).plan.over[0]
    assert (moved.at, moved.until, moved.clip) == (2.5, 4, "c2")
    assert _run(done.plan, {"op": "over", "index": 0, "remove": True}).plan.over == []
    with pytest.raises(handedit.EditError, match="Pick a clip"):
        _run(p, {"op": "over", "index": -1, "clip": "c9", "at": 0, "until": 1})


def test_keyframes_render_as_an_eased_zoom_and_pan():
    seg = Segment(id="s1", clip_id="a", src_in=0, src_out=3, keys=[[0, 1, .5, .5], [2, 1.6, .3, .4]])
    vf = motion_filter(seg, 1080, 1920, 3.0)
    assert "cos(PI*((t-0.0000)/2.0000))" in vf and "*iw-1080/2" in vf and "eval=frame" in vf


def test_cello_and_the_work_agent_cut_by_hand_the_same_way(studio_film):
    from vidosuper.studio import cello

    studio, client, csrf, pid, work = studio_film
    project = studio.db.project(pid)
    user = studio.user(project["user_id"])
    out = cello.act(studio, user, project, "timeline", {"ops": [{"op": "split", "shot": 1, "seconds": 0.6},
                                                                {"op": "speed", "shot": 2, "factor": 2}]})
    assert out["result"].startswith("Made, playing as the draft: Shot 1 is split") and out["ui"] == {"reload": True}
    shots = client.get(f"/api/p/{pid}/editor").json()["shots"]
    assert shots[1]["rate"] == 2 and shots[0]["end"] == pytest.approx(0.6, abs=0.05)
    with pytest.raises(ValueError, match="no shot 99"):
        cello.act(studio, user, studio.db.project(pid), "timeline", {"ops": [{"op": "delete", "shot": 99}]})


def test_the_editor_cuts_by_hand_at_once_undoes_and_the_render_follows(studio_film):
    studio, client, csrf, pid, work = studio_film
    assert 'data-editor="' in client.get(f"/p/{pid}/edit").text
    v = client.get(f"/api/p/{pid}/editor").json()
    assert v["which"] == "1" and len(v["shots"]) >= 2 and v["music"]["peaks"] and v["music"]["beats"] and v["play"]["shots"]
    first, second = v["shots"][0], v["shots"][1]
    at = round(0.6 * (second["end"] - second["start"]), 2)
    r = client.post(f"/api/p/{pid}/editor", headers={"X-CSRF": csrf}, json={"ops": [
        {"op": "trim", "shot": 1, "edge": "end", "seconds": -0.3},
        {"op": "volume", "shot": 1, "gain_db": -6},
        {"op": "keys", "shot": 2, "keys": [[0, 1, .5, .5], [at, 1.6, .3, .4]]},
        {"op": "music", "music": {"gain_db": -3, "jumps": [{"at": 2.0, "to": 4.0}]}},
        {"op": "over", "index": -1, "clip": v["footage"][-1]["short"], "src_in": 0.0, "at": 1.0, "until": 2.2}]})
    assert r.status_code == 200, r.text
    v2 = r.json()
    assert v2["which"] == "draft" and v2["undo"] and not v2["redo"] and len(v2["said"]) >= 4
    assert v2["shots"][0]["end"] == pytest.approx(first["end"] - 0.3, abs=0.06)
    draft = studio.draft_of(studio.db.project(pid))
    edl = EDL.model_validate_json((work / "out" / draft["edl"]).read_text(encoding="utf-8"))
    assert (edl.segments[0].audio.mode, edl.segments[0].audio.gain_db) == ("original", -6)
    assert edl.segments[1].keys[-1] == [at, 1.6, 0.3, 0.4]
    assert [(j.at, j.to) for j in edl.music.jumps] == [(2.0, 4.0)] and edl.music.gain_db <= -3
    assert v2["play"]["shots"][1]["keys"] and v2["play"]["music"]["jumps"]  # the preview plays the same
    assert [(o["start"], o["end"]) for o in v2["play"]["over"]] == [(1.0, 2.2)] and v2["over"][0]["short"]
    assert [(o.at, o.until) for o in edl.over] == [(1.0, 2.2)]
    r = client.post(f"/api/p/{pid}/editor", headers={"X-CSRF": csrf},
                    json={"ops": [{"op": "trim", "shot": 1, "edge": "start", "seconds": 99}]})
    assert r.status_code == 400 and "earlier" in r.json()["detail"]  # said plainly; nothing made
    assert client.post(f"/api/p/{pid}/editor", json={"ops": []}, headers={"X-CSRF": "x"}).status_code == 403
    v3 = client.post(f"/api/p/{pid}/editor/undo", headers={"X-CSRF": csrf}).json()
    assert v3["which"] == "1" and v3["redo"] and not v3["undo"]
    assert client.post(f"/api/p/{pid}/editor/redo", headers={"X-CSRF": csrf}).json()["which"] == "draft"
    assert client.post(f"/api/p/{pid}/editor/make", headers={"X-CSRF": csrf}).json()["making"]
    _drain_all(studio)
    project = studio.db.project(pid)
    assert project["version"] == 2 and studio.draft_of(project) is None
    made = EDL.model_validate_json((work / "out" / "v2-16x9.edl.json").read_text(encoding="utf-8"))
    assert made.segments[1].keys and [(j.at, j.to) for j in made.music.jumps] == [(2.0, 4.0)]
    assert made.segments[0].audio.gain_db == -6 and (work / "out" / "v2-16x9.mp4").exists() and made.over
    # the laid-over shot is what the made film shows there (not the film's own shot under it)
    import cv2
    import numpy as np

    from vidosuper.perceive.contact_sheet import grab_frames
    from vidosuper.workspace import Project

    clip = Project(work / "clips", studio.cfg).clips()[made.over[0].clip_id]
    ((_, shown),) = grab_frames(work / "out" / "v2-16x9.mp4", [1.6])
    ((_, under),) = grab_frames(work / "out" / "v1-16x9.mp4", [1.6])
    ((_, own),) = grab_frames(Path(clip.proxy or clip.path), [made.over[0].src_in + 0.6])

    def look(img):
        return cv2.resize(img, (32, 18), interpolation=cv2.INTER_AREA).astype(float)

    assert np.abs(look(shown) - look(own)).mean() < np.abs(look(shown) - look(under)).mean()
