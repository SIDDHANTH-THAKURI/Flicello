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
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import accounts
from ..profiles import QUESTIONS
from ..service import BUSY, FEEDBACK_TAGS, LENGTHS, STAGES, ActionError, Studio
from ..storage import UploadError, finish_upload, safe_child, start_upload, workdir, write_chunk

HERE = Path(__file__).parent
COOKIE = "vs_session"
mimetypes.add_type("application/manifest+json", ".webmanifest")
DEVICE = "vs_device"  # a browser the person has signed in from before (login throttling)
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
                                 app_name=s.name)
    templates.env.filters["fromjson"] = json.loads
    templates.env.filters["ago"] = _ago
    templates.env.filters["mmss"] = lambda x: f"{int(x or 0) // 60}:{int(x or 0) % 60:02d}"
    templates.env.filters["shapes"] = lambda x: "9:16 + 16:9" if x == "both" else x
    templates.env.filters["noids"] = lambda t: MOMENT_ID.sub("", str(t or "")).strip()
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    # ------------------------------------------------------------------ plumbing
    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"  # private: keep out of search engines
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

    def client_ip(request: Request) -> str:
        """The caller's address. Only our own proxy (tailscale serve/funnel) connects from loopback, so only then is
        its X-Forwarded-For believed (its last entry: the one the proxy itself added)."""
        peer = request.client.host if request.client else "?"
        if peer in ("127.0.0.1", "::1"):
            forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                return peer
        return peer

    def current(request: Request) -> tuple[dict, dict] | None:
        return accounts.session_user(studio.db, request.cookies.get(COOKIE))

    def need_user(request: Request, api: bool = False) -> tuple[dict, dict]:
        found = current(request)
        if found is None:
            if api:
                raise HTTPException(401, "Please sign in again.")
            raise Redirect("/login")
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
        resp.set_cookie(COOKIE, token, max_age=s.session_days * 86400, httponly=True, samesite="strict",
                        secure=s.https, path="/")

    def remember_device(request: Request, resp: Response, user_id: str) -> None:
        """Mark this browser as one the person uses (see accounts.login), unless it already is."""
        if accounts._known_device(studio.db, request.cookies.get(DEVICE), user_id):
            return
        resp.set_cookie(DEVICE, accounts.new_device(studio.db, user_id), max_age=365 * 86400, httponly=True,
                        samesite="strict", secure=s.https, path="/")

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots():
        return "User-agent: *\nDisallow: /\n"

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
        resp = back("/login")
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/join/{token}", response_class=HTMLResponse)
    def join_page(request: Request, token: str):
        invite = accounts.open_invite(studio.db, token)
        if invite is None:
            raise HTTPException(404, "This invite link has expired or was already used. Ask for a new one.")
        return page(request, "join.html", None, None, invite=invite, token=token, limits=s.limits)

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
            return page(request, "join.html", None, None, invite=invite, token=token, limits=s.limits, error=error,
                        typed=name[:40])
        resp = back("/welcome")
        set_cookie(resp, accounts.new_session(studio.db, s, user["id"], client_ip(request)))
        remember_device(request, resp, user["id"])
        return resp

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
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
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
        return page(request, "home.html", user, sess, projects=projects, thumbs=thumbs, left=studio.videos_left(user))

    @app.get("/new", response_class=HTMLResponse)
    def new_page(request: Request):
        user, sess = need_user(request)
        return page(request, "new.html", user, sess, left=studio.videos_left(user))

    @app.post("/new")
    def new_post(request: Request, csrf: str = Form(""), title: str = Form(""), shapes: str = Form("9:16"),
                 length: int = Form(30), intent: str = Form("")):
        user, sess = need_user(request)
        check_csrf(sess, csrf)
        try:
            project = studio.create_project(user, title, shapes, length, intent)
        except ActionError as exc:
            return page(request, "new.html", user, sess, left=studio.videos_left(user), error=str(exc))
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
        return page(request, "project.html", user, sess, p=p, job=job, clips=clips, pitches=pitches,
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

    @app.post("/p/{pid}/start")
    def start(request: Request, pid: str, csrf: str = Form("")):
        return act(request, csrf, pid, lambda u, p: studio.start(p))

    @app.post("/p/{pid}/pick")
    def pick(request: Request, pid: str, csrf: str = Form(""), pitch: int = Form(0)):
        return act(request, csrf, pid, lambda u, p: studio.choose(p, pitch))

    @app.post("/p/{pid}/another")
    def another(request: Request, pid: str, csrf: str = Form(""), reason: str = Form(""), own: str = Form("")):
        return act(request, csrf, pid, lambda u, p: studio.another(p, reason, own))

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
                "queued": bool(job and job["status"] == "queued"), "version": p["version"], "updated": p["updated"]}

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
            return back("/style", "The two new passwords are different.")
        try:
            accounts.change_password(studio.db, user["id"], old, new)
        except accounts.AccountError as exc:
            return back("/style", str(exc))
        resp = back("/login", "Password changed. Sign in again.")
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
        users = db.all("SELECT u.*, (SELECT COUNT(*) FROM projects p WHERE p.user_id=u.id) AS projects, "
                       "(SELECT COALESCE(SUM(usd),0) FROM spend sp WHERE sp.user_id=u.id AND sp.billed=1) AS usd "
                       "FROM users u ORDER BY created")
        jobs = db.all("SELECT j.*, p.title, u.name FROM jobs j LEFT JOIN projects p ON p.id=j.project_id "
                      "LEFT JOIN users u ON u.id=j.user_id ORDER BY j.id DESC LIMIT 40")
        alerts = db.all("SELECT * FROM alerts ORDER BY id DESC LIMIT 30")
        invites = db.all("SELECT * FROM invites WHERE used_by IS NULL AND expires > ? ORDER BY created DESC", (time.time(),))
        spend = db.all("SELECT task, model, backend, COUNT(*) AS n, SUM(usd) AS usd, SUM(tin) AS tin, SUM(tout) AS tout, "
                       "AVG(seconds) AS s FROM spend GROUP BY task, model, backend ORDER BY usd DESC")
        return page(request, "admin.html", user, sess, health=studio.health(), users=users, jobs=jobs, alerts=alerts,
                    invites=invites, spend=spend, new_link=new_link, default_videos=s.limits.videos_per_user)

    @app.post("/admin/invite")
    def admin_invite(request: Request, csrf: str = Form(""), label: str = Form("")):
        user, sess = need_owner(request)
        check_csrf(sess, csrf)
        token = accounts.create_invite(studio.db, s, label, "friend")
        # shown once in this response, never put in a URL (it would sit in the browser's history)
        return admin_page(request, user, sess, new_link="/join/" + token)

    @app.post("/admin/user")
    def admin_user(request: Request, csrf: str = Form(""), uid: str = Form(""), action: str = Form(""),
                   videos: int = Form(0)):
        user, sess = need_owner(request)
        check_csrf(sess, csrf)
        target = studio.user(uid)
        if not target or target["role"] == "owner":
            return back("/admin", "Not changed.")
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
