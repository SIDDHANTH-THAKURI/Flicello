"""Give every moment of a script that asks for an animation a real one from the library.

For each need ("two people podcasting at a table", topics podcast, microphone): an animation picked before for nearly the
same need is reused; otherwise the library's candidates for its topics (pulled from LottieFiles the first time) are laid
out on a numbered sheet and the brain picks the number that shows it best, or none. Several needs go to the brain in one
request. Whatever can't be filled keeps its emoji and text, so a video never waits on an animation.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from .lottie_lib import Library
from .script import VoiceScript, keywords

# picker(needs) -> one choice per need: an index into its sheet, or -1 for none. Each need: {"visual", "sheet", "n"}.
Picker = Callable[[list[dict]], list[int]]
BATCH = 6


def _topics(visual: str, topics: list[str]) -> list[str]:
    out = [t for t in topics if t.strip()][:3]
    if not out:
        out = [w.lower() for w in keywords(visual, 2)]
    return out


def needs_of(script: VoiceScript) -> list[tuple[str, int, str, list[str]]]:
    """(where, index, visual, topics) for everything in the script that wants an animation and has none yet."""
    out = []
    if script.ambient and not script.ambient_lottie:
        out.append(("ambient", 0, script.ambient, _topics(script.ambient, script.ambient_topics)))
    for k, c in enumerate(script.callouts):
        if c.kind in ("scene", "stack") and c.visual and not c.lottie:
            out.append(("callout", k, c.visual, _topics(c.visual, c.topics)))
    for k, s in enumerate(script.scenes):
        if s.visual and not s.lottie:
            out.append(("scene", k, s.visual, _topics(s.visual, s.topics)))
    return out


Rethink = Callable[[list[dict]], list[dict]]  # needs nothing fitted -> [{"visual", "topics"}] to try instead


def assign(script: VoiceScript, lib: Library, picker: Picker, workdir: Path,
           rethink: Rethink | None = None) -> tuple[VoiceScript, list[str]]:
    """The script with animations filled in, and a line per need on what happened (for the log). Needs nothing fitted
    get one more try with a simpler picture and commoner search words (`rethink`, the brain)."""
    log: list[str] = []
    chosen: dict[tuple[str, int], str] = {}
    failed = _round(needs_of(script), lib, picker, workdir, chosen, log)
    if failed and rethink is not None:
        try:
            alts = rethink([{"visual": v, "topics": t} for _, _, v, t in failed])
        except Exception as exc:  # noqa: BLE001
            alts = []
            log.append(f"rethinking failed ({type(exc).__name__})")
        second = [(w, k, a.get("visual", ""), list(a.get("topics", []))[:3]) for (w, k, _, _), a in zip(failed, alts)
                  if a.get("visual")]
        if second:
            log.append(f"second try for {len(second)}")
            _round(second, lib, picker, workdir, chosen, log)
    callouts = [c.model_copy(update={"lottie": chosen.get(("callout", k), c.lottie)}) for k, c in enumerate(script.callouts)]
    scenes = [s.model_copy(update={"lottie": chosen.get(("scene", k), s.lottie)}) for k, s in enumerate(script.scenes)]
    return script.model_copy(update={"callouts": callouts, "scenes": scenes,
                                     "ambient_lottie": chosen.get(("ambient", 0), script.ambient_lottie)}), log


def _round(found, lib: Library, picker: Picker, workdir: Path, chosen: dict, log: list[str]) -> list:
    """One pass of reuse-or-ask over `found`; returns the needs that got nothing."""
    failed = []
    ask: list[dict] = []
    for where, k, visual, topics in found:
        old = lib.remembered(visual)
        if old:
            chosen[(where, k)] = old
            log.append(f"{visual}: reused {old[:8]}")
            continue
        cands = lib.candidates(topics)
        if not cands:
            log.append(f"{visual}: nothing found for {', '.join(topics)}")
            failed.append((where, k, visual, topics))
            continue
        if lib.rejected(visual, cands):
            log.append(f"{visual}: none of these fitted last time")
            failed.append((where, k, visual, topics))
            continue
        sheet = lib.sheet(cands, Path(workdir) / f"need-{len(ask)}-{re.sub(r'[^a-z]+', '-', visual.lower())[:30]}.jpg")
        ask.append({"visual": visual, "sheet": str(sheet), "n": len(cands), "key": (where, k), "cands": cands})
    for i in range(0, len(ask), BATCH):
        part = ask[i:i + BATCH]
        try:
            picks = picker([{"visual": a["visual"], "sheet": a["sheet"], "n": a["n"]} for a in part])
        except Exception as exc:  # noqa: BLE001 - no pick is better than no video
            log.append(f"picking failed ({type(exc).__name__})")
            continue
        for a, choice in zip(part, picks):
            if 0 <= choice < len(a["cands"]):
                aid = a["cands"][choice]
                chosen[a["key"]] = aid
                lib.remember(aid, a["visual"])
                log.append(f"{a['visual']}: picked {aid[:8]} of {a['n']}")
            else:
                lib.reject(a["visual"], a["cands"])
                log.append(f"{a['visual']}: none of {a['n']} fit")
                failed.append((*a["key"], a["visual"], []))
    return failed
