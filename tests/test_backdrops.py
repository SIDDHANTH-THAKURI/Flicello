"""Looks and painted backdrops (explainer/backdrops.py): images found in any answer shape, plates made once per place and
reused, neighbours on one plate are one camera shot, and scenes drawn over their plate with the camera moving."""

from __future__ import annotations

import base64
import io
import json

import numpy as np
from PIL import Image

from vidosuper.explainer import backdrops
from vidosuper.explainer.backdrops import ImageMaker, Plates, make_plates, plate_path, shots
from vidosuper.explainer.script import Scene, VoiceScript

LOOK = {"title": "Night observatory", "world": "an observatory at night", "art_style": "painterly", "colours": ["#000000"]}


def _jpeg(colour=(200, 80, 40), size=(320, 180)) -> str:
    img = Image.new("RGB", size, colour)
    for x in range(0, size[0], 20):  # stripes, so a camera move shows
        img.paste((255, 255, 255), (x, 0, x + 4, size[1]))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return base64.b64encode(buf.getvalue()).decode()


class FakeHttp:
    def __init__(self):
        self.calls = []

    def post(self, url, headers=None, json=None):
        self.calls.append(json)
        aspect = json["response_format"]["aspect_ratio"]
        size = (180, 320) if aspect == "9:16" else (320, 180)

        class R:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return {"id": "x", "outputs": [{"type": "text", "text": "here"}],
                        "output_image": {"mime_type": "image/jpeg", "data": _jpeg(size=size)}}

        return R()


def test_an_image_is_found_wherever_the_answer_puts_it():
    data = _jpeg()
    assert backdrops._find_image({"output_image": {"data": data}})
    assert backdrops._find_image({"steps": [{"content": [{"type": "text", "text": "hi"}, {"type": "image", "data": data}]}]})
    assert backdrops._find_image({"candidates": [{"content": {"parts": [{"inlineData": {"data": data}}]}}]})
    assert backdrops._find_image({"outputs": [{"type": "text", "text": "no picture"}]}) is None


def _script():
    sc = [Scene(start=0, end=4, layout="keyword", headline="a", backdrop="the great screen wall", camera="push"),
          Scene(start=4, end=8, layout="keyword", headline="b", backdrop="the great screen wall", camera="left"),
          Scene(start=8, end=12, layout="keyword", headline="c", backdrop="a corridor of doorways", camera="rise")]
    return VoiceScript(name="x", scenes=sc)


def test_each_place_is_painted_once_and_reused(tmp_path):
    http = FakeHttp()
    spent = []
    maker = ImageMaker(key="test", spend=lambda *a: spent.append(a), http=http)
    out = make_plates(_script(), LOOK, maker, "16:9", tmp_path, None)
    assert len(http.calls) == 2  # two places, three scenes
    assert out.scenes[0].backdrop_file == out.scenes[1].backdrop_file != out.scenes[2].backdrop_file
    assert plate_path(tmp_path, out.scenes[2].backdrop_file, "16:9").exists()
    assert len(spent) == 2 and all(a[2] > 0 for a in spent)
    sent = json.dumps(http.calls)
    assert "No text" in sent and "the great screen wall" in sent
    assert any(len(c["input"]) == 2 for c in http.calls if isinstance(c["input"], list))  # later plates match the first
    again = make_plates(_script(), LOOK, maker, "16:9", tmp_path, None)
    assert len(http.calls) == 2 and again.scenes[2].backdrop_file == out.scenes[2].backdrop_file
    assert make_plates(_script(), LOOK, ImageMaker(key="", http=http), "9:16", tmp_path, None).scenes[0].backdrop_file == ""


def test_neighbours_on_one_plate_are_one_shot(tmp_path):
    out = make_plates(_script(), LOOK, ImageMaker(key="test", http=FakeHttp()), "16:9", tmp_path, None)
    sh = shots(out)
    assert sh[0] == sh[1] == (0, 8, "push") and sh[2] == (8, 12, "rise")


def test_scenes_are_drawn_over_their_plate_and_the_camera_moves(tmp_path):
    from vidosuper.explainer.compose import _scene_frame

    script = make_plates(_script(), LOOK, ImageMaker(key="test", http=FakeHttp()), "16:9", tmp_path, None)
    plates = Plates(tmp_path, (320, 180), "16:9")
    a = np.asarray(plates.frame(script.scenes[0].backdrop_file, "push", 0.0, 0.0).convert("RGB"), np.float32)
    b = np.asarray(plates.frame(script.scenes[0].backdrop_file, "push", 1.0, 0.0).convert("RGB"), np.float32)
    assert a.shape == (180, 320, 3) and np.abs(a - b).mean() > 3  # it travels
    frame = np.asarray(_scene_frame(script, script.scenes[2], 9.0, (320, 180), 0.0, plates, shots(script)).convert("RGB"))
    assert frame[..., 0].mean() > frame[..., 2].mean() + 20  # the orange plate, not the blue gradient


def _on_green() -> bytes:
    img = Image.new("RGB", (200, 200), (0, 255, 0))
    img.paste((220, 90, 60), (60, 50, 140, 150))  # the prop
    img.paste((0, 255, 0), (90, 90, 110, 110))  # green inside it, not touching the border: a hole in the prop
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_a_painted_prop_is_cut_out_of_its_green_ground():
    cut = backdrops.cut_prop(_on_green())
    assert cut is not None and cut.mode == "RGBA"
    assert 78 <= cut.width <= 92 and 98 <= cut.height <= 112  # cropped to the prop
    a = np.asarray(cut)[..., 3]
    assert a[cut.height // 2 - 30, cut.width // 2 - 30] > 200  # the prop stays
    grey = io.BytesIO()
    Image.new("RGB", (200, 200), (128, 128, 128)).save(grey, "PNG")
    assert backdrops.cut_prop(grey.getvalue()) is None  # not painted on green: never guessed


def test_props_are_painted_once_per_visual(tmp_path):
    class GreenHttp(FakeHttp):
        def post(self, url, headers=None, json=None):
            self.calls.append(json)

            class R:
                status_code = 200
                text = ""

                @staticmethod
                def json():
                    return {"output_image": {"data": base64.b64encode(_on_green()).decode()}}

            return R()

    from vidosuper.explainer.backdrops import Props, make_props

    http = GreenHttp()
    sc = _script()
    sc = sc.model_copy(update={"scenes": [s.model_copy(update={"visual": v}) for s, v in
                                          zip(sc.scenes, ["a paper brain", "a paper brain", "a toggle switch"])]})
    out = make_props(sc, LOOK, ImageMaker(key="test", http=http), tmp_path, None)
    assert len(http.calls) == 2 and "#00FF00" in json.dumps(http.calls[0])
    assert out.scenes[0].prop_file == out.scenes[1].prop_file != out.scenes[2].prop_file != ""
    props = Props(tmp_path)
    assert props.get(out.scenes[2].prop_file, 120).width == 120 and props.aspect(out.scenes[2].prop_file) > 1
