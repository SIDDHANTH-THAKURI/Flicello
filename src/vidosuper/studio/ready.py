"""Ready to post? A score out of 100 for a finished version, made of what short-video reach is known to hang on, each
part measured on the film itself and each weak part with a fix that can be made at once (a draft):
- **the hook**: how strongly the first shot grabs (the clip log's hook score for that moment), and whether a stronger
  opener is sitting later in the film;
- **a text hook**: words on screen in the first second and a half (a title or words on the beat);
- **pace**: the average shot length against what holds attention in a tall feed (0.7-2 s) or a wide one (1.2-4 s);
- **length**: against the platform's sweet spot;
- **captions**: when people speak in it and nothing shows their words (most feeds play muted);
- **a person early**: a face or someone close in the first three shots;
- **on the beat**: whether the cuts land on the music's hits (the brief's own check);
- **a loop**: whether the ending lands back where it began (rewatches count).

Platform research behind the parts is in docs/research/flicello-stack-2026-10.md (section 6). It is a checklist made by
measurement, not a prediction: a trained engagement model joins it when its data is in."""

from __future__ import annotations

from .brain.context import logged_at, place_of

WEIGHTS = {"hook": 25, "text": 10, "pace": 15, "length": 10, "captions": 15, "person": 10, "beat": 10, "loop": 5,
           "viewer": 20}


def _band(x: float, lo: float, hi: float, soft: float) -> float:
    """1 inside [lo, hi], falling to 0 over `soft` outside it."""
    if lo <= x <= hi:
        return 1.0
    d = lo - x if x < lo else x - hi
    return max(0.0, 1.0 - d / soft)


def assess(plan, facts: dict, cards: list[dict], motion: list, seconds: float, tall: bool, speech: bool,
           checks: list[dict] | None = None, viewer: dict | None = None) -> dict:
    """The score, its parts (each 0-1 with why, and a fix: a change to make as a draft), and a verdict."""
    pool = {m.key: m for m in plan.pool}
    parts = []

    def moment(s):
        m = pool.get(s.key)
        f = facts.get(m.clip) if m else None
        return logged_at(f, m.start, m.end) if f and m else None

    logged = [moment(s) for s in plan.plan]
    hooks = [int((lm or {}).get("hook") or 0) for lm in logged]
    first = hooks[0] if hooks else 0
    best = max(range(len(hooks)), key=lambda k: hooks[k]) if hooks else 0
    fix = None
    if hooks and best > 0 and hooks[best] >= first + 2:
        fix = {"op": "move", "shot": best + 1, "other": 1}
    parts.append({"id": "hook", "name": "Hook", "score": round(first / 10, 2) if first else 0.5,
                  "why": (f"the first shot's pull is {first}/10" if first else "the first shot's pull is unknown")
                  + (f"; shot {best + 1} pulls harder ({hooks[best]}/10)" if fix else ""), "fix": fix,
                  "fix_says": f"Open on shot {best + 1}" if fix else ""})
    early = [g for g in motion if g.kind in ("title", "kinetic") and g.start <= 1.5]
    parts.append({"id": "text", "name": "Words on screen early", "score": 1.0 if early else 0.3,
                  "why": "words on screen in the first second and a half" if early else "nothing to read in the first second",
                  "fix": None if early else {"op": "title", "shot": 1, "style": "slam"}, "fix_says": "" if early else "Add a title"})
    spans = [max(0.05, c["end"] - c["start"]) for c in cards] if cards else []
    mean = sum(spans) / len(spans) if spans else 0.0
    lo, hi = (0.7, 2.0) if tall else (1.2, 4.0)
    parts.append({"id": "pace", "name": "Pace", "score": round(_band(mean, lo, hi, 1.5), 2) if spans else 0.5,
                  "why": f"shots last {mean:.1f} s on average ({lo:g}-{hi:g} s holds attention here)", "fix": None,
                  "fix_says": ""})
    lo, hi = (7.0, 35.0) if tall else (20.0, 120.0)
    parts.append({"id": "length", "name": "Length", "score": round(_band(seconds, lo, hi, 25.0 if tall else 90.0), 2),
                  "why": f"{seconds:.0f} s ({lo:g}-{hi:g} s does best here)", "fix": None, "fix_says": ""})
    captioned = any(g.kind == "captions" and g.words for g in motion)
    if speech:
        parts.append({"id": "captions", "name": "Captions", "score": 1.0 if captioned else 0.0,
                      "why": "what is said shows on screen" if captioned else "people speak, but most feeds play muted",
                      "fix": None if captioned else {"op": "captions", "shot": 1},
                      "fix_says": "" if captioned else "Add captions"})
    near = [k for k, c in enumerate(cards) if c.get("people") in ("near", "face")]
    parts.append({"id": "person", "name": "A person early", "score": 1.0 if near and near[0] < 3 else 0.5 if near else 0.6,
                  "why": ("someone close in the first shots" if near and near[0] < 3 else
                          f"the first person close up is in shot {near[0] + 1}" if near else "nobody close up in it"),
                  "fix": None, "fix_says": ""})
    sync = next((c for c in checks or [] if c.get("check") == "sync"), None)
    if sync is not None:
        parts.append({"id": "beat", "name": "On the beat", "score": 1.0 if sync.get("ok") else 0.4,
                      "why": sync.get("detail") or ("cuts land on the hits" if sync.get("ok") else "cuts miss the hits"),
                      "fix": None, "fix_says": ""})
    if len(logged) >= 2:
        a = place_of(logged[0] or {})
        b = place_of(logged[-1] or {})
        loop = bool(a) and a == b
        parts.append({"id": "loop", "name": "A loop", "score": 1.0 if loop else 0.5,
                      "why": "it ends where it began, so it plays again" if loop else "the ending doesn't lead back to the start",
                      "fix": None, "fix_says": ""})
    if viewer:  # an AI viewer watched it (Gemini, with its sound): would they stop, would they stay
        parts.append({"id": "viewer", "name": "A viewer's eye", "score": round((viewer["stop"] + viewer["hold"]) / 20, 2),
                      "why": f"stops the scroll {viewer['stop']}/10, holds {viewer['hold']}/10"
                      + (f"; most leave at {viewer['leave_at']:g} s" if viewer.get("leave_at") and viewer["leave_at"] < seconds - 0.5 else "")
                      + f". {viewer['why']}", "fix": None, "fix_says": ""})
    total = sum(WEIGHTS[p["id"]] for p in parts)
    score = round(100 * sum(WEIGHTS[p["id"]] * p["score"] for p in parts) / max(1, total))
    verdict = "Ready to post" if score >= 80 else "Nearly there" if score >= 60 else "Not yet"
    return {"score": score, "verdict": verdict, "parts": parts, "viewer": viewer}
