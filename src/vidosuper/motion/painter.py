"""The painter on this machine: an open image model (Qwen-Image-Edit-2509, Apache-2.0) on the person's own GPU, so
the designers can paint without a paid image service.

It is asked the way an image editor is (scenes.paint): add the thing to a plain white picture and keep the rest of it
as it is; the cut-out model then lifts the thing off the white. With it come the pictures to follow (the shot whose
light it is painted in, an asset whose style it matches), so a cartoon's pieces stay in one style.

The model is big (about 58 GB of weights, bfloat16): loading it takes a while, so it is loaded once by a painter
process of its own (`python -m vidosuper.motion.painter serve`, on 127.0.0.1), started by the first painting and
left running until it has been idle for IDLE seconds, when it ends and gives the GPU back. Every desk's `bash paint`
asks that process. On a GPU with less memory than the model, its parts are moved onto the GPU as they are used
(slower, same pictures)."""

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

MODEL = os.environ.get("VIDO_PAINTER_MODEL", "Qwen/Qwen-Image-Edit-2509")
PORT = int(os.environ.get("VIDO_PAINTER_PORT", "8791"))
IDLE = float(os.environ.get("VIDO_PAINTER_IDLE", "1200"))
SIDE = 1024
STEPS = 40
WHOLE_ON_GPU_GB = 64  # below this much free GPU memory, the model's parts visit the GPU in turn (cpu offload)


class PainterError(RuntimeError):
    pass


def device() -> str | None:
    """The GPU the painter runs on ("cuda" or "mps"), or None (no GPU, or no PyTorch here)."""
    try:
        import torch
    except ImportError:
        return None
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return None


def available() -> bool:
    """The local painter can run here: a GPU, PyTorch and diffusers with the Qwen edit pipeline."""
    if device() is None:
        return False
    try:
        from diffusers import QwenImageEditPlusPipeline  # noqa: F401
    except ImportError:
        return False
    return True


def downloaded() -> bool:
    """The model's files are already on this machine (else the first painting downloads them)."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    found = try_to_load_from_cache(MODEL, "model_index.json")
    return isinstance(found, str) and Path(found).exists()


# ---------------------------------------------------------------- the painter process

class _Painter:
    def __init__(self):
        import torch
        from diffusers import QwenImageEditPlusPipeline

        self.torch = torch
        self.device = device()
        if self.device is None:
            raise PainterError("the local painter needs a GPU (NVIDIA with CUDA, or Apple Silicon)")
        self.pipe = QwenImageEditPlusPipeline.from_pretrained(MODEL, torch_dtype=torch.bfloat16)
        if self.device == "cuda" and torch.cuda.mem_get_info()[0] / 1024 ** 3 < WHOLE_ON_GPU_GB:  # free now: the other
            # models (music, cut-outs) may be on the same GPU
            self.pipe.enable_model_cpu_offload()
        else:
            self.pipe.to(self.device)
        self.pipe.set_progress_bar_config(disable=True)
        self.lock = threading.Lock()

    def paint(self, prompt: str, key: tuple[int, int, int], refs: list[str], seed: int) -> "Image.Image":
        """`key`: the colour of the plain picture it paints into (image 1)."""
        from PIL import Image

        canvas = Image.new("RGB", (SIDE, SIDE), key)
        images = [canvas] + [Image.open(r).convert("RGB") for r in refs[:2] if Path(r).exists()]
        gen = self.torch.Generator(device="cpu").manual_seed(seed)
        with self.lock, self.torch.inference_mode():
            out = self.pipe(image=images, prompt=prompt, negative_prompt=" ", true_cfg_scale=4.0, guidance_scale=1.0,
                            num_inference_steps=STEPS, num_images_per_prompt=1, generator=gen,
                            height=SIDE, width=SIDE)  # else it takes the size of the last picture given
        return out.images[0]


def serve(port: int = PORT) -> None:
    """The painter process: loads the model once, paints what it is asked on 127.0.0.1, ends when idle."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    state = {"painter": None, "error": "", "last": time.time()}

    def load():
        try:
            state["painter"] = _Painter()
        except Exception as exc:  # noqa: BLE001 - said to whoever asks
            state["error"] = f"{type(exc).__name__}: {exc}"

    threading.Thread(target=load, daemon=True).start()

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
            self._send(200, {"ready": state["painter"] is not None, "error": state["error"], "model": MODEL})

        def do_POST(self):  # noqa: N802
            state["last"] = time.time()
            if state["painter"] is None:
                self._send(503, {"error": state["error"] or "still loading"})
                return
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            try:
                img = state["painter"].paint(str(body["prompt"]), tuple(body["key"]), list(body.get("refs") or []),
                                             int(body.get("seed") or 0))
                out = Path(body["out"])
                out.parent.mkdir(parents=True, exist_ok=True)
                img.save(out)
                self._send(200, {"out": str(out)})
            except Exception as exc:  # noqa: BLE001 - the desk hears it and paints again or another way
                self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
            state["last"] = time.time()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)

    def idle_watch():
        while True:
            time.sleep(30)
            if time.time() - state["last"] > IDLE:
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
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - our own painter on this machine
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, **json.loads(exc.read() or b"{}")}


def _start() -> None:
    """The painter process, started detached (it outlives the desk command that started it)."""
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([sys.executable, "-m", "vidosuper.motion.painter", "serve"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
                     start_new_session=os.name != "nt")


def ready(wait: float = 1800.0) -> None:
    """Waits for the painter process (starting it when it isn't running): the first time it may download the
    model, then it loads it."""
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
            raise PainterError(f"the local painter couldn't start: {state['error']}")
        if state is None and not started:
            _start()
            started = True
        if time.time() > deadline:
            raise PainterError("the local painter didn't get ready in time (the model may still be downloading)")
        time.sleep(3)


def paint(prompt: str, key: tuple[int, int, int], refs: list[Path], out: Path, seed: int = 0) -> Path:
    """One painting by the painter process into a plain picture of the `key` colour (as RGB), saved at `out`."""
    ready()
    said = _ask("/paint", {"prompt": prompt, "key": list(key), "refs": [str(r) for r in refs], "out": str(out),
                           "seed": seed}, timeout=900)
    if said.get("error") or not out.exists():
        raise PainterError(f"the local painter failed: {said.get('error') or 'no picture'}")
    return out


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "serve":
        serve()
