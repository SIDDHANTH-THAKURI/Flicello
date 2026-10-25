"""Flicello on the person's own machine: only this computer may open it, the one person is signed in by itself, their
keys stay on the machine, and a service's problems (and only its problems) are remembered and said."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from vidosuper.local import checks, health, keys
from vidosuper.studio.service import Studio
from vidosuper.studio.settings import load_settings
from vidosuper.studio.web.app import create_app


@pytest.fixture()
def local_studio(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDO_STUDIO_BRAIN", raising=False)
    monkeypatch.setattr(keys, "_keyring", lambda: None)  # never the real keychain in tests
    s = load_settings(tmp_path / "home", {"brain": "local", "owner_brain": "local"})
    s.limits.min_free_gb = 0.0
    return Studio(s)


def test_only_this_computer_and_signed_in_by_itself(local_studio):
    client = TestClient(create_app(local_studio), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))
    r = client.get("/")
    assert r.status_code == 200 and "vs_session" in r.headers.get("set-cookie", "")
    owner = local_studio.db.one("SELECT * FROM users WHERE role='owner'")
    assert owner and owner["onboarded"] == 1
    assert client.get("/").status_code == 200  # the same person, the same session
    assert local_studio.db.value("SELECT COUNT(*) FROM users", (), 0) == 1
    evil = TestClient(create_app(local_studio), base_url="http://evil.example", client=("127.0.0.1", 50001))
    r = evil.get("/")
    assert r.status_code == 403 and "only answers on this computer" in r.text


def test_the_connect_page_shows_everything(local_studio, monkeypatch):
    monkeypatch.setattr(checks, "claude_code", lambda: [checks._item("claude", "brain", "Claude Code", checks.OK,
                                                                     "2.1, pro plan")])
    client = TestClient(create_app(local_studio), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))
    r = client.get("/connect")
    assert r.status_code == 200
    for words in ("This computer", "Set up with Claude Code", "Your keys", "Gemini", "ElevenLabs", "AI models",
                  "connect --json"):
        assert words in r.text, words


def test_keys_stay_on_the_machine(tmp_path, monkeypatch):
    monkeypatch.setattr(keys, "_keyring", lambda: None)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert keys.save("ELEVENLABS_API_KEY", "sk_test_123456789", tmp_path) == "file"
    assert keys.get("ELEVENLABS_API_KEY", tmp_path) == "sk_test_123456789"
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert keys.load(tmp_path) == ["ELEVENLABS_API_KEY"]
    keys.save("ELEVENLABS_API_KEY", "", tmp_path)
    assert keys.get("ELEVENLABS_API_KEY", tmp_path) == ""
    with pytest.raises(ValueError):
        keys.save("PATH", "x", tmp_path)
    assert keys.masked("sk_test_123456789") == "sk_t…6789"


def test_a_services_problems_are_remembered_and_cleared(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "HOME", [tmp_path])
    health.failed("anthropic", "Your credit balance is too low to access the Anthropic API.")
    health.failed("gemini", "KeyError: 'frames'")  # our own bug, not the service's problem
    found = {p["service"]: p for p in health.problems()}
    assert set(found) == {"anthropic"} and found["anthropic"]["means"] == "credit is out"
    with health.watching("anthropic"):
        pass
    assert health.problems() == []
    with pytest.raises(RuntimeError):
        with health.watching("elevenlabs"):
            raise RuntimeError("ElevenLabs Music said HTTP 401: invalid_api_key")
    assert health.problems()[0]["means"] == "the key was refused"
