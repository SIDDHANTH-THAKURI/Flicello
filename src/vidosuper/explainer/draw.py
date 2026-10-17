"""Drawing the animations: backgrounds, the scene layouts, callouts and captions, one frame at a time with Pillow.

Everything is sized from the frame's short side, so one design serves 9:16 and 16:9. Pictures are colour emoji (the
system's emoji font) so any idea can be drawn; text is the bundled Nunito Black. Motion is springs and fades timed to
the words, and the voice's loudness drives a soft pulse behind the hero picture.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..outro.draw import font, place, text_sprite
from .script import Callout, Scene

EMOJI_FONTS = [r"C:\Windows\Fonts\seguiemj.ttf", "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
               "/usr/share/fonts/noto/NotoColorEmoji.ttf", "/System/Library/Fonts/Apple Color Emoji.ttc"]

PALETTE = {
    "midnight": dict(top=(14, 17, 40), bottom=(36, 40, 96), ink=(255, 255, 255), soft=(196, 203, 240), accent=(255, 210, 74),
                     accent2=(94, 234, 212), card=(255, 255, 255, 30)),
    "sunrise": dict(top=(255, 154, 98), bottom=(255, 82, 124), ink=(255, 250, 240), soft=(255, 232, 214), accent=(255, 240, 150),
                    accent2=(124, 58, 237), card=(255, 255, 255, 52)),
    "mint": dict(top=(8, 52, 50), bottom=(24, 126, 106), ink=(244, 255, 250), soft=(190, 238, 224), accent=(255, 226, 120),
                 accent2=(255, 140, 160), card=(255, 255, 255, 36)),
    "paper": dict(top=(247, 242, 232), bottom=(238, 228, 206), ink=(28, 28, 40), soft=(96, 96, 112), accent=(255, 91, 74),
                  accent2=(47, 111, 237), card=(255, 255, 255, 170)),
    "berry": dict(top=(40, 14, 62), bottom=(112, 30, 112), ink=(255, 244, 252), soft=(226, 190, 232), accent=(255, 143, 208),
                  accent2=(255, 213, 107), card=(255, 255, 255, 34)),
}
BLACK, EXTRA = "Nunito-Black", "Nunito-ExtraBold"


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def back(x: float, k: float = 1.9) -> float:
    """A spring: out past 1, then settles."""
    x = clamp(x)
    return 1 + (k + 1) * (x - 1) ** 3 + k * (x - 1) ** 2


def pop(t: float, t0: float, dur: float = 0.42) -> float:
    return 0.0 if t < t0 else back((t - t0) / dur)


def fade(t: float, t0: float, dur: float = 0.25) -> float:
    return clamp((t - t0) / dur)


def smooth(x: float) -> float:
    x = clamp(x)
    return x * x * (3 - 2 * x)


# ---------------------------------------------------------------- emoji and text sprites

@lru_cache(maxsize=1)
def _emoji_font() -> ImageFont.FreeTypeFont | None:
    for path in EMOJI_FONTS:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, 109)
            except OSError:
                continue
    return None


@lru_cache(maxsize=256)
def _emoji_base(text: str) -> Image.Image | None:
    f = _emoji_font()
    if f is None:
        return None
    img = Image.new("RGBA", (int(f.getlength(text)) + 80, 200), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((30, 30), text, font=f, embedded_color=True)
    box = img.getbbox()
    return img.crop(box) if box else None


@lru_cache(maxsize=1)
def _notdef() -> bytes | None:
    base = _emoji_base("\U0010FFFF")
    return base.tobytes() if base is not None else None


@lru_cache(maxsize=512)
def emoji_sprite(text: str, px: int) -> Image.Image | None:
    """The emoji as a picture `px` tall (None when the font cannot draw it)."""
    base = _emoji_base(text)
    if base is None or (_notdef() is not None and base.tobytes() == _notdef()):
        return None
    scale = px / max(base.height, 1)
    return base.resize((max(1, round(base.width * scale)), max(1, round(base.height * scale))), Image.LANCZOS)


@lru_cache(maxsize=512)
def outlined(text: str, px: int, fill: tuple[int, int, int], stroke: tuple[int, int, int] = (14, 14, 24),
             max_width: int | None = None) -> Image.Image:
    """Text with a thick outline (captions that read over any picture)."""
    f = font(BLACK, px)
    sw = max(2, px // 9)
    w = int(f.getlength(text)) + 4 * sw
    img = Image.new("RGBA", (w, int(px * 1.5) + 4 * sw), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((2 * sw, 2 * sw), text, font=f, fill=(*fill, 255), stroke_width=sw, stroke_fill=(*stroke, 255))
    return img


@lru_cache(maxsize=128)
def fitted(text: str, name: str, color: tuple, max_w: int, max_h: int, px: int, align: str = "center") -> Image.Image:
    """Wrapped text sized down until it fits the box."""
    while True:
        sprite = text_sprite(text, name, px, color, max_width=max_w, align=align)
        if (sprite.height <= max_h and sprite.width <= max_w + px) or px <= 14:
            return sprite
        px = int(px * 0.9)


# ---------------------------------------------------------------- the background

@lru_cache(maxsize=16)
def _gradient(size: tuple[int, int], palette: str) -> Image.Image:
    w, h = size
    p = PALETTE[palette]
    t = np.linspace(0, 1, h)[:, None, None]
    top, bottom = np.array(p["top"], float), np.array(p["bottom"], float)
    arr = (top * (1 - t) + bottom * t).repeat(w, axis=1)
    return Image.fromarray(arr.astype(np.uint8), "RGB").convert("RGBA")


@lru_cache(maxsize=32)
def _blob(px: int, color: tuple[int, int, int], alpha: int) -> Image.Image:
    img = Image.new("RGBA", (px * 2, px * 2), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([px * 0.3, px * 0.3, px * 1.7, px * 1.7], fill=(*color, alpha))
    return img.filter(ImageFilter.GaussianBlur(px * 0.25))


def background(size: tuple[int, int], palette: str, t: float, level: float = 0.0) -> Image.Image:
    """The palette's gradient with three soft shapes drifting behind everything. Drawn a quarter size and enlarged:
    the shapes are soft anyway, and it is a third of the cost of a frame."""
    w, h = size
    sw, sh = max(8, w // 4), max(8, h // 4)
    p = PALETTE[palette]
    frame = _gradient((sw, sh), palette).copy()
    u = min(sw, sh)
    for k, (color, r, ax, ay, sp) in enumerate(((p["accent"], 0.55, 0.28, 0.22, 0.13), (p["accent2"], 0.7, 0.3, 0.2, 0.09),
                                                (p["soft"], 0.45, 0.25, 0.28, 0.17))):
        x = sw * (0.5 + ax * math.sin(t * sp * 6.28 + k * 2.1))
        y = sh * (0.5 + ay * math.cos(t * sp * 5.1 + k * 1.3))
        place(frame, _blob(int(u * r), color, 70 + 15 * round(4 * clamp(level))), x, y)  # 5 steps: each blur is made once
    return frame.resize((w, h), Image.BILINEAR)


# ---------------------------------------------------------------- pieces

def card(size: tuple[int, int], radius: int, fill: tuple[int, ...]) -> Image.Image:
    return _card(size, radius, fill)


@lru_cache(maxsize=256)
def _card(size: tuple[int, int], radius: int, fill: tuple[int, ...]) -> Image.Image:
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=fill)
    return img


LOTTIE = None  # set by the renderer: (animation id, seconds into it, width) -> RGBA image, for designer animations
LOTTIE_ASPECT = None
PROPS = None  # backdrops.Props when the look's painted props are drawn instead of animations  # set with it: animation id -> height / width of its drawing (None when it can't be drawn)


def art_box(frame: Image.Image, s: Scene, cx: float, cy: float, mw: float, mh: float, t: float, t0: float, pal: dict,
            level: float) -> None:
    """The scene's picture at the largest size that fits the box (mw x mh, centred on cx, cy): its designer animation
    when it has one, else its emoji in rings that pulse with the voice. Never spills out of the box onto the text."""
    if s.prop_file and PROPS is not None and (aspect := PROPS.aspect(s.prop_file)):
        prop_art(frame, s.prop_file, aspect, cx, cy, mw, mh, t, t0, level)
        return
    if s.lottie and LOTTIE is not None and LOTTIE_ASPECT is not None:
        aspect = LOTTIE_ASPECT(s.lottie)
        if aspect:
            k = pop(t, t0)
            width = int(max(8, min(mw, mh / max(0.05, aspect))))
            art = LOTTIE(s.lottie, t - t0, width)
            if art is not None:
                place(frame, art, cx, cy + math.sin(t * 2.2) * mh * 0.012, scale=0.6 + 0.4 * clamp(k, 0, 1.1),
                      alpha=clamp(k * 1.5))
                return
    hero(frame, s.emoji, cx, cy, int(min(mw, mh) * 0.42), t, t0, pal, level)


def prop_art(frame: Image.Image, key: str, aspect: float, cx: float, cy: float, mw: float, mh: float, t: float,
             t0: float, level: float) -> None:
    """A painted prop, brought to life: it springs in, floats and breathes a little with the voice, as a motion
    designer would animate a still illustration. (A drawn contact shadow read as a grey bar under the frosted
    backing: the prop floats.)"""
    k = pop(t, t0)
    if k <= 0:
        return
    width = min(mw, mh / max(0.05, aspect)) * 0.92
    scale = (0.55 + 0.45 * clamp(k, 0, 1.12)) * (1.0 + 0.012 * math.sin((t - t0) * 1.7) + 0.02 * level)
    art = PROPS.get(key, int(width * scale))
    if art is None:
        return
    bob = math.sin((t - t0) * 1.9) * mh * 0.012
    place(frame, art, cx, cy + bob, alpha=clamp(k * 1.6))


def _lerp(a: tuple, b: tuple, k: float) -> tuple:
    return tuple(x + (y - x) * k for x, y in zip(a, b))


def hero(frame: Image.Image, emoji: str, cx: float, cy: float, px: int, t: float, t0: float, pal: dict, level: float,
         lottie: str = "") -> None:
    """The scene's big picture: a designer animation when there is one, else a ring pulsing with the voice and the
    emoji springing in."""
    k = pop(t, t0)
    art = LOTTIE(lottie, t - t0, int(px * 1.9)) if (lottie and LOTTIE is not None) else None
    if art is not None:
        place(frame, art, cx, cy + math.sin(t * 2.2) * px * 0.02, scale=0.6 + 0.4 * clamp(k, 0, 1.1), alpha=clamp(k * 1.5))
        return
    ring = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ring)
    for j, (grow, a) in enumerate(((1.0, 90), (1.28, 55), (1.6, 28))):
        r = px * 0.72 * (grow + 0.12 * level) * clamp(k, 0, 1.1)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*pal["accent"], a), width=max(2, int(px * 0.035)))
    frame.alpha_composite(ring)
    sprite = emoji_sprite(emoji, px) if emoji else None
    if sprite is not None:
        place(frame, sprite, cx, cy + math.sin(t * 2.2) * px * 0.03, scale=0.35 + 0.65 * k, alpha=clamp(k * 1.5))


def title_text(frame: Image.Image, text: str, cx: float, cy: float, max_w: int, max_h: int, px: int, color: tuple,
               t: float, t0: float, rise: float = 0.05) -> None:
    sprite = fitted(text, BLACK, color, max_w, max_h, px)
    k = pop(t, t0, 0.5)
    place(frame, sprite, cx, cy + (1 - clamp(k, 0, 1)) * rise * frame.height, alpha=fade(t, t0, 0.2), scale=0.9 + 0.1 * clamp(k, 0, 1.05))


# ---------------------------------------------------------------- the scene layouts

def draw_scene(frame: Image.Image, s: Scene, t: float, pal: dict, level: float) -> None:
    """One scene at time t (seconds into the audio)."""
    w, h = frame.size
    u, vertical = min(w, h), h > w
    ink, accent, soft = pal["ink"], pal["accent"], pal["soft"]
    a = s.start
    maxw = int(w * (0.84 if vertical else 0.7))
    if s.layout in ("list", "steps", "compare", "flow", "chat") and s.items:
        _split(frame, s, t, pal, level)
        return
    if s.layout in ("title", "keyword"):
        big = s.layout == "title"
        if vertical:
            art_box(frame, s, w / 2, h * 0.34, w * 0.84, h * 0.38, t, a + 0.05, pal, level)
        else:
            art_box(frame, s, w / 2, h * 0.37, w * 0.62, h * 0.56, t, a + 0.05, pal, level)
        title_text(frame, s.headline.upper() if big else s.headline, w / 2, h * (0.62 if vertical else 0.73), maxw,
                   int(h * (0.26 if vertical else 0.17)), int(u * (0.11 if big else 0.12)), ink, t, a + 0.25)
        bar = pop(t, a + 0.5, 0.6)
        if bar > 0:
            bw = int(u * 0.5 * clamp(bar, 0, 1))
            ImageDraw.Draw(frame).rounded_rectangle([w / 2 - bw / 2, h * (0.75 if vertical else 0.825), w / 2 + bw / 2,
                                                     h * (0.75 if vertical else 0.825) + u * 0.014], u * 0.007, fill=(*accent, 255))
        cx0, cy0 = w / 2, h * (0.34 if vertical else 0.4)
        base_r = u * (0.34 if big else 0.28) * 0.95
        painted = bool(s.prop_file and PROPS is not None)  # a painted prop: no stock emoji circling it
        for k, it in enumerate(s.items[:4] if s.layout == "keyword" and not painted else []):
            pk = pop(t, it.at)
            em = emoji_sprite(it.emoji, int(u * 0.1)) if it.emoji else None
            if pk <= 0 or em is None:
                continue
            ang = (k / max(1, len(s.items[:4]))) * 6.283 + (t - it.at) * 0.7 + 0.6
            place(frame, em, cx0 + math.cos(ang) * base_r, cy0 + math.sin(ang) * base_r * 0.9, scale=clamp(pk, 0, 1.15),
                  alpha=clamp(pk * 1.4))
        for k, it in enumerate(s.items[:3] if s.layout == "keyword" else []):
            if it.text and not it.emoji:  # a picture orbits instead; the captions carry the words
                if vertical:  # stacked, so long labels never run off the edge
                    _chip(frame, "", it.text, w / 2, h * 0.8 + k * u * 0.125, u, t, it.at, pal)
                else:
                    _chip(frame, "", it.text, w / 2 + (k - (len(s.items[:3]) - 1) / 2) * u * 0.5, h * 0.93, u, t, it.at, pal)
    elif s.layout == "list":
        title_text(frame, s.headline, w / 2, h * 0.17, maxw, int(h * 0.14), int(u * 0.08), accent, t, a + 0.05, 0.03)
        rows = s.items[:4] or []
        gap = u * 0.2
        y0 = h * 0.5 - gap * (len(rows) - 1) / 2
        for k, it in enumerate(rows):
            _row(frame, it, w / 2, y0 + k * gap, u, t, pal, maxw)
    elif s.layout == "steps":
        title_text(frame, s.headline, w / 2, h * 0.14, maxw, int(h * 0.12), int(u * 0.07), accent, t, a + 0.05, 0.03)
        _steps(frame, s, t, pal, u, vertical)
    elif s.layout == "stat":
        title_text(frame, s.headline, w / 2, h * (0.76 if vertical else 0.84), maxw, int(h * 0.16), int(u * 0.075), soft, t, a + 0.05, 0.03)
        _stat(frame, s, t, pal, u)
    elif s.layout == "compare":
        title_text(frame, s.headline, w / 2, h * 0.12, maxw, int(h * 0.1), int(u * 0.065), accent, t, a + 0.05, 0.03)
        _compare(frame, s, t, pal, u, vertical)
    elif s.layout == "flow":
        title_text(frame, s.headline, w / 2, h * 0.12, maxw, int(h * 0.1), int(u * 0.065), accent, t, a + 0.05, 0.03)
        _flow(frame, s, t, pal, u, vertical)
    elif s.layout == "chat":
        title_text(frame, s.headline, w / 2, h * 0.11, maxw, int(h * 0.1), int(u * 0.06), accent, t, a + 0.05, 0.03)
        _chat(frame, s, t, pal, u, maxw)
    elif s.layout == "quote":
        k = pop(t, a + 0.05)
        if s.lottie and LOTTIE is not None:  # the animation is the picture; the line sits under it
            art_box(frame, s, w / 2, h * (0.3 if vertical else 0.33), w * 0.7, h * (0.32 if vertical else 0.44), t, a + 0.05, pal,
                    level)
            title_text(frame, "“" + s.headline + "”", w / 2, h * (0.62 if vertical else 0.72), maxw, int(h * 0.18),
                       int(u * 0.095), ink, t, a + 0.3)
            return
        q = text_sprite("\u201c", "Fraunces-SemiBold", int(u * 0.5), (*accent, 255))
        place(frame, q, w / 2, h * 0.27, scale=0.5 + 0.5 * clamp(k, 0, 1.1), alpha=clamp(k * 1.4))
        title_text(frame, s.headline, w / 2, h * 0.55, maxw, int(h * 0.32), int(u * 0.085), ink, t, a + 0.3)
        if s.emoji:
            sprite = emoji_sprite(s.emoji, int(u * 0.16))
            if sprite is not None:
                place(frame, sprite, w / 2, h * 0.78, alpha=fade(t, a + 0.7), scale=0.6 + 0.4 * clamp(pop(t, a + 0.7), 0, 1.1))


def _split(frame: Image.Image, s: Scene, t: float, pal: dict, level: float) -> None:
    """A scene with parts that arrive as they are spoken (list, steps, compare, flow, chat). Its picture holds the
    stage from the first frame, big, so the screen is never empty while the first part is waited for; as the first
    part arrives the picture glides aside (left in landscape, up in portrait) and the parts build in the space left."""
    w, h = frame.size
    u, vertical = min(w, h), h > w
    a = s.start
    first = min(it.at for it in s.items)
    k = smooth((t - (first - 0.45)) / 0.55)
    if vertical:
        alone, aside = (w / 2, h * 0.5, w * 0.84, h * 0.5), (w / 2, h * 0.215, w * 0.6, h * 0.15)
        region = (0, int(h * 0.31), w, h)
        head_y = h * 0.075
    else:
        alone, aside = (w / 2, h * 0.56, w * 0.6, h * 0.64), (w * 0.19, h * 0.57, w * 0.3, h * 0.58)
        region = (int(w * 0.35), int(h * 0.2), w, h)
        head_y = h * 0.1
    title_text(frame, s.headline, w / 2, head_y, int(w * 0.84), int(h * 0.11), int(u * 0.082), pal["ink"], t, a + 0.05, 0.03)
    art_box(frame, s, *_lerp(alone, aside, k), t, a + 0.05, pal, level)
    if t < first - 0.05:
        return
    x0, y0, x1, y1 = region
    sub = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    su, sv = min(sub.size), sub.height > sub.width
    if s.layout == "list":
        rows = s.items[:4]
        gap = su * 0.2
        mw = int(sub.width * 0.88)
        for j, it in enumerate(rows):
            _row(sub, it, sub.width / 2, sub.height * 0.48 - gap * (len(rows) - 1) / 2 + j * gap, su, t, pal, mw)
    elif s.layout == "steps":
        _steps(sub, s, t, pal, su, sv)
    elif s.layout == "compare":
        _compare(sub, s, t, pal, su, sv)
    elif s.layout == "flow":
        _flow(sub, s, t, pal, su, sv)
    else:
        _chat(sub, s, t, pal, su, int(sub.width * 0.92))
    frame.alpha_composite(sub, (x0, y0))


def _flow(frame: Image.Image, s: Scene, t: float, pal: dict, u: float, vertical: bool) -> None:
    """A diagram that plays: boxes appear as each is spoken, arrows draw to the next, and dots keep travelling along them."""
    w, h = frame.size
    items = s.items[:4]
    n = len(items)
    if n == 0:
        return
    pts = []
    for k in range(n):
        f = (k + 0.5) / n
        pts.append((w / 2, h * (0.2 + 0.64 * f)) if vertical else (w * (0.1 + 0.8 * f), h * 0.52))
    d = ImageDraw.Draw(frame)
    bw, bh = (w * 0.72, u * 0.2) if vertical else (w * 0.2, u * 0.4)
    width = max(3, int(u * 0.012))
    for k in range(n - 1):
        (x0, y0), (x1, y1) = pts[k], pts[k + 1]
        ux, uy = x1 - x0, y1 - y0
        norm = math.hypot(ux, uy) or 1
        gapx = (bh / 2 + u * 0.015) if vertical else (bw / 2 + u * 0.015)
        sx, sy = x0 + ux / norm * gapx, y0 + uy / norm * gapx
        ex, ey = x1 - ux / norm * gapx, y1 - uy / norm * gapx
        prog = smooth((t - items[k + 1].at + 0.45) / 0.4)
        if prog <= 0:
            continue
        cx, cy = sx + (ex - sx) * prog, sy + (ey - sy) * prog
        d.line([(sx, sy), (cx, cy)], fill=(*pal["accent"], 235), width=width)
        if prog > 0.97:
            a = math.atan2(ey - sy, ex - sx)
            for sgn in (-1, 1):
                d.line([(ex, ey), (ex - math.cos(a + sgn * 0.5) * u * 0.04, ey - math.sin(a + sgn * 0.5) * u * 0.04)],
                       fill=(*pal["accent"], 235), width=width)
            for j in range(2):  # data packets keep running along the arrow
                ph = ((t - items[k + 1].at) * 0.55 + j * 0.5) % 1.0
                px_, py_ = sx + (ex - sx) * ph, sy + (ey - sy) * ph
                r = u * 0.018
                d.ellipse([px_ - r, py_ - r, px_ + r, py_ + r], fill=(*pal["ink"], 255))
    for (x, y), it in zip(pts, items):
        k = pop(t, it.at)
        if k <= 0:
            continue
        glow = 1.0 + 0.04 * math.sin((t - it.at) * 5) if t > it.at + 0.4 else 1.0
        place(frame, card((int(bw), int(bh)), int(bh * 0.28), pal["card"]), x, y, scale=clamp(k, 0, 1.1) * glow,
              alpha=clamp(k * 1.4))
        em = emoji_sprite(it.emoji or "✨", int(bh * 0.55))
        label_w = int(bw * (0.62 if vertical else 0.86))
        if vertical:
            if em is not None:
                place(frame, em, x - bw * 0.36, y, scale=clamp(k, 0, 1.15))
            label = fitted(it.text, BLACK, (*pal["ink"], 255), label_w, int(bh * 0.74), int(u * 0.062), "left")
            place(frame, label, x - bw * 0.2, y, alpha=clamp(k * 1.4), anchor=(0.0, 0.5))
        else:
            if em is not None:
                place(frame, em, x, y - bh * 0.14, scale=clamp(k, 0, 1.15))
            label = fitted(it.text, BLACK, (*pal["ink"], 255), label_w, int(bh * 0.3), int(u * 0.05))
            place(frame, label, x, y + bh * 0.33, alpha=clamp(k * 1.4))


def _chat(frame: Image.Image, s: Scene, t: float, pal: dict, u: float, maxw: int) -> None:
    """A conversation: bubbles alternate sides as each line is spoken, with typing dots just before."""
    w, h = frame.size
    items = s.items[:5]
    n = max(1, len(items))
    gap = min(u * 0.26, (h * 0.74) / n)
    y0 = h * 0.2 + gap / 2
    for k, it in enumerate(items):
        left = k % 2 == 0
        p = pop(t, it.at)
        y = y0 + k * gap
        if 0 <= it.at - t < 0.5 and k > 0:  # typing dots
            for j in range(3):
                r = u * 0.014 * (1 + 0.5 * max(0.0, math.sin(t * 9 + j)))
                x = (w * 0.2 if left else w * 0.8) + (j - 1) * u * 0.04
                ImageDraw.Draw(frame).ellipse([x - r, y - r, x + r, y + r], fill=(*pal["soft"], 200))
        if p <= 0:
            continue
        bw = int(maxw * 0.78)
        label = fitted(it.text, EXTRA, (*(pal["ink"] if left else (20, 20, 36)), 255), int(bw * 0.78), int(gap * 0.62), int(u * 0.05),
                       "left")
        bh = int(max(u * 0.11, label.height + u * 0.05))
        fill = pal["card"] if left else (*pal["accent"], 255)
        cx = (bw / 2 + w * 0.06) if left else (w - bw / 2 - w * 0.06)
        slide = (1 - clamp(p, 0, 1)) * u * 0.18 * (-1 if left else 1)
        place(frame, card((bw, bh), int(bh * 0.4), fill), cx + slide, y, alpha=clamp(p * 1.5), scale=0.92 + 0.08 * clamp(p, 0, 1.1))
        em = emoji_sprite(it.emoji, int(bh * 0.7)) if it.emoji else None
        tx = cx - bw / 2 + u * 0.035 + slide
        if em is not None:
            place(frame, em, tx + em.width / 2, y, alpha=clamp(p * 1.5))
            tx += em.width + u * 0.02
        place(frame, label, tx, y, alpha=clamp(p * 1.5), anchor=(0.0, 0.5))


def _chip(frame: Image.Image, emoji: str, text: str, cx: float, cy: float, u: float, t: float, t0: float, pal: dict) -> None:
    k = pop(t, t0)
    if k <= 0:
        return
    label = text_sprite(text, EXTRA, int(u * 0.045), (*pal["ink"], 255))
    em = emoji_sprite(emoji, int(u * 0.07)) if emoji else None
    w = label.width + (em.width + u * 0.02 if em else 0) + u * 0.06
    bg = card((int(w), int(u * 0.11)), int(u * 0.055), pal["card"])
    place(frame, bg, cx, cy, scale=clamp(k, 0, 1.1), alpha=clamp(k * 1.5))
    x = cx - w * clamp(k, 0, 1) / 2 + u * 0.03
    if em:
        place(frame, em, x + em.width / 2, cy, scale=clamp(k, 0, 1.1), anchor=(0.5, 0.5))
        x += em.width + u * 0.02
    place(frame, label, x + label.width / 2, cy, alpha=clamp(k * 1.5), scale=clamp(k, 0, 1.05))


def _row(frame: Image.Image, it, cx: float, cy: float, u: float, t: float, pal: dict, maxw: int) -> None:
    k = pop(t, it.at)
    if k <= 0:
        return
    h = int(u * 0.16)
    bg = card((maxw, h), h // 2, pal["card"])
    slide = (1 - clamp(k, 0, 1)) * u * 0.25
    place(frame, bg, cx + slide, cy, alpha=clamp(k * 1.4))
    em = emoji_sprite(it.emoji or "✨", int(u * 0.11))
    if em is not None:
        place(frame, em, cx - maxw / 2 + h * 0.62 + slide, cy, scale=clamp(k, 0, 1.15))
    label = fitted(it.text, BLACK, (*pal["ink"], 255), int(maxw - h * 1.5), int(h * 0.8), int(u * 0.07), "left")
    place(frame, label, cx - maxw / 2 + h * 1.2 + slide, cy, alpha=clamp(k * 1.4), anchor=(0.0, 0.5))


def _steps(frame: Image.Image, s: Scene, t: float, pal: dict, u: float, vertical: bool) -> None:
    w, h = frame.size
    items = s.items[:4]
    n = max(1, len(items))
    d = ImageDraw.Draw(frame)
    pts = []
    for k in range(n):
        f = (k + 0.5) / n
        pts.append((w / 2, h * (0.25 + 0.6 * f)) if vertical else (w * (0.12 + 0.76 * f), h * 0.52))
    for k in range(n - 1):  # the arrow to the next step draws once that step is due
        nxt = items[k + 1].at
        prog = smooth((t - nxt + 0.4) / 0.4)
        if prog > 0:
            (x0, y0), (x1, y1) = pts[k], pts[k + 1]
            span = u * 0.17
            ux, uy = (x1 - x0), (y1 - y0)
            norm = math.hypot(ux, uy) or 1
            sx, sy, ex, ey = x0 + ux / norm * span, y0 + uy / norm * span, x1 - ux / norm * span, y1 - uy / norm * span
            cx, cy = sx + (ex - sx) * prog, sy + (ey - sy) * prog
            d.line([(sx, sy), (cx, cy)], fill=(*pal["accent"], 230), width=max(3, int(u * 0.012)))
            if prog > 0.95:
                a = math.atan2(ey - sy, ex - sx)
                for sgn in (-1, 1):
                    d.line([(ex, ey), (ex - math.cos(a + sgn * 0.5) * u * 0.04, ey - math.sin(a + sgn * 0.5) * u * 0.04)],
                           fill=(*pal["accent"], 230), width=max(3, int(u * 0.012)))
    for (x, y), it in zip(pts, items):
        k = pop(t, it.at)
        if k <= 0:
            continue
        r = u * 0.15
        place(frame, _disc(int(r * 2), pal["card"]), x, y, scale=clamp(k, 0, 1.12), alpha=clamp(k * 1.5))
        em = emoji_sprite(it.emoji or "✨", int(u * 0.15))
        if em is not None:
            place(frame, em, x, y, scale=clamp(k, 0, 1.15))
        label = fitted(it.text, BLACK, (*pal["ink"], 255), int(u * (0.5 if vertical else 0.3)), int(u * 0.12), int(u * 0.055))
        place(frame, label, x + (u * 0.0 if vertical else 0), y + r * 1.25 + label.height * 0.1, alpha=clamp(k * 1.5))


@lru_cache(maxsize=32)
def _disc(px: int, fill: tuple[int, ...]) -> Image.Image:
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([0, 0, px - 1, px - 1], fill=fill)
    return img


def _stat(frame: Image.Image, s: Scene, t: float, pal: dict, u: float) -> None:
    w, h = frame.size
    m = re.search(r"\d+(?:\.\d+)?", s.value or "")
    text = s.value or s.headline
    if m:
        target = float(m.group())
        decimals = len(m.group().split(".")[1]) if "." in m.group() else 0
        prog = smooth((t - s.start - 0.3) / 1.1)
        shown = f"{target * prog:.{decimals}f}"
        text = text[:m.start()] + shown + text[m.end():]
        denom = re.search(r"/\s*(\d+(?:\.\d+)?)", text)
        frac = clamp(prog * target / float(denom.group(1))) if denom else clamp(prog * (target / 100 if "%" in text else 0.75))
    else:
        frac = 0.75
    cx, cy, r = w / 2, h * 0.5, u * 0.34
    ring = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ring)
    width = int(u * 0.045)
    d.arc([cx - r, cy - r, cx + r, cy + r], 0, 360, fill=(255, 255, 255, 45), width=width)
    if frac > 0.002:
        d.arc([cx - r, cy - r, cx + r, cy + r], -90, -90 + 360 * frac, fill=(*pal["accent"], 255), width=width)
    frame.alpha_composite(ring)
    k = pop(t, s.start + 0.15)
    title_text(frame, text, cx, cy, int(r * 1.5), int(r * 1.1), int(u * 0.2), pal["ink"], t, s.start + 0.1, 0.0)
    if s.emoji:
        em = emoji_sprite(s.emoji, int(u * 0.12))
        if em is not None:
            place(frame, em, cx, cy - r * 1.3, scale=clamp(k, 0, 1.15), alpha=clamp(k * 1.5))


def _compare(frame: Image.Image, s: Scene, t: float, pal: dict, u: float, vertical: bool) -> None:
    w, h = frame.size
    items = s.items[:2]
    for k, it in enumerate(items):
        p = pop(t, it.at)
        if p <= 0:
            continue
        if vertical:
            cx, cy, cw, ch = w / 2, h * (0.32 + 0.3 * k), w * 0.8, h * 0.24
        else:
            cx, cy, cw, ch = w * (0.27 + 0.46 * k), h * 0.55, w * 0.4, h * 0.5
        bg = card((int(cw), int(ch)), int(u * 0.06), pal["card"])
        place(frame, bg, cx, cy, scale=clamp(p, 0, 1.08), alpha=clamp(p * 1.4))
        em = emoji_sprite(it.emoji or "✨", int(min(cw, ch) * 0.4))
        if em is not None:
            place(frame, em, cx, cy - ch * 0.12, scale=clamp(p, 0, 1.15))
        label = fitted(it.text, BLACK, (*pal["ink"], 255), int(cw * 0.86), int(ch * 0.3), int(u * 0.075))
        place(frame, label, cx, cy + ch * 0.32, alpha=clamp(p * 1.4))
    if len(items) == 2 and pop(t, items[1].at) > 0:
        vs = text_sprite("VS", BLACK, int(u * 0.075), (*pal["accent"], 255))
        place(frame, vs, w / 2, h * (0.47 if vertical else 0.55), scale=clamp(pop(t, items[1].at + 0.1), 0, 1.2))


# ---------------------------------------------------------------- overlays on a person's video

def draw_callout(frame: Image.Image, c: Callout, t: float, pal: dict) -> None:
    """A pill, a number or a title that pops over the video as the topic is spoken and leaves after a few seconds."""
    w, h = frame.size
    u, vertical = min(w, h), h > w
    k = pop(t, c.at, 0.4) * (1 - fade(t, c.until - 0.3, 0.3))
    if k <= 0.01:
        return
    dark = (12, 14, 30, 214)
    if c.kind == "title":
        sprite = fitted(c.text, BLACK, (255, 255, 255, 255), int(w * 0.8), int(h * 0.16), int(u * 0.12))
        bg = card((sprite.width + int(u * 0.1), sprite.height + int(u * 0.05)), int(u * 0.05), dark)
        cy = h * (0.13 if vertical else 0.14)
        place(frame, bg, w / 2, cy, scale=clamp(k, 0, 1.08), alpha=clamp(k * 1.5))
        place(frame, sprite, w / 2, cy, scale=clamp(k, 0, 1.05), alpha=clamp(k * 1.5))
        return
    x = w / 2 if vertical else w * 0.3
    y = h * (0.62 if vertical else 0.7)
    if c.kind == "stat":
        value = fitted(c.value or c.text, BLACK, (*pal["accent"], 255), int(w * 0.5), int(u * 0.2), int(u * 0.16))
        label = fitted(c.text, EXTRA, (255, 255, 255, 255), int(w * 0.5), int(u * 0.07), int(u * 0.05))
        bw, bh = max(value.width, label.width) + int(u * 0.14), value.height + label.height + int(u * 0.08)
        place(frame, card((bw, bh), int(u * 0.06), dark), x, y, scale=clamp(k, 0, 1.08), alpha=clamp(k * 1.5))
        place(frame, value, x, y - label.height * 0.5, scale=clamp(k, 0, 1.05), alpha=clamp(k * 1.5))
        place(frame, label, x, y + value.height * 0.42, scale=clamp(k, 0, 1.05), alpha=clamp(k * 1.5))
        return
    label = fitted(c.text, BLACK, (255, 255, 255, 255), int(w * 0.6), int(u * 0.1), int(u * 0.075))
    em = emoji_sprite(c.emoji, int(u * 0.12)) if c.emoji else None
    bw = label.width + (em.width + int(u * 0.03) if em else 0) + int(u * 0.1)
    bh = int(u * 0.17)
    place(frame, card((bw, bh), bh // 2, dark), x, y, scale=clamp(k, 0, 1.08), alpha=clamp(k * 1.5))
    left = x - bw * clamp(k, 0, 1) / 2 + u * 0.05
    if em is not None:
        place(frame, em, left + em.width / 2, y, scale=clamp(k, 0, 1.12))
        left += em.width + u * 0.03
    place(frame, label, left + label.width / 2, y, alpha=clamp(k * 1.5), scale=clamp(k, 0, 1.05))


def caption_pages(words: list[dict], per_page: int) -> list[list[dict]]:
    pages, cur = [], []
    for wd in words:
        cur.append(wd)
        if len(cur) >= per_page or re.search(r"[.!?]$", wd["text"].strip()):
            pages.append(cur)
            cur = []
    if cur:
        pages.append(cur)
    return pages


def draw_captions(frame: Image.Image, pages: list[list[dict]], t: float, pal: dict, y_frac: float) -> None:
    """The words being said, a few at a time, the current one lit up."""
    w, h = frame.size
    u = min(w, h)
    page = next((p for p in pages if p[0]["start"] - 0.05 <= t <= p[-1]["end"] + 0.25), None)
    if page is None:
        return
    px = int(u * 0.068)
    parts = []
    for wd in page:
        on = wd["start"] <= t
        lit = wd["start"] <= t <= wd["end"] + 0.08
        text = re.sub(r"^\W+|\W+$", "", wd["text"]) or wd["text"]
        parts.append((outlined(text.upper(), px, pal["accent"] if lit else (255, 255, 255)), on, lit))
    gap = int(px * 0.28)
    total = sum(p.width for p, _, _ in parts) + gap * (len(parts) - 1)
    x = w / 2 - total / 2
    for sprite, on, lit in parts:
        if on:
            place(frame, sprite, x + sprite.width / 2, h * y_frac, scale=1.1 if lit else 1.0, alpha=1.0)
        x += sprite.width + gap


# ---------------------------------------------------------------- the voice's loudness

def loudness(samples: np.ndarray, sr: int, fps: int) -> np.ndarray:
    """0-1 per video frame: how loud the voice is, smoothed (drives the pulse behind the hero picture)."""
    x = samples.mean(axis=1) if samples.ndim > 1 else samples
    hop = max(1, sr // fps)
    n = len(x) // hop
    if n == 0:
        return np.zeros(1)
    rms = np.sqrt(np.mean(x[:n * hop].reshape(n, hop) ** 2, axis=1))
    rms = rms / (np.percentile(rms, 95) + 1e-9)
    out = np.zeros(n)
    for i, v in enumerate(np.clip(rms, 0, 1)):
        out[i] = max(v, out[i - 1] * 0.82) if i else v
    return out
