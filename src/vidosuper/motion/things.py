"""Things: what someone says, made into something that moves on screen ("a small red ball drops from my head" is a red
ball that falls from the top of their head, hits the floor, bounces three times and settles; not those words in type).

- `read(words)` turns their words into a thing: which drawn object (blocks/thing.js has the library; anything else is
  drawn for it as an SVG by the watcher, `clean_svg` keeps only drawing), its colour, and how it moves.
- `paths(...)` works out the motion as a physicist would, frame by frame (gravity, bounces that lose energy, squash on
  each landing, a spin that slows), as key frames for the block and the moments of each hit, so every bounce has its
  sound on the frame it lands. The same numbers drive the instant preview and the render.
"""

from __future__ import annotations

import math
import random
import re

# the drawn library (blocks/thing.js draws each, shaded, in any colour): name -> words that mean it
LIBRARY: dict[str, tuple[str, ...]] = {
    "ball": ("ball", "balls", "sphere", "orb", "marble", "bubble"),
    "star": ("star", "stars"),
    "heart": ("heart", "hearts", "love"),
    "bolt": ("lightning", "bolt", "thunder", "electric", "zap"),
    "flame": ("fire", "flame", "flames", "burning"),
    "cloud": ("cloud", "clouds"),
    "sun": ("sun", "sunshine"),
    "moon": ("moon",),
    "drop": ("water drop", "raindrop", "drop of water", "tear", "droplet", "rain"),
    "coin": ("coin", "coins", "gold coin"),
    "cash": ("cash", "money", "dollar", "dollars", "note", "bill", "bills"),
    "bulb": ("bulb", "light bulb", "idea"),
    "check": ("tick", "check", "checkmark", "correct"),
    "cross": ("cross", "wrong", "x mark"),
    "question": ("question mark", "question"),
    "exclaim": ("exclamation", "exclamation mark", "alert"),
    "arrow": ("arrow", "arrows"),
    "note": ("music note", "musical note", "note of music"),
    "clock": ("clock", "time", "watch"),
    "trophy": ("trophy", "cup", "prize"),
    "crown": ("crown", "king", "queen"),
    "bomb": ("bomb", "explosive"),
    "gift": ("gift", "present", "box"),
    "rocket": ("rocket", "spaceship"),
    "planet": ("planet", "saturn", "world"),
    "sparkle": ("sparkle", "sparkles", "glitter", "shine", "magic"),
    "confetti": ("confetti", "party"),
    "eye": ("eye", "eyes"),
    "smile": ("smiley", "smile", "happy face", "emoji"),
    "cube": ("cube", "block", "dice"),
    "ring": ("ring", "circle", "hoop"),
    "pin": ("pin", "location", "marker"),
}
COLORS = {
    "red": "#e8322d", "crimson": "#c4162a", "orange": "#ff8a1f", "yellow": "#ffd23a", "gold": "#f5b82e",
    "green": "#2fbf5b", "lime": "#9be22d", "teal": "#14b8a6", "cyan": "#22d3ee", "blue": "#2f7cf6",
    "navy": "#1e3a8a", "purple": "#8b5cf6", "violet": "#7c3aed", "pink": "#ff5fa8", "magenta": "#e11d74",
    "white": "#f6f4ef", "black": "#1b1b1f", "grey": "#9aa0a6", "gray": "#9aa0a6", "silver": "#c9ced6",
    "brown": "#8b5a2b",
}
DEFAULT_COLOR = {"ball": "#e8322d", "star": "#ffd23a", "heart": "#ff3b5c", "bolt": "#ffd23a", "flame": "#ff7a1a",
                 "cloud": "#f6f4ef", "sun": "#ffc93a", "moon": "#f3e7b5", "drop": "#3aa6ff", "coin": "#f5b82e",
                 "cash": "#4cb86a", "bulb": "#ffe36b", "check": "#2fbf5b", "cross": "#e8322d", "question": "#8b5cf6",
                 "exclaim": "#ff8a1f", "arrow": "#ffd23a", "note": "#8b5cf6", "clock": "#f6f4ef", "trophy": "#f5b82e",
                 "crown": "#f5b82e", "bomb": "#26262b", "gift": "#e8322d", "rocket": "#e9edf2", "planet": "#ff8a1f",
                 "sparkle": "#fff1a8", "confetti": "#ff5fa8", "eye": "#2f7cf6", "smile": "#ffd23a", "cube": "#2f7cf6",
                 "ring": "#ffd23a", "pin": "#e8322d"}
