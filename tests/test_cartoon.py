"""Cartoons as code (motion/cartoon.py, motion/blender.py, work/cartoons_in_studio.py): the board held to its rules
and cut on the beat, the sound mixed under the shots, Blender's frames made into a clip with transparency, and a
made cartoon becoming a film."""

from __future__ import annotations

import json
import re
import shutil
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


def test_the_runner_renders_whole_shots_brings_rigs_in_and_saves_characters():
    assert 'film_transparent = bool(args.get("transparent"))' in blender.RUNNER
    assert '"OPTIX", "CUDA", "HIP", "METAL"' in blender.RUNNER
    assert "def cast(name)" in blender.RUNNER and "save_as_mainfile" in blender.RUNNER
    compile(blender.RUNNER, "runner.py", "exec")  # it is valid Python


def _fake_blender(monkeypatch, seen, rgba=False, rig_ok=True):
    import cv2
    import numpy as np

    real_run = subprocess.run

    def run(cmd, **kw):
        if cmd[0] != "blender-exe":
            return real_run(cmd, **kw)
        args = json.loads(cmd[-1])
        seen.update(args)
        out = Path(args["out"])
        if args.get("mode") == "rig":
            if not rig_ok:
                return subprocess.CompletedProcess(cmd, 5, b"THE CHARACTER HAS NO ARMATURE", b"")
            Path(args["blend"]).write_bytes(b"BLENDER")
            for k in range(3):
                cv2.imwrite(str(out / f"turn{k}.png"), np.full((64, 48, 3), 90 + k * 40, np.uint8))
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        for k in range(1, args["frames"] + 1):
            img = np.zeros((args["height"], args["width"], 4 if rgba else 3), np.uint8)
            cv2.circle(img, (20 + k * 4, 40), 18, (40, 200, 255, 255) if rgba else (40, 200, 255), -1)
            cv2.imwrite(str(out / f"f{k:04d}.png"), img)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(blender, "find", lambda: "blender-exe")
    monkeypatch.setattr(blender.subprocess, "run", run)


def test_a_whole_shot_is_rendered_opaque_with_its_cast(tmp_path, monkeypatch):
    seen = {}
    _fake_blender(monkeypatch, seen)
    (tmp_path / "shot.py").write_text("import bpy\n")
    (tmp_path / "cast").mkdir()
    out = blender.render(tmp_path / "shot.py", tmp_path / "clip.mp4", width=160, height=96, fps=30, seconds=0.5,
                         assets=tmp_path, cast=tmp_path / "cast")
    assert seen["transparent"] is False and seen["cast"].endswith("/cast") and seen["frames"] == 15
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,pix_fmt", "-of", "json",
                              str(out)], capture_output=True, check=True)
    meta = json.loads(streams.stdout)["streams"][0]
    assert meta["codec_name"] == "h264" and meta["pix_fmt"] == "yuv420p"


def test_a_character_rig_is_saved_and_filmed_from_three_sides(tmp_path, monkeypatch):
    seen = {}
    _fake_blender(monkeypatch, seen)
    (tmp_path / "rig_dog.py").write_text("import bpy\n")
    stills = blender.rig(tmp_path / "rig_dog.py", "dog", tmp_path / "cast", assets=tmp_path)
    assert seen["mode"] == "rig" and seen["name"] == "dog" and seen["blend"].endswith("cast/dog.blend")
    assert [s.name for s in stills] == ["dog-turn0.png", "dog-turn1.png", "dog-turn2.png"]
    assert (tmp_path / "cast" / "dog.blend").exists()
    _fake_blender(monkeypatch, seen, rig_ok=False)
    with pytest.raises(blender.BlenderError, match="NO ARMATURE"):
        blender.rig(tmp_path / "rig_dog.py", "cat", tmp_path / "cast", assets=tmp_path)


