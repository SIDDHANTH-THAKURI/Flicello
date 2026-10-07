from __future__ import annotations

import numpy as np
from PIL import Image

from vidosuper.edit import baseline_edl, build_timeline
from vidosuper.ffmpeg import ffprobe_json, run_ffmpeg
from vidosuper.ingest.chapters import chronological
from vidosuper.outro.card import Outro
from vidosuper.outro.intro import CLAP_AT, DONE_AT, Intro, IntroData, build_head, intro_audio
from vidosuper.outro.stats import RunStats
from vidosuper.outro.tail import build_tail
from vidosuper.render import render_edl
from vidosuper.render.audio_mix import frames_to_samples
from vidosuper.schemas import EditMeta, MusicSpec


def _data() -> IntroData:
    thumbs = [Image.new("RGB", (160, 90), c) for c in ((40, 120, 200), (220, 160, 60), (60, 180, 120))]
    beats = [round(k / 40, 4) for k in range(40)]
    blocks = [(0.0, 0.25, (64, 200, 182)), (0.25, 0.26, (255, 196, 96)), (0.26, 0.7, (156, 124, 255)),
              (0.7, 1.0, (255, 122, 104))]  # one shot is a sliver, like a stutter
    return IntroData(title="Can Claude edit?", header="CLAUDE CODE · VIDEO EDITING TRIALS", take="TAKE 2",
                     lines=[("vido analyze trip", "✓ 3 clips · 0:42"), ("cut to the beat", "✓ 4 shots · ±17 ms")],
                     thumbs=thumbs, wave=np.linspace(0.2, 1.0, 50), beats=beats, downbeats=beats[::4], drop=0.4,
                     blocks=blocks, deck_label="{n} clips", wave_label="120 BPM · drop 0:16", cut_label="{n} shots",
                     clips=3, shots=4)


def test_intro_frames_and_sound():
    for w, h in ((480, 270), (270, 480)):
        intro = Intro(_data(), w, h, 12)
        frames = [np.asarray(intro.frame(t), dtype=np.float32) for t in (0.6, 1.8, DONE_AT + 0.4, 4.2, intro.duration)]
        assert all(f.shape == (h, w, 3) for f in frames)
        assert frames[0].mean() > frames[-1].mean() + 2  # it fades to black for the film
        centre = frames[2][int(h * 0.42):int(h * 0.52), int(w * 0.3):int(w * 0.7)]
        assert centre.max() > 200  # "Done." is on screen
    kinds = {c.kind for c in intro.cues}
    assert {"key", "pop", "chime", "clap", "riser_end"} <= kinds
    assert any(abs(c.t - CLAP_AT) < 1e-9 and c.kind == "clap" for c in intro.cues)
    audio = intro_audio(intro)
    assert audio.shape == (frames_to_samples(intro.frames, intro.fps), 2) and np.abs(audio).max() > 0.05


def test_intro_plays_before_the_film(ingested, cfg, media, tmp_path):
    project, clips, signals = ingested
    meta = EditMeta(width=1920, height=1080, fps=30)
    edl = baseline_edl(chronological(list(clips.values())), signals, project_dir=str(project.clips_dir), meta=meta,
                       target_seconds=6, shot_seconds=2.0, music=MusicSpec(path=str(media / "music.wav"), gain_db=-6))
    film = build_timeline(edl, project.clips())
    intro = Intro(_data(), 960, 540, 30)  # draft size
    head = build_head(intro, tmp_path / "intro", "draft", cfg)
    tail = build_tail(Outro(RunStats(clips=3, shots=3), 960, 540, 30, "short"), tmp_path / "outro", "draft", cfg)
    result = render_edl(edl, project, cfg, quality="draft", out_path=tmp_path / "with_intro.mp4", head=head, tail=tail)
    video = next(s for s in ffprobe_json(result.output)["streams"] if s["codec_type"] == "video")
    assert int(video["nb_frames"]) == intro.frames + film.total_frames + tail.frames == result.frames
    still = tmp_path / "done.png"  # "Done." is on screen early on, before any of the film
    run_ffmpeg(["-ss", f"{DONE_AT + 0.4:.2f}", "-i", result.output, "-frames:v", "1", str(still)])
    assert np.asarray(Image.open(still).convert("L"), dtype=np.float32).max() > 200
