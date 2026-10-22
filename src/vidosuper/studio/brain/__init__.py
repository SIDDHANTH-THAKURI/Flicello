"""The studio's brain: the judgement steps (log clips, pitch stories, design and revise the cut, learn a
person's style), backed by the Claude API, headless Claude Code (the owner's own plan) or plain rules."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ...schemas import ClipSignals
from ..settings import BrainKind, StudioSettings
from .backends import ApiBackend, BrainError, BudgetExceeded, ClaudeCodeBackend, PersonLimit, SpendFn, Usage
from .core import Brain, LocalBrain, StoryContext

__all__ = ["Brain", "LocalBrain", "StoryContext", "BrainError", "BudgetExceeded", "PersonLimit", "Usage", "make_brain",
           "brain_kind_for", "make_watcher"]


def brain_kind_for(settings: StudioSettings, owner: bool) -> BrainKind:
    """Headless Claude Code runs on the owner's personal plan, so it only ever works on the owner's videos;
    everyone else's videos use the API (or the local rules when the owner chose that for a test)."""
    kind = settings.owner_brain if owner else settings.brain
    if kind == "claude_code" and not owner:
        raise BrainError("Claude Code is only for the owner's own videos; set the studio brain to 'api'.")
    return kind


def make_watcher(kind: BrainKind, settings: StudioSettings, spend: SpendFn, log_dir: Path | None = None):
    """Gemini watching each clip with its sound (brain/gemini.py), when there is a key and a model for it; None: the
    contact-sheet analyst logs the clips (and always does without the AI)."""
    import os

    if kind == "local" or not settings.models.watcher or not os.environ.get("GEMINI_API_KEY"):
        return None
    try:
        from .gemini import GeminiWatcher

        return GeminiWatcher(settings.models.watcher, spend, log_dir)
    except Exception:  # noqa: BLE001 - no SDK or no key: the analyst logs the clips, as before
        return None


def make_brain(kind: BrainKind, settings: StudioSettings, spend: SpendFn, log_dir: Path | None = None,
               signals: Callable[[str], ClipSignals | None] | None = None):
    if kind == "local":
        return LocalBrain(spend, signals)
    if kind == "claude_code":
        return Brain(ClaudeCodeBackend(), settings.models, spend, log_dir)
    return Brain(ApiBackend(), settings.models, spend, log_dir)
