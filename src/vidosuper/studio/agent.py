"""Your editor: one AI editor per person, the same across all their films (the main agent).

It is a stored identity, not a running process. Each turn it reads what it knows about the person (their style rules,
their answers to the starting questions, their earlier films and how they rated them), the film's spec (spec.py),
where the film is and what is on their screen, and the conversation. It answers in plain words and can:
- change the spec: what they said becomes an item with its check, or an item is switched off;
- ask one question with tap answers;
- offer the next step as a card (nothing runs until they tap it);
- offer to keep a preference for their next films (saved only on a yes).

The steps it offers are the studio's jobs, run by the specialists: watching the clips, the stories, the sample, the
film, reading changes. After each one it checks in, written by code from the step's real results, so the numbers are
exact and a check-in costs nothing.

Its turns run on the web server, beside the person, never on a render lane: a reply takes seconds even while a film
renders.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

from . import spec as specs
from .brain import BudgetExceeded
from .brain.backends import CACHE_SPLIT

log = logging.getLogger("vidosuper.studio.agent")

MAX_WORDS = 2000  # one message, in characters
RECENT = 24  # messages of the conversation the editor reads in full each turn
TURNS_PER_HOUR = 60  # a safety net against a stuck page, not a meter (the owner has none)


class TalkError(ValueError):
    """Shown to the person as it is."""


# ---------------------------------------------------------------- what the editor can offer, stage by stage

@dataclass(frozen=True)
class Action:
    id: str
    stages: tuple[str, ...]
    label: str  # the card's button
    job: str | None  # the step it starts (how long it takes); None: instant
    ai: bool  # the step uses the AI
    needs: str = ""  # "pitch" | "version" | "text" (what the offer must say)
    what: str = ""  # for the editor: what it does


ACTIONS = (
    Action("start", ("setup",), "Watch my clips", "prepare", True, "",
           "watch the clips (and listen to the song), then say back what I see"),
    Action("confirm_understood", ("understood",), "That's right: next step", "pitch", True, "",
           "take 'what I understood' as it is on screen and go on to the stories (or looks)"),
    Action("pick", ("choose",), "Make a sample of this one", "sample", True, "pitch",
           "make a short sample of story (or look) number N"),
    Action("another", ("choose",), "Another idea", "pitch", True, "",
           "one more story idea (their idea or what was missing goes in `text`)"),
    Action("sample_ok", ("review",), "Looks right: make the film", "render", True, "",
           "make the whole film from the sample on screen"),
    Action("sample_again", ("review",), "Make the sample again", "sample", True, "text",
           "make the sample again, doing what `text` says"),
    Action("change", ("done",), "Read these changes", "changes", True, "text",
           "read the changes in `text` into exact edits on the numbered shots, shown to them before anything is made"),
    Action("continue_from", ("done",), "Work on this version", None, False, "version",
           "make version N the one the next change starts from"),
    Action("make_draft", ("done",), "Make it", "revise", False, "",
           "make the draft on screen (the change they watched in the instant preview) into a real version"),
    Action("discard_draft", ("done",), "Discard the draft", None, False, "",
           "drop the draft on screen; the version stays as it was"),
    Action("cut_person", ("done",), "Cut their shots", "draft", False, "person",
           "a draft without the shots this person is close up in (person id in `person`)"),
    Action("never_person", ("setup", "understood", "choose", "review", "done"), "Never show them", None, False, "person",
           "never show this person in this film: every shot of them is kept out from now on (person id in `person`)"),
    Action("confirm_change", ("confirm",), "Make these changes", "revise", False, "",
           "make the changes as read (on screen)"),
    Action("cancel_change", ("confirm",), "Don't change anything", None, False, "",
           "drop the changes as read; the version stays as it is"),
    Action("stop", ("preparing", "pitching", "sampling", "rendering", "revising", "composing", "reading"),
           "Stop this step", None, False, "", "stop the step that is running and go back one step"),
)
ACTION = {a.id: a for a in ACTIONS}


def available(studio, project: dict, user: dict) -> list[str]:
    """The ids the editor may offer now: right for the stage, and possible (clips to watch, ideas left...)."""
    stage, pid = project["stage"], project["id"]
    busy = studio.db.active_job(pid) is not None
    out = []
    for a in ACTIONS:
        if stage not in a.stages or (busy and a.id != "stop"):
            continue
        if a.id == "start" and not studio.db.value("SELECT COUNT(*) FROM clips WHERE project_id=?", (pid,), 0) \
                and project["kind"] == "montage":
            continue
        if a.id == "confirm_understood" and not project.get("understood"):
            continue
        if a.id in ("pick",) and not [p for p in studio.pitches(project) if not p["rejected"]]:
            continue
        if a.id == "another" and studio.ideas_left(project) <= 0:
            continue
        if a.id == "sample_again" and studio.sample_tries_left(project) <= 0:
            continue
        if a.id == "continue_from" and len(studio.versions_list(project)) < 2:
            continue
        if a.id in ("make_draft", "discard_draft") and not studio.draft_of(project):
            continue
        if a.id in ("cut_person", "never_person") and not (studio.people_of(project) or {}).get("people"):
            continue
        out.append(a.id)
    return out


def takes(studio, a: Action) -> str:
    if a.job is None:
        return "instant"
    secs = studio.typical_seconds(a.job)
    return "under a minute" if secs < 60 else f"about {max(1, round(secs / 60))} min"


def _understood_form(studio, project: dict) -> dict:
    """'What I understood' as it is on screen, as the page's own form would send it."""
    u = studio.understanding(project) or {}
    form = {"story": u.get("story", ""), "feel": ", ".join(u.get("feel") or []), "must": "\n".join(u.get("must") or []),
            "avoid": "\n".join(u.get("avoid") or [])}
    if project["kind"] == "montage":
        form.update(music=u.get("music", ""), leave_out=[])
    else:
        form["parts"] = "\n".join((f"{int(x['at']) // 60}:{int(x['at']) % 60:02d} " if x.get("at") is not None else "")
                                  + x["what"] for x in u.get("parts") or [])
    return form


