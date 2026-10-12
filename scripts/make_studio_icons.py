"""Draw the Flicello mark as PNG app icons (home-screen icons, social previews) with cairo.

    python scripts/make_studio_icons.py        # writes into src/vidosuper/studio/web/static/
"""
from __future__ import annotations

import math
from pathlib import Path

import cairo

STATIC = Path(__file__).resolve().parents[1] / "src" / "vidosuper" / "studio" / "web" / "static"


def rounded(ctx, x, y, w, h, r):
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()


def icon(size: int, path: Path, full_bleed: bool = False, radius: float = 17) -> None:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)
    ctx.scale(size / 64, size / 64)
    grad = cairo.LinearGradient(0, 0, 64, 64)
    grad.add_color_stop_rgb(0.0, 1.0, 0.635, 0.29)
    grad.add_color_stop_rgb(0.55, 1.0, 0.31, 0.545)
    grad.add_color_stop_rgb(1.0, 0.478, 0.361, 1.0)
    rounded(ctx, 0, 0, 64, 64, 0.01 if full_bleed else radius)
    ctx.set_source(grad)
    ctx.fill()
    if full_bleed:  # a maskable icon keeps its drawing inside the central 80%
        ctx.translate(32, 32)
        ctx.scale(0.8, 0.8)
        ctx.translate(-32, -32)
    for x, y, h, a in ((10, 33, 14, 0.6), (19, 25, 22, 0.8), (28, 17, 30, 1.0)):
        rounded(ctx, x, y, 6, h, 3)
        ctx.set_source_rgba(1, 1, 1, a)
        ctx.fill()
    ctx.set_source_rgb(1, 1, 1)
    ctx.set_line_width(4)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    ctx.move_to(41, 18)
    ctx.line_to(41, 46)
    ctx.line_to(56, 32)
    ctx.close_path()
    ctx.fill_preserve()
    ctx.stroke()
    surface.write_to_png(str(path))


if __name__ == "__main__":
    icon(180, STATIC / "apple-touch-icon.png", full_bleed=True)  # iOS rounds the corners itself
    icon(192, STATIC / "icon-192.png")
    icon(512, STATIC / "icon-512.png")
    icon(512, STATIC / "icon-maskable-512.png", full_bleed=True)
    print("wrote icons to", STATIC)
