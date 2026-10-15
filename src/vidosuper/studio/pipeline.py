"""One studio project from uploaded clips to finished videos: the stages, with no web and no database.

    analyse  ingest + measure + transcripts + contact sheets, the brain logs every clip, the catalog, the song
    pitch    three story ideas (or one more), with stills from the footage
    design   the chosen story as a shot plan on the song's beats (checked), saved as a Design
    sample   ~8 s of the cut at draft quality: the run-up to and through the drop, or the opening
    render   the finished video in each shape, then the automatic checks
    revise   the person's fixes applied to the plan, then render again

The worker (worker.py) runs these as jobs and records progress; `vido studio run` runs them in a row
from the command line (to measure time and cost per video).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..config import Config, load_config
from ..design import Design, build_template
from ..edit import validate_edl
from ..ingest import ingest_folder
from ..music import load_or_analyze
from ..music.analysis import MusicAnalysis
from ..perceive import measure_project
from ..perceive.catalog import build_catalog, check_analysis as catalog_check
from ..perceive.prep import analysis_path, load_transcript, prepare_perception
from ..qa import run_checks
from ..render import render_edl
from ..schemas import ClipAnalysis, Moment, Transition
from ..template import Template, autofill
from ..workspace import Project, write_text_atomic
from .brain.context import HITS_WORDS, beat_map, clip_facts, load_catalog, moment_by_id
from .brain.core import StoryContext, brief_text, to_design
from .brain.schemas import DesignPlan, StoryPitch
from .brain.strikes import Hit, plan_on_strikes

class UserError(ValueError):
    """A problem the person can fix, in words made for them. Anything else is ours to fix and is never shown raw."""


Progress = Callable[[float, str], None]
SHAPES = {"9:16": (1080, 1920), "16:9": (1920, 1080)}
SAMPLE_SECONDS = 8.0
OWNER_ONLY_CHECKS = ("content",)  # the companion / car words are the owner's own rules


def studio_config(workspace: Path, render_preset: str = "medium", models_dir: Path | None = None) -> Config:
    """The pipeline's settings for studio projects: their own workspace, a quicker final encode."""
    # fewer FFmpeg jobs at once than the command line uses (3/4/2): a 4K phone clip decodes into a lot of memory,
    # and the studio shares a PC with the owner's other programs
    overrides: dict = {"workspace": str(workspace), "render": {"workers": 2, "final": {"preset": render_preset}},
                       "measure": {"workers": 2}, "ingest": {"workers": 1}, "output": {"outro": "none"}}
    if models_dir is not None:
        overrides["analysis"] = {"models_dir": str(models_dir)}
    if os.name != "nt" or os.environ.get("VIDO_STUDIO_HWACCEL") == "none":
        # a container lists QuickSync in FFmpeg's build but has no GPU: every shot would fail on it first
        overrides["ingest"]["hwaccel"] = "none"
    return load_config(overrides)


@dataclass
class Workdir:
    """A studio project's own folder: the uploads, the results, and the brain's notes. `clips_dir` points
    the command line at a footage folder it only reads (the studio itself keeps uploads in `root/clips`)."""

    root: Path
    clips_dir: Path | None = None

    @property
    def clips(self) -> Path:
        return self.clips_dir or self.root / "clips"

    @property
    def music(self) -> Path:
        return self.root / "music"

    @property
    def out(self) -> Path:
        return self.root / "out"

    @property
    def stills(self) -> Path:
        return self.root / "stills"

    @property
    def brain(self) -> Path:
        return self.root / "brain"

    def ensure(self) -> "Workdir":
        for p in (self.music, self.out, self.stills, self.brain):
            p.mkdir(parents=True, exist_ok=True)
        if self.clips_dir is None:
            self.clips.mkdir(parents=True, exist_ok=True)
        return self

    def song_path(self) -> Path | None:
        songs = sorted(self.music.glob("song.*"))
        return songs[0] if songs else None


