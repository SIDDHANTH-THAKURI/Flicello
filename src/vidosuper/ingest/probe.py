"""ffprobe → ClipInfo: orientation, frame rate, HDR, audio, recording time, GPS."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from ..ffmpeg import ffprobe_json
from ..schemas import ClipInfo

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
_ISO6709 = re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)")
_OFFSET_NO_COLON = re.compile(r"^(.*\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2})(\d{2})$")


def _ratio(value: str | None, sep: str = "/") -> float:
    if not value or value in ("0/0", "N/A", "0:1"):
        return 0.0
    if sep in value:
        num, den = value.split(sep, 1)
        try:
            return float(num) / float(den) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _rotation(stream: dict) -> int:
    rotation = None
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            rotation = float(side["rotation"])
            break
    if rotation is None:
        tag = (stream.get("tags") or {}).get("rotate")
        if tag:
            rotation = float(tag)
    return int(round(rotation)) % 360 if rotation is not None else 0


def parse_time(value: str | None) -> datetime | None:
    """ISO-ish timestamps from camera metadata → aware UTC datetime (None if bogus)."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    match = _OFFSET_NO_COLON.match(text)
    if match:
        text = f"{match.group(1)}{match.group(2)}:{match.group(3)}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed.year < 1995:  # 1904/1970 epoch defaults written by some cameras
        return None
    return parsed.astimezone(timezone.utc)


def parse_iso6709(value: str | None) -> tuple[float, float] | None:
    if not value:
        return None
    match = _ISO6709.search(value)
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    if abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
        return None
    return (lat, lon)


def _bit_depth(stream: dict) -> int:
    pix = stream.get("pix_fmt") or ""
    if "12" in pix:
        return 12
    if "10" in pix:
        return 10
    raw = str(stream.get("bits_per_raw_sample") or "")
    return int(raw) if raw.isdigit() else 8


def probe_clip(path: Path, *, clip_id: str, rel_path: str, fp: str) -> ClipInfo:
    data = ffprobe_json(path)
    streams = data.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"
             and not (s.get("disposition") or {}).get("attached_pic")]
    if not video:
        raise ValueError("no video stream")
    v = video[0]
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}
    tags = {k.lower(): val for k, val in (fmt.get("tags") or {}).items()}
    vtags = {k.lower(): val for k, val in (v.get("tags") or {}).items()}

    duration = _ratio(str(fmt.get("duration") or v.get("duration") or 0))
    coded_w, coded_h = int(v.get("width") or 0), int(v.get("height") or 0)
    sar = _ratio(v.get("sample_aspect_ratio"), sep=":") or 1.0
    width, height = int(round(coded_w * sar)), coded_h
    rotation = _rotation(v)
    if rotation in (90, 270):
        width, height = height, width

    avg = _ratio(v.get("avg_frame_rate"))
    nominal = _ratio(v.get("r_frame_rate"))
    fps = avg or nominal
    vfr = bool(avg and nominal and abs(avg - nominal) / nominal > 0.02)
    nb = str(v.get("nb_frames") or "")

    transfer = v.get("color_transfer")
    created, source = None, "file"
    for key, src in (("com.apple.quicktime.creationdate", "apple"), ("creation_time", "metadata")):
        created = parse_time(tags.get(key) or (vtags.get(key) if key == "creation_time" else None))
        if created:
            source = src
            break
    if created is None:
        created = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

    gps = parse_iso6709(tags.get("com.apple.quicktime.location.iso6709") or tags.get("location")
                        or tags.get("location-eng"))
    make = tags.get("com.apple.quicktime.make") or tags.get("make") or tags.get("com.android.manufacturer")
    model = tags.get("com.apple.quicktime.model") or tags.get("model") or tags.get("com.android.model")
    device = " ".join(x for x in (make, model) if x) or None

    return ClipInfo(
        clip_id=clip_id,
        path=str(path),
        rel_path=rel_path,
        fingerprint=fp,
        size_bytes=path.stat().st_size,
        duration=duration,
        width=width,
        height=height,
        coded_width=coded_w,
        coded_height=coded_h,
        rotation=rotation,
        fps=round(fps, 3),
        r_fps=round(nominal, 3),
        vfr=vfr,
        frame_count=int(nb) if nb.isdigit() else None,
        vcodec=v.get("codec_name") or "",
        pix_fmt=v.get("pix_fmt") or "",
        bit_depth=_bit_depth(v),
        color_transfer=transfer,
        color_primaries=v.get("color_primaries"),
        color_space=v.get("color_space"),
        color_range=v.get("color_range"),
        hdr=transfer in HDR_TRANSFERS,
        has_audio=audio is not None,
        audio_codec=audio.get("codec_name") if audio else None,
        audio_channels=int(audio.get("channels") or 0) if audio else 0,
        audio_rate=int(audio.get("sample_rate") or 0) if audio else 0,
        creation_time=created,
        time_source=source,
        gps=gps,
        device=device,
    )
