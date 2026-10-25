"""Where an ONNX model runs: the best GPU this machine has (NVIDIA's CUDA, any Windows GPU through DirectML, a Mac's
Core ML), else the CPU. A GPU that starts but can't run a model (out of memory, its driver) hands it to the CPU, and
that is remembered beside the model with the onnxruntime it failed on, so later runs don't try again (a new
onnxruntime tries again). VIDO_MATTE_DEVICE=cpu keeps every model on the CPU."""

from __future__ import annotations

import os
from pathlib import Path

GPUS = ("CUDAExecutionProvider", "DmlExecutionProvider", "CoreMLExecutionProvider")  # best first


def _marker(model: Path) -> Path:
    return model.with_name(model.name + ".cpu-only")


def _failed(model: Path) -> bool:
    import onnxruntime as ort

    try:
        return _marker(model).read_text(encoding="utf-8").strip() == ort.__version__
    except OSError:
        return False


def _gave_up(model: Path) -> None:
    import onnxruntime as ort

    try:
        _marker(model).write_text(ort.__version__, encoding="utf-8")
    except OSError:
        pass


def providers(model: Path) -> list[str]:
    """The devices to open `model` on, best first."""
    import onnxruntime as ort

    if os.environ.get("VIDO_MATTE_DEVICE", "").lower() == "cpu" or _failed(model):
        return ["CPUExecutionProvider"]
    have = ort.get_available_providers()
    return [p for p in GPUS if p in have][:1] + ["CPUExecutionProvider"]


class Model:
    """An ONNX model on the best device here. `run` hands it to the CPU for good if the GPU can't run it."""

    def __init__(self, path: Path, threads: int | None = None):
        self.path = Path(path)
        self.threads = threads
        self.session = self._open(cpu=False)

    def _open(self, cpu: bool):
        import onnxruntime as ort

        from ..cpus import usable_cpus

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self.threads or usable_cpus()
        chosen = ["CPUExecutionProvider"] if cpu else providers(self.path)
        if chosen[0] == "DmlExecutionProvider":  # DirectML's own rules
            opts.enable_mem_pattern = False
            opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        try:
            return ort.InferenceSession(str(self.path), opts, providers=chosen)
        except Exception:  # noqa: BLE001 - a GPU that won't start: the CPU always works
            if chosen[0] == "CPUExecutionProvider":
                raise
            _gave_up(self.path)
            return self._open(cpu=True)

    @property
    def gpu(self) -> bool:
        return self.session.get_providers()[0] != "CPUExecutionProvider"

    def run(self, x):
        """The model's first output for the input x."""
        name = self.session.get_inputs()[0].name
        try:
            return self.session.run(None, {name: x})[0]
        except Exception:  # noqa: BLE001 - a GPU that starts but can't run it: the CPU can
            if not self.gpu:
                raise
            _gave_up(self.path)
            self.session = self._open(cpu=True)
            return self.session.run(None, {name: x})[0]
