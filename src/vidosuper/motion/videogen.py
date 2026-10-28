"""Video made by a video model on this machine's GPU: a shot's painted start frame (and, when the shot must land on a
pose, its end frame) brought to life from a sentence of what happens. The model makes all the motion: bodies, fur,
cloth, light and the camera. Nothing is moved by code.

Two looks, one engine (Wan 2.2's image-to-video architecture, two experts of 14B, through diffusers):
- `cartoon`: Wan2.2-I2V-A14B (Apache-2.0), its experts from the fp16 single files of
  Comfy-Org/Wan_2.2_ComfyUI_Repackaged (half the download of the official fp32 ones), the rest (text encoder, VAE)
  from Wan-AI/Wan2.2-I2V-A14B-Diffusers.
- `anime`: Index-AniSora V3.2 (Bilibili, Apache-2.0): Wan 2.2 trained on anime, 8 steps. Its experts are published in
  fp32 (57 GB each); `prepare("anime")` downloads each, keeps it in bf16 under the models folder and drops the
  fp32 copy.

Like the painter (motion/painter.py), the model is loaded once by a process of its own (`python -m
vidosuper.motion.videogen serve`, on 127.0.0.1), started by the first shot and ended when idle. The painter and the
video model don't fit on one GPU together: before loading, this process asks the painter to let the GPU go."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = int(os.environ.get("VIDO_VIDEO_PORT", "8793"))
IDLE = float(os.environ.get("VIDO_VIDEO_IDLE", "1200"))
FPS = 16  # the models' own rate; the film resamples
BASE = "Wan-AI/Wan2.2-I2V-A14B-Diffusers"  # text encoder, VAE, scheduler and the experts' configs
REPACK = "Comfy-Org/Wan_2.2_ComfyUI_Repackaged"
ANISORA = "IndexTeam/Index-anisora"
# quality: 720p (about 1280x720 pixels) on an 80 GB GPU; VIDO_VIDEO_AREA to change it
AREA = int(os.environ.get("VIDO_VIDEO_AREA", str(1280 * 720)))
LOOKS = {
    "cartoon": {"steps": 40, "guidance": 3.5, "shift": None},
    "anime": {"steps": 8, "guidance": 1.0, "shift": 5.0},  # AniSora V3.2's own settings
}
NEGATIVE = ("static, frozen, still image, slideshow, sliding cut-out, morphing, the character changes, extra limbs, "
            "deformed, blurry, low quality, jpeg artifacts, flicker, text, subtitles, watermark")


class VideoError(RuntimeError):
    pass


def models_dir() -> Path:
    return Path(os.environ.get("VIDO_MODELS_DIR") or Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                / "VidoSuper" / "models")


def device() -> str | None:
    try:
        import torch
    except ImportError:
        return None
    return "cuda" if torch.cuda.is_available() else None


def available() -> bool:
    """A video model can run here: an NVIDIA GPU, PyTorch and diffusers with Wan's image-to-video pipeline."""
    if device() is None:
        return False
    try:
        from diffusers import WanImageToVideoPipeline  # noqa: F401
    except ImportError:
        return False
    return True


def _anime_file(which: str) -> Path:
    return models_dir() / "anisora" / f"anisora_v3.2_{which}_noise_bf16.safetensors"


def downloaded(look: str) -> bool:
    if look == "anime":
        return _anime_file("high").exists() and _anime_file("low").exists()
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    found = [try_to_load_from_cache(REPACK, f"split_files/diffusion_models/wan2.2_i2v_{w}_noise_14B_fp16.safetensors")
             for w in ("high", "low")]
    return all(isinstance(f, str) and Path(f).exists() for f in found)


