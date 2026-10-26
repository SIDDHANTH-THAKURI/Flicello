"""Music and sound effects made on this machine (music/maker.py): chosen when they can run, a music plan becomes the
music model's caption, structure tags, tempo and length, the sound process answers on 127.0.0.1 and what it makes
lands where the film's mix looks for it; a scene's sound cue and the pro effects are made here too."""

from __future__ import annotations

import socket
import threading

import numpy as np
import pytest
import soundfile as sf

from vidosuper.music import compose, made, maker


def _no_env(monkeypatch):
    monkeypatch.setattr(compose, "_load_dotenv", lambda: None)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    monkeypatch.setenv("VIDO_MUSIC", "auto")
    monkeypatch.setenv("VIDO_SFX", "auto")


def test_who_makes_the_music_and_the_sounds(monkeypatch):
    _no_env(monkeypatch)
    monkeypatch.setattr(maker, "can_make_music", lambda: True)
    monkeypatch.setattr(maker, "can_make_sounds", lambda: True)
    assert compose.maker() == "local" and compose.sound_maker() == "local"  # their own GPU first: nothing to pay
    monkeypatch.setattr(maker, "can_make_music", lambda: False)
    monkeypatch.setattr(maker, "can_make_sounds", lambda: False)
    assert compose.maker() == "" and not compose.available() and compose.sound_maker() == ""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "a-key")
    assert compose.maker() == "elevenlabs" and compose.available() and compose.sound_maker() == "elevenlabs"
    monkeypatch.setenv("VIDO_MUSIC", "local")
    assert compose.maker() == "local"
    monkeypatch.setenv("VIDO_SFX", "elevenlabs")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    assert compose.sound_maker() == ""  # asked for ElevenLabs without a key: nobody


def _sound(**kw):
    return made.Sound(title="Beach", bpm=96, styles=["warm lo-fi hip hop", "soft Rhodes piano chords", "dusty drums"],
                      parts={"drop": ["full band in"], "ending": ["one last chord"]}, **kw)


def test_a_cut_plan_becomes_a_caption_its_sections_tags_and_an_exact_tempo_and_length():
    sound = _sound()
    plan = made.plan(made.layout(60, sound.bpm, "cut"), sound, "cut")
    words = maker.from_plan(plan)
    assert words["bpm"] == 96 and words["seconds"] == pytest.approx(made.plan_seconds(plan), abs=0.01)
    cap = words["caption"]
    assert cap.startswith("instrumental") and "soft Rhodes piano chords" in cap and len(cap) <= maker.CAPTION_CHARS
    assert "bpm" not in cap.lower() and "steady tempo" not in cap  # tempo is set, never written in the caption
    tags = [line for line in words["lyrics"].split("\n\n")]
    assert all(t.startswith("[") and t.endswith("]") for t in tags)  # an instrumental: tags only, no words to sing
    names = [t[1:-1].split(" - ")[0] for t in tags]
    assert names[0] == "Intro" and names[1] == "Build" and "Drop" in names and names[-1] == "Outro"
    assert "[Build - building energy]" in tags and "[Breakdown - drums drop out]" in tags  # never "[Intro - short intro]"
    assert all(len(t) <= 50 for t in tags)  # a short "how", never a stack of them


def test_a_section_split_in_chunks_is_still_one_section():
    sections = [made.Section("intro", 2), made.Section("groove", 40), made.Section("ending", 2)]  # 137 s of groove
    plan = made.plan(sections, made.Sound(title="Talk", bpm=70, styles=["ambient pads"]), "bed")
    assert len(plan["chunks"]) == 4  # a section over 120 s is two chunks for ElevenLabs
    words = maker.from_plan(plan)
    assert [t.split(" - ")[0] for t in words["lyrics"].split("\n\n")] == ["[Intro", "[Main Theme", "[Outro]"]
    assert words["caption"].startswith("instrumental, ambient pads")
    with pytest.raises(maker.MakerError):
        maker.from_plan({"chunks": [{"song_id": "x", "range": {}}]})  # an ElevenLabs edit plan: nothing to make here


class FakeMaker:
    """What the models make, without a GPU: a tone of the length asked (the music at 48 kHz, the effects at 44.1)."""

    asked: list = []

    def __init__(self):
        pass

    def make_music(self, body, out):
        FakeMaker.asked.append(("music", body))
        seconds = 4.0 if not body.get("repaint") else 6.0
        t = np.arange(int(seconds * 48000)) / 48000
        sf.write(str(out), np.stack([np.sin(2 * np.pi * 220 * t)] * 2, axis=1) * 0.3, 48000, subtype="PCM_24")
        return seconds

    def make_sound(self, body, out):
        FakeMaker.asked.append(("sound", body))
        t = np.arange(int(float(body["seconds"]) * 44100)) / 44100
        sf.write(str(out), np.stack([np.sin(2 * np.pi * 880 * t)] * 2, axis=1) * 0.5, 44100, subtype="PCM_24")
        return float(body["seconds"])