@dataclass
class RenderInfo:
    shape: str
    path: str
    seconds: float
    render_seconds: float
    findings: list[dict] = field(default_factory=list)

    @property
    def failed(self) -> list[dict]:
        return [f for f in self.findings if f["level"] == "fail"]


class Pipeline:
    def __init__(self, work: Workdir, cfg: Config, brain, progress: Progress | None = None, owner: bool = False):
        self.work = work.ensure()
        self.cfg = cfg
        self.brain = brain
        self.progress = progress or (lambda f, m: None)
        self.owner = owner
        self.no_music = False  # set by the studio when the person chose no music
        self.project = Project(work.clips, cfg)

    # ------------------------------------------------------------ analyse
    def analyse(self, length: float = 30.0, pace: str = "balanced") -> dict:
        """Everything the story steps need; finished parts are kept, so a retry only redoes what failed."""
        t0 = time.monotonic()
        self.progress(0.02, "Looking at your clips")
        project, clips = ingest_folder(self.work.clips, self.cfg)
        usable = [c for c in clips if c.usable]
        if not usable:
            raise UserError("None of the uploaded files could be read as video.")
        self.progress(0.15, "Measuring shake, light and motion")
        measure_project(project, self.cfg)
        self.progress(0.3, "Listening for speech and making contact sheets")
        tasks = prepare_perception(project, self.cfg)
        by_id = {c.clip_id: c for c in usable}
        todo = [t for t in tasks["clips"] if catalog_check(project, by_id[t["clip_id"]])[1]]
        done = [0]

        def watch(task: dict) -> None:
            clip = by_id[task["clip_id"]]
            analysis = self.brain.analyse_clip(clip, brief_text(Path(task["brief"])), [Path(s) for s in task["sheets"]])
            write_text_atomic(analysis_path(project, clip.clip_id), analysis.model_dump_json(indent=1))
            done[0] += 1
            self.progress(0.35 + 0.5 * done[0] / len(todo), f"Watched {done[0]} of {len(todo)} clips")

        if todo:
            self.progress(0.35, f"Watching {len(todo)} clip{'s' if len(todo) > 1 else ''}")
            with ThreadPoolExecutor(max_workers=max(1, getattr(self.brain, "parallel", 1))) as pool:
                list(pool.map(watch, todo))  # the first failure (or a spending cap) stops the step
        self.progress(0.86, "Listening for hits")
        self.enrich_with_impacts(project, usable)
        self.progress(0.88, "Making the catalog")
        catalog = build_catalog(project)
        self.progress(0.93, "Listening to the song")
        self.ensure_song(length, pace)
        song = self.song()
        skipped = [c.rel_path for c in clips if not c.usable]
        return {"clips": len(usable), "skipped": skipped, "footage_seconds": round(sum(c.duration for c in usable), 1),
                "notes": footage_notes(catalog),
                "analysed": sum(1 for e in catalog["clips"] if e["analysis"]), "song_seconds": round(song.duration, 1),
                "tempo": round(song.tempo, 1), "seconds": round(time.monotonic() - t0, 1)}

    def enrich_with_impacts(self, project: Project, clips: list) -> int:
        """Every real hit sound in a clip (a bat meeting a ball, a clap, a splash: found to the millisecond from its own
        audio) becomes a moment that is centred on it, or snaps the peak of the moment that already covers it. The
        picture-only log can't tell where the hit really is (its frames are half a second apart); the sound can."""
        added = 0
        for clip in clips:
            path = analysis_path(project, clip.clip_id)
            sig = project.load_signals(clip.clip_id)
            if not path.exists() or sig is None or not sig.impacts:
                continue
            analysis = ClipAnalysis.model_validate_json(path.read_text(encoding="utf-8"))
            speech = [(u.start - 0.1, u.end + 0.1) for u in (getattr(load_transcript(project, clip.clip_id), "utterances", None) or [])]
            changed = False
            for t, _db in sig.impacts:
                if any(a <= t <= b for a, b in speech):  # a hard consonant, not a hit
                    continue
                cover = next((m for m in analysis.moments if m.start - 0.05 <= t <= m.end + 0.05), None)
                if cover is not None:
                    if cover.peak is None or abs(cover.peak - t) > 0.12:
                        cover.peak = round(min(cover.end, max(cover.start, t)), 3)
                        changed = True
                    continue
                analysis.moments.append(Moment(
                    start=round(max(0.0, t - 1.4), 2), end=round(min(clip.duration, t + 1.0), 2), peak=round(t, 3),
                    kind="action", description=f"A sharp hit sound at {t:.1f} s (found from the clip's own audio)", hook=7,
                    strength=6, best_use="highlight", sound="keep", notes="found from the sound"))
                added += 1
                changed = True
            if changed:
                analysis.moments.sort(key=lambda m: m.start)
                write_text_atomic(path, analysis.model_dump_json(indent=1))
        return added

    def song(self) -> MusicAnalysis:
        path = self.work.song_path()
        if path is None:
            raise UserError("No song yet: upload one first.")
        return load_or_analyze(path, self.project.music)

    def ensure_song(self, length: float, pace: str) -> bool:
        """No music uploaded: make a beat to cut on (see music/house.py). True when it made one."""
        if self.work.song_path() is not None:
            return False
        from ..music.house import make_beat

        self.progress(0.02, "Making a beat to cut on")
        make_beat(self.work.music / "song.wav", length + 8, pace)
        return True

    def context(self, shapes: list[str], length: float, intent: str, rules: list[dict], pace: str = "balanced") -> StoryContext:
        self.ensure_song(length, pace)
        catalog = load_catalog(self.project.root)
        song = self.song()
        ctx = StoryContext(catalog=catalog, facts=clip_facts(catalog), song=song, beats=beat_map(song, length),
                           shapes=shapes, intent=intent, rules=rules, pace=pace)
        good = sum(1 for f in ctx.facts.values() for m in f.moments if m["strength"] >= 4)
        ctx.allow_reuse = good < -(-ctx.beats.target // 4)  # fewer good moments than a shot every 4 beats: reuse them
        ctx.reuse_if_asked(intent, *[r["text"] for r in rules])
        return ctx

    # ------------------------------------------------------------ pitch
    def pitch(self, ctx: StoryContext, n: int = 3, rejected: list[StoryPitch] | None = None, reason: str = "",
              own_idea: str = "", tag: str = "r1") -> list[dict]:
        self.progress(0.1, "Thinking up story ideas")
        pitches = self.brain.pitch(ctx, n=n, rejected=rejected, reason=reason, own_idea=own_idea)
        self.progress(0.8, "Picking stills")
        out = []
        for i, p in enumerate(pitches):
            stills = [self.still(ctx, mid, f"{tag}-{i + 1}-{k + 1}") for k, mid in enumerate(p.stills[:4])]
            out.append({"pitch": p.model_dump(), "stills": [s for s in stills if s]})
        return out

    def still(self, ctx: StoryContext, moment_id: str, name: str) -> str | None:
        """A frame of a moment (its peak, else its middle), as a small JPEG for the pitch cards."""
        m = moment_by_id(ctx.facts, moment_id)
        if m is None:
            return None
        clip = self.project.clips()[ctx.facts[moment_id.split("#")[0]].clip_id]
        t = m.get("peak") if m.get("peak") is not None else (m["start"] + m["end"]) / 2
        cap = cv2.VideoCapture(str(clip.proxy or clip.path))
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, float(t) * 1000)
            ok, frame = cap.read()
        finally:
            cap.release()
        if not ok:
            return None
        h, w = frame.shape[:2]
        scale = 480 / max(h, w)
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        path = self.work.stills / f"{name}.jpg"
        cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return path.name

    # ------------------------------------------------------------ design, sample, render
    def design(self, ctx: StoryContext, pitch: StoryPitch, note: str = "") -> DesignPlan:
        self.progress(0.05, "Planning the cut on the song's beats")
        if ctx.pace == "every":
            if note.strip():  # "not quite": another take of the same idea, shaped by what they said
                return self.revise_strikes(ctx, pitch, note, 1 + (len(note) % 7))
            return self.strike_plan(ctx, pitch)
        return self.brain.design(ctx, pitch, note)

    def strike_plan(self, ctx: StoryContext, pitch: StoryPitch, seed: int = 0, density: str = "strikes",
                    max_zoom: float = 1.3) -> DesignPlan:
        """The one-shot-per-hit plan (see brain/strikes.py): made from the song's hits and the footage's motion and
        real impacts, no AI call."""
        def signals(short: str):
            with np.load(self.project.signals_npz(ctx.facts[short].clip_id)) as d:
                return d["t"], d["shake"], d["motion"]

        hits = []
        for short, f in ctx.facts.items():
            sig = self.project.load_signals(f.clip_id)
            hits += [Hit(short, float(t), float(db)) for t, db in (sig.impacts if sig else [])]
        hit_led = len(hits) >= 2 and bool(HITS_WORDS.search(ctx.intent or ""))  # they asked for the hits on the beats
        return plan_on_strikes(ctx.facts, signals, ctx.beats, hits, pitch.title, pitch.logline, seed, density, max_zoom,
                               hit_led)

    def revise_strikes(self, ctx: StoryContext, pitch: StoryPitch, feedback: str, round_no: int = 1) -> DesignPlan:
        """A new cut of the same idea: other stretches of footage, and the cut density follows what they said."""
        text = feedback.lower()
        slower = bool(re.search(r"too (fast|busy|many|frantic|quick)|slow(er)? down|calmer|fewer cuts", text))
        density = "beats" if slower else "strikes"
        tight = bool(re.search(r"zoom|crop|too close|can'?t see|wider|show more", text))
        zoom = 1.08 if tight else 1.3
        plan = self.strike_plan(ctx, pitch, seed=round_no, density=density, max_zoom=zoom)
        did = ["Picked other stretches of your footage for the shots"]
        did.append("Cuts on the beats only, not every strike (calmer)" if slower else "Still one shot for every hit of the music")
        if tight:
            did.append("Shots kept wide: zoom limited to 1.08x so the whole action shows")
        return plan.model_copy(update={"notes": "; ".join(did) + ". " + plan.notes})

    def revise(self, ctx: StoryContext, pitch: StoryPitch, current: DesignPlan, feedback: str) -> DesignPlan:
        self.progress(0.05, "Working your changes into the plan")
        if ctx.pace == "every":
            return self.revise_strikes(ctx, pitch, feedback, 1 + len(current.plan) % 7)
        return self.brain.revise(ctx, pitch, current, feedback)

    def _template(self, ctx: StoryContext, plan: DesignPlan, shape: str, look: str) -> tuple[Design, Template]:
        design = to_design(plan, ctx, str(self.work.song_path()), vertical=shape == "9:16", look=look)
        return design, heal_template(build_template(design, self.project))

    def edl_for(self, template: Template, width: int, height: int):
        """The EDL for a template that the renderer will accept: a shot the renderer finds too short on screen (frames
        taken by its transitions, rounding at the cut) is folded into its neighbour and the EDL rebuilt, until none is.
        The person never hears about it."""
        for _ in range(8):
            edl = autofill(template, self.project, width=width, height=height)
            if self.no_music:  # the cut still follows the (unheard) beat grid; nothing plays under it
                edl.music = None
            clips = self.project.clips()
            signals = {cid: s for cid in clips if (s := self.project.load_signals(cid)) is not None}
            short = {i.segment for i in validate_edl(edl, clips, signals, repair=False)
                     if i.level == "error" and "shot is only" in i.message and i.segment}
            if not short or len(template.slots) <= 1:
                break
            template = fold_slots(template, short)
        return edl, template

    def sample(self, ctx: StoryContext, plan: DesignPlan, shape: str, name: str, look: str = "vivid") -> RenderInfo:
        """About 8 seconds at draft quality: through the drop if there is one early enough, else the opening."""
        self.progress(0.3, "Cutting a short sample")
        _, template = self._template(ctx, plan, shape, look)
        hits = [s for s in template.slots if s.want.startswith("hit")]
        start = 0.0
        if hits and hits[0].start <= 25.0:
            start = max(0.0, hits[0].start - SAMPLE_SECONDS * 0.55)
        part = heal_template(slice_template(template, start, start + SAMPLE_SECONDS))
        w, h = SHAPES[shape]
        edl, _ = self.edl_for(part, w, h)
        edl.meta.true_peak_db = -2.3  # the AAC encode overshoots the limiter by up to ~0.9 dB
        t0 = time.monotonic()
        result = render_edl(edl, self.project, self.cfg, quality="draft", out_path=self.work.out / f"{name}.mp4")
        self.progress(0.95, "Sample ready")
        return RenderInfo(shape, Path(result.output).name, round(result.duration, 2), round(time.monotonic() - t0, 1))

    def render(self, ctx: StoryContext, plan: DesignPlan, shapes: list[str], version: int, look: str = "vivid") -> list[RenderInfo]:
        out = []
        for k, shape in enumerate(shapes):
            base, span = k / len(shapes), 1.0 / len(shapes)
            self.progress(base + 0.05 * span, f"Rendering the {shape} video")
            _, template = self._template(ctx, plan, shape, look)
            w, h = SHAPES[shape]
            edl, _ = self.edl_for(template, w, h)
            edl.meta.true_peak_db = -2.3
            name = f"v{version}-{shape.replace(':', 'x')}"
            (self.work.out / f"{name}.edl.json").write_text(edl.model_dump_json(indent=1), encoding="utf-8")
            t0 = time.monotonic()
            done = [0]
            total = max(1, len(edl.segments))

            def shot():
                done[0] += 1
                self.progress(base + span * (0.05 + 0.8 * done[0] / total), f"Rendering the {shape} video")

            result = render_edl(edl, self.project, self.cfg, quality="final", out_path=self.work.out / f"{name}.mp4",
                                on_shot=shot)
            seconds = round(time.monotonic() - t0, 1)
            self.progress(base + span * 0.9, "Checking it")
            findings, _sheet = run_checks(Path(result.output), edl, self.project, strict_beats=True)
            kept = [_studio_level(asdict(f)) for f in findings if self.owner or f.check not in OWNER_ONLY_CHECKS]
            out.append(RenderInfo(shape, Path(result.output).name, round(result.duration, 2), seconds, kept))
        return out


