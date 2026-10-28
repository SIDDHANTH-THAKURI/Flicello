"""Everything Flicello uses on this machine, each ready / missing / failing, with what to do about it: the Connect
page shows it, `vido connect --json` prints it (for the setup prompt Claude Code runs), and Cello reads it.

Tools and models are looked at on disk (fast, no network). Keys are tested against their services only when asked
(a button, or `--test`), since a test may use a little of the person's quota."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..config import REPO_ROOT
from . import health, keys
from .machine import machine

OK, MISSING, FAILING, OFF = "ok", "missing", "failing", "not set"


def _run(cmd: list[str], timeout: float = 20.0) -> tuple[int, str]:
    from . import plan_env

    try:  # never with the API key in the environment: Claude Code would report (and bill) that instead of the plan
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=plan_env())
        return out.returncode, (out.stdout or out.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)


def _item(cid: str, group: str, label: str, status: str, detail: str, fix: str = "", needed: bool = True,
          does: str = "") -> dict:
    return {"id": cid, "group": group, "label": label, "status": status, "detail": detail, "fix": fix,
            "needed": needed, "does": does}


# ---------------------------------------------------------------- the brain

def claude_code() -> list[dict]:
    exe = shutil.which("claude")
    if not exe:
        return [_item("claude", "brain", "Claude Code", MISSING, "not installed",
                      "install it: https://docs.anthropic.com/claude-code (npm install -g @anthropic-ai/claude-code), "
                      "then run `claude` once and log in with your Claude plan",
                      does="the brain: designs scenes, plans edits, writes cartoons, thinks for Cello")]
    code, version = _run([exe, "--version"])
    code, raw = _run([exe, "auth", "status", "--json"])
    try:
        status = json.loads(raw)
    except ValueError:
        status = {}
    if not status.get("loggedIn"):
        return [_item("claude", "brain", "Claude Code", FAILING, f"{version.split()[0] if version else ''} installed, "
                      "not logged in", "run `claude` in a terminal and log in with your Claude plan",
                      does="the brain")]
    plan = status.get("subscriptionType") or status.get("authMethod") or "logged in"
    note = "" if str(plan).lower().startswith("max") else (
        "works; heavy design work uses the plan's limits quickly (Max is recommended)")
    bad = [p for p in health.problems() if p["service"] == "claude_code"]
    if bad:
        return [_item("claude", "brain", "Claude Code", FAILING, f"{plan}: {bad[0]['means']}", bad[0]["todo"],
                      does="the brain")]
    return [_item("claude", "brain", "Claude Code", OK, f"{version.split()[0] if version else ''}, {plan} plan"
                  + (f": {note}" if note else ""), does="the brain: designs, plans, writes, thinks for Cello")]


# ---------------------------------------------------------------- tools

def tools() -> list[dict]:
    out = []
    out.append(_item("python", "tools", "Python", OK if sys.version_info >= (3, 11) else MISSING,
                     sys.version.split()[0], "install Python 3.11 or newer"))
    try:
        from ..ffmpeg import capabilities, ffmpeg_bin

        caps = capabilities()
        out.append(_item("ffmpeg", "tools", "FFmpeg", OK if caps["major"] >= 7 else FAILING,
                         f"{caps['version']} ({ffmpeg_bin()})", "install FFmpeg 7 or newer (a full build)"))
    except Exception as exc:  # noqa: BLE001 - missing or broken
        out.append(_item("ffmpeg", "tools", "FFmpeg", MISSING, str(exc)[:160],
                         "install FFmpeg 7 or newer (a full build) and put it on PATH"))
    node = shutil.which("node")
    if node:
        _, v = _run([node, "--version"])
        major = int(re.sub(r"[^0-9].*", "", v.lstrip("v")) or 0)
        out.append(_item("node", "tools", "Node.js", OK if major >= 22 else FAILING, v,
                         "install Node.js 22 or newer"))
    else:
        out.append(_item("node", "tools", "Node.js", MISSING, "not installed", "install Node.js 22 or newer"))
    dist = REPO_ROOT / "src" / "vidosuper" / "studio" / "web" / "static" / "dist"
    out.append(_item("frontend", "tools", "The app's pages", OK if any(dist.glob("**/manifest.json")) else MISSING,
                     str(dist), "build them: cd frontend && npm ci && npm run build"))
    engine = REPO_ROOT / "motion" / "node_modules" / ".bin" / ("hyperframes.cmd" if os.name == "nt" else "hyperframes")
    out.append(_item("hyperframes", "tools", "HyperFrames (the motion engine)", OK if engine.exists() else MISSING,
                     str(engine), "cd motion && npm ci && npx hyperframes browser ensure",
                     does="renders designed scenes frame by frame"))
    gsap = REPO_ROOT / "frontend" / "node_modules" / "gsap" / "dist" / "gsap.min.js"
    out.append(_item("gsap", "tools", "GSAP", OK if gsap.exists() else MISSING, str(gsap),
                     "cd frontend && npm ci", does="the animation library designed scenes are written with"))
    blender = os.environ.get("BLENDER_PATH") or shutil.which("blender") or _find_blender()
    out.append(_item("blender", "tools", "Blender (optional)", OK if blender else OFF,
                     blender or "not found", "install Blender 4.2+ from blender.org, then set its path on this page",
                     needed=False, does="3D scenes"))
    return out


def _find_blender() -> str:
    places = []
    if os.name == "nt":
        root = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation"
        places = sorted(root.glob("Blender*/blender.exe"), reverse=True)
    elif sys.platform == "darwin":
        places = [Path("/Applications/Blender.app/Contents/MacOS/Blender")]
    return next((str(p) for p in places if p.exists()), "")


# ---------------------------------------------------------------- models

MODELS = (  # file under the models folder, what it does, where it comes from (a hint for the setup prompt)
    ("u2net_human_seg.onnx", "finding people in a frame", "github.com/danielgatis/rembg releases (u2net_human_seg.onnx)"),
    ("birefnet_lite.onnx", "the clean cut-out of a person (hair included)",
     "huggingface.co/onnx-community/BiRefNet_lite-ONNX (onnx/model.onnx)"),
    ("modnet.onnx", "portrait cut-out for talking videos", "MODNet ONNX (huggingface.co/Xenova/modnet, onnx/model.onnx)"),
    ("depth/depth_anything_v2_small.onnx", "depth (3D parallax photos)",
     "huggingface.co/onnx-community/depth-anything-v2-small (onnx/model.onnx)"),
    ("inpaint/lama.onnx", "removing an object from a shot", "huggingface.co/Carve/LaMa-ONNX (lama_fp32.onnx)"),
    ("upscale/real_esrgan_x4plus-onnx-float/real_esrgan_x4plus.onnx", "upscaling",
     "Qualcomm AI Hub / huggingface.co/qualcomm/Real-ESRGAN-x4plus (ONNX, float)"),
    ("voice/kokoro-v1.0.onnx", "Cello's voice on this machine (Kokoro-82M: no voice service needed)",
     "github.com/thewh1teagle/kokoro-onnx releases, model-files-v1.0 (kokoro-v1.0.onnx)"),
    ("voice/voices-v1.0.bin", "the voices for Cello's voice",
     "github.com/thewh1teagle/kokoro-onnx releases, model-files-v1.0 (voices-v1.0.bin)"),
)


def models(models_dir: Path) -> list[dict]:
    out = []
    for rel, does, source in MODELS:
        path = models_dir / rel
        out.append(_item(f"model:{rel}", "models", Path(rel).name, OK if path.exists() and path.stat().st_size > 1000
                         else MISSING, str(path), f"download it into {path} (from {source})", does=does))
    whisper = any(models_dir.glob("models--Systran--faster-whisper-*")) or any((models_dir / "whisper").glob("*")) \
        if models_dir.exists() else False
    out.append(_item("model:whisper", "models", "Whisper (speech to text)", OK if whisper else MISSING,
                     str(models_dir), "it downloads itself on the first video with speech", does="what is said, word by word"))
    out.append(painter_item())
    out += sound_items()
    out += video_items()
    return out


def painter_item() -> dict:
    """The painter on this machine (motion/painter.py): optional, as Gemini can paint instead."""
    from ..motion import painter

    gpu = painter.device()
    if not painter.available():
        status, detail = OFF, ("no GPU for it here" if gpu is None and _has_torch() else
                               "not installed (pip install -e \".[paint]\")")
    elif painter.downloaded():
        status, detail = OK, f"{painter.MODEL} on {gpu}"
    else:
        status, detail = MISSING, f"{painter.MODEL}: not downloaded yet (about 58 GB)"
    return _item("model:painter", "models", "Painter (Qwen-Image-Edit, on your GPU)", status, detail,
                 "pip install -e \".[paint]\" (PyTorch with CUDA on NVIDIA), then download it with: huggingface-cli "
                 f"download {painter.MODEL}", needed=False,
                 does="paints the designers' pieces on your GPU, so no image service is needed")


def sound_items() -> list[dict]:
    """Music and sound effects made on this machine (music/maker.py): optional, as ElevenLabs can make them instead."""
    from ..music import maker

    gpu = maker.device()
    out = []
    for kind, label, model, able, size, does in (
            ("music", "Music maker (ACE-Step 1.5 XL, on your GPU)", maker.MUSIC_MODEL, maker.can_make_music(),
             "about 12 GB", "makes your film's music, so no music service is needed"),
            ("sounds", "Sound effects (Stable Audio 3, on your GPU)", maker.SOUND_REPO.get(maker.SOUND_MODEL, ""),
             maker.can_make_sounds(), "about 4 GB", "makes each sound effect to its description, so no sound service "
             "is needed. Powered by Stability AI (Community License: for commercial use, register at "
             "stability.ai/community-license)")):
        if not able:
            status, detail = OFF, ("no GPU for it here" if gpu is None and _has_torch() else
                                   "not installed (pip install -e \".[sound]\")")
        elif maker.downloaded(kind if kind == "music" else "sound"):
            status, detail = OK, f"{model} on {gpu}"
        else:
            status, detail = MISSING, f"{model}: not downloaded yet ({size})"
        fix = (f"pip install -e \".[sound]\" (PyTorch with CUDA on NVIDIA), then download it with: huggingface-cli "
               f"download {model}")
        if kind == "sounds":
            fix = ("pip install -e \".[sound]\" and pip install --no-deps \"" + STABLE_AUDIO_3 + "\"; the model is "
                   f"gated: accept its licence at huggingface.co/{model} and log in (huggingface-cli login), then "
                   f"huggingface-cli download {model}")
        out.append(_item(f"model:{kind}", "models", label, status, detail, fix, needed=False, does=does))
    return out


def video_items() -> list[dict]:
    """The video models that animate cartoons (motion/videogen.py): optional, as Blender rigs can animate instead."""
    from ..motion import videogen

    able = videogen.available()
    out = []
    for look, label, size, src in (
            ("cartoon", "Cartoon animation (Wan 2.2, on your GPU)", "about 70 GB", "Wan 2.2 (Apache-2.0)"),
            ("anime", "Anime animation (AniSora V3.2, on your GPU)", "about 70 GB", "AniSora by Bilibili (Apache-2.0)")):
        if not able:
            status, detail = OFF, ("needs an NVIDIA GPU with 80 GB" if videogen.device() is None and _has_torch()
                                   else "not installed (pip install -e \".[video]\")")
        elif videogen.downloaded(look):
            status, detail = OK, f"{src} ready"
        else:
            status, detail = MISSING, f"{src}: not downloaded yet ({size})"
        out.append(_item(f"model:video-{look}", "models", label, status, detail,
                         f"pip install -e \".[video]\" (PyTorch with CUDA), then: python -m vidosuper.motion.videogen "
                         f"prepare {look}", needed=False,
                         does="brings each painted shot to life (real motion), for cartoons" if look == "cartoon"
                         else "brings each painted shot to life in anime style"))
    return out


# Stable Audio 3 is installed on its own (pinned, without its dependencies: its own pins would replace this machine's
# PyTorch build); the `sound` extra has what it needs.
STABLE_AUDIO_3 = "stable-audio-3 @ git+https://github.com/Stability-AI/stable-audio-3@77e0398c26f7d22d801ec1bb036307e8b231f741"


def _has_torch() -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------- keys and services

def key_items(home: Path) -> list[dict]:
    out = []
    failing = {p["service"]: p for p in health.problems()}
    for k in keys.KEYS:
        value = os.environ.get(k.name) or keys.get(k.name, home)
        service = {"GEMINI_API_KEY": "gemini", "ELEVENLABS_API_KEY": "elevenlabs", "ANTHROPIC_API_KEY": "anthropic",
                   "HF_API_KEY_ID": "higgsfield", "HF_API_KEY_SECRET": "higgsfield"}[k.name]
        if not value:
            status, detail = (MISSING if k.needed else OFF), "not set"
        elif service in failing:
            status, detail = FAILING, f"{keys.masked(value)}: {failing[service]['means']}"
        else:
            status, detail = OK, keys.masked(value)
        out.append(_item(f"key:{k.name}", "keys", k.label, status, detail,
                         failing[service]["todo"] if service in failing else f"make one at {k.get}",
                         needed=k.needed, does=k.does) | {"key": k.name, "get": k.get})
    return out


def test(service: str, timeout: float = 20.0) -> dict:
    """Ask a service if this machine's key works (a light call, no generation). {ok, detail}."""
    import httpx

    try:
        if service == "gemini":
            key = os.environ.get("GEMINI_API_KEY", "")
            if not key:
                return {"ok": False, "detail": "no key"}
            r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": key, "pageSize": 1},
                          timeout=timeout)
            if r.status_code == 200:
                health.worked("gemini")
                return {"ok": True, "detail": "key works (voice and painting also need prepaid credit)"}
            health.failed("gemini", f"{r.status_code} {r.text[:200]}")
            return {"ok": False, "detail": health.explain(f"{r.status_code} {r.text}")[0]}
        if service == "elevenlabs":
            key = os.environ.get("ELEVENLABS_API_KEY", "")
            if not key:
                return {"ok": False, "detail": "no key"}
            r = httpx.get("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": key}, timeout=timeout)
            if r.status_code == 200:
                d = r.json()
                used, limit = d.get("character_count", 0), d.get("character_limit", 0)
                left = max(0, limit - used)
                health.worked("elevenlabs")
                return {"ok": True, "detail": f"{d.get('tier', 'plan')}: {left:,} of {limit:,} credits left"
                        + (" (low)" if limit and left < 0.05 * limit else "")}
            health.failed("elevenlabs", f"{r.status_code} {r.text[:200]}")
            return {"ok": False, "detail": health.explain(f"{r.status_code} {r.text}")[0]}
        if service == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                return {"ok": False, "detail": "no key (optional: Claude Code is used instead)"}
            r = httpx.get("https://api.anthropic.com/v1/models", headers={"x-api-key": key,
                                                                         "anthropic-version": "2023-06-01"},
                          timeout=timeout)
            if r.status_code == 200:
                health.worked("anthropic")
                return {"ok": True, "detail": "key works (credit is checked when it is used)"}
            health.failed("anthropic", f"{r.status_code} {r.text[:200]}")
            return {"ok": False, "detail": health.explain(f"{r.status_code} {r.text}")[0]}
        if service == "claude_code":
            item = claude_code()[0]
            return {"ok": item["status"] == OK, "detail": item["detail"]}
    except Exception as exc:  # noqa: BLE001 - said plainly
        return {"ok": False, "detail": f"couldn't reach it: {type(exc).__name__}"}
    return {"ok": False, "detail": "nothing to test"}


# ---------------------------------------------------------------- everything

def report(home: Path, models_dir: Path) -> dict:
    items = claude_code() + key_items(home) + tools() + models(models_dir)
    missing = [i for i in items if i["needed"] and i["status"] in (MISSING, FAILING)]
    return {"at": time.time(), "machine": machine(home), "items": items, "ready": not missing,
            "missing": [i["label"] for i in missing], "problems": health.problems(), "repo": str(REPO_ROOT),
            "home": str(home), "models_dir": str(models_dir)}
