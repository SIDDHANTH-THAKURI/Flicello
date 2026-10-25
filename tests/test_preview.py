"""The instant preview (studio/preview.py): light copies of the clips, an edit as a timeline the browser plays at once,
and drafts. A plain ask is made as a draft by code with no model in the way, watched in seconds, and only "make it"
renders a new version from exactly that draft."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from vidosuper.ffmpeg import ffmpeg_bin, ffprobe_bin, ffprobe_json
from vidosuper.perceive import prep as prep_module
from vidosuper.perceive.transcribe import Transcript
from vidosuper.schemas import EDL, EditMeta, Fx, MusicSpec, RampPoint, Segment, Transition
from vidosuper.studio import agent, preview

from conftest import local_client
from vidosuper.studio.service import Studio
from vidosuper.studio.settings import load_settings
from vidosuper.studio.worker import Worker


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDO_STUDIO_BRAIN", raising=False)
    s = load_settings(tmp_path / "home", {"brain": "local", "owner_brain": "local", "render_preset": "ultrafast",
                                         "render_processes": 1})
    s.limits.min_free_gb = 0.0
    st = Studio(s)
    st.soon = lambda fn: fn()
    return st


def test_an_edit_becomes_a_timeline_the_browser_can_play():
    edl = EDL(project_dir="x", meta=EditMeta(width=1080, height=1920, fps=30, look="vivid", fade_in=0.5), segments=[
        Segment(id="s01", clip_id="a", src_in=2.0, src_out=4.0, zoom=1.2, zoom_center=(0.4, 0.5),
                fx=[Fx(type="flash", at=0.5, duration=0.3)]),
        Segment(id="s02", clip_id="b", src_in=1.0, src_out=3.0, transition_in=Transition(type="dissolve", duration=0.5),
                flip=True, ramp=[RampPoint(at=0.0, speed=2.0), RampPoint(at=1.0, speed=0.5)]),
        Segment(id="s03", clip_id="a", src_in=6.0, src_out=7.0, speed=0.5, freeze=0.5),
    ], music=MusicSpec(path="C:/w/music/song.wav", start=12.5))
    tl = preview.timeline(edl, lambda c: f"/m/p/preview/{c}.mp4" if c == "a" else None, "/m/p/music/song.wav",
                          {"s01": 1, "s02": 2, "s03": 2})
    a, b, c = tl["shots"]
    assert (tl["w"], tl["h"], tl["fps"], tl["look"]) == (1080, 1920, 30, "vivid")
    assert a["src"] == "/m/p/preview/a.mp4" and b["src"] is None  # a clip without its copy yet: shown as missing
    assert a["start"] == 0 and a["end"] == pytest.approx(2.0) and a["zoom"] == 1.2 and a["fx"][0]["type"] == "flash"
    assert b["start"] == pytest.approx(1.5) and b["trans"] == {"type": "dissolve", "dur": pytest.approx(0.5)}
    assert b["flip"] and b["mute"] and len(b["steps"]) > 2  # a ramp plays as small speed steps, without its sound
    assert b["steps"][0][1] > 1.5 and b["steps"][-1][1] < 0.8  # it rushes in and sinks into slow motion
    assert c["steps"] == [[0.0, 0.5]] and c["end"] - c["start"] == pytest.approx(2.5, abs=0.04)  # slow + a held frame
    assert [x["n"] for x in tl["shots"]] == [1, 2, 2]  # numbered as the strip counts them
    assert tl["music"]["start"] == 12.5 and tl["music"]["src"] == "/m/p/music/song.wav"
    muted = preview.timeline(edl, lambda c: None, None, clip_sound=False)
    assert all(x["mute"] for x in muted["shots"]) and muted["music"] is None and not muted["clip_sound"]


def test_a_light_copy_seeks_fast_and_keeps_its_sound(tmp_path):
    proxy, wav, out = tmp_path / "p.mp4", tmp_path / "a.wav", tmp_path / "preview" / "c1.mp4"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
                    "-t", "3", "-pix_fmt", "yuv420p", str(proxy)], check=True)
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-ar", "48000", str(wav)], check=True)
    preview.make_copy(proxy, wav, out)
    info = ffprobe_json(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert v["codec_name"] == "h264" and max(v["width"], v["height"]) == preview.LONG_EDGE
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    keys = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v",
                           "-skip_frame", "nokey", "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(out)],
                          capture_output=True, text=True).stdout.split()
    assert len(keys) >= 5  # a keyframe every half second: a seek anywhere lands fast
    assert not list(out.parent.glob("*.part.mp4"))


def _csrf(html: str) -> str:
    return re.search(r'name="csrf" content="([^"]+)"', html).group(1)


def _upload(client, csrf, pid, path: Path, kind: str):
    data = path.read_bytes()
    up = client.post(f"/api/p/{pid}/uploads", json={"kind": kind, "name": path.name, "size": len(data)},
                     headers={"X-CSRF": csrf}).json()
    got = 0
    while got < len(data):
        got = client.put(f"/api/uploads/{up['id']}?offset={got}", content=data[got:got + 300_000],
                         headers={"X-CSRF": csrf}).json()["received"]
    assert client.post(f"/api/uploads/{up['id']}/done", headers={"X-CSRF": csrf}).status_code == 200


def _drain(studio: Studio, limit: int = 10) -> None:
    w = Worker(studio)
    for _ in range(limit):
        if not w.tick():
            return


def test_a_plain_ask_is_a_draft_in_seconds_watched_at_once_and_made_only_on_make_it(studio, media, monkeypatch):
    monkeypatch.setattr(prep_module, "transcribe", lambda cid, wav, model, root: Transcript(clip_id=cid, model="fake",
                                                                                            language="en"))
    client, friend, csrf = local_client(studio)
    client.post("/welcome", data={"csrf": csrf, "pace": "fast", "sound": "real", "look": "vivid"})
    r = client.post("/new", data={"csrf": csrf, "title": "Beach", "shapes": "16:9", "length": 15, "intent": "fun"},
                    follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    for name in ("a_stable.mp4", "c_pan.mp4", "f_portrait60.mov"):
        _upload(client, csrf, pid, media / "clips" / name, "clip")
    _upload(client, csrf, pid, media / "music.wav", "song")
    client.post(f"/p/{pid}/start", data={"csrf": csrf})
    _drain(studio)
    work = studio.s.users_dir / friend["id"] / "projects" / pid
    assert len(list((work / "preview").glob("*.mp4"))) == 3  # the light copies are made while the clips are watched
    client.post(f"/p/{pid}/understood", data={"csrf": csrf, "story": "a day at the beach"})
    _drain(studio)
    pitch = int(re.search(r'name="pitch" value="(\d+)"', client.get(f"/p/{pid}").text).group(1))
    client.post(f"/p/{pid}/pick", data={"csrf": csrf, "pitch": pitch})
    _drain(studio)
    client.post(f"/p/{pid}/sample", data={"csrf": csrf, "verdict": "ok"})
    _drain(studio)
    assert studio.db.project(pid)["version"] == 1

    # any version plays at once: every shot from its light copy, the music from the film's song
    v1 = client.get(f"/api/p/{pid}/preview?which=1").json()
    assert v1["shots"] and all(s["src"] for s in v1["shots"]) and not v1["missing"]
    assert client.get(v1["shots"][0]["src"]).status_code == 200
    assert v1["music"] and client.get(v1["music"]["src"]).status_code == 200
    assert v1["clip_sound"]  # they answered "keep the clips' own sound": the preview plays it too

    # a plain ask: read by code, made as a draft, no model in the way (the editor's brain would fail if asked)
    class NoModel:
        def editor_reply(self, *a, **k):
            raise AssertionError("a plain ask must not wait for a model")

    monkeypatch.setattr(studio, "editor_brain", lambda project, user: NoModel())
    r = client.post(f"/p/{pid}/talk", data={"csrf": csrf, "text": "swap 1 and 2"}, headers={"Accept": "application/json"})
    assert r.status_code == 200
    reply = agent.thread(studio.db, pid)[-1]
    assert reply["body"].get("fast") and reply["body"]["text"].startswith("On it: Shots 1 and 2 trade places")
    assert studio.db.active_job(pid)["kind"] == "draft" and studio.db.project(pid)["stage"] == "done"
    assert "Making your draft" in client.get(f"/p/{pid}").text
    _drain(studio)
    m = agent.thread(studio.db, pid)[-1]
    assert m["kind"] == "checkin" and m["body"]["text"].startswith("Your draft is ready")
    assert m["body"]["cards"][0]["action"] == "make_draft"
    p = studio.db.project(pid)
    assert p["version"] == 1 and studio.draft_of(p)  # nothing made, no change used
    d = client.get(f"/api/p/{pid}/preview?which=draft").json()
    assert [s["in"] for s in d["shots"][:2]] == [s["in"] for s in v1["shots"][:2]][::-1] or \
        [s["clip"] for s in d["shots"][:2]] == [s["clip"] for s in v1["shots"][:2]][::-1]
    page = client.get(f"/p/{pid}").text
    assert "Your draft" in page and 'data-preview="' in page and "/draft/make" in page

    # make it: a new version rendered from exactly that draft
    client.post(f"/p/{pid}/card", data={"csrf": csrf, "mid": m["id"], "k": 0, "choice": "yes"})
    assert studio.db.active_job(pid)["kind"] == "revise" and not studio.draft_of(studio.db.project(pid))
    _drain(studio)
    p = studio.db.project(pid)
    assert p["version"] == 2 and p["revisions"] == 1
    v2 = client.get(f"/api/p/{pid}/preview?which=2").json()
    assert [(s["clip"], s["in"]) for s in v2["shots"]] == [(s["clip"], s["in"]) for s in d["shots"]]
    assert "Version 2 is ready" in agent.thread(studio.db, pid)[-1]["body"]["text"]

    # another draft, discarded: the version stays as it was
    client.post(f"/p/{pid}/talk", data={"csrf": csrf, "text": "remove 2"}, headers={"Accept": "application/json"})
    _drain(studio)
    assert studio.draft_of(studio.db.project(pid))
    client.post(f"/p/{pid}/draft/discard", data={"csrf": csrf})
    assert not studio.draft_of(studio.db.project(pid)) and studio.db.project(pid)["version"] == 2

    # a film made before the light copies existed gets them as soon as a preview is asked for
    shutil.rmtree(work / "preview")
    again = client.get(f"/api/p/{pid}/preview?which=2").json()
    assert again["missing"] and again.get("making") and studio.db.active_job(pid)["kind"] == "copies"
    _drain(studio)
    assert not client.get(f"/api/p/{pid}/preview?which=2").json()["missing"]
    assert client.get(f"/m/{pid}/preview/..%2Fstudio.db").status_code == 404
