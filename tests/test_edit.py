from __future__ import annotations

from vidosuper.edit import build_timeline, choose_fps, validate_edl
from vidosuper.schemas import EDL, ClipInfo, EditMeta, Segment, Transition


def _clip(clip_id: str, duration: float = 10.0, fps: float = 30.0) -> ClipInfo:
    return ClipInfo(clip_id=clip_id, path=f"{clip_id}.mp4", rel_path=f"{clip_id}.mp4", fingerprint=clip_id,
                    duration=duration, width=1920, height=1080, fps=fps, has_audio=True)


def _edl(*segments: Segment, fps: int = 30) -> EDL:
    return EDL(project_dir=".", meta=EditMeta(fps=fps), segments=list(segments))


def test_timeline_frames_and_overlaps():
    clips = {"a": _clip("a"), "b": _clip("b")}
    edl = _edl(
        Segment(id="1", clip_id="a", src_in=0, src_out=2),
        Segment(id="2", clip_id="b", src_in=1, src_out=3, transition_in=Transition(type="dissolve", duration=0.5)),
        Segment(id="3", clip_id="a", src_in=4, src_out=5, speed=0.5),
    )
    tl = build_timeline(edl, clips)
    assert [i.frames for i in tl.items] == [60, 60, 60]
    assert [i.start for i in tl.items] == [0, 45, 105]
    assert tl.items[1].trans_frames == 15
    assert tl.total_frames == 165


def test_transition_is_shortened_when_shots_are_short():
    clips = {"a": _clip("a")}
    edl = _edl(Segment(id="1", clip_id="a", src_in=0, src_out=0.4),
               Segment(id="2", clip_id="a", src_in=5, src_out=7, transition_in=Transition(type="fade", duration=1.0)))
    tl = build_timeline(edl, clips)
    assert tl.items[1].trans_frames == 6 and tl.notes


def test_validation_catches_bad_ranges_and_repairs_tiny_overruns():
    clips = {"a": _clip("a", duration=5.0, fps=30)}
    edl = _edl(Segment(id="1", clip_id="a", src_in=3, src_out=5.03),
               Segment(id="2", clip_id="a", src_in=4, src_out=9),
               Segment(id="3", clip_id="zzz", src_in=0, src_out=1),
               Segment(id="4", clip_id="a", src_in=1, src_out=2, speed=0.25))
    issues = validate_edl(edl, clips)
    errors = {(i.segment, i.level) for i in issues}
    assert edl.segments[0].src_out == 5.0  # clamped, not an error
    assert ("2", "error") in errors and ("3", "error") in errors
    assert ("4", "warning") in errors  # 0.25x from a 30 fps clip is choppy


def test_output_fps_follows_the_footage():
    assert choose_fps([_clip("a", 10, 59.94), _clip("b", 5, 29.97)]) == 30
    assert choose_fps([_clip("a", 10, 50.0)]) == 25
    assert choose_fps([_clip("a", 10, 23.976)]) == 24
    assert choose_fps([]) == 30


def test_cuts_do_not_drift_when_shots_are_fractional_frames():
    """Twenty 0.25 s stutters (7.5 frames each) used to round up one by one and drift 167 ms late."""
    segs = [Segment(id=f"s{k}", clip_id="a", src_in=0.0, src_out=0.25) for k in range(20)]
    segs.append(Segment(id="s20", clip_id="a", src_in=1.0, src_out=2.0, speed=0.5,
                        transition_in=Transition(type="dissolve", duration=0.3)))
    timeline = build_timeline(_edl(*segs), {"a": _clip("a")})
    for k, item in enumerate(timeline.items[:20]):
        assert abs(item.end - round((k + 1) * 0.25 * 30)) <= 1  # each cut within a frame of its exact time
    assert timeline.items[19].end == 150  # 20 x 0.25 s = exactly 5 s
    last = timeline.items[-1]
    assert last.trans_frames == 4 and last.start == 146 and last.end == 206  # dissolve fits the 8-frame stutter
