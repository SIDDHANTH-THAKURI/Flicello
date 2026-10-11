"""Characters and set pieces that live in the footage: the crew acting - jumping in arcs with squash and
stretch, ducking, hop-walking between points, waving, facing either way - with contact shadows on the
ground; 3D letters that rise out of the landscape and cast shadows; the dark card the crew jumps out of;
speech bubbles. Positions follow the tracked scene when the graphic belongs to the world."""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ..outro.card import MUTED, background
from ..outro.crew import CREW, GROUND, POSES, draw_member, shadow_sprite
from ..outro.draw import darken, ease_in_out, ease_out_back, ease_out_cubic, font, lighten, place, ramp, text_sprite
from ..schemas import Graphic
from .track import transform_point, transform_scale

INK = (30, 26, 36)
NAMES = {m.name.lower(): i for i, m in enumerate(CREW)}


def member_index(name: str) -> int:
    return NAMES.get(name.strip().lower(), 0)


@lru_cache(maxsize=768)
def member_sprite(index: int, unit: float, pose: int, blink: bool) -> Image.Image:
    return draw_member(CREW[index], unit, pose, blink)


def parse_actions(style: str) -> tuple[list[tuple[str, float]], set[str]]:
    """'jump:0.0,duck:4.0,wave:6.0,flip' -> ([('jump', 0.0), ...], {'flip'})."""
    actions, flags = [], set()
    for part in (p.strip() for p in style.split(",") if p.strip()):
        if ":" in part:
            name, t = part.split(":", 1)
            try:
                actions.append((name.strip().lower(), float(t)))
            except ValueError:
                continue
        else:
            flags.add(part.lower())
    return actions, flags


def character_pose(g: Graphic, tau: float) -> dict:
    """Where the character is and how it's deformed at tau: position (fractions), lift (fraction of the
    short side), squash/stretch, tilt, appear scale."""
    pts = list(g.points) or [(g.x, g.y)]
    times = list(g.beats)[:len(pts)]
    while len(times) < len(pts):
        times.append((times[-1] + 1.0) if times else 0.0)
    actions, flags = parse_actions(g.style)
    jumps = {round(t, 3) for name, t in actions if name == "jump"}
    x, y, lift, sx, sy, angle = pts[-1][0], pts[-1][1], 0.0, 1.0, 1.0, 0.0
    if tau < times[0]:
        x, y = pts[0]
    for k in range(len(pts) - 1):
        t0, t1 = times[k], times[k + 1]
        if t0 <= tau < t1:
            p = (tau - t0) / max(t1 - t0, 1e-6)
            (x0, y0), (x1, y1) = pts[k], pts[k + 1]
            dist = math.hypot(x1 - x0, y1 - y0)
            if round(t0, 3) in jumps:  # a ballistic arc with stretch on the way
                x, y = x0 + (x1 - x0) * p, y0 + (y1 - y0) * p
                lift = (0.30 + 0.6 * dist) * math.sin(math.pi * p)
                sx, sy = (0.86, 1.18) if p < 0.5 else (0.92, 1.1)
            else:  # hop-walking
                e = ease_in_out(p)
                x, y = x0 + (x1 - x0) * e, y0 + (y1 - y0) * e
                hops = max(1, round(dist / 0.05))
                lift = 0.022 * abs(math.sin(math.pi * p * hops)) if dist > 0.002 else 0.0
            break
    for k in range(1, len(times)):  # land: a quick squash after every arrival
        since = tau - times[k]
        if 0 <= since < 0.18 and round(times[k - 1], 3) in jumps:
            q = since / 0.18
            sx, sy = 1 + 0.28 * (1 - q), 1 - 0.24 * (1 - q)
    for name, t in actions:
        since = tau - t
        if name == "duck" and 0 <= since < 0.9:
            q = 1 - ease_out_back(ramp(since, 0.55, 0.35)) if since > 0.55 else ease_out_cubic(ramp(since, 0, 0.12))
            sx, sy = 1 + 0.25 * q, 1 - 0.3 * q
        elif name == "wave" and 0 <= since < 1.4:
            angle = 11 * math.sin(2 * math.pi * 2.5 * since) * (1 - since / 1.4)
            lift = max(lift, 0.012 * abs(math.sin(2 * math.pi * 2.5 * since)))
        elif name == "shake" and 0 <= since < 0.6:
            angle = 7 * math.sin(2 * math.pi * 9 * since)
    appear = 1.0
    pops = [t for name, t in actions if name == "pop"]
    if pops:
        appear = ease_out_back(ramp(tau, pops[0], 0.32)) if tau >= pops[0] else 0.0
    return {"x": x, "y": y, "lift": lift, "sx": sx, "sy": sy, "angle": angle, "flip": "flip" in flags,
            "appear": appear}


