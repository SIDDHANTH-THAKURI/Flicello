"""Create: AI-made clips through Higgsfield's API (create/higgsfield.py), asked for in plain words and taking a shot's
place as a draft; the pro sound-effect library (render/sfx.py)."""

from __future__ import annotations

import io
import json
import shutil

import pytest

from vidosuper.create import higgsfield

from test_faces import _drain_all, studio_film  # noqa: F401 - a finished film to work on (a fixture)


class FakeHiggsfield:
    """The API's answers: an upload slot, a request, two polls, then the video."""

    def __init__(self):
        self.calls = []
        self.polls = 0

    def __call__(self, req, timeout=None):
        url = req.full_url
        body = json.loads(req.data.decode()) if req.data and req.get_method() == "POST" else None
        self.calls.append((req.get_method(), url, body, dict(req.header_items())))
        if url.endswith("/files/generate-upload-url"):
            data = {"public_url": "https://cdn/x.jpg", "upload_url": "https://s3/put", "content_type": "image/jpeg",
                    "upload_headers": {"Content-Type": "image/jpeg", "x-amz-tagging": "t"}}
        elif url == "https://s3/put":
            data = {}
        elif "/kling-video/" in url:
            data = {"status": "queued", "request_id": "r1", "status_url": "https://api.higgsfield.ai/requests/r1/status"}
        elif url.endswith("/status"):
            self.polls += 1
            data = {"status": "in_progress"} if self.polls < 2 else {"status": "completed", "video": {"url": "https://cdn/v.mp4"}}
        else:
            return io.BytesIO(b"\x00" * 4096)
        return io.BytesIO(json.dumps(data).encode())


def test_a_clip_is_made_from_words_and_a_still(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_API_KEY_ID", "kid")
    monkeypatch.setenv("HF_API_KEY_SECRET", "sec")
    fake = FakeHiggsfield()
    still = tmp_path / "s.jpg"
    still.write_bytes(b"jpeg")
    made = higgsfield.generate("a drone over the beach", tmp_path / "out.mp4", image=still, seconds=6, opener=fake,
                               sleep=lambda s: None)
    assert (tmp_path / "out.mp4").stat().st_size == 4096 and made == {"request_id": "r1", "model": "kling-3-std",
                                                                       "seconds": 6, "usd": 0.252}
    methods = [(m, u.replace("https://api.higgsfield.ai", "")) for m, u, _, _ in fake.calls]
    assert methods[:3] == [("POST", "/files/generate-upload-url"), ("PUT", "https://s3/put"),
                           ("POST", "/kling-video/v3.0/std/image-to-video")]
    sent = fake.calls[2]
    assert sent[2] == {"prompt": "a drone over the beach", "duration": 6, "sound": "off", "image_url": "https://cdn/x.jpg"}
    assert sent[3]["Authorization"] == "Key kid:sec"
    assert "Authorization" not in fake.calls[1][3]  # the key never goes to the storage URL
    # words alone: text to video, in the film's shape
    fake2 = FakeHiggsfield()
    higgsfield.generate("waves", tmp_path / "o2.mp4", seconds=20, aspect="9:16", opener=fake2, sleep=lambda s: None)
    body = fake2.calls[0][2]
    assert fake2.calls[0][1].endswith("/std/text-to-video") and body["aspect_ratio"] == "9:16" and body["duration"] == 15


def test_without_a_key_it_says_what_to_add(monkeypatch):
    monkeypatch.setenv("HF_API_KEY_ID", "")
    monkeypatch.setenv("HF_API_KEY_SECRET", "")
    assert not higgsfield.available()
    with pytest.raises(higgsfield.HiggsfieldError, match="Connect page"):
        higgsfield._call("GET", "https://api.higgsfield.ai/x")


def test_an_ai_clip_asked_for_takes_a_shots_place(studio_film, media, monkeypatch):
    from vidosuper.studio import agent

    studio, client, csrf, pid, work = studio_film
    monkeypatch.setenv("HF_API_KEY_ID", "kid")
    monkeypatch.setenv("HF_API_KEY_SECRET", "sec")

    def fake_generate(prompt, out, **kw):
        shutil.copyfile(media / "clips" / "b_shaky.mp4", out)  # a clip the film doesn't have
        return {"request_id": "r1", "model": "kling-3-std", "seconds": 5, "usd": 0.21}

    monkeypatch.setattr(higgsfield, "generate", fake_generate)
    client.post(f"/p/{pid}/talk", data={"csrf": csrf, "text": "make an AI clip of waves at sunset for shot 2"},
                headers={"Accept": "application/json"})
    assert agent.thread(studio.db, pid)[-1]["body"]["text"].startswith("Making it with an AI video model")
    _drain_all(studio)
    assert list(work.glob("clips/ai-01.mp4"))
    draft = studio.draft_of(studio.db.project(pid))
    assert draft and draft["said"][0].startswith("Shot 2 now shows")
    plan = studio.draft_plan(studio.db.project(pid))
    pool = {m.key: m for m in plan.pool}
    from vidosuper.perceive.catalog import short_ids
    from vidosuper.workspace import Project

    project = Project(work / "clips", studio.cfg)
    ai = next(c.clip_id for c in project.clips().values() if c.rel_path.endswith("ai-01.mp4"))
    assert pool[plan.plan[1].key].clip == short_ids(project)[ai]  # shot 2 is the AI clip
    spent = studio.db.one("SELECT model, backend, usd FROM spend WHERE task='generate'")
    assert dict(spent) == {"model": "kling-3-std", "backend": "higgsfield", "usd": 0.21}


def test_the_pro_sounds_are_made_once_and_kept(tmp_path, monkeypatch):
    from vidosuper.render import sfx

    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    assert sfx.pro_path("whoosh", tmp_path) is None  # no key: the synthesized whoosh plays
    assert sfx.sfx_path("whoosh", tmp_path).name == "whoosh.wav"
    kept = tmp_path / "pro" / "impact.wav"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"\x00" * 2048)
    assert sfx.sfx_path("impact", tmp_path) == kept  # made before: used, no call


