from __future__ import annotations

import pytest

from vidosuper.music.analysis import MusicAnalysis
from vidosuper.music.compose import to_v2_plan
from vidosuper.schemas import Fx, MusicSpec
from vidosuper.template import Slot, Template, retarget


def _song(period: float, drop: float, duration: float) -> MusicAnalysis:
    beats = [round(0.1 + k * period, 4) for k in range(int((duration - 0.1) / period) + 1)]
    return MusicAnalysis(path="song.mp3", fingerprint="x", duration=duration, tempo=60 / period, beats=beats,
                         downbeats=beats[::4], energy=[0.5] * int(duration), sections=[], drops=[drop])


def test_retarget_keeps_slots_in_beats_from_the_drop():
    old = _song(0.5, drop=20.1, duration=60.0)  # 120 BPM, drop on beat 40
    template = Template(name="t", music=MusicSpec(path="old.mp3", start=8.1), slots=[  # drop at 12.0 on screen
        Slot(id="a", start=0.0, end=12.0),
        Slot(id="b", start=12.0, end=13.0, fx=[Fx(type="pulse", at=0.5)]),
        Slot(id="c", start=13.0, end=19.0),
        Slot(id="d", start=19.0, end=20.0),
    ])
    # 150 BPM, drop on beat 40, room for 24 beats before it but only 12 after (the template has 16)
    new = _song(0.4, drop=16.1, duration=21.0)
    moved, notes = retarget(template, old, new, "new.mp3")
    assert moved.music.path == "new.mp3" and moved.music.start == pytest.approx(16.1 - 24 * 0.4, abs=1e-3)
    a, b, c, d = moved.slots
    assert b.start == pytest.approx(24 * 0.4, abs=1e-3) and b.end == pytest.approx(26 * 0.4, abs=1e-3)
    assert b.fx[0].at == pytest.approx(0.4)  # the hit stays on the same beat
    assert d.end == pytest.approx(36 * 0.4, abs=1e-3)  # squeezed after the drop to end with the song
    assert "b on the drop" in notes[0]
    # a song that drops early: the build squeezes into the beats there are, the rest stretches (at most 1.5x)
    early = _song(0.4, drop=4.9, duration=21.0)
    moved, _ = retarget(template, old, early, "early.mp3")
    a, b, c, d = moved.slots
    assert moved.music.start == pytest.approx(0.1) and b.start == pytest.approx(12 * 0.4, abs=1e-3)
    assert b.end - b.start == pytest.approx(3 * 0.4, abs=1e-3) and b.fx[0].at == pytest.approx(0.8)
    assert d.end == pytest.approx(36 * 0.4, abs=1e-3)


def test_v1_plan_becomes_v2_chunks():
    plan = {"positive_global_styles": ["house"], "negative_global_styles": ["vocals"], "sections": [
        {"section_name": "Intro", "positive_local_styles": ["pads"], "negative_local_styles": ["drums"],
         "duration_ms": 8000, "lines": []}]}
    chunk = to_v2_plan(plan)["chunks"][0]
    assert chunk["duration_ms"] == 8000 and chunk["text"].startswith("[Intro]")
    assert chunk["positive_styles"] == ["house", "pads"] and chunk["negative_styles"] == ["vocals", "drums"]
    assert to_v2_plan({"chunks": []}) == {"chunks": []}


def test_cuts_land_on_their_frames_without_drift():
    """Stutters (7.5 frames), whips and ramps: every cut stays within half a frame of its slot boundary."""
    from vidosuper.edit.timeline import build_timeline
    from vidosuper.schemas import EDL, ClipInfo, EditMeta, RampPoint, Segment, Transition
    from vidosuper.template import _span, frames_on_screen

    fps, t, slots = 30, 0.0, []
    shots = [(2.004, None, []), *[(0.25, None, [])] * 4, (0.501, ("smoothleft", 0.3), []),
             (2.004, None, [RampPoint(at=0, speed=2.0), RampPoint(at=1.8, speed=2.0), RampPoint(at=2.15, speed=0.4)]),
             (1.002, ("dissolve", 0.5), []), (0.501, None, [])] * 3
    for k, (length, tr, ramp) in enumerate(shots):
        slots.append(Slot(id=f"s{k}", start=round(t, 3), end=round(t + length, 3), ramp=ramp,
                          transition_in=Transition(type=tr[0], duration=tr[1]) if tr else Transition()))
        t += length
    clip = ClipInfo(clip_id="c", path="c", rel_path="c", fingerprint="c", duration=500.0, width=1920, height=1080,
                    fps=60, has_audio=True)
    frames, trans = frames_on_screen(slots, fps)
    segments = [Segment(id=s.id, clip_id="c", src_in=0.0, src_out=round(_span(s, f / fps), 4), ramp=s.ramp,
                        transition_in=Transition(type=s.transition_in.type, duration=t / fps) if t else Transition())
                for s, f, t in zip(slots, frames, trans)]
    timeline = build_timeline(EDL(project_dir=".", meta=EditMeta(fps=fps), music=MusicSpec(path="x.mp3"),
                                  segments=segments), {"c": clip})
    assert not timeline.notes and trans[5] == 3  # the whip after a 7-frame stutter gets the room it has
    for slot, item in zip(slots, timeline.items):
        assert item.start + item.trans_frames // 2 == round(slot.start * fps)  # fully on screen on its beat