def draw_character(layer: Image.Image, g: Graphic, tau: float, fade: float, h: np.ndarray) -> None:
    w, hh = layer.size
    s = min(w, hh)
    st = character_pose(g, tau)
    if st["appear"] <= 0.01:
        return
    fx, fy = st["x"] * w, st["y"] * hh
    zoom = 1.0
    if g.anchor == "world":
        fx, fy = transform_point(h, fx, fy)
        zoom = transform_scale(h)
    i = member_index(g.text or "scout")
    m = CREW[i]
    unit = round(0.105 * s * g.size * zoom / 92, 2)
    pose = int(((tau * m.pose_rate) + 0.37 * i) % 1.0 * POSES)
    blink = ((tau + 0.9 * i) % (2.7 + 0.35 * i)) < 0.13
    sprite = member_sprite(i, unit, pose, blink)
    box = sprite.getbbox() or (0, 0, sprite.width, sprite.height)
    ax, ay = (box[0] + box[2]) / 2 / sprite.width, GROUND * unit / sprite.height
    if st["flip"]:
        sprite, ax = ImageOps.mirror(sprite), 1 - ax
    if abs(st["sx"] - 1) > 0.01 or abs(st["sy"] - 1) > 0.01:
        sprite = sprite.resize((max(1, round(sprite.width * st["sx"])), max(1, round(sprite.height * st["sy"]))),
                               Image.BICUBIC)
    if abs(st["angle"]) > 0.2:  # rock around the feet: rotate, then keep the feet where they were
        cx, cy = ax * sprite.width, ay * sprite.height
        big = Image.new("RGBA", (sprite.width * 2, sprite.height * 2), (0, 0, 0, 0))
        big.alpha_composite(sprite, (round(sprite.width - cx), round(sprite.height - cy)))
        sprite = big.rotate(st["angle"], resample=Image.BICUBIC)
        ax, ay = 0.5, 0.5
    lift = st["lift"] * s * zoom
    bob = (0.5 + 0.5 * math.sin(2 * math.pi * (0.8 * tau + 0.29 * i))) * 1.6 * unit
    a = st["appear"]
    place(layer, shadow_sprite(unit, m.body.a * 2), fx + (0.0 if not st["flip"] else 0.0), fy,
          alpha=0.75 * fade * max(0.3, 1 - lift / (0.25 * s)), scale=max(0.01, a * (1 - min(0.4, lift / (0.5 * s)))))
    place(layer, sprite, fx, fy - lift - bob, alpha=min(1.0, 3 * a) * fade, scale=max(0.01, a), anchor=(ax, ay))


@lru_cache(maxsize=256)
def extruded_glyph(ch: str, px: int, color: tuple[int, int, int], depth: int) -> Image.Image:
    """A letter with real-looking depth: the face, and its sides receding up and to the right in
    darker shades (lit from the front-left)."""
    face = text_sprite(ch, "Nunito-Black", px, color)
    mask = face.getchannel("A")
    dx, dy = 0.55, -0.75
    out = Image.new("RGBA", (face.width + round(depth * abs(dx)) + 2, face.height + round(depth * abs(dy)) + 2),
                    (0, 0, 0, 0))
    ox, oy = 0, round(depth * abs(dy))
    for k in range(depth, 0, -1):
        shade = darken(color, 0.72 - 0.3 * (1 - k / depth))
        solid = Image.new("RGBA", face.size, (*shade, 255))
        solid.putalpha(mask)
        out.alpha_composite(solid, (round(ox + k * dx), round(oy + k * dy)))
    lit = Image.new("RGBA", face.size, (*lighten(color, 0.08), 255))
    lit.putalpha(mask)
    out.alpha_composite(lit, (ox, oy))
    return out


