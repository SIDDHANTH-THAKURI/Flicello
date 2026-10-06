"""Speed ramps, push-ins and hits (flash / shake / pulse): exact timing and a clean render."""

from __future__ import annotations

import numpy as np
from PIL import Image

from vidosuper.edit import build_timeline
from vidosuper.edit.speed import out_duration, out_time, setpts_expr, source_time
from vidosuper.edit.validate import validate_edl
from vidosuper.ffmpeg import ffprobe_json, run_ffmpeg
from vidosuper.render import render_edl
from vidosuper.schemas import EDL, EditMeta, Fx, RampPoint, Segment, Transition


def test_ramp_time_math_matches_integration():
    seg = Segment(id="s", clip_id="c", src_in=10, src_out=13,
                  ramp=[RampPoint(at=0, speed=2), RampPoint(at=1.0, speed=0.3), RampPoint(at=2.5, speed=1)])
    u = np.linspace(0, 3, 300001)
    numeric = np.trapezoid(1 / np.interp(u, [0, 1, 2.5], [2, 0.3, 1]), u)
    assert abs(out_duration(seg) - numeric) < 1e-3
    assert abs(source_time(seg, out_time(seg, 1.7)) - 1.7) < 1e-6
    assert "log(" in setpts_expr(seg) and setpts_expr(Segment(id="s", clip_id="c", src_in=0, src_out=1)) \
        == "setpts=(PTS-STARTPTS)/1.000000"


def test_ramped_shot_with_hits_renders_frame_exact(ingested, cfg, tmp_path):
    project, clips, signals = ingested
    pan, still = clips["c_pan"].clip_id, clips["a_stable"].clip_id
    ramp = [RampPoint(at=0.0, speed=2.0), RampPoint(at=1.6, speed=0.6)]
    edl = EDL(project_dir=str(project.clips_dir), meta=EditMeta(width=1920, height=1080, fps=30), segments=[
        Segment(id="s1", clip_id=pan, src_in=0.5, src_out=3.0, ramp=ramp, push=0.15, audio={"mode": "mute"},
                fx=[Fx(type="pulse", at=0.4), Fx(type="shake", at=1.0, duration=0.4)]),
        Segment(id="s2", clip_id=still, src_in=1.0, src_out=3.0, transition_in=Transition(type="zoomin", duration=0.3),
                fx=[Fx(type="flash", at=0.6, duration=0.3)]),
    ])
    assert not [i for i in validate_edl(edl, project.clips(), signals) if i.level == "error"]
    timeline = build_timeline(edl, project.clips())
    assert timeline.items[0].moving_frames == round(out_duration(edl.segments[0]) * 30)
    result = render_edl(edl, project, cfg, quality="draft", out_path=tmp_path / "motion.mp4")
    video = next(s for s in ffprobe_json(result.output)["streams"] if s["codec_type"] == "video")
    assert int(video["nb_frames"]) == timeline.total_frames

    def brightness(t: float) -> float:
        frame = tmp_path / f"f{t}.png"
        run_ffmpeg(["-ss", f"{t:.3f}", "-i", result.output, "-frames:v", "1", str(frame)])
        return float(np.asarray(Image.open(frame).convert("L"), dtype=np.float32).mean())

    s2 = timeline.items[1].start / 30
    assert brightness(s2 + 0.62) > brightness(s2 + 1.5) + 25  # the flash lifts the frame, then it's gone
