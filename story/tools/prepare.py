"""Get a talk ready for the story engine (story/public): the narrator's picture (H.264), his cut-out (VP9 with alpha),
his voice cleaned for listening (rumble and hiss out, sibilance tamed, gently compressed, -16 LUFS), its level per
frame (for ducking the music and drawing the waveform), the words with their times (captions), and the scene clips
made light enough to seek fast. Run with the studio's venv:

    python story/tools/prepare.py <talk video> <speech.json> <scene clips folder> [--alpha]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
PUBLIC = HERE.parent / "public"
FPS = 30


def ffmpeg(*args: str) -> None:
    from vidosuper.ffmpeg import find_tool

    subprocess.run([find_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def narrator(src: Path, out: Path, width: int = 1080, height: int = 1920) -> None:
    ffmpeg("-i", str(src), "-an", "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps={FPS}",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out))


def voice(src: Path, out: Path) -> None:
    """Clean speech: no rumble, no hiss, sibilance tamed, even level, -16 LUFS with true peaks under -2 dB."""
    chain = ("highpass=f=85,lowpass=f=13000,afftdn=nf=-28,deesser=i=0.35:m=0.5:f=0.5,"
             "acompressor=threshold=-21dB:ratio=2.6:attack=12:release=180:makeup=2,"
             "equalizer=f=3200:t=q:w=1.4:g=-2.5,loudnorm=I=-16:TP=-2:LRA=8")
    ffmpeg("-i", str(src), "-vn", "-af", chain, "-ar", "48000", "-ac", "2", str(out))


def levels(wav: Path, out: Path, fps: int = FPS) -> None:
    """The voice's level per frame, 0..1, quick to rise and slow to fall (so music ducks smoothly under speech)."""
    data, sr = sf.read(str(wav), dtype="float32", always_2d=True)
    x = data.mean(axis=1)
    hop = sr // fps
    n = len(x) // hop
    rms = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1))
    db = 20 * np.log10(rms + 1e-9)
    raw = np.clip((db + 50) / 30, 0, 1)  # -50 dB (silence) .. -20 dB (speech)
    smooth, level = np.zeros_like(raw), 0.0
    for k, v in enumerate(raw):
        level = level + (v - level) * (0.6 if v > level else 0.08)  # attack fast, release slow
        smooth[k] = level
    out.write_text(json.dumps([round(float(v), 3) for v in smooth]), encoding="utf-8")


def words(speech: Path, out: Path, fixes: dict[str, str]) -> None:
    data = json.loads(speech.read_text(encoding="utf-8"))
    rows = []
    for u in data["utterances"]:
        for w in u.get("words") or []:
            text = w["text"].strip()
            key = text.lower().strip(".,!?")
            rows.append({"t": round(w["start"], 3), "end": round(w["end"], 3), "text": fixes.get(key, text)})
    out.write_text(json.dumps(rows), encoding="utf-8")


def scenes(folder: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for clip in sorted(folder.glob("*.mp4")):
        dst = out / clip.name
        if dst.exists():
            continue
        ffmpeg("-i", str(clip), "-an", "-vf", f"scale='if(gt(iw,ih),-2,1080)':'if(gt(iw,ih),1080,-2)',fps={FPS}",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-g", "15",
               "-movflags", "+faststart", str(dst))


def cutout(src: Path, out: Path, width: int = 540, height: int = 960) -> None:
    """The narrator without his room, as a VP9 video with alpha (half size: it is only ever shown small)."""
    import cv2

    from vidosuper.explainer import cutout as matting
    from vidosuper.ffmpeg import find_tool

    tracker = matting.Tracker()
    cap = cv2.VideoCapture(str(src))
    enc = subprocess.Popen([find_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
                            "-pix_fmt", "rgba", "-s", f"{width}x{height}", "-r", str(FPS), "-i", "-",
                            "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "2M", "-auto-alt-ref", "0",
                            "-row-mt", "1", "-deadline", "realtime", "-cpu-used", "8", str(out)], stdin=subprocess.PIPE)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or FPS
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) / src_fps * FPS)
    for k in range(total):
        cap.set(cv2.CAP_PROP_POS_MSEC, k * 1000.0 / FPS)
        ok, bgr = cap.read()
        if not ok:
            break
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        rgb = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)
        alpha = tracker(rgb)
        rgb = matting.decontaminate(rgb, alpha)  # no light fringe of the room around him
        rgba = np.dstack([rgb, (np.clip(alpha, 0, 1) * 255).astype(np.uint8)])
        enc.stdin.write(rgba.tobytes())
        if k % 300 == 0:
            print(f"cut-out {k}/{total}", flush=True)
    enc.stdin.close()
    enc.wait()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("talk", type=Path)
    ap.add_argument("speech", type=Path)
    ap.add_argument("scenes", type=Path)
    ap.add_argument("--alpha", action="store_true", help="also make his cut-out (minutes: every frame is matted)")
    a = ap.parse_args()
    PUBLIC.mkdir(parents=True, exist_ok=True)
    print("narrator picture", flush=True)
    narrator(a.talk, PUBLIC / "narrator.mp4")
    print("voice", flush=True)
    voice(a.talk, PUBLIC / "voice.wav")
    levels(PUBLIC / "voice.wav", PUBLIC / "levels.json")
    words(a.speech, PUBLIC / "words.json", {"undertone": "Undertone", "youtube": "YouTube"})
    print("scenes", flush=True)
    scenes(a.scenes, PUBLIC / "scenes")
    if a.alpha:
        print("cut-out", flush=True)
        cutout(a.talk, PUBLIC / "narrator-alpha.webm")
    print("ready:", PUBLIC, flush=True)


if __name__ == "__main__":
    sys.exit(main())
