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
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
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
        profile.set_answers({q: str(form.get(q, "")) for q, _, _ in QUESTIONS}, str(form.get("never", "")))
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
        for p in projects:
            still = studio.db.one("SELECT stills FROM pitches WHERE project_id=? ORDER BY (id=?) DESC, id LIMIT 1",
                                  (p["id"], p["chosen"] or 0))
            names = json.loads(still["stills"]) if still else []
            thumbs[p["id"]] = names[0] if names else None
        return page(request, "home.html", user, sess, projects=projects, thumbs=thumbs, left=studio.videos_left(user),
                    allowed=user["videos_allowed"] or s.limits.videos_per_user)

    def new_ctx(user: dict) -> dict:
        lengths = studio.lengths_for(user)
        return {"lengths": lengths, "more_lengths": max(lengths) < max(LENGTHS)}

    @app.get("/new", response_class=HTMLResponse)
    def new_page(request: Request):
        user, sess = need_user(request)
        return page(request, "new.html", user, sess, left=studio.videos_left(user),
                    default_pace=studio.profile(user).answers.get("pace", "balanced"), **new_ctx(user))

    @app.post("/new")
    def new_post(request: Request, csrf: str = Form(""), title: str = Form(""), shapes: str = Form("9:16"),
                 length: str = Form("30"), length_custom: str = Form(""), length_unit: str = Form("s"),
                 intent: str = Form(""), pace: str = Form("balanced"), kind: str = Form("montage"),
                 style: str = Form("designed"), cut_note: str = Form(""), improvise: int = Form(35),
                 describe: str = Form("")):
        user, sess = need_user(request)
        check_csrf(sess, csrf)
        try:
            if length == "custom":  # their own length, in seconds or minutes
                try:
                    seconds = float(length_custom.replace(",", ".")) * (60 if length_unit == "min" else 1)
                except ValueError:
                    raise ActionError("Type the length as a number.") from None
            else:
                seconds = float(length or 30)
            project = studio.create_project(user, title, shapes, seconds, intent, pace, kind, style, cut_note,
                                            improvise, describe)
        except ActionError as exc:
            return page(request, "new.html", user, sess, left=studio.videos_left(user), error=str(exc),
                        default_pace=pace, **new_ctx(user))
        return back(f"/p/{project['id']}")

    def owned(user: dict, pid: str) -> dict:
        try:
            return studio.owned(user, pid)
        except ActionError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/p/{pid}", response_class=HTMLResponse)
    def project_page(request: Request, pid: str):
        user, sess = need_user(request)
        p = owned(user, pid)
        job = studio.db.active_job(pid)
        clips = studio.db.all("SELECT * FROM clips WHERE project_id=? ORDER BY created", (pid,))
        pitches = studio.pitches(p)
        samples = studio.renders(p, "sample")
        finals = studio.renders(p, "final")
        latest = sorted([r for r in finals if r["version"] == p["version"]], key=lambda r: r["shape"] != "9:16")
        older = [r for r in finals if r["version"] != p["version"]]
        feedback = studio.db.one("SELECT * FROM feedback WHERE project_id=? AND stage='final' AND version=?",
                                 (pid, p["version"]))
        return page(request, "project.html", user, sess, p=p, job=job, queued_note=studio.queued_note(job), clips=clips,
                    joblog=steps_of(job or (studio.db.one("SELECT * FROM jobs WHERE project_id=? ORDER BY id DESC LIMIT 1",
                                                          (pid,)) if p["stage"] == "failed" else None)),
                    pitches=pitches, looks=uses_looks(p), look_shape=("9:16" if p["shapes"] != "16:9" else "16:9"),
                    backs=studio.back_targets(p),
                    lengths=studio.lengths_for(user),
                    eta=studio.eta(job), total_eta=studio.total_estimate(p["kind"]),
                    note_lines=read_note(p["intent"], p["pace"]) if p["intent"] else [],
                    sample=samples[0] if samples else None, latest=latest, older=older, rated=feedback,
                    info=json.loads(p["info"]) if p["info"] else None,
                    revisions_left=None if studio.is_owner(user) else max(0, s.limits.revisions_per_video - p["revisions"]),
                    chunk=s.limits.chunk_bytes)

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

    @app.post("/p/{pid}/music")
    def music(request: Request, pid: str, csrf: str = Form(""), choice: str = Form("made")):
        return act(request, csrf, pid, lambda u, p: studio.set_music(p, choice))

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
                base: int = Form(0)):
        return act(request, csrf, pid, lambda u, p: studio.another(p, reason, own, base))

    @app.post("/p/{pid}/back")
    def go_back(request: Request, pid: str, csrf: str = Form(""), to: str = Form("setup")):
        return act(request, csrf, pid, lambda u, p: studio.go_back(p, to))

    @app.post("/p/{pid}/stop")
    def stop(request: Request, pid: str, csrf: str = Form("")):
        return act(request, csrf, pid, lambda u, p: studio.stop(p))

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
        async for piece in request.stream():
            data.extend(piece)
            if len(data) > limit:
                raise HTTPException(413, "Chunk too large.")
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
        try:
            return await run_in_threadpool(finish_upload, studio.db, s, up, p)
        except UploadError as exc:
            raise HTTPException(400, str(exc)) from exc

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
        if kind not in ("out", "stills"):
            raise HTTPException(404, "Not found.")
        folder = getattr(workdir(s, p["user_id"], pid), kind)
        path = safe_child(folder, name)
        if path is None or path.suffix.lower() not in (".mp4", ".jpg"):
            raise HTTPException(404, "Not found.")
        media_type = "video/mp4" if path.suffix.lower() == ".mp4" else "image/jpeg"
        headers = {"Cache-Control": "private, max-age=3600"}
        if dl:
            safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in p["title"])[:40].strip() or "video"
            headers["Content-Disposition"] = f'attachment; filename="{safe_title} {path.stem}.mp4"'
        return FileResponse(path, media_type=media_type, headers=headers)

    # ------------------------------------------------------------------ my style and account
    @app.get("/style", response_class=HTMLResponse)
    def style(request: Request):
        user, sess = need_user(request)
        return page(request, "style.html", user, sess, profile=studio.profile(user))

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
                    invites=invites, spend=spend, new_link=new_link, default_videos=s.limits.videos_per_user,
                    audit=audit, consents=consents, active="admin", public=s.public_signup, beta=s.beta,
                    requests=requests, tiers=s.tiers, reports=reports)

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
                   videos: int = Form(0), access: str = Form("")):
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
        elif action == "videos" and 0 <= videos <= 50:
            studio.db.run("UPDATE users SET videos_allowed=? WHERE id=?", (videos, uid))
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
