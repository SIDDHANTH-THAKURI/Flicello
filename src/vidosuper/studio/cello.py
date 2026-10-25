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

SYSTEM = """You are Cello, the editor that lives inside Flicello, a video editing studio. You are not a chat bot and
not a form: you are a calm, very capable editor sitting next to the person, with your own hands on the controls. They
should never feel they are operating software: they talk to you like a person, and you do it.

How you work:
- Act first. When they ask for something, call the tool that does it right away; then say, in a few words, what you
  did ("Done: captions on, karaoke"). Never say something is done before the tool says it is, and never promise to
  do it later ("I'll add it when..."): do it now, or say plainly why it can't be done yet and what's needed.
- Look before you act when you need to: `look` tells you the page they are on, the film's stage, its shots (numbered
  as they see them, with times and what they show), what is said in it, its graphics, its score and what is running.
  Look again after big changes. Never describe a page or a film you haven't looked at.
- "Cut the part where my eyes are closed", "after I walk...": `find` watches the film for it and says which shots;
  then change those with `edit`. Say what you found before cutting if it's more than a couple of shots.
- They point and say "this", "here", "that one", "make this bigger": call `pointing` to see what their cursor is on
  and where the video is playing, and act on that.
- You move the page for them: `page` scrolls, clicks a button or link by its words, types into a field, goes back.
  Use it whenever they ask you to do something on the page.

Starting a film is a conversation, not a form:
- "start a new video": call `new_film` at once with what you know (nothing is fine), then ask what it is about in one
  short question. "We'll figure it out as we build" is a perfectly good answer: you learn the film from the footage.
- Then `upload` to show them where their clips go (you can't open their files yourself; they tap or drop them).
- When clips are in, `step` start: you watch them. When that finishes, `look`, then tell them what you saw and heard
  in two sentences (for a talk: what they talk about) and suggest concrete moves (a thing for something they describe,
  captions, where to cut, music). They ask what's best: give your opinion plainly, like an editor would.
- They say "make it" / "make a good edit" / "go beast mode": use `brief` for anything they said about it (fast, beat
  sync, motion graphics, music, length, shape), then `build`: you carry it to the first cut yourself, picking for
  them. Ask one short question first only if something truly matters ("Any moment you want to open on, or shall I
  go all out?").
- Everything stays open: any change, at any time, in any order. Nothing is ever "past that step".
- Changes play at once as a draft. Make the real version (`make_it`) only when they clearly ask for it ("make it",
  "render it", "that's final"): an answer to a question of yours about something else is not a yes. A draft is
  ready only when an event says so: until then say it's being laid out.

Your hands for real work (`work`):
- Hand over anything that needs thinking or making: more than one simple step, anything creative (designed motion
  graphics like wings opening behind them or a ball that drops and bounces, a cartoon, a whole new edit, reworking
  the cut, music they describe), anything you have no direct tool for, or anything you're not sure how to do. Pass
  their words in full in `request` (and what you know of the moment: "when I say wings, at 0:02"). A Claude agent
  with every tool in Flicello does it in the background, designs and checks; you keep talking with them.
- Say in a few words that you're on it and roughly how long ("Designing the wings now; that takes a while, I'll tell
  you when it's in."). Don't do the same thing yourself at the same time.
- Your direct tools are for instant things: open, show, play, scroll, look, a single exact edit, captions, a style.
- Designed visuals are never faked: no stickers, emoji, clip art or captions standing in for a thing. That is `work`.
- Titles, captions, names, callouts, routes and kinetic words are for words.

Questions and answers:
- A "[question]" or "[work] question" note is something you must ask them: ask it in one short sentence with its
  options (a card with the options also appears beside you). Their reply, by voice: call `answer` with their words.
  They may also tap an option on the card; then a note tells you what they picked.
- "[work] done" or "[work] failed": tell them what it says, once, briefly, and what to look at.
- "Where I say X, put Y": any graphic takes `say` = their words, and lands exactly when they are said. Use the `says`
  of each shot from `look` to pick the moment and the shot.

How you talk:
- Always in English, unless they clearly ask you to speak another language. Their accent is not another language.
- If what you heard doesn't make sense (random words, another language, a game's name out of nowhere), you misheard:
  ask them to say it again. Never act on words you're not sure of, and never turn a mishearing into a change.
- Briefly, like a sharp editor sitting next to them: one or two short sentences. No lists, no markdown, no repeating
  what they said. Never say you can't before trying the tool that could.
- Notes in brackets that start "(Note, not them talking" come from Flicello, not the person: act on them, never read
  them out. Speak first when an event comes in: what it means and the next move, in one sentence, once. While a step
  is still running the film is not ready: say what is happening and how long, never "it's done".
- When something fails, call `diagnose`, then `repair` if it can be fixed; tell them plainly what happened.
- A "[service problem]" note means one of their services (Gemini, ElevenLabs, Claude, Higgsfield) can't do its part
  right now: say which, what it means and what to do, once, in a sentence ("Your ElevenLabs credit is out, so I
  can't make music until you top it up; everything else carries on.").
- Remember lasting preferences with `remember` only when they say "always" or "never" or clearly mean it for every film.
"""


