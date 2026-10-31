"""Worlds: the whole frame designed (2d in HyperFrames, 3d in Blender), the film in sections, the person cut out and
placed in the world, the desk that designs a section, and the film joined with their sound."""

import json
import subprocess

import numpy as np
import pytest

from vidosuper.motion import moments as mo
from vidosuper.motion import framing, scenes, worlds


def _design(spans, engine="2d"):
    return mo.FilmDesign(look="deep navy, flat", engine=engine, world="a navy space, them on the left", moments=[
        mo.Moment(start=a, end=b, before=f"thing {k}", after=f"done {k}",
                  changes=[mo.Change(word="circle", at=a + 0.5, becomes="it opens", over=0.6)])
        for k, (a, b) in enumerate(spans)])


def test_sections_cover_the_film_end_to_end_cut_between_moments_never_inside_one():
    d = _design([(1, 5), (8, 14), (16, 20), (30, 40), (42, 46)])
    parts = worlds.sections(d, 47.0)
    assert parts[0].start == 0 and parts[-1].end == 47.0
    assert all(a.end == b.start for a, b in zip(parts, parts[1:]))  # no gaps, no overlaps
    for s in parts:
        assert s.duration <= worlds.SECTION_MAX + 1e-6
        for m in d.moments:  # a cut never falls inside a moment
            assert not (m.start < s.end < m.end)
    assert parts[0].moments == [0, 1]  # the sample holds the opening moments
    assert sorted(k for s in parts for k in s.moments) == list(range(5))


def test_a_film_without_moments_is_still_cut_into_sections_and_no_tail_is_left_tiny():
    parts = worlds.sections(mo.FilmDesign(look="x"), 50.0)
    assert parts[0].start == 0 and parts[-1].end == 50.0
    assert all(s.duration >= worlds.SECTION_MIN for s in parts)


def test_one_long_moment_stays_whole():
    parts = worlds.sections(_design([(2, 40)]), 45.0)
    assert any(s.start <= 2 and s.end >= 40 for s in parts)


def test_the_cut_out_keeps_them_and_a_raised_hand_but_drops_what_touches_the_frame_edge():
    m = np.zeros((360, 640), np.uint8)
    m[60:360, 240:420] = 255  # them, down to the bottom edge
    m[90:130, 470:510] = 255  # a raised hand, floating free
    m[300:360, 0:90] = 255  # a pillow in the bottom-left corner, touching the edges
    out = worlds.keep_person(m)
    assert out[200, 300] == 255 and out[110, 490] == 255 and out[340, 40] == 0


def test_the_brief_says_the_world_the_engine_and_the_contract_in_section_seconds():
    d = _design([(1, 5), (30, 36)])
    parts = worlds.sections(d, 40.0)
    words = [(1.5, 1.9, "circle"), (31.0, 31.4, "squares")]
    first = worlds.brief_text(d, parts[0], words, "make it come alive", "talk", first=True)
    assert "THE WORLD" in first and "a navy space" in first and "ENGINE: 2d" in first
    assert "on \"circle\" at 1.50 s" in first and "world.md" in first
    assert "WORDS ON SCREEN: none at all" in first and "their cut-out stands in your world" in first
    later = worlds.brief_text(d, parts[-1], words, "", "voice", first=False, bible="palette #0b1026")
    assert "BIBLE" in later and "#0b1026" in later and "voice-only" in later
    assert f"at {30.5 - parts[-1].start:.2f} s" in later  # times are seconds into the section
    assert f"[{31.0 - parts[-1].start:.2f}] squares" in later


def test_checkpoints_are_in_section_seconds():
    d = _design([(30, 36)])
    s = worlds.Section(24.0, 40.0, [0])
    pts = worlds.checkpoints(d, s)
    assert pts and all(0 < p["t"] < s.duration for p in pts) and pts[0]["t"] < 30 - 24 + 0.6


def test_the_planner_picks_an_engine_and_the_plan_reads_it_back():
    d = mo.parse('{"look": "glass", "engine": "Blender", "world": "a void", "moments": []}')
    assert d.engine == "3d"
    assert mo.parse('{"look": "flat", "moments": []}').engine == "2d"
    text = mo.as_prompt(mo.FilmDesign(look="flat navy", world="a navy space", engine="2d"))
    assert "Look (2D)" in text and "World: a navy space" in text
    assert "WHOLE FRAME" in mo.SYSTEM and '"engine"' in mo.SYSTEM


