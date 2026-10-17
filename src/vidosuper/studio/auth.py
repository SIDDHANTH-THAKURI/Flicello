"""Public sign-in and sign-up: email codes, Google, passkeys. No passwords for new accounts.

- Email: a 6-digit code, valid 10 minutes, 5 tries, stored as an HMAC (never in plain text). Sending is limited per
  address and per IP so the form can't be used to spam anyone.
- Google: OpenID Connect with PKCE, a one-time `state` (also in a short cookie) and a `nonce`; the ID token's
  signature is checked against Google's published keys, and its audience, issuer, expiry and nonce.
- Passkeys: WebAuthn (py_webauthn). Registered from the account page; used from the sign-in page.
- New people: the verified identity waits in `pending` until they confirm they're 18+ and accept the Terms and the
  Privacy Policy; only then is the account made. Every consent is recorded with its document version and time.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from urllib.parse import urlencode

from .accounts import AccountError, _limited, new_session
from .db import DB, token_hash
from .settings import StudioSettings

CODE_MINUTES = 10
CODE_TRIES = 5
PENDING_MINUTES = 30
OAUTH_MINUTES = 10
# requested: signed up, waiting for the owner; beta: approved; special: approved with long videos; declined: not now
ACCESS = ("requested", "beta", "special", "declined")
LEGAL_VERSION = "2026-10-17"  # bump when the Terms or the Privacy Policy change materially

EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                   r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
# throwaway inboxes: accounts made with them are almost always abuse of the free allowance
DISPOSABLE = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "sharklasers.com", "10minutemail.com", "tempmail.com",
    "temp-mail.org", "yopmail.com", "trashmail.com", "getnada.com", "dispostable.com", "maildrop.cc", "throwawaymail.com",
    "fakeinbox.com", "mintemail.com", "emailondeck.com", "burnermail.io", "mailnesia.com", "moakt.com", "tempail.com",
    "tempr.email", "discard.email", "mohmal.com", "mytemp.email", "spamgourmet.com", "trbvm.com", "emailfake.com",
}

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_CERTS = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ["https://accounts.google.com", "accounts.google.com"]


class AuthError(AccountError):
    """Something the person can fix or retry (shown as is)."""


def _mac(s: StudioSettings, *parts: str) -> str:
    return hmac.new(s.secret_key(), "\x1f".join(parts).encode("utf-8"), hashlib.sha256).hexdigest()


def ip_tag(s: StudioSettings, ip: str) -> str:
    """A keyed hash of an address: enough to spot abuse and prove where a consent came from, not reversible."""
    return _mac(s, "ip", ip)[:24]


def audit(db: DB, user_id: str | None, action: str, detail: str = "", ip: str = "") -> None:
    db.run("INSERT INTO audit(at, user_id, action, detail, ip) VALUES (?,?,?,?,?)",
           (time.time(), user_id, action[:40], detail[:300], ip[:40]))


def normal_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if len(email) > 254 or not EMAIL.match(email):
        raise AuthError("That doesn't look like an email address.")
    if email.rsplit("@", 1)[1] in DISPOSABLE:
        raise AuthError("Please use an inbox you keep: throwaway addresses can't make accounts.")
    return email


# ---------------------------------------------------------------- email codes

def send_code(db: DB, s: StudioSettings, raw_email: str, ip: str) -> str:
    """Email a fresh code. Returns the normalised address. Says the same whether or not an account exists."""
    from . import mail

    email = normal_email(raw_email)
    if not mail.available(s):
        raise AuthError("Email sign-in isn't switched on yet. Please use Google for now.")
    keys = [f"code:{email}"] + ([] if ip in ("127.0.0.1", "::1", "?") else [f"codeip:{ip}"])
    if _limited(db, keys[:1], 5, 3600) or _limited(db, keys[1:], 12, 3600):
        raise AuthError("We've sent a few codes already. Wait a little, then try again.")
    code = f"{secrets.randbelow(10 ** 6):06d}"
    now = time.time()
    db.run("UPDATE login_codes SET used=? WHERE email=? AND used IS NULL", (now, email))  # one live code at a time
    db.run("INSERT INTO login_codes(email, code, created, expires) VALUES (?,?,?,?)",
           (email, _mac(s, "code", email, code), now, now + CODE_MINUTES * 60))
    for k in keys:
        db.run("INSERT INTO attempts(key, at) VALUES (?,?)", (k, now))
    subject, body, text = mail.code_email(s, code, CODE_MINUTES)
    mail.send(s, email, subject, body, text)
    return email


def check_code(db: DB, s: StudioSettings, raw_email: str, code: str) -> str:
    email = normal_email(raw_email)
    code = re.sub(r"\D", "", code or "")
    row = db.one("SELECT * FROM login_codes WHERE email=? AND used IS NULL ORDER BY id DESC LIMIT 1", (email,))
    if row is None or row["expires"] < time.time():
        raise AuthError("That code has expired. Ask for a new one.")
    if row["tries"] >= CODE_TRIES:
        raise AuthError("Too many wrong tries. Ask for a new code.")
    if len(code) != 6 or not hmac.compare_digest(row["code"], _mac(s, "code", email, code)):
        db.run("UPDATE login_codes SET tries=tries+1 WHERE id=?", (row["id"],))
        left = CODE_TRIES - row["tries"] - 1
        raise AuthError("That code isn't right." + (f" {left} tries left." if left > 0 else " Ask for a new one."))
    db.run("UPDATE login_codes SET used=? WHERE id=?", (time.time(), row["id"]))
    return email


# ---------------------------------------------------------------- who is this?

def find_user(db: DB, email: str | None = None, google_sub: str | None = None) -> dict | None:
    if google_sub:
        user = db.one("SELECT * FROM users WHERE google_sub=?", (google_sub,))
        if user:
            return user
    if email:
        return db.one("SELECT * FROM users WHERE email=?", (email,))
    return None


def start_pending(db: DB, email: str | None, google_sub: str | None = None, name: str = "") -> str:
    token = secrets.token_urlsafe(24)
    now = time.time()
    db.run("DELETE FROM pending WHERE expires < ?", (now,))
    db.run("INSERT INTO pending(token, email, google_sub, name, created, expires) VALUES (?,?,?,?,?,?)",
           (token_hash(token), email, google_sub, name[:60], now, now + PENDING_MINUTES * 60))
    return token


def get_pending(db: DB, token: str | None) -> dict | None:
    if not token or len(token) > 100:
        return None
    row = db.one("SELECT * FROM pending WHERE token=?", (token_hash(token),))
    return row if row and row["expires"] >= time.time() else None


def _handle(db: DB, wanted: str) -> str:
    """A unique `name` for the account (the old login name), from the display name or the email."""
    base = re.sub(r"[^A-Za-z0-9 ._-]", "", wanted).strip(" ._-")[:24] or "friend"
    if len(base) < 2:
        base = (base + "friend")[:24]
    name, n = base, 1
    while db.one("SELECT id FROM users WHERE name=?", (name,)):
        n += 1
        name = f"{base[:24]}-{n}"
    return name


def finish_signup(db: DB, s: StudioSettings, token: str, display: str, adult: bool, agree: bool, marketing: bool,
                  ip: str, invite: str | None = None) -> dict:
    """Make the account. An open invite lets someone join while public sign-up is closed, and gives its role."""
    from .accounts import open_invite

    invited = open_invite(db, invite) if invite else None
    pending = get_pending(db, token)
    if pending is None:
        raise AuthError("This sign-up took too long and expired. Please start again.")
    display = " ".join((display or "").split())[:40]
    if len(display) < 2:
        raise AuthError("Tell us what to call you (2 characters or more).")
    if not adult:
        raise AuthError("Flicello is for people aged 18 and over.")
    if not agree:
        raise AuthError("Please accept the Terms and the Privacy Policy to continue.")
    listed = bool(s.allowed_emails) and on_list(s, pending["email"])
    if not (s.public_signup or s.beta) and invited is None and not listed:
        raise AuthError("New accounts are by invitation for now.")
    if s.allowed_emails and not s.beta and invited is None and not listed:
        raise AuthError("Flicello is open to a few invited people for now. Ask the owner for an invitation.")
    # in the beta, a stranger's account waits for the owner; invited and allow-listed people are in at once
    access = "requested" if s.beta and invited is None and not listed else "beta"
    if find_user(db, pending["email"], pending["google_sub"]):
        raise AuthError("You already have an account: please sign in.")
    if invited is None:
        joined_today = db.value("SELECT COUNT(*) FROM users WHERE created > ? AND joined_by IN ('google', 'email')",
                                (time.time() - 86400,), 0)
        if joined_today >= s.signups_per_day:
            raise AuthError("We've reached today's limit for new accounts. Please try again tomorrow.")
    uid = "u" + secrets.token_hex(6)
    now = time.time()
    joined = "invite" if invited else ("google" if pending["google_sub"] else "email")
    if invited and not db.run("UPDATE invites SET used_by=?, used=? WHERE token=? AND used_by IS NULL",
                              (uid, now, token_hash(invite))):
        raise AuthError("This invite was just used. Ask for a new one.")
    db.run("INSERT INTO users(id, name, role, pw, created, email, google_sub, display, adult, terms, marketing, joined_by,"
           " access, requested) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
           (uid, _handle(db, display or (pending["email"] or "").split("@")[0]), invited["role"] if invited else "friend",
            "", now, pending["email"], pending["google_sub"], display, now, LEGAL_VERSION, 1 if marketing else 0, joined,
            access, now if access == "requested" else None))
    tag = ip_tag(s, ip)
    for kind, granted in (("age_18", True), ("terms", True), ("privacy", True), ("marketing", marketing)):
        db.run("INSERT INTO consents(user_id, kind, version, granted, at, ip) VALUES (?,?,?,?,?,?)",
               (uid, kind, LEGAL_VERSION, 1 if granted else 0, now, tag))
    db.run("DELETE FROM pending WHERE token=?", (token_hash(token),))
    audit(db, uid, "signup", joined, tag)
    return db.one("SELECT * FROM users WHERE id=?", (uid,))


def set_marketing(db: DB, s: StudioSettings, user_id: str, on: bool, ip: str) -> None:
    db.run("UPDATE users SET marketing=? WHERE id=?", (1 if on else 0, user_id))
    db.run("INSERT INTO consents(user_id, kind, version, granted, at, ip) VALUES (?,?,?,?,?,?)",
           (user_id, "marketing", LEGAL_VERSION, 1 if on else 0, time.time(), ip_tag(s, ip)))


def on_list(s: StudioSettings, email: str | None) -> bool:
    """With an allow-list set: is this email on it? (No list: everyone is.)"""
    return not s.allowed_emails or (email or "").strip().lower() in {e.lower() for e in s.allowed_emails}


def may_use(s: StudioSettings, user: dict) -> bool:
    """May this person sign in at all? In the beta everyone may (what they may do is `approved`); otherwise the owner,
    people the owner invited, and those on the allow-list."""
    if s.beta:
        return True
    return user["role"] == "owner" or user.get("joined_by") == "invite" or on_list(s, user.get("email"))


def approved(user: dict) -> bool:
    """May this person make videos? (A signed-in person waiting for approval only sees the request page.)"""
    return user["role"] == "owner" or (user.get("access") or "beta") in ("beta", "special")


def sign_in(db: DB, s: StudioSettings, user: dict, how: str, ip: str) -> str:
    if user["disabled"]:
        raise AuthError("This account is switched off. Contact hello@flicello.com if you think that's a mistake.")
    if not may_use(s, user):
        raise AuthError("Flicello is open to a few invited people for now. Ask the owner for an invitation.")
    db.run("UPDATE users SET last_seen=? WHERE id=?", (time.time(), user["id"]))
    audit(db, user["id"], "signin", how, ip_tag(s, ip))
    return new_session(db, s, user["id"], ip)


# ---------------------------------------------------------------- Google

def google_ready() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def google_start(db: DB, redirect_uri: str, next_url: str = "/") -> tuple[str, str]:
    """The URL to send the person to, and the state (to be kept in a short cookie as well)."""
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    now = time.time()
    db.run("DELETE FROM oauth WHERE created < ?", (now - OAUTH_MINUTES * 60,))
    db.run("INSERT INTO oauth(state, nonce, verifier, created, next) VALUES (?,?,?,?,?)",
           (token_hash(state), nonce, verifier, now, next_url[:200]))
    query = urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": "openid email profile", "state": state, "nonce": nonce, "prompt": "select_account",
        "code_challenge": _b64(hashlib.sha256(verifier.encode("ascii")).digest()), "code_challenge_method": "S256",
    })
    return f"{GOOGLE_AUTH}?{query}", state


_JWKS = None


def _google_keys():
    global _JWKS
    import jwt

    if _JWKS is None:
        _JWKS = jwt.PyJWKClient(GOOGLE_CERTS, cache_keys=True, lifespan=6 * 3600)
    return _JWKS


def google_finish(db: DB, redirect_uri: str, state: str, code: str, cookie_state: str | None,
                  exchange=None, verify=None) -> tuple[dict, str]:
    """(claims, next url) for a verified Google account. `exchange`/`verify` are seams for tests."""
    if not state or not cookie_state or not hmac.compare_digest(state, cookie_state):
        raise AuthError("That sign-in didn't come from this browser. Please try again.")
    row = db.one("SELECT * FROM oauth WHERE state=?", (token_hash(state),))
    db.run("DELETE FROM oauth WHERE state=?", (token_hash(state),))
    if row is None or row["created"] < time.time() - OAUTH_MINUTES * 60:
        raise AuthError("That sign-in took too long. Please try again.")
    if exchange is None:
        import httpx

        def exchange(code: str, verifier: str) -> dict:
            r = httpx.post(GOOGLE_TOKEN, timeout=12, data={
                "code": code, "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"], "redirect_uri": redirect_uri,
                "grant_type": "authorization_code", "code_verifier": verifier})
            if r.status_code != 200:
                raise AuthError("Google didn't confirm the sign-in. Please try again.")
            return r.json()
    tokens = exchange(code, row["verifier"])
    if verify is None:
        import jwt

        def verify(id_token: str) -> dict:
            key = _google_keys().get_signing_key_from_jwt(id_token).key
            return jwt.decode(id_token, key, algorithms=["RS256"], audience=os.environ["GOOGLE_CLIENT_ID"],
                              issuer=GOOGLE_ISSUERS, leeway=60)
    try:
        claims = verify(tokens.get("id_token", ""))
    except AuthError:
        raise
    except Exception as exc:  # noqa: BLE001 - any token problem is the same answer to the person
        raise AuthError("Google's answer couldn't be checked. Please try again.") from exc
    if not hmac.compare_digest(str(claims.get("nonce", "")), row["nonce"]):
        raise AuthError("That sign-in couldn't be checked. Please try again.")
    if not claims.get("email") or not claims.get("email_verified"):
        raise AuthError("Your Google account has no verified email address.")
    return claims, row["next"] or "/"


# ---------------------------------------------------------------- passkeys

def _rp(site: str) -> tuple[str, str]:
    """(rp id, origin) from the site address, e.g. https://flicello.com -> (flicello.com, https://flicello.com)."""
    from urllib.parse import urlparse

    u = urlparse(site)
    return u.hostname or "localhost", f"{u.scheme}://{u.netloc}"


