"""The outro animation, drawn frame by frame: a confession, the crew, the numbers, a sign-off.

Positions are fractions of the frame; type sizes are in px for a 1080-px short side and scale with
the canvas, so the same outro renders at 1080p, 4K or vertical.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
from PIL import Image, ImageDraw

from .crew import CREW, GROUND, POSES, Member, draw_member, shadow_sprite
from .draw import ease_in_out, ease_out_back, ease_out_cubic, font, place, ramp, text_sprite
from .stats import RunStats, short_credit

CREAM = (244, 239, 230)
MUTED = (172, 166, 186)
DIM = (130, 124, 142)
AMBER = (255, 196, 96)
BG_CENTRE = np.array((28, 26, 35), np.float32)
BG_EDGE = np.array((9, 8, 12), np.float32)
PENTATONIC = (523.25, 587.33, 659.25, 783.99, 880.0, 1046.5, 1174.66)

# scene windows (start, end) in seconds
STYLES: dict[str, dict[str, tuple[float, float] | None]] = {
    "full": {"confess": (0.0, 2.9), "crew": (2.8, 7.6), "numbers": (7.5, 10.3), "sign": (10.2, 12.4)},
    "short": {"confess": (0.0, 2.3), "crew": (2.2, 5.9), "numbers": None, "sign": (5.8, 7.8)},
}
LINE1 = "I didn’t edit this."
LINE2 = "No editing app. No code. Just a conversation."
PUNCHLINE = "Every one of them is Claude."
SIGN_SMALL = "Shot by a human."
SIGN_BIG = "Made with Claude."


@dataclass
class SoundCue:
    t: float
    kind: str  # pluck | pop | chime | tick | swell
    pitch: float = 0.0
    gain: float = 1.0


@dataclass
class Tile:
    value: float
    label: str
    clock_from: float | None = None  # count down from this many seconds (shown as m:ss)
    trail: tuple[float, ...] = ()  # little score chart under the label


def crew_for(stats: RunStats) -> list[Member]:
    """The agents that actually worked on this run, with what each did (from the run's own files)."""
    did = {
        "analyst": (stats.clips > 0, f"watched {stats.clips} clips",
                    f"logged {stats.moments} moments" if stats.moments else ""),
        "director": (stats.acts > 0, "found the story", f"planned {stats.acts} acts"),
        "editor": (stats.shots > 0, f"cut {stats.shots} shots", f"over {stats.drafts} drafts" if stats.drafts > 1 else ""),
        "critics": (bool(stats.panel), f"scored {stats.reviewed} draft{'s' if stats.reviewed != 1 else ''}",
                    f"{stats.critic_notes} notes" if stats.critic_notes else ""),
        "adversary": (stats.adversary_notes > 0, "picked it apart", f"{stats.adversary_notes} harsh notes"),
        "judge": (stats.verdicts > 0, "picked the winner", f"{stats.verdicts} head-to-heads"),
    }
    out = []
    for m in CREW:
        worked, role, note = did[m.key]
        if worked:
            out.append(replace(m, role=role, note=note, scores=tuple(stats.panel) if m.key == "critics" else ()))
    return out


def tiles_for(stats: RunStats) -> list[Tile]:
    tiles = []
    if stats.raw_seconds > 0 and stats.final_seconds > 0:
        tiles.append(Tile(stats.final_seconds, f"raw {'4K ' if stats.uhd else ''}footage → final film",
                          clock_from=stats.raw_seconds))
    if stats.measured_frames:
        tiles.append(Tile(stats.measured_frames, "frames checked for\nshake, blur and light"))
    notes = stats.critic_notes + stats.adversary_notes
    if notes:
        rounds = f"{stats.reviewed} review round{'s' if stats.reviewed != 1 else ''}"
        tiles.append(Tile(notes, f"critic notes over\n{rounds}", trail=tuple(stats.score_trail)))
    if stats.code_lines:
        tiles.append(Tile(stats.code_lines, "lines of code,\nzero by a human"))
    return tiles


def background(w: int, h: int) -> Image.Image:
    """The dark, softly vignetted backdrop shared by the outro and the intro."""
    y = ((np.arange(h, dtype=np.float32) - h * 0.46) / (h * 0.7))[:, None]
    x = ((np.arange(w, dtype=np.float32) - w / 2) / (w * 0.7))[None, :]
    t = np.clip(np.sqrt(x * x + y * y), 0, 1) ** 1.5
    rgb = BG_CENTRE * (1 - t[..., None]) + BG_EDGE * t[..., None]
    return Image.fromarray(rgb.astype(np.uint8), "RGB").convert("RGBA")


def _clock(seconds: float) -> str:
    s = max(0, round(seconds))
    return f"{s // 60}:{s % 60:02d}"


class Outro:
    def __init__(self, stats: RunStats, width: int, height: int, fps: int, style: str = "full"):
        if style not in STYLES:
            raise ValueError(f"outro style must be one of {', '.join(STYLES)}")
        self.stats, self.W, self.H, self.fps, self.style = stats, width, height, fps, style
        self.win = STYLES[style]
        self.duration = max(w[1] for w in self.win.values() if w)
        self.frames = round(self.duration * fps)
        self.u = min(width, height) / 1080
        self.landscape = width >= height
        self.crew = crew_for(stats)
        self.tiles = tiles_for(stats) if self.win["numbers"] else []
        self.credit = short_credit(stats.music_credit)
        self._bg = self._background()
        rng = np.random.default_rng(11)
        self._grain = [rng.integers(-2, 3, size=(height, width), dtype=np.int8) for _ in range(3)]

        n = max(1, len(self.crew))
        self.cols = n if self.landscape else min(3, n)
        self.rows = math.ceil(n / self.cols)
        self.slot_w = width * (0.9 if self.landscape else 0.94) / self.cols
        body = (0.18 if self.landscape else 0.11) * height
        self.unit = min(body / 92, self.slot_w / 135)
        self._sprites: dict[tuple[int, int, bool], Image.Image] = {}
        self._anchor: dict[int, tuple[float, float, float]] = {}  # anchor fractions + body x offset (px)
        self._shadows: dict[int, Image.Image] = {}
        for i, m in enumerate(self.crew):
            sprite = self._sprite(i, 0, False)
            box = sprite.getbbox() or (0, 0, sprite.width, sprite.height)
            ax = (box[0] + box[2]) / 2 / sprite.width
            body_x = (92 if m.prop == "cards" else 86) * self.unit - ax * sprite.width
            self._anchor[i] = (ax, GROUND * self.unit / sprite.height, body_x)
            self._shadows[i] = shadow_sprite(self.unit, 128 if m.prop == "cards" else m.body.a * 2)
        self.cues = self._cues()

    # ---- building blocks -----------------------------------------------------------------------

    def px(self, base: float) -> int:
        return max(8, round(base * self.u))

    def _background(self) -> Image.Image:
        return background(self.W, self.H)

    def _sprite(self, i: int, pose: int, blink: bool) -> Image.Image:
        key = (i, pose, blink)
        if key not in self._sprites:
            self._sprites[key] = draw_member(self.crew[i], self.unit, pose, blink)
        return self._sprites[key]

    def _slot(self, i: int) -> tuple[float, float]:
        row, col = divmod(i, self.cols)
        in_row = min(self.cols, len(self.crew) - row * self.cols)
        x = self.W / 2 + (col - (in_row - 1) / 2) * self.slot_w
        feet = self.H * (0.575 if self.landscape else (0.38, 0.63)[min(row, 1)])
        return x, feet

    def _pop_time(self, i: int) -> float:
        start = self.win["crew"][0]  # type: ignore[index]
        return start + 0.3 + (0.3 if self.style == "full" else 0.22) * i

    def _punch_time(self) -> float:
        return self._pop_time(len(self.crew) - 1) + (0.75 if self.style == "full" else 0.5)

    def _character(self, img: Image.Image, i: int, t: float, x: float, feet: float, *, scale: float = 1.0,
                   alpha: float = 1.0, hop: float = 0.0) -> None:
        m = self.crew[i]
        pose = int(((t * m.pose_rate) + 0.37 * i) % 1.0 * POSES)
        period = 2.7 + 0.35 * i
        blink = ((t + 0.9 * i) % period) < 0.13
        sprite = self._sprite(i, pose, blink)
        ax, ay, body_x = self._anchor[i]
        bob = (0.5 + 0.5 * math.sin(2 * math.pi * (0.8 * t + 0.29 * i))) * 2.6 * self.unit * scale
        lift = bob + hop * self.unit * scale
        shadow = self._shadows[i]
        sx = x + body_x * scale  # under the body (the anchor is the centre of body + prop)
        place(img, shadow, sx, feet, alpha=alpha * max(0.35, 1 - lift / (30 * self.unit * scale)),
              scale=scale * (1 - min(0.3, lift / (60 * self.unit * scale))))
        place(img, sprite, x, feet - lift, alpha=alpha, scale=scale, anchor=(ax, ay))

    # ---- scenes ----------------------------------------------------------------------------------

    def _confess(self, img: Image.Image, t: float) -> None:
        t0, t1 = self.win["confess"]  # type: ignore[misc]
        tau = t - t0
        gone = ease_in_out(ramp(t, t1 - 0.45, 0.45))
        if gone >= 1:
            return
        drift = -0.02 * self.H * gone
        fast = self.style == "short"
        size1 = self.px(96 if self.landscape else 84)
        f1 = font("Fraunces-SemiBold", size1)
        words = LINE1.split()
        widths = [f1.getlength(w) for w in words]
        space = f1.getlength(" ")
        x = self.W / 2 - (sum(widths) + space * (len(words) - 1)) / 2
        y1 = self.H * 0.45
        for k, word in enumerate(words):
            e = ease_out_cubic(ramp(tau, 0.25 + (0.1 if fast else 0.13) * k, 0.4))
            place(img, text_sprite(word, "Fraunces-SemiBold", size1, CREAM), x + widths[k] / 2,
                  y1 + (1 - e) * 0.025 * self.H + drift, alpha=e * (1 - gone))
            x += widths[k] + space
        e2 = ease_out_cubic(ramp(tau, 0.95 if fast else 1.3, 0.45))
        line2 = text_sprite(LINE2, "Nunito-SemiBold", self.px(44), MUTED, max_width=round(self.W * 0.86))
        place(img, line2, self.W / 2, self.H * 0.565 + (1 - e2) * 0.02 * self.H + drift, alpha=e2 * (1 - gone),
              anchor=(0.5, 0.0))

    def _crew(self, img: Image.Image, t: float) -> None:
        t0, t1 = self.win["crew"]  # type: ignore[misc]
        gone = ease_in_out(ramp(t, t1 - 0.45, 0.45))
        if t < t0 or gone >= 1:
            return
        keep = 1 - gone
        header = text_sprite("THE CREW", "Nunito-ExtraBold", self.px(28), MUTED, tracking=0.3)
        place(img, header, self.W / 2, self.H * (0.21 if self.landscape else 0.12),
              alpha=ease_out_cubic(ramp(t, t0, 0.4)) * keep)
        punch = self._punch_time()
        show_roles = self.style == "full"
        for i, m in enumerate(self.crew):
            x, feet = self._slot(i)
            tp = self._pop_time(i)
            if t < tp:
                continue
            grow = ease_out_back(ramp(t, tp, 0.42))
            hop = 16 * math.sin(math.pi * ramp(t, punch + 0.05 * i, 0.38)) if t >= punch + 0.05 * i else 0.0
            self._character(img, i, t, x, feet, scale=max(0.01, grow), alpha=min(1.0, 4 * ramp(t, tp, 0.42)) * keep,
                            hop=hop)
            e_name = ease_out_cubic(ramp(t, tp + 0.18, 0.35))
            name = text_sprite(m.name, "Nunito-ExtraBold", self.px(40), m.label_color)
            gap = 0.045 * self.H
            place(img, name, x, feet + gap + (1 - e_name) * 0.012 * self.H, alpha=e_name * keep)
            if show_roles:
                e_role = ease_out_cubic(ramp(t, tp + 0.3, 0.35))
                role = text_sprite("\n".join(s for s in (m.role, m.note) if s), "Nunito-SemiBold", self.px(27),
                                   MUTED, max_width=round(self.slot_w * 0.96))
                place(img, role, x, feet + gap + 0.032 * self.H + (1 - e_role) * 0.012 * self.H,
                      alpha=e_role * keep, anchor=(0.5, 0.0))
        e = ease_out_cubic(ramp(t, punch, 0.5))
        if e > 0:
            line = text_sprite(PUNCHLINE, "Fraunces-SemiBoldItalic", self.px(66), CREAM, max_width=round(self.W * 0.9))
            place(img, line, self.W / 2, self.H * (0.85 if self.landscape else 0.86) + (1 - e) * 0.02 * self.H,
                  alpha=e * keep)

    def _numbers(self, img: Image.Image, t: float) -> None:
        if not self.win["numbers"] or not self.tiles:
            return
        t0, t1 = self.win["numbers"]  # type: ignore[misc]
        gone = ease_in_out(ramp(t, t1 - 0.4, 0.4))
        if t < t0 or gone >= 1:
            return
        keep = 1 - gone
        header = text_sprite("BEHIND THE SCENES", "Nunito-ExtraBold", self.px(28), MUTED, tracking=0.3)
        place(img, header, self.W / 2, self.H * (0.2 if self.landscape else 0.12),
              alpha=ease_out_cubic(ramp(t, t0, 0.4)) * keep)
        n = len(self.tiles)
        cols = n if self.landscape else 2
        tile_w = self.W * (0.9 if self.landscape else 0.94) / cols
        for k, tile in enumerate(self.tiles):
            row, col = divmod(k, cols)
            in_row = min(cols, n - row * cols)
            x = self.W / 2 + (col - (in_row - 1) / 2) * tile_w
            y = self.H * ((0.44,) if self.landscape else (0.33, 0.62))[row]
            start = t0 + 0.2 + 0.18 * k
            appear = ease_out_cubic(ramp(t, start, 0.35))
            if appear <= 0:
                continue
            p = ease_out_cubic(ramp(t, start + 0.1, 1.2))
            if tile.clock_from is not None:
                text = _clock(tile.clock_from + (tile.value - tile.clock_from) * p)
            else:
                text = f"{round(tile.value * p):,}"
            big = text_sprite(text, "Fraunces-SemiBold", self.px(104), CREAM)
            dy = (1 - appear) * 0.02 * self.H
            place(img, big, x, y + dy, alpha=appear * keep)
            label = text_sprite(tile.label, "Nunito-SemiBold", self.px(28), MUTED, max_width=round(tile_w * 0.86))
            place(img, label, x, y + 0.08 * self.H + dy, alpha=appear * keep, anchor=(0.5, 0.0))
            extra_y = y + 0.22 * self.H + dy
            if tile.clock_from:
                self._bar(img, x, extra_y, tile_w * 0.62, 1 - (1 - tile.value / tile.clock_from) * p, appear * keep)
            elif len(tile.trail) >= 2:
                self._trail(img, x, extra_y, tile_w * 0.5, tile.trail, p, appear * keep)

    def _bar(self, img: Image.Image, x: float, y: float, width: float, fill: float, alpha: float) -> None:
        h = max(4, round(0.008 * self.H))
        layer = Image.new("RGBA", (round(width) + 2, h + 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle([1, 1, round(width), h], radius=h / 2, fill=(255, 255, 255, 40))
        d.rounded_rectangle([1, 1, max(h, round(width * fill)), h], radius=h / 2, fill=(*AMBER, 255))
        place(img, layer, x, y, alpha=alpha)

    def _trail(self, img: Image.Image, x: float, y: float, width: float, trail: tuple[float, ...], p: float,
               alpha: float) -> None:
        h = 0.05 * self.H
        lo, hi = min(trail), max(trail)
        span = max(hi - lo, 0.5)
        pts = [(width * k / (len(trail) - 1), h * (1 - (v - lo) / span)) for k, v in enumerate(trail)]
        pad = self.px(14)
        layer = Image.new("RGBA", (round(width) + 2 * pad, round(h) + 2 * pad), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        shown = max(1, math.ceil(p * (len(pts) - 1)))
        line = [(px + pad, py + pad) for px, py in pts[: shown + 1]]
        d.line(line, fill=(*AMBER, 255), width=max(2, self.px(4)), joint="curve")
        r = max(3, self.px(6))
        for px_, py_ in line:
            d.ellipse([px_ - r, py_ - r, px_ + r, py_ + r], fill=(*AMBER, 255))
        place(img, layer, x, y + h / 2, alpha=alpha)
        small = self.px(24)
        for (px_, py_), v in ((pts[0], trail[0]), (pts[-1], trail[-1])):
            label = text_sprite(f"{v:.1f}", "Nunito-ExtraBold", small, MUTED)
            place(img, label, x - width / 2 + px_, y - h / 2 + py_ - self.px(30), alpha=alpha * (p if v is trail[-1] else 1))

    def _sign(self, img: Image.Image, t: float) -> None:
        t0, _ = self.win["sign"]  # type: ignore[misc]
        if t < t0:
            return
        tau = t - t0
        e1 = ease_out_cubic(ramp(tau, 0.1, 0.45))
        small = text_sprite(SIGN_SMALL, "Nunito-SemiBold", self.px(42), MUTED)
        place(img, small, self.W / 2, self.H * 0.33 + (1 - e1) * 0.015 * self.H, alpha=e1)
        e2 = ease_out_cubic(ramp(tau, 0.45, 0.6))
        big = text_sprite(SIGN_BIG, "Fraunces-SemiBold", self.px(116), CREAM, max_width=round(self.W * 0.92))
        place(img, big, self.W / 2, self.H * 0.45, alpha=e2, scale=0.96 + 0.04 * e2)
        n = len(self.crew)
        gap = min(self.W * 0.09, self.W * 0.86 / max(1, n))
        for i in range(n):
            tp = 0.75 + 0.07 * i
            grow = ease_out_back(ramp(tau, tp, 0.4))
            if grow <= 0:
                continue
            x = self.W / 2 + (i - (n - 1) / 2) * gap
            self._character(img, i, t, x, self.H * 0.735, scale=0.55 * max(0.01, grow),
                            alpha=min(1.0, 4 * ramp(tau, tp, 0.4)))
        if self.credit:
            e3 = ease_out_cubic(ramp(tau, 1.0, 0.5))
            credit = text_sprite(self.credit, "Nunito-SemiBold", self.px(25), DIM, max_width=round(self.W * 0.9))
            place(img, credit, self.W / 2, self.H * 0.905, alpha=e3)

    # ---- frames and sound ------------------------------------------------------------------------

    def frame(self, t: float) -> Image.Image:
        img = self._bg.copy()
        self._confess(img, t)
        self._crew(img, t)
        self._numbers(img, t)
        self._sign(img, t)
        arr = np.asarray(img.convert("RGB"), dtype=np.int16)
        arr += self._grain[int(t * self.fps) % len(self._grain)][..., None]
        level = ease_in_out(ramp(t, 0.0, 0.45)) * (1 - ease_in_out(ramp(t, self.duration - 0.7, 0.7)))
        if level < 0.999:
            arr = arr * level
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    def frame_at(self, index: int) -> Image.Image:
        return self.frame(index / self.fps)

    def _cues(self) -> list[SoundCue]:
        cues = [SoundCue(0.25, "pluck", 392.0, 0.55),
                SoundCue(0.95 if self.style == "short" else 1.3, "pluck", 523.25, 0.4)]
        for i in range(len(self.crew)):
            cues.append(SoundCue(self._pop_time(i) + 0.02, "pop", PENTATONIC[i % len(PENTATONIC)], 0.6))
        if self.crew:
            cues.append(SoundCue(self._punch_time(), "chime", 0.0, 0.5))
        if self.win["numbers"] and self.tiles:
            t0 = self.win["numbers"][0]  # type: ignore[index]
            for k in range(len(self.tiles)):
                start = t0 + 0.2 + 0.18 * k + 0.1
                for j in range(1, 9):  # ticks slow down as the counter settles (ease-out cubic)
                    cues.append(SoundCue(start + 1.2 * (1 - (1 - j / 9) ** (1 / 3)), "tick", 0.0, 0.16))
        sign = self.win["sign"][0]  # type: ignore[index]
        cues.append(SoundCue(sign + 0.45, "swell_end", 0.0, 0.3))
        for k, f in enumerate((261.63, 392.0, 659.25)):
            cues.append(SoundCue(sign + 0.45 + 0.03 * k, "pluck", f, 0.35))
        return cues
