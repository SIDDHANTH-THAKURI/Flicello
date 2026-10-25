"""Shared fixtures: synthetic footage, an isolated workspace, and one ingest+measure pass."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from vidosuper.config import load_config
from vidosuper.devtools import make_synthetic_clips
from vidosuper.ingest import ingest_folder
from vidosuper.perceive import measure_project


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("media")
    make_synthetic_clips(root)
    return root


@pytest.fixture(scope="session")
def cfg(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("workspace")
    return load_config({"workspace": str(workspace), "render": {"final": {"preset": "veryfast"}}})


@pytest.fixture(scope="session")
def ingested(media, cfg):
    project, clips = ingest_folder(media / "clips", cfg)
    signals = measure_project(project, cfg)
    by_name = {Path(c.rel_path).stem: c for c in clips}
    return project, by_name, signals


CREDENTIAL = re.compile(r"(API_KEY|_SECRET|_TOKEN|CLIENT_ID)$")


@pytest.fixture(autouse=True)
def no_real_credentials(monkeypatch):
    """No test reaches a real service with the developer's own keys. The code under test loads the repo's .env with
    setdefault(), so every credential named there or in the environment is blanked (blank means "not set"
    everywhere); a test that needs one sets a fake."""
    from vidosuper.config import REPO_ROOT

    names = set(os.environ)
    env = REPO_ROOT / ".env"
    if env.exists():
        names |= {line.split("=", 1)[0].strip() for line in env.read_text(encoding="utf-8").splitlines()
                  if "=" in line and not line.lstrip().startswith("#")}
    for name in names:
        if CREDENTIAL.search(name):
            monkeypatch.setenv(name, "")


@pytest.fixture(autouse=True)
def no_real_keychain(monkeypatch):
    """The studio puts the keys kept in the keychain into its environment when it starts: never the developer's own
    in a test."""
    from vidosuper.local import keys

    monkeypatch.setattr(keys, "_keyring", lambda: None)


@pytest.fixture(autouse=True)
def offline_animations(tmp_path, monkeypatch):
    """No test reaches LottieFiles or runs the (slow) portrait-matting model: the animation library lives in a temp
    folder and is offline, and the cut-out is off unless a test turns it on with a fake matte."""
    from vidosuper.explainer import cutout, lottie_lib

    monkeypatch.setattr(lottie_lib, "ROOT", tmp_path / "lottie")

    def offline(url, timeout=20.0):
        raise OSError("offline in tests")

    monkeypatch.setattr(lottie_lib, "fetch_url", offline)
    monkeypatch.setattr(cutout, "available", lambda model=None: False)


def person(db, name: str = "You", role: str = "owner") -> dict:
    """A person in the studio's database: the one person of this Flicello (made the way it makes them the first time
    it opens), or, with another name, someone else whose films must stay out of reach."""
    import secrets
    import time

    from vidosuper.studio import accounts

    if role == "owner" and name == "You":
        return accounts.owner(db)
    uid = "u" + secrets.token_hex(6)
    db.run("INSERT INTO users(id, name, role, pw, created, onboarded) VALUES (?,?,?,?,?,1)",
           (uid, name, role, "-", time.time()))
    return db.one("SELECT * FROM users WHERE id=?", (uid,))


def local_client(studio, peer: str = "127.0.0.1"):
    """The browser on this computer, signed in by itself: (client, the person, their CSRF token). Another `peer`
    is a machine that isn't this one (it gets nowhere)."""
    from fastapi.testclient import TestClient

    from vidosuper.studio import accounts
    from vidosuper.studio.web.app import create_app

    client = TestClient(create_app(studio), base_url=f"http://127.0.0.1:{studio.s.port}", client=(peer, 50000))
    page = client.get("/style")
    found = re.search(r'name="csrf" content="([^"]+)"', page.text)
    return client, accounts.owner(studio.db), found.group(1) if found else ""
