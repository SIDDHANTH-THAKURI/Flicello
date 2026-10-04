"""Evidence pack for critics: what a draft actually looks and sounds like, as images and numbers.

Critics can't watch video or hear audio, so this turns a rendered draft into things they can judge:
cut pairs (the frames either side of every cut), the opening frame by frame, a dynamics chart
(loudness, cut timing, music energy, motion vs the director's plan) and a stats page.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont

from ..edit.snap import timeline_grid
from ..edit.timeline import Timeline, build_timeline
from ..ffmpeg import ffmpeg_bin
from ..music import MusicAnalysis
from ..perceive.contact_sheet import _font, build_sheet, format_time, grab_frames
from ..schemas import EDL, EditBrief
from ..workspace import Project, write_text_atomic

CHART_W, CHART_H = 1600, 560


def draft_loudness(draft: Path, scratch: Path, hop: float = 0.25) -> tuple[np.ndarray, np.ndarray]:
    """(times, dBFS RMS per hop) of the draft's soundtrack."""
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(draft), "-vn", "-ac", "1", "-ar", "16000", str(scratch)],
                   check=True)
    audio, sr = sf.read(str(scratch), dtype="float32")
    scratch.unlink(missing_ok=True)
    n = int(hop * sr)
    count = max(1, len(audio) // n)
    frames = audio[: count * n].reshape(count, n)
    db = 20 * np.log10(np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1)) + 1e-9)
    return (np.arange(count) + 0.5) * hop, db


def music_energy_on_timeline(edl: EDL, music: MusicAnalysis, total: float) -> tuple[np.ndarray, np.ndarray]:
    """Music energy (0-1) as heard on the timeline, including jumps and automation."""
    from ..edit.snap import music_pieces

    t = np.arange(0, total, 0.25)
    energy = np.zeros_like(t)
    if edl.music is None:
        return t, energy
    for t0, t1, m0 in music_pieces(edl.music, total):
        sel = (t >= t0) & (t < t1)
        idx = np.clip((m0 + t[sel] - t0).astype(int), 0, len(music.energy) - 1)
        energy[sel] = np.asarray(music.energy)[idx]
    if edl.music.automation:
        pts = sorted(edl.music.automation, key=lambda p: p.t)
        gain_db = np.interp(t, [p.t for p in pts], [p.gain_db for p in pts])
        energy *= np.clip(10 ** (gain_db / 20), 0, 1)
    return t, energy


def _cut_kinds(timeline: Timeline) -> list[tuple[float, str]]:
    fps = timeline.fps
    kinds = []
    for prev, item in zip(timeline.items, timeline.items[1:]):
        kind = item.seg.transition_in.type if item.seg.transition_in.type != "cut" else prev.seg.out_sync
        kinds.append((item.start / fps, kind))
    return kinds


