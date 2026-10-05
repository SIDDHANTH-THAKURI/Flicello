"""Drawing helpers for the outro: bundled fonts, text sprites, smooth shapes and compositing.

Fonts are bundled in assets/fonts (SIL Open Font License): Fraunces for display lines, Nunito for
labels and numbers.
"""

from __future__ import annotations

import math
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from ..config import REPO_ROOT

FONT_DIR = REPO_ROOT / "assets" / "fonts"


@lru_cache(maxsize=64)
def font(name: str, px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / f"{name}.ttf"), max(6, int(px)))


def lighten(c: tuple[int, int, int], f: float) -> tuple[int, int, int]:
    return tuple(round(v + (255 - v) * f) for v in c)  # type: ignore[return-value]


def darken(c: tuple[int, int, int], f: float) -> tuple[int, int, int]:
    return tuple(round(v * (1 - f)) for v in c)  # type: ignore[return-value]


ARROW = "→"  # the bundled fonts have no arrow glyph, so text_sprite draws it
ARROW_EM = 1.05


def _line_width(text: str, f: ImageFont.FreeTypeFont, tracking: float) -> float:
    plain = text.replace(ARROW, "")
    return f.getlength(plain) + text.count(ARROW) * ARROW_EM * f.size + tracking * max(0, len(text) - 1)


def _draw_arrow(draw: ImageDraw.ImageDraw, x: float, y_mid: float, px: int, fill: tuple[int, ...]) -> None:
    w, t = ARROW_EM * px, max(1.0, px * 0.085)
    x0, x1 = x + 0.14 * px, x + w - 0.14 * px
    head = 0.26 * px
    draw.line([(x0, y_mid), (x1 - t, y_mid)], fill=fill, width=max(1, round(t)))
    draw.polygon([(x1, y_mid), (x1 - head, y_mid - head * 0.8), (x1 - head, y_mid + head * 0.8)], fill=fill)


def wrap(text: str, f: ImageFont.FreeTypeFont, max_width: float, tracking: float = 0.0) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            trial = f"{line} {word}".strip()
            if not line or _line_width(trial, f, tracking) <= max_width:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


@lru_cache(maxsize=512)
def text_sprite(text: str, font_name: str, px: int, color: tuple[int, ...], *, tracking: float = 0.0,
                max_width: int | None = None, align: str = "center", leading: float = 1.22) -> Image.Image:
    """Tight RGBA image of (possibly wrapped) text. tracking: extra space between letters, in em."""
    f = font(font_name, px)
    track = tracking * px
    lines = wrap(text, f, max_width, track) if max_width else text.split("\n")
    ascent, descent = f.getmetrics()
    step = round(px * leading)
    widths = [_line_width(line, f, track) for line in lines]
    pad = max(2, px // 6)
    w = math.ceil(max(widths)) + 2 * pad
    h = ascent + descent + step * (len(lines) - 1) + 2 * pad
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fill = color if len(color) == 4 else (*color, 255)
    for k, (line, lw) in enumerate(zip(lines, widths)):
        x = pad + {"left": 0, "right": w - 2 * pad - lw}.get(align, (w - 2 * pad - lw) / 2)
        y = pad + ascent + k * step
        for j, part in enumerate(line.split(ARROW)):
            if j:
                _draw_arrow(draw, x, y + f.getbbox("x", anchor="ls")[1] * 0.5, px, fill)  # mid x-height
                x += ARROW_EM * px
            if track:
                for ch in part:
                    draw.text((x, y), ch, font=f, fill=fill, anchor="ls")
                    x += f.getlength(ch) + track
            else:
                draw.text((x, y), part, font=f, fill=fill, anchor="ls")
                x += f.getlength(part)
    return img


def with_alpha(img: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.999:
        return img
    out = img.copy()
    out.putalpha(img.getchannel("A").point(lambda v: round(v * alpha)))
    return out


def place(frame: Image.Image, sprite: Image.Image, x: float, y: float, *, alpha: float = 1.0,
          scale: float = 1.0, anchor: tuple[float, float] = (0.5, 0.5)) -> None:
    """Alpha-composite sprite onto frame with its anchor point (fractions of its size) at (x, y)."""
    if alpha <= 0.004 or scale <= 0.01:
        return
    s = sprite
    if abs(scale - 1.0) > 1e-3:
        s = s.resize((max(1, round(s.width * scale)), max(1, round(s.height * scale))), Image.BICUBIC)
    s = with_alpha(s, alpha)
    left, top = round(x - anchor[0] * s.width), round(y - anchor[1] * s.height)
    # clip to the frame (alpha_composite wants a non-negative destination inside the image)
    sx, sy = max(0, -left), max(0, -top)
    right, bottom = min(frame.width, left + s.width), min(frame.height, top + s.height)
    if right <= left + sx or bottom <= top + sy:
        return
    frame.alpha_composite(s, dest=(left + sx, top + sy), source=(sx, sy, sx + right - (left + sx), sy + bottom - (top + sy)))


def capsule(draw: ImageDraw.ImageDraw, p0: tuple[float, float], p1: tuple[float, float], width: float,
            fill: tuple[int, ...]) -> None:
    """A thick line with round ends."""
    draw.line([p0, p1], fill=fill, width=max(1, round(width)))
    r = width / 2
    for x, y in (p0, p1):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def round_arc(draw: ImageDraw.ImageDraw, box: list[float], start: float, end: float, width: float,
              fill: tuple[int, ...]) -> None:
    """An elliptical arc (degrees, clockwise from 3 o'clock) with round ends."""
    draw.arc(box, start, end, fill=fill, width=max(1, round(width)))
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    rx, ry = (box[2] - box[0]) / 2 - width / 2, (box[3] - box[1]) / 2 - width / 2
    r = width / 2
    for a in (start, end):
        x, y = cx + rx * math.cos(math.radians(a)), cy + ry * math.sin(math.radians(a))
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def star_points(cx: float, cy: float, r_out: float, r_in: float, points: int = 5,
                rotation: float = -90.0) -> list[tuple[float, float]]:
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = math.radians(rotation + i * 180 / points)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def ease_out_back(p: float, s: float = 1.9) -> float:
    p = min(1.0, max(0.0, p)) - 1
    return p * p * ((s + 1) * p + s) + 1


def ease_out_cubic(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 1 - (1 - p) ** 3


def ease_in_out(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 0.5 - 0.5 * math.cos(math.pi * p)


def ramp(t: float, start: float, dur: float) -> float:
    """0 before start, 1 after start + dur, linear in between."""
    if dur <= 0:
        return 1.0 if t >= start else 0.0
    return min(1.0, max(0.0, (t - start) / dur))
