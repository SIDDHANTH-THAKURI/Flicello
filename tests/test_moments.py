"""Designed moments: planned from what is said as before -> change -> after, held to it, laid into the film."""

import json
import subprocess

import pytest

from vidosuper.motion import moments as mo

WORDS = [(0.5, 0.8, "I"), (0.8, 1.0, "have"), (1.0, 1.1, "a"), (1.1, 1.4, "blue"), (1.4, 1.9, "circle"),
         (2.0, 2.3, "slowly"), (2.3, 2.8, "turning"), (2.8, 3.3, "orange"), (3.5, 3.8, "and"), (3.8, 4.4, "growing")]


def _good() -> mo.FilmDesign:
    return mo.FilmDesign(look="deep navy #0b1a3a, warm orange #ff8a1f, matte paper", moments=[mo.Moment(
        start=0.9, end=7.0, said="a blue circle slowly turning orange and growing",
        before="a blue circle, a fifth of the frame wide, beside them at shoulder height",
        changes=[mo.Change(word="turning", at=2.3, becomes="the circle turns orange", over=2.0),
                 mo.Change(word="growing", at=3.8, becomes="it grows slowly to twice its size", over=3.0)],
        after="a big orange circle beside them", where="right of their head")])


def test_the_plan_is_held_to_what_is_really_said():
    assert mo.check(_good(), WORDS, 10.0, "talk", "") == []
    bad = _good()
    bad.moments[0].changes[0] = mo.Change(word="exploding", at=2.3, becomes="it explodes")
    bad.moments[0].text = "SLOWLY EXPANDS"
    problems = mo.check(bad, WORDS, 10.0, "talk", "")
    assert any("'exploding' isn't said" in p for p in problems)
    assert any("words on screen" in p for p in problems)  # nobody asked for words
    assert mo.check(bad.model_copy(update={"moments": [bad.moments[0].model_copy(update={"changes": []})]}),
                    WORDS, 10.0, "talk", "add captions please") == []  # they asked for words: allowed


def test_overlaps_and_a_voice_film_with_gaps_are_caught():
    two = _good().model_copy(update={"moments": [_good().moments[0], _good().moments[0].model_copy(
        update={"start": 5.0, "end": 9.0, "changes": []})]})
    assert any("overlaps" in p for p in mo.check(two, WORDS, 10.0, "talk", ""))
    gaps = _good().model_copy(update={"moments": [_good().moments[0]]})
    problems = mo.check(gaps, WORDS, 10.0, "voice", "")
    assert any("must start at 0.00" in p for p in problems) and any("must end at 10.00" in p for p in problems)


def test_changes_snap_to_the_start_of_their_word():
    d = _good()
    d.moments[0].changes[0].at = 2.5  # near "turning" (2.3)
    assert mo.snap(d, WORDS, 10.0).moments[0].changes[0].at == 2.3


def test_planning_asks_again_with_what_was_wrong_until_it_holds():
    asked = []
    bad = _good().model_copy(deep=True)
    bad.moments[0].changes[0].word = "spinning"
    replies = [bad.model_dump_json(), _good().model_dump_json()]

    def ask(system, text):
        asked.append(text)
        return "Here it is: " + replies[len(asked) - 1]

    design = mo.plan(WORDS, 10.0, "", "talk", 1080, 1920, ask_claude=ask)
    assert len(asked) == 2 and "'spinning' isn't said" in asked[1]
    assert design.moments[0].changes[0].word == "turning"
    assert "every `text` is empty" in asked[0]  # no words unless asked, said up front


def test_a_plan_that_never_holds_is_repaired_not_dropped():
    bad = _good().model_copy(deep=True)
    bad.moments[0].text = "BLUE"
    bad.moments[0].end = 12.0
    design = mo.plan(WORDS, 10.0, "", "talk", 1080, 1920, ask_claude=lambda s, t: bad.model_dump_json(), tries=2)
    assert design.moments and design.moments[0].text == "" and design.moments[0].end == 10.0


