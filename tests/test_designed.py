"""Designed talking videos: the animation library, picking animations for moments, the cut-out clean-up, and the
renderer (with a fake matte and a hand-made animation, so no network and no model)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from vidosuper.explainer import cutout, lottie_lib
from vidosuper.explainer.lottie_lib import Library
from vidosuper.explainer.script import Callout, Scene, VoiceScript
from vidosuper.explainer.visuals import assign

# a red circle growing for one second: the smallest real Lottie animation
CIRCLE = json.dumps({
    "v": "5.7.4", "fr": 30, "ip": 0, "op": 30, "w": 200, "h": 200, "nm": "c", "ddd": 0, "assets": [],
    "layers": [{"ddd": 0, "ind": 1, "ty": 4, "nm": "s", "sr": 1, "ao": 0, "ip": 0, "op": 30, "st": 0, "bm": 0,
                "ks": {"o": {"a": 0, "k": 100}, "r": {"a": 0, "k": 0}, "p": {"a": 0, "k": [100, 100, 0]},
                       "a": {"a": 0, "k": [0, 0, 0]},
                       "s": {"a": 1, "k": [{"t": 0, "s": [60, 60, 100], "i": {"x": [0.5], "y": [0.5]}, "o": {"x": [0.5], "y": [0.5]}},
                                           {"t": 29, "s": [100, 100, 100]}]}},
                "shapes": [{"ty": "el", "p": {"a": 0, "k": [0, 0]}, "s": {"a": 0, "k": [150, 150]}, "nm": "e"},
                           {"ty": "fl", "c": {"a": 0, "k": [1, 0.1, 0.1, 1]}, "o": {"a": 0, "k": 100}, "nm": "f"}]}]})
IDS = ["00000000-0000-0000-0000-00000000000" + str(k) for k in range(1, 4)]


def dotlottie(data: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", "{}")
        z.writestr("animations/a.json", data)
    return buf.getvalue()


def fake_site(broken: str | None = None):
    """LottieFiles as far as the library can tell: a topic page listing three files, one of them broken."""
    files = {f"https://assets-v2.lottiefiles.com/a/{i}/abc{k}.lottie": dotlottie(CIRCLE if i != broken else "{not json")
             for k, i in enumerate(IDS)}
    page = " ".join(f'\\"{url}\\"' for url in files)  # the page escapes its quotes

    def fetch(url: str) -> bytes:
        if url.startswith("https://lottiefiles.com/free-animations/"):
            return page.encode()
        return files[url]

    return fetch


def test_the_library_pulls_measures_skips_broken_files_and_remembers(tmp_path):
    lib = Library(tmp_path / "lot", fake_site(broken=IDS[2]))
    ids = lib.pull("Podcast")
    assert ids == IDS[:2]  # the broken one is left out and marked
    assert lib.index["items"][IDS[2]]["bad"]
    it = lib.item(IDS[0])
    assert it["w"] == 200 and it["frames"] in (30, 31) and not it["filled"] and not it["faint"] and "podcast" in it["topics"]
    assert lib.pull("podcast") == IDS[:2]  # cached: the site is not asked again
    assert lib.candidates(["podcast"]) == IDS[:2]
    sheet = lib.sheet(lib.candidates(["podcast"]), tmp_path / "s.jpg")
    assert sheet.exists()
    assert lib.remembered("two people podcasting") is None
    lib.remember(IDS[1], "two people podcasting at a table")
    assert lib.remembered("two people podcasting at a table!") == IDS[1]
    assert Library(tmp_path / "lot").remembered("two people podcasting at a table") == IDS[1]  # saved
    frame = lib.lot(IDS[0]).frame(0.5, 120)
    assert frame.size == (120, 120) and np.asarray(frame)[60, 60, 3] > 200


def test_offline_the_library_gives_nothing_and_nothing_breaks(tmp_path):
    lib = Library(tmp_path / "lot")  # the test fixture makes the network fail
    assert lib.pull("ghost") == [] and lib.candidates(["ghost"]) == []


def test_moments_get_animations_picked_once_then_reused(tmp_path):
    lib = Library(tmp_path / "lot", fake_site())
    script = VoiceScript(name="x", ambient="a circular sound visualizer", ambient_topics=["sound wave"], callouts=[
        Callout(at=1, until=4, kind="scene", visual="two people podcasting", topics=["podcast"]),
        Callout(at=5, until=8, kind="pill", text="no animation needed"),
        Callout(at=9, until=12, kind="stack", count=10, visual="an audio waveform", topics=["audio"])],
        scenes=[Scene(start=12, end=16, layout="keyword", headline="x", visual="an email arriving", topics=["email"])])
    asked = []

    def picker(needs):
        asked.append([n["visual"] for n in needs])
        assert all(Path(n["sheet"]).exists() and n["n"] == 3 for n in needs)
        return [1, 0, -1, 2][:len(needs)]

    out, log = assign(script, lib, picker, tmp_path / "work")
    assert asked == [["a circular sound visualizer", "two people podcasting", "an audio waveform", "an email arriving"]]
    assert out.ambient_lottie == IDS[1] and out.callouts[0].lottie == IDS[0]
    assert out.callouts[2].lottie == "" and out.callouts[1].lottie == ""  # 'none fits' keeps the emoji
    assert out.scenes[0].lottie == IDS[2] and len(log) == 4
    again, _ = assign(script, lib, lambda needs: pytest.fail("should not ask again"), tmp_path / "work")
    assert again.callouts[0].lottie == IDS[0] and again.ambient_lottie == IDS[1]


def test_the_cut_out_drops_floating_pieces_and_cleans_the_edge():
    h, w = 400, 240
    m = np.zeros((h, w), np.float32)
    m[150:, 60:180] = 1.0  # the person, leaving the frame at the bottom
    m[10:60, 20:200] = 1.0  # a ceiling lamp floating above
    kept = cutout.attached(m)
    assert kept[300, 120] > 0.9 and kept[30, 100] < 0.1
    rgb = np.zeros((h, w, 3), np.uint8)
    rgb[...] = (200, 180, 255)  # a lilac wall
    rgb[150:, 60:180] = (40, 40, 40)  # a dark shirt
    soft = np.clip(cutout.guided(rgb.mean(axis=2).astype(np.float32) / 255, kept), 0, 1)
    clean = cutout.decontaminate(rgb, soft)
    assert clean.shape == (h, w, 3) and clean[300, 120, 0] < 60
    box = cutout.box(kept)
    assert box is not None and 0.2 < box[0] < 0.3 and 0.35 < box[1] < 0.4


def test_a_designed_video_puts_the_animation_behind_the_person_and_keeps_their_sound(media, tmp_path, monkeypatch):
    from vidosuper.explainer import designed
    from vidosuper.ffmpeg import ffprobe_json, find_tool

    lib = Library(tmp_path / "lot", fake_site())
    lib.pull("podcast")
    w, h = 270, 480

    def fake_matte(rgb, model=None):  # the person: the lower middle of the frame
        m = np.zeros(rgb.shape[:2], np.float32)
        m[int(rgb.shape[0] * 0.45):, int(rgb.shape[1] * 0.3):int(rgb.shape[1] * 0.7)] = 1.0
        return m

    monkeypatch.setattr(cutout, "available", lambda model=None: True)
    monkeypatch.setattr(cutout, "raw_matte", fake_matte)
    video = media / "clips" / "a_stable.mp4"
    words = [{"start": 0.2, "end": 0.5, "text": "hello"}, {"start": 0.6, "end": 0.9, "text": "podcast"}]
    script = VoiceScript(name="x", style="designed", palette="midnight", callouts=[
        Callout(at=0.3, until=2.0, kind="scene", lottie=IDS[0], text="a podcast"),
        Callout(at=0.2, until=1.5, kind="title", text="Undertone")])
    out = tmp_path / "d.mp4"
    designed.render_designed(script, video, [{"words": words}], out, (w, h), None, start=0.0, end=2.0, duration=10.0, lib=lib)
    info = ffprobe_json(out)
    kinds = [s["codec_type"] for s in info["streams"]]
    assert "video" in kinds and abs(float(info["format"]["duration"]) - 2.0) < 0.2
    import subprocess

    raw = subprocess.run([find_tool("ffmpeg"), "-loglevel", "error", "-ss", "1.2", "-i", str(out), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True).stdout
    frame = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
    top = frame[int(h * 0.1):int(h * 0.35), int(w * 0.3):int(w * 0.7)].reshape(-1, 3)
    assert ((top[:, 0] > 150) & (top[:, 1] < 90)).mean() > 0.05  # the red animation is up there


def test_the_parts_of_a_parallel_render_say_how_far_they_have_got(media, tmp_path):
    from vidosuper.explainer import designed

    script = VoiceScript(name="x", style="designed", palette="midnight",
                         callouts=[Callout(at=0.1, until=0.9, kind="title", text="Undertone")])
    jobs = [{"script": script.model_dump_json(), "lib": "", "video": str(media / "clips" / "a_stable.mp4"), "words": [],
             "size": [270, 480], "levels": [0.0], "start": 0.0, "frames": 20, "first": 0, "quality": "draft",
             "out": str(tmp_path / f"part{k}.mp4"), "threads": 2} for k in range(2)]
    assert designed._drawn(jobs) == 0.0  # nothing yet
    designed._chunk(jobs[0])  # drawn here, as one of the processes would
    assert Path(jobs[0]["out"]).exists() and designed._drawn(jobs) == 20.0
    designed._tally(jobs[1]).write_text("0.5", encoding="utf-8")  # the other one halfway
    assert designed._drawn(jobs) == 30.0


def _talk(seconds: float) -> list[dict]:
    """A talk that speaks the whole time, with a rating said at 40 s."""
    utts = []
    for k in range(int(seconds // 5)):
        text = "I would rate it 7.5 out of 10" if k == 8 else "and the story goes on with more sound"
        utts.append({"start": k * 5.0, "end": k * 5.0 + 4.8, "text": text, "words": []})
    return utts


def test_a_designed_talk_script_must_cover_the_talk_and_show_every_number():
    from vidosuper.studio.brain.core import check_voice_script

    utts = _talk(60)
    sparse = VoiceScript(name="x", style="designed", callouts=[
        Callout(at=t, until=t + 4, kind="scene", visual="a thing", topics=["thing"]) for t in (1, 5, 9, 13, 17, 21)])
    problems = " ".join(check_voice_script(sparse, utts, 60, "talk", "designed"))
    assert "no graphics for long stretches" in problems and "21-60 s" in problems
    assert "'7.5 out of 10' is said at 40 s" in problems
    full = VoiceScript(name="x", style="designed", callouts=[
        Callout(at=t, until=t + 4, kind="scene", visual="a thing", topics=["thing"]) for t in range(1, 60, 4)] + [
        Callout(at=40.5, until=43, kind="stat", text="rating", value="7.5/10")])
    assert check_voice_script(full, utts, 60, "talk", "designed") == []


def test_a_scene_stays_up_while_its_stack_flies_out_and_split_numbers_join():
    from vidosuper.explainer.designed import join_numbers
    from vidosuper.explainer.script import fit_script

    s = VoiceScript(name="x", callouts=[Callout(at=10, until=12, kind="scene", visual="an email"),
                                        Callout(at=12, until=15, kind="stack", count=10, visual="a waveform"),
                                        Callout(at=16, until=19, kind="scene", visual="a ghost")])
    out = fit_script(s, 30, "talk")
    assert out.callouts[0].until == 15 and out.callouts[1].count == 10
    words = [{"text": " around", "start": 0, "end": 1}, {"text": " 7", "start": 1, "end": 1.2},
             {"text": ".5", "start": 1.2, "end": 1.4}, {"text": " 40", "start": 2, "end": 2.1}, {"text": "%", "start": 2.1, "end": 2.2}]
    assert [w["text"] for w in join_numbers(words)] == [" around", "7.5", "40%"]


def test_needs_nothing_fitted_get_a_second_try_with_simpler_ideas(tmp_path):
    lib = Library(tmp_path / "lot", fake_site())
    script = VoiceScript(name="x", callouts=[Callout(at=1, until=4, kind="scene", visual="popcorn by a glowing screen",
                                                     topics=["popcorn"])])
    rounds = []

    def picker(needs):
        rounds.append([n["visual"] for n in needs])
        return [-1] if len(rounds) == 1 else [2]

    out, log = assign(script, lib, picker, tmp_path / "w", lambda needs: [{"visual": "a movie screen", "topics": ["movie"]}])
    assert rounds == [["popcorn by a glowing screen"], ["a movie screen"]]
    assert out.callouts[0].lottie == IDS[2] and any("second try" in line for line in log)


def test_rendered_animation_frames_are_cached_within_a_budget(tmp_path, monkeypatch):
    """An unbounded frame cache ran the PC out of memory during a 2-minute render."""
    lib = Library(tmp_path / "lot", fake_site())
    lib.pull("podcast")
    lot = lib.lot(IDS[0])
    monkeypatch.setattr(lottie_lib, "CACHE_BYTES", 400_000)
    for k in range(60):
        lot.frame(k / 30, 120)  # small frames: cached, but only up to the budget
    assert 0 < lot.bytes <= 400_000 and len(lot.cache) == lot.bytes // (120 * 120 * 4)
    before = len(lot.cache)
    lot.frame(0.5, 900)  # a big frame is drawn but never kept
    assert len(lot.cache) == before


def test_drawing_processes_never_outgrow_free_memory(monkeypatch):
    from vidosuper.explainer import designed

    monkeypatch.setattr(designed, "free_memory_gb", lambda: 7.0)
    assert designed.safe_workers(3) == 3
    monkeypatch.setattr(designed, "free_memory_gb", lambda: 6.0)
    assert designed.safe_workers(3) == 2  # 1.3 GB a part (drawing + encoder + decoder), 2.5 kept spare
    monkeypatch.setattr(designed, "free_memory_gb", lambda: 4.0)
    assert designed.safe_workers(3) == 1
    monkeypatch.setattr(designed, "free_memory_gb", lambda: 1.0)
    assert designed.safe_workers(3) == 1
    monkeypatch.setattr(designed, "free_memory_gb", lambda: None)
    assert designed.safe_workers(3) == 2
