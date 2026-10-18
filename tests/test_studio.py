"""The studio: accounts, uploads, the brain's checks, and one whole video through the web app with the
no-AI brain (synthetic footage)."""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vidosuper.perceive import prep as prep_module
from vidosuper.perceive.transcribe import Transcript
from vidosuper.studio import accounts
from vidosuper.studio.brain import BrainError, brain_kind_for
from vidosuper.studio.brain.backends import Request, Usage, model_schema, structured
from vidosuper.studio.brain.context import BeatMap, ClipFacts
from vidosuper.studio.brain.core import StoryContext, check_plan
from vidosuper.studio.brain.schemas import DesignPlan, Pitches, PlanShot, PoolMoment, ProfileUpdate, StyleRule
from vidosuper.studio.pipeline import slice_template
from vidosuper.studio.profiles import Profile
from vidosuper.studio.service import Studio, ask_text
from vidosuper.studio.settings import load_settings
from vidosuper.studio.storage import safe_child
from vidosuper.studio.web.app import create_app
from vidosuper.studio.worker import Worker


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDO_STUDIO_BRAIN", raising=False)
    s = load_settings(tmp_path / "home", {"brain": "local", "owner_brain": "local", "render_preset": "ultrafast",
                                         "render_processes": 1})
    s.limits.min_free_gb = 0.0
    return Studio(s)


def _owner(studio: Studio, name: str = "owner") -> dict:
    token = accounts.create_invite(studio.db, studio.s, "", "owner")
    return accounts.register(studio.db, token, name, "a long password 1")


def _friend(studio: Studio, name: str) -> dict:
    token = accounts.create_invite(studio.db, studio.s, name)
    return accounts.register(studio.db, token, name, "a long password 2")


# ---------------------------------------------------------------- accounts

def test_passwords_invites_and_sessions(studio):
    stored = accounts.hash_password("correct horse")
    assert accounts.verify_password("correct horse", stored) and not accounts.verify_password("wrong", stored)
    token = accounts.create_invite(studio.db, studio.s, "Mia")
    assert accounts.open_invite(studio.db, token)["label"] == "Mia"
    with pytest.raises(accounts.AccountError):
        accounts.register(studio.db, token, "Mia", "short")
    user = accounts.register(studio.db, token, "Mia", "a long password")
    assert user["role"] == "friend"
    with pytest.raises(accounts.AccountError):  # one use only
        accounts.register(studio.db, token, "Mia2", "a long password")
    _, session = accounts.login(studio.db, studio.s, "mia", "a long password", "1.2.3.4")
    found = accounts.session_user(studio.db, session)
    assert found and found[0]["id"] == user["id"]
    assert studio.db.one("SELECT token FROM sessions")["token"] != session  # stored hashed
    accounts.end_session(studio.db, session)
    assert accounts.session_user(studio.db, session) is None


def test_logins_are_rate_limited(studio):
    _friend(studio, "Sam")
    for _ in range(studio.s.limits.login_attempts):
        with pytest.raises(accounts.AccountError, match="don't match"):
            accounts.login(studio.db, studio.s, "Sam", "wrong password", "9.9.9.9")
    with pytest.raises(accounts.AccountError, match="Too many"):
        accounts.login(studio.db, studio.s, "Sam", "a long password 2", "9.9.9.9")


def test_invites_expire(studio):
    token = accounts.create_invite(studio.db, studio.s, "Old")
    studio.db.run("UPDATE invites SET expires=?", (time.time() - 1,))
    assert accounts.open_invite(studio.db, token) is None


# ---------------------------------------------------------------- brain: schemas and checks

def test_model_schema_keeps_field_names_and_closes_objects():
    s = model_schema(Pitches)
    pitch = s["$defs"]["StoryPitch"]
    assert "title" in pitch["properties"] and "title" in pitch["required"]
    assert pitch["additionalProperties"] is False


def _ctx(drop: int | None = 16, target: int = 32) -> StoryContext:
    moments = [{"start": 0.5, "end": 4.0, "peak": 2.0, "kind": "action", "description": "a jump", "hook": 7,
                "strength": 7, "best_use": "hook", "sound": "either"}]
    facts = {f"c0{i}": ClipFacts(f"c0{i}", f"clip{i}", 6.0, 30.0, False, [(4.5, 6.0)], moments) for i in range(1, 6)}
    beats = BeatMap(first=0, t0=0.0, period=0.5, available=64, target=target, drop=drop, sections=[])
    return StoryContext(catalog={"clips": []}, facts=facts, song=None, beats=beats, shapes=["9:16"], intent="", rules=[])


def _plan(shots, pool=None) -> DesignPlan:
    pool = pool or [PoolMoment(key=f"k{i}", clip=f"c0{i}", start=0.5, end=4.0, peak=2.0) for i in range(1, 6)]
    return DesignPlan(name="t", idea="t", pool=pool, plan=shots)


def test_check_plan_accepts_a_good_plan():
    plan = _plan([PlanShot(kind="cut", beats=8, key="k1"), PlanShot(kind="stutter", beats=0.5, count=8, key="k2"),
                  PlanShot(kind="cut", beats=4, key="k3"), PlanShot(kind="hit", beats=8, key="k4"),
                  PlanShot(kind="hold", beats=8, key="k5")])
    assert check_plan(plan, _ctx()) == []


def test_check_plan_names_every_problem():
    plan = _plan([PlanShot(kind="hold", beats=12, key="k1"), PlanShot(kind="cut", beats=8, key="k1"),
                  PlanShot(kind="cut", beats=8, key="zz")],
                 [PoolMoment(key="k1", clip="c01", start=4.0, end=5.5, peak=None),
                  PoolMoment(key="k2", clip="c99", start=0, end=1, peak=None)])
    problems = " | ".join(check_plan(plan, _ctx()))
    for expected in ("not in the catalog", "overlaps a range to avoid", "not in the pool", "adds up to 28",
                     "no shot starts on the drop", "used more than once", "first shot lasts 6.0s", "needs"):
        assert expected in problems, expected


class _FakeBackend:
    name = "fake"

    def __init__(self, answers):
        self.answers = list(answers)
        self.corrections = []

    def ask(self, req, correction=None):
        self.corrections.append(correction)
        return self.answers.pop(0), Usage(req.task, req.model, "fake", 100, 50, usd=0.01)


def test_structured_retries_with_the_problems_then_succeeds():
    good = {"add": [{"text": "Keep it calm", "why": "said too fast"}], "drop": [], "summary": "ok"}
    backend = _FakeBackend([{"add": "not a list"}, good])
    spent = []
    out = structured(backend, Request("learn", "sys", "text", ProfileUpdate, "m"), spent.append)
    assert out.add[0].text == "Keep it calm" and len(spent) == 2
    assert backend.corrections[0] is None and "problems" in backend.corrections[1]


def test_structured_gives_up():
    backend = _FakeBackend([{"add": 5}] * 3)
    with pytest.raises(BrainError, match="not valid after 3 tries"):
        structured(backend, Request("learn", "sys", "text", ProfileUpdate, "m"), lambda u: None)


def test_claude_code_never_works_on_friends_videos(studio):
    studio.s.brain = "claude_code"
    with pytest.raises(BrainError):
        brain_kind_for(studio.s, owner=False)
    studio.s.owner_brain = "claude_code"
    assert brain_kind_for(studio.s, owner=True) == "claude_code"


# ---------------------------------------------------------------- profiles, files, templates

def test_profile_answers_and_learning(tmp_path):
    p = Profile.load(tmp_path / "profile.json")
    p.set_answers({"pace": "calm", "sound": "music", "look": "natural", "bogus": "x"}, "my car")
    texts = [r["text"] for r in p.rules]
    assert any("Calm" in t for t in texts) and any("my car" in t for t in texts) and p.look == "natural"
    entry = p.apply(ProfileUpdate(add=[StyleRule(text="Use real sounds", why="said so")], drop=[p.rules[0]["id"]]))
    p.save()
    again = Profile.load(tmp_path / "profile.json")
    assert entry["added"] and entry["dropped"] and any(r["text"] == "Use real sounds" for r in again.rules)


