"""Cartoons as code (motion/cartoon.py, motion/blender.py, work/cartoons_in_studio.py): the board held to its rules
and cut on the beat, the sound mixed under the shots, Blender's frames made into a clip with transparency, and a
made cartoon becoming a film."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vidosuper.ffmpeg import ffmpeg_bin
from vidosuper.motion import blender, cartoon



@pytest.fixture
def studio_user(tmp_path):
    """A studio on this machine and a person in it (no films yet)."""
    from vidosuper.studio import accounts
    from vidosuper.studio.service import Studio
    from vidosuper.studio.settings import load_settings

    s = load_settings(tmp_path / "home", {"brain": "local", "owner_brain": "local"})
    s.limits.min_free_gb = 0.0
    studio = Studio(s)
    return studio, accounts.owner(studio.db)


def _board_desk(tmp_path: Path, shots: list[dict], cast=("mira",), seconds: float = 6.0, narration=None) -> Path:
    folder = tmp_path / "board"
    (folder / "assets").mkdir(parents=True)
    for name in ("style_key", *cast):
        (folder / "assets" / f"{name}.png").write_bytes(b"png")
    (folder / "settings.json").write_text(json.dumps({"seconds": seconds, "shape": "9:16", "width": 1080,
                                                      "height": 1920, "narration": bool(narration)}))
    if narration:
        (folder / "narration.json").write_text(json.dumps([{"start": a, "end": b, "word": w} for a, b, w in narration]))
    (folder / "board.json").write_text(json.dumps({
        "title": "The Kite", "logline": "A girl chases her kite.",
        "look": {"style": "gouache storybook", "palette": ["#f2c14e", "#2d3047"], "materials": "paper", "light": "dusk"},
        "cast": [{"id": c, "who": "a girl", "parts": ["head", "torso"]} for c in cast],
        "music": {"bpm": 120, "styles": ["warm folk"], "parts": {}},
        "shots": shots}))
    return folder


WHAT = "She runs in from the left, looks up, the kite tugs, she leans back and digs her heels in; slow push in."


def test_the_board_is_cut_on_the_beat_and_numbered_in_order(tmp_path):
    folder = _board_desk(tmp_path, [
        {"start": 2.1, "end": 4.13, "size": "close-up", "angle": "low", "what": WHAT},
        {"start": 0, "end": 2.1, "size": "wide", "angle": "eye level", "what": WHAT},
        {"start": 4.13, "end": 6.02, "size": "medium", "angle": "high", "what": WHAT}])
    board, problems = cartoon.check_board(folder)
    assert problems == []
    assert [(s["n"], s["start"], s["end"]) for s in board["shots"]] == [(1, 0.0, 2.0), (2, 2.0, 4.0), (3, 4.0, 6.0)]
    assert board["seconds"] == 6.0  # 0.5 s beats at 120 bpm
    assert json.loads((folder / "board.json").read_text())["shots"][1]["start"] == 2.0  # written back
    assert [s.size for s in cartoon.board_shots(board)] == ["wide", "close-up", "medium"]


def test_the_board_says_what_is_missing_or_flat(tmp_path):
    folder = _board_desk(tmp_path, [
        {"start": 0, "end": 3, "size": "wide", "angle": "eye level", "what": WHAT},
        {"start": 3, "end": 6, "size": "wide", "angle": "eye level", "what": "she smiles"},
        {"start": 6, "end": 6.4, "size": "portrait", "angle": "eye level", "what": WHAT}], cast=("mira", "kite"))
    (folder / "assets" / "kite.png").unlink()
    _, problems = cartoon.check_board(folder)
    said = "\n".join(problems)
    assert "'kite' has no model sheet" in said
    assert "shots 1 and 2 have the same size and angle" in said
    assert "shot 2: 'what' must say what happens" in said
    assert "shot 3 lasts 0.50 s" in said and "size 'portrait'" in said


def test_a_repeated_framing_is_allowed_when_the_board_says_why(tmp_path):
    folder = _board_desk(tmp_path, [
        {"start": 0, "end": 3, "size": "wide", "angle": "eye level", "what": WHAT},
        {"start": 3, "end": 6, "size": "wide", "angle": "eye level",
         "what": "Same framing as shot 1, now empty: the kite is gone and only the string drifts down; hold."}])
    assert cartoon.check_board(folder)[1] == []


def test_a_narrated_board_cuts_in_the_pauses_and_lasts_as_long_as_the_voice(tmp_path):
    words = [(0.2, 0.9, "Once"), (1.0, 1.6, "a"), (1.9, 2.8, "kite"), (3.1, 4.0, "flew"), (4.2, 5.4, "away")]
    folder = _board_desk(tmp_path, [
        {"start": 0, "end": 2.5, "size": "wide", "angle": "eye level", "what": WHAT},
        {"start": 2.5, "end": 5.0, "size": "close-up", "angle": "low", "what": WHAT}], seconds=6.4, narration=words)
    board, problems = cartoon.check_board(folder)
    assert problems == []
    assert board["shots"][0]["end"] == 2.8  # out of 'kite' (1.9-2.8), to the nearer edge of its pause
    assert board["seconds"] == pytest.approx(5.8)  # the voice and a breath


def _tone(path: Path, seconds: float, freq: int) -> Path:
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration={seconds}", "-ar", "48000", str(path)], check=True)
    return path


def _picture(path: Path, seconds: float, colour: str = "red") -> Path:
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c={colour}:s=320x568:r=30:d={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(path)], check=True)
    return path


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration:format=duration",
                          "-of", "json", str(path)], capture_output=True, check=True)
    return json.loads(out.stdout)


def test_the_shots_cut_together_and_the_sound_is_mixed_under_them(tmp_path, monkeypatch):
    shots = [_picture(tmp_path / "a.mp4", 1.0, "red"), _picture(tmp_path / "b.mp4", 1.5, "blue")]
    picture = cartoon.cut_together(shots, tmp_path / "picture.mp4")
    assert float(_probe(picture)["format"]["duration"]) == pytest.approx(2.5, abs=0.05)
    pop = _tone(tmp_path / "pop.wav", 0.3, 880)
    monkeypatch.setattr(cartoon.scenes, "sound_for", lambda what, secs, lib: pop if "pop" in what else None)
    out, laid = cartoon.mix(picture, 2.5, _tone(tmp_path / "music.wav", 4.0, 220), _tone(tmp_path / "voice.wav", 2.0, 440),
                            [{"t": 1.2, "sound": "a soft pop", "gain": -10}, {"t": 2.0, "sound": "nothing made"}],
                            tmp_path / "sfx", tmp_path / "cartoon.mp4")
    info = _probe(out)
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert float(info["format"]["duration"]) == pytest.approx(2.5, abs=0.1)
    assert laid == [{"file": str(pop), "at": 1.2, "seconds": 1.2, "gain": -10.0}]  # the one cue that could be made


def test_blender_frames_become_a_clip_with_transparency(tmp_path, monkeypatch):
    import cv2
    import numpy as np

    real_run = subprocess.run
    seen = {}

    def fake_blender(cmd, **kw):
        if cmd[0] != "blender-exe":
            return real_run(cmd, **kw)
        args = json.loads(cmd[-1])
        seen.update(args)
        for k in range(1, args["frames"] + 1):  # what the runner renders: RGBA frames f0001.png...
            img = np.zeros((args["height"], args["width"], 4), np.uint8)
            cv2.circle(img, (20 + k * 4, 40), 18, (40, 200, 255, 255), -1)
            cv2.imwrite(str(Path(args["out"]) / f"f{k:04d}.png"), img)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(blender, "find", lambda: "blender-exe")
    monkeypatch.setattr(blender.subprocess, "run", fake_blender)
    (tmp_path / "orbit.py").write_text("import bpy\n")
    out = blender.render(tmp_path / "orbit.py", tmp_path / "assets" / "orbit.webm", width=160, height=96, fps=30,
                         seconds=0.5, assets=tmp_path / "assets")
    assert seen["frames"] == 15 and seen["fps"] == 30 and seen["script"].endswith("orbit.py")
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name:stream_tags=alpha_mode",
                              "-of", "json", str(out)], capture_output=True, check=True)
    meta = json.loads(streams.stdout)["streams"][0]
    assert meta["codec_name"] == "vp9" and meta.get("tags", {}).get("alpha_mode") == "1"
    assert out.with_suffix(".png").exists()  # its middle frame, to Read
    assert not list((tmp_path / "assets").glob(".orbit-frames*"))  # its frames are cleared away


def test_blender_says_plainly_when_it_is_not_set_up(tmp_path, monkeypatch):
    monkeypatch.setattr(blender, "find", lambda: None)
    with pytest.raises(blender.BlenderError, match="Connect page"):
        blender.render(tmp_path / "x.py", tmp_path / "x.webm", width=10, height=10, fps=30, seconds=1,
                       assets=tmp_path)


def test_the_runner_builds_a_transparent_scene_and_renders_on_the_gpu_when_it_can():
    assert "film_transparent = True" in blender.RUNNER and '"OPTIX", "CUDA", "HIP", "METAL"' in blender.RUNNER
    compile(blender.RUNNER, "runner.py", "exec")  # it is valid Python


def test_a_made_cartoon_becomes_a_film_whose_shots_are_its_clips(studio_user, tmp_path):
    from vidosuper.schemas import EDL
    from vidosuper.studio import versions
    from vidosuper.studio.storage import workdir
    from vidosuper.work import cartoons_in_studio

    studio, user = studio_user
    p = studio.create_project(user, "A cartoon", "9:16", 8.0, "a kite story", kind="montage")
    shots = [_picture(tmp_path / "s1.mp4", 2.0, "red"), _picture(tmp_path / "s2.mp4", 2.0, "blue")]
    film = cartoon.cut_together(shots, tmp_path / "picture.mp4")
    music = _tone(tmp_path / "music.mp3", 4.0, 220)
    result = {"video": film, "music": music, "narration": None,
              "sounds": [{"file": str(_tone(tmp_path / "pop.wav", 0.3, 880)), "at": 1.0, "seconds": 0.3, "gain": -12}],
              "board": {"title": "The Kite!", "logline": "A girl chases her kite.", "seconds": 4.0,
                        "music": {"bpm": 120}, "shots": [{"what": "runs"}, {"what": "flies"}]},
              "shots": [{"n": 1, "start": 0.0, "end": 2.0, "picture": shots[0], "score": 7},
                        {"n": 2, "start": 2.0, "end": 4.0, "picture": shots[1], "score": 8}]}
    version = cartoons_in_studio.register(studio, p, result, "9:16")
    assert version == 1
    row = studio.db.project(p["id"])
    assert (row["stage"], row["version"], row["title"]) == ("done", 1, "The Kite!")
    work = workdir(studio.s, user["id"], p["id"])
    assert sorted(c["name"] for c in studio.db.all("SELECT * FROM clips WHERE project_id=?", (p["id"],))) == \
        ["Shot 1", "Shot 2"]
    assert (work.out / "v1-9x16.mp4").exists() and (work.out / "v1-9x16.jpg").exists()
    edl = EDL.model_validate_json(versions.edl_path(work.out, 1, "9:16").read_text())
    assert [(s.src_in, s.src_out) for s in edl.segments] == [(0.0, 2.0), (0.0, 2.0)]
    assert edl.music.path == str(music) and edl.sounds[0].at == 1.0 and (edl.meta.width, edl.meta.height) == (1080, 1920)
    assert studio.versions_list(row)[0]["files"][0]["file"] == "v1-9x16.mp4"


def test_the_agent_starts_a_cartoon_as_a_new_film(studio_user, monkeypatch):
    from vidosuper.work import cartoons_in_studio

    studio, user = studio_user
    started = {}
    monkeypatch.setattr(cartoons_in_studio, "_make", lambda *a: started.setdefault("args", a))
    with pytest.raises(ValueError, match="Tell the story"):
        cartoons_in_studio.start(studio, user, "short")
    row = cartoons_in_studio.start(studio, user, "A girl's kite pulls her into the sky over the town.", 30, "1:1",
                                   "paper cut-out")
    p = studio.db.project(row["film"])
    assert row["kind"] == "cartoon" and row["status"] == "running" and p["shapes"] == "9:16"
    assert studio._info(p)["cartoon"] == {"shape": "1:1", "style": "paper cut-out"}
    import time

    for _ in range(50):
        if started:
            break
        time.sleep(0.02)
    assert started["args"][4:8] == ("A girl's kite pulls her into the sky over the town.", 30, "1:1", "paper cut-out")


def test_each_shot_desk_gets_its_brief_the_films_style_and_cast_and_a_complete_contract(tmp_path, monkeypatch):
    board_dir = _board_desk(tmp_path, [
        {"start": 0, "end": 2, "size": "wide", "angle": "eye level", "what": WHAT},
        {"start": 2, "end": 4.5, "size": "close-up", "angle": "low", "what": WHAT + " The kite string snaps."},
        {"start": 4.5, "end": 6, "size": "medium", "angle": "high", "what": WHAT}])
    board, _ = cartoon.check_board(board_dir)
    shots = cartoon.board_shots(board)
    seen = {}

    def fake_desk(folder, system, prompt, commands, budget, timeout):
        seen.update(system=system, commands=commands)
        (folder / "scene.html").write_text('<div id="root" data-composition-id="main"></div>')
        return "DONE"

    monkeypatch.setattr(cartoon, "_desk", fake_desk)
    monkeypatch.setattr(blender, "find", lambda: None)
    folder = tmp_path / "s02"
    html = cartoon.shot_desk(board, board_dir, shots[1], shots, folder, 1080, 1920,
                             [(2.6, 3.0, "snap")], problems=["at 1.2 s: the arm separates"])
    assert "data-composition-id" in html
    for gone in ("{DURATION}", "{W}", "{H}", "{CRAFT}", "{MOTION}", "{PLUGINS}", "{FONTS}", "{BLENDER}", "{{"):
        assert gone not in seen["system"], gone
    assert 'data-duration="2.50"' in seen["system"] and "1080 x 1920" not in seen["system"]
    assert seen["commands"] == ("look", "paint", "review")  # no Blender here: no `bash blender`
    brief = (folder / "brief.md").read_text()
    assert "shot 2 of 3, 2.50 s" in brief and "The kite string snaps." in brief
    assert "0.60-1.00 snap" in brief  # its narration, in seconds into the shot
    assert "The shot before" in brief and "The shot after" in brief and "the arm separates" in brief
    assert sorted(p.name for p in (folder / "assets").iterdir()) == ["mira.png", "style_key.png"]
    assert json.loads((folder / "brief.json").read_text())["end"] == 2.5
