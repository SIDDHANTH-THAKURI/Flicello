"""A person matte per frame, so a title can sit behind the subject. Uses the open u2net human
segmentation model (from the rembg project, run with onnxruntime); the model file lives in
%LOCALAPPDATA%/VidoSuper/models/u2net_human_seg.onnx."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np

MODEL = Path(os.environ.get("VIDO_MODELS_DIR") or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VidoSuper" / "models")     / "u2net_human_seg.onnx"
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


@lru_cache(maxsize=1)
def _model(path: str):
    """The model on the best device here (graphics/device.py)."""
    from ..cpus import usable_cpus
    from .device import Model

    if not Path(path).exists():
        raise FileNotFoundError(f"person matte model missing: {path} (download u2net_human_seg.onnx from the rembg releases)")
    return Model(Path(path), threads=max(1, usable_cpus() // 2))


def person_matte(rgb: np.ndarray, model: Path = MODEL) -> np.ndarray:
    """0..1 float matte (height x width): 1 where the person is."""
    import cv2

    h, w = rgb.shape[:2]
    x = cv2.resize(rgb, (320, 320), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    x = ((x - MEAN) / STD).transpose(2, 0, 1)[None]
    out = _model(str(model)).run(x)[0, 0]
    out = (out - out.min()) / (out.max() - out.min() + 1e-6)
    m = cv2.resize(out, (w, h), interpolation=cv2.INTER_CUBIC)
    return np.clip((m - 0.25) / 0.5, 0.0, 1.0).astype(np.float32)  # firmer edges, faint halo dropped
