"""The legal pages: /legal and /legal/<slug>, written in markdown under ../legal/ and rendered with the facts below
(who runs Flicello, where data lives), plus /.well-known/security.txt.

Each document starts with a few `key: value` lines (title, summary, updated), a blank line, then markdown. Facts
change in one place (FACTS) and every document follows.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from jinja2 import Environment, StrictUndefined
from markupsafe import Markup

from ..auth import LEGAL_VERSION
from ..service import Studio

LEGAL = Path(__file__).resolve().parent.parent / "legal"
SLUG = re.compile(r"^[a-z][a-z-]{1,30}$")
ORDER = ["terms", "privacy", "cookies", "acceptable-use", "copyright", "ai", "subprocessors", "security", "contact"]

FACTS = {
    "operator": "Siddhanth Thakuri",
    "trading": "Flicello",
    "abn": "25 443 793 186",
    "country": "Australia",
    "state": "New South Wales",
    "site": "flicello.com",
    "hosting": "DigitalOcean data centre in Sydney, Australia",
    "hosting_country": "Australia",
    "render_location": "While a video is being made, its files are also copied to a computer we operate in New South Wales, Australia, which does the heavy video work; that copy is deleted when the project is.",
    "min_age": 18,
    "retention_days": 14,
    "version": LEGAL_VERSION,
    "effective": "17 October 2026",
    "email": {
        "hello": "hello@flicello.com",
        "privacy": "privacy@flicello.com",
        "legal": "legal@flicello.com",
        "abuse": "abuse@flicello.com",
        "security": "security@flicello.com",
        "copyright": "copyright@flicello.com",
        "grievance": "grievance@flicello.com",
    },
}

_env = Environment(undefined=StrictUndefined, autoescape=False)
_cache: dict[str, tuple[float, dict]] = {}


def load(slug: str) -> dict | None:
    """{slug, title, summary, updated, html, toc} for a document, or None."""
    import markdown

    if not SLUG.match(slug):
        return None
    path = LEGAL / f"{slug}.md"
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    hit = _cache.get(slug)
    if hit and hit[0] == mtime:
        return hit[1]
    raw = path.read_text(encoding="utf-8")
    head, _, body = raw.partition("\n\n")
    meta = {}
    for line in head.splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    text = _env.from_string(body).render(**FACTS)
    md = markdown.Markdown(extensions=["tables", "toc", "sane_lists", "attr_list"],
                           extension_configs={"toc": {"permalink": False, "toc_depth": "2-2"}})
    html = md.convert(text)
    doc = {"slug": slug, "title": meta.get("title", slug), "summary": meta.get("summary", ""),
           "updated": meta.get("updated", FACTS["effective"]), "html": Markup(html), "toc": md.toc_tokens}
    _cache[slug] = (mtime, doc)
    return doc


def mount(app: FastAPI, studio: Studio, h: SimpleNamespace) -> None:
    def docs() -> list[dict]:
        return [d for d in (load(slug) for slug in ORDER) if d]

    @app.get("/legal", response_class=HTMLResponse)
    def legal_index(request: Request):
        found = h.current(request)
        user, sess = found if found else (None, None)
        return h.page(request, "legal_index.html", user, sess, docs=docs(), facts=FACTS)

    @app.get("/legal/{slug}", response_class=HTMLResponse)
    def legal_doc(request: Request, slug: str):
        doc = load(slug)
        if doc is None:
            raise HTTPException(404, "There's no such page.")
        found = h.current(request)
        user, sess = found if found else (None, None)
        return h.page(request, "legal.html", user, sess, doc=doc, docs=docs(), facts=FACTS)

    @app.get("/.well-known/security.txt", response_class=PlainTextResponse)
    def security_txt(request: Request):
        site = (studio.s.site_url or f"{request.url.scheme}://{request.url.netloc}").rstrip("/")
        expires = time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime(time.time() + 300 * 86400))
        return (f"Contact: mailto:{FACTS['email']['security']}\n"
                f"Expires: {expires}\n"
                f"Policy: {site}/legal/security\n"
                f"Preferred-Languages: en\n"
                f"Canonical: {site}/.well-known/security.txt\n")