@pytest.mark.parametrize("engine", ["2d", "3d"])
def test_the_desk_gets_its_kit_its_tools_and_the_whole_frame_rule(tmp_path, monkeypatch, engine):
    from vidosuper import local
    from vidosuper.motion import stage

    seen = {}

    def run_claude(cmd, prompt, folder, cwd=None, timeout=None, env=None):
        seen["cmd"] = cmd
        scene = folder / ("scene.py" if engine == "3d" else "scene.html")
        scene.write_text("x" * 400, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"result": "DONE"}).encode(), b"")

    monkeypatch.setattr(local, "run_claude", run_claude)
    monkeypatch.setattr(local, "claude_exe", lambda: "claude")
    monkeypatch.setattr(local, "plan_env", lambda: {})
    monkeypatch.setattr(worlds, "_size", lambda video: (1920, 1080))
    folder = tmp_path / "sec"
    (folder / "stage").mkdir(parents=True)
    (folder / "stage" / stage.SHOT).write_bytes(b"")
    d = _design([(1, 5)], engine)
    s = worlds.sections(d, 10.0)[0]
    worlds.design(d, s, folder, [(1.5, 1.9, "circle")], "ask", "talk", first=True)
    desk = folder / "desk"
    system = (desk / "system.md").read_text(encoding="utf-8")
    assert "WHOLE FRAME" in system and "stickers" in system
    assert "Bash(bash look:*)" in seen["cmd"] and "Bash(bash review:*)" in seen["cmd"]
    info = json.loads((desk / "brief.json").read_text(encoding="utf-8"))
    assert info["end"] - info["start"] == s.duration and info["spec"]
    if engine == "3d":
        assert (desk / "blender_kit.py").exists() and "blender_kit" in system
        assert "look3d" in (desk / "look").read_text(encoding="utf-8")
    else:
        assert "#person" in system and "STEPPED FLAT RINGS" in system
        assert "scenes look" in (desk / "look").read_text(encoding="utf-8")


def _clip(path, seconds, colour="blue", size="160x90"):
    from vidosuper.ffmpeg import ffmpeg_bin

    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={colour}:s={size}:r=30",
                    "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000", "-t", f"{seconds:.3f}", "-c:v",
                    "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", str(path)], check=True)
    return path


def test_the_film_designs_the_first_section_alone_then_the_rest_to_its_bible_and_joins_them(tmp_path, monkeypatch):
    base = _clip(tmp_path / "base.mp4", 30.0)
    calls = []

    def design(design_, section, folder, words, ask, kind, first, bible="", reference=None, place="", **kw):
        calls.append((section.start, first, bible, reference.name if reference else None, place))
        desk = folder / "desk"
        (desk / "reviews").mkdir(parents=True, exist_ok=True)
        html = "<html><style>#person { transform: scale(.9); }</style></html>"
        (desk / "scene.html").write_text(html, encoding="utf-8")
        if first:
            (desk / "world.md").write_text("palette #0b1026", encoding="utf-8")
        (desk / "reviews" / "review-1.json").write_text(json.dumps({"scene": scenes._digest(html), "score": 8.5,
                                                                    "ok": True}), encoding="utf-8")
        return desk / "scene.html"

    def render(section, folder, base_, engine, library):
        return _clip(folder / "section.mp4", section.duration, "red")

    monkeypatch.setattr(worlds, "prepare", lambda *a, **k: None)
    monkeypatch.setattr(worlds, "design", design)
    monkeypatch.setattr(worlds, "render", render)
    monkeypatch.setattr(worlds, "parallel", lambda: 2)
    d = _design([(1, 5), (8, 12), (20, 26)])
    sample = worlds.make(d, base, [], "ask", "talk", tmp_path / "w", tmp_path / "sfx", only_first=True)
    assert len(calls) == 1 and calls[0][1] and sample["video"].name == "section.mp4"
    film = worlds.make(d, base, [], "ask", "talk", tmp_path / "w", tmp_path / "sfx")
    assert len(calls) == len(worlds.sections(d, 30.0))  # the sample is kept, not designed again
    assert all(c[2] == "palette #0b1026" and c[3] == "scene.html" for c in calls[1:])  # built to the bible
    assert all(c[4] == "#person { transform: scale(.9); }" for c in calls[1:])  # them held where the first put them
    assert film["video"].name == "film.mp4" and abs(worlds._duration(film["video"]) - 30.0) < 0.2
    rows = worlds.requirements(d, film, "ask", len(worlds.sections(d, 30.0)))
    assert rows[0]["level"] == "ok" and "8.5/10" in rows[1]["detail"]


