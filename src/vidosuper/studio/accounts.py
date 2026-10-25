"""The one person of this Flicello and their session.

Flicello runs on its person's own computer and answers only there (web/app.py), so there are no accounts to make
or passwords to type: the person is made the first time it opens, and the browser on this computer is signed in by
itself. The session is a random token in an HttpOnly, SameSite=Strict cookie, stored hashed, with its own CSRF
token for every change.
"""

from __future__ import annotations

import secrets
import time

from .db import DB, token_hash
from .settings import StudioSettings


def owner(db: DB) -> dict:
    """The person of this machine: made the first time Flicello opens."""
    row = db.one("SELECT * FROM users WHERE role='owner' AND disabled=0 ORDER BY created LIMIT 1")
    if row:
        return row
    uid = "u" + secrets.token_hex(6)
    db.run("INSERT INTO users(id, name, role, pw, created, onboarded) VALUES (?,?,?,?,?,1)",
           (uid, "You", "owner", "-", time.time()))  # no password: nobody signs in with one
    return db.one("SELECT * FROM users WHERE id=?", (uid,))


def new_session(db: DB, s: StudioSettings, user_id: str, ip: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    db.run("INSERT INTO sessions(token, user_id, csrf, created, expires, seen, ip) VALUES (?,?,?,?,?,?,?)",
           (token_hash(token), user_id, secrets.token_urlsafe(24), now, now + s.session_days * 86400, now, ip))
    return token


def session_user(db: DB, token: str | None) -> tuple[dict, dict] | None:
    """(user, session) for a cookie, or None (expired or gone)."""
    if not token or len(token) > 100:
        return None
    sess = db.one("SELECT * FROM sessions WHERE token=?", (token_hash(token),))
    if not sess or sess["expires"] < time.time():
        return None
    user = db.one("SELECT * FROM users WHERE id=?", (sess["user_id"],))
    if not user or user["disabled"]:
        return None
    if time.time() - (sess["seen"] or 0) > 300:
        db.run("UPDATE sessions SET seen=? WHERE token=?", (time.time(), sess["token"]))
    return user, sess


def renew(db: DB, s: StudioSettings, sess: dict) -> bool:
    """Push the session's end out again (at most once a day), never past `session_max_days` from its start. True
    when the cookie should be sent again."""
    now = time.time()
    target = min(now + s.session_days * 86400, sess["created"] + s.session_max_days * 86400)
    if target - sess["expires"] < 86400:
        return False
    db.run("UPDATE sessions SET expires=? WHERE token=?", (target, sess["token"]))
    return True
