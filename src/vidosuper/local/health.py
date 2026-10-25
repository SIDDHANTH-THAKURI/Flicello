"""What went wrong with a service while Flicello was working, remembered on this machine (health.json in its
home): a key refused, credit out, a limit hit, a service down. The Connect page shows it and Cello says it plainly,
with what to do; a later success clears it."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

_lock = threading.Lock()
HOME: list[Path] = []  # set by the app at start (the studio's home)

# what an error says -> (what it means, what to do)
SIGNS = [
    (r"credit balance is too low|prepayment credits are depleted|insufficient[_ ]credit|quota_exceeded|"
     r"exceeded your current quota|insufficient_quota|payment required|402",
     "credit is out", "top up your credit with this service"),
    (r"invalid[_ ]api[_ ]key|incorrect api key|api key not valid|unauthori[sz]ed|401|permission denied|403",
     "the key was refused", "check the key on the Connect page (it may be wrong or revoked)"),
    (r"rate[_ ]limit|too many requests|429|resource[_ ]exhausted", "it is rate limited",
     "wait a minute; it carries on by itself"),
    (r"session limit|usage limit|hit your limit", "your Claude plan's limit is reached",
     "wait for it to reset (Claude Code shows when), or switch to the Anthropic API on the Connect page"),
    (r"overloaded|unavailable|503|502|timed? ?out|timeout", "the service is down or slow right now",
     "it is tried again; nothing to do"),
]


def _path() -> Path | None:
    return HOME[0] / "health.json" if HOME else None


def read() -> dict:
    path = _path()
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _write(data: dict) -> None:
    path = _path()
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def explain(error: str) -> tuple[str, str]:
    for pattern, means, todo in SIGNS:
        if re.search(pattern, error or "", re.I):
            return means, todo
    return "it failed", "see the detail; it is tried again next time"


def failed(service: str, error: object) -> None:
    """Note that a service failed while working (gemini, elevenlabs, anthropic, claude_code, higgsfield...). Only the
    service's own problems count (credit, key, limits, outages): a bug of ours is not the service failing."""
    text = " ".join(str(error).split())[:400]
    if not any(re.search(pattern, text, re.I) for pattern, _, _ in SIGNS):
        return
    means, todo = explain(text)
    with _lock:
        data = read()
        data[service] = {"ok": False, "at": time.time(), "means": means, "todo": todo, "detail": text}
        _write(data)


def worked(service: str) -> None:
    """A service just worked: whatever was wrong with it is over."""
    with _lock:
        data = read()
        if data.get(service, {}).get("ok") is False:
            data[service] = {"ok": True, "at": time.time()}
            _write(data)


class watching:
    """`with watching("anthropic"): ...`: a failure of the service inside is noted (and raised on); a success clears
    what was wrong with it."""

    def __init__(self, service: str):
        self.service = service

    def __enter__(self):
        return self

    def __exit__(self, kind, error, tb):
        if error is None:
            worked(self.service)
        elif isinstance(error, Exception):
            failed(self.service, error)
        return False


def problems(since: float = 0.0) -> list[dict]:
    """The services failing now (newer than `since`), for the Connect page and Cello."""
    return [{"service": k, **v} for k, v in read().items() if v.get("ok") is False and v.get("at", 0) > since]