def run_action(studio, user: dict, project: dict, action: str, params: dict) -> None:
    """Do what a card they tapped offered (the same as the page's own button for it)."""
    if action == "start":
        studio.start(project)
    elif action == "confirm_understood":
        studio.confirm_understanding(project, _understood_form(studio, project))
    elif action == "pick":
        shown = [p for p in studio.pitches(project) if not p["rejected"]]
        n = int(params.get("pitch") or 0)
        if not 1 <= n <= len(shown):
            raise TalkError("That idea isn't on the page any more: pick one there.")
        studio.choose(project, shown[n - 1]["id"])
    elif action == "another":
        studio.another(project, reason=str(params.get("text") or ""))
    elif action == "sample_ok":
        studio.sample_verdict(user, project, True)
    elif action == "sample_again":
        studio.sample_verdict(user, project, False, str(params.get("text") or ""))
    elif action == "change":
        studio.ask_change(user, project, str(params.get("text") or ""))
    elif action == "continue_from":
        studio.continue_from(project, int(params.get("version") or 0))
    elif action == "cut_person":
        studio.person_draft(user, project, str(params.get("person") or ""))
    elif action == "never_person":
        studio.set_person(user, project, str(params.get("person") or ""), "never")
    elif action == "make_draft":
        studio.make_draft(user, project)
    elif action == "discard_draft":
        studio.discard_draft(project)
    elif action == "confirm_change":
        studio.confirm_change(user, project)
    elif action == "cancel_change":
        studio.cancel_change(project)
    elif action == "stop":
        studio.stop(project)
    else:
        raise TalkError("I can't do that from here.")


# ---------------------------------------------------------------- the conversation

def post(db, project: dict, role: str, kind: str, body: dict) -> int | None:
    return db.post_message(project["id"], project["user_id"], role, kind, body)


def thread(db, pid: str, after: int = 0, n: int = 80) -> list[dict]:
    rows = db.all("SELECT * FROM messages WHERE project_id=? AND id>? ORDER BY id DESC LIMIT ?", (pid, after, n))
    out = []
    for r in reversed(rows):
        try:
            r["body"] = json.loads(r["body"] or "{}")
        except ValueError:
            r["body"] = {}
        out.append(r)
    return out


THINK_SECONDS = 300  # a reply not in by then was lost (a restart): they may write again


def thinking(messages: list[dict]) -> bool:
    """Their last message is waiting for the editor's reply."""
    return bool(messages and messages[-1]["role"] == "you" and messages[-1]["body"].get("pending")
                and time.time() - float(messages[-1]["created"] or 0) < THINK_SECONDS)


def _clean(text, n: int = MAX_WORDS) -> str:
    return " ".join(str(text or "").split())[:n]


def _mmss(t) -> str:
    t = float(t or 0)
    return f"{int(t) // 60}:{t % 60:04.1f}"


def _person_text(studio, user: dict, project: dict) -> str:
    prof = studio.profile(user)
    who = user.get("display") or user.get("name") or "them"
    lines = [f"# Who they are\n{who}" + (" (the owner of Flicello)" if studio.is_owner(user) else "")]
    answers = {k: v for k, v in prof.answers.items() if not k.endswith("_other")}
    if answers:
        lines.append("Their answers to the starting questions: " + ", ".join(f"{k} = {v}" for k, v in answers.items()))
    films = studio.db.all("SELECT id, title, kind, length, version FROM projects WHERE user_id=? AND deleted=0 AND id<>? "
                          "ORDER BY created DESC LIMIT 6", (user["id"], project["id"]))
    if films:
        lines.append("Their earlier films, newest first:")
    for f in films:
        rated = studio.db.one("SELECT match, liked, text FROM feedback WHERE project_id=? AND stage='final' "
                              "ORDER BY id DESC LIMIT 1", (f["id"],))
        asked = [r["text"] for r in studio.db.all("SELECT text FROM feedback WHERE project_id=? AND stage IN "
                                                  "('change','final','sample') AND text<>'' ORDER BY id DESC LIMIT 2",
                                                  (f["id"],))]
        line = f"- \"{f['title']}\" ({f['kind']}, {f['length']:g} s, {f['version']} version(s))"
        if rated and rated.get("liked"):
            line += f": liked {rated['liked']}/10, matched their idea {rated['match'] or '?'}/5"
        if asked:
            line += "; they said: " + " / ".join(f"\"{_clean(t, 160)}\"" for t in asked)
        lines.append(line)
    return "\n".join(lines)


def _spec_text(items: list[dict]) -> str:
    src = {"you": "they said", "style": "their saved style", "editor": "your reading, not confirmed yet"}
    lines = ["# The film's spec (what was agreed: every step follows it; ids as listed)"]
    for it in items:
        quote = f" (their words: \"{it['quote']}\")" if it.get("quote") and it["quote"] != it["text"] else ""
        check = f" [check: {it['check']}]" if it.get("check") else ""
        lines.append(f"- {it['id']} | {it['part']} | {it['text']} ({src.get(it['source'], it['source'])}){check}"
                     f"{'' if it['on'] else ' [SWITCHED OFF]'}{quote}")
    return "\n".join(lines)


def _checks_text(results: list[dict]) -> str:
    return "\n".join(f"- {'met' if r['ok'] else 'NOT met'}: {r['text']} ({r['detail']})" for r in results)