def paint_sign(layer: Image.Image, g: Graphic, tau: float, fade: float) -> None:
    """Big 3D letters standing on the ground at (x, y) - their base - rising out of it one by one, each
    casting a soft shadow on the ground behind it."""
    w, hh = layer.size
    s = min(w, hh)
    text = g.text.upper()
    px = max(12, round(0.11 * s * g.size))
    f = font("Nunito-Black", px)
    depth = max(5, round(px * 0.26))
    color = (244, 239, 230) if g.color == "cream" else {"white": (255, 255, 255), "amber": (255, 196, 96)}.get(
        g.color, (244, 239, 230))
    total = f.getlength(text)
    x = g.x * w - total / 2
    base = g.y * hh
    shadow_layer = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    letters = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    k = 0
    for ch in text:
        cw = f.getlength(ch)
        if ch != " ":
            p = ease_out_back(ramp(tau, 0.07 * k, 0.4), 1.2)
            if p > 0:
                glyph = extruded_glyph(ch, px, color, depth)
                rise = (1 - min(1.0, p)) * glyph.height * 0.9
                gx, gy = x - 0.0, base - glyph.height + rise
                letters.alpha_composite(glyph, (round(gx), round(gy)))
                sh = glyph.getchannel("A").resize((glyph.width, max(1, round(glyph.height * 0.22))), Image.BILINEAR)
                skew = Image.new("L", (glyph.width + glyph.height // 2, sh.height), 0)
                skew.paste(sh, (glyph.height // 4, 0))
                skew = skew.transform(skew.size, Image.AFFINE, (1, 0.9, 0, 0, 1, 0), resample=Image.BILINEAR)
                dark = Image.new("RGBA", skew.size, (12, 10, 16, 0))
                dark.putalpha(skew.point(lambda v: round(v * 0.42 * min(1.0, p))))
                shadow_layer.alpha_composite(dark, (round(gx + glyph.width * 0.05), round(base - sh.height * 0.6)))
            k += 1
        x += cw
    # letters rising out of the ground are hidden below it
    cut = np.asarray(letters).copy()
    cut[int(base) + 1:, :, 3] = 0
    layer.alpha_composite(shadow_layer.filter(ImageFilter.GaussianBlur(max(1, s * 0.006))))
    rising = Image.fromarray(cut, "RGBA")
    layer.alpha_composite(rising if fade >= 0.999 else _faded(rising, fade))


def _faded(img: Image.Image, fade: float) -> Image.Image:
    out = img.copy()
    out.putalpha(img.getchannel("A").point(lambda v: round(v * fade)))
    return out


@lru_cache(maxsize=4)
def _card_face(w: int, h: int, text: str) -> Image.Image:
    img = background(w, h).copy()
    if text:
        s = min(w, h)
        header = text_sprite(text.upper(), "Nunito-ExtraBold", max(10, round(0.032 * s)), MUTED, tracking=0.3,
                             max_width=round(w * 0.9))
        place(img, header, w / 2, h * 0.30)
    return img


def draw_card(layer: Image.Image, g: Graphic, tau: float, fade: float) -> None:
    """The dark card of the intro and outro filling the frame; at its end it splits down the middle and
    the halves slide away like doors, revealing the footage underneath."""
    w, hh = layer.size
    face = _card_face(w, hh, g.text)
    life = (g.until - g.at) if g.until is not None else 4.0
    p = ease_in_out(ramp(tau, life - 0.55, 0.5))
    if p >= 1:
        return
    half = w // 2
    left, right = face.crop((0, 0, half, hh)), face.crop((half, 0, w, hh))
    shift = p * (half + 0.05 * w)
    layer.alpha_composite(left, (round(-shift), 0))
    layer.alpha_composite(right, (round(half + shift), 0))


def draw_bubble(layer: Image.Image, g: Graphic, tau: float, fade: float, h: np.ndarray) -> None:
    """A comic speech bubble pointing at (x, y) - e.g. a character's head."""
    w, hh = layer.size
    s = min(w, hh)
    tx, ty = transform_point(h, g.x * w, g.y * hh) if g.anchor == "world" else (g.x * w, g.y * hh)
    ox, oy = g.points[0] if g.points else (0.05, -0.08)
    cx, cy = tx + ox * w, ty + oy * hh
    px = max(10, round(0.046 * s * g.size))
    text = text_sprite(g.text, "Nunito-Black", px, INK)
    pad = round(px * 0.55)
    bw, bh = text.width + 2 * pad, text.height + pad
    lw = max(2, round(0.004 * s))
    margin = lw + 2
    sprite = Image.new("RGBA", (bw + 2 * margin, bh + 2 * margin), (0, 0, 0, 0))
    d = ImageDraw.Draw(sprite)
    d.rounded_rectangle([margin, margin, margin + bw, margin + bh], radius=bh // 2, fill=(255, 255, 255, 255),
                        outline=(*INK, 255), width=lw)
    sprite.alpha_composite(text, (margin + pad, margin + pad // 2))
    pop = ease_out_back(ramp(tau, 0, 0.25))
    if pop <= 0.01:
        return
    wob = 1 + 0.03 * math.sin(8 * tau)
    tail = ImageDraw.Draw(layer)
    edge_y = cy + (bh / 2) * pop * (1 if ty > cy else -1)
    base = 0.018 * s * pop
    tail.polygon([(cx - base, edge_y), (cx + base, edge_y), (tx, ty)], fill=(255, 255, 255, round(255 * fade)),
                 outline=(*INK, round(255 * fade)))
    place(layer, sprite, cx, cy, alpha=min(1.0, 3 * pop) * fade, scale=max(0.01, pop * wob))
