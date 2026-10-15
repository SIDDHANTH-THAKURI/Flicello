"""Shared fixtures: synthetic footage, an isolated workspace, and one ingest+measure pass."""

from __future__ import annotations

from pathlib import Path

import pytest

from vidosuper.config import load_config
from vidosuper.devtools import make_synthetic_clips
from vidosuper.ingest import ingest_folder
from vidosuper.perceive import measure_project


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("media")
    make_synthetic_clips(root)
    return root


@pytest.fixture(scope="session")
def cfg(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("workspace")
    return load_config({"workspace": str(workspace), "render": {"final": {"preset": "veryfast"}}})


@pytest.fixture(scope="session")
def ingested(media, cfg):
    project, clips = ingest_folder(media / "clips", cfg)
    signals = measure_project(project, cfg)
    by_name = {Path(c.rel_path).stem: c for c in clips}
    return project, by_name, signals


@pytest.fixture(autouse=True)
def offline_animations(tmp_path, monkeypatch):
    """No test reaches LottieFiles or runs the (slow) portrait-matting model: the animation library lives in a temp
    folder and is offline, and the cut-out is off unless a test turns it on with a fake matte."""
    from vidosuper.explainer import cutout, lottie_lib

    monkeypatch.setattr(lottie_lib, "ROOT", tmp_path / "lottie")

    def offline(url, timeout=20.0):
        raise OSError("offline in tests")

    monkeypatch.setattr(lottie_lib, "fetch_url", offline)
    monkeypatch.setattr(cutout, "available", lambda model=None: False)
