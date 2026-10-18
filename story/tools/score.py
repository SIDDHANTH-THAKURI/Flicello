"""Compose a film's whole score from the taste the owner picked (ElevenLabs music, the taste as its reference), shaped
to the story: sections with their lengths, each with the words that say what the music does there.

    python story/tools/score.py <score.json> <out.mp3>

score.json: {"taste": "music/taste2.json" (a stored taste: its song id and plan), "sections": [{"name": "intro",
"seconds": 8, "words": ["piano and cello enter softly"]}, ...], "extra": ["..."], "avoid": ["..."]}
Prints the energy per second of what came back: the model keeps its own time, so the cut is fitted to where its
moments really fall (not where the plan asked for them).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare import PUBLIC  # noqa: E402


def main() -> None:
    from vidosuper.music import analyze_music
    from vidosuper.music.compose import compose_plan

    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    taste = json.loads((PUBLIC / spec["taste"]).read_text(encoding="utf-8"))
    first = taste["plan"]["chunks"][0]
    base = [s for s in first["positive_styles"] if s not in ("piano and cello enter softly",)] + spec.get("extra", [])
    avoid = list(dict.fromkeys(first["negative_styles"] + spec.get("avoid", [])))[:50]
    chunks = []
    for s in spec["sections"]:
        chunks.append({"text": f"[{s['name'].title()}] {{instrumental}}", "duration_ms": int(s["seconds"] * 1000),
                       "positive_styles": list(dict.fromkeys(base + s.get("words", [])))[:50],
                       "negative_styles": avoid})
    if taste.get("song_id"):
        chunks[0]["conditioning_ref"] = {"song_id": taste["song_id"], "range": {"start_ms": 0, "end_ms": 30000}}
        chunks[0]["condition_strength"] = "high"
    plan = {"chunks": chunks}
    done = compose_plan(plan, out, store=True)
    out.with_suffix(".json").write_text(json.dumps({"song_id": done["song_id"], "plan": plan}), encoding="utf-8")
    a = analyze_music(out)
    print(f"{done['seconds']} s, stored: {bool(done['song_id'])}")
    print("energy per second:", " ".join(f"{e:.2f}" for e in a.energy))


if __name__ == "__main__":
    main()
