"""Designed talking videos: the person cut out in front of a stage, designer animations behind them that land on their
words, titles typed on, counts, stacks, and bold captions. Their own sound is untouched.

Where things go is worked out from where the person really is: their outline (from the cut-out, smoothed over time) leaves
a free zone above their head or beside them, and the main animation fills that zone. One main animation at a time; a new
one replaces the last with a spring. Text sits at the edge of the zone. Without the cut-out model the same graphics are
drawn over the original video in the top band, on a dark gradient so they read.
"""

from __future__ import annotations

import math
import re
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFilter

from ..ffmpeg import find_tool
from ..outro.draw import place, text_sprite
from . import cutout
from .draw import emoji_sprite, outlined
from .lottie_lib import Library
from .script import Callout, VoiceScript, respell_words

FPS = 30
Progress = Callable[[float], None]

STAGES = {  # stage colours per palette: top, bottom, fog, rim light, accent, text, chip
    "midnight": ((12, 12, 30), (30, 20, 72), (46, 40, 90), (120, 140, 255), (255, 70, 90), (250, 248, 255), (18, 20, 46, 228)),
    "berry": ((26, 8, 34), (70, 18, 70), (90, 40, 90), (255, 120, 200), (255, 210, 100), (255, 246, 252), (40, 14, 46, 228)),
    "sunrise": ((40, 16, 30), (120, 46, 50), (120, 70, 60), (255, 190, 120), (255, 230, 120), (255, 250, 240), (60, 22, 30, 228)),
    "mint": ((6, 26, 28), (14, 70, 64), (40, 90, 80), (120, 255, 210), (255, 220, 110), (240, 255, 250), (10, 36, 36, 228)),
    "paper": ((22, 22, 30), (52, 52, 70), (70, 70, 90), (255, 220, 160), (255, 120, 90), (250, 250, 250), (28, 28, 40, 228)),
}


def clamp(x: float, a: float = 0.0, b: float = 1.0) -> float:
    return max(a, min(b, x))


def ease(x: float) -> float:
    x = clamp(x)
    return 1 - (1 - x) ** 3


def spring(x: float, k: float = 1.7) -> float:
    x = clamp(x)
    return 1 + (k + 1) * (x - 1) ** 3 + k * (x - 1) ** 2


def life(t: float, t0: float, t1: float | None = None, grow: float = 0.38, out: float = 0.3) -> tuple[float, float]:
    """(scale, visibility) of something shown from t0 to t1: springs in, eases out."""
    if t < t0:
        return 0.0, 0.0
    s, v = spring((t - t0) / grow), clamp((t - t0) / (grow * 0.6))
    if t1 is not None and t > t1:
        o = clamp((t - t1) / out)
        s, v = s * (1 - 0.25 * ease(o)), v * (1 - o)
    return s, v


def card(w: int, h: int, r: int, fill) -> Image.Image:
    img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, max(1, w) - 1, max(1, h) - 1], radius=r, fill=fill)
    return img


def shadow_of(sprite: Image.Image, blur: int = 16, alpha: int = 140) -> Image.Image:
    a = sprite.getchannel("A").filter(ImageFilter.GaussianBlur(blur)).point(lambda v: v * alpha // 255)
    out = Image.new("RGBA", sprite.size, (0, 0, 0, 0))
    out.putalpha(a)
    return out


def rounded(img: Image.Image, r: int) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=r, fill=255)
    out = img.copy()
    out.putalpha(Image.fromarray(np.minimum(np.asarray(img.getchannel("A")), np.asarray(mask))))
    return out


def blend(base: np.ndarray, layer: Image.Image) -> np.ndarray:
    """An RGBA layer over an RGB float image, only where the layer has something (it is mostly empty)."""
    bbox = layer.getbbox()
    if bbox is None:
        return base
    x0, y0, x1, y1 = bbox
    a = np.asarray(layer.crop(bbox), dtype=np.float32)
    al = np.ascontiguousarray(a[..., 3] / 255.0)
    region = np.ascontiguousarray(base[y0:y1, x0:x1])
    base[y0:y1, x0:x1] = cv2.blendLinear(region, np.ascontiguousarray(a[..., :3]), 1.0 - al, al)
    return base


