"""The explainer steps the studio runs for a voice-only or a talking-video project (kinds "voice" and "talk").

prepare(): decode and transcribe the recording once (kept in the project folder, so a retry or a revision costs
           nothing); nothing is sent to an AI except the transcript, later, by the brain.
sample() / render(): draw the script (see compose.py); the sample is the first stretch at draft quality.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import soundfile as sf

from ..config import Config
from ..ffmpeg import ffprobe_json, find_tool
from ..ingest import ingest_folder
from ..perceive.prep import ensure_transcript
from ..perceive.transcribe import transcribe
from .compose import render_talk, render_voice
from .script import VoiceScript, fit_script

Progress = Callable[[float, str], None]
SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080)}
SAMPLE_SECONDS = 9.0
MAX_AUDIO_SECONDS = 600.0


class ExplainerError(RuntimeError):
    """Said to the person as is."""


def speech_path(root: Path) -> Path:
    return Path(root) / "speech.json"


def load_speech(root: Path) -> dict | None:
    p = speech_path(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def prepare(kind: str, root: Path, clips_dir: Path, audio_file: Path | None, cfg: Config, progress: Progress) -> dict:
    """Transcript with word times and the file paths the render needs. Cached in <root>/speech.json."""
    cached = load_speech(root)
    if cached and Path(cached.get("audio", "")).exists():
        return cached
    root = Path(root)
    if kind == "voice":
        if audio_file is None or not Path(audio_file).is_file():
            raise ExplainerError("Add your recording first.")
        progress(0.1, "Listening to your recording")
        wav = root / "voice.wav"
        wav16 = root / "voice16k.wav"
        ff = find_tool("ffmpeg")
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(audio_file), "-vn", "-ar", "48000", "-ac", "2", str(wav)],
                       check=True, timeout=600)
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(wav), "-ar", "16000", "-ac", "1", str(wav16)],
                       check=True, timeout=600)
        duration = sf.info(str(wav)).duration
        if duration < 3:
            raise ExplainerError("That recording is only a couple of seconds long: say a bit more.")
        if duration > MAX_AUDIO_SECONDS:
            raise ExplainerError(f"Recordings can be up to {MAX_AUDIO_SECONDS / 60:.0f} minutes.")
        progress(0.3, "Writing down what you said")
        tr = transcribe("voice", wav16, cfg.analysis.whisper_model, cfg.models_root / "whisper")
        data = {"kind": "voice", "audio": str(wav), "video": None, "wav": str(wav), "duration": round(duration, 3),
                "utterances": [u.model_dump() for u in tr.utterances if u.confident], "language": tr.language}
    else:
        progress(0.1, "Looking at your video")
        project, clips = ingest_folder(clips_dir, cfg)
        usable = [c for c in clips if c.usable]
        if not usable:
            raise ExplainerError("That file couldn't be read as a video.")
        clip = max(usable, key=lambda c: c.duration)
        if not clip.has_audio:
            raise ExplainerError("That video has no sound, so there is nothing to explain. Add a video where someone speaks.")
        progress(0.3, "Writing down what is said")
        tr = ensure_transcript(project, clip, cfg)
        data = {"kind": "talk", "audio": str(clip.path), "video": str(clip.path), "wav": clip.audio_wav16k,
                "duration": round(clip.duration, 3), "width": clip.width, "height": clip.height,
                "utterances": [u.model_dump() for u in (tr.utterances if tr else []) if u.confident],
                "language": tr.language if tr else None}
    if not data["utterances"]:
        raise ExplainerError("I couldn't hear any speech in it. Try a recording where the voice is clearer.")
    data["words"] = sum(len(u.get("words", [])) for u in data["utterances"])
    speech_path(root).write_text(json.dumps(data), encoding="utf-8")
    return data


def transcript_text(speech: dict) -> str:
    return " ".join(u["text"] for u in speech["utterances"])


def fit(script: VoiceScript, speech: dict) -> VoiceScript:
    return fit_script(script, float(speech["duration"]), speech["kind"])


def _size(shape: str) -> tuple[int, int]:
    return SIZES.get(shape, SIZES["9:16"])


def render(script: VoiceScript, speech: dict, shape: str, out: Path, *, music: Path | None = None, sample: bool = False,
          progress: Callable[[float], None] | None = None) -> dict:
    """Draw the video (or its first stretch). Returns {"path", "seconds", "render_seconds"}."""
    t0 = time.monotonic()
    size = _size(shape)
    end = min(float(speech["duration"]), SAMPLE_SECONDS) if sample else None
    quality = "draft" if sample else "final"
    if speech["kind"] == "voice":
        render_voice(script, Path(speech["audio"]), speech["utterances"], out, size, music=music, quality=quality,
                     end=end, progress=progress)
    else:
        render_talk(script, Path(speech["video"]), speech["utterances"], out, size, Path(speech["wav"]) if speech.get("wav") else None,
                    quality=quality, end=end, duration=float(speech["duration"]), progress=progress)
    info = ffprobe_json(out)
    seconds = float(info["format"]["duration"])
    return {"path": out.name, "seconds": round(seconds, 2), "render_seconds": round(time.monotonic() - t0, 1)}
