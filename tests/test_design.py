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