@pytest.mark.skipif(not __import__("vidosuper.create.effects", fromlist=["available"]).available(
    "depth/depth_anything_v2_small.onnx"), reason="the depth model isn't installed")
def test_a_shot_becomes_a_3d_photo(studio_film):
    from vidosuper.studio import agent

    studio, client, csrf, pid, work = studio_film
    client.post(f"/p/{pid}/talk", data={"csrf": csrf, "text": "make shot 2 a 3d photo"}, headers={"Accept": "application/json"})
    assert agent.thread(studio.db, pid)[-1]["body"]["text"].startswith("On it: shot 2 a 3D photo")
    _drain_all(studio)
    made = list(work.glob("clips/fx-01.mp4"))
    assert made and made[0].stat().st_size > 10_000
    draft = studio.draft_of(studio.db.project(pid))
    assert draft and draft["said"][0].startswith("Shot 2 now shows")


def test_effects_make_real_pictures(tmp_path):
    import numpy as np

    from vidosuper.create import effects
    from vidosuper.ffmpeg import ffprobe_json

    if not effects.available(effects.DEPTH):
        pytest.skip("the depth model isn't installed")
    rgb = np.zeros((180, 320, 3), np.uint8)
    rgb[:, :160] = (40, 90, 160)  # a far wall and a near box
    rgb[60:160, 180:260] = (230, 200, 60)
    d = effects.depth(rgb)
    assert d.shape == (180, 320) and 0.0 <= d.min() and d.max() <= 1.0
    out = effects.parallax(rgb, tmp_path / "p.mp4", seconds=1.0, fps=10)
    v = ffprobe_json(out)["streams"][0]
    assert (int(v["width"]), int(v["height"])) == (320, 180) and int(v.get("nb_frames", 10)) == 10


def test_a_stretch_of_the_music_is_made_again_and_the_rest_kept():
    from vidosuper.music.sections import plan
    from vidosuper.studio import agent

    p = plan("song1", 30000, 21000, 30000, ["calmer"], [])
    assert p["chunks"][0] == {"song_id": "song1", "range": {"start_ms": 0, "end_ms": 21000}}
    assert p["chunks"][1]["duration_ms"] == 9000 and p["chunks"][1]["context_adherence"] == "high" and len(p["chunks"]) == 2
    mid = plan("song1", 30000, 10000, 20000, ["bigger drums"], [])
    assert [c.get("range") for c in mid["chunks"]] == [{"start_ms": 0, "end_ms": 10000}, None,
                                                         {"start_ms": 20000, "end_ms": 30000}]
    assert agent.music_asked("make the ending calmer", {"length": 30}) == (21.0, None, "calmer")
    assert agent.music_asked("make the music bigger from 0:20 to 0:35", {"length": 60}) == (20.0, 35.0, "bigger")


def test_their_own_song_cant_be_changed_in_a_stretch(studio_film):
    from vidosuper.studio import agent

    studio, client, csrf, pid, work = studio_film
    client.post(f"/p/{pid}/talk", data={"csrf": csrf, "text": "make the ending calmer"}, headers={"Accept": "application/json"})
    assert agent.thread(studio.db, pid)[-1]["body"]["text"].startswith("Only music made here can be changed")


def test_an_object_is_filled_in_only_inside_its_box(tmp_path, monkeypatch):
    import numpy as np

    from vidosuper.create import effects
    from vidosuper.ffmpeg import ffmpeg_bin

    src = tmp_path / "src.mp4"
    import subprocess

    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x3060a0:s=320x240:r=10:d=1",
                    "-vf", "drawbox=x=140:y=100:w=40:h=40:color=red:t=fill", "-pix_fmt", "yuv420p", str(src)], check=True)
    calls = []

    def fake_lama(rgb, mask):  # "fills" the hole with the sky colour around it
        calls.append(1)
        out = rgb.copy()
        out[mask > 0.5] = (48, 96, 160)
        return out

    monkeypatch.setattr(effects, "_lama", fake_lama)
    out = effects.remove(src, 0.0, 1.0, [[0.0, 0.5, 0.5]], (40 / 320, 40 / 240), tmp_path / "out.mp4", every=4)
    frames = list(effects.frames(out, 0.0, 1.0))
    assert len(frames) == 10 and len(calls) == 3  # filled on frames 0, 4 and 8; carried between
    mid = frames[5][120, 160].astype(int)
    corner = frames[5][10, 10].astype(int)
    assert mid[0] < 100 and abs(mid[2] - 160) < 30  # the red box is gone
    assert abs(corner[2] - 160) < 30  # the rest as it was


