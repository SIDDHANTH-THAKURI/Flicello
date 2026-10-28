"""The drawn engine: cartoons drawn in code, frame by frame, with no AI video and no 3D.

Each character is a drawing function (cast/<id>.js) and each shot a drawing of the whole frame as a function of time
(shot.js), both written by Claude at its desks with the kit in toon.js: skies, light, depth, particles, a camera,
key poses with eases, two-bone rigs, a cast registry, motion blur. HyperFrames renders the shot frame by frame
(the canvas is redrawn at every seek), so every frame is exactly what the code says, at any size.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import scenes

TOON = Path(__file__).with_name("toon.js")
EXAMPLE = Path(__file__).with_name("toon_example.js")


class DrawnError(RuntimeError):
    pass


PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>html, body {{ margin: 0; background: #000; }} #root {{ position: relative; width: {W}px; height: {H}px; overflow: hidden; }}
#stage {{ position: absolute; inset: 0; }} canvas {{ position: absolute; inset: 0; width: 100%; height: 100%; }}</style>
<script src="gsap/gsap.min.js"></script>
</head><body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{D}" data-width="{W}" data-height="{H}">
<div id="stage" class="clip" data-start="0" data-duration="{D}"><canvas id="film" width="{W}" height="{H}"></canvas></div>
</div>
<script src="toon.js"></script>
<script>
window.__toonErrors = [];
function toonFail(where, err) {{
  window.__toonErrors.push(where + ": " + (err && err.stack || err));
  const c = document.getElementById("film").getContext("2d");
  c.setTransform(1, 0, 0, 1, 0, 0); c.fillStyle = "#300"; c.fillRect(0, 0, {W}, {H});
  c.fillStyle = "#ff6b6b"; c.font = "bold " + Math.round({H} / 34) + "px monospace";
  String(where + ": " + (err && err.message || err)).match(/.{{1,48}}/g).forEach((l, i) => c.fillText(l, {H} / 30, {H} / 12 + i * {H} / 28));
}}
window.addEventListener("error", (e) => toonFail("error", e.error || e.message));
</script>
{SCRIPTS}
<script>
const tl = gsap.timeline({{ paused: true }});
tl.to({{ v: 0 }}, {{ v: 1, duration: {D}, ease: "none" }}, 0);
window.__timelines = window.__timelines || {{}};
window.__timelines["main"] = tl;
try {{
  toon.run(document.getElementById("film"), Object.assign({CFG}, {{ fail: toonFail }}));
}} catch (err) {{ toonFail("start", err); }}
</script>
</body></html>
"""


def _node() -> str | None:
    return shutil.which("node")


def check_js(files: list[Path]) -> list[str]:
    """Each file's syntax checked by Node: the problems, said plainly (none when Node isn't here to ask)."""
    node = _node()
    if not node:
        return []
    problems = []
    for f in files:
        done = subprocess.run([node, "--check", str(f)], capture_output=True, timeout=60)
        if done.returncode != 0:
            tail = done.stderr.decode("utf-8", errors="replace").strip().splitlines()
            problems.append(f"{f.name}: " + " | ".join(tail[:5])[:500])
    return problems


def page(desk: Path, *, width: int, height: int, seconds: float, fps: int, script: Path | None = None,
         sheet: str = "", at: float = 0.0, name: str = "shot") -> Path:
    """A folder HyperFrames can render: the kit, the film's cast, and either a shot file (drawn at every seek) or a
    character's model sheet."""
    folder = desk / f".page-{name}"
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "gsap").mkdir(parents=True)
    shutil.copyfile(scenes.GSAP_DIR / "gsap.min.js", folder / "gsap" / "gsap.min.js")
    shutil.copyfile(TOON, folder / "toon.js")
    scripts = []
    cast_dir = desk / "cast"
    if cast_dir.exists():
        (folder / "cast").mkdir()
        for f in sorted(cast_dir.glob("*.js")):
            shutil.copyfile(f, folder / "cast" / f.name)
            scripts.append(f"cast/{f.name}")
    if script is not None:
        shutil.copyfile(script, folder / "shot.js")
        scripts.append("shot.js")
    cfg = {"mode": "sheet" if sheet else "shot", "id": sheet, "fps": fps, "at": at}
    tags = "\n".join(f'<script src="{s}"></script>' for s in scripts)
    (folder / "index.html").write_text(PAGE.format(W=width, H=height, D=f"{max(seconds, 0.1):.3f}", SCRIPTS=tags,
                                                   CFG=json.dumps(cfg)), encoding="utf-8")
    return folder


def render(desk: Path, script: Path, out: Path, *, width: int, height: int, fps: int, seconds: float) -> Path:
    """The shot rendered whole: an mp4 at the film's size and rate."""
    problems = check_js([script, *sorted((desk / "cast").glob("*.js"))])
    if problems:
        raise DrawnError("the code doesn't parse: " + "; ".join(problems))
    folder = page(desk, width=width, height=height, seconds=seconds, fps=fps, script=script)
    try:
        return scenes.render(folder, out, fps=fps)
    except scenes.SceneError as exc:
        raise DrawnError(str(exc)) from exc


def stills(desk: Path, script: Path | None, times: list[float], out: Path, *, width: int, height: int,
           seconds: float, fps: int = 30, sheet: str = "") -> list[Path]:
    """Frames of a shot (or a character's model sheet) at these times, as PNGs in `out`, in order."""
    files = [script] if script is not None else []
    problems = check_js([*files, *sorted((desk / "cast").glob("*.js"))])
    if problems:
        raise DrawnError("the code doesn't parse: " + "; ".join(problems))
    folder = page(desk, width=width, height=height, seconds=max(seconds, max(times) + 0.1), fps=fps, script=script,
                  sheet=sheet, name="sheet" if sheet else "still")
    try:
        got = scenes.stills(folder, times, out / ".snap")
    except scenes.SceneError as exc:
        raise DrawnError(str(exc)) from exc
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for k, t in enumerate(times):
        dest = out / f"still-{k:02d}.png"
        shutil.copyfile(got[t], dest)
        paths.append(dest)
    shutil.rmtree(out / ".snap", ignore_errors=True)
    return paths
