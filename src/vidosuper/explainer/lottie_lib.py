"""A library of designer animations (Lottie) that grows with every video.

When a video needs "two people podcasting" or "an email arriving", the library pulls the free animations for that topic
from LottieFiles (Lottie Simple License: free for commercial use and changes, no credit needed), checks that each one
draws, measures it (size, length, whether it has its own background, whether it is too faint to see) and keeps it. The
brain then looks at numbered previews and picks the one that shows the idea best; the choice is remembered, so the next
video that needs the same thing gets it without asking again.

Everything lives in %LOCALAPPDATA%/VidoSuper/lottie (index.json, <id>.json, previews), shared by every video. Nothing
here is needed for a video to render: without network or animations, the graphics fall back to plain text and emoji.
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import time
import urllib.request
import zipfile
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(os.environ.get("VIDO_LOTTIE_DIR") or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VidoSuper" / "lottie")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
PAGE = "https://lottiefiles.com/free-animations/{topic}"
FILE_URL = re.compile(r"https://assets-v2[.]lottiefiles[.]com/a/([0-9a-f-]{36})/[A-Za-z0-9]+[.]lottie")
STALE_DAYS = 60  # a topic page is pulled again after this long (new animations appear)
MAX_PER_TOPIC = 30
CACHE_BYTES = 48_000_000  # per animation: an unbounded cache of rendered frames ran the PC out of memory
CACHE_FRAME_MAX = 1_200_000  # frames bigger than this (a ~550 px square) are not kept
_LOCK = threading.Lock()

Fetch = Callable[[str], bytes]


def fetch_url(url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed LottieFiles addresses
        return r.read()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def unpack(blob: bytes) -> str | None:
    """The animation JSON inside a .lottie file (a zip), or the blob itself when it already is JSON."""
    if blob[:1] in (b"{", b"["):
        return blob.decode("utf-8", "replace")
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            names = sorted(n for n in z.namelist() if n.startswith("animations/") and n.endswith(".json"))
            return z.read(names[0]).decode("utf-8") if names else None
    except zipfile.BadZipFile:
        return None


class Lot:
    """One animation, drawn at any width; frames are cached."""

    def __init__(self, data: str):
        from rlottie_python import LottieAnimation

        self.anim = LottieAnimation.from_data(data)
        self.n = max(1, self.anim.lottie_animation_get_totalframe())
        self.fps = self.anim.lottie_animation_get_framerate() or 30
        self.w, self.h = self.anim.lottie_animation_get_size()
        self.cache: OrderedDict[tuple[int, int], Image.Image] = OrderedDict()
        self.bytes = 0
        self.box = self._content_box()

    def _content_box(self) -> tuple[float, float, float, float]:
        """Where the drawing really is on its canvas, over the whole animation (fractions: left, top, right, bottom).
        Many animations sit small in a big empty canvas: drawn by canvas size, a checkmark came out as a dot."""
        try:
            boxes = []
            for k in range(8):
                f = int((k + 0.5) / 8 * self.n)
                img = self.anim.render_pillow_frame(frame_num=f, width=160, height=max(8, int(160 * self.h / max(1, self.w))))
                a = np.asarray(img)[..., 3]
                ys, xs = np.nonzero(a > 10)
                if len(xs):
                    boxes.append((xs.min() / a.shape[1], ys.min() / a.shape[0], (xs.max() + 1) / a.shape[1], (ys.max() + 1) / a.shape[0]))
            if not boxes:
                return 0.0, 0.0, 1.0, 1.0
            l, t = min(b[0] for b in boxes), min(b[1] for b in boxes)
            r, b = max(b[2] for b in boxes), max(b[3] for b in boxes)
            pad = 0.04 * max(r - l, b - t)  # a little air, in proportion to the drawing
            l, t, r, b = max(0.0, l - pad), max(0.0, t - pad), min(1.0, r + pad), min(1.0, b + pad)
            if r - l < 0.05 or b - t < 0.05:
                return 0.0, 0.0, 1.0, 1.0
            return l, t, r, b
        except Exception:  # noqa: BLE001 - the whole canvas, as before
            return 0.0, 0.0, 1.0, 1.0

    @property
    def aspect(self) -> float:
        """Height over width of the drawing (not of its canvas)."""
        l, t, r, b = self.box
        return (b - t) * self.h / max(1e-6, (r - l) * self.w)

    def frame(self, t: float, width: int, once: bool = False) -> Image.Image:
        """The drawing at time t, `width` pixels wide, cropped to where it is (see _content_box)."""
        f = int(max(0.0, t) * self.fps)
        f = min(self.n - 1, f) if once else f % self.n
        width = max(8, int(width))
        key = (f, width)
        img = self.cache.get(key)
        if img is not None:
            self.cache.move_to_end(key)
            return img
        l, t_, r, b = self.box
        full_w = min(3200, int(round(width / max(0.05, r - l))))
        full_h = max(8, int(full_w * self.h / max(1, self.w)))
        img = self.anim.render_pillow_frame(frame_num=f, width=full_w, height=full_h)
        if (l, t_, r, b) != (0.0, 0.0, 1.0, 1.0):  # exactly `width` wide, the drawing's height
            x0, y0 = min(int(l * full_w), max(0, full_w - width)), int(t_ * full_h)
            img = img.crop((x0, y0, x0 + width, min(full_h, y0 + max(1, int(round(width * self.aspect))))))
        size = img.width * img.height * 4
        if size <= CACHE_FRAME_MAX:  # small, often repeated frames (tiles, icons); a big main animation is drawn once
            self.cache[key] = img
            self.bytes += size
            while self.bytes > CACHE_BYTES and self.cache:
                _, old = self.cache.popitem(last=False)
                self.bytes -= old.width * old.height * 4
        return img


def measure(data: str) -> dict | None:
    """Size, length and look of an animation; None when it does not draw. Draws it at the sizes videos use, so an
    animation that would crash the drawing engine is found here (run it in a child process: see measure_safe)."""
    try:
        spec = json.loads(data)
        if not isinstance(spec, dict) or not spec.get("layers"):
            return None
        lot = Lot(data)
        if lot.w <= 0 or lot.h <= 0 or lot.n < 2:
            return None
        alphas, corners = [], []
        for f, size in ((0.15, 96), (0.5, 96), (0.85, 96), (0.33, 420), (0.66, 760)):
            img = np.asarray(lot.frame(f * lot.n / lot.fps, size))
            a = img[..., 3].astype(np.float32) / 255.0
            alphas.append(float(a.mean()))
            h, w = a.shape
            k = max(2, min(h, w) // 8)
            corners.append(float(np.mean([a[:k, :k].mean(), a[:k, -k:].mean(), a[-k:, :k].mean(), a[-k:, -k:].mean()])))
    except Exception:  # noqa: BLE001 - an animation rlottie can't draw is just left out
        return None
    lead = 0.0
    try:
        seen = [float(np.asarray(lot.frame(k / lot.fps, 64))[..., 3].mean()) for k in range(0, min(lot.n, int(lot.fps * 1.5)), 2)]
        top = max(seen) if seen else 0.0
        first = next((k for k, v in enumerate(seen) if v >= 0.35 * top), 0)
        lead = round(first * 2 / lot.fps, 3)
    except Exception:  # noqa: BLE001
        pass
    return {"w": lot.w, "h": lot.h, "frames": lot.n, "fps": lot.fps, "seconds": round(lot.n / lot.fps, 2), "lead": lead,
            "filled": min(corners) > 0.85,  # it paints its own background: shown as a card
            "faint": max(alphas) < 0.03}  # almost nothing visible at small sizes


def measure_safe(paths: list[Path], timeout: float = 120.0) -> dict[str, dict | None]:
    """measure() for many files in a child process, so a file that crashes the native drawing engine only loses
    itself: the child reports each result as it goes; after a crash the next file is marked bad and the rest go on."""
    import subprocess
    import sys

    results: dict[str, dict | None] = {}
    todo = [str(p) for p in paths]
    while todo:
        proc = subprocess.run([sys.executable, "-m", "vidosuper.explainer.lottie_lib", *todo], capture_output=True,
                              text=True, timeout=timeout + 2 * len(todo))
        done = 0
        for line in proc.stdout.splitlines():
            if line.startswith("{"):
                row = json.loads(line)
                results[row["path"]] = row["meta"]
                done += 1
        if done < len(todo):
            results[todo[done]] = None  # the one it died on
            done += 1
        todo = todo[done:]
    return results


@dataclass
class Library:
    root: Path | None = None  # default: ROOT (looked up when made, so tests can point it elsewhere)
    fetch: Fetch | None = None  # default: fetch_url
    index: dict = field(default_factory=dict)

    def __post_init__(self):
        self.root = Path(self.root or ROOT)
        self.fetch = self.fetch or (lambda url: fetch_url(url))
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "index.json"
        self.index = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.index.setdefault("items", {})
        self.index.setdefault("topics", {})
        self._lots: dict[str, Lot] = {}

    def save(self) -> None:
        with _LOCK:
            tmp = self.root / "index.json.tmp"
            tmp.write_text(json.dumps(self.index, indent=1), encoding="utf-8")
            tmp.replace(self.root / "index.json")

    # -------------------------------------------------------------- getting animations
    def add(self, aid: str, data: str, topic: str, url: str = "", meta: dict | None = None) -> bool:
        """Keep an animation that drew safely (`meta` from measure_safe; measured here in a child process if not given)."""
        path = self.root / f"{aid}.json"
        path.write_text(data, encoding="utf-8")
        if meta is None:
            meta = measure_safe([path]).get(str(path))
        if meta is None:
            path.unlink(missing_ok=True)
            return False
        item = self.index["items"].setdefault(aid, {"url": url, "topics": [], "chosen_for": []})
        item.update(meta)
        if topic not in item["topics"]:
            item["topics"].append(topic)
        return True

    def pull(self, topic: str) -> list[str]:
        """The animations for a topic, fetched from LottieFiles' free page the first time (or when stale)."""
        topic = slug(topic)
        if not topic:
            return []
        known = self.index["topics"].get(topic)
        if known and time.time() - known.get("pulled", 0) < STALE_DAYS * 86400:
            return [i for i in known["ids"] if i in self.index["items"]]
        try:
            html = self.fetch(PAGE.format(topic=topic)).decode("utf-8", "replace").replace(chr(92) + '"', '"')
        except Exception:  # noqa: BLE001 - offline or the page moved: use what we have
            return [i for i in (known or {}).get("ids", []) if i in self.index["items"]]
        ids, fresh = [], {}
        for m in FILE_URL.finditer(html):
            aid, url = m.group(1), m.group(0)
            if aid in ids or aid in fresh or self.index["items"].get(aid, {}).get("bad"):
                continue
            if aid in self.index["items"]:
                if topic not in self.index["items"][aid]["topics"]:
                    self.index["items"][aid]["topics"].append(topic)
                ids.append(aid)
            else:
                try:
                    data = unpack(self.fetch(url))
                except Exception:  # noqa: BLE001
                    data = None
                if data:
                    path = self.root / f"{aid}.json"
                    path.write_text(data, encoding="utf-8")
                    fresh[aid] = (path, url)
            if len(ids) + len(fresh) >= MAX_PER_TOPIC:
                break
        metas = measure_safe([p for p, _ in fresh.values()]) if fresh else {}
        for aid, (path, url) in fresh.items():
            meta = metas.get(str(path))
            if meta and self.add(aid, path.read_text(encoding="utf-8"), topic, url, meta):
                ids.append(aid)
            else:
                path.unlink(missing_ok=True)
                self.index["items"][aid] = {"bad": True, "url": url, "topics": [topic], "chosen_for": []}
        self.index["topics"][topic] = {"pulled": time.time(), "ids": ids}
        self.save()
        return ids

    def lot(self, aid: str) -> Lot | None:
        if aid not in self._lots:
            path = self.root / f"{aid}.json"
            if not path.exists():
                return None
            try:
                self._lots[aid] = Lot(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return None
        return self._lots[aid]

    def item(self, aid: str) -> dict:
        return self.index["items"].get(aid, {})

    # -------------------------------------------------------------- choosing
    def remembered(self, need: str) -> str | None:
        """An animation already picked for (nearly) the same need."""
        words = set(re.findall(r"[a-z]{3,}", need.lower()))
        best, score = None, 0.0
        for aid, it in self.index["items"].items():
            for old in it.get("chosen_for", []):
                ow = set(re.findall(r"[a-z]{3,}", old.lower()))
                if ow and words:
                    s = len(words & ow) / len(words | ow)
                    if s > score:
                        best, score = aid, s
        return best if score >= 0.6 else None

    def candidates(self, topics: list[str], limit: int = 12) -> list[str]:
        """Usable animations for these topics, best first: ones picked before for something, then page order."""
        out: list[str] = []
        for t in topics:
            for aid in self.pull(t):
                it = self.item(aid)
                if aid not in out and it and not it.get("faint") and not it.get("bad"):
                    out.append(aid)
        out.sort(key=lambda a: -len(self.item(a).get("chosen_for", [])))
        return out[:limit]

    def rejected(self, need: str, cands: list[str]) -> bool:
        """True when all these candidates were turned down for this need before (don't ask again)."""
        before = set(self.index.get("none_fit", {}).get(need.lower().strip(), []))
        return bool(before) and set(cands) <= before

    def reject(self, need: str, cands: list[str]) -> None:
        self.index.setdefault("none_fit", {})[need.lower().strip()] = list(cands)
        self.save()

    def remember(self, aid: str, need: str) -> None:
        it = self.index["items"].get(aid)
        if it is not None and need and need not in it["chosen_for"]:
            it["chosen_for"] = (it["chosen_for"] + [need])[-12:]
            self.save()

    def sheet(self, ids: list[str], path: Path, tile: int = 170) -> Path:
        """Numbered previews (three moments of each animation) on a dark background, for the brain to choose from."""
        cols = 3
        rows = (len(ids) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tile * 3, max(1, rows) * tile), (22, 22, 30))
        for k, aid in enumerate(ids):
            lot = self.lot(aid)
            if lot is None:
                continue
            x0, y0 = (k % cols) * tile * 3, (k // cols) * tile
            for j, f in enumerate((0.2, 0.55, 0.9)):
                img = lot.frame(f * lot.n / lot.fps, int(tile * 0.9) if lot.aspect <= 1 else int(tile * 0.9 / lot.aspect))
                cell = Image.new("RGBA", (tile, tile), (34, 34, 46, 255))
                cell.alpha_composite(img, ((tile - img.width) // 2, (tile - img.height) // 2))
                sheet.paste(cell.convert("RGB"), (x0 + j * tile, y0))
            d = ImageDraw.Draw(sheet)
            d.rectangle([x0, y0, x0 + 30, y0 + 22], fill=(0, 0, 0))
            d.text((x0 + 6, y0 + 5), str(k), fill=(255, 220, 0))
        path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(path, quality=82)
        return path


if __name__ == "__main__":  # measure_safe's child: one JSON line per file, as each is done
    import sys

    for arg in sys.argv[1:]:
        meta = measure(Path(arg).read_text(encoding="utf-8"))
        print(json.dumps({"path": arg, "meta": meta}), flush=True)