def _shot(desc: str) -> dict:
    return {"type": "integer", "description": desc}


TOOLS = [
    {"name": "look", "description": "What is on the screen now and the open film's state: its stage, shots (numbered "
     "as they see them, with times and what they show), graphics, ready-to-post score, draft, notes, what is running.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "list_films", "description": "Their films, newest first, with their stage.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "open", "description": "Open a film by its title (or part of it), or a place: home, new, style, account, "
     "editor (the open film on a timeline, to cut by hand).",
     "parameters": {"type": "object", "properties": {"what": {"type": "string"}}, "required": ["what"]}},
    {"name": "show", "description": "Point at and scroll to a part of the film page, opening it if it is tucked away: "
     "preview, draft, timeline, styles, ready, exports, review, versions, brief, clips, story.",
     "parameters": {"type": "object", "properties": {"part": {"type": "string"}}, "required": ["part"]}},
    {"name": "play", "description": "Play, pause or jump the video on screen (the draft if there is one).",
     "parameters": {"type": "object", "properties": {"do": {"type": "string", "enum": ["play", "pause", "seek"]},
                                                     "seconds": {"type": "number"}}, "required": ["do"]}},
    {"name": "pointing", "description": "What their cursor is on right now (a button, a shot, a spot in the video with "
     "its time and where in the frame), where the video on screen is playing, and what the page shows. Call it when "
     "they say this, here, that, there.", "parameters": {"type": "object", "properties": {}}},
    {"name": "page", "description": "Do something on the page for them: scroll (up, down, top, bottom), click a "
     "button or link by its words, type into a field (its label or placeholder words, and the text), or go back.",
     "parameters": {"type": "object", "properties": {
         "do": {"type": "string", "enum": ["scroll", "click", "type", "back"]},
         "where": {"type": "string", "description": "scroll: up, down, top or bottom; click / type: the words on it."},
         "text": {"type": "string", "description": "type: what to type."}}, "required": ["do"]}},
    {"name": "upload", "description": "Show them where their clips go on the film page (lit up, ready to tap or drop "
     "files on). You can't pick their files yourself.", "parameters": {"type": "object", "properties": {}}},
    {"name": "brief", "description": "What the film is (all optional). title: its name, any time (name it once you "
     "know what it is about). The rest while it is still being set up: kind (montage: clips cut to music; talk: someone talking to camera; voice: only a recording), shape (9:16, 16:9, "
     "both), seconds, music (made: made for it; none), sound (natural: the clips' own sound kept; silent), note (what "
     "they want, in their words: fast cuts, beat sync, motion graphics, beast mode...).",
     "parameters": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["montage", "talk", "voice"]},
         "shape": {"type": "string", "enum": ["9:16", "16:9", "both"]}, "seconds": {"type": "number"},
         "music": {"type": "string", "enum": ["made", "none"]}, "sound": {"type": "string", "enum": ["natural", "silent"]},
         "note": {"type": "string"}, "title": {"type": "string"}}}},
    {"name": "find", "description": "Watch the film (the version they work on) for where something happens: 'my "
     "eyes are closed', 'I walk', 'the dog jumps'. Returns the moments (seconds) and the shots they fall in, to cut or "
     "change with edit. Takes about half a minute.",
     "parameters": {"type": "object", "properties": {"what": {"type": "string"}}, "required": ["what"]}},
    {"name": "work", "description": "Hand a request to your hands: a Claude agent that can do anything in Flicello "
     "(edit, design motion graphics, rework the cut, write a cartoon, music...), works in the background and reports "
     "back. Use it for anything beyond one instant step. Pass their words in full.",
     "parameters": {"type": "object", "properties": {
         "request": {"type": "string", "description": "What they want, in their words, complete."},
         "context": {"type": "string", "description": "What you know that helps: the moment (seconds), what's on "
                                                      "screen, what they said before."}},
         "required": ["request"]}},
    {"name": "answer", "description": "Their answer to the open question (yours or your hands'), in their words.",
     "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "build", "description": "Carry the film to its first cut yourself: watch the clips if not done, take what "
     "was understood as right, pick the strongest idea, make the film. They can step in at any time.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "edit", "description": "Exact changes to the open film's shots and graphics, made as a draft at once. "
     "ops: list of changes; each has op (remove, replace, swap, move, longer, shorter, speed, title, captions, lowerthird, "
     "transition, kinetic, callout, route, accent, style), shot (its number), and as needed other, beats, kind "
     "(speed: hold, cut, ramp, hit, stutter), text, sub, style, want, off (take it away), behind (title behind the "
     "person), say (any graphic: the words it comes in on, wherever they are said in the film), scale (title, "
     "captions, name, kinetic, callout: how big against the usual, 1.3 = bigger, 0.8 = smaller; without text it "
     "resizes the one there), place (top, middle or bottom; without text it moves the one there).",
     "parameters": {"type": "object", "properties": {
         "ops": {"type": "array", "items": {"type": "object", "properties": {
             "op": {"type": "string"}, "shot": {"type": "integer"}, "other": {"type": "integer"}, "beats": {"type": "number"},
             "kind": {"type": "string"}, "text": {"type": "string"}, "sub": {"type": "string"}, "style": {"type": "string"},
             "want": {"type": "string"}, "off": {"type": "boolean"}, "behind": {"type": "boolean"},
             "anchor": {"type": "string"}, "say": {"type": "string"}, "scale": {"type": "number"},
             "place": {"type": "string", "enum": ["top", "middle", "bottom"]}}, "required": ["op", "shot"]}},
         "said": {"type": "array", "items": {"type": "string"}, "description": "One plain line per change."}},
         "required": ["ops"]}},
    {"name": "timeline", "description": "Exact cuts on what's on screen, in seconds, made at once as the draft (what "
     "the editor's own hands do; look first for the shots' times). Each op: trim (shot, edge start|end, seconds + longer "
     "/ - shorter, ripple), split (shot, seconds into it), delete (shot, ripple: the film closes up), move (shot, to), "
     "duplicate (shot), speed (shot, factor 0.25-4), frame (shot: zoom 1-2.5, frame_x, push -0.4..0.4, flip), keys "
     "(shot, keys: [[seconds into it, zoom 1-3, x, y], ...] framing keyframes), volume (shot, gain_db, mute), blend "
     "(shot, style cut|dissolve|fadeblack|fadewhite|smoothleft|smoothright|zoomin, seconds), graphic (index as look "
     "lists them: text, sub, style, scale, place, at, until seconds, remove), over (a shot laid over the picture, B-roll "
     "with the film's sound going on: index -1 adds one with clip (short id like c03), src_in, at, until; an index "
     "moves, trims or removes it), music (music: slide, gain_db, fade_in, fade_out, duck_db, points [{t, db}], jumps "
     "[{at, to}]: the whole music edit as it should be).",
     "parameters": {"type": "object", "properties": {
         "ops": {"type": "array", "items": {"type": "object", "properties": {
             "op": {"type": "string"}, "shot": {"type": "integer"}, "edge": {"type": "string", "enum": ["start", "end"]},
             "seconds": {"type": "number"}, "ripple": {"type": "boolean"}, "to": {"type": "integer"},
             "factor": {"type": "number"}, "zoom": {"type": "number"}, "frame_x": {"type": "number"},
             "push": {"type": "number"}, "flip": {"type": "boolean"},
             "keys": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
             "gain_db": {"type": "number"}, "mute": {"type": "boolean"}, "style": {"type": "string"},
             "index": {"type": "integer"}, "text": {"type": "string"}, "sub": {"type": "string"},
             "scale": {"type": "number"}, "place": {"type": "string"}, "at": {"type": "number"},
             "until": {"type": "number"}, "remove": {"type": "boolean"}, "clip": {"type": "string"},
             "src_in": {"type": "number"},
             "music": {"type": "object", "properties": {
                 "slide": {"type": "number"}, "gain_db": {"type": "number"}, "fade_in": {"type": "number"},
                 "fade_out": {"type": "number"}, "duck_db": {"type": "number"},
                 "points": {"type": "array", "items": {"type": "object", "properties": {
                     "t": {"type": "number"}, "db": {"type": "number"}}}},
                 "jumps": {"type": "array", "items": {"type": "object", "properties": {
                     "at": {"type": "number"}, "to": {"type": "number"}}}}}}},
             "required": ["op"]}},
         "said": {"type": "array", "items": {"type": "string"}, "description": "One plain line per change (optional)."}},
         "required": ["ops"]}},
    {"name": "change", "description": "A change in their words for the editor AI to work out (a stretch to redo, a "
     "feeling); slower than edit.", "parameters": {"type": "object", "properties": {"words": {"type": "string"}},
                                                    "required": ["words"]}},
    {"name": "make_it", "description": "Make the draft on screen a real version (a full render).",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "discard", "description": "Throw the draft away.", "parameters": {"type": "object", "properties": {}}},
    {"name": "step", "description": "Run a step of making the film: start (watch the clips), confirm_understood, pick "
     "(a story: pitch number), another (more ideas), sample_ok (make the film), sample_again (with text), stop, "
     "continue_from (a version), retry, confirm_change (make the changes shown for their OK), cancel_change (drop "
     "them).",
     "parameters": {"type": "object", "properties": {"action": {"type": "string"}, "pitch": {"type": "integer"},
                                                     "version": {"type": "integer"}, "text": {"type": "string"}},
                    "required": ["action"]}},
    {"name": "new_film", "description": "Start a new film at once, with whatever is known (nothing is fine: a name "
     "comes from what it turns out to be). Then show them where the clips go.",
     "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "kind": {"type": "string",
                    "enum": ["montage", "talk", "voice"]}, "shape": {"type": "string", "enum": ["9:16", "16:9", "both"]},
                    "seconds": {"type": "number"}, "note": {"type": "string"}}}},
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
BROWSER = {"open", "show", "play", "pointing", "page", "upload"}  # run by the page itself


