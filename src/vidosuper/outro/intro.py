"""The intro: a five-second cold open in the outro's style, played before the film.

Everything on screen is this edit's own material, run at hyper-speed: the film's shots fly onto Scout's
deck, Muse marks the song's real beats on its real waveform (and flags the drop), Snip snaps the real
shot lengths into a timeline, and a terminal above them prints the measured numbers. Then it all freezes
on "Done.", the title card comes in with a clapperboard that snaps shut, and everything fades to black
before the film fades in.

Scenes (seconds): work 0-2.6 · done 2.6-3.35 · title 3.3-4.75 · fade to black 4.7-5.3.
Positions are fractions of the frame; type sizes are px for a 1080-px short side.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from ..config import Config
from ..ffmpeg import ffmpeg_bin
from ..render.audio_mix import SR, frames_to_samples, normalize
from .card import AMBER, CREAM, DIM, MUTED, PENTATONIC, SoundCue, background
from .crew import CREW, GROUND, POSES, draw_member, shadow_sprite
from .draw import ease_in_out, ease_out_back, ease_out_cubic, font, place, ramp, text_sprite
from .tail import Tail, cue_track, render_card_video

INTRO_VERSION = 1
INTRO_LUFS = -18.0  # punchy, still under the film
DURATION = 5.3
WORK_END, DONE_AT, TITLE_AT, CLAP_AT = 2.6, 2.6, 3.3, 3.95
GREEN = (128, 222, 190)
PANEL = (30, 28, 38)
MONO = "CascadiaMono"
ENERGY_COLORS = {1: (64, 200, 182), 2: (90, 176, 230), 3: (156, 124, 255), 4: (255, 122, 104), 5: (255, 196, 96)}


@dataclass
class IntroData:
    title: str
    header: str
    take: str
    lines: list[tuple[str, str]]  # terminal: (command, result)
    thumbs: list[Image.Image]  # the film's shots, in film order
    wave: np.ndarray  # loudness of the song under the film, 0..1
    beats: list[float]  # beat positions, as fractions of the film
    downbeats: list[float]
    drop: float | None  # the drop, as a fraction of the film
    blocks: list[tuple[float, float, tuple[int, int, int]]]  # (start, end) fractions and colour per shot
    deck_label: str  # "{n} clips" - n counts up to clips
    wave_label: str
    cut_label: str  # "{n} shots" - n counts up to shots
    clips: int
    shots: int

    def fingerprint(self) -> str:
        h = hashlib.sha1()
        h.update(json.dumps([self.title, self.header, self.take, self.lines, self.beats, self.downbeats, self.drop,
                             self.blocks, self.deck_label, self.wave_label, self.cut_label, self.clips, self.shots],
                            default=str).encode())
        h.update(np.round(self.wave, 3).tobytes())
        for t in self.thumbs:
            h.update(hashlib.md5(t.tobytes()).digest())
        return h.hexdigest()[:20]


def _count(label: str, n: int) -> str:
    """'{n} shots' -> '1 shot' / '42 shots'."""
    text = label.format(n=n)
    return text[:-1] if n == 1 and text.endswith("s") else text


def _speed(t: float, start: float, dur: float, power: float = 1.6) -> float:
    """Progress that starts slow and races to the end (the hyper-speed feel)."""
    return ramp(t, start, dur) ** power


def _speed_time(p: float, start: float, dur: float, power: float = 1.6) -> float:
    """When _speed reaches p."""
    return start + dur * max(0.0, p) ** (1 / power)


class Intro:
    def __init__(self, data: IntroData, width: int, height: int, fps: int):
        self.d, self.W, self.H, self.fps = data, width, height, fps
        self.duration = DURATION
        self.frames = round(DURATION * fps)
        self.u = min(width, height) / 1080
        self.landscape = width >= height
        self._bg = background(width, height)
        rng = np.random.default_rng(7)
        self._grain = [rng.integers(-2, 3, size=(height, width), dtype=np.int8) for _ in range(3)]
        self.crew = list(CREW[:3])  # Scout, Muse, Snip

        if self.landscape:
            self.term = (0.07 * width, 0.055 * height, 0.93 * width, 0.335 * height)
            self.stations = [(width * x, height * 0.535) for x in (0.2, 0.5, 0.8)]
            self.pw, self.card_w = 0.25 * width, 0.19 * width
            self.feet = [(width * x, height * 0.93) for x in (0.2, 0.5, 0.8)]
            body = 0.13 * height
        else:
            self.term = (0.05 * width, 0.05 * height, 0.95 * width, 0.25 * height)
            self.stations = [(width * 0.5, height * y) for y in (0.36, 0.53, 0.70)]
            self.pw, self.card_w = 0.62 * width, 0.46 * width
            self.feet = [(width * x, height * 0.94) for x in (0.22, 0.5, 0.78)]
            body = 0.07 * height
        self.unit = body / 92
        self.card_h = self.card_w * 9 / 16
        self._sprites: dict[tuple[int, int, bool], Image.Image] = {}
        self._anchor: dict[int, tuple[float, float, float]] = {}
        self._shadows: dict[int, Image.Image] = {}
        for i, m in enumerate(self.crew):
            sprite = self._sprite(i, 0, False)
            box = sprite.getbbox() or (0, 0, sprite.width, sprite.height)
            ax = (box[0] + box[2]) / 2 / sprite.width
            self._anchor[i] = (ax, GROUND * self.unit / sprite.height, 86 * self.unit - ax * sprite.width)
            self._shadows[i] = shadow_sprite(self.unit, m.body.a * 2)

        self._cards = self._card_sprites()
        self._land = [0.3 + 2.15 * (k / max(1, len(self._cards) - 1)) ** 0.75 for k in range(len(self._cards))]
        rng = np.random.default_rng(3)
        self._rest = [(rng.uniform(-0.012, 0.012) * width, rng.uniform(-0.012, 0.012) * height, rng.uniform(-6, 6))
                      for _ in self._cards]
        self._wave = self._wave_sprite()
        self._panel = self._terminal_panel()
        self._line_start = [0.12 + 2.2 * (k / max(1, len(data.lines))) ** 0.8 for k in range(len(data.lines))]
        self._cps = [55 + 30 * k for k in range(len(data.lines))]
        self.cues = self._cues()

    # ---- building blocks -----------------------------------------------------------------------

    def px(self, base: float) -> int:
        return max(8, round(base * self.u))

    def _sprite(self, i: int, pose: int, blink: bool) -> Image.Image:
        key = (i, pose, blink)
        if key not in self._sprites:
            self._sprites[key] = draw_member(self.crew[i], self.unit, pose, blink)
        return self._sprites[key]

    def _character(self, img: Image.Image, i: int, t: float, x: float, feet: float, *, scale: float, alpha: float,
                   hop: float, rate: float) -> None:
        m = self.crew[i]
        pose = int(((t * m.pose_rate * rate) + 0.37 * i) % 1.0 * POSES)
        blink = ((t + 0.9 * i) % (2.7 + 0.35 * i)) < 0.13
        sprite = self._sprite(i, pose, blink)
        ax, ay, body_x = self._anchor[i]
        bob = (0.5 + 0.5 * math.sin(2 * math.pi * (0.8 * rate * t + 0.29 * i))) * 2.6 * self.unit * scale
        lift = bob + hop * self.unit * scale
        place(img, self._shadows[i], x + body_x * scale, feet,
              alpha=alpha * max(0.35, 1 - lift / (30 * self.unit * scale)),
              scale=scale * (1 - min(0.3, lift / (60 * self.unit * scale))))
        place(img, sprite, x, feet - lift, alpha=alpha, scale=scale, anchor=(ax, ay))

    def _card_sprites(self) -> list[Image.Image]:
        """Each shot as a little photo card: rounded, a cream border, a soft shadow."""
        w, h = round(self.card_w), round(self.card_h)
        border, radius, pad = max(2, self.px(4)), max(4, self.px(12)), self.px(18)
        cards = []
        for thumb in self.d.thumbs:
            photo = thumb.convert("RGB").resize((w - 2 * border, h - 2 * border), Image.LANCZOS)
            card = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
            shadow = Image.new("RGBA", card.size, (0, 0, 0, 0))
            ImageDraw.Draw(shadow).rounded_rectangle([pad, pad + self.px(8), pad + w, pad + h + self.px(8)], radius,
                                                     fill=(0, 0, 0, 150))
            card.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(self.px(9))))
            face = Image.new("RGBA", (w, h), (*CREAM, 235))
            mask = Image.new("L", (w - 2 * border, h - 2 * border), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, mask.width - 1, mask.height - 1], max(2, radius - border), fill=255)
            face.paste(photo, (border, border), mask)
            outline = Image.new("L", (w, h), 0)
            ImageDraw.Draw(outline).rounded_rectangle([0, 0, w - 1, h - 1], radius, fill=255)
            face.putalpha(Image.fromarray(np.minimum(np.asarray(face.getchannel("A")), np.asarray(outline))))
            card.alpha_composite(face, (pad, pad))
            cards.append(card)
        return cards

    def _wave_sprite(self) -> Image.Image:
        """The song under the film as mirrored bars in Muse's colour."""
        w, h = round(self.pw), round(0.12 * self.H)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        env = self.d.wave if len(self.d.wave) else np.zeros(1)
        n = len(env)
        step = w / n
        bar = max(2.0, step * 0.62)
        for k, v in enumerate(env):
            x = (k + 0.5) * step
            half = max(1.5, v * h * 0.46)
            d.rounded_rectangle([x - bar / 2, h / 2 - half, x + bar / 2, h / 2 + half], radius=bar / 2,
                                fill=(*self.crew[1].color, 215))
        return img

    def _terminal_panel(self) -> Image.Image:
        x0, y0, x1, y1 = self.term
        w, h = round(x1 - x0), round(y1 - y0)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        r = self.px(18)
        d.rounded_rectangle([0, 0, w - 1, h - 1], r, fill=(14, 13, 19, 215), outline=(255, 255, 255, 30),
                            width=max(1, self.px(2)))
        bar = self.px(44)
        d.line([(0, bar), (w, bar)], fill=(255, 255, 255, 22), width=max(1, self.px(2)))
        for k, c in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
            cx, cy, rr = self.px(30) + k * self.px(26), bar / 2, self.px(8)
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(*c, 230))
        title = text_sprite("claude · vido", "Nunito-SemiBold", self.px(24), DIM)
        place(img, title, w / 2, bar / 2)
        return img

    # ---- scenes ----------------------------------------------------------------------------------

    def _terminal(self, layer: Image.Image, tw: float) -> None:
        x0, y0, x1, y1 = self.term
        place(layer, self._panel, x0, y0, anchor=(0, 0), alpha=ease_out_cubic(ramp(tw, 0.0, 0.2)))
        size = self.px(31 if self.landscape else 24)
        line_h = round(size * 1.55)
        bar, pad = self.px(44), self.px(26)
        area_w, area_h = round(x1 - x0 - 2 * pad), round(y1 - y0 - bar - self.px(14))
        text = Image.new("RGBA", (area_w, area_h), (0, 0, 0, 0))
        capacity = max(1, area_h // line_h)
        shown = sum(ease_out_cubic(ramp(tw, s, 0.09)) for s in self._line_start)
        scroll = max(0.0, shown - capacity) * line_h
        prompt = text_sprite("❯", MONO, size, AMBER)
        for k, ((cmd, result), start) in enumerate(zip(self.d.lines, self._line_start)):
            if tw < start:
                continue
            y = k * line_h - scroll + line_h / 2
            if y < -line_h or y > area_h + line_h:
                continue
            typed = min(len(cmd), int((tw - start) * self._cps[k]))
            place(text, prompt, 0, y, anchor=(0, 0.5))
            x = prompt.width + self.px(6)
            if typed:
                place(text, text_sprite(cmd[:typed], MONO, size, CREAM), x, y, anchor=(0, 0.5))
            done = start + len(cmd) / self._cps[k] + 0.06
            if typed < len(cmd) or tw < done:
                if (tw * 4) % 1 < 0.6:  # blinking block cursor
                    cur_x = x + (text_sprite(cmd[:typed], MONO, size, CREAM).width if typed else 0)
                    place(text, text_sprite("█", MONO, size, AMBER), cur_x, y, anchor=(0, 0.5), alpha=0.85)
            else:
                e = ease_out_back(ramp(tw, done, 0.18))
                place(text, text_sprite(result, MONO, size, GREEN), area_w, y, anchor=(1, 0.5),
                      alpha=min(1.0, 3 * ramp(tw, done, 0.18)), scale=0.85 + 0.15 * e)
        place(layer, text, x0 + pad, y0 + bar + self.px(8), anchor=(0, 0))

    def _deck(self, layer: Image.Image, tw: float) -> None:
        cx, cy = self.stations[0]
        for k, card in enumerate(self._cards):
            t_land = self._land[k]
            fly = 0.17
            if tw < t_land - fly:
                break
            if k < len(self._cards) - 7 and tw > self._land[min(len(self._cards) - 1, k + 7)]:
                continue  # buried under newer cards
            p = ease_out_cubic(ramp(tw, t_land - fly, fly))
            dx, dy, rot = self._rest[k]
            x = cx + dx + (1 - p) * 0.16 * self.W
            y = cy + dy - (1 - p) * 0.09 * self.H
            angle = rot + (1 - p) * 22
            sprite = card.rotate(angle, resample=Image.BICUBIC, expand=True)
            place(layer, sprite, x, y, alpha=min(1.0, 3 * p), scale=1.0 + 0.06 * (1 - p))
        landed = sum(tw >= t for t in self._land)
        n = round(self.d.clips * landed / max(1, len(self._cards)))
        self._label(layer, 0, _count(self.d.deck_label, n), tw)

    def _waveform(self, layer: Image.Image, tw: float) -> None:
        cx, cy = self.stations[1]
        w, h = self._wave.size
        left, top = cx - w / 2, cy - h / 2
        p = _speed(tw, 0.2, 2.25)
        reveal = round(w * p)
        if reveal > 0:
            place(layer, self._wave.crop((0, 0, reveal, h)), left, top, anchor=(0, 0))
        dot_y = top + h + self.px(16)
        for f in self.d.beats:
            if f > p:
                break
            big = any(abs(f - b) < 1e-4 for b in self.d.downbeats)
            r = self.px(5 if big else 3) * ease_out_back(ramp(tw, _speed_time(f, 0.2, 2.25), 0.15))
            if r > 0.5:
                ImageDraw.Draw(layer).ellipse([left + f * w - r, dot_y - r, left + f * w + r, dot_y + r],
                                              fill=(*AMBER, 235 if big else 170))
        if self.d.drop is not None and p >= self.d.drop:
            e = ease_out_back(ramp(tw, _speed_time(self.d.drop, 0.2, 2.25), 0.22))
            fx = left + self.d.drop * w
            ImageDraw.Draw(layer).line([(fx, top - self.px(4)), (fx, top + h)], fill=(*AMBER, 220), width=max(1, self.px(3)))
            flag = text_sprite("DROP", "Nunito-Black", self.px(22), AMBER, tracking=0.12)
            place(layer, flag, fx, top - self.px(20), alpha=min(1.0, 2 * e), scale=max(0.01, e))
        if 0 < p < 1:
            head = left + reveal
            ImageDraw.Draw(layer).line([(head, top - self.px(6)), (head, top + h + self.px(6))], fill=(*CREAM, 230),
                                       width=max(1, self.px(3)))
        self._label(layer, 1, self.d.wave_label, tw)

    def _timeline(self, layer: Image.Image, tw: float) -> None:
        cx, cy = self.stations[2]
        w, h = self.pw, 0.055 * self.H
        left, top = cx - w / 2, cy - h / 2
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle([left - self.px(6), top - self.px(6), left + w + self.px(6), top + h + self.px(6)],
                            self.px(10), fill=(255, 255, 255, round(18 * ease_out_cubic(ramp(tw, 0.05, 0.2)))))
        p = _speed(tw, 0.35, 2.15)
        snapped = 0
        gap = max(1, self.px(2))
        for start, end, color in self.d.blocks:
            if start > p:
                break
            snapped += 1
            appear = _speed_time(start, 0.35, 2.15)
            e = ease_out_cubic(ramp(tw, appear, 0.1))
            drop = (1 - e) * 0.035 * self.H
            bx0 = left + start * w + gap / 2
            bx1 = max(bx0 + 1, left + end * w - gap / 2)  # a stutter shot is only a few px wide
            d.rounded_rectangle([bx0, top - drop, bx1, top + h - drop], min(max(1, self.px(4)), (bx1 - bx0) / 2),
                                fill=(*color, round(235 * e)))
            flash = 1 - ramp(tw, appear, 0.09)
            if flash > 0:  # the cut: a white snip at the shot's left edge
                x = left + start * w
                d.line([(x, top - self.px(10)), (x, top + h + self.px(10))], fill=(255, 255, 255, round(255 * flash)),
                       width=max(1, self.px(3)))
        self._label(layer, 2, _count(self.d.cut_label, round(self.d.shots * snapped / max(1, len(self.d.blocks)))), tw)

    def _label(self, layer: Image.Image, i: int, text: str, tw: float) -> None:
        cx, cy = self.stations[i]
        below = (self.card_h / 2 + self.px(52)) if self.landscape else             (self.card_h if i == 0 else 0.12 * self.H if i == 1 else 0.055 * self.H) / 2 + self.px(46)
        sprite = text_sprite(text, "Nunito-ExtraBold", self.px(30), self.crew[i].label_color)
        place(layer, sprite, cx, cy + below, alpha=ease_out_cubic(ramp(tw, 0.15 + 0.1 * i, 0.3)))

    def _crew(self, img: Image.Image, t: float) -> None:
        to_title = ease_in_out(ramp(t, TITLE_AT, 0.4))
        n = len(self.crew)
        for i in range(n):
            x0, f0 = self.feet[i]
            x1 = self.W / 2 + (i - (n - 1) / 2) * min(0.085 * self.W, 0.25 * self.W)
            f1 = self.H * (0.88 if self.landscape else 0.9)
            x, feet = x0 + (x1 - x0) * to_title, f0 + (f1 - f0) * to_title
            scale = (1 - 0.42 * to_title) * max(0.01, ease_out_back(ramp(t, 0.05 + 0.08 * i, 0.35)))
            punch = DONE_AT + 0.04 + 0.05 * i
            hop = 18 * math.sin(math.pi * ramp(t, punch, 0.36)) if t >= punch else 0.0
            rate = 4.0 if t < WORK_END else 1.0  # hyper-speed while working
            self._character(img, i, t, x, feet, scale=scale, alpha=min(1.0, 4 * ramp(t, 0.05 + 0.08 * i, 0.35)),
                            hop=hop, rate=rate)

    def _done(self, img: Image.Image, t: float) -> None:
        if t < DONE_AT or t > TITLE_AT + 0.1:
            return
        alpha = min(1.0, ramp(t, DONE_AT, 0.1) * 3) * (1 - ease_in_out(ramp(t, TITLE_AT - 0.12, 0.2)))
        s = max(0.01, ease_out_back(ramp(t, DONE_AT, 0.35)))
        word = text_sprite("Done.", "Fraunces-SemiBold", self.px(132), CREAM)
        r = self.px(62)
        gap = self.px(30)
        total = 2 * r + gap + word.width
        cx = self.W / 2 - total / 2 * s + r * s
        cy = self.H * 0.47
        layer = Image.new("RGBA", (round(2 * r + 8), round(2 * r + 8)), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        ring = max(2, self.px(9))
        d.ellipse([4 + ring / 2, 4 + ring / 2, 4 + 2 * r - ring / 2, 4 + 2 * r - ring / 2], outline=(*AMBER, 255), width=ring)
        k = ease_out_cubic(ramp(t, DONE_AT + 0.12, 0.2))  # the tick draws itself
        pts = [(4 + r - 0.42 * r, 4 + r + 0.02 * r), (4 + r - 0.12 * r, 4 + r + 0.32 * r), (4 + r + 0.45 * r, 4 + r - 0.3 * r)]
        seg1 = min(1.0, k * 2)
        seg2 = max(0.0, k * 2 - 1)
        w = max(2, self.px(11))
        if seg1 > 0:
            p1 = (pts[0][0] + (pts[1][0] - pts[0][0]) * seg1, pts[0][1] + (pts[1][1] - pts[0][1]) * seg1)
            d.line([pts[0], p1], fill=(*AMBER, 255), width=w, joint="curve")
        if seg2 > 0:
            p2 = (pts[1][0] + (pts[2][0] - pts[1][0]) * seg2, pts[1][1] + (pts[2][1] - pts[1][1]) * seg2)
            d.line([pts[1], p2], fill=(*AMBER, 255), width=w, joint="curve")
        place(img, layer, cx, cy, alpha=alpha, scale=s)
        place(img, word, cx + (r + gap) * s, cy, anchor=(0, 0.52), alpha=alpha, scale=s)

    def _title(self, img: Image.Image, t: float) -> None:
        if t < TITLE_AT:
            return
        e = ease_out_cubic(ramp(t, TITLE_AT + 0.05, 0.35))
        header = text_sprite(self.d.header, "Nunito-ExtraBold", self.px(28), MUTED, tracking=0.3,
                             max_width=round(self.W * 0.9))
        place(img, header, self.W / 2, self.H * 0.29 + (1 - e) * 0.015 * self.H, alpha=e)
        size = self.px(124 if self.landscape else 104)
        f = font("Fraunces-SemiBold", size)
        words = self.d.title.split()
        widths = [f.getlength(wd) for wd in words]
        space = f.getlength(" ")
        x = self.W / 2 - (sum(widths) + space * (len(words) - 1)) / 2
        for k, wd in enumerate(words):
            ek = ease_out_cubic(ramp(t, TITLE_AT + 0.12 + 0.1 * k, 0.35))
            place(img, text_sprite(wd, "Fraunces-SemiBold", size, CREAM), x + widths[k] / 2,
                  self.H * 0.43 + (1 - ek) * 0.025 * self.H, alpha=ek)
            x += widths[k] + space
        self._clapper(img, t)

    def _clapper(self, img: Image.Image, t: float) -> None:
        appear = TITLE_AT + 0.3
        if t < appear:
            return
        bw, bh = round(min(0.17 * self.W, 0.42 * self.W if not self.landscape else 1e9)), round(0.09 * self.H)
        sh = round(0.03 * self.H)
        pad = sh * 2
        lift = math.ceil(bw * math.sin(math.radians(30)) + sh)  # head room for the open stick
        size = (bw + 2 * pad, bh + lift + 2 * pad)
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        x0, y0 = pad, pad + lift
        line = max(2, self.px(3))
        d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], self.px(10), fill=(*PANEL, 255), outline=(*CREAM, 255), width=line)
        take = text_sprite(self.d.take, "Nunito-Black", self.px(46), CREAM, tracking=0.08)
        place(layer, take, x0 + bw / 2, y0 + bh / 2)
        # the clapper stick: open, then it snaps shut on CLAP_AT
        angle = 24 * (1 - ramp(t, CLAP_AT - 0.17, 0.17) ** 2)
        a = math.radians(angle)

        def at(x: float, y: float) -> tuple[float, float]:
            """Stick coordinates (x along it from the hinge, y up from the board's top edge) on the layer."""
            return x0 + x * math.cos(a) + y * math.sin(a), y0 - x * math.sin(a) + y * math.cos(a)

        d.polygon([at(0, 0), at(bw, 0), at(bw, -sh), at(0, -sh)], fill=(*PANEL, 255), outline=(*CREAM, 255), width=line)
        stripe = sh * 1.3
        for k in range(0, int(bw / stripe) + 2, 2):  # diagonal clapper stripes, kept inside the stick
            xs = k * stripe
            quad = [(xs, -line), (xs + stripe * 0.6, -sh + line), (xs + stripe * 1.2, -sh + line), (xs + stripe * 0.6, -line)]
            quad = [(min(bw - line, max(line, x)), y) for x, y in quad]
            if quad[2][0] - quad[0][0] > 1:
                d.polygon([at(x, y) for x, y in quad], fill=(*CREAM, 255))
        e = ease_out_back(ramp(t, appear, 0.3))
        punch = 1 + 0.05 * math.sin(math.pi * ramp(t, CLAP_AT, 0.16))
        place(img, layer, self.W / 2, self.H * 0.71, alpha=min(1.0, 3 * ramp(t, appear, 0.3)),
              scale=max(0.01, e) * punch, anchor=(0.5, (y0 + bh / 2) / size[1]))

    # ---- frames and sound ------------------------------------------------------------------------

    def frame(self, t: float) -> Image.Image:
        img = self._bg.copy()
        tw = min(t, WORK_END)  # the work freezes on "Done."
        keep = (1 - 0.72 * ease_in_out(ramp(t, DONE_AT, 0.18))) * (1 - ease_in_out(ramp(t, TITLE_AT - 0.05, 0.3)))
        if keep > 0.004:
            work = Image.new("RGBA", img.size, (0, 0, 0, 0))
            self._terminal(work, tw)
            self._deck(work, tw)
            self._waveform(work, tw)
            self._timeline(work, tw)
            place(img, work, 0, 0, anchor=(0, 0), alpha=keep)
        self._crew(img, t)
        self._done(img, t)
        self._title(img, t)
        arr = np.asarray(img.convert("RGB"), dtype=np.int16)
        arr += self._grain[int(t * self.fps) % len(self._grain)][..., None]
        level = ease_in_out(ramp(t, 0.0, 0.2)) * (1 - ease_in_out(ramp(t, self.duration - 0.6, 0.6)))
        if level < 0.999:
            arr = arr * level
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    def frame_at(self, index: int) -> Image.Image:
        return self.frame(index / self.fps)

    def _cues(self) -> list[SoundCue]:
        cues = []
        for k, ((cmd, _), start) in enumerate(zip(self.d.lines, self._line_start)):
            for j in range(0, len(cmd), 2):  # key clicks while each command types itself
                t = start + j / self._cps[k]
                if t < WORK_END:
                    cues.append(SoundCue(t, "key", 1900 + (k * 331 + j * 97) % 1400, 0.1))
            done = start + len(cmd) / self._cps[k] + 0.06
            if done < WORK_END:
                cues.append(SoundCue(done, "pop", PENTATONIC[(k + 3) % len(PENTATONIC)] * 2, 0.12))
        for k, t in enumerate(self._land):
            cues.append(SoundCue(t, "pop", PENTATONIC[k % len(PENTATONIC)], 0.2))
        cues += [SoundCue(WORK_END, "riser_end", 0.0, 0.3),
                 SoundCue(DONE_AT, "pop", 392.0, 0.35), SoundCue(DONE_AT + 0.02, "chime", 0.0, 0.55),
                 SoundCue(TITLE_AT + 0.12, "swell_end", 0.0, 0.22)]
        cues += [SoundCue(TITLE_AT + 0.12 + 0.03 * k, "pluck", f, 0.3) for k, f in enumerate((261.63, 392.0, 659.25))]
        cues.append(SoundCue(CLAP_AT, "clap", 0.0, 0.85))
        return cues


