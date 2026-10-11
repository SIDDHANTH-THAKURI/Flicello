"""Draw a shot's graphics into its rendered frames: track the camera (world graphics), cut the person
out (graphics behind them), composite, re-encode. Streams frame by frame; the result is cached by the
shot's own render and everything that shapes the graphics."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import numpy as np

from ..config import Config
from ..ffmpeg import FFmpegError, ffmpeg_bin, ffprobe_json
from ..schemas import Graphic
from PIL import Image

from .layers import active, draw_shared, render_layer, shares_layer
from .matte import person_matte
from .track import SceneTracker

GRAPHICS_VERSION = 3


def graphics_key(video: Path, graphics: list[Graphic]) -> str:
    payload = {"v": GRAPHICS_VERSION, "shot": Path(video).name, "g": [g.model_dump() for g in graphics]}
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]


def _over(base: np.ndarray, layer: np.ndarray) -> np.ndarray:
    a = layer[..., 3:4].astype(np.float32) / 255.0
    return base * (1 - a) + layer[..., :3].astype(np.float32) * a


def apply_graphics(video: Path, graphics: list[Graphic], fps: int, cache_dir: Path, quality: str, cfg: Config) -> Path:
    from ..render.segments import intermediate_encoder

    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"g_{graphics_key(video, graphics)}.mp4"
    if out.exists() or not graphics:
        return out if graphics else video
    stream = next(s for s in ffprobe_json(video)["streams"] if s["codec_type"] == "video")
    w, h = int(stream["width"]), int(stream["height"])
    part = out.with_name(out.stem + ".part.mp4")
    dec = subprocess.Popen([ffmpeg_bin(), "-v", "error", "-i", str(video), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE)
    enc_cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", *intermediate_encoder(quality, cfg), "-an", str(part)]
    enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    from .frames import open_panels, panels_frame, snipcut_frame

    panels = [g for g in graphics if g.kind == "panels"]
    cuts = [g for g in graphics if g.kind == "snipcut"]
    need_matte = any(g.behind for g in graphics) or bool(panels)
    tracker = SceneTracker(w, h) if any(g.anchor == "world" for g in graphics) else None
    tiles = {id(g): open_panels(g, w, h, fps) for g in panels}
    smooth: np.ndarray | None = None
    frame_bytes = w * h * 3
    index = 0
    try:
        assert dec.stdout is not None and enc.stdin is not None
        while True:
            raw = dec.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            rgb = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
            t = index / fps
            matte = None
            if need_matte:
                m = person_matte(rgb)
                smooth = m if smooth is None else 0.6 * m + 0.4 * smooth  # steadier edges, no flicker
                matte = smooth
            transform = tracker.update(rgb, exclude=matte) if tracker else np.eye(3)
            img = rgb.astype(np.float32)
            for g in panels:  # the grid replaces the picture (the subject steps out of it)
                if t >= g.at and (g.until is None or t <= g.until):
                    img = panels_frame(rgb, matte, g, t - g.at, tiles[id(g)])
                else:
                    for src in tiles[id(g)]:
                        src.next()
            behind = [layer for g in graphics if g.behind and (layer := render_layer(g, t, (w, h), transform)) is not None]
            if behind:
                for layer in behind:
                    img = _over(img, np.asarray(layer))
                if matte is not None:  # the person stays in front of what is behind them
                    img = img * (1 - matte[..., None]) + rgb.astype(np.float32) * matte[..., None]
            batch: Image.Image | None = None  # neighbours that need no layer of their own share one
            for g in graphics:
                if g.behind or g.kind in ("panels", "snipcut") or not active(g, t):
                    continue
                if shares_layer(g):
                    batch = batch or Image.new("RGBA", (w, h), (0, 0, 0, 0))
                    draw_shared(batch, g, t, transform)
                    continue
                if batch is not None:
                    img, batch = _over(img, np.asarray(batch)), None
                if (layer := render_layer(g, t, (w, h), transform)) is not None:
                    img = _over(img, np.asarray(layer))
            if batch is not None:
                img = _over(img, np.asarray(batch))
            for g in cuts:  # last: the scissors cut everything, the end card is underneath
                if t >= g.at:
                    img = snipcut_frame(img, g, t - g.at)
            enc.stdin.write(np.clip(img, 0, 255).astype(np.uint8).tobytes())
            index += 1
        enc.stdin.close()
    except (BrokenPipeError, OSError):  # the encoder died: its own message is read below
        pass
    finally:  # never wait on a decoder still blocked on a full pipe: an error must surface, not hang
        if dec.poll() is None:
            dec.kill()
        dec.wait()
        for sources in tiles.values():
            for src in sources:
                src.close()
        if enc.poll() is None and (enc.stdin is None or enc.stdin.closed) is False:
            try:
                enc.stdin.close()
            except OSError:
                pass
    err = enc.stderr.read().decode("utf-8", errors="replace") if enc.stderr else ""
    if enc.wait() != 0 or index == 0:
        part.unlink(missing_ok=True)
        raise FFmpegError("Could not draw the graphics into the shot", enc_cmd, err)
    os.replace(part, out)
    return out