def live_config() -> dict:
    return {"response_modalities": ["AUDIO"], "system_instruction": SYSTEM, "tools": [{"function_declarations": TOOLS}],
            "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": VOICE}},
                              "language_code": "en-US"},  # English, whatever accent it hears
            "input_audio_transcription": {}, "output_audio_transcription": {}, "session_resumption": {},
            # patient turn-taking: a pause mid-sentence is not the end of what they're saying (BrewDeck's lesson)
            "realtime_input_config": {"automatic_activity_detection": {"silence_duration_ms": 1000,
                                                                       "end_of_speech_sensitivity": "END_SENSITIVITY_LOW"}},
            "context_window_compression": {"sliding_window": {}}}


def voice_mode() -> str:
    """Where Cello's voice comes from: "local" (this machine: Whisper, Claude on their plan, Kokoro; local/voice.py)
    or "gemini" (Gemini Live with their key). VIDO_CELLO_VOICE_MODE chooses; by default this machine when it can."""
    from ..local import voice

    want = os.environ.get("VIDO_CELLO_VOICE_MODE", "auto").strip().lower()
    if want in ("local", "gemini"):
        return want
    if voice.available():
        return "local"
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return "gemini"
    raise RuntimeError("Cello has no voice yet: download its voice on the Connect page (no key needed), or add a "
                       "Gemini key there.")


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

