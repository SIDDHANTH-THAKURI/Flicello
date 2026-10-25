"""Sound for the motion graphics, as a sound designer lays it: every graphic that moves makes the sound of its move,
under the music, on the frame it happens (render/sfx.py: the pro library, else the synthesized effects).
- a transition: a whoosh peaking on the cut; a flash: a riser into an impact on the cut;
- a title: an impact as a slam lands, a swell under letters rising, a whoosh under a wipe or a split;
- words on the beat: a tick each; a callout: a pop as its ring closes; a route: a long swoosh as it draws;
- viewfinder corners: a shutter; a name: a soft tick."""

from __future__ import annotations

from ..schemas import EDL, SoundLayer

LENGTH = {"whoosh": 0.8, "whoosh_long": 1.6, "riser": 1.5, "impact": 1.2, "swell": 1.5, "tick": 0.5, "pop": 0.5,
          "shutter": 0.5}


def _fx(name: str, at: float, gain: float) -> SoundLayer:
    return SoundLayer(source=f"sfx:{name}", at=round(max(0.0, at), 3), duration=LENGTH[name], gain_db=gain,
                      fade_in=0.0, fade_out=0.08)


def graphic_sounds(edl: EDL) -> list[SoundLayer]:
    out: list[SoundLayer] = []
    for g in edl.motion:
        if g.kind == "transition":
            cut = (g.start + g.end) / 2
            if g.style == "flash":
                out += [_fx("riser", cut - 1.4, -13.0), _fx("impact", cut, -7.0)]
            else:
                out.append(_fx("whoosh", cut - LENGTH["whoosh"] / 2, -10.0))
        elif g.kind == "title":
            style = g.style or "rise"
            if style == "slam":
                out.append(_fx("impact", g.start + 0.08 + 8 / 30, -8.0))
            elif style == "rise":
                out.append(_fx("swell", g.start - 0.4, -15.0))
            else:
                out.append(_fx("whoosh", g.start, -14.0))
        elif g.kind == "kinetic":
            out += [_fx("tick", b, -13.0) for b in g.beats]
        elif g.kind == "callout":
            out.append(_fx("pop", g.start + 0.35, -13.0))
        elif g.kind == "route":
            out.append(_fx("whoosh_long", g.start + 0.15, -16.0))
        elif g.kind == "accent" and g.style == "brackets":
            out.append(_fx("shutter", g.start + 0.05, -14.0))
        elif g.kind == "lowerthird":
            out.append(_fx("tick", g.start + 0.2, -18.0))
        elif g.kind == "thing":  # every landing, pop and burst on the frame it happens, softer as it dies down
            from .place import shape
            from .things import paths

            kind = shape(edl.meta.width, edl.meta.height)
            size = g.size or (0.07 if kind == "tall" else 0.1)
            anchor = tuple(g.points[0]) if g.points else (0.5, 0.3)
            _, hits = paths(g.style or "pop", anchor, g.end - g.start, size, 0.9 if kind == "tall" else 0.93, g.count,
                            g.bounces, seed=int(max(0.0, g.start) * 1000) % 9973)
            for t, strength, what in hits[:24]:
                name = {"bounce": "pop", "burst": "impact", "whoosh": "whoosh", "tick": "tick"}.get(what, "pop")
                out.append(_fx(name, g.start + t - (0.02 if name == "pop" else 0.0), -9.0 - 12.0 * (1 - strength)))
    return out
