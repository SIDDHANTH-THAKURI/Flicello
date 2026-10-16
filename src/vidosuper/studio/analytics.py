"""First-party analytics without cookies, and the numbers behind the owner's dashboard.

Visits: one row per page view of an HTML page. The visitor is a keyed hash of (IP, browser) with a salt that is made
fresh each day and deleted the next, so the same person can be counted once per day but never followed across days,
and no IP address is stored. Bots are skipped. Paths are normalised so no project id or invite token is ever kept.

Everything else (people, films, jobs, spend, ratings) is read from the studio's own tables. The dashboard shows
counts and aggregates; it never shows anyone's footage, titles, requests or written feedback.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from collections import Counter, defaultdict
from urllib.parse import urlparse

from .db import DB

DAY = 86400
BOT = re.compile(r"bot|crawl|spider|slurp|headless|preview|facebookexternalhit|whatsapp|telegram|curl|wget|python|"
                 r"httpx|go-http|java/|okhttp|monitor|uptime|lighthouse|puppeteer|playwright", re.I)
SKIP = ("/static/", "/api/", "/m/", "/healthz", "/admin/api/", "/.well-known/", "/robots.txt", "/favicon")
ID_PATHS = [(re.compile(r"^/p/[^/]+"), "/p/:id"), (re.compile(r"^/join/[^/]+$"), "/join/:invite"),
            (re.compile(r"^/admin/events/[^/]+"), "/admin/events/:id")]

SCHEMA = """
CREATE TABLE IF NOT EXISTS visits(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, day TEXT NOT NULL, path TEXT NOT NULL, ref TEXT,
  device TEXT, browser TEXT, visitor TEXT NOT NULL, user_id TEXT, utm TEXT);