SIZE_WORDS = [(r"\b(?:much|way|a lot|far|really)\s+(?:bigger|larger)\b|\bhuge\b|\bmassive\b", 1.6),
              (r"\bbigger\b|\blarger\b|\bbig\b", 1.3),
              (r"\b(?:much|way|a lot|far|really)\s+smaller\b|\btiny\b", 0.65), (r"\bsmaller\b|\bsmall\b", 0.8)]


def sized(o: dict) -> dict:
    """A size or a place said in a graphic change's words ('bigger, at the top') made its `scale` and `place`: the
    words alone change nothing for captions, titles or names."""
    import re

    if o.get("op") not in ("captions", "title", "lowerthird", "kinetic", "callout") or not o.get("want"):
        return o
    said = str(o["want"]).lower()
    if o.get("scale") is None:
        o["scale"] = next((k for pattern, k in SIZE_WORDS if re.search(pattern, said)), None)
    if o.get("place") is None and o["op"] != "callout":  # a callout's words are what it points at ('the top of it')
        o["place"] = next((w for w in ("top", "bottom", "middle") if re.search(rf"\b{w}\b", said.replace(
            "centre", "middle").replace("center", "middle"))), None)
    o = {k: v for k, v in o.items() if v is not None}
    if o["op"] != "callout" and (o.get("scale") or o.get("place")):
        o.pop("want")  # read: what was left is no subject of theirs
    return o


