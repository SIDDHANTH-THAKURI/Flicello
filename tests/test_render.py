from __future__ import annotations

import subprocess

import numpy as np
import pyloudnorm
import soundfile as sf

from vidosuper.edit import baseline_edl, build_timeline
from vidosuper.ffmpeg import ffmpeg_bin, ffprobe_json
from vidosuper.ingest.chapters import chronological
from vidosuper.render import render_edl
from vidosuper.schemas import EDL, EditMeta, MusicSpec, Segment, SegmentAudio, Transition


def _streams(path: str) -> tuple[dict, dict | None]:
    info = ffprobe_json(path)["streams"]
    video = next(s for s in info if s["codec_type"] == "video")
    audio = next((s for s in info if s["codec_type"] == "audio"), None)
    return video, audio


def _loudness(path: str, tmp_path) -> float:
    wav = tmp_path / "check.wav"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", path, "-vn", "-ac", "2", "-ar", "48000", str(wav)],
                   check=True)
    data, sr = sf.read(str(wav), always_2d=True)
    return pyloudnorm.Meter(sr).integrated_loudness(data)


def test_baseline_draft_render(ingested, cfg, media, tmp_path):
    project, clips, signals = ingested
    ordered = chronological(list(clips.values()))
    meta = EditMeta(width=1920, height=1080, fps=30)
    edl = baseline_edl(ordered, signals, project_dir=str(project.clips_dir), meta=meta, target_seconds=15,
                       shot_seconds=2.0, music=MusicSpec(path=str(media / "music.wav"), gain_db=-6))
    assert len(edl.segments) >= 5
    assert "b_shaky" not in {seg.clip_id.split("-")[0] for seg in edl.segments}  # too shaky to pass min score
    result = render_edl(edl, project, cfg, quality="draft", out_path=tmp_path / "baseline.mp4")
    video, audio = _streams(result.output)
    total = build_timeline(edl, project.clips()).total_frames
    assert (video["width"], video["height"]) == (960, 540)
    assert int(video["nb_frames"]) == total
    assert audio is not None and abs(result.duration - total / 30) < 0.1
    assert abs(_loudness(result.output, tmp_path) + 14) < 1.0

    again = render_edl(edl, project, cfg, quality="draft", out_path=tmp_path / "baseline2.mp4")
    assert again.shots_rendered == 0  # every shot came from the cache


def test_transitions_framing_speed_and_hdr_final(ingested, cfg, tmp_path):
    project, clips, _ = ingested
    cid = {name: clip.clip_id for name, clip in clips.items()}
    edl = EDL(
        project_dir=str(project.clips_dir),
        meta=EditMeta(width=640, height=360, fps=30, fade_in=0.3, fade_out=0.5),
        segments=[
            Segment(id="s1", clip_id=cid["a_stable"], src_in=1, src_out=3),
            Segment(id="s2", clip_id=cid["f_portrait60"], src_in=1, src_out=2, speed=0.5,
                    transition_in=Transition(type="dissolve", duration=0.5)),
            Segment(id="s3", clip_id=cid["i_rotated"], src_in=0.5, src_out=2.5,
                    transition_in=Transition(type="fadeblack", duration=0.4)),
            Segment(id="s4", clip_id=cid["h_hdr"], src_in=0, src_out=2, audio=SegmentAudio(mode="mute")),
            Segment(id="s5", clip_id=cid["g_noaudio"], src_in=0, src_out=1.5, framing="fit"),
        ],
    )
    result = render_edl(edl, project, cfg, quality="final", out_path=tmp_path / "final.mp4")
    total = build_timeline(edl, project.clips()).total_frames
    assert total == 60 + 60 + 60 + 60 + 45 - 15 - 12
    video, audio = _streams(result.output)
    assert (video["width"], video["height"]) == (640, 360)
    assert int(video["nb_frames"]) == total and audio is not None
    assert not [w for w in result.warnings if "choppy" in w]  # 0.5x from 60 fps is real slow motion

    # the HDR shot must not come out washed out: grab a frame from its middle and check contrast
    frame = tmp_path / "hdr.png"
    hdr_mid = (build_timeline(edl, project.clips()).items[3].start + 30) / 30
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-ss", f"{hdr_mid:.3f}", "-i", result.output,
                    "-frames:v", "1", str(frame)], check=True)
    import cv2

    gray = cv2.cvtColor(cv2.imread(str(frame)), cv2.COLOR_BGR2GRAY)
    assert gray.std() > 40 and np.percentile(gray, 5) < 60


def test_punch_in_shows_the_requested_part_of_the_frame(ingested, cfg, tmp_path):
    import cv2

    project, clips, _ = ingested
    clip = clips["a_stable"]
    edl = EDL(project_dir=str(project.clips_dir), meta=EditMeta(width=640, height=360, fps=30, fade_in=0, fade_out=0),
              segments=[Segment(id="z", clip_id=clip.clip_id, src_in=1.0, src_out=2.0, zoom=2.0,
                                zoom_center=(0.25, 0.25))])
    result = render_edl(edl, project, cfg, quality="final", out_path=tmp_path / "zoom.mp4")
    out = cv2.VideoCapture(result.output)
    ok, zoomed = out.read()
    src = cv2.VideoCapture(clip.path)
    src.set(cv2.CAP_PROP_POS_MSEC, 1000)
    ok2, full = src.read()
    assert ok and ok2
    h, w = full.shape[:2]
    quarter = cv2.resize(full[: h // 2, : w // 2], (640, 360), interpolation=cv2.INTER_AREA)
    whole = cv2.resize(full, (640, 360), interpolation=cv2.INTER_AREA)
    err_quarter = float(np.mean((zoomed.astype(float) - quarter) ** 2))
    err_whole = float(np.mean((zoomed.astype(float) - whole) ** 2))
    assert err_quarter < err_whole / 4, (err_quarter, err_whole)
