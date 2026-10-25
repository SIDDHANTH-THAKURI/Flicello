"""Motion graphics made the way a motion designer makes them: titles, word-by-word captions and lower thirds, timed
to the measured professional moves (blocks/motion.js), placed by the film (the safe zones, the faces, the speech).

Each block is a script (blocks/<name>.js) and a stylesheet (blocks/<name>.css) that build it on a stage of the
film's size with Web Animations only:
- **The render:** HyperFrames (HeyGen, Apache-2.0, installed in the repo's motion/ folder) steps a page holding the
  block frame by frame in headless Chrome and writes a video with transparency (ProRes 4444). The film's renderer
  lays it over the picture.
- **The instant preview:** the player mounts the same block over the preview and seeks it from its own clock.

So what they see in a second is what the render makes. Renders are cached by everything that shapes them, and the
engine's telemetry is always off.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from ..config import REPO_ROOT

HERE = Path(__file__).parent / "blocks"
BLOCKS = ("title", "captions", "lowerthird", "transition", "kinetic", "callout", "route", "accent", "logo", "thing")
RUNTIME = ("motion.js", "boot.js")
ENGINE = REPO_ROOT / "motion"
ENGINE_VERSION = "0.8.185"
CACHE_KEEP = 60  # rendered graphics kept per film folder (the oldest go first)


class MotionError(RuntimeError):
    pass


def engine() -> list[str]:
    """The HyperFrames command: VIDO_HYPERFRAMES, else the copy `npm ci` put in motion/node_modules."""
    if os.environ.get("VIDO_HYPERFRAMES"):
        return [os.environ["VIDO_HYPERFRAMES"]]
    exe = ENGINE / "node_modules" / ".bin" / ("hyperframes.cmd" if os.name == "nt" else "hyperframes")
    if exe.exists():
        return [str(exe)]
    raise MotionError("The motion engine isn't installed: run `npm ci` in the motion folder.")


def available() -> bool:
    try:
        engine()
    except MotionError:
        return False
    return True


def _code() -> str:
    """Everything a block is made of, for the cache key: a change to any block's code makes new renders."""
    h = hashlib.sha256(ENGINE_VERSION.encode())
    for f in sorted(HERE.rglob("*")):
        if f.is_file():
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


def fonts_css(base: str | None) -> str:
    """The font faces. `base`: where the browser loads the font files from; None: the fonts themselves inside (data
    URIs), for a render, so no font is ever looked up (HyperFrames swaps faces it can't resolve for its own)."""
    css = (HERE / "fonts.css").read_text(encoding="utf-8")
    if base is not None:
        return css.replace("__BASE__", base)

    def data(m) -> str:
        raw = (HERE / "fonts" / m.group(1)).read_bytes()
        return f"url('data:font/woff2;base64,{base64.b64encode(raw).decode('ascii')}')"

    return re.sub(r"url\('__BASE__fonts/([^']+)'\)", data, css)


def page(block: str, variables: dict, width: int, height: int, duration: float) -> str:
    """The render page: the block on a stage of the film's size, its values and fonts inside, its scripts beside it."""
    if block not in BLOCKS:
        raise MotionError(f"no motion block {block!r}")
    data = json.dumps({**variables, "block": block, "duration": duration}, ensure_ascii=False).replace("</", "<\\/")
    css = (HERE / "motion.css").read_text(encoding="utf-8") + (HERE / f"{block}.css").read_text(encoding="utf-8")
    return ((HERE / "page.html").read_text(encoding="utf-8").replace("__FONTS__", fonts_css(None))
            .replace("__CSS__", css).replace("__BLOCK__", block).replace("__W__", str(int(width)))
            .replace("__H__", str(int(height))).replace("__D__", f"{duration:.3f}").replace("__VARS__", data))


def render(block: str, variables: dict, width: int, height: int, fps: int, duration: float, cache: Path,
           timeout: float = 1800.0) -> Path:
    """The block rendered to a video with transparency (ProRes 4444 .mov) at the film's size and frame rate."""
    variables = {**variables, "fps": fps}
    key = hashlib.sha256(json.dumps([block, variables, width, height, fps, round(duration, 3), _code()],
                                    sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"{block}-{key}.mov"
    if out.exists() and out.stat().st_size > 0:
        os.utime(out)  # recently used: kept when the cache is trimmed
        return out
    project = cache / f".hf-{key}"
    shutil.rmtree(project, ignore_errors=True)
    project.mkdir(parents=True)
    try:
        (project / "index.html").write_text(page(block, variables, width, height, duration), encoding="utf-8")
        for name in (*RUNTIME, f"{block}.js"):
            shutil.copyfile(HERE / name, project / name)
        part = cache / f"{block}-{key}.part.mov"
        env = {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1"}
        proc = subprocess.run([*engine(), "render", str(project), "--format", "mov", "--fps", str(fps), "-o", str(part),
                               "--quiet"], capture_output=True, timeout=timeout, env=env, cwd=str(project))
        if proc.returncode != 0 or not part.exists():
            tail = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")[-800:]
            raise MotionError(f"the {block} graphic could not be rendered: {tail}")
        part.replace(out)
        _trim(cache)
        return out
    finally:
        shutil.rmtree(project, ignore_errors=True)


def snapshot(block: str, variables: dict, width: int, height: int, duration: float, times: list[float], out: Path,
             timeout: float = 240.0) -> list[Path]:
    """The block's frames at these times (seconds from its start, in order), as transparent PNGs: a graphic seen at
    an instant without rendering all of it (a draft's stills, work/frames.py)."""
    shutil.rmtree(out, ignore_errors=True)
    project = out / "page"
    project.mkdir(parents=True)
    (project / "index.html").write_text(page(block, {**variables}, width, height, duration), encoding="utf-8")
    for name in (*RUNTIME, f"{block}.js"):
        shutil.copyfile(HERE / name, project / name)
    shots = out / "frames"
    env = {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1"}
    at = ",".join(f"{max(0.0, min(duration - 0.001, t)):.4f}" for t in times)
    proc = subprocess.run([*engine(), "snapshot", str(project), "--at", at, "--no-end", "--json", "--describe", "false",
                           "-o", str(shots)], capture_output=True, timeout=timeout, env=env, cwd=str(project))
    drawn = sorted(shots.glob("frame-*.png"))
    if proc.returncode != 0 or len(drawn) != len(times):
        tail = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")[-800:]
        raise MotionError(f"the {block} graphic could not be drawn: {tail}")
    return drawn


def _trim(cache: Path) -> None:
    made = sorted(cache.glob("*.mov"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in made[CACHE_KEEP:]:
        old.unlink(missing_ok=True)


def asset(name: str) -> tuple[bytes, str] | None:
    """A block's script, stylesheet or font for the browser preview, with its type (None: not one of ours)."""
    if name == "fonts.css":
        return fonts_css("/motion/").encode("utf-8"), "text/css; charset=utf-8"
    path = (HERE / name).resolve()
    try:
        path.relative_to(HERE.resolve())
    except ValueError:
        return None
    kinds = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".woff2": "font/woff2"}
    if not path.is_file() or path.suffix not in kinds:
        return None
    return path.read_bytes(), kinds[path.suffix]
