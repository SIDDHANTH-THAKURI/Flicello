"""The old PC-hosted studio's leftovers keep the site's promise: friends' footage goes once untouched for the
retention time; the owner's stays; a talk in use stays; nothing is touched when whose is whose can't be told."""

import json
import os
import sqlite3
import time
from pathlib import Path

from vidosuper.studio import legacy

DAY = 86400.0


def _age(path: Path, days: float, now: float) -> None:
    t = now - days * DAY
    for f in [path, *path.rglob("*")] if path.is_dir() else [path]:
        os.utime(f, (t, t))


def _studio(root: Path, now: float) -> dict[str, Path]:
    studio = root / "studio"
    (studio / "users").mkdir(parents=True)
    con = sqlite3.connect(studio / "studio.db")
    con.execute("CREATE TABLE users (id TEXT, name TEXT, role TEXT)")
    con.executemany("INSERT INTO users VALUES (?, ?, ?)", [("uowner", "o", "owner"), ("ufriend", "f", "friend"),
                                                            ("ubusy", "b", "friend")])
    con.commit()
    con.close()
    made = {}
    for uid, pid in (("uowner", "pmine"), ("ufriend", "pold"), ("ubusy", "ptalk")):
        clips = studio / "users" / uid / "projects" / pid / "clips"
        clips.mkdir(parents=True)
        (clips / "a.mov").write_bytes(b"x")
        ws = studio / "workspace" / "projects" / f"clips-{pid}"
        ws.mkdir(parents=True)
        (ws / "project.json").write_text(json.dumps({"clips_dir": str(clips)}), encoding="utf-8")
        _age(clips.parent, 20, now)
        _age(ws, 20, now)
        made[pid] = clips.parent
        made["ws-" + pid] = ws
    return made


def test_old_friends_projects_go_and_the_owners_stay(tmp_path):
    now = time.time()
    made = _studio(tmp_path, now)
    # the busy friend's talk is being worked on in the story engine: touched today
    os.utime(made["ptalk"] / "clips" / "a.mov", (now, now))
    story = tmp_path / "story"
    (story / "work").mkdir(parents=True)
    (story / "public").mkdir(parents=True)
    (story / "work" / "source.json").write_text(json.dumps({"talk": str(made["ptalk"] / "clips" / "a.mov")}))
    (story / "public" / "voice.wav").write_bytes(b"v")
    removed = legacy.sweep(root=tmp_path, days=14, now=now, scratch=tmp_path / "nothing")
    assert not made["pold"].exists() and not made["ws-pold"].exists()
    assert not (tmp_path / "studio" / "users" / "ufriend").exists()  # the empty, old account folder too
    assert made["pmine"].exists() and made["ws-pmine"].exists()  # the owner's own: the owner decides
    assert made["ptalk"].exists() and (story / "public" / "voice.wav").exists()  # in use
    assert any("pold" in r for r in removed)

    # weeks later the talk is done with: it goes, and so do the story's copies of it
    later = now + 20 * DAY
    _age(story, 20, later)
    legacy.sweep(root=tmp_path, days=14, now=later, scratch=tmp_path / "nothing")
    assert not made["ptalk"].exists()
    assert not (story / "public" / "voice.wav").exists() and not (story / "work").exists()
    assert made["pmine"].exists()


def test_nothing_is_touched_when_the_owner_cannot_be_told(tmp_path):
    now = time.time()
    made = _studio(tmp_path, now)
    con = sqlite3.connect(tmp_path / "studio" / "studio.db")
    con.execute("UPDATE users SET role='friend'")
    con.commit()
    con.close()
    assert legacy.sweep(root=tmp_path, days=14, now=now) == []
    assert made["pold"].exists()
