from __future__ import annotations

import numpy as np

from vidosuper.qa import (beat_findings, black_findings, content_findings, loudness_findings, shake_findings,
                          voice_findings)
from vidosuper.schemas import EDL, EditMeta, Segment, SegmentAudio, SoundLayer


def _edl(*segments, sounds=()) -> EDL:
    return EDL(project_dir=".", meta=EditMeta(fps=30), segments=list(segments), sounds=list(sounds))


def test_beat_sync_and_black_and_loudness():
    grid = np.arange(0, 20, 0.25)
    ok = beat_findings([1.0, 2.505, 3.0], grid, 0.0, 10.0, strict=True)
    assert ok[0].level == "ok" and "worst 5 ms" in ok[0].detail
    late = beat_findings([1.0, 2.6], grid, 0.0, 10.0, strict=True)  # 100 ms late
    assert late[0].level == "fail" and "1 beyond 25 ms" in late[0].detail
    assert beat_findings([2.6], grid, 0.0, 10.0, strict=False)[0].level == "warn"
    assert black_findings([(0.0, 0.13), (5.2, 5.37)], [0.0, 5.3, 60.0])[0].level == "ok"
    assert black_findings([(20.0, 20.3)], [0.0, 5.3, 60.0])[0].level == "fail"  # mid-film black
    assert loudness_findings(-14.2, -1.4)[0].level == "ok"
    assert loudness_findings(-14.0, -0.2)[0].level == "fail"
    assert loudness_findings(-14.8, -1.1, -14.0, -1.5)[0].level == "warn"  # over the -1.5 target, under -1 dB


def test_voices_content_and_shake():
    seg = Segment(id="s1", clip_id="c23", src_in=5.4, src_out=7.0, audio=SegmentAudio(mode="mute"))
    clean = _edl(seg, sounds=[SoundLayer(source="clip:c23", at=0.0, duration=2.9, src_in=5.4)])
    voiced = _edl(seg, sounds=[SoundLayer(source="clip:c23", at=0.0, duration=3.6, src_in=5.4)])
    words = {"c23": [{"start": 8.7, "end": 10.3, "text": "how do you zoom"}]}
    assert voice_findings(clean, words)[0].level == "ok"
    assert voice_findings(voiced, words)[0].level == "fail"
    moments = {"c23": [{"start": 5.0, "end": 9.0, "description": "a woman in an olive jacket waves"}],
               "c20": [{"start": 40.0, "end": 50.0, "description": "waves over boulders"}]}
    assert content_findings(_edl(seg), moments, {})[0].level == "fail"
    surf = Segment(id="s2", clip_id="c20", src_in=46.0, src_out=48.0)
    found = content_findings(_edl(surf), moments, {"c20": "headland lookout, surf and a woman on the phone"})
    assert [f.level for f in found] == ["warn"]  # only the clip mentions her: look at the sheet
    t = np.arange(0, 60, 0.1)
    s = np.where((t > 47) & (t < 47.5), 0.2, 0.01)
    assert shake_findings(_edl(surf), {"c20": (t, s)}, 0.12)[0].level == "warn"
