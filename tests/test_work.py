"""Cello's hands (vidosuper.work): the work sessions survive a restart and hand over cleanly, Flicello's own questions
are asked once and carried out, and the agent can rewrite the whole plan of a film (checked, played as a draft),
including a caption or a title made bigger, smaller or moved."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

from vidosuper import motion
from vidosuper.edit import build_timeline
from vidosuper.ffmpeg import ffmpeg_bin
from vidosuper.motion import place
from vidosuper.schemas import EDL, CaptionWord, EditMeta, MotionGraphic, Segment
from vidosuper.studio import cello, versions
from vidosuper.studio.brain.schemas import ChangeOp, ChangeRequest, DesignPlan, PlanGraphic
from vidosuper.studio.service import ActionError, Studio
from vidosuper.studio.settings import load_settings
from vidosuper.studio.web.app import create_app
from vidosuper.work import asks, scenes_in_film

from conftest import person
from test_faces import _drain_all, studio_film  # noqa: F401 - a finished film to work on (a fixture)


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDO_STUDIO_BRAIN", raising=False)
    s = load_settings(tmp_path / "home", {"brain": "local", "owner_brain": "local"})
    s.limits.min_free_gb = 0.0
    return Studio(s)


def _person(studio: Studio, name: str = "Ana") -> dict:
    return person(studio.db)


def test_a_graphic_gets_bigger_smaller_or_moves_and_keeps_its_style():
    marks = [PlanGraphic(kind="captions", style="box"), PlanGraphic(kind="title", shot=2, text="Harbour", style="slam")]
    plan = DesignPlan(name="x", idea="x", pool=[], plan=[])

    def run(op: ChangeOp):
        done = versions.Applied(plan=plan)
        return versions.graphic_op(op, marks, 6, done), done

    out, done = run(ChangeOp(op="captions", shot=1, scale=1.4, place="top"))
    caps = next(g for g in out if g.kind == "captions")
    assert (caps.style, caps.scale, caps.place) == ("box", 1.4, "top")  # their style stays
    assert done.lines == ["The captions are bigger, at the top"]
    out, done = run(ChangeOp(op="title", shot=5, scale=0.8))  # no title on shot 5: the one there is
    title = next(g for g in out if g.kind == "title")
    assert (title.text, title.style, title.shot, title.scale) == ("Harbour", "slam", 2, 0.8)
    assert done.lines == ["The title is smaller (shot 2)"]
    out, done = run(ChangeOp(op="lowerthird", shot=1, place="top"))
    assert out == marks and done.skipped == ["there is no name to move"]
    out, done = run(ChangeOp(op="title", shot=3, text="Kiama", scale=9))  # kept to what still fits
    assert out[-1].scale == 3.0 and done.lines == ['A title "Kiama" comes in on shot 3 (much bigger)']
    out, done = run(ChangeOp(op="captions", shot=1))  # asked again: still box, not back to pop
    assert next(g for g in out if g.kind == "captions").style == "box"
    marks[0] = PlanGraphic(kind="captions", style="box", scale=1.6, place="top")
    out, done = run(ChangeOp(op="captions", shot=1, scale=1.3, place="top"))  # smaller than before, bigger than usual
    assert done.lines == ["The captions are smaller, still at the top"]
    # a size alone is a whole change for the reader's check (no words needed)
    asked = ChangeRequest(ops=[ChangeOp(op="title", shot=2, scale=1.3)], said=["The title is bigger"])
    assert not [p for p in versions.check_request(asked, 6, {}, {}) if "needs `text`" in p]


def test_size_and_place_said_in_an_edit_become_its_scale_and_place():
    assert cello.sized({"op": "captions", "shot": 1, "style": "box", "want": "bigger, at the top of the frame"}) ==         {"op": "captions", "shot": 1, "style": "box", "scale": 1.3, "place": "top"}
    assert cello.sized({"op": "title", "shot": 2, "want": "much bigger"}) == {"op": "title", "shot": 2, "scale": 1.6}
    assert cello.sized({"op": "callout", "shot": 2, "want": "the top of the tower"})["want"] == "the top of the tower"


def test_bigger_graphics_are_laid_out_bigger_and_where_they_were_asked():
    segs = [Segment(id="s01", clip_id="a", src_in=0, src_out=6)]
    words = [CaptionWord(w=w, s=0.5 + k * 0.4, e=0.8 + k * 0.4) for k, w in enumerate("one two three four".split())]

    def laid(**kw):
        edl = EDL(project_dir="x", meta=EditMeta(width=1080, height=1920, fps=30, fade_in=0, fade_out=0), segments=segs,
                  motion=[MotionGraphic(kind="captions", start=0, end=6, words=words, **kw),
                          MotionGraphic(kind="title", start=1, end=3, text="Hello", **kw)])
        return {x.block: x.variables for x in place.layers(edl, build_timeline(edl, defaultdict(lambda: None)))}

    usual, big = laid(), laid(scale=1.5, place="top")
    assert big["captions"]["size"] == pytest.approx(usual["captions"]["size"] * 1.5)
    assert big["captions"]["per"] == 2 and "per" not in usual["captions"]  # bigger words, fewer to a phrase
    assert big["captions"]["y"] < 0.3 < usual["captions"]["y"]
    assert big["title"]["size"] == pytest.approx(usual["title"]["size"] * 1.5) and big["title"]["y"] < usual["title"]["y"]


def test_a_restart_frees_the_films_session_and_says_what_stopped(studio):
    user = _person(studio)
    film = studio.create_project(user, "Beach", "9:16", 15, "fun")
    now = 1000.0
    for wid, status in (("w1", "running"), ("w2", "queued"), ("w3", "asking")):
        studio.db.run("INSERT INTO work(id, project_id, user_id, status, request, created, updated) VALUES (?,?,?,?,?,?,?)",
                      (wid, film["id"], user["id"], status, "do it", now, now))
    again = Studio(studio.s)  # Flicello started again on the same home
    assert again.work.recovered == 2
    rows = {r["id"]: r for r in again.db.all("SELECT * FROM work")}
    assert rows["w1"]["status"] == "failed" and "restarted" in rows["w1"]["say"]
    assert rows["w2"]["status"] == "stopped" and rows["w3"]["status"] == "asking"  # a question still waits for them


def test_a_new_request_takes_over_from_an_unanswered_question_and_a_stop_hands_over(studio, monkeypatch):
    user = _person(studio)
    film = studio.create_project(user, "Beach", "9:16", 15, "fun")
    launched = []
    monkeypatch.setattr(studio.work, "_launch", lambda wid, message=None: launched.append(wid))
    first = studio.work.start(user, film, "make the captions bigger")
    assert first["status"] == "running" and launched == [first["id"]]
    second = studio.work.start(user, film, "and add a title")
    assert second["status"] == "queued"  # one at a time per film
    studio.work.stop(user, first["id"])  # nothing running in this test: the stop itself hands over
    studio.db.run("UPDATE work SET status='asking' WHERE id=?", (first["id"],))
    studio.work.stop(user, first["id"])
    assert launched[-1] == second["id"]
    studio.db.run("UPDATE work SET status='asking' WHERE id=?", (second["id"],))
    third = studio.work.start(user, film, "actually, make it cinematic")
    assert third["status"] == "running"  # the open question doesn't hold the film's session
    assert studio.db.one("SELECT status FROM work WHERE id=?", (second["id"],))["status"] == "stopped"


def test_flicello_asks_the_shape_once_and_carries_out_the_answer(studio):
    user = _person(studio)
    film = studio.create_project(user, "Beach", "16:9", 15, "fun")
    studio.db.run("INSERT INTO clips(id, project_id, name, file, bytes, seconds, width, height, created) "
                  "VALUES ('c1', ?, 'a.mp4', 'a.mp4', 1, 10, 1080, 1920, 0)", (film["id"],))
    asks.after_upload(studio, user, film)
    asks.after_upload(studio, user, film)  # asked once
    rows = studio.db.all("SELECT * FROM work WHERE project_id=? AND kind='system'", (film["id"],))
    assert len(rows) == 1 and json.loads(rows[0]["options"])[0].startswith("Vertical")  # its pick first
    assert any(e.startswith("[question]") for e in cello.work_events(studio, user, film, 0))
    with pytest.raises(ValueError):
        studio.work.answer(user, rows[0]["id"], "hmm")
    done = studio.work.answer(user, rows[0]["id"], "vertical please")
    assert done["status"] == "done" and studio.db.project(film["id"])["shapes"] == "9:16"
    assert {"what": "Shape", "value": asks.SHAPES["9:16"]} in asks.choices(studio, film)


def test_cello_picks_the_idea_they_name_by_its_number(studio, monkeypatch):
    user = _person(studio)
    film = studio.create_project(user, "Beach", "9:16", 15, "fun")
    shown = [{"id": 57, "rejected": 0}, {"id": 58, "rejected": 1}, {"id": 59, "rejected": 0}]
    picked = []
    monkeypatch.setattr(studio, "pitches", lambda p: shown)
    monkeypatch.setattr(studio, "choose", lambda p, pid: picked.append(pid))
    cello.act(studio, user, film, "step", {"action": "pick", "pitch": 2})
    assert picked == [59]  # the second idea on the page (the rejected one isn't shown)


def test_the_agent_rewrites_the_whole_plan_and_it_plays_as_a_draft(studio_film):
    studio, client, csrf, pid, work = studio_film
    project = studio.db.project(pid)
    user = studio.user(project["user_id"])
    token = "t-agent"
    studio.work.tokens[token] = {"work": "w-test", "user_id": user["id"], "project_id": pid}
    agent = TestClient(create_app(studio), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))
    hands = {"X-Flicello-Agent": token}
    elsewhere = TestClient(create_app(studio), base_url="http://127.0.0.1:8765", client=("10.0.0.5", 50000))
    assert elsewhere.get("/api/work/plan", headers=hands).status_code == 401  # this computer only

    view = agent.get("/api/work/plan", headers=hands).json()
    plan = view["plan"]
    assert view["which"] == "version 1" and view["moments"] and view["clips"]
    assert "must add up to exactly" in view["about"][1] and view["beats_total"]

    broken = json.loads(json.dumps(plan))
    broken["plan"][0]["key"] = "nowhere"
    r = agent.post("/api/work/plan", headers=hands, json={"plan": broken, "said": ["x"]})
    assert r.status_code == 400 and "not in the pool" in r.text  # every problem comes back to fix, nothing is made
    r = agent.post("/api/work/plan", headers=hands, json={"plan": {"name": 3}, "said": []})
    assert r.status_code == 400 and "doesn't read" in r.text

    good = json.loads(json.dumps(plan))
    good["plan"][0], good["plan"][1] = good["plan"][1], good["plan"][0]
    good["graphics"] = [{"kind": "captions", "style": "box", "scale": 1.5, "place": "top"}]
    said = ["Shots 1 and 2 trade places", "Box captions, bigger, at the top"]
    r = agent.post("/api/work/plan", headers=hands, json={"plan": good, "said": said})
    assert r.status_code == 200, r.text
    shown = client.get(f"/api/work/state?pid={pid}&since=1").json()
    assert {"wait": "draft", "pid": pid} in shown["ui"] and shown["busy"]  # their screen shows it once it's made
    _drain_all(studio)
    project = studio.db.project(pid)
    draft = studio.draft_of(project)
    assert draft and draft["said"][:2] == said and draft["base"] == 1
    made = studio.draft_plan(project)
    assert [s.key for s in made.plan[:2]] == [plan["plan"][1]["key"], plan["plan"][0]["key"]]
    edl = EDL.model_validate_json((work / "out" / draft["edl"]).read_text(encoding="utf-8"))
    caps = [g for g in edl.motion if g.kind == "captions"]
    assert caps and (caps[0].style, caps[0].scale, caps[0].place) == ("box", 1.5, "top")
    assert agent.get("/api/work/plan", headers=hands).json()["which"].startswith("the draft on screen")
    with pytest.raises(ActionError):
        studio.plan_view(dict(project, kind="voice"), user)


def _scene_clip(out, seconds: float, width: int, height: int, fps: int):
    """A stand-in for a designed scene's finished stretch: red, with a tone, at the film's size and rate."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c=red:s={width}x{height}:r={fps}:d={seconds:.3f}", "-f", "lavfi", "-i",
                    f"sine=frequency=440:sample_rate=48000:duration={seconds:.3f}", "-ac", "2", "-shortest",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], check=True)
    return out