def test_the_designer_gets_the_contract_and_the_director_checks_it_over_time():
    d = _good()
    m = d.moments[0]
    text = mo.brief_text(m, d, "make my words come alive", "talk")
    assert "BEFORE (as the scene opens, 0.00 s): a blue circle" in text
    assert 'on "turning" at 1.40 s, over about 2 s: the circle turns orange' in text
    assert "TEXT ON SCREEN: none at all" in text
    points = mo.checkpoints(m)
    times = [p["t"] for p in points]
    assert times == sorted(times) and points[0]["expect"].startswith("BEFORE") and points[-1]["expect"].startswith("AFTER")
    assert points[0]["t"] < 1.4  # the opening state is checked before the circle turns
    assert any("halfway" in p["expect"] and "grows" in p["expect"] for p in points)  # slow changes checked mid-way
    assert any(abs(p["t"] - (1.4 + 2.0 + 0.2)) < 0.01 for p in points)  # and once landed


def test_a_moment_is_reused_only_for_the_same_contract_look_and_picture():
    d = _good()
    a = mo.digest(d.moments[0], d, "base:1")
    assert a == mo.digest(d.moments[0].model_copy(update={"why": "other"}), d, "base:1")
    assert a != mo.digest(d.moments[0].model_copy(update={"after": "a red square"}), d, "base:1")
    assert a != mo.digest(d.moments[0], d, "base:2")


def test_words_are_wanted_only_when_asked():
    assert mo.wants_text("add captions") and mo.wants_text("put a title on it")
    assert not mo.wants_text("make the shapes come alive")
    assert not mo.wants_text("no text please, just the shapes")


def test_the_scene_brief_says_no_words_unless_asked(monkeypatch, tmp_path):
    from vidosuper.motion import scenes

    brief = scenes.Brief(ask="x", start=0, end=4, width=1080, height=1920, spec=[{"t": 0.5, "expect": "BEFORE: a ball"}])
    text = scenes._content(brief, None, None)[0]["text"]
    assert "Words allowed on screen: none at all." in text and "0.50 s: BEFORE: a ball" in text
    assert "unless the brief's TEXT line allows them" in scenes._system()


# ---------------------------------------------------------------- the picture

def _video(path, colour, seconds, size="108x192"):
    from vidosuper.ffmpeg import ffmpeg_bin

    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={colour}:s={size}:r=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", str(seconds), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def _colour_at(path, t):
    import cv2

    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, frame = cap.read()
    cap.release()
    assert ok
    return [int(v) for v in frame[frame.shape[0] // 2, frame.shape[1] // 2]]  # BGR


def _seconds(path):
    import cv2

    cap = cv2.VideoCapture(str(path))
    n, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT), cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return n / fps


def test_the_moments_are_laid_into_the_film_frame_for_frame(tmp_path):
    from vidosuper.work import moments_film as mf

    film = _video(tmp_path / "film.mp4", "blue", 6)
    red = _video(tmp_path / "red.mp4", "red", 1.5)
    green = _video(tmp_path / "green.mp4", "green", 1.0)
    out = mf.splice_all(film, [(4.0, 5.0, green), (1.0, 2.5, red)], tmp_path / "out.mp4")
    assert abs(_seconds(out) - 6.0) < 0.1
    b, g, r = _colour_at(out, 1.7)
    assert r > 150 and b < 80  # red where the first moment is
    b, g, r = _colour_at(out, 4.5)
    assert g > 80 and r < 80 and b < 80  # green where the second is
    b, g, r = _colour_at(out, 3.2)
    assert b > 150 and r < 80  # their own picture between


def test_a_voice_film_is_designed_on_an_empty_frame_with_their_voice(tmp_path):
    from vidosuper.work import moments_film as mf

    voice = tmp_path / "voice.wav"
    from vidosuper.ffmpeg import ffmpeg_bin

    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000",
                    "-t", "3", str(voice)], check=True)
    out = mf.base({"duration": 3.0, "audio": str(voice), "video": None}, "16:9", tmp_path / "base.mp4")
    import cv2

    cap = cv2.VideoCapture(str(out))
    assert (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))) == (1920, 1080)
    cap.release()
    assert abs(_seconds(out) - 3.0) < 0.1


def test_a_talk_is_framed_to_its_shape(tmp_path):
    from vidosuper.work import moments_film as mf

    clip = _video(tmp_path / "talk.mp4", "white", 2, size="320x240")
    out = mf.base({"duration": 2.0, "video": str(clip), "audio": str(clip)}, "9:16", tmp_path / "base.mp4")
    import cv2

    cap = cv2.VideoCapture(str(out))
    assert (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))) == (1080, 1920)
    cap.release()


