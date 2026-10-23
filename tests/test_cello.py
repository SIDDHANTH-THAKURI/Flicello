"""Cello: its shell holds only Flicello's own pages, its voice token is never handed out without a key and a CSRF check,
and its tools act with the same ownership checks as the page's buttons."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from vidosuper.studio import accounts, cello
from vidosuper.studio.service import Studio
from vidosuper.studio.settings import load_settings
from vidosuper.studio.web.app import create_app


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDO_STUDIO_BRAIN", raising=False)
    s = load_settings(tmp_path / "home", {"brain": "local", "owner_brain": "local"})
    s.limits.min_free_gb = 0.0
    return Studio(s)


def _signed_in(studio: Studio, name: str) -> tuple[TestClient, dict, str]:
    user = accounts.register(studio.db, accounts.create_invite(studio.db, studio.s, name), name, "a long password 9")
    client = TestClient(create_app(studio))
    client.post("/login", data={"name": name, "password": "a long password 9"}, follow_redirects=False)
    csrf = re.search(r'name="csrf" content="([^"]+)"', client.get("/cello").text).group(1)
    return client, user, csrf


def p_stage(studio: Studio, p: dict) -> str:
    return studio.db.project(p["id"])["stage"]


def test_every_tool_cello_is_told_about_is_one_it_can_use():
    names = [t["name"] for t in cello.TOOLS]
    assert len(names) == len(set(names))
    assert cello.BROWSER <= set(names)
    cfg = cello.live_config()
    assert cfg["tools"][0]["function_declarations"] is cello.TOOLS and cfg["response_modalities"] == ["AUDIO"]
    # patient turn-taking: a pause mid-sentence is not the end of what they're saying
    assert cfg["realtime_input_config"]["automatic_activity_detection"]["silence_duration_ms"] >= 800


def test_server_tools_all_answer(studio):
    user = accounts.register(studio.db, accounts.create_invite(studio.db, studio.s, "Ana"), "Ana", "a long password 9")
    for name in [t["name"] for t in cello.TOOLS if t["name"] not in cello.BROWSER]:
        try:
            out = cello.act(studio, user, None, name, {})
        except (ValueError, KeyError) as exc:  # the route turns these into "Not done: ..."
            out = {"result": str(exc)}
        assert "result" in out and not str(out["result"]).startswith("Cello has no tool"), name
    with pytest.raises(ValueError, match="no tool"):
        cello.act(studio, user, None, "rm_rf", {})


def test_the_shell_only_holds_flicellos_own_pages(studio):
    client, _, _ = _signed_in(studio, "Ana")
    for bad in ("https://evil.example/x", "//evil.example/x", "/cello?to=/cello", "javascript:alert(1)"):
        r = client.get("/cello", params={"to": bad})
        assert r.status_code == 200 and 'src="/"' in r.text, bad
    r = client.get("/cello", params={"to": "/style"})
    assert 'src="/style"' in r.text
    r = TestClient(create_app(studio)).get("/cello", follow_redirects=False)
    assert r.status_code == 303  # signed out: to sign-in, not the shell
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
    assert "wss://generativelanguage.googleapis.com" in client.get("/").headers["content-security-policy"]


def test_no_voice_token_without_csrf_or_a_key(studio, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client, _, csrf = _signed_in(studio, "Ana")
    assert client.post("/api/cello/token").status_code == 403
    r = client.post("/api/cello/token", headers={"X-CSRF": csrf})
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["detail"]
    assert TestClient(create_app(studio)).post("/api/cello/token", headers={"X-CSRF": csrf}).status_code in (401, 403)


def test_cello_cannot_touch_someone_elses_film(studio):
    owner = accounts.register(studio.db, accounts.create_invite(studio.db, studio.s, "Bo"), "Bo", "a long password 9")
    theirs = studio.create_project(owner, "Bo's film", "9:16", 15, "fun")
    client, user, csrf = _signed_in(studio, "Ana")
    mine = studio.create_project(user, "Ana's film", "9:16", 15, "fun")
    r = client.post("/api/cello/act", headers={"X-CSRF": csrf}, json={"pid": theirs["id"], "name": "discard", "args": {}})
    assert r.json()["result"] == "That film isn't theirs or doesn't exist."
    assert client.post("/api/cello/act", json={"pid": mine["id"], "name": "look"}).status_code == 403  # no CSRF
    seen = client.post("/api/cello/act", headers={"X-CSRF": csrf}, json={"pid": mine["id"], "name": "look"}).json()["result"]
    assert seen["title"] == "Ana's film" and seen["stage"] == p_stage(studio, mine)
    films = client.post("/api/cello/act", headers={"X-CSRF": csrf}, json={"name": "list_films"}).json()["result"]
    assert [f["title"] for f in films] == ["Ana's film"]
    r = client.post("/api/cello/act", headers={"X-CSRF": csrf}, json={"name": "make_it", "args": {}})
    assert r.json()["result"].startswith("Not done: No film is open")
    assert client.get("/api/cello/events", params={"pid": theirs["id"]}).status_code == 404
    assert client.get("/api/cello/events", params={"pid": mine["id"], "since": 0}).json()["events"] == []


def test_cello_hears_when_a_step_ends(studio):
    user = accounts.register(studio.db, accounts.create_invite(studio.db, studio.s, "Ana"), "Ana", "a long password 9")
    p = studio.create_project(user, "Ana's film", "9:16", 15, "fun")
    since = cello.now() - 1
    studio.db.run("INSERT INTO jobs (project_id, kind, status, error, created, finished) VALUES (?, 'render', 'failed', "
                      "'ffmpeg ran out of disk', ?, ?)", (p["id"], since, cello.now()))
    said = cello.events(studio, p, since)
    assert said == ["[event] the render step failed: ffmpeg ran out of disk"]
    assert cello.events(studio, p, cello.now() + 5) == []
