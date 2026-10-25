"""The watcher without Gemini: Claude looks at a film the way it looks at a scene (on the person's own Claude Code
plan, or with their Anthropic key when that is their brain), from what can be measured on this machine:

- sheets of frames in order, each labelled with its time (two a second for a short film, fewer for long ones);
- what is said, word by word, with its times (Whisper, on this machine);
- where the sound jumps (a hit, a laugh, a splash), from its loudness.

It answers the same questions as the Gemini watcher, in the same form: where something happens (`find_moments`), an
honest viewer's review (`review`), where a thing is in a still (`point_at`, `find_box`) and a drawing (`draw`). The
clips themselves are logged by the contact-sheet analyst, as before (`watches_clips` False)."""

from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from .backends import SpendFn, Usage

WATCH_MODEL = "claude-opus-5-5"  # looking at a whole film: the strongest eyes
QUICK_MODEL = "claude-sonnet-5-5"  # one still, one drawing: quick
TILE_W = 320
COLS, ROWS = 4, 4


class ClaudeWatcher:
    kind = "claude"
    watches_clips = False  # the contact-sheet analyst logs the clips

    def __init__(self, via: str, spend: SpendFn, log_dir: Path | None = None):
        self.via = via  # "claude_code" (their plan) or "api" (their key)
        self.model = WATCH_MODEL
        self.spend = spend
        self.log_dir = log_dir
        self.parallel = 2

    # ---------------------------------------------------------------- asking Claude
    def _ask(self, text: str, images: list[bytes], model: str, task: str) -> str:
        content: list[dict] = []
        for k, jpg in enumerate(images):
            content.append({"type": "text", "text": f"Image {k + 1}:"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.b64encode(jpg).decode("ascii")}})
        content.append({"type": "text", "text": text})
        system = ("You are a sharp, honest video editor's eyes. Answer exactly in the JSON asked for, with nothing "
                  "before or after it.")
        t0 = time.monotonic()
        if self.via == "api":
            import anthropic

            msg = anthropic.Anthropic().messages.create(model=model, max_tokens=4000, system=system,
                                                        messages=[{"role": "user", "content": content}])
            self.spend(Usage.priced(task, model, "api", msg.usage.input_tokens, msg.usage.output_tokens, 0, 0,
                                    time.monotonic() - t0))
            return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        from ...motion import scenes

        return scenes._ask_claude_code(system, content, task, timeout=1200, model=model)

    @staticmethod
    def _json(text: str) -> dict | None:
        m = re.search(r"\{.*\}", text or "", re.S)
        try:
            return json.loads(m.group(0)) if m else None
        except ValueError:
            return None

    # ---------------------------------------------------------------- the film, measured here
    def evidence(self, video: Path) -> tuple[list[bytes], str]:
        """Sheets of its frames (labelled with their times) and what is said and heard, with times."""
        seconds = _duration(video)
        step = 0.5 if seconds <= 60 else 1.0 if seconds <= 180 else max(1.0, seconds / 240)
        frames = _frames(video, step)
        sheets = []
        per = COLS * ROWS
        for k in range(0, len(frames), per):
            sheets.append(_sheet(frames[k:k + per]))
        words = _words(video)
        said = " ".join(f"[{a:.1f}] {w}" for a, _, w in words) or "(nobody speaks)"
        loud = _loud(video)
        heard = ", ".join(f"{t:.1f} s" for t in loud) or "(no sudden sounds)"
        notes = (f"The film is {seconds:.1f} s long. The sheets show a frame every {step:g} s, left to right, top to "
                 f"bottom, each labelled with its time in seconds.\nWhat is said, each word with the second it starts: "
                 f"{said}\nWhere the sound jumps (a hit, a laugh, a splash, a cut in the music): {heard}")
        return sheets, notes

    # ---------------------------------------------------------------- the watcher's questions
    def find_moments(self, video: Path, what: str) -> list[dict]:
        sheets, notes = self.evidence(video)
        reply = self._ask(f"{notes}\n\nFind every stretch of this film where this happens: {what}. Use the frames and "
                          "what is said and heard. Reply with JSON only: {\"moments\": [{\"start\": seconds, \"end\": "
                          "seconds, \"seen\": \"what is there, in a few words\"}]}: tight to the frames that show it, an "
                          "empty list if it never happens. Only real matches.", sheets, self.model, "find")
        found = self._json(reply) or {}
        out = []
        for m in found.get("moments") or []:
            try:
                start, end = float(m["start"]), float(m["end"])
            except (KeyError, TypeError, ValueError):
                continue
            out.append({"start": round(max(0.0, start), 2), "end": round(max(start, end), 2),
                        "seen": str(m.get("seen", ""))[:120]})
        return out[:20]

    def review(self, video: Path, platform: str = "Instagram Reels and TikTok") -> dict | None:
        sheets, notes = self.evidence(video)
        reply = self._ask(f"{notes}\n\nYou are a typical viewer scrolling {platform}. Judge this video honestly, as the "
                          "feed would. Reply with JSON only: {\"stop\": 0-10 (how likely you stop scrolling in its first "
                          "two seconds), \"hold\": 0-10 (how likely you watch to the end), \"leave_at\": the second most "
                          "people would scroll away (its length if they wouldn't), \"best\": the best moment with its "
                          "second, \"weakest\": the weakest with its second, \"why\": what would make it travel further, "
                          "concretely, in one or two sentences}.", sheets, self.model, "review")
        r = self._json(reply)
        if not r:
            return None
        try:
            return {"stop": max(0, min(10, int(r["stop"]))), "hold": max(0, min(10, int(r["hold"]))),
                    "leave_at": round(float(r["leave_at"]), 1), "best": str(r["best"])[:200],
                    "weakest": str(r["weakest"])[:200], "why": str(r["why"])[:400]}
        except (KeyError, TypeError, ValueError):
            return None

    def point_at(self, jpeg: bytes, what: str) -> tuple[float, float] | None:
        box = self.find_box(jpeg, what)
        return (box[0], box[1]) if box else None

    def find_box(self, jpeg: bytes, what: str) -> tuple[float, float, float, float] | None:
        """The box round `what` in a still: centre x, y and width, height as shares of the frame (None: not there).
        The still is shown with a labelled grid, so the edges can be read off it rather than guessed."""
        reply = self._ask(f"Image 1 is a still with a grid over it: lines every tenth of the width and height, "
                          f"labelled 0 to 1000. Find {what} in it. Reply with JSON only: {{\"found\": true or false, "
                          "\"xmin\": 0-1000, \"ymin\": 0-1000, \"xmax\": 0-1000, \"ymax\": 0-1000}: the box tight round "
                          "the whole of it (x from the left, y from the top); found false if it isn't clearly there.",
                          [_gridded(jpeg)], QUICK_MODEL, "point")
        b = self._json(reply) or {}
        try:
            if not b.get("found"):
                return None
            x0, y0, x1, y1 = (max(0.0, min(1000.0, float(b[k]))) for k in ("xmin", "ymin", "xmax", "ymax"))
        except (KeyError, TypeError, ValueError):
            return None
        if x1 <= x0 or y1 <= y0:
            return None
        return ((x0 + x1) / 2000, (y0 + y1) / 2000, (x1 - x0) / 1000, (y1 - y0) / 1000)

    def draw(self, what: str) -> str:
        reply = self._ask(f"Draw {what} as one bold, clean SVG illustration, like a premium animated sticker: "
                          "viewBox=\"0 0 100 100\", the object centred and filling most of the box, no background, no "
                          "text. Use only svg, g, path, circle, ellipse, rect, polygon, defs, linearGradient, "
                          "radialGradient and stop. Shade it: 2 to 4 tones with light from the top left, a soft "
                          "highlight, a slightly darker outline. Recognisable at a glance at small size. Reply with "
                          "{\"svg\": \"<svg ...>...</svg>\"} only.", [], QUICK_MODEL, "draw")
        got = self._json(reply) or {}
        svg = str(got.get("svg") or "")
        if not svg:
            m = re.search(r"<svg.*</svg>", reply or "", re.S)
            svg = m.group(0) if m else ""
        return svg


