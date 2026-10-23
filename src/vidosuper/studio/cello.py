"""Cello: the presence that edits with you. Not a chat box: a live voice (Gemini Live, in the browser, always listening,
interruptible) with its hands on the controls. Every Flicello action is one of its tools, it watches the screen it
is on, says what it notices before it is asked, and repairs what fails.

- The browser opens the voice session directly with a short-lived token this server makes
  (`token`): the model, the voice, the instructions and the tools are locked into the token, so a page can't change them.
- Tools run in two places. The ones that move the screen (open a film, play, seek, point at something) run in the
  browser. The ones that change a film run here (`act`), with the same checks as the page's own buttons.
- `look` tells it what is on screen and the film's state; the browser also feeds it events (a draft is ready, a version
  finished, a step failed) as they happen, so it can speak first.
"""

from __future__ import annotations

import datetime
import json
import os
import time

MODEL = "gemini-3.8-live"
VOICE = "Charon"

SYSTEM = """You are Cello, the editor that lives inside Flicello, a video editing studio. You are not a chat bot: you are a
calm, very capable presence on the person's screen who does what they say at once, with your own hands on the
controls, and who speaks up when you notice something.

How you work:
- Act first. When they ask for something, call the tool that does it right away; then say, in a few words, what you
  did ("Done: captions on, karaoke"). Never say something is done before the tool says it is.
- Look before you act when you need to: `look` tells you what is on the screen, the film's shots (numbered as they see
  them), its graphics, its score and what is going on. Look again after big changes.
- Turn their words into exact changes yourself with `edit` (shot numbers from `look`). Use `change` only for what
  can't be said as exact changes ("make the second half faster").
- Edits become a draft that plays at once; nothing is made for real until they say so (`make_it`).
- Speak briefly, like a sharp editor sitting next to them: one or two short sentences. No lists, no markdown, no
  repeating what they said.
- Speak first when an event comes in (a draft ready, a version made, a weak score, a failure): say what it means and
  offer the next move in one sentence. Stay quiet about routine progress.
- When something fails, call `diagnose`, then `repair` if it can be fixed; tell them plainly what happened.
- Remember lasting preferences with `remember` only when they say "always" or "never" or clearly mean it for every film.
- You can open films, start new ones, run every step, style, add graphics, effects, AI clips, music changes, exports,
  review links and settings. If something truly can't be done, say so in a sentence.
"""


def _shot(desc: str) -> dict:
    return {"type": "integer", "description": desc}