def test_the_engine_is_the_one_asked_for_else_the_best_this_machine_can_run(monkeypatch):
    from vidosuper.motion import videogen

    monkeypatch.delenv("VIDO_CARTOON_ENGINE", raising=False)
    assert cartoon.engine_for("anime") == "anime"
    monkeypatch.setattr(videogen, "available", lambda: True)
    assert cartoon.engine_for() == "cartoon"
    monkeypatch.setattr(videogen, "available", lambda: False)
    monkeypatch.setattr(blender, "find", lambda: "blender-exe")
    assert cartoon.engine_for() == "rig"
    monkeypatch.setattr(blender, "find", lambda: None)
    import vidosuper.motion as motion

    monkeypatch.setattr(motion, "available", lambda: True)
    assert cartoon.engine_for() == "drawn"  # drawn in code: it needs no GPU and no Blender
    monkeypatch.setattr(motion, "available", lambda: False)
    with pytest.raises(cartoon.CartoonError, match=r"\[video\].*Blender.*npm ci"):
        cartoon.engine_for()
    monkeypatch.setenv("VIDO_CARTOON_ENGINE", "rig")
    assert cartoon.engine_for() == "rig"


def test_a_rigged_film_needs_every_character_rigged(tmp_path):
    folder = _board_desk(tmp_path, [{"start": 0, "end": 3, "size": "wide", "angle": "low", "what": WHAT}],
                         cast=("dog",), seconds=3.0)
    st = json.loads((folder / "settings.json").read_text())
    (folder / "settings.json").write_text(json.dumps({**st, "engine": "rig"}))
    _, problems = cartoon.check_board(folder)
    assert any("has no rig: bash rig dog rig_dog.py" in p for p in problems)
    (folder / "cast").mkdir()
    (folder / "cast" / "dog.blend").write_bytes(b"BLENDER")
    _, problems = cartoon.check_board(folder)
    assert not any("rig" in p for p in problems)


def _shots(tmp_path):
    folder = _board_desk(tmp_path, [{"start": 0, "end": 2, "size": "wide", "angle": "eye level", "what": WHAT},
                                    {"start": 2, "end": 4, "size": "close-up", "angle": "low", "what": WHAT}],
                         seconds=4.0)
    board, _ = cartoon.check_board(folder)
    return folder, board, cartoon.board_shots(board)


def test_a_shot_desk_paints_its_frames_and_writes_its_motion(tmp_path, monkeypatch):
    board_dir, board, shots = _shots(tmp_path)
    seen = {}

    def fake_desk(folder, system, prompt, commands, budget, timeout):
        seen.update(system=system, commands=commands)
        (folder / "start.png").write_bytes(b"png")
        (folder / "shot.json").write_text(json.dumps({"motion": "She leaps up and grabs the string, the kite drags "
                                                                "her forward; the camera follows low.", "end": False,
                                                      "sounds": [{"t": 0.4, "sound": "a whoosh", "gain": -12}]}))
        return "DONE"

    monkeypatch.setattr(cartoon, "_desk", fake_desk)
    folder = tmp_path / "shots" / "s02"
    spec = cartoon.frame_desk(board, board_dir, shots[1], shots, folder, 1080, 1920, [], "cartoon")
    assert spec["motion"].startswith("She leaps") and seen["commands"] == ("frame",)
    assert "{" + "CRAFT}" not in seen["system"] and "1080 x 1920" in seen["system"]
    assert "cut-out" not in seen["system"].lower() and "gsap" not in seen["system"].lower()  # no stickers
    brief = (folder / "brief.md").read_text()
    assert "shot 2 of 2, 2.00 s" in brief and "The shot before" in brief
    assert sorted(p.name for p in (folder / "assets").iterdir()) == ["mira.png", "style_key.png"]

    def lazy(folder, *a, **k):
        (folder / "start.png").write_bytes(b"png")
        (folder / "shot.json").write_text(json.dumps({"motion": ""}))
        return ""

    monkeypatch.setattr(cartoon, "_desk", lazy)
    with pytest.raises(cartoon.CartoonError, match="says no motion"):
        cartoon.frame_desk(board, board_dir, shots[0], shots, tmp_path / "shots" / "s01", 1080, 1920, [], "cartoon")


