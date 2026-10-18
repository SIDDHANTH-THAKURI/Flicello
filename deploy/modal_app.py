"""Flicello's cloud renderer on Modal: the same remote worker as the owner's PC (src/vidosuper/studio/remote.py), in
containers the site starts when it has work the PC isn't taking (src/vidosuper/studio/cloud.py). Each one takes
steps until the queue has been empty for a minute and a half, then stops: nothing runs, or costs, while idle.

From the repo, with the venv's `modal` (logged in to the owner's workspace):

    modal secret create flicello-worker --from-dotenv <file>   # VIDO_STUDIO_WORKER_TOKEN, ANTHROPIC_API_KEY,
                                                                # GEMINI_API_KEY, ELEVENLABS_API_KEY (docs/studio.md)
    modal volume put flicello-models <modnet.onnx> /modnet.onnx # the person cut-out model, once
    modal deploy deploy/modal_app.py                            # prints the `wake` address
    modal run deploy/modal_app.py::prepare                      # downloads the speech models into the volume, once
    modal run deploy/modal_app.py::check                        # a self-test in a fresh container

and on the server: VIDO_STUDIO_MODAL_URL=<the wake address> in /srv/flicello/.env.studio.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import modal
from fastapi import HTTPException, Request

ROOT = Path(__file__).resolve().parents[1]
SERVER = "https://flicello.com"
CORES, MEMORY_MB = 8.0, 16384  # keep in step with cloud.CORES / MEMORY_GIB (the books use them)
MAX_AT_ONCE = 3  # keep in step with the server's modal_max
IDLE_EXIT = 90.0  # seconds with nothing to do before a renderer stops
SKIP = ["**/__pycache__", "**/__pycache__/**", "**/*.pyc", "**/static/dist", "**/static/dist/**"]

app = modal.App("flicello-render")
secret = modal.Secret.from_name("flicello-worker")
models = modal.Volume.from_name("flicello-models", create_if_missing=True)

image = (
    # FFmpeg 7 (Debian trixie) for -/filter_complex, vidstab and zscale; cairo and fonts for the graphics; Noto Color
    # Emoji for the explainers' emoji; the build tools for pycairo
    modal.Image.from_registry("python:3.12-slim-trixie")
    .apt_install("ffmpeg", "libcairo2", "libsndfile1", "fonts-dejavu-core", "fonts-noto-color-emoji",
                 "build-essential", "pkg-config", "libcairo2-dev")
    .pip_install_from_pyproject(str(ROOT / "pyproject.toml"), optional_dependencies=["ai", "studio"])
    .env({
        "PYTHONPATH": "/app/src", "PYTHONUNBUFFERED": "1",
        "VIDO_MODELS_DIR": "/models", "HF_HOME": "/models/hf", "VIDO_LOTTIE_DIR": "/models/lottie",
        "VIDO_REMOTE_HOME": "/tmp/flicello", "NUMBA_CACHE_DIR": "/tmp/numba", "MPLCONFIGDIR": "/tmp/mpl",
        "VIDO_STUDIO_SERVER": SERVER,
        # 16 vCPUs: four drawing processes for designed talks (each cut-out on 3 threads), four FFmpeg jobs at once
        "VIDO_STUDIO_RENDER_PROCESSES": "4", "VIDO_MATTE_THREADS": "3", "VIDO_STUDIO_PIPELINE_WORKERS": "4",
        "VIDO_CPUS": "16",  # threads sized to the 8 cores (16 vCPUs) asked for: bursting past them is billed too
    })
    .add_local_dir(ROOT / "src", "/app/src", ignore=SKIP)
    .add_local_dir(ROOT / "config", "/app/config", ignore=SKIP)
    .add_local_dir(ROOT / "assets", "/app/assets", ignore=SKIP)
)
web = modal.Image.debian_slim(python_version="3.12").pip_install("fastapi[standard]")


def _logging() -> None:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@app.function(image=image, cpu=CORES, memory=MEMORY_MB, timeout=4 * 3600, secrets=[secret], volumes={"/models": models},
              max_containers=MAX_AT_ONCE, scaledown_window=10)
def work(why: str = "") -> int:
    """Take the site's steps until there has been nothing to do for IDLE_EXIT seconds; returns how many it ran."""
    import uuid

    _logging()
    from vidosuper.studio.remote import RemoteWorker

    name = "modal-" + uuid.uuid4().hex[:6]
    print(f"{name} starting ({why or 'asked by the site'})", flush=True)
    worker = RemoteWorker(server=os.environ.get("VIDO_STUDIO_SERVER", SERVER), name=name, lane="all", cloud=True)
    try:
        return worker.run(idle_exit=IDLE_EXIT)
    finally:
        try:
            models.commit()  # keep anything it fetched (an animation, a model) for the next renderer
        except Exception:  # noqa: BLE001 - only a cache
            pass


@app.function(image=image, cpu=4.0, memory=8192, timeout=3600, secrets=[secret], volumes={"/models": models})
def prepare() -> dict:
    """Fetch the speech models into the models volume (once, and after the models change)."""
    _logging()
    from vidosuper.config import load_config
    from vidosuper.perceive.transcribe import _model

    cfg = load_config({})
    took = {}
    for name in dict.fromkeys([cfg.analysis.whisper_model, cfg.analysis.speech_model]):
        t = time.time()
        _model(name, Path("/models/whisper"))
        took[name] = round(time.time() - t, 1)
    models.commit()
    return {"models": took, "modnet": Path("/models/modnet.onnx").exists()}


@app.function(image=image, cpu=CORES, memory=MEMORY_MB, timeout=1800, secrets=[secret], volumes={"/models": models})
def check(processes: int = 0, matte_threads: int = 0, cv_threads: int = 0) -> dict:
    """The renderer's self-test (src/vidosuper/studio/selftest.py) in a fresh container, without the site. The
    numbers (0: as deployed) try other settings for drawing processes and threads."""
    for key, value in (("VIDO_STUDIO_RENDER_PROCESSES", processes), ("VIDO_MATTE_THREADS", matte_threads),
                       ("VIDO_CV_THREADS", cv_threads)):
        if value:
            os.environ[key] = str(value)
    _logging()
    from vidosuper.config import load_config
    from vidosuper.studio import selftest

    result = selftest.run(lambda f, m: print(f"{f:4.0%} {m}", flush=True), load_config({}).analysis.speech_model,
                          Path("/models"), int(os.environ["VIDO_STUDIO_RENDER_PROCESSES"]))
    result["summary"] = selftest.summary(result)
    print("RESULT", result, flush=True)
    return result


@app.function(image=web, secrets=[secret])
@modal.fastapi_endpoint(method="POST")
def wake(request: Request, body: dict) -> dict:
    """The site asks for renderers (`n`) when steps are waiting. Only the site may: it holds the worker token."""
    import hashlib
    import hmac

    token = os.environ.get("VIDO_STUDIO_WORKER_TOKEN", "")
    given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if len(token) < 32 or not hmac.compare_digest(hashlib.sha256(given.encode()).digest(),
                                                  hashlib.sha256(token.encode()).digest()):
        raise HTTPException(404, "Not found.")
    n = max(0, min(int(body.get("n") or 1), MAX_AT_ONCE))
    why = str(body.get("why") or "")[:80]
    for _ in range(n):
        work.spawn(why)
    return {"started": n}