def intro_audio(intro: Intro) -> np.ndarray:
    n = frames_to_samples(intro.frames, intro.fps)
    mono = cue_track(intro, n)
    mix = np.stack([mono, mono], axis=1).astype(np.float32) * 0.5
    mix, _, _ = normalize(mix, INTRO_LUFS, -1.5)
    return mix.astype(np.float32)


def build_head(intro: Intro, cache_dir: Path, quality: str, cfg: Config) -> Tail:
    key = hashlib.sha1(json.dumps({"v": INTRO_VERSION, "data": intro.d.fingerprint(), "size": [intro.W, intro.H],
                                   "fps": intro.fps, "quality": quality}).encode()).hexdigest()[:20]
    video = render_card_video(intro, cache_dir / f"intro_{key}.mp4", quality, cfg)
    return Tail(video=video, frames=intro.frames, audio=intro_audio(intro))


# ---- the material: this edit's own shots, song and numbers -------------------------------------------

def _clock(seconds: float) -> str:
    s = max(0, round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _thumbnail(path: str, at: float) -> Image.Image | None:
    png = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", f"{max(0.0, at):.3f}", "-i", path, "-frames:v", "1",
                          "-vf", "scale=480:-2", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
    if not png:
        return None
    img = Image.open(io.BytesIO(png)).convert("RGB")
    return ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.3)).enhance(1.08)  # near the film's vivid look