def footage_notes(catalog: dict) -> list[str]:
    """Plain heads-ups about the footage, shown before anyone waits for a result it can't deliver."""
    notes = []
    sigs = [e["signals"] for e in catalog["clips"] if e.get("signals")]
    if sigs and sum(s["brightness_mean"] for s in sigs) / len(sigs) < 75:
        notes.append("Your footage is quite dark. It will be brightened, but footage shot in better light always looks "
                     "better.")
    small = [e for e in catalog["clips"] if e.get("clip") and min(e["clip"]["width"], e["clip"]["height"]) < 700]
    if small:
        notes.append(f"{len(small)} of your clips are low resolution (for example {small[0]['clip']['width']}x"
                     f"{small[0]['clip']['height']}), so they will look soft once enlarged. Filming in 1080p or 4K helps a lot.")
    moments = [m for e in catalog["clips"] if e.get("analysis") for m in e["analysis"]["moments"] if m["strength"] >= 5]
    if len(moments) < 4:
        notes.append("There are only a few strong moments in these clips, so the video may reuse some of them (cut "
                     "differently). More clips, or clips from more angles, give a richer video.")
    return notes


def _studio_level(f: dict) -> dict:
    """Studio renders limit peaks at -2.3 dB so that the AAC encode stays under the -1 dB streaming limit; the
    check then reports any peak between -2.3 and -1 as 'over target'. Under -1 dB is what matters: that's ok."""
    import re

    if f["check"] == "loudness" and f["level"] == "warn":
        m = re.search(r"(-?[\d.]+) LUFS, true peak (-?[\d.]+) dB", f["detail"])
        if m and float(m.group(2)) <= -1.0 and abs(float(m.group(1)) + 14.0) <= 1.5:
            f = {**f, "level": "ok"}
    return f


