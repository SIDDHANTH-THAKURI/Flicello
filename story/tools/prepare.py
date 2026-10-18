"""Get a talk ready for the story engine (story/public).

The talk is tightened first, the way an editor would: the long silences shortened to a breath, and the stumbles named in
the edit file (a repeated phrase, a false start) cut out, each cut placed on the quietest moment near the words. Then:
his picture (H.264), his cut-out (VP9 with alpha), his voice cleaned for listening (gentle denoise, mud and sibilance
tamed, evenly compressed, -16 LUFS), its level per frame (to duck the music and draw the waveform), the words with their
new times (captions), where the jump cuts are (so the engine can punch in on them), and the scene clips made light enough
to seek fast. Run with the studio's venv:

    python story/tools/prepare.py <talk video> <speech.json> <scene clips folder> [--edit edit.json] [--alpha]

edit.json: {"drop": [[from, to], ...] (seconds in the original, rough: each end moves to the quietest point within
0.15 s), "pause": 0.12, "sentence_pause": 0.25, "fixes": {"heard": "Written"}}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
# the media lives outside the repo (which sits in a synced folder): %LOCALAPPDATA%/VidoSuper/story/public, or VIDO_STORY_PUBLIC
PUBLIC = Path(os.environ.get("VIDO_STORY_PUBLIC") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "VidoSuper" / "story" / "public")
FPS = 30
SR = 48000
HOP = SR // 100  # the level is read every 10 ms


def tool(name: str = "ffmpeg") -> str:
    from vidosuper.ffmpeg import find_tool

    return find_tool(name)


def ffmpeg(*args: str) -> None:
    subprocess.run([tool(), "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def decode_audio(src: Path) -> np.ndarray:
    raw = subprocess.run([tool(), "-v", "error", "-i", str(src), "-map", "0:a:0", "-ac", "1", "-ar", str(SR),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def level_db(x: np.ndarray) -> np.ndarray:
    n = len(x) // HOP
    return 20 * np.log10(np.sqrt(np.mean(x[: n * HOP].reshape(n, HOP) ** 2, axis=1)) + 1e-9)


def speech_runs(db: np.ndarray, floor: float = -50.0, hang: int = 6, fill: int = 12) -> list[tuple[float, float]]:
    """Where he is speaking (seconds): louder than `floor`, held 60 ms either side, gaps under 120 ms joined."""
    on = db > floor
    if hang:
        on = np.convolve(on.astype(int), np.ones(2 * hang + 1, int), "same") > 0
    runs, start = [], None
    for k, v in enumerate(on):
        if v and start is None:
            start = k
        elif not v and start is not None:
            runs.append([start, k])
            start = None
    if start is not None:
        runs.append([start, len(on)])
    joined: list[list[int]] = []
    for a, b in runs:
        if joined and a - joined[-1][1] < fill:
            joined[-1][1] = b
        else:
            joined.append([a, b])
    return [(a / 100, b / 100) for a, b in joined]


def snap(t: float, db: np.ndarray, reach: float = 0.15) -> float:
    """The quietest 10 ms within `reach` of t: where a cut is least heard."""
    k0, k1 = max(0, int((t - reach) * 100)), min(len(db), int((t + reach) * 100) + 1)
    if k1 <= k0:
        return t
    return (k0 + int(np.argmin(db[k0:k1]))) / 100


def sounds(db: np.ndarray, quiet: float = -48.0, min_gap: int = 3) -> list[tuple[float, float]]:
    """Each run of sound between quiet moments (30 ms or more under `quiet`), in seconds."""
    runs, start, end, gap = [], None, 0, 0
    for k, v in enumerate(db > quiet):
        if v:
            start = k if start is None else start
            end, gap = k + 1, 0
        elif start is not None:
            gap += 1
            if gap >= min_gap:
                runs.append((start / 100, end / 100))
                start = None
    if start is not None:
        runs.append((start / 100, end / 100))
    return runs


FILLERS = {"uh", "um", "uhm", "er", "erm", "ah", "hmm", "mm"}


def heard(src: Path, cache: Path) -> list[dict]:
    """Every word of the original as Whisper hears it, fillers included (the talk's own transcript leaves them out)."""
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    import tempfile

    from vidosuper.config import load_config
    from vidosuper.perceive.transcribe import transcribe

    cfg = load_config()
    wav16 = Path(tempfile.gettempdir()) / "story-heard16k.wav"
    ffmpeg("-i", str(src), "-vn", "-ar", "16000", "-ac", "1", str(wav16))
    tr = transcribe("heard", wav16, cfg.analysis.speech_model, cfg.models_root / "whisper")
    rows = [{"text": w.text.strip(), "start": round(w.start, 3), "end": round(w.end, 3)} for u in tr.utterances for w in u.words]
    cache.write_text(json.dumps(rows), encoding="utf-8")
    return rows


def filler_drops(db: np.ndarray, words: list[dict]) -> tuple[list[tuple[float, float]], list[float]]:
    """Each "uh" -> the sound it is, when that sound is the filler alone: between two quiet moments, and with no
    other word in it. One glued to a word is left in: a cut inside a word is heard, a filler is not much."""
    runs = sounds(db)
    out, kept = [], []
    for i, w in enumerate(words):
        if re.sub(r"[^a-z]", "", w["text"].lower()) not in FILLERS:
            continue
        fs, fe = w["start"], w["end"]

        def overlap(r):
            return min(r[1], fe) - max(r[0], fs)

        best = max(runs, key=overlap, default=None)
        others = [(o["start"] + o["end"]) / 2 for j, o in enumerate(words) if j != i
                  and re.sub(r"[^a-z]", "", o["text"].lower()) not in FILLERS]
        if (best and overlap(best) > 0 and best[1] - best[0] <= 1.2
                and not any(best[0] - 0.03 <= c <= best[1] + 0.03 for c in others)):
            out.append((best[0] - 0.02, best[1] + 0.02))
        else:
            kept.append(fs)
    return out, kept


def ends_sentence(words: list[dict], t: float) -> bool:
    before = [w for w in words if w["end"] <= t + 0.3]
    return bool(before) and bool(re.search(r"[.?!]$", before[-1]["text"].strip()))


def keep_segments(db: np.ndarray, words: list[dict], drops: list[tuple[float, float]], pause: float,
                  sentence_pause: float, duration: float) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """The parts of the original to keep, on whole frames, and the dropped ranges as snapped."""
    snapped = [(snap(a, db), snap(b, db)) for a, b in drops]
    pieces: list[list[float]] = []  # [start, end, ends at a drop]
    for a, b in speech_runs(db):
        parts = [[a, b, False]]
        for s, e in snapped:
            nxt = []
            for pa, pb, cut in parts:
                if e <= pa or s >= pb:
                    nxt.append([pa, pb, cut])
                    continue
                if s > pa:
                    nxt.append([pa, s, True])
                if e < pb:
                    nxt.append([e, pb, cut])
            parts = nxt
        pieces += [p for p in parts if p[1] - p[0] >= 0.05]
    pieces.sort()
    for s, e in snapped:  # a drop that starts in a pause: the piece before it still ends at a cut, not a breath
        for p in pieces:
            if abs(p[1] - s) < 0.02:
                p[2] = True
    segs: list[list[float]] = []
    for i, (a, b, cut) in enumerate(pieces):
        start, end = a, b
        if i == 0:
            start = max(0.0, a - 0.15)
        else:
            prev_end, prev_cut = pieces[i - 1][1], pieces[i - 1][2]
            gap = a - prev_end
            p = sentence_pause if ends_sentence(words, prev_end) else pause
            if prev_cut or gap <= p:
                start = a if prev_cut else prev_end + gap * 0.55
            else:
                start = a - p * 0.45
        if i == len(pieces) - 1:
            end = min(duration, b + 0.45)
        else:
            nxt_a = pieces[i + 1][0]
            gap = nxt_a - b
            p = sentence_pause if ends_sentence(words, b) else pause
            if cut or gap <= p:
                end = b if cut else b + gap * 0.55
            else:
                end = b + p * 0.55
        segs.append([round(start * FPS) / FPS, round(end * FPS) / FPS])
    merged: list[list[float]] = []
    for a, b in segs:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], b)
        elif b > a:
            merged.append([a, b])
    return [(a, b) for a, b in merged], snapped


