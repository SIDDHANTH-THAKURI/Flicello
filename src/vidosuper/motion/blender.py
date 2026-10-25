"""Blender, driven headless by a designer's script: 3D made for a shot and laid into its page as a clip with
transparency.

The designer writes a Blender Python script that builds the scene (objects, materials, lights, the camera and its
keyframes) on the empty scene it is given. `render` runs it in Blender in the background (`blender -b
--factory-startup -P`), renders the frames with a transparent background (Cycles on the GPU when there is one, else
Eevee), and turns them into a VP9 WebM with alpha, which the page plays as a timed `<video>`.

Blender is optional: it is found at BLENDER_PATH, on the PATH, or where its installer puts it (local/checks.py)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

RUNNER = '''
import json, sys, traceback
import bpy

args = json.loads(sys.argv[sys.argv.index("--") + 1])
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y = args["width"], args["height"]
scene.render.resolution_percentage = 100
scene.render.fps = args["fps"]
scene.frame_start, scene.frame_end = 1, args["frames"]
scene.render.film_transparent = True
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.render.image_settings.color_depth = "8"
scene.render.filepath = args["out"] + "/f"
scene.view_settings.view_transform = "AgX" if "AgX" in [i.identifier for i in
    scene.view_settings.bl_rna.properties["view_transform"].enum_items] else "Filmic"

def gpu_cycles():
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for kind in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
        try:
            prefs.compute_device_type = kind
            prefs.refresh_devices()
        except TypeError:
            continue
        devices = [d for d in prefs.devices if d.type == kind]
        if devices:
            for d in devices:
                d.use = True
            return kind
    return None

engine = args.get("engine") or "auto"
if engine in ("auto", "cycles"):
    found = gpu_cycles()
    if found or engine == "cycles":
        scene.render.engine = "CYCLES"
        scene.cycles.device = "GPU" if found else "CPU"
        scene.cycles.samples = args.get("samples", 128)
        scene.cycles.use_denoising = True
    else:
        engine = "eevee"
if engine == "eevee":
    names = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in names else "BLENDER_EEVEE"

namespace = {"bpy": bpy, "scene": scene, "W": args["width"], "H": args["height"], "FPS": args["fps"],
             "FRAMES": args["frames"], "SECONDS": args["frames"] / args["fps"], "ASSETS": args["assets"]}
try:
    exec(compile(open(args["script"], encoding="utf-8").read(), args["script"], "exec"), namespace)
except Exception:
    traceback.print_exc()
    sys.exit(3)
if scene.camera is None:
    print("THE SCRIPT MADE NO CAMERA: add one and set scene.camera")
    sys.exit(4)
bpy.ops.render.render(animation=True)
'''


class BlenderError(RuntimeError):
    pass


def find() -> str | None:
    from ..local.checks import _find_blender

    return os.environ.get("BLENDER_PATH") or shutil.which("blender") or _find_blender() or None


def render(script: Path, out: Path, *, width: int, height: int, fps: int, seconds: float, assets: Path,
           engine: str = "auto", samples: int = 128, timeout: float = 3600.0) -> Path:
    """The script's scene rendered for `seconds` at this size and rate, as a WebM with alpha at `out`."""
    exe = find()
    if not exe:
        raise BlenderError("Blender isn't set up on this computer: install it and set its path on the Connect page.")
    frames_dir = out.parent / f".{out.stem}-frames"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True)
    runner = frames_dir / "runner.py"
    runner.write_text(RUNNER, encoding="utf-8")
    n = max(1, int(round(seconds * fps)))
    args = {"width": width, "height": height, "fps": fps, "frames": n, "out": frames_dir.as_posix(),
            "script": Path(script).resolve().as_posix(), "assets": Path(assets).resolve().as_posix(),
            "engine": engine, "samples": samples}
    proc = subprocess.run([exe, "-b", "--factory-startup", "--python-exit-code", "2", "-P", str(runner), "--",
                           json.dumps(args)], capture_output=True, timeout=timeout)
    pngs = sorted(frames_dir.glob("f*.png"))
    if proc.returncode != 0 or len(pngs) < n:
        tail = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")[-1500:]
        raise BlenderError(f"Blender couldn't render the script (exit {proc.returncode}, {len(pngs)} of {n} "
                           f"frames):\n{tail}")
    from ..ffmpeg import ffmpeg_bin

    out.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-framerate", str(fps), "-start_number", "1",
                           "-i", str(frames_dir / "f%04d.png"), "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
                           "-b:v", "0", "-crf", "14", "-row-mt", "1", "-deadline", "good", "-cpu-used", "2",
                           "-auto-alt-ref", "0", str(out)], capture_output=True)
    if done.returncode != 0 or not out.exists():
        raise BlenderError("the frames couldn't be made into a clip: " + done.stderr.decode(errors="replace")[-600:])
    still = out.with_suffix(".png")  # its middle frame, to Read
    shutil.copyfile(pngs[len(pngs) // 2], still)
    shutil.rmtree(frames_dir, ignore_errors=True)
    return out