def test_a_frame_is_painted_whole_at_the_films_shape_on_its_character(tmp_path, monkeypatch):
    board_dir, board, shots = _shots(tmp_path)
    desk = tmp_path / "shots" / "s01"
    (desk / "assets").mkdir(parents=True)
    for name in ("style_key", "mira"):
        (desk / "assets" / f"{name}.png").write_bytes(b"png")
    (desk / "brief.json").write_text(json.dumps({"width": 1080, "height": 1920}))
    asked = {}
    monkeypatch.setattr(cartoon.scenes, "paint", lambda what, out, light, like, whole, aspect:
                        (asked.update(what=what, out=out, like=like, whole=whole, aspect=aspect), (768, 1360))[1])
    cartoon.frame_command(desk, ["start", "Mira on the hill, the kite pulling", "--with", "mira"])
    assert asked["whole"] is True and asked["out"] == desk / "start.png" and asked["like"].name == "mira.png"
    assert asked["aspect"] == pytest.approx(1080 / 1920)
    cartoon.frame_command(desk, ["end", "Mira lifted off her feet"])
    assert asked["out"] == desk / "end.png" and asked["like"].name == "style_key.png"


def test_a_shot_is_taken_again_when_the_director_finds_it_drifts(tmp_path, monkeypatch):
    from vidosuper.motion import videogen

    board_dir, board, shots = _shots(tmp_path)
    folder = tmp_path / "shots" / "s01"
    (folder / "assets").mkdir(parents=True)
    (folder / "start.png").write_bytes(b"png")
    takes = []

    def fake_animate(image, prompt, out, *, seconds, look, last_image=None, seed=0, **kw):
        takes.append({"prompt": prompt, "seed": seed, "look": look, "seconds": seconds, "last": last_image})
        out.write_bytes(b"mp4")
        return {"out": str(out)}

    verdicts = iter([{"score": 4, "ok": False, "problems": ["at 0.5 s: her hair turns blue: keep it brown"]},
                     {"score": 8, "ok": True, "problems": []}])
    monkeypatch.setattr(videogen, "animate", fake_animate)
    monkeypatch.setattr(cartoon, "review_clip", lambda *a, **k: next(verdicts))
    monkeypatch.delenv("VIDO_CARTOON_QUICK", raising=False)
    spec = {"motion": "Mira runs right and leaps for the kite string.", "end": False}
    best = cartoon.animate_shot(folder, board, shots[0], spec, "anime", log=lambda m: None)
    assert best == {"clip": folder / "take2.mp4", "score": 8}
    assert [t["look"] for t in takes] == ["anime", "anime"] and takes[0]["seed"] != takes[1]["seed"]
    assert "Avoid: her hair turns blue: keep it brown" in takes[1]["prompt"] and "gouache storybook" in takes[0]["prompt"]
    assert takes[0]["seconds"] == pytest.approx(2.25) and takes[0]["last"] is None
    monkeypatch.setenv("VIDO_CARTOON_QUICK", "1")
    takes.clear()
    best = cartoon.animate_shot(tmp_path / "shots" / "s01", board, shots[0], spec, "cartoon", log=lambda m: None)
    assert best["clip"].name == "take1.mp4" and best["score"] is None  # kept: one take, no review


def test_a_made_track_that_is_noise_is_not_called_music(tmp_path):
    assert cartoon.music_is_music(_tone(tmp_path / "tone.wav", 3.0, 330))
    noise = tmp_path / "noise.wav"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anoisesrc=d=3:c=white:a=0.5", str(noise)], check=True)
    assert not cartoon.music_is_music(noise)  # what "1%" got: a buzz the whole film


def test_a_clip_is_fitted_to_the_films_size_rate_and_length(tmp_path):
    clip = tmp_path / "take1.mp4"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=464x832:r=16:d=1.4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], check=True)
    out = cartoon.fit_clip(clip, 2.0, 1080, 1920, tmp_path / "picture.mp4")
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                      "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(out)],
                                     capture_output=True, check=True).stdout)
    st = info["streams"][0]
    assert (st["width"], st["height"], st["r_frame_rate"]) == (1080, 1920, "30/1")
    assert float(info["format"]["duration"]) == pytest.approx(2.0, abs=0.05)  # held on its last frame