def prepare(look: str, log=print) -> None:
    """Download a look's weights. AniSora's fp32 experts are each kept in bf16 and their fp32 copy dropped, so a
    100 GB disk is enough."""
    from huggingface_hub import hf_hub_download, snapshot_download

    snapshot_download(BASE, allow_patterns=["*.json", "*.txt", "*.model", "text_encoder/*", "tokenizer/*", "vae/*",
                                            "image_encoder/*", "image_processor/*", "scheduler/*",
                                            "transformer/config.json", "transformer_2/config.json"])
    if look == "cartoon":
        for w in ("high", "low"):
            hf_hub_download(REPACK, f"split_files/diffusion_models/wan2.2_i2v_{w}_noise_14B_fp16.safetensors")
        return
    import torch
    from safetensors.torch import load_file, save_file

    for w in ("high", "low"):
        dest = _anime_file(w)
        if dest.exists():
            continue
        log(f"AniSora {w}-noise expert: downloading (57 GB), then keeping it in bf16")
        src = Path(hf_hub_download(ANISORA, f"V3.2/{w}_noise_model/diffusion_pytorch_model.safetensors"))
        tensors = {k: v.to(torch.bfloat16) for k, v in load_file(str(src)).items()}
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        save_file(tensors, str(part))
        os.replace(part, dest)
        del tensors
        real = src.resolve()
        src.unlink(missing_ok=True)
        real.unlink(missing_ok=True)  # the fp32 blob itself


