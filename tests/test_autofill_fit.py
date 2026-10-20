"""A shot whose beats ask for more footage than its whole clip (short phone clips; the beat grid and transitions add a
little to what the plan measured) plays a touch slower to fill them, instead of running past the clip's end and
failing the whole film at its final render."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from vidosuper import template as tpl
from vidosuper.edit.validate import validate_edl
from vidosuper.schemas import MusicSpec
from vidosuper.template import Slot, Template, autofill


def _clip(cid: str, seconds: float) -> SimpleNamespace:
    return SimpleNamespace(clip_id=cid, duration=seconds, fps=30.0, width=1080, height=1920, usable=True, error=None,
                           has_audio=True)


@pytest.fixture()
def fake(monkeypatch, tmp_path):
    clips = {"a": _clip("a", 4.0), "b": _clip("b", 4.0), "long": _clip("long", 12.0)}
    monkeypatch.setattr(tpl, "_short_ids", lambda project: {})
    project = SimpleNamespace(clips=lambda: clips, clips_dir=tmp_path)
    return clips, project


def _template(slots: list[Slot]) -> Template:
    return Template(name="t", music=MusicSpec(), vertical=True, slots=slots)


def _on_screen(seg) -> float:
    return (seg.src_out - seg.src_in) / seg.speed


def test_a_hand_picked_shot_longer_than_its_clip_fills_its_beats_a_little_slower(fake, monkeypatch):
    clips, project = fake
    monkeypatch.setattr(tpl, "_moments", lambda project: [])
    edl = autofill(_template([Slot(id="s1", start=0.0, end=2.0, clip="a", src_in=0.5),
                              Slot(id="s2", start=2.0, end=6.47, clip="b", src_in=1.0)]), project, width=1080, height=1920)
    s1, s2 = edl.segments
    assert (s1.src_in, s1.src_out, s1.speed) == (0.5, 2.5, 1.0)  # fits: untouched
    assert s2.src_out <= clips["b"].duration and s2.src_in >= 0.0
    assert 0.75 <= s2.speed < 1.0 and _on_screen(s2) == pytest.approx(4.47, abs=0.01)
    assert not [i for i in validate_edl(edl, clips, {}, repair=False) if i.level == "error" and "past the clip end" in i.message]


def test_much_too_short_takes_a_long_enough_clip_when_there_is_one(fake, monkeypatch):
    clips, project = fake
    moments = [{"clip": clips["long"], "start": 1.0, "end": 9.0, "peak": None, "kind": "scenic", "score": 5, "text": "sea"}]
    monkeypatch.setattr(tpl, "_moments", lambda project: moments)
    edl = autofill(_template([Slot(id="s1", start=0.0, end=7.0, clip="a")]), project, width=1080, height=1920)
    (seg,) = edl.segments
    assert seg.clip_id == "long" and seg.speed == 1.0 and seg.src_out <= 12.0  # 0.57x would judder: the long clip


def test_with_no_clip_long_enough_the_film_still_gets_made(fake, monkeypatch):
    clips, project = fake
    monkeypatch.setattr(tpl, "_moments", lambda project: [])
    edl = autofill(_template([Slot(id="s1", start=0.0, end=7.0, clip="a")]), project, width=1080, height=1920)
    (seg,) = edl.segments
    assert seg.clip_id == "a" and seg.src_out <= 4.0 and _on_screen(seg) == pytest.approx(7.0, abs=0.01)
