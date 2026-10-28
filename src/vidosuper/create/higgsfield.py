"""AI-made video clips through Higgsfield's API (https://docs.higgsfield.ai): one key for many models (Kling, Wan,
Seedance...), paid per second from the API's own balance.

- Auth: `Authorization: Key {HF_API_KEY_ID}:{HF_API_KEY_SECRET}` (both from the Higgsfield console; the person adds
  them on the Connect page, which keeps them in the keychain).
- A still to start from is uploaded first (files/generate-upload-url, then a PUT to the presigned URL).
- A generation is a POST to the model's path; it answers with a request id and its status URL, which is polled until
  completed (`video.url`), failed, nsfw or canceled. Outputs are kept a week: they are downloaded at once.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

API = "https://api.higgsfield.ai"
MODELS = {  # the paths, and what a second costs (USD, as published Oct 2026; for the books)
    "kling-3-std": ("/kling-video/v3.0/std/{mode}", 0.042),
    "kling-3-pro": ("/kling-video/v3.0/pro/{mode}", 0.084),
}
DEFAULT = "kling-3-std"


class HiggsfieldError(RuntimeError):
    pass


def credentials() -> tuple[str, str] | None:
    from ..music.compose import _load_dotenv

    _load_dotenv()
    kid, secret = os.environ.get("HF_API_KEY_ID", "").strip(), os.environ.get("HF_API_KEY_SECRET", "").strip()
    if kid and secret:
        return kid, secret
    both = os.environ.get("HIGGSFIELD_API_KEY", "").strip().strip("\"'")  # the console's one-line "id:secret"
    if ":" in both:
        kid, secret = (part.strip() for part in both.split(":", 1))
        if kid and secret:
            return kid, secret
    return None


def available() -> bool:
    return credentials() is not None


def _call(method: str, url: str, body: dict | None = None, opener=urllib.request.urlopen) -> dict:
    creds = credentials()
    if creds is None:
        raise HiggsfieldError("No Higgsfield key: add your key id and secret on the Connect page.")
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Key {creds[0]}:{creds[1]}", "Content-Type": "application/json"})
    try:
        with opener(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:  # its message is the useful part
        raise HiggsfieldError(f"Higgsfield said {e.code}: {e.read().decode(errors='replace')[:300]}") from e


def upload(image: Path, opener=urllib.request.urlopen) -> str:
    """A local still, uploaded for a generation to start from: its public URL."""
    kind = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}[image.suffix.lower()]
    slot = _call("POST", f"{API}/files/generate-upload-url", {"content_type": kind}, opener)
    put = urllib.request.Request(slot["upload_url"], method="PUT", data=image.read_bytes(),
                                 headers=dict(slot.get("upload_headers") or {"Content-Type": kind}))
    with opener(put, timeout=120) as r:
        r.read()
    return slot["public_url"]


def generate(prompt: str, out: Path, *, image: Path | None = None, seconds: int = 5, aspect: str = "9:16",
             model: str = DEFAULT, sound: bool = False, opener=urllib.request.urlopen, sleep=time.sleep,
             timeout: float = 900.0) -> dict:
    """A clip made from words (and a still to start from): saved to `out`. Returns its request id, model, seconds
    and what it cost."""
    path, per_second = MODELS[model]
    seconds = max(3, min(15, int(round(seconds))))
    body: dict = {"prompt": prompt[:2500], "duration": seconds, "sound": "on" if sound else "off"}
    if image is not None:
        body["image_url"] = upload(image, opener)
        mode = "image-to-video"
    else:
        body["aspect_ratio"] = aspect if aspect in ("16:9", "9:16", "1:1") else "9:16"
        mode = "text-to-video"
    sent = _call("POST", API + path.format(mode=mode), body, opener)
    status_url = sent.get("status_url") or f"{API}/requests/{sent['request_id']}/status"
    waited, state = 0.0, sent
    while state.get("status") not in ("completed", "failed", "nsfw", "canceled"):
        if waited > timeout:
            raise HiggsfieldError("Higgsfield took too long to make the clip.")
        sleep(5.0)
        waited += 5.0
        state = _call("GET", status_url, None, opener)
    if state["status"] != "completed":
        raise HiggsfieldError({"nsfw": "Higgsfield wouldn't make that clip (it judged it unsafe).",
                               "canceled": "The clip was canceled."}.get(state["status"],
                                                                         f"Higgsfield couldn't make the clip: {state.get('error', '')}"))
    url = (state.get("video") or {}).get("url")
    if not url:
        raise HiggsfieldError("Higgsfield finished but sent no video.")
    out.parent.mkdir(parents=True, exist_ok=True)
    with opener(urllib.request.Request(url), timeout=300) as r:
        part = out.with_suffix(".part")
        part.write_bytes(r.read())
        part.replace(out)
    return {"request_id": sent.get("request_id"), "model": model, "seconds": seconds,
            "usd": round(per_second * seconds, 3)}
