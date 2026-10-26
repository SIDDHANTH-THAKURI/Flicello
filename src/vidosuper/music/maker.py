"""Music and sounds made on this machine: open models on the person's own GPU, so no paid music or sound service is
needed (ElevenLabs stays an option for whoever has a key).

- Music: ACE-Step 1.5 XL SFT (MIT, code and weights), through diffusers' AceStepPipeline. A track is made from the
  same plan ElevenLabs gets (music/made.py): the sound's words become its caption, the plan's sections become
  structure tags in its lyrics (an instrumental: no words, only the tags), its tempo and length are set exactly.
  The full track of a long film takes the taste they picked as its reference audio, so it sounds the same; a stretch
  of a made track is written again in place (repaint), the rest kept.
- Sound effects: Stable Audio 3 Small-SFX, Powered by Stability AI (Stability AI Community License: free to use
  commercially under $1M a year in revenue, once registered with Stability AI; trained on licensed audio only). A
  cue's description becomes the sound, at the length asked.

Both are loaded once by a sound process of its own (`python -m vidosuper.music.maker serve`, on 127.0.0.1), started
by the first track or sound and left running until it has been idle for IDLE seconds, when it ends and gives the GPU
back, as the painter does (motion/painter.py). Each model is loaded the first time it is asked for. On a GPU with
less memory than the music model, its parts visit the GPU in turn (slower, the same music)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

MUSIC_MODEL = os.environ.get("VIDO_MUSIC_MODEL", "ACE-Step/acestep-v15-xl-sft-diffusers")
SOUND_MODEL = os.environ.get("VIDO_SOUND_MODEL", "small-sfx")  # a Stable Audio 3 model name
SOUND_REPO = {"small-sfx": "stabilityai/stable-audio-3-small-sfx", "medium": "stabilityai/stable-audio-3-medium"}
PORT = int(os.environ.get("VIDO_SOUND_PORT", "8792"))
IDLE = float(os.environ.get("VIDO_SOUND_IDLE", "1200"))
MUSIC_STEPS, MUSIC_GUIDANCE, MUSIC_SHIFT = 50, 7.0, 3.0  # XL SFT's own defaults (it is not guidance-distilled)
SOUND_STEPS = 8
MUSIC_SR = 48000
MIN_SECONDS, MAX_SECONDS = 10.0, 600.0  # what ACE-Step makes
WHOLE_ON_GPU_GB = 20  # below this much GPU memory, the music model's parts visit the GPU in turn (cpu offload)
CAPTION_CHARS = 512


class MakerError(RuntimeError):
    pass


def device() -> str | None:
    """The GPU the models run on ("cuda" or "mps"), or None (no GPU, or no PyTorch here)."""
    try:
        import torch
    except ImportError:
        return None
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return None


def can_make_music() -> bool:
    """Music can be made here: a GPU, PyTorch and diffusers with ACE-Step's pipeline."""
    if device() is None:
        return False
    try:
        from diffusers import AceStepPipeline  # noqa: F401
    except ImportError:
        return False
    return True


def can_make_sounds() -> bool:
    """Sound effects can be made here: a GPU, PyTorch and Stable Audio 3."""
    if device() is None:
        return False
    try:
        import stable_audio_3  # noqa: F401
    except ImportError:
        return False
    return True


