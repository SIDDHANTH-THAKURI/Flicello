"""The graphics, drawn per frame with Pillow in the outro's visual language (Nunito Black titles, cream
and amber, the crew characters). Sizes are in units of the frame's short side, so the same design works
in 16:9 and 9:16. "Painted" graphics (titles, words, doodles) are drawn in the shot's first-frame
coordinates and warped with the camera when they belong to the world; "pinned" graphics (labels, the
crew) follow a tracked point but stay upright."""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..outro.crew import CREW, GROUND, POSES, draw_member, shadow_sprite
from ..outro.draw import ease_in_out, ease_out_back, ease_out_cubic, font, place, ramp, text_sprite
from ..schemas import Graphic
from .track import transform_point, transform_scale

COLORS = {"cream": (244, 239, 230), "white": (255, 255, 255), "amber": (255, 196, 96), "teal": (64, 200, 182),
          "coral": (255, 122, 104)}
SHADOW = (12, 10, 16)

# A stylised stretch of the New South Wales coast, north to south (lat, lon), and the places on it.
COAST = [(-33.58, 151.33), (-33.66, 151.31), (-33.72, 151.30), (-33.79, 151.29), (-33.83, 151.28), (-33.85, 151.28),
         (-33.89, 151.28), (-33.92, 151.26), (-33.97, 151.25), (-34.00, 151.24), (-33.985, 151.19), (-34.00, 151.17),
         (-34.02, 151.21), (-34.04, 151.21), (-34.06, 151.16), (-34.09, 151.15), (-34.13, 151.10), (-34.18, 151.05),
         (-34.23, 150.99), (-34.27, 150.96), (-34.32, 150.93), (-34.36, 150.92), (-34.40, 150.91), (-34.42, 150.915),
         (-34.46, 150.91), (-34.48, 150.92), (-34.53, 150.88), (-34.57, 150.87), (-34.59, 150.90), (-34.62, 150.86),
         (-34.64, 150.86), (-34.67, 150.865), (-34.72, 150.84), (-34.77, 150.82)]
PLACES = {"sydney": (-33.87, 151.21), "wollongong": (-34.425, 150.89), "minnamurra": (-34.635, 150.85),
          "kiama": (-34.67, 150.855), "bondi": (-33.89, 151.27), "cronulla": (-34.06, 151.15)}
ROAD = [(-33.87, 151.21), (-33.95, 151.10), (-34.05, 151.02), (-34.18, 150.97), (-34.30, 150.90), (-34.425, 150.89),
        (-34.52, 150.84), (-34.635, 150.85), (-34.67, 150.855)]


