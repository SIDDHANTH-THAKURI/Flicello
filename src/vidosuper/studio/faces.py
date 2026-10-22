"""Who is in the footage: faces found, followed and grouped into people, so "cut where my face shows" or "never show
her" is a lookup, not a fresh look at every clip.

- Each clip's proxy is sampled 4 times a second. YuNet finds the faces (with their landmarks), and SFace turns each
  into a 128-number print.
- In one clip, faces in the same place or with the same print join into a track. Across clips, tracks whose prints
  are close (SFace's own same-person threshold) are one person.
- The index (people.json in the film's folder) lists each person with:
  - how long they are on screen;
  - a face still;
  - every appearance: clip, from and to (source seconds), where in the frame, and how near (near or far).
- They can name a person, mark one as themselves, or say never show them. Those choices are kept in the index, so
  every later step reads them.

Models: OpenCV Zoo YuNet and SFace (Apache-2.0 and MIT). They are fetched once into the models folder and checked by
hash before use.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.request
from pathlib import Path

import numpy as np

log = logging.getLogger("vidosuper.studio.faces")

ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
MODELS = {  # name -> (file, url, sha256)
    "detector": ("face_detection_yunet_2023mar.onnx", f"{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
                 "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "prints": ("face_recognition_sface_2021dec.onnx", f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
               "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}
INDEX = "people.json"
VERSION = 1
RATE = 4.0  # frames looked at per second of footage
SCORE = 0.8  # how sure YuNet must be that it is a face
SAME_PERSON = 0.363  # SFace's cosine threshold: at or above, the same person (its authors' value)
TRACK_GAP = 0.8  # seconds a face may go missing and still be the same track
NEAR = 0.07  # a face this big (share of the frame's height) or more is someone close, not a far-off figure


def models_dir() -> Path:
    base = os.environ.get("VIDO_MODELS_DIR") or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VidoSuper" / "models"
    return Path(base) / "faces"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def ensure_models() -> tuple[Path, Path]:
    """The two model files, fetched once from the OpenCV Zoo and checked by hash (a file that doesn't match is
    fetched again, never used)."""
    folder = models_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for name in ("detector", "prints"):
        file, url, sha = MODELS[name]
        path = folder / file
        if not path.exists() or _sha(path) != sha:
            part = path.with_suffix(".part")
            with urllib.request.urlopen(url, timeout=120) as r, open(part, "wb") as f:  # noqa: S310 - fixed https URL
                while block := r.read(1 << 20):
                    f.write(block)
            if _sha(part) != sha:
                part.unlink(missing_ok=True)
                raise RuntimeError(f"the face model {file} did not match its checksum")
            part.replace(path)
        out.append(path)
    return out[0], out[1]


class _Faces:
    """YuNet + SFace, made once per index run (they aren't shared between threads)."""

    def __init__(self):
        import cv2

        det, rec = ensure_models()
        self.cv2 = cv2
        self.det = cv2.FaceDetectorYN.create(str(det), "", (320, 320), SCORE, 0.3, 50)
        self.rec = cv2.FaceRecognizerSF.create(str(rec), "")

    def find(self, frame) -> list[tuple[np.ndarray, np.ndarray]]:
        """(the face row: box, landmarks, score; its print, unit length) for every face in the frame."""
        h, w = frame.shape[:2]
        self.det.setInputSize((w, h))
        _, faces = self.det.detect(frame)
        out = []
        for face in faces if faces is not None else []:
            aligned = self.rec.alignCrop(frame, face)
            vec = self.rec.feature(aligned).flatten().astype(np.float32)
            vec /= max(1e-6, float(np.linalg.norm(vec)))
            out.append((face, vec))
        return out


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1, x2, y2 = max(ax, bx), max(ay, by), min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _tracks(faces: _Faces, video: Path, clip_short: str, clip_id: str, budget: float | None = None) -> list[dict]:
    """Every face track in one clip: when it starts and ends (source seconds), where it is, how big, its print, and
    its best frame (kept as a still)."""
    cv2 = faces.cv2
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    every = max(1, int(round(fps / RATE)))
    tracks: list[dict] = []
    k = 0
    started = time.monotonic()
    try:
        while True:
            ok = cap.grab()
            if not ok:
                break
            if k % every:
                k += 1
                continue
            ok, frame = cap.retrieve()
            t = k / fps
            k += 1
            if not ok:
                continue
            h, w = frame.shape[:2]
            for face, vec in faces.find(frame):
                box = (float(face[0]) / w, float(face[1]) / h, float(face[2]) / w, float(face[3]) / h)
                score = float(face[-1])
                best, best_sim = None, 0.0
                for tr in tracks:
                    if t - tr["to"] > TRACK_GAP:
                        continue
                    sim = float(np.dot(tr["vec"] / max(1e-6, np.linalg.norm(tr["vec"])), vec))
                    if _iou(tr["box"], box) > 0.3 or sim >= SAME_PERSON + 0.1:
                        if sim > best_sim:
                            best, best_sim = tr, sim
                if best is None:
                    best = {"clip": clip_short, "clip_id": clip_id, "from": t, "to": t, "vec": np.zeros_like(vec),
                            "n": 0, "box": box, "size": 0.0, "best": None}
                    tracks.append(best)
                best["to"] = t
                best["vec"] = best["vec"] + vec
                best["n"] += 1
                best["box"] = box
                best["size"] = max(best["size"], box[3])
                area = box[2] * box[3] * score
                if best["best"] is None or area > best["best"][0]:
                    x, y, fw, fh = (int(v) for v in face[:4])
                    m = int(0.35 * max(fw, fh))
                    crop = frame[max(0, y - m):min(h, y + fh + m), max(0, x - m):min(w, x + fw + m)]
                    best["best"] = (area, crop.copy() if crop.size else None, box)
            if budget is not None and time.monotonic() - started > budget:
                break
    finally:
        cap.release()
    out = []
    for tr in tracks:
        if tr["n"] < 2:  # one glimpse is noise more often than a person
            continue
        vec = tr["vec"] / max(1e-6, np.linalg.norm(tr["vec"]))
        out.append({"clip": tr["clip"], "clip_id": tr["clip_id"], "from": round(max(0.0, tr["from"] - 0.5 / RATE), 2),
                    "to": round(tr["to"] + 0.5 / RATE, 2), "size": "near" if tr["size"] >= NEAR else "far",
                    "x": round(tr["box"][0] + tr["box"][2] / 2, 3), "y": round(tr["box"][1] + tr["box"][3] / 2, 3),
                    "vec": vec, "best": tr["best"]})
    return out


def _group(tracks: list[dict]) -> list[list[dict]]:
    """Tracks of the same person together: joined when any two of their prints are the same person (SFace's
    threshold), the largest group first."""
    n = len(tracks)
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    vecs = np.stack([t["vec"] for t in tracks]) if tracks else np.zeros((0, 128))
    sims = vecs @ vecs.T if n else vecs
    for i in range(n):
        for j in range(i + 1, n):
            if sims[i, j] >= SAME_PERSON:
                parent[root(i)] = root(j)
    groups: dict[int, list[dict]] = {}
    for i in range(n):
        groups.setdefault(root(i), []).append(tracks[i])
    return sorted(groups.values(), key=lambda g: -sum(t["to"] - t["from"] for t in g))


def build(proxies: list[tuple[str, str, Path]], stills: Path, out: Path, budget: float | None = None) -> dict:
    """Index the faces of every clip (short id, clip id, proxy) into people.json; names and choices made before are
    kept for the people found again. Returns the index."""
    import cv2

    old = load(out)
    faces = _Faces()
    tracks: list[dict] = []
    for short, clip_id, video in proxies:
        if not video.exists():
            continue
        try:
            tracks += _tracks(faces, video, short, clip_id, budget)
        except Exception:  # noqa: BLE001 - one clip that can't be read never stops the rest
            log.warning("faces: could not read %s", video, exc_info=True)
    people = []
    stills.mkdir(parents=True, exist_ok=True)
    for k, group in enumerate(_group(tracks), start=1):
        vec = sum(t["vec"] for t in group)
        vec = vec / max(1e-6, np.linalg.norm(vec))
        best = max((t["best"] for t in group if t["best"] and t["best"][1] is not None), key=lambda b: b[0], default=None)
        thumb = f"person-{k}.jpg"
        if best is not None:
            face = best[1]
            size = 160
            hh, ww = face.shape[:2]
            scale = size / max(1, max(hh, ww))
            face = cv2.resize(face, (max(1, int(ww * scale)), max(1, int(hh * scale))))
            cv2.imwrite(str(stills / thumb), face, [cv2.IMWRITE_JPEG_QUALITY, 88])
        person = {"id": f"p{k}", "name": "", "me": False, "never": False, "thumb": thumb if best is not None else "",
                  "seconds": round(sum(t["to"] - t["from"] for t in group), 1),
                  "clips": sorted({t["clip"] for t in group}), "near": any(t["size"] == "near" for t in group),
                  "print": [round(float(x), 5) for x in vec],
                  "seen": [{k2: t[k2] for k2 in ("clip", "clip_id", "from", "to", "size", "x", "y")} for t in group]}
        prior = _match(old, vec)
        if prior is not None:  # the same person as before: what they said about them still holds
            person.update(name=prior.get("name", ""), me=bool(prior.get("me")), never=bool(prior.get("never")))
        people.append(person)
    index = {"version": VERSION, "made": round(time.time()), "people": people}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(index), encoding="utf-8")
    tmp.replace(out)
    return index