def look(studio, user: dict, project: dict | None) -> dict:
    out: dict = {"films": len(studio.projects(user))}
    if project is None:
        return {**out, "screen": "their films"}
    stage = project["stage"]
    clips = studio.db.all("SELECT name FROM clips WHERE project_id=? ORDER BY rowid", (project["id"],))
    job = studio.db.active_job(project["id"])
    info = {"screen": "a film", "title": project["title"], "stage": stage, "kind": project["kind"],
            "shape": project["shapes"], "length_s": project["length"], "version": project["version"],
            "running": {"step": job["kind"], "progress": round(float(job.get("progress") or 0), 2)} if job else None,
            "clips": [c["name"] for c in clips][:20] or "none yet",
            "talking_film": bool(studio._info(project).get("speech")) if project["kind"] == "montage" else None}
    if stage in ("understood", "choose", "review", "done"):
        u = studio.understanding(project) or {}
        if u.get("story"):
            info["understood"] = " ".join(str(u["story"]).split())[:500]
        best = [f"{c.get('short')}: {c.get('best')}" for c in u.get("clips") or [] if c.get("best")][:5]
        if best:
            info["best_moments"] = best
        if studio._info(project).get("speech") and stage != "done":
            heard = spoken(studio, project)
            if heard:
                info["what_they_say"] = heard
    if stage == "setup":
        info["next"] = ("they add their clips (call upload to show them where)" if not clips else
                        "the clips are in: step start watches them (or build carries it all the way)")
    if stage == "confirm":
        pending = studio._info(project).get("pending") or {}
        info["waiting_for_ok"] = pending.get("said") or []
        if pending.get("unclear"):
            info["unclear"] = pending["unclear"]
        info["next"] = ("these changes, read from their words, wait for their OK on screen: step confirm_change makes "
                        "them, step cancel_change drops them; nothing else changes the film until then")
    draft = studio.draft_of(project) if stage == "done" else None
    if draft and int(draft.get("base") or 0) != studio.work_version(project):
        draft = None  # made on another version: not what their screen shows
    if draft:
        info["draft"] = draft.get("said")
        info["next"] = ("a draft plays on their screen: make_it renders it as a new version, discard drops it; edits "
                        "made now are made on it")
    if stage in ("done", "confirm") and project["kind"] == "montage":
        try:
            tl = studio.timeline_of(project, "draft" if draft else str(studio.work_version(project)))
            strip = studio.strip_of(project, studio.work_version(project)) or {}
            what = {c["n"]: c.get("what", "") for c in strip.get("cards") or []}
            words = said_words(studio, project, studio.work_version(project))
            info["shots"] = [{"n": s["n"], "from": s["start"], "to": s["end"], "beats": s["beats"], "kind": s["kind"],
                              "shows": what.get(s["n"], "")[:90],
                              **({"says": " ".join(w for t, w in words if s["start"] <= t < s["end"])[:200]}
                                 if words else {})} for s in tl["shots"]]
            info["graphics"] = [{"index": k, **{x: g[x] for x in ("kind", "shot", "text", "style") if g.get(x)},
                                 **({"scale": g["scale"]} if g.get("scale", 1.0) != 1.0 else {}),
                                 **({"place": g["place"]} if g.get("place", "auto") != "auto" else {}),
                                 **({"from": g["at"], "to": g["until"]} if g.get("at") is not None else {})}
                                for k, g in enumerate(tl["graphics"])]
            if tl.get("over"):  # shots laid over the picture (B-roll), by index
                info["laid_over"] = [{"index": k, "from": o["at"], "to": o["until"], "clip": o["clip"],
                                      "clip_from": o["src_in"]} for k, o in enumerate(tl["over"])]
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


