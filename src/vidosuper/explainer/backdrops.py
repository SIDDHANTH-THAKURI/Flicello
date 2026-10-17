"""Backdrops: the world behind a voice or talking video, painted by an image model in the look the person picked.

A look (studio/brain/schemas.Direction) is pitched with a preview frame; once picked, every place the script names
(`Scene.backdrop`, and `VoiceScript.stage` behind a talking person) becomes one background plate in that look, made
with the preview as its style reference so the scenes belong to one film. Neighbouring scenes that name the same place
share a plate, and the camera keeps moving through it across them.

Plates are made once and kept in the project's `backdrops/` folder under a name taken from what they show, so a
revision only pays for places that are new. Without an image key, or when a plate can't be made, that scene keeps the
drawn gradient: a video never fails for a background.
"""

from __future__ import annotations

import base64
import hashlib
import io
import math
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from .script import VoiceScript

URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
MODEL = "gemini-nano-banana-2.1"
PRICE = {"1K": 0.0336, "2K": 0.0504, "4K": 0.113}  # USD per image (ai.google.dev pricing, 2026-10)
MAX_PLACES = 16  # plates per video and shape at most: past that, places are reused
NO_TEXT = "No text, letters, numbers, signs with writing, logos, watermarks, borders or user interface anywhere in it."
Spend = Callable[[str, str, float, float], None]  # (task, model, usd, seconds) -> raises to stop when over budget


class ImageError(RuntimeError):
    """An image could not be made (said to the log, never to the person)."""


def image_key() -> str:
    from ..music.sources import _load_dotenv

    _load_dotenv()
    return os.environ.get("GEMINI_API_KEY", "").strip()


def _find_image(data) -> bytes | None:
    """The first image in an answer, wherever the API put it (Interactions `output_image`, `steps[].content[]`, or
    generateContent's `inlineData`)."""
    if isinstance(data, dict):
        for key in ("output_image", "image", "inlineData", "inline_data"):
            v = data.get(key)
            if isinstance(v, dict) and isinstance(v.get("data"), str) and len(v["data"]) > 200:
                return base64.b64decode(v["data"])
        if data.get("type") == "image" and isinstance(data.get("data"), str) and len(data["data"]) > 200:
            return base64.b64decode(data["data"])
        for v in data.values():
            found = _find_image(v)
            if found:
                return found
    elif isinstance(data, list):
        for v in data:
            found = _find_image(v)
            if found:
                return found
    return None


class ImageMaker:
    """Gemini's image model over plain HTTPS. `spend` is told the price of every image made."""

    def __init__(self, key: str | None = None, model: str = MODEL, spend: Spend | None = None, http=None):
        self.key = image_key() if key is None else key
        self.model = model
        self.spend = spend or (lambda *a: None)
        self._http = http

    @property
    def available(self) -> bool:
        return bool(self.key)

    def http(self):
        if self._http is None:
            import httpx

            self._http = httpx.Client(timeout=httpx.Timeout(20.0, read=240.0))
        return self._http

    def make(self, prompt: str, aspect: str, size: str = "2K", refs: list[Path] = (), task: str = "image") -> bytes:
        if not self.available:
            raise ImageError("no image key")
        blocks: list[dict] = [{"type": "text", "text": prompt}]
        for r in refs:
            blocks.append({"type": "image", "mime_type": "image/jpeg",
                           "data": base64.b64encode(Path(r).read_bytes()).decode("ascii")})
        body = {"model": self.model, "input": blocks if len(blocks) > 1 else prompt,
                "response_format": {"type": "image", "mime_type": "image/jpeg", "aspect_ratio": aspect,
                                    "image_size": size}}
        t0 = time.monotonic()
        last = ""
        for attempt in range(4):
            r = self.http().post(URL, headers={"x-goog-api-key": self.key}, json=body)
            if r.status_code == 429 and "limit: 0" in r.text:  # no paid tier (billing off): waiting won't help
                raise ImageError("the image key has no paid tier (turn on billing for its project)")
            if r.status_code == 402:  # prepaid credit used up
                raise ImageError("the image account's prepaid credit is used up")
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                last = f"HTTP {r.status_code}"
                time.sleep(4.0 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise ImageError(f"HTTP {r.status_code}: {r.text[:300]}")
            img = _find_image(r.json())
            if img is None:
                last = "no image in the answer"
                continue  # it sometimes answers in words: ask again
            self.spend(task, self.model, PRICE.get(size, PRICE["2K"]), time.monotonic() - t0)
            return img
        raise ImageError(last or "no image")


# ---------------------------------------------------------------- prompts

def _look_text(look: dict) -> str:
    colours = ", ".join(look.get("colours") or [])
    return (f"World: {look.get('world', '')}\nArt style: {look.get('art_style', '')}\n"
            + (f"Colours: {colours}\n" if colours else ""))


def preview_prompt(look: dict, aspect: str) -> str:
    return (f"A single key frame from a beautifully art-directed animated explainer video ({_orientation(aspect)}).\n"
            f"{_look_text(look)}The frame: {look.get('preview', '')}\n"
            "Cinematic composition with real depth (foreground, middle ground, far away), considered light, rich "
            "texture: it should look like the work of a top motion design studio. " + NO_TEXT)


def plate_prompt(look: dict, place: str, aspect: str, behind_person: bool = False) -> str:
    middle = ("A person will be cut out and stand in the lower middle: keep that area simple and a little out of focus, "
              "the interest around the sides and top." if behind_person else
              "Titles and graphics will be laid over the middle: keep the centre calm, uncluttered and slightly darker, "
              "with the detail and interest around the edges.")
    return (f"A background plate for one scene of an animated explainer video ({_orientation(aspect)}).\n"
            f"{_look_text(look)}This scene's place: {place}\n"
            f"{middle} Real depth (foreground, middle ground, far away) so a slow camera move feels like moving through "
            "it. Match the attached reference image exactly in medium, rendering, line, texture, light and colours, as "
            "if from the same film; a different place in that world. " + NO_TEXT)


def _orientation(aspect: str) -> str:
    return "vertical 9:16, for phones" if aspect == "9:16" else "widescreen 16:9"


# ---------------------------------------------------------------- making them

def key_of(look: dict, place: str, behind: bool = False) -> str:
    raw = "|".join([look.get("title", ""), look.get("art_style", ""), look.get("world", ""), place.strip().lower(),
                    "stage" if behind else "scene"])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]


