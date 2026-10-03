from __future__ import annotations

import numpy as np
import pyloudnorm

from vidosuper.render.audio_mix import SR, _true_peaks, duck_envelope, normalize


def _noise(seconds: float, level: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal((int(seconds * SR), 2)) * level).astype(np.float32)


def test_normalize_hits_target_and_respects_true_peak():
    x = _noise(10, 0.02)
    x[SR * 5: SR * 5 + 200] = 0.9  # a sharp transient that would clip after gain
    y, measured, gain = normalize(x, -14.0, -1.0)
    assert measured is not None and gain > 10
    assert abs(pyloudnorm.Meter(SR).integrated_loudness(y.astype(np.float64)) + 14.0) < 0.7
    assert 20 * np.log10(_true_peaks(y).max()) <= -0.9


def test_silence_is_left_alone():
    y, measured, gain = normalize(np.zeros((SR * 2, 2), np.float32), -14.0, -1.0)
    assert measured is None and gain is None and not y.any()


def test_duck_envelope_dips_before_and_recovers_after_speech():
    hops = np.zeros(500, bool)  # 5 s at 10 ms hops
    hops[200:300] = True  # speech from 2.0 s to 3.0 s
    env = duck_envelope(hops, total=5 * SR)
    assert env[int(2.5 * SR)] > 0.99
    assert env[int(1.95 * SR)] > 0.5  # already ducking just before speech starts
    assert env[int(3.25 * SR)] > 0.5  # still ducked shortly after it ends
    assert env[int(0.5 * SR)] == 0 and env[int(4.5 * SR)] < 0.01
