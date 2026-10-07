"""The learning loop. Every rated result goes into config/ratings.jsonl (with what the critics predicted)
and a dated post-mortem into config/lessons.md, which every agent reads before the next edit
(Reflexion: turn each result into a lesson in words). The critics' bias against the user's ratings is
measured here, so a drifting panel shows up as a number."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .config import REPO_ROOT

RATINGS = REPO_ROOT / "config" / "ratings.jsonl"
LESSONS = REPO_ROOT / "config" / "lessons.md"
POSTMORTEMS = "## 6. Post-mortems (newest first)"


def read_ratings(path: Path = RATINGS) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def log_rating(entry: dict, path: Path = RATINGS) -> dict:
    """Append one rating ({version, user, ...}); the date defaults to today."""
    if "user" not in entry or "version" not in entry:
        raise ValueError("a rating needs at least 'version' and 'user'")
    record = {"date": date.today().isoformat(), **entry}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def critic_bias(ratings: list[dict]) -> tuple[float | None, int]:
    """Mean of (critics' prediction - user's rating) over the ratings that have both, and how many."""
    pairs = [(r["critics"], r["user"]) for r in ratings
             if isinstance(r.get("critics"), (int, float)) and isinstance(r.get("user"), (int, float))]
    if not pairs:
        return None, 0
    return round(sum(c - u for c, u in pairs) / len(pairs), 2), len(pairs)


def add_postmortem(title: str, text: str, path: Path = LESSONS, day: str | None = None) -> None:
    """Put a dated post-mortem at the top of the lessons file's post-mortem list."""
    content = path.read_text(encoding="utf-8")
    if POSTMORTEMS not in content:
        content = content.rstrip("\n") + f"\n\n{POSTMORTEMS}\n"
    head, tail = content.split(POSTMORTEMS, 1)
    line = f"- **{day or date.today().isoformat()} {title}** — {text.strip()}"
    path.write_text(f"{head}{POSTMORTEMS}\n{line}\n{tail.lstrip(chr(10))}", encoding="utf-8")


def summary(ratings: list[dict]) -> str:
    """The rating history, the trend and the critics' bias, as plain lines."""
    if not ratings:
        return "No ratings yet."
    lines = []
    for r in ratings:
        extra = "".join(f", {k} {r[k]:g}" for k in ("music", "intro_outro") if isinstance(r.get(k), (int, float)))
        critics = f" (critics {r['critics']:g})" if isinstance(r.get("critics"), (int, float)) else ""
        lines.append(f"{r.get('date', '?')}  {r['user']:>4g}/10{critics}{extra}  {r['version']}")
    recent = [r["user"] for r in ratings[-3:]]
    lines.append(f"Last {len(recent)}: {sum(recent) / len(recent):.1f} average; best {max(r['user'] for r in ratings):g}.")
    bias, n = critic_bias(ratings)
    if bias is not None:
        lines.append(f"Critics vs the user: {bias:+.1f} on average over {n} rating{'s' if n != 1 else ''}"
                     + (" - they are too generous; score harder." if bias > 0.5 else
                        " - they are too harsh." if bias < -0.5 else " - calibrated."))
    return "\n".join(lines)
