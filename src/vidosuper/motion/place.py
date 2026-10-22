"""Where and when each motion graphic goes on the film's frame:
- clear of the apps' own buttons and captions (the safe zones of 9:16, square and 16:9 frames);
- off the faces in the shots under it (the face index's near faces);
- captions from what is said in the shots that play their own sound, each word at its film time.

The same layers feed the render (vidosuper.motion.render) and the instant preview (the player mounts the same blocks
with the same values), so both put every graphic in the same place.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..edit.timeline import Timeline
from ..schemas import EDL, CaptionWord, MotionGraphic

# The share of the frame kept clear at the top, the bottom and the sides, by frame shape. 9:16 has the apps' buttons
# and captions over it: TikTok keeps 240 px at the top, 660 px at the bottom and 120 px at the right of 1080 x 1920
# clear; Reels 14% at the top and 35% at the bottom; Shorts 288 / 672 px. 16:9 keeps the TV title-safe margin.
SAFE = {"tall": (0.14, 0.35, 0.11), "square": (0.08, 0.16, 0.07), "wide": (0.07, 0.1, 0.06)}
TITLE_SIZE = {"tall": 0.068, "square": 0.09, "wide": 0.11}  # cap height share of the frame's height
CAPTION_SIZE = {"tall": 0.05, "square": 0.055, "wide": 0.06}
NAME_SIZE = {"tall": 0.042, "square": 0.05, "wide": 0.055}
KINETIC_SIZE = {"tall": 0.1, "square": 0.13, "wide": 0.16}
CALLOUT_SIZE = {"tall": 0.045, "square": 0.06, "wide": 0.075}  # the ring's radius
LINE_CHARS = {"tall": 13, "square": 18, "wide": 26}  # a title line longer than this breaks in two
FACE = 0.13  # half the height of a near face, as a share of the frame
CAPTION_GAP = 2.5  # seconds without a word: the captions end and start again (nothing to render in between)


def shape(width: int, height: int) -> str:
    ratio = height / width
    return "tall" if ratio >= 1.4 else "square" if ratio >= 0.9 else "wide"


@dataclass
class Layer:
    graphic: MotionGraphic
    block: str
    start: float
    end: float
    variables: dict

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)

    def public(self, fps: int) -> dict:
        """What the preview's player mounts."""
        return {"block": self.block, "start": round(self.start, 3), "end": round(self.end, 3),
                "vars": {**self.variables, "fps": fps, "duration": self.duration}}


def speech_words(timeline: Timeline, transcripts: dict) -> list[CaptionWord]:
    """What is said in the shots that play their own sound, each word at its film time. A shot's words count only
    while it moves (not in a freeze), slowed or sped shots scale their times, ramped and muted shots have no sound,
    and where a crossfade plays two shots at once the later shot's words wait for the earlier one's."""
    fps = timeline.fps
    found: list[CaptionWord] = []
    for item in timeline.items:
        seg = item.seg
        said = transcripts.get(seg.clip_id)
        if said is None or seg.audio.mode == "mute" or seg.ramp or seg.audio.gain_db <= -18:
            continue
        a = item.start / fps
        b = (item.start + (item.moving_frames or item.frames)) / fps
        for u in said.utterances:
            if not u.confident:
                continue
            for w in u.words:
                if not seg.src_in <= (w.start + w.end) / 2 < seg.src_out:
                    continue
                s = max(a, a + (w.start - seg.src_in) / seg.speed)
                e = min(b, a + (w.end - seg.src_in) / seg.speed)
                text = w.text.strip()
                if text and e - s >= 0.04:
                    found.append(CaptionWord(w=text, s=round(s, 3), e=round(e, 3)))
    found.sort(key=lambda x: x.s)
    words: list[CaptionWord] = []
    for w in found:
        if words and w.s < words[-1].e - 0.02:
            continue
        words.append(w)
    return words


def faces_under(timeline: Timeline, seen: list[dict], start: float, end: float) -> list[float]:
    """The heights (frame shares) of the near faces in the shots under [start, end): a face's height in its clip,
    moved by the shot's punch-in."""
    fps = timeline.fps
    heights = []
    for item in timeline.items:
        a, b = item.start / fps, item.end / fps
        lo, hi = max(a, start), min(b, end)
        if hi - lo <= 0.05:
            continue
        seg = item.seg
        src_lo, src_hi = seg.src_in + (lo - a) * seg.speed, seg.src_in + (hi - a) * seg.speed
        for s in seen:
            if s.get("clip_id") != seg.clip_id or s.get("size") != "near":
                continue
            if min(src_hi, float(s["to"])) - max(src_lo, float(s["from"])) <= 0:
                continue
            y = 0.5 + (float(s["y"]) - seg.zoom_center[1]) * seg.zoom
            if 0.0 <= y <= 1.0:
                heights.append(y)
    return heights


