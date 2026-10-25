"""The scene designer's kit (motion/scenes.py, motion/rig.js): paintings keyed without a fringe, stretches seen as
strips, the person's cut-out following the picture between its clean frames, and the rigs in a real render."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from vidosuper import motion
from vidosuper.motion import scenes

ENGINE = pytest.mark.skipif(not motion.available(), reason="the motion engine isn't installed (npm ci in motion/)")


def _disc(bg, fg, size=200):
    """A disc of colour `fg` on `bg` (BGR), its rim blended half and half with the background."""
    img = np.zeros((size, size, 3), np.float32)
    img[:] = bg
    yy, xx = np.mgrid[:size, :size]
    d = np.hypot(yy - size / 2, xx - size / 2)
    img[d < 50] = fg
    rim = (d >= 50) & (d < 56)
    img[rim] = 0.5 * np.array(fg, np.float32) + 0.5 * np.array(bg, np.float32)
    return img.astype(np.uint8), rim


def test_a_painting_on_a_key_colour_comes_out_clean_even_where_it_is_soft():
    img, rim = _disc((252, 3, 249), (255, 255, 255))  # white on the painter's not-quite-pure magenta
    rgba = scenes.key_out(img)
    assert rgba[100, 100, 3] > 0.97 and rgba[5, 5, 3] < 0.03
    assert 0.35 < rgba[..., 3][rim].mean() < 0.65  # the soft rim: half there
    assert rgba[..., :3][rim].min(axis=1).mean() > 0.9  # and white, with no pink left in it
    pink, _ = _disc((0, 255, 0), (180, 105, 255))  # a pink thing is painted on green instead
    out = scenes.key_out(pink, green=True)
    assert out[100, 100, 3] > 0.97 and abs(out[100, 100, 2] - 1.0) < 0.05 and out[5, 5, 3] < 0.03


def test_a_stretch_is_seen_as_a_strip_of_frames():
    tile = cv2.imencode(".jpg", np.full((960, 540, 3), 128, np.uint8))[1].tobytes()
    jpg = scenes.strip([(0.1, tile), (0.3, tile), (0.5, None), (0.7, tile)])
    img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[0] == 480 and img.shape[1] == 3 * 270 + 2 * 4  # the missing frame is left out


def _moving_square(path: Path, n=9, step=6):
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (160, 120))
    for i in range(n):
        f = np.zeros((120, 160, 3), np.uint8)
        f[40:80, 20 + i * step:60 + i * step] = 255
        out.write(f)
    out.release()


def test_the_cut_out_follows_the_picture_between_its_clean_frames(tmp_path, monkeypatch):
    from vidosuper.graphics import birefnet

    calls = []

    def clean(rgb):
        calls.append(1)
        return (rgb[..., 0] > 128).astype(np.float32)

    monkeypatch.setattr(birefnet, "available", lambda: True)
    monkeypatch.setattr(birefnet, "person_matte", clean)
    video = tmp_path / "clip.mp4"
    _moving_square(video)
    assert scenes.matte_keys(video, 0.0, 9 / 30, tmp_path / "mattes", every=4) == 3 and len(calls) == 3
    out = scenes._matte_birefnet(video, 0.0, 9 / 30, tmp_path / "matte.mkv", every=4)
    cap = cv2.VideoCapture(str(out))
    mattes = []
    while True:
        ok, m = cap.read()
        if not ok:
            break
        mattes.append(m[..., 0] > 128)
    assert len(mattes) == 9 and len(calls) == 3  # its clean frames made once, kept
    truth = np.zeros((120, 160), bool)
    truth[40:80, 32:72] = True  # frame 2: between the clean frames 0 and 4
    iou = (mattes[2] & truth).sum() / (mattes[2] | truth).sum()
    assert iou > 0.8  # where the square really is, not a smear of where it was and will be


def test_a_gpu_that_cant_run_a_model_hands_it_to_the_cpu_once_for_good(tmp_path, monkeypatch):
    import onnxruntime as ort

    from vidosuper.graphics import device

    model = tmp_path / "birefnet_lite.onnx"
    model.write_bytes(b"")
    opened = []

    class Session:  # DirectML opens, then runs out of memory on the first frame (the owner's Iris Xe)
        def __init__(self, path, opts, providers):
            opened.append(providers[0])
            self.providers = providers

        def get_providers(self):
            return self.providers

        def get_inputs(self):
            return [type("Input", (), {"name": "x"})]

        def run(self, _names, feed):
            if self.providers[0] != "CPUExecutionProvider":
                raise RuntimeError("8007000E Not enough memory resources are available to complete this operation.")
            return [np.full((1, 1, 4, 4), 40.0, np.float32)]

    monkeypatch.setattr(ort, "InferenceSession", Session)
    monkeypatch.setattr(ort, "get_available_providers", lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])
    monkeypatch.delenv("VIDO_MATTE_DEVICE", raising=False)
    m = device.Model(model)
    assert m.gpu
    assert m.run(np.zeros((1, 3, 4, 4), np.float32)).max() == 40.0  # the GPU failed: the CPU ran the frame
    assert opened == ["DmlExecutionProvider", "CPUExecutionProvider"] and not m.gpu
    again = device.Model(model)  # and from then on the GPU isn't tried
    assert opened[-1] == "CPUExecutionProvider" and not again.gpu
    assert device.providers(model) == ["CPUExecutionProvider"]


@ENGINE
def test_the_rigs_move_things_with_weight_in_a_real_render(tmp_path):
    scenes.PERSON_TRACK[:] = [{"t": round(k * 0.1, 2), "head": [0.3 + 0.03 * k, 0.3],
                               "shoulders": [[0.2, 0.45], [0.6, 0.45]], "box": [0.2, 0.28, 0.6, 1.0]} for k in range(21)]
    html = """<!doctype html><html><head><style>#stage{position:absolute;inset:0}
    .ball{position:absolute;left:100px;top:0;width:80px;height:80px;border-radius:50%;background:#f00}
    .tag{position:absolute;width:40px;height:40px;background:#0f0}
    .cam{position:absolute;left:400px;top:100px;width:40px;height:40px;background:#00f}</style></head><body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="2" data-width="540" data-height="960">
    <div id="stage" class="clip" data-start="0" data-duration="2"><div class="ball" id="ball"></div>
    <div class="tag" id="tag"></div><div class="cam" id="cam"></div></div></div><script>
    const tl = gsap.timeline({ paused: true });
    rig.drop(tl, document.getElementById('ball'), { at: 0, y0: 50, y1: 800, g: 3000, e: 0.4, bounces: 2 });
    rig.pin(tl, document.getElementById('tag'), 'head', { from: 0, to: 2, W: 540, H: 960 });
    rig.shake(tl, document.getElementById('cam'), { at: 1.0, amount: 30, duration: 0.4, seed: 2 });
    window.__timelines = window.__timelines || {}; window.__timelines["main"] = tl;
    </script></body></html>"""
    times = [0.1, 0.5, 1.05, 1.1, 1.15, 1.9]
    shots = scenes.stills(scenes.project(html, tmp_path / "proj"), times, tmp_path / "stills")

    def centre(png, channel):
        img = cv2.imread(str(png), cv2.IMREAD_UNCHANGED)
        ys, xs = np.where((img[..., 3] > 128) & (img[..., channel] > 200))
        return float(xs.mean()), float(ys.mean())

    ball = [centre(shots[t], 2) for t in times]  # red
    tag = [centre(shots[t], 1) for t in times]  # green
    cam = [centre(shots[t], 0) for t in times]  # blue
    assert ball[0][1] < ball[1][1] <= 845  # falling, then on the floor (its top at 800)
    assert abs(ball[-1][1] - 840) < 6  # at rest on the floor at the end
    assert tag[-1][0] - tag[0][0] > 0.4 * 540 * 0.9 * 0.5  # carried right with the person's head
    assert max(abs(x - 420) + abs(y - 120) for x, y in cam[2:5]) > 3  # jolted by the shake
    assert abs(cam[-1][0] - 420) < 0.6 and abs(cam[-1][1] - 120) < 0.6  # and back at rest after it


@ENGINE
def test_the_director_reviews_the_scene_at_the_desk(tmp_path, monkeypatch, capsys):
    import json

    folder = tmp_path / "desk-r1"
    folder.mkdir()
    (folder / "scene.html").write_text("""<!doctype html><html><head><style>#stage{position:absolute;inset:0}
    #ball{position:absolute;left:100px;top:100px;width:60px;height:60px;border-radius:50%;background:#f00}</style>
    </head><body><div id="root" data-composition-id="main" data-start="0" data-duration="1" data-width="270"
    data-height="480"><div id="stage" class="clip" data-start="0" data-duration="1"><div id="ball"></div></div></div>
    <script type="application/json" id="beats">[{"t": 0.4, "what": "the ball lands"}]</script><script>
    const tl = gsap.timeline({ paused: true }); tl.fromTo('#ball', { y: 0 }, { y: 200, duration: 0.4 }, 0);
    window.__timelines = window.__timelines || {}; window.__timelines["main"] = tl;</script></body></html>""",
                                         encoding="utf-8")
    (folder / "brief.json").write_text(json.dumps({"ask": "a ball lands", "start": 0.0, "end": 1.0, "width": 270,
                                                   "height": 480, "fps": 30, "style": "", "words": [[0.3, 0.5, "lands"]]}),
                                       encoding="utf-8")
    asked = []

    def director(client, system, content, *args, **kw):
        asked.append(content)
        return '{"score": 6, "ok": false, "problems": ["at 0.40 s: the ball lands without a squash"]}'

    monkeypatch.setattr(scenes, "_ask", director)
    monkeypatch.setattr(scenes, "_client", lambda: None)
    scenes.review_command(folder)
    said = capsys.readouterr().out
    assert "review 1: 6/10, not ready yet" in said and "without a squash" in said
    assert (folder / "reviews" / "review-1.md").exists() and list((folder / "reviews").glob("review-1-around-*.jpg"))
    assert any(b.get("type") == "image" for b in asked[0])  # it judged real frames of the scene
    scenes.review_command(folder)
    assert "review 2:" in capsys.readouterr().out  # each review kept apart


def test_a_desk_round_ends_on_the_directors_last_review_of_that_very_scene(tmp_path):
    import json

    reviews = tmp_path / "reviews"
    reviews.mkdir()
    html = "<div id='stage'>final</div>"
    (reviews / "review-1.json").write_text(json.dumps({"scene": scenes._digest("<div id='stage'>first</div>"),
                                                       "score": 3, "ok": False, "problems": ["old"]}), encoding="utf-8")
    (reviews / "review-2.json").write_text(json.dumps({"scene": scenes._digest(html), "score": 7, "ok": False,
                                                       "problems": ["at 1.2 s: soften the glow"]}), encoding="utf-8")
    (reviews / "review-2-around-01.20s.jpg").write_bytes(b"jpg")
    seen = scenes.last_review(tmp_path, html)
    assert seen["score"] == 7 and seen["problems"] == ["at 1.2 s: soften the glow"] and seen["sheet"] == [(1.2, b"jpg")]
    assert scenes.last_review(tmp_path, "<div id='stage'>changed after its review</div>") is None