def plate_path(folder: Path, key: str, aspect: str) -> Path:
    return Path(folder) / f"{key}-{aspect.replace(':', 'x')}.jpg"


def save_jpeg(data: bytes, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(io.BytesIO(data)).convert("RGB")
    part = path.with_name(path.name + ".part")
    img.save(part, "JPEG", quality=92)
    os.replace(part, path)
    return path


def make_preview(look: dict, maker: ImageMaker, aspect: str, path: Path,
                 log: Callable[[str], None] | None = None) -> Path | None:
    """The key frame of a look, at 1K (it is only looked at small). None when it can't be made."""
    try:
        return save_jpeg(maker.make(preview_prompt(look, aspect), aspect, "1K", task="look"), path)
    except ImageError as exc:
        (log or (lambda m: None))(f"preview not made: {exc}")
        return None


def make_plates(script: VoiceScript, look: dict, maker: ImageMaker, aspect: str, folder: Path, style_ref: Path | None,
                window: tuple[float, float] | None = None, log: Callable[[str], None] | None = None,
                mode: str = "voice") -> VoiceScript:
    """The script with `backdrop_file` (and `stage_file`) set to the key of each plate, every plate made for `aspect`
    (only the scenes inside `window`, for a sample). Plates already made are reused."""
    log = log or (lambda m: None)
    places: list[tuple[str, bool]] = []
    for s in script.scenes:
        if s.backdrop.strip() and (window is None or (s.end > window[0] and s.start < window[1])):
            if (s.backdrop.strip(), False) not in places:
                places.append((s.backdrop.strip(), False))
    if mode == "talk" and script.stage.strip():
        places.insert(0, (script.stage.strip(), True))
    if len(places) > MAX_PLACES:
        log(f"{len(places)} places asked for; making {MAX_PLACES}, the rest reuse them")
        places = places[:MAX_PLACES]
    keys = {(p, b): key_of(look, p, b) for p, b in places}
    todo = [(p, b) for p, b in places if not plate_path(folder, keys[(p, b)], aspect).exists()]
    made: set[str] = {keys[x] for x in places if x not in todo}
    refs = [style_ref] if style_ref and Path(style_ref).exists() else []

    def one(item) -> str | None:
        place, behind = item
        try:
            save_jpeg(maker.make(plate_prompt(look, place, aspect, behind), aspect, "2K", refs[:2],
                                 task="backdrop"), plate_path(folder, keys[item], aspect))
            return keys[item]
        except ImageError as exc:
            log(f"backdrop not made ({exc}): {place[:60]}")
            return None

    if todo and maker.available:
        if not refs:  # no preview to match: the first plate sets the style for the rest
            first = one(todo.pop(0))
            if first:
                made.add(first)
                refs = [plate_path(folder, first, aspect)]
        with ThreadPoolExecutor(4) as ex:
            made.update(k for k in ex.map(one, todo) if k)
    by_place = {p: keys[(p, b)] for p, b in places if keys[(p, b)] in made and not b}
    order = [k for k in by_place.values()]
    scenes = []
    for s in script.scenes:
        k = by_place.get(s.backdrop.strip(), "")
        if not k and s.backdrop.strip() and order and window is None:
            k = order[len(scenes) % len(order)]  # past the cap: an earlier place, rather than none
        scenes.append(s.model_copy(update={"backdrop_file": k}))
    stage = keys.get((script.stage.strip(), True), "") if mode == "talk" and script.stage.strip() else ""
    return script.model_copy(update={"scenes": scenes,
                                     "stage_file": stage if stage in made else script.stage_file})


# ---------------------------------------------------------------- drawing them

def shots(script: VoiceScript) -> dict[int, tuple[float, float, str]]:
    """Scene index -> (start, end, camera) of the shot it is part of: neighbours on the same plate are one shot, so
    the camera keeps going instead of jumping back."""
    out: dict[int, tuple[float, float, str]] = {}
    k = 0
    sc = script.scenes
    while k < len(sc):
        j = k
        while j + 1 < len(sc) and sc[j + 1].backdrop_file and sc[j + 1].backdrop_file == sc[k].backdrop_file:
            j += 1
        for i in range(k, j + 1):
            out[i] = (sc[k].start, sc[j].end, sc[k].camera)
        k = j + 1
    return out


def _ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def camera_box(camera: str, p: float, t: float) -> tuple[float, float, float]:
    """(zoom, x, y) at progress p of a shot: zoom >= 1 over the cover-fitted plate, x/y the centre offset as a share
    of the frame. Moves are slow and steady (an eased start and end), with a faint breath so it never freezes."""
    e = 0.15 * p + 0.85 * _ease(p)
    br = 0.004 * math.sin(t * 0.9)
    if camera == "pull":
        return 1.13 - 0.11 * e + br, 0.0, 0.0
    if camera == "left":
        return 1.1 + br, 0.04 - 0.08 * e, 0.0
    if camera == "right":
        return 1.1 + br, -0.04 + 0.08 * e, 0.0
    if camera == "rise":
        return 1.1 + br, 0.0, 0.04 - 0.08 * e
    if camera == "still":
        return 1.04 + 0.02 * e + br, 0.0, 0.0
    return 1.02 + 0.11 * e + br, 0.01 * e, -0.005 * e  # push


class Plates:
    """Plates for one size, graded so text and graphics read over them: a little darker (lighter for the paper
    palette's dark ink), calmer in the middle, a vignette. Kept in memory once loaded."""

    def __init__(self, folder: Path | None, size: tuple[int, int], aspect: str):
        self.folder, self.size, self.aspect = (Path(folder) if folder else None), size, aspect
        self._cache: dict[tuple[str, bool, bool], np.ndarray | None] = {}

    def has(self, key: str) -> bool:
        return bool(key) and self.folder is not None and plate_path(self.folder, key, self.aspect).exists()

    def _load(self, key: str, light: bool, soft: bool) -> np.ndarray | None:
        ck = (key, light, soft)
        if ck in self._cache:
            return self._cache[ck]
        import cv2

        img = None
        if self.has(key):
            w, h = self.size
            src = Image.open(plate_path(self.folder, key, self.aspect)).convert("RGB")
            sc = max(w * 1.16 / src.width, h * 1.16 / src.height)  # room for the camera to move
            src = src.resize((max(1, round(src.width * sc)), max(1, round(src.height * sc))), Image.LANCZOS)
            a = np.asarray(src, np.float32)
            if soft:  # behind a person: out of focus, like a real lens
                a = cv2.GaussianBlur(a, (0, 0), max(2.0, w / 380))
            hh, ww = a.shape[:2]
            yy, xx = np.mgrid[0:hh, 0:ww].astype(np.float32)
            r = np.sqrt(((xx - ww / 2) / (ww / 2)) ** 2 + ((yy - hh / 2) / (hh / 2)) ** 2)
            centre = np.clip(1 - r, 0, 1)[..., None]  # 1 in the middle, 0 at the edges
            grey = a.mean(axis=2, keepdims=True)
            a = grey + (a - grey) * 0.9
            if light:  # dark ink on top: lift it a little, most in the middle where the text is
                a = a * (0.86 - 0.12 * centre) + 255 * (0.14 + 0.12 * centre)
            else:
                vig = (1 - 0.45 * np.clip(r - 0.35, 0, 1))[..., None]
                a = a * (0.74 - 0.16 * centre) * vig
            img = np.clip(a, 0, 255).astype(np.uint8)
        self._cache[ck] = img
        return img

    def frame(self, key: str, camera: str, p: float, t: float, light: bool = False, soft: bool = False) -> Image.Image | None:
        src = self._load(key, light, soft)
        if src is None:
            return None
        import cv2

        w, h = self.size
        z, ox, oy = camera_box(camera, p, t)
        hh, ww = src.shape[:2]
        base = min(ww / w, hh / h)  # source pixels per frame pixel at zoom 1
        s = base / z
        cx = ww / 2 + ox * w * base
        cy = hh / 2 + oy * h * base
        m = np.array([[s, 0, cx - s * w / 2], [0, s, cy - s * h / 2]], np.float32)
        out = cv2.warpAffine(src, m, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)
        return Image.fromarray(out).convert("RGBA")
