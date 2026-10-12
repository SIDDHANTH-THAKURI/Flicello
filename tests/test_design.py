from __future__ import annotations

import numpy as np

from vidosuper.design import Design, build_template
from vidosuper.music import load_or_analyze


def test_design_lays_shots_on_the_song(ingested, media):
    project, clips, _ = ingested
    a, b = sorted(c.clip_id for c in clips.values() if c.duration >= 4)[:2]
    design = Design.model_validate({
        "name": "test", "song": str(media / "music.wav"), "sound_design": False,
        "pool": {"calm": [a, 0.0, 3.0], "hero": [b, 0.0, 3.0, 1.2]},
        "plan": [["hold", 4, "calm", {"speed": 1.0}], ["stutter", 2, "hero"],
                 ["hit", 4, "hero", {"transition": ["fadewhite", 0.2]}]]})
    template = build_template(design, project)
    kinds = [s.want.split()[0] for s in template.slots]
    assert kinds == ["hold", "stutter", "stutter", "hit"]
    song = load_or_analyze(design.song, project.music)
    period = float(np.median(np.diff(song.beats)))
    lengths = [s.end - s.start for s in template.slots]
    assert np.allclose(lengths, [4 * period, period / 2, period / 2, 4 * period], atol=0.01)  # on the beat grid
    hit = template.slots[-1]
    assert {f.type for f in hit.fx} == {"flash", "pulse"} and hit.transition_in.type == "fadewhite"
    assert hit.src_in == round(1.2 - 0.15, 2)  # on its logged action peak
    assert template.slots[2].src_in > template.slots[1].src_in - 1e-9  # the stutter steps on
    assert template.music.start == round(song.downbeats[0], 3)  # the film starts on the first bar


def test_an_action_peak_lands_exactly_on_a_beat(ingested, media, monkeypatch):
    from vidosuper.design import _Footage

    project, clips, _ = ingested
    a = sorted(c.clip_id for c in clips.values() if c.duration >= 4)[0]
    song = load_or_analyze(str(media / "music.wav"), project.music)
    period = float(np.median(np.diff(song.beats)))
    monkeypatch.setattr(_Footage, "refine_peak", lambda self, short, peak, reach=0.8, motion=True: (peak + 0.1, "sound"))  # the crack, 0.1 s later

    def build(peak):
        design = Design.model_validate({
            "name": "t", "song": str(media / "music.wav"), "sound_design": False,
            "pool": {"swing": [a, 0.0, 3.5, peak]},
            "plan": [["cut", 4, "swing", {"peak_beat": 2, "speed": 1.0}]]})
        return build_template(design, project)

    t = build(2.0)
    slot = t.slots[0]
    assert abs((slot.src_in + 2 * period) - 2.1) < 0.011  # refined peak (2.1 s) plays 2 beats into the shot
    assert t.idea == "1 of 1 action peaks land on a beat"
    assert build(0.3).idea == "0 of 1 action peaks land on a beat"  # too early in the clip to lead in: honestly off the beat


def test_a_punch_in_and_the_sound_of_the_impact_reach_the_template(ingested, media, monkeypatch):
    from vidosuper.design import _Footage
    from vidosuper.schemas import SoundLayer

    project, clips, _ = ingested
    a = sorted(c.clip_id for c in clips.values() if c.duration >= 4 and c.has_audio)[0]
    monkeypatch.setattr(_Footage, "refine_peak", lambda self, short, peak, reach=0.8, motion=True: (peak, "sound"))
    monkeypatch.setattr(_Footage, "ambience", lambda self, short, at, src, seconds, db, fi=0.5, fo=0.6:
                        SoundLayer(source=f"clip:{short}", at=round(at, 3), duration=seconds, src_in=round(src, 3), gain_db=db))
    design = Design.model_validate({
        "name": "t", "song": str(media / "music.wav"), "sound_design": True,
        "pool": {"swing": [a, 0.0, 3.5, 2.0]},
        "plan": [["cut", 4, "swing", {"peak_beat": 1, "zoom": 1.3, "zoom_center": [0.3, 0.5]}]]})
    template = build_template(design, project)
    assert template.slots[0].zoom == 1.3 and tuple(template.slots[0].zoom_center) == (0.3, 0.5)
    assert any(layer.source.startswith("clip:") and layer.duration == 0.5 for layer in template.sounds)
