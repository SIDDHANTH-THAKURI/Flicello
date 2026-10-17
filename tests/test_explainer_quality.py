"""The fixes from the owner's 16 Oct voice video (rated 'bad shots'): scenes never sit empty, animations are drawn at
the size of their drawing (not their canvas), one animation per video, misheard names put right in the captions, and
film rules kept out of explainers."""

from __future__ import annotations

import json

import numpy as np

from vidosuper.explainer import draw
from vidosuper.explainer.script import Item, Respell, Scene, VoiceScript, respell_words
from vidosuper.explainer.visuals import assign
from vidosuper.studio.brain.core import check_voice_script
from vidosuper.studio.profiles import Profile, rules_for, scope_of

SMALL_SQUARE = {  # a 40 px red square in the middle of a 400 px canvas
    "v": "5.7.0", "fr": 30, "ip": 0, "op": 30, "w": 400, "h": 400,
    "layers": [{"ty": 4, "ip": 0, "op": 30, "st": 0,
                "ks": {"o": {"a": 0, "k": 100}, "r": {"a": 0, "k": 0}, "p": {"a": 0, "k": [200, 200, 0]},
                       "a": {"a": 0, "k": [0, 0, 0]}, "s": {"a": 0, "k": [100, 100, 100]}},
                "shapes": [{"ty": "gr", "it": [
                    {"ty": "rc", "p": {"a": 0, "k": [0, 0]}, "s": {"a": 0, "k": [40, 40]}, "r": {"a": 0, "k": 0}},
                    {"ty": "fl", "c": {"a": 0, "k": [1, 0, 0, 1]}, "o": {"a": 0, "k": 100}},
                    {"ty": "tr", "p": {"a": 0, "k": [0, 0]}, "a": {"a": 0, "k": [0, 0]}, "s": {"a": 0, "k": [100, 100]},
                     "r": {"a": 0, "k": 0}, "o": {"a": 0, "k": 100}}]}]}],
}


def test_animation_is_cropped_to_its_drawing():
    from vidosuper.explainer.lottie_lib import Lot

    lot = Lot(json.dumps(SMALL_SQUARE))
    left, top, right, bottom = lot.box
    assert 0.4 < left < 0.5 and 0.5 < right < 0.6 and 0.4 < top < 0.5 and 0.5 < bottom < 0.6
    assert abs(lot.aspect - 1.0) < 0.1
    img = np.asarray(lot.frame(0.1, 200))
    assert abs(img.shape[1] - 200) <= 4
    assert (img[..., 3] > 128).mean() > 0.7  # the square fills it, not a dot in the middle


def test_parts_scene_is_never_empty_before_its_first_part():
    scene = Scene(start=0.0, end=10.0, layout="compare", headline="Decides", emoji="🧠",
                  items=[Item(at=6.0, text="Decisions", emoji="🧠"), Item(at=8.0, text="LLMs", emoji="💬")])
    size = (480, 270)
    bare = np.asarray(draw.background(size, "berry", 2.0).convert("RGB"), np.float32)
    frame = draw.background(size, "berry", 2.0)
    draw.draw_scene(frame, scene, 2.0, draw.PALETTE["berry"], 0.0)
    diff = np.abs(np.asarray(frame.convert("RGB"), np.float32) - bare).sum(axis=2)
    middle = diff[60:230, 140:340]
    assert (middle > 40).mean() > 0.03  # its picture holds the stage while the first part is waited for


def test_captions_spell_names_as_meant():
    words = [{"start": 0, "end": 1, "text": "Jeff"}, {"start": 1, "end": 2, "text": "Jeff's"},
             {"start": 2, "end": 3, "text": "Jeb,"}, {"start": 3, "end": 4, "text": "chef"}]
    out = respell_words(words, [Respell(heard="Jeff", written="JEPA"), Respell(heard="Jeb", written="JEPA")])
    assert [w["text"] for w in out] == ["JEPA", "JEPA's", "JEPA,", "chef"]
    assert respell_words(words, []) is words
    both = respell_words(words, [Respell(heard="Jeff", written="JEPA"), Respell(heard="Jeff's", written="JEPA's")])
    assert [w["text"] for w in both][:2] == ["JEPA", "JEPA's"]  # never JEPA's's


def test_slow_voice_scripts_are_sent_back():
    utterances = [{"start": float(k), "end": k + 1.0, "text": "words words", "words": []} for k in range(0, 40)]
    slow = VoiceScript(name="x", scenes=[Scene(start=a, end=a + 10, layout="keyword", headline=f"h{a}", emoji="💡")
                                         for a in (0.0, 10.0, 20.0, 30.0)])
    problems = check_voice_script(slow, utterances, 40.0, "voice", "designed")
    assert any("nothing new appears" in p for p in problems)
    brisk = VoiceScript(name="x", scenes=[Scene(start=a, end=a + 4, layout="keyword", headline=f"h{a}", emoji="💡")
                                          for a in np.arange(0.0, 40.0, 4.0).tolist()])
    assert not any("nothing new appears" in p for p in check_voice_script(brisk, utterances, 40.0, "voice", "designed"))


class StubLibrary:
    """Every topic finds the same two animations; 'a' was picked before for a browser."""

    def remembered(self, need):
        return "a" if "browser" in need else None

    def candidates(self, topics, limit=12):
        return ["a", "b"]

    def rejected(self, need, cands):
        return False

    def sheet(self, ids, path):
        return path

    def remember(self, aid, need):
        pass

    def reject(self, need, cands):
        pass


def test_one_animation_appears_once_per_video(tmp_path):
    script = VoiceScript(name="x", scenes=[
        Scene(start=0, end=4, layout="keyword", headline="one", visual="a browser opening", topics=["browser"]),
        Scene(start=4, end=8, layout="keyword", headline="two", visual="a browser window", topics=["browser"]),
        Scene(start=8, end=12, layout="keyword", headline="three", visual="a fast rocket", topics=["rocket"])])
    out, _ = assign(script, StubLibrary(), lambda needs: [0 for _ in needs], tmp_path)
    picked = [s.lottie for s in out.scenes if s.lottie]
    assert len(picked) == len(set(picked))


def test_film_rules_stay_out_of_explainers(tmp_path):
    rules = [{"id": "o1", "text": "No on-screen text in the film.", "source": "owner", "for": "films"},
             {"id": "o2", "text": "A new video is a new idea.", "source": "owner", "for": "all"},
             {"id": "r1", "text": "Captions big.", "source": "feedback", "for": "talks"},
             {"id": "r2", "text": "Old rule.", "source": "feedback"}]
    assert [r["id"] for r in rules_for(rules, "voice")] == ["o2", "r1", "r2"]
    assert [r["id"] for r in rules_for(rules, "talk")] == ["o2", "r1", "r2"]
    assert [r["id"] for r in rules_for(rules, "edit")] == ["o1", "o2", "r2"]
    assert scope_of("voice") == "talks" and scope_of("edit") == "films"
    path = tmp_path / "profile.json"  # a profile saved before rules had a scope: the seeded film rules become films
    path.write_text(json.dumps({"rules": [{"id": "o4", "text": "No on-screen text in the film.", "source": "owner"}]}),
                    encoding="utf-8")
    assert Profile.load(path).rules[0]["for"] == "films"
