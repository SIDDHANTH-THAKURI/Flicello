"""Motion graphics (vidosuper.motion): titles, word-by-word captions and lower thirds, placed by the film (the apps'
safe zones, the faces, the speech), rendered with transparency by the motion engine and laid over the picture; the
same blocks in the instant preview; and in the studio, graphics asked for in plain words that stay where they belong
through every later change."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vidosuper import motion
from vidosuper.edit import build_timeline
from vidosuper.ffmpeg import ffmpeg_bin, ffprobe_json
from vidosuper.motion import place
from vidosuper.perceive.transcribe import Transcript, Utterance, Word
from vidosuper.render import render_edl
from vidosuper.render.assemble import assembly_graph
from vidosuper.schemas import EDL, CaptionWord, EditMeta, MotionGraphic, Segment, SegmentAudio
from vidosuper.studio import preview, versions
from vidosuper.studio.brain.schemas import ChangeOp, DesignPlan, PlanGraphic, PlanShot, PoolMoment

from test_faces import _drain_all, studio_film  # noqa: F401 - a finished film to work on (a fixture)

ENGINE = pytest.mark.skipif(not motion.available(), reason="the motion engine isn't installed (npm ci in motion/)")


def tall(segments, graphics=(), w=1080, h=1920) -> EDL:
    return EDL(project_dir="x", meta=EditMeta(width=w, height=h, fps=30, fade_in=0, fade_out=0), segments=segments,
               motion=list(graphics))


def test_a_render_page_holds_the_block_its_values_and_its_own_fonts():
    html = motion.page("title", {"text": "Hi </script><b>", "sub": "x"}, 1080, 1920, 2.0)
    assert 'data-width="1080"' in html and 'data-height="1920"' in html and 'data-duration="2.000"' in html
    for hole in ("__FONTS__", "__CSS__", "__BLOCK__", "__W__", "__H__", "__D__", "__VARS__", "__BASE__"):
        assert hole not in html
    assert html.count("data:font/woff2;base64,") == 3  # no font is looked up: HyperFrames would swap it for its own
    assert "</script><b>" not in html  # a value can't end the script it sits in
    values = json.loads(re.search(r'id="vars">(.*?)</script>', html, re.S).group(1))
    assert values["block"] == "title" and values["duration"] == 2.0 and values["text"] == "Hi </script><b>"
    assert '<script src="title.js">' in html and "data-no-timeline" in html
    with pytest.raises(motion.MotionError):
        motion.page("fireworks", {}, 10, 10, 1.0)
    # the preview loads the same files from the app, with the fonts from there too
    body, kind = motion.asset("fonts.css")
    assert kind.startswith("text/css") and body.decode().count("url('/motion/fonts/") == 3
    assert motion.asset("title.js")[1].startswith("text/javascript") and motion.asset("fonts/bricolage.woff2")
    assert motion.asset("../__init__.py") is None and motion.asset("page.html") is None


def test_graphics_stay_clear_of_the_apps_buttons_and_of_faces():
    segs = [Segment(id="s01", clip_id="a", src_in=0, src_out=4), Segment(id="s02", clip_id="b", src_in=0, src_out=4)]
    words = [CaptionWord(w="hello", s=4.2, e=4.6), CaptionWord(w="there", s=4.7, e=5.0),
             CaptionWord(w="again.", s=7.6, e=7.9)]
    edl = tall(segs, [MotionGraphic(kind="title", start=0.2, end=2.5, text="Sydney after dark", sub="a night walk"),
                      MotionGraphic(kind="captions", start=0, end=8, words=words),
                      MotionGraphic(kind="lowerthird", start=4.0, end=7.0, text="Circular Quay", sub="Sydney")])
    tl = build_timeline(edl, defaultdict(lambda: None))
    face = [{"clip_id": "a", "from": 0.0, "to": 4.0, "size": "near", "x": 0.5, "y": 0.42}]
    title, cap1, cap2, name = place.layers(edl, tl, {}, face)
    top, bottom, side = place.SAFE["tall"]
    v = title.variables
    assert v["text"] == "Sydney\nafter dark"  # too long for one line of a tall frame: broken in the middle
    assert abs(v["y"] - 0.42) > 0.15 and top < v["y"] < 1 - bottom  # off the face, inside the safe area
    assert place.layers(edl, tl, {}, [])[0].variables["y"] == pytest.approx(0.42)  # no face: the middle
    # a far-off figure or a face in another shot doesn't move it
    far = [{**face[0], "size": "far"}, {**face[0], "clip_id": "b"}]
    assert place.layers(edl, tl, {}, far)[0].variables["y"] == pytest.approx(0.42)
    # captions: just above the apps' own captions and buttons, a stretch per run of speech
    c = cap1.variables
    assert c["y"] + c["size"] * 1.15 <= 1 - bottom + 1e-6 and c["side"] == side
    assert (cap1.start, cap2.start) == (pytest.approx(4.0), pytest.approx(7.4))
    assert [w["w"] for w in c["words"]] == ["hello", "there"] and c["words"][0]["s"] == pytest.approx(0.2)
    assert cap2.end == pytest.approx(8.0)  # never past the film's end
    # a name over captions in a tall frame goes up top-left, clear of them
    assert name.variables["y"] < 0.3 and name.variables["x"] < 0.1
    assert name.public(30)["vars"]["fps"] == 30 and name.public(30)["vars"]["duration"] == pytest.approx(3.0)
    # a wide frame keeps the TV-safe margins: the name at the bottom left when there are no captions
    wide = EDL(project_dir="x", meta=EditMeta(width=1920, height=1080, fps=30), segments=segs,
               motion=[MotionGraphic(kind="lowerthird", start=1, end=3, text="Ana")])
    lt = place.layers(wide, build_timeline(wide, defaultdict(lambda: None)))[0].variables
    assert lt["y"] > 0.75 and lt["x"] == pytest.approx(0.06)


def test_captions_come_from_what_is_said_in_the_shots_that_play_their_sound():
    said = Transcript(clip_id="a", model="t", utterances=[Utterance(start=1, end=3, text="one two three", words=[
        Word(start=1.0, end=1.4, text=" one", prob=0.9), Word(start=1.5, end=1.9, text=" two", prob=0.9),
        Word(start=2.5, end=2.9, text=" three", prob=0.9)]), Utterance(start=5, end=6, text="wind", confident=False,
                                                                     words=[Word(start=5, end=6, text="wind", prob=0.2)])])
    segs = [Segment(id="s01", clip_id="a", src_in=1.4, src_out=2.2, speed=0.5),  # "two" (said 1.5-1.9) is in it
            Segment(id="s02", clip_id="a", src_in=2.0, src_out=3.0, audio=SegmentAudio(mode="mute")),  # heard: nothing
            Segment(id="s03", clip_id="a", src_in=2.4, src_out=6.0)]  # "three", not the unsure "wind"
    edl = tall(segs)
    tl = build_timeline(edl, defaultdict(lambda: None))
    words = place.speech_words(tl, {"a": said})
    assert [w.w for w in words] == ["two", "three"]
    # half speed from 1.4 s: "two" is said 0.2-1.0 s into the film
    assert words[0].s == pytest.approx(0.2, abs=0.04) and words[0].e == pytest.approx(1.0, abs=0.04)
    third = tl.items[2].start / tl.fps
    assert words[1].s == pytest.approx(third + 0.1, abs=0.04)
    # a caption graphic without words takes them from the speech
    edl.motion = [MotionGraphic(kind="captions", start=0, end=tl.duration)]
    layers = place.layers(edl, tl, {"a": said})
    assert len(layers) == 1 and [w["w"] for w in layers[0].variables["words"]] == ["two", "three"]
    assert place.layers(edl, tl, {}) == []  # nobody heard: nothing to render


def test_type_gets_a_deeper_pad_over_a_bright_picture():
    sky = np.full((1080, 1920, 3), 245, np.uint8)  # landscape clips, cut to the tall frame
    night = np.full((1080, 1920, 3), 18, np.uint8)
    half = np.zeros((1080, 1920, 3), np.uint8)
    half[:540] = 255  # bright above, dark below
    frames = {"sky": sky, "night": night, "half": half}

    def read(cid, seconds):
        return frames.get(cid)

    edl = tall([Segment(id="s01", clip_id="sky", src_in=0, src_out=3), Segment(id="s02", clip_id="night", src_in=0, src_out=3)])
    tl = build_timeline(edl, defaultdict(lambda: None))
    bright = place.backdrop(tl, read, 0.5, 2.5, (0.15, 0.3, 0.85, 0.5), 1080 / 1920)
    dark = place.backdrop(tl, read, 3.5, 5.5, (0.15, 0.3, 0.85, 0.5), 1080 / 1920)
    assert bright > 0.9 and dark < 0.1
    assert place.scrim_for(bright) == 0.5 and place.scrim_for(dark) == 0.22 and place.scrim_for(None) is None
    # only the part of the frame under the graphic counts
    one = tall([Segment(id="s01", clip_id="half", src_in=0, src_out=3)])
    tl1 = build_timeline(one, defaultdict(lambda: None))
    assert place.backdrop(tl1, read, 0.5, 2.5, (0.1, 0.1, 0.9, 0.4), 1080 / 1920) > 0.9
    assert place.backdrop(tl1, read, 0.5, 2.5, (0.1, 0.6, 0.9, 0.9), 1080 / 1920) < 0.1
    assert place.backdrop(tl1, lambda cid, s: None, 0.5, 2.5, (0.1, 0.1, 0.9, 0.4), 0.5625) is None
    # the measured depth reaches the block; the box measured is where the title sits
    edl.motion = [MotionGraphic(kind="title", start=0.5, end=2.5, text="Hi", scrim=0.6)]
    layer = place.layers(edl, tl)[0]
    x0, y0, x1, y1 = place.boxes(layer)
    assert layer.variables["scrim"] == 0.6 and y0 < layer.variables["y"] < y1 and (x0, x1) == (0.15, 0.85)


def test_graphics_are_laid_over_the_picture_from_their_start():
    segs = [Segment(id="s01", clip_id="a", src_in=0, src_out=3), Segment(id="s02", clip_id="b", src_in=0, src_out=3)]
    edl = tall(segs)
    tl = build_timeline(edl, defaultdict(lambda: None))
    n = len(tl.items)
    graph = assembly_graph(tl, edl.meta, [], (1080, 1920), graphics=[(1.0, 2.5, Path("a.mov")), (3.0, 4.0, Path("b.mov"))])
    assert f"[{n}:v]setpts=PTS-STARTPTS+1.000000/TB[g0]" in graph and f"[{n + 1}:v]setpts=PTS-STARTPTS+3.000000/TB" in graph
    assert "overlay=0:0:eof_action=pass:format=auto:enable='between(t,1.000000,2.500000)'" in graph
    # an outro after the film takes the input after the graphics
    graph = assembly_graph(tl, edl.meta, [], (1080, 1920), tail=True, graphics=[(1.0, 2.5, Path("a.mov"))])
    assert f"[{n + 1}:v]settb=AVTB" in graph


def test_the_preview_plays_the_same_graphics_in_the_same_places():
    segs = [Segment(id="s01", clip_id="a", src_in=0, src_out=4)]
    edl = tall(segs, [MotionGraphic(kind="title", start=0.5, end=2.5, text="Hello", style="slam"),
                      MotionGraphic(kind="captions", start=0, end=4, words=[CaptionWord(w="hi", s=1.0, e=1.3)])])
    tl = preview.timeline(edl, lambda c: None, None)
    title, caps = tl["motion"]
    assert title["block"] == "title" and title["start"] == 0.5 and title["end"] == 2.5
    assert title["vars"]["style"] == "slam" and title["vars"]["fps"] == 30 and title["vars"]["duration"] == 2.0
    assert caps["block"] == "captions" and caps["vars"]["words"][0]["w"] == "hi"
    layers = place.layers(edl, build_timeline(edl, defaultdict(lambda: None)))
    assert [x.variables["y"] for x in layers] == [title["vars"]["y"], caps["vars"]["y"]]  # as in the render


def test_plain_words_add_and_take_away_graphics():
    def read(text):
        return [(o.op, o.shot, o.text, o.style, o.off) for o in versions.read_simple(text, 10).ops]

    assert read("add captions") == [("captions", 1, "", "pop", False)]
    assert read("make the captions karaoke") == [("captions", 1, "", "karaoke", False)]
    assert read("no subtitles") == [("captions", 1, "", "", True)]
    assert read('add a title "Sydney after dark"') == [("title", 1, "Sydney after dark", "", False)]
    assert read("add a title saying Harbour Days on shot 3") == [("title", 3, "Harbour Days", "", False)]
    assert read("add a slam title 'DROP' on shot 5") == [("title", 5, "DROP", "slam", False)]
    assert read("remove the title") == [("title", 1, "", "", True)]
    assert read("swap 2 and 3 and add captions") == [("swap", 2, "", "", False), ("captions", 1, "", "pop", False)]
    assert read("add a title") == []  # what should it say? the editor asks


def test_graphics_stay_where_they_belong_through_later_changes():
    pool = [PoolMoment(key=f"m{i}", clip="c01", start=i * 2.0, end=i * 2.0 + 1.5) for i in range(1, 6)]
    plan = DesignPlan(name="t", idea="i", pool=pool, plan=[PlanShot(kind="cut", beats=2, key=f"m{i}") for i in range(1, 6)],
                      graphics=[PlanGraphic(kind="title", shot=1, text="Hello"),
                                PlanGraphic(kind="lowerthird", shot=4, text="Quay")])
    ctx = SimpleNamespace(beats=SimpleNamespace(period=0.5, drop=None), facts={})

    def after(*ops):
        done = versions.apply(plan, ctx, list(ops))
        return [(g.kind, g.shot, g.text) for g in done.plan.graphics], done

    got, done = after(ChangeOp(op="remove", shot=2), ChangeOp(op="captions", shot=1),
                      ChangeOp(op="title", shot=3, text="Second"))
    # shot 2 gone: the name moves up with its picture; the new title on (watched) shot 3 is now shot 2
    assert got == [("title", 1, "Hello"), ("lowerthird", 3, "Quay"), ("captions", 1, ""), ("title", 2, "Second")]
    assert 'A title "Second" comes in on shot 3' in done.lines and not done.touched - {0}
    got, _ = after(ChangeOp(op="move", shot=4, other=1))  # the name follows its picture; the opening title stays
    assert got == [("title", 1, "Hello"), ("lowerthird", 1, "Quay")]
    got, done = after(ChangeOp(op="title", shot=1, off=True), ChangeOp(op="captions", shot=1, off=True))
    assert got == [("lowerthird", 4, "Quay")] and done.skipped == ["there are no captions to take away"]
    # a stretch the editor makes again with more or fewer shots: the graphics after it move with their shots
    from vidosuper.studio.brain.schemas import RangePlan

    new = PoolMoment(key="n1", clip="c02", start=0, end=1)
    more = versions.merge_range(plan, 1, 2, RangePlan(pool=[new], plan=[PlanShot(kind="cut", beats=1, key="n1")] * 4))
    assert [(g.kind, g.shot) for g in more.graphics] == [("title", 1), ("lowerthird", 6)]
    fewer = versions.merge_range(plan, 1, 3, RangePlan(pool=[new], plan=[PlanShot(kind="cut", beats=4, key="n1")]))
    assert [(g.kind, g.shot) for g in fewer.graphics] == [("title", 1), ("lowerthird", 2)]  # inside: on the new shot
    assert versions.check_request(versions.read_simple("add a title", 5), 5, {}, {}) == []
    bad = versions.ChangeRequest(ops=[ChangeOp(op="title", shot=2)], said=["a title"])
    assert versions.check_request(bad, 5, {}, {}) == ["change 1: title needs `text`, the words to show"]
    text = versions.request_text(1, [], SimpleNamespace(period=0.5, booms=[], drop=None), [], [], [], "x", [],
                                 graphics=plan.graphics)
    assert '- title on shot 1 for 4 beats: "Hello"' in text and '- name on shot 4 for 4 beats: "Quay"' in text


@ENGINE
def test_a_title_renders_with_transparency_and_lands_on_the_film(ingested, cfg, tmp_path):
    project, clips, _ = ingested
    clip = clips["a_stable"]
    seg = Segment(id="s01", clip_id=clip.clip_id, src_in=0.5, src_out=2.5, framing="fill")
    meta = EditMeta(width=360, height=640, fps=30, fade_in=0, fade_out=0)
    plain = EDL(project_dir=str(project.root), meta=meta, segments=[seg])
    titled = plain.model_copy(update={"motion": [MotionGraphic(kind="title", start=0.6, end=1.9, text="HELLO",
                                                               style="slam")]})
    a = render_edl(plain, project, cfg, quality="final", out_path=tmp_path / "plain.mp4")
    b = render_edl(titled, project, cfg, quality="final", out_path=tmp_path / "titled.mp4")
    assert not [w for w in b.warnings if "still text" in w or "left out" in w] and b.duration == pytest.approx(a.duration)
    made = sorted((project.root / "motion").glob("title-*.mov"))
    assert made and "a" in ffprobe_json(made[-1])["streams"][0]["pix_fmt"].replace("yuv", "")  # it has an alpha

    def frame(path, t):
        import subprocess

        raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", f"{t}", "-i", str(path), "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(640, 360, 3).astype(np.float32)

    before = np.abs(frame(a.output, 0.3) - frame(b.output, 0.3)).mean()
    during = np.abs(frame(a.output, 1.3) - frame(b.output, 1.3))
    assert before < 1.5  # nothing before it comes in
    middle, corner = during[260:380, 40:320].mean(), during[:60, :60].mean()
    assert middle > 8 and corner < 1.5  # the title is there, in the middle, and nowhere else


def test_a_title_asked_for_in_plain_words_is_seen_at_once_and_made(studio_film):
    studio, client, csrf, pid, work = studio_film
    from vidosuper.studio import agent

    client.post(f"/p/{pid}/talk", data={"csrf": csrf, "text": "add a title 'Beach Days' at the start"},
                headers={"Accept": "application/json"})
    said = agent.thread(studio.db, pid)[-1]["body"]
    assert said.get("fast") and 'A title "Beach Days" comes in on shot 1' in said["text"]
    _drain_all(studio)
    tl = client.get(f"/api/p/{pid}/preview?which=draft").json()
    title = next(x for x in tl["motion"] if x["block"] == "title")
    assert title["start"] == pytest.approx(tl["shots"][0]["start"], abs=0.05) and title["vars"]["text"] == "Beach Days"
    assert client.get("/motion/title.js").headers["content-type"].startswith("text/javascript")
    assert client.get("/motion/fonts.css").status_code == 200 and client.get("/motion/..%2Fplace.py").status_code == 404
    # the next ask is made on the draft they're watching: captions join the title (on a film that plays only music,
    # said plainly: there is nothing to caption)
    client.post(f"/p/{pid}/talk", data={"csrf": csrf, "text": "add captions"}, headers={"Accept": "application/json"})
    _drain_all(studio)
    draft = studio.draft_of(studio.db.project(pid))
    assert [g.kind for g in studio.draft_plan(studio.db.project(pid)).graphics] == ["title", "captions"]
    assert draft["said"][0] == 'A title "Beach Days" comes in on shot 1'
    assert any(line.startswith("Nobody is heard in this film") for line in draft["said"])
    if not motion.available():
        return
    # "make it": the version is rendered with the title on it, and keeps it for the changes after
    client.post(f"/p/{pid}/draft/make", data={"csrf": csrf})
    _drain_all(studio)
    project = studio.db.project(pid)
    assert project["version"] == 2
    edl = EDL.model_validate_json(next(work.glob("out/v2-*.edl.json")).read_text(encoding="utf-8"))
    assert [g.kind for g in edl.motion] == ["title", "captions"] and edl.motion[0].text == "Beach Days"
    from vidosuper.workspace import Project

    assert list((Project(work / "clips", studio.cfg).root / "motion").glob("title-*.mov"))  # made by the engine


def test_every_new_graphic_has_its_place_and_its_moment():
    segs = [Segment(id="s01", clip_id="a", src_in=0, src_out=3), Segment(id="s02", clip_id="b", src_in=0, src_out=3)]
    edl = tall(segs, [
        MotionGraphic(kind="transition", start=2.7, end=3.3, style="iris"),
        MotionGraphic(kind="kinetic", start=3.0, end=5.5, text="never stop exploring", beats=[3.0, 3.5, 4.0, 9.0]),
        MotionGraphic(kind="callout", start=0.5, end=2.5, text="Opera House", points=[[0.4, 0.4]],
                      track=[[0.5, 0.4, 0.4], [1.5, 0.45, 0.42], [2.5, 0.5, 0.44], [7.0, 0.9, 0.9]]),
        MotionGraphic(kind="route", start=3.0, end=6.0, names=["Sydney", "Wollongong", "Kiama"]),
        MotionGraphic(kind="accent", start=0.0, end=2.0, style="lines", beats=[0.5, 1.0]),
        MotionGraphic(kind="title", start=0.2, end=2.0, text="SYDNEY", behind=True, points=[[0.5, 0.5]])])
    tl = build_timeline(edl, defaultdict(lambda: None))
    by = {x.block: x for x in place.layers(edl, tl)}
    assert by["transition"].variables["cut"] == pytest.approx(0.3) and by["transition"].variables["style"] == "iris"
    assert by["kinetic"].variables["beats"] == [0.0, 0.5, 1.0]  # beats outside it are dropped
    assert by["callout"].variables["track"] == [[0.0, 0.4, 0.4], [1.0, 0.45, 0.42], [2.0, 0.5, 0.44]]
    route = by["route"].variables
    assert route["names"] == ["Sydney", "Wollongong", "Kiama"] and len(route["points"]) == 3
    top, bottom, _ = place.SAFE["tall"]
    assert all(top < y < 1 - bottom for _, y in route["points"])  # stops inside the safe area
    assert by["accent"].variables["beats"] == [0.5, 1.0]
    behind = by["title"].variables  # big, across the upper frame, at the person's head
    assert behind["y"] == 0.5 and behind["size"] > place.TITLE_SIZE["tall"] * 2
    for block in ("transition", "kinetic", "callout", "route", "accent"):
        assert motion.asset(f"{block}.js") and motion.asset(f"{block}.css")


def test_a_point_is_followed_as_the_camera_pans():
    rng = np.random.default_rng(3)
    world = (rng.random((900, 2400, 3)) * 255).astype(np.uint8)
    import cv2

    world = cv2.GaussianBlur(world, (0, 0), 2)  # texture the tracker can hold on to

    def read(cid, seconds):  # the camera pans right 200 px a second over a wide scene
        x = int(200 + seconds * 200)
        return world[:, x:x + 1600].copy()

    edl = tall([Segment(id="s01", clip_id="a", src_in=0, src_out=3)], w=1600, h=900)
    tl = build_timeline(edl, defaultdict(lambda: None))
    track = place.follow(tl, read, 0.0, 2.0, 0.5, 0.5, 1600 / 900)
    assert len(track) >= 15 and track[0][1:] == [0.5, 0.5]
    t, x, y = track[-1]  # 2 s later the scene has moved 400 px left: 0.25 of the width
    assert t == pytest.approx(2.0, abs=0.11) and x == pytest.approx(0.5 - 400 * t / 2 / 1600, abs=0.02)
    assert y == pytest.approx(0.5, abs=0.02)


def test_plain_words_add_the_new_graphics():
    def read(text):
        return [(o.op, o.shot, o.text, o.style, o.want, o.off, o.behind) for o in versions.read_simple(text, 10).ops]

    assert read("add an iris transition into shot 4") == [("transition", 4, "", "iris", "", False, False)]
    assert read("circle the Opera House in shot 2") == [("callout", 2, "Opera House", "circle", "Opera House", False, False)]
    assert read("box the bridge in shot 3 'Harbour Bridge'") == [("callout", 3, "Harbour Bridge", "box", "bridge", False, False)]
    assert read("add a route from sydney to kiama via wollongong") == [("route", 1, "Sydney|Wollongong|Kiama", "", "", False, False)]
    assert read("add kinetic text 'never stop exploring' on shot 3") == [("kinetic", 3, "never stop exploring", "", "", False, False)]
    assert read("light leaks on shot 4") == [("accent", 4, "", "leak", "", False, False)]
    assert read("remove the transitions") == [("transition", 1, "", "", "", True, False)]
    assert read("add a title 'Harbour Days' behind me") == [("title", 1, "Harbour Days", "", "", False, True)]
    pool = [PoolMoment(key=f"m{i}", clip="c01", start=i * 2.0, end=i * 2.0 + 1.5) for i in range(1, 6)]
    plan = DesignPlan(name="t", idea="i", pool=pool, plan=[PlanShot(kind="cut", beats=2, key=f"m{i}") for i in range(1, 6)])
    ctx = SimpleNamespace(beats=SimpleNamespace(period=0.5, drop=None), facts={})
    done = versions.apply(plan, ctx, [*versions.read_simple("circle the ferry in shot 4", 5).ops,
                                      ChangeOp(op="transition", shot=1), ChangeOp(op="route", shot=2, text="Sydney")])
    assert [(g.kind, g.shot, g.want) for g in done.plan.graphics] == [("callout", 4, "ferry")]
    assert done.skipped == ["transition on shot 1: a transition goes into a shot after the first", "route on shot 2: which places?"]
    moved = versions.apply(done.plan, ctx, [ChangeOp(op="swap", shot=4, other=2)])
    assert [g.shot for g in moved.plan.graphics] == [2]  # the callout goes with the ferry's picture
    slots = [versions.Slot(PlanShot(kind="cut", beats=2, key=k), n) for n, k in ((1, "m1"), (2, "new"), (3, "m3"))]
    marks = [PlanGraphic(kind="callout", shot=2, want="ferry"), PlanGraphic(kind="lowerthird", shot=2, text="Quay")]
    after_replace = versions.placed(marks, slots, 3, {1: "m1", 2: "m2", 3: "m3"})
    assert [(g.kind, g.shot) for g in after_replace] == [("lowerthird", 2)]  # the callout's subject is gone


def test_new_graphics_asked_for_in_plain_words_reach_the_draft(studio_film):
    studio, client, csrf, pid, work = studio_film
    for ask in ("add an iris transition into shot 2", "add a route from sydney to kiama via wollongong",
                "circle the boat in shot 1"):
        client.post(f"/p/{pid}/talk", data={"csrf": csrf, "text": ask}, headers={"Accept": "application/json"})
        _drain_all(studio)
    draft = studio.draft_of(studio.db.project(pid))
    edl = EDL.model_validate_json((work / "out" / draft["edl"]).read_text(encoding="utf-8"))
    kinds = {g.kind: g for g in edl.motion}
    assert set(kinds) == {"transition", "route"}  # the callout: nothing here can find a boat (no watcher)
    tl = client.get(f"/api/p/{pid}/preview?which=draft").json()
    cut = tl["shots"][1]["start"]
    assert kinds["transition"].start == pytest.approx(cut - 0.3, abs=0.05) and kinds["route"].names == ["Sydney", "Wollongong", "Kiama"]
    assert {x["block"] for x in tl["motion"]} == {"transition", "route"}
    assert any("couldn't find the boat in shot 1" in line for line in draft["said"])


def test_an_edit_style_is_their_own_film_in_seconds(studio_film):
    studio, client, csrf, pid, work = studio_film
    page = client.get(f"/p/{pid}").text
    assert 'id="styles"' in page and page.count("data-style-preview") == 6 and "Camcorder" in page
    r = client.post(f"/p/{pid}/style", data={"csrf": csrf, "style": "hype"}, follow_redirects=False)
    assert "msg=" not in r.headers["location"]
    _drain_all(studio)
    project = studio.db.project(pid)
    plan = studio.draft_plan(project)
    assert plan.style == "hype" and any(g.kind == "title" and g.style == "slam" and g.by == "style" for g in plan.graphics)
    edl = EDL.model_validate_json((work / "out" / studio.draft_of(project)["edl"]).read_text(encoding="utf-8"))
    assert edl.meta.look == "punchy" and edl.motion[0].kind == "title" and edl.motion[0].accent == "#ff5a36"
    # another style on the draft replaces the first one's graphics; one they asked for stays
    client.post(f"/p/{pid}/talk", data={"csrf": csrf, "text": "add a route from sydney to kiama"},
                headers={"Accept": "application/json"})
    _drain_all(studio)
    client.post(f"/p/{pid}/talk", data={"csrf": csrf, "text": "make it cinematic"}, headers={"Accept": "application/json"})
    _drain_all(studio)
    plan = studio.draft_plan(studio.db.project(pid))
    assert plan.style == "cinematic" and {(g.kind, g.style, g.by) for g in plan.graphics} == {
        ("route", "", ""), ("title", "mask", "style"), ("accent", "sweep", "style")}
    assert "In your draft" in client.get(f"/p/{pid}").text
    assert client.post(f"/p/{pid}/style", data={"csrf": csrf, "style": "nope"}, follow_redirects=False) \
        .headers["location"].count("msg=") == 1


def test_ready_to_post_scores_the_version_and_fixes_its_weak_parts(studio_film):
    studio, client, csrf, pid, work = studio_film
    board = studio.ready_of(studio.db.project(pid))
    assert board and 0 <= board["score"] <= 100 and board["verdict"]
    parts = {p["id"]: p for p in board["parts"]}
    assert {"hook", "text", "pace", "length", "person"} <= set(parts) and "captions" not in parts  # nobody speaks
    assert parts["text"]["score"] < 0.5 and parts["text"]["fix_says"] == "Add a title"
    page = client.get(f"/p/{pid}").text
    assert "Ready to post?" in page and f"{board['score']}/100" in page and "Add a title" in page
    r = client.post(f"/p/{pid}/fix", data={"csrf": csrf, "part": "text"}, follow_redirects=False)
    assert "msg=" not in r.headers["location"]
    _drain_all(studio)
    plan = studio.draft_plan(studio.db.project(pid))
    assert [(g.kind, g.style, g.text) for g in plan.graphics] == [("title", "slam", "Beach")]
    assert "msg=" in client.post(f"/p/{pid}/fix", data={"csrf": csrf, "part": "pace"}, follow_redirects=False).headers["location"]


def test_the_score_weighs_what_feeds_reward():
    from vidosuper.studio import ready

    facts = {"c01": SimpleNamespace(moments=[{"start": 0, "end": 5, "hook": 3, "place": "Quay"},
                                             {"start": 5, "end": 10, "hook": 9, "place": "Bridge"},
                                             {"start": 10, "end": 15, "hook": 5, "place": "Quay"}])}
    pool = [PoolMoment(key=f"m{i}", clip="c01", start=i * 5.0 + 1, end=i * 5.0 + 3) for i in range(3)]
    plan = DesignPlan(name="t", idea="i", pool=pool, plan=[PlanShot(kind="cut", beats=2, key=f"m{i}") for i in range(3)])
    cards = [{"start": k * 1.2, "end": (k + 1) * 1.2, "people": "near" if k == 0 else "none"} for k in range(3)]
    weak = ready.assess(plan, facts, cards, [], 3.6, True, speech=True)
    parts = {p["id"]: p for p in weak["parts"]}
    assert parts["hook"]["fix"] == {"op": "move", "shot": 2, "other": 1} and parts["hook"]["score"] == 0.3
    assert parts["captions"]["fix"] == {"op": "captions", "shot": 1} and parts["loop"]["score"] == 1.0
    assert parts["length"]["score"] < 1 and parts["person"]["score"] == 1.0
    fixed = ready.assess(plan, facts, cards, [MotionGraphic(kind="title", start=0.1, end=2, text="Hi"),
                                              MotionGraphic(kind="captions", start=0, end=3, words=[CaptionWord(w="hi", s=1, e=1.2)])],
                         12.0, True, speech=True)
    assert fixed["score"] > weak["score"]


def test_every_move_makes_its_sound_on_its_frame():
    from vidosuper.motion.sound import graphic_sounds

    edl = tall([Segment(id="s01", clip_id="a", src_in=0, src_out=6)], [
        MotionGraphic(kind="transition", start=2.7, end=3.3, style="panels"),
        MotionGraphic(kind="transition", start=4.7, end=5.3, style="flash"),
        MotionGraphic(kind="title", start=0.2, end=2.0, text="HI", style="slam"),
        MotionGraphic(kind="kinetic", start=3.0, end=5.0, text="a b", beats=[3.0, 3.5]),
        MotionGraphic(kind="captions", start=0, end=6)])
    got = [(x.source, round(x.at, 2)) for x in graphic_sounds(edl)]
    assert ("sfx:whoosh", 2.6) in got  # its peak on the cut at 3.0
    assert ("sfx:impact", 5.0) in got and ("sfx:riser", 3.6) in got  # a riser into the hit on the flash cut
    assert ("sfx:impact", round(0.2 + 0.08 + 8 / 30, 2)) in got  # as the slam lands
    assert [a for s, a in got if s == "sfx:tick"] == [3.0, 3.5]
    assert len(got) == 6  # captions make no sound


def test_exports_and_platforms(studio_film, tmp_path):
    from vidosuper.studio import agent, exports

    studio, client, csrf, pid, work = studio_film
    for kind in ("feed", "gif", "thumb", "mp3"):
        assert agent.export_asked(f"export a {dict(feed='4:5', thumb='thumbnail').get(kind, kind)}") == kind
        client.post(f"/p/{pid}/export", data={"csrf": csrf, "kind": kind})
        _drain_all(studio)
    made = {x["kind"]: x["file"] for x in studio.exports_of(studio.db.project(pid))}
    for kind in ("feed", "gif", "thumb", "mp3"):
        assert made[kind] and (work / "out" / made[kind]).stat().st_size > 1000
        assert client.get(f"/m/{pid}/out/{made[kind]}").status_code == 200
    feed = ffprobe_json(work / "out" / made["feed"])["streams"][0]
    assert (feed["width"], feed["height"]) == (1080, 1350)
    # nobody is heard in this film: no captions file, said plainly
    client.post(f"/p/{pid}/export", data={"csrf": csrf, "kind": "srt"})
    _drain_all(studio)
    assert not {x["kind"]: x["file"] for x in studio.exports_of(studio.db.project(pid))}["srt"]
    # "make it for Instagram": this 16:9 film gets a 9:16 version, and the next film starts in 9:16
    client.post(f"/p/{pid}/talk", data={"csrf": csrf, "text": "make it for instagram"}, headers={"Accept": "application/json"})
    said = agent.thread(studio.db, pid)[-1]["body"]["text"]
    assert said.startswith("For Instagram Reels: 9:16") and "making this film in 9:16" in said
    _drain_all(studio)
    project = studio.db.project(pid)
    assert project["shapes"] == "9:16" and project["version"] == 2
    assert list(work.glob("out/v2-9x16.mp4"))
    assert 'value="9:16" checked' in client.get("/new").text


def test_subtitles_break_like_people_read():
    from vidosuper.studio import exports

    words = [CaptionWord(w=w, s=k * 0.3, e=k * 0.3 + 0.25) for k, w in enumerate("this is the best view in Sydney. Look at that".split())]
    text = exports.srt(EDL(project_dir="x", segments=[Segment(id="s", clip_id="a", src_in=0, src_out=1)]), words)
    blocks = text.strip().split("\n\n")
    assert blocks[0].splitlines()[1:] == ["00:00:00,000 --> 00:00:02,100", "this is the best view in Sydney."]
    assert blocks[1].splitlines()[2] == "Look at that"


def test_the_cut_opens_in_an_editors_own_software(studio_film):
    import xml.etree.ElementTree as ET

    studio, client, csrf, pid, work = studio_film
    for kind in ("fcpxml", "edl"):
        client.post(f"/p/{pid}/export", data={"csrf": csrf, "kind": kind})
        _drain_all(studio)
    made = {x["kind"]: x["file"] for x in studio.exports_of(studio.db.project(pid))}
    root = ET.parse(work / "out" / made["fcpxml"]).getroot()
    clips = root.findall(".//spine/asset-clip")
    assets = {a.get("id"): a for a in root.findall(".//resources/asset")}
    assert root.get("version") == "1.10" and clips and all(c.get("ref") in assets for c in clips)
    assert all(a.find("media-rep").get("src").startswith("file:") for a in assets.values())
    edl = (work / "out" / made["edl"]).read_text(encoding="utf-8").splitlines()
    events = [ln for ln in edl if ln[:3].isdigit()]
    assert edl[0].startswith("TITLE: Beach") and len(events) == len(clips)
    assert all(len(ln.split()[-4:]) == 4 and ln.split()[-1].count(":") == 3 for ln in events)


def test_a_client_reviews_a_version_without_an_account(studio_film):
    from fastapi.testclient import TestClient

    from vidosuper.studio import agent
    from vidosuper.studio.web.app import create_app

    studio, client, csrf, pid, work = studio_film
    client.post(f"/p/{pid}/review", data={"csrf": csrf})
    token = studio.db.one("SELECT token FROM review_links WHERE project_id=?", (pid,))["token"]
    assert f"/r/{token}" in client.get(f"/p/{pid}").text
    guest = TestClient(create_app(studio))  # nobody signed in
    page = guest.get(f"/r/{token}")
    assert page.status_code == 200 and "Beach" in page.text and page.headers["x-robots-tag"].startswith("noindex")
    assert guest.get(f"/r/{token}/video").status_code == 200
    r = guest.post(f"/r/{token}/note", data={"at": "4.2", "name": "Mia (client)", "text": "make the logo bigger here"},
                   follow_redirects=False)
    assert "your%20note%20is%20with%20them" in r.headers["location"]
    assert guest.post(f"/r/{token}/note", data={"at": "1", "text": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    notes = studio.review_notes(studio.db.project(pid))
    assert [(n["at"], n["name"], n["text"]) for n in notes] == [(4.2, "Mia (client)", "make the logo bigger here")]
    text, _ = agent.view(studio, studio.db.project(pid), studio.user(studio.db.project(pid)["user_id"]), "hi", [], [])
    assert "Review notes from their clients" in text and "at 0:04, Mia (client): make the logo bigger here" in text
    client.post(f"/p/{pid}/notes/{notes[0]['id']}/done", data={"csrf": csrf})
    assert not studio.review_notes(studio.db.project(pid), open_only=True)
    client.post(f"/p/{pid}/review/revoke", data={"csrf": csrf, "token": token})
    assert guest.get(f"/r/{token}").status_code == 404 and guest.get(f"/r/{token}/video").status_code == 404


def test_a_brand_kit_dresses_every_graphic(studio_film):
    import io

    from PIL import Image

    studio, client, csrf, pid, work = studio_film
    logo = io.BytesIO()
    Image.new("RGBA", (300, 120), (255, 90, 54, 255)).save(logo, "PNG")
    r = client.post("/style/brand", data={"csrf": csrf, "name": "Wavecut", "color": "#ff5a36", "font": "serif"},
                    files={"logo": ("logo.png", logo.getvalue(), "image/png")}, follow_redirects=False)
    assert "brand%20is%20saved" in r.headers["location"]
    assert 'class="brand-logo" src="data:image/png;base64,' in client.get("/style").text
    client.post(f"/p/{pid}/talk", data={"csrf": csrf, "text": "add a title 'Beach Days' at the start"},
                headers={"Accept": "application/json"})
    _drain_all(studio)
    edl = EDL.model_validate_json((work / "out" / studio.draft_of(studio.db.project(pid))["edl"]).read_text(encoding="utf-8"))
    title = next(g for g in edl.motion if g.kind == "title")
    logo_g = next(g for g in edl.motion if g.kind == "logo")
    assert title.accent == "#ff5a36" and title.font == "serif" and logo_g.image.startswith("data:image/png;base64,")
    tl = client.get(f"/api/p/{pid}/preview?which=draft").json()
    blocks = {x["block"]: x for x in tl["motion"]}
    assert blocks["title"]["vars"]["font"] == "serif" and blocks["logo"]["vars"]["x"] > 0.7
    assert "msg=" in client.post("/style/brand", data={"csrf": csrf, "color": "red"}, follow_redirects=False).headers["location"]


def test_the_timeline_shows_the_film_and_takes_changes_by_hand(studio_film):
    studio, client, csrf, pid, work = studio_film
    assert 'data-timeline="' in client.get(f"/p/{pid}").text
    tl = client.get(f"/api/p/{pid}/timeline?which=1").json()
    assert tl["shots"] and tl["shots"][0]["n"] == 1 and tl["shots"][-1]["end"] == pytest.approx(tl["duration"], abs=0.1)
    assert all(a["end"] <= b["start"] + 0.6 for a, b in zip(tl["shots"], tl["shots"][1:]))
    r = client.post(f"/api/p/{pid}/edit", json={"ops": [{"op": "longer", "shot": 1, "beats": 1}],
                                                 "said": ["Shot 1 is 1 beat longer"]}, headers={"X-CSRF": csrf})
    assert r.status_code == 200
    _drain_all(studio)
    draft = studio.draft_of(studio.db.project(pid))
    assert draft and "longer" in " ".join(draft["said"])
    tl = client.get(f"/api/p/{pid}/timeline?which=draft").json()
    assert tl["which"] == "draft" and tl["shots"]
    # remembered as what they do by hand: the learning pass reads it
    row = studio.db.one("SELECT stage, text FROM feedback WHERE project_id=? AND stage='manual'", (pid,))
    assert row["text"] == "Shot 1 is 1 beat longer"
    assert client.post(f"/api/p/{pid}/edit", json={"ops": [{"op": "longer", "shot": 1}]}, headers={"X-CSRF": "x"}).status_code == 403