def spoken(studio, project: dict) -> str:
    """Everything said in their clips, in order (a talking film before its first cut)."""
    from ..perceive.prep import load_transcript
    from ..workspace import Project
    from .storage import workdir

    try:
        ws = Project(workdir(studio.s, project["user_id"], project["id"]).clips, studio.cfg)
        lines = []
        for cid in ws.clips():
            t = load_transcript(ws, cid)
            if t is not None:
                lines += [u.text.strip() for u in t.utterances if u.confident]
        return " ".join(lines)[:1200]
    except Exception:  # noqa: BLE001 - the rest of what it sees
        return ""


def said_words(studio, project: dict, version: int) -> list[tuple[float, str]]:
    """What is said in the version, word by word with its film time (empty: nobody speaks, or it can't be read)."""
    from ..edit.timeline import build_timeline
    from ..motion.place import speech_words
    from ..perceive.prep import load_transcript
    from ..schemas import EDL
    from .storage import workdir
    from . import versions
    from ..workspace import Project

    try:
        work = workdir(studio.s, project["user_id"], project["id"])
        edl = EDL.model_validate_json(versions.edl_path(work.out, version, studio.shapes(project)[0])
                                      .read_text(encoding="utf-8"))
        ws = Project(work.clips, studio.cfg)
        said = {cid: t for cid in {seg.clip_id for seg in edl.segments} if (t := load_transcript(ws, cid)) is not None}
        return [(round(w.s, 2), w.w) for w in speech_words(build_timeline(edl, ws.clips()), said)]
    except Exception:  # noqa: BLE001 - the shots without their words
        return []


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
                 "arrow", "brackets", "sweep", "leak", "lines", "hold", "cut", "ramp", "hit", "stutter", "drop",
                 "bounce", "pop", "fly", "float", "orbit", "spin", "shake", "burst", "rain", "grow")

        def tidy(o: dict) -> dict:  # "karaoke style" -> "karaoke"; numbers that came as words
            o = {k: v for k, v in o.items() if v not in (None, "")}
            for key in ("style", "kind"):
                if isinstance(o.get(key), str):
                    o[key] = next((w for w in known if w in o[key].lower()), o[key].lower().strip())
            if isinstance(o.get("place"), str):  # "the top", "centre"
                where = o.pop("place").lower().replace("centre", "middle").replace("center", "middle")
                o.update({"place": w for w in ("top", "middle", "bottom") if w in where})
            return sized(o)

        if any(str(o.get("op", "")).lower() == "thing" for o in args.get("ops") or []):
            raise ValueError("Designed motion graphics aren't in yet (they're being built): don't fake them with "
                             "stickers; say so plainly.")
        ops = [ChangeOp.model_validate(tidy(o)).model_dump() for o in args.get("ops") or []]
        if p["kind"] != "montage":
            raise ValueError("This film is a talking video made as scenes: its shots can't be changed one by one yet; "
                             "use change with their words.")
        if not ops:
            raise ValueError("No changes given.")
        said = [str(x) for x in args.get("said") or []] or [f"{o['op']} shot {o['shot']}" for o in ops]
        studio.ask_draft(user, p, ops, said, "; ".join(said))
        return {"result": "The draft is being laid out; it plays in a few seconds.", "ui": {"wait": "draft"}}
    if name == "timeline":
        p = need()
        from .editing import apply as by_hand

        done = by_hand(studio, user, p, list(args.get("ops") or []), [str(x) for x in args.get("said") or []])
        return {"result": "Made, playing as the draft: " + "; ".join(done), "ui": {"reload": True}}
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
            agent.run_action(studio, user, p, action, params)  # pick: their numbering (the n-th idea shown)
        return {"result": f"{action}: started.", "ui": {"reload": True}}
    if name == "new_film":
        kind = "voice" if args.get("kind") == "voice" else "montage"  # a talk is a film cut on its words (speech_cut)
        shape = args.get("shape") if args.get("shape") in ("9:16", "16:9", "both") else "9:16"
        p = studio.create_project(user, str(args.get("title") or "")[:80], shape,
                                  float(args.get("seconds") or 30),
                                  str(args.get("note") or ""), kind=kind)
        if args.get("kind") == "talk":  # a guess of "montage" is left to the footage (a talk is found when watched)
            studio._set_info(p, speech=True)
        return {"result": f"Film started ('{p['title']}', a name for now). Next: their clips. Call upload to show them "
                          "where, and ask what it's about if you don't know.", "ui": {"go": f"/p/{p['id']}"}}
    if name == "brief":
        p = need()
        said = []
        if args.get("title"):
            said.append(f"named '{studio.rename(p, str(args['title']))}'")
            p = studio.db.project(p["id"])
        if p["stage"] != "setup":
            if said and not any(args.get(k) for k in ("kind", "shape", "seconds", "music", "sound", "note")):
                return {"result": "; ".join(said), "ui": {"reload": True}}
            raise ValueError("The film is past setting up: change it with edits now (or step back to the clips).")
        if args.get("kind") in ("montage", "talk", "voice"):
            engine = "voice" if args["kind"] == "voice" else "montage"  # a talk is cut on its words, as a film
            if engine != p["kind"]:
                studio.set_kind(p, engine)
                p = studio.db.project(p["id"])
            if engine == "montage":
                studio._set_info(p, speech=args["kind"] == "talk")
            said.append({"talk": "a talking video, cut on what they say", "montage": "clips cut to music",
                         "voice": "a recording made into animation"}[args["kind"]])
        if args.get("shape") or args.get("seconds"):
            said.append(studio.set_settings(p, args.get("shape") or p["shapes"],
                                            float(args["seconds"]) if args.get("seconds") else None))
            p = studio.db.project(p["id"])
        if args.get("music") in ("made", "none"):
            studio.set_music(p, args["music"])
            said.append("music made for it" if args["music"] == "made" else "no music")
            p = studio.db.project(p["id"])
        if args.get("sound") in ("natural", "silent"):
            studio.set_sound(p, args["sound"])
            said.append("the clips' own sound kept" if args["sound"] == "natural" else "the clips' sound off")
            p = studio.db.project(p["id"])
        if args.get("note"):
            studio.add_note(p, str(args["note"])[:400])
            said.append(f"noted: {args['note'][:120]}")
        return {"result": "; ".join(str(x) for x in said) or "Nothing to change.", "ui": {"reload": True}}
    if name == "work":
        request = str(args.get("request", "")).strip()
        if args.get("context"):
            request += f"\n(What Cello knows: {args['context']})"
        row = studio.work.start(user, studio.db.project(project["id"]) if project else None, request)
        if row["status"] == "queued":
            return {"result": "Queued: your hands are finishing the last task first; this starts right after."}
        return {"result": "Your hands are on it, in the background. Tell them in a few words; you'll get a note when "
                          "it asks something, finishes or fails."}
    if name == "answer":
        open_q = studio.db.one("SELECT id FROM work WHERE user_id=? AND project_id IS ? AND status='asking' "
                               "ORDER BY updated DESC LIMIT 1", (user["id"], project["id"] if project else None))
        if open_q is None:
            return {"result": "There's no open question; treat what they said as a new request."}
        row = studio.work.answer(user, open_q["id"], str(args.get("text", "")))
        return {"result": row["say"] or "Got it: carrying on with their answer."}
    if name == "find":
        found = studio.find_moments(user, need(), str(args.get("what", "")))
        if not found["moments"]:
            return {"result": f"Watched version {found['version']}: \"{found['looked_for']}\" never happens in it."}
        return {"result": found}
    if name == "build":
        p = need()
        if p["stage"] == "done":
            return {"result": "It's already made: change it with edits."}
        if p["stage"] == "setup":
            if not studio.db.one("SELECT 1 FROM clips WHERE project_id=? LIMIT 1", (p["id"],)) and \
                    not studio.db.one("SELECT 1 FROM uploads WHERE project_id=? LIMIT 1", (p["id"],)):
                raise ValueError("There are no clips yet: call upload and ask them to add them.")
        studio._set_info(p, autopilot=True)
        nxt = {"setup": ("start", {}), "understood": ("confirm_understood", {}), "choose": ("pick", {"pitch": 1}),
               "review": ("sample_ok", {})}.get(p["stage"])
        if nxt:
            agent.run_action(studio, user, p, nxt[0], nxt[1])
        return {"result": "Building it: I'll carry each step on to the first cut and pick for them (a few minutes). "
                          "Say what you're doing as it goes.", "ui": {"wait": "job"}}
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
        return {"result": f"Review link: http://127.0.0.1:{studio.s.port}/r/{tok}", "ui": {"reload": True}}
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
    said = studio.db.one("SELECT body FROM messages WHERE project_id=? AND role='editor' ORDER BY id DESC LIMIT 1",
                         (project["id"],)) if rows else None
    for r in rows:
        if r["status"] == "done":
            out.append(f"[event] the {r['kind']} step finished")
        elif r["status"] == "failed":
            out.append(f"[event] the {r['kind']} step failed: {' '.join(str(r['error'] or '').split())[:200]}")
    if said and any(r["status"] == "done" for r in rows):
        try:
            text = json.loads(said["body"]).get("text", "")
        except ValueError:
            text = ""
        if text:
            out.append(f"[what it found] {' '.join(text.split())[:700]}")
    if not out:
        return out  # nothing happened: nothing to say (never the same note again and again)
    job = studio.db.active_job(project["id"])
    if job:  # the next step already running: the film is NOT ready yet
        out.append(f"[still working] the {job['kind']} step is running now ({round(float(job.get('progress') or 0) * 100)}%): "
                   "the film is not ready yet; say what is happening, never that it is done")
    elif studio._info(project).get("autopilot"):
        out.append("[building] you are carrying it on to the first cut by yourself")
    return out


