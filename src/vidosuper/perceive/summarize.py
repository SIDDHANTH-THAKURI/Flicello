"""Turn raw measurement series into a per-clip quality curve, bad ranges, and best windows.

Thresholds are first guesses; they get calibrated on real footage. Bump SIGNALS_VERSION whenever
the maths changes so cached measurements are recomputed.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from scipy.ndimage import uniform_filter1d

from ..config import Config
from ..schemas import ClipInfo, ClipSignals, Interval, Window
from ..workspace import Project, write_model
from .audio_signals import AudioSeries, audio_peaks, measure_audio
from .video_signals import VideoSeries, measure_video

SIGNALS_VERSION = 6

# Camera shake: RMS jitter velocity, fraction of the frame diagonal per second.
SHAKE_OK, SHAKE_BAD = 0.05, 0.25
# Sharpness: log10 variance of the Laplacian at 320 px.
SHARP_OK, SHARP_BAD = 1.9, 1.2
# Subject motion level that counts as "a lot is happening".
MOTION_FULL = 0.08


def _odd(n: int) -> int:
    return max(3, n | 1)


def _fill_invalid(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    out = values.astype(np.float64).copy()
    good = valid & np.isfinite(out)
    if good.sum() == 0:
        return np.zeros_like(out)
    idx = np.arange(len(out))
    out[~good] = np.interp(idx[~good], idx[good], out[good])
    return out


def camera_shake(series: VideoSeries) -> tuple[np.ndarray, np.ndarray]:
    """Split camera motion into intentional (smooth pan/tilt) and jitter (shake)."""
    fs = series.sample_fps
    valid = series.valid.copy()
    if len(valid):
        valid[0] = True  # first sample has zero motion by definition
    dx = _fill_invalid(series.dx, valid)
    dy = _fill_invalid(series.dy, valid)
    da = _fill_invalid(series.da, valid)
    win = _odd(int(round(fs)))
    sx = uniform_filter1d(dx, win, mode="nearest")
    sy = uniform_filter1d(dy, win, mode="nearest")
    sa = uniform_filter1d(da, win, mode="nearest")
    jitter2 = (dx - sx) ** 2 + (dy - sy) ** 2 + (0.5 * (da - sa)) ** 2
    # running-sum filters can dip a hair below zero on mostly-zero input; clamp before sqrt
    shake = np.sqrt(np.maximum(uniform_filter1d(jitter2, win, mode="nearest"), 0.0)) * fs
    pan = np.hypot(sx, sy) * fs
    return shake.astype(np.float32), pan.astype(np.float32)


def _ramp(x: np.ndarray, good: float, bad: float) -> np.ndarray:
    return np.clip((x - good) / (bad - good), 0.0, 1.0)


def quality_curve(series: VideoSeries, shake: np.ndarray) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    sharp = series.sharpness.astype(np.float64)
    median_sharp = float(np.median(sharp)) if len(sharp) else 0.0
    p_shake = _ramp(shake, SHAKE_OK, SHAKE_BAD)
    p_blur_abs = _ramp(-sharp, -SHARP_OK, -SHARP_BAD)
    p_blur_rel = _ramp(median_sharp - sharp, 0.25, 0.7)  # focus hunting / motion blur vs the rest of the clip
    p_blur = np.maximum(0.8 * p_blur_abs, p_blur_rel)
    b = series.brightness.astype(np.float64)
    p_exp = np.maximum.reduce([
        _ramp(series.clip_high, 0.05, 0.25),
        _ramp(series.clip_low, 0.15, 0.45),
        _ramp(-b, -70.0, -25.0),  # mean luma below ~70 starts to look murky
        _ramp(b, 205.0, 240.0),
    ])
    q = (1 - 0.8 * p_shake) * (1 - 0.7 * p_blur) * (1 - 0.5 * p_exp)
    penalties = {"shaky": p_shake, "blurry": p_blur, "badly exposed": p_exp}
    return q.astype(np.float32), {k: v.astype(np.float32) for k, v in penalties.items()}


def interest_curve(series: VideoSeries, audio: AudioSeries | None, peaks: list[float]) -> np.ndarray:
    m = np.clip(series.motion / MOTION_FULL, 0, 1)
    if audio is None or len(audio.t) == 0:
        return m.astype(np.float32)
    level = np.clip((np.interp(series.t, audio.t, audio.rms_db) + 50.0) / 35.0, 0, 1)
    bonus = np.zeros_like(m)
    for p in peaks:
        bonus[np.abs(series.t - p) <= 0.5] = 1.0
    return np.clip(0.6 * m + 0.25 * level + 0.3 * bonus, 0, 1).astype(np.float32)


FAST_PAN = 0.25  # diagonal fraction per second: above this a "shaky" stretch is really a fast camera move


def bad_intervals(t: np.ndarray, q: np.ndarray, penalties: dict[str, np.ndarray], fs: float,
                  pan: np.ndarray | None = None, threshold: float = 0.5, min_len: float = 0.5) -> list[Interval]:
    out: list[Interval] = []
    low = q < threshold
    i = 0
    while i < len(low):
        if not low[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(low) and low[j + 1]:
            j += 1
        start, end = float(t[i]), float(t[j]) + 1.0 / fs
        if end - start >= min_len:
            reason = max(penalties, key=lambda k: float(penalties[k][i:j + 1].mean()))
            if reason == "shaky" and pan is not None and float(pan[i:j + 1].mean()) > FAST_PAN:
                reason = "fast camera move"  # abrupt pan/turn: unusable as a shot, maybe usable as a whip transition
            out.append(Interval(start=round(start, 2), end=round(end, 2), reason=reason))
        i = j + 1
    return out


def best_windows(t: np.ndarray, q: np.ndarray, interest: np.ndarray, duration: float, fs: float,
                 window_s: float, count: int = 3) -> list[Window]:
    if duration < 0.8 or len(t) == 0:
        return []
    margin = 0.25 if duration > window_s + 1.0 else 0.0
    length = min(window_s, duration - 2 * margin)
    n = max(1, int(round(length * fs)))
    if len(q) <= n:
        return [Window(start=round(margin, 2), end=round(margin + length, 2), score=round(float(q.mean()), 3),
                       quality=round(float(q.mean()), 3), interest=round(float(interest.mean()), 3))]
    cq = np.concatenate([[0.0], np.cumsum(q, dtype=np.float64)])
    ci = np.concatenate([[0.0], np.cumsum(interest, dtype=np.float64)])
    starts = np.arange(len(q) - n + 1)
    mean_q = (cq[starts + n] - cq[starts]) / n
    mean_i = (ci[starts + n] - ci[starts]) / n
    allowed = (t[starts] >= margin) & (t[starts] + length <= duration - margin + 1e-6)
    score = np.where(allowed, mean_q * (0.75 + 0.25 * mean_i), -1.0)
    picked: list[Window] = []
    taken = np.zeros(len(starts), dtype=bool)
    for _ in range(count):
        cand = np.where(taken, -1.0, score)
        k = int(np.argmax(cand))
        if cand[k] < 0:
            break
        start = float(t[starts[k]])
        picked.append(Window(start=round(start, 2), end=round(start + length, 2), score=round(float(score[k]), 3),
                             quality=round(float(mean_q[k]), 3), interest=round(float(mean_i[k]), 3)))
        taken[max(0, k - n + 1): k + n] = True  # no overlapping windows
    return picked


def scene_changes(t: np.ndarray, cut: np.ndarray, threshold: float = 0.5) -> list[float]:
    changes: list[float] = []
    for k in np.where(cut > threshold)[0]:
        if not changes or t[k] - changes[-1] > 1.0:
            changes.append(round(float(t[k]), 2))
    return changes


def measure_clip(clip: ClipInfo, project: Project, cfg: Config) -> ClipSignals:
    series = measure_video(clip.proxy, cfg.measure.analysis_fps, cfg.measure.analysis_width)
    if len(series.t) == 0:
        raise RuntimeError(f"No frames could be read from the proxy of {clip.rel_path}")
    fs = series.sample_fps
    audio = measure_audio(clip.audio_wav16k) if clip.audio_wav16k else None
    peaks = audio_peaks(audio) if audio is not None else []
    shake, pan = (np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0) for a in camera_shake(series))
    q, penalties = quality_curve(series, shake)
    q = np.nan_to_num(q, nan=0.0)  # an unmeasurable sample counts as bad, never as NaN in the cache
    interest = np.nan_to_num(interest_curve(series, audio, peaks), nan=0.0)
    windows = best_windows(series.t, q, interest, clip.duration, fs, cfg.measure.window_seconds)

    arrays = series.as_arrays()
    arrays.pop("sample_fps")
    arrays.update(shake=shake, pan=pan, quality=q, interest=interest)
    if audio is not None:
        arrays.update(audio_t=audio.t, audio_rms_db=audio.rms_db)
    np.savez_compressed(project.signals_npz(clip.clip_id), **arrays)

    summary = ClipSignals(
        clip_id=clip.clip_id,
        version=SIGNALS_VERSION,
        analysis_fps=round(fs, 3),
        duration=clip.duration,
        shake_median=round(float(np.median(shake)), 4),
        shake_p90=round(float(np.percentile(shake, 90)), 4),
        pan_speed_median=round(float(np.median(pan)), 4),
        sharpness_median=round(float(np.median(series.sharpness)), 3),
        brightness_mean=round(float(series.brightness.mean()), 1),
        clipped_high_pct=round(float(series.clip_high.mean() * 100), 2),
        clipped_low_pct=round(float(series.clip_low.mean() * 100), 2),
        motion_median=round(float(np.median(series.motion)), 4),
        motion_p90=round(float(np.percentile(series.motion, 90)), 4),
        saturation_mean=round(float(series.saturation.mean()), 1),
        audio_rms_db_median=round(float(np.median(audio.rms_db)), 1) if audio is not None and len(audio.t) else None,
        audio_silence_pct=round(float((audio.rms_db < -50).mean() * 100), 1) if audio is not None and len(audio.t) else None,
        quality_mean=round(float(q.mean()), 3),
        quality_best=round(max((w.quality for w in windows), default=float(q.mean())), 3),
        bad_intervals=bad_intervals(series.t, q, penalties, fs, pan),
        best_windows=windows,
        scene_changes=scene_changes(series.t, series.cut),
        audio_peaks=peaks,
    )
    write_model(project.signals_json(clip.clip_id), summary)
    return summary


def measure_project(project: Project, cfg: Config, *, force: bool = False,
                    on_clip: Callable[[str], None] | None = None) -> dict[str, ClipSignals]:
    clips = [c for c in project.clips().values() if c.usable]
    results: dict[str, ClipSignals] = {}
    todo: list[ClipInfo] = []
    for clip in clips:
        cached = None if force else project.load_signals(clip.clip_id)
        if cached is not None and cached.version == SIGNALS_VERSION and project.signals_npz(clip.clip_id).exists():
            results[clip.clip_id] = cached
        else:
            todo.append(clip)

    def work(clip: ClipInfo) -> tuple[str, ClipSignals | None]:
        try:
            return clip.clip_id, measure_clip(clip, project, cfg)
        except Exception:  # keep going; the clip just won't have measurements
            return clip.clip_id, None
        finally:
            if on_clip:
                on_clip(clip.rel_path)

    with ThreadPoolExecutor(max_workers=max(1, cfg.measure.workers)) as pool:
        for clip_id, summary in pool.map(work, todo):
            if summary is not None:
                results[clip_id] = summary
    return results