class Stage:
    """The backdrop: a gradient in the palette's colours, slow fog, a vignette. Drawn at quarter size."""

    def __init__(self, size: tuple[int, int], palette: str):
        self.w, self.h = size
        self.top, self.bottom, self.fog, *_ = STAGES.get(palette, STAGES["midnight"])
        rng = np.random.default_rng(7)
        sw, sh = self.w // 4, self.h // 4
        self.fogs = [cv2.resize(cv2.GaussianBlur(rng.random((40, 24)).astype(np.float32), (0, 0), 2.4), (sw, sh)) for _ in range(2)]
        yy, xx = np.mgrid[0:sh, 0:sw]
        self.vig = (np.clip(1.25 - np.sqrt(((xx - sw / 2) / (sw / 2)) ** 2 * 0.7 + ((yy - sh / 2.2) / (sh / 1.25)) ** 2), 0, 1)
                    ** 1.3)[..., None]
        g = np.linspace(0, 1, sh)[:, None, None]
        self.grad = np.array(self.top, np.float32) * (1 - g) + np.array(self.bottom, np.float32) * g

    def frame(self, t: float, level: float) -> np.ndarray:
        sw = self.w // 4
        img = self._base(t, level)
        for k in range(26):  # slow particles drifting up, a little depth
            seed = (k * 7919) % 997
            x = (seed * 0.61 + 13 * math.sin(t * 0.21 + k)) % sw
            y = (self.h // 4) * (1 - ((t * (0.012 + 0.004 * (k % 5)) + seed / 997) % 1.0))
            r = 0.6 + (k % 3) * 0.5
            cv2.circle(img, (int(x), int(y)), int(round(r)), tuple(int(c * 0.9) for c in self.fog), -1, lineType=cv2.LINE_AA)
        return cv2.resize(img, (self.w, self.h), interpolation=cv2.INTER_LINEAR).astype(np.float32)

    def _base(self, t: float, level: float) -> np.ndarray:
        sw = self.w // 4
        f1 = np.roll(self.fogs[0], int(t * 3) % sw, axis=1)
        f2 = np.roll(self.fogs[1], -int(t * 2) % (self.h // 4), axis=0)
        fog = (0.55 * f1 + 0.45 * f2)[..., None] * np.array(self.fog, np.float32) * (0.85 + 0.3 * level)
        return np.clip((self.grad + fog) * self.vig, 0, 255).astype(np.uint8)


class Zones:
    """Where the graphics can go, from the person's outline, smoothed so nothing jumps."""

    def __init__(self, size: tuple[int, int]):
        self.w, self.h = size
        self.box = None

    def update(self, m: np.ndarray | None) -> None:
        b = cutout.box(m) if m is not None else None
        if b is None:
            b = (0.2, 0.35, 0.8, 1.0)
        b = np.array(b, np.float32)
        self.box = b if self.box is None else 0.92 * self.box + 0.08 * b

    @property
    def head(self) -> tuple[float, float]:
        """Roughly the middle of the head, in pixels."""
        left, top, right, _ = self.box
        return (left + right) / 2 * self.w, (top + 0.11) * self.h

    def hero(self, title_space: float) -> tuple[float, float, float, float]:
        """The main animation's box (cx, cy, max width, max height) in pixels."""
        left, top, right, _ = self.box
        y0, y1 = 0.02 + title_space, top + 0.07  # it may sink behind the top of the head: it is behind them
        if y1 - y0 >= 0.18:  # room above the head
            return self.w * 0.5, self.h * (y0 + y1) / 2, self.w * 0.86, self.h * (y1 - y0)
        gap_l, gap_r = left, 1 - right
        if max(gap_l, gap_r) >= 0.24:  # room beside them
            x0, x1 = (0.02, left - 0.01) if gap_l >= gap_r else (right + 0.01, 0.98)
            return self.w * (x0 + x1) / 2, self.h * 0.3, self.w * (x1 - x0), self.h * 0.3
        return self.w * 0.5, self.h * (0.02 + title_space + 0.12), self.w * 0.8, self.h * 0.24  # behind the head


def join_numbers(words: list[dict]) -> list[dict]:
    """Speech-to-text splits '7.5' into '7' and '.5' (and '40' '%'): put them back together for the captions."""
    out: list[dict] = []
    for w in words:
        text = w["text"].strip()
        if out and re.match(r"^([.,]\d|%)", text) and re.search(r"\d$", out[-1]["text"].strip()):
            out[-1] = {**out[-1], "text": out[-1]["text"].strip() + text, "end": w["end"]}
        else:
            out.append(w)
    return out


class Designer:
    """Draws one frame of a designed talking video."""

    def __init__(self, script: VoiceScript, words: list[dict], size: tuple[int, int], lib: Library | None,
                 levels: np.ndarray, cut: bool, plates_dir: Path | None = None, duration: float = 0.0):
        from .backdrops import Plates, shots

        self.s, self.words, self.size, self.lib, self.levels, self.cut = script, join_numbers(words), size, lib, levels, cut
        self.w, self.h = size
        self.plates = Plates(plates_dir, size, "9:16" if self.h > self.w else "16:9") if plates_dir else None
        from . import draw as _draw
        from .backdrops import Props

        _draw.PROPS = Props(plates_dir) if plates_dir else None  # the cutaway scenes' painted props
        self.shots = shots(script)
        self.duration = max(1.0, duration)
        pal = STAGES.get(script.palette, STAGES["midnight"])
        self.rim, self.accent, self.ink, self.chip_fill = pal[3], pal[4], pal[5], pal[6]
        self.stage = Stage(size, script.palette)
        self.zones = Zones(size)
        self.tracker = cutout.Tracker() if cut else None
        self.heroes = [c for c in script.callouts if c.kind == "scene"]
        self.labels: list = []
        from . import draw as _draw

        if lib is not None:  # the cutaway scenes draw their main picture with these animations
            _draw.LOTTIE = lambda aid, tt, width: (lot.frame(tt + float(lib.item(aid).get("lead", 0.0)), width)
                                                   if (lot := lib.lot(aid)) is not None else None)
            _draw.LOTTIE_ASPECT = lambda aid: (lot.aspect if (lot := lib.lot(aid)) is not None else None)
        self.titles = [c for c in script.callouts if c.kind == "title"]

    # ---------------------------------------------------------- pieces
    def backdrop(self, t: float, level: float) -> np.ndarray:
        """What stands behind the person: the look's painted place (out of focus, the camera creeping in over the
        whole video), else the drawn stage."""
        if self.plates is not None and self.plates.has(self.s.stage_file):
            img = self.plates.array(self.s.stage_file, "push", t / self.duration, t, soft=True)
            if img is not None:
                return img.astype(np.float32)
        return self.stage.frame(t, level)

    def glow(self, level: float) -> Image.Image:
        """The soft light behind the head, breathing with the voice. Its size moves in 4-pixel steps and its strength
        in 6 steps of 255, so the few dozen blurs it takes are made once (blurring it every frame cost 29 ms)."""
        r = int(self.w * (0.33 + 0.05 * level)) // 4 * 4
        a = int(70 + 50 * level) // 6 * 6
        cache = self.__dict__.setdefault("_glows", {})
        if (r, a) not in cache:
            g = Image.new("RGBA", (2 * r, 2 * r), (0, 0, 0, 0))
            ImageDraw.Draw(g).ellipse([0, 0, 2 * r - 1, 2 * r - 1], fill=(*self.rim, a))
            cache[(r, a)] = g.filter(ImageFilter.GaussianBlur(r * 0.35))
        return cache[(r, a)]

    def lottie(self, aid: str, t: float, width: int, once: bool = False) -> Image.Image | None:
        if not aid or self.lib is None:
            return None
        lot = self.lib.lot(aid)
        if lot is None:
            return None
        img = lot.frame(t + float(self.lib.item(aid).get("lead", 0.0)), width, once=once)
        if self.lib.item(aid).get("filled"):
            img = rounded(img, max(8, width // 14))
        return img

    def fit(self, aid: str, t: float, maxw: float, maxh: float, once: bool = False) -> Image.Image | None:
        """The animation at the largest size that fits the box (None without one)."""
        if aid and self.lib is not None and (lot := self.lib.lot(aid)) is not None:
            width = int(min(maxw, maxh / max(0.05, lot.aspect)))
            return self.lottie(aid, t, width, once)
        return None

    def kinetic(self, layer: Image.Image, text: str, emoji: str, t0: float, t: float, cx: float, cy: float, mw: float,
                mh: float, fade: float) -> None:
        """When no animation fits: the words themselves, big, each springing in, the longest in the accent colour."""
        words = text.upper().split()[:5]
        if not words:
            return
        px = int(self.w * 0.12)
        lines: list[list[str]] = [[]]
        while True:
            lines = [[]]
            for wd in words:
                trial = " ".join(lines[-1] + [wd])
                if lines[-1] and text_sprite(trial, "Nunito-Black", px, (255, 255, 255, 255)).width > mw:
                    lines.append([wd])
                else:
                    lines[-1].append(wd)
            if len(lines) * px * 1.15 <= mh * 0.8 or px < 40:
                break
            px = int(px * 0.88)
        longest = max(words, key=len)
        y = cy - (len(lines) - 1) * px * 0.58
        k = 0
        for line in lines:
            sprites = [text_sprite(wd, "Nunito-Black", px, (*(self.accent if wd == longest else self.ink), 255)) for wd in line]
            gap = px * 0.25
            x = cx - (sum(s.width for s in sprites) + gap * (len(sprites) - 1)) / 2
            for sp in sprites:
                s, v = life(t, t0 + k * 0.09, None, 0.35)
                place(layer, shadow_of(sp, 10, 120), x + sp.width / 2 + 6, y + 10, scale=min(s, 1.12), alpha=v * fade * 0.8)
                place(layer, sp, x + sp.width / 2, y + (1 - min(s, 1)) * px * 0.4, scale=min(s, 1.12), alpha=v * fade)
                x += sp.width + gap
                k += 1
            y += px * 1.15
        if emoji and (em := emoji_sprite(emoji, int(px * 0.9))) is not None:
            s, v = life(t, t0, None, 0.4)
            place(layer, em, cx, cy - (len(lines) - 1) * px * 0.58 - px * 1.05, scale=min(s, 1.1), alpha=v * fade)

    def put(self, layer: Image.Image, sprite: Image.Image | None, x: float, y: float, s: float, v: float) -> None:
        if sprite is None or v <= 0.01 or s <= 0.02:
            return
        place(layer, shadow_of(sprite), x + 10, y + 16, scale=s, alpha=v * 0.75)
        place(layer, sprite, x, y, scale=s, alpha=v)

    def chip(self, text: str, px: int, fill=None, color=None) -> Image.Image:
        label = text_sprite(text, "Nunito-Black", px, (*(color or self.ink), 255), tracking=0.06)
        bg = card(label.width + int(px * 1.1), label.height + int(px * 0.45), int(px * 0.6), fill or self.chip_fill)
        bg.alpha_composite(label, ((bg.width - label.width) // 2, (bg.height - label.height) // 2))
        return bg

    def title(self, layer: Image.Image, c: Callout, t: float) -> float:
        """Letters spring in one by one at the top; returns the height it takes (fraction of the frame)."""
        text = (c.text or c.value).upper()
        if not text:
            return 0.0
        out = 1 - clamp((t - c.until) / 0.3)
        px = int(self.w * 0.1)
        while px > 30 and text_sprite(text, "Nunito-Black", px, (255, 255, 255, 255)).width > self.w * 0.9:
            px = int(px * 0.9)
        widths = [text_sprite(ch, "Nunito-Black", px, (255, 255, 255, 255)).width for ch in text]
        tight = 0.06 * px
        x = self.w / 2 - (sum(widths) - tight * (len(text) - 1)) / 2
        for k, (ch, cw) in enumerate(zip(text, widths)):
            kk, vv = life(t, c.at + k * 0.04, None, 0.3)
            if vv and ch.strip():
                col = self.accent if k < (len(text) + 1) // 2 else self.ink
                sp = text_sprite(ch, "Nunito-Black", px, (*col, 255))
                place(layer, sp, x + cw / 2, self.h * 0.045 + (1 - min(kk, 1)) * 40, scale=min(kk, 1.15), alpha=vv * out)
            x += cw - tight
        bar = ease((t - c.at - 0.35) / 0.5) * out
        if bar > 0:
            bw = self.w * 0.45 * bar
            ImageDraw.Draw(layer).rounded_rectangle([self.w / 2 - bw / 2, self.h * 0.078, self.w / 2 + bw / 2, self.h * 0.083],
                                                    6, fill=(*self.accent, 255))
        return 0.07

    # ---------------------------------------------------------- the frame
    def behind(self, t: float, level: float) -> Image.Image:
        layer = Image.new("RGBA", self.size, (0, 0, 0, 0))
        self.labels = []
        hx, hy = self.zones.head
        amb = self.lottie(self.s.ambient_lottie, t, int(self.w * 0.95)) if self.s.ambient_lottie else None
        if amb is not None:
            place(layer, amb, hx, hy + self.h * 0.03, alpha=0.5)
        else:  # a soft light behind the head that breathes with the voice
            place(layer, self.glow(level), hx, hy)
        title_space = 0.0
        for c in self.titles:
            if c.at - 0.05 <= t <= c.until + 0.3:
                title_space = max(title_space, self.title(layer, c, t))
        cx, cy, mw, mh = self.zones.hero(title_space)
        stacks = [c for c in self.s.callouts if c.kind == "stack" and c.at - 0.3 <= t <= c.until + 0.3]
        if stacks:  # make room: the main animation slides left and gets smaller
            k = ease((t - stacks[0].at + 0.3) / 0.5) * (1 - ease((t - stacks[0].until) / 0.4))
            cx, mw, mh = cx - self.w * 0.24 * k, mw * (1 - 0.45 * k), mh * (1 - 0.25 * k)
        current = None
        for c in self.heroes:
            s, v = life(t, c.at - 0.05, c.until)
            if v <= 0:
                continue
            current = c
            sprite = self.fit(c.lottie, t - c.at, mw, mh * (0.8 if c.text else 0.95))
            if sprite is None:  # nothing fitted: the words, animated
                fade = 1 - clamp((t - c.until) / 0.3)
                self.kinetic(layer, c.text or c.visual, c.emoji, c.at - 0.05, t, cx, cy, mw, mh, fade)
                continue
            bob = math.sin((t - c.at) * 2.2) * self.h * 0.004
            hy_ = cy - (mh * 0.08 if c.text else 0)
            self.put(layer, sprite, cx, hy_ + bob, s, v)
            if c.text:  # its label goes in front, above the head (drawn in frame())
                self.labels.append((self.chip(c.text.upper(), int(self.w * 0.042)), s, v))
        for c in self.s.callouts:
            if c.kind == "stack" and c.at - 0.3 <= t <= c.until + 0.3:
                self.stack(layer, c, t, cx, cy, mw, mh)
            elif c.kind == "stat" and c.at - 0.05 <= t <= c.until + 0.3:
                self.stat(layer, c, t, cx, cy, mw, mh, current is not None)
        return layer

    def stack(self, layer, c: Callout, t: float, cx, cy, mw, mh) -> None:
        n = max(1, c.count)
        rows = min(5, n)
        cols = (n + rows - 1) // rows
        tw, th = int(self.w * 0.19), int(self.h * 0.038)
        gap = self.w * 0.018
        x_start = self.w * 0.97 - cols * (tw + gap)  # the right side, inside the frame
        out = 1 - clamp((t - c.until) / 0.3)
        for j in range(n):
            t0 = c.at + j * 0.06
            if t < t0:
                continue
            k = ease((t - t0) / 0.5)
            col, row = j // rows, j % rows
            tx = x_start + col * (tw + gap) + tw / 2
            ty = cy - th * 1.18 * (rows - 1) / 2 + row * (th * 1.18)
            x, y = cx + (tx - cx) * k, cy + (ty - cy) * k
            tile = card(tw, th, th // 2, self.chip_fill)
            d = ImageDraw.Draw(tile)
            d.polygon([(th * 0.35, th * 0.28), (th * 0.35, th * 0.72), (th * 0.75, th * 0.5)], fill=(*self.accent, 255))
            num = text_sprite(f"{j + 1:02d}", "Nunito-Black", int(th * 0.5), (*self.ink, 255))
            tile.alpha_composite(num, (int(th * 0.95), (th - num.height) // 2))
            art = self.lottie(c.lottie, t - t0 + j * 0.13, int(tw * 0.5)) if c.lottie else None
            if art is not None:
                tile.alpha_composite(art, (int(tw * 0.46), max(0, (th - art.height) // 2)))
            place(layer, tile, x, y, scale=0.35 + 0.65 * k, alpha=min(1, k * 2) * out)
        if c.text:
            s, v = life(t, c.at - 0.05, c.until)
            self.labels.append((self.chip(c.text.upper(), int(self.w * 0.045), fill=(*self.accent, 235), color=(255, 255, 255)), s, v))

    def stat(self, layer, c: Callout, t: float, cx, cy, mw, mh, beside: bool) -> None:
        s, v = life(t, c.at - 0.05, c.until, 0.3)
        m = re.search(r"\d+(?:\.\d+)?", c.value or c.text or "")
        text = c.value or c.text
        if m:  # the number counts up
            target, dec = float(m.group()), (len(m.group().split(".")[1]) if "." in m.group() else 0)
            text = text[:m.start()] + f"{target * ease((t - c.at) / 0.9):.{dec}f}" + text[m.end():]
        num = text_sprite(text, "Nunito-Black", int(self.w * 0.15), (*self.accent, 255))
        x = cx - mw * 0.36 if beside else cx
        place(layer, num, x, cy - num.height * 0.2, scale=min(s, 1.1), alpha=v)
        if c.text and c.value:
            lab = self.chip(c.text.upper(), int(self.w * 0.036))
            place(layer, lab, x, cy + num.height * 0.45, scale=min(s, 1.05), alpha=v)

    def pills(self, layer: Image.Image, t: float) -> None:
        _, top, _, _ = self.zones.box
        for c in self.s.callouts:
            if c.kind != "pill" or not (c.at - 0.05 <= t <= c.until + 0.3):
                continue
            s, v = life(t, c.at - 0.05, c.until, 0.3)
            px = int(self.w * 0.05)
            label = text_sprite(c.text.upper(), "Nunito-Black", px, (*self.ink, 255), tracking=0.05)
            em = emoji_sprite(c.emoji, int(px * 1.5)) if c.emoji else None
            wid = label.width + (em.width + px // 2 if em else 0) + px
            bg = card(wid, int(px * 1.9), int(px * 0.95), self.chip_fill)
            x0 = px // 2
            if em is not None:
                bg.alpha_composite(em, (x0, (bg.height - em.height) // 2))
                x0 += em.width + px // 2
            bg.alpha_composite(label, (x0, (bg.height - label.height) // 2))
            place(layer, bg, self.w / 2, self.h * max(0.12, top - 0.035), scale=min(s, 1.05), alpha=v)

    def captions(self, layer: Image.Image, t: float) -> None:
        said = [w for w in self.words if w["start"] <= t + 0.02]
        if not said or t > said[-1]["end"] + 0.6:
            return
        cur = said[-1]
        k = len(said) - 1
        start = k - k % 3
        group = [w for w in self.words[start:start + 3] if w["start"] <= t + 0.02]
        parts = [outlined(re.sub(r"[^\w'%$.-]", "", w["text"]).upper().strip(".") or w["text"], int(self.w * 0.085),
                          self.accent if w is cur else (255, 255, 255)) for w in group]
        gap = int(self.w * 0.022)
        total = sum(p.width for p in parts) + gap * (len(parts) - 1)
        sc = min(1.0, self.w * 0.9 / max(1, total))
        x = self.w / 2 - total * sc / 2
        for p in parts:
            place(layer, p, x + p.width * sc / 2, self.h * 0.84, scale=sc)
            x += (p.width + gap) * sc

    def person(self, base: np.ndarray, rgb: np.ndarray, m: np.ndarray) -> None:
        """The cut-out person over `base` (in place): their colours cleaned at the edge, a touch more contrast, a rim
        of light along the outline and a soft shadow behind them. Only the box around them is touched: outside it the
        matte, the rim and the shadow are all zero, so the picture there is the backdrop as it was (the box has room
        for the shadow's blur), and a person filling a third of the frame costs a third."""
        h, w = m.shape
        q4 = cv2.resize(m, (w // 4, h // 4), interpolation=cv2.INTER_AREA)
        found = cv2.findNonZero(cv2.threshold(q4, 0.002, 255, cv2.THRESH_BINARY)[1].astype(np.uint8))
        if found is None:
            return
        bx, by, bw, bh = cv2.boundingRect(found)
        pad = 30  # quarter-size pixels: the shadow blur (sigma 6) reaches 18, the rim's 5
        qx0, qy0 = max(0, bx - pad), max(0, by - pad)
        qx1, qy1 = min(q4.shape[1], bx + bw + pad), min(q4.shape[0], by + bh + pad)
        x0, y0, x1, y1 = qx0 * 4, qy0 * 4, min(w, qx1 * 4), min(h, qy1 * 4)
        if qx1 * 4 >= w:
            x1 = w
        if qy1 * 4 >= h:
            y1 = h
        rw, rh = x1 - x0, y1 - y0
        # the quarter-size blurs are worked out on the whole quarter frame (cheap), then cut to the box at full size
        wide = cv2.resize(cv2.GaussianBlur(q4, (0, 0), 1.5), (w, h), interpolation=cv2.INTER_LINEAR)[y0:y1, x0:x1]
        shade = cv2.resize(cv2.GaussianBlur(q4, (0, 0), 6), (w, h), interpolation=cv2.INTER_LINEAR)[y0:y1, x0:x1]
        mb = np.ascontiguousarray(m[y0:y1, x0:x1])
        person = cutout.decontaminate(np.ascontiguousarray(rgb[y0:y1, x0:x1]), mb)
        cv2.multiply(person, 1.06, dst=person)
        cv2.subtract(person, 11.7, dst=person)  # (p - 128) * 1.06 + 124: a touch more contrast
        soft = cv2.GaussianBlur(mb, (0, 0), 1.2)
        edge = cv2.subtract(soft, wide)
        np.clip(edge, 0, 1, out=edge)
        r, g, b = (float(v) * 0.9 for v in self.rim)
        cv2.add(person, cv2.merge([edge * r, edge * g, edge * b]), dst=person)
        shade *= -0.45
        shade += 1.0
        region = np.ascontiguousarray(base[y0:y1, x0:x1])
        cv2.multiply(region, cv2.merge([shade, shade, shade]), dst=region)
        np.clip(person, 0, 255, out=person)
        base[y0:y1, x0:x1] = cv2.blendLinear(region, person, 1.0 - soft, soft)
        assert region.shape[:2] == (rh, rw)

    def frame(self, i: int, t: float, rgb: np.ndarray, raw: np.ndarray | None) -> np.ndarray:
        level = float(self.levels[min(i, len(self.levels) - 1)]) if len(self.levels) else 0.0
        m = self.tracker(rgb, raw) if self.tracker is not None else None
        self.zones.update(m)
        behind = self.behind(t, level)
        cut = next((s for s in self.s.scenes if s.start - 0.25 <= t <= s.end + 0.25), None)
        covered = cut is not None and cut.start <= t <= cut.end  # a full-screen scene hides the person completely
        if covered:
            img = np.zeros((self.h, self.w, 3), np.float32)
        elif m is not None:
            img = blend(self.backdrop(t, level), behind)
            self.person(img, rgb, m)
        else:  # no cut-out: graphics over the picture, on a dark band so they read
            img = rgb.astype(np.float32)
            band = np.clip(1 - np.linspace(0, 1, self.h) / 0.42, 0, 1)[:, None, None] * 0.6
            img = img * (1 - band)
            img = blend(np.ascontiguousarray(img, dtype=np.float32), behind)
        if cut is not None:  # a full-screen animated scene, cross-faded in and out over the person
            from .compose import _scene_frame

            k = ease((t - cut.start + 0.25) / 0.25) * (1 - ease((t - cut.end) / 0.25))
            scene = _scene_frame(self.s, cut, t, self.size, level, self.plates, self.shots)
            img = cv2.addWeighted(np.ascontiguousarray(img, dtype=np.float32), 1 - k,
                                  np.asarray(scene.convert("RGB"), np.float32), k, 0.0)
        front = Image.new("RGBA", self.size, (0, 0, 0, 0))
        _, top, _, _ = self.zones.box
        y = self.h * max(0.12, top - 0.03)
        for tag, s, v in self.labels[-1:]:  # the newest label, in front, just above the head
            place(front, tag, self.w / 2, y, scale=min(s, 1.05), alpha=v)
        self.pills(front, t)
        self.captions(front, t)
        img = blend(img, front)
        z = 1.0 + 0.03 * clamp(t / 20.0)
        for c in self.s.callouts:
            if c.punch and c.at <= t <= c.at + 1.6:
                z += 0.08 * ease((t - c.at) / 0.18) * (1 - ease((t - c.at - 0.9) / 0.7))
        img = cv2.convertScaleAbs(img)  # to 8 bits, saturating
        if z > 1.001:
            cw, ch = int(self.w / z), int(self.h / z)
            x0 = (self.w - cw) // 2
            img = cv2.resize(img[:ch, x0:x0 + cw], (self.w, self.h), interpolation=cv2.INTER_LINEAR)
        return img


def free_memory_gb() -> float | None:
    """Physical memory free right now (None when it can't be told)."""
    try:
        import ctypes
        import os

        if os.name == "nt":
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MemoryStatus()
            st.dwLength = ctypes.sizeof(st)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return st.ullAvailPhys / 1e9
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1e9
    except Exception:  # noqa: BLE001
        return None


def safe_workers(wanted: int, per_process_gb: float = 1.3, spare_gb: float = 2.5) -> int:
    """As many drawing processes as asked for, but never more than free memory allows (2.5 GB kept spare for
    everything else on the machine). Each part costs about 1.3 GB at its peak, measured on a 1080x1920 talk: the
    drawing process ~0.7 GB, its encoder ~0.45 and decoder ~0.15 (with their threads capped; uncapped, the encoder
    alone took ~0.9)."""
    free = free_memory_gb()
    if free is None:
        return max(1, min(wanted, 2))
    return max(1, min(wanted, int((free - spare_gb) / per_process_gb)))


def voice_levels(wav: Path | None, start: float, seconds: float) -> np.ndarray:
    if wav is None or not Path(wav).exists():
        return np.zeros(1)
    data, sr = sf.read(str(wav), dtype="float32", always_2d=True)
    x = data.mean(axis=1)[int(start * sr):int((start + seconds) * sr)]
    hop = max(1, sr // FPS)
    n = len(x) // hop
    if n == 0:
        return np.zeros(1)
    rms = np.sqrt(np.mean(x[:n * hop].reshape(n, hop) ** 2, axis=1))
    return np.clip(rms / (np.percentile(rms, 95) + 1e-9), 0, 1)


def _draw_frames(script: VoiceScript, video: Path, words: list[dict], size: tuple[int, int], levels: np.ndarray,
                 start: float, frames: int, first: int, write, lib: Library | None, progress: Progress | None,
                 plates_dir: Path | None = None, duration: float = 0.0) -> int:
    """Draw `frames` frames of the video from `start` and hand each to write(bytes). Returns how many were drawn."""
    w, h = size
    use_cut = cutout.available()
    designer = Designer(script, words, size, lib, levels, use_cut, plates_dir, duration)
    ff = find_tool("ffmpeg")
    dec = subprocess.Popen([ff, "-hide_banner", "-loglevel", "error", "-threads", "2", "-ss", f"{start:.3f}", "-i", str(video),
                            "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS}",
                            "-frames:v", str(frames), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size_b = w * h * 3
    pool = cutout.Stream() if use_cut else None
    i = 0
    try:
        nxt = dec.stdout.read(size_b)
        fut = pool.raw(np.frombuffer(nxt, np.uint8).reshape(h, w, 3)) if pool and len(nxt) == size_b else None
        while len(nxt) == size_b and i < frames:
            rgb = np.frombuffer(nxt, np.uint8).reshape(h, w, 3)
            raw = fut.result() if fut is not None else None
            nxt = dec.stdout.read(size_b)
            fut = pool.raw(np.frombuffer(nxt, np.uint8).reshape(h, w, 3)) if pool and len(nxt) == size_b else None
            write(designer.frame(first + i, start + i / FPS, rgb, raw).tobytes())
            i += 1
            if progress and i % 15 == 0:
                progress(min(0.99, i / max(1, frames)))
    finally:
        if pool is not None:
            pool.close()
        dec.kill()
    return i


def _encoder(size: tuple[int, int], out: Path, quality: str, audio: list[str] | None = None,
             threads: int | None = None) -> subprocess.Popen:
    """x264 fed raw frames. The drawing is the slow part (about a frame a second), so the encoder needs few threads:
    left on its own it starts one per core plus a frame buffer for each, close to 1 GB at 1080x1920 (`threads` caps it
    for the parts of a parallel render, which share the machine)."""
    w, h = size
    preset, crf = ("veryfast", "21") if quality == "draft" else ("medium", "19")
    cmd = [find_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-r", str(FPS), "-i", "pipe:0"]
    cmd += (audio or []) + ["-map", "0:v"] + (["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-shortest"] if audio else [])
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p"]
    cmd += ["-threads", str(threads)] if threads else []
    cmd += ["-movflags", "+faststart", str(out)]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)


def _tally(job: dict) -> Path:
    """Where a part of a parallel render keeps how far it has got (a fraction), for the parent's progress."""
    return Path(job["out"] + ".drawn")


def _drawn(jobs: list[dict]) -> float:
    """Frames drawn so far by all parts (a part that can't be read just now counts as not started)."""
    total = 0.0
    for job in jobs:
        try:
            total += float(_tally(job).read_text(encoding="utf-8")) * job["frames"]
        except (OSError, ValueError):
            pass
    return total


def _chunk(job: dict) -> str:
    """One process's part of a parallel render: video only, exactly `frames` frames."""
    import os

    os.environ["VIDO_MATTE_THREADS"] = str(job["threads"])
    script = VoiceScript.model_validate_json(job["script"])
    lib = Library(Path(job["lib"])) if job["lib"] else None
    out = Path(job["out"])
    enc = _encoder(tuple(job["size"]), out, job["quality"], threads=min(4, job["threads"]))

    def tell(f: float) -> None:
        try:
            _tally(job).write_text(f"{f:.4f}", encoding="utf-8")
        except OSError:  # the parent reading it just now: the next one will do
            pass

    _draw_frames(script, Path(job["video"]), job["words"], tuple(job["size"]), np.array(job["levels"], np.float32),
                 job["start"], job["frames"], job["first"], enc.stdin.write, lib, tell,
                 Path(job["plates"]) if job.get("plates") else None, job.get("duration", 0.0))
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError("the video encoder stopped: " + enc.stderr.read().decode("utf-8", "replace")[-300:])
    tell(1.0)
    return str(out)


def render_designed(script: VoiceScript, video: Path, utterances: list[dict], out: Path, size: tuple[int, int],
                    wav: Path | None, *, start: float = 0.0, end: float | None = None, duration: float | None = None,
                    quality: str = "final", lib: Library | None = None, progress: Progress | None = None,
                    workers: int = 1, plates_dir: Path | None = None) -> Path:
    """The person's video, designed: their own sound exactly as it was. With workers > 1 the video is drawn in that
    many parts at once (separate processes) and joined; the sound is laid in once, from the original."""
    import os
    import tempfile
    import time
    from concurrent.futures import ProcessPoolExecutor

    end = min(duration or 1e9, end if end is not None else (duration or 1e9))
    seconds = end - start
    words = respell_words([wd for u in utterances for wd in u.get("words", [])], script.respell)
    levels = voice_levels(wav, start, seconds)
    total = max(1, int(seconds * FPS))
    audio = ["-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", str(video)]
    out.parent.mkdir(parents=True, exist_ok=True)
    workers = safe_workers(workers)
    if workers <= 1 or seconds < 8:
        enc = _encoder(size, out, quality, audio, threads=4)
        err: list[bytes] = []
        threading.Thread(target=lambda: err.append(enc.stderr.read()), daemon=True).start()
        _draw_frames(script, video, words, size, levels, start, total, 0, enc.stdin.write, lib, progress, plates_dir,
                     duration or 0.0)
        enc.stdin.close()
        if enc.wait() != 0:
            raise RuntimeError("the video encoder stopped: " + b"".join(err).decode("utf-8", "replace")[-300:])
        return out
    tmp = Path(tempfile.mkdtemp(prefix="designed-", dir=out.parent))
    bounds = [round(total * k / workers) for k in range(workers + 1)]
    threads = max(2, (os.cpu_count() or 4) // workers)
    jobs = [{"script": script.model_dump_json(), "lib": str(lib.root) if lib else "", "video": str(video), "words": words,
             "size": list(size), "levels": levels[bounds[k]:bounds[k + 1]].tolist() if len(levels) > 1 else [0.0],
             "start": start + bounds[k] / FPS, "frames": bounds[k + 1] - bounds[k], "first": 0, "quality": quality,
             "out": str(tmp / f"part{k}.mp4"), "threads": threads, "plates": str(plates_dir) if plates_dir else "",
             "duration": duration or 0.0} for k in range(workers)]
    with ProcessPoolExecutor(workers) as ex:
        futs = [ex.submit(_chunk, j) for j in jobs]
        best = 0.0
        while progress and not all(f.done() for f in futs):  # frames drawn by all the parts together
            best = max(best, _drawn(jobs) / total)
            progress(min(0.97, best))
            time.sleep(2)
        parts = [f.result() for f in futs]
    if progress:
        progress(0.98)  # joining the parts and laying in the sound
    listing = tmp / "parts.txt"
    listing.write_text("".join(f"file '{Path(p).as_posix()}'\n" for p in parts), encoding="utf-8")
    subprocess.run([find_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                    *audio, "-map", "0:v", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                    "-movflags", "+faststart", str(out)], check=True, timeout=1800)
    for p in tmp.iterdir():
        p.unlink(missing_ok=True)
    tmp.rmdir()
    return out
