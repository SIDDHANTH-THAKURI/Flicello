"""Animated films from a brief (motion/animated.py, motion/kits, motion/event_sound.py, music/arrange.py,
work/animated_in_studio.py): the music measured and cut on its bars, the plan and the timing held to their rules,
the frame-timing rules, the checks said plainly, the film page, its sounds from its events, the whole run with its
pitch and its review, and a made film becoming a film in the studio."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from vidosuper.ffmpeg import ffmpeg_bin
from vidosuper.motion import animated, event_sound, kits
from vidosuper.music import arrange

SR = 22050


def _beat_track(path: Path, bpm: float = 120.0, soft: int = 8, drums: int = 8, tail: int = 4) -> Path:
    """A track with a steady beat: soft plucks for `soft` bars, then kick and noise snare for `drums` bars, then soft."""
    beat = 60.0 / bpm
    bars = soft + drums + tail
    n = int(bars * 4 * beat * SR) + SR
    y = np.zeros(n)
    t = np.arange(int(0.25 * SR)) / SR
    rng = np.random.default_rng(3)
    for k in range(bars * 4):
        i = int(k * beat * SR)
        bar = k // 4
        pluck = 0.25 * np.sin(2 * np.pi * 440 * t) * np.exp(-t / 0.08)
        y[i:i + len(t)] += pluck
        if soft <= bar < soft + drums:
            kick = 0.9 * np.sin(2 * np.pi * 60 * t * (1 - t)) * np.exp(-t / 0.07)
            y[i:i + len(t)] += kick
            if k % 2:
                y[i:i + len(t)] += 0.5 * rng.standard_normal(len(t)) * np.exp(-t / 0.05)
    pad = 0.08 * np.sin(2 * np.pi * 220 * np.arange(n) / SR)
    sf.write(str(path), (y + pad) / 1.6, SR)
    return path


def test_a_track_is_measured_bar_by_bar_and_cut_again_on_its_bars(tmp_path):
    track = _beat_track(tmp_path / "music.wav")
    m = arrange.measure(track)
    assert m["bpm"] == pytest.approx(120, abs=3)
    drums = [b.drums for b in m["bars"]]
    assert np.mean(drums[9:15]) > np.mean(drums[1:7]) + 0.3  # the drum bars are measured as drums
    assert m["drops"] and 7 <= m["drops"][0] <= 9  # the drums come in about bar 8
    text = arrange.describe(m)
    assert "Drops" in text and "Every bar" in text
    out = arrange.arrange(track, [[0, 3], [8, 11], [16, len(m["bars"]) - 1]], tmp_path / "cut.wav", m)
    got, sr = sf.read(str(out))
    # bar 0 keeps what comes before it; the pieces join with short crossfades, no gaps
    want = m["bars"][3].end + (m["bars"][11].end - m["bars"][8].start) + (m["duration"] - m["bars"][16].start)
    assert len(got) / sr == pytest.approx(want, abs=0.1)
    with pytest.raises(arrange.ArrangeError, match="bars 0-"):
        arrange.arrange(track, [[0, 400]], tmp_path / "bad.wav", m)
    data = arrange.motion_data(out, 30)
    assert len(data["env"]) == pytest.approx(len(got) / sr * 30, abs=3) and max(data["kick"]) <= 1.0
    assert data["period"] == pytest.approx(0.5, abs=0.02) and data["bars"]  # the cut keeps the beat exactly
    cut = arrange.measure(out)
    assert len(cut["bars"]) == 4 + 4 + (len(m["bars"]) - 16)
    assert cut["bars"][4].start == pytest.approx(m["bars"][4].start, abs=0.05)  # bar 8 now plays where bar 4 was
    assert cut["drops"] and cut["drops"][0] == 4


def _director(tmp_path: Path, seconds: float = 30.0) -> Path:
    folder = tmp_path / "director"
    folder.mkdir(parents=True)
    (folder / "settings.json").write_text(json.dumps({"seconds": seconds, "shape": "16:9", "kit": "paper",
                                                      "w": 1920, "h": 1080}))
    what = "A paper valley pops up at dawn; a filmmaker on a hill lifts a camera to the rising sun; birds cross."
    (folder / "plan.json").write_text(json.dumps({
        "title": "The Idea", "logline": "Every film starts as an idea.",
        "look": {"palette": ["#2d2a4a", "#ee8a6a", "#f6b38a", "#fbf6ec"], "notes": "dawn paper"},
        "cast": [{"id": "maker", "who": "a filmmaker in a beanie", "kit": "person"}],
        "music": {"bpm": 120, "styles": ["warm folk", "plucked guitar", "hopeful"], "avoid": [], "arc": "soft to big"},
        "scenes": [{"id": "dawn", "what": what, "lines": ["Every film starts as an idea."], "seconds": 14, "music": "soft"},
                   {"id": "stuck", "what": what, "lines": ["Then it gets stuck."], "seconds": 16, "music": "drop"}]}))
    return folder


def test_the_plan_and_the_timing_are_held_to_their_rules(tmp_path):
    folder = _director(tmp_path)
    plan, problems = animated.check_plan(folder)
    assert problems == []
    plan["scenes"][0]["lines"] = ["one two three four five six seven eight nine ten eleven twelve thirteen"]
    plan["scenes"][1]["what"] = "words"
    plan["cast"][0]["kit"] = "sticker"
    (folder / "plan.json").write_text(json.dumps(plan))
    _, problems = animated.check_plan(folder)
    assert any("too long for paper" in p for p in problems) and any("says too little" in p for p in problems)
    assert any("kit is one of" in p for p in problems)
    folder = _director(tmp_path / "b")
    (folder / "timing.json").write_text(json.dumps({"music": None, "seconds": 30.0, "drop": 16.0, "scenes": [
        {"id": "dawn", "start": 0.0, "end": 14.0, "in": "cut"}, {"id": "stuck", "start": 14.0, "end": 30.0, "in": "turn"}]}))
    _, problems = animated.check_timing(folder)
    assert problems == []
    (folder / "timing.json").write_text(json.dumps({"music": None, "seconds": 30.0, "scenes": [
        {"id": "stuck", "start": 0.0, "end": 1.0, "in": "wipe"}, {"id": "dawn", "start": 2.0, "end": 30.0}]}))
    _, problems = animated.check_timing(folder)
    assert any("plan's order" in p for p in problems) and any("lasts 1.0 s" in p for p in problems)
    assert any("'in' is one of" in p for p in problems) and any("must start where the last ended" in p for p in problems)


def test_the_ways_between_scenes_and_the_drop_are_written_as_kit_calls():
    js = animated.transitions_js({"drop": 20.5, "scenes": [{"id": "a", "start": 0, "end": 8, "in": "cut"},
                                                           {"id": "b", "start": 8, "end": 16, "in": "turn"},
                                                           {"id": "c", "start": 16, "end": 24, "in": "zoom"}]})
    assert 'transition("turn", 8.000, "a");' in js and 'transition("zoom", 16.000, "b");' in js
    assert "sfx(20.500, 'hit');" in js


def test_the_frame_timing_rules_are_said_with_their_line():
    text = ("{ const { C } = scene('dawn', 0, 8);\n"
            "  gsap.to(x, { y: 4 });\n"
            "  tl.call(() => swap());\n"
            "  const r = Math.random(); // const s = Math.random() in a comment is fine\n"
            "  setTimeout(go, 100);\n"
            "  sfx(bt(7) + 0.3, 'say', { sound: 'a crow cawing' }); sfx(land, 'drop', { gain: -2 });\n"
            "  sfx(Math.max(T, 2), 'ding');\n}")
    said = animated.static_problems(text, "dawn")
    assert any(s.startswith("line 2:") and "gsap.to" in s for s in said)
    assert any(s.startswith("line 3:") and "tl.call" in s for s in said)
    assert sum("Math.random" in s for s in said) == 1 and any(s.startswith("line 5:") for s in said)
    assert [s for s in said if "sfx" in s] == [s for s in said if s.startswith("line 7: sfx 'ding' isn't one of the sounds")]
    assert len([s for s in said if "sfx" in s]) == 1
    assert any("must call scene('other'" in s for s in animated.static_problems(text, "other"))


def test_the_checks_are_said_plainly_and_the_noise_left_out():
    report = {"lint": {"findings": [{"code": "missing_timeline_registry", "severity": "error"}]},
              "layout": {"findings": [
                  {"code": "text_occluded", "severity": "error", "time": 3.2, "text": "Hello", "message": "Text is hidden",
                   "selector": "#world inside #grain"},
                  {"code": "canvas_overflow", "severity": "info", "time": 4.0, "text": "final.mp4", "message": "off canvas"},
                  {"code": "container_overflow", "severity": "info", "time": 4.0, "message": "not for words"}]},
              "motion": {"findings": [{"code": "rotation_pivot_drift", "severity": "warning", "time": 2.0, "message": "x"}]},
              "craft": {"findings": [{"code": "film_silent", "severity": "warning"},
                                     {"code": "line_unread", "severity": "warning", "time": 9.0, "text": "Hi there",
                                      "message": "on screen 0.2 s"}]}}
    lines = animated.findings(report)
    assert lines == ['4.00 s canvas_overflow "final.mp4": off canvas', '9.00 s line_unread "Hi there": on screen 0.2 s']


def test_the_film_page_loads_the_kit_the_scenes_in_order_and_registers_its_timeline(tmp_path):
    a, b = tmp_path / "a" / "scene.js", tmp_path / "b" / "scene.js"
    for f, sid in ((a, "a"), (b, "b")):
        f.parent.mkdir()
        f.write_text(f"{{ const {{ C }} = scene('{sid}', 0, 1); }}")
    pg = animated.page(tmp_path, animated.film_of("9:16", 12.5), [a, b], "transition('turn', 6, 'a');",
                       {"env": [], "kick": []}, "film")
    html = (pg / "index.html").read_text(encoding="utf-8")
    assert '"w": 1080, "h": 1920' in html and 'data-duration="12.500"' in html
    assert html.index("scenes/00-a.js") < html.index("scenes/01-b.js") < html.index("transition('turn'")
    assert 'window.__timelines["main"] = tl;' in html and "data-layout-ignore" in html
    assert (pg / "kit.js").exists() and (pg / "gsap" / "gsap.min.js").exists() and list((pg / "fonts").glob("*.woff2"))


@pytest.mark.skipif(shutil.which("node") is None or not animated.PROBE.exists(), reason="needs Node and the motion engine")
def test_the_kit_runs_and_logs_the_sounds_of_what_happens(tmp_path):
    try:
        animated._browser()
    except Exception:  # noqa: BLE001
        pytest.skip("no renderer browser here")
    scene = tmp_path / "s" / "scene.js"
    scene.parent.mkdir()
    scene.write_text("""{
  const { C, cam, T, E } = scene('s', 0, 4);
  backdrop(C, '#f2c48a', '#eeb878');
  const t1 = strip(C, 'Hello paper', W / 2, 200, 72);
  stripIn(t1, 0.5);
  const box = labelBox(C, W / 2, 600, 400, 150, '#e9765b', 'A tool', 'does things', 'cube');
  stamp(box, 1.0);
  scissors(C, 300, 500, 1, [2.0, 2.5]);
  pageTurn(TOP, 3.5, 0.8);
}""")
    pg = animated.page(tmp_path, animated.film_of("16:9", 4.0), [scene], "", None, "t")
    seen = animated.probe(pg)
    assert seen["errors"] == [] and seen["timeline"] and seen["duration"] == pytest.approx(4.0)
    assert seen["scenes"] == {"s": [0, 4]}
    kinds = [e["type"] for e in seen["events"]]
    assert "flick" in kinds and "stamp" in kinds and kinds.count("snip") == 2 and "turn" in kinds


@pytest.mark.skipif(shutil.which("node") is None or not animated.PROBE.exists(), reason="needs Node and the motion engine")
def test_the_ways_between_scenes_take_the_top_page_away_and_never_show_an_empty_sheet(tmp_path):
    try:
        animated._browser()
    except Exception:  # noqa: BLE001
        pytest.skip("no renderer browser here")
    import cv2

    cols = {"a": "#d04040", "b": "#40a040", "c": "#4060d0", "d": "#e0c020"}
    timing = {"seconds": 8.0, "scenes": [{"id": "a", "start": 0.0, "end": 2.0, "in": "cut"},
                                         {"id": "b", "start": 2.0, "end": 4.0, "in": "turn"},
                                         {"id": "c", "start": 4.0, "end": 6.0, "in": "zoom"},
                                         {"id": "d", "start": 6.0, "end": 8.0, "in": "fold"}]}
    files = []
    for x in timing["scenes"]:
        f = tmp_path / x["id"] / "scene.js"
        f.parent.mkdir()
        f.write_text(f"{{ const sc = scene('{x['id']}', {x['start']}, {x['end']}); backdrop(sc.C, '{cols[x['id']]}', "
                     f"'{cols[x['id']]}'); exitAt(sc, W / 2, H / 2); }}")
        files.append(f)
    pg = animated.page(tmp_path, animated.film_of("16:9", 8.0, timing["scenes"]), files,
                       animated.transitions_js(timing), None, "ways")
    times = [2.0, 3.85, 5.8]
    try:
        pngs = animated.stills(pg, times, tmp_path / "stills")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no renderer here: {exc}")
    rgb = {k: np.array([int(v[i:i + 2], 16) for i in (1, 3, 5)], float) for k, v in cols.items()}

    def seen(img, ys, xs):  # the scene whose colour is nearest to this region's
        bgr = img[ys, xs].reshape(-1, 3).mean(axis=0)
        return min(rgb, key=lambda k: np.abs(rgb[k] - bgr[::-1]).sum())

    turn, iris, fold = (cv2.imread(str(p)) for p in pngs)
    h, w = turn.shape[:2]
    assert seen(turn, slice(h // 3, 2 * h // 3), slice(0, w // 12)) == "a"  # the page being turned, still on the left
    assert seen(turn, slice(h // 3, 2 * h // 3), slice(w - w // 12, w)) == "b"  # the next page under it, on the right
    cream = (np.abs(turn.astype(int) - np.array([207, 227, 239])).sum(axis=2) < 40).mean()  # the turned page's back
    assert cream < 0.35
    assert seen(iris, slice(h // 2 - 20, h // 2 + 20), slice(w // 2 - 20, w // 2 + 20)) == "c"  # through the hole
    assert seen(iris, slice(0, h // 12), slice(0, w // 12)) == "b"  # the page the hole is cut in
    assert seen(fold, slice(0, h // 6), slice(w // 3, 2 * w // 3)) == "d"  # what the folding page uncovers
    assert seen(fold, slice(h - h // 8, h), slice(w // 3, 2 * w // 3)) == "c"  # the page folding down


def test_the_sounds_are_made_from_the_events_and_never_clip(tmp_path):
    events = [{"t": 0.2, "type": "flap", "big": True}, {"t": 0.5, "type": "snip"}, {"t": 0.6, "type": "keys", "until": 1.4},
              {"t": 1.0, "type": "turn", "dur": 0.8}, {"t": 1.5, "type": "hit"}, {"t": 1.8, "type": "pop", "pitch": 4},
              *[{"t": 0.3, "type": "flap"} for _ in range(10)]]  # a pile of folds at once is one rustle
    out = tmp_path / "fx.wav"
    info = event_sound.render(events, 3.0, out)
    x, sr = sf.read(str(out))
    assert sr == 48000 and len(x) == 3 * 48000 and np.abs(x).max() <= 0.951
    assert np.abs(x[int(1.5 * sr):int(1.7 * sr)]).max() > 0.05  # the big hit is heard
    assert info["sounds"] < 10 + 6 + 8  # the pile was thinned
    small = tmp_path / "small.wav"  # 'drop' is something small landing, never the film's big hit
    event_sound.render([{"t": 0.1, "type": "drop"}], 1.0, small)
    hit = tmp_path / "hit.wav"
    event_sound.render([{"t": 0.1, "type": "hit"}], 1.0, hit)
    assert np.sqrt(np.mean(sf.read(str(small))[0] ** 2)) < 0.1 * np.sqrt(np.mean(sf.read(str(hit))[0] ** 2))
    music = tmp_path / "m.wav"
    sf.write(str(music), 0.3 * np.sin(2 * np.pi * 220 * np.arange(3 * 48000) / 48000), 48000)
    pic = tmp_path / "p.mp4"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=320x180:r=30:d=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(pic)], check=True)
    film = event_sound.mix(pic, music, out, tmp_path / "film.mp4", 3.0)
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration",
                                       "-of", "json", str(film)], capture_output=True, check=True).stdout)
    assert {s["codec_type"] for s in probe["streams"]} == {"video", "audio"}
    assert float(probe["format"]["duration"]) == pytest.approx(3.0, abs=0.1)


def test_the_effects_sit_under_the_music_and_a_pile_of_big_sounds_is_pulled_down(tmp_path):
    sr = 48000
    rng = np.random.default_rng(3)
    bed = 0.2 * rng.standard_normal(20 * sr) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.5 * np.arange(20 * sr) / sr))
    music = tmp_path / "m.wav"
    sf.write(str(music), bed, 44100)  # another sample rate than the effects: it's met where it is
    events = [{"t": 0.3 + 0.6 * k, "type": ("pop", "flick", "swish")[k % 3], "gain": 8} for k in range(31)]
    events += [{"t": 10.0 + 0.25 * k, "type": "hit", "gain": 6} for k in range(8)]  # eight booms in two seconds
    info = event_sound.render(events, 20.0, tmp_path / "fx.wav", music=music)
    assert info["held"] > 6  # the pile came down further than the rest
    assert abs(info["balance"]["median"] - event_sound.UNDER) < 3
    assert info["balance"]["max"] <= event_sound.CEILING + 2
    quiet = event_sound.render([{"t": 1.0, "type": "tick", "gain": -30}], 8.0, tmp_path / "q.wav", music=music)
    assert quiet["shift"] == 6  # a sound far too quiet to hear is brought up, but only so far


def test_a_yes_makes_it_and_anything_else_is_a_change():
    from vidosuper.work.animated_in_studio import is_go

    assert is_go("Make it") and is_go("yes please") and is_go("looks good!") and is_go("perfect, go")
    assert not is_go("make it but warmer") and not is_go("Change something") and not is_go("shorter ending")


def test_the_look_is_picked_from_their_words():
    assert "paper" in kits.names()
    assert kits.from_words("a cut paper promo") == "paper" and kits.from_words("anything") == "paper"
    with pytest.raises(ValueError, match="no look called"):
        kits.folder("claymation")


GOOD_SCENE = "{ const { C, cam, T, E } = scene('%s', %s, %s); backdrop(C, '#f2c48a'); }"


def test_a_whole_film_is_directed_pitched_built_reviewed_rendered_and_picks_up_where_it_stopped(tmp_path, monkeypatch):
    calls = {"director": [], "scenes": [], "renders": 0}

    def director(brief, folder, seconds, shape, kit, notes="", timeout=0):
        calls["director"].append(notes)
        src = _director(tmp_path / f"d{len(calls['director'])}")
        folder.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            shutil.copyfile(f, folder / f.name)
        (folder / "timing.json").write_text(json.dumps({"music": None, "seconds": 30.0, "drop": 16.0, "scenes": [
            {"id": "dawn", "start": 0.0, "end": 14.0, "in": "cut"}, {"id": "stuck", "start": 14.0, "end": 30.0, "in": "turn"}]}))
        return {}

    def scene_desk(plan, timing, sid, folder, film, audio, kit, notes="", timeout=0):
        calls["scenes"].append((sid, notes))
        folder.mkdir(parents=True, exist_ok=True)
        t = next(x for x in timing["scenes"] if x["id"] == sid)
        (folder / "scene.js").write_text(GOOD_SCENE % (sid, t["start"], t["end"]))
        return folder / "scene.js"

    def render(folder, out, fps=30, timeout=0):
        calls["renders"] += 1
        subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=30:d=30",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
        return out

    monkeypatch.setattr(animated, "director_desk", director)
    monkeypatch.setattr(animated, "scene_desk", scene_desk)
    monkeypatch.setattr(animated, "hf_check", lambda *a, **k: ["20.00 s line_unread \"Then\": on screen 0.3 s"])
    monkeypatch.setattr(animated, "film_review", lambda *a, **k: {"score": 15, "scenes": {"dawn": ["at 3 s: the sky is flat"]}})
    monkeypatch.setattr(animated, "probe", lambda pg: {"errors": [], "events": [{"t": 1.0, "type": "flap"}], "scenes": {}})
    monkeypatch.setattr(animated.scenes, "render", render)
    answers = iter(["make the ending warmer", None])
    pitched = []
    brief = "An explainer of a tiny paper film studio, warm and honest."
    result = animated.make(brief, tmp_path / "work", 30.0, "16:9", log=lambda m: None,
                           pitch=lambda p: pitched.append(p) or next(answers))
    assert calls["director"] == ["", "make the ending warmer"]  # the pitch changed once, then approved
    assert [p["title"] for p in pitched] == ["The Idea", "The Idea"] and pitched[0]["scenes"][1]["start"] == 14.0
    first = [s for s, n in calls["scenes"] if not n]
    fixes = {s: n for s, n in calls["scenes"] if n}
    assert sorted(first) == ["dawn", "stuck"]
    assert "the sky is flat" in fixes["dawn"] and "line_unread" in fixes["stuck"]  # the review and the checks, to their scenes
    assert result["video"].exists() and calls["renders"] == 1
    assert [round(s["end"] - s["start"], 2) for s in result["scenes"]] == [14.0, 16.0]
    assert all(s["picture"].exists() for s in result["scenes"])
    calls["scenes"].clear()
    again = animated.make(brief, tmp_path / "work", 30.0, "16:9", log=lambda m: None, pitch=lambda p: pytest.fail("asked twice"))
    assert calls["scenes"] == [] and calls["renders"] == 1 and again["video"] == result["video"]  # nothing made twice


def test_a_made_animated_film_becomes_a_film_whose_scenes_are_its_clips(tmp_path):
    from vidosuper.schemas import EDL
    from vidosuper.studio import accounts, versions
    from vidosuper.studio.service import Studio
    from vidosuper.studio.settings import load_settings
    from vidosuper.studio.storage import workdir
    from vidosuper.work import animated_in_studio

    s = load_settings(tmp_path / "home", {"brain": "local", "owner_brain": "local"})
    s.limits.min_free_gb = 0.0
    studio = Studio(s)
    user = accounts.owner(studio.db)
    p = studio.create_project(user, "An animated film", "16:9", 8.0, "a paper studio", kind="montage")
    pics = []
    for k, colour in enumerate(("red", "blue")):
        pic = tmp_path / f"s{k}.mp4"
        subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={colour}:s=320x180:r=30:d=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(pic)], check=True)
        pics.append(pic)
    film = tmp_path / "film.mp4"
    shutil.copyfile(pics[0], film)
    fx = tmp_path / "fx.wav"
    sf.write(str(fx), np.zeros(48000 * 4), 48000)
    result = {"video": film, "music": None, "effects": fx,
              "plan": {"title": "The Paper Studio", "logline": "Every film starts as an idea.", "music": {"bpm": 120}},
              "timing": {"seconds": 4.0},
              "scenes": [{"id": "dawn", "start": 0.0, "end": 2.0, "picture": pics[0]},
                         {"id": "stuck", "start": 2.0, "end": 4.0, "picture": pics[1]}]}
    assert animated_in_studio.register(studio, p, result, "16:9") == 1
    row = studio.db.project(p["id"])
    assert (row["stage"], row["version"], row["title"]) == ("done", 1, "The Paper Studio")
    names = sorted(c["name"] for c in studio.db.all("SELECT * FROM clips WHERE project_id=?", (p["id"],)))
    assert names == ["Scene 1: dawn", "Scene 2: stuck"]
    work = workdir(studio.s, user["id"], p["id"])
    edl = EDL.model_validate_json(versions.edl_path(work.out, 1, "16:9").read_text())
    assert [(sg.src_in, sg.src_out) for sg in edl.segments] == [(0.0, 2.0), (0.0, 2.0)]
    assert edl.sounds[0].source == str(fx) and edl.music is None and (edl.meta.width, edl.meta.height) == (1920, 1080)
