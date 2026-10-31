"""The redesign's working parts: Cello's access levels (enforced in code, whatever the model decides), rules switched
off and kept in a bin, Fix it for me (fixed steps only, proved by the same check), the new film begun by a drop or a
take, takes recorded in the browser, the film page's waveform and its words."""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from vidosuper.studio import cello
from vidosuper.studio.profiles import Profile, rules_for
from vidosuper.studio.service import Studio
from vidosuper.studio.settings import load_settings
from vidosuper.studio.web.app import create_app

from conftest import local_client, person


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDO_STUDIO_BRAIN", raising=False)
    s = load_settings(tmp_path / "home", {"brain": "local", "owner_brain": "local", "render_preset": "ultrafast",
                                         "render_processes": 1})
    s.limits.min_free_gb = 0.0
    return Studio(s)


def _csrf(html: str) -> str:
    return re.search(r'name="csrf" content="([^"]+)"', html).group(1)


# ---------------------------------------------------------------- Cello's access levels

def test_advisor_only_looks_copilot_asks_before_long_work_and_paid_credit_is_always_asked():
    assert cello.allowed("advisor", "look", {}) is None
    assert "Advisor" in cello.allowed("advisor", "edit", {})
    assert cello.allowed("copilot", "edit", {}) is None  # quick, undoable: at once
    assert "ask before anything that takes time" in cello.allowed("copilot", "make_it", {})
    assert cello.allowed("copilot", "make_it", {"they_said_yes": True}) is None
    assert cello.allowed("full", "make_it", {}) is None  # full access: it does it and tells them after
    assert "paid credit" in cello.allowed("full", "ai_clip", {})  # the promise holds at every level
    assert cello.allowed("full", "ai_clip", {"they_said_yes": True}) is None
    assert cello.allowed("", "build", {}) is not None  # not asked yet: co-pilot
    assert cello.allowed("copilot", "work", {}, hands=True) is None  # its hands, on a request they said yes to
    assert "paid credit" in cello.allowed("copilot", "ai_clip", {}, hands=True)
    long_tools = [t for t in cello.TOOLS if t["name"] in cello.LONG | cello.PAID]
    assert long_tools and all("they_said_yes" in t["parameters"]["properties"] for t in long_tools)


def test_the_access_level_is_kept_and_act_refuses_what_it_does_not_allow(studio):
    user = person(studio.db)
    client = local_client(studio)[0]
    csrf = _csrf(client.get("/", follow_redirects=True).text)
    assert client.get("/api/cello/settings").json()["access"] == ""  # asked on the first hello
    r = client.post("/api/cello/settings", json={"access": "advisor"}, headers={"X-CSRF": csrf})
    assert r.status_code == 200 and "Advisor" in r.json()["said"]
    assert cello.access_of(studio, user) == "advisor"
    said = cello.act(studio, user, None, "new_film", {})
    assert said["result"].startswith("Not done") and "Advisor" in said["result"]
    assert not studio.projects(user)  # nothing was made
    client.post("/api/cello/settings", json={"access": "nonsense"}, headers={"X-CSRF": csrf})
    assert cello.access_of(studio, user) == "advisor"


# ---------------------------------------------------------------- rules: off, into the bin, and back

def test_a_rule_switched_off_is_kept_but_not_followed_and_a_removed_one_can_come_back(tmp_path):
    p = Profile.load(tmp_path / "profile.json")
    rid = p.add("No car shots", "you added it", "you")
    other = p.add("No made-up voices", "you added it", "you")
    assert p.switch(rid, False) and [r["text"] for r in rules_for(p.rules, "montage")] == ["No made-up voices"]
    assert p.switch(rid, True) and len(rules_for(p.rules, "montage")) == 2
    assert p.drop(other) and [r["text"] for r in p.binned()] == ["No made-up voices"]
    p.save()
    again = Profile.load(tmp_path / "profile.json")
    assert again.binned() and again.restore(other)
    assert any(r["text"] == "No made-up voices" for r in again.rules) and not again.binned()


def test_rules_change_in_place_from_the_style_page(studio):
    user = person(studio.db)
    client = local_client(studio)[0]
    csrf = _csrf(client.get("/", follow_redirects=True).text)
    profile = studio.profile(user)
    rid = profile.add("No slow motion", "you added it", "you")
    profile.save()
    page = client.get("/style?tab=rules").text
    assert "No slow motion" in page and 'data-tabs="tab"' in page and "Looks I liked" not in page
    st = client.post("/api/style/rule", json={"rule": rid, "op": "off"}, headers={"X-CSRF": csrf}).json()
    assert st["ok"] and studio.profile(user).rules[-1].get("off") is True
    st = client.post("/api/style/rule", json={"rule": rid, "op": "drop"}, headers={"X-CSRF": csrf}).json()
    assert st["ok"] and st["bin"] == 1
    assert client.post("/api/style/rule", json={"rule": rid, "op": "restore"}, headers={"X-CSRF": csrf}).json()["ok"]