TOOLS = [
    {"name": "look", "description": "What is on the screen now and the open film's state: its stage, shots (numbered "
     "as they see them, with times and what they show), graphics, ready-to-post score, draft, notes, what is running.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "list_films", "description": "Their films, newest first, with their stage.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "open", "description": "Open a film by its title (or part of it), or a place: home, new, style, account.",
     "parameters": {"type": "object", "properties": {"what": {"type": "string"}}, "required": ["what"]}},
    {"name": "show", "description": "Point at and scroll to a part of the film page: preview, draft, timeline, styles, "
     "ready, exports, review, versions, brief.",
     "parameters": {"type": "object", "properties": {"part": {"type": "string"}}, "required": ["part"]}},
    {"name": "play", "description": "Play, pause or jump the video on screen (the draft if there is one).",
     "parameters": {"type": "object", "properties": {"do": {"type": "string", "enum": ["play", "pause", "seek"]},
                                                     "seconds": {"type": "number"}}, "required": ["do"]}},
    {"name": "edit", "description": "Exact changes to the open film's shots and graphics, made as a draft at once. "
     "ops: list of changes; each has op (remove, replace, swap, move, longer, shorter, speed, title, captions, lowerthird, "
     "transition, kinetic, callout, route, accent, style), shot (its number), and as needed other, beats, kind (speed: "
     "hold, cut, ramp, hit, stutter), text, sub, style, want, off (take it away), behind (title behind the person).",
     "parameters": {"type": "object", "properties": {
         "ops": {"type": "array", "items": {"type": "object", "properties": {
             "op": {"type": "string"}, "shot": {"type": "integer"}, "other": {"type": "integer"}, "beats": {"type": "number"},
             "kind": {"type": "string"}, "text": {"type": "string"}, "sub": {"type": "string"}, "style": {"type": "string"},
             "want": {"type": "string"}, "off": {"type": "boolean"}, "behind": {"type": "boolean"}}, "required": ["op", "shot"]}},
         "said": {"type": "array", "items": {"type": "string"}, "description": "One plain line per change."}},
         "required": ["ops"]}},
    {"name": "change", "description": "A change in their words for the editor AI to work out (a stretch to redo, a "
     "feeling); slower than edit.", "parameters": {"type": "object", "properties": {"words": {"type": "string"}},
                                                    "required": ["words"]}},
    {"name": "make_it", "description": "Make the draft on screen a real version (a full render).",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "discard", "description": "Throw the draft away.", "parameters": {"type": "object", "properties": {}}},
    {"name": "step", "description": "Run a step of making the film: start (watch the clips), confirm_understood, pick "
     "(a story: pitch number), another (more ideas), sample_ok (make the film), sample_again (with text), stop, "
     "continue_from (a version), retry.",
     "parameters": {"type": "object", "properties": {"action": {"type": "string"}, "pitch": {"type": "integer"},
                                                     "version": {"type": "integer"}, "text": {"type": "string"}},
                    "required": ["action"]}},
    {"name": "new_film", "description": "Start a new film (then they add clips).",
     "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "shape": {"type": "string",
                    "enum": ["9:16", "16:9", "both"]}, "seconds": {"type": "number"}, "note": {"type": "string"}},
                    "required": ["title"]}},
    {"name": "style", "description": "The film in an edit style, as a draft: cinematic, hype, travel, soft, documentary, camcorder.",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "fix_score", "description": "Fix a weak part of the ready-to-post score as a draft (its id from look).",
     "parameters": {"type": "object", "properties": {"part": {"type": "string"}}, "required": ["part"]}},
    {"name": "effect", "description": "Make a shot new: parallax (a 3D photo), remove (take something out: want), upscale.",
     "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["parallax", "remove", "upscale"]},
                                                     "shot": _shot("the shot"), "want": {"type": "string"}},
                    "required": ["kind", "shot"]}},
    {"name": "ai_clip", "description": "Make a clip with an AI video model from words, optionally into a shot's place.",
     "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}, "shot": _shot("the shot to replace")},
                    "required": ["prompt"]}},
    {"name": "music", "description": "Make a stretch of the film's made music again (the rest kept): start, end seconds "
     "(empty: the track's start or end), want (calmer, bigger drums...).",
     "parameters": {"type": "object", "properties": {"start": {"type": "number"}, "end": {"type": "number"},
                                                     "want": {"type": "string"}}, "required": ["want"]}},
    {"name": "export", "description": "Export the version: srt, feed (4:5), square, prores, gif, thumb, mp3, fcpxml, edl.",
     "parameters": {"type": "object", "properties": {"kind": {"type": "string"}}, "required": ["kind"]}},
    {"name": "platform", "description": "Make the film for a platform and remember it: instagram, tiktok, shorts, youtube.",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "review_link", "description": "A private review link for this version (for a client).",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "remember", "description": "Keep a lasting preference for all their films.",
     "parameters": {"type": "object", "properties": {"rule": {"type": "string"}}, "required": ["rule"]}},
    {"name": "diagnose", "description": "What went wrong: the film's failed steps and errors, and the system's health.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "repair", "description": "Repair what failed: try the failed step again (after diagnose).",
     "parameters": {"type": "object", "properties": {"note": {"type": "string"}}}},
]
BROWSER = {"open", "show", "play"}  # run by the page itself


def live_config() -> dict:
    return {"response_modalities": ["AUDIO"], "system_instruction": SYSTEM, "tools": [{"function_declarations": TOOLS}],
            "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": VOICE}}},
            "input_audio_transcription": {}, "output_audio_transcription": {}, "session_resumption": {},
            # patient turn-taking: a pause mid-sentence is not the end of what they're saying (BrewDeck's lesson)
            "realtime_input_config": {"automatic_activity_detection": {"silence_duration_ms": 1000,
                                                                       "end_of_speech_sensitivity": "END_SENSITIVITY_LOW"}},
            "context_window_compression": {"sliding_window": {}}}


def token() -> dict:
    """A one-session voice token for the browser, locked to Cello's model, voice, instructions and tools."""
    from google import genai

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Cello's voice needs GEMINI_API_KEY.")
    client = genai.Client(api_key=key, http_options={"api_version": "v1alpha"})
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    made = client.auth_tokens.create(config={
        "uses": 1, "expire_time": now + datetime.timedelta(minutes=30),
        "new_session_expire_time": now + datetime.timedelta(minutes=2),
        "live_connect_constraints": {"model": MODEL, "config": live_config()}})
    return {"token": made.name, "model": MODEL}


# ---------------------------------------------------------------- what is on screen

