"""Design a template on a song's real bars: the shot kinds and their beat lengths, the moments that fill
them, and the sound design - from a small design file.

A design names a song, a pool of hand-picked moments (clip short id, window, optional action peak) and a
plan: a list of shots, each a kind, a length in beats and a pool key. Kinds:
- hold: a long slow look (0.5x, a push-in); dissolves where the music breathes;
- cut: plain cut, timed on its action when short; shots that start on a bar get a zoom pulse;
- hit: the drop or a second impact - slow motion, a flash and a pulse on the downbeat;
- ramp: rushes in at 2x and sinks into slow motion on the action;
- stutter: rapid jump cuts on one moment (count pieces of `each` beats), a zoom bump on each.
Every window is the steadiest stretch of its moment, or timed on its action peak (the logged peak, else
the busiest frame). Sound design (optional): the holds before the first hit keep their own sound, the
first hit gets its own crash plus an impact and a sub drop, later hits an impact, whip transitions a
whoosh, and the closing holds carry their sound out. Clip sound stops before any transcribed voice.

    {"name": "...", "song": "C:/Music/song.mp3", "music_credit": "...", "intro": {"title": "Can Claude edit?"},
     "pool": {"lighthouse": ["c09", 7.0, 14.0], "boom": ["c40", 43.6, 47.6, 44.5]},
     "plan": [["hold", 8, "lighthouse", {"transition": ["dissolve", 0.5]}], ["stutter", 4, "curl"],
              ["hit", 4, "boom"], ["cut", 1, "fan"], ["ramp", 4, "ledge"]]}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator

from .perceive.prep import transcript_path
from .schemas import Fx, Graphic, MusicSpec, RampPoint, SoundLayer, Transition
from .template import IntroSpec, Slot, Template, _short_ids, _span
from .workspace import Project

KINDS = ("hold", "cut", "hit", "ramp", "stutter")
DEFAULTS = {"hold": (0.5, 0.1, 2), "cut": (1.0, 0.0, 4), "hit": (0.5, 0.0, 5), "ramp": (1.0, 0.0, 4), "stutter": (1.0, 0.0, 5)}
WHIPS = ("smoothleft", "smoothright")


class Moment(BaseModel):
    clip: str  # catalog short id (c09) or full clip id
    start: float
    end: float
    peak: float | None = None  # where the action is; None = find the busiest frame


class Shot(BaseModel):
    kind: Literal["hold", "cut", "hit", "ramp", "stutter"]
    beats: float = Field(..., gt=0, description="Length in beats (for a stutter: of each piece).")
    key: str
    count: int = Field(1, ge=1, description="Stutter pieces.")
    speed: float | None = None
    push: float | None = None
    transition: tuple[str, float] | None = None
    frame: tuple[float, float] | None = Field(None, description="Where the crop sits (fractions), e.g. on the subject in 9:16.")
    graphics: list[Graphic] = Field(default_factory=list, description="Motion graphics drawn into this shot.")
    peak_beat: float | None = Field(None, description=(
        "Put the moment's action peak (the bat meeting the ball, the jump, the splash) exactly on the beat this many beats "
        "into the shot; the peak is refined from the clip's own sound and motion. cut and hold shots only."))
    zoom: float = Field(1.0, ge=1.0, le=2.5, description="A punch-in on the shot (a reused moment looks different).")
    zoom_center: tuple[float, float] = Field((0.5, 0.5), description="Where that punch-in is centred (fractions).")


class Design(BaseModel):
    name: str
    song: str
    music_credit: str | None = None
    look: str = "vivid"
    stabilize: bool = True
    fade_in: float = 0.8
    fade_out: float = 1.0
    music_fade_out: float = 0.6
    intro: IntroSpec | None = None
    sound_design: bool = True
    vertical: bool = Field(False, description="9:16 for Reels.")
    beat_at: float | None = Field(None, description=(
        "Song seconds where a beat certainly is (the opening hit, the drop): the beat grid is shifted to "
        "it when the analysis put the beat on the off-beat."))
    bar_pulses: bool = Field(True, description="A zoom pulse on every cut that starts a bar (off = fewer effects).")
    pool: dict[str, Moment]
    plan: list[Shot]
    sfx: list[tuple[float, str, float]] = Field(default_factory=list, description=(
        "Extra sound effects on the timeline: (seconds, built-in name, gain dB), e.g. pops when characters appear."))

    @model_validator(mode="before")
    @classmethod
    def _compact(cls, data):
        """Accept the short forms: pool entries as [clip, start, end, peak?], shots as [kind, n, key, {...}]."""
        if not isinstance(data, dict):
            return data
        pool = {k: (dict(zip(("clip", "start", "end", "peak"), v)) if isinstance(v, list) else v)
                for k, v in (data.get("pool") or {}).items()}
        plan = []
        for item in data.get("plan") or []:
            if isinstance(item, list):
                kind, n, key, *rest = item
                opts = dict(rest[0]) if rest else {}
                if kind == "stutter":
                    item = {"kind": kind, "count": n, "beats": opts.pop("each", 0.5), "key": key, **opts}
                else:
                    item = {"kind": kind, "beats": n, "key": key, **opts}
            plan.append(item)
        return {**data, "pool": pool, "plan": plan}

    @model_validator(mode="after")
    def _keys(self):
        missing = sorted({s.key for s in self.plan} - set(self.pool))
        if missing:
            raise ValueError(f"plan uses moments not in the pool: {', '.join(missing)}")
        return self


def load_design(path: Path) -> Design:
    return Design.model_validate_json(Path(path).read_text(encoding="utf-8"))


class _Footage:
    """The project's clips, measured signals, sound and transcripts, looked up by short id."""

    def __init__(self, project: Project):
        self.project = project
        self.clips = project.clips()
        self.shorts = _short_ids(project)
        self._signals: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

    def clip(self, short: str):
        return self.clips[self.shorts.get(short, short)]

    def signals(self, short: str):
        if short not in self._signals:
            with np.load(self.project.signals_npz(self.clip(short).clip_id)) as d:
                self._signals[short] = (d["t"], d["shake"], d["motion"])
        return self._signals[short]

    def utterances(self, short: str) -> list[dict]:
        path = transcript_path(self.project, self.clip(short).clip_id)
        return json.loads(path.read_text(encoding="utf-8")).get("utterances") or [] if path.exists() else []

    def steadiest(self, short: str, lo: float, hi: float, need: float) -> float:
        t, shake, _ = self.signals(short)
        starts = np.arange(lo, max(lo, hi - need) + 1e-6, 0.1)

        def worst(s: float) -> float:
            w = (t >= s) & (t <= s + need)
            return float(shake[w].max()) if w.any() else 9.0

        return float(min(starts, key=worst))

    def refine_peak(self, short: str, peak: float, reach: float = 0.8, motion: bool = True) -> tuple[float, str]:
        """The instant of the action, to the frame: the nearest sudden sound (the crack of a bat, a splash, a clap)
        within `reach` seconds of the logged peak, else the busiest frame of motion there, else the logged peak.
        The logged peak comes from contact sheets half a second apart; this is what makes an impact land on a beat."""
        clip = self.clip(short)
        sig = self.project.load_signals(clip.clip_id)
        if sig is not None:
            for found in ([t for t, _ in sig.impacts], sig.audio_peaks):  # the millisecond impacts first, then the coarse ones
                near = [p for p in found if abs(p - peak) <= reach]
                if near:
                    return float(min(near, key=lambda p: abs(p - peak))), "sound"
        if motion:
            t, _, energy = self.signals(short)
            w = (t >= peak - 0.5) & (t <= peak + 0.5)
            if w.any():
                return float(t[w][np.argmax(energy[w])]), "motion"
        return float(peak), "logged"

    def peak_of(self, m: Moment, refine: bool | str = False) -> float | None:
        """The moment's action peak: the logged one, else the busiest frame; refined to the frame when asked
        (True: by sound, else motion; "sound": only when a real sound marks it)."""
        peak = m.peak
        if peak is None:
            t, _, motion = self.signals(m.clip)
            w = (t >= m.start) & (t <= m.end)
            peak = float(t[w][np.argmax(motion[w])]) if w.any() else None
        if refine and peak is not None:
            peak = self.refine_peak(m.clip, peak, motion=refine is True)[0]
        return peak

    def source_in(self, m: Moment, need: float, lead: float | None, refine: bool | str = False) -> float:
        """Where the shot starts: on the action (peak - lead) or the steadiest stretch of the moment."""
        peak = self.peak_of(m, refine) if lead is not None else None
        if lead is not None and peak is not None:
            s = peak - lead
        else:
            s = self.steadiest(m.clip, m.start, max(m.end, m.start + need), need)
        return round(max(0.0, min(s, self.clip(m.clip).duration - need - 0.05)), 2)

    def ambience(self, short: str, at: float, src: float, seconds: float, target_db: float,
                 fade_in: float = 0.5, fade_out: float = 0.6) -> SoundLayer | None:
        """The shot's own sound (real time), levelled to target_db RMS and stopped before any voice."""
        import soundfile as sf

        clip = self.clip(short)
        utter = self.utterances(short)
        if not clip.audio_wav or any(u["start"] <= src < u["end"] for u in utter):
            return None
        stop = min([src + seconds] + [u["start"] - 0.3 for u in utter if u["start"] > src])
        if stop - src < 0.8:
            return None
        x, sr = sf.read(clip.audio_wav, start=int(src * 48000), stop=int(stop * 48000), always_2d=True)
        if not len(x):
            return None
        rms = 20 * np.log10(float(np.sqrt(np.mean(x.mean(1) ** 2))) + 1e-12)
        return SoundLayer(source=f"clip:{short}", at=round(max(0.0, at), 3), duration=round(stop - src, 3),
                          src_in=round(src, 3), gain_db=round(max(-12.0, min(12.0, target_db - rms)), 1),
                          fade_in=fade_in, fade_out=fade_out)


