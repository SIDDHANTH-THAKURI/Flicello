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
import shutil
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2

from ..config import Config, load_config
from ..design import Design, build_template
from ..ingest import ingest_folder
from ..music import load_or_analyze
from ..music.analysis import MusicAnalysis
from ..perceive import measure_project
from ..perceive.catalog import build_catalog, check_analysis as catalog_check
from ..perceive.prep import analysis_path, prepare_perception
from ..qa import run_checks
from ..render import render_edl
from ..schemas import Transition
from ..template import Template, autofill
from ..workspace import Project, write_text_atomic
from .brain.context import beat_map, clip_facts, load_catalog, moment_by_id
from .brain.core import StoryContext, brief_text, to_design
from .brain.schemas import DesignPlan, StoryPitch

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
        self.project = Project(work.clips, cfg)

    # ------------------------------------------------------------ analyse
    def analyse(self) -> dict:
        """Everything the story steps need; finished parts are kept, so a retry only redoes what failed."""
        t0 = time.monotonic()
        self.progress(0.02, "Looking at your clips")
        project, clips = ingest_folder(self.work.clips, self.cfg)
        usable = [c for c in clips if c.usable]
        if not usable:
            raise ValueError("None of the uploaded files could be read as video.")
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
        self.progress(0.88, "Making the catalog")
        catalog = build_catalog(project)
        self.progress(0.93, "Listening to the song")
        song = self.song()
        skipped = [c.rel_path for c in clips if not c.usable]
        return {"clips": len(usable), "skipped": skipped, "footage_seconds": round(sum(c.duration for c in usable), 1),
                "notes": footage_notes(catalog),
                "analysed": sum(1 for e in catalog["clips"] if e["analysis"]), "song_seconds": round(song.duration, 1),
                "tempo": round(song.tempo, 1), "seconds": round(time.monotonic() - t0, 1)}

    def song(self) -> MusicAnalysis:
        path = self.work.song_path()
        if path is None:
            raise ValueError("No song yet: upload one first.")
        return load_or_analyze(path, self.project.music)

    def context(self, shapes: list[str], length: float, intent: str, rules: list[dict]) -> StoryContext:
        catalog = load_catalog(self.project.root)
        song = self.song()
        ctx = StoryContext(catalog=catalog, facts=clip_facts(catalog), song=song, beats=beat_map(song, length),
                           shapes=shapes, intent=intent, rules=rules)
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
        return self.brain.design(ctx, pitch, note)

    def revise(self, ctx: StoryContext, pitch: StoryPitch, current: DesignPlan, feedback: str) -> DesignPlan:
        self.progress(0.05, "Working your changes into the plan")
        return self.brain.revise(ctx, pitch, current, feedback)

    def _template(self, ctx: StoryContext, plan: DesignPlan, shape: str, look: str) -> tuple[Design, Template]:
        design = to_design(plan, ctx, str(self.work.song_path()), vertical=shape == "9:16", look=look)
        return design, build_template(design, self.project)

    def sample(self, ctx: StoryContext, plan: DesignPlan, shape: str, name: str, look: str = "vivid") -> RenderInfo:
        """About 8 seconds at draft quality: through the drop if there is one early enough, else the opening."""
        self.progress(0.3, "Cutting a short sample")
        _, template = self._template(ctx, plan, shape, look)
        hits = [s for s in template.slots if s.want.startswith("hit")]
        start = 0.0
        if hits and hits[0].start <= 25.0:
            start = max(0.0, hits[0].start - SAMPLE_SECONDS * 0.55)
        part = slice_template(template, start, start + SAMPLE_SECONDS)
        w, h = SHAPES[shape]
        edl = autofill(part, self.project, width=w, height=h)
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
            edl = autofill(template, self.project, width=w, height=h)
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
