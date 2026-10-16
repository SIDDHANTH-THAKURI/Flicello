"""Sign-in, sign-up and the account page (see ../auth.py for the rules behind them).

Anonymous POSTs (sign-in forms) carry no session to tie a CSRF token to, so they must come from our own origin: a
login form posted from another site (login CSRF) is refused.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .. import auth, mail
from ..accounts import AccountError
from ..service import ActionError, Studio

PENDING = "vs_pending"
INVITE = "vs_invite"
OAUTH = "vs_oauth"
WEBAUTHN = "vs_wa"


def safe_next(url: str | None) -> str:
    """Only paths on this site: never an open redirect."""
    url = (url or "/").strip()
    if not url.startswith("/") or url.startswith("//") or "\\" in url or len(url) > 200:
        return "/"
    return url


def mount(app: FastAPI, studio: Studio, h: SimpleNamespace) -> None:
    s = studio.s
    db = studio.db

    def site(request: Request) -> str:
        return (s.site_url or f"{request.url.scheme}://{request.url.netloc}").rstrip("/")

    def same_origin(request: Request) -> None:
        mine = site(request)
        origin = request.headers.get("origin")
        if origin:
            if origin.rstrip("/") != mine:
                raise HTTPException(403, "This form has to be sent from Flicello itself.")
            return
        referer = request.headers.get("referer", "")
        if not referer.startswith(mine + "/"):
            raise HTTPException(403, "This form has to be sent from Flicello itself.")

    def short_cookie(resp: Response, name: str, value: str, minutes: int) -> None:
        # Lax: Google sends the person back with a top-level GET from its own site
        resp.set_cookie(name, value, max_age=minutes * 60, httponly=True, samesite="lax", secure=s.https, path="/")

    def signed_in(request: Request, user: dict, how: str, next_url: str = "/") -> RedirectResponse:
        token = auth.sign_in(db, s, user, how, h.client_ip(request))
        resp = h.back(safe_next(next_url))
        h.set_cookie(resp, token)
        h.remember_device(request, resp, user["id"])
        resp.delete_cookie(PENDING, path="/")
        return resp

    def to_join(identity_email: str | None, google_sub: str | None, name: str) -> RedirectResponse:
        token = auth.start_pending(db, identity_email, google_sub, name)
        resp = h.back("/join")
        short_cookie(resp, PENDING, token, auth.PENDING_MINUTES)
        return resp

    def may_join(request: Request) -> bool:
        from ..accounts import open_invite

        token = request.cookies.get(INVITE)
        return s.public_signup or bool(token and open_invite(db, token))

    def signin_page(request: Request, mode: str = "signin", **ctx) -> HTMLResponse:
        return h.page(request, "signin.html", None, None, mode=mode, google=auth.google_ready(),
                      email_ok=mail.available(s), public=s.public_signup,
                      next=safe_next(request.query_params.get("next")), **ctx)

    # ------------------------------------------------------------------ pages
    @app.get("/signin", response_class=HTMLResponse)
    def signin(request: Request):
        if h.current(request):
            return h.back(safe_next(request.query_params.get("next")))
        return signin_page(request, "signin")

    @app.get("/signup", response_class=HTMLResponse)
    def signup(request: Request):
        if h.current(request):
            return h.back("/")
        return signin_page(request, "signup")

    # ------------------------------------------------------------------ invites
    @app.get("/join/{token}", response_class=HTMLResponse)
    def invite_page(request: Request, token: str):
        from ..accounts import open_invite

        invite = open_invite(db, token)
        if invite is None:
            raise HTTPException(404, "This invite link has expired or was already used. Ask for a new one.")
        resp = h.page(request, "invite.html", None, None, invite=invite, token=token, google=auth.google_ready(),
                      email_ok=mail.available(s), limits=s.limits, next="/welcome")
        short_cookie(resp, INVITE, token, 60)
        return resp

    # ------------------------------------------------------------------ email code
    @app.post("/auth/email", response_class=HTMLResponse)
    def email_start(request: Request, email: str = Form(""), next: str = Form("/"), mode: str = Form("signin")):
        same_origin(request)
        try:
            address = auth.send_code(db, s, email, h.client_ip(request))
        except (AccountError, mail.MailError) as exc:
            return signin_page(request, mode, error=str(exc), typed=email[:120])
        return h.page(request, "code.html", None, None, email=address, next=safe_next(next), mode=mode,
                      minutes=auth.CODE_MINUTES)

    @app.post("/auth/email/verify", response_class=HTMLResponse)
    def email_verify(request: Request, email: str = Form(""), code: str = Form(""), next: str = Form("/"),
                     mode: str = Form("signin")):
        same_origin(request)
        try:
            address = auth.check_code(db, s, email, code)
        except AccountError as exc:
            return h.page(request, "code.html", None, None, email=email[:254], next=safe_next(next), mode=mode,
                          minutes=auth.CODE_MINUTES, error=str(exc))
        user = auth.find_user(db, address)
        if user:
            try:
                return signed_in(request, user, "email", next)
            except AccountError as exc:
                return signin_page(request, mode, error=str(exc))
        if not may_join(request):
            return signin_page(request, mode, error="There's no Flicello account for that email yet, and new "
                                                    "accounts are by invitation for now.")
        return to_join(address, None, address.split("@")[0])

    # ------------------------------------------------------------------ Google
    @app.get("/auth/google")
    def google_start(request: Request, next: str = "/"):
        if not auth.google_ready():
            return h.back("/signin", "Google sign-in isn't switched on yet.")
        url, state = auth.google_start(db, f"{site(request)}/auth/google/callback", safe_next(next))
        resp = RedirectResponse(url, status_code=303)
        short_cookie(resp, OAUTH, state, auth.OAUTH_MINUTES)
        return resp

    @app.get("/auth/google/callback")
    def google_back(request: Request, state: str = "", code: str = "", error: str = ""):
        if error:
            return h.back("/signin", "Google sign-in was cancelled.")
        try:
            claims, next_url = auth.google_finish(db, f"{site(request)}/auth/google/callback", state, code,
                                                  request.cookies.get(OAUTH))
        except AccountError as exc:
            return h.back("/signin", str(exc))
        email = str(claims["email"]).strip().lower()
        user = auth.find_user(db, email, str(claims["sub"]))
        if user:
            if not user.get("google_sub"):  # an email account meets its Google account: link them
                db.run("UPDATE users SET google_sub=? WHERE id=?", (str(claims["sub"]), user["id"]))
            try:
                resp = signed_in(request, user, "google", next_url)
            except AccountError as exc:
                return h.back("/signin", str(exc))
            resp.delete_cookie(OAUTH, path="/")
            return resp
        if not may_join(request):
            return h.back("/signin", "There's no Flicello account for that Google account yet, and new accounts are "
                                     "by invitation for now.")
        resp = to_join(email, str(claims["sub"]), str(claims.get("name") or email.split("@")[0]))
        resp.delete_cookie(OAUTH, path="/")
        return resp

    # ------------------------------------------------------------------ new account: who you are, 18+, the terms
    @app.get("/join", response_class=HTMLResponse)
    def join_page(request: Request):
        pending = auth.get_pending(db, request.cookies.get(PENDING))
        if pending is None:
            return h.back("/signup", "That sign-up expired. Please start again.")
        return h.page(request, "join_consent.html", None, None, pending=pending, version=auth.LEGAL_VERSION)

    @app.post("/join", response_class=HTMLResponse)
    def join_post(request: Request, display: str = Form(""), adult: str = Form(""), agree: str = Form(""),
                  marketing: str = Form("")):
        same_origin(request)
        token = request.cookies.get(PENDING)
        pending = auth.get_pending(db, token)
        if pending is None:
            return h.back("/signup", "That sign-up expired. Please start again.")
        try:
            user = auth.finish_signup(db, s, token, display, adult == "yes", agree == "yes", marketing == "yes",
                                      h.client_ip(request), request.cookies.get(INVITE))
        except AccountError as exc:
            return h.page(request, "join_consent.html", None, None, pending=pending, version=auth.LEGAL_VERSION,
                          error=str(exc), typed=display[:40])
        resp = signed_in(request, user, "signup", "/welcome")
        resp.delete_cookie(INVITE, path="/")
        return resp

    # ------------------------------------------------------------------ passkeys: sign in
    @app.post("/auth/passkey/options")
    def passkey_options(request: Request):
        same_origin(request)
        options, token = auth.passkey_login_options(db, site(request))
        resp = Response(options, media_type="application/json")
        short_cookie(resp, WEBAUTHN, token, 10)
        return resp

    @app.post("/auth/passkey/verify")
    async def passkey_verify(request: Request):
        same_origin(request)
        body = await request.json()
        try:
            user = auth.passkey_login(db, site(request), request.cookies.get(WEBAUTHN), body.get("credential") or {})
            token = auth.sign_in(db, s, user, "passkey", h.client_ip(request))
        except AccountError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        resp = JSONResponse({"ok": True, "next": safe_next(body.get("next"))})
        h.set_cookie(resp, token)
        h.remember_device(request, resp, user["id"])
        resp.delete_cookie(WEBAUTHN, path="/")
        return resp

    # ------------------------------------------------------------------ the account page
    @app.get("/account", response_class=HTMLResponse)
    def account(request: Request):
        user, sess = h.need_user(request)
        keys = db.all("SELECT id, name, created, last_used FROM passkeys WHERE user_id=? ORDER BY created", (user["id"],))
        sessions = db.all("SELECT token, created, seen, ip FROM sessions WHERE user_id=? AND expires > strftime('%s','now') "
                          "ORDER BY seen DESC", (user["id"],))
        return h.page(request, "account.html", user, sess, passkeys=keys, sessions=sessions,
                      this_session=sess["token"], google=auth.google_ready())

    @app.post("/account/profile")
    def account_profile(request: Request, csrf: str = Form(""), display: str = Form("")):
        user, sess = h.need_user(request)
        h.check_csrf(sess, csrf)
        display = " ".join(display.split())[:40]
        if len(display) < 2:
            return h.back("/account", "Your name needs 2 characters or more.")
        db.run("UPDATE users SET display=? WHERE id=?", (display, user["id"]))
        return h.back("/account", "Saved.")

    @app.post("/account/email", response_class=HTMLResponse)
    def account_email(request: Request, csrf: str = Form(""), email: str = Form("")):
        """Someone who joined with a name and password adds an email, proven with a code, so codes and Google work."""
        user, sess = h.need_user(request)
        h.check_csrf(sess, csrf)
        try:
            address = auth.normal_email(email)
            other = auth.find_user(db, address)
            if other and other["id"] != user["id"]:
                raise auth.AuthError("That email belongs to another Flicello account.")
            auth.send_code(db, s, address, h.client_ip(request))
        except (AccountError, mail.MailError) as exc:
            return h.back("/account", str(exc))
        return h.page(request, "account_email.html", user, sess, email=address, minutes=auth.CODE_MINUTES,
                      active="account")

    @app.post("/account/email/verify", response_class=HTMLResponse)
    def account_email_verify(request: Request, csrf: str = Form(""), email: str = Form(""), code: str = Form("")):
        user, sess = h.need_user(request)
        h.check_csrf(sess, csrf)
        try:
            address = auth.check_code(db, s, email, code)
            other = auth.find_user(db, address)
            if other and other["id"] != user["id"]:
                raise auth.AuthError("That email belongs to another Flicello account.")
        except AccountError as exc:
            return h.page(request, "account_email.html", user, sess, email=email[:254], minutes=auth.CODE_MINUTES,
                          error=str(exc), active="account")
        db.run("UPDATE users SET email=? WHERE id=?", (address, user["id"]))
        auth.audit(db, user["id"], "email_added", "", auth.ip_tag(s, h.client_ip(request)))
        return h.back("/account", "Email added. You can sign in with a code from now on.")

    @app.post("/account/marketing")
    def account_marketing(request: Request, csrf: str = Form(""), on: str = Form("")):
        user, sess = h.need_user(request)
        h.check_csrf(sess, csrf)
        auth.set_marketing(db, s, user["id"], on == "yes", h.client_ip(request))
        return h.back("/account", "Saved: " + ("you'll get the occasional update." if on == "yes" else "no product emails."))

    @app.post("/account/passkeys/options")
    def account_passkey_options(request: Request):
        user, sess = h.need_user(request, api=True)
        h.check_csrf(sess, request.headers.get("x-csrf"))
        options, token = auth.passkey_register_options(db, site(request), user)
        resp = Response(options, media_type="application/json")
        short_cookie(resp, WEBAUTHN, token, 10)
        return resp

    @app.post("/account/passkeys/add")
    async def account_passkey_add(request: Request):
        user, sess = h.need_user(request, api=True)
        h.check_csrf(sess, request.headers.get("x-csrf"))
        body = await request.json()
        try:
            auth.passkey_register(db, site(request), user, request.cookies.get(WEBAUTHN), body.get("credential") or {},
                                  str(body.get("label") or "")[:40])
        except AccountError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        auth.audit(db, user["id"], "passkey_added", "", auth.ip_tag(s, h.client_ip(request)))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(WEBAUTHN, path="/")
        return resp

    @app.post("/account/passkeys/{kid}/delete")
    def account_passkey_delete(request: Request, kid: str, csrf: str = Form("")):
        user, sess = h.need_user(request)
        h.check_csrf(sess, csrf)
        db.run("DELETE FROM passkeys WHERE id=? AND user_id=?", (kid[:600], user["id"]))
        return h.back("/account", "Passkey removed.")

    @app.post("/account/sessions/others")
    def account_sign_out_others(request: Request, csrf: str = Form("")):
        user, sess = h.need_user(request)
        h.check_csrf(sess, csrf)
        db.run("DELETE FROM sessions WHERE user_id=? AND token != ?", (user["id"], sess["token"]))
        return h.back("/account", "Signed out everywhere else.")

    @app.get("/account/export")
    def account_export(request: Request):
        user, _ = h.need_user(request)
        data = json.dumps(studio.export(user), indent=2, default=str)
        auth.audit(db, user["id"], "export", "", auth.ip_tag(s, h.client_ip(request)))
        return Response(data, media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="flicello-my-data.json"',
                                 "Cache-Control": "no-store"})

    @app.post("/account/delete")
    def account_delete(request: Request, csrf: str = Form(""), sure: str = Form("")):
        user, sess = h.need_user(request)
        h.check_csrf(sess, csrf)
        if sure.strip().upper() != "DELETE":
            return h.back("/account", "Type DELETE to confirm.")
        try:
            studio.delete_account(user)
        except ActionError as exc:
            return h.back("/account", str(exc))
        resp = h.back("/", "Your account and everything in it is deleted.")
        resp.delete_cookie("vs_session", path="/")
        return resp