def dynamics_chart(path: Path, timeline: Timeline, loud: tuple[np.ndarray, np.ndarray],
                   music_energy: tuple[np.ndarray, np.ndarray] | None, motion: list[float],
                   planned: list[float], sections: list[tuple[str, float, float]]) -> Path:
    total = timeline.duration
    img = Image.new("RGB", (CHART_W, CHART_H), (18, 18, 22))
    d = ImageDraw.Draw(img)
    font, small = _font(16), _font(13)
    left, right, top, bottom = 60, CHART_W - 20, 78, CHART_H - 50
    x = lambda s: left + (right - left) * s / max(total, 1e-6)  # noqa: E731
    y = lambda v: bottom - (bottom - top) * float(np.clip(v, 0, 1))  # noqa: E731
    for k, (name, s0, s1) in enumerate(sections):  # section bands; labels on two rows so short sections don't collide
        d.rectangle([x(s0), top, x(s1), bottom], outline=(60, 60, 70))
        d.text((x(s0) + 4, 32 + (k % 2) * 20), name[:34], fill=(200, 200, 210), font=small)
    for item, level in zip(timeline.items, motion):  # subject motion per shot (grey bars)
        s0, s1 = item.start / timeline.fps, item.end / timeline.fps
        d.rectangle([x(s0), y(level), x(s1), bottom], fill=(55, 55, 62))
    for t, kind in _cut_kinds(timeline):  # cuts
        colour = {"beat": (90, 200, 120), "downbeat": (60, 160, 255), "free": (255, 160, 60)}.get(kind, (220, 90, 220))
        d.line([x(t), top, x(t), bottom], fill=colour, width=2)
    if planned:  # director's planned dynamics (dashed white)
        ts = np.linspace(0, total, len(planned))
        pts = [(x(a), y(b)) for a, b in zip(ts, planned)]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            for k in range(0, 20, 2):
                f0, f1 = k / 20, (k + 1) / 20
                d.line([x0 + (x1 - x0) * f0, y0 + (y1 - y0) * f0, x0 + (x1 - x0) * f1, y0 + (y1 - y0) * f1],
                       fill=(240, 240, 240), width=2)
    if music_energy is not None:  # music energy as heard (purple)
        t, e = music_energy
        d.line([(x(a), y(b)) for a, b in zip(t, e)], fill=(170, 110, 255), width=2)
    t, db = loud  # soundtrack loudness (cyan): -50 dBFS .. -5 dBFS mapped to 0..1
    d.line([(x(a), y((b + 50) / 45)) for a, b in zip(t, db) if a <= total], fill=(60, 220, 230), width=2)
    for s in range(0, int(total) + 1, 5):
        d.text((x(s) - 8, bottom + 6), f"{s}s", fill=(150, 150, 160), font=small)
    legend = [("planned dynamics", (240, 240, 240)), ("soundtrack loudness", (60, 220, 230)),
              ("music energy as heard", (170, 110, 255)), ("cut on beat", (90, 200, 120)),
              ("cut on bar", (60, 160, 255)), ("free cut", (255, 160, 60)), ("transition", (220, 90, 220)),
              ("subject motion", (110, 110, 120))]
    lx = left
    for label, colour in legend:
        d.rectangle([lx, CHART_H - 22, lx + 14, CHART_H - 10], fill=colour)
        d.text((lx + 18, CHART_H - 26), label, fill=(210, 210, 215), font=small)
        lx += 26 + int(d.textlength(label, font=small))
    d.text((left, 8), "Dynamics: plan vs what the draft actually does", fill=(255, 255, 255), font=font)
    img.save(path)
    return path


def cut_pair_sheets(draft: Path, timeline: Timeline, shorts: dict[str, str], out_dir: Path) -> list[Path]:
    fps = timeline.fps
    frames_wanted, labels = [], []
    kinds = _cut_kinds(timeline)
    for (prev, item), (_, kind) in zip(zip(timeline.items, timeline.items[1:]), kinds):
        before = (item.start - 1) / fps  # last frame before the cut (or before a transition begins)
        after = (item.start + item.trans_frames + 0.5) / fps  # first clean frame of the next shot
        frames_wanted += [before, after]
        labels += [f"{prev.seg.id} end {shorts.get(prev.seg.clip_id, '')}",
                   f"-> {item.seg.id} @{format_time(item.start / fps)} {kind}"]
    frames = grab_frames(draft, frames_wanted)
    for old in out_dir.glob("cuts_*.jpg"):
        old.unlink()
    paths = []
    per = 16  # 8 cut pairs per sheet, laid out 4 tiles (2 pairs) per row
    for n, start in enumerate(range(0, len(frames), per)):
        sheet = build_sheet(frames[start:start + per], tile_long_edge=360, columns=4, labels=labels[start:start + per])
        path = out_dir / f"cuts_{n + 1:02d}.jpg"
        sheet.save(path, quality=85)
        paths.append(path)
    return paths


