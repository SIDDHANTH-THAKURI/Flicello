"""A person's style profile: a short list of preferences the brain reads before every video they make.

It starts from a few answers (or, for the owner, from their known rules), grows from their feedback (the
coach step) and is theirs to edit: every rule can be removed on the "My style" page. It is personal; the
shared craft rules live in config/studio/craft.md and only the owner changes those.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from ..config import REPO_ROOT
from .brain.schemas import ProfileUpdate

SEED = REPO_ROOT / "config" / "studio" / "owner_rules.yaml"
MAX_RULES = 24
MAX_TASTE = 40  # look choices remembered (picked, passed over, changes asked, how the video was rated)
SCOPES = ("all", "films", "talks")  # a rule learned on a travel film ("no on-screen text") must not shape an explainer
SCOPE_LABEL = {"films": "films from footage", "talks": "voice and talking videos"}


def scope_of(kind: str) -> str:
    """Which rules a video of this kind follows: footage films, or voice and talking videos."""
    return "talks" if kind in ("voice", "talk") else "films"


def rules_for(rules: list[dict], kind: str) -> list[dict]:
    """The rules that apply to a video of this kind (rules without a scope apply to all)."""
    scope = scope_of(kind)
    return [r for r in rules if r.get("for", "all") in ("all", scope)]

QUESTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("pace", "How should your videos move?", [("calm", "Calm and cinematic"), ("balanced", "A mix of both"),
                                              ("fast", "Fast and punchy")]),
    ("sound", "The clips' own sound (waves, laughter, a crowd)?", [("real", "Keep it where it adds"),
                                                                    ("music", "Music only")]),
    ("look", "Colour?", [("vivid", "Vivid and bright"), ("natural", "Natural, as shot"), ("warm", "Warm"),
                         ("film", "Filmic")]),
]

# their own words for the colour, to the nearest grade the renderer has (first match wins; none: the default)
OWN_LOOKS = [
    (r"black\s*(?:and|&|n)\s*white|\bb\s*&\s*w\b|\bmono(?:chrome)?\b|gr[ae]y\s*scale|no colou?r", "mono"),
    (r"\bteal\b|blockbuster|hollywood", "teal-orange"),
    (r"\bfaded\b|washed|vintage|retro|muted|pastel|soft colou?rs?", "faded"),
    (r"\b(?:cool|cold|icy|blue)\b", "cool"),
    (r"\b(?:warm|golden|sunny|sunset)\b", "warm"),
    (r"\bfilm(?:ic|y)?\b|cinematic|grain", "film"),
    (r"\bpunchy\b|high contrast|\bbold\b|moody", "punchy"),
    (r"\b(?:natural|as shot|true to life|realistic|untouched)\b", "natural"),
    (r"\b(?:bright|vivid|colou?rful|saturated|pop)\b", "vivid"),
]


def own_look(words: str) -> str | None:
    for pattern, look in OWN_LOOKS:
        if re.search(pattern, words or "", re.I):
            return look
    return None


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
    return [{"id": f"o{i + 1}", "text": it["text"], "why": it.get("why", ""), "source": "owner", "at": today,
             "for": it.get("for", "all") if it.get("for", "all") in SCOPES else "all"} for i, it in enumerate(items or [])]


@dataclass
class Profile:
    path: Path
    rules: list[dict] = field(default_factory=list)
    answers: dict = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    taste: list[dict] = field(default_factory=list)  # their looks: what they picked, passed over, changed, rated

    @classmethod
    def load(cls, path: Path) -> "Profile":
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            rules = data.get("rules", [])
            seeds = {r["text"]: r["for"] for r in owner_seed_rules()}
            for r in rules:  # made before rules had a scope: the owner's seeded film rules are about films
                if "for" not in r and r.get("source") == "owner" and r.get("text") in seeds:
                    r["for"] = seeds[r["text"]]
            return cls(path, rules, data.get("answers", {}), data.get("history", []), data.get("taste", []))
        return cls(path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"rules": self.rules, "answers": self.answers, "history": self.history[-100:],
                                   "taste": self.taste[-MAX_TASTE:]}, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    @property
    def look(self) -> str:
        """The colour grade for their films. Only a real look ever reaches the renderer: "Decide for each film" keeps
        the adaptive default, and their own words pick the nearest grade when they name one (they are a rule too)."""
        from ..render.looks import LOOKS

        v = self.answers.get("look")
        if v == "other":
            v = own_look(self.answers.get("look_other", ""))
        return v if v in LOOKS else "vivid"

    def add(self, text: str, why: str = "", source: str = "feedback", scope: str = "all") -> str | None:
        text = " ".join(str(text).split())[:240]
        if not text or any(r["text"].lower() == text.lower() for r in self.rules):
            return None
        rid = f"r{secrets.token_hex(3)}"
        self.rules.append({"id": rid, "text": text, "why": " ".join(str(why).split())[:240], "source": source,
                           "at": date.today().isoformat(), "for": scope if scope in SCOPES else "all"})
        if len(self.rules) > MAX_RULES:  # keep the profile short: the oldest learned rules go first
            learned = [r for r in self.rules if r["source"] == "feedback"]
            if learned:
                self.rules.remove(learned[0])
        return rid

    def note_look(self, what: str, look: dict, note: str = "") -> None:
        """Remember a look choice: 'picked', 'passed' (shown, not picked), 'changed' (with what they asked), 'own'
        (their own idea) or 'rated' (with how the video made in it was rated)."""
        self.taste.append({"at": date.today().isoformat(), "what": what, "note": " ".join(str(note).split())[:200],
                           "look": {k: str(look.get(k, ""))[:200] for k in ("title", "art_style", "world", "palette")}
                                   | {"colours": list(look.get("colours") or [])[:5]}})
        self.taste = self.taste[-MAX_TASTE:]

    def taste_text(self) -> str:
        """Their looks so far, for the art director: newest first."""
        lines = []
        for t in reversed(self.taste[-MAX_TASTE:]):
            lk = t["look"]
            desc = f"'{lk.get('title')}' ({lk.get('art_style', '')[:90]}; {lk.get('world', '')[:90]})"
            lines.append({"picked": f"- picked {desc}", "passed": f"- passed over {desc}",
                          "changed": f"- asked to change {desc}: \"{t['note']}\"",
                          "own": f"- described their own look: \"{t['note']}\"",
                          "rated": f"- the video made in {desc} was rated {t['note']}"}.get(t["what"], f"- {desc}"))
        return "\n".join(lines[:24])

    def drop(self, rid: str) -> bool:
        before = len(self.rules)
        self.rules = [r for r in self.rules if r["id"] != rid]
        return len(self.rules) < before

    def set_answers(self, answers: dict[str, str], never: str = "", others: dict[str, str] | None = None) -> None:
        """Answers to the starting questions become rules (replacing earlier answer rules). Every question also takes
        'other' (their own words, kept as their rule) and 'any' (no rule: decided for each film), so nothing they
        didn't really choose is fixed in their style."""
        valid = {q: {v for v, _ in opts} | {"any", "other"} for q, _, opts in QUESTIONS}
        clean = {k: v for k, v in answers.items() if k in valid and v in valid[k]}
        self.rules = [r for r in self.rules if r["source"] != "answer"]
        self.answers = {}
        for (q, v), text in ANSWER_RULES.items():
            if clean.get(q) == v:
                self.add(text, "your answer", "answer")
        for q, question, _ in QUESTIONS:
            v = clean.get(q)
            if v == "other":
                own = " ".join(str((others or {}).get(q, "")).split())[:200]
                if not own:
                    continue  # 'other' with nothing written is no answer
                self.answers[f"{q}_other"] = own
                self.add(f"{question.rstrip('?')}: {own}", "your answer", "answer")
            if v:
                self.answers[q] = v
        never = " ".join(never.split())[:200]
        if never:
            self.answers["never"] = never
            self.add(f"Never show: {never}.", "your answer", "answer")

    def apply(self, update: ProfileUpdate, scope: str = "all") -> dict:
        """What the coach learned from one video; new rules apply to videos of that video's kind (`scope`)."""
        added = [rid for rule in update.add[:3] if (rid := self.add(rule.text, rule.why, scope=scope))]
        dropped = [rid for rid in update.drop if self.drop(rid)]
        entry = {"at": date.today().isoformat(), "summary": update.summary, "added": added, "dropped": dropped}
        self.history.append(entry)
        return entry
