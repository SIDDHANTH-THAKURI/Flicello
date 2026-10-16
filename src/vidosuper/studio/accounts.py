"""People, invites, passwords and sessions.

- Passwords: scrypt (n=2^15, r=8, p=1) with a random salt; compared in constant time.
- Invites: one-time links that expire; stored hashed. The first account is made from an owner invite
  printed by `vido studio invite --owner`.
- Sessions: a random token in an HttpOnly, SameSite=Strict cookie, stored hashed, with its own CSRF token.
- Failed logins are rate-limited per account and per address.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

from .db import DB, token_hash
from .settings import StudioSettings

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{1,30}$")
MIN_PASSWORD = 10
_N, _R, _P = 2 ** 15, 8, 1


class AccountError(ValueError):
    """Something the person can fix (shown to them as is)."""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        test = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                              maxmem=64 * 1024 * 1024, dklen=len(digest) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(test.hex(), digest)


# ---------------------------------------------------------------- invites and accounts

def create_invite(db: DB, s: StudioSettings, label: str = "", role: str = "friend") -> str:
    if role not in ("owner", "friend"):
        raise ValueError("role is owner or friend")
    token = secrets.token_urlsafe(24)
    now = time.time()
    db.run("INSERT INTO invites(token, label, role, created, expires) VALUES (?,?,?,?,?)",
           (token_hash(token), label[:60], role, now, now + s.invite_days * 86400))
    return token


def open_invite(db: DB, token: str) -> dict | None:
    row = db.one("SELECT * FROM invites WHERE token=?", (token_hash(token),))
    if not row or row["used_by"] or row["expires"] < time.time():
        return None
    return row


def register(db: DB, token: str, name: str, password: str) -> dict:
    invite = open_invite(db, token)
    if invite is None:
        raise AccountError("This invite link has expired or was already used. Ask for a new one.")
    name = " ".join(name.split())
    if not NAME.match(name):
        raise AccountError("Pick a name of 2-31 letters, numbers, spaces, dots or dashes.")
    if len(password) < MIN_PASSWORD:
        raise AccountError(f"Use a password of at least {MIN_PASSWORD} characters.")
    if db.one("SELECT id FROM users WHERE name=?", (name,)):
        raise AccountError("That name is taken; try another.")
    uid = "u" + secrets.token_hex(6)
    con = db.connect()
    try:  # the invite is spent in the same transaction that makes the account
        con.execute("BEGIN IMMEDIATE")
        used = con.execute("UPDATE invites SET used_by=?, used=? WHERE token=? AND used_by IS NULL",
                           (uid, time.time(), token_hash(token))).rowcount
        if not used:
            con.execute("ROLLBACK")
            raise AccountError("This invite link was just used. Ask for a new one.")
        con.execute("INSERT INTO users(id, name, role, pw, created) VALUES (?,?,?,?,?)",
                    (uid, name, invite["role"], hash_password(password), time.time()))
        con.execute("COMMIT")
    finally:
        con.close()
    return db.one("SELECT * FROM users WHERE id=?", (uid,))


# ---------------------------------------------------------------- logins and sessions

def _limited(db: DB, keys: list[str], limit: int, window: float = 900.0) -> bool:
    since = time.time() - window
    db.run("DELETE FROM attempts WHERE at < ?", (time.time() - 86400,))
    return any(db.value("SELECT COUNT(*) FROM attempts WHERE key=? AND at>=?", (k, since), 0) >= limit for k in keys)


def new_device(db: DB, user_id: str) -> str:
    """A long-lived marker for a browser this person signed in from (OWASP 'device cookie')."""
    token = secrets.token_urlsafe(24)
    db.run("INSERT INTO devices(token, user_id, created) VALUES (?,?,?)", (token_hash(token), user_id, time.time()))
    return token


def _known_device(db: DB, token: str | None, user_id: str | None) -> str | None:
    if not token or not user_id or len(token) > 100:
        return None
    hashed = token_hash(token)
    return hashed if db.one("SELECT 1 AS ok FROM devices WHERE token=? AND user_id=?", (hashed, user_id)) else None


def login(db: DB, s: StudioSettings, name: str, password: str, ip: str, device: str | None = None) -> tuple[dict, str]:
    """Failed tries are limited per account, so guessing a password is hopeless; but on a browser the person
    has signed in from before they're limited per that browser instead, so someone else's bad tries can't
    lock them out."""
    user = db.one("SELECT * FROM users WHERE name=?", (" ".join(name.split()),))
    known = _known_device(db, device, user["id"] if user else None)
    keys = [f"dev:{known}"] if known else [f"user:{name.strip().lower()}"]
    unknown_ip = ip in ("127.0.0.1", "::1", "?")  # a proxy that didn't say who called: no per-address limit possible
    if not known:
        if not unknown_ip:
            keys.append(f"ip:{ip}")
        # a spray of guesses across many accounts and addresses: slow everyone but known browsers
        if _limited(db, ["all"], s.limits.login_attempts * 12):
            raise AccountError("Too many tries from everywhere right now. Wait a few minutes and try again.")
    if _limited(db, keys, s.limits.login_attempts):
        raise AccountError("Too many tries. Wait 15 minutes and try again.")
    ok = user is not None and not user["disabled"] and verify_password(password, user["pw"])
    if not ok:
        if user is None:
            verify_password(password, "scrypt$32768$8$1$00$00")  # same work either way: no name probing by timing
        for k in keys + ([] if known else ["all"]):
            db.run("INSERT INTO attempts(key, at) VALUES (?,?)", (k, time.time()))
        raise AccountError("That name and password don't match.")
    return user, new_session(db, s, user["id"], ip)


def new_session(db: DB, s: StudioSettings, user_id: str, ip: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    db.run("INSERT INTO sessions(token, user_id, csrf, created, expires, seen, ip) VALUES (?,?,?,?,?,?,?)",
           (token_hash(token), user_id, secrets.token_urlsafe(24), now, now + s.session_days * 86400, now, ip))
    return token


def session_user(db: DB, token: str | None) -> tuple[dict, dict] | None:
    """(user, session) for a cookie, or None (expired, signed out or disabled)."""
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
    """Push an active sign-in's end out again (at most once a day), so people who keep coming back stay signed in;
    never past `session_max_days` from when they signed in. True when the cookie should be sent again."""
    now = time.time()
    target = min(now + s.session_days * 86400, sess["created"] + s.session_max_days * 86400)
    if target - sess["expires"] < 86400:
        return False
    db.run("UPDATE sessions SET expires=? WHERE token=?", (target, sess["token"]))
    return True


def end_session(db: DB, token: str | None) -> None:
    if token:
        db.run("DELETE FROM sessions WHERE token=?", (token_hash(token),))


def change_password(db: DB, user_id: str, old: str, new: str) -> None:
    user = db.one("SELECT * FROM users WHERE id=?", (user_id,))
    if not user or not verify_password(old, user["pw"]):
        raise AccountError("Your current password is not right.")
    if len(new) < MIN_PASSWORD:
        raise AccountError(f"Use a password of at least {MIN_PASSWORD} characters.")
    db.run("UPDATE users SET pw=? WHERE id=?", (hash_password(new), user_id))
    db.run("DELETE FROM sessions WHERE user_id=?", (user_id,))  # signed out everywhere else too
