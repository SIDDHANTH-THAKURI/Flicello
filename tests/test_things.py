"""Things (what they say made into something that moves) and the speech cut (a talking film cut on its words)."""

from __future__ import annotations

from types import SimpleNamespace

from vidosuper.motion.things import bounces_said, clean_svg, paths, read
from vidosuper.studio import speech_cut
from vidosuper.studio.brain.schemas import ChangeOp
from vidosuper.studio.versions import Applied, graphic_op


def test_their_words_become_a_thing_not_text():
    t = read("a small red ball drops from my head and bounces three times")
    assert t["shape"] == "ball" and t["color"] == "#e8322d" and t["motion"] == "drop"
    assert t["count"] == 0  # "three times" is how often it bounces, not three balls
    assert bounces_said("a small red ball drops from my head and bounces three times") == 3
    assert read("five stars rain down")["count"] == 5 and read("five stars rain down")["motion"] == "rain"
    assert read("a light bulb pops up")["shape"] == "bulb"
    assert read("a rubber duck flies in")["shape"] == ""  # not in the library: drawn for it


def test_a_drop_falls_lands_and_bounces_as_many_times_as_asked():
    copies, hits = paths("drop", (0.5, 0.3), 4.0, size=0.07, floor=0.9, bounces=3)
    keys = copies[0]["keys"]
    assert len(hits) == 4  # the landing and three bounces
    assert all(a < b for (a, *_), (b, *_) in zip(hits, hits[1:]))
    assert hits[0][1] == 1.0 and all(s < 1.0 for _, s, _ in hits[1:])  # each softer than the last
    assert max(k[2] + 0.035 * k[4] for k in keys) <= 0.9 + 0.004  # its bottom never through the floor (its last pop aside)
    assert any(k[3] > 1.05 and k[4] < 0.95 for k in keys)  # squashed on landing
    assert keys[-1][6] == 0.0 and keys[0][6] == 0.0  # comes in from nothing, goes out to nothing
    burst, bh = paths("burst", (0.5, 0.5), 2.0, count=10)
    assert len(burst) == 10 and bh[0][2] == "burst"


def test_a_drawing_from_a_model_keeps_only_drawing():
    bad = ('<svg viewBox="0 0 100 100" onload="alert(1)"><script>alert(1)</script><circle cx="50" cy="50" r="40" '
           'fill="url(https://evil.example/x)" onclick="x()"/><image href="https://evil.example/a.png"/>'
           '<a href="javascript:alert(1)"><rect width="10" height="10"/></a><path d="M0 0L10 10" fill="#f00"/></svg>')
    svg = clean_svg("Here you go:\n" + bad)
    for gone in ("script", "onload", "onclick", "evil", "image", "javascript", "<a"):
        assert gone not in svg
    assert "<path" in svg and "<circle" in svg and 'viewBox="0 0 100 100"' in svg
    assert clean_svg("no drawing here") == "" and clean_svg("<svg><unclosed") == ""


def test_a_thing_change_is_made_and_said_plainly():
    done = Applied(None)
    marks = graphic_op(ChangeOp(op="thing", shot=2, want="a small red ball drops from my head and bounces three times",
                                anchor="the top of my head", say="red ball"), [], 6, done)
    g = marks[0]
    assert g.kind == "thing" and g.style == "drop" and g.anchor == "the top of my head" and g.say == "red ball"
    assert g.bounces == 3 and g.accent == "#e8322d" and g.beats >= 8
    assert "drawn and moving" in done.lines[0] and '"red ball"' in done.lines[0]
    nothing = Applied(None)
    assert graphic_op(ChangeOp(op="thing", shot=9, want="a star"), [], 6, nothing) == []
    assert "only 6 shots" in nothing.skipped[0]


def _w(a, b, t):
    return (a, b, t)


def test_a_talk_is_cut_on_its_phrases():
    words = [_w(1.5, 1.8, "Hi,"), _w(1.85, 2.2, "I"), _w(2.25, 2.6, "want"), _w(2.65, 3.0, "this."),
             _w(5.2, 5.4, "Um."),  # a filler on its own goes
             _w(7.0, 7.3, "Imagine"), _w(7.35, 7.6, "a"), _w(7.65, 8.0, "red"), _w(8.05, 8.4, "ball.")]
    said = speech_cut.phrases(words)
    assert [p[2] for p in said] == ["Hi, I want this.", "Imagine a red ball."]
    long = [_w(k * 0.5, k * 0.5 + 0.4, "word,") if k == 9 else _w(k * 0.5, k * 0.5 + 0.4, "word") for k in range(20)]
    parts = speech_cut.phrases(long)
    assert len(parts) >= 2 and all(b - a <= speech_cut.LONG + 0.5 for a, b, _ in parts)


def test_the_speech_plan_is_pinned_to_the_words_and_syncs_where_a_pause_allows(monkeypatch):
    from vidosuper.perceive import prep

    words = [SimpleNamespace(start=a, end=b, text=t) for a, b, t in
             [(1.0, 1.4, "One"), (1.45, 2.0, "thing."), (3.0, 3.4, "Two"), (3.45, 4.1, "things.")]]
    transcript = SimpleNamespace(utterances=[SimpleNamespace(start=1.0, end=4.1, text="", confident=True, words=words)])
    monkeypatch.setattr(prep, "load_transcript", lambda project, cid: transcript)
    facts = {"c01": SimpleNamespace(clip_id="clipA", duration=6.0, avoid=[])}
    ctx = SimpleNamespace(facts=facts, beats=SimpleNamespace(period=0.5, booms=[]), sync=False)
    plan = speech_cut.plan(ctx, object())
    assert [s.src_in for s in plan.plan] == [0.9, 2.9] and len(plan.pool) == 2
    assert plan.pool[0].end <= 2.0 + speech_cut.TAIL + 1e-6
    ctx.sync = True
    synced = speech_cut.plan(ctx, object())
    first = synced.plan[0].beats
    assert abs(first - round(first)) < 1e-3  # the first cut lands on a beat, inside the pause
    assert synced.pool[0].end >= 2.0  # never inside the last word