def _quick_film(monkeypatch, tmp_path, engine):
    calls = {"board": 0, "frames": [], "animate": [], "rig": [], "drawn": []}
    board = {"title": "1%", "seconds": 4.0, "music": {"bpm": 120}, "look": {"style": "picture book"},
             "cast": [{"id": "dot", "who": "a small robot vacuum"}],
             "shots": [{"n": n, "start": 2.0 * (n - 1), "end": 2.0 * n, "what": WHAT} for n in (1, 2)]}

    def board_desk(story, folder, seconds, shape, style, words, eng):
        calls["board"] += 1
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "board.json").write_text(json.dumps(board))
        (folder / "settings.json").write_text(json.dumps({"engine": eng}))
        return board

    def frame_desk(board, board_dir, shot, shots, folder, w, h, words, eng):
        calls["frames"].append(shot.n)
        folder.mkdir(parents=True, exist_ok=True)
        return {"motion": "Dot rolls forward and bumps the rug.", "sounds": [{"t": 0.5, "sound": "a bump"}]}

    def animate_shot(folder, board, shot, spec, eng, log=print):
        calls["animate"].append((shot.n, eng))
        clip = _picture(folder / "take1.mp4", 2.0)
        return {"clip": clip, "score": 8}

    def rig_desk(board, board_dir, shot, shots, folder, w, h, words):
        calls["rig"].append(shot.n)
        folder.mkdir(parents=True, exist_ok=True)
        return _picture(folder / "clip.mp4", 2.0)

    from vidosuper.motion import videogen

    monkeypatch.setattr(cartoon, "board_desk", board_desk)
    monkeypatch.setattr(cartoon, "frame_desk", frame_desk)
    monkeypatch.setattr(cartoon, "animate_shot", animate_shot)
    monkeypatch.setattr(cartoon, "rig_desk", rig_desk)

    def drawn_desk(board, board_dir, shot, shots, folder, w, h, words):
        calls["drawn"].append(shot.n)
        folder.mkdir(parents=True, exist_ok=True)
        return _picture(folder / "clip.mp4", 2.0)

    monkeypatch.setattr(cartoon, "drawn_desk", drawn_desk)
    monkeypatch.setattr(cartoon, "music_for", lambda board, total, out, under_voice: None)
    def fit_clip(clip, secs, w, h, out, interpolate=True):
        calls.setdefault("interpolated", []).append(interpolate)
        return (shutil.copyfile(clip, out), out)[1]

    monkeypatch.setattr(cartoon, "fit_clip", fit_clip)
    monkeypatch.setattr(cartoon, "mix", lambda picture, total, music, narration, cues, library, out:
                        (shutil.copyfile(picture, out), out)[1:] + (cues,))
    monkeypatch.setattr(videogen, "release", lambda: None)
    return calls


def test_a_video_cartoon_paints_every_frame_then_animates_every_shot_and_picks_up_where_it_stopped(tmp_path,
                                                                                                    monkeypatch):
    calls = _quick_film(monkeypatch, tmp_path, "cartoon")
    work = tmp_path / "film"
    out = cartoon.make("A robot vacuum at 1% races to its dock.", work, 4.0, engine="cartoon", library=tmp_path,
                       log=lambda m: None)
    assert out["engine"] == "cartoon" and out["video"] == work / "cartoon.mp4" and out["video"].exists()
    assert sorted(calls["frames"]) == [1, 2] and calls["animate"] == [(1, "cartoon"), (2, "cartoon")]
    assert out["sounds"] == [{"t": 0.5, "sound": "a bump"}, {"t": 2.5, "sound": "a bump"}]  # at film time
    assert [s["score"] for s in out["shots"]] == [8, 8] and not calls["rig"]
    cartoon.make("A robot vacuum at 1% races to its dock.", work, 4.0, library=tmp_path, log=lambda m: None)
    assert calls["board"] == 1 and len(calls["animate"]) == 2  # all of it kept: nothing made twice