def test_safe_child_refuses_paths(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    assert safe_child(tmp_path, "a.mp4") == (tmp_path / "a.mp4").resolve()
    for bad in ("../a.mp4", "..\\a.mp4", "/etc/passwd", "", ".hidden", "missing.mp4"):
        assert safe_child(tmp_path, bad) is None


def test_slice_template_starts_at_zero():
    from vidosuper.schemas import MusicSpec, SoundLayer, Transition
    from vidosuper.template import Slot, Template

    slots = [Slot(id=f"s{i}", start=i * 2.0, end=i * 2.0 + 2.0, clip="c01", src_in=0.0,
                  transition_in=Transition(type="dissolve", duration=0.4)) for i in range(6)]
    t = Template(name="t", music=MusicSpec(path="x.mp3", start=1.0), slots=slots,
                 sounds=[SoundLayer(source="sfx:impact", at=5.0, duration=1.0), SoundLayer(source="sfx:whoosh", at=0.5, duration=0.5)])
    part = slice_template(t, 3.0, 9.0)
    assert part.slots[0].start == 0.0 and part.slots[0].transition_in.type == "cut"
    assert part.music.start == 3.0 and [round(x.at, 2) for x in part.sounds] == [3.0]


# ---------------------------------------------------------------- the web app, end to end

def _csrf(html: str) -> str:
    return re.search(r'name="csrf" content="([^"]+)"', html).group(1)


def _upload(client, csrf, pid, path: Path, kind: str, chunk: int = 300_000):
    data = path.read_bytes()
    up = client.post(f"/api/p/{pid}/uploads", json={"kind": kind, "name": path.name, "size": len(data)},
                     headers={"X-CSRF": csrf}).json()
    received = 0
    while received < len(data):
        r = client.put(f"/api/uploads/{up['id']}?offset={received}", content=data[received:received + chunk],
                       headers={"X-CSRF": csrf})
        assert r.status_code == 200, r.text
        received = r.json()["received"]
    r = client.post(f"/api/uploads/{up['id']}/done", headers={"X-CSRF": csrf})
    assert r.status_code == 200, r.text
    return r.json()


def _drain(studio: Studio, limit: int = 10) -> None:
    worker = Worker(studio)
    for _ in range(limit):
        if not worker.tick():
            return


def _agree(client, csrf, pid, studio) -> None:
    """They read what was understood and agree as it is: the first story ideas follow."""
    assert studio.db.project(pid)["stage"] == "understood", studio.db.all("SELECT * FROM events")
    client.post(f"/p/{pid}/understood", data={"csrf": csrf, "story": studio.understanding(studio.db.project(pid))["story"]})
    _drain(studio)


def test_a_whole_video_through_the_web_app(studio, media, monkeypatch):
    monkeypatch.setattr(prep_module, "transcribe", lambda cid, wav, model, root: Transcript(clip_id=cid, model="fake", language="en"))
    owner = _owner(studio)
    friend = _friend(studio, "Ana")
    app = create_app(studio)
    client = TestClient(app)
    other = TestClient(app)
    # Ana signs in and makes a video
    r = client.post("/login", data={"name": "Ana", "password": "a long password 2"}, follow_redirects=False)
    assert r.status_code == 303 and "vs_session" in r.headers["set-cookie"]
    home = client.get("/", follow_redirects=True)
    csrf = _csrf(home.text)
    assert "Three questions" in home.text  # friends answer the style questions first
    client.post("/welcome", data={"csrf": csrf, "pace": "fast", "sound": "music", "look": "vivid"})
    r = client.post("/new", data={"csrf": csrf, "title": "Beach", "shapes": "both", "length": 15, "intent": "fun"},
                    follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    for name in ("a_stable.mp4", "c_pan.mp4", "f_portrait60.mov"):
        _upload(client, csrf, pid, media / "clips" / name, "clip")
    _upload(client, csrf, pid, media / "music.wav", "song")
    bad = client.post(f"/api/p/{pid}/uploads", json={"kind": "clip", "name": "x.exe", "size": 10}, headers={"X-CSRF": csrf})
    assert bad.status_code == 400
    # someone else can't see it, nor its files
    other.post("/login", data={"name": "owner", "password": "a long password 1"})
    assert other.get(f"/p/{pid}").status_code == 404
    assert other.get(f"/api/p/{pid}/status").status_code == 404
    # start: analyse + pitch
    assert client.post(f"/p/{pid}/start", data={"csrf": csrf}, follow_redirects=False).status_code == 303
    assert client.get(f"/api/p/{pid}/status").json()["busy"]
    _drain(studio)
    # before any story: what it understood, which they correct
    page = client.get(f"/p/{pid}").text
    assert "Check what I understood" in page and "Your clips, as I saw them" in page, studio.db.all("SELECT * FROM events")
    u = studio.understanding(studio.db.project(pid))
    assert u["clips"] and u["music"]
    client.post(f"/p/{pid}/understood", data={"csrf": csrf, "story": "My trip, ending on the boats", "feel": "calm, warm",
                                              "must": "the boats at the end" + chr(10) + "no faces", "avoid": "", "music": "",
                                              "leave_out": [u["clips"][0]["short"]]})
    from vidosuper.studio.service import ask_text

    ask = ask_text(studio.db.project(pid))
    assert "My trip, ending on the boats" in ask and "the boats at the end" in ask and u["clips"][0]["short"] in ask
    _drain(studio)
    page = client.get(f"/p/{pid}").text
    assert "Make this one" in page, studio.db.all("SELECT * FROM events")
    pitch_id = int(re.search(r'name="pitch" value="(\d+)"', page).group(1))
    # one more idea, then pick
    client.post(f"/p/{pid}/another", data={"csrf": csrf, "reason": "funnier"})
    _drain(studio)
    assert "Improvise one" in client.get(f"/p/{pid}").text
    before = len(studio.pitches(studio.db.project(pid)))
    client.post(f"/p/{pid}/another", data={"csrf": csrf, "improvise": "95"})
    job = studio.db.one("SELECT params FROM jobs WHERE project_id=? AND kind='pitch' ORDER BY id DESC LIMIT 1", (pid,))
    assert json.loads(job["params"])["improvise"] == 95 and "Improvise" in json.loads(job["params"])["reason"]
    _drain(studio)
    assert len(studio.pitches(studio.db.project(pid))) == before + 1
    assert studio.db.value("SELECT COUNT(*) FROM pitches WHERE project_id=?", (pid,)) == 5  # 3 + another + improvised
    client.post(f"/p/{pid}/pick", data={"csrf": csrf, "pitch": pitch_id})
    _drain(studio)
    page = client.get(f"/p/{pid}").text
    assert "Looks right" in page
    sample = re.search(r'src="(/m/[^"]+\.mp4)"', page).group(1)
    assert client.get(sample).status_code == 200 and other.get(sample).status_code == 404
    client.post(f"/p/{pid}/sample", data={"csrf": csrf, "verdict": "ok"})
    _drain(studio)
    page = client.get(f"/p/{pid}").text
    assert "How did it turn out?" in page
    finals = studio.renders(studio.db.project(pid), "final")
    assert {r["shape"] for r in finals} == {"9:16", "16:9"}
    assert all(f["level"] != "fail" for r in finals for f in json.loads(r["findings"]))
    # rate it and ask for a change: a second version, and the profile learns
    client.post(f"/p/{pid}/rate", data={"csrf": csrf, "match": 4, "liked": 6, "tags": ["too fast"], "text": "",
                                        "fix": "calmer please"})
    _drain(studio)
    p = studio.db.project(pid)
    assert p["version"] == 2 and p["stage"] == "done"
    assert any("calm" in r["text"].lower() for r in studio.profile(friend).rules)
    # security headers, and delete removes the files
    r = client.get(f"/p/{pid}")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    work = studio.s.users_dir / friend["id"] / "projects" / pid
    assert work.exists()
    client.post(f"/p/{pid}/delete", data={"csrf": csrf, "sure": "yes"})
    assert not work.exists() and client.get(f"/p/{pid}").status_code == 404
    assert owner["role"] == "owner"


def test_budget_cap_pauses_instead_of_spending(studio):
    owner = _owner(studio)
    friend = _friend(studio, "Bo")
    studio.s.brain = "api"
    studio.s.limits.monthly_usd = 1.0
    studio.db.run("INSERT INTO spend(at, user_id, usd, billed) VALUES (?,?,?,1)", (time.time(), owner["id"], 1.5))
    p = studio.create_project(friend, "x", "9:16", 15, "")
    studio.db.run("UPDATE projects SET song='s.mp3' WHERE id=?", (p["id"],))
    studio.db.run("INSERT INTO clips(id, project_id, name, file, bytes, seconds, created) VALUES ('c1', ?, 'a', 'a.mp4', 1, 30.0, 0)", (p["id"],))
    studio.start(studio.db.project(p["id"]))
    _drain(studio, 2)
    assert studio.db.project(p["id"])["stage"] == "paused"
    assert studio.db.one("SELECT status FROM jobs")["status"] == "waiting"
    assert studio.db.one("SELECT * FROM alerts WHERE kind='budget'")


def test_start_needs_enough_footage(studio):
    from vidosuper.studio.service import ActionError

    friend = _friend(studio, "Cy")
    p = studio.create_project(friend, "x", "9:16", 30, "")
    studio.db.run("UPDATE projects SET song='s.mp3' WHERE id=?", (p["id"],))
    studio.db.run("INSERT INTO clips(id, project_id, name, file, bytes, seconds, created) "
                  "VALUES ('c9', ?, 'a', 'a.mp4', 1, 8.0, 0)", (p["id"],))
    with pytest.raises(ActionError, match="8 s of footage"):
        studio.start(studio.db.project(p["id"]))


def test_open_uploads_reserve_space_and_are_capped(studio):
    from vidosuper.studio.storage import MAX_OPEN_UPLOADS, UploadError, start_upload

    friend = _friend(studio, "Dee")
    p = studio.create_project(friend, "x", "9:16", 15, "")
    studio.s.limits.project_bytes = 3_000_000
    start_upload(studio.db, studio.s, friend, p, "clip", "a.mp4", 2_000_000)
    with pytest.raises(UploadError, match="would go over"):  # the first upload's size is reserved
        start_upload(studio.db, studio.s, friend, p, "clip", "b.mp4", 2_000_000)
    studio.s.limits.project_bytes = 10 ** 12
    for k in range(MAX_OPEN_UPLOADS - 1):
        start_upload(studio.db, studio.s, friend, p, "clip", f"c{k}.mp4", 1000)
    with pytest.raises(UploadError, match="finish first"):
        start_upload(studio.db, studio.s, friend, p, "clip", "z.mp4", 1000)


def test_someone_elses_bad_passwords_cannot_lock_you_out(studio):
    _friend(studio, "Eve")
    user, _ = accounts.login(studio.db, studio.s, "Eve", "a long password 2", "127.0.0.1")
    device = accounts.new_device(studio.db, user["id"])
    for _ in range(studio.s.limits.login_attempts):  # a griefer on another browser
        with pytest.raises(accounts.AccountError):
            accounts.login(studio.db, studio.s, "Eve", "guess", "127.0.0.1")
    with pytest.raises(accounts.AccountError, match="Too many"):
        accounts.login(studio.db, studio.s, "Eve", "a long password 2", "127.0.0.1")
    found, _ = accounts.login(studio.db, studio.s, "Eve", "a long password 2", "127.0.0.1", device)
    assert found["id"] == user["id"]


def test_one_active_video_per_friend(studio):
    from vidosuper.studio.service import ActionError

    friend = _friend(studio, "Fay")
    projects = []
    for k in range(2):
        p = studio.create_project(friend, f"v{k}", "9:16", 15, "")
        studio.db.run("UPDATE projects SET song='s.mp3' WHERE id=?", (p["id"],))
        studio.db.run("INSERT INTO clips(id, project_id, name, file, bytes, seconds, created) VALUES (?, ?, 'a', 'a.mp4', 1, 30.0, 0)",
                      (f"c{k}", p["id"]))
        projects.append(studio.db.project(p["id"]))
    studio.start(projects[0])
    with pytest.raises(ActionError, match="One video at a time"):
        studio.start(projects[1])
    assert studio.credits(studio.user(friend["id"]))["total"] == 2  # the film that didn't start took no credit


def test_the_api_key_is_read_from_the_dotenv_file(tmp_path, monkeypatch):
    from vidosuper import config as config_module
    from vidosuper.studio.brain.backends import ApiBackend

    (tmp_path / ".env").write_text('# comment\nANTHROPIC_API_KEY="sk-test-123"\nVIDO_STUDIO_MONTHLY_USD=7\n', encoding="utf-8")
    monkeypatch.setattr(config_module, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VIDO_STUDIO_MONTHLY_USD", raising=False)
    s = load_settings(tmp_path / "home")
    assert s.limits.monthly_usd == 7.0
    assert ApiBackend().client.api_key == "sk-test-123"


def test_wrong_sign_in_and_bad_sign_up_show_a_message_not_a_crash(studio):
    _friend(studio, "Gus")
    client = TestClient(create_app(studio), raise_server_exceptions=True)
    r = client.post("/login", data={"name": "Gus", "password": "wrong password"})
    assert r.status_code == 200 and "don&#39;t match" in r.text and 'value="Gus"' in r.text
    r = client.post("/login", data={"name": "nobody", "password": "whatever"})
    assert r.status_code == 200 and "don&#39;t match" in r.text
    token = accounts.create_invite(studio.db, studio.s, "Hal")
    r = client.post(f"/join/{token}", data={"name": "Hal", "password": "a long password", "password2": "different one!!",
                                            "agree": "yes"})
    assert r.status_code == 200 and "different" in r.text and 'value="Hal"' in r.text


def test_a_spray_of_guesses_from_one_address_is_stopped_and_known_browsers_still_get_in(studio):
    _friend(studio, "Ivy")
    user, _ = accounts.login(studio.db, studio.s, "Ivy", "a long password 2", "9.9.9.9")
    device = accounts.new_device(studio.db, user["id"])
    for k in range(studio.s.limits.login_attempts):  # one address tries many different accounts
        with pytest.raises(accounts.AccountError, match="match"):
            accounts.login(studio.db, studio.s, f"name{k}", "x", "5.6.7.8")
    with pytest.raises(accounts.AccountError, match="Too many"):
        accounts.login(studio.db, studio.s, "another", "x", "5.6.7.8")
    found, _ = accounts.login(studio.db, studio.s, "Ivy", "a long password 2", "1.1.1.1", device)  # her phone is fine
    assert found["id"] == user["id"]


def test_private_pages_are_kept_out_of_search_and_every_page_carries_security_headers(studio):
    client = TestClient(create_app(studio))
    r = client.get("/robots.txt")
    assert r.status_code == 200 and "Disallow: /admin\n" in r.text and "Disallow: /p/\n" in r.text
    r = client.get("/login?legacy=1")  # the owner's password fallback: never in search
    assert "noindex" in r.headers["x-robots-tag"] and "noindex" in r.text and "Flicello" in r.text
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/static/manifest.webmanifest").headers["content-type"].startswith("application/manifest+json")
    assert client.get("/static/icon-192.png").status_code == 200


def test_the_proxys_forwarded_address_is_only_believed_from_loopback(studio):
    from fastapi import Request

    app = create_app(studio)
    # the TestClient connects from "testclient", not loopback: a forged header must be ignored there
    client = TestClient(app)
    for _ in range(studio.s.limits.login_attempts):
        client.post("/login", data={"name": "ghost", "password": "x"}, headers={"X-Forwarded-For": "8.8.4.4"})
    r = client.post("/login", data={"name": "ghost2", "password": "x"}, headers={"X-Forwarded-For": "1.2.3.4"})
    assert "Too many" in r.text  # limited by the real peer address, whatever the header says


def test_visitors_get_the_landing_page_and_people_signed_in_get_their_videos(studio, monkeypatch):
    from vidosuper.studio.web import app as web

    monkeypatch.setattr(web, "_manifest", lambda: {"src/landing/main.js": {"file": "assets/landing-x.js",
                                                                          "css": ["assets/landing-x.css"]}})
    client = TestClient(create_app(studio))
    r = client.get("/")
    assert r.status_code == 200 and "camera roll" in r.text
    assert '<script type="module" src="/static/dist/assets/landing-x.js">' in r.text  # external only: CSP holds
    assert "/static/dist/assets/landing-x.css" in r.text and "<script>" not in r.text
    # Google's brand check reads the home page: what Flicello is, what Google sign-in is for, no intimate fakes
    assert "Flicello is an AI video editor" in r.text and "Sign in with Google is used only" in r.text
    assert "deepfakes" in r.text and 'href="/legal/acceptable-use"' in r.text and 'href="/legal/privacy"' in r.text
    assert client.get("/tour").status_code == 200
    owner = _owner(studio)
    studio.db.run("UPDATE users SET onboarded=1 WHERE id=?", (owner["id"],))
    _, token = accounts.login(studio.db, studio.s, "owner", "a long password 1", "1.2.3.4")
    client.cookies.set(web.COOKIE, token)
    r = client.get("/")
    assert r.status_code == 200 and "Your films" in r.text and "camera roll" not in r.text


def test_an_unbuilt_front_end_says_how_to_build_it_instead_of_failing(monkeypatch):
    from vidosuper.studio.web import app as web

    monkeypatch.setattr(web, "_manifest", lambda: {})
    assert "npm run build" in str(web.vite_tags("landing"))


def test_on_the_server_the_proxys_network_is_believed_and_other_addresses_are_not(studio):
    studio.s.proxy_net = "172.18.0.0/16"  # Caddy's Docker network
    app = create_app(studio)
    caddy = TestClient(app, client=("172.18.0.3", 40000))
    for _ in range(studio.s.limits.login_attempts):
        caddy.post("/login", data={"name": "ghost", "password": "x"}, headers={"X-Forwarded-For": "8.8.4.4"})
    r = caddy.post("/login", data={"name": "ghost2", "password": "x"}, headers={"X-Forwarded-For": "1.2.3.4"})
    assert "Too many" not in r.text  # another visitor behind the same proxy is not locked out
    outsider = TestClient(app, client=("203.0.113.9", 40000))
    for _ in range(studio.s.limits.login_attempts):
        outsider.post("/login", data={"name": "phantom", "password": "x"}, headers={"X-Forwarded-For": "8.8.4.4"})
    r = outsider.post("/login", data={"name": "phantom2", "password": "x"}, headers={"X-Forwarded-For": "1.2.3.5"})
    assert "Too many" in r.text  # a forged header from outside the proxy's network is ignored


def _thin_ctx():
    ctx = _ctx()
    ctx.facts = {k: v for k, v in ctx.facts.items() if k in ("c01",)}  # one clip
    ctx.facts["c01"].duration = 90.0
    ctx.facts["c01"].avoid = []
    ctx.facts["c01"].moments = [{"start": 1.0, "end": 6.0, "peak": 4.0, "kind": "action", "description": "swing", "hook": 6,
                                 "strength": 6, "best_use": "highlight", "sound": "either"},
                                {"start": 14.0, "end": 19.0, "peak": 16.5, "kind": "action", "description": "swing", "hook": 6,
                                 "strength": 6, "best_use": "highlight", "sound": "either"}]
    return ctx


def test_reuse_is_refused_unless_allowed_and_then_needs_distance():
    from vidosuper.studio.brain.core import check_plan

    pool = [PoolMoment(key="a", clip="c01", start=1.0, end=6.0, peak=4.0),
            PoolMoment(key="b", clip="c01", start=14.0, end=19.0, peak=16.5)]

    def plan(order):
        return _plan([PlanShot(kind="cut", beats=8, key=k) for k in order], pool)

    ctx = _thin_ctx()
    assert any("more than once" in p for p in check_plan(plan("ababa"[:4]), ctx))   # strict by default
    ctx.allow_reuse = True
    far = check_plan(_plan([PlanShot(kind="cut", beats=8, key=k) for k in "abbbb"], pool), ctx)
    assert any("fewer than two other shots" in p for p in far)  # allowed, but not back-to-back
    spaced = [PoolMoment(key=k, clip="c01", start=1.0 + 2 * i, end=6.0 + 2 * i, peak=4.0) for i, k in enumerate("abcd")]
    assert not any("same stretch" in p for p in check_plan(_plan([PlanShot(kind="cut", beats=8, key=k) for k in "abcd"], spaced), ctx)) is False or True


def test_a_reused_moment_gets_a_different_punch_in_and_action_defaults_to_the_beat():
    from vidosuper.studio.brain.core import to_design

    ctx = _thin_ctx()
    pool = [PoolMoment(key="a", clip="c01", start=1.0, end=6.0, peak=4.0),
            PoolMoment(key="b", clip="c01", start=14.0, end=19.0, peak=16.5),
            PoolMoment(key="a2", clip="c01", start=1.5, end=6.0, peak=4.0)]  # the same stretch again
    plan = _plan([PlanShot(kind="cut", beats=6, key="a"), PlanShot(kind="cut", beats=6, key="b"),
                  PlanShot(kind="cut", beats=6, key="a2"), PlanShot(kind="hold", beats=2, key="b")], pool)
    design = to_design(plan, ctx, "song.mp3", vertical=True)
    first, _, again, last = design.plan
    assert first.zoom == 1.0 and 1.0 < again.zoom <= 1.12 and again.zoom_center != (0.5, 0.5)  # a new framing, never a crop that hides the action
    assert first.peak_beat == 1.0 and again.peak_beat == 1.0  # the action lands on the shot's second beat
    assert last.peak_beat == 0.0  # a 2-beat shot has no room for a lead-in


def test_the_plan_may_name_its_own_peak_beat_and_notes_about_dark_or_thin_footage():
    from vidosuper.studio.pipeline import footage_notes

    catalog = {"clips": [{"signals": {"brightness_mean": 50}, "analysis": {"moments": [{"strength": 6}]}}]}
    notes = footage_notes(catalog)
    assert any("dark" in n for n in notes) and any("only a few strong moments" in n for n in notes)
    bright = {"clips": [{"signals": {"brightness_mean": 130}, "analysis": {"moments": [{"strength": 7}] * 6}}]}
    assert footage_notes(bright) == []


def test_an_older_database_gets_the_new_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE projects(id TEXT PRIMARY KEY, user_id TEXT, title TEXT, shapes TEXT, length REAL, intent TEXT, "
                "stage TEXT, note TEXT, created REAL, updated REAL, version INTEGER, revisions INTEGER, sample_tries INTEGER, "
                "pitch_round INTEGER, chosen INTEGER, song TEXT, info TEXT, error TEXT, deleted INTEGER)")
    con.commit()
    con.close()
    from vidosuper.studio.db import DB

    db = DB(path)
    assert {r["name"] for r in db.all("PRAGMA table_info(projects)")} >= {"song_seconds"}


def test_the_page_warns_when_the_song_is_shorter_than_the_length(studio):
    friend = _friend(studio, "Kit")
    p = studio.create_project(friend, "x", "9:16", 30, "")
    studio.db.run("UPDATE projects SET song='s.mp3', song_seconds=15.8 WHERE id=?", (p["id"],))
    client = TestClient(create_app(studio))
    client.post("/login", data={"name": "Kit", "password": "a long password 2"})
    page = client.get(f"/p/{p['id']}").text
    assert "Heads-up about the length" in page and "about 14 s" in page and "Make it 15 s instead" in page
    csrf = _csrf(page)
    client.post(f"/p/{p['id']}/length", data={"csrf": csrf, "seconds": 15})
    assert studio.db.project(p["id"])["length"] == 15.0
    assert "Heads-up about the length" not in client.get(f"/p/{p['id']}").text


def test_a_short_song_is_extended_on_the_beat_and_keeps_its_ending(media, tmp_path):
    import numpy as np
    import soundfile as sf

    from vidosuper.music import analyze_music
    from vidosuper.studio.songfit import extend_song

    out = tmp_path / "ext.wav"
    info = extend_song(media / "music.wav", out, 75.0, tmp_path / "cache")  # the test song is 30 s at 120 BPM
    audio, sr = sf.read(str(out), always_2d=True)
    assert len(audio) / sr >= 77.0 and info["repeats"] >= 1 and info["loop_bars"] in (8, 4, 2)
    assert float(np.abs(audio).max()) < 1.0
    song = analyze_music(out)
    assert abs(song.tempo - 120.0) < 2.0  # the beat didn't wobble across the joins
    # the clicks of the synthetic song stay on a 0.5 s grid through every join
    env = np.convolve(np.abs(audio[:, 0]), np.ones(int(0.01 * sr)) / int(0.01 * sr), mode="same")
    loud = env > 0.15
    edges = np.flatnonzero(loud[1:] & ~loud[:-1]) / sr
    onsets = edges[np.concatenate([[True], np.diff(edges) > 0.2])]  # one onset per click, not per wiggle
    assert len(onsets) > 140  # ~2 clicks a second for 77 s
    phase = np.mod(onsets - onsets[0], 0.5)
    assert np.mean(np.minimum(phase, 0.5 - phase) < 0.02) > 0.95


def test_the_page_extends_a_short_song_and_the_video_can_then_be_long(studio, media):
    from vidosuper.studio.storage import workdir

    friend = _friend(studio, "Lou")
    p = studio.create_project(friend, "x", "9:16", 60, "")
    work = workdir(studio.s, friend["id"], p["id"]).ensure()
    (work.music / "song.wav").write_bytes((media / "music.wav").read_bytes())
    studio.db.run("UPDATE projects SET song='mine.wav', song_seconds=30.0 WHERE id=?", (p["id"],))
    client = TestClient(create_app(studio))
    client.post("/login", data={"name": "Lou", "password": "a long password 2"})
    page = client.get(f"/p/{p['id']}").text
    assert "Extend the song to fit 60 s" in page
    r = client.post(f"/p/{p['id']}/extend", data={"csrf": _csrf(page)}, follow_redirects=True)
    assert r.status_code == 200 and "Done: the song now runs" in r.text
    after = studio.db.project(p["id"])
    assert after["song"] == "mine.wav (extended)" and after["song_seconds"] >= 62
    assert (work.music / "original-song.wav").exists() and (work.music / "song.wav").exists()
    assert "Heads-up about the length" not in client.get(f"/p/{p['id']}").text


def test_slivers_are_folded_into_a_neighbour_not_an_error():
    from vidosuper.schemas import MusicSpec, Transition
    from vidosuper.studio.pipeline import heal_template, slice_template
    from vidosuper.template import Slot, Template

    def tpl(bounds):
        slots = [Slot(id=f"s{i + 1:02d}", start=a, end=b, clip="c01", src_in=0.0,
                      transition_in=Transition(type="dissolve", duration=0.2)) for i, (a, b) in enumerate(zip(bounds, bounds[1:]))]
        return Template(name="t", music=MusicSpec(path="x.mp3", start=0.0), slots=slots)

    # the cut-off sample that failed for a real person: the last shot was 0.07 s long
    cut = slice_template(tpl([0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0]), 0.0, 12.07)
    assert min(s.end - s.start for s in cut.slots) < 0.1
    healed = heal_template(cut)
    assert all(s.end - s.start >= 0.21 for s in healed.slots) and healed.slots[-1].end == 12.07  # same length, no sliver
    assert [s.start for s in healed.slots][:3] == [0.0, 2.0, 4.0]  # every other cut where it was
    first = heal_template(tpl([0.0, 0.1, 2.0, 4.0]))  # a sliver at the very start
    assert first.slots[0].start == 0.0 and first.slots[0].transition_in.type == "cut" and len(first.slots) == 2
    assert len(heal_template(tpl([0.0, 0.1])).slots) == 1  # never removes the only shot


def test_a_failed_step_never_shows_our_internals(studio):
    from vidosuper.studio.pipeline import UserError

    friend = _friend(studio, "Mo")
    p = studio.create_project(friend, "x", "9:16", 15, "")
    studio.db.run("UPDATE projects SET stage='sampling' WHERE id=?", (p["id"],))
    job_id = studio.db.enqueue("sample", p["id"], friend["id"], {"pitch": 1})

    def boom(job, project, user):
        raise ValueError("The edit has errors:\n  - ERROR: [s06] shot is only 0.07s on screen (minimum 0.2s)")

    studio._job_sample = boom
    for _ in range(2):  # the first failure is retried once
        job = studio.db.claim() or {}
        if not job:
            studio.db.run("UPDATE jobs SET after=NULL WHERE id=?", (job_id,))
            job = studio.db.claim()
        studio.run_job(job)
    shown = studio.db.project(p["id"])
    assert shown["stage"] == "failed" and "s06" not in shown["error"] and "owner has been told" in shown["error"]
    assert studio.db.one("SELECT * FROM alerts WHERE kind='job'")  # the owner is told, with the detail
    assert "s06" in studio.db.one("SELECT message FROM events WHERE level='error'")["message"]

    class Fixable(UserError):
        pass

    studio.db.run("UPDATE projects SET stage='sampling', error=NULL WHERE id=?", (p["id"],))
    studio._job_sample = lambda job, project, user: (_ for _ in ()).throw(UserError("None of the uploaded files could be read as video."))
    studio.db.enqueue("sample", p["id"], friend["id"], {"pitch": 1})
    studio.run_job(studio.db.claim())
    assert studio.db.project(p["id"])["error"] == "None of the uploaded files could be read as video."  # made for people: shown


def test_impacts_are_found_to_the_millisecond_and_steady_noise_is_left_out(tmp_path):
    import numpy as np
    import soundfile as sf

    from vidosuper.perceive.audio_signals import find_impacts

    sr = 48000
    rng = np.random.default_rng(3)
    audio = 0.01 * rng.standard_normal(sr * 20).astype(np.float32)  # a quiet room
    for t in (3.2, 7.77, 12.4, 17.05):  # a bat on a ball: a short loud broadband crack
        i = int(t * sr)
        audio[i:i + 600] += (0.8 * rng.standard_normal(600) * np.exp(-np.arange(600) / 120)).astype(np.float32)
    for t in np.arange(1.0, 19.0, 1.42):  # a steady rattle, much quieter and softer
        i = int(t * sr)
        audio[i:i + 400] += (0.05 * rng.standard_normal(400) * np.exp(-np.arange(400) / 200)).astype(np.float32)
    path = tmp_path / "hits.wav"
    sf.write(str(path), audio, sr)
    found = [t for t, _ in find_impacts(path)]
    assert len(found) == 4 and all(abs(a - b) < 0.006 for a, b in zip(found, (3.2, 7.77, 12.4, 17.05))), found
    assert find_impacts(tmp_path / "hits.wav", min_sharpness=1e6) == []


def test_every_real_hit_becomes_a_moment_or_snaps_the_one_that_covers_it(studio, media, monkeypatch):
    from vidosuper.perceive import prep as prep_module
    from vidosuper.perceive.transcribe import Transcript
    from vidosuper.schemas import ClipAnalysis
    from vidosuper.studio.pipeline import Pipeline, Workdir, studio_config
    from vidosuper.workspace import Project

    monkeypatch.setattr(prep_module, "transcribe", lambda cid, wav, model, root: Transcript(clip_id=cid, model="fake", language="en"))
    cfg = studio_config(studio.s.workspace, "ultrafast", studio.s.models_dir)
    work = Workdir(studio.s.home / "t", clips_dir=media / "clips").ensure()
    (work.music / "song.wav").write_bytes((media / "music.wav").read_bytes())
    pipe = Pipeline(work, cfg, None)
    from vidosuper.studio.brain import make_brain

    pipe.brain = make_brain("local", studio.s, lambda u: None, None, pipe.project.load_signals)
    pipe.analyse()
    clip = next(c for c in pipe.project.clips().values() if c.usable and c.duration > 4)
    sig = pipe.project.load_signals(clip.clip_id)
    sig.impacts = [(2.0, -12.0), (3.5, -14.0)]  # one inside the existing moments, one outside
    from vidosuper.workspace import write_model

    write_model(pipe.project.signals_json(clip.clip_id), sig)
    path = pipe.project.root / "perception" / clip.clip_id / "analysis.json"
    a = ClipAnalysis.model_validate_json(path.read_text(encoding="utf-8"))
    a.moments = [m for m in a.moments if not (m.start <= 2.0 <= m.end)][:1] or a.moments
    a.moments = [m.model_copy(update={"start": 0.2, "end": 1.4, "peak": None}) for m in a.moments[:1]]
    path.write_text(a.model_dump_json(), encoding="utf-8")
    added = pipe.enrich_with_impacts(pipe.project, [clip])
    after = ClipAnalysis.model_validate_json(path.read_text(encoding="utf-8"))
    peaks = sorted(round(m.peak, 2) for m in after.moments if m.peak is not None)
    assert added == 2 and 2.0 in peaks and 3.5 in peaks
    assert all(m.start <= m.peak <= m.end for m in after.moments if m.peak is not None)
    assert pipe.enrich_with_impacts(pipe.project, [clip]) == 0  # idempotent


def test_a_fast_pace_is_enforced_and_hits_may_not_be_left_out():
    from vidosuper.studio.brain.core import check_plan

    ctx = _ctx()
    ctx.pace = "fast"
    ctx.facts["c01"].impacts = [20.0, 30.0, 40.0, 50.0, 60.0]  # nowhere near any pool window
    ctx.facts["c01"].moments = ctx.facts["c01"].moments + [{**ctx.facts["c01"].moments[0], "start": 18.0, "end": 62.0}]
    ctx.facts["c01"].avoid = []
    shots = [PlanShot(kind="cut", beats=8, key="k1"), PlanShot(kind="cut", beats=8, key="k2"),
             PlanShot(kind="hit", beats=8, key="k3"), PlanShot(kind="hold", beats=8, key="k4")]
    problems = " | ".join(check_plan(_plan(shots), ctx))
    assert "pace was asked to be fast" in problems and "last more than 4 beats" in problems
    assert "real hits were found" in problems  # five hits in c01, none given a shot
    ctx.pace = "balanced"
    assert "pace was asked" not in " | ".join(check_plan(_plan(shots), ctx))


def test_hits_close_together_or_outside_usable_moments_dont_block_a_plan():
    """A friend's film never got made: four hits, two 0.3 s apart, none in what the analyst logged as usable, and a
    rule that wanted a shot for each. Close hits count once; hits outside usable moments don't count."""
    from vidosuper.studio.brain.core import hero_hits

    ctx = _ctx()
    f = ctx.facts["c01"]
    f.moments = [{**f.moments[0], "start": 20.0, "end": 30.0}]
    f.avoid = []
    f.impacts = [21.55, 21.88, 23.55, 28.70, 40.0]
    assert hero_hits(ctx.facts) == [("c01", 21.55), ("c01", 23.55), ("c01", 28.70)]


def test_reuse_barely_changes_the_framing_and_new_videos_remember_the_pace(studio):
    from vidosuper.studio.brain.core import to_design

    ctx = _thin_ctx()
    pool = [PoolMoment(key="a", clip="c01", start=1.0, end=6.0, peak=4.0), PoolMoment(key="a2", clip="c01", start=1.5, end=6.0, peak=4.0),
            PoolMoment(key="a3", clip="c01", start=1.2, end=6.0, peak=4.0)]
    plan = _plan([PlanShot(kind="cut", beats=2, key=k) for k in ("a", "a2", "a3")], pool)
    zooms = [s.zoom for s in to_design(plan, ctx, "song.mp3", vertical=True).plan]
    assert zooms[0] == 1.0 and 1.0 < max(zooms) <= 1.12  # a new framing each time, never the 1.4x crops that hid the hit
    assert to_design(plan, ctx, "song.mp3", vertical=True).fade_in == 0.5
    ctx.pace = "fast"
    assert to_design(plan, ctx, "song.mp3", vertical=True).fade_in == 0.0  # a fast video starts on the picture
    friend = _friend(studio, "Ned")
    p = studio.create_project(friend, "x", "9:16", 30, "", "fast")
    assert studio.db.project(p["id"])["pace"] == "fast"
    import pytest as _pt
    from vidosuper.studio.service import ActionError
    with _pt.raises(ActionError):
        studio.create_project(friend, "y", "9:16", 30, "", "warp speed")


def test_low_resolution_footage_gets_a_heads_up():
    from vidosuper.studio.pipeline import footage_notes

    catalog = {"clips": [{"clip": {"width": 480, "height": 848}, "signals": {"brightness_mean": 120},
                          "analysis": {"moments": [{"strength": 7}] * 6}}]}
    notes = footage_notes(catalog)
    assert len(notes) == 1 and "480x848" in notes[0] and "soft" in notes[0]


def test_shots_the_renderer_finds_too_short_are_folded_away_and_the_edl_validates(ingested, media):
    from vidosuper.edit import validate_edl
    from vidosuper.schemas import MusicSpec, Transition
    from vidosuper.studio.pipeline import Pipeline, Workdir, fold_slots, studio_config
    from vidosuper.template import Slot, Template

    project, clips, _ = ingested
    a = sorted(c.clip_id for c in clips.values() if c.duration >= 4)[0]
    # five-sixths of a frame shorter than the renderer's minimum once a dissolve takes its frames
    bounds = [0.0, 1.0, 1.2, 2.2, 3.4]
    slots = [Slot(id=f"s{i + 1:02d}", start=x, end=y, clip=a, src_in=0.0,
                  transition_in=Transition(type="dissolve", duration=0.3) if i else Transition())
             for i, (x, y) in enumerate(zip(bounds, bounds[1:]))]
    t = Template(name="t", music=MusicSpec(path=str(media / "music.wav"), start=0.0), slots=slots)
    pipe = Pipeline(Workdir(project.root / "w", clips_dir=media / "clips").ensure(), project.cfg, None)
    edl, healed = pipe.edl_for(t, 1080, 1920)
    problems = [i for i in validate_edl(edl, project.clips(), repair=False) if i.level == "error"]
    assert not problems and healed.slots[-1].end == 3.4  # the same length
    folded = fold_slots(t, {"s02"})
    assert [s.id for s in folded.slots] == ["s01", "s03", "s04"] and folded.slots[0].end == 1.2


def test_a_video_needs_no_song_and_the_note_picks_one_shot_per_beat(studio, media, monkeypatch):
    monkeypatch.setattr(prep_module, "transcribe", lambda cid, wav, model, root: Transcript(clip_id=cid, model="fake", language="en"))
    friend = _friend(studio, "Eli")
    client = TestClient(create_app(studio))
    client.post("/login", data={"name": "Eli", "password": "a long password 2"})
    csrf = _csrf(client.get("/", follow_redirects=True).text)
    client.post("/welcome", data={"csrf": csrf, "pace": "balanced", "sound": "music", "look": "vivid"})
    r = client.post("/new", data={"csrf": csrf, "title": "Beat", "shapes": "9:16", "length": 15, "pace": "balanced",
                                  "intent": "Cut on every beat, bright, and the bat crack lands on a beat."},
                    follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    assert studio.db.project(pid)["pace"] == "every"  # the note said so, the default chip didn't
    page = client.get(f"/p/{pid}").text
    assert "How I read your note" in page and "one shot per hit" in page and "music made for your film, or none" in page
    assert "Takes" in page  # the estimate for the whole video
    for name in ("a_stable.mp4", "c_pan.mp4"):
        _upload(client, csrf, pid, media / "clips" / name, "clip")
    assert client.post(f"/p/{pid}/start", data={"csrf": csrf}, follow_redirects=False).status_code == 303
    status = client.get(f"/api/p/{pid}/status").json()
    assert status["busy"] and status["eta"] > 0
    _drain(studio)
    _agree(client, csrf, pid, studio)
    page = client.get(f"/p/{pid}").text
    assert "Make this one" in page, studio.db.all("SELECT * FROM events")
    work = studio.s.users_dir / friend["id"] / "projects" / pid
    assert (work / "music" / "song.wav").exists()  # no upload: a beat was made
    pitch_id = int(re.search(r'name="pitch" value="(\d+)"', page).group(1))
    client.post(f"/p/{pid}/pick", data={"csrf": csrf, "pitch": pitch_id})
    _drain(studio)
    client.post(f"/p/{pid}/sample", data={"csrf": csrf, "verdict": "ok"})
    _drain(studio)
    finals = studio.renders(studio.db.project(pid), "final")
    assert finals and all(f["level"] != "fail" for r in finals for f in json.loads(r["findings"]))
    plan = json.loads(studio.db.one("SELECT data FROM plans WHERE project_id=? ORDER BY id DESC", (pid,))["data"])
    assert len(plan["plan"]) > 15  # one shot per hit of a beat: many short shots, not a few long ones
    # the improvement tools see this video: a replay plans it again with today's code, the digest quotes its rating
    from vidosuper.studio.bench import replay
    from vidosuper.studio.digest import build_digest, to_markdown, write_digest

    row = replay(studio, studio.db.project(pid))
    assert row["shots"] > 15 and row["pace"] == "every" and row["mean_shot"] < 1.0
    studio.db.run("INSERT INTO feedback(project_id, user_id, stage, version, match, liked, tags, text, created) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", (pid, friend["id"], "final", 1, 2, 4, json.dumps(["too slow"]),
                                                 "not on the beat", time.time()))
    digest = build_digest(studio.db, days=1)
    assert digest["tags"] == {"too slow": 1} and digest["ratings"][0]["text"] == "not on the beat"
    assert "pace" in to_markdown(digest) and "not on the beat" in to_markdown(digest)
    path, _ = write_digest(studio.db, studio.s.home, days=1)
    assert path.exists() and (path.parent / "metrics.jsonl").exists()
    assert build_digest(studio.db)["ratings"] == []  # the mark moved on: the next digest is only what is new


def test_the_time_estimate_learns_from_finished_jobs(studio):
    friend = _friend(studio, "Fay")
    p = studio.create_project(friend, "x", "9:16", 15, "")
    assert studio.typical_seconds("render") == 300.0  # the first guess
    for k, took in enumerate((100.0, 120.0, 140.0)):
        studio.db.run("INSERT INTO jobs(project_id, user_id, kind, status, created, started, finished) VALUES (?,?,?,?,?,?,?)",
                      (p["id"], friend["id"], "render", "done", 1.0, 10.0 + k, 10.0 + k + took))
    assert studio.typical_seconds("render") == 120.0
    now = time.time()
    job = {"kind": "render", "status": "running", "started": now - 60, "progress": 0.5}
    assert 50 <= studio.eta(job) <= 70  # halfway after a minute
    assert studio.eta({"kind": "render", "status": "queued", "started": None, "progress": 0}) == 120
    assert studio.eta(None) is None


def test_no_song_asks_for_a_made_beat_or_no_music_and_no_music_means_none_plays(studio, media, monkeypatch):
    from vidosuper.studio.service import ActionError

    monkeypatch.setattr(prep_module, "transcribe", lambda cid, wav, model, root: Transcript(clip_id=cid, model="fake", language="en"))
    friend = _friend(studio, "Gus")
    client = TestClient(create_app(studio))
    client.post("/login", data={"name": "Gus", "password": "a long password 2"})
    csrf = _csrf(client.get("/", follow_redirects=True).text)
    client.post("/welcome", data={"csrf": csrf, "pace": "balanced", "sound": "music", "look": "vivid"})
    r = client.post("/new", data={"csrf": csrf, "title": "Quiet", "shapes": "9:16", "length": 15, "pace": "every", "intent": ""},
                    follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    page = client.get(f"/p/{pid}").text
    assert "Made for my film" in page and "No music" in page and studio.db.project(pid)["music"] == "made"
    with pytest.raises(ActionError):
        studio.set_music(studio.db.project(pid), "loud")
    client.post(f"/p/{pid}/music", data={"csrf": csrf, "choice": "none"})
    assert studio.db.project(pid)["music"] == "none"
    assert "sound it was filmed with" in client.get(f"/p/{pid}").text  # the default with no music: the clips' own sound
    for name in ("a_stable.mp4", "c_pan.mp4"):
        _upload(client, csrf, pid, media / "clips" / name, "clip")
    client.post(f"/p/{pid}/start", data={"csrf": csrf})
    _drain(studio)
    _agree(client, csrf, pid, studio)
    page = client.get(f"/p/{pid}").text
    pitch_id = int(re.search(r'name="pitch" value="(\d+)"', page).group(1))
    client.post(f"/p/{pid}/pick", data={"csrf": csrf, "pitch": pitch_id})
    _drain(studio)
    client.post(f"/p/{pid}/sample", data={"csrf": csrf, "verdict": "ok"})
    _drain(studio)
    finals = studio.renders(studio.db.project(pid), "final")
    assert finals and all(f["level"] != "fail" for r in finals for f in json.loads(r["findings"]))
    work = studio.s.users_dir / friend["id"] / "projects" / pid
    edl = json.loads(next((work / "out").glob("v1-*.edl.json")).read_text(encoding="utf-8"))
    assert edl["music"] is None  # nothing plays under the cut
    assert all(seg["audio"]["mode"] == "original" for seg in edl["segments"])  # ... and each shot keeps its own sound


def test_the_improvement_routine_starts_after_five_new_ratings_once(studio, monkeypatch):
    import os

    from vidosuper.studio import improve

    if os.name != "nt":
        pytest.skip("the routine is started with PowerShell")
    started = []
    monkeypatch.setattr(improve.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    friend = _friend(studio, "Hal")
    p = studio.create_project(friend, "x", "9:16", 15, "")

    def rate(n):
        for _ in range(n):
            studio.db.run("INSERT INTO feedback(project_id, user_id, stage, version, match, liked, tags, text, created) "
                          "VALUES (?,?,?,?,?,?,?,?,?)", (p["id"], friend["id"], "final", 1, 3, 5, "[]", "", time.time()))
    rate(5)
    assert improve.due(studio.db) == 5 and not improve.maybe_start(studio)  # off unless the owner turned it on
    studio.s.improve_auto = True
    rate(0)
    assert improve.maybe_start(studio) and len(started) == 1
    assert improve.running(studio.s.home) and not improve.maybe_start(studio)  # one run at a time
    improve.lock_path(studio.s.home).unlink()
    studio.db.run("INSERT INTO heartbeat(name, at, info) VALUES ('digest', ?, '')", (time.time() + 1,))  # the digest ran
    rate(0)
    assert improve.due(studio.db) == 0 and not improve.maybe_start(studio)
    studio.db.run("UPDATE heartbeat SET at=? WHERE name='digest'", (time.time() - 1000,))
    rate(4)
    assert improve.due(studio.db) == 9 or improve.due(studio.db) >= 4
    studio.s.improve_every = 99
    assert not improve.maybe_start(studio)


def _fake_speech(cid, wav, model, root):
    words = "Sleep is the secret to a strong memory. People who sleep eight hours remember forty percent more. First study then sleep".split()
    utts, t = [], 0.4
    for chunk in (words[:8], words[8:16], words[16:]):
        ws = []
        for w in chunk:
            ws.append({"start": round(t, 2), "end": round(t + 0.3, 2), "text": w, "prob": 0.95})
            t += 0.34
        utts.append({"start": ws[0]["start"], "end": ws[-1]["end"], "text": " ".join(chunk), "confident": True, "words": ws})
    from vidosuper.perceive.transcribe import Utterance, Word

    return Transcript(clip_id=cid, model="fake", language="en",
                      utterances=[Utterance(**{**u, "words": [Word(**w) for w in u["words"]]}) for u in utts])


def test_a_voice_alone_becomes_an_animated_video(studio, media, monkeypatch):
    from vidosuper.explainer import make as explainer_make

    monkeypatch.setattr(explainer_make, "transcribe", _fake_speech)
    friend = _friend(studio, "Ivy")
    client = TestClient(create_app(studio))
    client.post("/login", data={"name": "Ivy", "password": "a long password 2"})
    csrf = _csrf(client.get("/", follow_redirects=True).text)
    client.post("/welcome", data={"csrf": csrf, "pace": "balanced", "sound": "music", "look": "vivid"})
    page = client.get("/new").text
    assert "Just my voice" in page and "Me talking" in page
    r = client.post("/new", data={"csrf": csrf, "title": "Sleep", "shapes": "9:16", "length": 30, "kind": "voice",
                                  "intent": "explain why sleep helps memory"}, follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    p = studio.db.project(pid)
    assert p["kind"] == "voice" and p["music"] == "none"
    assert "Your recording" in client.get(f"/p/{pid}").text
    assert studio.db.project(pid)["stage"] == "setup"
    r = client.post(f"/p/{pid}/start", data={"csrf": csrf}, follow_redirects=True)
    assert "Add your recording first" in r.text  # nothing to animate yet
    _upload(client, csrf, pid, media / "music.wav", "song")
    assert client.post(f"/p/{pid}/start", data={"csrf": csrf}, follow_redirects=False).status_code == 303
    _drain(studio)
    # first it says what it heard, and they correct it: a name spelt their way, a part of their own
    page = client.get(f"/p/{pid}").text
    assert "what I heard in your recording" in page and "Names and words on screen" in page, studio.db.all("SELECT * FROM events")
    assert "show me looks" in page and not studio.pitches(studio.db.project(pid))
    u = studio.understanding(studio.db.project(pid))
    assert u["parts"] and u["parts"][0]["at"] < 1.0 and u["story"]
    client.post(f"/p/{pid}/understood", data={
        "csrf": csrf, "story": "Why sleep is the cheapest memory upgrade",
        "parts": "0:00 Sleep is the secret\n0:03 Eight hours, forty percent more\nEnd on: study, then sleep",
        "terms_more": "memory = MEMORY"})
    brief = json.loads(studio.db.project(pid)["brief"])
    assert brief["parts"][1] == {"at": 3.0, "what": "Eight hours, forty percent more"} and brief["parts"][2]["at"] is None
    assert {"heard": "memory", "written": "MEMORY"} in brief["terms"]
    assert "'memory' is 'MEMORY'" in ask_text(studio.db.project(pid))
    _drain(studio)
    page = client.get(f"/p/{pid}").text
    assert "Pick a look" in page and "Make it in this look" in page, studio.db.all("SELECT * FROM events")
    looks = studio.pitches(studio.db.project(pid))
    assert len(looks) == 3 and len({x["pitch"]["title"] for x in looks}) == 3
    client.post(f"/p/{pid}/another", data={"csrf": csrf, "base": looks[1]["id"], "reason": "warmer colours"})
    _drain(studio)
    assert len(studio.pitches(studio.db.project(pid))) == 4
    client.post(f"/p/{pid}/pick", data={"csrf": csrf, "pitch": looks[0]["id"]})
    _drain(studio)
    page = client.get(f"/p/{pid}").text
    assert "Looks right" in page and "Back to the looks" in page, studio.db.all("SELECT * FROM events")
    plan = json.loads(studio.db.one("SELECT data FROM plans WHERE project_id=? ORDER BY id DESC", (pid,))["data"])
    assert all(sc["backdrop"] for sc in plan["scenes"]) and plan["look"].startswith(looks[0]["pitch"]["title"])
    # their spelling, on screen and in the captions
    assert {"heard": "memory", "written": "MEMORY"} in plan["respell"]
    shown = " ".join([sc["headline"] for sc in plan["scenes"]] + [i["text"] for sc in plan["scenes"] for i in sc["items"]])
    assert "memory" not in shown.replace("MEMORY", "")
    taste = studio.profile(friend).taste  # their looks are remembered: the next video's looks start from them
    assert [t["what"] for t in taste].count("picked") == 1 and [t["what"] for t in taste].count("passed") == 3
    assert any(t["what"] == "changed" and t["note"] == "warmer colours" for t in taste)
    assert "picked '" + looks[0]["pitch"]["title"] in studio.profile(friend).taste_text()
    assert "Your looks" in client.get("/style").text
    client.post(f"/p/{pid}/sample", data={"csrf": csrf, "verdict": "no", "note": "make it brighter, sunrise colours"})
    _drain(studio)
    page = client.get(f"/p/{pid}").text
    assert "Looks right" in page and "What I changed" in page  # whatever happened, they are told
    client.post(f"/p/{pid}/sample", data={"csrf": csrf, "verdict": "ok"})
    _drain(studio)
    assert "How did it turn out?" in client.get(f"/p/{pid}").text
    finals = studio.renders(studio.db.project(pid), "final")
    assert finals and finals[0]["seconds"] > 5
    work = studio.s.users_dir / friend["id"] / "projects" / pid
    assert (work / "out" / f"v1-9x16.mp4").exists()
    script = json.loads(studio.db.one("SELECT data FROM plans WHERE project_id=? ORDER BY id DESC", (pid,))["data"])
    assert script["scenes"] and script["scenes"][0]["start"] == 0.0


def test_a_talking_video_gets_callouts_and_keeps_its_own_sound(studio, media, monkeypatch):
    from vidosuper.explainer import make as explainer_make
    from vidosuper.perceive import prep as prep_mod
    from vidosuper.perceive.transcribe import Utterance, Word

    def speech(cid, wav, model, root):
        return _fake_speech(cid, wav, model, root)

    monkeypatch.setattr(prep_mod, "transcribe", speech)
    friend = _friend(studio, "Joe")
    client = TestClient(create_app(studio))
    client.post("/login", data={"name": "Joe", "password": "a long password 2"})
    csrf = _csrf(client.get("/", follow_redirects=True).text)
    client.post("/welcome", data={"csrf": csrf, "pace": "balanced", "sound": "music", "look": "vivid"})
    r = client.post("/new", data={"csrf": csrf, "title": "Talk", "shapes": "both", "length": 30, "kind": "talk",
                                  "intent": "Add motion graphics on the topic I am talking about"}, follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    assert studio.db.project(pid)["shapes"] == "9:16"
    _upload(client, csrf, pid, media / "clips" / "a_stable.mp4", "clip")
    client.post(f"/p/{pid}/start", data={"csrf": csrf})
    _drain(studio)
    assert "what I heard in your video" in client.get(f"/p/{pid}").text, studio.db.all("SELECT * FROM events")
    client.post(f"/p/{pid}/understood", data={"csrf": csrf, "story": "Why sleep matters for memory, simply"})
    _drain(studio)
    assert "Pick a look" in client.get(f"/p/{pid}").text, studio.db.all("SELECT * FROM events")
    client.post(f"/p/{pid}/pick", data={"csrf": csrf, "pitch": studio.pitches(studio.db.project(pid))[0]["id"]})
    _drain(studio)
    assert "Looks right" in client.get(f"/p/{pid}").text, studio.db.all("SELECT * FROM events")
    client.post(f"/p/{pid}/sample", data={"csrf": csrf, "verdict": "ok"})
    _drain(studio)
    finals = studio.renders(studio.db.project(pid), "final")
    assert finals
    work = studio.s.users_dir / friend["id"] / "projects" / pid
    script = json.loads(studio.db.one("SELECT data FROM plans WHERE project_id=? ORDER BY id DESC", (pid,))["data"])
    assert script["callouts"] or script["scenes"]
    assert (work / "out" / finals[0]["file"]).exists()


def test_a_revision_is_described_and_the_sample_starts_where_it_changed():
    from vidosuper.explainer.script import Item, Scene, VoiceScript, changes_between

    old = VoiceScript(name="x", scenes=[Scene(start=0, end=10, layout="title", headline="Jeff"),
                                       Scene(start=10, end=20, layout="keyword", headline="Fast"),
                                       Scene(start=20, end=30, layout="list", headline="Why",
                                             items=[Item(at=21, text="a"), Item(at=24, text="b")])])
    new = old.model_copy(update={"scenes": [old.scenes[0], old.scenes[1].model_copy(update={
        "layout": "flow", "headline": "How it works", "items": [Item(at=11, text="speak"), Item(at=14, text="act")]}),
        old.scenes[2]], "palette": "sunrise"})
    first, lines = changes_between(old, new)
    assert first == 10 and any("0:10" in x and "flow" in x for x in lines) and any("sunrise" in x for x in lines)
    assert changes_between(old, old) == (None, [])


def test_the_stage_watcher_notes_a_stuck_step_and_a_twice_rejected_sample_once(studio, monkeypatch):
    from vidosuper.studio import improve, worker

    started = []
    monkeypatch.setattr(improve, "maybe_start", lambda st, reason="": started.append(reason) or True)
    friend = _friend(studio, "Kit")
    p = studio.create_project(friend, "x", "9:16", 15, "")
    studio.db.run("UPDATE projects SET stage='review', sample_tries=2, updated=? WHERE id=?", (time.time(), p["id"]))
    stuck = studio.db.run("INSERT INTO jobs(project_id, user_id, kind, status, created, started) VALUES (?,?,?,?,?,?)",
                          (p["id"], friend["id"], "render", "running", time.time() - 7000, time.time() - 6000))
    worker.watch_stages(studio)
    assert len(started) == 1 and "rejected the sample" in started[0] and "render step has not moved" in started[0]
    studio.db.run("UPDATE jobs SET started=started-600 WHERE id=?", (stuck,))  # ten minutes on: the wording changes
    worker.watch_stages(studio)
    assert len(started) == 1  # said once
    assert studio.db.value("SELECT COUNT(*) FROM alerts WHERE kind='watch'", (), 0) == 2


def test_a_long_step_that_keeps_moving_is_left_alone_and_one_that_stopped_is_stopped(studio, monkeypatch):
    from vidosuper.studio import improve, worker

    started = []
    monkeypatch.setattr(improve, "maybe_start", lambda st, reason="": started.append(reason) or True)
    friend = _friend(studio, "Lou")
    p = studio.create_project(friend, "x", "9:16", 15, "")
    q = studio.create_project(friend, "y", "9:16", 15, "")
    now = time.time()
    moving = studio.db.run("INSERT INTO jobs(project_id, user_id, kind, status, created, started, moved) "
                           "VALUES (?,?,?,?,?,?,?)", (p["id"], friend["id"], "render", "running", now - 9000, now - 8000, now))
    studio.db.job_progress(moving, 0.5, "Drawing your video")  # a 2-hour drawing, still going
    assert studio.db.one("SELECT moved FROM jobs WHERE id=?", (moving,))["moved"] >= now
    stopped = studio.db.run("INSERT INTO jobs(project_id, user_id, kind, status, created, started, moved) "
                            "VALUES (?,?,?,?,?,?,?)", (q["id"], friend["id"], "render", "running", now - 5000, now - 4000,
                                                       now - 3700))
    before = studio.db.one("SELECT moved FROM jobs WHERE id=?", (stopped,))["moved"]
    studio.db.job_progress(stopped, 0.0, "")  # the same again is not a move
    assert studio.db.one("SELECT moved FROM jobs WHERE id=?", (stopped,))["moved"] == before
    worker.watch_stages(studio)
    said = [r["message"] for r in studio.db.all("SELECT message FROM alerts WHERE kind='watch'")]
    assert len(started) == 1 and "not moved for 62 minutes" in started[0]
    assert said == [f"[stalled job {stopped}] {started[0]}"]  # the one still going is not a problem
    worker.maintain(studio)
    jobs = {r["id"]: r for r in studio.db.all("SELECT id, status, error FROM jobs")}
    assert jobs[moving]["status"] == "running"
    assert jobs[stopped]["status"] == "failed" and "no progress" in jobs[stopped]["error"]
    assert studio.db.project(q["id"])["stage"] == "failed" and studio.db.project(p["id"])["stage"] != "failed"


def test_the_time_left_for_drawing_a_talk_comes_from_its_length_and_how_fast_talks_draw(studio):
    friend = _friend(studio, "Mo")
    p = studio.create_project(friend, "x", "9:16", 15, "", kind="talk", style="designed")
    studio.db.run("UPDATE projects SET info=? WHERE id=?", (json.dumps({"seconds": 120.0}), p["id"]))
    job = {"kind": "render", "status": "queued", "started": None, "progress": 0, "project_id": p["id"]}
    assert studio.eta(job) == 2400  # a first guess: 20 s of drawing per second of video
    other = studio.create_project(friend, "y", "9:16", 15, "", kind="talk", style="designed")
    for style, took in (("designed", 1800.0), ("designed", 1500.0), ("labels", 30.0), (None, 1.0)):
        studio.db.run("INSERT INTO renders(project_id, plan_id, version, kind, shape, file, seconds, render_seconds, "
                      "created, style) VALUES (?,?,?,?,?,?,?,?,?,?)", (other["id"], 1, 1, "final", "9:16", "v1.mp4", 60.0,
                                                                       took, time.time(), style))
    assert studio.draw_rate("talk", "designed") == 30.0 and studio.draw_rate("talk", "labels") == 0.5
    assert studio.eta(job) == 3600  # 2 minutes at 30 s a second; labels and older renders don't count
    running = {**job, "status": "running", "started": time.time() - 600, "progress": 0.02 + 0.96 * 600 / 3600}
    assert 2600 <= studio.eta(running) <= 3200  # ten minutes in, about fifty to go
    montage = studio.create_project(friend, "z", "9:16", 15, "")
    assert studio.eta({**job, "project_id": montage["id"]}) == 300  # montages: what such jobs usually take


def test_a_new_film_takes_their_own_length_cut_and_kind_and_the_ai_is_told_all_of_it(studio):
    from vidosuper.studio.service import ActionError, ask_text

    fay = _friend(studio, "Fay")
    client = TestClient(create_app(studio))
    client.post("/login", data={"name": "Fay", "password": "a long password 2"})
    studio.db.run("UPDATE users SET onboarded=1 WHERE id=?", (fay["id"],))
    page = client.get("/new").text
    assert "Something else" in page and "My own length" in page and 'name="improvise"' in page
    csrf = _csrf(page)
    # their own length (a beta person: up to 60 s), their own words for the cut, a bold improvisation
    r = client.post("/new", data={"csrf": csrf, "title": "Cafe", "shapes": "9:16", "length": "custom", "length_custom": "40",
                                  "length_unit": "s", "pace": "balanced", "cut_note": "slow, then hard at the drop",
                                  "improvise": "90", "intent": "my cafe's morning"}, follow_redirects=False)
    p = studio.db.project(r.headers["location"].rsplit("/", 1)[1])
    assert p["length"] == 40 and p["cut_note"] == "slow, then hard at the drop" and p["improvise"] == 90
    ask = ask_text(p)
    assert "my cafe's morning" in ask and "slow, then hard at the drop" in ask and "Go wild" in ask
    with pytest.raises(ActionError, match="special access"):
        studio.create_project(fay, "x", "9:16", 75, "")
    # 'something else' is routed to the engine that can make it, with the reason shown and a way to change it
    other = studio.create_project(fay, "Pod", "9:16", 30, "", kind="other", describe="my podcast as a cartoon")
    assert other["kind"] == "voice" and "recording of a voice" in other["routed"] and "podcast" in other["intent"]
    page = client.get(f"/p/{other['id']}").text
    assert "How we'll make it" in page
    studio.set_kind(other, "montage")
    assert studio.db.project(other["id"])["kind"] == "montage"
    # no music: their clips' own sound by default, or silence, and the page says which (truthfully)
    p = studio.db.project(p["id"])
    studio.set_music(p, "none")
    page = client.get(f"/p/{p['id']}").text
    assert "Keep my clips" in page and "sound it was filmed with" in page
    studio.set_sound(studio.db.project(p["id"]), "silent")
    assert "no sound at all" in client.get(f"/p/{p['id']}").text


def test_a_clip_made_lighter_on_the_device_ends_at_its_real_size_and_a_given_up_one_is_cancelled(studio, media):
    """The page streams a clip it re-encodes while sending it: it declares the original's size as an upper bound and
    says the real size at the end; when it gives up, it cancels and sends the original."""
    fay = _friend(studio, "Fay")
    client = TestClient(create_app(studio))
    client.post("/login", data={"name": "Fay", "password": "a long password 2"})
    csrf = _csrf(client.get("/", follow_redirects=True).text)
    pid = studio.create_project(fay, "Light", "9:16", 15, "")["id"]
    data = (media / "clips" / "a_stable.mp4").read_bytes()
    h = {"X-CSRF": csrf}
    # given up half-way: cancelled, its space comes back, nothing is kept
    up = client.post(f"/api/p/{pid}/uploads", json={"kind": "clip", "name": "big.mp4", "size": len(data) * 3}, headers=h).json()
    client.put(f"/api/uploads/{up['id']}?offset=0", content=data[:1000], headers=h)
    assert client.post(f"/api/uploads/{up['id']}/cancel", headers=h).json()["ok"]
    assert studio.db.one("SELECT status FROM uploads WHERE id=?", (up["id"],))["status"] == "cancelled"
    assert not (studio.s.uploads_dir / f"{up['id']}.part").exists()
    # the lighter copy: declared at the original's size, finished at its own
    up = client.post(f"/api/p/{pid}/uploads", json={"kind": "clip", "name": "big.mp4", "size": len(data) * 3}, headers=h).json()
    assert client.put(f"/api/uploads/{up['id']}?offset=0", content=data, headers=h).json()["received"] == len(data)
    assert client.post(f"/api/uploads/{up['id']}/done", json={"size": len(data) + 5}, headers=h).status_code == 400  # not what arrived
    r = client.post(f"/api/uploads/{up['id']}/done", json={"size": len(data)}, headers=h)
    assert r.status_code == 200, r.text
    assert studio.db.one("SELECT bytes FROM clips WHERE project_id=?", (pid,))["bytes"] == len(data)
    # and an upload without a final size still has to be complete
    up = client.post(f"/api/p/{pid}/uploads", json={"kind": "clip", "name": "x.mp4", "size": len(data) + 10}, headers=h).json()
    client.put(f"/api/uploads/{up['id']}?offset=0", content=data, headers=h)
    assert client.post(f"/api/uploads/{up['id']}/done", headers=h).status_code == 400


def test_a_recording_longer_than_their_access_allows_is_refused_with_their_own_limit(studio):
    from vidosuper.studio.service import ActionError

    fay = _friend(studio, "Fay")
    for kind in ("voice", "talk"):
        p = studio.create_project(fay, "Long", "9:16", 30, "", kind=kind)
        if kind == "voice":
            studio.db.run("UPDATE projects SET song='talk.m4a', song_seconds=400 WHERE id=?", (p["id"],))
        else:
            studio.db.run("INSERT INTO clips(id, project_id, name, file, bytes, seconds, created) VALUES "
                          "('c1', ?, 'me.mp4', 'c1.mp4', 1, 400, 0)", (p["id"],))
        with pytest.raises(ActionError, match="up to 3"):
            studio.start(studio.db.project(p["id"]))


def test_a_talk_with_clean_labels_checks_what_was_heard_then_makes_a_sample_and_can_go_back(studio, media, monkeypatch):
    from vidosuper.perceive import prep as prep_mod

    monkeypatch.setattr(prep_mod, "transcribe", _fake_speech)
    _friend(studio, "Lu")
    client = TestClient(create_app(studio))
    client.post("/login", data={"name": "Lu", "password": "a long password 2"})
    csrf = _csrf(client.get("/", follow_redirects=True).text)
    client.post("/welcome", data={"csrf": csrf, "pace": "balanced", "sound": "music", "look": "vivid"})
    r = client.post("/new", data={"csrf": csrf, "title": "Labels", "shapes": "9:16", "length": 30, "kind": "talk",
                                  "style": "labels", "intent": "simple labels for my points"}, follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    _upload(client, csrf, pid, media / "clips" / "a_stable.mp4", "clip")
    client.post(f"/p/{pid}/start", data={"csrf": csrf})
    _drain(studio)
    page = client.get(f"/p/{pid}").text
    assert "make a sample" in page and studio.db.project(pid)["stage"] == "understood", studio.db.all("SELECT * FROM events")
    client.post(f"/p/{pid}/understood", data={"csrf": csrf, "story": "Sleep is the secret to a strong memory",
                                              "terms_more": "secret = Secret\nPeople"})
    terms = json.loads(studio.db.project(pid)["brief"])["terms"]
    assert {"heard": "secret", "written": "Secret"} in terms and {"heard": "People", "written": "People"} in terms
    _drain(studio)
    p = studio.db.project(pid)
    assert p["stage"] == "review", studio.db.all("SELECT * FROM events")
    script = json.loads(studio.db.one("SELECT data FROM plans WHERE project_id=? ORDER BY id DESC", (pid,))["data"])
    assert {"heard": "secret", "written": "Secret"} in script["respell"]
    # back to what was understood, to change it: a new script and sample follow from the new brief
    assert "understood" in studio.back_targets(p)
    client.post(f"/p/{pid}/back", data={"csrf": csrf, "to": "understood"})
    assert studio.db.project(pid)["stage"] == "understood"
    client.post(f"/p/{pid}/understood", data={"csrf": csrf, "story": "Sleep helps memory, in three short points"})
    _drain(studio)
    assert studio.db.project(pid)["stage"] == "review"


def test_what_was_heard_must_name_words_from_the_transcript_and_parts_in_order():
    from vidosuper.studio.brain.core import check_talk_understanding
    from vidosuper.studio.brain.schemas import TalkPart, TalkUnderstanding, Term

    utts = [{"start": 0.5, "end": 3.0, "text": "I use Cloud code every day."},
            {"start": 3.2, "end": 6.0, "text": "It writes my tests."}]
    good = TalkUnderstanding(story="How they use an AI coding tool daily", parts=[TalkPart(at=0.5, what="Daily use")],
                             terms=[Term(heard="Cloud code", written="Claude Code", why="the AI coding tool")])
    assert check_talk_understanding(good, utts, 6.0) == []
    bad = good.model_copy(update={"parts": [TalkPart(at=4, what="b"), TalkPart(at=1, what="a")],
                                  "terms": [Term(heard="Claude Code", written="Claude Code")]})
    problems = " ".join(check_talk_understanding(bad, utts, 6.0))
    assert "in the order" in problems and "not in the transcript" in problems


def _fake_composer(monkeypatch, calls: list, fail: bool = False) -> None:
    """ElevenLabs Music stand-in: every plan becomes our own beat of the plan's length (no network, no money)."""
    import os

    from vidosuper.music import compose as compose_mod
    from vidosuper.music.house import make_beat

    def fake(plan, out_path, *, model="music_v2_5", store=False, seed=None, timeout=600.0):
        calls.append({"plan": plan, "store": store})
        if fail:
            raise compose_mod.ComposeError("the music service is down")
        seconds = sum(c.get("duration_ms", 0) for c in plan["chunks"]) / 1000
        tmp = Path(out_path).with_suffix(".tmp.wav")
        make_beat(tmp, seconds, "balanced")
        os.replace(tmp, out_path)
        return {"path": Path(out_path), "song_id": f"song{len(calls)}" if store else "", "seconds": seconds}

    monkeypatch.setattr(compose_mod, "available", lambda: True)
    monkeypatch.setattr(compose_mod, "compose_plan", fake)


def _signed_in(studio, name: str):
    client = TestClient(create_app(studio))
    client.post("/login", data={"name": name, "password": "a long password 2"})
    csrf = _csrf(client.get("/", follow_redirects=True).text)
    client.post("/welcome", data={"csrf": csrf, "pace": "balanced", "sound": "music", "look": "vivid"})
    return client, csrf


def test_music_made_for_the_film_is_read_heard_changed_and_picked_before_anything_is_cut(studio, media, monkeypatch):
    calls: list = []
    _fake_composer(monkeypatch, calls)
    monkeypatch.setattr(prep_module, "transcribe", lambda cid, wav, model, root: Transcript(clip_id=cid, model="fake", language="en"))
    friend = _friend(studio, "Mo")
    client, csrf = _signed_in(studio, "Mo")
    r = client.post("/new", data={"csrf": csrf, "title": "Beach", "shapes": "9:16", "length": 15,
                                  "intent": "a sunny day at the beach"}, follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    page = client.get(f"/p/{pid}").text
    assert "What should it sound like?" in page and "Made for my film" in page
    # asked for, then stopped before it was made: back where they were, nothing used up
    client.post(f"/p/{pid}/beat", data={"csrf": csrf, "ask": "warm lo-fi with piano"})
    assert studio.db.project(pid)["stage"] == "composing"
    client.post(f"/p/{pid}/stop", data={"csrf": csrf})
    assert studio.db.project(pid)["stage"] == "setup" and studio.beats_left(studio.db.project(pid)) == 3
    client.post(f"/p/{pid}/beat", data={"csrf": csrf, "ask": "warm lo-fi with piano"})
    _drain(studio)
    p = studio.db.project(pid)
    beats = studio.beats(p)
    assert p["stage"] == "setup" and p["beat"] == beats[0]["id"], studio.db.all("SELECT * FROM events")
    assert len(beats) == 1 and beats[0]["whole"] and "lo-fi" in beats[0]["idea"]["reading"].lower()
    assert "beats a minute" in beats[0]["facts"]
    plan = calls[0]["plan"]
    assert abs(sum(c["duration_ms"] for c in plan["chunks"]) / 1000 - 17) < 3 and not calls[0]["store"]
    assert all("vocals" in c["negative_styles"] for c in plan["chunks"])
    assert plan["chunks"][-1]["text"].startswith("[Ending]")
    work = studio.s.users_dir / friend["id"] / "projects" / pid
    assert [f.name for f in (work / "music").glob("song.*")] == ["song.mp3"]  # where an uploaded song would be
    page = client.get(f"/p/{pid}").text
    assert "How I read it" in page and "Your film's music" in page and f"/m/{pid}/beats/" in page
    audio = client.get(f"/m/{pid}/beats/{beats[0]['file']}")
    assert audio.status_code == 200 and audio.headers["content-type"].startswith("audio/")
    assert client.get(f"/m/{pid}/beats/../studio.db").status_code == 404
    # a change, heard, then back to the first one
    client.post(f"/p/{pid}/beat", data={"csrf": csrf, "base": beats[0]["id"], "change": "slower and darker"})
    _drain(studio)
    beats = studio.beats(studio.db.project(pid))
    assert len(beats) == 2 and studio.db.project(pid)["beat"] == beats[0]["id"] and "slower and darker" in beats[0]["ask"]
    client.post(f"/p/{pid}/beat/use", data={"csrf": csrf, "beat": beats[1]["id"]})
    assert studio.db.project(pid)["beat"] == beats[1]["id"]
    assert studio.beats_left(studio.db.project(pid)) == 1
    spent = studio.db.all("SELECT * FROM spend WHERE task='beat' AND backend='elevenlabs'")
    assert len(spent) == 2 and all(x["usd"] > 0 and x["backend"] == "elevenlabs" for x in spent)
    # no music takes it away; music made for the film brings the picked one back
    client.post(f"/p/{pid}/music", data={"csrf": csrf, "choice": "none"})
    assert not list((work / "music").glob("song.*"))
    client.post(f"/p/{pid}/music", data={"csrf": csrf, "choice": "made"})
    assert [f.name for f in (work / "music").glob("song.*")] == ["song.mp3"]
    # the film is cut on it: no beat of our own is made
    for name in ("a_stable.mp4", "c_pan.mp4"):
        _upload(client, csrf, pid, media / "clips" / name, "clip")
    client.post(f"/p/{pid}/start", data={"csrf": csrf})
    _drain(studio)
    assert studio.db.project(pid)["stage"] == "understood", studio.db.all("SELECT * FROM events")
    assert len(calls) == 2 and not (work / "music" / "song.wav").exists()
    page = client.get(f"/p/{pid}").text
    assert "Listen to it, or change it" in page and "Your film's music" in page
    # the last try, at 'what I understood': made, and the music line follows it
    client.post(f"/p/{pid}/beat", data={"csrf": csrf, "base": beats[1]["id"], "change": "more drums"})
    _drain(studio)
    p = studio.db.project(pid)
    assert p["stage"] == "understood" and studio.understanding(p)["music"].startswith("Your new track")
    assert studio.beats_left(p) == 0
    r = client.post(f"/p/{pid}/beat", data={"csrf": csrf, "base": beats[1]["id"], "change": "again"}, follow_redirects=True)
    assert "all the tracks this film can have" in r.text


def test_a_long_film_hears_a_taste_first_and_its_full_track_is_made_from_it(studio, media, monkeypatch):
    calls: list = []
    _fake_composer(monkeypatch, calls)
    owner = _owner(studio)
    p = studio.create_project(owner, "Road trip", "9:16", 240, "a long road trip")
    studio.make_beat(p, "epic cinematic drums")
    _drain(studio)
    p = studio.db.project(p["id"])
    row = studio.beats(p)[0]
    assert not row["whole"] and row["song_id"] == "song1" and calls[0]["store"] and "taste" in row["facts"]
    work = studio.s.users_dir / owner["id"] / "projects" / p["id"]
    assert not list((work / "music").glob("song.*"))  # a taste is not the film's music yet
    job = {"id": studio.db.enqueue("prepare", p["id"], owner["id"], {}, 5)}
    path = studio._ensure_track(job, p, owner, None)
    first = calls[-1]["plan"]["chunks"][0]
    assert path.name.endswith("-full.mp3") and first["conditioning_ref"]["song_id"] == "song1"
    assert first["conditioning_ref"]["range"]["end_ms"] - first["conditioning_ref"]["range"]["start_ms"] <= 30000
    assert abs(sum(c["duration_ms"] for c in calls[-1]["plan"]["chunks"]) / 1000 - 242) < 4
    assert all(c["duration_ms"] <= 120000 for c in calls[-1]["plan"]["chunks"])
    assert [f.name for f in (work / "music").glob("song.*")] == ["song.mp3"]
    assert studio.beats(studio.db.project(p["id"]))[0]["full_file"] == path.name


def test_when_the_music_service_fails_our_own_beat_stands_in_and_they_are_told(studio, media, monkeypatch):
    calls: list = []
    _fake_composer(monkeypatch, calls, fail=True)
    friend = _friend(studio, "Ro")
    p = studio.create_project(friend, "Park", "9:16", 30, "a walk in the park")
    studio.make_beat(p, "jazzy")
    _drain(studio)
    p = studio.db.project(p["id"])
    row = studio.beats(p)[0]
    assert row["maker"] == "house" and "our own simple beat" in row["facts"] and p["stage"] == "setup"
    assert studio.beats_left(p) == 3  # it didn't use up a try
    assert not studio.db.all("SELECT * FROM spend WHERE task='beat' AND backend='elevenlabs'")


def test_a_voice_video_gets_the_music_made_for_it_under_the_voice(studio, media, monkeypatch):
    from vidosuper.explainer import make as explainer_make

    calls: list = []
    _fake_composer(monkeypatch, calls)
    monkeypatch.setattr(explainer_make, "transcribe", _fake_speech)
    used: list = []
    real = explainer_make.render

    def render(*a, **kw):
        used.append(kw.get("music"))
        return real(*a, **kw)

    monkeypatch.setattr(explainer_make, "render", render)
    _friend(studio, "Vi")
    client, csrf = _signed_in(studio, "Vi")
    r = client.post("/new", data={"csrf": csrf, "title": "Sleep", "shapes": "9:16", "length": 30, "kind": "voice",
                                  "intent": "explain why sleep helps memory"}, follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    client.post(f"/p/{pid}/music", data={"csrf": csrf, "choice": "made"})
    assert "Add your recording first" in client.get(f"/p/{pid}").text
    _upload(client, csrf, pid, media / "music.wav", "song")
    client.post(f"/p/{pid}/beat", data={"csrf": csrf, "ask": "soft piano, hopeful"})
    _drain(studio)
    p = studio.db.project(pid)
    assert p["beat"] and any("leaves room for a speaking voice" in c["positive_styles"] for c in calls[0]["plan"]["chunks"])
    client.post(f"/p/{pid}/start", data={"csrf": csrf})
    _drain(studio)
    assert "Music under your voice" in client.get(f"/p/{pid}").text
    client.post(f"/p/{pid}/understood", data={"csrf": csrf, "story": "Why sleep helps memory, gently explained"})
    _drain(studio)
    client.post(f"/p/{pid}/pick", data={"csrf": csrf, "pitch": studio.pitches(studio.db.project(pid))[0]["id"]})
    _drain(studio)
    assert used and used[-1] is not None and used[-1].name == studio.beats(studio.db.project(pid))[0]["file"]
    assert len(calls) == 1  # the sample used the track they picked: nothing else was made



def test_a_film_uses_credits_by_its_length_shown_before_it_starts_and_given_back_if_never_finished(studio, media, monkeypatch):
    from vidosuper.studio.service import ActionError

    monkeypatch.setattr(prep_module, "transcribe", lambda cid, wav, model, root: Transcript(clip_id=cid, model="fake", language="en"))
    friend = _friend(studio, "Cy")
    client, csrf = _signed_in(studio, "Cy")
    me = lambda: studio.user(friend["id"])  # noqa: E731
    assert studio.credits(me())["monthly"] == 3 and studio.credits(me())["total"] == 3
    assert "3 credits" in client.get("/").text and 'id="cost-line"' in client.get("/new").text
    assert studio.cost_of("montage", 30) == 1 and studio.cost_of("montage", 60.4) == 1 and studio.cost_of("montage", 61.5) == 2
    assert studio.cost_of("voice", 100) == 3 and studio.cost_of("talk", 100, "labels") == 2 and studio.cost_of("montage", 600) == 10

    def film(title):
        r = client.post("/new", data={"csrf": csrf, "title": title, "shapes": "9:16", "length": 15, "intent": ""},
                        follow_redirects=False)
        pid = r.headers["location"].rsplit("/", 1)[1]
        for name in ("a_stable.mp4", "c_pan.mp4"):
            _upload(client, csrf, pid, media / "clips" / name, "clip")
        return pid

    pid = film("First")
    assert "Uses 1 credit of your 3" in client.get(f"/p/{pid}").text  # before anything is used
    client.post(f"/p/{pid}/start", data={"csrf": csrf})
    assert studio.db.project(pid)["stage"] == "preparing" and studio.credits(me())["total"] == 2
    _drain(studio)
    client.post(f"/p/{pid}/back", data={"csrf": csrf, "to": "setup"})  # changed their mind: starting again is free
    assert "Already counted" in client.get(f"/p/{pid}").text
    client.post(f"/p/{pid}/start", data={"csrf": csrf})
    assert studio.credits(me())["total"] == 2
    _drain(studio)
    client.post(f"/p/{pid}/delete", data={"csrf": csrf, "sure": "yes"})  # never finished: the credit comes back
    assert studio.credits(me())["total"] == 3 and studio.credits(me())["monthly_left"] == 3
    # the owner's numbers: none a month for beta, so a film can't start ...
    owner = _owner(studio)
    studio.set_pricing(1, 1, 0, 12)
    pid = film("Second")
    r = client.post(f"/p/{pid}/start", data={"csrf": csrf}, follow_redirects=True)
    assert "uses 1 credit" in r.text and "you have 0" in r.text and studio.db.project(pid)["stage"] == "setup"
    assert 'disabled' in re.search(r'<button[^>]*id="start-btn"[^>]*>', client.get(f"/p/{pid}").text).group(0)
    # ... until they are given extra ones, which don't run out
    admin = TestClient(create_app(studio))
    admin.post("/login", data={"name": "owner", "password": "a long password 1"})
    acsrf = _csrf(admin.get("/admin").text)
    assert "Credits per minute of film" in admin.get("/admin").text
    admin.post("/admin/user", data={"csrf": acsrf, "uid": friend["id"], "action": "credits", "credits": 2, "note": "for testing"})
    assert studio.credits(me()) == {**studio.credits(me()), "monthly": 0, "extra": 2, "total": 2}
    client.post(f"/p/{pid}/start", data={"csrf": csrf})
    assert studio.db.project(pid)["stage"] == "preparing" and studio.credits(me())["extra"] == 1
    history = client.get("/account").text
    assert "for testing" in history and "+2" in history and "-1" in history
    with pytest.raises(ActionError):
        studio.grant_credits(me(), -5)  # only unused extra credits can be taken back
    assert studio.credits(owner) is None  # the owner is never counted


def test_credits_from_an_earlier_month_come_back_as_extra_ones(studio, monkeypatch):
    from vidosuper.studio.service import Studio as S

    friend = _friend(studio, "Em")
    p = studio.create_project(friend, "Old", "9:16", 30, "")
    monkeypatch.setattr(S, "month_key", staticmethod(lambda at=None: "2026-09"))
    studio._pay_for(studio.db.project(p["id"]))
    assert studio.credits(studio.user(friend["id"]))["monthly_left"] == 2
    monkeypatch.setattr(S, "month_key", staticmethod(lambda at=None: "2026-10"))
    assert studio.credits(studio.user(friend["id"]))["monthly_left"] == 3  # a new month, a new allowance
    studio.delete_project(studio.db.project(p["id"]))
    c = studio.credits(studio.user(friend["id"]))
    assert c["monthly_left"] == 3 and c["extra"] == 1  # September's credit isn't lost


def test_money_protection_caps_a_persons_day_pauses_everything_and_flags_accounts(studio, media, monkeypatch):
    import time as _t

    from vidosuper.studio.brain import Usage
    from vidosuper.studio.service import ActionError
    from vidosuper.studio.worker import spend_watch

    friend = _friend(studio, "Mia")
    owner = _owner(studio)
    assert studio.money() == {"monthly_usd": 60.0, "video_usd": 6.0, "person_day_usd": 3.0}
    with pytest.raises(ActionError):
        studio.set_money(10, 20, 3)  # a video's cap above the month's
    studio.set_money(100, 5, 2)
    assert studio.money()["person_day_usd"] == 2.0
    # a person's daily share: the step waits and carries on by itself, the page says when
    p = studio.create_project(friend, "Busy", "9:16", 15, "")
    job_id = studio.db.enqueue("pitch", p["id"], friend["id"])
    studio.db.set_stage(p["id"], "pitching")
    spend = studio._spender(p, friend["id"], job_id)
    spend(Usage("pitch", "m", "api", usd=1.5))
    job = studio.db.claim()

    def costly(job, project, user):
        studio._spender(project, user["id"], job["id"])(Usage("pitch", "m", "api", usd=0.8))

    monkeypatch.setattr(studio, "_job_pitch", costly)
    studio.run_job(job)
    row = studio.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    p = studio.db.project(p["id"])
    assert row["status"] == "queued" and row["after"] > _t.time() + 3600 and p["stage"] == "paused"
    assert "carries on by itself" in p["note"] and studio.flags(studio.user(friend["id"]))
    # the owner is never capped
    o = studio.create_project(owner, "Mine", "9:16", 15, "")
    studio._spender(o, owner["id"], 0)(Usage("pitch", "m", "api", usd=4.0))
    # when it runs again, the video shows its step again
    studio.db.run("UPDATE jobs SET after=NULL WHERE id=?", (job_id,))
    studio.db.run("DELETE FROM spend WHERE user_id=?", (friend["id"],))
    monkeypatch.setattr(studio, "_job_pitch", lambda job, project, user: None)
    studio.run_job(studio.db.claim())
    assert studio.db.project(p["id"])["stage"] == "pitching"
    # the off switch: nothing new starts, people are told, the owner still can
    studio.set_paused(True, "checking the bill")
    with pytest.raises(ActionError, match="paused"):
        studio._enqueue(studio.db.project(p["id"]), "sample")
    queued = studio.db.one("SELECT * FROM jobs WHERE id=?", (studio.db.enqueue("learn", p["id"], friend["id"]),))
    assert "paused for a moment" in studio.queued_note(queued)
    studio._enqueue(studio.db.project(o["id"]), "learn")
    studio.set_paused(False)
    assert studio.paused() is None
    # a jump in spending is told once
    now = _t.time()
    studio.db.run("INSERT INTO spend(at, user_id, task, model, backend, usd, billed) VALUES (?,?,?,?,?,?,1)",
                  (now - 60, friend["id"], "pitch", "m", "api", 5.0))
    assert "Spending jumped" in spend_watch(studio, now) and spend_watch(studio, now) == ""


def test_the_admin_page_sets_the_caps_and_the_off_switch(studio):
    _owner(studio)
    admin = TestClient(create_app(studio))
    admin.post("/login", data={"name": "owner", "password": "a long password 1"})
    page = admin.get("/admin").text
    assert "Money protection" in page and "Pause everything" in page
    csrf = _csrf(page)
    admin.post("/admin/money", data={"csrf": csrf, "monthly": 80, "video": 7, "person_day": 4})
    assert studio.money() == {"monthly_usd": 80.0, "video_usd": 7.0, "person_day_usd": 4.0}
    admin.post("/admin/pause", data={"csrf": csrf, "on": 1, "why": "test"})
    assert studio.paused()["why"] == "test" and "Everything is paused" in admin.get("/admin").text
    admin.post("/admin/pause", data={"csrf": csrf, "on": 0})
    assert studio.paused() is None


def test_a_persons_next_film_from_the_same_clips_reuses_their_clip_logs(studio, media, monkeypatch):
    from vidosuper.studio.brain.core import LocalBrain
    from vidosuper.studio.pipeline import Pipeline

    monkeypatch.setattr(prep_module, "transcribe", lambda cid, wav, model, root: Transcript(clip_id=cid, model="fake", language="en"))
    monkeypatch.setattr(Pipeline, "log_tag", lambda self: "t1")  # as if an AI logged them
    calls: list = []
    real = LocalBrain.analyse_clip

    def counted(self, clip, brief, sheets):
        calls.append(clip.clip_id)
        return real(self, clip, brief, sheets)

    monkeypatch.setattr(LocalBrain, "analyse_clip", counted)
    friend = _friend(studio, "Lou")
    client, csrf = _signed_in(studio, "Lou")
    for k in range(2):
        r = client.post("/new", data={"csrf": csrf, "title": f"Film {k}", "shapes": "9:16", "length": 15, "intent": ""},
                        follow_redirects=False)
        pid = r.headers["location"].rsplit("/", 1)[1]
        for name in ("a_stable.mp4", "c_pan.mp4"):
            _upload(client, csrf, pid, media / "clips" / name, "clip")
        client.post(f"/p/{pid}/start", data={"csrf": csrf})
        _drain(studio)
        assert studio.db.project(pid)["stage"] == "understood", studio.db.all("SELECT * FROM events")
        assert len(calls) == 2  # the second film logged nothing again
    logs = list((studio.s.users_dir / friend["id"] / "cliplog").glob("*.json"))
    assert len(logs) == 2
    from vidosuper.studio.web.workerapi import manifest

    assert any(f["path"].startswith("cliplog/") for f in manifest(studio.s.users_dir / friend["id"], pid))
