"""Seed a local dev studio with films in every state, so the screens can be looked at without running the AI.
    VIDO_STUDIO_HOME=... python frontend/tools/seed_dev.py <tiles dir> [email]
Uses the landing page's 9:16 tiles as footage. Only for a throwaway dev home: never point it at real data."""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

from vidosuper.studio.service import Studio
from vidosuper.studio.settings import load_settings
from vidosuper.studio.storage import workdir

tiles = Path(sys.argv[1])
email = sys.argv[2] if len(sys.argv) > 2 else None
s = load_settings()
assert "flicello-dev" in str(s.home), "seed only a dev home"
studio = Studio(s)
user = (studio.db.one("SELECT * FROM users WHERE email=?", (email,)) if email
        else studio.db.one("SELECT * FROM users ORDER BY created DESC LIMIT 1"))
assert user, "make an account first"
now = time.time()


def project(title, stage, kind="montage", intent="", song="", age=0.0):
    pid = "p" + secrets.token_hex(6)
    studio.db.run("INSERT INTO projects(id, user_id, title, shapes, length, intent, stage, created, updated, kind, song)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,?)", (pid, user["id"], title, "9:16", 30, intent, stage, now - age,
                                                      now - age, kind, song or None))
    w = workdir(s, user["id"], pid)
    for d in (w.out, w.stills):
        d.mkdir(parents=True, exist_ok=True)
    return pid, w


def still(w, tile, name):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1.5", "-i", str(tiles / f"{tile:02d}.mp4"), "-frames:v", "1",
                    str(w.stills / name)], check=True)
    return name


def film(w, tiles_used, name, seconds=8):
    parts = []
    for i, t in enumerate(tiles_used):
        parts += ["-i", str(tiles / f"{t:02d}.mp4")]
    n = len(tiles_used)
    graph = "".join(f"[{i}:v]scale=540:960,setsar=1[v{i}];" for i in range(n)) + "".join(f"[v{i}]" for i in range(n)) + \
        f"concat=n={n}:v=1:a=0[out]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *parts, "-filter_complex", graph, "-map", "[out]", "-c:v", "libx264",
                    "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(w.out / name)], check=True)


pitch = lambda title, log, feel: {  # noqa: E731
    "title": title, "logline": log, "feeling": feel, "hook": "A wave folds over the rocks in slow motion",
    "turn": "The tide turns and the cuts quicken on the drop", "ending": "The lighthouse, alone, as the music breathes out",
    "beats": [{"what": "Quiet water, wide and still", "music": "intro"}, {"what": "Waves build, closer and closer", "music": "build"},
              {"what": "The drop: every hit a new crash", "music": "drop"}, {"what": "Calm returns at the lighthouse", "music": "outro"}],
    "stills": [], "why": "Your footage is all water and light."}

# a story to pick
pid, w = project("Salt", "choose", intent="The coast in one minute, calm then wild", song="Tides.mp3", age=3600)
for k, (t, log, feel) in enumerate([("Salt", "The sea keeps rewriting the coast.", "calm → wild → calm"),
                                     ("Harbour Hours", "One city, measured in ferries.", "busy → golden"),
                                     ("Edge of the Map", "Every road ends at water.", "restless → open")]):
    names = [still(w, tile, f"pitch{k}-{i}.jpg") for i, tile in enumerate([(k * 3 + j) % 12 for j in range(4)])]
    studio.db.run("INSERT INTO pitches(project_id, round, data, stills, created) VALUES (?,?,?,?,?)",
                  (pid, 0, json.dumps(pitch(t, log, feel)), json.dumps(names), now))

# working on it
pid, w = project("Edge of the Map", "rendering", intent="", song="Run.mp3", age=600)
still(w, 7, "pitch0-0.jpg")
studio.db.run("INSERT INTO pitches(project_id, round, data, stills, created) VALUES (?,?,?,?,?)",
              (pid, 0, json.dumps(pitch("Edge of the Map", "Every road ends at water.", "restless → open")), json.dumps(["pitch0-0.jpg"]), now))
studio.db.run("INSERT INTO jobs(kind, project_id, user_id, status, priority, created, started, progress, message, attempts, moved)"
              " VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("render", pid, user["id"], "running", 6, now - 120, now - 90, 0.42,
                                                 "Grading shot 12 of 28", 1, now))

# a sample to check
pid, w = project("Lighthouse", "review", song="Beacon.mp3", age=7200)
film(w, [3, 10], "sample-1.mp4")
studio.db.run("INSERT INTO renders(project_id, version, kind, shape, file, seconds, created) VALUES (?,?,?,?,?,?,?)",
              (pid, 0, "sample", "9:16", "sample-1.mp4", 8.0, now))

# finished
pid, w = project("Harbour Hours", "done", intent="Sydney harbour, golden and slow", song="Golden.mp3", age=86400)
still(w, 0, "pitch0-0.jpg")
studio.db.run("INSERT INTO pitches(project_id, round, data, stills, created) VALUES (?,?,?,?,?)",
              (pid, 0, json.dumps(pitch("Harbour Hours", "One city, measured in ferries.", "busy → golden")), json.dumps(["pitch0-0.jpg"]), now))
film(w, [0, 1, 11, 2], "v1-9x16.mp4")
studio.db.run("UPDATE projects SET version=1 WHERE id=?", (pid,))
studio.db.run("INSERT INTO renders(project_id, version, kind, shape, file, seconds, created) VALUES (?,?,?,?,?,?,?)",
              (pid, 1, "final", "9:16", "v1-9x16.mp4", 16.0, now))

# just started
pid, w = project("Beach day", "setup", intent="A lazy Sunday with the dog", age=60)
print("seeded films for", user["email"] or user["name"])