MOTIONS = {
    "drop": ("drop", "drops", "dropping", "falls", "falling", "fall", "lands", "landing", "bounce", "bounces",
             "bouncing"),
    "pop": ("pop", "pops", "appears", "appear", "shows up", "comes up"),
    "fly": ("flies", "fly", "flying", "zooms", "shoots", "throw", "throws"),
    "float": ("floats", "float", "floating", "hovers", "hover"),
    "orbit": ("orbit", "orbits", "circles", "around my head", "spins around", "goes round"),
    "spin": ("spin", "spins", "spinning", "rotates", "rotating"),
    "shake": ("shake", "shakes", "shaking", "wobble", "vibrates"),
    "burst": ("burst", "bursts", "explodes", "explode", "explosion", "pops out"),
    "rain": ("rain", "raining", "rains", "showers", "shower"),
    "grow": ("grows", "grow", "growing", "gets bigger", "inflates"),
}
SIZES = {"tiny": 0.045, "small": 0.065, "little": 0.065, "medium": 0.09, "big": 0.13, "large": 0.13, "huge": 0.2,
         "giant": 0.24}
FPS = 30
G = 5.2  # gravity, frame heights per second squared: a fall across the frame in about half a second, as it reads
RESTITUTION = {"drop": 0.56, "bounce": 0.68}


def read(words: str) -> dict:
    """Their words as a thing: {shape (a library name, or '' when it has to be drawn), what (the thing in their
    words), color, motion, size, count}."""
    low = " " + " ".join(re.sub(r"[^a-z0-9 ]", " ", (words or "").lower()).split()) + " "
    shape = ""
    best = 0
    for name, said in LIBRARY.items():
        for w in said:
            if f" {w} " in low and len(w) > best:
                shape, best = name, len(w)
    color = next((hexa for name, hexa in COLORS.items() if f" {name} " in low), "")
    motion = ""
    for name, said in MOTIONS.items():
        if any(f" {w} " in low for w in said):
            motion = name
            break
    size = next((v for k, v in SIZES.items() if f" {k} " in low), 0.0)
    m = re.search(r"\b(\d+|two|three|four|five|six|seven|eight|nine|ten|dozen)\s+(?:of\s+)?"
                  r"(?!times\b|bounces\b|seconds\b|beats\b)[a-z]+s\b", low)
    numbers = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
               "dozen": 12}
    count = (int(m.group(1)) if m.group(1).isdigit() else numbers[m.group(1)]) if m else 0
    return {"shape": shape, "color": color or DEFAULT_COLOR.get(shape, "#ffd23a"), "motion": motion, "size": size,
            "count": max(0, min(24, count))}


def bounces_said(words: str) -> int:
    """'bounces three times' -> 3 (0: as physics has it)."""
    m = re.search(r"\b(once|twice|\d+|two|three|four|five)\s*(?:times?)?\b", (words or "").lower())
    if not m or "bounc" not in (words or "").lower():
        return 0
    return {"once": 1, "twice": 2, "two": 2, "three": 3, "four": 4, "five": 5}.get(m.group(1)) or int(m.group(1))


# ---------------------------------------------------------------- the motion, frame by frame

def _key(t, x, y, sx=1.0, sy=1.0, rot=0.0, op=1.0):
    return [round(t, 3), round(x, 4), round(y, 4), round(sx, 3), round(sy, 3), round(rot, 1), round(op, 3)]


def _appear(keys, t0, x, y, rot=0.0):
    """Arrives the way designers make it: from nothing, a little over size, settling (6 frames)."""
    for k, (s, op) in enumerate([(0.0, 0.0), (0.55, 1.0), (1.12, 1.0), (1.03, 1.0), (1.0, 1.0)]):
        keys.append(_key(t0 + k * 1.5 / FPS, x, y, s, s, rot, op))


def _leave(keys, t_end, x, y, rot=0.0):
    keys.append(_key(t_end - 0.24, x, y, 1.0, 1.0, rot, 1.0))
    keys.append(_key(t_end - 0.12, x, y, 1.08, 1.08, rot, 0.9))
    keys.append(_key(t_end, x, y, 0.0, 0.0, rot, 0.0))


