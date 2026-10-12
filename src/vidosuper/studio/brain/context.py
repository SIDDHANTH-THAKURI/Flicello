"""What the brain is told: the craft rules, a person's style, the footage catalog and the song as a beat map."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

from ...config import REPO_ROOT
from ...music.analysis import MusicAnalysis
from ...perceive.catalog import render_markdown
from ...template import audible_end, main_drop

PROMPTS = Path(__file__).parent / "prompts"
CRAFT = REPO_ROOT / "config" / "studio" / "craft.md"


@lru_cache(maxsize=None)
def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def craft_rules() -> str:
    return CRAFT.read_text(encoding="utf-8") if CRAFT.exists() else ""


def rules_text(rules: list[dict]) -> str:
    if not rules:
        return "(none yet: this person is new; follow the craft rules and their request)"
    return "\n".join(f"- [{r['id']}] {r['text']}" for r in rules)


REUSE_WORDS = re.compile(r"\b(repeat\w*|re-?use\w*|again|loop\w*|same shots?)\b", re.IGNORECASE)


@dataclass
class BeatMap:
    """The song from the edit's point of view: beat 0 is the first downbeat, where the video starts."""

    first: int  # index of beat 0 in the song's beat list
    t0: float  # song seconds of beat 0
    period: float  # seconds per beat
    available: int  # beats from beat 0 to where the song dies away
    target: int  # beats the plan must add up to (a whole number of bars)
    drop: int | None  # beat of the main drop inside the target, if any
    sections: list[tuple[str, int, int, float]] = field(default_factory=list)  # label, from beat, to beat, energy

    @property
    def seconds(self) -> float:
        return self.target * self.period

    def text(self, song: MusicAnalysis) -> str:
        bpm = 60.0 / self.period
        lines = [f"Song: {song.duration:.0f} s, {bpm:.0f} BPM; one beat = {self.period:.3f} s, a bar = 4 beats.",
                 f"The video starts on beat 0 (song time {self.t0:.2f} s). Target: {self.target} beats "
                 f"({self.seconds:.1f} s, {self.target // 4} bars) - the plan's beats must add up to exactly "
                 f"{self.target}.",
                 "Sections, in beats from the start: " + "; ".join(
                     f"{label} {a}-{b} (energy {e:.2f})" for label, a, b, e in self.sections if a < self.target)]
        if self.drop is not None:
            lines.append(f"Drop: beat {self.drop} (song time {self.t0 + self.drop * self.period:.1f} s). The shot that "
                         f"starts on beat {self.drop} is the hit.")
        else:
            lines.append("No drop inside the target: build towards the strongest moment near the end instead.")
        weak = [(a, b) for a, b in song.weak_beat_stretches() if b > self.t0 and a < self.t0 + self.seconds]
        if weak:
            lines.append("No clear beat in: " + ", ".join(
                f"beats {max(0, round((a - self.t0) / self.period))}-{round((b - self.t0) / self.period)}"
                for a, b in weak) + " (prefer holds and dissolves there).")
        return "\n".join(lines)


def beat_map(song: MusicAnalysis, target_seconds: float) -> BeatMap:
    beats = np.asarray(song.beats, dtype=float)
    period = float(np.median(np.diff(beats)))
    first = int(np.argmin(np.abs(beats - song.downbeats[0]))) if song.downbeats else 0
    t0 = float(beats[first])
    end = audible_end(song)
    available = int((end - t0) / period)
    target = int(round(target_seconds / period / 4)) * 4
    target = max(8, min(target, available - available % 4))

    def at_beat(t: float) -> int:
        return int(np.argmin(np.abs(beats - t))) - first

    drop = main_drop(song, t0 + 4 * period, t0 + (target - 4) * period)
    drop_beat = at_beat(drop) if drop is not None else None
    if drop_beat is not None:
        drop_beat = int(round(drop_beat / 4)) * 4  # drops are bar starts; keep the plan on the bar grid
        if not 4 <= drop_beat <= target - 4:
            drop_beat = None
    sections = []
    for s in song.sections:
        a, b = max(0, at_beat(s.start)), max(0, at_beat(s.end))
        if b > a:
            sections.append((s.label, a, b, s.energy))
    return BeatMap(first, t0, period, available, target, drop_beat, sections)


def catalog_text(catalog: dict) -> str:
    """The token-cheap catalog the agents read, without the server's folder path."""
    lines = render_markdown(catalog).splitlines()
    keep = [line for line in lines if not line.startswith("Clips folder:")]
    return "\n".join(keep)


def load_catalog(root: Path) -> dict:
    return json.loads((root / "catalog.json").read_text(encoding="utf-8"))


@dataclass
class ClipFacts:
    short: str
    clip_id: str
    duration: float
    fps: float
    vertical: bool
    avoid: list[tuple[float, float]]  # what the analyst marked unusable (the automatic problem ranges are only
    # hints: the tool already picks the steadiest window inside each moment)
    moments: list[dict]  # sorted by start; moment n is "<short>#<n+1>"
    impacts: list[float] = field(default_factory=list)  # real hit sounds, seconds into the clip


def clip_facts(catalog: dict) -> dict[str, ClipFacts]:
    out = {}
    for e in catalog["clips"]:
        a = e.get("analysis")
        if not a:
            continue
        avoid = [(u["start"], u["end"]) for u in a.get("unusable") or []]
        clip = e["clip"]
        out[e["short"]] = ClipFacts(e["short"], clip["clip_id"], float(clip["duration"]), float(clip["fps"]),
                                    clip["height"] > clip["width"], avoid,
                                    sorted(a.get("moments") or [], key=lambda m: m["start"]),
                                    [float(t) for t, _ in (e.get("signals") or {}).get("impacts", [])])
    return out


def moment_by_id(facts: dict[str, ClipFacts], mid: str) -> dict | None:
    short, _, n = mid.partition("#")
    f = facts.get(short.strip())
    if f is None or not n.strip().isdigit():
        return None
    k = int(n) - 1
    return f.moments[k] if 0 <= k < len(f.moments) else None
