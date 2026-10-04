"""On-screen text rendered with Pillow into transparent PNGs, overlaid by FFmpeg.

(FFmpeg's own drawtext crashes on this Windows build without fontconfig, and Pillow gives better
typography control anyway.)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..schemas import TextOverlay

FONT_FILES = {
    "clean": ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"],
    "bold": ["seguibl.ttf", "segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"],
    "serif": ["georgia.ttf", "times.ttf", "DejaVuSerif.ttf"],
    "mono": ["consola.ttf", "cour.ttf", "DejaVuSansMono.ttf"],
}
FONT_DIRS = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")]
SIZES = {"small": 0.035, "medium": 0.055, "large": 0.09}  # fraction of frame height


def _font(style: str, px: int) -> ImageFont.ImageFont:
    for name in FONT_FILES.get(style, FONT_FILES["clean"]):
        for folder in FONT_DIRS:
            if (folder / name).exists():
                return ImageFont.truetype(str(folder / name), px)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words, line = paragraph.split(), ""
        for word in words:
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=font) <= max_width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def render_text_png(overlay: TextOverlay, width: int, height: int, path: Path) -> Path:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    px = max(12, int(height * SIZES[overlay.size]))
    font = _font(overlay.style, px)
    lines = _wrap(draw, overlay.text, font, int(width * 0.8))
    spacing = int(px * 0.25)
    boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    block_w = max(b[2] - b[0] for b in boxes)
    block_h = sum(b[3] - b[1] for b in boxes) + spacing * (len(lines) - 1)
    margin_x, margin_y = int(width * 0.06), int(height * 0.08)
    x = {"bottom-left": margin_x, "bottom-right": width - margin_x - block_w}.get(overlay.position, (width - block_w) // 2)
    y = {"top": margin_y, "lower-third": int(height * 0.70), "bottom-left": height - margin_y - block_h,
         "bottom-right": height - margin_y - block_h}.get(overlay.position, (height - block_h) // 2)
    stroke = max(1, px // 18)
    cursor = y
    for line, box in zip(lines, boxes):
        lw = box[2] - box[0]
        lx = x + (block_w - lw) // 2 if overlay.position in ("center", "top", "lower-third") else x
        draw.text((lx + stroke, cursor + stroke), line, font=font, fill=(0, 0, 0, 140))  # soft shadow
        draw.text((lx, cursor), line, font=font, fill=(255, 255, 255, 255), stroke_width=stroke,
                  stroke_fill=(0, 0, 0, 90))
        cursor += (box[3] - box[1]) + spacing
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path