def downloaded(kind: str) -> bool:
    """The model's files are already on this machine (else its first use downloads them)."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    repo, name = (MUSIC_MODEL, "model_index.json") if kind == "music" else (SOUND_REPO.get(SOUND_MODEL, ""), "model.safetensors")
    found = try_to_load_from_cache(repo, name) if repo else None
    return isinstance(found, str) and Path(found).exists()


# ---------------------------------------------------------------- a plan in ACE-Step's words

TAGS = {  # our sections as the structure tags ACE-Step knows (a short "how" after the dash, never a stack of them)
    "intro": "Intro", "build": "Build", "drop": "Drop", "breath": "Breakdown", "lift": "Drop",
    "groove": "Main Theme", "ending": "Outro", "section": "Section",
}
SAID_ELSEWHERE = re.compile(r"^\s*\d+\s*bpm\s*$|^(instrumental|steady tempo)$", re.I)


def from_plan(plan: dict) -> dict:
    """A music plan (music_v2 chunks: section text, length, styles) as ACE-Step's inputs: {caption, lyrics, bpm,
    seconds}. The words every section shares are the caption (the track's portrait); what a section adds is the
    short "how" of its structure tag; tempo and length are set exactly, never written in the caption (its guide)."""
    chunks = [c for c in plan.get("chunks") or [] if "duration_ms" in c]
    if not chunks:
        raise MakerError("the music plan has no sections")
    styles = [[str(s) for s in c.get("positive_styles") or []] for c in chunks]
    bpm = None
    for s in styles[0]:
        if m := re.match(r"^\s*(\d{2,3})\s*bpm\s*$", s, re.I):
            bpm = int(m.group(1))
    shared = [s for s in styles[0] if all(s in other for other in styles[1:]) and not SAID_ELSEWHERE.match(s)]
    caption, used = [], 0
    for s in ["instrumental", *shared]:
        if used + len(s) + 2 > CAPTION_CHARS:
            break
        caption.append(s)
        used += len(s) + 2
    lines, last = [], None
    for c, own in zip(chunks, styles):
        name = (re.match(r"^\s*\[([^\]]+)\]", str(c.get("text") or "")) or [None, "section"])[1].strip().lower()
        if name == last:  # one long section split in two: the same section goes on
            continue
        last = name
        tag = TAGS.get(name, name.title())
        how = next((s for s in own if s not in shared and not SAID_ELSEWHERE.match(s) and not s.lower().endswith(tag.lower())), "")
        lines.append(f"[{tag} - {how}]" if how and len(how) <= 40 else f"[{tag}]")
    seconds = sum(c["duration_ms"] for c in chunks) / 1000.0
    return {"caption": ", ".join(caption), "lyrics": "\n\n".join(lines), "bpm": bpm, "seconds": round(seconds, 2)}


# ---------------------------------------------------------------- the sound process

class _Maker:
    def __init__(self):
        import torch

        self.torch = torch
        self.device = device()
        if self.device is None:
            raise MakerError("music and sounds made here need a GPU (NVIDIA with CUDA, or Apple Silicon)")
        self.music = None
        self.sound = None
        self.lock = threading.Lock()

    def _music(self):
        if self.music is None:
            from diffusers import AceStepPipeline

            torch = self.torch
            pipe = AceStepPipeline.from_pretrained(MUSIC_MODEL, torch_dtype=torch.bfloat16)
            if self.device == "cuda" and torch.cuda.get_device_properties(0).total_memory / 1024 ** 3 < WHOLE_ON_GPU_GB:
                pipe.enable_model_cpu_offload()
            else:
                pipe.to(self.device)
            pipe.vae.enable_tiling()  # long tracks: the decode's memory stays bounded
            pipe.set_progress_bar_config(disable=True)
            self.music = pipe
        return self.music

    def _sound(self):
        if self.sound is None:
            from stable_audio_3 import StableAudioModel

            self.sound = StableAudioModel.from_pretrained(SOUND_MODEL, device=self.device)
        return self.sound

    def _read(self, path: str):
        """A track as the music model takes it: [channels, samples] at 48 kHz, stereo."""
        import numpy as np
        import soundfile as sf

        audio, sr = sf.read(path, dtype="float32", always_2d=True)
        if sr != MUSIC_SR:
            n = int(round(len(audio) * MUSIC_SR / sr))
            x = np.linspace(0, len(audio) - 1, n)
            audio = np.stack([np.interp(x, np.arange(len(audio)), audio[:, k]) for k in range(audio.shape[1])], axis=1)
        if audio.shape[1] == 1:
            audio = np.repeat(audio, 2, axis=1)
        return self.torch.from_numpy(np.ascontiguousarray(audio[:, :2].T))

    def make_music(self, body: dict, out: Path) -> float:
        import soundfile as sf

        seconds = max(MIN_SECONDS, min(MAX_SECONDS, float(body["seconds"])))
        args = dict(prompt=str(body["caption"]), lyrics=str(body.get("lyrics") or "[Instrumental]"),
                    audio_duration=seconds, vocal_language="en", num_inference_steps=MUSIC_STEPS,
                    guidance_scale=MUSIC_GUIDANCE, shift=MUSIC_SHIFT,
                    generator=self.torch.Generator(device="cpu").manual_seed(int(body.get("seed") or 0)))
        if body.get("bpm"):
            args["bpm"] = int(body["bpm"])
        if body.get("reference") and Path(body["reference"]).exists():
            args["reference_audio"] = self._read(body["reference"])
        if body.get("repaint"):  # a stretch written again, the rest of the source kept
            src = self._read(body["repaint"]["source"])
            args.update(task_type="repaint", src_audio=src, repainting_start=float(body["repaint"]["start"]),
                        repainting_end=float(body["repaint"]["end"]), audio_duration=src.shape[-1] / MUSIC_SR)
        with self.lock, self.torch.inference_mode():  # one at a time on the GPU (the first one loads the model)
            pipe = self._music()
            audio = pipe(**args).audios[0]
        data = audio.T.float().cpu().numpy()
        sf.write(str(out), data, pipe.sample_rate, subtype="PCM_24")
        return len(data) / pipe.sample_rate

    def make_sound(self, body: dict, out: Path) -> float:
        import soundfile as sf

        seconds = max(0.3, min(30.0, float(body["seconds"])))
        with self.lock, self.torch.inference_mode():
            model = self._sound()
            audio = model.generate(prompt=str(body["prompt"]), duration=seconds, steps=SOUND_STEPS,
                                   seed=int(body.get("seed") or 0))
        data = audio[0].float().cpu().numpy().T
        peak = float(abs(data).max()) if data.size else 0.0
        if peak > 0:
            data = data / peak * 0.5  # -6 dBFS peak, as the built-in effects: the mix sets the level
        sf.write(str(out), data, model.model.sample_rate, subtype="PCM_24")
        return len(data) / model.model.sample_rate


def serve(port: int = PORT) -> None:
    """The sound process: answers on 127.0.0.1, loads each model the first time it is asked for, ends when idle."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    state = {"maker": None, "error": "", "last": time.time(), "busy": 0}

    try:
        state["maker"] = _Maker()
    except Exception as exc:  # noqa: BLE001 - said to whoever asks
        state["error"] = f"{type(exc).__name__}: {exc}"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            state["last"] = time.time()
            self._send(200, {"ready": state["maker"] is not None, "error": state["error"],
                             "music": MUSIC_MODEL, "sound": SOUND_MODEL})

        def do_POST(self):  # noqa: N802
            state["last"] = time.time()
            if state["maker"] is None:
                self._send(503, {"error": state["error"] or "not ready"})
                return
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            state["busy"] += 1
            try:
                out = Path(body["out"])
                out.parent.mkdir(parents=True, exist_ok=True)
                part = out.with_name(out.stem + ".part.wav")
                make = state["maker"].make_music if self.path == "/music" else state["maker"].make_sound
                seconds = make(body, part)
                os.replace(part, out)
                self._send(200, {"out": str(out), "seconds": round(seconds, 3)})
            except Exception as exc:  # noqa: BLE001 - the caller hears it (the film is never stopped by it)
                self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
            finally:
                state["busy"] -= 1
                state["last"] = time.time()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)

    def idle_watch():
        while True:
            time.sleep(30)
            if not state["busy"] and time.time() - state["last"] > IDLE:
                server.shutdown()
                return

    threading.Thread(target=idle_watch, daemon=True).start()
    server.serve_forever()


