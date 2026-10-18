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


HOT = {"undertone", "audio", "sound", "terrify", "linear", "7.5", "climax", "podcast", "anonymous", "horror"}


def full_plan(w: Words) -> dict:
    """The whole review: the opening as approved (the style test), then the rest of the talk acted out, building to
    the film's genre with the lights out, and a last turn after his sign-off (an eleventh clip, sent to him)."""
    o = test_plan(w, [])
    shots, sfx, shakes = o["shots"][:-1], o["sfx"], o["shakes"]
    director = o["shots"][-1]["start"]
    scenes = "scenes/"
    long_take = w.at("the way the movie actually progresses") - 0.05
    papers = w.at("there is no much") - 0.08
    catch = w.at("so that is the catch") - 0.05
    watching = w.at("you might enjoy") - 0.05
    give = w.at("just give it a try") - 0.05
    rate = w.at("if you ask me") - 0.05
    fill = w.at("rate around")
    score = w.at("7.5")
    liked = w.at("i really like") - 0.05
    arc = w.at("the way it actually starts") - 0.1
    starts, turns, climax = w.at("starts"), w.at("turns"), w.at("climax")
    gist = w.at("so to give you") - 0.05
    story = w.at("it's a story of") - 0.05
    called = w.at("a podcast called") - 0.1
    sign = w.at("Undertone", after=60)
    friend = w.at("with one of her") - 0.08
    laptop = w.at("they actually come across") - 0.1
    email = w.at("one anonymous") - 0.1
    anon = w.at("anonymous")
    ten = w.at("10 audio clips")
    in_it = w.find("clips in it")["end"]
    each = w.at("so they go through")
    discover = w.at("try to discover") - 0.1
    why = w.at("and why") - 0.1
    rest = w.at("and the rest of the story") - 0.1
    dark = w.at("it's basically") - 0.08
    horror = w.at("horror")
    look = w.at("have a look") - 0.15
    said = w.find("let me know")["end"]
    out = said + 0.35
    end = out + 2.6
    shots += [
        {"type": "narrator", "start": director, "end": long_take, "in": {"type": "swap", "seconds": 0.9}},
        # "it doesn't actually cut in between": one slow take, no cut, while he says it
        {"type": "scene", "file": scenes + "33671897.mp4", "from": 0.2, "rate": 0.85, "start": long_take, "end": papers,
         "in": {"type": "fade", "seconds": 0.8}, "zoom": [1.0, 1.2], "focus": [0.5, 0.42]},
        {"type": "scene", "file": scenes + "6878201.mp4", "from": 1.0, "start": papers, "end": catch,
         "zoom": [1.18, 1.0], "focus": [0.42, 0.62], "exposure": 1.5},
        {"type": "narrator", "start": catch, "end": watching},
        {"type": "scene", "file": scenes + "9808087.mp4", "from": 1.5, "start": watching, "end": give,
         "in": {"type": "fade", "seconds": 0.3}, "zoom": [1.0, 1.08], "focus": [0.5, 0.45], "frame": [0.76, 0.5]},
        {"type": "narrator", "start": give, "end": rate},
        {"type": "narrator", "start": rate, "end": liked, "rating": 7.5, "at": fill},
        {"type": "narrator", "start": liked, "end": arc},
        {"type": "card", "kind": "arc", "text": "", "start": arc, "end": gist, "marks": [starts, turns, climax],
         "in": {"type": "fade", "seconds": 0.3}},
        {"type": "narrator", "start": gist, "end": story, "in": {"type": "fade", "seconds": 0.25}},
        # the story: he steps into the podcast studio and tells it from the corner
        {"type": "scene", "file": scenes + "6878737.mp4", "from": 1.0, "start": story, "end": called, "pip": True,
         "in": {"type": "swap", "seconds": 1.1}, "zoom": [1.0, 1.08], "focus": [0.5, 0.45], "frame": [0.84, 0.5]},
        {"type": "scene", "file": scenes + "6883827.mp4", "from": 1.0, "start": called, "end": friend, "pip": True,
         "zoom": [1.04, 1.12], "focus": [0.62, 0.4], "sign": "UNDERTONE", "signAt": sign},
        {"type": "scene", "file": scenes + "6883833.mp4", "from": 1.0, "start": friend, "end": laptop, "pip": True,
         "zoom": [1.04, 1.12], "focus": [0.5, 0.42]},
        {"type": "scene", "file": scenes + "34771082.mp4", "from": 1.0, "start": laptop, "end": email,
         "in": {"type": "fade", "seconds": 0.3}, "zoom": [1.0, 1.12], "focus": [0.5, 0.5], "dark": 0.1},
        {"type": "ui", "kind": "email", "start": email, "end": discover, "in": {"type": "whip", "seconds": 0.36},
         "title": "listen.", "count": 10, "showAt": anon, "listAt": [ten, in_it], "playAt": each},
        {"type": "scene", "file": scenes + "6883839.mp4", "from": 2.0, "start": discover, "end": why,
         "zoom": [1.06, 1.16], "focus": [0.6, 0.38], "dark": 0.12},
        {"type": "scene", "file": scenes + "6883835.mp4", "from": 1.0, "start": why, "end": rest,
         "zoom": [1.0, 1.1], "focus": [0.5, 0.55], "dark": 0.1, "frame": [0.05, 0.5]},
        {"type": "scene", "file": scenes + "6883832.mp4", "from": 1.0, "start": rest, "end": dark,
         "zoom": [1.0, 1.25], "focus": [0.45, 0.4], "dark": 0.25},
        # the genre, with the lights already out (a hard cut into the dark); the music has dropped away
        {"type": "narrator", "start": dark, "end": look, "title": "HORROR", "at": horror, "dark": True},
        {"type": "narrator", "start": look, "end": out},
        # after he signs off: the lights go, and one more clip arrives - for him
        {"type": "ui", "kind": "notify", "start": out, "end": end, "note": "Anonymous", "title": "clip_11.wav",
         "at": out + 0.4},
    ]
    ticks = 7
    sfx += [
        {"at": long_take, "file": effect("subdrop"), "gain": 0.3},
        {"at": papers - 0.02, "file": effect("tick"), "gain": 0.16},
        {"at": watching - 0.15, "file": effect("whoosh", 7000), "gain": 0.12},
        {"at": rate, "file": effect("switch"), "gain": 0.3},
        *({"at": fill + (score - fill) * k / ticks, "file": effect("tick"), "gain": 0.14} for k in range(ticks)),
        {"at": score - 0.02, "file": effect("impact"), "gain": 0.42},
        {"at": liked - 0.03, "file": effect("switch"), "gain": 0.22},
        {"at": arc - 0.15, "file": effect("whoosh", 6000), "gain": 0.1},
        {"at": starts, "file": effect("blip-01"), "gain": 0.14},
        {"at": climax - 2.6, "file": effect("riser"), "gain": 0.22},
        {"at": climax - 0.02, "file": effect("impact"), "gain": 0.5},
        {"at": story - 0.1, "file": effect("whoosh", 7000), "gain": 0.16},
        {"at": sign - 0.02, "file": effect("buzz"), "gain": 0.2},
        {"at": laptop, "file": effect("tick"), "gain": 0.12},
        {"at": email - 0.2, "file": effect("whoosh", 7000), "gain": 0.2},
        *({"at": ten + (in_it - ten) * k / 9, "file": effect(f"blip-{k + 1:02d}"), "gain": 0.13} for k in range(10)),
        *({"at": each + 0.42 * k, "file": effect("static"), "gain": 0.07} for k in range(int((discover - each) / 0.42) + 1)),
        {"at": dark - 2.6, "file": effect("riser"), "gain": 0.3},
        {"at": dark, "file": effect("switch"), "gain": 0.32},
        {"at": horror - 0.03, "file": effect("impact"), "gain": 0.6},
        {"at": horror - 0.03, "file": effect("subdrop"), "gain": 0.6},
        {"at": look - 0.05, "file": effect("switch"), "gain": 0.2},
        {"at": out, "file": effect("switch"), "gain": 0.3},
        {"at": out + 0.4, "file": effect("chime-dark"), "gain": 0.3},
        {"at": out + 0.75, "file": effect("static"), "gain": 0.1},
    ]
    shakes += [{"at": score, "amount": 8}, {"at": climax, "amount": 14}, {"at": horror, "amount": 22}]
    # the score keeps its own shape; the story is given to it here: room for the title, the verdict and the genre,
    # a swell into the climax and through the story, silence before the last hit, nothing after the lights go out
    title = o["shots"][1]["start"]
    shape = [[0, 1], [title - 0.1, 1], [title + 0.05, 0.45], [title + 1.5, 0.45], [title + 1.8, 1],
             [papers + 2.0, 1], [papers + 2.6, 0.72], [catch + 1.8, 0.72], [catch + 2.4, 1],
             [rate - 0.1, 1], [rate + 0.15, 0.4], [liked - 0.2, 0.4], [liked + 0.2, 1],
             [arc, 1], [climax, 1.45], [gist + 0.3, 1],
             [laptop, 1], [dark - 0.05, 1.6], [dark + 0.05, 0.35], [horror - 0.8, 0.0], [horror - 0.02, 0.0],
             [horror + 0.15, 0.9], [out - 0.05, 0.9], [out + 0.05, 0.0]]
    return {"duration": round(end, 3), "said": said, "shots": shots, "sfx": sfx, "shakes": shakes, "shape": shape}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="all", choices=["test", "all"])
    ap.add_argument("--music", default="")
    a = ap.parse_args()
    words = json.loads((PUBLIC / "words.json").read_text(encoding="utf-8"))
    cuts = json.loads((PUBLIC / "cuts.json").read_text(encoding="utf-8"))["cuts"]
    w = Words(words)
    part = full_plan(w) if a.part == "all" else test_plan(w, cuts)
    music = a.music or ("music/score-b.mp3" if a.part == "all" else "music/taste2.mp3")
    end = part["duration"]
    plan = {
        "width": 1080, "height": 1920, "fps": 30, "duration": end,
        "narrator": {"video": "narrator.mp4", "alpha": "narrator-alpha.webm", "voice": "voice.wav"},
        "cuts": [c for c in cuts if c < end],
        "music": {"file": bed(PUBLIC / music, PUBLIC / "music" / (Path(music).stem + "-bed.wav")), "gain": 0.9,
                  "duck": 0.58, "fadeOut": 1.6, **({"shape": part["shape"]} if part.get("shape") else {})},
        "loudness": "levels.json",
        "grade": {"contrast": 1.12, "saturate": 0.72, "brightness": 0.94, "warmth": 1, "grain": 0.12, "vignette": 0.5},
        "shots": part["shots"],
        "words": [{**x, **({"hot": True} if x["text"].lower().strip(".,!?") in HOT else {})}
                  for x in words if x["t"] < part["said"]],
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
