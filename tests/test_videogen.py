"""The video model on this machine (motion/videogen.py): its frame counts and sizes follow the model's rules, its
process animates on 127.0.0.1 and lets the GPU go when asked, and AniSora's fp32 experts are kept in bf16."""

from __future__ import annotations

import socket
import threading

import pytest

from vidosuper.motion import videogen


def test_frames_follow_the_models_rule_and_sizes_its_step():
    assert videogen.frames_for(5.0) == 81 and videogen.frames_for(2.25) == 37  # 4k+1 at 16 fps
    assert videogen.frames_for(0.3) == 17 and videogen.frames_for(30) == 121  # at least 17, at most 121
    assert all((videogen.frames_for(s) - 1) % 4 == 0 for s in (0.9, 1.7, 3.3, 6.1))
    w, h = videogen.size_for(9 / 16)
    assert w % 16 == 0 and h % 16 == 0 and h > w and abs(w * h - videogen.AREA) < videogen.AREA * 0.05


class FakeModel:
    asked: list = []

    def __init__(self):
        pass

    def animate(self, body, out):
        FakeModel.asked.append(body)
        out.write_bytes(b"mp4")
        return {"frames": 81, "width": 720, "height": 1280}


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_process_animates_a_frame_and_lets_the_gpu_go(tmp_path, monkeypatch):
    port = _port()
    monkeypatch.setattr(videogen, "PORT", port)
    monkeypatch.setattr(videogen, "_Model", FakeModel)
    monkeypatch.setattr(videogen, "_start", lambda: pytest.fail("already running"))
    threading.Thread(target=videogen.serve, args=(port,), daemon=True).start()
    videogen.ready(wait=10)
    start = tmp_path / "start.png"
    start.write_bytes(b"png")
    out = videogen.animate(start, "the dog wakes and lifts its head", tmp_path / "take1.mp4", seconds=3.0,
                           look="anime", last_image=start, seed=7)
    assert out["frames"] == 81 and (tmp_path / "take1.mp4").exists()
    body = FakeModel.asked[-1]
    assert body["look"] == "anime" and body["seed"] == 7 and body["last_image"] == str(start.resolve())
    videogen.release()  # the painter needs the GPU: the process ends
    import time

    for _ in range(50):
        try:
            videogen._ask(timeout=1)
        except OSError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the video process didn't end when asked")


def test_starting_the_video_model_asks_the_painter_to_let_the_gpu_go(monkeypatch):
    seen = []
    monkeypatch.setattr(videogen, "_release_painter", lambda: seen.append("painter"))
    monkeypatch.setattr(videogen.subprocess, "Popen", lambda *a, **k: seen.append("started"))
    videogen._start()
    assert seen == ["painter", "started"]


def test_anisoras_fp32_experts_are_kept_in_bf16_and_the_fp32_copy_dropped(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    from safetensors.torch import load_file, save_file

    monkeypatch.setenv("VIDO_MODELS_DIR", str(tmp_path / "models"))
    blobs = tmp_path / "hub"
    blobs.mkdir()

    def fake_download(repo, name):
        src = blobs / name.replace("/", "_")
        save_file({"blocks.0.weight": torch.ones(4, 4, dtype=torch.float32)}, str(src))
        return str(src)

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *a, **k: str(tmp_path))
    videogen.prepare("anime", log=lambda m: None)
    for which in ("high", "low"):
        kept = load_file(str(videogen._anime_file(which)))
        assert kept["blocks.0.weight"].dtype == torch.bfloat16
    assert not list(blobs.iterdir())  # the 57 GB fp32 copies are gone
    assert videogen.downloaded("anime")