def _screen_text(studio, project: dict, facts: dict) -> str:
    """Where the film is and what is on their screen, from the studio's own records."""
    from .service import STAGES

    stage, pid = project["stage"], project["id"]
    info = studio._info(project)
    lines = [f"# Where the film is\n\"{project['title']}\": {project['kind']} video, step: {STAGES.get(stage, stage)}."]
    job = studio.db.active_job(pid)
    if job:
        lines.append(f"Working now: {job['kind']} ({int(100 * (job['progress'] or 0))}%: {job['message'] or 'starting'}).")
    if stage == "setup":
        clips = studio.db.all("SELECT name, seconds FROM clips WHERE project_id=? ORDER BY created", (pid,))
        lines.append(f"On screen: their clips ({len(clips)}: " + ", ".join(
            f"{c['name']} {c['seconds'] or 0:.0f} s" for c in clips[:20]) + "), the music choice and the settings, and a "
            "Start button.")
        lines.append(f"Song: {project['song'] or ('none' if project['music'] == 'none' else 'music will be made')}.")
    elif stage == "understood":
        u = studio.understanding(project) or {}
        lines.append("On screen: 'what I understood', which they can correct, then confirm:")
        lines.append(json.dumps({k: u.get(k) for k in ("story", "feel", "must", "avoid", "music", "clips", "questions",
                                                       "parts", "terms") if u.get(k)}, ensure_ascii=False)[:6000])
    elif stage == "choose":
        lines.append("On screen: these ideas as cards, each with a button to make a sample:")
        for k, p in enumerate([p for p in studio.pitches(project) if not p["rejected"]], start=1):
            d = p["pitch"]
            lines.append(f"{k}. \"{d.get('title', '')}\": {d.get('logline') or d.get('pitch', '')} "
                         f"(feeling: {d.get('feeling', '')}; opens on: {d.get('hook', '')}; ends: {d.get('ending', '')})")
    elif stage == "review":
        lines.append("On screen: the sample (a short piece of the film), its storyboard, 'Looks right' and a box for "
                     "what to change.")
        if info.get("changes"):
            lines.append("What changed in it: " + "; ".join(info["changes"][:6]))
        checks = (info.get("checks") or {}).get("sample")
        if checks:
            lines.append("Its checks:\n" + _checks_text(checks))
    elif stage in ("done", "confirm"):
        v = studio.work_version(project)
        lines.append(f"On screen: version {v} playing" + (f" (the newest is {project['version']})"
                                                           if v != project["version"] else "")
                     + ", its numbered shots with change buttons, a box for changes in words, and every version.")
        strip = studio.strip_of(project, v)
        if strip and strip.get("cards"):
            lines.append("Its shots (number, when, clip, what it shows, place, people):")
            for c in strip["cards"][:60]:
                lines.append(f"{c['n']}. {_mmss(c['start'])}-{_mmss(c.get('end', c['start']))} {c['clip']} "
                             f"{c['what'][:90]}" + (f" | {c['place']}" if c.get("place") else "")
                             + (f" | people: {c['people']}" if c.get("people") not in (None, "unknown", "none") else "")
                             + (" | ON THE DROP" if c.get("drop") else ""))
        checks = (info.get("checks") or {}).get(str(v))
        if checks:
            lines.append("Its checks:\n" + _checks_text(checks))
        others = [x for x in studio.versions_list(project) if x["version"] != v]
        if others:
            lines.append("Other versions: " + "; ".join(
                f"v{x['version']}" + (f" (liked {x['liked']}/10)" if x.get("liked") else "")
                + (f": {'; '.join(x['changes'][:2])}" if x.get("changes") else "") for x in others[:8]))
        if stage == "confirm":
            pending = info.get("pending") or {}
            lines.append("On screen now: the changes as read, for them to check: " + "; ".join(pending.get("said") or [])
                         + (f" (unclear: {pending['unclear']})" if pending.get("unclear") else ""))
    elif stage in ("paused", "failed"):
        lines.append(f"On screen: {project.get('note') or project.get('error') or stage}.")
    return "\n".join(lines)


def _people_text(studio, project: dict) -> str:
    from . import faces

    people = (studio.people_of(project) or {}).get("people") or []
    if not people:
        return "# People in the footage\n(not looked at yet, or nobody's face is in it)"
    lines = ["# People in the footage (faces found and grouped; ids as listed)"]
    for p in people[:12]:
        flags = [x for x, on in (("it's them", p.get("me")), ("never show", p.get("never"))) if on]
        lines.append(f"- {p['id']}: {faces.label(p)}, {p['seconds']:g} s on screen in {', '.join(p['clips'])}, "
                     f"{'close up' if p.get('near') else 'only far off'}" + (f" ({'; '.join(flags)})" if flags else ""))
    return "\n".join(lines)


def _notes_text(studio, project: dict) -> str:
    """What their clients and teammates said on review links, not yet done."""
    notes = studio.review_notes(project, open_only=True)[:20]
    if not notes:
        return ""
    return "# Review notes from their clients (not done yet; in their words, at the moment of the film they mean)\n" + "\n".join(
        f"- v{n['version']} at {int(n['at']) // 60}:{int(n['at']) % 60:02d}, {n['name'] or 'someone'}: {n['text']}" for n in notes)


def _graphics_text(studio, project: dict) -> str:
    """The graphics on the version they're working on (titles, captions, names)."""
    from . import versions

    if project["stage"] != "done":
        return ""
    try:
        plan = studio._version_plan(project, studio.work_version(project))[1]
    except Exception:  # noqa: BLE001 - an old film without its plan: nothing to say about graphics
        return ""
    return "# Graphics on this version\n" + "\n".join(versions.graphics_text(plan.graphics) or ["- none"])


def _actions_text(studio, ids: list[str]) -> str:
    if not ids:
        return "# What you can offer now\nNothing at this step: answer, or change the spec."
    return "# What you can offer now (ids exactly as listed)\n" + "\n".join(
        f"- {ACTION[i].id}: {ACTION[i].what} ({takes(studio, ACTION[i])}"
        + (", uses the AI" if ACTION[i].ai else ", no AI") + ")" for i in ids)


def _talk_text(messages: list[dict]) -> str:
    lines = ["# The conversation so far (oldest first)"]
    for m in messages[-RECENT:]:
        text = _clean(m["body"].get("text"), 800)
        if not text:
            continue
        who = {"you": "them", "editor": "you"}.get(m["role"], m["role"])
        if m["kind"] == "checkin":
            who = "you (check-in after a step)"
        lines.append(f"{who}: {text}")
    return "\n".join(lines) if len(lines) > 1 else "# The conversation so far\n(nothing yet)"


def view(studio, project: dict, user: dict, words: str, refs: list[str], messages: list[dict]) -> tuple[str, dict]:
    """Everything the editor reads for one turn, and the facts its answer is checked against."""
    items = studio.spec_items(project, user, extra=[words])
    ids = available(studio, project, user)
    shown = [p for p in studio.pitches(project) if not p["rejected"]] if project["stage"] == "choose" else []
    versions = [v["version"] for v in studio.versions_list(project)] if project["stage"] in ("done", "confirm") else []
    steady = "\n\n".join(x for x in [_person_text(studio, user, project), _spec_text(items),
                                     _screen_text(studio, project, {}), _people_text(studio, project),
                                     _graphics_text(studio, project), _notes_text(studio, project),
                                     _actions_text(studio, ids)] if x)
    talk = "\n\n".join([_talk_text(messages), "# Their new message\n" + words
                        + (f"\n(They pointed at: {', '.join(refs)})" if refs else "")])
    text = steady + CACHE_SPLIT + talk  # the steady part is cached on its own: the next turn in this step reuses it
    people = [p["id"] for p in (studio.people_of(project) or {}).get("people") or []]
    return text, {"items": items, "actions": ids, "pitches": len(shown), "versions": versions,
                  "stage": project["stage"], "people": people}


