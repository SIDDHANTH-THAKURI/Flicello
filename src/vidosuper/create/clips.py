"""Shots made from words, for a film that has no footage of something: on this machine's GPU (a still painted from
the words, then brought to life by Wan 2.2), or by Higgsfield with the person's own key (create/higgsfield.py).

VIDO_AI_CLIPS chooses ("local" or "higgsfield"); by default this machine when it can, else Higgsfield when its key is
on the Connect page.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


class ClipError(RuntimeError):
    pass


def local_ready() -> bool:
    from ..motion import videogen

    return videogen.available()


def maker() -> str | None:
    """Who makes a clip from words here: "local", "higgsfield", or None (nothing can)."""
    from . import higgsfield

    want = os.environ.get("VIDO_AI_CLIPS", "").strip().lower()
    if want == "local" and local_ready():
        return "local"
    if want == "higgsfield" and higgsfield.available():
        return "higgsfield"
    if local_ready():
        return "local"
    if higgsfield.available():
        return "higgsfield"
    return None


def describe(who: str | None) -> str:
    """What the page says about who will make it."""
    return {"local": "Made on your GPU: a frame painted from your words, then Wan 2.2 brings it to life",
            "higgsfield": "Made by Higgsfield (Kling 3) with your key, paid from your Higgsfield balance"}.get(
        who or "", "Add your Higgsfield key on the Connect page, or run Flicello on an NVIDIA GPU with the video "
                   "extra, to make shots from words")


def make(prompt: str, out: Path, *, seconds: int = 5, aspect: str = "9:16", log=print) -> dict:
    """A clip of what the words say, saved to `out` (mp4). Returns {maker, model, seconds, usd}."""
    who = maker()
    if who is None:
        raise ClipError(describe(None) + ".")
    seconds = max(3, min(7 if who == "local" else 15, int(round(seconds))))
    if who == "higgsfield":
        from . import higgsfield

        try:
            made = higgsfield.generate(prompt, out, seconds=seconds, aspect=aspect)
        except higgsfield.HiggsfieldError as exc:
            raise ClipError(str(exc)) from exc
        return {"maker": "higgsfield", **made}
    from ..motion import cartoon, scenes, videogen

    w, h = cartoon.SHAPES.get(aspect, (1080, 1920))
    work = out.with_suffix("")
    work.mkdir(parents=True, exist_ok=True)
    start = work / "start.png"
    log("Painting the first frame from your words")
    scenes.paint(f"{prompt}. A real photograph, a frame of a film, natural light.", start, whole=True, aspect=w / h)
    log("Bringing it to life on your GPU")
    raw = work / "raw.mp4"
    videogen.animate(start, prompt, raw, seconds=seconds + 0.25, look="cartoon", seed=7)
    videogen.release()
    cartoon.fit_clip(raw, float(seconds), w, h, out)  # the film's size, 30 fps
    shutil.rmtree(work, ignore_errors=True)
    return {"maker": "local", "model": "wan-2.2-i2v", "seconds": seconds, "usd": 0.0}
