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
from .script import VoiceScript, respell_words

FPS = 30
Progress = Callable[[float], None]


def _scene_at(script: VoiceScript, t: float):
    return next((s for s in script.scenes if s.start <= t < s.end), None)


def _words(utterances: list[dict]) -> list[dict]:
    return [w for u in utterances for w in u.get("words", [])]


FADE = 0.4  # seconds a new backdrop takes to dissolve in over the last one


def frost(plate: Image.Image, layer: Image.Image, ink: tuple[int, int, int]) -> Image.Image:
    """The plate with a frosted backing behind everything drawn on `layer`: there the painting is blurred and pushed
    to the opposite of the ink (light behind dark text, dark behind light text), fading out softly around it, so text
    and graphics read on any part of any painting while the painting still shows around them. Only the area around
    what is drawn is touched (whole-frame float maths made a video 4x slower to draw)."""
    import cv2

    w, h = plate.size
    bbox = layer.getbbox()
    if bbox is None:
        return plate
    pad = max(8, min(w, h) // 12)
    x0, y0 = max(0, bbox[0] - pad) // 4 * 4, max(0, bbox[1] - pad) // 4 * 4
    x1, y1 = min(w, bbox[2] + pad), min(h, bbox[3] + pad)
    cw, ch = x1 - x0, y1 - y0
    sw, sh = max(1, cw // 4), max(1, ch // 4)
    a = cv2.resize(np.asarray(layer.getchannel("A").crop((x0, y0, x1, y1)), np.float32) / 255.0, (sw, sh),
                   interpolation=cv2.INTER_AREA)
    r = max(2, min(w, h) // 160)
    a = cv2.dilate(a, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    m = np.clip(cv2.GaussianBlur(a, (0, 0), r * 1.6) * 1.5, 0, 0.92)
    base = np.asarray(plate.crop((x0, y0, x1, y1)).convert("RGB"))
    soft = cv2.GaussianBlur(cv2.resize(base, (sw, sh), interpolation=cv2.INTER_AREA).astype(np.float32), (0, 0), r * 1.2)
    target = np.array((246, 242, 234) if sum(ink) / 3 < 128 else (14, 14, 22), np.float32)
    soft = soft * 0.32 + target * 0.68
    mf = np.ascontiguousarray(cv2.resize(m, (cw, ch), interpolation=cv2.INTER_LINEAR))
    soft = np.ascontiguousarray(cv2.resize(soft, (cw, ch), interpolation=cv2.INTER_LINEAR))
    out = cv2.convertScaleAbs(cv2.blendLinear(base.astype(np.float32), soft, 1.0 - mf, mf))
    plate = plate.copy()
    plate.paste(Image.fromarray(out).convert("RGBA"), (x0, y0))
    return plate


def _plate(script: VoiceScript, k: int, t: float, plates, shots) -> Image.Image | None:
    s = script.scenes[k]
    if plates is None or not plates.has(s.backdrop_file):
        return None
    a, b, cam = shots[k]
    return plates.frame(s.backdrop_file, cam, (t - a) / max(0.1, b - a), t, light=(s.palette or script.palette) == "paper")


def _scene_frame(script: VoiceScript, scene, t: float, size: tuple[int, int], level: float, plates=None,
                 shots=None) -> Image.Image:
    pal_name = scene.palette or script.palette
    k = script.scenes.index(scene) if plates is not None and scene in script.scenes else -1
    plate = _plate(script, k, t, plates, shots) if k >= 0 else None
    if plate is None:
        frame = background(size, pal_name, t, level)
        draw_scene(frame, scene, t, PALETTE[pal_name], level)
        return frame
    a = shots[k][0]
    if k > 0 and t - a < FADE and shots[k - 1][0] != a:  # a new place: dissolve from the last one
        prev = script.scenes[k - 1]
        before = _plate(script, k - 1, t, plates, shots) or background(size, prev.palette or script.palette, t, level)
        plate = Image.blend(before, plate, float(smooth((t - a) / FADE)))
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw_scene(layer, scene, t, PALETTE[pal_name], level)
    plate = frost(plate, layer, PALETTE[pal_name]["ink"])
    plate.alpha_composite(layer)
    return plate


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
                 progress: Progress | None = None, lib=None, plates_dir: Path | None = None) -> Path:
    """An animated video for a recording alone. `start`/`end` cut a sample (seconds of the recording). With
    `plates_dir`, scenes that have a backdrop are drawn over it (see backdrops.py)."""
    from . import draw as _draw
    from .backdrops import Plates, shots as shots_of

    _draw.LOTTIE = (lambda aid, t, width: (lot.frame(t + float(lib.item(aid).get("lead", 0.0)), width)
                                           if (lot := lib.lot(aid)) is not None else None)) if lib is not None else None
    _draw.LOTTIE_ASPECT = (lambda aid: (lot.aspect if (lot := lib.lot(aid)) is not None else None)) if lib is not None else None
    samples, sr = sf.read(str(audio), dtype="float32", always_2d=True)
    total_s = len(samples) / sr
    end = min(total_s, end if end is not None else total_s)
    level = loudness(samples, sr, FPS)
    pages = caption_pages(respell_words(_words(utterances), script.respell), 3 if size[1] > size[0] else 5) if script.captions else []
    n = int((end - start) * FPS)
    pal = PALETTE[script.palette]
    plates = Plates(plates_dir, size, "9:16" if size[1] > size[0] else "16:9") if plates_dir else None
    shots = shots_of(script)

    def frames():
        for i in range(n):
            t = start + i / FPS
            scene = _scene_at(script, t) or (script.scenes[-1] if script.scenes else None)
            lv = float(level[min(len(level) - 1, int(t * FPS))])
            if scene is None:
                frame = background(size, script.palette, t, lv)
            else:
                frame = _scene_frame(script, scene, t, size, lv, plates, shots)
            if pages:
                cap_pal = PALETTE[(scene.palette if scene and scene.palette else script.palette)]
                y = 0.8 if size[1] > size[0] else 0.915
                if plates is not None and scene is not None and plates.has(scene.backdrop_file):
                    cap = Image.new("RGBA", size, (0, 0, 0, 0))
                    draw_captions(cap, pages, t, cap_pal, y)
                    frame = frost(frame, cap, (255, 255, 255))  # captions are white with an outline: a dark backing
                    frame.alpha_composite(cap)
                else:
                    draw_captions(frame, pages, t, cap_pal, y)
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
    pages = caption_pages(respell_words(_words(utterances), script.respell), 3 if size[1] > size[0] else 5) if script.captions else []
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