def _expand(plan: list[Shot]) -> list[tuple[Shot, int]]:
    """Stutters become their pieces: (shot, piece index)."""
    return [(shot, rep) for shot in plan for rep in range(shot.count if shot.kind == "stutter" else 1)]


def _sound_design(items: list[tuple[Shot, int]], slots: list[Slot], footage: _Footage) -> list[SoundLayer]:
    out: list[SoundLayer | None] = []
    hits = [i for i, (shot, _) in enumerate(items) if shot.kind == "hit"]
    first_hit = hits[0] if hits else len(items)
    for i, ((shot, _), slot) in enumerate(zip(items, slots)):
        if i < first_hit and shot.kind == "hold":
            out.append(footage.ambience(slot.clip, slot.start - 0.3, slot.src_in, slot.end - slot.start + 0.8, -34.0))
        if shot.kind == "hit":
            if i == first_hit:
                out.append(footage.ambience(slot.clip, slot.start - 0.03, slot.src_in, 2.4, -22.0, 0.02, 0.9))
                out += [SoundLayer(source="sfx:impact", at=round(slot.start - 0.02, 3), duration=1.4, gain_db=-6.0,
                                   fade_in=0.0),
                        SoundLayer(source="sfx:subdrop", at=round(slot.start, 3), duration=1.8, gain_db=-12.0, fade_in=0.0)]
            else:
                out.append(SoundLayer(source="sfx:impact", at=round(slot.start - 0.02, 3), duration=1.4, gain_db=-9.0,
                                      fade_in=0.0))
        if shot.transition and shot.transition[0] in WHIPS and i > 0:
            out.append(SoundLayer(source="sfx:whoosh", at=round(slot.start - 0.35, 3), duration=0.7, gain_db=-9.0,
                                  fade_in=0.0, fade_out=0.1))
    closing = []  # the holds that end the film carry their own sound out
    for (shot, _), slot in zip(reversed(items), reversed(slots)):
        if shot.kind != "hold" or len(closing) == 2:
            break
        closing.insert(0, slot)
    end = slots[-1].end
    for k, slot in enumerate(closing):
        last = k == len(closing) - 1
        out.append(footage.ambience(slot.clip, slot.start - 0.5 if last else slot.start - 0.4, slot.src_in,
                                    (end - slot.start + 0.5) if last else (slot.end - slot.start + 0.8),
                                    -31.0 if last else -33.0, 0.8 if last else 0.6, 1.4 if last else 0.8))
    return [layer for layer in out if layer is not None]