def test_a_failed_section_leaves_the_plain_picture_and_says_so(tmp_path, monkeypatch):
    base = _clip(tmp_path / "base.mp4", 30.0)

    def design(design_, section, folder, *a, first=False, **kw):
        if not first:
            raise worlds.WorldError("the designer left no scene")
        (folder / "desk").mkdir(parents=True, exist_ok=True)
        (folder / "desk" / "scene.html").write_text("<html></html>", encoding="utf-8")
        return folder / "desk" / "scene.html"

    monkeypatch.setattr(worlds, "prepare", lambda *a, **k: None)
    monkeypatch.setattr(worlds, "design", design)
    monkeypatch.setattr(worlds, "render", lambda s, f, *a: _clip(f / "section.mp4", s.duration, "red"))
    monkeypatch.setattr(worlds, "review", lambda desk: None)
    d = _design([(1, 5), (8, 12), (20, 26)])
    film = worlds.make(d, base, [], "", "talk", tmp_path / "w", tmp_path / "sfx")
    assert any("Section 2" in n and "couldn't be designed" in n for n in film["notes"])
    assert any("Section 1" in n and "not ready" in n for n in film["notes"])  # never judged: said, not hidden
    assert abs(worlds._duration(film["video"]) - 30.0) < 0.2  # still the whole film
    assert worlds.requirements(d, film, "", 2)[0]["level"] == "warn"


def test_the_blender_kit_and_the_kits_are_shipped():
    import py_compile

    py_compile.compile(str(worlds.BLENDER_KIT), doraise=True)
    assert "def person(" in worlds.BLENDER_KIT.read_text(encoding="utf-8")
    assert worlds.KIT_2D.exists() and worlds.KIT_3D.exists()


def test_a_used_up_claude_plan_is_said_plainly_not_as_an_unreadable_reply(monkeypatch):
    from vidosuper import local
    from vidosuper.motion import scenes

    reply = json.dumps({"result": "You've hit your 5-hour limit · resets 4pm (UTC)", "is_error": True}).encode()
    monkeypatch.setattr(scenes, "run_claude", lambda *a, **k: subprocess.CompletedProcess([], 1, reply, b""))
    monkeypatch.setattr(scenes, "claude_exe", lambda: "claude")
    monkeypatch.setattr(scenes, "plan_env", lambda: {})
    with pytest.raises(scenes.SceneError, match="limit is reached"):
        scenes._ask_claude_code("system", [{"type": "text", "text": "plan it"}], "moments")


def test_a_desk_stopped_by_the_plan_limit_fails_its_section_instead_of_shipping_half_a_scene(tmp_path, monkeypatch):
    from vidosuper import local

    reply = json.dumps({"result": "You've hit your 5-hour limit · resets 6:50pm (UTC)", "is_error": True}).encode()
    monkeypatch.setattr(local, "run_claude", lambda *a, **k: subprocess.CompletedProcess([], 1, reply, b""))
    monkeypatch.setattr(local, "claude_exe", lambda: "claude")
    monkeypatch.setattr(local, "plan_env", lambda: {})
    with pytest.raises(worlds.WorldError, match="limit is reached"):
        worlds._at_desk(tmp_path, "design it", 60, "session.json")


def _person_frames(h=360, w=640, x0=250, scale=1.0):
    """A person alone on black (a head and shoulders), their mask, and their shot's own mask."""
    alone = np.zeros((h, w, 3), np.uint8)
    mask = np.zeros((h, w), np.uint8)
    hw, hh = int(60 * scale), int(80 * scale)
    top = h - int(300 * scale)
    mask[top:top + hh, x0:x0 + hw] = 255  # head
    mask[top + hh:, max(0, x0 - int(40 * scale)):x0 + hw + int(40 * scale)] = 255  # body to the bottom
    alone[mask > 0] = (90, 120, 170)
    source = np.zeros((h, w), np.uint8)
    source[60:140, 290:350] = 255
    source[140:, 250:390] = 255
    return alone, mask, source