def film_crop(frame, seg, aspect: float):
    """A clip's frame cut to the film's shape (`aspect`, width / height) and punch-in, the way the shot shows it."""
    h, w = frame.shape[:2]
    cw, ch = (h * aspect, h) if w / h > aspect else (w, w / aspect)
    cw, ch = cw / seg.zoom, ch / seg.zoom
    centre = seg.zoom_center if seg.zoom > 1 else seg.frame_center
    cx = min(max(centre[0] * w, cw / 2), w - cw / 2)
    cy = min(max(centre[1] * h, ch / 2), h - ch / 2)
    return frame[int(cy - ch / 2):int(cy + ch / 2), int(cx - cw / 2):int(cx + cw / 2)]


def subject_top(timeline: Timeline, read, start: float, end: float, aspect: float, matte) -> float | None:
    """How far down the frame the top of the person is (a share of its height): the highest of three moments of
    [start, end), from `matte(rgb)` (0..1, the person). None: nobody stands in it."""
    import numpy as np

    fps = timeline.fps
    tops = []
    for f in (0.2, 0.5, 0.8):
        t = start + (end - start) * f
        item = next((i for i in timeline.items if i.start / fps <= t < i.end / fps), None)
        if item is None:
            continue
        seg = item.seg
        frame = read(seg.clip_id, seg.src_in + (t - item.start / fps) * seg.speed)
        if frame is None:
            continue
        m = matte(np.ascontiguousarray(film_crop(frame, seg, aspect)[..., ::-1]))
        w = m.shape[1]
        rows = np.where(m[:, int(0.15 * w):int(0.85 * w)].max(axis=1) > 0.5)[0]
        if rows.size and (m > 0.5).mean() > 0.01:
            tops.append(rows[0] / m.shape[0])
    return min(tops) if tops else None


def backdrop(timeline: Timeline, read, start: float, end: float, box: tuple[float, float, float, float],
             aspect: float) -> float | None:
    """How bright the picture is under a graphic: the mean luminance (0-1) of `box` (x0, y0, x1, y1 as shares of the
    film's frame) at three moments of [start, end). `read(clip_id, seconds)` gives a clip's frame (BGR) or None; each is
    cut to the film's shape (`aspect`, width / height) and punch-in the way the shot shows it."""
    fps = timeline.fps
    found = []
    for f in (0.2, 0.5, 0.8):
        t = start + (end - start) * f
        item = next((i for i in timeline.items if i.start / fps <= t < i.end / fps), None)
        if item is None:
            continue
        seg = item.seg
        moving = (item.moving_frames or item.frames) / fps
        frame = read(seg.clip_id, seg.src_in + min(t - item.start / fps, moving) * seg.speed)
        if frame is None:
            continue
        crop = film_crop(frame, seg, aspect)
        ch, cw = crop.shape[:2]
        part = crop[int(box[1] * ch):max(int(box[1] * ch) + 1, int(box[3] * ch)),
                    int(box[0] * cw):max(int(box[0] * cw) + 1, int(box[2] * cw))]
        if part.size:
            b, g, r = part[..., 0].mean(), part[..., 1].mean(), part[..., 2].mean()
            found.append((0.0722 * b + 0.7152 * g + 0.2126 * r) / 255.0)
    return sum(found) / len(found) if found else None


def scrim_for(brightness: float | None) -> float | None:
    """How dark the pad behind white type should be over a picture this bright: the usual soft pad at night, a deeper
    one over a bright sky (where white type would otherwise sink)."""
    if brightness is None:
        return None
    return round(min(0.5, max(0.22, 0.22 + (brightness - 0.4) * 0.65)), 2)  # past 0.5 it reads as a smudge


def boxes(layer: Layer) -> tuple[float, float, float, float] | None:
    """The part of the frame a title or a name covers (shares of the frame), for measuring what is under it."""
    v = layer.variables
    if layer.block == "title":
        lines = str(v.get("text", "")).count("\n") + 1
        half = v["size"] * (0.55 * lines + (0.6 if v.get("sub") else 0.1))
        return 0.15, max(0.0, v["y"] - half), 0.85, min(1.0, v["y"] + half)
    if layer.block == "lowerthird":
        return v["x"], max(0.0, v["y"] - v["size"] * 1.3), min(1.0, v["x"] + 0.55), min(1.0, v["y"] + v["size"] * 1.3)
    return None