def collect_intro(edl, project, title: str, header: str, take: str, cards: int = 12) -> IntroData:
    """Everything the intro shows, read from this edit and its project - nothing invented."""
    import soundfile as sf

    from ..edit.timeline import build_timeline
    from ..music import load_or_analyze
    from ..render.audio_mix import decode_music
    from ..template import main_drop
    from .stats import footage_stats

    clips = project.clips()
    timeline = build_timeline(edl, clips)
    film = timeline.duration
    stats = footage_stats(project)

    # the film's shots, spread across it, one per source clip
    thumbs, seen = [], set()
    items = sorted(timeline.items, key=lambda it: -it.frames)[: max(cards * 3, 1)]
    for item in sorted(items, key=lambda it: it.start):
        if item.seg.clip_id in seen or len(thumbs) >= cards:
            continue
        seen.add(item.seg.clip_id)
        clip = item.clip
        mid = item.seg.src_in + (item.seg.src_out - item.seg.src_in) / 2
        if (img := _thumbnail(clip.proxy or clip.path, mid)) is not None:
            thumbs.append(img)

    total = max(1, timeline.total_frames)
    blocks = []
    for item in timeline.items:
        level = int(item.seg.role.split()[-1]) if item.seg.role.startswith("energy") else 3
        blocks.append((item.start / total, min(1.0, (item.start + item.frames) / total), ENERGY_COLORS.get(level, ENERGY_COLORS[3])))

    wave, beats, downbeats, drop, wave_label = np.zeros(0), [], [], None, "the beat, mapped"
    song_line = None
    if edl.music is not None and edl.music.path:
        start = edl.music.start
        data, sr = sf.read(str(decode_music(edl.music.path, project.music)), dtype="float32", always_2d=True)
        mono = data[int(start * sr): int((start + film) * sr)].mean(axis=1)
        bins = 110
        if len(mono) >= bins:
            rms = np.sqrt(np.array([np.mean(c ** 2) for c in np.array_split(mono, bins)]))
            wave = np.clip(rms / (np.percentile(rms, 98) + 1e-9), 0, 1) ** 0.8
        song = load_or_analyze(edl.music.path, project.music)
        beats = [round((b - start) / film, 5) for b in song.beats if start <= b <= start + film]
        downbeats = [round((b - start) / film, 5) for b in song.downbeats if start <= b <= start + film]
        d = main_drop(song, start, start + film)
        if d is not None:
            drop = (d - start) / film
            wave_label = f"{song.tempo:.0f} BPM · drop {_clock(d - start)}"
            song_line = (f'beat-map "{Path(edl.music.path).stem}"', f"✓ {song.tempo:.0f} BPM · drop {_clock(d - start)}")
        else:
            wave_label = f"{song.tempo:.0f} BPM"
            song_line = (f'beat-map "{Path(edl.music.path).stem}"', f"✓ {song.tempo:.0f} BPM")

    meta = edl.meta
    lines = [(f"vido analyze {project.clips_dir.name}",
              f"✓ {stats.clips} clips · {_clock(stats.raw_seconds)}{' 4K' if stats.uhd else ''}")]
    if stats.measured_frames:
        lines.append(("measure shake · blur · light", f"✓ {stats.measured_frames:,} frames"))
    if stats.moments:
        lines.append(("log the moments", f"✓ {stats.moments} moments"))
    if song_line:
        lines.append(song_line)
    lines.append(("cut to the beat", f"✓ {len(edl.segments)} shots · ±{1000 / (2 * timeline.fps):.0f} ms"))
    lines.append(("grade · stabilise · mix", f"✓ {meta.loudness_lufs:g} LUFS".replace("-", "−")))
    lines.append(("render --final", f"✓ {'4K' if min(meta.width, meta.height) >= 2160 else f'{min(meta.width, meta.height)}p'}"))
    return IntroData(title=title, header=header, take=take, lines=lines, thumbs=thumbs, wave=wave, beats=beats,
                     downbeats=downbeats, drop=drop, blocks=blocks, deck_label="{n} clips", wave_label=wave_label,
                     cut_label="{n} shots", clips=stats.clips, shots=len(edl.segments))
