"""Listen to the tightened voice again (Whisper) and say what an editor would still cut: fillers ("uh", "um") and any
word that came out of a join wrong, each with its time in the original talk, ready for the edit file's "drop" list.

    python story/tools/listen.py [--expect "the words he should say, in order"]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare import PUBLIC, tool  # noqa: E402

FILLERS = {"uh", "um", "uhm", "er", "erm", "ah", "hmm", "mm"}


def to_source(t: float, segs: list[list[float]]) -> float:
    at = 0.0
    for a, b in segs:
        if t <= at + (b - a) + 1e-6:
            return a + (t - at)
        at += b - a
    return segs[-1][1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", default="")
    a = ap.parse_args()
    from vidosuper.config import load_config
    from vidosuper.perceive.transcribe import transcribe

    cfg = load_config()
    segs = json.loads((PUBLIC / "cuts.json").read_text(encoding="utf-8"))["segments"]
    wav16 = Path(tempfile.gettempdir()) / "story-listen16k.wav"
    subprocess.run([tool(), "-y", "-loglevel", "error", "-i", str(PUBLIC / "voice.wav"), "-ar", "16000", "-ac", "1",
                    str(wav16)], check=True)
    tr = transcribe("listen", wav16, cfg.analysis.speech_model, cfg.models_root / "whisper")
    words = [w for u in tr.utterances for w in u.words]
    print(" ".join(w.text.strip() for w in words))
    print()
    for w in words:
        key = re.sub(r"[^a-z']", "", w.text.lower())
        if key in FILLERS:
            print(f"filler {w.text.strip():>8}  tight {w.start:7.2f}-{w.end:7.2f}   original {to_source(w.start, segs):7.2f}-{to_source(w.end, segs):7.2f}")
    if a.expect:
        heard = [re.sub(r"[^a-z0-9']", "", w.text.lower()) for w in words]
        heard = [h for h in heard if h and h not in FILLERS]
        want = [re.sub(r"[^a-z0-9']", "", x.lower()) for x in a.expect.split()]
        import difflib

        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=want, b=heard, autojunk=False).get_opcodes():
            if op != "equal":
                print(f"{op:>8}: expected {' '.join(want[i1:i2])!r} heard {' '.join(heard[j1:j2])!r}")


if __name__ == "__main__":
    main()
