"""Does a renderer machine (a new cloud container) have everything a real video needs? Checked with no one's
footage: FFmpeg encodes, the person cut-out model runs, the speech model loads, a short designed talking clip
(cut-out, captions, emoji, the layouts) renders end to end, and the motion engine (Node, HyperFrames and its Chrome)
renders a title with transparency. Asked for from the admin page (cloud.request_test)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path


def _clip(folder: Path, seconds: float = 6.0) -> tuple[Path, Path]:
    """A synthetic 720x1280 talking clip (a test pattern with a tone) and its 16 kHz mono sound."""
    from ..ffmpeg import find_tool

    ff = find_tool("ffmpeg")
    clip, wav = folder / "talk.mp4", folder / "talk16k.wav"
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size=720x1280:rate=30:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}", "-c:v", "libx264", "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)], check=True, timeout=120)
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(clip), "-vn", "-ar", "16000", "-ac", "1", str(wav)],
                   check=True, timeout=120)
    return clip, wav


def _speech(clip: Path, wav: Path, seconds: float) -> dict:
    words = "This is Flicello checking that this machine can make a real video from start to finish".split()
    step = (seconds - 0.6) / len(words)
    ws = [{"start": round(0.3 + k * step, 2), "end": round(0.3 + (k + 0.85) * step, 2), "text": w, "prob": 0.99}
          for k, w in enumerate(words)]
    half = len(ws) // 2
    utts = [{"start": g[0]["start"], "end": g[-1]["end"], "text": " ".join(w["text"] for w in g), "confident": True,
             "words": g} for g in (ws[:half], ws[half:])]
    return {"kind": "talk", "audio": str(clip), "video": str(clip), "wav": str(wav), "duration": seconds, "width": 720,
            "height": 1280, "utterances": utts, "language": "en", "words": len(ws)}


def run(progress: Callable[[float, str], None] = lambda f, m: None, speech_model: str = "large-v3-turbo",
        models_dir: Path | None = None, workers: int = 2) -> dict:
    """Every check, with what it measured; `ok` is False when one failed (its error is in `problems`)."""
    from ..explainer import cutout, make
    from ..explainer.script import local_script
    from ..ffmpeg import find_tool

    from ..cpus import usable_cpus

    out: dict = {"cpus": usable_cpus(), "problems": []}
    models_dir = Path(models_dir or os.environ.get("VIDO_MODELS_DIR") or ".")
    work = Path(tempfile.mkdtemp(prefix="flicello-selftest-"))
    progress(0.05, "Checking FFmpeg")
    try:
        first = subprocess.run([find_tool("ffmpeg"), "-version"], capture_output=True, text=True, timeout=30).stdout
        out["ffmpeg"] = first.splitlines()[0].split(" Copyright")[0][:60] if first else "?"
        clip, wav = _clip(work)
    except Exception as exc:  # noqa: BLE001 - nothing else can run without it
        out["problems"].append(f"ffmpeg: {exc}")
        out["ok"] = False
        return out
    progress(0.2, "Running the person cut-out model")
    try:
        if not cutout.available():
            raise RuntimeError(f"the model file is missing ({cutout.MODEL})")
        import numpy as np

        frame = np.full((1280, 720, 3), 128, dtype=np.uint8)
        cutout.raw_matte(frame, full=False)  # loads it
        t = time.perf_counter()
        for _ in range(10):
            cutout.raw_matte(frame, full=False)
        out["cutout_ms"] = round((time.perf_counter() - t) * 100, 1)
        out["cutout_on"] = cutout.providers()[0]
    except Exception as exc:  # noqa: BLE001
        out["problems"].append(f"cut-out: {exc}")
    progress(0.35, "Loading the speech model")
    try:
        from ..perceive.transcribe import _model

        t = time.perf_counter()
        _model(speech_model, models_dir / "whisper")
        out["speech_model_s"] = round(time.perf_counter() - t, 1)
    except Exception as exc:  # noqa: BLE001
        out["problems"].append(f"speech model: {exc}")
    progress(0.5, "Drawing a designed talking clip")
    try:  # long enough to be drawn in parallel parts, as a real talk is
        seconds = max(9.0, 6.0 * workers)
        clip, wav = _clip(work, seconds)
        speech = _speech(clip, wav, seconds)
        script = local_script(speech["utterances"], seconds, "talk", "Cloud check", designed=True)
        t = time.perf_counter()
        info = make.render(script, speech, "9:16", work / "check.mp4", workers=workers,
                           progress=lambda f: progress(0.5 + 0.45 * f, "Drawing a designed talking clip"))
        took = time.perf_counter() - t
        out["render_s"] = round(took, 1)
        out["render_fps"] = round(info["seconds"] * 30 / took, 2)
    except Exception as exc:  # noqa: BLE001
        out["problems"].append(f"render: {type(exc).__name__}: {exc}"[:300])
    progress(0.96, "Rendering a motion graphic")
    try:
        from .. import motion

        t = time.perf_counter()
        made = motion.render("title", {"text": "Flicello", "style": "rise"}, 540, 960, 30, 1.0, work / "motion")
        out["motion_s"] = round(time.perf_counter() - t, 1)
        if made.stat().st_size < 1000:
            raise RuntimeError("the title came out empty")
    except Exception as exc:  # noqa: BLE001
        out["problems"].append(f"motion graphics: {type(exc).__name__}: {exc}"[:300])
    out["ok"] = not out["problems"]
    return out


def summary(result: dict) -> str:
    """One line for the owner."""
    if not result.get("ok"):
        return "Cloud test failed: " + "; ".join(result.get("problems") or ["unknown"])[:300]
    return (f"Cloud test passed on {result.get('cpus')} CPUs: designed talk drawn at {result.get('render_fps')} "
            f"frames/s, cut-out {result.get('cutout_ms')} ms a frame on {result.get('cutout_on')}, speech model "
            f"ready in {result.get('speech_model_s')} s, a motion graphic in {result.get('motion_s')} s, "
            f"{result.get('ffmpeg')}.")