def frames_for(seconds: float) -> int:
    """Frames the model makes for `seconds` at 16 fps: 4k+1 (its rule), at least 17, at most 121."""
    n = int(round(seconds * FPS))
    return max(17, min(121, -(-(n - 1) // 4) * 4 + 1))


def size_for(aspect: float, mod: int = 16) -> tuple[int, int]:
    """(width, height) of about AREA pixels at `aspect` (width / height), multiples of the model's own step."""
    h = int(round((AREA / aspect) ** 0.5 / mod)) * mod
    w = int(round((AREA * aspect) ** 0.5 / mod)) * mod
    return w, h


# ---------------------------------------------------------------- the video process

class _Model:
    def __init__(self):
        import torch

        self.torch = torch
        if device() is None:
            raise VideoError("video made here needs an NVIDIA GPU with CUDA")
        self.look = None
        self.pipe = None
        self.lock = threading.Lock()

    def _load(self, look: str):
        if self.look == look and self.pipe is not None:
            return self.pipe
        from diffusers import UniPCMultistepScheduler, WanImageToVideoPipeline, WanTransformer3DModel
        from huggingface_hub import hf_hub_download

        torch = self.torch
        self.pipe = None
        torch.cuda.empty_cache()

        def expert(which: str, sub: str):
            if look == "anime":
                path = _anime_file(which)
                if not path.exists():
                    raise VideoError("the anime model isn't downloaded yet (the Connect page says how)")
            else:
                path = hf_hub_download(REPACK, f"split_files/diffusion_models/wan2.2_i2v_{which}_noise_14B_fp16"
                                               ".safetensors")
            return WanTransformer3DModel.from_single_file(str(path), config=BASE, subfolder=sub,
                                                          torch_dtype=torch.bfloat16)

        pipe = WanImageToVideoPipeline.from_pretrained(BASE, transformer=expert("high", "transformer"),
                                                       transformer_2=expert("low", "transformer_2"),
                                                       torch_dtype=torch.bfloat16)
        shift = LOOKS[look]["shift"]
        if shift:
            pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=shift)
        free = torch.cuda.mem_get_info()[0] / 1024 ** 3
        if free < 72:  # both experts and the text encoder don't fit whole: their parts visit the GPU in turn
            pipe.enable_model_cpu_offload()
        else:
            pipe.to("cuda")
        pipe.set_progress_bar_config(disable=True)
        self.pipe, self.look = pipe, look
        return pipe

    def animate(self, body: dict, out: Path) -> dict:
        from diffusers.utils import export_to_video
        from PIL import Image

        look = body.get("look") or "cartoon"
        if look not in LOOKS:
            raise VideoError(f"no look {look!r}: {', '.join(LOOKS)}")
        with self.lock, self.torch.inference_mode():
            pipe = self._load(look)
            first = Image.open(body["image"]).convert("RGB")
            mod = pipe.vae_scale_factor_spatial * pipe.transformer.config.patch_size[1]
            w, h = size_for(first.width / first.height, mod)
            args = dict(image=first.resize((w, h), Image.LANCZOS), prompt=str(body["prompt"]),
                        negative_prompt=str(body.get("negative") or NEGATIVE), height=h, width=w,
                        num_frames=frames_for(float(body.get("seconds") or 5.0)),
                        guidance_scale=LOOKS[look]["guidance"], num_inference_steps=LOOKS[look]["steps"],
                        generator=self.torch.Generator("cuda").manual_seed(int(body.get("seed") or 0)))
            if body.get("last_image") and Path(body["last_image"]).exists():  # the pose it must land on
                args["last_image"] = Image.open(body["last_image"]).convert("RGB").resize((w, h), Image.LANCZOS)
            frames = pipe(**args).frames[0]
        export_to_video(frames, str(out), fps=FPS)
        return {"frames": len(frames), "width": w, "height": h}


def _release_painter() -> None:
    """The painter (60 GB) and a video model (70 GB) don't share a GPU: ask the painter to end now."""
    from . import painter

    try:
        req = urllib.request.Request(f"http://127.0.0.1:{painter.PORT}/quit", data=b"{}", method="POST")
        urllib.request.urlopen(req, timeout=10)  # noqa: S310 - our own painter on this machine
        time.sleep(5)
    except (urllib.error.URLError, OSError):
        pass


def serve(port: int = PORT) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    state = {"model": None, "error": "", "last": time.time(), "busy": 0}
    try:
        state["model"] = _Model()
    except Exception as exc:  # noqa: BLE001 - said to whoever asks
        state["error"] = f"{type(exc).__name__}: {exc}"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
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
            self._send(200, {"ready": state["model"] is not None, "error": state["error"]})

        def do_POST(self):  # noqa: N802
            state["last"] = time.time()
            if self.path == "/quit":
                self._send(200, {"bye": True})
                threading.Thread(target=server.shutdown, daemon=True).start()
                return
            if state["model"] is None:
                self._send(503, {"error": state["error"] or "not ready"})
                return
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            state["busy"] += 1
            try:
                out = Path(body["out"])
                out.parent.mkdir(parents=True, exist_ok=True)
                part = out.with_name(out.stem + ".part.mp4")
                info = state["model"].animate(body, part)
                os.replace(part, out)
                self._send(200, {"out": str(out), **info})
            except Exception as exc:  # noqa: BLE001 - the caller hears it
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
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - our own process on this machine
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, **json.loads(exc.read() or b"{}")}


def _start() -> None:
    _release_painter()
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([sys.executable, "-m", "vidosuper.motion.videogen", "serve"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
                     start_new_session=os.name != "nt")


def ready(wait: float = 120.0) -> None:
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
            raise VideoError(f"the video model couldn't start: {state['error']}")
        if state is None and not started:
            _start()
            started = True
        if time.time() > deadline:
            raise VideoError("the video model didn't start in time")
        time.sleep(2)


def release() -> None:
    """Let the GPU go now (the painter needs it)."""
    try:
        _ask("/quit", {}, timeout=10)
    except (urllib.error.URLError, OSError):
        pass


def animate(image: Path, prompt: str, out: Path, *, seconds: float, look: str = "cartoon",
            last_image: Path | None = None, negative: str = "", seed: int = 0, timeout: float = 3600.0) -> dict:
    """A clip of `seconds` (at 16 fps) brought to life from `image` by `prompt`, at `out` (mp4). `last_image`: the
    frame it must end on. Returns {out, frames, width, height}. The first call loads the model (minutes)."""
    ready()
    out = Path(out)
    body = {"image": str(Path(image).resolve()), "prompt": prompt, "out": str(out.resolve()), "seconds": seconds,
            "look": look, "seed": seed, "negative": negative}
    if last_image is not None:
        body["last_image"] = str(Path(last_image).resolve())
    said = _ask("/animate", body, timeout=timeout)
    if said.get("error") or not out.exists():
        raise VideoError(f"the video model failed: {said.get('error') or 'no clip'}")
    return said


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "serve":
        serve()
    elif len(sys.argv) >= 3 and sys.argv[1] == "prepare":
        prepare(sys.argv[2])