def build_template(design: Design, project: Project) -> Template:
    """The design laid on the song's beat grid from its first downbeat, every window chosen."""
    from .music import load_or_analyze

    song = load_or_analyze(design.song, project.music)
    beats = np.asarray(song.beats, dtype=float)
    period = float(np.median(np.diff(beats)))
    shift = 0.0
    if design.beat_at is not None:  # put a beat exactly where the editor heard one
        shift = (design.beat_at - beats[0]) % period
        shift = shift - period if shift > period / 2 else shift
        start = design.beat_at % period  # the same regular grid, through the heard beat, from the top
        beats = start + period * np.arange(int((song.duration - start) / period) + 1)
        first = int(np.argmin(np.abs(beats - design.beat_at))) % 4
    else:
        first = int(np.argmin(np.abs(beats - song.downbeats[0]))) if song.downbeats else 0

    def at(pos: float) -> float:  # song seconds at a beat position (half beats allowed)
        i = first + pos
        k = int(np.floor(i))
        return float(beats[k] + (i - k) * period) if k < len(beats) else float(beats[-1] + (i - len(beats) + 1) * period)

    footage = _Footage(project)
    items = _expand(design.plan)
    t0, pos, slots = at(0), 0.0, []
    impacts: list[tuple[int, str, float, float]] = []  # slot index, clip, the action's instant in the clip, timeline second
    for n, (shot, rep) in enumerate(items):
        start, end = at(pos) - t0, at(pos + shot.beats) - t0
        seconds = end - start
        speed, push, energy = DEFAULTS[shot.kind]
        tr = shot.transition if rep == 0 else None
        transition = Transition(type=tr[0], duration=tr[1]) if tr else Transition()
        fx, ramp_pts, lead = [], [], None
        if shot.kind == "hit":
            fx = [Fx(type="flash", at=0.0, duration=0.35, strength=0.9), Fx(type="pulse", at=0.0, duration=0.6, strength=1.2)]
            lead = 0.15  # (a real sound at the impact moves it onto the downbeat: see refine below)
        elif shot.kind == "ramp":
            fast = 0.45 * seconds * 2.0  # source seconds played at 2x before slowing
            ramp_pts = [RampPoint(at=0.0, speed=2.0), RampPoint(at=round(fast, 3), speed=2.0),
                        RampPoint(at=round(fast + 0.35, 3), speed=0.4)]
            fx = [Fx(type="pulse", at=round(seconds * 0.55, 3), duration=0.4, strength=0.8)]
            lead = fast + 0.3
        elif shot.kind == "stutter":
            fx = [Fx(type="pulse", at=0.0, duration=0.2, strength=min(2.0, 0.6 + 0.15 * rep))]
            lead = 0.4
        elif shot.kind == "cut":
            lead = 0.15 if shot.beats <= 2 else None
            if design.bar_pulses and abs(pos % 4) < 1e-6 and pos > 0:
                fx = [Fx(type="pulse", at=0.0, duration=0.4, strength=0.8)]
        moment = design.pool[shot.key]
        slot_speed = shot.speed if shot.speed is not None else speed
        synced = shot.peak_beat is not None and shot.kind in ("cut", "hold") and rep == 0
        if synced:  # the action's peak lands on a beat: it's this many on-screen seconds into the shot
            lead = max(0.0, min(shot.peak_beat, shot.beats - 0.25)) * period * slot_speed
        slot = Slot(id=f"s{n + 1:02d}", start=round(start, 3), end=round(end, 3), energy=energy,
                    want=f"{shot.kind} {shot.key}", clip=moment.clip,
                    speed=slot_speed,
                    ramp=ramp_pts, push=shot.push if shot.push is not None else push, fx=fx, transition_in=transition,
                    frame_center=shot.frame or (0.5, 0.5), graphics=shot.graphics if rep == 0 else [],
                    zoom=shot.zoom, zoom_center=shot.zoom_center)
        d_in = transition.duration if n and transition.type != "cut" else 0.0
        nxt = items[n + 1] if n + 1 < len(items) else None
        d_out = nxt[0].transition[1] if nxt and nxt[1] == 0 and nxt[0].transition else 0.0
        need = _span(slot, seconds + d_in / 2 + d_out / 2) + 0.05
        refine = True if synced else ("sound" if shot.kind == "hit" else False)
        if refine == "sound" and (heard := footage.peak_of(moment, "sound")) is not None and heard != footage.peak_of(moment):
            lead = 0.04  # a sound marks the impact: it lands on the downbeat (a frame early, so the flash meets it)
        src = footage.source_in(moment, need, lead, refine=refine)
        if shot.kind == "stutter":  # A A B C ...: repeat, then step on
            src = round(src + max(0, rep - 1) * 0.12, 2)
        slots.append(slot.model_copy(update={"src_in": src}))
        if synced and (peak_t := footage.peak_of(moment, refine=True)) is not None:
            impacts.append((len(slots) - 1, moment.clip, peak_t, start + max(0.0, shot.peak_beat) * period))
        pos += shot.beats
    sounds = _sound_design(items, slots, footage) if design.sound_design else []
    sounds += [SoundLayer(source=f"sfx:{name}", at=round(t, 3), duration=1.8, gain_db=gain, fade_in=0.0, fade_out=0.05)
               for t, name, gain in design.sfx]
    exact = 0
    for idx, short, peak_t, on_beat in impacts:
        slot = slots[idx]
        plays_at = slot.start + (peak_t - slot.src_in) / slot.speed  # where the impact really plays in this shot
        exact += abs(plays_at - on_beat) < 0.03  # a shot kept inside its clip can play it a little off the beat
        if design.sound_design and (layer := footage.ambience(short, plays_at - 0.04, peak_t - 0.04, 0.5, -19.0, 0.01, 0.25)):
            sounds.append(layer)  # the real sound of the impact, with the picture (never over a voice)
    return Template(name=design.name, music=MusicSpec(path=design.song, start=round(t0, 3), fade_out=design.music_fade_out,
                                                      grid_shift=round(shift, 4)),
                    look=design.look, stabilize=design.stabilize, fade_in=design.fade_in, fade_out=design.fade_out,
                    slots=slots, music_credit=design.music_credit, sounds=sounds, intro=design.intro,
                    vertical=design.vertical,
                    idea=f"{exact} of {len(impacts)} action peaks land on a beat" if impacts else "")


def design_summary(template: Template) -> str:
    lens = [s.end - s.start for s in template.slots]
    hits = [s for s in template.slots if s.want.startswith("hit")]
    first = f"; first hit at {hits[0].start:.2f}s" if hits else ""
    return (f"{len(template.slots)} shots, {template.slots[-1].end:.1f}s; shortest {min(lens):.2f}s, longest "
            f"{max(lens):.1f}s, {sum(x < 1 for x in lens)} under 1 s{first}; {len(template.sounds)} sound layers")