@pytest.fixture()
def process(monkeypatch):
    """The sound process for real, on a free port, its models faked."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(maker, "PORT", port)
    monkeypatch.setattr(maker, "_Maker", FakeMaker)
    monkeypatch.setattr(maker, "_start", lambda: pytest.fail("the process was already running"))
    FakeMaker.asked = []
    threading.Thread(target=maker.serve, args=(port,), daemon=True).start()
    maker.ready(wait=10)
    return FakeMaker.asked


def test_a_track_is_made_by_the_process_saved_as_the_mp3_the_film_uses_and_follows_its_taste(process, tmp_path):
    sound = _sound()
    plan = made.plan(made.layout(30, sound.bpm, "cut"), sound, "cut")
    taste = tmp_path / "beat-1.mp3"
    out = maker.compose(plan, taste, seed=7)
    assert out["path"] == taste and out["song_id"] == "" and out["seconds"] == pytest.approx(4.0, abs=0.01)
    assert sf.info(str(taste)).format == "MP3" and not list(tmp_path.glob("*.wav"))
    kind, body = process[-1]
    assert kind == "music" and body["seed"] == 7 and body["bpm"] == 96 and body["lyrics"].startswith("[Intro")
    assert "reference" not in body
    maker.compose(plan, tmp_path / "beat-1-full.mp3", reference=taste)
    assert process[-1][1]["reference"] == str(taste.resolve())
    maker.repaint(taste, 1.0, 3.0, "warm lo-fi, calmer", tmp_path / "beat-1-e1.mp3")
    assert process[-1][1]["repaint"] == {"source": str(taste.resolve()), "start": 1.0, "end": 3.0}
    assert (tmp_path / "beat-1-e1.mp3").exists()


def test_a_sound_is_made_to_its_words_at_48k_stereo_for_the_mix(process, tmp_path):
    out = maker.sound("a wooden drawer slammed shut", 1.2, tmp_path / "made" / "x.wav")
    info = sf.info(str(out))
    assert info.samplerate == 48000 and info.channels == 2 and info.duration == pytest.approx(1.2, abs=0.02)
    assert process[-1] == ("sound", {"prompt": "a wooden drawer slammed shut", "seconds": 1.2, "seed": 0,
                                     "out": process[-1][1]["out"]})
    assert sorted(p.name for p in out.parent.iterdir()) == ["x.wav"]  # nothing half-made left behind


def test_when_the_model_fails_the_caller_hears_it(monkeypatch, tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    class Broken(FakeMaker):
        def make_music(self, body, out):
            raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(maker, "PORT", port)
    monkeypatch.setattr(maker, "_Maker", Broken)
    threading.Thread(target=maker.serve, args=(port,), daemon=True).start()
    maker.ready(wait=10)
    plan = made.plan(made.layout(30, 96, "cut"), _sound(), "cut")
    with pytest.raises(maker.MakerError, match="CUDA out of memory"):
        maker.compose(plan, tmp_path / "beat.mp3")
    _no_env(monkeypatch)
    monkeypatch.setenv("VIDO_MUSIC", "local")
    with pytest.raises(compose.ComposeError, match="CUDA out of memory"):  # the studio's beat stands in for it
        compose.compose_plan(plan, tmp_path / "beat.mp3")
    assert not list(tmp_path.glob("*"))


def test_a_scenes_sound_cue_and_the_pro_effects_are_made_here_and_kept(monkeypatch, tmp_path):
    from vidosuper.motion import scenes
    from vidosuper.render import sfx

    _no_env(monkeypatch)
    monkeypatch.setenv("VIDO_SFX", "local")
    asked = []

    def fake(description, seconds, out, **kw):
        asked.append((description, seconds))
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out), np.zeros((int(seconds * 48000), 2), np.float32) + 0.1, 48000)
        return out

    monkeypatch.setattr(maker, "sound", fake)
    path = scenes.sound_for("glass shatters on stone", 9.0, tmp_path)
    assert path.parent == tmp_path / "made" and asked == [("glass shatters on stone, clean isolated sound effect", 4.0)]
    assert scenes.sound_for("glass shatters on stone", 9.0, tmp_path) == path and len(asked) == 1  # kept by its words
    pro = sfx.sfx_path("whoosh", tmp_path)
    assert pro == tmp_path / "pro" / "whoosh.wav" and asked[-1] == sfx.PRO["whoosh"]

    def broken(*a, **k):
        raise maker.MakerError("the sound maker couldn't start")

    monkeypatch.setattr(maker, "sound", broken)
    assert scenes.sound_for("a door creaks", 1.0, tmp_path) is None  # that cue stays silent, the film goes on
    assert sfx.sfx_path("impact", tmp_path) == tmp_path / "impact.wav"  # the synthesized effect plays instead
