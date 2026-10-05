"""A one-page review of an iteration, opened in the browser: the video, what the edit is trying to
be, the critics' verdict, and the evidence images."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path

from ..schemas import EditBrief
from .review import ADVERSARY, CRITICS, critiques_dir, load_critique


def _uri(path: Path) -> str:
    return path.resolve().as_uri()


def _section(title: str, body: str) -> str:
    return f"<section><h2>{html.escape(title)}</h2>{body}</section>"


def iteration_report(run, iteration: int, video: Path | None = None) -> Path:
    esc = html.escape
    it = run.iteration_dir(iteration)
    state = run.load()
    video = video or it / "draft.mp4"
    brief = EditBrief.model_validate_json(run.brief_path.read_text(encoding="utf-8")) if run.brief_path.exists() else None
    parts = [
        "<!doctype html><meta charset='utf-8'><title>VidoSuper review</title><style>",
        "body{font:15px/1.5 system-ui,sans-serif;margin:0;background:#101014;color:#e8e8ee}"
        "main{max-width:1180px;margin:0 auto;padding:28px}h1{font-size:26px;margin:0 0 4px}"
        "h2{font-size:18px;margin:28px 0 10px;color:#fff}.muted{color:#9a9aa6}"
        "video{width:100%;border-radius:10px;background:#000;margin-top:14px}"
        ".score{font-size:40px;font-weight:700}.pill{display:inline-block;padding:2px 10px;border-radius:12px;"
        "background:#2a2a33;margin-right:6px}.bad{color:#ff8a80}.good{color:#9be7a5}"
        "table{border-collapse:collapse}td,th{padding:4px 12px;border-bottom:1px solid #2a2a33;text-align:left}"
        "img{max-width:100%;border-radius:8px;margin:6px 0;cursor:zoom-in}li{margin:6px 0}"
        "section{background:#17171d;border-radius:12px;padding:6px 20px 16px;margin-top:18px}",
        "</style><main>",
    ]
    title = brief.title if brief else "Edit"
    parts.append(f"<h1>{esc(title)} <span class='muted'>— iteration {iteration}</span></h1>")
    if brief:
        parts.append(f"<div class='muted'>{esc(brief.logline)}</div>")
    if video.exists():
        parts.append(f"<video controls autoplay muted src='{_uri(video)}'></video>"
                     "<div class='muted'>Starts muted so the browser lets it autoplay — turn the sound on.</div>")
    if brief:
        body = f"<p><b>Point of view:</b> {esc(brief.thesis)}</p>"
        if brief.convention_break:
            body += f"<p><b>Convention it breaks:</b> {esc(brief.convention_break)}</p>"
        parts.append(_section("What this edit is trying to be", body))

    review_file = it / "review.json"
    if review_file.exists():
        review = json.loads(review_file.read_text(encoding="utf-8"))
        verdict = "good" if review["decision"] == "approve" else "bad"
        body = (f"<div class='score'>{review['score']:.2f}<span class='muted' style='font-size:18px'> / 10</span></div>"
                f"<p><span class='pill {verdict}'>{esc(review['decision'])}</span>"
                f"<span class='pill'>{review['blockers']} blocker</span><span class='pill'>{review['majors']} major</span></p>")
        rows = []
        for name in (*CRITICS, ADVERSARY):
            critique, _ = load_critique(critiques_dir(it) / f"{name}.json")
            if critique:
                rows.append(f"<tr><td>{esc(name)}</td><td><b>{critique.overall:.1f}</b></td><td class='muted'>"
                            + esc(", ".join(f"{k} {v:g}" for k, v in critique.scores.items())) + "</td></tr>")
        body += "<table><tr><th>critic</th><th>score</th><th>details</th></tr>" + "".join(rows) + "</table>"
        issues = "".join(f"<li><b>{esc(i['id'])}</b> <span class='muted'>({esc(i['critic'])}, {esc(i['severity'])})</span> "
                         f"{esc(i['problem'])}<br><span class='muted'>Fix: {esc(i['fix'])}</span></li>"
                         for i in review["top_issues"])
        body += f"<h3>Fix next</h3><ol>{issues}</ol>"
        parts.append(_section("Critic panel", body))

    notes = it / "notes.md"
    if notes.exists():
        parts.append(_section("Cut summary", "<pre style='white-space:pre-wrap'>" + esc(notes.read_text(encoding="utf-8")) + "</pre>"))
    images = [("Dynamics: plan vs actual", ["dynamics.png"]), ("Shots", sorted(p.name for p in it.glob("shots_*.jpg"))),
              ("Opening, frame by frame", ["hook.jpg"]), ("Either side of every cut", sorted(p.name for p in it.glob("cuts_*.jpg")))]
    for heading, names in images:
        imgs = "".join(f"<a href='{_uri(it / n)}' target='_blank'><img src='{_uri(it / n)}'></a>" for n in names if (it / n).exists())
        if imgs:
            parts.append(_section(heading, imgs))
    if run.music_md.exists():
        parts.append(_section("Music", "<pre style='white-space:pre-wrap'>" + esc(run.music_md.read_text(encoding="utf-8")) + "</pre>"))
    parts.append(f"<p class='muted'>Run folder: {esc(str(run.dir))} · stage {esc(state.stage)} · "
                 f"best iteration so far: {state.best_iteration or '-'}</p></main>")
    out = it / "report.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


def open_in_browser(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 - the user's own report