def work_events(studio, user: dict, project: dict | None, since: float) -> list[str]:
    """What Cello's hands (and Flicello's own questions) did since `since`: a question to ask, done, failed."""
    out = []
    rows = studio.db.all("SELECT * FROM work WHERE user_id=? AND project_id IS ? AND updated>? AND status IN "
                         "('asking','done','failed') ORDER BY updated", (user["id"], project["id"] if project else None,
                                                                         since))
    for r in rows:
        if r["status"] == "asking":
            opts = json.loads(r["options"] or "[]")
            tag = "[question]" if r["kind"] == "system" else "[work] question"
            out.append(f"{tag} {(r['say'] or '').strip()} {r['question']}" + (f" (options: {' / '.join(opts)})" if opts
                                                                               else ""))
        elif r["status"] == "done" and r["kind"] != "system":
            out.append(f"[work] done: {r['say']}")
        elif r["status"] == "failed":
            out.append(f"[work] failed: {r['say']}")
    return out


def service_events(since: float) -> list[str]:
    """A service failing since `since` (credit out, key refused, limits, outages), for Cello to say plainly."""
    from ..local import health

    return [f"[service problem] {p['service']}: {p['means']}; what to do: {p['todo']}" for p in health.problems(since)]


def now() -> float:
    return time.time()