def rgb(name: str) -> tuple[int, int, int]:
    if name.startswith("#") and len(name) == 7:
        return tuple(int(name[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    return COLORS.get(name, COLORS["cream"])


# ---- hand-drawn strokes --------------------------------------------------------------------------

def wobble(pts: np.ndarray, amp: float, seed: int) -> np.ndarray:
    """Smooth, low-frequency jitter along a polyline: the hand-drawn look (re-seeded = 'boiling')."""
    n = len(pts)
    if n < 2 or amp <= 0:
        return pts
    rng = np.random.default_rng(seed)
    knots = max(2, n // 8)
    noise = rng.normal(0, amp, size=(knots, 2))
    idx = np.linspace(0, knots - 1, n)
    off = np.stack([np.interp(idx, np.arange(knots), noise[:, k]) for k in (0, 1)], axis=1)
    return pts + off


def partial(pts: np.ndarray, frac: float) -> np.ndarray:
    """The first `frac` of a polyline by length (a stroke being drawn)."""
    if frac >= 1 or len(pts) < 2:
        return pts
    if frac <= 0:
        return pts[:1]
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.concatenate([[0], np.cumsum(seg)])
    target = cum[-1] * frac
    k = int(np.searchsorted(cum, target))
    if k <= 0:
        return pts[:1]
    a = (target - cum[k - 1]) / max(seg[k - 1], 1e-9)
    return np.vstack([pts[:k], pts[k - 1] + a * (pts[k] - pts[k - 1])])


def stroke(draw: ImageDraw.ImageDraw, pts: np.ndarray, width: float, fill: tuple[int, ...], shadow: bool = True) -> None:
    if len(pts) < 2:
        return
    w = max(1, round(width))
    layers = ([(pts + width * 0.35, (*SHADOW, 110), w + 2)] if shadow else []) + [(pts, fill, w)]
    for p, colour, wd in layers:
        seq = [tuple(map(float, q)) for q in p]
        draw.line(seq, fill=colour, width=wd, joint="curve")
        r = wd / 2
        for x, y in (seq[0], seq[-1]):
            draw.ellipse([x - r, y - r, x + r, y + r], fill=colour)


def _star(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    pts = []
    for k in range(8):
        rr = r if k % 2 == 0 else r * 0.32
        a = math.pi / 4 * k - math.pi / 2
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


# ---- painted graphics (drawn in first-frame coordinates) ------------------------------------------

def _shadowed(layer: Image.Image, blur: float) -> Image.Image:
    """The layer over a soft dark shadow of itself, so light text reads on a bright sky."""
    alpha = layer.getchannel("A").filter(ImageFilter.GaussianBlur(blur))
    shadow = Image.new("RGBA", layer.size, (*SHADOW, 0))
    shadow.putalpha(alpha.point(lambda v: round(v * 0.55)))
    shadow.alpha_composite(layer)
    return shadow


def paint_title(layer: Image.Image, g: Graphic, tau: float, fade: float) -> None:
    w, h = layer.size
    s = min(w, h)
    px = max(12, round(g.size * 0.21 * s))
    f = font("Nunito-Black", px)
    lines = g.text.upper().split("\n")
    lead = px * 0.98
    y0 = g.y * h - lead * (len(lines) - 1) / 2
    k = 0
    for li, line in enumerate(lines):
        x = g.x * w - f.getlength(line) / 2
        y = y0 + li * lead
        for ch in line:
            cw = f.getlength(ch)
            if ch != " ":
                t0 = g.beats[k] if k < len(g.beats) else 0.055 * k
                p = ramp(tau, t0, 0.24)
                if p > 0:
                    if g.style == "rise":
                        dy, scale = (1 - ease_out_cubic(p)) * 0.4 * px, 1.0
                    else:  # slam: in from big, settles with a small overshoot
                        dy, scale = 0.0, 1 + 0.95 * (1 - ease_out_back(p, 1.4))
                    place(layer, text_sprite(ch, "Nunito-Black", px, rgb(g.color)), x + cw / 2, y + dy,
                          alpha=min(1.0, 3 * p) * fade, scale=max(0.05, scale))
                k += 1
            x += cw


def paint_words(layer: Image.Image, g: Graphic, tau: float, fade: float) -> None:
    w, h = layer.size
    words = [x for x in g.text.split("|") if x]
    starts = list(g.beats) or [0.45 * k for k in range(len(words))]
    shown = [k for k, t0 in enumerate(starts[:len(words)]) if tau >= t0]
    if not shown:
        return
    k = shown[-1]
    p = ramp(tau, starts[k], 0.16)
    px = max(12, round(g.size * 0.12 * min(w, h)))
    sprite = text_sprite(words[k].upper(), "Nunito-Black", px, rgb(g.color))
    place(layer, sprite, g.x * w, g.y * h, alpha=min(1.0, 4 * p) * fade, scale=1 + 0.45 * (1 - ease_out_cubic(p)))


def paint_doodle(layer: Image.Image, g: Graphic, tau: float, fade: float) -> None:
    w, h = layer.size
    s = min(w, h)
    draw = ImageDraw.Draw(layer)
    colour = (*rgb(g.color), round(255 * fade))
    width = 0.0075 * s * max(0.6, min(1.6, g.size))
    seed = int(tau * 8) * 7919 + sum(map(ord, g.style))  # re-jitter ~8x a second: the line "boils"
    amp = 0.0028 * s
    cx, cy = g.x * w, g.y * h
    style = g.style or "circle"
    if style == "circle":
        rx, ry = 0.13 * s * g.size, 0.17 * s * g.size
        a = np.linspace(0, 2 * math.pi * 1.12, 140) - 2.2
        pts = np.stack([cx + rx * np.cos(a) * (1 + 0.05 * np.sin(3 * a)), cy + ry * np.sin(a)], axis=1)
        stroke(draw, partial(wobble(pts, amp, seed), ease_out_cubic(ramp(tau, 0, 0.45))), width, colour)
    elif style == "arrow" and len(g.points) >= 2:
        (x0, y0), (x1, y1) = (g.points[0][0] * w, g.points[0][1] * h), (g.points[1][0] * w, g.points[1][1] * h)
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        nx, ny = -(y1 - y0) * 0.18, (x1 - x0) * 0.18
        u = np.linspace(0, 1, 60)[:, None]
        p0, pc, p1 = np.array([x0, y0]), np.array([mx + nx, my + ny]), np.array([x1, y1])
        pts = (1 - u) ** 2 * p0 + 2 * (1 - u) * u * pc + u ** 2 * p1
        e = ease_out_cubic(ramp(tau, 0, 0.35))
        line = partial(wobble(pts, amp, seed), e)
        stroke(draw, line, width, colour)
        if e >= 1:
            d = (p1 - pts[-6]) / (np.linalg.norm(p1 - pts[-6]) + 1e-9)
            for ang in (0.5, -0.5):
                rot = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
                tip = p1 - rot @ d * 0.045 * s
                stroke(draw, np.array([tip, p1]), width, colour)
    elif style == "burst":
        e = ease_out_cubic(ramp(tau, 0, 0.45))
        alpha = fade * (1 - ramp(tau, 0.35, 0.35))
        if alpha > 0:
            rng = np.random.default_rng(int(g.x * 1000 + g.y * 100))
            for k in range(12):
                ang = 2 * math.pi * k / 12 + rng.uniform(-0.15, 0.15)
                r0 = (0.08 + 0.12 * e) * s * g.size
                r1 = r0 + (0.06 - 0.02 * e) * s * g.size * rng.uniform(0.7, 1.2)
                seg = np.array([[cx + r0 * math.cos(ang), cy + r0 * math.sin(ang)],
                                [cx + r1 * math.cos(ang), cy + r1 * math.sin(ang)]])
                stroke(draw, seg, width, (*rgb(g.color), round(255 * alpha)))
    elif style == "spiral":
        turns = 2.4
        a = np.linspace(0, 2 * math.pi * turns, 200)
        r = 0.16 * s * g.size * a / a[-1]
        rot = tau * 0.8
        pts = np.stack([cx + r * np.cos(a + rot), cy + r * np.sin(a + rot) * 0.8], axis=1)
        stroke(draw, partial(wobble(pts, amp * 0.6, seed), ease_out_cubic(ramp(tau, 0, 0.7))), width, colour)
    elif style == "stars":
        rng = np.random.default_rng(int(g.x * 997 + g.y * 131))
        for k in range(5):
            ang, dist = rng.uniform(0, 2 * math.pi), rng.uniform(0.06, 0.16) * s * g.size
            sx, sy = cx + dist * math.cos(ang), cy + dist * math.sin(ang)
            e = ease_out_back(ramp(tau, 0.07 * k, 0.25))
            if e > 0:
                r = 0.022 * s * g.size * e * (1 + 0.22 * math.sin(11 * tau + k))
                draw.polygon(_star(sx + 2, sy + 2, r), fill=(*SHADOW, round(100 * fade)))
                draw.polygon(_star(sx, sy, r), fill=colour)
    elif style == "line" and len(g.points) >= 2:
        pts = np.array([(px_ * w, py_ * h) for px_, py_ in g.points], float)
        seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        total = float(seg.sum())
        n = max(2, int(total / (0.028 * s)))
        cum = np.concatenate([[0], np.cumsum(seg)])
        shown = int(n * ease_out_cubic(ramp(tau, 0, 0.6)))
        r = 0.0055 * s
        for k in range(shown):
            d = total * k / (n - 1)
            j = min(len(seg) - 1, int(np.searchsorted(cum, d, side="right") - 1))
            a = (d - cum[j]) / max(seg[j], 1e-9)
            x, y = pts[j] + a * (pts[j + 1] - pts[j])
            draw.ellipse([x - r + 1.5, y - r + 1.5, x + r + 1.5, y + r + 1.5], fill=(*SHADOW, round(100 * fade)))
            draw.ellipse([x - r, y - r, x + r, y + r], fill=colour)
    elif style == "waves":  # sound coming out of a megaphone: arcs expanding and fading, on repeat
        dx, dy = g.points[0] if g.points else (1.0, -0.3)
        base = math.atan2(dy, dx)
        for k in range(3):
            q = (tau * 1.8 + k / 3) % 1.0
            r = (0.03 + 0.09 * q) * s * g.size
            a = (1 - q) * fade
            box = [cx - r, cy - r, cx + r, cy + r]
            deg = math.degrees(base)
            draw.arc([b + 2.5 for b in box], deg - 32, deg + 32, fill=(*SHADOW, round(150 * a)), width=max(2, round(width * 1.6)))
            draw.arc(box, deg - 32, deg + 32, fill=(*rgb(g.color), round(255 * a)), width=max(2, round(width * 1.6)))
    elif style == "underline":
        half = 0.15 * s * g.size
        x = np.linspace(cx - half, cx + half, 80)
        pts = np.stack([x, cy + 0.008 * s * np.sin((x - cx) / half * math.pi * 1.5)], axis=1)
        stroke(draw, partial(wobble(pts, amp, seed), ease_out_cubic(ramp(tau, 0, 0.3))), width, colour)


# ---- pinned graphics (follow a tracked point, stay upright) ---------------------------------------

def draw_label(layer: Image.Image, g: Graphic, tau: float, fade: float, h: np.ndarray) -> None:
    w, hh = layer.size
    s = min(w, hh)
    ax, ay = transform_point(h, g.x * w, g.y * hh) if g.anchor == "world" else (g.x * w, g.y * hh)
    draw = ImageDraw.Draw(layer)
    pop = ease_out_back(ramp(tau, 0, 0.18))
    r = 0.010 * s * g.size * pop
    if r > 0.5:
        draw.ellipse([ax - r * 1.9, ay - r * 1.9, ax + r * 1.9, ay + r * 1.9], fill=(*rgb(g.color), round(70 * fade)))
        draw.ellipse([ax - r, ay - r, ax + r, ay + r], fill=(*rgb("amber"), round(255 * fade)))
    ox, oy = g.points[0] if g.points else (0.10, -0.08)
    ex, ey = ax + ox * w, ay + oy * hh
    e = ease_out_cubic(ramp(tau, 0.1, 0.25))
    if e > 0:
        stroke(draw, np.array([[ax, ay], [ax + (ex - ax) * e, ay + (ey - ay) * e]]), 0.003 * s,
               (*rgb("cream"), round(230 * fade)))
    e2 = ease_out_cubic(ramp(tau, 0.3, 0.25))
    if e2 > 0 and g.text:
        px = max(10, round(0.036 * s * g.size))
        text = text_sprite(g.text, "Nunito-ExtraBold", px, rgb("cream"))
        pad = round(px * 0.45)
        box = Image.new("RGBA", (text.width + 2 * pad, text.height + pad), (0, 0, 0, 0))
        ImageDraw.Draw(box).rounded_rectangle([0, 0, box.width - 1, box.height - 1], radius=box.height // 2,
                                              fill=(*SHADOW, 165))
        box.alpha_composite(text, (pad, pad // 2))
        side = 0.0 if ox >= 0 else 1.0
        place(layer, box, ex + (1 - e2) * 0.02 * s * (1 if ox >= 0 else -1), ey, alpha=e2 * fade, anchor=(side, 0.5))


@lru_cache(maxsize=256)
def _member_sprite(index: int, unit: float, pose: int, blink: bool) -> Image.Image:
    return draw_member(CREW[index], unit, pose, blink)


def draw_crew(layer: Image.Image, g: Graphic, tau: float, fade: float, h: np.ndarray) -> None:
    """The crew characters standing in the shot on a tracked point (their feet), popping in one by one."""
    w, hh = layer.size
    s = min(w, hh)
    names = [n.strip().lower() for n in g.text.split("|") if n.strip()] or ["scout", "muse", "snip"]
    members = [i for i, m in enumerate(CREW) if m.name.lower() in names]
    fx, fy = transform_point(h, g.x * w, g.y * hh) if g.anchor == "world" else (g.x * w, g.y * hh)
    zoom = transform_scale(h) if g.anchor == "world" else 1.0
    unit = round(0.10 * s * g.size * zoom / 92, 2)
    gap = 0.075 * s * g.size * zoom
    for k, i in enumerate(members):
        grow = ease_out_back(ramp(tau, 0.22 * k, 0.4))
        if grow <= 0:
            continue
        m = CREW[i]
        pose = int(((tau * m.pose_rate) + 0.37 * k) % 1.0 * POSES)
        blink = ((tau + 0.9 * k) % (2.7 + 0.35 * k)) < 0.13
        sprite = _member_sprite(i, unit, pose, blink)
        box = sprite.getbbox() or (0, 0, sprite.width, sprite.height)
        ax = (box[0] + box[2]) / 2 / sprite.width
        x = fx + (k - (len(members) - 1) / 2) * gap
        bob = (0.5 + 0.5 * math.sin(2 * math.pi * (0.8 * tau + 0.29 * k))) * 2.0 * unit
        place(layer, shadow_sprite(unit, m.body.a * 2), x, fy, alpha=0.8 * fade, scale=max(0.01, grow))
        place(layer, sprite, x, fy - bob, alpha=min(1.0, 4 * ramp(tau, 0.22 * k, 0.4)) * fade,
              scale=max(0.01, grow), anchor=(ax, GROUND * unit / sprite.height))


# ---- the route map (screen) ----------------------------------------------------------------------

def draw_route(layer: Image.Image, g: Graphic, tau: float, fade: float) -> None:
    """A stylised coast that draws itself, then the trip as a dashed line, place by place."""
    w, hh = layer.size
    s = min(w, hh)
    dim = ease_in_out(ramp(tau, 0, 0.25)) * fade
    layer.alpha_composite(Image.new("RGBA", layer.size, (*SHADOW, round(180 * dim))))
    stops = [PLACES[n.strip().lower()] for n in g.text.split("|") if n.strip().lower() in PLACES] or \
        [PLACES["sydney"], PLACES["wollongong"], PLACES["minnamurra"]]
    lat_c, lon_c = (COAST[0][0] + COAST[-1][0]) / 2, 151.05
    span_lat = COAST[0][0] - COAST[-1][0]
    k = (0.62 if hh > w else 0.8) * hh * g.size / span_lat
    kx = k * math.cos(math.radians(abs(lat_c)))

    def xy(lat: float, lon: float) -> tuple[float, float]:
        return g.x * w + (lon - lon_c) * kx, g.y * hh - (lat - lat_c) * k

    draw = ImageDraw.Draw(layer)
    coast = np.array([xy(*p) for p in COAST])
    stroke(draw, partial(wobble(coast, 0.0012 * s, 3), ease_out_cubic(ramp(tau, 0.1, 0.6))), 0.0060 * s,
           (*rgb("cream"), round(235 * fade)), shadow=False)
    sea = ease_out_cubic(ramp(tau, 0.55, 0.4))
    if sea > 0:
        label = text_sprite("TASMAN SEA", "Fraunces-SemiBoldItalic", round(0.034 * s), (172, 166, 186), tracking=0.15)
        place(layer, label, xy(-34.2, 151.33)[0], xy(-34.2, 151.33)[1], alpha=sea * fade * 0.9)
    # the road: inland of the coast, through the stops in order
    path = [p for p in ROAD if stops[-1][0] <= p[0] + 1e-9] if stops else ROAD
    pts = np.array([xy(*p) for p in path])
    e = ease_in_out(ramp(tau, 0.7, 1.5))
    shown = partial(pts, e)
    if len(shown) >= 2:  # dashes along what is drawn so far
        seg = np.linalg.norm(np.diff(shown, axis=0), axis=1)
        cum = np.concatenate([[0], np.cumsum(seg)])
        dash = 0.022 * s
        d = 0.0
        while d < cum[-1]:
            a, b = d, min(cum[-1], d + dash * 0.6)
            ia = min(len(seg) - 1, int(np.searchsorted(cum, a, side="right") - 1))
            ib = min(len(seg) - 1, int(np.searchsorted(cum, b, side="right") - 1))
            pa = shown[ia] + (a - cum[ia]) / max(seg[ia], 1e-9) * (shown[ia + 1] - shown[ia])
            pb = shown[ib] + (b - cum[ib]) / max(seg[ib], 1e-9) * (shown[ib + 1] - shown[ib])
            stroke(draw, np.array([pa, pb]), 0.006 * s, (*rgb("amber"), round(255 * fade)), shadow=False)
            d += dash
        hx, hy = shown[-1]
        r = 0.011 * s
        draw.ellipse([hx - r, hy - r, hx + r, hy + r], fill=(*rgb("amber"), round(255 * fade)))
    full = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    for n, (lat, lon) in zip([n for n in g.text.split("|") if n.strip()] or ["Sydney", "Wollongong", "Minnamurra"], stops):
        px_, py_ = xy(lat, lon)
        reach = float(full[int(np.argmin(np.linalg.norm(pts - np.array([px_, py_]), axis=1)))] / max(full[-1], 1e-9))
        if e + 1e-6 < reach:
            continue
        p = ease_out_back(ramp(tau, 0.7 + 1.5 * reach, 0.3))
        r = 0.013 * s * p
        draw.ellipse([px_ - r, py_ - r, px_ + r, py_ + r], fill=(*rgb("cream"), round(255 * fade)))
        name = text_sprite(n.strip(), "Nunito-ExtraBold", round(0.040 * s), rgb("cream"))
        place(layer, name, px_ - 0.03 * s, py_, alpha=min(1.0, 3 * ramp(tau, 0.7 + 1.5 * reach, 0.3)) * fade,
              anchor=(1.0, 0.5))


from .actors import draw_bubble, draw_card, draw_character, paint_sign  # noqa: E402

PAINTED = {"title": paint_title, "words": paint_words, "doodle": paint_doodle, "sign": paint_sign}
FRAME_EFFECTS = ("panels", "snipcut")  # rebuild the whole frame: see frames.py


SHARED = ("label", "crew", "route", "character", "card", "bubble")  # drawn straight onto one shared layer


def shares_layer(g: Graphic) -> bool:
    return g.kind in SHARED


def active(g: Graphic, t: float) -> bool:
    return t >= g.at and (g.until is None or t <= g.until)


def draw_shared(layer: Image.Image, g: Graphic, t: float, h: np.ndarray) -> None:
    """Draw a graphic that needs no layer of its own (no blur, no warp) onto a shared frame layer."""
    tau = t - g.at
    fade = 1.0 - (ramp(t, g.until - 0.2, 0.2) if g.until is not None else 0.0)
    {"label": lambda: draw_label(layer, g, tau, fade, h), "crew": lambda: draw_crew(layer, g, tau, fade, h),
     "route": lambda: draw_route(layer, g, tau, fade), "character": lambda: draw_character(layer, g, tau, fade, h),
     "card": lambda: draw_card(layer, g, tau, fade), "bubble": lambda: draw_bubble(layer, g, tau, fade, h)}[g.kind]()


def render_layer(g: Graphic, t: float, size: tuple[int, int], h: np.ndarray) -> Image.Image | None:
    """The graphic at shot time t as a frame-sized RGBA layer (already moved with the camera)."""
    import cv2

    if not active(g, t):
        return None
    tau = t - g.at
    fade = 1.0 - (ramp(t, g.until - 0.2, 0.2) if g.until is not None else 0.0)
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    if g.kind in PAINTED:
        PAINTED[g.kind](layer, g, tau, fade)
        if g.kind in ("title", "words"):
            layer = _shadowed(layer, 0.012 * min(size))
        if g.anchor == "world" and not np.allclose(h, np.eye(3), atol=1e-4):
            arr = cv2.warpAffine(np.asarray(layer), h[:2].astype(np.float64), size, flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
            layer = Image.fromarray(arr, "RGBA")
    elif g.kind == "label":
        draw_label(layer, g, tau, fade, h)
    elif g.kind == "crew":
        draw_crew(layer, g, tau, fade, h)
    elif g.kind == "route":
        draw_route(layer, g, tau, fade)
    elif g.kind == "character":
        draw_character(layer, g, tau, fade, h)
    elif g.kind == "card":
        draw_card(layer, g, tau, fade)
    elif g.kind == "bubble":
        draw_bubble(layer, g, tau, fade, h)
    elif g.kind in FRAME_EFFECTS:
        return None
    return layer
