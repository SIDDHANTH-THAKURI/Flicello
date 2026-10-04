"""Render features for dynamic editing: freeze frames, looks, letterbox, text, J/L cuts, music edits
and automation, sound layers."""

from __future__ import annotations

import cv2
import numpy as np
import pytest
import soundfile as sf

from vidosuper.edit import build_timeline
from vidosuper.render import render_edl
from vidosuper.render.audio_mix import SR, music_bed
from vidosuper.render.sfx import SFX_NAMES, synthesize
from vidosuper.schemas import (EDL, EditMeta, GainPoint, MusicJump, MusicSpec, Segment, SegmentAudio, SoundLayer,
                               TextOverlay)


def _frames(path):
    cap = cv2.VideoCapture(str(path))
    out = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        out.append(frame)
    return out


@pytest.fixture(scope="module")
def styled(ingested, cfg, tmp_path_factory):
    project, clips, _ = ingested
    pan = clips["c_pan"].clip_id  # the camera pans: consecutive frames differ
    edl = EDL(project_dir=str(project.clips_dir),
              meta=EditMeta(width=640, height=360, fps=30, fade_in=0, fade_out=0, look="mono", letterbox=2.39),
              segments=[Segment(id="s1", clip_id=pan, src_in=1.0, src_out=2.0, freeze=1.0),
                        Segment(id="s2", clip_id=clips["a_stable"].clip_id, src_in=1.0, src_out=2.0)],
              texts=[TextOverlay(text="HELLO", start=2.2, end=2.9, size="large", fade=0.0)])
    out = tmp_path_factory.mktemp("styled") / "styled.mp4"
    render_edl(edl, project, cfg, quality="final", out_path=out)
    return edl, _frames(out), build_timeline(edl, project.clips())


def test_freeze_holds_the_last_moving_frame(styled):
    _, frames, timeline = styled
    assert len(frames) == timeline.total_frames == 30 + 30 + 30
    held = [f.astype(int) for f in frames[30:60]]
    assert max(float(np.abs(h - held[0]).mean()) for h in held) < 1.0  # frozen
    assert float(np.abs(frames[5].astype(int) - frames[25].astype(int)).mean()) > 3.0  # the pan was moving


def test_mono_look_and_letterbox(styled):
    _, frames, _ = styled
    frame = frames[10]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    bar = int((360 - 640 / 2.39) / 2)
    assert hsv[bar + 5:-bar - 5, :, 1].mean() < 8  # no colour left
    assert frame[: bar - 2].mean() < 6 and frame[-bar + 2:].mean() < 6  # black bars


def test_text_appears_only_during_its_window(styled):
    _, frames, _ = styled
    centre = (slice(150, 210), slice(220, 420))
    # shot 2 is a still image, so frames inside and outside the text window differ only by the text
    with_text = frames[int(2.5 * 30)][centre].astype(int)
    before, after = frames[int(2.05 * 30)][centre].astype(int), frames[int(2.95 * 30)][centre].astype(int)
    assert np.abs(with_text - before).mean() > 4
    assert np.abs(after - before).mean() < 2


def test_music_bed_jumps_and_automation(tmp_path):
    sr = SR
    t = np.arange(10 * sr) / sr
    tone = np.where(t < 5, np.sin(2 * np.pi * 220 * t), np.sin(2 * np.pi * 880 * t)).astype(np.float32)
    path = tmp_path / "two_tones.wav"
    sf.write(str(path), np.stack([tone, tone], 1), sr, subtype="FLOAT")
    spec = MusicSpec(path=str(path), start=0.0, fade_in=0, fade_out=0, jumps=[MusicJump(at=1.0, to=6.0, crossfade=0.0)],
                     automation=[GainPoint(t=0.0, gain_db=0), GainPoint(t=2.5, gain_db=0), GainPoint(t=2.6, gain_db=-60)])
    bed = music_bed(path, spec, 3 * sr)
    first, after_jump = bed[int(0.2 * sr):int(0.7 * sr), 0], bed[int(1.2 * sr):int(1.7 * sr), 0]
    zero_crossings = lambda x: int(np.sum(np.diff(np.signbit(x)) != 0))  # noqa: E731
    assert zero_crossings(after_jump) > 3 * zero_crossings(first)  # jumped to the 880 Hz part
    assert np.abs(bed[int(2.8 * sr):]).max() < 0.01  # automation pulled it down to silence


def test_j_cut_sound_starts_before_its_picture(ingested, cfg, tmp_path):
    project, clips, _ = ingested
    a, b = clips["a_stable"].clip_id, clips["f_portrait60"].clip_id  # 440 Hz and 660 Hz tones
    edl = EDL(project_dir=str(project.clips_dir), meta=EditMeta(width=640, height=360, fps=30, fade_in=0, fade_out=0),
              segments=[Segment(id="s1", clip_id=a, src_in=1.0, src_out=3.0, audio=SegmentAudio(mode="mute")),
                        Segment(id="s2", clip_id=b, src_in=2.0, src_out=4.0, audio=SegmentAudio(lead=1.0))])
    out = tmp_path / "jcut.mp4"
    render_edl(edl, project, cfg, quality="draft", out_path=out)
    import subprocess

    from vidosuper.ffmpeg import ffmpeg_bin
    wav = tmp_path / "jcut.wav"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(out), "-ac", "1", "-ar", "48000", str(wav)], check=True)
    audio, _ = sf.read(str(wav))
    window = audio[int(1.3 * SR):int(1.8 * SR)]  # picture still shows shot 1 (muted), but s2's sound leads in
    spectrum = np.abs(np.fft.rfft(window))
    peak_hz = np.argmax(spectrum) * SR / len(window)
    assert abs(peak_hz - 660) < 20 and np.abs(window).max() > 0.01


def test_sound_layers_and_effects(ingested, cfg, tmp_path):
    project, clips, _ = ingested
    a = clips["a_stable"].clip_id
    edl = EDL(project_dir=str(project.clips_dir), meta=EditMeta(width=640, height=360, fps=30, fade_in=0, fade_out=0),
              segments=[Segment(id="s1", clip_id=clips["g_noaudio"].clip_id, src_in=0.5, src_out=3.5,
                                audio=SegmentAudio(mode="mute"))],
              sounds=[SoundLayer(source=f"clip:{a}", at=0.0, duration=1.5, src_in=1.0),
                      SoundLayer(source="sfx:impact", at=2.0, duration=1.0, gain_db=-3)])
    result = render_edl(edl, project, cfg, quality="draft", out_path=tmp_path / "layers.mp4")
    assert result.loudness_measured is not None  # silent picture, but the layers made sound
    for name in SFX_NAMES:
        fx = synthesize(name)
        assert fx.shape[1] == 2 and 0.1 < np.abs(fx).max() <= 0.51