def test_a_rig_cartoon_is_built_shot_by_shot_in_blender(tmp_path, monkeypatch):
    calls = _quick_film(monkeypatch, tmp_path, "rig")
    out = cartoon.make("A robot vacuum at 1% races to its dock.", tmp_path / "film", 4.0, engine="rig",
                       library=tmp_path, log=lambda m: None)
    assert out["engine"] == "rig" and sorted(calls["rig"]) == [1, 2] and not calls["animate"]
    assert out["video"].exists() and calls["interpolated"] == [False, False]  # rendered at 30: its own frames


def test_a_drawn_cartoon_is_drawn_shot_by_shot_in_code(tmp_path, monkeypatch):
    calls = _quick_film(monkeypatch, tmp_path, "drawn")
    out = cartoon.make("A robot vacuum at 1% races to its dock.", tmp_path / "film", 4.0, engine="drawn",
                       library=tmp_path, log=lambda m: None)
    assert out["engine"] == "drawn" and sorted(calls["drawn"]) == [1, 2] and not calls["rig"] and not calls["animate"]
    assert out["video"].exists() and calls["interpolated"] == [False, False]


def test_a_drawn_film_needs_every_character_drawn_with_its_model_sheet(tmp_path):
    folder = _board_desk(tmp_path, [{"start": 0, "end": 3, "size": "wide", "angle": "low", "what": WHAT}],
                         cast=("dog",), seconds=3.0)
    st = json.loads((folder / "settings.json").read_text())
    (folder / "settings.json").write_text(json.dumps({**st, "engine": "drawn"}))
    (folder / "assets" / "style_key.png").unlink()
    _, problems = cartoon.check_board(folder)
    assert any("isn't drawn: write cast/dog.js" in p for p in problems)
    assert any("bash draw style_key style_key.js" in p for p in problems)
    (folder / "cast").mkdir()
    (folder / "cast" / "dog.js").write_text('toon.cast("dog", (c, o) => {});')
    _, problems = cartoon.check_board(folder)
    assert any("has no model sheet: bash cast dog" in p for p in problems)
    (folder / "cast" / "dog-sheet.png").write_bytes(b"png")
    (folder / "assets" / "style_key.png").write_bytes(b"png")
    _, problems = cartoon.check_board(folder)
    assert not any("dog" in p or "style key" in p for p in problems)


def test_the_drawn_page_loads_the_kit_the_cast_and_the_shot_and_is_timed_for_the_engine(tmp_path):
    from vidosuper.motion import drawn

    (tmp_path / "cast").mkdir()
    (tmp_path / "cast" / "dog.js").write_text('toon.cast("dog", (c, o) => {});')
    (tmp_path / "shot.js").write_text("toon.shot({ draw(c, w, h, t) {} });")
    folder = drawn.page(tmp_path, width=1080, height=1920, seconds=2.5, fps=30, script=tmp_path / "shot.js")
    html = (folder / "index.html").read_text()
    assert (folder / "toon.js").exists() and (folder / "cast" / "dog.js").exists() and (folder / "shot.js").exists()
    assert html.index("toon.js") < html.index("cast/dog.js") < html.index('src="shot.js"')
    assert 'data-composition-id="main"' in html and 'data-duration="2.500"' in html and 'width="1080"' in html
    assert 'window.__timelines["main"] = tl' in html and '"mode": "shot"' in html
    sheet = drawn.page(tmp_path, width=1920, height=1080, seconds=1, fps=30, sheet="dog", name="sheet")
    assert '"mode": "sheet", "id": "dog"' in (sheet / "index.html").read_text()