MIN_SHOT = 0.21  # seconds on screen; the renderer refuses anything under 0.2 (a stutter piece of 0.22 s is fine)


def fold_slots(t: Template, ids: set[str]) -> Template:
    """Fold the named shots into their neighbours (the one before, or the next for the first), keeping the timeline."""
    slots = [x.model_copy() for x in t.slots]
    for sid in ids:
        i = next((k for k, x in enumerate(slots) if x.id == sid), None)
        if i is None or len(slots) <= 1:
            continue
        if i > 0:
            slots[i - 1] = slots[i - 1].model_copy(update={"end": slots[i].end})
        else:
            slots[1] = slots[1].model_copy(update={"start": slots[0].start, "transition_in": Transition()})
        del slots[i]
    return t.model_copy(update={"slots": slots})


def heal_template(t: Template, minimum: float = MIN_SHOT) -> Template:
    """No shot too short to show: a sliver (a stutter piece at a fast tempo, the end of a cut-off sample) is folded into
    its neighbour so the timeline keeps its length and every other cut stays on its beat. Never an error for the person."""
    slots = [s.model_copy() for s in t.slots]
    i = 0
    while i < len(slots) and len(slots) > 1:
        s = slots[i]
        if s.end - s.start >= minimum:
            i += 1
            continue
        if i > 0:  # the shot before holds on a little longer
            slots[i - 1] = slots[i - 1].model_copy(update={"end": s.end})
        else:  # the first shot: the next one starts a little earlier
            slots[1] = slots[1].model_copy(update={"start": s.start, "transition_in": Transition()})
        del slots[i]
        i = max(0, i - 1)
    return t.model_copy(update={"slots": slots})


