"""Blender, driven headless by Claude's scripts: characters built as real rigs (bones, meshes that bend, faces that
change) and whole shots built, posed, lit and filmed in 3D, the way animated films are made.

- `rig`: a character script builds one character (its mesh, its armature and weights, its face controls, its toon
  materials) on an empty scene; it is saved as `cast/<id>.blend` and filmed from the front, the side and three
  quarters so it can be checked.
- `render`: a shot script builds the set, brings the cast in (`cast("dog")` appends a saved rig), poses and keys
  them, places the lights and the camera, and the whole shot is rendered, opaque, as an mp4. A transparent WebM of a
  single element is still possible (`transparent=True`).

Everything runs in the background (`blender -b --factory-startup -P`), Cycles on the GPU when there is one, else
Eevee. Blender is optional: found at BLENDER_PATH, on the PATH, or where its installer puts it (local/checks.py)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

RUNNER = '''
import json, math, sys, traceback
import bpy
from mathutils import Vector

args = json.loads(sys.argv[sys.argv.index("--") + 1])
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y = args["width"], args["height"]
scene.render.resolution_percentage = 100
scene.render.fps = args["fps"]
scene.frame_start, scene.frame_end = 1, args["frames"]
scene.render.film_transparent = bool(args.get("transparent"))
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA" if args.get("transparent") else "RGB"
scene.render.image_settings.color_depth = "8"
scene.render.filepath = args["out"] + "/f"
scene.view_settings.view_transform = "Standard" if args.get("toon") else ("AgX" if "AgX" in [i.identifier for i in
    scene.view_settings.bl_rna.properties["view_transform"].enum_items] else "Filmic")

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

def cast(name):
    """A saved character rig appended into this scene: its collection, linked to the scene. Returns the collection;
    its armature is the object of type ARMATURE in it."""
    path = args["cast"] + "/" + name + ".blend"
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        dst.collections = [c for c in src.collections if c == name] or list(src.collections)[:1]
    col = dst.collections[0]
    scene.collection.children.link(col)
    return col

def armature(col):
    return next(o for o in col.all_objects if o.type == "ARMATURE")

namespace = {"bpy": bpy, "scene": scene, "W": args["width"], "H": args["height"], "FPS": args["fps"],
             "FRAMES": args["frames"], "SECONDS": args["frames"] / args["fps"], "ASSETS": args["assets"],
             "CAST": args.get("cast", ""), "cast": cast, "armature": armature, "Vector": Vector, "math": math}
try:
    exec(compile(open(args["script"], encoding="utf-8").read(), args["script"], "exec"), namespace)
except Exception:
    traceback.print_exc()
    sys.exit(3)

if args.get("mode") == "rig":
    # the character saved as its own file, its collection named after it, then filmed from three sides
    name = args["name"]
    objs = [o for o in scene.objects]
    if not any(o.type == "ARMATURE" for o in objs):
        print("THE CHARACTER HAS NO ARMATURE: build its bones, parent the mesh to them with weights")
        sys.exit(5)
    col = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if col.name not in [c.name for c in scene.collection.children]:
        scene.collection.children.link(col)
    for o in objs:
        if o.name not in col.objects and o.type not in ("CAMERA", "LIGHT"):
            col.objects.link(o)
            for c in list(o.users_collection):
                if c != col:
                    c.objects.unlink(o)
    bpy.ops.wm.save_as_mainfile(filepath=args["blend"])
    pts = [o.matrix_world @ Vector(c) for o in col.all_objects if o.type == "MESH" for c in o.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    mid, size = (lo + hi) / 2, max((hi - lo).length, 0.1)
    if not any(o.type == "LIGHT" for o in scene.objects):
        sun = bpy.data.objects.new("key", bpy.data.lights.new("key", "SUN"))
        sun.rotation_euler = (math.radians(50), 0, math.radians(30))
        scene.collection.objects.link(sun)
    cam = bpy.data.objects.new("turn", bpy.data.cameras.new("turn"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    scene.frame_start = scene.frame_end = 1
    for k, angle in enumerate((0, 45, 90)):
        a = math.radians(angle)
        cam.location = mid + Vector((math.sin(a) * size * 1.8, -math.cos(a) * size * 1.8, size * 0.25))
        cam.rotation_euler = (mid - cam.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = args["out"] + f"/turn{k}"
        bpy.ops.render.render(write_still=True)
    sys.exit(0)

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


def _run(args: dict, timeout: float) -> subprocess.CompletedProcess:
    exe = find()
    if not exe:
        raise BlenderError("Blender isn't set up on this computer: install it and set its path on the Connect page.")
    frames_dir = Path(args["out"])
    runner = frames_dir / "runner.py"
    runner.write_text(RUNNER, encoding="utf-8")
    return subprocess.run([exe, "-b", "--factory-startup", "--python-exit-code", "2", "-P", str(runner), "--",
                           json.dumps(args)], capture_output=True, timeout=timeout)


def render(script: Path, out: Path, *, width: int, height: int, fps: int, seconds: float, assets: Path,
           cast: Path | None = None, engine: str = "auto", samples: int = 128, transparent: bool | None = None,
           toon: bool = False, timeout: float = 3600.0) -> Path:
    """The script's scene rendered for `seconds` at this size and rate: a whole shot as an mp4 (opaque), or a
    single element as a WebM with transparency (`out` ending .webm, or `transparent=True`)."""
    transparent = out.suffix.lower() == ".webm" if transparent is None else transparent
    frames_dir = out.parent / f".{out.stem}-frames"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True)
    n = max(1, int(round(seconds * fps)))
    proc = _run({"width": width, "height": height, "fps": fps, "frames": n, "out": frames_dir.as_posix(),
                 "script": Path(script).resolve().as_posix(), "assets": Path(assets).resolve().as_posix(),
                 "cast": Path(cast).resolve().as_posix() if cast else "", "engine": engine, "samples": samples,
                 "transparent": transparent, "toon": toon}, timeout)
    pngs = sorted(frames_dir.glob("f*.png"))
    if proc.returncode != 0 or len(pngs) < n:
        tail = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")[-1500:]
        raise BlenderError(f"Blender couldn't render the script (exit {proc.returncode}, {len(pngs)} of {n} "
                           f"frames):\n{tail}")
    from ..ffmpeg import ffmpeg_bin

    out.parent.mkdir(parents=True, exist_ok=True)
    codec = (["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "14", "-row-mt", "1", "-deadline",
              "good", "-cpu-used", "2", "-auto-alt-ref", "0"] if transparent else
             ["-c:v", "libx264", "-preset", "slow", "-crf", "14", "-pix_fmt", "yuv420p", "-movflags", "+faststart"])
    done = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-framerate", str(fps), "-start_number", "1",
                           "-i", str(frames_dir / "f%04d.png"), *codec, str(out)], capture_output=True)
    if done.returncode != 0 or not out.exists():
        raise BlenderError("the frames couldn't be made into a clip: " + done.stderr.decode(errors="replace")[-600:])
    still = out.with_suffix(".png")  # its middle frame, to Read
    shutil.copyfile(pngs[len(pngs) // 2], still)
    shutil.rmtree(frames_dir, ignore_errors=True)
    return out


def rig(script: Path, name: str, cast: Path, *, assets: Path, engine: str = "auto",
        timeout: float = 1800.0) -> list[Path]:
    """A character built by its script, saved as cast/<name>.blend, and filmed from the front, three quarters and
    the side. Returns the three stills."""
    cast.mkdir(parents=True, exist_ok=True)
    frames_dir = cast / f".{name}-turn"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True)
    proc = _run({"width": 768, "height": 1024, "fps": 24, "frames": 1, "out": frames_dir.as_posix(),
                 "script": Path(script).resolve().as_posix(), "assets": Path(assets).resolve().as_posix(),
                 "cast": cast.resolve().as_posix(), "engine": engine, "samples": 64, "mode": "rig", "name": name,
                 "blend": (cast / f"{name}.blend").resolve().as_posix(), "transparent": False}, timeout)
    stills = sorted(frames_dir.glob("turn*.png"))
    if proc.returncode != 0 or len(stills) < 3 or not (cast / f"{name}.blend").exists():
        tail = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")[-1500:]
        raise BlenderError(f"the character couldn't be built (exit {proc.returncode}):\n{tail}")
    kept = []
    for k, s in enumerate(stills):
        dest = cast / f"{name}-turn{k}.png"
        shutil.copyfile(s, dest)
        kept.append(dest)
    shutil.rmtree(frames_dir, ignore_errors=True)
    return kept
