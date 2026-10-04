"""Colour looks as FFmpeg filter chains (applied per shot after framing)."""

from __future__ import annotations

LOOKS: dict[str, str] = {
    "natural": "",
    "warm": "colorbalance=rs=0.05:gs=0.01:bs=-0.05:rm=0.04:bm=-0.04:rh=0.02:bh=-0.02,eq=saturation=1.06",
    "cool": "colorbalance=rs=-0.04:bs=0.05:rm=-0.03:bm=0.04,eq=saturation=0.98",
    "teal-orange": ("colorbalance=rs=-0.07:gs=-0.01:bs=0.07:rm=-0.02:bm=0.02:rh=0.07:gh=0.02:bh=-0.07,"
                    "eq=contrast=1.05:saturation=1.1"),
    "film": "curves=preset=medium_contrast,eq=saturation=0.9,vignette=angle=PI/5,noise=alls=7:allf=t",
    "faded": "curves=all='0/0.07 0.5/0.52 1/0.93',eq=saturation=0.82",
    "punchy": "eq=contrast=1.1:saturation=1.22:gamma=0.98,unsharp=5:5:0.35",
    "mono": "hue=s=0,eq=contrast=1.12",
}


def look_filter(name: str | None) -> str:
    if not name or name == "natural":
        return ""
    if name not in LOOKS:
        raise ValueError(f"unknown look {name!r}; choose from {', '.join(LOOKS)}")
    return LOOKS[name]
