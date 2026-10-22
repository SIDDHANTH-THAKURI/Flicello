"""Who is in the footage (studio/faces.py): faces followed through a clip, grouped into people across clips, their
choices kept when the index is made again, and the shots of a version they're in."""

from __future__ import annotations

import json
import subprocess

import numpy as np
import pytest

from vidosuper.ffmpeg import ffmpeg_bin
from vidosuper.studio import faces


def _print(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=128).astype(np.float32)
    return v / np.linalg.norm(v)


class FakeFaces:
    """Stands in for YuNet + SFace: who is in each frame comes from `script(t)` (time in the clip -> faces)."""

    def __init__(self, script):
        import cv2

        self.cv2 = cv2
        self.script = script
        self.t = -1

    def find(self, frame):
        self.t += 1
        out = []
        for x, y, w, h, who in self.script(self.t):
            face = np.array([x * frame.shape[1], y * frame.shape[0], w * frame.shape[1], h * frame.shape[0],
                             0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.95], dtype=np.float32)
            noise = np.random.default_rng(self.t).normal(scale=0.05, size=128).astype(np.float32)
            v = _print(who) + noise
            out.append((face, v / np.linalg.norm(v)))
        return out


@pytest.fixture()
def clips(tmp_path):
    made = []
    for k in range(2):
        path = tmp_path / f"clip{k}.mp4"
        subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
                        "-t", "3", "-pix_fmt", "yuv420p", str(path)], check=True)
        made.append((f"c0{k + 1}", f"clip{k}-id", path))
    return made


def test_faces_are_followed_grouped_into_people_and_their_choices_kept(clips, tmp_path, monkeypatch):
    # clip 1 (looked at 12 times: 3 s at 4 a second): person A close up all through, person B far off at the end;
    # clip 2: person A again, for its first half
    def script(n):
        if n < 12:
            return [(0.40, 0.30, 0.20, 0.30, 1)] + ([(0.05, 0.05, 0.03, 0.04, 2)] if n >= 8 else [])
        return [(0.50, 0.30, 0.18, 0.28, 1)] if n - 12 < 6 else []

    monkeypatch.setattr(faces, "_Faces", lambda: FakeFaces(script))
    out = tmp_path / "work" / faces.INDEX
    index = faces.build(clips, tmp_path / "work" / "stills", out)
    people = index["people"]
    assert len(people) == 2
    a, b = people  # the most screen time first
    assert a["clips"] == ["c01", "c02"] and a["near"] and a["seconds"] > 3.5 and (tmp_path / "work" / "stills" / a["thumb"]).exists()
    assert b["clips"] == ["c01"] and not b["near"]
    first = next(s for s in a["seen"] if s["clip"] == "c01")
    assert first["from"] == 0.0 and first["to"] >= 2.8 and first["size"] == "near"
    assert faces.label(a) == "Person 1" and "print" not in faces.public(index)[0]
    # they say who is who; making the index again (new clips, a retry) keeps it
    a["me"], b["never"], b["name"] = True, True, "Sam"
    faces.save(out, index)
    again = faces.build(clips, tmp_path / "work" / "stills", out)["people"]
    assert again[0]["me"] and faces.label(again[0]) == "You"
    assert again[1]["never"] and faces.label(again[1]) == "Sam"
    # the shots of a version they're in (close up only, unless asked), and their scenes as bans
    cards = [{"n": 1, "clip": "c01", "src": 1.5, "start": 0.0, "end": 1.0},
             {"n": 2, "clip": "c02", "src": 2.5, "start": 1.0, "end": 1.5},
             {"n": 3, "clip": "c02", "src": 0.2, "start": 1.5, "end": 2.5}]
    assert faces.shots_with(again[0], cards) == [1, 3]
    assert faces.shots_with(again[1], cards) == [] and faces.shots_with(again[1], cards, near_only=False) == [1]
    bans = faces.bans(again[1])
    assert bans and bans[0]["clip"] == "c01" and bans[0]["what"] == "Sam in shot"


