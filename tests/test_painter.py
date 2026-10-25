"""The painter on this machine (motion/painter.py): chosen when it can run, it paints on the key colour from the
canvas and the pictures to follow, its process loads the model once and answers on 127.0.0.1, and what it paints is
cut out like any painting."""

from __future__ import annotations

import socket
import threading

import cv2
import numpy as np
import pytest

from vidosuper.motion import painter, scenes


def test_who_paints(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setattr(scenes, "_load_dotenv", lambda: None, raising=False)
    monkeypatch.setattr("vidosuper.music.compose._load_dotenv", lambda: None)
    monkeypatch.setenv("VIDO_PAINTER", "auto")
    monkeypatch.setattr(painter, "available", lambda: True)
    assert scenes.painter_kind() == "local"  # the open model on their GPU first: no paid service
    monkeypatch.setattr(painter, "available", lambda: False)
    with pytest.raises(scenes.SceneError, match=r"nothing here can paint.*\[paint\].*Gemini key"):
        scenes.painter_kind()
    monkeypatch.setenv("GEMINI_API_KEY", "a-key")
    assert scenes.painter_kind() == "gemini"
    monkeypatch.setenv("VIDO_PAINTER", "local")
    assert scenes.painter_kind() == "local"


def _painted(path, rgb=(255, 0, 255)):
    """What the model sends back: the key colour with a blue disc on it."""
    img = np.zeros((512, 512, 3), np.uint8)
    img[:] = rgb[::-1]
    cv2.circle(img, (256, 256), 120, (200, 90, 30), -1)
    cv2.imwrite(str(path), img)


def test_the_local_painter_follows_the_shots_light_and_an_assets_style_and_is_cut_out(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDO_PAINTER", "local")
    light = tmp_path / "frame.jpg"
    cv2.imwrite(str(light), np.full((90, 160, 3), 120, np.uint8))
    like = tmp_path / "assets" / "hero.png"
    like.parent.mkdir()
    sheet = np.zeros((64, 64, 4), np.uint8)
    sheet[16:48, 16:48] = (10, 200, 10, 255)
    cv2.imwrite(str(like), sheet)
    asked = {}

    def fake(prompt, key, refs, out, seed=0):
        asked.update(prompt=prompt, key=key, refs=refs, seed=seed)
        out.parent.mkdir(parents=True, exist_ok=True)
        _painted(out, key)
        return out

    monkeypatch.setattr(painter, "paint", fake)
    w, h = scenes.paint("a blue moon, gouache", tmp_path / "assets" / "moon.png", light, like)
    assert asked["key"] == (255, 0, 255) and asked["refs"][0] == light
    flat = cv2.imread(str(asked["refs"][1]), cv2.IMREAD_COLOR)
    assert flat.shape == (64, 64, 3) and tuple(flat[0, 0]) == (255, 0, 255)  # its transparency on the key colour
    assert "Image 1 is an empty canvas of flat pure magenta" in asked["prompt"]
    assert "person in image 2" in asked["prompt"] and "style, detail, palette and light of image 3" in asked["prompt"]
    again = {}
    monkeypatch.setattr(painter, "paint", lambda *a, **k: (again.setdefault("seed", k["seed"]), fake(*a, **k))[1])
    scenes.paint("a blue moon, gouache", tmp_path / "assets" / "moon2.png", light, like)
    assert again["seed"] == asked["seed"]  # the same words paint the same way
    cut = cv2.imread(str(tmp_path / "assets" / "moon.png"), cv2.IMREAD_UNCHANGED)
    assert cut.shape[2] == 4 and (w, h) == (cut.shape[1], cut.shape[0]) and 240 <= w <= 260
    assert cut[0, 0, 3] == 0 and cut[h // 2, w // 2, 3] == 255  # the key is gone, the disc is solid


def test_a_pink_thing_is_painted_on_green(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDO_PAINTER", "local")
    asked = {}

    def fake(prompt, key, refs, out, seed=0):
        asked.update(key=key, prompt=prompt)
        out.parent.mkdir(parents=True, exist_ok=True)
        _painted(out, key)
        return out

    monkeypatch.setattr(painter, "paint", fake)
    scenes.paint("a pink balloon", tmp_path / "assets" / "balloon.png")
    assert asked["key"] == (0, 255, 0) and "flat pure green" in asked["prompt"]


def test_the_painter_process_loads_once_and_paints_on_this_machine(tmp_path, monkeypatch):
    loads = []

    class Model:  # stands in for Qwen-Image-Edit: the canvas with a disc, from the canvas it is given
        def __init__(self):
            loads.append(1)

        def paint(self, prompt, key, refs, seed):
            from PIL import Image, ImageDraw

            img = Image.new("RGB", (256, 256), key)
            ImageDraw.Draw(img).ellipse((64, 64, 192, 192), fill=(30, 90, 200))
            return img

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setattr(painter, "_Painter", Model)
    monkeypatch.setattr(painter, "PORT", port)
    monkeypatch.setattr(painter, "_start", lambda: threading.Thread(target=painter.serve, args=(port,), daemon=True).start())
    out = painter.paint("a disc", (255, 0, 255), [], tmp_path / "a.png", seed=3)
    painter.paint("another disc", (255, 0, 255), [], tmp_path / "b.png", seed=4)
    img = cv2.imread(str(out))
    assert img.shape == (256, 256, 3) and tuple(img[0, 0]) == (255, 0, 255) and loads == [1]  # loaded once
    assert (tmp_path / "b.png").exists()


def test_the_painter_says_why_it_cannot_start(monkeypatch):
    class Broken:
        def __init__(self):
            raise painter.PainterError("the local painter needs a GPU (NVIDIA with CUDA, or Apple Silicon)")

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setattr(painter, "_Painter", Broken)
    monkeypatch.setattr(painter, "PORT", port)
    monkeypatch.setattr(painter, "_start", lambda: threading.Thread(target=painter.serve, args=(port,), daemon=True).start())
    with pytest.raises(painter.PainterError, match="needs a GPU"):
        painter.ready(wait=30)
