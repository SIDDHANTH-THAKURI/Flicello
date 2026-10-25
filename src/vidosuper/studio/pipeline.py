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
import logging
import os
import re
import shutil
import threading
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
from .brain import BudgetExceeded, PersonLimit
from .brain.context import HITS_WORDS, beat_map, clip_facts, load_catalog, moment_by_id
from .brain.core import StoryContext, brief_text, to_design
from .brain.schemas import DesignPlan, StoryPitch

log = logging.getLogger("vidosuper.studio.pipeline")
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
    many = int(os.environ.get("VIDO_STUDIO_PIPELINE_WORKERS") or 2)  # a cloud renderer has the cores to do more at once
    overrides: dict = {"workspace": str(workspace), "render": {"workers": many, "final": {"preset": render_preset}},
                       "measure": {"workers": many}, "ingest": {"workers": 1}, "output": {"outro": "none"}}
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

    @property
    def beats(self) -> Path:
        """Tracks made for this film (each try, and the full track made from a taste)."""
        return self.root / "beats"

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
        self.natural_sound = False  # ... and that the clips keep their own sound (else the film is silent)
        self.mute_clips = False  # they asked for no sound from the clips: every shot muted, music or not
        self.speech = False  # a talking film: cut on its words, every shot with its own voice
        self.watcher = None
        self.mute_effects = False  # no sound effects under the graphics (a silent film, or they asked)
        self.brand: dict = {}  # their brand kit: colour, type, logo (profile answers["brand"])  # Gemini watching each clip with its sound (brain/gemini.py), when the studio has one
        self.project = Project(work.clips, cfg)

    # ------------------------------------------------------------ analyse
    def cliplog_dir(self) -> Path | None:
        """Where this person's clip logs are kept for their next films (users/<id>/cliplog), or None outside the
        studio. Kept as long as their films are (the retention time), and only ever used for the same person."""
        root = self.work.root
        return root.parent.parent / "cliplog" if root.parent.name == "projects" else None

    def log_tag(self) -> str | None:
        """Which brain logged a clip (the analyst's instructions and model): a log is re-used only by the same one.
        The no-AI brain's logs are free to make again, so they are never kept."""
        import hashlib

        models = getattr(self.brain, "models", None)
        if self.brain is None or getattr(self.brain, "kind", "local") == "local" or models is None:
            return None
        from .brain.context import prompt

        who = f"gemini:{self.watcher.model}" if self.watcher is not None else models.analyst
        return hashlib.sha1((prompt("analyst") + who).encode("utf-8")).hexdigest()[:10]

    def analyse(self, length: float = 30.0, pace: str = "balanced") -> dict:
        """Everything the story steps need; finished parts are kept, so a retry only redoes what failed."""
        t0 = time.monotonic()
        self.progress(0.02, "Looking at your clips")
        project, clips = ingest_folder(self.work.clips, self.cfg)
        usable = [c for c in clips if c.usable]
        if not usable:
            raise UserError("None of the uploaded files could be read as video.")
        if self.watcher is not None:  # the watcher watches the clips' light copies (with their sound)
            self.progress(0.1, "Making quick previews of your clips")
            self.make_previews(project, usable)
        self.progress(0.15, "Measuring shake, light and motion")
        measure_project(project, self.cfg)
        self.progress(0.3, "Listening for speech and making contact sheets")
        tasks = prepare_perception(project, self.cfg)
        by_id = {c.clip_id: c for c in usable}
        todo = [t for t in tasks["clips"] if catalog_check(project, by_id[t["clip_id"]])[1]]
        done = [0]
        logs, tag = self.cliplog_dir(), self.log_tag()
        for task in list(todo):  # a clip this person's earlier film already had logged: no new AI call
            clip = by_id[task["clip_id"]]
            kept = logs / f"{clip.fingerprint[:24]}-{tag}.json" if logs and tag else None
            if kept is not None and kept.exists():
                try:
                    analysis = ClipAnalysis.model_validate_json(kept.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                analysis.clip_id = clip.clip_id
                write_text_atomic(analysis_path(project, clip.clip_id), analysis.model_dump_json(indent=1))
                todo.remove(task)

        def watch(task: dict) -> None:
            clip = by_id[task["clip_id"]]
            analysis = None
            if self.watcher is not None:  # picture and sound together; the contact sheets if it can't
                from . import preview

                video = preview.copy_path(self.work.root, clip.clip_id)
                if video.exists():
                    try:
                        analysis = self.watcher.analyse_clip(clip, brief_text(Path(task["brief"])), video)
                    except (BudgetExceeded, PersonLimit):
                        raise
                    except Exception as exc:  # noqa: BLE001 - the analyst logs this one from its contact sheets
                        log.warning("the watcher could not log %s; using the contact sheets", clip.clip_id, exc_info=True)
                        from ..local import health

                        health.failed("gemini", exc)
            if analysis is None:
                analysis = self.brain.analyse_clip(clip, brief_text(Path(task["brief"])), [Path(s) for s in task["sheets"]])
            write_text_atomic(analysis_path(project, clip.clip_id), analysis.model_dump_json(indent=1))
            if logs and tag:
                logs.mkdir(parents=True, exist_ok=True)
                write_text_atomic(logs / f"{clip.fingerprint[:24]}-{tag}.json", analysis.model_dump_json(indent=1))
            done[0] += 1
            self.progress(0.35 + 0.5 * done[0] / len(todo), f"Watched {done[0]} of {len(todo)} clips")

        found: dict = {}

        def people() -> None:
            try:
                found["index"] = self.index_faces(project)
            except Exception:  # noqa: BLE001 - who's in shot helps the editor; the film never waits on it
                log.warning("could not index the faces", exc_info=True)

        faces_thread = threading.Thread(target=people, name="faces", daemon=True)
        faces_thread.start()
        if todo:
            self.progress(0.35, f"Watching {len(todo)} clip{'s' if len(todo) > 1 else ''}")
            lanes = getattr(self.watcher, "parallel", None) or getattr(self.brain, "parallel", 1)
            with ThreadPoolExecutor(max_workers=max(1, lanes)) as pool:
                list(pool.map(watch, todo))  # the first failure (or a spending cap) stops the step
        faces_thread.join(timeout=900)
        self.progress(0.86, "Listening for hits")
        self.enrich_with_impacts(project, usable)
        self.progress(0.88, "Making the catalog")
        catalog = build_catalog(project)
        self.progress(0.91, "Making quick previews of your clips")
        self.make_previews(project, usable)
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

    def supported_seconds(self) -> float:
        """How long a film this footage carries without showing a shot twice: footage inside strong moments (strength 6
        and up, of 10) counts whole, middling ones (5) half, the rest (and anything marked unusable) not at all."""
        facts = clip_facts(load_catalog(self.project.root))
        total = 0.0
        for f in facts.values():
            n = int(f.duration * 10) + 1
            w = [0.0] * n  # weight of each tenth of a second
            for m in f.moments:
                k = 1.0 if m["strength"] >= 6 else 0.5 if m["strength"] >= 5 else 0.0
                for i in range(max(0, int(m["start"] * 10)), min(n, int(m["end"] * 10))):
                    w[i] = max(w[i], k)
            for a, b in f.avoid:
                for i in range(max(0, int(a * 10)), min(n, int(b * 10))):
                    w[i] = 0.0
            total += sum(w) / 10
        return total

    def fill_places(self) -> bool:
        """Logs made before the analyst named each moment's place and who is in it get both, read from their own
        words (one short text call for the whole film), so 'the Opera House twice' is caught in older films too.
        True when it tagged something."""
        root = self.project.root
        if self.brain is None or not hasattr(self.brain, "places") or not (root / "catalog.json").exists():
            return False
        catalog = load_catalog(root)
        entries = [e for e in catalog["clips"] if e.get("analysis")]
        if not any("place" not in m for e in entries for m in e["analysis"]["moments"]):
            return False
        lines, ids = [], []
        for e in entries:
            a = e["analysis"]
            lines.append(f"## {e['short']}: {a['summary']}" + (f" (the analyst's place: {a['location_guess']})"
                                                             if a.get("location_guess") else ""))
            for n, m in enumerate(sorted(a["moments"], key=lambda m: m["start"]), start=1):
                ids.append(f"{e['short']}#{n}")
                lines.append(f"- {e['short']}#{n}: {m['description']}" + (f" [{m['notes']}]" if m.get("notes") else ""))
        self.progress(0.03, "Noting the places in your clips")
        tags = {t.id: t for t in self.brain.places("\n".join(lines), ids).tags}
        for e in entries:
            path = analysis_path(self.project, e["clip"]["clip_id"])
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            data["moments"] = sorted(data.get("moments") or [], key=lambda m: m["start"])
            for n, m in enumerate(data["moments"], start=1):
                t = tags.get(f"{e['short']}#{n}")
                m.setdefault("place", t.place if t else "")
                if m.get("people") in (None, "unknown"):
                    m["people"] = t.people if t else "unknown"
            write_text_atomic(path, json.dumps(data, indent=1))
        build_catalog(self.project)
        return True

    def context(self, shapes: list[str], length: float, intent: str, rules: list[dict], pace: str = "balanced",
                start_at: float | None = None) -> StoryContext:
        """`start_at`: the beat a version was cut from (a change to it keeps that grid)."""
        self.ensure_song(length, pace)
        try:
            self.fill_places()
        except (BudgetExceeded, PersonLimit):
            raise
        except Exception:  # noqa: BLE001 - without the tags the film is still made, as before
            log.warning("could not tag the places of the clips", exc_info=True)
        catalog = load_catalog(self.project.root)
        song = self.song()
        ctx = StoryContext(catalog=catalog, facts=clip_facts(catalog), song=song, beats=beat_map(song, length, start_at),
                           shapes=shapes, intent=intent, rules=rules, pace=pace)
        good = sum(1 for f in ctx.facts.values() for m in f.moments if m["strength"] >= 4)
        ctx.allow_reuse = good < -(-ctx.beats.target // 4)  # fewer good moments than a shot every 4 beats: reuse them
        ctx.reuse_if_asked(intent, *[r["text"] for r in rules])
        ctx.read_words(*[r["text"] for r in rules])  # (their own words are added by the caller: not the AI's brief)
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
        ctx.read_words(note)
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
        ctx.read_words(feedback)
        if ctx.pace == "every":
            return self.revise_strikes(ctx, pitch, feedback, 1 + len(current.plan) % 7)
        return self.brain.revise(ctx, pitch, current, feedback)

    def make_previews(self, project: Project | None = None, clips: list | None = None) -> int:
        """Light copies of the clips for the instant preview (see preview.py); a film made before them gets theirs
        here too (ingest is a no-op for clips it already knows)."""
        from . import preview

        if project is None or clips is None:
            project, clips = ingest_folder(self.work.clips, self.cfg)
        return preview.make_copies(project, clips, self.work.root, log=log.info)

    def index_faces(self, project: Project | None = None) -> dict:
        """Who is in the footage (see faces.py): every face found, followed and grouped into people."""
        from ..perceive.catalog import short_ids
        from . import faces

        if project is None:
            project, _ = ingest_folder(self.work.clips, self.cfg)
        shorts = short_ids(project)
        proxies = [(short, cid, project.proxies / f"{cid}.mp4") for cid, short in shorts.items()]
        return faces.build(proxies, self.work.stills, self.work.root / faces.INDEX)

    def add_motion(self, edl, plan: DesignPlan, ctx: StoryContext) -> list[str]:
        """The plan's graphics on this edit's timeline, everything a graphic needs found here, beside the clips, so the
        preview shows exactly what the render makes:
        - each comes in with its shot and stays its beats; a transition sits on the cut into its shot;
        - captions carry the words said in the shots that play their own sound (from the transcripts);
        - beat words and beat lines land on the music's beats;
        - a callout's point is found in the shot (Gemini, from their words) and followed as the camera moves;
        - under each title and name, how bright the picture is sets how deep the pad behind the type is.
        Returns what they should know (a callout's subject that couldn't be found)."""
        from ..edit.timeline import build_timeline
        from ..motion.place import backdrop, boxes, follow, layers, scrim_for, speech_words
        from ..schemas import MotionGraphic
        from .versions import slot_ids

        edl.motion = []
        notes: list[str] = []
        brand = self.brand or {}
        if not plan.graphics and not brand.get("logo"):
            return notes
        timeline = build_timeline(edl, self.project.clips())
        fps = timeline.fps
        starts = {item.seg.id: item.start / fps for item in timeline.items}
        ends = {item.seg.id: item.end / fps for item in timeline.items}
        ids = slot_ids(plan)
        total = timeline.duration
        aspect = edl.meta.width / edl.meta.height
        period = ctx.beats.period
        caps: dict[str, cv2.VideoCapture] = {}

        def read(cid: str, seconds: float):
            path = self.project.proxies / f"{cid}.mp4"
            if cid not in caps:
                if not path.exists():
                    return None
                caps[cid] = cv2.VideoCapture(str(path))
            caps[cid].set(cv2.CAP_PROP_POS_MSEC, max(0.0, seconds) * 1000.0)
            ok, frame = caps[cid].read()
            return frame if ok else None

        try:
            for gi, g in enumerate(plan.graphics):
                if g.kind == "scene":  # a designed scene: laid into the finished film afterwards (work/scenes_in_film)
                    continue
                if g.kind == "captions":
                    said = {cid: t for cid in {seg.clip_id for seg in edl.segments}
                            if (t := load_transcript(self.project, cid)) is not None}
                    lo = min(total, max(0.0, g.at)) if g.at is not None else 0.0  # placed by hand: only that stretch
                    hi = min(total, g.until) if g.until is not None else total
                    if hi - lo >= 0.3:
                        edl.motion.append(MotionGraphic(kind="captions", start=round(lo, 3), end=round(hi, 3),
                                                        style=g.style, words=speech_words(timeline, said), place=g.place,
                                                        scale=min(3.0, max(0.3, g.scale or 1.0)), ref=gi))
                    continue
                n = min(max(1, g.shot), len(ids))
                # where its shot starts (a shot folded into the one before it: that one's start)
                at = next((starts[sid] for k in range(n - 1, -1, -1) for sid in ids[k] if sid in starts), 0.0)
                shot_end = next((ends[sid] for sid in reversed(ids[n - 1]) if sid in ends), total)
                if g.say and g.kind != "thing" and g.at is None:  # on the words they named, wherever they are said
                    heard = self.when_said(timeline, g.say)
                    if heard is None:
                        notes.append(f"I couldn't hear \"{g.say}\" in the film, so the {g.kind} comes in on shot {n}")
                    else:
                        at = heard
                        shot_end = next((i.end / fps for i in timeline.items if i.start / fps <= at < i.end / fps), total)
                end = min(total, at + max(1.0, g.beats * period))
                if g.at is not None:  # placed by hand on the timeline: its own film seconds
                    at = min(max(0.0, g.at), max(0.0, total - 0.5))
                    end = min(total, g.until if g.until is not None and g.until > at + 0.3 else at + max(1.0, g.beats * period))
                    shot_end = end
                base = dict(text=g.text, sub=g.sub, style=g.style, behind=g.behind, accent=g.accent, place=g.place,
                            scale=min(3.0, max(0.3, g.scale or 1.0)), ref=gi)
                if g.kind == "transition":
                    if at > 0.3:
                        edl.motion.append(MotionGraphic(kind="transition", start=round(at - 0.3, 3),
                                                        end=round(min(total, at + 0.3), 3), style=g.style, ref=gi))
                    continue
                if g.kind in ("kinetic", "accent"):
                    step = period if period >= 0.4 else period * 2  # a word a beat, or every other beat when fast
                    count = len(g.text.split()) if g.kind == "kinetic" else max(1, round((end - at) / step))
                    beats = [round(at + k * step, 3) for k in range(count) if at + k * step < total]
                    if g.kind == "kinetic":
                        end = min(total, max(end, (beats[-1] if beats else at) + step + 0.4))
                    edl.motion.append(MotionGraphic(kind=g.kind, start=round(at, 3), end=round(end, 3), beats=beats,
                                                    **base))
                    continue
                if g.kind == "route":
                    edl.motion.append(MotionGraphic(kind="route", start=round(at, 3), end=round(end, 3),
                                                    names=[x for x in g.text.split("|") if x], **base))
                    continue
                if g.kind == "thing":
                    made = self.thing(g, timeline, read, at, end, period, aspect, notes)
                    if made is not None:
                        made.ref = gi
                        edl.motion.append(made)
                    continue
                if g.kind == "callout":
                    point = (g.x, g.y) if g.x is not None and g.y is not None else self.locate(timeline, read, at, g.want or g.text, aspect)
                    if point is None:
                        from .versions import the

                        notes.append(f"I couldn't find {the(g.want or g.text)} in shot {n}, so it has no callout")
                        continue
                    end = min(end, shot_end)
                    track = follow(timeline, read, at, end, point[0], point[1], aspect)
                    inside = [p for p in track if -0.02 <= p[1] <= 1.02 and -0.02 <= p[2] <= 1.02]
                    if len(inside) < len(track):  # it leaves the frame: the callout goes with it
                        end = min(end, inside[-1][0] if inside else at)
                    if end - at >= 0.8:
                        edl.motion.append(MotionGraphic(kind="callout", start=round(at, 3), end=round(end, 3),
                                                        points=[[round(point[0], 4), round(point[1], 4)]], track=inside,
                                                        **base))
                    continue
                if end - at >= 0.5:
                    points = []
                    if g.kind == "title" and g.behind:  # just above the person's head, so they cross its letters
                        from ..motion.place import subject_top
                        from ..render.behind import people_matte

                        find = people_matte()
                        top = subject_top(timeline, read, at, end, aspect, find) if find else None
                        if top is None:
                            notes.append(f"Nobody stands in shot {n} for the title to go behind, so it's in front")
                            base["behind"] = False
                        else:
                            points = [[0.5, round(min(0.6, max(0.12, top + 0.05)), 3)]]
                    edl.motion.append(MotionGraphic(kind=g.kind, start=round(at, 3), end=round(end, 3), points=points,
                                                    **base))
            # the words said always on top: a thing falling past a caption never hides the word being said
            edl.motion.sort(key=lambda g: g.kind == "captions")
            # their brand: its colour on every graphic that has none of its own, its type, its logo in a corner
            for g in edl.motion:
                if brand.get("color") and not g.accent:
                    g.accent = brand["color"]
                if brand.get("font") in ("serif", "mono") and not g.font:
                    g.font = brand["font"]
            if brand.get("logo"):
                edl.motion.append(MotionGraphic(kind="logo", start=0.0, end=round(total, 3), image=brand["logo"]))
            # looked at before anyone sees it: how bright the picture is under each title and name, for a deeper pad
            # where white type would sink into a bright sky
            for layer in layers(edl, timeline, {}, self.faces_seen()):
                if (box := boxes(layer)) is not None:
                    layer.graphic.scrim = scrim_for(backdrop(timeline, read, layer.start, layer.end, box, aspect))
            if not self.mute_effects:  # every move makes its sound, under the music
                from ..motion.sound import graphic_sounds

                edl.sounds = [*edl.sounds, *graphic_sounds(edl)]
        finally:
            for cap in caps.values():
                cap.release()
        return notes

    def locate(self, timeline, read, at: float, what: str, aspect: float) -> tuple[float, float] | None:
        """Where `what` is in the film's frame at `at` (shares), found by the watcher in that frame; remembered per shot
        and words, so a draft and the version made from it agree (and it's asked once)."""
        import json

        item = next((i for i in timeline.items if i.start / timeline.fps <= at + 0.05 < i.end / timeline.fps), None)
        if item is None or not what or self.watcher is None or not hasattr(self.watcher, "point_at"):
            return None
        seg = item.seg
        src = round(seg.src_in + (at + 0.3 - item.start / timeline.fps) * seg.speed, 2)
        key = f"{seg.clip_id}@{src}:{seg.zoom}:{seg.zoom_center}:{seg.frame_center}:{what.lower()}"
        memo = self.work.root / "located.json"
        try:
            known = json.loads(memo.read_text(encoding="utf-8")) if memo.exists() else {}
        except ValueError:
            known = {}
        if key in known:
            return tuple(known[key]) if known[key] else None
        frame = read(seg.clip_id, src)
        if frame is None:
            return None
        from ..motion.place import film_crop

        crop = film_crop(frame, seg, aspect)  # as the shot shows it
        ok, jpeg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 88])
        try:
            point = self.watcher.point_at(jpeg.tobytes(), what) if ok else None
        except Exception:  # noqa: BLE001 - not found is said plainly; the film goes on
            log.warning("could not locate %r", what, exc_info=True)
            point = None
        known[key] = list(point) if point else None
        memo.write_text(json.dumps(known), encoding="utf-8")
        return point

    def when_said(self, timeline, say: str) -> float | None:
        """The film second these words of theirs are said (the whole phrase, else its most telling word)."""
        from ..motion.place import speech_words

        said = {cid: t for cid in {item.seg.clip_id for item in timeline.items}
                if (t := load_transcript(self.project, cid)) is not None}
        words = speech_words(timeline, said)
        want = [w for w in re.sub(r"[^a-z0-9 ]", " ", say.lower()).split() if w]
        if not want or not words:
            return None
        bare = [re.sub(r"[^a-z0-9]", "", w.w.lower()) for w in words]
        hit = next((k for k in range(len(bare)) if bare[k:k + len(want)] == want), None)
        if hit is None:
            key = max(want, key=len)
            hit = next((k for k, b in enumerate(bare) if b == key), None)
        return round(words[hit].s, 3) if hit is not None else None

    def thing(self, g, timeline, read, at: float, end: float, period: float, aspect: float, notes: list[str]):
        """Something they described, made real: drawn (from the library, else drawn for it), coming in as its words
        are said, from where they said (found in the frame), moving as they said."""
        import json

        from ..motion.things import clean_svg, read as read_thing
        from ..schemas import MotionGraphic

        seen = read_thing(g.want)
        name = f"the {seen['shape']}" if seen["shape"] else " ".join(g.want.split()[:4])

        def yours(text: str) -> str:  # their words, said back to them
            return re.sub(r"\bmy\b", "your", re.sub(r"\bme\b", "you", text, flags=re.I), flags=re.I)

        if g.say and g.at is None:  # as these words are said (unless placed by hand on the timeline)
            heard = self.when_said(timeline, g.say)
            if heard is not None:
                at = heard
            else:
                notes.append(f'I couldn\'t hear "{g.say}" in the film, so {name} comes in as its shot starts')
        total = timeline.duration
        motion = g.style or "pop"
        length = max(g.beats * period, 3.2 if motion in ("drop", "bounce", "rain") else 2.0)
        end = min(total, at + length)
        if g.at is not None and g.until is not None:  # placed by hand on the timeline
            end = min(total, max(at + 0.8, g.until))
        if end - at < 0.8:
            return None
        anchor_words = (g.anchor or "").lower()
        if g.x is not None and g.y is not None:
            point = (g.x, g.y)
        elif re.search(r"\b(sky|above|top of the (?:screen|frame)|ceiling|nowhere)\b", anchor_words):
            point = (0.5, 0.02)
        elif anchor_words:
            what = "the very top of the person's head" if re.search(r"\b(head|hair)\b", anchor_words) else g.anchor
            point = self.locate(timeline, read, at, what, aspect)
            if point is None:
                notes.append(f"I couldn't find {yours(g.anchor)} in that shot, so {name} starts near the top")
                point = (0.5, 0.3)
        else:
            point = (0.5, 0.3 if motion in ("drop", "bounce") else 0.42)
        shape, svg = seen["shape"], ""
        if not shape:  # not in the library: drawn for it once, then remembered
            memo = self.work.root / "drawn.json"
            try:
                known = json.loads(memo.read_text(encoding="utf-8")) if memo.exists() else {}
            except ValueError:
                known = {}
            key = re.sub(r"\s+", " ", g.want.lower()).strip()[:120]
            if not known.get(key) and self.watcher is not None and hasattr(self.watcher, "draw"):
                try:
                    drawn = clean_svg(self.watcher.draw(g.want))
                except Exception:  # noqa: BLE001 - a sparkle stands in, said plainly; drawn next time
                    log.warning("could not draw %r", g.want, exc_info=True)
                    drawn = ""
                if drawn:  # only a real drawing is kept (a failure is tried again next time)
                    known[key] = drawn
                    memo.write_text(json.dumps(known, ensure_ascii=False), encoding="utf-8")
            svg = known.get(key, "")
            if not svg:
                shape = "sparkle"
                notes.append(f"I couldn't draw {name}, so a sparkle stands in for it")
        return MotionGraphic(kind="thing", start=round(at, 3), end=round(end, 3), text=g.text, style=motion,
                             accent=g.accent or seen["color"], points=[[round(point[0], 4), round(point[1], 4)]],
                             shape=shape, svg=svg, size=seen["size"] or None, count=g.count or seen["count"],
                             bounces=g.bounces)

    def faces_seen(self) -> list[dict]:
        """Where faces are in the clips (the face index, when it has been made): what graphics keep clear of."""
        from . import faces

        return faces.appearances(faces.load(self.work.root / faces.INDEX))

    def draft(self, ctx: StoryContext, plan: DesignPlan, shape: str, name: str, look: str = "vivid") -> Path:
        """The edit list of a plan, without rendering it: what the instant preview plays (seconds, not minutes)."""
        _, template = self._template(ctx, plan, shape, look)
        w, h = SHAPES[shape]
        edl, _ = self.edl_for(template, w, h)
        self.hand_edits(edl, plan)
        self.motion_notes = self.add_motion(edl, plan, ctx)
        path = self.work.out / f"{name}.edl.json"
        path.write_text(edl.model_dump_json(indent=1), encoding="utf-8")
        return path

    def _template(self, ctx: StoryContext, plan: DesignPlan, shape: str, look: str) -> tuple[Design, Template]:
        design = to_design(plan, ctx, str(self.work.song_path()), vertical=shape == "9:16", look=look)
        return design, heal_template(build_template(design, self.project))

    def hand_edits(self, edl, plan: DesignPlan) -> None:
        """What they set by hand on the timeline (studio/handedit.py), on the edit list: each shot's own sound (louder,
        softer or off; a shot whose sound they turned up is heard), and the music's slide, level, fades, dips and
        cuts."""
        from ..schemas import GainPoint, MusicJump
        from .versions import slot_ids

        by_id = {seg.id: seg for seg in edl.segments}
        for shot, ids in zip(plan.plan, slot_ids(plan)):
            first = next((by_id[i] for i in ids if i in by_id), None)
            if shot.keys and first is not None:  # its framing keyframes (the shot's own seconds)
                first.keys = [[round(float(k[0]), 3), round(min(3.0, max(1.0, float(k[1]))), 3),
                               round(min(1.0, max(0.0, float(k[2]))), 3), round(min(1.0, max(0.0, float(k[3]))), 3)]
                              for k in sorted(shot.keys, key=lambda k: k[0])[:16]]
            if shot.gain_db is None and not shot.mute:
                continue
            for seg in (by_id[i] for i in ids if i in by_id):
                if shot.mute:
                    seg.audio = seg.audio.model_copy(update={"mode": "mute"})
                else:
                    seg.audio = seg.audio.model_copy(update={"mode": "original", "gain_db": round(shot.gain_db, 1)})
        if plan.over:  # shots laid over the picture (B-roll): the film's sound goes on under them
            from ..schemas import Overlay
            from .brain.context import clip_facts, load_catalog

            ids = {short: f.clip_id for short, f in clip_facts(load_catalog(self.project.root)).items()}
            edl.over = [Overlay(clip_id=ids[o.clip], at=round(o.at, 3), until=round(o.until, 3), src_in=round(o.src_in, 3),
                                speed=min(4.0, max(0.25, o.speed)), zoom=min(2.5, max(1.0, o.zoom)),
                                frame_center=(min(1.0, max(0.0, o.frame_x)), 0.5) if o.frame_x is not None else (0.5, 0.5))
                        for o in plan.over if o.clip in ids and o.until - o.at >= 0.2]
        m = plan.music
        if m is None or edl.music is None:
            return
        spec = edl.music
        spec.start = round(max(0.0, spec.start - m.slide), 3)  # later against the picture: earlier in the song
        spec.gain_db = round(spec.gain_db + m.gain_db, 2)
        if m.fade_in is not None:
            spec.fade_in = round(max(0.0, m.fade_in), 3)
        if m.fade_out is not None:
            spec.fade_out = round(max(0.0, m.fade_out), 3)
        spec.duck_db = m.duck_db
        if m.points:
            spec.automation = [GainPoint(t=round(p.t, 3), gain_db=round(p.db, 1)) for p in sorted(m.points, key=lambda p: p.t)]
        if m.jumps:
            spec.jumps = [MusicJump(at=round(j.at, 3), to=round(max(0.0, j.to), 3))
                          for j in sorted(m.jumps, key=lambda j: j.at)]

    def edl_for(self, template: Template, width: int, height: int):
        """The EDL for a template that the renderer will accept: a shot the renderer finds too short on screen (frames
        taken by its transitions, rounding at the cut) is folded into its neighbour and the EDL rebuilt, until none is.
        The person never hears about it."""
        for _ in range(8):
            edl = autofill(template, self.project, width=width, height=height)
            if self.no_music:  # the cut still follows the (unheard) beat grid; nothing plays under it
                edl.music = None
                if self.natural_sound:  # each shot with the sound it was filmed with (faded at the cuts)
                    for seg in edl.segments:
                        seg.audio = seg.audio.model_copy(update={"mode": "original"})
            if self.speech:  # a talking film: every shot with its own voice, the music under it; cuts stay on words
                edl.meta.snap_to_beats = False
                edl.meta.speech = True
                for seg in edl.segments:
                    seg.audio = seg.audio.model_copy(update={"mode": "original", "duck_music": True})
            if self.mute_clips:  # whatever the plan chose, no shot plays the sound it was filmed with
                for seg in edl.segments:
                    seg.audio = seg.audio.model_copy(update={"mode": "mute"})
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
            self.hand_edits(edl, plan)
            self.motion_notes = self.add_motion(edl, plan, ctx)
            name = f"v{version}-{shape.replace(':', 'x')}"
            (self.work.out / f"{name}.edl.json").write_text(edl.model_dump_json(indent=1), encoding="utf-8")
            t0 = time.monotonic()
            done = [0]
            total = max(1, len(edl.segments))

            def shot():
                done[0] += 1
                self.progress(base + span * (0.05 + 0.8 * done[0] / total), f"Rendering the {shape} video")

            result = render_edl(edl, self.project, self.cfg, quality="final", out_path=self.work.out / f"{name}.mp4",
                                on_shot=shot, faces=self.faces_seen())
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