# ---------------------------------------------------------------- Fix it for me

def test_fix_it_runs_only_known_steps_and_says_plainly_what_it_cannot_fix(monkeypatch):
    from vidosuper.local import fixes

    monkeypatch.setattr(fixes, "_npm", lambda: "npm")
    monkeypatch.setattr(fixes, "_npx", lambda: "npx")
    steps, why = fixes.plan("frontend")
    assert [s.argv[1:] for s in steps] == [["ci", "--no-audit", "--no-fund"], ["run", "build"]] and not why
    steps, why = fixes.plan("model:whisper")
    assert steps == [] and why == ""  # not something it fixes: the page shows the written fix instead
    monkeypatch.setattr(fixes, "_npm", lambda: None)
    steps, why = fixes.plan("hyperframes")
    assert not steps and "Node.js" in why


def test_a_fix_is_proved_by_the_check_that_found_it(tmp_path, monkeypatch):
    from vidosuper.local import checks, fixes

    py = sys.executable
    monkeypatch.setattr(fixes, "plan", lambda item: ([fixes.Step("Doing it", [py, "-c", "print(1)"])], ""))
    monkeypatch.setattr(checks, "report", lambda home, models: {"items": [{"id": "x", "label": "The thing", "status": checks.OK,
                                                                           "detail": "works"}]})
    run = fixes.start("x", tmp_path, tmp_path)
    for _ in range(100):
        if fixes.state("x")["done"]:
            break
        __import__("time").sleep(0.05)
    st = fixes.state("x")
    assert st["ok"] and [s["state"] for s in st["steps"]] == ["ok", "ok"] and "proved" in st["detail"] and run.item == "x"
    monkeypatch.setattr(fixes, "plan", lambda item: ([fixes.Step("Breaking", [py, "-c", "import sys; sys.exit(3)"])], ""))
    fixes.start("y", tmp_path, tmp_path)
    for _ in range(100):
        if fixes.state("y")["done"]:
            break
        __import__("time").sleep(0.05)
    assert not fixes.state("y")["ok"] and "Stopped at" in fixes.state("y")["detail"]


def test_connect_shows_tabs_counts_and_the_health_ring(studio, monkeypatch):
    monkeypatch.setenv("VIDO_RENDERER_CHECK", "0")
    client = local_client(studio)[0]
    page = client.get("/connect").text
    for t in ("This computer", "Claude", "Keys", "Tools", "Models"):
        assert f">{t} <span class=\"n" in page.replace("</svg>", ">"), t
    assert "working" in page and 'class="health"' in page


# ---------------------------------------------------------------- the new film: a drop or a take begins it

def test_a_drop_or_a_take_begins_a_draft_and_start_fills_it_in(studio):
    user = person(studio.db)
    clips = studio.new_draft(user, "clips")
    assert clips["kind"] == "montage" and clips["stage"] == "setup"
    take = studio.new_draft(user, "camera")
    assert take["kind"] == "talk"
    memo = studio.new_draft(user, "voice")
    assert memo["kind"] == "voice"
    filled = studio.fill_draft(user, clips["id"], "Me explaining my recipe to camera, with the steps on screen")
    assert filled["intent"].startswith("Me explaining my recipe") and filled["kind"] == "talk"
    kept = studio.fill_draft(user, take["id"], "Sydney in 30 seconds on the beat")
    assert kept["kind"] == "talk"  # what they recorded decides: a take of them talking stays a talk


def test_a_suggestion_is_never_an_apology_or_an_error(studio, monkeypatch):
    from vidosuper.studio import service as svc
    from vidosuper.studio.storage import workdir

    user = person(studio.db)
    p = studio.new_draft(user, "clips")
    work = workdir(studio.s, user["id"], p["id"])
    work.stills.mkdir(parents=True, exist_ok=True)
    studio.db.run("INSERT INTO clips(id, project_id, file, name, bytes, seconds, created) VALUES ('c1', ?, 'a.mp4', 'a.mp4', 10, 3, 0)",
                  (p["id"],))
    (work.stills / "clip-c1.jpg").write_bytes(b"\xff\xd8\xff")
    for said, shown in [("Our beach day, slow and golden", "Our beach day, slow and golden"),
                        ("I couldn't write answer.txt", ""), ("Sorry, I can't see the frames", ""), ("", "")]:
        monkeypatch.setattr(svc, "_one_line", lambda system, prompt, folder, said=said: said)
        assert studio.suggest_from_clips(user, p["id"]) == shown, said