def _challenge(db: DB, challenge: bytes, kind: str, user_id: str | None) -> str:
    token = secrets.token_urlsafe(24)
    now = time.time()
    db.run("DELETE FROM challenges WHERE created < ?", (now - 600,))
    db.run("INSERT INTO challenges(token, challenge, user_id, kind, created) VALUES (?,?,?,?,?)",
           (token_hash(token), challenge, user_id, kind, now))
    return token


def _take_challenge(db: DB, token: str | None, kind: str) -> dict:
    row = db.one("SELECT * FROM challenges WHERE token=? AND kind=?", (token_hash(token or ""), kind))
    db.run("DELETE FROM challenges WHERE token=?", (token_hash(token or ""),))
    if row is None or row["created"] < time.time() - 600:
        raise AuthError("That took too long. Please try again.")
    return row


def passkey_register_options(db: DB, site: str, user: dict) -> tuple[str, str]:
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers import base64url_to_bytes
    from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                          ResidentKeyRequirement, UserVerificationRequirement)

    rp_id, _ = _rp(site)
    have = db.all("SELECT id FROM passkeys WHERE user_id=?", (user["id"],))
    opts = generate_registration_options(
        rp_id=rp_id, rp_name="Flicello", user_id=user["id"].encode("utf-8"),
        user_name=user.get("email") or user["name"], user_display_name=user.get("display") or user["name"],
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,
                                                               user_verification=UserVerificationRequirement.PREFERRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(p["id"])) for p in have],
    )
    return options_to_json(opts), _challenge(db, opts.challenge, "register", user["id"])