LOG_KINDS = ("you", "cello", "tool", "result", "note", "event", "error", "line")
LOG_MAX = 20_000_000  # bytes per person's log before it rolls over


def log_lines(studio, user: dict, pid: str | None, lines: list, owner: bool) -> int:
    """Cello's conversation kept, line by line (logs/cello/<person>.jsonl), to see later what it heard, said and did
    and fix what went wrong. The owner's in full; anyone else's without what was said (tool calls, events, failures)."""
    folder = studio.s.logs_dir / "cello"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{user['id']}.jsonl"
    if path.exists() and path.stat().st_size > LOG_MAX:
        path.replace(path.with_suffix(".old.jsonl"))
    kept = 0
    with path.open("a", encoding="utf-8") as f:
        for x in lines[:200]:
            if not isinstance(x, dict) or x.get("who") not in LOG_KINDS:
                continue
            if x["who"] == "line":  # the voice line closed: Google's reason may be the key or the credit
                from ..local import health

                health.failed("gemini", str(x.get("text", "")))
            if not owner and x["who"] in ("you", "cello", "note"):
                continue  # what a friend says stays theirs
            f.write(json.dumps({"at": round(float(x.get("at") or time.time()), 3), "pid": pid, "who": x["who"],
                                "text": str(x.get("text", ""))[:4000]}, ensure_ascii=False) + "\n")
            kept += 1
    return kept


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:12000]