def look(studio, user: dict, project: dict | None) -> dict:
    out: dict = {"films": len(studio.projects(user))}
    if project is None:
        return {**out, "screen": "their films"}
    stage = project["stage"]
    job = studio.db.active_job(project["id"])
    info = {"screen": "a film", "title": project["title"], "stage": stage, "kind": project["kind"],
            "shape": project["shapes"], "length_s": project["length"], "version": project["version"],
            "running": {"step": job["kind"], "progress": round(float(job.get("progress") or 0), 2)} if job else None}
    draft = studio.draft_of(project) if stage == "done" else None
    if draft:
        info["draft"] = draft.get("said")
    if stage == "done" and project["kind"] == "montage":
        try:
            tl = studio.timeline_of(project, "draft" if draft else str(studio.work_version(project)))
            strip = studio.strip_of(project, studio.work_version(project)) or {}
            what = {c["n"]: c.get("what", "") for c in strip.get("cards") or []}
            info["shots"] = [{"n": s["n"], "from": s["start"], "to": s["end"], "beats": s["beats"], "kind": s["kind"],
                              "shows": what.get(s["n"], "")[:90]} for s in tl["shots"]]
            info["graphics"] = [{k: g[k] for k in ("kind", "shot", "text", "style") if g.get(k)} for g in tl["graphics"]]
            info["style"] = tl.get("style") or ""
        except Exception:  # noqa: BLE001 - what can be seen of it
            pass
        board = studio.ready_of(project)
        if board:
            info["score"] = {"total": board["score"], "parts": [{"id": p["id"], "score": p["score"], "why": p["why"][:120],
                                                                 "fix": p.get("fix_says")} for p in board["parts"]]}
        notes = studio.review_notes(project, open_only=True)[:8]
        if notes:
            info["client_notes"] = [f"{n['name'] or 'someone'} at {n['at']:.0f}s: {n['text'][:140]}" for n in notes]
    elif stage == "choose":
        info["ideas"] = [{"pitch": i + 1, "title": p["pitch"].get("title"), "logline": p["pitch"].get("logline", "")[:140]}
                         for i, p in enumerate(x for x in studio.pitches(project) if not x["rejected"])]
    if stage in ("failed", "paused"):
        info["trouble"] = diagnose(studio, project)
    return info


def diagnose(studio, project: dict) -> dict:
    """The film's last failed step with its error, and recent warnings."""
    job = studio.db.one("SELECT kind, status, error, attempts FROM jobs WHERE project_id=? AND status IN ('failed','waiting') "
                        "ORDER BY id DESC LIMIT 1", (project["id"],))
    events = studio.db.all("SELECT level, message FROM events WHERE project_id=? AND level IN ('error','warn') "
                           "ORDER BY id DESC LIMIT 5", (project["id"],))
    return {"stage": project["stage"], "failed": dict(job) if job else None,
            "recent": [f"{e['level']}: {' '.join(str(e['message']).split())[:200]}" for e in events]}


# ---------------------------------------------------------------- doing it

