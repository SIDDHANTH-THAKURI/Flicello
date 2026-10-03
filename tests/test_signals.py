from __future__ import annotations


def _sig(ingested, name):
    _, clips, signals = ingested
    return signals[clips[name].clip_id]


def test_shake_separates_jitter_from_pans(ingested):
    stable, shaky, pan = (_sig(ingested, n) for n in ("a_stable", "b_shaky", "c_pan"))
    assert shaky.shake_median > 0.1
    assert shaky.shake_median > 5 * max(stable.shake_median, 0.005)
    assert pan.pan_speed_median > 0.03 > stable.pan_speed_median
    assert pan.shake_median < shaky.shake_median / 3


def test_blur_and_exposure(ingested):
    stable, blurry, dark = (_sig(ingested, n) for n in ("a_stable", "d_blurry", "e_dark"))
    assert blurry.sharpness_median < stable.sharpness_median - 0.3
    assert dark.brightness_mean < stable.brightness_mean - 40


def test_moving_objects_on_a_static_camera_are_not_shake(ingested):
    # testsrc2 = flat colour bars + small moving objects, filmed by a perfectly still "camera"
    from vidosuper.perceive.summarize import SHAKE_OK

    for name in ("f_portrait60", "g_noaudio", "h_hdr", "i_rotated"):
        assert _sig(ingested, name).shake_median < SHAKE_OK, name


def test_dim_clip_loses_quality(ingested):
    stable, dark = _sig(ingested, "a_stable"), _sig(ingested, "e_dark")
    assert dark.quality_mean < stable.quality_mean - 0.05


def test_quality_ranks_bad_clips_lower(ingested):
    stable, shaky = _sig(ingested, "a_stable"), _sig(ingested, "b_shaky")
    assert shaky.quality_mean < stable.quality_mean - 0.2
    assert any(b.reason == "shaky" for b in shaky.bad_intervals)
    assert stable.best_windows and stable.best_windows[0].end - stable.best_windows[0].start <= 3.01
