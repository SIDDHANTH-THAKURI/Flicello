"""Effects that rebuild the whole frame rather than draw on it.

panels  - a 2x2 grid of shots popping in on the beats (other clips, decoded alongside, graded like the
          film), the shot's own subject in the last tile, cut out and drawn across the tile borders: he
          steps out of the frame.
snipcut - Snip walks across the frame cutting a line with the scissors; the picture splits along it like
          paper, the halves fall away, and the end card is underneath ("Shot by a human. Made with
          Claude." with the crew waving).
"""

from __future__ import annotations

import math
import subprocess
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw

from ..ffmpeg import ffmpeg_bin
from ..outro.card import CREAM, DIM, MUTED, SIGN_BIG, SIGN_SMALL, background
from ..outro.crew import CREW, GROUND, POSES
from ..outro.draw import ease_in_out, ease_out_back, ease_out_cubic, place, ramp, text_sprite
from ..render.looks import LOOKS
from ..schemas import Graphic
from .actors import member_sprite

INK = np.array([18, 16, 22], np.float32)


class TileSource:
    """One clip decoded at tile size from a source second, frame by frame, graded like the film."""

    def __init__(self, path: str, src_in: float, size: tuple[int, int], fps: int):
        w, h = size
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},{LOOKS['vivid']},"
              "format=rgb24")
        self.size = size
        self.proc = subprocess.Popen([ffmpeg_bin(), "-v", "error", "-ss", f"{max(0.0, src_in):.3f}", "-i", path,
                                      "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        self.last = np.zeros((h, w, 3), np.uint8)

    def next(self) -> np.ndarray:
        w, h = self.size
        raw = self.proc.stdout.read(w * h * 3) if self.proc.stdout else b""
        if len(raw) == w * h * 3:
            self.last = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
        return self.last

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()


def tile_rects(w: int, h: int) -> list[tuple[int, int, int, int]]:
    s = min(w, h)
    margin, gutter = round(0.035 * s), round(0.018 * s)
    cw = (w - 2 * margin - gutter) // 2
    ch = (h - 2 * margin - gutter) // 2
    return [(margin + c * (cw + gutter), margin + r * (ch + gutter), cw, ch) for r in (0, 1) for c in (0, 1)]


def open_panels(g: Graphic, w: int, h: int, fps: int) -> list[TileSource]:
    rects = tile_rects(w, h)
    sources = []
    for k, token in enumerate(t for t in g.text.split("|") if t.strip()):
        if k >= 3:
            break
        path, _, sec = token.strip().rpartition("@")
        _, _, cw, ch = rects[k]
        sources.append(TileSource(path, float(sec or 0), (cw, ch), fps))
    return sources


def _pop(tile: np.ndarray, p: float) -> tuple[np.ndarray, float]:
    """A tile scaling in (returned with its alpha)."""
    import cv2

    scale = 0.82 + 0.18 * ease_out_back(p, 1.6)
    h, w = tile.shape[:2]
    small = cv2.resize(tile, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    canvas = np.zeros_like(tile)
    dy, dx = (h - small.shape[0]) // 2, (w - small.shape[1]) // 2  # negative while the pop overshoots
    sy, sx, ty, tx = max(0, -dy), max(0, -dx), max(0, dy), max(0, dx)
    ny, nx = min(h - ty, small.shape[0] - sy), min(w - tx, small.shape[1] - sx)
    canvas[ty:ty + ny, tx:tx + nx] = small[sy:sy + ny, sx:sx + nx]
    return canvas, min(1.0, 3 * p)


def panels_frame(main: np.ndarray, matte: np.ndarray | None, g: Graphic, tau: float,
                 sources: list[TileSource]) -> np.ndarray:
    import cv2

    h, w = main.shape[:2]
    rects = tile_rects(w, h)
    beats = list(g.beats) or [0.0, 0.25, 0.5, 0.75]
    out = np.empty((h, w, 3), np.float32)
    out[:] = INK
    frames = [src.next() for src in sources]  # every decoder keeps time, shown or not
    border = max(2, round(0.004 * min(w, h)))
    for k, (x, y, cw, ch) in enumerate(rects):
        t0 = beats[k] if k < len(beats) else beats[-1]
        if tau < t0:
            continue
        p = ramp(tau, t0, 0.25)
        if k < len(frames):
            tile = frames[k]
        else:  # the subject's tile: the shot itself, larger than the tile so he spills out of it
            z = 1.08
            scaled = cv2.resize(main, (round(w * z), round(h * z)), interpolation=cv2.INTER_AREA)
            sy, sx = (scaled.shape[0] - ch) // 2, (scaled.shape[1] - cw) // 2
            tile = scaled[sy:sy + ch, sx:sx + cw]
        tile, a = _pop(np.ascontiguousarray(tile), p)
        region = out[y:y + ch, x:x + cw]
        region[:] = region * (1 - a) + tile.astype(np.float32) * a
        cv2.rectangle(out, (x - border, y - border), (x + cw + border - 1, y + ch + border - 1),
                      tuple(float(v) * a + 18 * (1 - a) for v in CREAM), border)
    if matte is not None and len(rects) > len(frames) and tau >= (beats[len(frames)] if len(frames) < len(beats) else 0):
        x, y, cw, ch = rects[len(frames)]
        z = 1.08
        scaled = cv2.resize(main, (round(w * z), round(h * z)), interpolation=cv2.INTER_AREA).astype(np.float32)
        m = cv2.resize(matte, (scaled.shape[1], scaled.shape[0]), interpolation=cv2.INTER_LINEAR)[..., None]
        p = ramp(tau, beats[len(frames)] if len(frames) < len(beats) else 0, 0.25)
        m = m * min(1.0, 3 * p)
        ox = x + cw // 2 - scaled.shape[1] // 2
        oy = y + ch // 2 - scaled.shape[0] // 2
        x0, y0 = max(0, ox), max(0, oy)
        x1, y1 = min(w, ox + scaled.shape[1]), min(h, oy + scaled.shape[0])
        src = scaled[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
        mm = m[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
        out[y0:y1, x0:x1] = out[y0:y1, x0:x1] * (1 - mm) + src * mm  # the subject on top of every border
    return out


@lru_cache(maxsize=2)
def end_card(w: int, h: int, title: str) -> Image.Image:
    img = background(w, h).copy()
    s = min(w, h)
    place(img, text_sprite(SIGN_SMALL, "Nunito-SemiBold", round(0.04 * s), MUTED), w / 2, h * 0.36)
    place(img, text_sprite(title or SIGN_BIG, "Fraunces-SemiBold", round(0.105 * s), CREAM, max_width=round(w * 0.9)),
          w / 2, h * 0.45)
    return img


def snipcut_frame(img: np.ndarray, g: Graphic, tau: float) -> np.ndarray:
    import cv2

    h, w = img.shape[:2]
    s = min(w, h)
    cut_y = int(g.y * h)
    walk = 1.1
    out = img.copy()
    p_walk = ease_in_out(ramp(tau, 0, walk))
    split = ease_in_out(ramp(tau, walk + 0.1, 0.7))
    if split > 0:  # the picture falls apart along the cut, the end card underneath
        card = np.asarray(end_card(w, h, g.text).convert("RGB"), np.float32).copy()
        card_img = Image.fromarray(card.astype(np.uint8), "RGB").convert("RGBA")
        n = len(CREW[:3])
        for k in range(n):  # the crew waves on the card
            unit = round(0.07 * s / 92, 2)
            pose = int(((tau * CREW[k].pose_rate) + 0.37 * k) % 1.0 * POSES)
            sprite = member_sprite(k, unit, pose, False)
            box = sprite.getbbox() or (0, 0, sprite.width, sprite.height)
            hop = 0.02 * s * abs(math.sin(2 * math.pi * (1.6 * tau + 0.3 * k)))
            place(card_img, sprite, w / 2 + (k - 1) * 0.16 * w, h * 0.66 - hop,
                  anchor=((box[0] + box[2]) / 2 / sprite.width, GROUND * unit / sprite.height),
                  alpha=ease_out_cubic(ramp(tau, walk + 0.4 + 0.1 * k, 0.3)))
        card = np.asarray(card_img.convert("RGB"), np.float32)
        top, bottom = img[:cut_y], img[cut_y:]
        out = card.copy()
        dy_top = -int(split * (cut_y + 0.06 * h))
        dy_bot = int(split * (h - cut_y + 0.06 * h))
        rot_top = cv2.getRotationMatrix2D((0, cut_y), -4 * split, 1.0)
        rot_top[1, 2] += dy_top
        top_full = np.zeros_like(img)
        top_full[:cut_y] = top
        top_mask = np.zeros((h, w), np.float32)
        top_mask[:cut_y] = 1
        t_img = cv2.warpAffine(top_full, rot_top, (w, h))
        t_m = cv2.warpAffine(top_mask, rot_top, (w, h))[..., None]
        rot_bot = cv2.getRotationMatrix2D((w, cut_y), -3 * split, 1.0)
        rot_bot[1, 2] += dy_bot
        bot_full = np.zeros_like(img)
        bot_full[cut_y:] = bottom
        bot_mask = np.zeros((h, w), np.float32)
        bot_mask[cut_y:] = 1
        b_img = cv2.warpAffine(bot_full, rot_bot, (w, h))
        b_m = cv2.warpAffine(bot_mask, rot_bot, (w, h))[..., None]
        out = out * (1 - t_m) + t_img * t_m
        out = out * (1 - b_m) + b_img * b_m
    else:
        pil = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
        d = ImageDraw.Draw(pil)
        sx = -0.12 * w + p_walk * 1.24 * w
        dash = 0.025 * s
        x = 0.0
        while x < min(w, sx):  # the dashed cut line behind the scissors
            d.line([(x, cut_y), (min(x + dash * 0.6, sx), cut_y)], fill=(255, 255, 255, 235), width=max(2, round(0.004 * s)))
            x += dash
        unit = round(0.16 * s / 92, 2)
        pose = int((tau * 3.0) % 1.0 * POSES)  # snipping fast
        sprite = member_sprite(2, unit, pose, False)
        box = sprite.getbbox() or (0, 0, sprite.width, sprite.height)
        hop = 0.012 * s * abs(math.sin(2 * math.pi * 3 * tau))
        place(pil, sprite, sx, cut_y - hop, anchor=((box[0] + box[2]) / 2 / sprite.width, GROUND * unit / sprite.height))
        out = np.asarray(pil.convert("RGB"), np.float32)
    return out
