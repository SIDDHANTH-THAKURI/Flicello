"""The Undertone review as a story: his talk (tightened by tools/prepare.py) told over scenes that act it out.

Shots are placed on his words (found in the tightened captions), so the plan survives re-tightening. Writes the props
file for Remotion and gets the media the plan names ready (stills for the phone feed, the music bed, the sound effects).

    python story/plans/undertone.py [--part test|all]
    cd story && npx remotion render src/index.ts Story <out>.mp4 --props=<props file>
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from prepare import PUBLIC, ffmpeg, two_pass_loudnorm  # noqa: E402

OUT = PUBLIC.parent / "out"
SFX = PUBLIC.parent.parent / "sfx"  # %LOCALAPPDATA%/VidoSuper/sfx
STOCK = Path(__import__("tempfile").gettempdir()) / "wow" / "story" / "clips"


class Words:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.keys = [re.sub(r"[^a-z0-9']", "", r["text"].lower()) for r in rows]

    def find(self, phrase: str, after: float = 0.0) -> dict:
        want = [re.sub(r"[^a-z0-9']", "", x) for x in phrase.lower().split()]
        for i in range(len(self.keys) - len(want) + 1):
            if self.keys[i:i + len(want)] == want and self.rows[i]["t"] >= after:
                return {"t": self.rows[i]["t"], "end": self.rows[i + len(want) - 1]["end"]}
        raise KeyError(f"not said: {phrase!r} (after {after})")

    def at(self, phrase: str, after: float = 0.0) -> float:
        return self.find(phrase, after)["t"]


def still(clip: Path, at: float, out: Path, width: int = 960) -> str:
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg("-ss", str(at), "-i", str(clip), "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(out))
    return str(out.relative_to(PUBLIC)).replace("\\", "/")


def bed(src: Path, out: Path) -> str:
    """The music under a voice: no sub rumble, a dip where his voice lives, -20 LUFS (ducked further under speech)."""
    if not out.exists():
        mid = out.with_suffix(".eq.wav")
        ffmpeg("-i", str(src), "-af", "highpass=f=38,equalizer=f=110:t=q:w=0.9:g=-2.5,equalizer=f=320:t=q:w=1.1:g=-3,"
               "equalizer=f=2600:t=q:w=1.4:g=-2", "-ar", "48000", str(mid))
        two_pass_loudnorm(mid, out, target=-20.0, peak=-3.0)
        mid.unlink(missing_ok=True)
    return str(out.relative_to(PUBLIC)).replace("\\", "/")


def effect(name: str, lowpass: int = 9000) -> str:
    """A sound effect, softened (nothing bright enough to sting) and levelled."""
    out = PUBLIC / "sfx" / f"{name}.wav"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg("-i", str(SFX / f"{name}.wav"), "-af", f"lowpass=f={lowpass},afade=t=out:st=0:d=0.01:curve=nofade",
               "-ar", "48000", "-ac", "2", str(out))
    return f"sfx/{name}.wav"


def test_plan(w: Words, cuts: list[float]) -> dict:
    """The opening (to "...done a job very well"): the style test."""
    title = w.at("Undertone") - 0.06
    lights_on = w.at("released") - 0.1
    feed = w.at("and a lot of people") - 0.1
    notify = w.at("suggestion") - 0.08
    play = w.at("one of the popular") - 0.05
    bed_scene = w.at("so i just went") - 0.12
    back = w.at("the movie actually") - 0.08
    audio = w.at("audio") - 0.06
    sound = w.at("sound") - 0.1
    terrify = w.at("terrify")
    director = w.at("and i think the director") - 0.05
    said = w.find("very well")["end"]
    end = said + 0.55
    scenes = "scenes/"
    shots = [
        {"type": "narrator", "start": 0, "end": title},
        {"type": "narrator", "start": title, "end": lights_on, "title": "UNDERTONE"},
        {"type": "narrator", "start": lights_on, "end": feed},
        {"type": "ui", "kind": "phone", "start": feed, "end": bed_scene, "in": {"type": "whip", "seconds": 0.36},
         "title": "UNDERTONE", "note": "Recommended for you", "notifyAt": notify, "playAt": play,
         "poster": still(STOCK / "3988278.mp4", 1.0, PUBLIC / "stills" / "poster-corridor.jpg"),
         "clip": {"file": scenes + "3988278.mp4", "from": 1.0}},
        {"type": "scene", "file": scenes + "7924639.mp4", "from": 1.5, "start": bed_scene, "end": back,
         "in": {"type": "fade", "seconds": 0.4}, "zoom": [1.0, 1.1], "focus": [0.42, 0.5], "dark": 0.1},
        {"type": "narrator", "start": back, "end": audio, "in": {"type": "fade", "seconds": 0.3}},
        {"type": "scene", "file": scenes + "29594574.mp4", "from": 0.5, "start": audio, "end": sound, "pip": True,
         "in": {"type": "swap", "seconds": 1.1}, "zoom": [1.05, 1.15], "focus": [0.5, 0.5]},
        {"type": "scene", "file": scenes + "7586157.mp4", "from": 1.0, "start": sound, "end": terrify - 0.12,
         "pip": True, "in": {"type": "wave", "seconds": 0.3}, "zoom": [1.0, 1.08], "focus": [0.5, 0.55]},
        {"type": "scene", "file": scenes + "7984182.mp4", "from": 1.0, "start": terrify - 0.12, "end": director,
         "pip": True, "in": {"type": "cut", "seconds": 0}, "zoom": [1.04, 1.16], "focus": [0.5, 0.42], "dark": 0.05},
        {"type": "narrator", "start": director, "end": end, "in": {"type": "swap", "seconds": 0.9}},
    ]
    return {
        "duration": round(end, 3),
        "said": said,
        "shots": shots,
        "sfx": [
            {"at": title - 0.02, "file": effect("subdrop"), "gain": 0.55},
            {"at": feed - 0.2, "file": effect("whoosh", 7000), "gain": 0.22},
            {"at": notify + 0.05, "file": effect("chime", 6000), "gain": 0.16},
            {"at": audio - 0.1, "file": effect("whoosh", 7000), "gain": 0.16},
            {"at": terrify - 0.06, "file": effect("impact"), "gain": 0.5},
            {"at": director - 0.1, "file": effect("whoosh", 6000), "gain": 0.12},
        ],
        "shakes": [{"at": terrify - 0.04, "amount": 16}],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="test", choices=["test"])
    ap.add_argument("--music", default="music/taste2.mp3")
    a = ap.parse_args()
    words = json.loads((PUBLIC / "words.json").read_text(encoding="utf-8"))
    cuts = json.loads((PUBLIC / "cuts.json").read_text(encoding="utf-8"))["cuts"]
    w = Words(words)
    part = test_plan(w, cuts)
    end = part["duration"]
    plan = {
        "width": 1080, "height": 1920, "fps": 30, "duration": end,
        "narrator": {"video": "narrator.mp4", "alpha": "narrator-alpha.webm", "voice": "voice.wav"},
        "cuts": [c for c in cuts if c < end],
        "music": {"file": bed(PUBLIC / a.music, PUBLIC / "music" / (Path(a.music).stem + "-bed.wav")), "gain": 0.9,
                  "duck": 0.58, "fadeOut": 1.6},
        "loudness": "levels.json",
        "grade": {"contrast": 1.12, "saturate": 0.72, "brightness": 0.94, "warmth": 1, "grain": 0.12, "vignette": 0.5},
        "shots": part["shots"],
        "words": [x for x in words if x["t"] < part["said"]],
        "sfx": part["sfx"],
        "shakes": part["shakes"],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    props = OUT / f"undertone-{a.part}.json"
    props.write_text(json.dumps({"plan": plan}, indent=1), encoding="utf-8")
    fonts = PUBLIC / "fonts"  # the site's own two faces
    src = Path(__file__).resolve().parents[2] / "frontend" / "node_modules" / "@fontsource-variable"
    for name, face in (("bricolage", "bricolage-grotesque/files/bricolage-grotesque-latin-wght-normal.woff2"),
                       ("geist-mono", "geist-mono/files/geist-mono-latin-wght-normal.woff2")):
        if not (fonts / f"{name}.woff2").exists():
            fonts.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / face, fonts / f"{name}.woff2")
    print(props)
    for s in plan["shots"]:
        print(f"  {s['start']:6.2f}-{s['end']:6.2f}  {s['type']:9} {s.get('title') or s.get('file') or s.get('kind') or ''}")


if __name__ == "__main__":
    main()
