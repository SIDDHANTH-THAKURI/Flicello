"""The clip watcher (studio/brain/gemini.py): Gemini watches each clip itself, picture and sound, and logs it like the
contact-sheet analyst; a wrong answer is sent back with its problems, every call is paid for, and the upload is
deleted at once."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from vidosuper.schemas import ClipInfo
from vidosuper.studio.brain import make_watcher
from vidosuper.studio.brain.backends import BadReply
from vidosuper.studio.brain.gemini import GeminiWatcher, frames_per_second, watcher_prompt
from vidosuper.studio.settings import load_settings

CLIP = ClipInfo(clip_id="c-1", path="x.mp4", rel_path="x.mp4", fingerprint="f", duration=13.1, fps=59.94)


def _log(start: float, end: float, people: str = "far") -> str:
    return json.dumps({"clip_id": "whatever", "summary": "A car waits at a red light, then pulls away.",
                       "scene": "city street from a car", "camera": "car mount", "energy": 2, "visual_appeal": 4,
                       "moments": [{"start": start, "end": end, "peak": None, "kind": "establishing",
                                    "description": "Towers over the intersection", "hook": 3, "strength": 4,
                                    "best_use": "establishing", "sound": "music-only", "people": people}]})


class FakeClient:
    def __init__(self, answers):
        self.answers = list(answers)
        self.asked, self.deleted = [], []
        self.files = NS(upload=self._upload, get=self._get, delete=lambda name: self.deleted.append(name))
        self.models = NS(generate_content=self._generate)
        self._polls = 0

    def _upload(self, file):
        return NS(name="files/1", uri="https://x/files/1", state=NS(name="PROCESSING"))

    def _get(self, name):
        self._polls += 1
        return NS(name=name, uri="https://x/files/1", state=NS(name="ACTIVE"))

    def _generate(self, model, contents, config):
        self.asked.append((model, contents, config))
        return NS(text=self.answers.pop(0), usage_metadata=NS(prompt_token_count=3000, candidates_token_count=600,
                                                             thoughts_token_count=1000))


def test_the_watcher_logs_a_clip_from_its_picture_and_sound(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    client = FakeClient([_log(5.0, 20.0), _log(5.5, 9.0)])  # first: a moment past the clip's end
    spent = []
    w = GeminiWatcher("gemini-pro-latest", spent.append, tmp_path / "logs", client=client)
    a = w.analyse_clip(CLIP, "brief: 13.1 s, 59.94 fps", tmp_path / "copy.mp4")
    assert a.clip_id == "c-1" and a.moments[0].start == 5.5 and a.moments[0].people == "far"
    assert len(client.asked) == 2 and "must satisfy 0 <= start < end <= 13.10" in client.asked[1][1][-1]
    assert client.asked[0][1][0].video_metadata.fps == frames_per_second(13.1) == 2.0
    assert client.deleted == ["files/1"]  # their footage never stays with Google
    assert len(spent) == 2 and spent[0].task == "watch" and spent[0].usd > 0 and spent[0].output_tokens == 1600
    assert (tmp_path / "logs" / "watch-c-1-2.json").exists()
    assert "watch and listen to all of it" in client.asked[0][2].system_instruction


def test_a_watcher_that_stays_wrong_gives_way_and_still_deletes_the_upload(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    client = FakeClient(["not json", _log(5.0, 20.0), _log(1.0, 30.0)])
    w = GeminiWatcher("gemini-pro-latest", lambda u: None, client=client)
    with pytest.raises(BadReply):
        w.analyse_clip(CLIP, "brief", tmp_path / "copy.mp4")
    assert client.deleted == ["files/1"] and len(client.asked) == 3


def test_gemini_watches_with_a_key_and_a_model_and_claude_watches_without(tmp_path, monkeypatch):
    s = load_settings(tmp_path / "home", {"brain": "api"})
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    w = make_watcher("claude_code", s, lambda u: None)  # no key: Claude watches, the analyst logs the clips
    assert w.kind == "claude" and w.via == "claude_code" and not w.watches_clips
    assert make_watcher("api", s, lambda u: None).via == "api"
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    assert make_watcher("local", s, lambda u: None) is None  # no AI at all
    s.models.watcher = ""
    assert make_watcher("api", s, lambda u: None).kind == "claude"  # Gemini switched off
    s.models.watcher = "gemini-pro-latest"
    w = make_watcher("api", s, lambda u: None)
    assert w is not None and w.model == "gemini-pro-latest"
    assert "contact sheets" not in watcher_prompt()
    assert frames_per_second(300) == 1.0 and frames_per_second(1200) == 0.5


def test_claude_watches_a_film_from_its_frames_words_and_sounds(tmp_path, monkeypatch):
    import subprocess

    import cv2

    from vidosuper.ffmpeg import ffmpeg_bin
    from vidosuper.studio.brain import claude_watcher as cw

    film = tmp_path / "film.mp4"  # 4 s: blue, then a white flash with a bang at 2 s
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x568:r=30:d=4",
                    "-f", "lavfi", "-i", "anoisesrc=d=4:a=0.001", "-f", "lavfi", "-i", "sine=f=300:d=0.3",
                    "-filter_complex", "[2:a]adelay=2000|2000,volume=8[b];[1:a][b]amix=inputs=2:duration=first[a];"
                    "[0:v]drawbox=enable='between(t,2,2.5)':x=0:y=0:w=iw:h=ih:color=white:t=fill[v]",
                    "-map", "[v]", "-map", "[a]", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(film)],
                   check=True)
    monkeypatch.setattr(cw, "_words", lambda video: [(1.0, 1.4, "now")])
    asked = {}

    def ask(self, text, images, model, task):
        asked.update(text=text, images=images, model=model, task=task)
        if task == "find":
            return 'Here: {"moments": [{"start": 2.0, "end": 2.5, "seen": "a white flash"}, {"start": "x"}]}'
        if task == "review":
            return '{"stop": 12, "hold": 4, "leave_at": 2.6, "best": "2 s, the flash", "weakest": "0 s", "why": "faster"}'
        if task == "point":
            return '{"found": true, "xmin": 100, "ymin": 200, "xmax": 300, "ymax": 600}'
        return '{"svg": "<svg viewBox=\\"0 0 100 100\\"><circle cx=\\"50\\" cy=\\"50\\" r=\\"40\\"/></svg>"}'

    monkeypatch.setattr(cw.ClaudeWatcher, "_ask", ask)
    w = cw.ClaudeWatcher("claude_code", lambda u: None)
    found = w.find_moments(film, "a flash")
    assert found == [{"start": 2.0, "end": 2.5, "seen": "a white flash"}]  # the broken one is left out
    assert "[1.0] now" in asked["text"] and "2.0 s" in asked["text"] and "every 0.5 s" in asked["text"]
    sheet = cv2.imdecode(__import__("numpy").frombuffer(asked["images"][0], "uint8"), cv2.IMREAD_COLOR)
    assert len(asked["images"]) == 1 and sheet.shape[1] == 4 * cw.TILE_W  # 8 frames, one sheet of four across
    assert w.review(film) == {"stop": 10, "hold": 4, "leave_at": 2.6, "best": "2 s, the flash", "weakest": "0 s",
                              "why": "faster"}
    jpeg = cv2.imencode(".jpg", cv2.imread(str(tmp_path / "x.png")) if False else
                        __import__("numpy").zeros((100, 100, 3), "uint8"))[1].tobytes()
    assert w.find_box(jpeg, "the boat") == (0.2, 0.4, 0.2, 0.4) and w.point_at(jpeg, "the boat") == (0.2, 0.4)
    assert asked["model"] == cw.QUICK_MODEL
    assert w.draw("a moon").startswith("<svg")
