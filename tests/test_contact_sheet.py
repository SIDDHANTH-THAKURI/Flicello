from __future__ import annotations

from PIL import Image

from vidosuper.perceive.contact_sheet import format_time, grab_frames, sheets_for_video, uniform_times


def test_uniform_times_cover_the_clip():
    times = uniform_times(10.0, every=2.0, max_frames=100)
    assert len(times) == 6 and times[0] > 0 and times[-1] < 10.0
    assert len(uniform_times(600.0, every=1.0, max_frames=48)) == 48
    assert format_time(75.25) == "1:15.2"


def test_frames_are_taken_at_the_requested_times(ingested):
    _, clips, _ = ingested
    clip = clips["a_stable"]
    frames = grab_frames(clip.proxy, [0.5, 2.0, 4.26])
    assert [round(t * 30) for t, _ in frames] == [15, 60, 128]  # nearest frames at 30 fps


def test_sheets_are_split_and_sized(ingested, tmp_path):
    _, clips, _ = ingested
    clip = clips["f_portrait60"]  # portrait: tiles laid out 6 per row
    paths = sheets_for_video(clip.proxy, clip.duration, tmp_path, "p", every=0.25, per_sheet=12, max_frames=18)
    assert len(paths) == 2
    first = Image.open(paths[0])
    tile_w = round(480 * 360 / 640)
    assert first.width == 6 * tile_w + 5 * 4 and first.height == 2 * 480 + 4