def route_points(n: int, kind: str) -> list[list[float]]:
    """Stops for a route nobody drew: from the lower left up to the upper right of the safe area, zigzagging."""
    top, bottom, side = SAFE[kind]
    y0, y1 = 1 - bottom - 0.08, top + 0.1
    out = []
    for i in range(n):
        t = i / max(1, n - 1)
        swing = (0.1 if i % 2 else -0.1) if 0 < i < n - 1 else 0.0
        out.append([round(0.25 + 0.5 * t + swing, 3), round(y0 + (y1 - y0) * t, 3)])
    return out


def follow(timeline: Timeline, read, start: float, end: float, x: float, y: float, aspect: float,
           rate: float = 10.0) -> list[list[float]]:
    """Where a point of the picture goes as the camera moves: [[film seconds, x, y], ...] in shares of the film's
    frame, from the scene tracker (background features, KLT and RANSAC) run on the clip, cut to the film's shape.
    It follows the shot under `start` to its end (a callout belongs to one shot)."""
    import numpy as np

    from ..graphics.track import SceneTracker, transform_point

    fps = timeline.fps
    item = next((i for i in timeline.items if i.start / fps <= start < i.end / fps), None)
    if item is None:
        return [[round(start, 3), x, y]]
    seg = item.seg
    a = item.start / fps
    stop = min(end, item.end / fps, a + (item.moving_frames or item.frames) / fps)
    tracker, out, size = None, [], None
    t = start
    while t <= stop + 1e-6:
        frame = read(seg.clip_id, seg.src_in + (t - a) * seg.speed)
        if frame is None:
            break
        crop = np.ascontiguousarray(film_crop(frame, seg, aspect)[..., ::-1])
        if tracker is None:
            size = crop.shape[1], crop.shape[0]
            tracker = SceneTracker(*size)
        if (crop.shape[1], crop.shape[0]) != size:
            break
        hmat = tracker.update(crop)
        px, py = transform_point(hmat, x * size[0], y * size[1])  # in the crop's own pixels
        out.append([round(t, 3), round(px / size[0], 4), round(py / size[1], 4)])
        t += 1.0 / rate
    return out or [[round(start, 3), x, y]]


def _clearest(candidates: list[float], faces: list[float], half: float) -> float:
    """The first height whose band misses every face, else the one furthest from the nearest face."""
    for y in candidates:
        if all(abs(f - y) > half + FACE for f in faces):
            return y
    return max(candidates, key=lambda y: min(abs(f - y) for f in faces))


def title_lines(text: str, kind: str) -> str:
    """A long title broken at the space nearest its middle (lines they broke themselves are kept)."""
    text = " ".join(text.split()) if "\n" not in text else text.strip()
    if "\n" in text or len(text) <= LINE_CHARS[kind] or " " not in text:
        return text
    mid = len(text) / 2
    cut = min((i for i, c in enumerate(text) if c == " "), key=lambda i: abs(i - mid))
    return text[:cut] + "\n" + text[cut + 1:]


