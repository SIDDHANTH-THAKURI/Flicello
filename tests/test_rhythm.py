"""A steady sound layer (ticks) that drifts against the music's beat is flagged; one on the grid isn't."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import soundfile as sf

from vidosuper.edit.rhythm import rhythm_warnings
from vidosuper.schemas import EDL, GainPoint, MusicSpec, Segment, SoundLayer

SR = 48000
BEAT = 60 / 90


def _clicks(path, period: float, first: float, seconds: float = 12.0):
    y = np.zeros(int(seconds * SR), np.float32)
    t = np.arange(int(0.01 * SR)) / SR
    click = (np.sin(2 * np.pi * 2000 * t) * np.exp(-t * 400)).astype(np.float32)
    for at in np.arange(first, seconds - 0.05, period):
        i = int(round(at * SR))
        y[i:i + len(click)] += click
    sf.write(str(path), np.stack([y, y], axis=1), SR)
    return path


def _edl(path, automation=()) -> EDL:
    return EDL(project_dir=".", segments=[Segment(id="s1", clip_id="x", src_in=0.0, src_out=1.0)],
               music=MusicSpec(path="m.wav", start=0.0, automation=list(automation)),
               sounds=[SoundLayer(source=str(path), at=0.0, duration=11.0)])


def test_drifting_ticks_are_flagged_and_ticks_on_the_grid_are_not(tmp_path):
    analysis = SimpleNamespace(beats=list(np.arange(0, 30, BEAT)), downbeats=list(np.arange(0, 30, 4 * BEAT)))
    drifting = _clicks(tmp_path / "drift.wav", 0.70, 0.0)  # an indicator a little slower than the beat
    on_grid = _clicks(tmp_path / "grid.wav", BEAT / 2, 0.0)  # tick on the beat, tock on the half-beat
    found = rhythm_warnings(_edl(drifting), analysis, 12.0, lambda s: s)
    assert len(found) == 1 and "drifts against the track" in found[0]
    assert rhythm_warnings(_edl(on_grid), analysis, 12.0, lambda s: s) == []
    # with the music muted there is nothing to drift against
    muted = [GainPoint(t=0.0, gain_db=-60.0), GainPoint(t=20.0, gain_db=-60.0)]
    assert rhythm_warnings(_edl(drifting, muted), analysis, 12.0, lambda s: s) == []
