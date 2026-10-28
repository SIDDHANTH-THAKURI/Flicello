"""The painter on this machine (motion/painter.py): chosen when it can run, it paints on the key colour from the
canvas and the pictures to follow, its process loads the model once and answers on 127.0.0.1, and what it paints is
cut out like any painting."""

from __future__ import annotations

import json
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


def _painted(path, rgb=(255, 255, 255)):
    """What the model sends back: the plain picture it was given with a blue disc added, its edge blended."""
    img = np.zeros((512, 512, 3), np.uint8)
    img[:] = rgb[::-1]
    cv2.circle(img, (256, 256), 120, (200, 90, 30), -1, lineType=cv2.LINE_AA)
    cv2.imwrite(str(path), img)


def _disc_matte(monkeypatch):
    """The cut-out model (BiRefNet), standing in: the disc, with a soft edge."""
    from vidosuper.graphics import birefnet

    def matte(rgb, model=None):
        h, w = rgb.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w]
        d = np.hypot(yy - h / 2, xx - w / 2)
        return np.clip((121 - d) / 2.0, 0, 1).astype(np.float32)

    monkeypatch.setattr(birefnet, "available", lambda: True)
    monkeypatch.setattr(birefnet, "person_matte", matte)


def test_the_local_painter_adds_the_thing_to_white_follows_the_light_and_a_style_and_is_cut_out(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDO_PAINTER", "local")
    _disc_matte(monkeypatch)
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
    assert asked["key"] == (255, 255, 255) and asked["refs"][0] == light
    flat = cv2.imread(str(asked["refs"][1]), cv2.IMREAD_COLOR)
    assert flat.shape == (64, 64, 3) and tuple(flat[0, 0]) == (255, 255, 255)  # its transparency on white
    # asked as an image editor is (on a real GPU, "paint on this canvas" repainted the canvas as paper)
    assert asked["prompt"].startswith("Add a blue moon, gouache in the centre of image 1. Keep the plain white")
    assert "person in image 2" in asked["prompt"] and "only a sample of the painting style" in asked["prompt"]
    assert "draw nothing that is in image 3" in asked["prompt"]  # on a real GPU it drew the sample itself
    again = {}
    monkeypatch.setattr(painter, "paint", lambda *a, **k: (again.setdefault("seed", k["seed"]), fake(*a, **k))[1])
    scenes.paint("a blue moon, gouache", tmp_path / "assets" / "moon2.png", light, like)
    assert again["seed"] == asked["seed"]  # the same words paint the same way
    cut = cv2.imread(str(tmp_path / "assets" / "moon.png"), cv2.IMREAD_UNCHANGED)
    assert cut.shape[2] == 4 and (w, h) == (cut.shape[1], cut.shape[0]) and 240 <= w <= 260
    assert cut[0, 0, 3] == 0 and cut[h // 2, w // 2, 3] == 255  # the white is gone, the disc is solid
    edge = cut[h // 2, :, :][cut[h // 2, :, 3] > 0][:3]  # the soft edge: the disc's own blue, no white fringe
    assert len(edge) == 3 and all(int(px[0]) - int(px[2]) > 120 for px in edge)


def test_without_the_cut_out_model_it_says_how_to_get_it(tmp_path, monkeypatch):
    from vidosuper.graphics import birefnet

    monkeypatch.setenv("VIDO_PAINTER", "local")
    monkeypatch.setattr(birefnet, "available", lambda: False)
    monkeypatch.setattr(painter, "paint", lambda prompt, key, refs, out, seed=0: (out.parent.mkdir(parents=True,
                                                                                    exist_ok=True), _painted(out))[0])
    with pytest.raises(scenes.SceneError, match="cut-out model.*Connect page"):
        scenes.paint("a pink balloon", tmp_path / "assets" / "balloon.png")


def test_the_painter_process_loads_once_and_paints_on_this_machine(tmp_path, monkeypatch):
    loads = []

    class Model:  # stands in for Qwen-Image-Edit: the canvas with a disc, from the canvas it is given
        def __init__(self):
            loads.append(1)

        def paint(self, prompt, key, refs, seed, size=(256, 256)):
            from PIL import Image, ImageDraw

            img = Image.new("RGB", size, key)
            ImageDraw.Draw(img).ellipse((64, 64, 192, 192), fill=(30, 90, 200))
            return img

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setattr(painter, "_Painter", Model)
    monkeypatch.setattr(painter, "PORT", port)
    monkeypatch.setattr(painter, "_start", lambda: threading.Thread(target=painter.serve, args=(port,), daemon=True).start())
    out = painter.paint("a disc", (255, 0, 255), [], tmp_path / "a.png", seed=3, size=(256, 256))
    painter.paint("another disc", (255, 0, 255), [], tmp_path / "b.png", seed=4, size=(144, 256))
    img = cv2.imread(str(out))
    assert img.shape == (256, 256, 3) and tuple(img[0, 0]) == (255, 0, 255) and loads == [1]  # loaded once
    assert cv2.imread(str(tmp_path / "b.png")).shape == (256, 144, 3)  # a whole frame at its own shape


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


def test_a_background_and_the_style_key_are_painted_whole_at_the_films_shape_and_never_cut_out(tmp_path, monkeypatch):
    """On a real GPU the cut-out lifted a chair and a rug out of 'the living room at night': a set is a whole frame."""
    from vidosuper.graphics import birefnet
    from vidosuper.motion import cartoon

    monkeypatch.setenv("VIDO_PAINTER", "local")
    monkeypatch.setattr(birefnet, "person_matte", lambda *a, **k: pytest.fail("a whole frame is never cut out"))
    asked = []

    def fake(prompt, key, refs, out, seed=0, size=(1024, 1024)):
        asked.append({"prompt": prompt, "size": size, "out": out})
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out), np.full((size[1], size[0], 3), (90, 40, 20), np.uint8))
        return out

    monkeypatch.setattr(painter, "paint", fake)
    desk = tmp_path / "board"
    (desk / "assets").mkdir(parents=True)
    (desk / "settings.json").write_text(json.dumps({"width": 1080, "height": 1920}))
    cartoon.paint_command(desk, ["style_key", "the moonlit living room, the robot tiny in it",
                                 "bg_hallway", "the long dark hallway"])
    assert [a["size"] for a in asked] == [(768, 1360), (768, 1360)]  # the film's 9:16, about a megapixel
    assert all(a["prompt"].startswith("Paint over the whole of image 1, edge to edge") for a in asked)
    img = cv2.imread(str(desk / "assets" / "bg_hallway.png"), cv2.IMREAD_UNCHANGED)
    assert img.shape == (1360, 768, 3)  # whole: no transparency, nothing trimmed
    asked.clear()
    cartoon.paint_command(desk, ["--whole", "far_hills", "rolling hills under the moon"])
    assert asked and asked[0]["size"] == (768, 1360)
    assert painter.size_for(16 / 9) == (1360, 768)


def test_a_painting_asked_for_as_a_whole_frame_is_whole_whatever_its_name():
    from vidosuper.motion import scenes

    assert scenes.is_whole("floor2", "picture-book gouache painting filling the whole vertical frame")
    assert scenes.is_whole("far_room2", "the room")
    assert scenes.is_whole("hall", "a full-bleed vertical background painting of the hallway")
    assert not scenes.is_whole("dog_sniff", "the dog's nose, sniffing, three-quarter view")
    assert not scenes.is_whole("plug", "a white plug on its cable, isolated")
