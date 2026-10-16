"""Fill a throwaway dev studio with 90 days of believable activity, to look at the dashboard. Dev homes only.
    VIDO_STUDIO_HOME=...flicello-dev python frontend/tools/seed_metrics.py"""

from __future__ import annotations

import json
import math
import random
import secrets
import time

from vidosuper.studio import analytics
from vidosuper.studio.service import Studio
from vidosuper.studio.settings import load_settings

s = load_settings()
assert "flicello-dev" in str(s.home), "seed only a dev home"
st = Studio(s)
db = st.db
analytics.ensure(db)
rnd = random.Random(7)
now = time.time()
DAY = 86400
pages = ["/", "/", "/", "/signup", "/signin", "/legal/privacy", "/p/:id", "/new", "/legal/terms", "/style"]
refs = [None, None, "instagram.com", "t.co", "google.com", "reddit.com", "whatsapp", "producthunt.com"]
uas = ["Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1",
       "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36",
       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
       "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15",
       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36 Edg/131.0"]
db.run("DELETE FROM visits")
for d in range(90):
    t0 = now - (89 - d) * DAY
    base = 18 + d * 1.6 + 25 * math.sin(d / 6) ** 2 + (60 if d in (61, 62) else 0)  # a launch spike
    for v in range(int(base * rnd.uniform(0.8, 1.2))):
        visitor = secrets.token_hex(8)
        ua = rnd.choice(uas)
        dev, br = analytics.device_of(ua)
        for _ in range(rnd.choice([1, 1, 2, 3])):
            at = t0 + rnd.uniform(0, DAY)
            db.run("INSERT INTO visits(at, day, path, ref, device, browser, visitor, user_id, utm) VALUES (?,?,?,?,?,?,?,?,?)",
                   (at, analytics.day_of(at), rnd.choice(pages), rnd.choice(refs), dev, br, visitor, None, None))
# accounts, films, jobs, spend, ratings
tasks = ["analyse", "pitch", "design", "revise", "coach"]
for d in range(90):
    t0 = now - (89 - d) * DAY
    for _ in range(rnd.choice([0, 0, 1, 1, 2, 3]) + (4 if d in (61, 62) else 0)):
        uid = "u" + secrets.token_hex(6)
        created = t0 + rnd.uniform(0, DAY)
        db.run("INSERT INTO users(id, name, role, pw, created, email, display, joined_by, adult, terms, onboarded)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,1)", (uid, f"seed-{uid}", "friend", "", created, f"{uid}@example.com",
                                                   rnd.choice(["Mia", "Leo", "Zara", "Kai", "Noor", "Eli", "Ava"]),
                                                   rnd.choice(["google", "email", "email"]), created, "2026-10-17"))
        db.run("INSERT INTO audit(at, user_id, action, detail) VALUES (?,?,?,?)", (created, uid, "signup", "email"))
        for w in range(1, 8):  # some come back
            if rnd.random() < 0.55 / w ** 0.6 and created + w * 7 * DAY < now:
                db.run("INSERT INTO audit(at, user_id, action, detail) VALUES (?,?,?,?)",
                       (created + w * 7 * DAY + rnd.uniform(0, 6 * DAY), uid, "signin", "passkey"))
        if rnd.random() < 0.72:
            pid = "p" + secrets.token_hex(6)
            db.run("INSERT INTO projects(id, user_id, title, shapes, length, stage, created, updated) VALUES (?,?,?,?,?,?,?,?)",
                   (pid, uid, "seed", "9:16", 30, "done", created + 600, created + 900))
            for kind in ("prepare", "pitch", "sample", "render"):
                ok = rnd.random() > (0.12 if kind == "render" else 0.05)
                db.run("INSERT INTO jobs(project_id, user_id, kind, status, created, started, finished) VALUES (?,?,?,?,?,?,?)",
                       (pid, uid, kind, "done" if ok else "failed", created + 700, created + 710, created + 900))
            if rnd.random() < 0.8:
                db.run("INSERT INTO renders(project_id, version, kind, shape, file, seconds, render_seconds, created)"
                       " VALUES (?,?,?,?,?,?,?,?)", (pid, 1, "final", "9:16", "v1.mp4", 30, rnd.uniform(120, 420), created + 1200))
                if rnd.random() < 0.6:
                    db.run("INSERT INTO feedback(project_id, user_id, stage, version, match, liked, tags, text, created)"
                           " VALUES (?,?,?,?,?,?,?,?,?)", (pid, uid, "final", 1, rnd.choice([3, 4, 4, 5, 5]),
                                                           rnd.choice([5, 6, 7, 7, 8, 8, 8, 9, 9, 10]),
                                                           json.dumps(rnd.sample(["too fast", "wrong music", "boring start", "colour", "too long"], rnd.choice([0, 0, 1, 2]))),
                                                           "", created + 1500))
            for task in tasks:
                db.run("INSERT INTO spend(at, user_id, project_id, task, model, backend, tin, tout, usd, billed, seconds)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?,?)", (created + 800, uid, pid, task, "claude-sonnet-5-5", "api",
                                                         20000, 3000, rnd.uniform(0.02, 0.25) * (3 if task == "analyse" else 1), 1, 20))
print("seeded", db.value("SELECT COUNT(*) FROM visits", (), 0), "visits,", db.value("SELECT COUNT(*) FROM users", (), 0), "people")
