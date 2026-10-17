"""Original music for an edit, composed by ElevenLabs Music from a section-by-section plan
(ELEVENLABS_API_KEY in .env). The plan pins where the build and the drop fall, so the track is
written for the cut instead of the cut being bent to a stock track."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from .sources import _load_dotenv

API = "https://api.elevenlabs.io/v1/music"
DEFAULT_MODEL = "music_v2_5"


def to_v2_plan(plan: dict) -> dict:
    """music_v2/v2.5 take 'chunks' (text + exact duration + styles) instead of v1's sections; the global
    styles go into every chunk so the track stays one piece of music."""
    if "chunks" in plan:
        return plan
    pos, neg = plan.get("positive_global_styles", []), plan.get("negative_global_styles", [])
    chunks = []
    for s in plan["sections"]:
        lines = "\n".join(s.get("lines") or [])
        text = f"[{s['section_name']}]" + (f"\n{lines}" if lines else " {instrumental}")
        chunks.append({"text": text, "duration_ms": s["duration_ms"],
                       "positive_styles": [*pos, *s.get("positive_local_styles", [])],
                       "negative_styles": [*neg, *s.get("negative_local_styles", [])]})
    return {"chunks": chunks}


def compose(plan: dict, out_path: Path, *, model: str = DEFAULT_MODEL, output_format: str = "mp3_44100_192",
            seed: int | None = None) -> Path:
    """plan: an ElevenLabs composition plan (positive/negative global styles and timed sections)."""
    _load_dotenv()
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set (put it in .env)")
    body = {"composition_plan": to_v2_plan(plan) if model.startswith("music_v2") else plan, "model_id": model}
    if seed is not None:
        body["seed"] = seed
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for fmt in (output_format, "mp3_44100_128"):
        request = urllib.request.Request(f"{API}?output_format={fmt}", data=json.dumps(body).encode("utf-8"),
                                         headers={"xi-api-key": key, "Content-Type": "application/json"},
                                         method="POST")
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                out_path.write_bytes(response.read())
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            if fmt != "mp3_44100_128" and exc.code in (400, 403, 422) and "format" in detail.lower():
                continue  # this plan can't have that format; take the standard one
            raise RuntimeError(f"ElevenLabs Music said HTTP {exc.code}: {detail}") from exc
    out_path.with_suffix(".plan.json").write_text(json.dumps({"model": model, "plan": plan}, indent=1),
                                                  encoding="utf-8")
    return out_path


class ComposeError(RuntimeError):
    """ElevenLabs could not make the track (no key, refused, down)."""


class BadPlan(ComposeError):
    """The plan was refused (for example a style named a real artist or song); `detail` says what to change."""

    def __init__(self, detail: str):
        super().__init__(f"the music plan was refused: {detail[:300]}")
        self.detail = detail


def available() -> bool:
    """A key for ElevenLabs Music is set on this machine."""
    _load_dotenv()
    return bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())


def compose_plan(plan: dict, out_path: Path, *, model: str = "music_v2_5", store: bool = False,
                 seed: int | None = None, timeout: float = 600.0) -> dict:
    """Compose a music_v2 plan (chunks) into `out_path` (mp3). `store`: keep it at ElevenLabs as a reference for a
    later track (a taste the full track should sound like). Returns {"path", "song_id", "seconds"}. Busy or down:
    tried three times; a refused plan raises BadPlan, anything else ComposeError."""
    import time

    import httpx

    _load_dotenv()
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise ComposeError("ELEVENLABS_API_KEY is not set on this machine")
    body: dict = {"composition_plan": plan, "model_id": model}
    if store:
        body["store_for_inpainting"] = True
    if seed is not None:
        body["seed"] = int(seed)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = "mp3_44100_192"
    last = ""
    for attempt in range(3):
        try:
            r = httpx.post(f"{API}?output_format={fmt}", json=body, timeout=httpx.Timeout(30.0, read=timeout),
                           headers={"xi-api-key": key, "Content-Type": "application/json"})
        except httpx.HTTPError as exc:  # never reached them, or the line dropped: try again
            last = f"{type(exc).__name__}"
            time.sleep(5.0 * (attempt + 1))
            continue
        if r.status_code == 200:
            part = out_path.with_name(out_path.name + ".part")
            part.write_bytes(r.content)
            os.replace(part, out_path)
            seconds = sum(c.get("duration_ms", 0) for c in plan.get("chunks", [])) / 1000.0
            try:
                import soundfile as sf

                seconds = float(sf.info(str(out_path)).duration)
            except Exception:  # noqa: BLE001 - the plan's length stands
                pass
            return {"path": out_path, "song_id": r.headers.get("song-id", ""), "seconds": round(seconds, 2)}
        detail = r.text[:600]
        if r.status_code in (400, 403, 422) and "format" in detail.lower() and fmt != "mp3_44100_128":
            fmt = "mp3_44100_128"  # this plan can't have that format: the standard one
            continue
        if "bad_composition_plan" in detail or (r.status_code == 422 and "composition" in detail.lower()):
            raise BadPlan(detail)
        if r.status_code in (401, 403):
            raise ComposeError(f"ElevenLabs refused the key (HTTP {r.status_code}): {detail[:200]}")
        last = f"HTTP {r.status_code}: {detail[:200]}"
        if r.status_code in (408, 409, 429) or r.status_code >= 500:
            time.sleep(5.0 * (attempt + 1))
            continue
        break
    raise ComposeError(f"ElevenLabs Music didn't make the track ({last})")