# ---------------------------------------------------------------- measuring the film here

def _duration(video: Path) -> float:
    import cv2

    cap = cv2.VideoCapture(str(video))
    n, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT), cap.get(cv2.CAP_PROP_FPS) or 30
    cap.release()
    return float(n / fps) if n else 0.0


def _frames(video: Path, step: float) -> list[tuple[float, np.ndarray]]:
    import cv2

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out, t = [], 0.0
    while t < n / fps:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        out.append((round(t, 2), cv2.resize(frame, (TILE_W, int(TILE_W * h / w)), interpolation=cv2.INTER_AREA)))
        t += step
    cap.release()
    return out


def _sheet(frames: list[tuple[float, np.ndarray]]) -> bytes:
    import cv2

    tiles = []
    for t, img in frames:
        img = img.copy()
        cv2.rectangle(img, (0, 0), (84, 26), (0, 0, 0), -1)
        cv2.putText(img, f"{t:.1f}s", (5, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 230, 255), 1, cv2.LINE_AA)
        tiles.append(img)
    th, tw = tiles[0].shape[:2]
    blank = np.zeros((th, tw, 3), np.uint8)
    rows = []
    for r in range(0, len(tiles), COLS):
        row = tiles[r:r + COLS] + [blank] * (COLS - len(tiles[r:r + COLS]))
        rows.append(np.hstack(row))
    ok, jpg = cv2.imencode(".jpg", np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 82])
    return jpg.tobytes()


