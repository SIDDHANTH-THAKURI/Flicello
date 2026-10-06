"""The outro: real numbers from a run, the crew drawn as characters, and the tail appended after the film."""

from __future__ import annotations

import json

import numpy as np

from vidosuper.edit import baseline_edl, build_timeline
from PIL import Image

from vidosuper.ffmpeg import ffprobe_json, run_ffmpeg
from vidosuper.ingest.chapters import chronological
from vidosuper.outro.card import STYLES, Outro, crew_for, tiles_for
from vidosuper.outro.draw import text_sprite
from vidosuper.outro.stats import RunStats, collect_stats, short_credit
from vidosuper.outro.tail import build_tail, music_time_at_end, outro_audio
from vidosuper.render import render_edl
from vidosuper.schemas import EditMeta, MusicJump, MusicSpec, TextOverlay

STATS = RunStats(clips=16, raw_seconds=475.3, uhd=True, measured_frames=4755, moments=56, acts=5, drafts=4, reviewed=2, shots=18,
                 final_seconds=57.6, critic_notes=54, adversary_notes=7, verdicts=2, score_trail=[6.38, 6.88],
                 panel=[("story", "7.2"), ("rhythm", "7"), ("hook", "5.8"), ("craft", "6.5"), ("sound", "8")],
                 code_lines=8000, music_credit='"Dream keeper" by zero-project is licensed under CC BY 3.0. To view '
                                               'a copy of this license, visit https://creativecommons.org/')


def test_stats_come_from_the_run(ingested, tmp_path):
    project, clips, _ = ingested
    run = tmp_path / "run"
    for it in ("01", "02"):
        (run / "iterations" / it / "critiques").mkdir(parents=True)
        (run / "iterations" / it / "draft.mp4").write_bytes(b"")
    (run / "run.json").write_text(json.dumps({"scores": {"2": 6.5}, "best_iteration": 2,
                                              "music_credit": STATS.music_credit}), encoding="utf-8")
    (run / "brief.json").write_text(json.dumps({"structure": [{}, {}, {}]}), encoding="utf-8")
    crit = run / "iterations" / "02" / "critiques"
    (crit / "story.json").write_text(json.dumps({"overall": 7.0, "issues": [{}, {}]}), encoding="utf-8")
    (crit / "adversary.json").write_text(json.dumps({"overall": 5, "issues": [{}]}), encoding="utf-8")
    (run / "iterations" / "02" / "judge").mkdir()
    (run / "iterations" / "02" / "judge" / "ab.json").write_text("{}", encoding="utf-8")

    s = collect_stats(run, project, 2)
    assert s.clips == len(clips) and s.raw_seconds > 0 and s.measured_frames > 0
    assert (s.acts, s.drafts, s.reviewed, s.critic_notes, s.adversary_notes, s.verdicts) == (3, 2, 1, 2, 1, 1)
    assert s.score_trail == [6.5] and s.panel == [("story", "7")] and s.code_lines > 1000
    # who appears is decided by what actually ran: no edit yet, so no editor
    assert "editor" not in {m.key for m in crew_for(s)} and "judge" in {m.key for m in crew_for(s)}


def test_credit_is_shortened_for_the_screen():
    assert short_credit(STATS.music_credit) == "Music: “Dream keeper” by zero-project · CC BY 3.0"
    assert short_credit(None) is None


def test_outro_frames_in_both_orientations():
    for w, h in ((480, 270), (270, 480)):
        outro = Outro(STATS, w, h, 10, "full")
        assert abs(outro.duration - STYLES["full"]["sign"][1]) < 1e-6 and outro.frames == round(outro.duration * 10)
        assert [m.name for m in outro.crew] == ["Scout", "Muse", "Snip", "The Panel", "Grump", "Judge"]
        assert len(outro.tiles) == 4 and outro.tiles[0].clock_from == STATS.raw_seconds
        black, crew = (np.asarray(outro.frame(t), dtype=np.float32) for t in (0.0, 6.0))
        assert black.shape == (h, w, 3) and black.mean() < 2  # starts from the film's black
        assert crew.std() > 10  # characters and text on screen
    short = Outro(STATS, 480, 270, 10, "short")
    assert short.duration < 9 and not short.tiles
    assert {c.kind for c in short.cues} >= {"pop", "chime", "pluck"}


def test_arrow_is_drawn_not_missing():
    with_arrow = text_sprite("a → b", "Nunito-SemiBold", 40, (255, 255, 255))
    plain = text_sprite("a  b", "Nunito-SemiBold", 40, (255, 255, 255))
    assert with_arrow.width > plain.width + 20  # room for the drawn arrow


def test_music_carries_on_from_where_the_film_left_it():
    music = MusicSpec(path="x.wav", start=10.0, jumps=[MusicJump(at=4.0, to=40.0)])
    assert abs(music_time_at_end(music, 9.0) - 45.0) < 1e-9


def test_outro_is_appended_after_the_film(ingested, cfg, media, tmp_path):
    project, clips, signals = ingested
    meta = EditMeta(width=1920, height=1080, fps=30)
    edl = baseline_edl(chronological(list(clips.values())), signals, project_dir=str(project.clips_dir), meta=meta,
                       target_seconds=6, shot_seconds=2.0, music=MusicSpec(path=str(media / "music.wav"), gain_db=-6))
    # on-screen text is a looped still: it must not keep the film running over the outro (it did once,
    # leaving the whole outro black)
    edl.texts = [TextOverlay(text="How do you zoom?", start=1.0, end=2.5, position="lower-third")]
    film = build_timeline(edl, project.clips())
    outro = Outro(STATS, 960, 540, 30, "short")  # draft size
    tail = build_tail(outro, tmp_path / "outro", "draft", cfg)
    assert outro_audio(outro).shape == tail.audio.shape and np.abs(tail.audio).max() > 0.05
    result = render_edl(edl, project, cfg, quality="draft", out_path=tmp_path / "with_outro.mp4", tail=tail)
    video = next(s for s in ffprobe_json(result.output)["streams"] if s["codec_type"] == "video")
    assert int(video["nb_frames"]) == film.total_frames + outro.frames == result.frames
    assert abs(result.duration - (film.total_frames + outro.frames) / 30) < 0.1
    still = tmp_path / "crew.png"  # the crew is on screen ~4 s into the short outro
    run_ffmpeg(["-ss", f"{film.total_frames / 30 + 4.0:.2f}", "-i", result.output, "-frames:v", "1", str(still)])
    assert np.asarray(Image.open(still).convert("L"), dtype=np.float32).mean() > 8