# ---------------------------------------------------------------- asking it

def _ask(path: str = "/", body: dict | None = None, timeout: float = 10.0) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - our own sound process on this machine
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, **json.loads(exc.read() or b"{}")}


def _start() -> None:
    """The sound process, started detached (it outlives the job or desk command that started it)."""
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([sys.executable, "-m", "vidosuper.music.maker", "serve"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
                     start_new_session=os.name != "nt")


def ready(wait: float = 120.0) -> None:
    """Waits for the sound process (starting it when it isn't running)."""
    deadline = time.time() + wait
    started = False
    while True:
        try:
            state = _ask(timeout=5)
        except (urllib.error.URLError, OSError):
            state = None
        if state and state.get("ready"):
            return
        if state and state.get("error"):
            raise MakerError(f"the sound maker couldn't start: {state['error']}")
        if state is None and not started:
            _start()
            started = True
        if time.time() > deadline:
            raise MakerError("the sound maker didn't start in time")
        time.sleep(2)


def _encode(wav: Path, out: Path) -> None:
    """The made audio in the format `out` asks for (by its suffix): mp3 at 320k, or a wav as made."""
    if out.suffix.lower() == ".wav":
        os.replace(wav, out)
        return
    from ..ffmpeg import ffmpeg_bin

    part = out.with_name(out.stem + ".part" + out.suffix)
    done = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "320k",
                           str(part)], capture_output=True)
    wav.unlink(missing_ok=True)
    if done.returncode != 0 or not part.exists():
        raise MakerError("the made track couldn't be saved: " + done.stderr.decode(errors="replace")[-300:])
    os.replace(part, out)