def _audio16k(video: Path) -> np.ndarray:
    from ...ffmpeg import ffmpeg_bin

    done = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-f",
                           "s16le", "-"], capture_output=True)
    return np.frombuffer(done.stdout, np.int16).astype(np.float32) / 32768.0


def _words(video: Path) -> list[tuple[float, float, str]]:
    import soundfile as sf

    from ...config import load_config
    from ...perceive.transcribe import transcribe

    audio = _audio16k(video)
    if len(audio) < 16000 * 0.5 or float(np.abs(audio).max(initial=0)) < 0.01:
        return []
    cfg = load_config()
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        sf.write(str(wav), audio, 16000)
        t = transcribe("film", wav, cfg.analysis.whisper_model, cfg.models_root / "whisper")
    return [(round(w.start, 2), round(w.end, 2), w.text.strip()) for u in t.utterances if u.confident
            for w in u.words if w.text.strip()]


def _loud(video: Path, top: int = 12) -> list[float]:
    """The moments the sound jumps: onsets well above the film's own level (at most `top`, a second apart)."""
    audio = _audio16k(video)
    hop = 1600  # 0.1 s
    if len(audio) < hop * 10:
        return []
    rms = np.sqrt(np.convolve(audio ** 2, np.ones(hop) / hop, mode="valid")[::hop] + 1e-12)
    rise = np.diff(rms, prepend=rms[0])
    bar = float(np.mean(rise) + 3 * np.std(rise))
    picks: list[float] = []
    for k in np.argsort(-rise):
        if rise[k] <= bar or len(picks) >= top:
            break
        t = round(k * 0.1, 1)
        if all(abs(t - p) >= 1.0 for p in picks):
            picks.append(t)
    return sorted(picks)


def _gridded(jpeg: bytes) -> bytes:
    import cv2

    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return jpeg
    h, w = img.shape[:2]
    for k in range(1, 10):
        x, y = int(w * k / 10), int(h * k / 10)
        cv2.line(img, (x, 0), (x, h), (0, 230, 255), 1, cv2.LINE_AA)
        cv2.line(img, (0, y), (w, y), (0, 230, 255), 1, cv2.LINE_AA)
        cv2.putText(img, str(k * 100), (x + 2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 230, 255), 1, cv2.LINE_AA)
        cv2.putText(img, str(k * 100), (2, y - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 230, 255), 1, cv2.LINE_AA)
    ok, out = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return out.tobytes() if ok else jpeg
