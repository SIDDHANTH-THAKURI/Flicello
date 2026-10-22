"""The studio's web app: pages for people, a small JSON API for uploads and progress, media for videos.

Security in one place:
- every page and API call needs a signed-in session (HttpOnly, SameSite=Strict cookie), except /login,
  /join/<invite> and /healthz; the owner's admin pages also check the role;
- every state change is a POST/PUT/DELETE carrying the session's CSRF token (form field or X-CSRF header);
- every project, upload and file is looked up by id *and* owner; files are served only from the project's
  own folders by plain names (no paths from the client);
- strict security headers (CSP without inline scripts, no framing, no referrer, no MIME sniffing);
- errors show a plain message, never a stack trace.
"""

from __future__ import annotations

import ipaddress
import json
import mimetypes
import re
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import accounts, analytics, auth
from ..brain.context import read_note
from ..profiles import QUESTIONS
from ..service import BUSY, FEEDBACK_TAGS, LENGTHS, STAGES, ActionError, Studio, uses_looks
from ..storage import UploadError, finish_upload, safe_child, start_upload, workdir, write_chunk

HERE = Path(__file__).parent
COOKIE = "vs_session"
mimetypes.add_type("application/manifest+json", ".webmanifest")
DEVICE = "vs_device"  # a browser the person has signed in from before (login throttling)
def note_read(p: dict) -> list[str]:
    """'How I read your note': what was taken from their words, so a miss is visible before anything starts."""
    lines = read_note(p["intent"], p["pace"]) if p["intent"] else []
    if (p.get("cut_note") or "").strip():
        lines.append(f"Cutting your way: {p['cut_note']}")
    if p["kind"] == "montage" and p.get("sound") == "silent":
        lines.append("No sound from your clips" + (": only the music" if p.get("music") != "none" else ""))
    return lines


def eta_text(seconds) -> str:
    """'about 3 min' for the progress card; nothing when unknown."""
    if seconds is None:
        return ""
    s = int(seconds)
    if s < 45:
        return "under a minute"
    if s < 90:
        return "about a minute"
    return f"about {round(s / 60)} min"


DIST = HERE / "static" / "dist"
PUBLIC_PAGES = {"/", "/tour", "/signin", "/legal"}  # the public face: always readable by search and Google's brand check


def indexable(request: Request, public_signup: bool) -> bool:
    """Only signed-out views of the public face (and /signup while anyone can join) may be indexed."""
    if getattr(request.state, "uid", None):
        return False
    path = request.url.path
    return path in PUBLIC_PAGES or path.startswith("/legal/") or (path == "/signup" and public_signup)
_MANIFEST: dict = {"mtime": None, "data": {}}


def _manifest() -> dict:
    """Vite's build manifest (frontend/ -> static/dist), re-read when a new build lands."""
    path = DIST / "manifest.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _MANIFEST["mtime"] != mtime:
        _MANIFEST.update(mtime=mtime, data=json.loads(path.read_text(encoding="utf-8")))
    return _MANIFEST["data"]


def vite_tags(name: str) -> Markup:
    """<link>/<script> tags for a front-end entry (frontend/src/<name>/main.js): its CSS, the CSS of the chunks
    it imports, and the module itself. External files only, so the strict CSP holds."""
    m = _manifest()
    entry = m.get(f"src/{name}/main.js")
    if not entry:
        return Markup(f"<!-- front end '{escape(name)}' not built: cd frontend && npm run build -->")
    css: list[str] = []
    for imp in entry.get("imports", []):  # shared chunks first: the entry's own rules must come last to win
        css += [c for c in m.get(imp, {}).get("css", []) if c not in css]
    css += [c for c in entry.get("css", []) if c not in css]
    tags = [f'<link rel="stylesheet" href="/static/dist/{escape(c)}">' for c in css]
    tags.append(f'<script type="module" src="/static/dist/{escape(entry["file"])}"></script>')
    return Markup("\n  ".join(tags))


MOMENT_ID = re.compile(r"\(?\b[cC]\d{2,3}#\d+\b\)?:?\s*")  # catalog ids like c04#1 mean nothing to people
CSP = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; script-src 'self'; "
       "style-src 'self'; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
       "form-action 'self'; object-src 'none'")


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


from .. import abilities  # noqa: E402 - what people ask for that can't be done yet


