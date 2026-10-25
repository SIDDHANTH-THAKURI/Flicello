"""The computer Flicello runs on, against what it is built for.

Flicello is made for high-end machines: designed scenes, clean cut-outs, painted assets and long renders all run here.
It never lowers its quality for a weaker one; it says so instead, at the start, so nobody is surprised."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

# what Flicello is built for (below it still runs, with a plain warning)
RAM_GB = 32
VRAM_GB = 12
DISK_GB = 100
CORES = 8


def _run(cmd: list[str], timeout: float = 10.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def gpus() -> list[dict]:
    """The graphics chips: name, memory (GB, when known) and kind (nvidia / apple / other)."""
    found = []
    smi = shutil.which("nvidia-smi")
    if smi:
        for line in _run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]).splitlines():
            name, _, mem = line.partition(",")
            try:
                found.append({"name": name.strip(), "vram_gb": round(float(mem) / 1024, 1), "kind": "nvidia"})
            except ValueError:
                found.append({"name": name.strip(), "vram_gb": None, "kind": "nvidia"})
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Apple Silicon"
        found.append({"name": chip, "vram_gb": None, "kind": "apple"})  # memory is shared with the system
    if not found and platform.system() == "Windows":
        names = _run(["powershell", "-NoProfile", "-Command",
                      "(Get-CimInstance Win32_VideoController).Name"])
        found += [{"name": n.strip(), "vram_gb": None, "kind": "other"} for n in names.splitlines() if n.strip()]
    return found


def machine(home: Path | None = None) -> dict:
    """This computer: memory, cores, graphics, free disk, and whether it is what Flicello is built for (with the
    reasons when it isn't)."""
    import psutil

    ram = round(psutil.virtual_memory().total / 1024 ** 3, 1)
    free_ram = round(psutil.virtual_memory().available / 1024 ** 3, 1)
    cores = os.cpu_count() or 0
    where = Path(home or Path.home())
    while not where.exists() and where.parent != where:
        where = where.parent
    disk = round(shutil.disk_usage(where).free / 1024 ** 3, 1)
    chips = gpus()
    nvidia = [g for g in chips if g["kind"] == "nvidia"]
    apple = [g for g in chips if g["kind"] == "apple"]
    short = []
    if ram < RAM_GB - 1:
        short.append(f"{ram:g} GB of memory (built for {RAM_GB} GB or more)")
    if nvidia:
        best = max((g["vram_gb"] or 0) for g in nvidia)
        if best < VRAM_GB:
            short.append(f"an NVIDIA GPU with {best:g} GB (built for {VRAM_GB} GB or more)")
    elif apple:
        if ram < RAM_GB - 1:
            pass  # already said: Apple's graphics share the system's memory
    else:
        short.append("no NVIDIA or Apple Silicon graphics (cut-outs, depth and upscaling run on the CPU, slowly)")
    if disk < DISK_GB:
        short.append(f"{disk:g} GB of free disk (built for {DISK_GB} GB or more)")
    if cores < CORES:
        short.append(f"{cores} CPU threads (built for {CORES} or more)")
    return {"system": f"{platform.system()} {platform.release()}", "cpu": platform.processor() or platform.machine(),
            "cores": cores, "ram_gb": ram, "free_ram_gb": free_ram, "disk_free_gb": disk, "gpus": chips,
            "ready": not short, "short": short,
            "built_for": f"{RAM_GB} GB memory, an NVIDIA GPU with {VRAM_GB} GB+ (or Apple Silicon M2 Pro or better), "
                         f"{DISK_GB} GB free disk, {CORES}+ CPU threads"}