def test_drawn_code_that_does_not_parse_is_said_plainly(tmp_path):
    from vidosuper.motion import drawn

    if not shutil.which("node"):
        pytest.skip("Node isn't here")
    (tmp_path / "shot.js").write_text("toon.shot({ draw(c, w, h, t) { c.fillRect(0, 0, w h); } });")
    with pytest.raises(drawn.DrawnError, match="shot.js"):
        drawn.render(tmp_path, tmp_path / "shot.js", tmp_path / "clip.mp4", width=64, height=64, fps=30, seconds=1)


def test_a_drawn_shot_is_drawn_by_the_engine_and_an_error_shows_red(tmp_path):
    """The real engine (HyperFrames): a frame drawn from code at a time, and code that throws draws a red frame that
    says why, so the desk sees its mistake."""
    import cv2

    from vidosuper.motion import available, drawn

    if not available() or not shutil.which("node"):
        pytest.skip("the motion engine isn't installed here")
    (tmp_path / "shot.js").write_text(
        "toon.shot({ blur: 0, draw(c, w, h, t) { c.fillStyle = t < 0.5 ? '#0000ff' : '#00ff00'; c.fillRect(0, 0, w, h); } });")
    got = drawn.stills(tmp_path, tmp_path / "shot.js", [0.2, 0.8], tmp_path / "out", width=96, height=64, seconds=1)
    early, late = (cv2.imread(str(p))[32, 48] for p in got)
    assert early[0] > 200 and early[1] < 60 and late[1] > 200 and late[0] < 60  # BGR: blue, then green
    (tmp_path / "shot.js").write_text("toon.shot({ draw(c, w, h, t) { nothingHere(); } });")
    got = drawn.stills(tmp_path, tmp_path / "shot.js", [0.2], tmp_path / "out2", width=96, height=64, seconds=1)
    b, g, r = cv2.imread(str(got[0]))[2, 2]
    assert r > 40 and r > g and r > b  # the red error frame


def test_the_sound_never_clips(tmp_path, monkeypatch):
    picture = _picture(tmp_path / "p.mp4", 2.0)
    loud = tmp_path / "loud.wav"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=220:duration=3,volume=12dB", "-ar", "48000", str(loud)], check=True)
    monkeypatch.setattr(cartoon.scenes, "sound_for", lambda what, secs, lib: loud)
    out, _ = cartoon.mix(picture, 2.0, loud, None, [{"t": 0.5, "sound": "a crash", "gain": -4}], tmp_path,
                         tmp_path / "film.mp4")
    said = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(out), "-af", "volumedetect", "-vn", "-f", "null",
                           "-"], capture_output=True, text=True).stderr
    peak = float(re.search(r"max_volume: (-?[\d.]+) dB", said).group(1))
    assert peak < -0.5  # "1%" peaked at 0 dB


def test_the_agent_starts_a_cartoon_as_a_new_film_with_its_engine(studio_user, monkeypatch):
    from vidosuper.work import cartoons_in_studio

    studio, user = studio_user
    started = {}
    monkeypatch.setattr(cartoons_in_studio, "_make", lambda *a: started.setdefault("args", a))
    monkeypatch.setattr(cartoon, "engine_for", lambda asked="": asked or "rig")
    with pytest.raises(ValueError, match="Tell the story"):
        cartoons_in_studio.start(studio, user, "short")
    with pytest.raises(ValueError, match="cartoon, anime, rig, drawn"):
        cartoons_in_studio.start(studio, user, "A girl's kite pulls her into the sky.", 30, "1:1", engine="stickers")
    row = cartoons_in_studio.start(studio, user, "A girl's kite pulls her into the sky over the town.", 30, "1:1",
                                   "paper cut-out", engine="anime")
    p = studio.db.project(row["film"])
    assert row["kind"] == "cartoon" and row["status"] == "running" and p["shapes"] == "9:16"
    assert studio._info(p)["cartoon"] == {"shape": "1:1", "style": "paper cut-out", "engine": "anime"}
    import time

    for _ in range(50):
        if started:
            break
        time.sleep(0.02)
    assert started["args"][4:8] == ("A girl's kite pulls her into the sky over the town.", 30, "1:1", "paper cut-out")
    assert started["args"][-1] == "anime"