def test_something_drawn_over_their_face_is_measured_on_their_cut_out():
    alone, mask, source = _person_frames()
    full = alone.copy()
    full[mask == 0] = (60, 20, 30)  # the world behind them changes nothing
    clean = framing.measure(1.0, full, alone, mask, source)
    assert clean.face and clean.covered == 0 and not framing.problems([clean])
    full[85:110, :] = (255, 255, 255)  # a white line across the frame, through their head
    hit = framing.measure(2.0, full, alone, mask, source)
    said = framing.problems([clean, hit])
    assert hit.covered > framing.COVER_SHARE and len(said) == 1
    assert said[0].startswith("MEASURED at 2.0 s") and "face" in said[0]
    thin = alone.copy()
    thin[100:104, :] = (255, 255, 255)  # a thin line: little of the face, but right across it
    crossed = framing.measure(3.0, thin, alone, mask, source)
    assert crossed.covered < framing.COVER_SHARE and crossed.crossed
    assert "crosses their face" in framing.problems([crossed])[0]


def test_them_cut_off_at_an_edge_or_shrunk_is_measured_against_their_own_shot():
    _, _, source = _person_frames()
    alone, mask, _ = _person_frames(x0=10)  # pushed off the left edge
    cut = framing.measure(1.0, alone, alone, mask, source)
    assert "left" in cut.cut
    alone, mask, _ = _person_frames(x0=300, scale=0.45)  # small in the frame
    small = framing.measure(3.0, alone, alone, mask, source)
    said = " ".join(framing.problems([cut, small]))
    assert small.size < framing.SMALL_AREA and "left edge" in said and "of their own size" in said


def test_the_person_alone_page_hides_everything_but_them():
    html = "<html><body><div id='stage'><div class='behind'></div></div></body></html>"
    alone = scenes.alone_html(html)
    assert "visibility:hidden !important" in alone and alone.index("vido-alone") < alone.index("</body>")
    assert "invert(1)" in scenes.alone_html(html, True) and "invert(1)" not in alone
    measured = scenes.with_measured({"score": 8, "ok": True, "problems": ["late pop"]}, ["MEASURED at 2.0 s: face"])
    assert measured["score"] == 6 and not measured["ok"] and measured["problems"][0].startswith("MEASURED")
    assert scenes.with_measured({"score": 8, "ok": True}, []) == {"score": 8, "ok": True}


def test_a_desk_gets_one_deep_review_and_no_second(tmp_path, capsys):
    desk = tmp_path / "desk"
    (desk / "reviews").mkdir(parents=True)
    (desk / "scene.html").write_text("<html>one</html>", encoding="utf-8")
    (desk / "brief.json").write_text(json.dumps({"ask": "", "start": 0, "end": 5, "width": 1920, "height": 1080,
                                                 "fps": 30, "words": []}), encoding="utf-8")
    (desk / "review-limit").write_text("1", encoding="utf-8")
    (desk / "reviews" / "review-1.md").write_text("The director's review 1: 6/10", encoding="utf-8")
    (desk / "reviews" / "review-1.json").write_text(json.dumps({"score": 6, "ok": False}), encoding="utf-8")
    scenes.review_command(desk)
    assert "You've had your review" in capsys.readouterr().out
    assert len(list((desk / "reviews").glob("review-*.json"))) == 1
    assert scenes.review_allowance(desk) == 1 and scenes.review_allowance(tmp_path) is None
    assert worlds.score(desk) == 6  # its one review, though the scene was fixed after it
    assert "ONCE" in worlds.DESK and "no second" in worlds.DESK


def test_the_one_review_spreads_over_the_whole_scene_not_its_start(monkeypatch):
    seen = {}

    def frames(times, sub=""):
        seen.setdefault("times", []).extend(times)
        return {}

    brief = scenes.Brief(ask="", start=0, end=30, width=1920, height=1080, fps=30, words=[])
    monkeypatch.setattr(scenes, "_ask", lambda *a, **k: '{"score": 7, "ok": false, "problems": []}')
    scenes.judge(brief, [{"t": float(t)} for t in range(1, 29)], frames, client=object(), most=scenes.DEEP)
    assert max(seen["times"]) > 25 and min(seen["times"]) < 2


