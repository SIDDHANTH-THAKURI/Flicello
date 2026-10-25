"""The prompt the person pastes into Claude Code (in a terminal, anywhere): it checks this machine with Flicello's
own report, installs or fixes whatever is missing, and explains as it goes. It never touches their keys: those are
entered on the Connect page, which keeps them in the keychain."""

from __future__ import annotations

import platform
import sys

from ..config import REPO_ROOT


def prompt(port: int = 8765) -> str:
    py = sys.executable
    steps = [
        f"Set up Flicello on this computer for me. Flicello is an open-source video studio at {REPO_ROOT}.",
        "",
        f'1. Run this to see what is ready and what is missing (it prints JSON): "{py}" -m vidosuper connect --json',
        '2. For every item whose status is "missing" or "failing" in the groups "tools" and "models", fix it, one at '
        "a time: install the tool (FFmpeg 7+ full build, Node.js 22+, Blender only if I ask), build what needs "
        "building, or download the model to the exact path shown, from the source in its \"fix\" text (check it is "
        f"the right file: ONNX, the size you expect). This is {platform.system()} {platform.release()}: use this "
        "system's normal way of installing things, and tell me before anything needs administrator rights.",
        f"3. Do NOT handle my API keys or log in to anything for me. If a key is missing, tell me to add it on the "
        f"Connect page (http://127.0.0.1:{port}/connect). If Claude Code itself is not logged in, tell me to run "
        "`claude` and log in.",
        "4. Run the report again and show me what is ready now, and anything you could not fix, with why.",
        "",
        "Explain each step in one line as you go.",
    ]
    return "\n".join(steps)