def check_reply(reply, facts: dict) -> list[str]:
    out = []
    if not reply.say.strip():
        out.append("say something back in `say`")
    if len(reply.say) > 1200:
        out.append("`say` is too long: 1-4 short sentences")
    ids = {it["id"] for it in facts["items"]}
    for x in [*reply.off, *reply.on]:
        if x not in ids:
            out.append(f"there is no spec item {x!r}: use the ids exactly as listed")
    if len(reply.add) > 4:
        out.append("add at most 4 items")
    if any(not a.text.strip() for a in reply.add):
        out.append("every added item needs its text")
    if reply.ask and not 2 <= len(reply.ask.options) <= 4:
        out.append("a question needs 2-4 tap answers")
    if len(reply.remember) > 1:
        out.append("offer at most one preference to keep")
    o = reply.offer
    if o is not None:
        if o.action not in facts["actions"]:
            out.append(f"{o.action!r} can't be offered now; only: {', '.join(facts['actions']) or 'nothing'}")
        else:
            need = ACTION[o.action].needs
            if need == "pitch" and not (o.pitch and 1 <= o.pitch <= facts["pitches"]):
                out.append(f"pick: give the idea's number as listed, 1-{facts['pitches']}")
            if need == "version" and o.version not in facts["versions"]:
                out.append(f"continue_from: give one of the versions: {', '.join(map(str, facts['versions']))}")
            if need == "text" and not o.text.strip():
                out.append(f"{o.action}: say what, in their words, in `text`")
            if need == "person" and o.person not in facts.get("people", []):
                out.append(f"{o.action}: give the person's id as listed ({', '.join(facts.get('people', [])) or 'none'})")
    return out


def trim(reply, facts: dict):
    """What still doesn't fit after its tries is left out, never shown as a broken card: an offer that can't be made
    now, ids that aren't in the spec, a question without 2-4 answers, more than one preference."""
    o = reply.offer
    if o is not None:
        need = ACTION[o.action].needs if o.action in ACTION else None
        if need is None or o.action not in facts["actions"] \
                or (need == "pitch" and not (o.pitch and 1 <= o.pitch <= facts["pitches"])) \
                or (need == "version" and o.version not in facts["versions"]) \
                or (need == "text" and not o.text.strip()) \
                or (need == "person" and o.person not in facts.get("people", [])):
            reply.offer = None
    known = {it["id"] for it in facts["items"] if it.get("switchable", True)}
    reply.off = [x for x in reply.off if x in known]
    reply.on = [x for x in reply.on if x in known]
    reply.add = [a for a in reply.add if a.text.strip()][:4]
    if reply.ask is not None and not 2 <= len([x for x in reply.ask.options if x.strip()]) <= 4:
        reply.ask = None
    reply.remember = reply.remember[:1]
    if not reply.say.strip():
        reply.say = "Noted."
    return reply


def _apply_spec(studio, project: dict, reply, items: list[dict]) -> list[dict]:
    """The spec changes in the reply, made: returned as lines for the message (each with an undo)."""
    proj = dict(project)
    done: list[dict] = []
    names = {it["id"]: it["text"] for it in items}
    for a in reply.add:
        check = None if a.check == "none" else a.check
        ov, iid = specs.add(proj, a.text, a.part, check, a.quote, "you", items)
        if iid:
            proj["spec"] = json.dumps(ov)
            done.append({"op": "add", "id": iid, "text": specs._clean(a.text)})
            if a.part == "goal":  # a new story line replaces the ones before
                for it in items:
                    if it["part"] == "goal" and it["id"] != "feel" and it["on"]:
                        proj["spec"] = json.dumps(specs.switch(proj, it["id"], False))
    for x in reply.off:
        proj["spec"] = json.dumps(specs.switch(proj, x, False))
        done.append({"op": "off", "id": x, "text": names.get(x, x)})
    for x in reply.on:
        proj["spec"] = json.dumps(specs.switch(proj, x, True))
        done.append({"op": "on", "id": x, "text": names.get(x, x)})
    if done:
        studio.db.run("UPDATE projects SET spec=?, updated=? WHERE id=?", (proj["spec"], time.time(), project["id"]))
    return done


def _cards(studio, project: dict, reply, facts: dict) -> list[dict]:
    cards = []
    if reply.ask:
        cards.append({"type": "ask", "question": _clean(reply.ask.question, 200),
                      "options": [_clean(o, 60) for o in reply.ask.options[:4] if _clean(o, 60)]})
    o = reply.offer
    if o is not None and o.action in facts["actions"]:
        a = ACTION[o.action]
        params = {k: v for k, v in (("pitch", o.pitch), ("version", o.version), ("text", _clean(o.text, 1500)),
                                    ("person", o.person)) if v}
        cards.append({"type": "offer", "action": a.id, "label": a.label, "why": _clean(o.why, 200), "params": params,
                      "takes": takes(studio, a), "ai": a.ai, "stage": project["stage"]})
    for r in reply.remember[:1]:
        if _clean(r.text, 240):
            cards.append({"type": "remember", "text": _clean(r.text, 240), "why": _clean(r.why, 240)})
    return cards


FAST_WORDS = 12  # a plain ask is short: "swap 2 and 3", "remove 4 and 5", "extend 8 by one beat"


EXPORTS = [(r"\bsrt\b|\bsubtitles?\s+file\b|\bcaptions?\s+file\b", "srt"),
           (r"\b4\s*[:x]\s*5\b|\bfeed\s+(?:cut|version)\b", "feed"), (r"\b1\s*[:x]\s*1\b|\bsquare\b", "square"),
           (r"\bpro\s?res\b|\bmaster\s+file\b", "prores"), (r"\bgif\b", "gif"),
           (r"\bthumbnail\b|\bcover\s+(?:image|photo)\b", "thumb"),
           (r"\bmp3\b|\baudio\s+only\b|\bjust\s+the\s+(?:sound|audio)\b", "mp3"),
           (r"\bfcpxml\b|\bfinal\s+cut\b|\bresolve\b|\bdavinci\b", "fcpxml"), (r"\bedl\b|\bpremiere\b|\bavid\b", "edl")]
GENERATE = re.compile(r"\b(?:generate|make|create|render)\s+(?:me\s+)?(?:an?\s+)?(?:ai\s+|new\s+)?(?:clip|shot|video|scene)\s+"
                      r"(?:of|showing|with|where)\s+(?P<what>.+?)(?:\s+(?:for|into|as|in|to\s+replace)\s+(?:shot|clip)\s*(?P<shot>\d+))?\s*[.!]?$",
                      re.I)
