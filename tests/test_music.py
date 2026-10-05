from __future__ import annotations

import numpy as np
import pytest

from vidosuper.devtools import make_placeholder_music
from vidosuper.edit.snap import beat_alignment, snap_to_beats
from vidosuper.music import analyze_music
from vidosuper.schemas import EDL, ClipInfo, EditMeta, Interval, MusicSpec, Segment


@pytest.fixture(scope="module")
def placeholder(tmp_path_factory):
    path = make_placeholder_music(tmp_path_factory.mktemp("music") / "placeholder.wav", bpm=100)
    return path, analyze_music(path)


def test_tempo_beats_and_bars_are_exact(placeholder):
    _, music = placeholder
    assert abs(music.tempo - 100) < 0.5
    beats = np.array(music.beats)
    assert np.abs(beats - np.round(beats / 0.6) * 0.6).max() < 0.02  # within 20 ms of the real hits
    assert beats[0] < 0.7 and beats[-1] > 76  # grid covers the drum-less intro and outro too
    downs = np.array(music.downbeats)
    assert np.abs(downs - np.round(downs / 2.4) * 2.4).max() < 0.02  # bar starts, not other beats


def test_sections_follow_the_arrangement(placeholder):
    _, music = placeholder
    labels = [s.label for s in music.sections]
    assert labels[0] == "intro" and labels[-1] == "outro" and labels.count("peak") == 2
    peaks = [s for s in music.sections if s.label == "peak"]
    assert abs(peaks[0].start - 19.2) < 1.3 and abs(peaks[1].start - 48.0) < 1.3


def test_beat_clarity_finds_the_drumless_stretches(placeholder):
    from vidosuper.music.analysis import pulse_score

    _, music = placeholder
    weak = music.weak_beat_stretches()
    assert weak[0][0] == 0.0 and weak[0][1] <= 19.2  # the drum-less intro and build
    assert any(a >= 38.0 and b <= 48.5 for a, b in weak)  # the break
    assert pulse_score(music, 19.2, 16) > pulse_score(music, 0.0, 16) + 0.4
    assert "No audible beat on the grid" in music.summary()


def _clip(cid, duration=20.0):
    return ClipInfo(clip_id=cid, path=cid, rel_path=cid, fingerprint=cid, duration=duration, width=1920,
                    height=1080, fps=60, has_audio=True)


def test_cuts_snap_onto_beats_without_entering_bad_footage(placeholder):
    path, music = placeholder
    clips = {"a": _clip("a"), "b": _clip("b"), "c": _clip("c", duration=3.05)}
    edl = EDL(project_dir=".", meta=EditMeta(fps=30), music=MusicSpec(path=str(path), start=0.0), segments=[
        Segment(id="s1", clip_id="a", src_in=1.0, src_out=3.33),  # cut at 2.33 s -> beat 2.4 (+70 ms)
        Segment(id="s2", clip_id="b", src_in=5.0, src_out=5.9),   # once s1 ends on 2.4: cut at 3.3, halfway between beats
        Segment(id="s3", clip_id="c", src_in=0.0, src_out=1.0),
        Segment(id="s4", clip_id="a", src_in=8.0, src_out=9.0),
    ])
    notes = snap_to_beats(edl, clips, music, avoid={"a": [Interval(start=3.35, end=4.0, reason="shaky")]})
    assert any("s1->s2" in n and "could not" in n for n in notes)  # extending s1 would enter shaky footage
    edl.segments[0].src_out = 3.3  # without the shaky range in the way it snaps
    notes = snap_to_beats(edl, clips, music)
    assert any("s1->s2" in n and "moved" in n for n in notes)
    assert any("s2->s3" in n and "too far" in n for n in notes)
    on, total, free = beat_alignment(edl, clips, music)
    assert total == 3 and on >= 1 and free == 0