def test_a_take_recorded_in_the_browser_without_its_length_gets_one(tmp_path):
    from vidosuper.ffmpeg import ffmpeg_bin
    from vidosuper.studio import storage

    webm = tmp_path / "take.webm"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:r=30",
                    "-f", "lavfi", "-i", "sine=frequency=330", "-t", "2", "-c:v", "libvpx", "-c:a", "libopus",
                    "-f", "webm", "-live", "1", str(webm)], check=True)  # live: no length written, like MediaRecorder
    seconds = storage._fix_length(webm)
    assert seconds and 1.5 < seconds < 2.6
    assert ".webm" in storage.AUDIO_EXT and ".webm" in storage.VIDEO_EXT


# ---------------------------------------------------------------- the film page

def test_the_film_page_has_one_conversation_docked_beside_the_film(studio, tmp_path):
    from vidosuper.ffmpeg import ffmpeg_bin
    from vidosuper.studio.storage import workdir

    user = person(studio.db)
    client = local_client(studio)[0]
    csrf = _csrf(client.get("/", follow_redirects=True).text)
    p = studio.new_draft(user, "clips")
    page = client.get(f"/p/{p['id']}").text
    assert page.count('id="editor"') == 1 and 'class="cello-dock"' in page and 'data-record="camera"' in page
    assert 'class="composer" data-talk' in page and page.index('data-convo') < page.index('data-talk')
    # a drawer at the window's edge: resized by its edge, closed by its head, brought back by its tab; the type box's
    # buttons on a bar under the words
    assert all(a in page for a in ("data-dock-edge", "data-dock-close", "data-dock-tab", 'class="composer-bar"'))
    assert page.index('data-fp-main') < page.index('data-dock') and "fp-rail" not in page
    work = workdir(studio.s, user["id"], p["id"])
    work.out.mkdir(parents=True, exist_ok=True)
    film = work.out / "v1-9x16.mp4"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=30",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(film)], check=True)
    peaks = client.get(f"/api/p/{p['id']}/wave?file=v1-9x16.mp4").json()
    assert len(peaks) == 600 and max(peaks) == 1.0 and min(peaks) >= 0
    assert client.get(f"/api/p/{p['id']}/wave?file=../../secret.mp4").status_code == 404
    assert csrf


# ---------------------------------------------------------------- a talking film cut on its words

def _speech(video, words, duration):
    return {"video": str(video), "duration": duration,
            "utterances": [{"words": [{"start": a, "end": b, "text": t} for a, b, t in words]}]}


def test_a_talk_is_cut_on_its_phrases_and_its_words_move_with_the_cut(tmp_path):
    from vidosuper.work import moments_film as mf

    words = [(0.5, 0.8, "So"), (0.85, 1.2, "picture"), (1.25, 1.6, "this."),  # phrase 1
             (3.6, 3.9, "um"),  # a lone um, between long pauses
             (6.0, 6.3, "A"), (6.35, 6.8, "ball."), (7.0, 7.1, "")]
    cut = mf.tighten(_speech(tmp_path / "v.mp4", words, 9.0))
    assert cut["keep"][0][0] == pytest.approx(0.4) and len(cut["keep"]) == 2
    moved = mf.words_of(cut)
    assert [w[2] for w in moved] == ["So", "picture", "this.", "A", "ball."]  # the lone um is gone
    first_len = cut["keep"][0][1] - cut["keep"][0][0]
    assert moved[3][0] == pytest.approx(first_len + 0.1, abs=0.01)  # "A" lands a breath into the second stretch
    assert cut["duration"] < 3.0 and cut["tightened"]["fillers"] == 1 and cut["tightened"]["from"] == 9.0
    assert mf.tighten(cut) is cut  # already cut: left alone
    calm = _speech(tmp_path / "v.mp4", [(0.1, 0.4, "Hi"), (0.45, 0.9, "there.")], 1.1)
    assert "keep" not in mf.tighten(calm)  # nothing worth taking out


def test_the_picture_is_cut_on_the_kept_stretches(tmp_path):
    import subprocess as sp

    from vidosuper.ffmpeg import ffmpeg_bin
    from vidosuper.work import moments_film as mf

    video = tmp_path / "talk.mp4"
    sp.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30", "-f", "lavfi",
            "-i", "sine=frequency=300", "-t", "9", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)],
           check=True)
    words = [(0.5, 0.8, "So"), (0.85, 1.6, "picture."), (6.0, 6.8, "Ball.")]
    cut = mf.tighten(_speech(video, words, 9.0))
    out = mf.base(cut, "16:9", tmp_path / "base-cut.mp4")
    from vidosuper.ffmpeg import ffprobe_json

    seconds = float(ffprobe_json(out)["format"]["duration"])
    assert abs(seconds - cut["duration"]) < 0.15
