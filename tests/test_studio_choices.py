"""The studio's newer choices and safety nets: "Other" and "Decide for each film" answers, the clip-sound rule,
changing the settings and adding a note at the clips step, polishing what was understood, the caps on ideas, tracks
and reports, stopping a step that runs on this machine, clip pictures, and the pages in both themes."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from vidosuper.render.looks import look_filter
from vidosuper.studio import accounts, mail, requirements
from vidosuper.studio.service import MAX_IDEAS, MAX_POLISH, MAX_TRACKS, ActionError, Studio
from vidosuper.studio.settings import load_settings
from vidosuper.studio.storage import workdir
from vidosuper.studio.web.app import create_app
from vidosuper.studio.worker import Worker


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDO_STUDIO_BRAIN", raising=False)
    s = load_settings(tmp_path / "home", {"brain": "local", "owner_brain": "local", "render_preset": "ultrafast",
                                         "render_processes": 1})
    s.limits.min_free_gb = 0.0
    return Studio(s)


def _friend(studio: Studio, name: str = "Ava") -> dict:
    token = accounts.create_invite(studio.db, studio.s, name)
    user = accounts.register(studio.db, token, name, "a long password 2")
    studio.db.run("UPDATE users SET onboarded=1 WHERE id=?", (user["id"],))
    return studio.user(user["id"])


def _client(studio: Studio, user: dict) -> TestClient:
    client = TestClient(create_app(studio))
    client.cookies.set("vs_session", accounts.new_session(studio.db, studio.s, user["id"], "1.2.3.4"))
    return client


def _csrf(html: str) -> str:
    return re.search(r'name="csrf" content="([^"]+)"', html).group(1)


def _msg(r) -> str:
    return unquote(r.headers["location"])


def _upload(client, csrf, pid, path: Path, kind: str) -> dict:
    data = path.read_bytes()
    up = client.post(f"/api/p/{pid}/uploads", json={"kind": kind, "name": path.name, "size": len(data)},
                     headers={"X-CSRF": csrf}).json()
    r = client.put(f"/api/uploads/{up['id']}?offset=0", content=data, headers={"X-CSRF": csrf})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/uploads/{up['id']}/done", headers={"X-CSRF": csrf})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- the starting questions

def test_style_answers_take_their_own_words_or_fix_nothing(studio):
    prof = studio.profile(_friend(studio))
    prof.set_answers({"pace": "any", "sound": "other", "look": "other", "junk": "x"}, "",
                     {"sound": "  ", "look": "black and white, grainy"})
    # "decide for each film" fixes nothing; "other" with nothing written is no answer; their words become their rule
    assert prof.answers == {"pace": "any", "look": "other", "look_other": "black and white, grainy"}
    assert [r["text"] for r in prof.rules if r["source"] == "answer"] == ["Colour: black and white, grainy"]
    assert prof.look == "mono"  # their words, to the nearest grade the renderer has
    for answers, others, look in [({"look": "any"}, {}, "vivid"), ({"look": "other"}, {"look": "something odd"}, "vivid"),
                                  ({"look": "other"}, {"look": "warm and golden"}, "warm"), ({"look": "film"}, {}, "film"),
                                  ({"look": "loud"}, {}, "vivid"), ({}, {}, "vivid")]:
        prof.set_answers(answers, "", others)
        assert prof.look == look, (answers, others)
        look_filter(prof.look)  # only a real look ever reaches the renderer


def test_the_welcome_questions_offer_decide_for_each_film_and_other(studio):
    user = _friend(studio)
    client = _client(studio, user)
    page = client.get("/welcome").text
    assert page.count('value="any" checked') == 3 and 'data-other-for="look"' in page
    client.post("/welcome", data={"csrf": _csrf(page), "pace": "fast", "sound": "any", "look": "other",
                                  "look_other": "Faded, like an old photo"})
    prof = studio.profile(user)
    assert prof.answers["pace"] == "fast" and prof.answers["sound"] == "any" and prof.look == "faded"
    style = client.get("/style").text
    assert 'value="other" checked' in style and "Faded, like an old photo" in style


# ---------------------------------------------------------------- no sound from the clips

MUTE = [
    "no audio of that video", "No audio from the video please", "no sound from my clips",
    "without any sound from the footage", "remove all the audio from my videos", "mute the clips", "mute all clips",
    "music only", "Music only, nothing else", "only music", "just the music", "no clip sound", "no clips' sound",
    "no video's audio", "no original audio", "without the original sound", "no camera audio",
    "no sound except music", "no audio at all except the music", "no sound, but the music",
    "turn off the sound of the clips", "get rid of the audio in the videos", "strip the audio from the clips",
    "take out the sound from the footage", "mute the original audio", "dont add the audio of the video",
    "do not use audio from the clips", "don't want any sound from the videos", "the clips should be muted",
    "no voices from the clips", "no talking in the video", "a beach reel. no audio of that video. make it fun",
]
KEEP = [
    "keep the sound of the waves in the clip", "don't lose the sound of the crowd in the video",
    "no cuts during the sound of the waves in the clip", "remove background noise from the clips",
    "on the drop, the sound of the crowd from the video", "cut to the sound of the waves in the clip", "no music",
    "only music at the start", "just music and the waves", "no talking heads", "no voiceover",
    "without music, keep the sound from the clips", "no sound effects from the clips", "no sound in the first clip",
    "don't remove the sound from the clips", "don't mute the clips", "never mute the videos",
    "I do not want the clips muted", "the clips should not be muted", "music only at the end",
    "just the music for the intro", "a fun beach video with my friends", "every hit with its own sound",
    "remove the shaky clips", "make the drop hit hard", "no text on the video", "no faces in the clips",
]


@pytest.mark.parametrize("words", MUTE)
def test_asking_for_no_clip_sound_is_heard(words):
    assert requirements.wants_no_clip_sound(words)


@pytest.mark.parametrize("words", KEEP)
def test_words_that_dont_ask_for_silence_never_mute_a_film(words):
    assert not requirements.wants_no_clip_sound(words)


def test_clip_sound_follows_their_words_and_their_style(studio):
    user = _friend(studio)
    plain = studio.create_project(user, "Plain", "9:16", 15, "a beach day with friends")
    quiet = studio.create_project(user, "Quiet", "9:16", 15, "a beach day. no audio of that video")
    assert (plain["sound"], quiet["sound"]) == ("natural", "silent")

    def muted(p):
        return studio._pipeline({"id": 0}, studio.db.project(p["id"]), user).mute_clips

    assert not muted(plain) and muted(quiet)
    # their style says "music only": the clips are muted while music plays (never into a silent film), and a
    # film's own words asking to hear its clips win
    prof = studio.profile(user)
    prof.set_answers({"sound": "music"})
    prof.save()
    hits = studio.create_project(user, "Hits", "9:16", 15, "every hit with its own sound")
    assert studio.db.project(plain["id"])["sound"] == "natural" and muted(plain) and not muted(hits)
    studio.set_music(studio.db.project(plain["id"]), "none")
    assert not muted(plain)
    # a note at the clips step that asks for it is heard, and said
    studio.add_note(studio.db.project(hits["id"]), "actually, mute the clips")
    assert studio.db.project(hits["id"])["sound"] == "silent" and muted(hits)
    assert any("muted" in e["message"] for e in studio.db.all("SELECT message FROM events WHERE project_id=?",
                                                              (hits["id"],)))
    # a talking video keeps its speech whatever is said
    talk = studio.create_project(user, "Talk", "9:16", 15, "mute the clips", kind="talk")
    assert talk["sound"] == "natural" and not studio.mute_if_asked(talk, "no audio from the video")


def test_only_their_own_words_on_what_was_understood_count():
    u = {"story": "A day at the beach.", "must": ["The sunset", "No sound from the clips"], "avoid": [], "music": "Calm"}
    same = {"story": "A day at the beach. ", "must": ["the sunset", "No sound from the clips"], "avoid": [],
            "music": "Calm", "answers": []}
    assert Studio._their_words(u, same) == ""  # the AI's own lines only repeat what they asked for
    edited = {**same, "avoid": ["mute the clips"], "answers": [{"ask": "The sound?", "answer": "Music only"}]}
    assert Studio._their_words(u, edited) == "Music only mute the clips"


# ---------------------------------------------------------------- the clips step

def test_shape_length_and_a_note_can_change_at_the_clips_step(studio):
    user = _friend(studio)
    p = studio.create_project(user, "Trip", "9:16", 15, "a trip")
    client = _client(studio, user)
    page = client.get(f"/p/{p['id']}").text
    assert 'id="settings-edit"' in page and 'href="#settings-edit"' in page
    csrf = _csrf(page)
    r = client.post(f"/p/{p['id']}/settings", data={"csrf": csrf, "shapes": "16:9", "length": "custom",
                                                     "length_custom": "40", "length_unit": "s"}, follow_redirects=False)
    assert "Saved: widescreen 16:9, 40 seconds." in _msg(r)
    q = studio.db.project(p["id"])
    assert (q["shapes"], q["length"]) == ("16:9", 40)
    r = client.post(f"/p/{p['id']}/settings", data={"csrf": csrf, "shapes": "9:16", "length": "custom",
                                                     "length_custom": "lots"}, follow_redirects=False)
    assert "as a number" in _msg(r)
    r = client.post(f"/p/{p['id']}/settings", data={"csrf": csrf, "shapes": "9:16", "length": "9000"},
                    follow_redirects=False)
    assert "Pick a length" in _msg(r) and studio.db.project(p["id"])["length"] == 40
    r = client.post(f"/p/{p['id']}/note", data={"csrf": csrf, "note": "start on the sunset"}, follow_redirects=False)
    assert studio.db.project(p["id"])["intent"] == "a trip start on the sunset"
    with pytest.raises(ActionError):
        studio.add_note(studio.db.project(p["id"]), " ")
    # a talking video keeps its own shape and length: nothing to change there
    talk = studio.create_project(user, "Talk", "9:16", 15, "", kind="talk")
    assert 'id="settings-edit"' not in client.get(f"/p/{talk['id']}").text
    # once it has started, the settings change only after going back
    studio.db.set_stage(p["id"], "choose")
    with pytest.raises(ActionError):
        studio.set_settings(studio.db.project(p["id"]), "9:16", 20)


def test_a_made_track_that_no_longer_fits_the_length_is_made_again(studio, monkeypatch):
    user = _friend(studio)
    p = studio.create_project(user, "Trip", "9:16", 15, "a trip")
    rid = studio.db.run("INSERT INTO beats(project_id, created, ask, idea, plan, file, seconds, whole) "
                        "VALUES (?,?,?,?,?,?,?,1)", (p["id"], time.time(), "", "{}", "{}", "b1.mp3", 17.0))
    studio.db.run("UPDATE projects SET beat=?, music='made' WHERE id=?", (rid, p["id"]))
    p = studio.db.project(p["id"])
    row = studio.beat_row(p, rid)
    assert studio._track_fits(p, row) and studio._track_fits({**p, "length": 16}, row)
    said = studio.set_settings(p, "9:16", 45)
    assert "made again" in said and not studio._track_fits(studio.db.project(p["id"]), row)
    remade = []
    monkeypatch.setattr(studio, "_full_from_taste", lambda job, project, user, row, span=(0.0, 1.0): remade.append(
        row["id"]) or row)
    studio._ensure_track({"id": 0}, studio.db.project(p["id"]), user, None)
    assert remade == [rid]


def test_each_clip_gets_a_small_picture_that_goes_with_it(studio, media):
    user = _friend(studio)
    p = studio.create_project(user, "Trip", "9:16", 15, "")
    client = _client(studio, user)
    csrf = _csrf(client.get(f"/p/{p['id']}").text)
    clip = _upload(client, csrf, p["id"], media / "clips" / "a_stable.mp4", "clip")["clip"]
    thumb = workdir(studio.s, user["id"], p["id"]).stills / f"clip-{clip}.jpg"
    assert thumb.exists() and thumb.stat().st_size > 500
    page = client.get(f"/p/{p['id']}").text
    assert f'src="/m/{p["id"]}/stills/clip-{clip}.jpg"' in page and "clip-remove" in page
    assert client.get(f"/m/{p['id']}/stills/clip-{clip}.jpg").status_code == 200
    assert client.post(f"/api/p/{p['id']}/clips/{clip}/delete", headers={"X-CSRF": csrf}).status_code == 200
    assert not thumb.exists()


def test_their_own_way_of_cutting_needs_their_words(studio):
    user = _friend(studio)
    client = _client(studio, user)
    page = client.get("/new").text
    assert 'name="pace" value="other"' in page and 'data-other-need="pace"' in page
    form = {"csrf": _csrf(page), "title": "Mine", "shapes": "16:9", "length": "custom", "length_custom": "20",
            "length_unit": "s", "pace": "other", "intent": "my dog at the beach", "kind": "montage"}
    r = client.post("/new", data=form, follow_redirects=False)
    # shown again with the problem, and with everything they chose and wrote still there
    assert r.status_code == 200 and "Describe how it should cut" in r.text
    assert 'value="Mine"' in r.text and ">my dog at the beach</textarea>" in r.text and 'value="20"' in r.text
    assert re.search(r'value="16:9"\s+checked', r.text) and re.search(r'name="pace" value="other"\s+checked', r.text)
    assert re.search(r'name="length" value="custom"\s+checked', r.text)
    r = client.post("/new", data={**form, "cut_note": "long dreamy takes", "length": "lots"}, follow_redirects=False)
    assert r.status_code == 200 and "as a number" in r.text  # a broken length is a message, not an error page
    # their words decide: "quick" in them doesn't turn the whole film into fast cuts
    words = "long dreamy takes, then quick at the drop"
    r = client.post("/new", data={**form, "cut_note": words, "intent": "my dog. no audio of that video"},
                    follow_redirects=False)
    q = studio.db.project(r.headers["location"].rsplit("/", 1)[1])
    assert q["pace"] == "balanced" and q["cut_note"] == words and q["sound"] == "silent"
    page = client.get(f"/p/{q['id']}").text
    assert f"Cutting your way: {words}" in page and "No sound from your clips: only the music" in page
    assert "Fast cuts on the beat" not in page


# ---------------------------------------------------------------- what was understood

def test_what_was_understood_can_be_polished_and_put_back(studio):
    user = _friend(studio)
    p = studio.create_project(user, "Trip", "9:16", 15, "a trip")
    u = {"story": "a trip with friends", "feel": ["fun"], "must": ["the sunset"], "avoid": [], "music": "",
         "clips": [], "questions": []}
    studio.db.run("UPDATE projects SET understood=?, stage='understood' WHERE id=?", (json.dumps(u), p["id"]))
    p = studio.db.project(p["id"])
    assert studio.polish_left(p) == MAX_POLISH
    studio.polish_understanding(p, {"story": "a trip  with   friends and the dog", "must": "the sunset\n- my dog\n\n",
                                    "avoid": "", "feel": "fun"})
    after = studio.understanding(studio.db.project(p["id"]))
    assert after["story"] == "A trip with friends and the dog" and after["must"] == ["The sunset", "My dog"]
    assert after["_polished"] == 1 and studio.polish_left(studio.db.project(p["id"])) == MAX_POLISH - 1
    studio.undo_understanding(studio.db.project(p["id"]))
    back = studio.understanding(studio.db.project(p["id"]))
    assert back["story"] == "a trip with friends and the dog" and back["must"] == ["the sunset", "my dog"]
    with pytest.raises(ActionError):
        studio.undo_understanding(studio.db.project(p["id"]))
    # what they typed counts before it is polished: asking for no clip sound there is heard
    studio.polish_understanding(studio.db.project(p["id"]), {"story": "a trip", "avoid": "no audio from the clips"})
    assert studio.db.project(p["id"])["sound"] == "silent"
    # and through the page: the tools are there, and every press asks first
    page = _client(studio, user).get(f"/p/{p['id']}").text
    assert "Polish my words with AI" in page and "Undo the last polish" in page and 'data-confirm="' in page


# ---------------------------------------------------------------- caps

def test_ideas_and_tracks_have_a_cap(studio):
    user = _friend(studio)
    p = studio.create_project(user, "Trip", "9:16", 15, "a trip")
    studio.db.set_stage(p["id"], "choose")
    for k in range(MAX_IDEAS):
        studio.db.run("INSERT INTO pitches(project_id, round, data, stills, created) VALUES (?,?,?,?,?)",
                      (p["id"], 1, "{}", "[]", time.time()))
    p = studio.db.project(p["id"])
    assert studio.ideas_left(p) == 0
    with pytest.raises(ActionError, match="the most it can have"):
        studio.another(p, "funnier")
    for k in range(MAX_TRACKS + 1):
        studio.db.run("INSERT INTO beats(project_id, created, ask, idea, plan, file, seconds) VALUES (?,?,?,?,?,?,?)",
                      (p["id"], time.time(), "", "{}", "{}", f"b{k}.mp3", 17.0))
    assert studio.beats_left(p) == 0


def test_reports_are_limited_and_go_to_the_owners_address(studio):
    studio.s.mail_outbox = True
    studio.s.alert_emails = ["alerts@example.com"]
    user = _friend(studio)
    p = studio.create_project(user, "Trip", "9:16", 15, "")
    studio.report_problem(user, p, "stuck for ages")
    with pytest.raises(ActionError, match="in an hour"):
        studio.report_problem(user, p, "still stuck")
    for _ in range(2):
        studio.db.run("UPDATE reports SET at=at-4000")  # an hour later
        studio.report_problem(user, p, "still stuck")
    studio.db.run("UPDATE reports SET at=at-4000")
    with pytest.raises(ActionError, match="today"):
        studio.report_problem(user, p, "a fourth time")
    assert [m["to"] for m in mail.outbox(studio.s)] == ["alerts@example.com"] * 3


def test_the_owners_address_comes_from_the_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDO_STUDIO_ALERT_EMAILS", " a@example.com, b@example.com ,")
    assert load_settings(tmp_path / "a").alert_emails == ["a@example.com", "b@example.com"]
    monkeypatch.delenv("VIDO_STUDIO_ALERT_EMAILS")
    assert load_settings(tmp_path / "b").alert_emails == ["zenithvelocitylabs@gmail.com"]


# ---------------------------------------------------------------- stopping a step on this machine

def test_a_stopped_step_ends_at_once_and_stays_stopped(studio, monkeypatch):
    user = _friend(studio)
    p = studio.create_project(user, "Trip", "9:16", 15, "a trip")
    worker = Worker(studio)  # steps run here: a stopped one ends at its next progress update
    assert studio.db.stop_local
    seen = []

    def carries_on(job, project, user):
        studio.stop(studio.db.project(project["id"]))  # they press Stop while it runs
        seen.append("stopped")
        studio.db.job_progress(job["id"], 0.5, "Watching your clips")
        seen.append("went on")  # never: the step ended at that update

    def breaks(job, project, user):
        studio.stop(studio.db.project(project["id"]))
        raise RuntimeError("the renderer fell over")  # not retried, not shown as a failure

    def finishes(job, project, user):
        studio.stop(studio.db.project(project["id"]))
        studio.db.set_stage(project["id"], "understood")  # it moved the video on as it ended: put back

    for step in (carries_on, breaks, finishes):
        monkeypatch.setattr(studio, "_job_prepare", step)
        studio.db.set_stage(p["id"], "preparing")
        job = studio.db.enqueue("prepare", p["id"], user["id"])
        assert worker.tick()
        assert studio.db.value("SELECT status FROM jobs WHERE id=?", (job,)) == "cancelled", step.__name__
        assert studio.db.project(p["id"])["stage"] == "setup", step.__name__
    assert seen == ["stopped"]


# ---------------------------------------------------------------- pages

def test_pages_follow_the_theme_cookie(studio):
    user = _friend(studio)
    p = studio.create_project(user, "Trip", "9:16", 15, "")
    client = _client(studio, user)
    assert 'data-theme="light"' in client.get(f"/p/{p['id']}").text  # the default
    client.cookies.set("theme", "dark")
    page = client.get(f"/p/{p['id']}").text
    assert 'data-theme="dark"' in page and '<meta name="color-scheme" content="dark">' in page
    assert "data-theme-toggle" in page
    client.cookies.set("theme", '"><script>')
    assert 'data-theme="light"' in client.get(f"/p/{p['id']}").text  # only the two themes, never their text


@pytest.mark.parametrize("kind", ["montage", "talk", "voice"])
def test_the_project_page_shows_at_every_stage(studio, kind):
    user = _friend(studio)
    p = studio.create_project(user, "Trip", "9:16", 15, "", kind=kind)
    client = _client(studio, user)
    for stage in ("setup", "preparing", "understood", "choose", "pitching", "sampling", "review", "rendering",
                  "done", "revising", "composing", "paused", "failed"):
        studio.db.set_stage(p["id"], stage)
        r = client.get(f"/p/{p['id']}")
        assert r.status_code == 200 and "didn't go to plan" not in r.text, (kind, stage)


# ---------------------------------------------------------------- pictures, not boxes

def test_film_cards_show_footage_and_players_open_on_a_still(studio, media):
    user = _friend(studio)
    p = studio.create_project(user, "Trip", "9:16", 15, "")
    client = _client(studio, user)
    csrf = _csrf(client.get(f"/p/{p['id']}").text)
    clip = _upload(client, csrf, p["id"], media / "clips" / "a_stable.mp4", "clip")["clip"]
    assert f'src="/m/{p["id"]}/stills/clip-{clip}.jpg"' in client.get("/").text  # no story yet: its first clip
    # a finished film: its player opens on a still from the film (made once the step ends), and its card shows it
    work = workdir(studio.s, user["id"], p["id"]).ensure()
    import shutil
    shutil.copy(media / "clips" / "a_stable.mp4", work.out / "v1-9x16.mp4")
    studio.db.run("INSERT INTO renders(project_id, version, kind, shape, file, seconds, created) VALUES (?,?,?,?,?,?,?)",
                  (p["id"], 1, "final", "9:16", "v1-9x16.mp4", 5.0, time.time()))
    studio.db.run("UPDATE projects SET stage='done', version=1 WHERE id=?", (p["id"],))
    studio._posters(studio.db.project(p["id"]))
    assert (work.out / "v1-9x16.jpg").exists()
    assert f'poster="/m/{p["id"]}/out/v1-9x16.jpg"' in client.get(f"/p/{p['id']}").text
    assert client.get(f"/m/{p['id']}/out/v1-9x16.jpg").status_code == 200


def test_a_failed_step_is_marked_where_it_broke(studio):
    user = _friend(studio)
    p = studio.create_project(user, "Trip", "9:16", 15, "")
    job = studio.db.enqueue("sample", p["id"], user["id"])
    for m in ("Reading the plan", "Mixing the sound"):
        studio.db.step(job, m)
    studio.db.run("UPDATE jobs SET status='failed' WHERE id=?", (job,))
    studio.db.set_stage(p["id"], "failed", error="Something went wrong on our side.")
    page = _client(studio, user).get(f"/p/{p['id']}").text
    assert '<li class="done"><span>Reading the plan' in page and '<li class="broke"><span>Mixing the sound' in page