def passkey_register(db: DB, site: str, user: dict, token: str | None, credential: dict, label: str = "") -> None:
    from webauthn import verify_registration_response
    from webauthn.helpers import bytes_to_base64url

    row = _take_challenge(db, token, "register")
    if row["user_id"] != user["id"]:
        raise AuthError("That passkey request was for another account.")
    rp_id, origin = _rp(site)
    try:
        v = verify_registration_response(credential=credential, expected_challenge=row["challenge"],
                                         expected_origin=origin, expected_rp_id=rp_id, require_user_verification=False)
    except Exception as exc:  # noqa: BLE001
        raise AuthError("Your device didn't confirm the passkey. Please try again.") from exc
    cid = bytes_to_base64url(v.credential_id)
    transports = json.dumps((credential.get("response") or {}).get("transports") or [])
    db.run("INSERT OR REPLACE INTO passkeys(id, user_id, public_key, sign_count, transports, name, created) "
           "VALUES (?,?,?,?,?,?,?)", (cid, user["id"], v.credential_public_key, v.sign_count, transports,
                                      (label or "Passkey")[:40], time.time()))


def passkey_login_options(db: DB, site: str) -> tuple[str, str]:
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import UserVerificationRequirement

    rp_id, _ = _rp(site)
    opts = generate_authentication_options(rp_id=rp_id, user_verification=UserVerificationRequirement.PREFERRED)
    return options_to_json(opts), _challenge(db, opts.challenge, "login", None)


def passkey_login(db: DB, site: str, token: str | None, credential: dict) -> dict:
    from webauthn import verify_authentication_response

    row = _take_challenge(db, token, "login")
    key = db.one("SELECT * FROM passkeys WHERE id=?", (str(credential.get("id", ""))[:600],))
    if key is None:
        raise AuthError("This passkey isn't registered with Flicello. Sign in another way, then add it.")
    rp_id, origin = _rp(site)
    try:
        v = verify_authentication_response(credential=credential, expected_challenge=row["challenge"],
                                           expected_rp_id=rp_id, expected_origin=origin,
                                           credential_public_key=key["public_key"],
                                           credential_current_sign_count=key["sign_count"],
                                           require_user_verification=False)
    except Exception as exc:  # noqa: BLE001
        raise AuthError("Your device didn't confirm the passkey. Please try again.") from exc
    db.run("UPDATE passkeys SET sign_count=?, last_used=? WHERE id=?", (v.new_sign_count, time.time(), key["id"]))
    user = db.one("SELECT * FROM users WHERE id=?", (key["user_id"],))
    if user is None:
        raise AuthError("This passkey's account no longer exists.")
    return user
