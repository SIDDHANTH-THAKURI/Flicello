"""Validate the analysts' JSON and assemble the clip catalog (machine JSON + human HTML viewer)."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path

from pydantic import ValidationError

from ..schemas import ClipAnalysis, ClipInfo
from ..workspace import Project, write_text_atomic
from .contact_sheet import format_time
from .prep import analysis_path, load_transcript, perception_dir

BOUND_SLACK = 0.1


def check_analysis(project: Project, clip: ClipInfo) -> tuple[ClipAnalysis | None, list[str]]:
    """Returns (analysis, problems). Problems are phrased so an analyst can fix them."""
    path = analysis_path(project, clip.clip_id)
    if not path.exists():
        return None, [f"missing: {path}"]
    try:
        analysis = ClipAnalysis.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        problems = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:12]]
        return None, ["schema: " + p for p in problems]
    except ValueError as exc:
        return None, [f"not valid JSON: {exc}"]

    problems: list[str] = []
    if analysis.clip_id != clip.clip_id:
        problems.append(f"clip_id is {analysis.clip_id!r}, expected {clip.clip_id!r}")
    end = clip.duration + BOUND_SLACK
    for i, m in enumerate(analysis.moments):
        where = f"moments[{i}] ({m.description[:40]!r})"
        if not 0 <= m.start < m.end <= end:
            problems.append(f"{where}: start/end {m.start}-{m.end} must satisfy 0 <= start < end <= {clip.duration:.2f}")
        elif m.peak is not None and not m.start <= m.peak <= m.end:
            problems.append(f"{where}: peak {m.peak} is outside {m.start}-{m.end}")
        elif m.end - m.start < 0.4:
            problems.append(f"{where}: only {m.end - m.start:.2f}s long; moments need at least 0.4s")
    for i, r in enumerate(analysis.unusable):
        if not 0 <= r.start < r.end <= end:
            problems.append(f"unusable[{i}]: {r.start}-{r.end} outside the clip (0-{clip.duration:.2f})")
    if not analysis.moments and not analysis.unusable:
        problems.append("no moments and nothing marked unusable: log at least one moment, "
                        "or mark the clip unusable with a reason")
    return analysis, problems


def check_all(project: Project, clip_ids: list[str] | None = None) -> dict[str, list[str]]:
    clips = {cid: c for cid, c in project.clips().items() if c.usable}
    return {cid: check_analysis(project, clips[cid])[1] for cid in (clip_ids or list(clips)) if cid in clips}


def build_catalog(project: Project) -> dict:
    entries = []
    for clip in project.clips().values():
        if not clip.usable:
            continue
        analysis, problems = check_analysis(project, clip)
        signals = project.load_signals(clip.clip_id)
        transcript = load_transcript(project, clip.clip_id)
        sheets = sorted(str(p) for p in perception_dir(project, clip.clip_id).glob("sheet_*.jpg"))
        entries.append({
            "clip": clip.model_dump(mode="json", include={"clip_id", "rel_path", "duration", "width", "height",
                                                          "fps", "has_audio", "creation_time", "chapter"}),
            "signals": signals.model_dump(mode="json") if signals else None,
            "analysis": analysis.model_dump(mode="json") if analysis and not problems else None,
            "problems": problems,
            "speech": transcript.as_lines(only_confident=True) if transcript else [],
            "sheets": sheets,
        })
    catalog = {"clips_dir": str(project.clips_dir), "clips": entries}
    write_text_atomic(project.root / "catalog.json", json.dumps(catalog, indent=1))
    write_text_atomic(project.root / "catalog.html", render_html(catalog, project.root))
    return catalog


def _rel(path: str, root: Path) -> str:
    return Path(os.path.relpath(path, root)).as_posix()


def render_html(catalog: dict, root: Path) -> str:
    esc = html.escape
    done = sum(1 for e in catalog["clips"] if e["analysis"])
    parts = [
        "<!doctype html><meta charset='utf-8'><title>VidoSuper catalog</title><style>",
        "body{font:14px system-ui,sans-serif;margin:24px;background:#111;color:#eee}"
        "h1{font-size:20px}.clip{background:#1c1c1c;border-radius:10px;padding:16px;margin:0 0 20px}"
        ".head{display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}.head b{font-size:16px}"
        ".muted{color:#999}.sheets img{width:32%;margin:4px 1% 0 0;border-radius:4px;cursor:zoom-in}"
        "table{border-collapse:collapse;width:100%;margin-top:10px}td,th{padding:4px 8px;border-bottom:1px solid #333;"
        "text-align:left;vertical-align:top}th{color:#aaa;font-weight:600}.bad{color:#f88}.tag{display:inline-block;"
        "background:#2b2b2b;border-radius:10px;padding:1px 8px;margin:2px}.score{font-variant-numeric:tabular-nums}",
        "</style>",
        f"<h1>Clip catalog &mdash; {esc(catalog['clips_dir'])}</h1>",
        f"<p class='muted'>{done}/{len(catalog['clips'])} clips analysed. Times are clip seconds.</p>",
    ]
    for entry in catalog["clips"]:
        clip, analysis, signals = entry["clip"], entry["analysis"], entry["signals"]
        parts.append("<div class='clip'><div class='head'>")
        parts.append(f"<b>{esc(clip['clip_id'])}</b><span class='muted'>{clip['duration']:.1f}s &middot; "
                     f"{clip['width']}x{clip['height']} &middot; {clip['fps']:.0f} fps &middot; chapter {clip['chapter']}</span>")
        if signals:
            parts.append(f"<span class='muted'>tech quality {signals['quality_mean']:.2f}</span>")
        parts.append("</div>")
        if analysis:
            parts.append(f"<p>{esc(analysis['summary'])}</p><p class='muted'>{esc(analysis['scene'])} &middot; "
                         f"{esc(analysis['camera'])} &middot; {esc(analysis.get('location_guess') or '')} &middot; "
                         f"energy {analysis['energy']}/5 &middot; appeal {analysis['visual_appeal']}/10</p>")
            if analysis["issues"]:
                parts.append("<p class='bad'>Issues: " + esc("; ".join(analysis["issues"])) + "</p>")
            if analysis["moments"]:
                parts.append("<table><tr><th>time</th><th>kind</th><th>use</th><th>hook</th><th>strength</th>"
                             "<th>sound</th><th>moment</th></tr>")
                for m in sorted(analysis["moments"], key=lambda m: m["start"]):
                    parts.append(f"<tr><td class='score'>{format_time(m['start'])}&ndash;{format_time(m['end'])}</td>"
                                 f"<td>{esc(m['kind'])}</td><td>{esc(m['best_use'])}</td>"
                                 f"<td class='score'>{m['hook']}</td><td class='score'>{m['strength']}</td>"
                                 f"<td>{esc(m['sound'])}</td><td>{esc(m['description'])}</td></tr>")
                parts.append("</table>")
            if analysis["tags"]:
                parts.append("<p>" + "".join(f"<span class='tag'>{esc(t)}</span>" for t in analysis["tags"]) + "</p>")
        else:
            parts.append("<p class='bad'>Not analysed yet" + (": " + esc("; ".join(entry["problems"][:3]))
                                                              if entry["problems"] else "") + "</p>")
        if entry["speech"]:
            parts.append("<p class='muted'>Speech: " + esc(" / ".join(entry["speech"][:6])) + "</p>")
        parts.append("<div class='sheets'>" + "".join(
            f"<a href='{esc(_rel(s, root))}'><img src='{esc(_rel(s, root))}' loading='lazy'></a>" for s in entry["sheets"])
            + "</div></div>")
    return "\n".join(parts)