def _match(old: dict | None, vec: np.ndarray) -> dict | None:
    best, best_sim = None, SAME_PERSON
    for p in (old or {}).get("people") or []:
        if not p.get("print"):
            continue
        sim = float(np.dot(np.asarray(p["print"], dtype=np.float32), vec))
        if sim >= best_sim and (p.get("name") or p.get("me") or p.get("never")):
            best, best_sim = p, sim
    return best


def load(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version") == VERSION else None


def save(path: Path, index: dict) -> None:
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps(index), encoding="utf-8")
    tmp.replace(path)


def label(person: dict) -> str:
    """How a person is called on screen: their name, "You", or "Person 2"."""
    if person.get("me"):
        return "You"
    return person.get("name") or f"Person {person['id'][1:]}"


def shots_with(person: dict, cards: list[dict], near_only: bool = True) -> list[int]:
    """The numbers of a version's shots in which this person is seen (clip and source time overlap; far-off figures
    left out unless asked)."""
    hits = []
    for c in cards:
        start = float(c.get("src") or 0.0)
        span = max(0.1, float(c.get("end", 0)) - float(c.get("start", 0)))
        for s in person["seen"]:
            if near_only and s["size"] != "near":
                continue
            if s["clip"] == c["clip"] and min(start + span, s["to"]) - max(start, s["from"]) > 0.15:
                hits.append(c["n"])
                break
    return hits


def bans(person: dict) -> list[dict]:
    """Every appearance of this person as a scene no plan may show again."""
    return [{"clip": s["clip"], "start": round(max(0.0, s["from"] - 0.2), 2), "end": round(s["to"] + 0.2, 2),
             "what": f"{label(person)} in shot"} for s in person["seen"]]


def appearances(index: dict | None) -> list[dict]:
    """Every face seen, whoever it is (clip, times, size, where in the frame): what graphics keep clear of."""
    return [{k: s[k] for k in ("clip_id", "from", "to", "size", "x", "y") if k in s}
            for p in (index or {}).get("people") or [] for s in p.get("seen") or []]


def public(index: dict | None) -> list[dict]:
    """The people for the page and the editor: no prints."""
    return [{k: v for k, v in p.items() if k != "print"} for p in (index or {}).get("people") or []]
