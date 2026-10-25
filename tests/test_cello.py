"""Cello: its shell holds only Flicello's own pages, its voice token is never handed out without a key and a CSRF check,
and its tools act with the same ownership checks as the page's buttons."""

from __future__ import annotations

import pytest

from vidosuper.studio import cello
from vidosuper.studio.service import Studio
from vidosuper.studio.settings import load_settings

from conftest import local_client, person


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDO_STUDIO_BRAIN", raising=False)
    s = load_settings(tmp_path / "home", {"brain": "local", "owner_brain": "local"})
    s.limits.min_free_gb = 0.0
    return Studio(s)


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
    user = person(studio.db)
    for name in [t["name"] for t in cello.TOOLS if t["name"] not in cello.BROWSER]:
        try:
            out = cello.act(studio, user, None, name, {})
        except (ValueError, KeyError) as exc:  # the route turns these into "Not done: ..."
            out = {"result": str(exc)}
        assert "result" in out and not str(out["result"]).startswith("Cello has no tool"), name
    with pytest.raises(ValueError, match="no tool"):
        cello.act(studio, user, None, "rm_rf", {})


def test_the_shell_only_holds_flicellos_own_pages(studio):
    client, _, _ = local_client(studio)
    for bad in ("https://evil.example/x", "//evil.example/x", "/cello?to=/cello", "javascript:alert(1)"):
        r = client.get("/cello", params={"to": bad})
        assert r.status_code == 200 and 'src="/"' in r.text, bad
    r = client.get("/cello", params={"to": "/style"})
    assert 'src="/style"' in r.text
    stranger, _, _ = local_client(studio, peer="10.0.0.5")  # another machine: no session, no shell
    r = stranger.get("/cello", follow_redirects=False)
    assert r.status_code == 403 and 'src="/"' not in r.text
    assert "frame-ancestors 'self'" in client.get("/cello").headers["content-security-policy"]
    assert "wss://generativelanguage.googleapis.com" in client.get("/").headers["content-security-policy"]


def test_no_voice_token_without_csrf_or_a_key(studio, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("VIDO_CELLO_VOICE_MODE", "gemini")  # Gemini Live's voice (this machine's has its own test)
    client, _, csrf = local_client(studio)
    assert client.post("/api/cello/token").status_code == 403
    r = client.post("/api/cello/token", headers={"X-CSRF": csrf})
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["detail"]
    stranger, _, _ = local_client(studio, peer="10.0.0.5")
    assert stranger.post("/api/cello/token", headers={"X-CSRF": csrf}).status_code in (401, 403)


def test_cello_cannot_touch_someone_elses_film(studio):
    other = person(studio.db, "Bo", "friend")  # someone else's film in the same database stays out of reach
    theirs = studio.create_project(other, "Bo's film", "9:16", 15, "fun")
    client, user, csrf = local_client(studio)
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
    user = person(studio.db)
    p = studio.create_project(user, "Ana's film", "9:16", 15, "fun")
    since = cello.now() - 1
    studio.db.run("INSERT INTO jobs (project_id, kind, status, error, created, finished) VALUES (?, 'render', 'failed', "
                      "'ffmpeg ran out of disk', ?, ?)", (p["id"], since, cello.now()))
    said = cello.events(studio, p, since)
    assert said == ["[event] the render step failed: ffmpeg ran out of disk"]
    assert cello.events(studio, p, cello.now() + 5) == []


def test_nothing_new_means_nothing_to_say_even_while_building(studio):
    user = person(studio.db)
    p = studio.create_project(user, "Ana's film", "9:16", 15, "fun")
    studio._set_info(p, autopilot=True)
    assert cello.events(studio, p, cello.now() - 60) == []  # no step ended: Cello stays quiet (no note every 4 s)
    studio.db.run("INSERT INTO jobs (project_id, kind, status, created, finished) VALUES (?, 'sample', 'done', ?, ?)",
                  (p["id"], cello.now(), cello.now()))
    studio.db.run("INSERT INTO jobs (project_id, user_id, kind, status, progress, created) VALUES (?, ?, 'render', "
                  "'running', 0.1, ?)", (p["id"], user["id"], cello.now()))
    said = cello.events(studio, p, cello.now() - 60)
    assert said[0] == "[event] the sample step finished" and "not ready yet" in said[-1]


def test_the_log_keeps_their_conversation_with_cello(studio):
    client, user, csrf = local_client(studio)
    lines = [{"who": "you", "text": "make it hype"}, {"who": "cello", "text": "Done."}, {"who": "tool", "text": "style"},
             {"who": "bogus", "text": "x"}]
    assert client.post("/api/cello/log", headers={"X-CSRF": csrf}, json={"lines": lines}).json()["kept"] == 3
    assert client.post("/api/cello/log", json={"lines": lines}).status_code == 403  # no CSRF