FX_PARALLAX = re.compile(r"\b(?:make\s+)?(?:shot|clip)\s*(\d+)\s+(?:a|into\s+a|as\s+a)\s+(?:3\s*d|three\s*d|parallax|depth)\b|"
                         r"\b(?:3\s*d|parallax|depth)\s+(?:photo|effect|move)?\s*(?:on|for|in)\s+(?:shot|clip)\s*(\d+)", re.I)
FX_REMOVE = re.compile(r"\b(?:remove|erase|take\s+out|get\s+rid\s+of|delete|paint\s+out)\s+(?:the\s+|that\s+|this\s+)?"
                       r"(?P<what>(?!shots?\b|clips?\b)[a-z][\w\s'-]{1,40}?)\s+(?:from|in|out\s+of)\s+(?:shot|clip)\s*(?P<shot>\d+)", re.I)
FX_UPSCALE = re.compile(r"\b(?:upscale|sharpen|enhance|make\s+sharper)\s+(?:shot|clip)\s*(\d+)", re.I)


MUSIC = re.compile(r"\b(?:make|let|have)\s+the\s+(?:music|song|track|beat)\s+(?P<want>.+?)(?:\s+(?P<where>at\s+the\s+"
                   r"(?:start|beginning|end|ending|middle)|in\s+the\s+(?:middle|end)|from\s+\d+:\d\d\s+to\s+\d+:\d\d))?\s*[.!]?$|"
                   r"\bmake\s+the\s+(?P<part>ending|end|start|intro|opening|middle)\s+(?:of\s+the\s+(?:music|song|track)\s+)?"
                   r"(?P<want2>calmer|softer|quieter|bigger|louder|harder|more\s+\w+|less\s+\w+)\b", re.I)


def music_asked(words: str, project: dict) -> tuple[float | None, float | None, str] | None:
    """A stretch of the music to make again ('make the ending calmer', 'make the music bigger from 0:20 to 0:35'):
    (start, end in film seconds or None for the track's ends, what they want), or None."""
    m = MUSIC.search(words)
    if not m:
        return None
    want = (m.group("want") or m.group("want2") or "").strip(" .")
    where = (m.group("where") or m.group("part") or "").lower()
    length = float(project.get("length") or 30)
    if t := re.search(r"(\d+):(\d\d)\s+to\s+(\d+):(\d\d)", where):
        a = int(t.group(1)) * 60 + int(t.group(2))
        b = int(t.group(3)) * 60 + int(t.group(4))
        return float(a), float(b), want
    if "end" in where:
        return length * 0.7, None, want
    if any(x in where for x in ("start", "beginning", "intro", "opening")):
        return None, length * 0.3, want
    if "middle" in where:
        return length * 0.35, length * 0.65, want
    return None, None, want


def effect_asked(words: str) -> tuple[str, int, str] | None:
    """An effect their words ask for: ('parallax' | 'remove' | 'upscale', shot, what to remove), or None."""
    if m := FX_REMOVE.search(words):
        return "remove", int(m.group("shot")), m.group("what").strip()
    if m := FX_PARALLAX.search(words):
        return "parallax", int(m.group(1) or m.group(2)), ""
    if m := FX_UPSCALE.search(words):
        return "upscale", int(m.group(1)), ""
    return None


EXPORT_VERB = re.compile(r"\b(?:export|give|get|make|send|download|want|need)\b", re.I)


def export_asked(words: str) -> str | None:
    """The export format their words ask for ('export the srt', 'give me a gif'), or None."""
    if not EXPORT_VERB.search(words):
        return None
    return next((kind for pattern, kind in EXPORTS if re.search(pattern, words, re.I)), None)


def fast_path(studio, project: dict, user: dict, words: str) -> dict | None:
    """A plain ask about the numbered shots ("swap 2 and 3", "remove 4", "extend 8 by one beat") is read by code and
    made as a draft at once, with no model in the way: the reply, or None when it needs the editor's thinking."""
    from . import versions

    from . import exports

    if (platform := exports.read_platform(words)) and len(words.split()) <= FAST_WORDS and project["kind"] == "montage":
        try:
            return {"text": studio.set_platform(user, project, platform), "cards": [], "spec": [], "fast": True}
        except Exception as exc:  # noqa: BLE001 - said plainly
            return {"text": str(exc), "cards": [], "spec": []}
    if project["stage"] == "done" and (m := GENERATE.search(words)) and not studio.db.active_job(project["id"]):
        shot = int(m.group("shot")) if m.group("shot") else None
        try:
            studio.generate_clip(user, project, m.group("what"), shot)
        except Exception as exc:  # noqa: BLE001 - said plainly (no key yet, no changes left)
            return {"text": str(exc), "cards": [], "spec": []}
        return {"text": f"Making it with an AI video model (a minute or two){f', then it takes the place of shot {shot} in a draft' if shot else ''}.",
                "cards": [], "spec": [], "fast": True}
    if project["stage"] == "done" and (mus := music_asked(words, project)) and not studio.db.active_job(project["id"]):
        start, end, want = mus
        try:
            studio.edit_music(user, project, start, end, want)
        except Exception as exc:  # noqa: BLE001 - said plainly
            return {"text": str(exc), "cards": [], "spec": []}
        return {"text": f"Making that stretch of the music again ({want}); the rest stays note for note, then the film is "
                        "made on it (a few minutes).", "cards": [], "spec": [], "fast": True}
    if project["stage"] == "done" and (fx := effect_asked(words)) and not studio.db.active_job(project["id"]):
        kind, shot, want = fx
        try:
            studio.ask_effect(user, project, kind, shot, want)
        except Exception as exc:  # noqa: BLE001 - said plainly
            return {"text": str(exc), "cards": [], "spec": []}
        return {"text": f"On it: shot {shot} {studio.EFFECTS[kind]}. It takes a few minutes, then plays above as a draft.",
                "cards": [], "spec": [], "fast": True}
    if project["stage"] == "done" and (kind := export_asked(words)) and not studio.db.active_job(project["id"]):
        studio.ask_export(user, project, kind)
        return {"text": f"Making the {exports.KINDS[kind][0]} of this version: it shows under the video in a moment.",
                "cards": [], "spec": [], "fast": True}
    if project["stage"] != "done" or project["kind"] != "montage" or len(words.split()) > FAST_WORDS \
            or studio.db.active_job(project["id"]):
        return None
    strip = studio.strip_of(project, studio.work_version(project))
    cards = (strip or {}).get("cards") or []
    if not cards:
        return None
    draft = studio.draft_plan(project)  # a draft on screen: its shots are the ones they mean
    req = versions.read_simple(words, len(draft.plan) if draft else len(cards))
    if not req.ops or req.unclear:
        return None
    ops = [o.model_copy(update={"text": o.text or project["title"]}) if o.op == "style" else o for o in req.ops]
    studio.ask_draft(user, project, [o.model_dump() for o in ops], req.said, words)
    return {"text": "On it: " + "; ".join(req.said) + ". It plays above in a few seconds as a draft; nothing is made "
                    "until you say.", "cards": [], "spec": [], "fast": True}