def join_audio(x: np.ndarray, segs: list[tuple[float, float]], fade: float = 0.006) -> np.ndarray:
    n = int(fade * SR)
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n, dtype=np.float32))
    parts = []
    for a, b in segs:
        part = x[int(round(a * SR)):int(round(b * SR))].copy()
        if len(part) > 2 * n:
            part[:n] *= ramp
            part[-n:] *= ramp[::-1]
        parts.append(part)
    return np.concatenate(parts)


def two_pass_loudnorm(src: Path, out: Path, target: float = -16.0, peak: float = -1.5) -> None:
    probe = subprocess.run([tool(), "-hide_banner", "-i", str(src), "-af",
                            f"loudnorm=I={target}:TP={peak}:LRA=11:print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True, check=True).stderr
    m = json.loads(probe[probe.rindex("{"):probe.rindex("}") + 1])
    ffmpeg("-i", str(src), "-af",
           f"loudnorm=I={target}:TP={peak}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
           f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true",
           "-ar", str(SR), "-ac", "2", str(out))


def clean_voice(x: np.ndarray, out: Path) -> None:
    """Speech for listening: brought up to a working level, rumble out, gentle denoise (the room is quiet: hard
    denoise only adds a watery sound), mud and sibilance tamed, evenly compressed, then -16 LUFS in one linear move."""
    import pyloudnorm as pyln

    loud = pyln.Meter(SR).integrated_loudness(x.astype(np.float64))
    x = x * np.float32(10 ** ((-22.0 - loud) / 20))
    raw, mid = out.with_suffix(".raw.wav"), out.with_suffix(".mid.wav")
    sf.write(str(raw), x, SR, subtype="FLOAT")
    chain = ("highpass=f=80:p=2,afftdn=nr=8:nf=-60,equalizer=f=220:t=q:w=1.2:g=-2,"
             "deesser=i=0.3:m=0.5:f=0.5,acompressor=threshold=-26dB:ratio=2.4:attack=10:release=160:makeup=2:knee=6,"
             "equalizer=f=3400:t=q:w=1.6:g=-1.5,lowpass=f=14000")
    ffmpeg("-i", str(raw), "-af", chain, "-c:a", "pcm_f32le", str(mid))
    two_pass_loudnorm(mid, out)
    raw.unlink(missing_ok=True)
    mid.unlink(missing_ok=True)


def full_picture(src: Path, out: Path, width: int = 1080, height: int = 1920) -> None:
    """His picture at a constant 30 fps (phones record a varying rate), so frame n is at n/30 s exactly."""
    ffmpeg("-i", str(src), "-an", "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,"
           f"crop={width}:{height},fps={FPS}", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
           "-pix_fmt", "yuv420p", str(out))


def tight_picture(full: Path, segs: list[tuple[float, float]], out: Path) -> None:
    terms = "+".join(f"between(n\\,{round(a * FPS)}\\,{round(b * FPS) - 1})" for a, b in segs)
    script = out.with_suffix(".filter.txt")
    script.write_text(f"select='{terms}',setpts=N/({FPS}*TB)", encoding="utf-8")
    ffmpeg("-i", str(full), "-/vf", str(script), "-an", "-c:v", "libx264", "-preset", "medium",
           "-crf", "17", "-pix_fmt", "yuv420p", "-g", "30", "-movflags", "+faststart", str(out))
    script.unlink(missing_ok=True)


def to_output(t: float, segs: list[tuple[float, float]]) -> float | None:
    at = 0.0
    for a, b in segs:
        if a - 1e-6 <= t <= b + 1e-6:
            return at + min(max(t, a), b) - a
        at += b - a
    return None


def tight_words(words: list[dict], segs: list[tuple[float, float]], dropped: list[tuple[float, float]],
                fixes: dict[str, str]) -> list[dict]:
    """The words on the tightened timeline. A word goes only when most of it lies in a cut (a stumble, a filler); one
    whose rough start fell in a shortened pause (Whisper starts words early) begins where its part of the talk does."""
    starts, at = [], 0.0
    for a, b in segs:
        starts.append(at)
        at += b - a
    rows: list[dict] = []
    for w in words:
        s0 = w["start"]
        s1 = min(max(w["end"], s0 + 0.05), s0 + 1.0)
        cut = max(dropped, key=lambda r: min(s1, r[1]) - max(s0, r[0]), default=None)
        inside = min(s1, cut[1]) - max(s0, cut[0]) if cut else 0.0
        if inside >= 0.6 * (s1 - s0) and s1 - cut[1] < 0.12:  # (a word whose tail outlives the cut is still heard)
            continue
        k = next((i for i, (a, b) in enumerate(segs) if b > s0 + 1e-6), None)
        if k is None:
            continue
        a, b = segs[k]
        t = starts[k] + max(s0, a) - a
        end = starts[k] + min(max(s1, a), b) - a
        text = w["text"].strip()
        key = text.lower().strip(".,!?")
        fixed = fixes.get(key)
        if fixed:
            text = fixed + text[len(text.rstrip(".,!?")):]
        if rows and re.match(r"^[.,]\d", text):  # "7" ".5" -> "7.5"
            rows[-1]["text"] += text
            rows[-1]["end"] = round(end, 3)
            continue
        rows.append({"t": round(t, 3), "end": round(max(end, t + 0.1), 3), "text": text})
    return rows


def jump_cuts(segs: list[tuple[float, float]], min_gap: float = 0.25) -> list[float]:
    """Output times where the original jumps by more than a breath: where his picture visibly skips."""
    cuts, at = [], 0.0
    for i, (a, b) in enumerate(segs):
        if i and a - segs[i - 1][1] > min_gap:
            cuts.append(round(at, 3))
        at += b - a
    return cuts


def levels(wav: Path, out: Path) -> None:
    """The voice's level per frame, 0..1, quick to rise and slow to fall (so music ducks smoothly under speech)."""
    data, sr = sf.read(str(wav), dtype="float32", always_2d=True)
    x = data.mean(axis=1)
    hop = sr // FPS
    n = len(x) // hop
    rms = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1))
    raw = np.clip((20 * np.log10(rms + 1e-9) + 50) / 30, 0, 1)  # -50 dB (silence) .. -20 dB (speech)
    smooth, level = np.zeros_like(raw), 0.0
    for k, v in enumerate(raw):
        level = level + (v - level) * (0.6 if v > level else 0.08)
        smooth[k] = level
    out.write_text(json.dumps([round(float(v), 3) for v in smooth]), encoding="utf-8")