def _fall(rng, x, y, floor, size, t0, t_end, e, bounces, kick=0.0, drift=None):
    """A body dropped at (x, y): gravity, landings that squash it and lose energy, a spin that slows, a rest on the
    floor. Returns its key frames and its hits [(seconds, strength 0-1)]."""
    keys: list[list[float]] = []
    hits: list[tuple[float, float]] = []
    r = size / 2
    vy = -kick
    vx = drift if drift is not None else rng.uniform(-0.05, 0.05)
    spin = rng.uniform(140, 260) * (1 if vx >= 0 else -1)
    rot = 0.0
    t = t0
    dt = 1.0 / (FPS * 2)
    landed = 0
    squash_until = -1.0
    resting = False
    first_v = None
    _appear(keys, t, x, y)
    t += 6 / FPS
    step = 0
    while t < t_end - 0.26:
        if not resting:
            vy += G * dt
            y += vy * dt
            x = min(max(x + vx * dt, r), 1 - r)
            rot += spin * dt
            if y >= floor - r:
                y = floor - r
                strength = min(1.0, abs(vy) / (first_v or abs(vy) or 1))
                first_v = first_v or abs(vy)
                hits.append((round(t, 3), round(strength, 3)))
                landed += 1
                vy = -abs(vy) * e
                vx *= 0.7
                spin *= 0.6
                squash_until = t + 0.07
                if (bounces and landed > bounces) or abs(vy) < 0.22:
                    resting = True
                    vy = 0.0
                    if bounces and landed > bounces:  # their count of bounces: the last landing is the rest
                        hits[-1] = (hits[-1][0], hits[-1][1])
        step += 1
        if step % 2 == 0:  # key frames at the film's rate
            if t < squash_until:
                k = (squash_until - t) / 0.07
                sx, sy = 1 + 0.26 * k, 1 - 0.3 * k
                keys.append(_key(t, x, y + r * (1 - sy), sx, sy, rot))
            else:
                keys.append(_key(t, x, y, 1.0, 1.0, rot))
        t += dt
    _leave(keys, t_end, x, y, rot)
    return keys, hits


def paths(motion: str, anchor: tuple[float, float], duration: float, size: float = 0.07, floor: float = 0.9,
          count: int = 0, bounces: int = 0, seed: int = 7) -> tuple[list[dict], list[tuple[float, float, str]]]:
    """Every copy's key frames [t, x, y, scale x, scale y, rotation, opacity] (frame shares, seconds into the
    graphic) and the hits [(seconds, strength, sound)] for the motion named, from `anchor` (where it starts or
    stays)."""
    rng = random.Random(seed)
    x0, y0 = anchor
    end = max(0.8, duration)
    copies: list[dict] = []
    hits: list[tuple[float, float, str]] = []
    motion = motion if motion in MOTIONS or motion == "bounce" else "pop"
    if motion in ("drop", "bounce"):
        n = max(1, count or 1)
        for k in range(n):
            x = min(max(x0 + (k - (n - 1) / 2) * size * 1.3, size), 1 - size)
            e = max(RESTITUTION[motion], 0.66) if bounces else RESTITUTION[motion]  # each bounce they asked for, seen
            keys, h = _fall(rng, x, y0 - size * 0.55, floor, size, k * 0.12, end, e, bounces,
                            kick=0.25 if motion == "bounce" else 0.0)
            copies.append({"keys": keys, "s": 1.0})
            hits += [(t, s, "bounce") for t, s in h]
        return copies, hits
    if motion == "rain":
        n = count or 12
        for k in range(n):
            t0 = rng.uniform(0, max(0.1, end * 0.55))
            x = rng.uniform(0.08, 0.92)
            s = rng.uniform(0.6, 1.0)
            keys, h = _fall(rng, x, -0.08, floor, size * s, t0, end, 0.4, 1, drift=rng.uniform(-0.03, 0.03))
            copies.append({"keys": keys, "s": s})
            hits += [(t, st * 0.5, "tick") for t, st in h[:1]]
        return copies, hits
    if motion == "burst":
        n = count or 12
        for k in range(n):
            a = (k / n) * 2 * math.pi + rng.uniform(-0.2, 0.2)
            speed = rng.uniform(0.35, 0.6)
            vx, vy = math.cos(a) * speed, math.sin(a) * speed * 1.4 - 0.35
            keys = []
            s = rng.uniform(0.45, 0.85)
            t = 0.0
            x, y = x0, y0
            rot = 0.0
            spin = rng.uniform(-400, 400)
            while t <= min(end, 1.6):
                fade = max(0.0, 1 - t / min(end, 1.6))
                keys.append(_key(t, x, y, s * (0.4 + 0.6 * min(1, t / 0.12)), s * (0.4 + 0.6 * min(1, t / 0.12)), rot,
                                 min(1.0, fade * 1.6)))
                vy += G * 0.35 / FPS
                x += vx / FPS
                y += vy / FPS
                rot += spin / FPS
                t += 1 / FPS
            copies.append({"keys": keys, "s": 1.0})
        hits.append((0.02, 1.0, "burst"))
        return copies, hits
    keys: list[list[float]] = []
    if motion == "fly":
        side = -0.18 if x0 >= 0.5 else 1.18
        t = 0.0
        arrive = min(0.7, end * 0.4)
        while t <= arrive:
            p = 1 - (1 - t / arrive) ** 3
            x = side + (x0 - side) * p
            y = y0 - 0.12 * math.sin(math.pi * p)
            keys.append(_key(t, x, y, 1.0, 1.0, (1 - p) * (-35 if side < 0 else 35), min(1.0, t / 0.08)))
            t += 1 / FPS
        hits += [(0.0, 0.6, "whoosh"), (round(arrive, 3), 0.8, "pop")]
        t = arrive
        while t < end - 0.26:
            keys.append(_key(t, x0, y0 + 0.008 * math.sin((t - arrive) * 3.9), 1.0, 1.0, 0.0))
            t += 1 / FPS
        _leave(keys, end, x0, y0)
        return [{"keys": keys, "s": 1.0}], hits
    _appear(keys, 0.0, x0, y0)
    hits.append((0.05, 0.8, "pop"))
    t = 6 / FPS
    while t < end - 0.26:
        u = t - 6 / FPS
        if motion == "orbit":
            a = u * 2 * math.pi / 1.8
            r = max(size * 2.2, 0.15)  # round a head, not on it
            keys.append(_key(t, x0 + r * math.cos(a), y0 + r * 0.32 * math.sin(a), 0.85 + 0.2 * (1 + math.sin(a)) / 2,
                             0.85 + 0.2 * (1 + math.sin(a)) / 2, 0.0))
        elif motion == "spin":
            keys.append(_key(t, x0, y0, 1.0, 1.0, u * 400))
        elif motion == "shake":
            keys.append(_key(t, x0 + 0.012 * math.sin(u * 2 * math.pi * 12) * math.exp(-u * 1.2), y0, 1.0, 1.0,
                             6 * math.sin(u * 2 * math.pi * 12) * math.exp(-u * 1.2)))
        elif motion == "grow":
            p = min(1.0, u / max(0.4, end * 0.7))
            k = 1 + 1.6 * (1 - (1 - p) ** 2)
            keys.append(_key(t, x0, y0, k, k, 0.0))
        else:  # pop / float: it stays, breathing gently
            keys.append(_key(t, x0, y0 + 0.008 * math.sin(u * 3.9), 1.0, 1.0, 2.5 * math.sin(u * 2.1)))
        t += 1 / FPS
    _leave(keys, end, keys[-1][1], keys[-1][2], keys[-1][5])
    return [{"keys": keys, "s": 1.0}], hits