ME = re.compile(r"\b(?:my\s+face|myself|me\s+out|(?:remove|cut|hide|take|drop)\s+me\b|(?:of|with)\s+me\b|"
                r"show\s+me\b|where\s+(?:i|i'?m|i\s+am)\b|i'?m\s+(?:in|visible|seen|showing)\b)", re.I)
CUT = re.compile(r"\b(?:cut|remove|delete|clip\s+out|take\s+out|drop|hide|get\s+rid\s+of)\b", re.I)
NEVER = re.compile(r"\b(?:never\s+show|don'?t\s+show|do\s+not\s+show|no\s+shots?\s+(?:of|with))\b", re.I)
PERSON_N = re.compile(r"\bperson\s+(\d{1,2})\b", re.I)


def people_path(studio, project: dict, user: dict, words: str) -> dict | None:
    """A plain ask about someone in the footage ("cut where my face shows", "never show person 2", "remove the shots
    with Mia"), answered from the face index without a model: the reply, or None when it isn't one."""
    from . import faces

    if len(words.split()) > FAST_WORDS + 4 or not (CUT.search(words) or NEVER.search(words)):
        return None
    index = studio.people_of(project)
    people = (index or {}).get("people") or []
    target, about_me = None, bool(ME.search(words))
    if m := PERSON_N.search(words):
        target = next((p for p in people if p["id"] == f"p{int(m.group(1))}"), None)
    if target is None:
        for p in people:
            if p.get("name") and re.search(rf"\b{re.escape(p['name'])}\b", words, re.I):
                target = p
                break
    if target is None and about_me:
        target = next((p for p in people if p.get("me")), None)
    if target is None and not about_me and not PERSON_N.search(words):
        return None  # not about a person we know: the editor's own reading takes over
    if index is None:
        if studio.find_people(project):
            return {"text": "I'm looking at who is in your clips now (about a minute). Ask me again then.", "cards": []}
        return None
    then = "never" if NEVER.search(words) else "cut"
    if target is None:
        near = [p for p in people if p.get("near")] or people
        if not near:
            return {"text": "I don't see anyone's face close up in your clips.", "cards": []}
        return {"text": "Which one is you? Tap your face and I'll do it.", "cards": [{
            "type": "who", "question": "Which one is you?", "then": then,
            "people": [{"id": p["id"], "label": faces.label(p), "thumb": p.get("thumb", ""), "seconds": p["seconds"]}
                       for p in near[:6]]}]}
    return person_act(studio, project, user, target, then)


def person_act(studio, project: dict, user: dict, person: dict, then: str) -> dict:
    """Do it for this person: never show them (from now on), and on a finished film, a draft without their shots."""
    from . import faces

    name = faces.label(person)
    lines = []
    if then == "never":
        studio.set_person(user, project, person["id"], "never")
        lines.append(f"{name} won't be shown in this film again (it's in the brief).")
    if project["stage"] == "done" and not studio.db.active_job(project["id"]):
        try:
            hits = studio.person_draft(user, project, person["id"])
            who = "you're" if person.get("me") else f"{name} is"
            lines.append(f"On it: cutting shot{'s' if len(hits) > 1 else ''} {', '.join(map(str, hits))}, where {who} "
                         "close up. It plays above in a few seconds as a draft.")
        except Exception as exc:  # noqa: BLE001 - said plainly instead
            lines.append(str(exc))
    return {"text": " ".join(lines) or f"{name} noted.", "cards": [], "fast": True}


def turn(studio, pid: str, mid: int) -> None:
    """The editor answers their message `mid` (run beside the web request, see Studio.talk)."""
    project = studio.db.project(pid)
    row = studio.db.one("SELECT * FROM messages WHERE id=? AND project_id=?", (mid, pid))
    if project is None or row is None:
        return
    user = studio.user(project["user_id"])
    body = json.loads(row["body"] or "{}")
    words = body.get("text", "")
    out: dict
    try:
        fast = people_path(studio, project, user, words) or fast_path(studio, project, user, words)
        if fast is not None:
            post(studio.db, project, "editor", "text", fast)
            body["pending"] = False
            studio.db.run("UPDATE messages SET body=? WHERE id=?", (json.dumps(body), mid))
            return
    except Exception:  # noqa: BLE001 - the editor's own reading takes over
        log.warning("fast path failed for %s", pid, exc_info=True)
    try:
        messages = [m for m in thread(studio.db, pid) if m["id"] < mid]
        text, facts = view(studio, project, user, words, body.get("refs") or [], messages)
        brain = studio.editor_brain(project, user)
        reply = trim(brain.editor_reply(text, words, facts, lambda r: check_reply(r, facts)), facts)
        changes = _apply_spec(studio, project, reply, facts["items"])
        if studio.mute_if_asked(project, words):
            changes.append({"op": "note", "text": "No sound from the clips (music only)"})
        out = {"text": _clean(reply.say, 1200), "cards": _cards(studio, project, reply, facts), "spec": changes}
    except BudgetExceeded as exc:
        out = {"text": f"I can't answer right now ({exc}). Your message is kept, and the buttons on this page still "
                       "work.", "cards": [], "error": True}
    except Exception as exc:  # noqa: BLE001 - the person always gets an answer, and their film is untouched
        log.warning("editor reply failed for %s", pid, exc_info=True)
        studio.db.event(pid, f"editor reply failed: {type(exc).__name__}: {' '.join(str(exc).split())[:200]}", "warn")
        out = {"text": "I couldn't answer just now. Your message is kept: send it again in a moment, or use the "
                       "buttons on this page.", "cards": [], "error": True}
    post(studio.db, project, "editor", "text", out)
    body["pending"] = False  # after the reply is in: the page never shows "thinking" with no answer to show
    studio.db.run("UPDATE messages SET body=? WHERE id=?", (json.dumps(body), mid))