CREATE INDEX IF NOT EXISTS visits_day ON visits(day);
CREATE TABLE IF NOT EXISTS salts(day TEXT PRIMARY KEY, salt TEXT NOT NULL);
"""


def ensure(db: DB) -> None:
    with db.connect() as con:
        con.executescript(SCHEMA)


def day_of(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def _salt(db: DB, day: str) -> str:
    row = db.one("SELECT salt FROM salts WHERE day=?", (day,))
    if row:
        return row["salt"]
    salt = secrets.token_hex(16)
    db.run("INSERT OR IGNORE INTO salts(day, salt) VALUES (?,?)", (day, salt))
    db.run("DELETE FROM salts WHERE day < ?", (day,))  # yesterday's salt is gone: yesterday's hashes can't be redone
    return db.one("SELECT salt FROM salts WHERE day=?", (day,))["salt"]


def normal_path(path: str) -> str | None:
    if not path or path.startswith(SKIP):
        return None
    for pattern, repl in ID_PATHS:
        path = pattern.sub(repl, path)
    return path[:80]


def device_of(ua: str) -> tuple[str, str]:
    u = ua or ""
    device = "tablet" if re.search(r"iPad|Tablet", u) else ("mobile" if re.search(r"Mobi|Android|iPhone", u) else "desktop")
    for name, pat in (("Edge", r"Edg/"), ("Samsung", r"SamsungBrowser"), ("Chrome", r"Chrome/"), ("Firefox", r"Firefox/"),
                      ("Safari", r"Safari/")):
        if re.search(pat, u):
            return device, name
    return device, "Other"


def referrer(header: str | None, own_host: str, utm: str | None) -> str | None:
    if utm:
        return utm.strip().lower()[:40]
    if not header:
        return None
    host = (urlparse(header).hostname or "").lower()
    if not host or host == own_host:
        return None
    return host[4:] if host.startswith("www.") else host


def record(db: DB, *, path: str, ip: str, ua: str, ref: str | None, host: str, utm: str | None,
           user_id: str | None) -> bool:
    """One page view. False (nothing kept) for bots, private paths and non-pages."""
    clean = normal_path(path)
    if clean is None or BOT.search(ua or "") or not ua:
        return False
    now = time.time()
    day = day_of(now)
    visitor = hashlib.sha256(f"{_salt(db, day)}|{ip}|{ua}".encode("utf-8")).hexdigest()[:16]
    device, browser = device_of(ua)
    db.run("INSERT INTO visits(at, day, path, ref, device, browser, visitor, user_id, utm) VALUES (?,?,?,?,?,?,?,?,?)",
           (now, day, clean, referrer(ref, host, utm), device, browser, visitor, user_id, (utm or "")[:40] or None))
    return True


def purge(db: DB, keep_days: int = 400) -> None:
    db.run("DELETE FROM visits WHERE at < ?", (time.time() - keep_days * DAY,))


# ---------------------------------------------------------------- the dashboard's numbers

def _days(n: int) -> list[str]:
    now = time.time()
    return [day_of(now - (n - 1 - i) * DAY) for i in range(n)]


def metrics(db: DB, days: int = 30) -> dict:
    """Everything the dashboard draws, for the last `days` days (and the period before, for deltas)."""
    ensure(db)
    now = time.time()
    start, prev = now - days * DAY, now - 2 * days * DAY
    owners = [r["id"] for r in db.all("SELECT id FROM users WHERE role='owner'")]
    no_owner = f"NOT IN ({','.join('?' * len(owners))})" if owners else "IS NOT NULL OR 1"
    ox = tuple(owners)
    labels = _days(days)
    index = {d: i for i, d in enumerate(labels)}

    def series(rows, key="n"):
        out = [0] * days
        for r in rows:
            if r["day"] in index:
                out[index[r["day"]]] = r[key] or 0
        return out

    visitors = series(db.all(
        f"SELECT day, COUNT(DISTINCT visitor) AS n FROM visits WHERE at >= ? AND (user_id IS NULL OR user_id {no_owner}) "
        f"GROUP BY day", (start, *ox)))
    views = series(db.all(
        f"SELECT day, COUNT(*) AS n FROM visits WHERE at >= ? AND (user_id IS NULL OR user_id {no_owner}) GROUP BY day",
        (start, *ox)))
    signups = series(db.all(
        "SELECT strftime('%Y-%m-%d', created, 'unixepoch') AS day, COUNT(*) AS n FROM users "
        "WHERE created >= ? AND role != 'owner' GROUP BY day", (start,)))
    films = series(db.all(
        "SELECT strftime('%Y-%m-%d', created, 'unixepoch') AS day, COUNT(DISTINCT project_id) AS n FROM renders "
        "WHERE kind='final' AND created >= ? GROUP BY day", (start,)))
    render_min = series(db.all(
        "SELECT strftime('%Y-%m-%d', created, 'unixepoch') AS day, SUM(COALESCE(render_seconds, 0)) / 60.0 AS n "
        "FROM renders WHERE created >= ? GROUP BY day", (start,)))

    def total(sql, args):
        return db.value(sql, args, 0) or 0

    # headline numbers, this period and the one before
    def two(sql_now, sql_prev, args_now, args_prev):
        return total(sql_now, args_now), total(sql_prev, args_prev)

    v_now, v_prev = two(
        f"SELECT COUNT(DISTINCT day || visitor) FROM visits WHERE at >= ? AND (user_id IS NULL OR user_id {no_owner})",
        f"SELECT COUNT(DISTINCT day || visitor) FROM visits WHERE at >= ? AND at < ? AND (user_id IS NULL OR user_id {no_owner})",
        (start, *ox), (prev, start, *ox))
    s_now, s_prev = two("SELECT COUNT(*) FROM users WHERE created >= ? AND role != 'owner'",
                        "SELECT COUNT(*) FROM users WHERE created >= ? AND created < ? AND role != 'owner'",
                        (start,), (prev, start))
    f_now, f_prev = two("SELECT COUNT(DISTINCT project_id) FROM renders WHERE kind='final' AND created >= ?",
                        "SELECT COUNT(DISTINCT project_id) FROM renders WHERE kind='final' AND created >= ? AND created < ?",
                        (start,), (prev, start))
    usd_now, usd_prev = two("SELECT SUM(usd) FROM spend WHERE billed=1 AND at >= ?",
                            "SELECT SUM(usd) FROM spend WHERE billed=1 AND at >= ? AND at < ?", (start,), (prev, start))
    rm_now, rm_prev = two("SELECT SUM(COALESCE(render_seconds,0))/60.0 FROM renders WHERE created >= ?",
                          "SELECT SUM(COALESCE(render_seconds,0))/60.0 FROM renders WHERE created >= ? AND created < ?",
                          (start,), (prev, start))
    rating = db.one("SELECT AVG(liked) AS a, AVG(match) AS m, COUNT(liked) AS n FROM feedback WHERE created >= ? "
                    "AND liked IS NOT NULL", (start,)) or {}
    rating_prev = db.one("SELECT AVG(liked) AS a FROM feedback WHERE created >= ? AND created < ? AND liked IS NOT NULL",
                         (prev, start)) or {}
    jobs = db.all("SELECT kind, status, COUNT(*) AS n FROM jobs WHERE created >= ? AND status IN ('done','failed') "
                  "GROUP BY kind, status", (start,))
    done = sum(j["n"] for j in jobs if j["status"] == "done")
    failed = sum(j["n"] for j in jobs if j["status"] == "failed")
    active = total("SELECT COUNT(DISTINCT user_id) FROM jobs WHERE created >= ? AND user_id IS NOT NULL AND user_id "
                   + (f"{no_owner}" if owners else "IS NOT NULL"), (start, *ox))

    # the funnel, for accounts made in the period
    cohort = [r["id"] for r in db.all("SELECT id FROM users WHERE created >= ? AND role != 'owner'", (start,))]

    def having(sql):
        if not cohort:
            return 0
        marks = ",".join("?" * len(cohort))
        return db.value(sql.format(ids=marks), tuple(cohort), 0) or 0

    funnel = [
        {"stage": "Visitors", "n": v_now},
        {"stage": "Signed up", "n": len(cohort)},
        {"stage": "Uploaded footage", "n": having("SELECT COUNT(DISTINCT p.user_id) FROM projects p WHERE p.user_id IN ({ids}) "
                                                  "AND (p.stage != 'setup' OR EXISTS (SELECT 1 FROM clips c WHERE c.project_id=p.id))")},
        {"stage": "First film", "n": having("SELECT COUNT(DISTINCT p.user_id) FROM projects p JOIN renders r ON r.project_id=p.id "
                                            "AND r.kind='final' WHERE p.user_id IN ({ids})")},
        {"stage": "Rated it", "n": having("SELECT COUNT(DISTINCT user_id) FROM feedback WHERE user_id IN ({ids})")},
        {"stage": "Second film", "n": having("SELECT COUNT(*) FROM (SELECT p.user_id FROM projects p JOIN renders r ON "
                                             "r.project_id=p.id AND r.kind='final' WHERE p.user_id IN ({ids}) "
                                             "GROUP BY p.user_id HAVING COUNT(DISTINCT p.id) >= 2)")},
    ]

    # AI spend per day by step (top four steps, the rest as Other)
    spend_rows = db.all("SELECT strftime('%Y-%m-%d', at, 'unixepoch') AS day, task, SUM(usd) AS usd FROM spend "
                        "WHERE at >= ? AND billed=1 GROUP BY day, task", (start,))
    by_task = Counter()
    for r in spend_rows:
        by_task[r["task"] or "other"] += r["usd"] or 0
    top = [t for t, _ in by_task.most_common(4)]
    spend_series = {t: [0.0] * days for t in top + (["Other"] if len(by_task) > 4 else [])}
    for r in spend_rows:
        key = r["task"] if r["task"] in top else "Other"
        if r["day"] in index and key in spend_series:
            spend_series[key][index[r["day"]]] += r["usd"] or 0

    # job outcomes by kind
    outcomes = defaultdict(lambda: {"done": 0, "failed": 0})
    for j in jobs:
        outcomes[j["kind"]][j["status"]] += j["n"]
    order = ["prepare", "pitch", "sample", "render", "revise", "learn"]
    outcome_list = [{"kind": k, **outcomes[k]} for k in sorted(outcomes, key=lambda k: order.index(k) if k in order else 99)]

    # ratings 1-10, and the "anything off?" tags as counts (never the written words)
    hist = [0] * 10
    for r in db.all("SELECT liked, COUNT(*) AS n FROM feedback WHERE created >= ? AND liked BETWEEN 1 AND 10 "
                    "GROUP BY liked", (start,)):
        hist[int(r["liked"]) - 1] = r["n"]
    tags = Counter()
    for r in db.all("SELECT tags FROM feedback WHERE created >= ?", (start,)):
        try:
            tags.update(json.loads(r["tags"] or "[]"))
        except ValueError:
            pass

    # traffic
    def top_of(column, limit=8):
        return [{"label": r["k"] or "(direct)", "n": r["n"]} for r in db.all(
            f"SELECT {column} AS k, COUNT(DISTINCT day || visitor) AS n FROM visits WHERE at >= ? AND "
            f"(user_id IS NULL OR user_id {no_owner}) GROUP BY k ORDER BY n DESC LIMIT {limit}", (start, *ox))]

    # retention: weekly sign-up cohorts, share active in each later week (sign-in or a film step)
    weeks = 8
    week0 = now - weeks * 7 * DAY
    users = db.all("SELECT id, created FROM users WHERE role != 'owner' AND created >= ?", (week0,))
    acts = defaultdict(set)
    for r in db.all("SELECT user_id, at FROM audit WHERE action='signin' AND at >= ? AND user_id IS NOT NULL", (week0,)):
        acts[r["user_id"]].add(int((r["at"] - week0) // (7 * DAY)))
    for r in db.all("SELECT user_id, created AS at FROM jobs WHERE created >= ? AND user_id IS NOT NULL", (week0,)):
        acts[r["user_id"]].add(int((r["at"] - week0) // (7 * DAY)))
    cohorts = []
    for w in range(weeks):
        members = [u for u in users if int((u["created"] - week0) // (7 * DAY)) == w]
        row = []
        for k in range(weeks - w):
            if not members:
                row.append(None)
                continue
            row.append(round(100 * sum(1 for u in members if (w + k) in acts[u["id"]]) / len(members)))
        cohorts.append({"week": day_of(week0 + w * 7 * DAY), "size": len(members), "cells": row})

    return {
        "days": labels,
        "kpi": {
            "visitors": [v_now, v_prev], "signups": [s_now, s_prev], "films": [f_now, f_prev],
            "spend": [round(usd_now, 2), round(usd_prev, 2)], "render_min": [round(rm_now, 1), round(rm_prev, 1)],
            "rating": [round(rating.get("a") or 0, 2), round(rating_prev.get("a") or 0, 2), rating.get("n") or 0],
            "match": round(rating.get("m") or 0, 2), "success": [done, failed], "active": active,
        },
        "series": {"visitors": visitors, "views": views, "signups": signups, "films": films,
                   "render_min": [round(x, 1) for x in render_min]},
        "funnel": funnel,
        "spend": {"series": {k: [round(v, 4) for v in vals] for k, vals in spend_series.items()}},
        "outcomes": outcome_list,
        "ratings": {"hist": hist, "tags": tags.most_common(8)},
        "traffic": {"referrers": top_of("ref"), "devices": top_of("device", 4), "browsers": top_of("browser", 6),
                    "pages": top_of("path", 8)},
        "cohorts": cohorts,
    }


def live(db: DB) -> dict:
    """Jobs moving right now (no titles: kind, stage, progress, who by display name)."""
    rows = db.all("SELECT j.id, j.kind, j.status, j.progress, j.message, j.created, j.started, "
                  "COALESCE(u.display, u.name) AS who FROM jobs j LEFT JOIN users u ON u.id=j.user_id "
                  "WHERE j.status IN ('running', 'queued', 'waiting') ORDER BY j.status='running' DESC, j.id")
    return {"jobs": rows, "at": time.time()}
