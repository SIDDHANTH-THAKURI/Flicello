"""Looks for animated films (motion/animated.py). A kit is a folder: kit.js (the parts, the scenes, the ways between
them and the film's runner, seek-safe by construction), craft.md (how to design with it, and the bar), example.js
(scenes written with it at that bar). New looks are new folders with the same API names."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent
FONTS = ROOT.parent / "blocks" / "fonts"
WORDS = {  # what each look is called in a request, for picking it from their words
    "paper": ("paper", "cut paper", "paper cut", "papercut", "pop-up", "pop up book", "collage", "craft"),
}


def names() -> list[str]:
    return sorted(p.name for p in ROOT.iterdir() if p.is_dir() and (p / "kit.js").exists())


def folder(name: str) -> Path:
    path = ROOT / name
    if not (path / "kit.js").exists():
        raise ValueError(f"there's no look called '{name}' (there is: {', '.join(names())})")
    return path


def from_words(words: str, default: str = "paper") -> str:
    low = (words or "").lower()
    for name, said in WORDS.items():
        if name in names() and any(w in low for w in said):
            return name
    return default