def answer(studio, user: dict, project: dict, mid: int, k: int, choice: str) -> None:
    """They tapped a card: a question's answer (it becomes their message), a step offered (it runs now, exactly as
    the page's own button would), or a preference to keep for their next films."""
    from .profiles import scope_of

    row = studio.db.one("SELECT * FROM messages WHERE id=? AND project_id=? AND role='editor'", (mid, project["id"]))
    if row is None:
        raise TalkError("That card is gone.")
    body = json.loads(row["body"] or "{}")
    cards = body.get("cards") or []
    if not 0 <= k < len(cards):
        raise TalkError("That card is gone.")
    card = cards[k]
    if card.get("done"):
        raise TalkError("That's already answered.")

    def save(state: str) -> None:
        card["done"] = state
        studio.db.run("UPDATE messages SET body=? WHERE id=?", (json.dumps(body), mid))

    if card["type"] == "ask":
        if choice not in card["options"]:
            raise TalkError("Pick one of the answers.")
        save(choice)
        studio.talk(user, project, choice)
    elif card["type"] == "offer":
        if choice != "yes":
            save("no")
            return
        if project["stage"] != card.get("stage") or card["action"] not in available(studio, project, user):
            save("passed")
            raise TalkError("That step has passed. Tell me what you want now.")
        run_action(studio, user, project, card["action"], card.get("params") or {})
        save("yes")
        post(studio.db, project, "you", "tap", {"text": f"✓ {card['label']}"})
    elif card["type"] == "who":
        person = next((p for p in (studio.people_of(project) or {}).get("people") or [] if p["id"] == choice), None)
        if person is None:
            raise TalkError("Tap one of the faces.")
        save(choice)
        studio.set_person(user, project, person["id"], "me")
        person["me"] = True
        post(studio.db, project, "you", "tap", {"text": "✓ That's me"})
        post(studio.db, project, "editor", "text", person_act(studio, project, user, person, card.get("then", "cut")))
    elif card["type"] == "remember":
        if choice == "yes":
            prof = studio.profile(user)
            rid = prof.add(card["text"], card.get("why") or "said to their editor", "talk", scope_of(project["kind"]))
            prof.save()
            save("yes")
            post(studio.db, project, "editor", "text", {
                "text": f"Kept for your next films: {card['text']}" if rid else "That's already in your style."})
        else:
            save("no")


# ---------------------------------------------------------------- check-ins after each step

def _count_strong(studio, project: dict) -> int | None:
    try:
        from .brain.context import load_catalog
        from .pipeline import Pipeline
        from .storage import workdir

        work = workdir(studio.s, project["user_id"], project["id"])
        catalog = load_catalog(Pipeline(work, studio.cfg, None).project.root)
        return sum(1 for e in catalog.get("clips") or [] for m in ((e.get("analysis") or {}).get("moments") or [])
                   if (m.get("strength") or 0) >= 6)
    except Exception:  # noqa: BLE001 - a count for a sentence; the film doesn't need it
        return None


def checks_sentence(results: list[dict]) -> str:
    """The checklist in one line (its lines, with the evidence, are shown under the message)."""
    if not results:
        return ""
    bad = [r for r in results if not r["ok"]]
    if not bad:
        return f"It meets all {len(results)} checks of the brief."
    return f"{len(results) - len(bad)} of {len(results)} checks met; not yet: " + ", ".join(
        r["text"][:1].lower() + r["text"][1:] for r in bad[:3]) + "."


def _offer(studio, project: dict, action: str, why: str, params: dict | None = None) -> dict:
    a = ACTION[action]
    return {"type": "offer", "action": a.id, "label": a.label, "why": why, "params": params or {},
            "takes": takes(studio, a), "ai": a.ai, "stage": project["stage"]}


def _after_prepare(studio, p: dict) -> dict | None:
    info = json.loads(p["info"] or "{}")
    u = studio.understanding(p) or {}
    if p["kind"] != "montage":
        what = "recording" if p["kind"] == "voice" else "video"
        parts = len(u.get("parts") or [])
        text = (f"I listened to your {what}: {u.get('story', '')} " + (f"I hear {parts} parts. " if parts else "")
                + "Check what I heard below, above all the names, which speech-to-text often gets wrong. Then tap the "
                  "card, or tell me what's off.")
        return {"text": _clean(text, 1200), "stage": "understood",
                "cards": [_offer(studio, p, "confirm_understood", "What I heard is right: go on")]}
    n, secs = info.get("clips") or 0, info.get("footage_seconds") or 0
    strong = _count_strong(studio, p)
    lines = [f"I watched your {n} clip{'s' if n != 1 else ''} ({secs:.0f} s of footage)"
             + (f" and flagged {strong} strong moments." if strong else ".")]
    best = [c for c in u.get("clips") or [] if c.get("best")][:3]
    if best:
        lines.append("The best: " + "; ".join(f"{c['short']}, {c['best']}" for c in best) + ".")
    if u.get("music"):
        lines.append(_clean(u["music"], 300))
    fit = info.get("fit")
    if fit:
        lines.append(f"Your footage carries about {fit['carries']:.0f} s of strong moments, so the film is {fit['to']:g} s, "
                     f"not {fit['from']:g} s: say if you want it longer anyway.")
    if info.get("skipped"):
        lines.append(f"{len(info['skipped'])} file(s) couldn't be read and were left out.")
    lines.append("Check what I understood below. If it's right, tap the card, or tell me what's off.")
    return {"text": " ".join(lines), "stage": "understood",
            "cards": [_offer(studio, p, "confirm_understood", "What I understood is right: the stories next")]}


def _after_pitch(studio, p: dict, job: dict) -> dict | None:
    from .service import uses_looks

    shown = [x for x in studio.pitches(p) if not x["rejected"]]
    if not shown:
        return None
    word = "look" if uses_looks(p) else "idea"
    params = job.get("params") or {}
    if isinstance(params, str):
        params = json.loads(params or "{}")
    fresh = shown if params.get("first") or len(shown) <= 3 else shown[-1:]
    first = len(shown) - len(fresh) + 1
    listed = "; ".join(f"{first + k}: \"{x['pitch'].get('title', '')}\", {x['pitch'].get('logline') or x['pitch'].get('pitch', '')}"
                       for k, x in enumerate(fresh))
    head = f"{len(fresh)} {word}s" if len(fresh) > 1 else f"One more {word}"
    return {"text": _clean(f"{head}, as cards below. {listed}. Pick the one you'd post, or tell me what to change or "
                           "mix.", 1500), "stage": "choose", "cards": []}


