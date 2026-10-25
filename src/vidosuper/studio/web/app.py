"""The studio's web app: pages for people, a small JSON API for uploads and progress, media for videos.

Security in one place:
- it answers only on this computer: any other host name is refused (no other site reaches it through DNS
  rebinding), and the one person of this machine is signed in by itself (HttpOnly, SameSite=Strict cookie);
- every state change is a POST/PUT/DELETE carrying the session's CSRF token (form field or X-CSRF header);
- every project, upload and file is looked up by id *and* owner; files are served only from the project's
  own folders by plain names (no paths from the client);
- strict security headers (CSP without inline scripts, no framing, no referrer, no MIME sniffing);
- errors show a plain message, never a stack trace.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import accounts
from ..brain.context import read_note
from ..profiles import QUESTIONS
from ..service import BUSY, FEEDBACK_TAGS, LENGTHS, STAGES, ActionError, Studio, uses_looks
from ..storage import UploadError, finish_upload, safe_child, start_upload, workdir, write_chunk

HERE = Path(__file__).parent
COOKIE = "vs_session"
mimetypes.add_type("application/manifest+json", ".webmanifest")


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
       "style-src 'self'; font-src 'self'; connect-src 'self' wss://generativelanguage.googleapis.com; "
       "frame-ancestors 'self'; base-uri 'none'; form-action 'self'; object-src 'none'")  # framed only by Cello's shell


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


from .. import abilities  # noqa: E402 - what people ask for that can't be done yet


def create_app(studio: Studio) -> FastAPI:
    s = studio.s
    @asynccontextmanager
    async def lifespan(_app):
        # on Windows a browser that drops a connection (a video seeking cancels its range request) makes asyncio log a
        # ConnectionResetError from its Proactor transport: nothing is wrong, so it isn't logged (anything else is)
        loop = asyncio.get_running_loop()
        before = loop.get_exception_handler()

        def quiet(lp, context):
            if isinstance(context.get("exception"), ConnectionResetError):
                return
            if before is not None:
                before(lp, context)
            else:
                lp.default_exception_handler(context)

        loop.set_exception_handler(quiet)
        yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals.update(STAGES=STAGES, BUSY=BUSY, QUESTIONS=QUESTIONS, TAGS=FEEDBACK_TAGS, LENGTHS=LENGTHS,
                                 app_name=s.name, vite=vite_tags)
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
    from ...local import health as local_health

    local_health.HOME[:] = [s.home]  # services failing while working: remembered here, said by Connect and Cello
    from ...local import keys as local_keys  # their keys from this machine's keychain, their Blender if they set one

    local_keys.load(s.home)
    if s.blender_path:
        import os as _os

        _os.environ.setdefault("BLENDER_PATH", s.blender_path)
    templates.env.globals["local"] = True
    if studio.work.recovered:  # Cello's hands (work/sessions.py): what a restart cut short is said, not left hanging
        __import__("logging").getLogger("vidosuper.studio").info("work cut short by the restart: %d", studio.work.recovered)

    # ------------------------------------------------------------------ plumbing
    LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}

    @app.middleware("http")
    async def headers(request: Request, call_next):
        if (request.url.hostname or "") not in LOCAL_HOSTS:  # only this computer (and no DNS rebinding)
            return PlainTextResponse("Flicello only answers on this computer: open http://127.0.0.1:"
                                     f"{s.port}", status_code=403)
        response = await call_next(request)
        fresh = getattr(request.state, "fresh", None)  # the one person, signed in by itself
        if fresh:
            response.set_cookie(COOKIE, fresh, max_age=s.session_days * 86400, httponly=True, samesite="strict",
                                secure=False, path="/")
        renewed = getattr(request.state, "renew", None)
        if renewed and COOKIE not in response.headers.get("set-cookie", ""):
            response.set_cookie(COOKIE, renewed, max_age=s.session_days * 86400, httponly=True, samesite="strict",
                                secure=False, path="/")
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Frame-Options"] = "SAMEORIGIN"  # Cello's shell holds the app in a frame of our own
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"  # no referrer to other sites; our own forms keep their Origin
        response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"  # talking to Cello
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
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

    def current(request: Request) -> tuple[dict, dict] | None:
        """The person of this machine: their session, else a new one when the page comes from this computer."""
        token = request.cookies.get(COOKIE)
        found = accounts.session_user(studio.db, token)
        if found is None and (request.client.host if request.client else "") in ("127.0.0.1", "::1", "testclient"):
            token = accounts.new_session(studio.db, s, accounts.owner(studio.db)["id"], "127.0.0.1")
            request.state.fresh = token
            found = accounts.session_user(studio.db, token)
        if found and accounts.renew(studio.db, s, found[1]):
            request.state.renew = token  # the middleware sends the cookie again with a fresh lifetime
        return found

    def need_user(request: Request, api: bool = False) -> tuple[dict, dict]:
        """The person of this machine (see `current`)."""
        found = current(request)
        if found is None:
            raise HTTPException(401 if api else 403, "Flicello opens only on this computer: http://127.0.0.1:"
                                                     f"{s.port}")
        return found

    def need_owner(request: Request) -> tuple[dict, dict]:
        return need_user(request)

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

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots():
        return "User-agent: *\nDisallow: /\n"  # a studio on someone's own computer: nothing to index

    # ------------------------------------------------------------------ health (no login, no details)
    @app.get("/healthz")
    def healthz():
        h = studio.health()
        ok = h["worker_seen"] is not None and h["worker_seen"] < 60
        return JSONResponse({"ok": ok}, status_code=200 if ok else 503)

    # ------------------------------------------------------------------ sign in, invites

    # ------------------------------------------------------------------ the closed beta: asking for access

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

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
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
        return page(request, "home.html", user, sess, projects=projects, thumbs=thumbs)

    def new_ctx(user: dict) -> dict:
        lengths = studio.lengths_for(user)
        return {"lengths": lengths, "more_lengths": max(lengths) < max(LENGTHS)}

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
                    can_compose=studio.can_compose(),
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
                    site_url=f"http://127.0.0.1:{s.port}",
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

    # ------------------------------------------------------------------ Connect: what this machine has (local)
    @app.get("/connect", response_class=HTMLResponse)
    def connect_page(request: Request):
        """Everything Flicello uses on this machine, each ready or not, with what to do; the keys; the setup prompt
        for Claude Code."""
        user, sess = need_owner(request)
        from ...local import checks, keys as local_keys, setup_prompt

        rep = checks.report(s.home, s.models_dir)
        groups = {}
        for item in rep["items"]:
            groups.setdefault(item["group"], []).append(item)
        return page(request, "connect.html", user, sess, rep=rep, groups=groups, prompt=setup_prompt.prompt(s.port),
                    brain=s.owner_brain, blender=s.blender_path, keys=local_keys.KEYS)

    @app.get("/api/connect/report")
    def connect_report(request: Request):
        need_owner(request)
        from ...local import checks

        return JSONResponse(checks.report(s.home, s.models_dir))

    @app.post("/api/connect/key")
    async def connect_key(request: Request):
        """Keep (or forget) one of their keys on this machine, then test it."""
        user, sess = need_owner(request)
        check_csrf(sess, request.headers.get("x-csrf"))
        body = await request.json()
        from ...local import checks, keys as local_keys

        name, value = str(body.get("name", "")), str(body.get("value", ""))
        try:
            where = local_keys.save(name, value, s.home)
        except ValueError as exc:
            return JSONResponse({"ok": False, "detail": str(exc)}, status_code=400)
        service = {"GEMINI_API_KEY": "gemini", "ELEVENLABS_API_KEY": "elevenlabs",
                   "ANTHROPIC_API_KEY": "anthropic"}.get(name)
        result = await run_in_threadpool(checks.test, service) if (service and value) else {"ok": bool(value),
                                                                                          "detail": "saved" if value else "forgotten"}
        return JSONResponse({"where": where, **result})

    @app.post("/api/connect/test")
    async def connect_test(request: Request):
        user, sess = need_owner(request)
        check_csrf(sess, request.headers.get("x-csrf"))
        body = await request.json()
        from ...local import checks

        return JSONResponse(await run_in_threadpool(checks.test, str(body.get("service", ""))))

    @app.post("/api/connect/settings")
    async def connect_settings(request: Request):
        """The brain (their Claude Code plan, or the Anthropic API) and Blender's path, kept in studio.yaml."""
        user, sess = need_owner(request)
        check_csrf(sess, request.headers.get("x-csrf"))
        body = await request.json()
        import yaml

        path = s.home / "studio.yaml"
        data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
        said = []
        if body.get("brain") in ("claude_code", "api"):
            data["owner_brain"] = s.owner_brain = body["brain"]
            said.append("Your Claude Code plan thinks for Flicello" if body["brain"] == "claude_code"
                        else "The Anthropic API thinks for Flicello (billed per use)")
        if "blender" in body:
            found = str(body.get("blender") or "").strip().strip('"')
            if found and not Path(found).exists():
                return JSONResponse({"ok": False, "detail": "There's no file there: check Blender's path."}, status_code=400)
            data["blender_path"] = s.blender_path = found
            import os as _os

            if found:
                _os.environ["BLENDER_PATH"] = found
            else:
                _os.environ.pop("BLENDER_PATH", None)
            said.append("Blender connected" if found else "Blender disconnected")
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return JSONResponse({"ok": True, "detail": "; ".join(said) or "Nothing changed."})

    # ------------------------------------------------------------------ Cello: the presence that edits with you
    @app.get("/cello", response_class=HTMLResponse)
    def cello_shell(request: Request, to: str = "/"):
        """The app held in a frame, with Cello over it: its voice and its orb live here, so they stay through every page."""
        user, sess = need_user(request)
        if not to.startswith("/") or to.startswith("//") or to.startswith("/cello"):
            to = "/"
        return page(request, "cello.html", user, sess, to=to)

    def cello_film(user: dict, pid: str | None):
        try:
            return studio.owned(user, pid) if pid else None
        except ActionError:
            raise HTTPException(404, "That film isn't theirs or doesn't exist.") from None

    @app.post("/api/cello/token")
    def cello_token(request: Request):
        user, sess = need_user(request, api=True)
        check_csrf(sess, request.headers.get("x-csrf"))
        from ..cello import token

        try:
            return JSONResponse(token())
        except Exception as exc:  # noqa: BLE001 - said to the page plainly
            return JSONResponse({"detail": f"Cello can't speak right now: {' '.join(str(exc).split())[:160]}"}, status_code=503)

    def agent(request: Request) -> tuple[dict, dict | None, dict] | None:
        """The work agent (its session's token, from this computer only): (the person, its film, the token's entry)."""
        found = studio.work.by_token(request.headers.get("x-flicello-agent"))
        if found is None or (request.client.host if request.client else "") not in ("127.0.0.1", "::1"):
            return None
        user = studio.user(found["user_id"])
        project = studio.db.project(found["project_id"]) if found["project_id"] else None
        return user, project, found

    def need_agent(request: Request) -> tuple[dict, dict | None, dict]:
        got = agent(request)
        if got is None:
            raise HTTPException(401, "Not a running work session.")
        return got

    @app.post("/api/cello/act")
    async def cello_act(request: Request):
        """One of Cello's tools, run with the same checks as the page's buttons; its result is plain words for Cello.
        The work agent uses them too, with its session's token (its film is the session's)."""
        hands = agent(request)
        if hands is None:
            user, sess = need_user(request, api=True)
            check_csrf(sess, request.headers.get("x-csrf"))
        else:
            user, film, entry = hands
        body = await request.json()
        from ..cello import act

        try:
            project = film if hands is not None else cello_film(user, body.get("pid"))
            out = await run_in_threadpool(act, studio, user, project, str(body.get("name", "")), body.get("args") or {})
            go = (out.get("ui") or {}).get("go") or "" if isinstance(out, dict) else ""
            if hands is not None and go.startswith("/p/"):  # the agent opened or started a film: it works on that now
                entry["project_id"] = go.split("/")[2]
            if hands is not None and isinstance(out, dict) and out.get("ui"):  # on their screen too, as it happens
                studio.work.show(user["id"], out["ui"], project["id"] if project else None)
        except HTTPException:
            return JSONResponse({"result": "That film isn't theirs or doesn't exist."})
        except (ActionError, ValueError, KeyError) as exc:
            return JSONResponse({"result": f"Not done: {exc}"})
        except Exception as exc:  # noqa: BLE001 - Cello hears it and can diagnose
            __import__("logging").getLogger("vidosuper.studio").warning("cello tool %s failed", body.get("name"), exc_info=True)
            return JSONResponse({"result": f"Not done, something broke: {type(exc).__name__}: {str(exc)[:200]}. "
                                           "Try diagnose, or tell them plainly."})
        return JSONResponse(out)

    @app.post("/api/cello/log")
    async def cello_log(request: Request):
        user, sess = need_user(request, api=True)
        check_csrf(sess, request.headers.get("x-csrf"))
        body = await request.json()
        from ..cello import log_lines

        pid = body.get("pid") if isinstance(body.get("pid"), str) and re.fullmatch(r"p[0-9a-f]{12}", body["pid"]) else None
        kept = log_lines(studio, user, pid, body.get("lines") or [], studio.is_owner(user))
        return JSONResponse({"kept": kept})

    # ------------------------------------------------------------------ work: Cello's hands (work/)
    @app.get("/api/work/tools")
    def work_tools(request: Request):
        need_agent(request)
        from ..cello import BROWSER, TOOLS

        return {"tools": [t for t in TOOLS if t["name"] not in BROWSER and t["name"] not in ("work", "answer")]}

    @app.post("/api/work/progress")
    async def work_progress(request: Request):
        _, _, entry = need_agent(request)
        body = await request.json()
        studio.work.progress(entry["work"], str(body.get("text", "")))
        return {"ok": True}

    @app.post("/api/work/choice")
    async def work_choice(request: Request):
        _, project, _ = need_agent(request)
        body = await request.json()
        if project is None:
            raise HTTPException(400, "No film is open.")
        from ...work import asks

        asks.choose(studio, project, str(body.get("what", "")), str(body.get("value", "")))
        return {"ok": True}

    @app.post("/api/work/open")
    async def work_open(request: Request):
        user, _, entry = need_agent(request)
        body = await request.json()
        want = " ".join(str(body.get("film", "")).split()).lower()
        films = studio.projects(user)
        hit = next((f for f in films if f["id"] == want), None) or next(
            (f for f in films if f["title"].lower() == want), None) or next(
            (f for f in films if want and want in f["title"].lower()), None)
        if hit is None:
            return {"opened": None, "films": [f["title"] for f in films[:12]]}
        entry["project_id"] = hit["id"]
        return {"opened": hit["title"], "id": hit["id"], "stage": hit["stage"]}

    @app.post("/api/work/scene")
    async def work_scene(request: Request):
        user, project, entry = need_agent(request)
        body = await request.json()
        if project is None:
            raise HTTPException(400, "No film is open.")
        from ...work import scenes_in_film

        try:
            row = scenes_in_film.start(studio, user, project, str(body.get("what", "")), float(body.get("start")),
                                       float(body.get("end")), str(body.get("style") or ""),
                                       on_progress=lambda m: studio.work.progress(entry["work"], f"Scene: {m}"))
        except (ValueError, TypeError, ActionError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"task": row["id"], "note": "Designing takes a while (often 15 to 40 minutes): "
                                           f"bash fl wait scene {row['id']}"}

    @app.get("/api/work/scene/{wid}")
    def work_scene_state(request: Request, wid: str):
        hands = agent(request)
        user = hands[0] if hands else need_user(request, api=True)[0]
        row = studio.db.one("SELECT * FROM work WHERE id=? AND user_id=? AND kind='scene'", (wid, user["id"]))
        if row is None:
            raise HTTPException(404, "No such scene task.")
        return {"done": row["status"] in ("done", "failed", "stopped"), "status": row["status"], "say": row["say"],
                "progress": row["progress"]}

    @app.post("/api/work/cartoon")
    async def work_cartoon(request: Request):
        user, project, entry = need_agent(request)
        body = await request.json()
        from ...work import cartoons_in_studio

        voice = None
        if body.get("voice"):  # their narration: a clip of the open film (its sound)
            if project is None:
                raise HTTPException(400, "Open the film whose clip is the narration first.")
            want = str(body["voice"]).strip().lower()
            row = next((c for c in studio.db.all("SELECT * FROM clips WHERE project_id=?", (project["id"],))
                        if want in (c["id"].lower(), c["name"].lower())), None)
            if row is None:
                raise HTTPException(400, f"There's no clip '{body['voice']}' in this film.")
            voice = workdir(s, project["user_id"], project["id"]).clips / row["file"]
        try:
            row = cartoons_in_studio.start(studio, user, str(body.get("story", "")), float(body.get("seconds") or 40),
                                           str(body.get("shape") or "9:16"), str(body.get("style") or ""), voice,
                                           on_progress=lambda m: studio.work.progress(entry["work"], f"Cartoon: {m}"))
        except (ValueError, TypeError, ActionError) as exc:
            raise HTTPException(400, str(exc)) from exc
        entry["project_id"] = row["film"]  # the agent works on the new film from here
        return {"task": row["id"], "film": row["film"],
                "note": f"A new film is being made as a cartoon (often 1 to 3 hours): bash fl wait cartoon {row['id']}"}

    @app.get("/api/work/cartoon/{wid}")
    def work_cartoon_state(request: Request, wid: str):
        hands = agent(request)
        user = hands[0] if hands else need_user(request, api=True)[0]
        row = studio.db.one("SELECT * FROM work WHERE id=? AND user_id=? AND kind='cartoon'", (wid, user["id"]))
        if row is None:
            raise HTTPException(404, "No such cartoon task.")
        return {"done": row["status"] in ("done", "failed", "stopped"), "status": row["status"], "say": row["say"],
                "progress": row["progress"], "film": row["project_id"]}

    @app.get("/api/work/plan")
    def work_plan(request: Request):
        user, project, _ = need_agent(request)
        if project is None:
            raise HTTPException(400, "No film is open.")
        try:
            return studio.plan_view(studio.db.project(project["id"]), user)
        except ActionError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/work/plan")
    async def work_plan_set(request: Request):
        user, project, _ = need_agent(request)
        if project is None:
            raise HTTPException(400, "No film is open.")
        body = await request.json()
        try:
            job = await run_in_threadpool(studio.plan_draft, user, studio.db.project(project["id"]),
                                          body.get("plan") or {}, list(body.get("said") or []),
                                          str(body.get("text") or ""))
        except ActionError as exc:
            raise HTTPException(400, str(exc)) from exc
        studio.work.show(user["id"], {"wait": "draft"}, project["id"])
        return {"job": job, "note": "Checked: the draft is being laid out on their screen."}

    @app.get("/api/work/frames")
    def work_frames(request: Request, t: str = "", which: str = ""):
        """Stills of what is on their screen at these film seconds (the draft as it plays, else the version they
        work on; or a version by number), into the agent's folder for it to look at."""
        user, project, _ = need_agent(request)
        if project is None:
            raise HTTPException(400, "No film is open.")
        from ...work.frames import frames

        p = studio.db.project(project["id"])
        version = studio.work_version(p)
        draft = studio.draft_of(p)
        pick = which.strip() or ("draft" if draft and int(draft.get("base") or 0) == version else str(version))
        try:
            times = [float(x) for x in t.replace(" ", "").split(",") if x]
        except ValueError as exc:
            raise HTTPException(400, "Times are film seconds, like 1.5,4") from exc
        if not times or (pick != "draft" and not pick.isdigit()):
            raise HTTPException(400, "Say which film seconds (and a version number, or the draft).")
        folder = studio.work.folder(p["id"], user["id"])
        try:
            made = frames(studio, p, pick, times, folder / "frames")
        except (ValueError, ActionError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"which": pick, "frames": [{"t": m["t"], "file": m["file"].relative_to(folder).as_posix()}
                                          for m in made]}

    @app.get("/api/work/job")
    def work_job(request: Request):
        _, project, _ = need_agent(request)
        if project is None:
            return {"done": True, "note": "no film open"}
        job = studio.db.active_job(project["id"])
        if job:
            return {"done": False, "step": job["kind"], "progress": round(float(job.get("progress") or 0), 2),
                    "message": job.get("message") or ""}
        last = studio.db.one("SELECT kind, status, error FROM jobs WHERE project_id=? ORDER BY id DESC LIMIT 1",
                             (project["id"],))
        return {"done": True, "last": dict(last) if last else None, "stage": studio.db.project(project["id"])["stage"]}

    @app.get("/api/work/state")
    def work_state(request: Request, pid: str = "", since: float = 0.0):
        """For the panel beside Cello: what its hands are doing, the open question, what was done, their choices;
        and what its hands did on the screen since `since` (a film opened, a draft on its way), for the shell to show
        whether or not Cello's voice is on."""
        user, _ = need_user(request, api=True)
        project = cello_film(user, pid) if pid else None
        from ...work import asks

        rows = studio.db.all("SELECT * FROM work WHERE user_id=? AND project_id IS ? ORDER BY updated DESC LIMIT 12",
                             (user["id"], project["id"] if project else None))
        def view(r):
            return {"id": r["id"], "kind": r["kind"], "status": r["status"], "request": r["request"],
                    "progress": r["progress"], "say": r["say"], "question": r["question"],
                    "options": json.loads(r["options"]) if r["options"] else [], "updated": r["updated"],
                    "created": r["created"]}
        return {"work": [view(r) for r in rows], "choices": asks.choices(studio, project), "now": time.time(),
                "ui": studio.work.shown_since(user["id"], since) if since else [],
                "busy": bool(project and studio.db.active_job(project["id"]))}

    @app.post("/api/work/answer")
    async def work_answer(request: Request):
        user, sess = need_user(request, api=True)
        check_csrf(sess, request.headers.get("x-csrf"))
        body = await request.json()
        try:
            row = await run_in_threadpool(studio.work.answer, user, str(body.get("id", "")), str(body.get("text", "")))
        except (ValueError, ActionError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "status": row["status"], "say": row["say"]}

    @app.post("/api/work/stop")
    async def work_stop(request: Request):
        user, sess = need_user(request, api=True)
        check_csrf(sess, request.headers.get("x-csrf"))
        body = await request.json()
        try:
            studio.work.stop(user, str(body.get("id", "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.get("/api/cello/events")
    def cello_events(request: Request, pid: str = "", since: float = 0.0):
        user, _ = need_user(request, api=True)
        from ..cello import events, now

        from ..cello import service_events, work_events

        project = cello_film(user, pid) if pid else None
        said = (events(studio, project, since) if project else []) + work_events(studio, user, project, since) + \
            service_events(since)
        return JSONResponse({"now": now(), "events": said})

    # ------------------------------------------------------------------ the editor: the film on a timeline (editing.py)
    @app.get("/p/{pid}/edit", response_class=HTMLResponse)
    def edit_page(request: Request, pid: str):
        user, sess = need_user(request)
        p = owned(user, pid)
        return page(request, "edit.html", user, sess, p=p)

    def timeline_view(user: dict, pid: str) -> JSONResponse:
        from ..editing import view

        try:
            return JSONResponse(view(studio, studio.db.project(pid), user))
        except (ActionError, ValueError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/p/{pid}/editor")
    async def editor_get(request: Request, pid: str):
        user, _ = need_user(request, api=True)
        owned(user, pid)
        return await run_in_threadpool(timeline_view, user, pid)

    @app.post("/api/p/{pid}/editor")
    async def editor_post(request: Request, pid: str):
        """Exact edits by hand (JSON: ops, said), made at once as the draft; answers with the editor's new view."""
        user, sess = need_user(request, api=True)
        check_csrf(sess, request.headers.get("x-csrf"))
        p = owned(user, pid)
        body = await request.json()
        from pydantic import ValidationError

        from ..editing import apply

        try:
            await run_in_threadpool(apply, studio, user, p, list(body.get("ops") or []), list(body.get("said") or []))
        except (ActionError, ValueError, ValidationError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        return await run_in_threadpool(timeline_view, user, pid)

    @app.get("/api/p/{pid}/sprite/{clip_id}")
    async def editor_sprite(request: Request, pid: str, clip_id: str):
        """A clip's filmstrip sheet for the editor's tracks (made once, then kept)."""
        user, _ = need_user(request, api=True)
        p = owned(user, pid)
        from ..editing import sprite

        try:
            path = await run_in_threadpool(sprite, studio, p, clip_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    @app.post("/api/p/{pid}/editor/{what}")
    async def editor_step(request: Request, pid: str, what: str):
        """undo, redo, discard (back to the version) or make (render the draft as a version)."""
        user, sess = need_user(request, api=True)
        check_csrf(sess, request.headers.get("x-csrf"))
        p = owned(user, pid)
        try:
            if what == "undo":
                studio.draft_undo(p)
            elif what == "redo":
                studio.draft_redo(p)
            elif what == "discard":
                studio.discard_draft(p)
            elif what == "make":
                studio.make_draft(user, p)
                return JSONResponse({"ok": True, "making": True})
            elif what == "remake":  # a stretch of the made music written again (song seconds): a new version
                body = await request.json()
                studio.edit_music(user, p, float(body["start"]), float(body["end"]), str(body.get("want") or ""))
                return JSONResponse({"ok": True, "making": True})
            else:
                raise HTTPException(404, "No such step.")
        except (ActionError, KeyError, ValueError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        return await run_in_threadpool(timeline_view, user, pid)

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
            done = await run_in_threadpool(finish_upload, studio.db, s, up, p, final)
        except UploadError as exc:
            raise HTTPException(400, str(exc)) from exc
        if up["kind"] == "clip":  # its clips are in: Flicello asks the film's shape (once), its pick first
            from ...work import asks

            try:
                asks.after_upload(studio, user, p)
            except Exception:  # noqa: BLE001 - a question to help; the upload is done either way
                __import__("logging").getLogger("vidosuper.studio").warning("shape question failed", exc_info=True)
        return done

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