def build_evidence(run_dir: Path, iteration_dir: Path, project: Project, brief: EditBrief | None,
                   music: MusicAnalysis | None, shorts: dict[str, str]) -> Path:
    edl = EDL.model_validate_json((iteration_dir / "edl.final.json").read_text(encoding="utf-8"))
    clips = project.clips()
    timeline = build_timeline(edl, clips)
    draft = iteration_dir / "draft.mp4"
    total = timeline.duration
    fps = timeline.fps

    hook_times = [k / 5 for k in range(15)]
    build_sheet(grab_frames(draft, hook_times), tile_long_edge=320, columns=5,
                labels=[f"@{format_time(t)}" for t in hook_times]).save(iteration_dir / "hook.jpg", quality=85)
    cut_pair_sheets(draft, timeline, shorts, iteration_dir)

    loud = draft_loudness(draft, iteration_dir / "_loud.wav")
    music_curve = music_energy_on_timeline(edl, music, total) if music is not None else None
    motion = []
    for item in timeline.items:
        npz = project.signals_npz(item.seg.clip_id)
        level = 0.0
        if npz.exists():
            data = np.load(npz)
            sel = (data["t"] >= item.seg.src_in) & (data["t"] <= item.seg.src_out)
            level = float(np.clip(np.median(data["motion"][sel]) / 0.08, 0, 1)) if sel.any() else 0.0
        motion.append(level)
    sections = [(s.name, s.start, s.end) for s in brief.structure] if brief else []
    dynamics_chart(iteration_dir / "dynamics.png", timeline, loud, music_curve, motion,
                   brief.dynamics if brief else [], sections)

    lengths = np.array([i.frames / fps for i in timeline.items])
    kinds = [k for _, k in _cut_kinds(timeline)]
    lines = ["# Evidence pack", "",
             f"Duration {total:.1f}s, {len(lengths)} shots, average {lengths.mean():.2f}s, median {np.median(lengths):.2f}s, "
             f"shortest {lengths.min():.2f}s, longest {lengths.max():.2f}s.",
             f"Cuts: {kinds.count('beat')} on the beat, {kinds.count('downbeat')} on bar starts, {kinds.count('free')} free, "
             f"{sum(1 for k in kinds if k not in ('beat', 'downbeat', 'free'))} transitions.",
             f"First cut at {timeline.items[1].start / fps:.2f}s." if len(timeline.items) > 1 else "Single shot."]
    if sections:
        lines += ["", "| section | planned | shots | avg shot | loudness (dBFS) | motion |", "|---|---|---|---|---|---|"]
        lt, ldb = loud
        for name, s0, s1 in sections:
            inside = [k for k, it in enumerate(timeline.items) if s0 <= (it.start + it.frames / 2) / fps < s1]
            avg = lengths[inside].mean() if inside else 0.0
            db = ldb[(lt >= s0) & (lt < s1)]
            mot = np.mean([motion[k] for k in inside]) if inside else 0.0
            lines.append(f"| {name} | {s0:.1f}-{s1:.1f}s | {len(inside)} | {avg:.2f}s | "
                         f"{db.mean() if len(db) else float('nan'):.1f} | {mot:.2f} |")
    lt, ldb = loud
    quiet = lt[ldb < -40]
    lines += ["", f"Soundtrack: {float(np.mean(ldb)):.1f} dBFS average; quietest stretch {ldb.min():.1f} dBFS; "
              f"{len(quiet) * 0.25:.1f}s below -40 dBFS (near-silence)."]
    repeats = [f"{a.seg.id}/{b.seg.id}" for a, b in zip(timeline.items, timeline.items[1:]) if a.seg.clip_id == b.seg.clip_id]
    if repeats:
        lines.append(f"Back-to-back shots from the same clip: {', '.join(repeats)}.")
    if edl.texts:
        lines.append("Text: " + "; ".join(f"\"{t.text}\" {t.start:.1f}-{t.end:.1f}s" for t in edl.texts))
    if edl.sounds:
        lines.append("Sound layers: " + "; ".join(f"{s.source.split(':')[0]} @{s.at:.1f}s {s.duration:.1f}s" for s in edl.sounds))
    lines.append(f"Look: {edl.meta.look}; letterbox: {edl.meta.letterbox or 'none'}.")
    if music is not None and edl.music is not None:
        beats, downs = timeline_grid(edl.music, music, total)
        lines.append(f"Music: {music.tempo:.0f} BPM from {edl.music.start:.1f}s; {len(edl.music.automation)} automation points, "
                     f"{len(edl.music.jumps)} jumps; {len(beats)} beats on the timeline.")
    lines += ["", "Images: hook.jpg (first 3 s, 5 fps), cuts_*.jpg (frames either side of each cut), "
              "shots_*.jpg (middle of every shot), dynamics.png (plan vs actual)."]
    out = iteration_dir / "evidence.md"
    write_text_atomic(out, "\n".join(lines) + "\n")
    return out