def test_a_section_its_desk_never_reviewed_gets_the_review_and_one_fix_without_a_second_review(tmp_path, monkeypatch):
    desk = tmp_path / "desk"
    desk.mkdir()
    (desk / "scene.html").write_text("<html></html>", encoding="utf-8")
    calls = {"review": 0}
    sittings = []

    def review(d):
        calls["review"] += 1
        return {"score": 5, "ok": False, "problems": ["MEASURED at 21.0 s: something is drawn over their face"]}

    monkeypatch.setattr(worlds, "review", review)
    monkeypatch.setattr(worlds, "_at_desk", lambda d, prompt, timeout, name: sittings.append((prompt, name)))
    seen = worlds.settle(desk, "2d")
    assert seen["score"] == 5 and calls["review"] == 1 and len(sittings) == 1  # reviewed once, fixed once
    assert "over their face" in sittings[0][0] and "no second review" in sittings[0][0]
    (desk / "reviews").mkdir()
    (desk / "reviews" / "review-1.json").write_text(json.dumps({"score": 6, "ok": False}), encoding="utf-8")
    worlds.settle(desk, "2d")
    assert len(sittings) == 1  # its desk had its review and fixed it: no extra sitting


def test_the_times_in_a_persons_notes_are_read():
    assert worlds.times_in("at 0:12 the tree pops flat, and at 1:05.5 too; also 20 s in it's dark") == [12, 65.5, 20]
    assert worlds.times_in("make the trees nicer") == []


def test_notes_change_only_the_sections_they_point_at_and_the_film_is_joined_again(tmp_path, monkeypatch):
    base = _clip(tmp_path / "base.mp4", 30.0)
    d = _design([(1, 5), (8, 12), (20, 26)])
    parts = worlds.sections(d, 30.0)
    assert len(parts) == 2
    folder = tmp_path / "w"
    for s in parts:
        here = folder / worlds.digest(d, s, base, "ask")
        (here / "desk").mkdir(parents=True)
        _clip(here / "section.mp4", s.duration, "blue")
    sat, rendered = [], []
    monkeypatch.setattr(worlds, "_at_desk", lambda desk, prompt, timeout, name: sat.append((desk, prompt)))
    monkeypatch.setattr(worlds, "render", lambda s, here, *a: rendered.append(s.start) or _clip(
        here / "section.mp4", s.duration, "red"))
    t = parts[1].start + 2
    film = worlds.revise(d, base, [], "ask", "voice", folder, tmp_path / "sfx", f"at 0:{t:02.0f} the tree is flat")
    assert rendered == [parts[1].start] and film["changed"] == [2] and len(sat) == 1
    assert "the tree is flat" in sat[0][1] and f"{parts[1].start:.2f}" in sat[0][1]
    assert abs(worlds._duration(film["video"]) - 30.0) < 0.2
    worlds.revise(d, base, [], "ask", "voice", folder, tmp_path / "sfx", "make every tree rounder")
    assert len(rendered) == 3  # no times: every section
    with pytest.raises(worlds.WorldError):
        worlds.revise(d, base, [], "other ask", "voice", folder, tmp_path / "sfx", "x")


def test_the_person_is_held_where_the_first_section_put_them():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        first, later = Path(tmp) / "a", Path(tmp) / "b"
        first.mkdir()
        later.mkdir()
        (first / "scene.html").write_text("<style>#stage{}\n#person { transform: translate(-330px, 70px) scale(.86); }"
                                          "</style>", encoding="utf-8")
        place = worlds.place_of(first, "2d")
        assert place.startswith("#person") and "scale(.86)" in place
        (later / "scene.html").write_text("<style>#person { transform: translate(-500px, 0) scale(.5); }</style>",
                                          encoding="utf-8")
        assert worlds.hold_place(later, "2d", place)
        assert "scale(.86)" in (later / "scene.html").read_text(encoding="utf-8")
        assert not worlds.hold_place(later, "2d", place)  # already there
        (first / "scene.py").write_text("him = k.person(x=-2.55, z=-0.22, scale=1.16)\n", encoding="utf-8")
        (later / "scene.py").write_text("him = k.person(x=-4.0, z=0.5, scale=0.6)\n", encoding="utf-8")
        place3 = worlds.place_of(first, "3d")
        assert place3 == "x=-2.55, z=-0.22, scale=1.16" and worlds.hold_place(later, "3d", place3)
        assert "k.person(x=-2.55, z=-0.22, scale=1.16)" in (later / "scene.py").read_text(encoding="utf-8")