def test_one_moment_failing_never_loses_the_others(tmp_path, monkeypatch):
    from vidosuper.work import moments_film as mf

    d = _good()
    d = d.model_copy(update={"moments": [d.moments[0], d.moments[0].model_copy(update={"start": 7.5, "end": 9.5,
                                                                                        "changes": []})]})

    def one(m, design, picture, words, ask, kind, folder, library, rounds=3, progress=None):
        if m.start > 7:
            raise RuntimeError("the renderer fell over")
        return {"start": m.start, "end": m.end, "video": "x.mp4", "rounds": [{"ok": True}]}

    monkeypatch.setattr(mf, "design_one", one)
    made, notes = mf.design_all(d, tmp_path / "p.mp4", WORDS, "", "talk", tmp_path, tmp_path)
    assert len(made) == 1 and len(notes) == 1 and "Moment 2 of 2 (0:07)" in notes[0]
    rows = mf.requirements(d, made, notes, "")
    assert rows[0]["level"] == "warn" and "1 of 2 moments" in rows[0]["detail"]
    assert rows[2]["level"] == "ok"


def test_a_designed_moment_is_kept_and_not_designed_again(tmp_path, monkeypatch):
    from vidosuper.work import moments_film as mf

    picture = tmp_path / "base.mp4"
    picture.write_bytes(b"x" * 10)
    d = _good()
    key = mo.digest(d.moments[0], d, f"{picture.name}:{picture.stat().st_size}")
    (tmp_path / "m" / key).mkdir(parents=True)
    (tmp_path / "m" / key / "scene.mp4").write_bytes(b"v")
    (tmp_path / "m" / key / "meta.json").write_text(json.dumps({"digest": key, "start": 0.9, "end": 7.0,
                                                                "video": "scene.mp4", "rounds": [], "score": 9}))
    monkeypatch.setattr(mf.subprocess, "Popen", lambda *a, **k: pytest.fail("designed again"))
    meta = mf.design_one(d.moments[0], d, picture, WORDS, "", "talk", tmp_path / "m", tmp_path)
    assert meta["kept"] and meta["video"].endswith("scene.mp4")


def test_slowly_and_suddenly_hold_the_timing():
    quick = _good().model_copy(deep=True)
    quick.moments[0].changes[0].over = 0.5  # "slowly turning" made a quick flip
    assert any("they say it happens slowly" in p for p in mo.check(quick, WORDS, 10.0, "talk", ""))
    words = WORDS[:5] + [(2.0, 2.3, "suddenly"), (2.3, 2.8, "turning"), (2.8, 3.3, "orange.")]
    slow = mo.FilmDesign(look="x", moments=[mo.Moment(start=1.0, end=5.0, before="a blue circle", after="orange",
                                                      changes=[mo.Change(word="turning", at=2.3, becomes="orange",
                                                                         over=2.0)])])
    assert any("suddenly" in p for p in mo.check(slow, words, 10.0, "talk", ""))
    later = words + [(3.5, 3.7, "Then"), (3.7, 4.0, "it"), (4.0, 4.4, "splits")]
    assert mo._pace(later, 4.0) == ""  # a new sentence: "suddenly" was about the turn


def test_the_designer_shows_one_live_line_per_round_and_keeps_the_directors_notes_in_the_log():
    from vidosuper.work.moments_film import say

    assert say("brief: 8.2 s, 11 words, 6 frames, person in 6", 3) == "round 1 of 3: designing it and checking its frames"
    assert say("round 1: score 7, 7 problems (2186 s)", 3) == "round 1 of 3 scored 7/10: round 2 fixes 7 things"
    assert say("round 3: score 6, 9 problems (1500 s)", 3) == "round 3 of 3 scored 6/10: keeping the best round"
    assert say("   - at 0.80-8.12 s: the stage feels pasted over the footage", 3) is None  # a note: log only
    assert say("rendered in 41 s", 3) is None and say("rendering", 3) == "rendering it"


def test_a_talk_is_not_read_as_a_pace_of_cuts():
    from vidosuper.studio.web.app import note_read

    lines = note_read({"kind": "talk", "intent": "Everything I describe appears beside me", "pace": "", "cut_note": ""})
    assert not any("cuts" in line for line in lines) and "No words on screen unless you ask" in lines
    assert "Words on screen, as you asked" in note_read({"kind": "voice", "intent": "with the steps written on screen",
                                                          "pace": "", "cut_note": ""})