def layers(edl: EDL, timeline: Timeline, transcripts: dict | None = None, seen: list[dict] | None = None) -> list[Layer]:
    """Every motion graphic of the edit as a block with its values and its stretch of the film."""
    kind = shape(edl.meta.width, edl.meta.height)
    top, bottom, side = SAFE[kind]
    total = timeline.duration
    seen = seen or []
    captioned = [(g.start, g.end) for g in edl.motion if g.kind == "captions"]
    out: list[Layer] = []
    for g in edl.motion:
        start, end = max(0.0, g.start), min(total, g.end)
        if end - start < 0.3:
            continue
        if g.kind == "captions":
            words = [w for w in (g.words or speech_words(timeline, transcripts or {})) if start <= w.s < end]
            size = CAPTION_SIZE[kind]
            half = size * 1.15  # two lines at most
            y = {"top": top + half + 0.01, "middle": 0.5}.get(g.place, 1 - bottom - half - 0.01)
            # a stretch per run of speech: the long quiet parts between have nothing to render
            runs: list[list[CaptionWord]] = []
            for w in words:
                if runs and w.s - runs[-1][-1].e < CAPTION_GAP:
                    runs[-1].append(w)
                else:
                    runs.append([w])
            for run in runs:
                a, b = max(start, run[0].s - 0.2), min(end, total, run[-1].e + 0.8)
                out.append(Layer(g, "captions", round(a, 3), round(b, 3), {**({"font": g.font} if g.font else {}),
                    "words": [{"w": w.w, "s": round(w.s - a, 3), "e": round(min(w.e, b) - a, 3)} for w in run],
                    "style": g.style or "pop", "y": round(y, 3), "size": size, "side": side,
                    "accent": g.accent or "#ffd84d"}))
            continue
        accent = g.accent or "#ffd38a"
        beats = [round(b - start, 3) for b in g.beats if start <= b < end]
        if g.kind == "logo":  # a corner clear of the apps' buttons (top right; a place tag has the top left)
            if g.image:
                out.append(Layer(g, "logo", round(start, 3), round(end, 3), {
                    "image": g.image, "x": round(1 - side - 0.07, 3), "y": round(top + 0.04, 3),
                    "size": 0.13 if kind == "tall" else 0.08}))
            continue
        if g.kind == "transition":  # centred on its cut
            out.append(Layer(g, "transition", round(start, 3), round(end, 3), {
                "style": g.style or "panels", "cut": round((g.start + g.end) / 2 - start, 3),
                **({"colors": [accent, "#14161c", "#f4efe6"]} if g.accent else {})}))
            continue
        if g.kind == "accent":
            out.append(Layer(g, "accent", round(start, 3), round(end, 3),
                             {"style": g.style or "brackets", "beats": beats, "accent": accent}))
            continue
        if g.kind == "route":
            names = g.names or [x.strip() for x in g.text.split("|") if x.strip()]
            points = g.points if len(g.points) >= 2 else route_points(max(2, len(names)), kind)
            out.append(Layer(g, "route", round(start, 3), round(end, 3), {
                "points": [[round(x, 3), round(y, 3)] for x, y in points], "names": names, "accent": accent}))
            continue
        if not g.text.strip():
            continue
        if g.kind == "kinetic":
            out.append(Layer(g, "kinetic", round(start, 3), round(end, 3), {
                **({"font": g.font} if g.font else {}),
                "text": g.text, "beats": beats, "y": 0.45 if kind == "tall" else 0.5, "size": KINETIC_SIZE[kind],
                "side": side, "accent": accent}))
            continue
        if g.kind == "callout":
            track = [[round(t - start, 3), round(x, 4), round(y, 4)] for t, x, y in g.track if start - 0.2 <= t <= end + 0.2]
            if not track:
                x, y = g.points[0] if g.points else (0.5, 0.5)
                track = [[0.0, x, y]]
            out.append(Layer(g, "callout", round(start, 3), round(end, 3), {
                "track": track, "text": g.text, "sub": g.sub, "style": g.style or "circle", "size": CALLOUT_SIZE[kind],
                "accent": accent}))
            continue
        faces = faces_under(timeline, seen, start, end)
        if g.kind == "title":
            text = title_lines(g.text, kind)
            size = TITLE_SIZE[kind]
            lines = text.count("\n") + 1
            half = size * (0.55 * lines + (0.6 if g.sub else 0.1))
            lo, hi = top + half, 1 - bottom - half
            middle = min(max(0.42 if kind == "tall" else 0.45, lo), hi)
            y = {"top": lo, "middle": middle, "bottom": hi}.get(g.place) or _clearest([middle, lo, hi], faces, half)
            if g.behind:  # huge, across the upper frame, where the person's head and shoulders cross it
                text = " ".join(text.split())
                size *= 2.6 if kind == "tall" else 2.0
                y = {"middle": 0.45, "bottom": 0.55}.get(g.place, 0.3 if kind == "tall" else 0.36)
                if g.points:  # just above the person's head (found by the tool), so they cross its letters
                    y = g.points[0][1]
            out.append(Layer(g, "title", round(start, 3), round(end, 3), {
                **({"font": g.font} if g.font else {}),
                "text": text, "sub": g.sub, "bar": bool(g.sub), "style": g.style or "rise", "x": 0.5, "y": round(y, 3),
                "size": size, "side": side, "accent": g.accent or "#ffd38a",
                **({"scrim": g.scrim} if g.scrim is not None else {})}))
        else:
            size = NAME_SIZE[kind]
            y = 1 - bottom - size * 1.6
            over_captions = any(min(end, b) - max(start, a) > 0 for a, b in captioned)
            if g.place == "top" or (kind == "tall" and over_captions):  # a tall frame's tag goes up top-left
                y = top + size * 1.6
            elif over_captions:
                y -= CAPTION_SIZE[kind] * 2.6  # above the captions
            out.append(Layer(g, "lowerthird", round(start, 3), round(end, 3), {
                **({"font": g.font} if g.font else {}),
                "name": g.text, "role": g.sub, "x": round(max(0.06, side * 0.65), 3), "y": round(y, 3), "size": size,
                "side": side, "accent": g.accent or "#ffd38a", **({"scrim": g.scrim} if g.scrim is not None else {})}))
    return out