def act(studio, user: dict, project: dict | None, name: str, args: dict) -> dict:
    """One of Cello's tools, run with the same checks as the page's buttons. Returns what it should know and say, and
    what the page should do (`ui`: reload a part, go somewhere, point at something)."""
    from . import agent, exports, styles
    from .brain.schemas import ChangeOp

    args = dict(args or {})

    def need() -> dict:
        if project is None:
            raise ValueError("No film is open: open one first.")
        return studio.db.project(project["id"])

    if name == "look":
        return {"result": look(studio, user, studio.db.project(project["id"]) if project else None)}
    if name == "list_films":
        return {"result": [{"title": p["title"], "stage": p["stage"], "id": p["id"]} for p in studio.projects(user)[:20]]}
    if name == "edit":
        p = need()
        known = ("karaoke", "pop", "box", "rise", "slam", "mask", "split", "panels", "iris", "bars", "flash", "circle",
                 "arrow", "brackets", "sweep", "leak", "lines", "hold", "cut", "ramp", "hit", "stutter")

        def tidy(o: dict) -> dict:  # "karaoke style" -> "karaoke"; numbers that came as words
            o = {k: v for k, v in o.items() if v not in (None, "")}
            for key in ("style", "kind"):
                if isinstance(o.get(key), str):
                    o[key] = next((w for w in known if w in o[key].lower()), o[key].lower().strip())
            return o

        ops = [ChangeOp.model_validate(tidy(o)).model_dump() for o in args.get("ops") or []]
        if not ops:
            raise ValueError("No changes given.")
        said = [str(x) for x in args.get("said") or []] or [f"{o['op']} shot {o['shot']}" for o in ops]
        studio.ask_draft(user, p, ops, said, "; ".join(said))
        return {"result": "The draft is being laid out; it plays in a few seconds.", "ui": {"wait": "draft"}}
    if name == "change":
        p = need()
        studio.ask_change(user, p, str(args.get("words", "")))
        return {"result": "The editor is reading it into exact changes (about a minute); then they are shown.", "ui": {"wait": "job"}}
    if name == "make_it":
        studio.make_draft(user, need())
        return {"result": "Rendering the draft as a new version (a few minutes).", "ui": {"wait": "job"}}
    if name == "discard":
        studio.discard_draft(need())
        return {"result": "Draft discarded.", "ui": {"reload": True}}
    if name == "step":
        p = need()
        action = str(args.get("action", ""))
        if action == "retry":
            studio.retry(p)
        else:
            params = {k: args[k] for k in ("pitch", "version", "text") if args.get(k) not in (None, "")}
            if action == "pick" and "pitch" in params:  # their numbering: the n-th idea shown
                shown = [x for x in studio.pitches(p) if not x["rejected"]]
                params["pitch"] = shown[int(params["pitch"]) - 1]["id"] if 0 < int(params["pitch"]) <= len(shown) else 0
            agent.run_action(studio, user, p, action, params)
        return {"result": f"{action}: started.", "ui": {"reload": True}}
    if name == "new_film":
        shape = args.get("shape") or "9:16"
        p = studio.create_project(user, str(args.get("title") or "New film")[:80], shape,
                                  float(args.get("seconds") or 30), str(args.get("note") or ""))
        return {"result": f"Film '{p['title']}' started: add the clips.", "ui": {"go": f"/p/{p['id']}"}}
    if name == "style":
        sid = styles.read(f"{args.get('name', '')} style") or str(args.get("name", "")).lower()
        studio.try_style(user, need(), sid)
        return {"result": f"The {styles.STYLES[sid].name} style is being laid out as a draft.", "ui": {"wait": "draft"}}
    if name == "fix_score":
        studio.fix_ready(user, need(), str(args.get("part", "")))
        return {"result": "The fix is being laid out as a draft.", "ui": {"wait": "draft"}}
    if name == "effect":
        studio.ask_effect(user, need(), str(args.get("kind")), int(args.get("shot") or 1), str(args.get("want") or ""))
        return {"result": "Working on the effect (a few minutes); it comes back as a draft.", "ui": {"wait": "job"}}
    if name == "ai_clip":
        studio.generate_clip(user, need(), str(args.get("prompt", "")), int(args["shot"]) if args.get("shot") else None)
        return {"result": "An AI model is making the clip (a minute or two).", "ui": {"wait": "job"}}
    if name == "music":
        studio.edit_music(user, need(), args.get("start"), args.get("end"), str(args.get("want", "")))
        return {"result": "Making that stretch of the music again, then the film on it.", "ui": {"wait": "job"}}
    if name == "export":
        kind = str(args.get("kind", "")).lower()
        kind = {"4:5": "feed", "1:1": "square", "thumbnail": "thumb", "captions": "srt", "subtitles": "srt"}.get(kind, kind)
        studio.ask_export(user, need(), kind)
        return {"result": f"Making the {exports.KINDS[kind][0]}; it appears under the film.", "ui": {"wait": "job"}}
    if name == "platform":
        pid = exports.read_platform(f"for {args.get('name', '')}")
        if not pid:
            raise ValueError("Which platform: Instagram, TikTok, Shorts or YouTube?")
        return {"result": studio.set_platform(user, need(), pid), "ui": {"reload": True}}
    if name == "review_link":
        p = need()
        tok = studio.review_link(user, p)
        return {"result": f"Review link: {(studio.s.site_url or '').rstrip('/')}/r/{tok}", "ui": {"reload": True}}
    if name == "remember":
        prof = studio.profile(user)
        if prof.add(str(args.get("rule", ""))[:200], "Cello heard it", "you"):
            prof.save()
        return {"result": "Kept for all their films."}
    if name == "diagnose":
        return {"result": diagnose(studio, need()) if project else {"note": "no film open"}}
    if name == "repair":
        p = need()
        trouble = diagnose(studio, p)
        if p["stage"] in ("failed", "paused"):
            studio.retry(p)
            studio.db.event(p["id"], f"Cello repaired: retried {trouble['failed']['kind'] if trouble['failed'] else 'the step'}"
                            + (f" ({args['note'][:120]})" if args.get("note") else ""), "info")
            return {"result": "Retrying the failed step now.", "ui": {"wait": "job"}}
        return {"result": "Nothing has failed on this film.", "detail": trouble}
    raise ValueError(f"Cello has no tool called {name}.")


def events(studio, project: dict, since: float) -> list[str]:
    """What happened to the film since `since` (for Cello to speak first): steps done or failed."""
    rows = studio.db.all("SELECT kind, status, error, finished FROM jobs WHERE project_id=? AND finished IS NOT NULL AND "
                         "finished>? ORDER BY id", (project["id"], since))
    out = []
    for r in rows:
        if r["status"] == "done":
            out.append(f"[event] the {r['kind']} step finished")
        elif r["status"] == "failed":
            out.append(f"[event] the {r['kind']} step failed: {' '.join(str(r['error'] or '').split())[:200]}")
    return out


def now() -> float:
    return time.time()


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:12000]
