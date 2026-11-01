"""Ready-made things for 3D scenes: Poly Haven's library (CC0: free for any use, no account, no credit needed),
searched by words. Models (props, furniture, nature, rocks, buildings' parts) and HDRIs (real skies and places that
light a scene). The 3D desk lists them with `bash assets <words>`; its scene.py loads one with `k.model(...)` /
`k.hdri(...)` (blender_kit.py downloads it once into the models folder).

    python -m vidosuper.motion.assets find lighthouse rocks      what there is, best first
    python -m vidosuper.motion.assets find --hdri sunset beach   skies and places"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

API = "https://api.polyhaven.com"
AGENT = "Flicello/1.0 (open-source video studio; github.com/SIDDHANTH-THAKURI/Flicello)"
KEEP_DAYS = 7  # the catalogue is fetched again after this long


def cache() -> Path:
    base = os.environ.get("VIDO_MODELS_DIR") or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VidoSuper" / "models"
    out = Path(base) / "polyhaven"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _get(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - a fixed https host
        return r.read()


def catalogue(kind: str = "models") -> dict:
    """Every asset of a kind ("models" or "hdris"): id -> its name, tags, categories, size. Kept for a week."""
    path = cache() / f"{kind}.json"
    if path.exists() and time.time() - path.stat().st_mtime < KEEP_DAYS * 86400:
        return json.loads(path.read_text(encoding="utf-8"))
    data = json.loads(_get(f"{API}/assets?t={kind}"))
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1]


def _stems(text: str) -> set[str]:
    return {t.rstrip("s") for t in _words(text)}


def find(words: str, kind: str = "models", limit: int = 12) -> list[dict]:
    """The assets that best match the words: each word found in the name counts most, then tags and categories."""
    want = _words(words)
    if not want:
        return []
    out = []
    for aid, a in catalogue(kind).items():
        name = _stems(a.get("name", "") + " " + aid.replace("_", " "))
        tags = _stems(" ".join(a.get("tags", [])))
        cats = _stems(" ".join(a.get("categories", [])))
        score = 0.0
        for w in want:  # whole words, plural or not ("rock" is not "rocking chair")
            stem = w.rstrip("s")
            score += 3 if stem in name else 2 if stem in tags else 1 if stem in cats else 0
        if score:
            dims = a.get("dimensions") or []  # millimetres
            out.append({"id": aid, "name": a.get("name", aid), "score": score,
                        "size_m": [round(d / 1000, 2) for d in dims][:3] if dims else None,
                        "tags": a.get("tags", [])[:8], "polycount": a.get("polycount")})
    out.sort(key=lambda x: (-x["score"], x.get("polycount") or 0))
    return out[:limit]


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "find":
        print(__doc__)
        return 2
    args = argv[1:]
    kind = "hdris" if "--hdri" in args else "models"
    words = " ".join(a for a in args if a != "--hdri")
    try:
        hits = find(words, kind)
    except OSError as exc:
        print(f"The library can't be reached right now ({exc}). Build it yourself from the kit's shapes.")
        return 1
    if not hits:
        print(f'Nothing in the library matches "{words}". Try other words (what it is made of, where it is), '
              "or build it yourself from the kit's shapes.")
        return 0
    for h in hits:
        size = f"{h['size_m'][0]} x {h['size_m'][1]} x {h['size_m'][2]} m" if h.get("size_m") else ""
        print(f"{h['id']:<32} {h['name']:<34} {size:<22} {', '.join(h['tags'])}")
    load = "k.hdri(\"<id>\")" if kind == "hdris" else "k.model(\"<id>\", height=<metres in the scene>, loc=(x, y, z))"
    print(f"\nLoad one in scene.py: {load}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