def test_a_model_that_doesnt_match_its_checksum_is_never_used(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDO_MODELS_DIR", str(tmp_path))
    bad = tmp_path / "faces"
    bad.mkdir()
    (bad / faces.MODELS["detector"][0]).write_bytes(b"not a model")

    class Fake:
        def __init__(self, data):
            self.data = data

        def read(self, n):
            out, self.data = self.data[:n], self.data[n:]
            return out

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(faces.urllib.request, "urlopen", lambda url, timeout: Fake(b"still not the model"))
    with pytest.raises(RuntimeError, match="checksum"):
        faces.ensure_models()
    assert not list(bad.glob("*.part"))


@pytest.fixture()
def studio_film(tmp_path, media, monkeypatch):
    """A friend's finished film (version 1, made with the no-AI brain from synthetic clips), signed in."""
    import re

    from fastapi.testclient import TestClient

    from vidosuper.perceive import prep as prep_module
    from vidosuper.perceive.transcribe import Transcript
    from vidosuper.studio import accounts
    from vidosuper.studio.service import Studio
    from vidosuper.studio.settings import load_settings
    from vidosuper.studio.web.app import create_app

    monkeypatch.delenv("VIDO_STUDIO_BRAIN", raising=False)
    monkeypatch.setattr(prep_module, "transcribe", lambda cid, wav, model, root: Transcript(clip_id=cid, model="fake",
                                                                                            language="en"))
    s = load_settings(tmp_path / "home", {"brain": "local", "owner_brain": "local", "render_preset": "ultrafast",
                                         "render_processes": 1})
    s.limits.min_free_gb = 0.0
    studio = Studio(s)
    studio.soon = lambda fn: fn()
    friend = accounts.register(studio.db, accounts.create_invite(studio.db, s, "Ana"), "Ana", "a long password 2")
    client = TestClient(create_app(studio))
    client.post("/login", data={"name": "Ana", "password": "a long password 2"})
    csrf = re.search(r'name="csrf" content="([^"]+)"', client.get("/", follow_redirects=True).text).group(1)
    client.post("/welcome", data={"csrf": csrf, "pace": "fast", "sound": "music", "look": "vivid"})
    r = client.post("/new", data={"csrf": csrf, "title": "Beach", "shapes": "16:9", "length": 15, "intent": "fun"},
                    follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    for path, kind in [(media / "clips" / n, "clip") for n in ("a_stable.mp4", "c_pan.mp4", "f_portrait60.mov")] + \
            [(media / "music.wav", "song")]:
        data = path.read_bytes()
        up = client.post(f"/api/p/{pid}/uploads", json={"kind": kind, "name": path.name, "size": len(data)},
                         headers={"X-CSRF": csrf}).json()
        got = 0
        while got < len(data):
            got = client.put(f"/api/uploads/{up['id']}?offset={got}", content=data[got:got + 300_000],
                             headers={"X-CSRF": csrf}).json()["received"]
        client.post(f"/api/uploads/{up['id']}/done", headers={"X-CSRF": csrf})
    client.post(f"/p/{pid}/start", data={"csrf": csrf})
    _drain_all(studio)
    client.post(f"/p/{pid}/understood", data={"csrf": csrf, "story": "a day at the beach"})
    _drain_all(studio)
    pitch = int(re.search(r'name="pitch" value="(\d+)"', client.get(f"/p/{pid}").text).group(1))
    client.post(f"/p/{pid}/pick", data={"csrf": csrf, "pitch": pitch})
    _drain_all(studio)
    client.post(f"/p/{pid}/sample", data={"csrf": csrf, "verdict": "ok"})
    _drain_all(studio)
    assert studio.db.project(pid)["version"] == 1
    return studio, client, csrf, pid, s.users_dir / friend["id"] / "projects" / pid


def test_people_on_a_finished_film_cut_their_shots_and_are_never_shown_again(studio_film):
    studio, client, csrf, pid, work = studio_film
    from vidosuper.studio import agent

    strip = studio.strip_of(studio.db.project(pid), 1)
    first = strip["cards"][0]
    index = {"version": faces.VERSION, "made": 0, "people": [
        {"id": "p1", "name": "", "me": False, "never": False, "thumb": "", "seconds": 2.0, "clips": [first["clip"]],
         "near": True, "print": [0.0] * 128,
         "seen": [{"clip": first["clip"], "clip_id": "x", "from": max(0.0, first["src"] - 0.2), "to": first["src"] + 0.5,
                   "size": "near", "x": 0.5, "y": 0.4}]},
        {"id": "p2", "name": "", "me": False, "never": False, "thumb": "", "seconds": 1.0, "clips": ["c99"],
         "near": True, "print": [0.0] * 128,
         "seen": [{"clip": "c99", "clip_id": "y", "from": 1.0, "to": 2.0, "size": "near", "x": 0.5, "y": 0.4}]},
    ]}
    (work / faces.INDEX).write_text(json.dumps(index), encoding="utf-8")
    page = client.get(f"/p/{pid}").text
    assert "People in your clips" in page and "That&#39;s me" in page.replace("'", "&#39;")

    # "cut where my face shows": which one is you? (a tap on a face, no model)
    client.post(f"/p/{pid}/talk", data={"csrf": csrf, "text": "cut where my face shows"}, headers={"Accept": "application/json"})
    m = agent.thread(studio.db, pid)[-1]
    assert m["body"]["cards"][0]["type"] == "who" and [f["id"] for f in m["body"]["cards"][0]["people"]] == ["p1", "p2"]
    client.post(f"/p/{pid}/card", data={"csrf": csrf, "mid": m["id"], "k": 0, "choice": "p1"})
    people = faces.load(work / faces.INDEX)["people"]
    assert people[0]["me"] and not people[1]["me"]
    said = agent.thread(studio.db, pid)[-1]["body"]["text"]
    assert said.startswith("On it: cutting shot") and "where you're close up" in said
    assert studio.db.active_job(pid)["kind"] == "draft"
    _drain_all(studio)
    draft = studio.draft_of(studio.db.project(pid))
    assert draft and "removed shot 1 " in " ".join(draft["said"]).lower()
    first_in_draft = client.get(f"/api/p/{pid}/preview?which=draft").json()["shots"][0]
    assert (first_in_draft["clip"], first_in_draft["in"]) != (first["clip"], first["src"])  # the shot with them is gone

    # never show person 2: their scenes are banned for every later plan, and the brief says so
    client.post(f"/p/{pid}/draft/discard", data={"csrf": csrf})
    client.post(f"/p/{pid}/people", data={"csrf": csrf, "person": "p2", "op": "never"})
    info = json.loads(studio.db.project(pid)["info"])
    assert any(b["clip"] == "c99" and b["what"] == "Person 2 in shot" for b in info["bans"])
    assert any(it["text"] == "Never show Person 2" for it in studio.spec_items(studio.db.project(pid)))
    client.post(f"/p/{pid}/people", data={"csrf": csrf, "person": "p2", "op": "show"})
    assert not any(b["clip"] == "c99" for b in json.loads(studio.db.project(pid)["info"])["bans"])
    # and a name, said in words later ("remove the shots with Mia") is found by it
    client.post(f"/p/{pid}/people", data={"csrf": csrf, "person": "p1", "op": "name", "name": "Mia"})
    assert faces.load(work / faces.INDEX)["people"][0]["name"] == "Mia"


def _drain_all(studio, limit: int = 10):
    from vidosuper.studio.worker import Worker

    w = Worker(studio)
    for _ in range(limit):
        if not w.tick():
            return
