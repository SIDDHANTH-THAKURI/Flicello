"""Checks before a result is shown: everything the user has caught by eye or ear so far, measured.

- beat sync: every hard cut in the film against the music's beat grid (a 145 ms drift once made the
  user hear "music beat not syncing");
- black: black frames anywhere but the planned fades;
- loudness: integrated loudness and true peak for posting;
- voices: no clip sound used where a voice was transcribed ("how do you zoom");
- content: no moment whose log mentions the companion or a car / windshield;
- shake: the measured shake inside every shot's source window.
A contact sheet of the film's shots is written for the things no number catches.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .edit.timeline import build_timeline
from .ffmpeg import ffmpeg_bin
from .perceive.prep import analysis_path, transcript_path
from .schemas import EDL
from .template import CAR_WORDS
from .workspace import Project

COMPANION = re.compile(r"\b(woman|women|girl|she|her|lady|olive jacket|companion|selfie)\b", re.I)


@dataclass
class Finding:
    level: str  # ok | warn | fail
    check: str
    detail: str


@dataclass
class Measured:
    duration: float
    cuts: list[float]
    blacks: list[tuple[float, float]]
    lufs: float | None
    true_peak: float | None


def measure(video: Path) -> Measured:
    """One FFmpeg pass: hard cuts (scene changes), black stretches, loudness and true peak."""
    err = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(video),
                          "-vf", "scale=320:-2,scdet=threshold=9,blackdetect=d=0.1:pix_th=0.06",
                          "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True).stderr
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    duration = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3)) if dur else 0.0
    summary = err[err.rfind("Summary:"):] if "Summary:" in err else ""
    lufs = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
    peak = re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary)
    return Measured(
        duration=duration,
        cuts=[float(x) for x in re.findall(r"lavfi\.scd\.time: ([\d.]+)", err)],
        blacks=[(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)],
        lufs=float(lufs.group(1)) if lufs else None, true_peak=float(peak.group(1)) if peak else None)


def beat_findings(cuts: list[float], grid: np.ndarray, start: float, end: float, *, strict: bool,
                  tolerance: float = 0.025) -> list[Finding]:
    """Cuts inside the film [start, end] against the beat grid (beats and half beats, film time)."""
    inside = [c for c in cuts if start + 0.3 < c < end - 0.3]
    if not inside or not len(grid):
        return [Finding("warn", "beat sync", "no hard cuts or no beat grid to check")]
    errs = np.array([np.min(np.abs(grid - c)) for c in inside])
    off = int((errs > tolerance).sum())
    detail = (f"{len(inside)} hard cuts: median {np.median(errs) * 1000:.0f} ms, worst {errs.max() * 1000:.0f} ms "
              f"off the beat" + (f"; {off} beyond {tolerance * 1000:.0f} ms" if off else ""))
    if not off:
        return [Finding("ok", "beat sync", detail)]
    return [Finding("fail" if strict else "warn", "beat sync",
                    detail + ("" if strict else " (free cuts are fine where the edit cuts on motion)"))]


def black_findings(blacks: list[tuple[float, float]], fades: list[float], slack: float = 0.6) -> list[Finding]:
    """Black is fine at the planned fades (start, intro/film/outro joins, end) and short."""
    bad = [(a, b) for a, b in blacks
           if b - a > 0.7 or not any(abs(a - f) <= slack or abs(b - f) <= slack for f in fades)]
    if bad:
        return [Finding("fail", "black frames", "unplanned black at " +
                        ", ".join(f"{a:.2f}-{b:.2f}s" for a, b in bad))]
    return [Finding("ok", "black frames", f"only the planned fades ({len(blacks)})")]


def loudness_findings(lufs: float | None, true_peak: float | None, target: float = -14.0,
                      ceiling: float = -1.0) -> list[Finding]:
    if lufs is None or true_peak is None:
        return [Finding("warn", "loudness", "could not measure")]
    detail = f"{lufs:.1f} LUFS, true peak {true_peak:.1f} dB"
    if true_peak > ceiling + 0.3:
        return [Finding("fail", "loudness", detail + f" (peaks above {ceiling:g} dB clip on phones)")]
    if abs(lufs - target) > 1.5:
        return [Finding("warn", "loudness", detail + f" (target {target:g})")]
    return [Finding("ok", "loudness", detail)]


def _sound_windows(edl: EDL) -> list[tuple[str, str, float, float]]:
    """(what, clip id, source start, source end) for every clip sound the edit plays."""
    out = [(seg.id, seg.clip_id, seg.src_in, seg.src_out) for seg in edl.segments
           if seg.audio.mode == "original" and not seg.ramp]
    out += [(f"sound {k + 1}", layer.source[5:], layer.src_in, layer.src_in + layer.duration)
            for k, layer in enumerate(edl.sounds) if layer.source.startswith("clip:")]
    return out


def voice_findings(edl: EDL, utterances: dict[str, list[dict]]) -> list[Finding]:
    hits = []
    for what, cid, a, b in _sound_windows(edl):
        for u in utterances.get(cid, []):
            if u.get("end", 0) > a and u.get("start", 0) < b:
                hits.append(f"{what} ({cid} {a:.1f}-{b:.1f}s) has a voice at {u['start']:.1f}s: "
                            f"\"{str(u.get('text', '')).strip()[:40]}\"")
    if hits:
        return [Finding("fail", "voices", "; ".join(hits))]
    return [Finding("ok", "voices", f"{len(_sound_windows(edl))} clip sounds, no voices in them")]


def content_findings(edl: EDL, moments: dict[str, list[dict]], scenes: dict[str, str]) -> list[Finding]:
    """Shots whose logged moment mentions the companion or a car / windshield fail; a clip that mentions
    them somewhere else only asks for a look at the contact sheet."""
    inside, clip_level = [], {}
    for seg in edl.segments:
        texts = [f"{m.get('description', '')} {m.get('notes') or ''}" for m in moments.get(seg.clip_id, [])
                 if m.get("end", 0) > seg.src_in and m.get("start", 0) < seg.src_out]
        hit = next((h for h in (COMPANION.search(t) or CAR_WORDS.search(t) for t in texts) if h), None)
        if hit:
            inside.append(f"{seg.id} ({seg.clip_id} {seg.src_in:.1f}s): '{hit.group(0)}' in the moment used")
        elif (h := COMPANION.search(scenes.get(seg.clip_id, "")) or CAR_WORDS.search(scenes.get(seg.clip_id, ""))):
            clip_level.setdefault(seg.clip_id, (h.group(0), []))[1].append(seg.id)
    out = []
    if inside:
        out.append(Finding("fail", "content", "; ".join(inside)))
    if clip_level:
        out.append(Finding("warn", "content", "check these by eye - the clip has it somewhere: " + "; ".join(
            f"{cid} '{word}' ({', '.join(ids)})" for cid, (word, ids) in clip_level.items())))
    if not out:
        out.append(Finding("ok", "content", f"{len(edl.segments)} shots, none logged with the companion or a car"))
    return out


def shake_findings(edl: EDL, shake: dict[str, tuple[np.ndarray, np.ndarray]], limit: float) -> list[Finding]:
    worst = []
    for seg in edl.segments:
        if seg.clip_id not in shake:
            continue
        t, s = shake[seg.clip_id]
        w = (t >= seg.src_in) & (t <= seg.src_out)
        if w.any() and float(s[w].max()) > limit:
            worst.append((float(s[w].max()), seg))
    if worst:
        worst.sort(key=lambda x: -x[0])
        return [Finding("warn", "shake", "shaky windows: " + ", ".join(
            f"{seg.id} ({seg.clip_id} {seg.src_in:.1f}s) {v:.3f}" for v, seg in worst[:6]))]
    return [Finding("ok", "shake", f"every shot under {limit:g}")]


def contact_sheet(video: Path, times: list[float], out: Path, width: int = 320) -> Path:
    """One frame from the middle of every shot, for the checks no number catches."""
    from PIL import Image

    height = round(width * 9 / 16)
    cols = 8
    sheet = Image.new("RGB", (cols * width, max(1, -(-len(times) // cols)) * height))
    for k, t in enumerate(times):
        png = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                              "-vf", f"scale={width}:{height}", "-f", "image2pipe", "-vcodec", "png", "-"],
                             capture_output=True).stdout
        if png:
            sheet.paste(Image.open(io.BytesIO(png)).convert("RGB"), ((k % cols) * width, (k // cols) * height))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=85)
    return out


def run_checks(video: Path, edl: EDL, project: Project, *, head: float = 0.0, tail: bool = False,
               strict_beats: bool = False) -> tuple[list[Finding], Path]:
    """All checks for a rendered video of this EDL (head: seconds of intro before the film)."""
    from .music import load_or_analyze

    video = Path(video)
    m = measure(video)
    clips = project.clips()
    timeline = build_timeline(edl, clips)
    film = timeline.duration
    findings: list[Finding] = []
    if edl.music is not None and edl.music.path:
        song = load_or_analyze(edl.music.path, project.music)
        beats = np.asarray(song.beats) - edl.music.start + head
        period = float(np.median(np.diff(beats))) if len(beats) > 1 else 0.0
        # a jump by whole beats (a bar repeated or skipped) leaves the grid where it was
        on_grid = all(period and abs(((j.at + edl.music.start - j.to) / period) - round((j.at + edl.music.start - j.to)
                                                                                         / period)) * period < 0.01
                      for j in edl.music.jumps)
        if on_grid:
            grid = np.sort(np.concatenate([beats, beats + period / 2]))
            if edl.music.jumps:  # extend the grid past the song's end by the beats the jumps add
                extra = beats[-1] + period * np.arange(1, 64)
                grid = np.sort(np.concatenate([grid, extra, extra + period / 2]))
            findings += beat_findings(m.cuts, grid, head, head + film, strict=strict_beats)
        else:
            findings.append(Finding("warn", "beat sync", "music jumps off the beat grid: not checked"))
    fades = [0.0, head, head + film, m.duration] if (head or tail) else [0.0, m.duration]
    findings += black_findings(m.blacks, fades)
    findings += loudness_findings(m.lufs, m.true_peak, edl.meta.loudness_lufs, edl.meta.true_peak_db)

    used = {seg.clip_id for seg in edl.segments} | {l.source[5:] for l in edl.sounds if l.source.startswith("clip:")}
    utterances, moments, scenes, shake = {}, {}, {}, {}
    for cid in used:
        if (p := transcript_path(project, cid)).exists():
            utterances[cid] = json.loads(p.read_text(encoding="utf-8")).get("utterances") or []
        if (p := analysis_path(project, cid)).exists():
            analysis = json.loads(p.read_text(encoding="utf-8"))
            moments[cid] = analysis.get("moments") or []
            scenes[cid] = analysis.get("scene") or ""
        if (p := project.signals_npz(cid)).exists():
            with np.load(p) as d:
                shake[cid] = (d["t"], d["shake"])
    findings += voice_findings(edl, utterances)
    findings += content_findings(edl, moments, scenes)
    findings += shake_findings(edl, shake, 0.12 if edl.meta.stabilize else 0.06)
    mids = [head + (item.start + item.frames / 2) / timeline.fps for item in timeline.items]
    sheet = contact_sheet(video, mids, project.tmp / f"check_{video.stem}.jpg")
    return findings, sheet
