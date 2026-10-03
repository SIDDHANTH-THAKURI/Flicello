from __future__ import annotations

import os
from pathlib import Path

from vidosuper.ffmpeg import ffprobe_json
from vidosuper.ingest import ingest_folder


def _video_stream(path: str) -> dict:
    return next(s for s in ffprobe_json(path)["streams"] if s["codec_type"] == "video")


def test_every_clip_ingests(ingested):
    _, clips, _ = ingested
    assert len(clips) == 9
    for name, clip in clips.items():
        assert clip.usable, f"{name}: {clip.error}"
        assert Path(clip.proxy).exists()
        assert abs(float(ffprobe_json(clip.proxy)["format"]["duration"]) - clip.duration) < 0.2, name


def test_rotation_gives_portrait_display(ingested):
    _, clips, _ = ingested
    rotated = clips["i_rotated"]
    assert rotated.rotation in (90, 270)
    assert (rotated.width, rotated.height) == (720, 1280)
    assert rotated.orientation == "portrait"
    proxy = _video_stream(rotated.proxy)
    assert (proxy["width"], proxy["height"]) == (360, 640)


def test_hdr_is_detected_and_proxy_is_sdr(ingested):
    _, clips, _ = ingested
    hdr = clips["h_hdr"]
    assert hdr.hdr and hdr.bit_depth == 10
    proxy = _video_stream(hdr.proxy)
    assert proxy["pix_fmt"] == "yuv420p"
    assert proxy.get("color_transfer") not in ("arib-std-b67", "smpte2084")


def test_high_frame_rate_portrait(ingested):
    _, clips, _ = ingested
    clip = clips["f_portrait60"]
    assert round(clip.fps) == 60 and clip.orientation == "portrait"
    assert _video_stream(clip.proxy)["avg_frame_rate"] in ("30/1", "30")


def test_clip_without_audio(ingested):
    _, clips, _ = ingested
    clip = clips["g_noaudio"]
    assert not clip.has_audio and clip.audio_wav is None


def test_reingest_uses_cache(media, cfg, ingested):
    _, clips, _ = ingested
    before = {n: os.path.getmtime(c.proxy) for n, c in clips.items()}
    _, again = ingest_folder(media / "clips", cfg)
    after = {Path(c.rel_path).stem: os.path.getmtime(c.proxy) for c in again}
    assert before == after
