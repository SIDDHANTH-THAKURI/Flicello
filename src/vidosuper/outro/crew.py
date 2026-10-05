"""The crew: cute, gender-neutral characters for the agents that made the video, drawn with Pillow.

Everything is drawn at SS x size and downsampled, so edges stay smooth at any resolution. Coordinates
are sprite units: a body is ~90 units tall and stands on y = GROUND; a sprite is SPRITE_W x SPRITE_H.
Each character has a few animation poses (its prop moves) and a blink.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .draw import capsule, darken, font, lighten, round_arc, star_points

SS = 3
SPRITE_W, SPRITE_H = 200, 176
GROUND = 160
POSES = 6
INK = (38, 33, 46, 255)
WHITE = (255, 255, 255, 255)
CHEEK = (255, 120, 150)
PROP = 1.45  # props are drawn this much bigger than their sketch sizes, so they read when small


@dataclass(frozen=True)
class Body:
    a: float  # half width
    top: float  # height above the centre
    bottom: float  # depth below the centre
    n_top: float = 2.3  # superellipse exponents (2 = ellipse, higher = boxier)
    n_bottom: float = 3.0
    bumps: bool = False  # cloud-like top


@dataclass(frozen=True)
class Member:
    key: str
    name: str
    color: tuple[int, int, int]
    body: Body
    face: str  # happy | dreamy | focused | grumpy | wise
    prop: str  # magnifier | megaphone | scissors | cards | pen | gavel
    pose_rate: float = 0.6  # prop animation cycles per second
    role: str = ""
    note: str = ""
    scores: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # the Panel's cards

    @property
    def label_color(self) -> tuple[int, int, int]:
        return lighten(self.color, 0.25)


PANEL_COLORS = {"story": (255, 150, 170), "rhythm": (255, 196, 128), "hook": (128, 222, 190),
                "craft": (160, 176, 255), "sound": (204, 232, 120)}

CREW = (
    Member("analyst", "Scout", (64, 200, 182), Body(48, 44, 40), "happy", "magnifier", 0.3),
    Member("director", "Muse", (156, 124, 255), Body(40, 52, 42, 2.1, 2.8), "dreamy", "megaphone", 0.7),
    Member("editor", "Snip", (255, 122, 104), Body(44, 46, 42, 3.0, 3.2), "focused", "scissors", 1.2),
    Member("critics", "The Panel", (255, 176, 140), Body(19, 17, 16), "happy", "cards", 0.45),
    Member("adversary", "Grump", (128, 140, 172), Body(48, 38, 40, 2.4, 3.0, bumps=True), "grumpy", "pen", 0.55),
    Member("judge", "Judge", (242, 192, 78), Body(45, 45, 43, 4.2, 4.2), "wise", "gavel", 0.8),
)


class _Pad:
    """A supersampled RGBA canvas addressed in sprite units. A region is a small pad over part of a
    bigger one (same units), so soft effects (blurs) only cost what they cover."""

    def __init__(self, unit_px: float, w: float = SPRITE_W, h: float = SPRITE_H, origin: tuple[int, int] = (0, 0)):
        self.k = unit_px * SS
        self.origin = origin  # top-left of this pad in the parent's pixels
        self.size = (max(1, round(w * self.k)), max(1, round(h * self.k)))
        self.img = Image.new("RGBA", self.size, (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.img)

    def p(self, x: float, y: float) -> tuple[float, float]:
        return x * self.k - self.origin[0], y * self.k - self.origin[1]

    def box(self, x0: float, y0: float, x1: float, y1: float) -> list[float]:
        (a, b), (c, d) = self.p(x0, y0), self.p(x1, y1)
        return [a, b, c, d]

    def region(self, x0: float, y0: float, x1: float, y1: float) -> "_Pad":
        left, top = max(0, int(x0 * self.k)), max(0, int(y0 * self.k))
        right, bottom = min(self.size[0], math.ceil(x1 * self.k)), min(self.size[1], math.ceil(y1 * self.k))
        sub = _Pad(self.k / SS, 0, 0, (left, top))
        sub.size = (max(1, right - left), max(1, bottom - top))
        sub.img = Image.new("RGBA", sub.size, (0, 0, 0, 0))
        sub.draw = ImageDraw.Draw(sub.img)
        return sub

    def put(self, sub: "_Pad", img: Image.Image | None = None) -> None:
        self.img.alpha_composite(img if img is not None else sub.img, dest=sub.origin)
        self.draw = ImageDraw.Draw(self.img)

    def layer(self) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        img = Image.new("RGBA", self.size, (0, 0, 0, 0))
        return img, ImageDraw.Draw(img)

    def over(self, layer: Image.Image) -> None:
        self.img.alpha_composite(layer)
        self.draw = ImageDraw.Draw(self.img)

    def done(self) -> Image.Image:
        w, h = self.size
        return self.img.resize((max(1, round(w / SS)), max(1, round(h / SS))), Image.LANCZOS)


def _superellipse(cx: float, cy: float, b: Body, scale: float = 1.0, steps: int = 240) -> list[tuple[float, float]]:
    pts = []
    for i in range(steps):
        th = 2 * math.pi * i / steps
        c, s = math.cos(th), math.sin(th)
        n, r = (b.n_bottom, b.bottom) if s > 0 else (b.n_top, b.top)
        pts.append((cx + scale * b.a * math.copysign(abs(c) ** (2 / n), c),
                    cy + scale * r * math.copysign(abs(s) ** (2 / n), s)))
    return pts


def _body(pad: _Pad, cx: float, cy: float, b: Body, color: tuple[int, int, int], scale: float = 1.0) -> None:
    """Body silhouette with a soft top light, a highlight and a little shade at the bottom."""
    top = cy - (b.top + (14 if b.bumps else 0)) * scale
    bottom = cy + b.bottom * scale
    reg = pad.region(cx - b.a * scale - 2, top - 2, cx + b.a * scale + 2, bottom + 2)
    mask = Image.new("L", reg.size, 0)
    md = ImageDraw.Draw(mask)
    md.polygon([reg.p(x, y) for x, y in _superellipse(cx, cy, b, scale)], fill=255)
    if b.bumps:  # a cloud: puffs along the top edge
        for dx, dy, r in ((-30, -22, 17), (-9, -33, 19), (14, -31, 18), (33, -18, 15)):
            md.ellipse(reg.box(cx + dx * scale - r * scale, cy + dy * scale - r * scale,
                               cx + dx * scale + r * scale, cy + dy * scale + r * scale), fill=255)
    top_y, bot_y = reg.p(0, top)[1], reg.p(0, bottom)[1]
    w, h = reg.size
    ys = np.clip((np.arange(h, dtype=np.float32) - top_y) / max(1.0, bot_y - top_y), 0, 1)[:, None]
    hi, lo = np.array(lighten(color, 0.18), np.float32), np.array(darken(color, 0.16), np.float32)
    column = (hi * (1 - ys) + lo * ys).astype(np.uint8)  # (h, 3)
    fill = Image.fromarray(np.ascontiguousarray(np.broadcast_to(column[:, None, :], (h, w, 3))), "RGB").convert("RGBA")
    fill.putalpha(mask)

    glow = Image.new("L", reg.size, 0)
    ImageDraw.Draw(glow).ellipse(reg.box(cx - b.a * 0.66 * scale, cy - b.top * 0.86 * scale,
                                         cx - b.a * 0.02 * scale, cy - b.top * 0.30 * scale), fill=105)
    glow = ImageChops.multiply(glow.filter(ImageFilter.GaussianBlur(pad.k * 6 * scale)), mask)
    white = Image.new("RGBA", reg.size, (255, 255, 255, 0))
    white.putalpha(glow)
    fill.alpha_composite(white)

    shade = Image.new("L", reg.size, 0)
    ImageDraw.Draw(shade).ellipse(reg.box(cx - b.a * 0.9 * scale, cy + b.bottom * 0.55 * scale,
                                          cx + b.a * 0.9 * scale, cy + b.bottom * 1.25 * scale), fill=70)
    shade = ImageChops.multiply(shade.filter(ImageFilter.GaussianBlur(pad.k * 5 * scale)), mask)
    dark = Image.new("RGBA", reg.size, (*darken(color, 0.5), 0))
    dark.putalpha(shade)
    fill.alpha_composite(dark)
    pad.put(reg, fill)


def _eye(pad: _Pad, x: float, y: float, *, size: float = 1.0, look: float = 0.0, closed: str = "") -> None:
    d, k = pad.draw, pad.k
    w, h = 10.5 * size, 12.5 * size
    if closed == "blink":  # relaxed closed eye: a soft downward curve
        round_arc(d, pad.box(x - w * 0.6, y - h * 0.45, x + w * 0.6, y + h * 0.35), 20, 160, 2.3 * k * size, INK)
        return
    if closed == "happy":  # ^ shaped
        round_arc(d, pad.box(x - w * 0.6, y - h * 0.25, x + w * 0.6, y + h * 0.55), 200, 340, 2.4 * k * size, INK)
        return
    d.ellipse(pad.box(x - w / 2, y - h / 2, x + w / 2, y + h / 2), fill=INK)
    hx, hy, r = x + w * 0.16 + look, y - h * 0.18, w * 0.2
    d.ellipse(pad.box(hx - r, hy - r, hx + r, hy + r), fill=WHITE)
    sx, sy, r2 = x - w * 0.2 + look * 0.5, y + h * 0.2, w * 0.09
    d.ellipse(pad.box(sx - r2, sy - r2, sx + r2, sy + r2), fill=WHITE)


def _cheeks(pad: _Pad, cx: float, y: float, spread: float, alpha: int = 120, scale: float = 1.0) -> None:
    reg = pad.region(cx - spread - 9 * scale, y - 7 * scale, cx + spread + 9 * scale, y + 7 * scale)
    for side in (-1, 1):
        x = cx + side * spread
        reg.draw.ellipse(reg.box(x - 5.5 * scale, y - 3.2 * scale, x + 5.5 * scale, y + 3.2 * scale),
                         fill=(*CHEEK, alpha))
    pad.put(reg, reg.img.filter(ImageFilter.GaussianBlur(pad.k * 0.8 * scale)))


def _mouth(pad: _Pad, cx: float, y: float, kind: str, scale: float = 1.0) -> None:
    d, k = pad.draw, pad.k
    s = scale
    if kind == "open":
        d.chord(pad.box(cx - 5.5 * s, y - 4 * s, cx + 5.5 * s, y + 5 * s), 0, 180, fill=INK)
        d.chord(pad.box(cx - 3 * s, y + 0.8 * s, cx + 3 * s, y + 4.6 * s), 180, 360, fill=(255, 112, 130, 255))
    elif kind == "frown":
        round_arc(d, pad.box(cx - 6 * s, y, cx + 6 * s, y + 8 * s), 205, 335, 2.3 * k * s, INK)
    elif kind == "tongue":  # concentrating
        round_arc(d, pad.box(cx - 5 * s, y - 4 * s, cx + 5 * s, y + 2.5 * s), 30, 150, 2.2 * k * s, INK)
        d.ellipse(pad.box(cx + 2.5 * s, y + 0.2 * s, cx + 7 * s, y + 5.2 * s), fill=(255, 112, 130, 255))
    else:  # smile
        round_arc(d, pad.box(cx - 5.5 * s, y - 4.5 * s, cx + 5.5 * s, y + 2.5 * s), 25, 155, 2.3 * k * s, INK)


def _nub(pad: _Pad, x: float, y: float, color: tuple[int, int, int], scale: float = 1.0) -> None:
    pad.draw.ellipse(pad.box(x - 5.5 * scale, y - 4.5 * scale, x + 5.5 * scale, y + 4.5 * scale),
                     fill=(*darken(color, 0.08), 255))


def _face(pad: _Pad, m: Member, cx: float, cy: float, blink: bool, look: float = 0.0, scale: float = 1.0) -> float:
    """Draws eyes, cheeks and mouth; returns the eye line y."""
    s = scale
    ey = cy - 5 * s
    closed = "happy" if m.face == "dreamy" else ("blink" if blink else "")
    if m.prop == "magnifier":  # the right eye is drawn big behind the lens
        _eye(pad, cx - 14 * s, ey, size=s, look=look, closed=closed)
    else:
        for side in (-1, 1):
            _eye(pad, cx + side * 14 * s, ey, size=s, look=look, closed=closed)
    if m.face == "grumpy":
        for side in (-1, 1):
            capsule(pad.draw, pad.p(cx + side * 20 * s, ey - 10.5 * s), pad.p(cx + side * 7.5 * s, ey - 7 * s),
                    2.8 * pad.k * s, INK)
    _cheeks(pad, cx, ey + 9 * s, 22 * s, 70 if m.face == "grumpy" else 125, s)
    mouth = {"dreamy": "open", "grumpy": "frown", "focused": "tongue"}.get(m.face, "smile")
    _mouth(pad, cx, ey + 11 * s, mouth, s)
    return ey


# ---- props ---------------------------------------------------------------------------------------

def _magnifier(pad: _Pad, m: Member, cx: float, ey: float, pose: int, blink: bool) -> None:
    d, k = pad.draw, pad.k
    lx, ly, r = cx + 15, ey + 1, 15.5
    look = 1.4 * math.sin(2 * math.pi * pose / POSES)
    # handle and the hand holding it
    a = math.radians(50)
    h0 = (lx + (r + 1) * math.cos(a), ly + (r + 1) * math.sin(a))
    h1 = (h0[0] + 19 * math.cos(a), h0[1] + 19 * math.sin(a))
    capsule(d, pad.p(*h0), pad.p(*h1), 6.4 * k, (140, 92, 58, 255))
    _nub(pad, h1[0] - 3, h1[1] - 3, m.color)
    # the magnified eye inside the lens
    _eye(pad, lx, ly, size=1.6, look=look * 1.5, closed="blink" if blink else "")
    lens = pad.region(lx - r - 2, ly - r - 2, lx + r + 2, ly + r + 2)
    lens.draw.ellipse(lens.box(lx - r, ly - r, lx + r, ly + r), fill=(205, 238, 255, 60))
    round_arc(lens.draw, lens.box(lx - r + 4, ly - r + 4, lx + r - 4, ly + r - 4), 200, 250, 2.0 * k,
              (255, 255, 255, 210))
    pad.put(lens)
    d = pad.draw
    d.ellipse(pad.box(lx - r, ly - r, lx + r, ly + r), outline=(242, 234, 216, 255), width=round(3.8 * k))
    d.ellipse(pad.box(lx - r + 3.3, ly - r + 3.3, lx + r - 3.3, ly + r - 3.3), outline=(196, 184, 160, 255),
              width=max(1, round(0.9 * k)))


def _megaphone(pad: _Pad, m: Member, cx: float, cy: float, pose: int) -> None:
    k = pad.k * PROP
    w, h = 44, 30
    layer = Image.new("RGBA", (round(w * k), round(h * k)), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    q = lambda x, y: (x * k, y * k)  # noqa: E731 - local unit helper
    d.rounded_rectangle([*q(2, 11), *q(9, 19)], radius=2 * k, fill=(70, 66, 84, 255))  # mouthpiece
    d.polygon([q(8, 11.5), q(34, 3), q(34, 27), q(8, 18.5)], fill=(246, 241, 232, 255))  # cone
    d.polygon([q(22, 7), q(27, 5.4), q(27, 24.6), q(22, 23)], fill=(255, 104, 104, 255))  # red band
    d.ellipse([*q(31, 2.5), *q(37, 27.5)], fill=(214, 206, 194, 255))  # bell rim
    d.ellipse([*q(32.5, 5), *q(36, 25)], fill=(92, 86, 104, 255))  # opening
    d.rounded_rectangle([*q(12, 17), *q(16, 25)], radius=1.5 * k, fill=(70, 66, 84, 255))  # handle
    bob = 1.5 * math.sin(2 * math.pi * pose / POSES)
    rot = layer.rotate(28 + bob * 2, resample=Image.BICUBIC, expand=True)
    gx, gy = cx + m.body.a - 4, cy + 8 + bob
    pad.img.alpha_composite(rot, dest=(round(gx * pad.k - rot.width * 0.30), round(gy * pad.k - rot.height * 0.62)))
    pad.draw = ImageDraw.Draw(pad.img)
    _nub(pad, gx + 1, gy + 4, m.color)


def _antenna(pad: _Pad, m: Member, cx: float, cy: float, pose: int) -> None:
    d, k = pad.draw, pad.k
    top = cy - m.body.top
    p0, p1, p2 = (cx - 2, top + 3), (cx - 4, top - 14), (cx - 15, top - 22)
    pts = [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
            (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]) for t in np.linspace(0, 1, 24)]
    d.line([pad.p(*p) for p in pts], fill=(*darken(m.color, 0.25), 255), width=round(2.4 * k), joint="curve")
    pulse = 0.85 + 0.3 * (0.5 + 0.5 * math.sin(2 * math.pi * pose / POSES))
    sx, sy = p2
    reg = pad.region(sx - 24, sy - 24, sx + 24, sy + 24)
    reg.draw.polygon([reg.p(*p) for p in star_points(sx, sy, 13 * pulse, 6 * pulse)], fill=(255, 214, 120, 120))
    pad.put(reg, reg.img.filter(ImageFilter.GaussianBlur(k * 3)))
    pad.draw.polygon([pad.p(*p) for p in star_points(sx, sy, 8 * pulse, 3.6 * pulse)], fill=(255, 216, 112, 255))


def _scissors(pad: _Pad, m: Member, cx: float, cy: float, pose: int) -> None:
    d, k = pad.draw, pad.k
    px, py = cx + m.body.a + 6, cy + 6
    open_deg = 5 + 22 * (0.5 + 0.5 * math.cos(2 * math.pi * pose / POSES))
    base = -48  # blades point up and to the right
    for side in (-1, 1):
        a = math.radians(base + side * open_deg / 2)
        tip = (px + 38 * math.cos(a), py + 38 * math.sin(a))
        nx, ny = -math.sin(a), math.cos(a)
        blade = [(px + nx * 3.6, py + ny * 3.6), (tip[0] + nx * 0.5, tip[1] + ny * 0.5),
                 (tip[0] - nx * 0.5, tip[1] - ny * 0.5), (px - nx * 3.6, py - ny * 3.6)]
        d.polygon([pad.p(*q) for q in blade], fill=(226, 232, 242, 255), outline=(170, 178, 194, 255))
        ha = math.radians(base + 180 - side * open_deg / 2 - side * 16)
        hx, hy = px + 13 * math.cos(ha), py + 13 * math.sin(ha)
        d.ellipse(pad.box(hx - 7.4, hy - 7.4, hx + 7.4, hy + 7.4), outline=(58, 140, 255, 255), width=round(4.2 * k))
    d.ellipse(pad.box(px - 2.6, py - 2.6, px + 2.6, py + 2.6), fill=(150, 156, 170, 255))
    _nub(pad, px - 12, py + 9, m.color)


def _pen(pad: _Pad, m: Member, cx: float, cy: float, pose: int) -> None:
    d, k = pad.draw, pad.k
    p0, p1 = (cx + m.body.a - 8, cy + 18), (cx + m.body.a + 20, cy - 16)
    capsule(d, pad.p(*p0), pad.p(*p1), 7.5 * k, (66, 72, 92, 255))
    a = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    ux, uy, nx, ny = math.cos(a), math.sin(a), -math.sin(a), math.cos(a)
    tip = (p1[0] + ux * 11, p1[1] + uy * 11)
    d.polygon([pad.p(p1[0] + nx * 3.7, p1[1] + ny * 3.7), pad.p(*tip), pad.p(p1[0] - nx * 3.7, p1[1] - ny * 3.7)],
              fill=(255, 84, 84, 255))
    band = (p0[0] + (p1[0] - p0[0]) * 0.62, p0[1] + (p1[1] - p0[1]) * 0.62)
    capsule(d, pad.p(band[0] - ux * 1.6, band[1] - uy * 1.6), pad.p(band[0] + ux * 1.6, band[1] + uy * 1.6),
            7.6 * k, (255, 84, 84, 255))
    _nub(pad, p0[0] + 2, p0[1] - 1, m.color)
    # a red cross mark that pops in and out: the adversary found something
    phase = (pose + 0.5) / POSES
    if phase < 0.6:
        s = 1.0 if phase > 0.15 else phase / 0.15
        mx, my, r = cx + m.body.a + 24, cy - m.body.top - 2, 6.5 * s
        for sgn in (-1, 1):
            capsule(d, pad.p(mx - r, my - r * sgn), pad.p(mx + r, my + r * sgn), 3.6 * k * max(s, 0.3),
                    (255, 84, 84, 255))


def _gavel(pad: _Pad, m: Member, cx: float, cy: float, pose: int) -> None:
    k = pad.k * PROP
    w, h = 64, 48
    gx0, gy0 = 32, 44  # grip point in the gavel's own canvas
    layer = Image.new("RGBA", (round(w * k), round(h * k)), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    q = lambda x, y: (x * k, y * k)  # noqa: E731 - local unit helper
    capsule(d, q(gx0, gy0), q(gx0, 16), 4.2 * k, (156, 102, 62, 255))
    d.rounded_rectangle([*q(18, 4), *q(46, 17)], radius=3.2 * k, fill=(120, 76, 46, 255))
    for x in (21, 41):
        d.rectangle([*q(x, 4), *q(x + 2.4, 17)], fill=(182, 128, 76, 255))
    phase = pose / POSES  # slowly raise, then tap
    angle = -38 * (phase / 0.75) if phase < 0.75 else -38 * (1 - (phase - 0.75) / 0.25)
    rot = layer.rotate(-angle - 18, resample=Image.BICUBIC, center=q(gx0, gy0))
    hx, hy = cx + m.body.a + 2, cy + 14
    pad.img.alpha_composite(rot, dest=(round(hx * pad.k - gx0 * k), round(hy * pad.k - gy0 * k)))
    pad.draw = ImageDraw.Draw(pad.img)
    _nub(pad, hx, hy - 1, m.color)


def _glasses(pad: _Pad, cx: float, ey: float) -> None:
    d, k = pad.draw, pad.k
    for side in (-1, 1):
        x = cx + side * 14
        d.ellipse(pad.box(x - 9, ey - 8.5, x + 9, ey + 8.5), outline=(43, 39, 51, 255), width=round(1.9 * k))
    round_arc(d, pad.box(cx - 5, ey - 4, cx + 5, ey + 4), 200, 340, 1.9 * k, (43, 39, 51, 255))


# ---- characters -----------------------------------------------------------------------------------

def _panel(pad: _Pad, m: Member, pose: int, blink: bool) -> None:
    """Five small critics in a huddle, each holding up a card with their score."""
    scores = list(m.scores) or [(k, "?") for k in PANEL_COLORS]
    n = len(scores)
    spacing, centre, half = 25.0, 92.0, max(1.0, (len(scores) - 1) / 2)
    num_font = font("Nunito-Black", round(9 * pad.k))
    for i in sorted(range(n), key=lambda i: -abs(i - (n - 1) / 2)):  # outer ones behind the middle ones
        rubric, score = scores[i]
        x = centre + (i - (n - 1) / 2) * spacing
        rise = 6.0 * (1 - abs(i - (n - 1) / 2) / half)  # a gentle arch, the middle one tallest
        color = PANEL_COLORS.get(rubric, m.color)
        body = Body(15.5, 14.5, 13.5)
        cy = GROUND - body.bottom - rise
        lift = 1.6 * math.sin(2 * math.pi * (pose / POSES + i * 0.21))
        card_y = cy - 36 - rise * 0.6 + lift
        capsule(pad.draw, pad.p(x, cy - 6), pad.p(x, card_y + 5), 1.7 * pad.k, (214, 200, 176, 255))
        _body(pad, x, cy, body, color)
        pad.draw.rounded_rectangle(pad.box(x - 10.5, card_y - 7, x + 10.5, card_y + 7), radius=2.4 * pad.k,
                                   fill=(252, 249, 242, 255))
        pad.draw.text(pad.p(x, card_y + 0.6), score, font=num_font, fill=INK, anchor="mm")
        ey = cy - 2
        for side in (-1, 1):
            _eye(pad, x + side * 5.4, ey, size=0.45, closed="blink" if blink and i % 2 == 0 else "")
        _cheeks(pad, x, ey + 4.5, 9, 120, 0.45)
        _mouth(pad, x, ey + 5.4, "smile", 0.45)
        _nub(pad, x + 1.5, cy - 5, color, 0.55)


def draw_member(m: Member, unit_px: float, pose: int = 0, blink: bool = False) -> Image.Image:
    """The character as an RGBA sprite (SPRITE_W x SPRITE_H units), standing on GROUND."""
    pad = _Pad(unit_px)
    if m.prop == "cards":
        _panel(pad, m, pose, blink)
        return pad.done()
    cx, cy = 86, GROUND - m.body.bottom
    if m.prop == "megaphone":
        _antenna(pad, m, cx, cy, pose)
    _body(pad, cx, cy, m.body, m.color)
    look = 1.4 * math.sin(2 * math.pi * pose / POSES) if m.prop == "magnifier" else 0.0
    ey = _face(pad, m, cx, cy, blink, look)
    if m.face == "wise":
        _glasses(pad, cx, ey)
    {"magnifier": lambda: _magnifier(pad, m, cx, ey, pose, blink),
     "megaphone": lambda: _megaphone(pad, m, cx, cy, pose),
     "scissors": lambda: _scissors(pad, m, cx, cy, pose),
     "pen": lambda: _pen(pad, m, cx, cy, pose),
     "gavel": lambda: _gavel(pad, m, cx, cy, pose)}.get(m.prop, lambda: None)()
    return pad.done()


def shadow_sprite(unit_px: float, width_units: float = 92) -> Image.Image:
    k = unit_px * SS
    w, h = round((width_units + 24) * k), round(22 * k)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([12 * k, 7 * k, w - 12 * k, h - 7 * k], fill=(0, 0, 0, 120))
    img = img.filter(ImageFilter.GaussianBlur(3 * k))
    return img.resize((max(1, round(w / SS)), max(1, round(h / SS))), Image.LANCZOS)
