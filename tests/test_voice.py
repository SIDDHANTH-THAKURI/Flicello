"""Cello's voice on this machine (local/voice.py, studio/cello_mcp.py): what they say is cut by its pauses, Claude's
words are spoken sentence by sentence, speaking over it stops it, its tools run on the page, and only the page that
asked (on this computer) gets the line."""

from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest

from vidosuper.local import voice
from vidosuper.studio import cello, cello_mcp

from conftest import local_client


def test_words_are_spoken_a_whole_sentence_at_a_time():
    done, rest = voice.sentences("Done: captions on. Karaoke sty")
    assert done == ["Done:", "captions on."] and rest == "Karaoke sty"
    assert voice.sentences("Karaoke style!  Have a look", final=True) == (["Karaoke style!", "Have a look"], "")
    assert voice.sentences("1.5 seconds", final=False) == ([], "1.5 seconds")  # a decimal point is no sentence's end
    assert voice.sentences("  ...  ", final=True) == ([], "")


def _tone(seconds: float, level: float) -> bytes:
    t = np.arange(int(seconds * 16000)) / 16000
    return (np.sin(2 * np.pi * 220 * t) * level * 32767).astype(np.int16).tobytes()


def _feed(ears, pcm: bytes):
    for k in range(0, len(pcm), 1280):  # 40 ms at a time, as the page sends
        ears.feed(pcm[k:k + 1280])


def test_what_they_say_is_cut_by_its_pauses_and_room_noise_is_not_speech():
    starts, stretches = [], []
    ears = voice.Ears(lambda: starts.append(1), stretches.append)
    _feed(ears, _tone(1.0, 0.002))  # the room
    _feed(ears, _tone(1.2, 0.2))  # they speak
    _feed(ears, _tone(0.5, 0.002))  # a short pause: still the same thing they are saying
    _feed(ears, _tone(0.6, 0.2))
    assert starts == [1] and not stretches
    _feed(ears, _tone(1.1, 0.002))  # a second of quiet: done
    assert len(stretches) == 1
    seconds = len(stretches[0]) / 16000
    assert 2.3 <= seconds <= 3.6  # both parts, the pause between and the end's quiet, with a little before it began
    _feed(ears, _tone(3.0, 0.003))
    assert starts == [1]  # quiet noise never starts a turn


def test_the_tools_are_cellos_and_each_runs_on_the_page(monkeypatch):
    listed = cello_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in listed] == [t["name"] for t in cello.TOOLS]
    assert all(t["inputSchema"]["type"] == "object" for t in listed)
    init = cello_mcp.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["capabilities"] == {"tools": {}} and init["result"]["protocolVersion"] == "2025-06-18"
    assert cello_mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    monkeypatch.setattr(cello_mcp, "call", lambda name, args: (f"ran {name} {json.dumps(args)}", False))
    out = cello_mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": "edit", "arguments": {"ops": []}}})["result"]
    assert out == {"content": [{"type": "text", "text": 'ran edit {"ops": []}'}], "isError": False}


class FakeMind:
    def __init__(self, folder, url, token, on_text, on_done, on_error):
        self.on_text, self.on_done = on_text, on_done
        self.said, self.interrupts = [], 0

    def say(self, text):
        self.said.append(text)

    def interrupt(self):
        self.interrupts += 1

    def close(self):
        pass


def test_it_speaks_as_it_writes_stops_when_spoken_over_and_runs_tools_on_the_page(monkeypatch):
    monkeypatch.setattr(voice, "Mind", FakeMind)
    monkeypatch.setattr(voice, "speak", lambda s: np.zeros(int(0.1 * 24000), np.int16))
    sent = []

    async def run():
        async def send(msg):
            sent.append(msg)

        live = voice.Live("http://127.0.0.1:1", send, asyncio.get_running_loop())
        live.text("(Note: they woke you)")
        live.mind.on_text("Hello there. I")
        live.mind.on_text(" can see your film.")
        live.mind.on_done()
        await asyncio.sleep(0.3)
        said = [m["serverContent"]["outputTranscription"]["text"] for m in sent
                if "outputTranscription" in m.get("serverContent", {})]
        assert said == ["Hello there. ", "I can see your film. "]
        assert sum(1 for m in sent if m.get("serverContent", {}).get("modelTurn")) == 2
        assert sent[-1] == {"serverContent": {"turnComplete": True}}
        # spoken over while it answers: it stops at once and the rest of that turn is never said
        live.text("make it hype")
        live.mind.on_text("Sure, I'll make")
        live.ears.on_start()
        await asyncio.sleep(0.05)
        assert live.mind.interrupts == 1 and {"serverContent": {"interrupted": True}} in sent
        live.mind.on_text(" it hype now.")
        live.mind.on_done()
        await asyncio.sleep(0.2)
        assert not any("hype now" in json.dumps(m) for m in sent)
        # a tool: asked of the page, answered by it
        task = asyncio.ensure_future(live.tool("look", {}))
        await asyncio.sleep(0.05)
        call = next(m for m in sent if "toolCall" in m)["toolCall"]["functionCalls"][0]
        assert call["name"] == "look"
        live.answered([{"id": call["id"], "name": "look", "response": {"result": {"page": "home"}}}])
        assert await task == {"result": {"page": "home"}}
        live.close()
        assert live.token not in voice.SESSIONS

    asyncio.run(run())


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    from vidosuper.studio.service import Studio
    from vidosuper.studio.settings import load_settings

    s = load_settings(tmp_path / "home", {"brain": "local", "owner_brain": "local"})
    s.limits.min_free_gb = 0.0
    return Studio(s)


def test_the_page_gets_this_machines_voice_with_a_one_time_ticket(studio, monkeypatch):
    monkeypatch.setenv("VIDO_CELLO_VOICE_MODE", "local")
    client, _, csrf = local_client(studio)
    tok = client.post("/api/cello/token", headers={"X-CSRF": csrf}).json()
    assert tok["local"] and tok["url"].startswith("/api/cello/live?ticket=")
    with pytest.raises(Exception):  # no ticket, no line
        with client.websocket_connect("/api/cello/live?ticket=wrong"):
            pass
    assert client.post("/api/cello/live/tool", headers={"X-Cello-Live": "nope"}, json={"name": "look"}).status_code == 401
    monkeypatch.setenv("VIDO_CELLO_VOICE_MODE", "auto")
    monkeypatch.setattr(voice, "available", lambda: False)
    monkeypatch.setenv("GEMINI_API_KEY", "")
    r = client.post("/api/cello/token", headers={"X-CSRF": csrf})
    assert r.status_code == 503 and "download its voice on the Connect page" in r.json()["detail"]
