"""A person's style profile: a short list of preferences the brain reads before every video they make.

It starts from a few answers (or, for the owner, from their known rules), grows from their feedback (the
coach step) and is theirs to edit: every rule can be removed on the "My style" page. It is personal; the
shared craft rules live in config/studio/craft.md and only the owner changes those.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from ..config import REPO_ROOT
from .brain.schemas import ProfileUpdate

SEED = REPO_ROOT / "config" / "studio" / "owner_rules.yaml"
MAX_RULES = 24

QUESTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("pace", "How should your videos move?", [("calm", "Calm and cinematic"), ("balanced", "A mix of both"),
                                              ("fast", "Fast and punchy")]),
    ("sound", "The clips' own sound (waves, laughter, a crowd)?", [("real", "Keep it where it adds"),
                                                                    ("music", "Music only")]),
    ("look", "Colour?", [("vivid", "Vivid and bright"), ("natural", "Natural, as shot"), ("warm", "Warm"),
                         ("film", "Filmic")]),
]

ANSWER_RULES = {
    ("pace", "calm"): "Calm, cinematic pacing: shots of 2-3 s or longer; fast cuts only at the drop.",
    ("pace", "fast"): "Fast and punchy: short shots and quick builds; hold only to let a moment breathe.",
    ("sound", "real"): "Use the clips' own sounds (waves, laughter, the crowd) where they carry a moment.",
    ("sound", "music"): "Music only: no clip sound.",
    ("look", "natural"): "Keep the colour natural, as it was shot.",
    ("look", "warm"): "Warm colour.",
    ("look", "film"): "A filmic look.",
}


def owner_seed_rules() -> list[dict]:
    items = yaml.safe_load(SEED.read_text(encoding="utf-8")) if SEED.exists() else []
    today = date.today().isoformat()
    return [{"id": f"o{i + 1}", "text": it["text"], "why": it.get("why", ""), "source": "owner", "at": today}
            for i, it in enumerate(items or [])]


@dataclass
class Profile:
    path: Path
    rules: list[dict] = field(default_factory=list)
    answers: dict = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Profile":
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(path, data.get("rules", []), data.get("answers", {}), data.get("history", []))
        return cls(path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"rules": self.rules, "answers": self.answers, "history": self.history[-100:]},
                                  indent=1), encoding="utf-8")
        tmp.replace(self.path)

    @property
    def look(self) -> str:
        return self.answers.get("look") or "vivid"

    def add(self, text: str, why: str = "", source: str = "feedback") -> str | None:
        text = " ".join(str(text).split())[:240]
        if not text or any(r["text"].lower() == text.lower() for r in self.rules):
            return None
        rid = f"r{secrets.token_hex(3)}"
        self.rules.append({"id": rid, "text": text, "why": " ".join(str(why).split())[:240], "source": source,
                           "at": date.today().isoformat()})
        if len(self.rules) > MAX_RULES:  # keep the profile short: the oldest learned rules go first
            learned = [r for r in self.rules if r["source"] == "feedback"]
            if learned:
                self.rules.remove(learned[0])
        return rid

    def drop(self, rid: str) -> bool:
        before = len(self.rules)
        self.rules = [r for r in self.rules if r["id"] != rid]
        return len(self.rules) < before

    def set_answers(self, answers: dict[str, str], never: str = "") -> None:
        """Answers to the starting questions become rules (replacing earlier answer rules)."""
        valid = {q: {v for v, _ in opts} for q, _, opts in QUESTIONS}
        clean = {k: v for k, v in answers.items() if k in valid and v in valid[k]}
        self.rules = [r for r in self.rules if r["source"] != "answer"]
        self.answers = clean
        for (q, v), text in ANSWER_RULES.items():
            if clean.get(q) == v:
                self.add(text, "your answer", "answer")
        never = " ".join(never.split())[:200]
        if never:
            self.answers["never"] = never
            self.add(f"Never show: {never}.", "your answer", "answer")

    def apply(self, update: ProfileUpdate) -> dict:
        added = [rid for rule in update.add[:3] if (rid := self.add(rule.text, rule.why))]
        dropped = [rid for rid in update.drop if self.drop(rid)]
        entry = {"at": date.today().isoformat(), "summary": update.summary, "added": added, "dropped": dropped}
        self.history.append(entry)
        return entry
