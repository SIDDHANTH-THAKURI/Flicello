"""Find and download music that is safe to post.

Only Creative Commons licences that allow commercial use and editing are accepted: CC0 (no
conditions) and CC BY (credit the artist — the tool writes the credit line for you). Share-alike,
non-commercial and no-derivatives tracks are filtered out.

Providers (plain REST calls, no extra services):
- Jamendo  — needs a free client id (JAMENDO_CLIENT_ID in .env); best search: tags, tempo, length,
             instrumental-only.
- Openverse — no key needed (rate limited); keyword search over Jamendo, ccMixter, Freesound...
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from pydantic import BaseModel, Field

from ..workspace import write_text_atomic

USER_AGENT = "VidoSuper/0.1 (personal video editor; +https://github.com)"
SAFE_LICENSES = {"cc0", "by"}
SEARCH_CACHE_SECONDS = 24 * 3600


class TrackCandidate(BaseModel):
    provider: str
    id: str
    title: str
    artist: str
    duration: float
    license: str  # cc0 | by
    license_url: str = ""
    attribution: str
    download_url: str
    page_url: str = ""
    tags: list[str] = Field(default_factory=list)
    instrumental: bool | None = None

    @property
    def key(self) -> str:
        return f"{self.provider}-{re.sub(r'[^A-Za-z0-9_-]+', '_', self.id)}"


class MusicQuery(BaseModel):
    keywords: list[str]
    instrumental: bool = True
    min_duration: float = 60.0
    max_duration: float = 420.0
    speed: str | None = None  # Jamendo tempo class: verylow | low | medium | high | veryhigh
    limit: int = 20


def license_from_url(url: str) -> str:
    """'https://creativecommons.org/licenses/by-sa/3.0/' -> 'by-sa'; publicdomain/zero -> 'cc0'."""
    if "publicdomain/zero" in url or "/cc0" in url:
        return "cc0"
    match = re.search(r"/licenses/([a-z-]+)/", url)
    return match.group(1) if match else "unknown"


def _get_json(url: str, cache_dir: Path | None) -> dict:
    cache = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache = cache_dir / (re.sub(r"[^A-Za-z0-9]+", "_", url)[-150:] + ".json")
        if cache.exists() and time.time() - cache.stat().st_mtime < SEARCH_CACHE_SECONDS:
            return json.loads(cache.read_text(encoding="utf-8"))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("Music search is rate limited right now (too many searches); try again in a minute") from exc
        raise RuntimeError(f"Music search failed: HTTP {exc.code} from {urllib.parse.urlsplit(url).netloc}") from exc
    if cache is not None:
        write_text_atomic(cache, json.dumps(data))
    return data


class OpenverseProvider:
    name = "openverse"
    base = "https://api.openverse.org/v1/audio/"

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir

    def search(self, query: MusicQuery) -> list[TrackCandidate]:
        params = {"q": " ".join(query.keywords), "license": ",".join(sorted(SAFE_LICENSES)),
                  "category": "music", "page_size": str(min(query.limit, 20))}
        data = _get_json(self.base + "?" + urllib.parse.urlencode(params), self.cache_dir)
        out = []
        for r in data.get("results", []):
            lic = (r.get("license") or "").lower()
            duration = (r.get("duration") or 0) / 1000.0
            if lic not in SAFE_LICENSES or not r.get("url") or not query.min_duration <= duration <= query.max_duration:
                continue
            tags = [t.get("name", "") for t in (r.get("tags") or [])] + list(r.get("genres") or [])
            out.append(TrackCandidate(
                provider=self.name, id=str(r.get("id")), title=r.get("title") or "Untitled",
                artist=r.get("creator") or "Unknown", duration=duration, license=lic,
                license_url=r.get("license_url") or "", attribution=r.get("attribution") or "",
                download_url=r["url"], page_url=r.get("foreign_landing_url") or "", tags=[t for t in tags if t],
            ))
        return out


class JamendoProvider:
    name = "jamendo"
    base = "https://api.jamendo.com/v3.0/tracks/"

    def __init__(self, client_id: str, cache_dir: Path | None = None):
        self.client_id = client_id
        self.cache_dir = cache_dir

    def search(self, query: MusicQuery) -> list[TrackCandidate]:
        params = {
            "client_id": self.client_id, "format": "json", "limit": str(min(query.limit, 50)),
            "fuzzytags": " ".join(query.keywords), "include": "musicinfo licenses",
            "audioformat": "mp32", "audiodlformat": "mp32", "boost": "popularity_total",
            "durationbetween": f"{int(query.min_duration)}_{int(query.max_duration)}",
        }
        if query.instrumental:
            params["vocalinstrumental"] = "instrumental"
        if query.speed:
            params["speed"] = query.speed
        data = _get_json(self.base + "?" + urllib.parse.urlencode(params), self.cache_dir)
        status = (data.get("headers") or {}).get("status")
        if status not in (None, "success"):
            raise RuntimeError(f"Jamendo said: {(data.get('headers') or {}).get('error_message') or status}")
        out = []
        for r in data.get("results", []):
            lic = license_from_url(r.get("license_ccurl") or "")
            url = r.get("audiodownload") if r.get("audiodownload_allowed") else None
            if lic not in SAFE_LICENSES or not (url or r.get("audio")):
                continue
            info = r.get("musicinfo") or {}
            tag_groups = info.get("tags") or {}
            tags = [t for group in ("genres", "vartags", "instruments") for t in (tag_groups.get(group) or [])]
            version = (re.search(r"/(\d\.\d)/", r.get("license_ccurl") or "") or [None, ""])[1]
            title, artist = r.get("name") or "Untitled", r.get("artist_name") or "Unknown"
            attribution = (f'"{title}" by {artist} (Jamendo) is licensed under CC {lic.upper()} {version}. '
                           f"{r.get('license_ccurl', '')}").strip()
            out.append(TrackCandidate(
                provider=self.name, id=str(r.get("id")), title=title, artist=artist,
                duration=float(r.get("duration") or 0), license=lic, license_url=r.get("license_ccurl") or "",
                attribution=attribution, download_url=url or r["audio"], page_url=r.get("shareurl") or "",
                tags=tags, instrumental=(info.get("vocalinstrumental") == "instrumental") if info else None,
            ))
        return out


def providers_from_env(cache_dir: Path | None, prefer: str = "auto") -> list:
    """Jamendo when a key is configured (better search), Openverse always as the fallback."""
    _load_dotenv()
    client_id = os.environ.get("JAMENDO_CLIENT_ID", "").strip()
    providers = []
    if prefer in ("auto", "jamendo") and client_id:
        providers.append(JamendoProvider(client_id, cache_dir))
    if prefer in ("auto", "openverse") or not providers:
        providers.append(OpenverseProvider(cache_dir))
    return providers


def _load_dotenv() -> None:
    """Read KEY=VALUE lines from the repo's .env (never committed) into the environment."""
    from ..config import REPO_ROOT

    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def download(candidate: TrackCandidate, library: Path) -> Path:
    """Download once into the music library, with a .json sidecar holding licence and credit."""
    library.mkdir(parents=True, exist_ok=True)
    audio = library / f"{candidate.key}.mp3"
    meta = library / f"{candidate.key}.json"
    if not audio.exists():
        part = audio.with_name(audio.stem + ".part.mp3")
        request = urllib.request.Request(candidate.download_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response, part.open("wb") as fh:
            while chunk := response.read(1 << 16):
                fh.write(chunk)
        if part.stat().st_size < 50_000:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"Download of {candidate.title!r} looks broken (too small)")
        os.replace(part, audio)
    write_text_atomic(meta, candidate.model_dump_json(indent=1))
    return audio


def load_track_info(audio: Path) -> TrackCandidate | None:
    meta = audio.with_suffix(".json")
    return TrackCandidate.model_validate_json(meta.read_text(encoding="utf-8")) if meta.exists() else None