def _red_at(video, t: float) -> bool:
    from vidosuper.perceive.contact_sheet import grab_frames

    (_, frame), = grab_frames(video, [t])
    b, g, r = frame.reshape(-1, 3).mean(axis=0)
    return r > 150 and g < 90 and b < 90


def test_a_designed_scene_goes_into_a_new_version_and_back_in_only_while_the_film_under_it_is_the_same(
        studio_film, monkeypatch, tmp_path):
    from vidosuper.motion import scenes

    studio, client, csrf, pid, work = studio_film
    project = studio.db.project(pid)
    user = studio.user(project["user_id"])
    edl1 = json.loads((work / "out" / "v1-16x9.edl.json").read_text(encoding="utf-8"))
    w, h, fps = edl1["meta"]["width"], edl1["meta"]["height"], edl1["meta"]["fps"]

    def designed(video, words, start, end, what, folder, **kw):
        return {"video": str(_scene_clip(folder / "scene.mp4", end - start, w, h, fps)), "rounds": [{"score": 8}]}

    monkeypatch.setattr(scenes, "make", designed)
    studio.db.run("INSERT INTO work(id, project_id, user_id, kind, status, request, created, updated) "
                  "VALUES ('s1', ?, ?, 'scene', 'running', 'wings', 0, 0)", (pid, user["id"]))
    scenes_in_film._design(studio, user, project, "s1", "wings open behind me", 1.0, 2.5, "", 1, None)
    row = studio.db.one("SELECT * FROM work WHERE id='s1'")
    assert row["status"] == "done" and "version 2" in row["say"], row["say"]
    project = studio.db.project(pid)
    assert project["version"] == 2 and project["stage"] == "done"
    v2 = work / "out" / "v2-16x9.mp4"
    assert _red_at(v2, 1.8) and not _red_at(v2, 0.5) and not _red_at(v2, 3.5)
    _, plan, _ = studio._version_plan(project, 2)
    assert [(g.kind, g.scene, g.at, g.until) for g in plan.graphics if g.kind == "scene"] == [("scene", "s1", 1.0, 2.5)]

    again = shutil.copyfile(work / "out" / "v1-16x9.mp4", tmp_path / "again.mp4")  # the film made again, same cut
    assert scenes_in_film.lay_back(studio, project, plan, 2, "16:9", again) == [] and _red_at(again, 1.8)
    edl2 = work / "out" / "v2-16x9.edl.json"
    data = json.loads(edl2.read_text(encoding="utf-8"))
    data["motion"].append({"kind": "title", "start": 0.5, "end": 3.0, "text": "Hi"})  # a title over it now
    edl2.write_text(json.dumps(data), encoding="utf-8")
    plain = shutil.copyfile(work / "out" / "v1-16x9.mp4", tmp_path / "plain.mp4")
    notes = scenes_in_film.lay_back(studio, project, plan, 2, "16:9", plain)
    assert len(notes) == 1 and "left out" in notes[0] and not _red_at(plain, 1.8)  # designed without it: not laid in


@pytest.mark.skipif(not motion.available(), reason="the motion engine isn't installed (npm ci in motion/)")
def test_the_agent_sees_a_draft_as_their_player_shows_it(studio_film):
    from vidosuper.work.frames import frames

    studio, client, csrf, pid, work = studio_film
    project = studio.db.project(pid)
    user = studio.user(project["user_id"])
    studio.ask_draft(user, project, [{"op": "title", "shot": 1, "text": "HELLO", "style": "rise", "scale": 1.5,
                                      "place": "middle"}], ["A title"])
    _drain_all(studio)
    made = frames(studio, studio.db.project(pid), "draft", [1.0, 99], work / "agent-frames")
    assert [m["t"] for m in made] == [1.0]  # past the end: left out
    from PIL import Image

    still = Image.open(made[0]["file"]).convert("L")
    w, h = still.size
    middle = still.crop((w // 4, int(h * 0.4), 3 * w // 4, int(h * 0.6)))
    assert middle.getextrema()[1] > 240  # the white title is drawn on the picture, in the middle
    version = frames(studio, studio.db.project(pid), "1", [1.0], work / "agent-frames")
    assert version and version[0]["file"].name == "v1-1.00.jpg"