def _made(path: str, body: dict, out: Path, timeout: float) -> float:
    ready()
    wav = out.with_name(out.stem + ".made.wav")
    said = _ask(path, {**body, "out": str(wav.resolve())}, timeout=timeout)
    if said.get("error") or not wav.exists():
        raise MakerError(f"the sound maker failed: {said.get('error') or 'nothing was made'}")
    _encode(wav, out)
    return float(said.get("seconds") or 0.0)


def compose(plan: dict, out: Path, *, seed: int | None = None, reference: Path | None = None,
            timeout: float = 3600.0) -> dict:
    """A track for a music plan (music/made.py), at `out`. `reference`: a track it should sound like (the taste they
    picked). Returns {"path", "song_id" (none here), "seconds"}."""
    words = from_plan(plan)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    body = {"caption": words["caption"], "lyrics": words["lyrics"], "bpm": words["bpm"], "seconds": words["seconds"],
            "seed": seed if seed is not None else int.from_bytes(os.urandom(4), "little") % 2 ** 31}
    if reference is not None and Path(reference).exists():
        body["reference"] = str(Path(reference).resolve())
    seconds = _made("/music", body, out, timeout)
    return {"path": out, "song_id": "", "seconds": round(seconds or words["seconds"], 2)}


def repaint(source: Path, start: float, end: float, caption: str, out: Path, *, timeout: float = 1800.0) -> dict:
    """[start, end) of a made track written again from words, the rest of it kept; at `out`."""
    out = Path(out)
    body = {"caption": caption[:CAPTION_CHARS], "lyrics": "[Instrumental]", "seconds": MIN_SECONDS, "seed": 0,
            "repaint": {"source": str(Path(source).resolve()), "start": float(start), "end": float(end)}}
    seconds = _made("/music", body, out, timeout)
    return {"path": out, "song_id": "", "seconds": round(seconds, 2)}


def sound(description: str, seconds: float, out: Path, *, seed: int = 0, timeout: float = 600.0) -> Path:
    """A sound effect made to its description, `seconds` long, at `out` (48 kHz stereo when it is a wav)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    made = out.with_name(out.stem + ".sfx.wav")
    _made("/sound", {"prompt": description, "seconds": seconds, "seed": seed}, made, timeout)
    from ..ffmpeg import ffmpeg_bin

    part = out.with_name(out.stem + ".part" + out.suffix)
    done = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(made), "-ar", "48000", "-ac", "2", str(part)],
                          capture_output=True)
    made.unlink(missing_ok=True)
    if done.returncode != 0 or not part.exists():
        raise MakerError("the made sound couldn't be saved: " + done.stderr.decode(errors="replace")[-300:])
    os.replace(part, out)
    return out


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "serve":
        serve()