_POSTERING: set[str] = set()  # videos whose still is being made right now
_POSTER_FAILED: set[str] = set()  # videos that gave no still: not tried again on every page load
_POSTER_LOCK = __import__("threading").Lock()


def poster_for(video: Path) -> Path | None:
    """The still a video's player shows before it plays (a film often opens on black, which reads as broken): a frame
    about a quarter of the way in, next to the video with the same name (.jpg). Never fails what made the video."""
    from ..ffmpeg import ffprobe_json, run_ffmpeg

    video = Path(video)
    out = video.with_suffix(".jpg")
    key = str(video)
    if out.exists():
        return out
    with _POSTER_LOCK:
        if key in _POSTERING or key in _POSTER_FAILED:
            return None
        _POSTERING.add(key)
    tmp = out.with_name(out.stem + ".part.jpg")
    try:
        seconds = float(ffprobe_json(video, timeout=20).get("format", {}).get("duration") or 4.0)
        at = min(3.0, max(0.6, seconds * 0.25))
        run_ffmpeg(["-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", "-vf", "scale=-2:'min(960,ih)'", "-q:v", "3",
                    str(tmp)], what="video poster", timeout=30)
        if tmp.exists():
            os.replace(tmp, out)
        if not out.exists():
            _POSTER_FAILED.add(key)
        return out if out.exists() else None
    except Exception:  # noqa: BLE001 - a player without a still still plays
        tmp.unlink(missing_ok=True)
        _POSTER_FAILED.add(key)
        return None
    finally:
        with _POSTER_LOCK:
            _POSTERING.discard(key)


def folder_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def plan_json(plan: DesignPlan) -> str:
    return json.dumps(plan.model_dump(), indent=1)