def scenes(folder: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for clip in sorted(folder.glob("*.mp4")):
        dst = out / clip.name
        if dst.exists():
            continue
        part = dst.with_suffix(".part.mp4")
        ffmpeg("-i", str(clip), "-an", "-vf", f"scale='if(gt(iw,ih),-2,1080)':'if(gt(iw,ih),1080,-2)',fps={FPS}",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-g", "15",
               "-movflags", "+faststart", str(part))
        part.replace(dst)


def frames(src: Path, width: int, height: int):
    """Every frame of a video in order, as RGB at width x height: one decode, no seeking."""
    proc = subprocess.Popen([tool(), "-v", "error", "-i", str(src), "-an", "-vf", f"scale={width}:{height}:flags=area",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    size = width * height * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(height, width, 3)
    finally:
        proc.stdout.close()
        proc.wait()


def cutout(src: Path, out: Path, width: int = 1080, height: int = 1920) -> None:
    """Him without his room, as VP9 with alpha, full size (he is shown full size while his room turns into a scene)."""
    from vidosuper.explainer import cutout as matting

    stream = matting.Stream()
    total = int(subprocess.run([tool("ffprobe"), "-v", "error", "-count_packets", "-select_streams", "v:0",
                                "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(src)],
                               capture_output=True, text=True, check=True).stdout.strip() or 0)
    part = out.with_suffix(".part.webm")
    enc = subprocess.Popen([tool(), "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
                            "-s", f"{width}x{height}", "-r", str(FPS), "-i", "-", "-c:v", "libvpx-vp9",
                            "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "24", "-deadline", "good", "-cpu-used", "4",
                            "-row-mt", "1", "-tile-columns", "2", "-auto-alt-ref", "0", str(part)], stdin=subprocess.PIPE)
    pending, prev = None, None
    try:
        for k, rgb in enumerate(frames(src, width, height)):
            nxt = stream.raw(rgb)  # the model works on this frame while the last one is finished below
            if pending is not None:
                _write(enc, prev, stream.tracker(prev, pending.result()), matting)
            pending, prev = nxt, rgb
            if k % 300 == 0:
                print(f"cut-out {k}/{total}", flush=True)
        if pending is not None:
            _write(enc, prev, stream.tracker(prev, pending.result()), matting)
    finally:
        stream.close()
        enc.stdin.close()
        enc.wait()
    part.replace(out)


def _write(enc, rgb: np.ndarray, alpha: np.ndarray, matting) -> None:
    clean = np.clip(matting.decontaminate(rgb, alpha), 0, 255).astype(np.uint8)  # no light fringe of the room
    a = (np.clip(alpha, 0, 1) * 255).astype(np.uint8)
    enc.stdin.write(np.dstack([clean, a]).tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("talk", type=Path)
    ap.add_argument("speech", type=Path)
    ap.add_argument("scenes", type=Path)
    ap.add_argument("--edit", type=Path)
    ap.add_argument("--alpha", action="store_true", help="also make his cut-out (minutes: every frame is matted)")
    a = ap.parse_args()
    PUBLIC.mkdir(parents=True, exist_ok=True)
    edit = json.loads(a.edit.read_text(encoding="utf-8")) if a.edit else {}
    words = [w for u in json.loads(a.speech.read_text(encoding="utf-8"))["utterances"] for w in u.get("words") or []]
    work = PUBLIC.parent / "work"  # in-between files: kept out of public (Remotion copies all of public per render)
    work.mkdir(parents=True, exist_ok=True)
    # whose talk this is, and that it is in use: these copies are deleted with it once untouched for the retention
    # time (studio/legacy.py), and working on it now keeps it
    (work / "source.json").write_text(json.dumps({"talk": str(a.talk.resolve())}), encoding="utf-8")
    os.utime(a.talk)
    full = work / "narrator-full.mp4"
    if not full.exists():
        print("his picture, constant rate", flush=True)
        full_picture(a.talk, full)
    x = decode_audio(a.talk)
    db = level_db(x)
    fillers, glued = filler_drops(db, heard(a.talk, PUBLIC / "heard.json")) if edit.get("fillers") else ([], [])
    print(f"fillers cut: {len(fillers)}" + (f", left in (glued to a word): {', '.join(f'{t:.2f}' for t in glued)}" if glued else ""), flush=True)
    segs, dropped = keep_segments(db, words, [tuple(d) for d in edit.get("drop", [])] + fillers, edit.get("pause", 0.12),
                                  edit.get("sentence_pause", 0.25), len(x) / SR)
    tight = sum(b - a for a, b in segs)
    print(f"tightened {len(x) / SR:.1f} s -> {tight:.1f} s in {len(segs)} parts", flush=True)
    key = hashlib.sha1(json.dumps(segs).encode()).hexdigest()[:12]
    cuts_file = PUBLIC / "cuts.json"
    old = json.loads(cuts_file.read_text(encoding="utf-8")).get("key") if cuts_file.exists() else None
    clean_voice(join_audio(x, segs), PUBLIC / "voice.wav")
    levels(PUBLIC / "voice.wav", PUBLIC / "levels.json")
    # captions from what Whisper heard in the original (its times are closer than the talk's own transcript's),
    # without the fillers
    spoken = [{"text": w["text"], "start": w["start"], "end": w["end"]} for w in heard(a.talk, PUBLIC / "heard.json")
              if re.sub(r"[^a-z]", "", w["text"].lower()) not in FILLERS] if edit.get("fillers") else words
    out_words = tight_words(spoken, segs, dropped, edit.get("fixes", {}))
    (PUBLIC / "words.json").write_text(json.dumps(out_words), encoding="utf-8")
    cuts_file.write_text(json.dumps({"key": key, "seconds": round(tight, 3), "cuts": jump_cuts(segs),
                                     "segments": segs}), encoding="utf-8")
    if old != key or not (PUBLIC / "narrator.mp4").exists():
        print("his picture, tightened", flush=True)
        tight_picture(full, segs, PUBLIC / "narrator.mp4")
    print("scenes", flush=True)
    scenes(a.scenes, PUBLIC / "scenes")
    alpha = PUBLIC / "narrator-alpha.webm"
    if a.alpha and (old != key or not alpha.exists()):
        print("cut-out", flush=True)
        cutout(PUBLIC / "narrator.mp4", alpha)
    print("ready:", PUBLIC, flush=True)


if __name__ == "__main__":
    sys.exit(main())