# ---------------------------------------------------------------- a drawing made for it, kept to drawing only

SVG_TAGS = {"svg", "g", "path", "circle", "ellipse", "rect", "polygon", "polyline", "line", "defs", "lineargradient",
            "radialgradient", "stop"}
SVG_ATTRS = {"viewbox", "d", "cx", "cy", "r", "rx", "ry", "x", "y", "x1", "y1", "x2", "y2", "width", "height", "points",
             "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "opacity", "fill-opacity",
             "stroke-opacity", "transform", "id", "offset", "stop-color", "stop-opacity", "gradientunits", "fx", "fy",
             "fill-rule", "xmlns"}


def clean_svg(svg: str) -> str:
    """Only shapes, paths and gradients survive: no scripts, links, images, styles or events (a drawing from a model
    goes into a page)."""
    import xml.etree.ElementTree as ET

    m = re.search(r"<svg[\s\S]*</svg>", svg or "")
    if not m:
        return ""
    try:
        root = ET.fromstring(m.group(0))
    except ET.ParseError:
        return ""

    def tag(el) -> str:
        return el.tag.split("}")[-1].lower()

    def keep(el) -> bool:
        if tag(el) not in SVG_TAGS:
            return False
        for k in list(el.attrib):
            name = k.split("}")[-1].lower()
            v = el.attrib[k]
            if name not in SVG_ATTRS or re.search(r"url\((?!#)|javascript|expression|data:", v, re.I):
                del el.attrib[k]
        for child in list(el):
            if not keep(child):
                el.remove(child)
        return True

    if not keep(root) or tag(root) != "svg":
        return ""
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    out = ET.tostring(root, encoding="unicode")
    return out if len(out) <= 20000 else ""