def create_app(studio: Studio) -> FastAPI:
    s = studio.s
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals.update(STAGES=STAGES, BUSY=BUSY, QUESTIONS=QUESTIONS, TAGS=FEEDBACK_TAGS, LENGTHS=LENGTHS,
                                 app_name=s.name, vite=vite_tags,
                                 is_public=lambda request: indexable(request, s.public_signup))
    templates.env.filters["fromjson"] = json.loads
    templates.env.filters["ago"] = _ago
    templates.env.filters["mmss"] = lambda x: f"{int(x or 0) // 60}:{int(x or 0) % 60:02d}"
    templates.env.filters["shapes"] = lambda x: "9:16 + 16:9" if x == "both" else x
    templates.env.filters["eta"] = eta_text
    templates.env.filters["ago_s"] = lambda n: f"{int(n)} s" if int(n) < 60 else f"{int(n) // 60} min {int(n) % 60:02d} s" if int(n) < 3600 else f"{int(n) // 3600} h {int(n) % 3600 // 60} min"
    templates.env.filters["span"] = lambda n: f"{int(n)} seconds" if int(n) <= 60 else f"{int(n) / 60:g} min"
    templates.env.filters["dur"] = lambda n: f"{int(n)} s" if int(n) < 60 else (
        f"{int(n) // 60} min" + (f" {int(n) % 60} s" if int(n) % 60 else ""))
    from ..service import IMPROVISE

    templates.env.globals["IMPROVISE"] = IMPROVISE
    templates.env.filters["noids"] = lambda t: MOMENT_ID.sub("", str(t or "")).strip()
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    analytics.ensure(studio.db)

    # ------------------------------------------------------------------ plumbing
    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        if request.method == "GET" and response.status_code == 200 and \
                "text/html" in response.headers.get("content-type", ""):
            try:  # cookieless page-view count (see analytics.py); never in the way of the page
                analytics.record(studio.db, path=request.url.path, ip=client_ip(request),
                                 ua=request.headers.get("user-agent", ""), ref=request.headers.get("referer"),
                                 host=request.url.hostname or "", utm=request.query_params.get("utm_source"),
                                 user_id=getattr(request.state, "uid", None))
            except Exception:  # noqa: BLE001
                pass
        renewed = getattr(request.state, "renew", None)
        if renewed and COOKIE not in response.headers.get("set-cookie", ""):
            response.set_cookie(COOKIE, renewed, max_age=s.session_days * 86400, httponly=True, samesite="lax",
                                secure=s.https, path="/")
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"  # no referrer to other sites; our own forms keep their Origin
        response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"  # talking to Cello
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        public = indexable(request, s.public_signup)
        response.headers["X-Robots-Tag"] = "index, follow" if public else "noindex, nofollow, noarchive"
        if s.https:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.url.path.startswith(("/m/", "/api/")) or "text/html" in response.headers.get("content-type", ""):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(Redirect)
    async def _redirect(request: Request, exc: Redirect):
        return RedirectResponse(exc.url, status_code=303)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        return templates.TemplateResponse(request, "error.html", {"message": exc.detail, "user": None},
                                          status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _crash(request: Request, exc: Exception):
        import logging

        logging.getLogger("vidosuper.studio").exception("request failed: %s", request.url.path)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "Something went wrong on our side."}, status_code=500)
        return templates.TemplateResponse(request, "error.html", {"message": "Something went wrong on our side.",
                                                                  "user": None}, status_code=500)

    proxy_net = ipaddress.ip_network(s.proxy_net) if s.proxy_net else None

    def ours(peer: str) -> bool:
        if peer in ("127.0.0.1", "::1"):
            return True
        try:
            return proxy_net is not None and ipaddress.ip_address(peer) in proxy_net
        except ValueError:
            return False

    def client_ip(request: Request) -> str:
        """The caller's address. Only our own proxy (tailscale serve/funnel on loopback, or Caddy on the server's
        proxy network) is believed about X-Forwarded-For (its last entry: the one the proxy itself added)."""
        peer = request.client.host if request.client else "?"
        if ours(peer):
            forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                return peer
        return peer

    def current(request: Request) -> tuple[dict, dict] | None:
        token = request.cookies.get(COOKIE)
        found = accounts.session_user(studio.db, token)
        if found:
            request.state.uid = found[0]["id"]
        if found and accounts.renew(studio.db, s, found[1]):
            request.state.renew = token  # the middleware sends the cookie again with a fresh lifetime
        return found

    def need_user(request: Request, api: bool = False, gate: bool = True) -> tuple[dict, dict]:
        """The signed-in person; with `gate`, only one who may make videos (in the beta, someone still waiting for
        approval is sent to the request page instead)."""
        found = current(request)
        if found is None:
            if api:
                raise HTTPException(401, "Please sign in again.")
            from urllib.parse import quote

            path = request.url.path if request.method == "GET" else "/"
            raise Redirect("/signin" + (f"?next={quote(path)}" if path not in ("/", "") else ""))
        if gate and not auth.approved(found[0]):
            if api:
                raise HTTPException(403, "Your access is waiting for approval.")
            raise Redirect("/access")
        return found

    def need_owner(request: Request) -> tuple[dict, dict]:
        user, sess = need_user(request)
        if not studio.is_owner(user):
            raise HTTPException(404, "Not found.")
        return user, sess

    def check_csrf(sess: dict, token: str | None) -> None:
        if not token or not secrets.compare_digest(str(token), sess["csrf"]):
            raise HTTPException(403, "This form has expired. Go back, refresh the page and try again.")

    def page(request: Request, name: str, user: dict | None, sess: dict | None, **ctx) -> HTMLResponse:
        flash = request.query_params.get("msg", "")[:200]
        return templates.TemplateResponse(request, name, {"user": user, "csrf": sess["csrf"] if sess else "",
                                                          "is_owner": bool(user and studio.is_owner(user)),
                                                          "flash": flash, **ctx})

    def back(url: str, msg: str = "") -> RedirectResponse:
        from urllib.parse import quote

        return RedirectResponse(url + (f"?msg={quote(msg)}" if msg else ""), status_code=303)

    def set_cookie(resp: Response, token: str) -> None:
        # Lax, not Strict: someone opening a Flicello link from email or a chat app arrives signed in. Every state
        # change still needs the session's CSRF token, and anonymous forms are checked for our own origin.
        resp.set_cookie(COOKIE, token, max_age=s.session_days * 86400, httponly=True, samesite="lax",
                        secure=s.https, path="/")

    def remember_device(request: Request, resp: Response, user_id: str) -> None:
        """Mark this browser as one the person uses (see accounts.login), unless it already is."""
        if accounts._known_device(studio.db, request.cookies.get(DEVICE), user_id):
            return
        resp.set_cookie(DEVICE, accounts.new_device(studio.db, user_id), max_age=365 * 86400, httponly=True,
                        samesite="lax", secure=s.https, path="/")

    from types import SimpleNamespace

    from . import authweb, legalweb

    helpers = SimpleNamespace(page=page, back=back, current=current, need_user=need_user, check_csrf=check_csrf,
                              client_ip=client_ip, set_cookie=set_cookie, remember_device=remember_device,
                              templates=templates)
    authweb.mount(app, studio, helpers)
    legalweb.mount(app, studio, helpers)
    from . import workerapi

    workerapi.mount(app, studio)

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots():
        private = ["/p/", "/m/", "/api/", "/admin", "/account", "/style", "/new", "/welcome", "/join", "/auth/"]
        if not s.public_signup:
            private.append("/signup")
        return "User-agent: *\n" + "".join(f"Disallow: {x}\n" for x in private) + "Allow: /\n"

    # ------------------------------------------------------------------ health (no login, no details)
    @app.get("/healthz")
    def healthz():
        h = studio.health()
        ok = h["worker_seen"] is not None and h["worker_seen"] < 60
        return JSONResponse({"ok": ok}, status_code=200 if ok else 503)

    # ------------------------------------------------------------------ sign in, invites
    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if current(request):
            return back("/")
        if request.query_params.get("legacy") != "1":
            return back("/signin")
        return page(request, "login.html", None, None)

    @app.post("/login")
    def login_post(request: Request, name: str = Form(""), password: str = Form("")):
        try:
            user, token = accounts.login(studio.db, s, name, password, client_ip(request), request.cookies.get(DEVICE))
        except accounts.AccountError as exc:
            return page(request, "login.html", None, None, error=str(exc), typed=name[:40])
        resp = back("/")
        set_cookie(resp, token)
        remember_device(request, resp, user["id"])
        return resp

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form("")):
        found = current(request)
        if found:
            check_csrf(found[1], csrf)
            accounts.end_session(studio.db, request.cookies.get(COOKIE))
        resp = back("/")
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.post("/join/{token}")
    def join_post(request: Request, token: str, name: str = Form(""), password: str = Form(""),
                  password2: str = Form(""), agree: str = Form("")):
        invite = accounts.open_invite(studio.db, token)
        if invite is None:
            raise HTTPException(404, "This invite link has expired or was already used. Ask for a new one.")
        error = None
        if password != password2:
            error = "The two passwords are different."
        elif agree != "yes":
            error = "Please read and tick the privacy note."
        if error is None:
            try:
                user = accounts.register(studio.db, token, name, password)
            except accounts.AccountError as exc:
                error = str(exc)
        if error:
            return page(request, "invite.html", None, None, invite=invite, token=token, limits=s.limits, error=error,
                        typed=name[:40], legacy_open=True, google=False, email_ok=False, next="/welcome")
        resp = back("/welcome")
        set_cookie(resp, accounts.new_session(studio.db, s, user["id"], client_ip(request)))
        remember_device(request, resp, user["id"])
        return resp

    # ------------------------------------------------------------------ the closed beta: asking for access
    @app.get("/access", response_class=HTMLResponse)
    def access_page(request: Request):
        user, sess = need_user(request, gate=False)
        if auth.approved(user):
            return back("/")
        return page(request, "access.html", user, sess, state=user.get("access") or "requested",
                    note=user.get("request_note") or "")

    @app.post("/access")
    def access_post(request: Request, csrf: str = Form(""), note: str = Form("")):
        user, sess = need_user(request, gate=False)
        check_csrf(sess, csrf)
        if auth.approved(user):
            return back("/")
        if (user.get("access") or "") == "declined":
            return back("/access")
        try:
            studio.request_access(user, note)
        except ActionError as exc:
            return page(request, "access.html", user, sess, state="requested", note=note[:600], error=str(exc))
        return back("/access", "Thanks! We'll email you when you're in.")

    @app.get("/welcome", response_class=HTMLResponse)
    def welcome(request: Request):
        user, sess = need_user(request)
        return page(request, "welcome.html", user, sess, profile=studio.profile(user))

    @app.post("/welcome")
    async def welcome_post(request: Request):
        user, sess = need_user(request)
        form = await request.form()
        check_csrf(sess, form.get("csrf"))
        profile = studio.profile(user)
        profile.set_answers({q: str(form.get(q, "")) for q, _, _ in QUESTIONS}, str(form.get("never", "")),
                            {q: str(form.get(f"{q}_other", "")) for q, _, _ in QUESTIONS})
        profile.save()
        studio.db.run("UPDATE users SET onboarded=1 WHERE id=?", (user["id"],))
        if form.get("next") == "/style":
            return back("/style", "Saved.")
        return back("/", "Thanks! Your style is saved; you can change it any time.")

    # ------------------------------------------------------------------ home and projects
    def landing(request: Request) -> HTMLResponse:
        site = f"{request.url.scheme}://{request.url.netloc}"
        return templates.TemplateResponse(request, "landing.html", {"site_url": site, "year": time.gmtime().tm_year,
                                                                    "beta": s.beta})

    @app.get("/tour", response_class=HTMLResponse)
    def tour(request: Request):
        return landing(request)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        if current(request) is None:
            return landing(request)
        user, sess = need_user(request)
        if not user["onboarded"] and not studio.is_owner(user):
            raise Redirect("/welcome")
        projects = studio.projects(user)
        thumbs = {}
        for p in projects:  # its story's still, else its finished film's, else its first clip's: footage, not a box
            still = studio.db.one("SELECT stills FROM pitches WHERE project_id=? ORDER BY (id=?) DESC, id LIMIT 1",
                                  (p["id"], p["chosen"] or 0))
            names = json.loads(still["stills"]) if still else []
            thumbs[p["id"]] = f"stills/{names[0]}" if names else None
            if thumbs[p["id"]] is None:
                work = workdir(s, p["user_id"], p["id"])
                film = studio.db.one("SELECT file FROM renders WHERE project_id=? AND kind='final' ORDER BY id DESC LIMIT 1",
                                     (p["id"],))
                clip = studio.db.one("SELECT id FROM clips WHERE project_id=? ORDER BY created LIMIT 1", (p["id"],))
                for rel in ([f"out/{Path(film['file']).with_suffix('.jpg').name}"] if film else []) + \
                        ([f"stills/clip-{clip['id']}.jpg"] if clip else []):
                    if (work.root / rel).exists():
                        thumbs[p["id"]] = rel
                        break
        return page(request, "home.html", user, sess, projects=projects, thumbs=thumbs, credits=studio.credits(user))

    def new_ctx(user: dict) -> dict:
        lengths = studio.lengths_for(user)
        return {"lengths": lengths, "more_lengths": max(lengths) < max(LENGTHS), "credits": studio.credits(user),
                "pricing": studio.pricing()}

    @app.get("/new", response_class=HTMLResponse)
    def new_page(request: Request):
        user, sess = need_user(request)
        answers = studio.profile(user).answers
        pace = answers.get("pace", "balanced")  # their style's pace ("their own words" is a rule)
        from ..exports import PLATFORMS

        shape = PLATFORMS.get(answers.get("platform", ""), {}).get("shape")  # "make it for YouTube", remembered
        return page(request, "new.html", user, sess, default_pace=pace if pace in ("calm", "fast") else "balanced",
                    form={"shapes": shape} if shape else {}, **new_ctx(user))

    @app.post("/new")
    def new_post(request: Request, csrf: str = Form(""), title: str = Form(""), shapes: str = Form("9:16"),
                 length: str = Form("30"), length_custom: str = Form(""), length_unit: str = Form("s"),
                 intent: str = Form(""), pace: str = Form("balanced"), kind: str = Form("montage"),
                 style: str = Form("designed"), cut_note: str = Form(""), improvise: int = Form(35),
                 describe: str = Form("")):
        user, sess = need_user(request)
        check_csrf(sess, csrf)
        sent = {"kind": kind, "describe": describe, "title": title, "style": style, "shapes": shapes, "length": length,
                "length_custom": length_custom, "length_unit": length_unit, "pace": pace, "cut_note": cut_note,
                "improvise": improvise, "intent": intent}  # shown again if something needs fixing: nothing is lost
        try:
            try:  # their own length, in seconds or minutes, or one of the chips
                seconds = (float(length_custom.replace(",", ".")) * (60 if length_unit == "min" else 1)
                           if length == "custom" else float(length or 30))
            except ValueError:
                raise ActionError("Type the length as a number.") from None
            project = studio.create_project(user, title, shapes, seconds, intent, pace, kind, style, cut_note,
                                            improvise, describe)
        except ActionError as exc:
            return page(request, "new.html", user, sess, error=str(exc), default_pace=sent["pace"], form=sent,
                        **new_ctx(user))
        return back(f"/p/{project['id']}")

    def owned(user: dict, pid: str) -> dict:
        try:
            return studio.owned(user, pid)
        except ActionError as exc:
            raise HTTPException(404, str(exc)) from exc

    EDITOR_ASKS = {  # what the editor's message box suggests at each step
        "setup": "What's it for, where will you post it, what must be in it?",
        "understood": "Anything I got wrong? Tell me here, or correct it below.",
        "choose": "Like one? Want something else? e.g. 'the second, but end on the bridge'",
        "review": "What should change in the sample?",
        "done": "What should change? e.g. 'shot 3 out, shot 7 two beats longer, nobody in shot'",
        "confirm": "Did I read it right? Tell me what to fix.",
    }

    def editor_view(p: dict, user: dict) -> dict:
        """The editor panel's data: the conversation, the film's spec and the checks of what is on screen."""
        from .. import agent
        from .. import spec as specs

        msgs = agent.thread(studio.db, p["id"])
        checks = (json.loads(p["info"]) if p.get("info") else {}).get("checks") or {}
        if p["stage"] in ("done", "confirm", "revising", "reading", "rendering") and p["version"]:
            results = checks.get(str(studio.work_version(p))) or []
        elif p["stage"] in ("review", "sampling"):
            results = checks.get("sample") or []
        else:
            results = []
        by_item: dict[str, dict] = {}
        for r in results:
            for iid in r.get("items") or []:
                by_item.setdefault(iid, r)
        from .. import faces

        index = studio.people_of(p)
        return {"people": faces.public(index), "people_looked": index is not None,
                "editor_thread": msgs, "editor_thinking": agent.thinking(msgs), "spec_items": studio.spec_items(p, user),
                "item_checks": by_item, "brief_checks": specs.summary(results),
                "editor_placeholder": EDITOR_ASKS.get(p["stage"], "Tell me anything: I keep it for the next step."),
                "editable_spec": p["stage"] not in BUSY}

    @app.get("/p/{pid}", response_class=HTMLResponse)
    def project_page(request: Request, pid: str):
        user, sess = need_user(request)
        p = owned(user, pid)
        job = studio.db.active_job(pid)
        clips = studio.db.all("SELECT * FROM clips WHERE project_id=? ORDER BY created", (pid,))
        clip_thumbs = set()
        if p["stage"] == "setup" and clips:  # a frame per clip (made at upload; older clips get theirs in the background)
            from ..storage import clip_thumb
            work = workdir(s, p["user_id"], pid)
            missing = []
            for c in clips:
                if (work.stills / f"clip-{c['id']}.jpg").exists():
                    clip_thumbs.add(c["id"])
                elif (work.clips / c["file"]).exists():
                    missing.append(c)
            if missing:
                import threading
                threading.Thread(target=lambda: [clip_thumb(work.clips / c["file"], work.stills, c["id"], c["seconds"] or 0)
                                                 for c in missing[:20]], daemon=True).start()
        work = workdir(s, p["user_id"], pid)
        videos = sorted(work.out.glob("*.mp4")) if work.out.exists() else []
        posters = {v.with_suffix(".jpg").name for v in videos if v.with_suffix(".jpg").exists()}
        if len(posters) < len(videos):  # older videos get their still in the background (new ones have it already)
            import threading

            from ..pipeline import poster_for
            todo = [v for v in videos if v.with_suffix(".jpg").name not in posters][:12]
            threading.Thread(target=lambda: [poster_for(v) for v in todo], daemon=True).start()
        pitches = studio.pitches(p)
        samples = studio.renders(p, "sample")
        finals = studio.renders(p, "final")
        work_v = studio.work_version(p) if finals else 0  # the version they work on (the newest, or one they picked)
        latest = sorted([r for r in finals if r["version"] == work_v], key=lambda r: r["shape"] != "9:16")
        older = [r for r in finals if r["version"] != p["version"]]
        feedback = studio.db.one("SELECT * FROM feedback WHERE project_id=? AND stage='final' AND version=? "
                                 "ORDER BY id DESC LIMIT 1", (pid, work_v))
        strip = studio.strip_of(p, work_v) if work_v and p["kind"] == "montage" else None
        if strip is None and work_v and p["kind"] == "montage" and p["stage"] == "done" and not job:
            if studio.number_shots(p, work_v):  # made before shots were numbered: a few seconds, no AI
                job = studio.db.active_job(pid)
        versions_all = studio.versions_list(p) if finals else []
        work = next((v for v in versions_all if v["version"] == work_v), None)
        pending = studio._info(p).get("pending") if p["stage"] == "confirm" else None
        draft = studio.draft_of(p) if p["stage"] == "done" else None
        from ..storage import earlier_clips
        earlier = earlier_clips(studio.db, s, p) if p["stage"] == "setup" and p["kind"] == "montage" else []
        return page(request, "project.html", user, sess, p=p, job=job, queued_note=studio.queued_note(job), clips=clips,
                    clip_thumbs=clip_thumbs, posters=posters, earlier=earlier,
                    joblog=steps_of(job or (studio.db.one("SELECT * FROM jobs WHERE project_id=? ORDER BY id DESC LIMIT 1",
                                                          (pid,)) if p["stage"] == "failed" else None)),
                    pitches=pitches, looks=uses_looks(p), look_shape=("9:16" if p["shapes"] != "16:9" else "16:9"),
                    backs=studio.back_targets(p), understood=studio.understanding(p), tier=studio.tier(user),
                    beats=studio.beats(p), beats_left=studio.beats_left(p), track_seconds=studio.track_seconds(p),
                    can_compose=studio.can_compose(), cost=studio.film_cost(p), credits=studio.credits(user),
                    names=clip_names(p) if p["stage"] == "understood" else {},
                    lengths=studio.lengths_for(user),
                    eta=studio.eta(job), total_eta=studio.total_estimate(p["kind"]),
                    render_eta=studio.typical_seconds("render"),
                    note_lines=note_read(p),
                    sample=samples[0] if samples else None, latest=latest, older=older, rated=feedback,
                    work=work, strip=strip, versions_all=versions_all, pending=pending, draft=draft,
                    style_cards=studio.style_cards(p) if p["stage"] == "done" and p["kind"] == "montage" else [],
                    ready=studio.ready_of(p) if p["stage"] == "done" else None,
                    exports=studio.exports_of(p) if p["stage"] == "done" else [],
                    review_links=[dict(r) for r in studio.db.all("SELECT token, version FROM review_links WHERE project_id=? "
                                                                 "AND revoked=0 ORDER BY version DESC", (pid,))],
                    review_notes=studio.review_notes(p) if p["stage"] in ("done", "confirm") else [],
                    site_url=(s.site_url or "").rstrip("/"),
                    drafting=bool(job and job["kind"] == "draft"),
                    shot_times=json.dumps([[c["start"], c["n"]] for c in strip["cards"]]) if strip else "",
                    board=studio.board(p, samples[0]["plan_id"]) if samples and p["kind"] == "montage" else None,
                    gaps=abilities.for_project(studio.db, p),
                    info=json.loads(p["info"]) if p["info"] else None,
                    revisions_left=None if studio.is_owner(user) else max(0, s.limits.revisions_per_video - p["revisions"]),
                    ideas_left=studio.ideas_left(p), sample_tries_left=studio.sample_tries_left(p),
                    polish_left=studio.polish_left(p) if p["stage"] == "understood" else 0,
                    chunk=s.limits.chunk_bytes, brief_open=request.query_params.get("open") == "brief",
                    **editor_view(p, user))

    def act(request: Request, csrf: str, pid: str, fn) -> RedirectResponse:
        user, sess = need_user(request)
        check_csrf(sess, csrf)
        p = owned(user, pid)
        try:
            fn(user, p)
        except (ActionError, UploadError) as exc:
            return back(f"/p/{pid}", str(exc))
        return back(f"/p/{pid}")

    @app.post("/p/{pid}/extend")
    async def extend(request: Request, pid: str, csrf: str = Form("")):
        user, sess = need_user(request)
        check_csrf(sess, csrf)
        p = owned(user, pid)
        try:
            info = await run_in_threadpool(studio.extend_song, p)
        except ActionError as exc:
            return back(f"/p/{pid}", str(exc))
        return back(f"/p/{pid}", f"Done: the song now runs {info['seconds']:.0f} s (it repeats its strongest {info['loop_bars']} bars).")

    @app.post("/p/{pid}/settings")
    def settings_edit(request: Request, pid: str, csrf: str = Form(""), shapes: str = Form("9:16"),
                      length: str = Form(""), length_custom: str = Form(""), length_unit: str = Form("s")):
        said: list[str] = []

        def go(u, p):
            seconds = None
            try:
                if length == "custom":
                    seconds = float(length_custom.replace(",", ".")) * (60 if length_unit == "min" else 1)
                elif length:
                    seconds = float(length)
            except ValueError:
                raise ActionError("Type the length as a number.") from None
            said.append(studio.set_settings(p, shapes, seconds))
        done = act(request, csrf, pid, go)
        return back(f"/p/{pid}", said[0]) if said else done

    @app.post("/p/{pid}/note")
    def add_note(request: Request, pid: str, csrf: str = Form(""), note: str = Form("")):
        return act(request, csrf, pid, lambda u, p: studio.add_note(p, note))

    @app.post("/p/{pid}/understood/polish")
    async def understood_polish(request: Request, pid: str):
        form = await request.form()
        data = {k: form.get(k) for k in form.keys()}
        return await run_in_threadpool(act, request, str(form.get("csrf", "")), pid,
                                       lambda u, p: studio.polish_understanding(p, data))

    @app.post("/p/{pid}/understood/undo")
    def understood_undo(request: Request, pid: str, csrf: str = Form("")):
        return act(request, csrf, pid, lambda u, p: studio.undo_understanding(p))

    @app.post("/p/{pid}/music")
    def music(request: Request, pid: str, csrf: str = Form(""), choice: str = Form("made")):
        return act(request, csrf, pid, lambda u, p: studio.set_music(p, choice))

    @app.post("/p/{pid}/beat")
    def beat(request: Request, pid: str, csrf: str = Form(""), ask: str = Form(""), base: int = Form(0),
             change: str = Form("")):
        return act(request, csrf, pid, lambda u, p: studio.make_beat(p, ask, base, change))

    @app.post("/p/{pid}/beat/use")
    def beat_use(request: Request, pid: str, csrf: str = Form(""), beat: int = Form(0)):
        return act(request, csrf, pid, lambda u, p: studio.use_beat(p, beat))

    @app.post("/p/{pid}/sound")
    def sound(request: Request, pid: str, csrf: str = Form(""), sound: str = Form("natural")):
        return act(request, csrf, pid, lambda u, p: studio.set_sound(p, sound))

    @app.post("/p/{pid}/kind")
    def kind(request: Request, pid: str, csrf: str = Form(""), kind: str = Form("montage")):
        return act(request, csrf, pid, lambda u, p: studio.set_kind(p, kind))

    @app.post("/p/{pid}/length")
    def length(request: Request, pid: str, csrf: str = Form(""), seconds: int = Form(30)):
        return act(request, csrf, pid, lambda u, p: studio.set_length(p, seconds))

    @app.post("/p/{pid}/start")
    def start(request: Request, pid: str, csrf: str = Form("")):
        return act(request, csrf, pid, lambda u, p: studio.start(p))

    @app.post("/p/{pid}/pick")
    def pick(request: Request, pid: str, csrf: str = Form(""), pitch: int = Form(0)):
        return act(request, csrf, pid, lambda u, p: studio.choose(p, pitch))

    @app.post("/p/{pid}/another")
    def another(request: Request, pid: str, csrf: str = Form(""), reason: str = Form(""), own: str = Form(""),
                base: int = Form(0), improvise: int = Form(-1)):
        return act(request, csrf, pid, lambda u, p: studio.another(p, reason, own, base,
                                                                    improvise if 0 <= improvise <= 100 else None))

    @app.post("/p/{pid}/understood")
    async def understood(request: Request, pid: str):
        form = await request.form()
        data = {k: form.get(k) for k in form.keys()}
        data["leave_out"] = form.getlist("leave_out")
        return act(request, str(form.get("csrf", "")), pid, lambda u, p: studio.confirm_understanding(p, data))

    def clip_names(p: dict) -> dict[str, str]:
        """c01 -> the file name they uploaded, from the clip log (when it has arrived from the renderer)."""
        try:
            cat = json.loads((workdir(s, p["user_id"], p["id"]).root / "catalog.json").read_text(encoding="utf-8"))
            return {e["short"]: Path(str(e["clip"].get("path") or e["clip"].get("name") or "")).name for e in cat["clips"]}
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    @app.post("/p/{pid}/back")
    def go_back(request: Request, pid: str, csrf: str = Form(""), to: str = Form("setup")):
        return act(request, csrf, pid, lambda u, p: studio.go_back(p, to))

    @app.post("/p/{pid}/stop")
    def stop(request: Request, pid: str, csrf: str = Form("")):
        return act(request, csrf, pid, lambda u, p: studio.stop(p))

    @app.post("/p/{pid}/gap")
    def gap(request: Request, pid: str, csrf: str = Form(""), ability: str = Form("")):
        return act(request, csrf, pid, lambda u, p: studio.want_gap(p, ability))

    @app.post("/admin/gap")
    def admin_gap(request: Request, csrf: str = Form(""), ability: str = Form("")):
        user, sess = need_owner(request)
        check_csrf(sess, csrf)
        n = studio.tell_gap(ability)
        return back("/admin#gaps", f"Told {n} {'person' if n == 1 else 'people'} it's ready.")

    @app.post("/p/{pid}/board")
    async def board_edit(request: Request, pid: str):
        form = await request.form()
        data = {k: form.get(k) for k in form.keys()}
        return act(request, str(form.get("csrf", "")), pid, lambda u, p: studio.edit_board(p, data))

    @app.post("/p/{pid}/sample")
    def sample(request: Request, pid: str, csrf: str = Form(""), verdict: str = Form(""), note: str = Form("")):
        if verdict == "ideas":
            return act(request, csrf, pid, lambda u, p: studio.back_to_ideas(p))
        return act(request, csrf, pid, lambda u, p: studio.sample_verdict(u, p, verdict == "ok", note))

    @app.post("/p/{pid}/rate")
    async def rate(request: Request, pid: str):
        form = await request.form()

        def go(u, p):
            try:
                match, liked = int(form.get("match", 0)), int(form.get("liked", 0))
            except ValueError as exc:
                raise ActionError("Pick a score for both questions.") from exc
            if not (1 <= match <= 5 and 1 <= liked <= 10):
                raise ActionError("Pick a score for both questions.")
            studio.rate(u, p, match, liked, [str(t) for t in form.getlist("tags")], str(form.get("text", "")),
                        str(form.get("fix", "")))

        return await run_in_threadpool(act, request, str(form.get("csrf", "")), pid, go)

    @app.post("/p/{pid}/change")
    def change(request: Request, pid: str, csrf: str = Form(""), text: str = Form("")):
        """What they want changed, in their words: read into exact changes, shown before anything is made."""
        return act(request, csrf, pid, lambda u, p: studio.ask_change(u, p, text))

    @app.post("/p/{pid}/change/confirm")
    async def change_confirm(request: Request, pid: str):
        form = await request.form()
        picks = [int(x) for x in form.getlist("pick") if str(x).isdigit()] if form.get("picked") == "1" else None
        return await run_in_threadpool(act, request, str(form.get("csrf", "")), pid,
                                       lambda u, p: studio.confirm_change(u, p, picks))

    @app.post("/p/{pid}/change/cancel")
    def change_cancel(request: Request, pid: str, csrf: str = Form("")):
        return act(request, csrf, pid, lambda u, p: studio.cancel_change(p))

    @app.post("/p/{pid}/shots")
    async def shots_edit(request: Request, pid: str):
        """Changes picked on the numbered shots (exact: no AI reads them)."""
        form = await request.form()
        data = {k: form.get(k) for k in form.keys()}
        return await run_in_threadpool(act, request, str(form.get("csrf", "")), pid,
                                       lambda u, p: studio.direct_change(u, p, data))

    @app.post("/p/{pid}/continue")
    def continue_version(request: Request, pid: str, csrf: str = Form(""), version: int = Form(0)):
        """Work on an earlier version: the next change starts from it."""
        return act(request, csrf, pid, lambda u, p: studio.continue_from(p, version))

    # ------------------------------------------------------------------ your editor (agent.py)
    @app.post("/p/{pid}/talk")
    def talk(request: Request, pid: str, csrf: str = Form(""), text: str = Form(""), refs: str = Form("")):
        """Their message to their editor: kept at once; the reply lands in the panel a few seconds later."""
        user, sess = need_user(request)
        check_csrf(sess, csrf)
        p = owned(user, pid)
        as_json = "application/json" in request.headers.get("accept", "")
        try:
            mid = studio.talk(user, p, text, [x for x in refs.split("|") if x.strip()])
        except ActionError as exc:
            if as_json:
                return JSONResponse({"detail": str(exc)}, status_code=400)
            return back(f"/p/{pid}", str(exc))
        if as_json:
            return {"id": mid}
        return RedirectResponse(f"/p/{pid}#editor", status_code=303)

    @app.get("/p/{pid}/editor", response_class=HTMLResponse)
    def editor_panel(request: Request, pid: str):
        """The editor panel alone, for the page to swap in when a reply lands."""
        user, sess = need_user(request, api=True)
        p = owned(user, pid)
        return templates.TemplateResponse(request, "_editor.html", {"user": user, "csrf": sess["csrf"], "p": p,
                                                                    **editor_view(p, user)})

    # ------------------------------------------------------------------ drafts and the instant preview
    @app.get("/api/p/{pid}/preview")
    def preview_data(request: Request, pid: str, which: str = "draft"):
        """What the browser player needs to play a version or the draft at once (see preview.py)."""
        user, _ = need_user(request, api=True)
        p = owned(user, pid)
        try:
            return studio.preview_of(p, which)
        except ActionError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/p/{pid}/draft/make")
    def draft_make(request: Request, pid: str, csrf: str = Form("")):
        """The draft on screen becomes a real version (rendered in full)."""
        return act(request, csrf, pid, lambda u, p: studio.make_draft(u, p))

    # ------------------------------------------------------------------ review links (no account needed to watch)
    def review_film(token: str):
        found = studio.review_by_token(token)
        if not found:
            raise HTTPException(404, "This review link isn't open.")
        return found

    def review_video(link: dict, p: dict):
        work = workdir(s, p["user_id"], p["id"])
        shape = studio.shapes(p)[0].replace(":", "x")
        path = work.out / f"v{link['version']}-{shape}.mp4"
        if not path.exists():
            found = sorted(work.out.glob(f"v{link['version']}-*x*.mp4"))
            path = next((f for f in found if f.stem.count("-") == 1), path)
        return path

    @app.get("/r/{token}", response_class=HTMLResponse)
    def review_page(request: Request, token: str):
        link, p = review_film(token)
        resp = page(request, "review.html", None, None, token=token, title=p["title"], version=link["version"],
                    tall=studio.shapes(p)[0] == "9:16",
                    notes=[n for n in studio.review_notes(p) if n["token"] == token])
        resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return resp

    @app.get("/r/{token}/video")
    def review_file(token: str):
        link, p = review_film(token)
        path = review_video(link, p)
        if not path.exists():
            raise HTTPException(404, "Not found.")
        return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=600"})

    @app.post("/r/{token}/note")
    def review_note(request: Request, token: str, at: float = Form(0.0), name: str = Form(""), text: str = Form("")):
        from urllib.parse import urlparse

        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.url.netloc:  # only our own review page posts here
            raise HTTPException(403, "Not from this site.")
        try:
            studio.add_review_note(token, at, name, text)
        except ActionError as exc:
            return back(f"/r/{token}", str(exc))
        return back(f"/r/{token}", "Thanks: your note is with them.")

    @app.post("/p/{pid}/review")
    def review_make(request: Request, pid: str, csrf: str = Form("")):
        return act(request, csrf, pid, lambda u, p: studio.review_link(u, p))

    @app.post("/p/{pid}/review/revoke")
    def review_revoke(request: Request, pid: str, csrf: str = Form(""), token: str = Form("")):
        return act(request, csrf, pid, lambda u, p: studio.revoke_review(p, token))

    @app.post("/p/{pid}/notes/{note_id}/done")
    def review_done(request: Request, pid: str, note_id: int, csrf: str = Form("")):
        return act(request, csrf, pid, lambda u, p: studio.settle_note(p, note_id))

    @app.get("/api/p/{pid}/timeline")
    def timeline_api(request: Request, pid: str, which: str = "draft"):
        user, _ = need_user(request, api=True)
        p = owned(user, pid)
        try:
            return JSONResponse(studio.timeline_of(p, which))
        except ActionError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.post("/api/p/{pid}/edit")
    async def timeline_edit(request: Request, pid: str):
        """Changes made by hand on the timeline (JSON: ops, said), as a draft."""
        user, sess = need_user(request, api=True)
        check_csrf(sess, request.headers.get("x-csrf"))
        p = owned(user, pid)
        body = await request.json()
        try:
            studio.manual_edit(user, p, list(body.get("ops") or []), list(body.get("said") or []))
        except (ActionError, ValueError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        return JSONResponse({"ok": True})

    @app.post("/p/{pid}/export")
    def export_make(request: Request, pid: str, csrf: str = Form(""), kind: str = Form("")):
        return act(request, csrf, pid, lambda u, p: studio.ask_export(u, p, kind))

    @app.post("/p/{pid}/fix")
    def ready_fix(request: Request, pid: str, csrf: str = Form(""), part: str = Form("")):
        """A weak part of the ready-to-post score, fixed as a draft."""
        return act(request, csrf, pid, lambda u, p: studio.fix_ready(u, p, part))

    @app.post("/p/{pid}/style")
    def style_try(request: Request, pid: str, csrf: str = Form(""), style: str = Form("")):
        """Their film in an edit style, as a draft to watch at once."""
        return act(request, csrf, pid, lambda u, p: studio.try_style(u, p, style))

    @app.post("/p/{pid}/draft/discard")
    def draft_discard(request: Request, pid: str, csrf: str = Form("")):
        return act(request, csrf, pid, lambda u, p: studio.discard_draft(p))

    @app.post("/p/{pid}/people")
    def people_edit(request: Request, pid: str, csrf: str = Form(""), person: str = Form(""), op: str = Form(""),
                    name: str = Form("")):
        """Someone in the footage: this is me, their name, never show them, or cut the shots they're in."""
        def go(u, p):
            if op == "cut":
                studio.person_draft(u, p, person)
            elif op == "look":
                studio.find_people(p)
            else:
                studio.set_person(u, p, person, op, name)
        return act(request, csrf, pid, go)

    @app.post("/p/{pid}/card")
    def card(request: Request, pid: str, csrf: str = Form(""), mid: int = Form(0), k: int = Form(0),
             choice: str = Form("")):
        """A card in the conversation tapped: an answer, a step to run now, a preference to keep."""
        return act(request, csrf, pid, lambda u, p: studio.answer_card(u, p, mid, k, choice))

    @app.post("/p/{pid}/spec")
    def spec_edit(request: Request, pid: str, csrf: str = Form(""), item: str = Form(""), op: str = Form("")):
        """An item of the film's brief switched on or off, or one they added removed."""
        user, sess = need_user(request)
        check_csrf(sess, csrf)
        p = owned(user, pid)
        try:
            studio.spec_change(user, p, item, op)
        except ActionError as exc:
            return back(f"/p/{pid}", str(exc))
        return RedirectResponse(f"/p/{pid}?open=brief#brief", status_code=303)

    @app.post("/p/{pid}/report")
    def report(request: Request, pid: str, csrf: str = Form(""), note: str = Form("")):
        user, sess = need_user(request)
        check_csrf(sess, csrf)
        p = owned(user, pid)
        try:
            studio.report_problem(user, p, note)
        except ActionError as exc:
            return back(f"/p/{pid}", str(exc))
        return back(f"/p/{pid}", "Thanks, we've got it. We'll look into it and fix it.")

    @app.post("/admin/report")
    def admin_report(request: Request, csrf: str = Form(""), rid: int = Form(0)):
        user, sess = need_owner(request)
        check_csrf(sess, csrf)
        studio.db.run("UPDATE reports SET resolved=? WHERE id=?", (time.time(), rid))
        return back("/admin#reports", "Marked as sorted.")

    @app.post("/p/{pid}/retry")
    def retry(request: Request, pid: str, csrf: str = Form("")):
        return act(request, csrf, pid, lambda u, p: studio.retry(p))

    @app.post("/p/{pid}/delete")
    def delete(request: Request, pid: str, csrf: str = Form(""), sure: str = Form("")):
        user, sess = need_user(request)
        check_csrf(sess, csrf)
        p = owned(user, pid)
        if sure != "yes":
            return back(f"/p/{pid}", "Tick the box to confirm.")
        try:
            studio.delete_project(p)
        except ActionError as exc:
            return back(f"/p/{pid}", str(exc))
        return back("/", "Deleted, with all its files.")

    # ------------------------------------------------------------------ uploads (JSON)
    def api_user(request: Request) -> tuple[dict, dict]:
        user, sess = need_user(request, api=True)
        check_csrf(sess, request.headers.get("x-csrf"))
        return user, sess

    @app.post("/api/p/{pid}/uploads")
    async def upload_start(request: Request, pid: str):
        user, _ = api_user(request)
        p = owned(user, pid)
        if p["stage"] != "setup":
            raise HTTPException(409, "Clips can only be added before the video is started.")
        try:
            body = await request.json()
            info = await run_in_threadpool(start_upload, studio.db, s, user, p, str(body.get("kind", "")),
                                           str(body.get("name", ""))[:200], int(body.get("size", 0)))
        except (UploadError, ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return info

    def _upload(user: dict, uid: str) -> dict:
        up = studio.db.one("SELECT * FROM uploads WHERE id=? AND user_id=?", (uid, user["id"]))
        if up is None:
            raise HTTPException(404, "No such upload.")
        return up

    @app.get("/api/uploads/{uid}")
    def upload_status(request: Request, uid: str):
        user, _ = need_user(request, api=True)
        up = _upload(user, uid)
        return {"received": up["received"], "size": up["size"], "status": up["status"]}

    @app.put("/api/uploads/{uid}")
    async def upload_chunk(request: Request, uid: str, offset: int = 0):
        user, _ = api_user(request)
        up = _upload(user, uid)
        limit = s.limits.chunk_bytes
        if int(request.headers.get("content-length") or 0) > limit:
            raise HTTPException(413, "Chunk too large.")
        data = bytearray()
        try:
            async for piece in request.stream():
                data.extend(piece)
                if len(data) > limit:
                    raise HTTPException(413, "Chunk too large.")
        except ClientDisconnect:  # the phone lost signal mid-chunk: nothing written, the page sends it again
            return JSONResponse({"detail": "Connection dropped; send the chunk again."}, status_code=503)
        try:
            received = await run_in_threadpool(write_chunk, studio.db, s, up, offset, bytes(data))
        except UploadError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"received": received}

    @app.post("/api/uploads/{uid}/done")
    async def upload_done(request: Request, uid: str):
        user, _ = api_user(request)
        up = _upload(user, uid)
        p = owned(user, up["project_id"])
        if p["stage"] != "setup":
            raise HTTPException(409, "This video has already started.")
        final = None
        try:
            body = await request.json()
            final = int(body["size"]) if isinstance(body, dict) and body.get("size") is not None else None
        except Exception:  # noqa: BLE001 - no body: the declared size stands
            final = None
        try:
            return await run_in_threadpool(finish_upload, studio.db, s, up, p, final)
        except UploadError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/uploads/{uid}/cancel")
    def upload_cancel(request: Request, uid: str):
        user, _ = api_user(request)
        from ..storage import cancel_upload

        cancel_upload(studio.db, s, _upload(user, uid))
        return {"ok": True}

    @app.post("/p/{pid}/clips/reuse")
    def clip_reuse(request: Request, pid: str, csrf: str = Form(""), clip: list[str] = Form([])):
        """Add clips from their earlier films (ticked on the setup page)."""
        from ..storage import reuse_clip

        def add(user, p):
            if p["stage"] != "setup":
                raise ActionError("Clips can only be added before the video is started.")
            if not clip:
                raise ActionError("Tick the clips you want first.")
            for cid in clip[:20]:
                reuse_clip(studio.db, s, user, p, cid)
        return act(request, csrf, pid, add)

    @app.post("/api/p/{pid}/clips/{cid}/delete")
    def clip_delete(request: Request, pid: str, cid: str):
        user, _ = api_user(request)
        p = owned(user, pid)
        if p["stage"] != "setup":
            raise HTTPException(409, "Clips can only be removed before the video is started.")
        from ..storage import delete_clip

        if not delete_clip(studio.db, s, p, cid):
            raise HTTPException(404, "No such clip.")
        return {"ok": True}

    @app.get("/api/p/{pid}/status")
    def status(request: Request, pid: str):
        user, _ = need_user(request, api=True)
        p = owned(user, pid)
        job = studio.db.active_job(pid)
        return {"stage": p["stage"], "label": STAGES.get(p["stage"], p["stage"]), "busy": p["stage"] in BUSY,
                "progress": round(job["progress"], 3) if job else None, "message": job["message"] if job else "",
                "queued": bool(job and job["status"] == "queued"), "version": p["version"], "updated": p["updated"],
                "job": job["kind"] if job else None,
                "queued_note": studio.queued_note(job),
                "eta": studio.eta(job), "steps": steps_of(job)}

    def steps_of(job: dict | None) -> list[dict]:
        """The step log for the page: each step, how long it took (or has taken so far), the last one still going."""
        if not job:
            return []
        rows = studio.db.steps(job["id"])
        now = time.time()
        return [{"m": r["message"], "s": int((rows[k + 1]["at"] if k + 1 < len(rows) else now) - r["at"]),
                 "now": k + 1 == len(rows) and job["status"] == "running"} for k, r in enumerate(rows)]

    # ------------------------------------------------------------------ media
    @app.get("/m/{pid}/{kind}/{name}")
    def media(request: Request, pid: str, kind: str, name: str, dl: int = 0):
        user, _ = need_user(request, api=True)
        p = owned(user, pid)
        if kind not in ("out", "stills", "beats", "preview", "music"):
            raise HTTPException(404, "Not found.")
        from ..preview import AUDIO_TYPES, FOLDER

        work = workdir(s, p["user_id"], pid)
        folder = work.root / FOLDER if kind == "preview" else getattr(work, kind)
        path = safe_child(folder, name)
        types = {".mp4": "video/mp4", ".jpg": "image/jpeg", **AUDIO_TYPES, ".srt": "application/x-subrip",
                 ".mov": "video/quicktime", ".gif": "image/gif", ".fcpxml": "application/xml", ".edl": "text/plain"}
        sound = kind in ("beats", "music")
        audio = path is not None and path.suffix.lower() in AUDIO_TYPES
        if path is None or path.suffix.lower() not in types or (sound and not audio) or (audio and kind not in ("beats", "music", "out")) \
                or (kind != "out" and path.suffix.lower() in (".srt", ".mov", ".gif", ".fcpxml", ".edl")) \
                or (kind == "preview" and path.suffix.lower() != ".mp4") or (kind == "music" and path.stem != "song"
                                                                              and not path.stem.startswith("original")):
            raise HTTPException(404, "Not found.")
        media_type = types[path.suffix.lower()]
        headers = {"Cache-Control": "private, max-age=3600"}
        if dl:
            safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in p["title"])[:40].strip() or "video"
            headers["Content-Disposition"] = f'attachment; filename="{safe_title} {path.stem}{path.suffix}"'
        return FileResponse(path, media_type=media_type, headers=headers)

    @app.get("/motion/{name:path}")
    def motion_asset(name: str):
        """The motion graphics' scripts, stylesheets and fonts, for the instant preview (the same the render uses)."""
        from ...motion import asset

        found = asset(name)
        if found is None:
            raise HTTPException(404, "Not found.")
        body, media_type = found
        return Response(body, media_type=media_type, headers={"Cache-Control": "public, max-age=300"})

    # ------------------------------------------------------------------ my style and account
    @app.get("/style", response_class=HTMLResponse)
    def style(request: Request):
        user, sess = need_user(request)
        return page(request, "style.html", user, sess, profile=studio.profile(user))

    @app.post("/style/brand")
    async def style_brand(request: Request):
        user, sess = need_user(request)
        form = await request.form()
        check_csrf(sess, str(form.get("csrf", "")))
        logo = form.get("logo")
        data = await logo.read() if hasattr(logo, "read") else None
        if data is not None and len(data) > 5_000_000:
            return back("/style", "That logo file is too big (5 MB at most).")
        try:
            studio.set_brand(user, str(form.get("name", "")), str(form.get("color", "")), str(form.get("font", "")),
                             data or None, drop_logo=form.get("drop_logo") == "1")
        except ActionError as exc:
            return back("/style", str(exc))
        return back("/style", "Your brand is saved: your next films and changes use it.")

    @app.post("/style/forget-looks")
    def style_forget_looks(request: Request, csrf: str = Form("")):
        user, sess = need_user(request)
        check_csrf(sess, csrf)
        profile = studio.profile(user)
        profile.taste = []
        profile.save()
        return RedirectResponse("/style", status_code=303)

    @app.post("/style/drop")
    def style_drop(request: Request, csrf: str = Form(""), rule: str = Form("")):
        user, sess = need_user(request)
        check_csrf(sess, csrf)
        profile = studio.profile(user)
        if profile.drop(rule):
            profile.save()
        return back("/style", "Removed.")

    @app.post("/style/add")
    def style_add(request: Request, csrf: str = Form(""), text: str = Form("")):
        user, sess = need_user(request)
        check_csrf(sess, csrf)
        profile = studio.profile(user)
        if profile.add(text, "you added it", "you"):
            profile.save()
        return back("/style", "Added.")

    @app.post("/account/password")
    def password(request: Request, csrf: str = Form(""), old: str = Form(""), new: str = Form(""), new2: str = Form("")):
        user, sess = need_user(request)
        check_csrf(sess, csrf)
        if new != new2:
            return back("/account", "The two new passwords are different.")
        try:
            accounts.change_password(studio.db, user["id"], old, new)
        except accounts.AccountError as exc:
            return back("/account", str(exc))
        resp = back("/login?legacy=1", "Password changed. Sign in again.")
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.post("/account/wipe")
    def wipe(request: Request, csrf: str = Form(""), sure: str = Form("")):
        user, sess = need_user(request)
        check_csrf(sess, csrf)
        if sure != "yes":
            return back("/style", "Tick the box to confirm.")
        try:
            studio.delete_everything(user)
        except ActionError as exc:
            return back("/style", str(exc))
        return back("/", "Everything is deleted: your videos, clips and style.")

    # ------------------------------------------------------------------ the owner's admin page
    @app.get("/admin", response_class=HTMLResponse)
    def admin(request: Request):
        user, sess = need_owner(request)
        return admin_page(request, user, sess)

    def admin_page(request: Request, user: dict, sess: dict, new_link: str = "") -> HTMLResponse:
        db = studio.db
        users = db.all("SELECT u.*, (SELECT COUNT(*) FROM projects p WHERE p.user_id=u.id AND p.deleted=0) AS projects, "
                       "(SELECT COUNT(DISTINCT r.project_id) FROM renders r JOIN projects p ON p.id=r.project_id "
                       " WHERE p.user_id=u.id AND r.kind='final') AS films, "
                       "(SELECT COUNT(*) FROM passkeys k WHERE k.user_id=u.id) AS passkeys, "
                       "(SELECT COALESCE(SUM(usd),0) FROM spend sp WHERE sp.user_id=u.id AND sp.billed=1) AS usd "
                       "FROM users u ORDER BY created DESC")
        jobs = db.all("SELECT j.*, p.title, u.name FROM jobs j LEFT JOIN projects p ON p.id=j.project_id "
                      "LEFT JOIN users u ON u.id=j.user_id ORDER BY j.id DESC LIMIT 40")
        alerts = db.all("SELECT * FROM alerts ORDER BY id DESC LIMIT 30")
        invites = db.all("SELECT * FROM invites WHERE used_by IS NULL AND expires > ? ORDER BY created DESC", (time.time(),))
        spend = db.all("SELECT task, model, backend, COUNT(*) AS n, SUM(usd) AS usd, SUM(tin) AS tin, SUM(tout) AS tout, "
                       "AVG(seconds) AS s FROM spend GROUP BY task, model, backend ORDER BY usd DESC")
        audit = db.all("SELECT a.at, a.action, a.detail, COALESCE(u.display, u.name) AS who FROM audit a "
                       "LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 40")
        consents = db.one("SELECT SUM(marketing) AS yes, COUNT(*) AS n FROM users WHERE role != 'owner'") or {}
        reports = db.all("SELECT r.*, COALESCE(u.display, u.name) AS who, u.email, p.title FROM reports r "
                         "LEFT JOIN users u ON u.id=r.user_id LEFT JOIN projects p ON p.id=r.project_id "
                         "WHERE r.resolved IS NULL ORDER BY r.id DESC LIMIT 30")
        requests = [u for u in users if (u.get("access") or "beta") == "requested" and u["role"] != "owner"]
        requests.sort(key=lambda u: (not u.get("request_note"), -(u.get("requested") or u["created"])))
        return page(request, "admin.html", user, sess, health=studio.health(), users=users, jobs=jobs, alerts=alerts,
                    invites=invites, spend=spend, new_link=new_link,
                    audit=audit, consents=consents, active="admin", public=s.public_signup, beta=s.beta,
                    requests=requests, tiers=s.tiers, reports=reports, pricing=studio.pricing(),
                    tier_credits=studio.tier_credits(),
                    balances={u["id"]: studio.credits(u) for u in users if u["role"] != "owner"},
                    money=studio.money(), flags={u["id"]: studio.flags(u) for u in users},
                    gaps=abilities.summary(db),
                    day_spend={u["id"]: studio.person_spend(u["id"]) for u in users})

    @app.post("/admin/money")
    def admin_money(request: Request, csrf: str = Form(""), monthly: float = Form(60.0), video: float = Form(6.0),
                    person_day: float = Form(3.0)):
        user, sess = need_owner(request)
        check_csrf(sess, csrf)
        try:
            studio.set_money(monthly, video, person_day)
        except ActionError as exc:
            return back("/admin#money", str(exc))
        return back("/admin#money", "Saved: the new caps count from the next AI call, music or cloud minute.")

    @app.post("/admin/pause")
    def admin_pause(request: Request, csrf: str = Form(""), on: int = Form(0), why: str = Form("")):
        user, sess = need_owner(request)
        check_csrf(sess, csrf)
        studio.set_paused(bool(on), why)
        return back("/admin#money", "Paused: nothing new starts; steps already running finish." if on else
                    "Resumed: waiting steps start again.")

    @app.post("/admin/cloud-test")
    def admin_cloud_test(request: Request, csrf: str = Form("")):
        user, sess = need_owner(request)
        check_csrf(sess, csrf)
        from ..cloud import request_test, wake

        try:
            request_test(studio)
        except ActionError as exc:
            return back("/admin#cloud", str(exc))
        wake(studio)
        return back("/admin#cloud", "A cloud renderer is starting to test itself: the result comes in Alerts in a "
                                    "few minutes (the first test also fetches the speech models).")

    @app.post("/admin/pricing")
    def admin_pricing(request: Request, csrf: str = Form(""), per_minute: int = Form(1), looks: int = Form(1),
                      beta: int = Form(3), special: int = Form(12)):
        user, sess = need_owner(request)
        check_csrf(sess, csrf)
        try:
            studio.set_pricing(per_minute, looks, beta, special)
        except ActionError as exc:
            return back("/admin#credits", str(exc))
        return back("/admin#credits", "Saved: new films use the new numbers; films already started keep theirs.")

    @app.get("/admin/api/metrics")
    def admin_metrics(request: Request, days: int = 30):
        need_owner(request)
        days = max(7, min(180, days))
        return JSONResponse(analytics.metrics(studio.db, days), headers={"Cache-Control": "no-store"})

    @app.get("/admin/api/live")
    def admin_live(request: Request):
        need_owner(request)
        return JSONResponse({**analytics.live(studio.db), "health": studio.health()}, headers={"Cache-Control": "no-store"})

    @app.post("/admin/invite")
    def admin_invite(request: Request, csrf: str = Form(""), label: str = Form("")):
        user, sess = need_owner(request)
        check_csrf(sess, csrf)
        token = accounts.create_invite(studio.db, s, label, "friend")
        # shown once in this response, never put in a URL (it would sit in the browser's history)
        return admin_page(request, user, sess, new_link="/join/" + token)

    @app.post("/admin/user")
    def admin_user(request: Request, csrf: str = Form(""), uid: str = Form(""), action: str = Form(""),
                   access: str = Form(""), credits: int = Form(0), note: str = Form("")):
        user, sess = need_owner(request)
        check_csrf(sess, csrf)
        target = studio.user(uid)
        if not target or target["role"] == "owner":
            return back("/admin", "Not changed.")
        if action == "access":
            try:
                studio.decide_access(target, access)
            except ActionError as exc:
                return back("/admin", str(exc))
            who = target.get("display") or target["name"]
            return back("/admin", {"beta": f"{who} is in.", "special": f"{who} is in, with special access.",
                                   "declined": f"{who} was declined.", "requested": f"{who} is waiting again."}[access])
        if action == "disable":
            studio.db.run("UPDATE users SET disabled=1 WHERE id=?", (uid,))
            studio.db.run("DELETE FROM sessions WHERE user_id=?", (uid,))
        elif action == "enable":
            studio.db.run("UPDATE users SET disabled=0 WHERE id=?", (uid,))
        elif action == "credits":
            try:
                studio.grant_credits(target, credits, note)
            except ActionError as exc:
                return back("/admin", str(exc))
            who = target.get("display") or target["name"]
            return back("/admin", f"{who} got {credits} credits." if credits > 0 else f"Took {-credits} credits back from {who}.")
        return back("/admin", "Saved.")

    @app.post("/admin/job")
    def admin_job(request: Request, csrf: str = Form(""), job: int = Form(0), action: str = Form("")):
        user, sess = need_owner(request)
        check_csrf(sess, csrf)
        row = studio.db.one("SELECT * FROM jobs WHERE id=?", (job,))
        if row and action == "retry" and row["status"] in ("failed", "waiting"):
            studio.db.run("UPDATE jobs SET attempts=0 WHERE id=?", (job,))
            studio.db.requeue(job)
            if row["project_id"]:
                from ..service import JOB_STAGE

                studio.db.set_stage(row["project_id"], JOB_STAGE.get(row["kind"], "setup"))
        elif row and action == "cancel" and row["status"] in ("queued", "waiting"):
            studio.db.job_done(job, "cancelled")
        return back("/admin", "Done.")

    @app.post("/admin/alerts")
    def admin_alerts(request: Request, csrf: str = Form("")):
        user, sess = need_owner(request)
        check_csrf(sess, csrf)
        studio.db.run("UPDATE alerts SET seen=1")
        return back("/admin")

    @app.get("/admin/events/{pid}", response_class=HTMLResponse)
    def admin_events(request: Request, pid: str):
        user, sess = need_owner(request)
        events = studio.db.all("SELECT * FROM events WHERE project_id=? ORDER BY id DESC LIMIT 200", (pid,))
        return page(request, "events.html", user, sess, events=events, pid=pid)

    return app


def _ago(t: float | None) -> str:
    if not t:
        return "-"
    d = time.time() - float(t)
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    return time.strftime("%d %b", time.localtime(t))
