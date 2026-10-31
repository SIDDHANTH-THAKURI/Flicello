"""Fix it for me: the problems on the Connect page Flicello can mend itself, mended, then proved with the same check
that found them (never "should work now"). A person is never asked to run a command.

Only fixed, known commands run (nothing typed by anyone): each fix is a list of steps for this kind of machine. A fix
runs in the background; the page asks how it is going and sees each step as it happens.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import REPO_ROOT

LINUX_RENDERER_LIBS = ("libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libdrm2 libxkbcommon0 "
                       "libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2t64 libpango-1.0-0 libcairo2 "
                       "libxshmfence1 libx11-xcb1 fonts-liberation").split()
LINUX_GL_LIBS = "libegl1 libgl1 libglvnd0 libopengl0".split()


@dataclass
class Step:
    says: str  # what it is doing, in plain words
    argv: list[str]
    cwd: Path | None = None
    timeout: float = 900.0


@dataclass
class Run:
    item: str
    steps: list[dict] = field(default_factory=list)  # {says, state: wait|now|ok|bad, detail}
    done: bool = False
    ok: bool = False
    detail: str = ""
    started: float = field(default_factory=time.time)


RUNS: dict[str, Run] = {}
_LOCK = threading.Lock()


def _root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _npm() -> str | None:
    return shutil.which("npm.cmd" if os.name == "nt" else "npm") or shutil.which("npm")


def _npx() -> str | None:
    return shutil.which("npx.cmd" if os.name == "nt" else "npx") or shutil.which("npx")


def _apt(packages: list[str]) -> list[Step]:
    env_prefix = ["env", "DEBIAN_FRONTEND=noninteractive"]
    return [Step("Getting the list of system packages", env_prefix + ["apt-get", "update", "-qq"], timeout=600),
            Step(f"Installing {len(packages)} system libraries", env_prefix + ["apt-get", "install", "-y", "-qq",
                                                                             *packages], timeout=1200)]


def plan(item: str) -> tuple[list[Step], str]:
    """The steps that mend this item here, or ([], why it can't be mended from this page)."""
    npm, npx = _npm(), _npx()
    linux = sys.platform.startswith("linux")
    if item == "hyperframes":
        if not npm or not npx:
            return [], "Node.js isn't installed, and the motion engine runs on it: fix Node.js first."
        steps = [Step("Installing the motion engine", [npm, "ci", "--no-audit", "--no-fund"], REPO_ROOT / "motion"),
                 Step("Getting its browser", [npx, "hyperframes", "browser", "ensure"], REPO_ROOT / "motion")]
        if linux:
            if not _root() or not shutil.which("apt-get"):
                return [], ("Its browser needs system libraries this computer doesn't have, and installing them needs "
                            "an administrator: run Flicello's pod setup (scripts/pod.sh) once as root.")
            steps = _apt(LINUX_RENDERER_LIBS) + steps
        return steps, ""
    if item == "frontend":
        if not npm:
            return [], "Node.js isn't installed: fix Node.js first."
        return [Step("Installing the app's parts", [npm, "ci", "--no-audit", "--no-fund"], REPO_ROOT / "frontend"),
                Step("Building the app's pages", [npm, "run", "build"], REPO_ROOT / "frontend")], ""
    if item == "gsap":
        if not npm:
            return [], "Node.js isn't installed: fix Node.js first."
        return [Step("Installing the animation library", [npm, "ci", "--no-audit", "--no-fund"], REPO_ROOT / "frontend")], ""
    if item == "claude":
        if not shutil.which("claude"):
            if not npm:
                return [], "Node.js isn't installed: fix Node.js first."
            return [Step("Installing Claude Code", [npm, "install", "-g", "@anthropic-ai/claude-code"])], ""
        return [], "Claude Code is installed but not logged in: open a terminal, run `claude`, and log in with your plan."
    if item == "blender-gpu":
        if not linux:
            return [], ""
        if not _root() or not shutil.which("apt-get"):
            return [], "Blender needs the GL loader (libegl1) to reach the GPU; installing it needs an administrator."
        return _apt(LINUX_GL_LIBS), ""
    return [], ""


def fixable(item: str) -> bool:
    return bool(plan(item)[0])


def _proof(item: str, home: Path, models_dir: Path) -> tuple[bool, str]:
    """The same check that found the problem, run again."""
    from . import checks

    if item == "hyperframes":
        checks._RENDERER.clear()
    if item == "blender-gpu":
        import ctypes.util

        return (bool(ctypes.util.find_library("EGL")), "Blender can reach the GPU now"
                if ctypes.util.find_library("EGL") else "the GL loader still isn't found")
    found = next((i for i in checks.report(home, models_dir)["items"] if i["id"] == item), None)
    if found is None:
        return False, "couldn't check it again"
    return found["status"] == checks.OK, f"{found['label']}: {found['status']}" + (
        f" ({found['detail']})" if found["detail"] else "")


def start(item: str, home: Path, models_dir: Path) -> Run:
    """Start mending `item` (or return the run already going)."""
    with _LOCK:
        run = RUNS.get(item)
        if run is not None and not run.done:
            return run
        steps, why = plan(item)
        run = RUNS[item] = Run(item)
        if not steps:
            run.done, run.ok, run.detail = True, False, why or "There's nothing Flicello can do about this one itself."
            return run
        run.steps = [{"says": s.says, "state": "wait", "detail": ""} for s in steps] + [
            {"says": "Checking it really works now", "state": "wait", "detail": ""}]
    threading.Thread(target=_work, args=(run, steps, home, models_dir), daemon=True, name=f"fix-{item}").start()
    return run


def _work(run: Run, steps: list[Step], home: Path, models_dir: Path) -> None:
    for k, step in enumerate(steps):
        run.steps[k]["state"] = "now"
        try:
            done = subprocess.run(step.argv, cwd=str(step.cwd) if step.cwd else None, capture_output=True, text=True,
                                  timeout=step.timeout)
            ok = done.returncode == 0
            tail = (done.stderr or done.stdout or "").strip().splitlines()
            run.steps[k]["detail"] = "" if ok else (tail[-1][:240] if tail else f"exit {done.returncode}")
        except (OSError, subprocess.SubprocessError) as exc:
            ok = False
            run.steps[k]["detail"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        run.steps[k]["state"] = "ok" if ok else "bad"
        if not ok:
            run.done, run.ok = True, False
            run.detail = f"Stopped at “{step.says}”: {run.steps[k]['detail']}"
            return
    last = run.steps[-1]
    last["state"] = "now"
    ok, said = _proof(run.item, home, models_dir)
    last["state"], last["detail"] = ("ok" if ok else "bad"), said
    run.done, run.ok = True, ok
    run.detail = ("Fixed, and proved: " if ok else "The steps ran, but the check still fails: ") + said


def state(item: str) -> dict | None:
    run = RUNS.get(item)
    if run is None:
        return None
    return {"item": run.item, "steps": run.steps, "done": run.done, "ok": run.ok, "detail": run.detail}
