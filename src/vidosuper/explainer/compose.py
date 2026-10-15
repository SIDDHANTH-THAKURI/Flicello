"""Rendering: the script drawn frame by frame and handed to FFmpeg with the audio.

voice: every frame is drawn from scratch (background, the scene, captions); the audio is the person's recording, with
       an optional quiet beat underneath that ducks under the voice.
talk:  the person's video is decoded, passed through untouched except where something is drawn on it (callouts, captions,
       a short animated cutaway), and re-encoded with its own sound exactly as it was (no music, no loudness change).
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image

from ..ffmpeg import find_tool
from .draw import PALETTE, background, caption_pages, draw_callout, draw_captions, draw_scene, loudness, smooth
from .script import VoiceScript

FPS = 30
Progress = Callable[[float], None]


def _scene_at(script: VoiceScript, t: float):
    return next((s for s in script.scenes if s.start <= t < s.end), None)


def _words(utterances: list[dict]) -> list[dict]:
    return [w for u in utterances for w in u.get("words", [])]


def _scene_frame(script: VoiceScript, scene, t: float, size: tuple[int, int], level: float) -> Image.Image:
    pal_name = scene.palette or script.palette
    frame = background(size, pal_name, t, level)
    draw_scene(frame, scene, t, PALETTE[pal_name], level)
    return frame


def _encode_cmd(size: tuple[int, int], out: Path, audio_args: list[str], filter_complex: str | None, quality: str) -> list[str]:
    preset, crf = ("veryfast", "21") if quality == "draft" else ("fast", "18")
    cmd = [find_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{size[0]}x{size[1]}", "-r", str(FPS), "-i", "pipe:0", *audio_args]
    if filter_complex:
        cmd += ["-filter_complex", filter_complex, "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a:0"]
    return cmd + ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
                  "-movflags", "+faststart", "-shortest", str(out)]


def _run(cmd: list[str], frames, total: int, progress: Progress | None) -> None:
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    err: list[bytes] = []
    threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True).start()
    try:
        for n, data in enumerate(frames):
            proc.stdin.write(data)
            if progress and n % 15 == 0:
                progress(min(0.99, n / max(1, total)))
        proc.stdin.close()
    except BrokenPipeError:
        pass
    code = proc.wait()
    if code != 0:
        raise RuntimeError("the video encoder stopped: " + b"".join(err).decode("utf-8", "replace")[-400:])


def render_voice(script: VoiceScript, audio: Path, utterances: list[dict], out: Path, size: tuple[int, int],
                 music: Path | None = None, quality: str = "final", start: float = 0.0, end: float | None = None,
                 progress: Progress | None = None) -> Path:
    """An animated video for a recording alone. `start`/`end` cut a sample (seconds of the recording)."""
    samples, sr = sf.read(str(audio), dtype="float32", always_2d=True)
    total_s = len(samples) / sr
    end = min(total_s, end if end is not None else total_s)
    level = loudness(samples, sr, FPS)
    pages = caption_pages(_words(utterances), 3 if size[1] > size[0] else 5) if script.captions else []
    n = int((end - start) * FPS)
    pal = PALETTE[script.palette]

    def frames():
        for i in range(n):
            t = start + i / FPS
            scene = _scene_at(script, t) or (script.scenes[-1] if script.scenes else None)
            lv = float(level[min(len(level) - 1, int(t * FPS))])
            if scene is None:
                frame = background(size, script.palette, t, lv)
            else:
                frame = _scene_frame(script, scene, t, size, lv)
            if pages:
                draw_captions(frame, pages, t, PALETTE[(scene.palette if scene and scene.palette else script.palette)],
                              0.8 if size[1] > size[0] else 0.88)
            yield frame.convert("RGB").tobytes()

    audio_args = ["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(audio)]
    fc = None
    if music is not None:
        audio_args += ["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(music)]
        fc = ("[2:a]volume=0.22[m];[1:a]asplit=2[v][k];[m][k]sidechaincompress=threshold=0.04:ratio=9:attack=15:release=350[d];"
              "[v][d]amix=inputs=2:duration=first:normalize=0[a]")
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(_encode_cmd(size, out, audio_args, fc, quality), frames(), n, progress)
    return out


def render_talk(script: VoiceScript, video: Path, utterances: list[dict], out: Path, size: tuple[int, int], samples_wav: Path | None,
                quality: str = "final", start: float = 0.0, end: float | None = None, duration: float | None = None,
                progress: Progress | None = None) -> Path:
    """The person's own video with motion graphics over it, their sound untouched."""
    end = min(duration, end) if (duration and end is not None) else (end if end is not None else duration)
    seconds = (end - start) if end else None
    level = np.zeros(1)
    if samples_wav is not None and Path(samples_wav).is_file():
        data, sr = sf.read(str(samples_wav), dtype="float32", always_2d=True)
        level = loudness(data, sr, FPS)
    pages = caption_pages(_words(utterances), 3 if size[1] > size[0] else 5) if script.captions else []
    pal = PALETTE[script.palette]
    w, h = size
    src = [find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", *(["-t", f"{seconds:.3f}"] if seconds else []),
           "-i", str(video), "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS}",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    n_total = int((seconds or 0) * FPS) or None
    frame_bytes = w * h * 3

    def frames():
        proc = subprocess.Popen(src, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        i = 0
        try:
            while True:
                raw = proc.stdout.read(frame_bytes)
                if len(raw) < frame_bytes:
                    break
                t = start + i / FPS
                i += 1
                scene = _scene_at(script, t)
                callouts = [c for c in script.callouts if c.at - 0.05 <= t <= c.until]
                captions = bool(pages) and any(p[0]["start"] - 0.05 <= t <= p[-1]["end"] + 0.25 for p in pages)
                if scene is None and not callouts and not captions:
                    yield raw  # untouched
                    continue
                lv = float(level[min(len(level) - 1, int(t * FPS))])
                base = Image.frombuffer("RGB", (w, h), raw, "raw", "RGB", 0, 1).convert("RGBA")
                if scene is not None:
                    k = smooth((t - scene.start) / 0.22) * smooth((scene.end - t) / 0.22)
                    cut = _scene_frame(script, scene, t, size, lv)
                    base = Image.blend(base, cut, k) if k < 0.999 else cut
                for c in callouts:
                    draw_callout(base, c, t, pal)
                if captions:
                    draw_captions(base, pages, t, pal, 0.8 if h > w else 0.88)
                yield base.convert("RGB").tobytes()
        finally:
            proc.kill()

    audio_args = ["-ss", f"{start:.3f}", *(["-t", f"{seconds:.3f}"] if seconds else []), "-i", str(video)]
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(_encode_cmd(size, out, audio_args, None, quality), frames(), n_total or 1, progress)
    return out
