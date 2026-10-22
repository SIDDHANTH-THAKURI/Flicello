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


def test_the_watcher_is_used_only_with_a_key_a_model_and_the_ai(tmp_path, monkeypatch):
    s = load_settings(tmp_path / "home", {"brain": "api"})
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert make_watcher("api", s, lambda u: None) is None  # no key: the contact sheets, as before
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    assert make_watcher("local", s, lambda u: None) is None  # no AI at all
    s.models.watcher = ""
    assert make_watcher("api", s, lambda u: None) is None  # switched off
    s.models.watcher = "gemini-pro-latest"
    w = make_watcher("api", s, lambda u: None)
    assert w is not None and w.model == "gemini-pro-latest"
    assert "contact sheets" not in watcher_prompt()
    assert frames_per_second(300) == 1.0 and frames_per_second(1200) == 0.5
