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


VIVID_BASE = ("curves=all='0/0 0.25/0.235 0.5/0.5 0.75/0.775 1/1',"
              "colorbalance=rs=-0.01:bs=0.015:rm=0.01:rh=0.03:gh=0.01:bh=-0.025,"
              "cas=strength=0.15")
LOOKS["vivid"] = "eq=contrast=1.08:saturation=1.2:gamma=0.97," + VIVID_BASE + ",vibrance=intensity=0.2"


def look_filter(name: str | None) -> str:
    if not name or name == "natural":
        return ""
    if name not in LOOKS:
        raise ValueError(f"unknown look {name!r}; choose from {', '.join(LOOKS)}")
    return LOOKS[name]


def vivid_grade(saturation: float, brightness: float, contrast: float) -> str:
    """A summer grade adapted to the shot as measured (means over its source range, 0-255 scale):
    flat, grey, washed-out shots get more saturation, vibrance, contrast and deeper midtones; already
    rich shots get little, so the cut looks like one day in one light."""
    sat_mult = min(1.35, max(1.05, (100.0 / max(saturation, 1.0)) ** 0.35))
    vibrance = min(0.35, max(0.1, (100.0 - saturation) / 200.0))
    con = min(1.15, max(1.05, 1.0 + (55.0 - contrast) / 250.0))
    gamma = 0.94 if brightness > 135 else 0.97 if brightness > 120 else 1.0 if brightness > 100 else 1.05
    return (f"eq=contrast={con:.3f}:saturation={sat_mult:.3f}:gamma={gamma:.2f},{VIVID_BASE},"
            f"vibrance=intensity={vibrance:.2f}")
