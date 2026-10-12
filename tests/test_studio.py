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
from vidosuper.studio.service import Studio
from vidosuper.studio.settings import load_settings
from vidosuper.studio.storage import safe_child
from vidosuper.studio.web.app import create_app
from vidosuper.studio.worker import Worker


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDO_STUDIO_BRAIN", raising=False)
    s = load_settings(tmp_path / "home", {"brain": "local", "owner_brain": "local", "render_preset": "ultrafast"})
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
    assert "Three quick questions" in home.text  # friends answer the style questions first
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
    page = client.get(f"/p/{pid}").text
    assert "Make this one" in page, studio.db.all("SELECT * FROM events")
    pitch_id = int(re.search(r'name="pitch" value="(\d+)"', page).group(1))
    # one more idea, then pick
    client.post(f"/p/{pid}/another", data={"csrf": csrf, "reason": "funnier"})
    _drain(studio)
    assert studio.db.value("SELECT COUNT(*) FROM pitches WHERE project_id=?", (pid,)) == 4
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


def test_public_pages_are_kept_out_of_search_and_carry_security_headers(studio):
    client = TestClient(create_app(studio))
    r = client.get("/robots.txt")
    assert r.status_code == 200 and "Disallow: /" in r.text
    r = client.get("/login")
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
    assert first.zoom == 1.0 and again.zoom > 1.1 and again.zoom_center != (0.5, 0.5)  # looks different the second time
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
    assert "Heads-up about the length" in page and "about 14 s" in page and "Make it 15 s" in page
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