def _after_sample(studio, p: dict) -> dict | None:
    info = json.loads(p["info"] or "{}")
    checks = (info.get("checks") or {}).get("sample") or []
    lines = ["Here's the sample: a short piece of the film, to check before the whole film is made."]
    if info.get("changes"):
        lines.append("What changed: " + "; ".join(info["changes"][:3]) + ".")
    if checks:
        lines.append(checks_sentence(checks))
    lines.append("If it's right, tap the card; or tell me what to change.")
    return {"text": _clean(" ".join(lines), 1500), "stage": "review", "checks": checks,
            "cards": [_offer(studio, p, "sample_ok", "Make the whole film like this sample")]}


def _after_film(studio, p: dict) -> dict | None:
    v = int(p["version"] or 0)
    if not v:
        return None
    info = json.loads(p["info"] or "{}")
    finals = [r for r in studio.renders(p, "final") if r["version"] == v]
    secs = finals[0]["seconds"] if finals and finals[0]["seconds"] else None
    strip = studio.strip_of(p, v)
    shots = len(strip["cards"]) if strip and strip.get("cards") else None
    changes = json.loads(finals[0]["changes"]) if finals and finals[0].get("changes") else []
    base = finals[0].get("base") if finals else None
    checks = (info.get("checks") or {}).get(str(v)) or []
    head = f"Version {v} is ready" + (f": {secs:.0f} s" if secs else "") + (f", {shots} shots" if shots else "") + "."
    lines = [head]
    if changes:
        lines.append("What changed: " + "; ".join(changes[:4]) + "."
                     + (f" Every other shot is exactly as it was in version {base}." if base else ""))
    if checks:
        lines.append(checks_sentence(checks))
    lines.append("Tap a shot below to change it, or tell me what's off." if p["kind"] == "montage"
                 else "Tell me what to change, or rate it below.")
    return {"text": _clean(" ".join(lines), 1500), "stage": "done", "checks": checks, "cards": []}


def _after_changes(studio, p: dict) -> dict | None:
    pending = json.loads(p["info"] or "{}").get("pending") or {}
    said = pending.get("said") or []
    if not said:
        return None
    text = "I read your changes as: " + "; ".join(f"{k + 1}. {x}" for k, x in enumerate(said[:8])) + "."
    if pending.get("unclear"):
        text += f" One thing I'm not sure about: {pending['unclear']}"
    text += " Only these shots change; every other shot stays exactly as it is."
    return {"text": _clean(text, 1500), "stage": "confirm",
            "cards": [_offer(studio, p, "confirm_change", "Make the changes as read")]}


def _after_draft(studio, p: dict) -> dict | None:
    draft = studio.draft_of(p)
    if not draft:
        return None
    text = ("Your draft is ready, playing above: " + "; ".join(draft.get("said") or []) + ". Every other shot is exactly "
            "as it was. Make it for real, or tell me what to change.")
    return {"text": _clean(text, 1500), "stage": "done",
            "cards": [_offer(studio, p, "make_draft", "Render this draft in full quality")]}


def _after_beat(studio, p: dict) -> dict | None:
    row = studio.db.one("SELECT * FROM beats WHERE project_id=? ORDER BY id DESC LIMIT 1", (p["id"],))
    if row is None:
        return None
    try:
        idea = json.loads(row.get("idea") or "{}")
    except ValueError:
        idea = {}
    title, reading = idea.get("title") or "your track", idea.get("reading") or ""
    return {"text": _clean(f"Your track \"{title}\" is ready. {reading} Listen to it on this page, then tap Use this "
                           "one, or tell me what to change.", 1200), "stage": p["stage"], "cards": []}


def checkin(studio, job: dict, project: dict) -> None:
    """After a step finished: what it found or made, the one thing to look at, the decision it needs."""
    p = studio.db.project(project["id"])
    if p is None:
        return
    kind = job["kind"]
    if kind == "prepare":
        body = _after_prepare(studio, p)
    elif kind == "pitch":
        body = _after_pitch(studio, p, job)
    elif kind == "sample":
        body = _after_sample(studio, p)
    elif kind in ("render", "revise"):
        body = _after_film(studio, p)
    elif kind == "changes":
        body = _after_changes(studio, p)
    elif kind == "beat":
        body = _after_beat(studio, p)
    elif kind == "draft":
        body = _after_draft(studio, p)
    else:
        body = None
    if body:
        post(studio.db, p, "editor", "checkin", body)


def trouble(studio, project: dict, text: str) -> None:
    """A step that didn't work, said in the conversation too (the page shows it as well)."""
    try:
        post(studio.db, project, "editor", "checkin", {"text": _clean(text, 600), "trouble": True, "cards": []})
    except Exception:  # noqa: BLE001 - the stage and its note already say it
        log.warning("could not post the trouble note for %s", project.get("id"), exc_info=True)


def hello(studio, project: dict, user: dict) -> None:
    """The first message of a film: who it is, what it will keep from their style, and how to talk to it."""
    rules = studio.spec_items(project, user)
    style = [it["text"] for it in rules if it["source"] == "style"][:3]
    first = "I'm your editor for this film."
    if project["kind"] == "montage":
        nxt = "Add your clips and a song (or I'll make the music), then tap Start: I'll watch everything and tell you what I see."
    elif project["kind"] == "voice":
        nxt = "Add your recording, then tap Start: I'll listen and tell you what I hear."
    else:
        nxt = "Add your video, then tap Start: I'll listen and tell you what I hear."
    keep = (" From your style I'll keep: " + "; ".join(s.rstrip(".") for s in style) + ".") if style else ""
    post(studio.db, project, "editor", "checkin", {
        "text": f"{first} {nxt}{keep} Tell me anything about it here, any time: nothing is made until you tap.",
        "stage": project["stage"], "cards": []})


def own_words(messages: list[dict]) -> list[str]:
    """What they wrote to their editor on this film (it counts like their notes)."""
    return [m["body"].get("text", "") for m in messages if m["role"] == "you" and m["kind"] == "text"]

