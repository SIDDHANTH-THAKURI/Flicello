"""Your editor (agent.py) and the film's spec (spec.py): one editor per person through the whole film, a brief both
sides can see whose checks every step obeys, a checklist on every version, and check-ins after every step."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from vidosuper.perceive import prep as prep_module
from vidosuper.perceive.transcribe import Transcript
from vidosuper.studio import accounts, agent
from vidosuper.studio import spec as specs
from vidosuper.studio.brain.context import BeatMap, ClipFacts
from vidosuper.studio.brain.core import LocalBrain, StoryContext
from vidosuper.studio.brain.schemas import (DesignPlan, EditorAsk, EditorOffer, EditorRemember, EditorReply, PlanShot,
                                            PoolMoment, SpecAdd)
from vidosuper.studio.service import ActionError, Studio, ask_text
from vidosuper.studio.settings import load_settings
from vidosuper.studio.web.app import create_app
from vidosuper.studio.worker import Worker


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDO_STUDIO_BRAIN", raising=False)
    s = load_settings(tmp_path / "home", {"brain": "local", "owner_brain": "local", "render_preset": "ultrafast",
                                         "render_processes": 1})
    s.limits.min_free_gb = 0.0
    st = Studio(s)
    st.soon = lambda fn: fn()  # the editor answers at once
    return st


def _friend(studio: Studio, name: str) -> dict:
    token = accounts.create_invite(studio.db, studio.s, name)
    return accounts.register(studio.db, token, name, "a long password 2")


def _project(**kw) -> dict:
    p = {"id": "p1", "user_id": "u1", "title": "t", "intent": "", "cut_note": "", "pace": "balanced", "shapes": "9:16",
         "length": 30, "kind": "montage", "music": "made", "song": None, "brief": None, "understood": None, "spec": None}
    p.update(kw)
    return p


# ---------------------------------------------------------------- the spec

def test_the_spec_holds_everything_they_said_and_its_checks_follow_it():
    brief = {"story": "A ferry ride at dusk", "feel": ["calm"], "must": ["end on the bridge", "cuts on the 808s"],
             "avoid": ["no place twice"], "leave_out": ["c03"],
             "answers": [{"ask": "Who's it about?", "answer": "just the city at night"}]}
    p = _project(intent="Sydney at dusk, nobody in shot", brief=json.dumps(brief))
    rules = [{"id": "r1", "text": "Sync cuts and transitions tightly to the beat", "why": "rated 9"}]
    items = specs.film_spec(p, rules)
    by = {it["id"]: it for it in items}
    assert by["goal"]["text"] == "A ferry ride at dusk" and by["goal"]["source"] == "you"
    assert by["must-1"]["check"] == "sync" and by["never-0"]["check"] == "no_repeats"
    assert by["out-c03"]["check"] == "clips_out" and by["rule-r1"]["source"] == "style"
    assert by["answer-0"]["quote"] == "Who's it about?"
    assert by["word-no_people"]["quote"].startswith("Sydney at dusk")  # their own words, quoted
    assert specs.flags(items) == {"sync": True, "no_people": True, "no_repeats": True}
    # their style's beat-sync rule switched off: their own words for this film still ask for it
    p["spec"] = json.dumps(specs.switch(p, "rule-r1", False))
    assert specs.flags(specs.film_spec(p, rules))["sync"] is True
    p["spec"] = json.dumps(specs.switch(p, "must-1", False))
    assert specs.flags(specs.film_spec(p, rules))["sync"] is False  # every item asking for it is off: so is the check
    assert specs.rule_off(specs.film_spec(p, rules), "r1")
    # their rules can be switched; the film itself and what it is delivered as change by talking
    switchable = {it["id"] for it in specs.film_spec(p, rules) if it["switchable"]}
    assert {"must-0", "never-0", "out-c03", "answer-0", "rule-r1", "word-no_people"} <= switchable
    assert not {"goal", "feel", "deliver", "pace", "music"} & switchable
    assert next(it for it in items if it["id"] == "rule-r1")["why"] == "rated 9"
    # before 'what I understood' is confirmed, the editor's reading shows but switches no check on
    early = specs.film_spec(_project(understood=json.dumps({"story": "A ferry ride", "must": ["every cut on the beat"]})), [])
    assert next(it for it in early if it["id"] == "must-0")["source"] == "editor" and "sync" not in specs.flags(early)
    # an item they add by talking: once, with its check found in its words
    ov, iid = specs.add(p, "No faces at all", "never", None, "no faces at all")
    assert iid == "a1" and ov["added"][0]["check"] == "no_people"
    p["spec"] = json.dumps(ov)
    assert specs.add(p, "no faces at all", "never", items=specs.film_spec(p, rules))[1] is None
    assert [a["id"] for a in specs.remove(p, "a1")["added"]] == []


def test_what_the_ai_reads_drops_what_they_switched_off_and_adds_what_they_agreed_by_talking():
    brief = {"story": "A ferry ride at dusk", "must": ["end on the bridge", "text on screen"], "avoid": ["car shots"],
             "leave_out": ["c03"]}
    p = _project(intent="ferry", brief=json.dumps(brief))
    p["spec"] = json.dumps(specs.switch(p, "must-1", False))
    ov, _ = specs.add(p, "Nobody in shot", "never", "no_people", "nobody in it please")
    p["spec"] = json.dumps(ov)
    text = ask_text(p)
    assert "end on the bridge" in text and "text on screen" not in text and "car shots" in text and "c03" in text
    assert "Agreed with them since" in text and "Leave out: Nobody in shot" in text
    ctx = NS(sync=False, no_people=False, no_repeats=False)
    specs.apply(ctx, specs.film_spec(p, []))
    assert ctx.no_people and not ctx.sync and not ctx.no_repeats


PLACES = {"c01": "Sydney Opera House", "c02": "Sydney Harbour Bridge", "c03": "", "c04": "Sydney Opera House"}
PEOPLE = {"c01": "none", "c02": "none", "c03": "near", "c04": "far"}


def _ctx() -> StoryContext:
    facts = {}
    for short, place in PLACES.items():
        moments = [{"start": 0.5, "end": 5.5, "peak": None, "kind": "scenic", "description": f"{short} view", "hook": 6,
                    "strength": 7, "best_use": "highlight", "sound": "either", "place": place, "people": PEOPLE[short],
                    "best_at": 3.0}]
        facts[short] = ClipFacts(short, f"{short}-x", 8.0, 30.0, False, [], moments)
    beats = BeatMap(first=0, t0=0.0, period=0.5, available=64, target=32, drop=16, sections=[],
                    booms=[float(b) for b in range(0, 33)])
    return StoryContext(catalog={"clips": []}, facts=facts, song=None, beats=beats, shapes=["16:9"], intent="", rules=[])


def _plan(keys) -> DesignPlan:
    pool = [PoolMoment(key=k, clip=k, start=1.0, end=5.0) for k in PLACES]
    return DesignPlan(name="t", idea="t", pool=pool, plan=[PlanShot(kind="cut", beats=8, key=k) for k in keys])


def test_every_sample_and_version_gets_a_checklist_with_the_evidence():
    items = [{"id": i, "text": t, "check": c, "on": True, "source": "you", "part": "must"} for i, t, c in (
        ("word-sync", "Cuts land on the music's hits", "sync"), ("word-no_people", "Nobody in shot", "no_people"),
        ("never-0", "no place twice", "no_repeats"), ("out-c02", "Leave out clip c02", "clips_out"),
        ("rule-x", "switched off, so never checked", "no_people"))]
    items[-1]["on"] = False
    rows = [{"level": "warn", "check": "requirement", "detail": "fast cuts: 4.00 s per shot on average"}]
    results = specs.checklist(items, _plan(["c01", "c03", "c02", "c04"]), _ctx(), rows=rows, seconds=16.0, length=16.0)
    by = {r["check"]: r for r in results}
    assert by["sync"]["ok"] and by["sync"]["detail"].startswith("3 of 3 cuts")
    assert not by["no_people"]["ok"] and "shot 2" in by["no_people"]["detail"] and by["no_people"]["items"] == ["word-no_people"]
    assert not by["no_repeats"]["ok"] and "Opera House in shot 1 and 4" in by["no_repeats"]["detail"]
    assert not by["clips_out"]["ok"] and "c02" in by["clips_out"]["detail"]
    assert by["length"]["ok"] and by["requirement"]["text"] == "Fast cuts" and not by["requirement"]["ok"]
    assert specs.summary(results) == "2 of 6 checks met"
    # the version's own numbered shots are the evidence when there are some (a far-off figure is fine)
    # what breaks beat sync, in a few words for the person (the AI gets the long version)
    assert specs.sync_said(["beat sync was asked for: after the drop the music hits every 1 beat, but shots 10, 11 "
                            "hold longer than 2 beats. Cut on its hits there", "beat sync was asked for: shots 4 come in "
                            "with a dissolve or fade, which hides the hit"]) == \
        "after the drop, shots 10 and 11 hold past the hits; shot 4 fades in instead of cutting"
    cards = [{"n": 1, "place": "Sydney Opera House", "people": "none"}, {"n": 2, "place": "", "people": "far"},
             {"n": 3, "place": "Sydney Opera House", "people": "none"}]
    by = {r["check"]: r for r in specs.checklist(items, _plan(["c01", "c04", "c01"]), _ctx(), cards)}
    assert by["no_people"]["ok"] and "far-off" in by["no_people"]["detail"] and not by["no_repeats"]["ok"]


# ---------------------------------------------------------------- the editor's answers

def test_without_the_ai_their_plainest_asks_become_cards_and_nothing_else():
    facts = {"items": [], "actions": ["pick", "another"], "pitches": 3, "versions": [], "stage": "choose"}
    r = LocalBrain().editor_reply("", "story 2 please, and no faces", facts, None)
    assert r.offer.action == "pick" and r.offer.pitch == 2 and [a.check for a in r.add] == ["no_people"]
    assert LocalBrain().editor_reply("", "story 7", facts, None).offer is None  # there is no story 7
    done = {"items": [], "actions": ["change", "continue_from"], "pitches": 0, "versions": [1, 2], "stage": "done"}
    r = LocalBrain().editor_reply("", "swap 2 and 5, then shot 7 longer", done, None)
    assert r.offer.action == "change" and r.offer.text == "swap 2 and 5, then shot 7 longer"


def test_an_answer_that_doesnt_fit_loses_only_what_doesnt_fit():
    facts = {"items": [{"id": "goal"}], "actions": ["change"], "pitches": 0, "versions": [1], "stage": "done"}
    reply = EditorReply(say="Sure.", off=["goal", "nope"], offer=EditorOffer(action="render", why="x"),
                        ask=EditorAsk(question="Which?", options=["only one"]),
                        remember=[EditorRemember(text="a"), EditorRemember(text="b")])
    problems = agent.check_reply(reply, facts)
    assert any("'render' can't be offered now" in p for p in problems) and any("'nope'" in p for p in problems)
    t = agent.trim(reply, facts)
    assert t.offer is None and t.off == ["goal"] and t.ask is None and len(t.remember) == 1 and t.say == "Sure."
    change = EditorReply(say="ok", offer=EditorOffer(action="change", why="x", text=""))
    assert agent.trim(change, facts).offer is None  # a change with nothing to change is no card


def test_their_answer_is_their_next_message_and_a_preference_is_kept_only_on_yes(studio, monkeypatch):
    friend = _friend(studio, "Lu")
    p = studio.create_project(friend, "Harbour", "9:16", 15, "A day out on the harbour")
    hello = agent.thread(studio.db, p["id"])
    assert hello and hello[0]["kind"] == "checkin" and "I'm your editor for this film" in hello[0]["body"]["text"]
    replies = iter([
        EditorReply(say="Who is it about?", ask=EditorAsk(question="Who is it about?", options=["Me", "The city"]),
                    remember=[EditorRemember(text="Cuts on the 808s", why="I always want cuts on the 808s")],
                    add=[SpecAdd(text="Cuts on the 808s", part="must", quote="cuts on the 808s", check="sync")]),
        EditorReply(say="The city it is.", add=[SpecAdd(text="The city at dusk, not people", part="goal", quote="The city")]),
    ])
    seen = []

    class Editor:
        def editor_reply(self, text, words, facts, check):
            seen.append((text, words, facts))
            return next(replies)

    monkeypatch.setattr(studio, "editor_brain", lambda project, user: Editor())
    studio.talk(friend, studio.db.project(p["id"]), "I always want cuts on the 808s")
    m = agent.thread(studio.db, p["id"])[-1]
    assert m["role"] == "editor" and [c["type"] for c in m["body"]["cards"]] == ["ask", "remember"]
    assert m["body"]["spec"] == [{"op": "add", "id": "a1", "text": "Cuts on the 808s"}]
    text, words, facts = seen[0]
    for part in ("# Who they are", "# The film's spec", "# Where the film is", "# What you can offer now",
                 "# The conversation so far", "# Their new message"):
        assert part in text
    assert words == "I always want cuts on the 808s" and facts["stage"] == "setup"
    assert not json.loads(studio.db.one("SELECT body FROM messages WHERE role='you' ORDER BY id LIMIT 1")["body"])["pending"]
    # their tap is their next message, answered in turn
    studio.answer_card(friend, studio.db.project(p["id"]), m["id"], 0, "The city")
    assert seen[1][1] == "The city" and agent.thread(studio.db, p["id"])[-1]["body"]["text"] == "The city it is."
    with pytest.raises(ActionError, match="already answered"):
        studio.answer_card(friend, studio.db.project(p["id"]), m["id"], 0, "Me")
    items = {it["id"]: it for it in studio.spec_items(studio.db.project(p["id"]))}
    assert items["a1"]["check"] == "sync" and items["a2"]["part"] == "goal" and not items["goal"]["on"]  # new story line
    assert "The film, as they now put it: The city at dusk, not people" in ask_text(studio.db.project(p["id"]))
    # a preference for their next films: only on their yes
    assert not any("808" in r["text"] for r in studio.profile(friend).rules)
    studio.answer_card(friend, studio.db.project(p["id"]), m["id"], 1, "yes")
    assert any(r["text"] == "Cuts on the 808s" and r["source"] == "talk" for r in studio.profile(friend).rules)
    assert "Kept for your next films" in agent.thread(studio.db, p["id"])[-1]["body"]["text"]


def test_the_steady_part_of_a_turn_is_cached_apart_from_the_conversation():
    from vidosuper.studio.brain.backends import CACHE_SPLIT, ApiBackend, Request

    req = Request("editor", "system", "who they are, the spec" + CACHE_SPLIT + "the conversation", EditorReply, "m")
    blocks = ApiBackend._content(None, req, None)
    assert [b["text"] for b in blocks] == ["who they are, the spec", "the conversation"]
    assert all(b.get("cache_control") for b in blocks)  # the steady part, and the whole (for a retry)
    plain = ApiBackend._content(None, Request("x", "s", "one block", EditorReply, "m"), None)
    assert len(plain) == 1 and plain[0]["text"] == "one block"


def test_one_message_at_a_time_and_a_lost_reply_never_blocks_them(studio, monkeypatch):
    friend = _friend(studio, "Jo")
    p = studio.create_project(friend, "x", "9:16", 15, "")
    studio.soon = lambda fn: None  # the reply hasn't landed yet
    studio.talk(friend, p, "make it calm")
    with pytest.raises(ActionError, match="still answering"):
        studio.talk(friend, p, "and warm")
    studio.db.run("UPDATE messages SET created=? WHERE role='you'", (time.time() - agent.THINK_SECONDS - 5,))
    studio.talk(friend, p, "and warm")  # a reply that never came (a restart) doesn't lock them out
    with pytest.raises(ActionError):
        studio.talk(friend, p, "   ")


def test_when_the_editor_cant_answer_they_are_told_and_nothing_changes(studio, monkeypatch):
    friend = _friend(studio, "Al")
    p = studio.create_project(friend, "x", "9:16", 15, "")

    class Broken:
        def editor_reply(self, text, words, facts, check):
            raise RuntimeError("Error code: 529 - overloaded")

    monkeypatch.setattr(studio, "editor_brain", lambda project, user: Broken())
    studio.talk(friend, p, "nobody in shot please")
    last = agent.thread(studio.db, p["id"])[-1]
    assert last["body"]["error"] and "529" not in last["body"]["text"] and "Your message is kept" in last["body"]["text"]
    assert studio.db.project(p["id"])["spec"] is None
    assert studio.db.one("SELECT * FROM events WHERE message LIKE 'editor reply failed%'")


def test_a_step_that_breaks_is_said_in_the_conversation_without_our_internals(studio):
    friend = _friend(studio, "Mo")
    p = studio.create_project(friend, "x", "9:16", 15, "")
    studio.db.run("UPDATE projects SET stage='sampling' WHERE id=?", (p["id"],))
    job_id = studio.db.enqueue("sample", p["id"], friend["id"], {"pitch": 1})

    def boom(job, project, user):
        raise ValueError("The edit has errors:\n  - ERROR: [s06] shot is only 0.07s on screen (minimum 0.2s)")

    studio._job_sample = boom
    for _ in range(2):  # the first failure is retried once
        job = studio.db.claim() or {}
        if not job:
            studio.db.run("UPDATE jobs SET after=NULL WHERE id=?", (job_id,))
            job = studio.db.claim()
        studio.run_job(job)
    last = agent.thread(studio.db, p["id"])[-1]
    assert last["body"]["trouble"] and "s06" not in last["body"]["text"] and "owner has been told" in last["body"]["text"]


def test_a_renderers_check_in_arrives_after_the_step_it_belongs_to(monkeypatch):
    from vidosuper.studio import remote

    calls = []
    monkeypatch.setattr(remote, "_call", lambda http, method, url, **kw: calls.append(kw["json"]) or NS(
        json=lambda: {"id": 1, "rows": []}))
    db = remote.RemoteDB(None)
    db.hold = True
    db.set_stage("p1", "done")
    assert db.post_message("p1", "u1", "editor", "checkin", {"text": "Version 2 is ready."}) is None and not calls
    db.release()
    assert [c["sql"].split()[0] for c in calls] == ["UPDATE", "INSERT"]  # the stage first, then its check-in


# ---------------------------------------------------------------- a whole film, made by talking to the editor

def _csrf(html: str) -> str:
    return re.search(r'name="csrf" content="([^"]+)"', html).group(1)


def _upload(client, csrf, pid, path: Path, kind: str, chunk: int = 300_000):
    data = path.read_bytes()
    up = client.post(f"/api/p/{pid}/uploads", json={"kind": kind, "name": path.name, "size": len(data)},
                     headers={"X-CSRF": csrf}).json()
    received = 0
    while received < len(data):
        r = client.put(f"/api/uploads/{up['id']}?offset={received}", content=data[received:received + chunk],
                       headers={"X-CSRF": csrf})
        received = r.json()["received"]
    assert client.post(f"/api/uploads/{up['id']}/done", headers={"X-CSRF": csrf}).status_code == 200


def _drain(studio: Studio, limit: int = 10) -> None:
    worker = Worker(studio)
    for _ in range(limit):
        if not worker.tick():
            return


def test_a_film_made_by_talking_to_your_editor(studio, media, monkeypatch):
    monkeypatch.setattr(prep_module, "transcribe", lambda cid, wav, model, root: Transcript(clip_id=cid, model="fake",
                                                                                            language="en"))
    friend = _friend(studio, "Ana")
    client = TestClient(create_app(studio))
    client.post("/login", data={"name": "Ana", "password": "a long password 2"})
    csrf = _csrf(client.get("/", follow_redirects=True).text)
    client.post("/welcome", data={"csrf": csrf, "pace": "fast", "sound": "music", "look": "vivid"})
    r = client.post("/new", data={"csrf": csrf, "title": "Beach", "shapes": "16:9", "length": 15, "intent": "fun"},
                    follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    page = client.get(f"/p/{pid}").text
    assert "Cello" in page and "your editor for this film" in page and "The brief" in page
    for name in ("a_stable.mp4", "c_pan.mp4", "f_portrait60.mov"):
        _upload(client, csrf, pid, media / "clips" / name, "clip")
    _upload(client, csrf, pid, media / "music.wav", "song")

    def say(text: str) -> dict:
        r = client.post(f"/p/{pid}/talk", data={"csrf": csrf, "text": text}, headers={"Accept": "application/json"})
        assert r.status_code == 200, r.text
        return agent.thread(studio.db, pid)[-1]

    def tap(m: dict, k: int = 0, choice: str = "yes") -> None:
        r = client.post(f"/p/{pid}/card", data={"csrf": csrf, "mid": m["id"], "k": k, "choice": choice},
                        follow_redirects=False)
        assert r.status_code == 303 and "msg=" not in r.headers["location"], r.headers["location"]

    def last() -> dict:
        return agent.thread(studio.db, pid)[-1]

    # they say what they want once: it is in the brief with its checks, and the editor offers to start
    m = say("Beat sync please, nobody in shot. Let's start")
    assert m["role"] == "editor" and m["body"]["cards"][0]["action"] == "start"
    items = {it["id"]: it for it in studio.spec_items(studio.db.project(pid))}
    assert items["word-sync"]["on"] and items["word-no_people"]["on"] and "Beat sync please" in items["word-sync"]["quote"]
    tap(m)  # the card does what the Start button does
    assert studio.db.project(pid)["stage"] == "preparing" and last()["kind"] == "tap"
    start_card = m
    _drain(studio)
    # it checks in with what it saw, and offers the next step
    m = last()
    assert m["kind"] == "checkin" and "I watched your 3 clips" in m["body"]["text"], m
    assert m["body"]["cards"][0]["action"] == "confirm_understood"
    tap(m)
    _drain(studio)
    m = last()
    assert m["kind"] == "checkin" and m["body"]["text"].startswith("3 ideas"), m
    m = say("story 2 please")
    assert m["body"]["cards"][0]["action"] == "pick" and m["body"]["cards"][0]["params"] == {"pitch": 2}
    tap(m)
    second = [x for x in studio.pitches(studio.db.project(pid)) if not x["rejected"]][1]["id"]
    assert studio.db.project(pid)["chosen"] == second
    _drain(studio)
    m = last()
    assert m["kind"] == "checkin" and "Here's the sample" in m["body"]["text"]
    assert {c["check"] for c in m["body"]["checks"]} >= {"sync", "no_people"}
    page = client.get(f"/p/{pid}").text
    assert 'class="chk ' in page and "checks met" in page  # the brief shows what the sample met
    tap(m)  # looks right: make the film
    _drain(studio)
    m = last()
    assert m["kind"] == "checkin" and m["body"]["text"].startswith("Version 1 is ready"), m
    checks = json.loads(studio.db.project(pid)["info"])["checks"]["1"]
    assert {c["check"] for c in checks} >= {"sync", "no_people", "length"}
    # a plain ask: read by code, a draft to watch at once, made only on "make it"
    m = say("swap 1 and 2")
    assert m["body"].get("fast") and m["body"]["text"].startswith("On it:")
    _drain(studio)
    m = last()
    assert m["kind"] == "checkin" and m["body"]["text"].startswith("Your draft is ready")
    assert m["body"]["cards"][0]["action"] == "make_draft"
    tap(m)
    _drain(studio)
    m = last()
    assert studio.db.project(pid)["version"] == 2 and m["body"]["text"].startswith("Version 2 is ready")
    assert "What changed" in m["body"]["text"] and "exactly as it was in version 1" in m["body"]["text"]
    # a change explained at length: the editor reads it into exact changes, shows them, then they're made
    m = say("please swap 1 and 2 again because the opening feels slow and the other shot is much the stronger start")
    assert m["body"]["cards"][0]["action"] == "change"
    tap(m)
    assert studio.db.project(pid)["stage"] == "reading"
    _drain(studio)
    m = last()
    assert m["kind"] == "checkin" and "I read your changes as" in m["body"]["text"] and \
        m["body"]["cards"][0]["action"] == "confirm_change"
    tap(m)
    _drain(studio)
    m = last()
    assert studio.db.project(pid)["version"] == 3 and m["body"]["text"].startswith("Version 3 is ready")
    assert "exactly as it was in version 2" in m["body"]["text"]
    # an old card can't run again
    r = client.post(f"/p/{pid}/card", data={"csrf": csrf, "mid": start_card["id"], "k": 0, "choice": "yes"},
                    follow_redirects=False)
    assert "msg=" in r.headers["location"]
    # the brief: switched off by a tap, and the next film step obeys it
    client.post(f"/p/{pid}/spec", data={"csrf": csrf, "item": "word-no_people", "op": "off"})
    items = {it["id"]: it for it in studio.spec_items(studio.db.project(pid))}
    assert not items["word-no_people"]["on"] and specs.flags(list(items.values())) == {"sync": True, "no_people": False}
    # said in their note instead, the AI is also told they took it back
    studio.db.run("UPDATE projects SET intent='fun, nobody in shot' WHERE id=?", (pid,))
    assert "not wanted for this film: nobody in shot" in ask_text(studio.db.project(pid))
    # the panel alone, for the page to swap in; signed out it is refused
    panel = client.get(f"/p/{pid}/editor")
    assert panel.status_code == 200 and 'id="editor"' in panel.text and "Version 3 is ready" in panel.text
    assert TestClient(create_app(studio)).get(f"/p/{pid}/editor").status_code == 401
    # their conversation is theirs: in their export, and gone with the film
    talk = studio.export(friend)["projects"][0]["conversation"]
    assert any(x["text"] == "swap 1 and 2" for x in talk)
    client.post(f"/p/{pid}/delete", data={"csrf": csrf, "sure": "yes"})
    assert not studio.db.all("SELECT id FROM messages WHERE project_id=?", (pid,))