def test_a_shot_from_words_is_made_here_on_a_gpu_else_by_higgsfield_else_says_how(tmp_path, monkeypatch):
    from vidosuper.create import clips

    monkeypatch.delenv("VIDO_AI_CLIPS", raising=False)
    monkeypatch.setattr(clips, "local_ready", lambda: False)
    monkeypatch.setattr(higgsfield, "available", lambda: False)
    assert clips.maker() is None and "Connect page" in clips.describe(None)
    with pytest.raises(clips.ClipError, match="Higgsfield key"):
        clips.make("waves at sunset", tmp_path / "x.mp4")
    monkeypatch.setattr(higgsfield, "available", lambda: True)
    assert clips.maker() == "higgsfield"
    monkeypatch.setattr(clips, "local_ready", lambda: True)
    assert clips.maker() == "local"  # the GPU here first: no balance spent
    monkeypatch.setenv("VIDO_AI_CLIPS", "higgsfield")
    assert clips.maker() == "higgsfield"  # unless they choose Higgsfield


def test_a_shot_from_words_on_this_gpu_is_painted_then_animated_at_the_films_size(tmp_path, monkeypatch, media):
    from vidosuper.create import clips
    from vidosuper.motion import cartoon, scenes, videogen

    monkeypatch.setenv("VIDO_AI_CLIPS", "local")
    monkeypatch.setattr(clips, "local_ready", lambda: True)
    seen = {}

    def paint(what, out, light=None, like=None, whole=False, aspect=9 / 16):
        seen["paint"] = (what, whole, round(aspect, 3))
        out.write_bytes(b"png")
        return 1080, 1920

    def animate(image, prompt, out, **kw):
        seen["animate"] = (prompt, kw["seconds"])
        shutil.copyfile(media / "clips" / "b_shaky.mp4", out)

    monkeypatch.setattr(scenes, "paint", paint)
    monkeypatch.setattr(videogen, "animate", animate)
    monkeypatch.setattr(videogen, "release", lambda: None)
    monkeypatch.setattr(cartoon, "fit_clip", lambda clip, secs, w, h, out, interpolate=True:
                        (seen.setdefault("fit", (secs, w, h)), shutil.copyfile(clip, out)))
    made = clips.make("gulls over the Opera House at sunrise", tmp_path / "shot.mp4", seconds=5, aspect="9:16")
    assert made == {"maker": "local", "model": "wan-2.2-i2v", "seconds": 5, "usd": 0.0}
    assert seen["paint"][1] and seen["paint"][2] == round(1080 / 1920, 3) and "Opera House" in seen["paint"][0]
    assert seen["animate"][0] == "gulls over the Opera House at sunrise" and seen["fit"] == (5.0, 1080, 1920)
    assert (tmp_path / "shot.mp4").exists() and not (tmp_path / "shot").exists()


def test_a_shot_from_words_at_the_clips_step_joins_their_clips(studio_film, media, monkeypatch):
    from vidosuper.create import clips

    studio, client, csrf, pid, work = studio_film
    studio.db.set_stage(pid, "setup")
    monkeypatch.setattr(clips, "maker", lambda: "higgsfield")

    def make(prompt, out, **kw):
        shutil.copyfile(media / "clips" / "b_shaky.mp4", out)
        return {"maker": "higgsfield", "model": "kling-3-std", "seconds": 5, "usd": 0.21}

    monkeypatch.setattr(clips, "make", make)
    before = studio.db.value("SELECT COUNT(*) FROM clips WHERE project_id=?", (pid,), 0)
    assert "Make a shot from words" in client.get(f"/p/{pid}").text
    client.post(f"/p/{pid}/ai-clip", data={"csrf": csrf, "prompt": "waves at sunset"})
    _drain_all(studio)
    rows = studio.db.all("SELECT * FROM clips WHERE project_id=?", (pid,))
    assert len(rows) == before + 1 and rows[-1]["name"].startswith("Made: waves at sunset")
    assert (work / "clips" / rows[-1]["file"]).exists() and studio.db.project(pid)["stage"] == "setup"


def test_the_consoles_one_line_key_works_too(monkeypatch):
    monkeypatch.setenv("HF_API_KEY_ID", "")
    monkeypatch.setenv("HF_API_KEY_SECRET", "")
    monkeypatch.setenv("HIGGSFIELD_API_KEY", "kid-123:secret-456")
    assert higgsfield.credentials() == ("kid-123", "secret-456")
    monkeypatch.setenv("HIGGSFIELD_API_KEY", "no-colon-here")
    assert higgsfield.credentials() is None
