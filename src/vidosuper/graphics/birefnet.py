"""BiRefNet (lite, MIT): a clean cut-out of the person, hair included, for scenes that put things behind them or replace
their background. On a GPU it runs on every frame; on a CPU (several seconds a frame) a scene's matte runs it on every
few frames and follows the picture's motion between them (motion/scenes.py)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np

MODEL = Path(os.environ.get("VIDO_MODELS_DIR") or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VidoSuper"
             / "models") / "birefnet_lite.onnx"
SIDE = 1024
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def available() -> bool:
    return MODEL.exists()


@lru_cache(maxsize=1)
def _model(path: str):
    from .device import Model

    return Model(Path(path))


def on_gpu(model: Path = MODEL) -> bool:
    """Whether the cut-out runs on a GPU here (it has run a frame there: that is where a GPU that can't run it
    fails)."""
    if not Path(model).exists():
        return False
    person_matte(np.zeros((64, 64, 3), np.uint8), model)
    return _model(str(model)).gpu


def person_matte(rgb: np.ndarray, model: Path = MODEL) -> np.ndarray:
    """0..1 matte of the person in an RGB frame (uint8), at the frame's size."""
    import cv2

    h, w = rgb.shape[:2]
    x = cv2.resize(rgb.astype(np.float32) / 255.0, (SIDE, SIDE), interpolation=cv2.INTER_AREA)
    x = ((x - MEAN) / STD).transpose(2, 0, 1)[None].astype(np.float32)
    y = _model(str(model)).run(x)[0, 0]
    m = 1.0 / (1.0 + np.exp(-np.clip(y, -40, 40)))
    return cv2.resize(m.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