def slice_template(t: Template, start: float, end: float) -> Template:
    """The part of a template between two timeline seconds, as a template of its own (for samples)."""
    slots = [s for s in t.slots if s.end > start + 0.05 and s.start < end - 0.05]
    if not slots:
        return t
    shift = slots[0].start
    stop = min(end, slots[-1].end)
    moved = []
    for i, s in enumerate(slots):
        upd = {"start": round(s.start - shift, 3), "end": round(min(s.end, stop) - shift, 3)}
        if i == 0:
            upd["transition_in"] = Transition()
        moved.append(s.model_copy(update=upd))
    sounds = []
    for layer in t.sounds:
        at = layer.at - shift
        if at + layer.duration <= 0 or at >= stop - shift:
            continue
        if at < 0:  # starts before the sample: begin it part-way through
            if layer.source.startswith("clip:") and layer.src_in is not None:
                layer = layer.model_copy(update={"src_in": round(layer.src_in - at, 3), "duration": round(layer.duration + at, 3),
                                                 "fade_in": 0.2})
                at = 0.0
            else:
                continue
        sounds.append(layer.model_copy(update={"at": round(at, 3)}))
    music = t.music.model_copy(update={"start": round(t.music.start + shift, 3)})
    return t.model_copy(update={"slots": moved, "sounds": sounds, "music": music, "intro": None, "fade_in": 0.15,
                                "fade_out": 0.5})


def remove_project_files(work: Workdir, cfg: Config) -> None:
    """Delete a project's uploads, results and the pipeline's working files for it (never a footage folder
    the command line only read)."""
    pipeline_root = Project(work.clips, cfg).root
    for path in (pipeline_root, work.root):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)


def folder_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def plan_json(plan: DesignPlan) -> str:
    return json.dumps(plan.model_dump(), indent=1)
