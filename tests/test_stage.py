"""The real shot in the scene's page (motion/stage.py): the shot first in #stage, the person cut out of it right after
the behind layer, and the browser compositing them with the design for real (a blend mode mixes with the footage)."""

from __future__ import annotations

import json
import subprocess

import cv2
import numpy as np
import pytest

from vidosuper import motion
from vidosuper.ffmpeg import ffmpeg_bin
from vidosuper.motion import scenes, stage

ENGINE = pytest.mark.skipif(not motion.available(), reason="the motion engine isn't installed (npm ci in motion/)")

DOC = """<!doctype html><html><head><style>#stage{position:absolute;inset:0}</style></head><body>
<div id="root" data-composition-id="main" data-start="0" data-duration="2" data-width="270" data-height="480">
<div class="clip" data-start="0" data-duration="2" id="stage"><img src="assets/a.png" alt="">
<div class="behind" style="position:absolute;inset:0"><div><div class="sky"></div></div><br><svg><path d="M0 0"/></svg></div>
<div id="front"><div class="behind-not">x</div></div></div></div></body></html>"""


def test_the_shot_goes_first_in_the_stage_and_the_person_right_after_its_behind_layer():
    doc = stage.place(DOC, person=True)
    stage_tag = doc.index('id="stage">')
    shot, person = doc.index('<video id="shot"'), doc.index('<video id="person"')
    assert shot == stage_tag + len('id="stage">')  # the very first thing inside #stage
    assert doc[:person].endswith('<svg><path d="M0 0"/></svg></div>')  # right after .behind closes, nested and all
    assert person < doc.index('<div id="front">')
    assert 'id="person"' not in stage.place(DOC, person=False)
    no_behind = DOC.replace('class="behind"', 'class="world"')
    assert 'id="person"' not in stage.place(no_behind, person=True) and 'id="shot"' in stage.place(no_behind, True)
    assert stage.place("<div>no stage here</div>", person=True) == "<div>no stage here</div>"


def test_the_looks_say_plainly_what_the_cut_out_cant_show_yet(tmp_path, monkeypatch):
    monkeypatch.setattr(scenes, "STAGE", [tmp_path])
    behind = '<div id="stage"><div class="behind"></div></div>'
    assert "isn't ready yet" in scenes.person_note(behind, "you")
    (tmp_path / stage.PERSON).write_bytes(b"x")
    (tmp_path / "person.json").write_text(json.dumps({"clean": False}), encoding="utf-8")
    assert "quick one" in scenes.person_note(behind, "you") and "don't count" in scenes.person_note(behind, "reviewer")
    (tmp_path / "person.json").write_text(json.dumps({"clean": True}), encoding="utf-8")
    assert scenes.person_note(behind, "you") == "" and scenes.person_note("<div id='stage'></div>", "you") == ""


def _video(path, frames, fps=30):
    """frames (h, w, 3) BGR uint8 -> a near-lossless mp4."""
    h, w = frames[0].shape[:2]
    enc = subprocess.Popen([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
                            "-r", str(fps), "-i", "-", "-c:v", "libx264", "-crf", "4", "-pix_fmt", "yuv420p", str(path)],
                           stdin=subprocess.PIPE)
    for f in frames:
        enc.stdin.write(f.tobytes())
    enc.stdin.close()
    enc.wait()


@ENGINE
def test_the_browser_composites_the_shot_the_behind_layer_the_person_and_real_blends(tmp_path, monkeypatch):
    w, h, n = 270, 480, 30
    frames, mattes = [], []
    for i in range(n):  # a blue room, the "person" a white block that moves right
        f = np.zeros((h, w, 3), np.uint8)
        f[:] = (200, 60, 30)
        x = 60 + i * 2
        f[150:330, x:x + 100] = 255
        m = np.zeros((h, w, 3), np.uint8)
        m[150:330, x:x + 100] = 255
        frames.append(f)
        mattes.append(m)
    _video(tmp_path / "film.mp4", frames)
    _video(tmp_path / "matte.mp4", mattes)
    st = tmp_path / "stage"
    stage.cut_shot(tmp_path / "film.mp4", 0.0, n / 30, st / stage.SHOT)
    stage.person_clip(st / stage.SHOT, tmp_path / "matte.mp4", st / stage.PERSON)
    monkeypatch.setattr(scenes, "STAGE", [st])
    monkeypatch.setattr(scenes, "ASSETS", [])
    html = """<!doctype html><html><head><style>#stage{position:absolute;inset:0}
    .behind{position:absolute;inset:0} #wall{position:absolute;inset:0;background:#ff0000}
    #lamp{position:absolute;left:0;top:380px;width:270px;height:100px;background:#00ff00;mix-blend-mode:screen}
    </style></head><body><div id="root" data-composition-id="main" data-start="0" data-duration="1" data-width="270"
    data-height="480"><div id="stage" class="clip" data-start="0" data-duration="1">
    <div class="behind"><div id="wall"></div></div><div id="lamp"></div></div></div><script>
    const tl = gsap.timeline({ paused: true }); tl.to('#lamp', { opacity: 1, duration: 1 }, 0);
    window.__timelines = window.__timelines || {}; window.__timelines["main"] = tl;</script></body></html>"""
    got = scenes.moments(html, tmp_path / "look", [0.5])
    img = cv2.imdecode(np.frombuffer(got[0.5], np.uint8), cv2.IMREAD_COLOR).astype(int)
    x = 60 + 15 * 2  # where the block is at 0.5 s (frame 15)
    assert (img[240, x + 50] > 225).all()  # the person, over the behind layer
    assert img[240, 20, 2] > 225 and img[240, 20, :2].max() < 40  # the red wall where their room was
    lamp = img[430, 20]  # green in screen over the red wall: yellow, as light adds
    assert lamp[1] > 225 and lamp[2] > 225 and lamp[0] < 40
