"""An edit run: intent → brief (director) → EDL drafts (editor) → final render.

All state lives on disk, so a usage-limit pause loses nothing, and every agent output is validated
here rather than trusted: the agents do creative work, this module keeps the process honest.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from ..config import Config
from ..edit.snap import beat_alignment, snap_to_beats
from ..edit.timeline import Timeline, build_timeline, choose_fps
from ..edit.validate import validate_edl
from ..music import MusicAnalysis, load_or_analyze
from ..perceive.catalog import build_catalog, check_analysis
from ..perceive.contact_sheet import build_sheet, format_time, grab_frames
from ..render import render_edl
from ..schemas import EDL, ClipAnalysis, EditBrief, Interval, canvas_for
from ..workspace import Project, write_model, write_text_atomic

RUN_VERSION = 1


class RunError(RuntimeError):
    pass


class RunState(BaseModel):
    version: int = RUN_VERSION
    run_id: str
    clips_dir: str
    created: datetime
    intent: str
    platform: str
    resolution: str
    target_seconds: float
    fps: int
    music: str | None = None
    stage: Literal["brief", "edit", "review", "done"] = "brief"
    iteration: int = 0
    final: str | None = None
    history: list[str] = Field(default_factory=list)


@dataclass
class SubmitResult:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    draft: Path | None = None
    sheets: list[Path] = field(default_factory=list)


class Run:
    def __init__(self, path: str | Path):
        self.dir = Path(path).expanduser().resolve()

    @property
    def state_path(self) -> Path:
        return self.dir / "run.json"

    @property
    def brief_path(self) -> Path:
        return self.dir / "brief.json"

    @property
    def catalog_md(self) -> Path:
        return self.dir / "catalog.md"

    @property
    def music_json(self) -> Path:
        return self.dir / "music.json"

    def iteration_dir(self, n: int) -> Path:
        return self.dir / "iterations" / f"{n:02d}"

    def load(self) -> RunState:
        if not self.state_path.exists():
            raise RunError(f"No edit run at {self.dir}")
        return RunState.model_validate_json(self.state_path.read_text(encoding="utf-8"))

    def save(self, state: RunState, event: str | None = None) -> None:
        if event:
            state.history.append(f"{datetime.now():%Y-%m-%d %H:%M:%S} {event}")
        write_model(self.state_path, state)

    def music(self) -> MusicAnalysis | None:
        if not self.music_json.exists():
            return None
        return MusicAnalysis.model_validate_json(self.music_json.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------------------


def _analyses(project: Project) -> dict[str, ClipAnalysis]:
    out = {}
    for clip in project.clips().values():
        if clip.usable:
            analysis, problems = check_analysis(project, clip)
            if analysis and not problems:
                out[clip.clip_id] = analysis
    return out


def _moment_index(catalog: dict) -> set[str]:
    ids = set()
    for entry in catalog["clips"]:
        if entry["analysis"]:
            ids.update(f"{entry['short']}#{n + 1}" for n in range(len(entry["analysis"]["moments"])))
    return ids


def start_run(project: Project, cfg: Config, *, intent: str, platform: str, target_seconds: float,
              resolution: str = "1080p", music: Path | None = None) -> Run:
    catalog = build_catalog(project)
    missing = [e["short"] for e in catalog["clips"] if not e["analysis"]]
    if missing:
        raise RunError(f"{len(missing)} clip(s) not analysed yet ({', '.join(missing[:8])}). Run /analyze-footage first.")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run = Run(project.root / "runs" / run_id)
    run.dir.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(project.root / "catalog.md", run.catalog_md)
    clips = [c for c in project.clips().values() if c.usable]
    fps = choose_fps(clips) if cfg.output.fps == "auto" else int(cfg.output.fps)
    music_path = None
    if music is not None:
        music = Path(music).expanduser().resolve()
        if not music.is_file():
            raise RunError(f"Music file not found: {music}")
        analysis = load_or_analyze(music, project.music)
        write_model(run.music_json, analysis)
        music_path = str(music)
    state = RunState(run_id=run_id, clips_dir=str(project.clips_dir), created=datetime.now(), intent=intent,
                     platform=platform, resolution=resolution, target_seconds=target_seconds, fps=fps,
                     music=music_path)
    run.save(state, f"run started: {platform}, {target_seconds:.0f}s, music={'yes' if music_path else 'no'}")
    return run


def next_step(run: Run) -> str:
    state = run.load()
    music = f", {run.music_json}" if state.music else ""
    if state.stage == "brief":
        return (f"STAGE brief. Director: read {run.state_path} (intent, platform, length), {run.catalog_md}{music}; "
                f"write {run.brief_path} (format: `vido schema edit-brief`), then run `vido edit submit-brief \"{run.dir}\"`.")
    if state.stage == "edit":
        it = run.iteration_dir(state.iteration)
        return (f"STAGE edit, iteration {state.iteration}. Editor: read {run.brief_path}, {run.catalog_md}{music}; "
                f"write {it / 'edl.json'} (format: `vido schema edl`), then run `vido edit submit-edl \"{run.dir}\"`.")
    if state.stage == "review":
        it = run.iteration_dir(state.iteration)
        return (f"STAGE review, iteration {state.iteration}. Draft: {it / 'draft.mp4'}; shots: {it}\\shots_*.jpg; "
                f"notes: {it / 'notes.md'}. Show the user. Approve → `vido edit final \"{run.dir}\"`; "
                f"changes → `vido edit revise \"{run.dir}\"` then the editor edits the new iteration's edl.json.")
    return f"STAGE done. Final video: {state.final}"


def submit_brief(run: Run, project: Project) -> list[str]:
    state = run.load()
    if state.stage != "brief":
        return [f"run is at stage '{state.stage}', not 'brief'"]
    if not run.brief_path.exists():
        return [f"missing {run.brief_path}"]
    try:
        brief = EditBrief.model_validate_json(run.brief_path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:12]]
    except ValueError as exc:
        return [f"not valid JSON: {exc}"]
    known = _moment_index(json.loads((project.root / "catalog.json").read_text(encoding="utf-8")))
    problems = []
    referenced = {m for s in brief.structure for m in s.moments} | set(brief.must_include)
    unknown = sorted(m for m in referenced if "#" in m and m not in known)
    if unknown:
        problems.append(f"unknown moment ids: {', '.join(unknown[:10])} (ids look like c05#2; see catalog.md)")
    if not brief.structure:
        problems.append("structure is empty")
    else:
        if brief.structure[0].start > 0.5:
            problems.append("the first section must start at 0")
        for a, b in zip(brief.structure, brief.structure[1:]):
            if b.start < a.end - 0.5 or b.start > a.end + 0.5:
                problems.append(f"sections '{a.name}' and '{b.name}' should meet (end {a.end} vs start {b.start})")
        end = brief.structure[-1].end
        if abs(end - state.target_seconds) > 0.15 * state.target_seconds:
            problems.append(f"structure ends at {end:.1f}s but the target is {state.target_seconds:.0f}s (±15%)")
    if problems:
        return problems
    state.stage, state.iteration = "edit", 1
    run.iteration_dir(1).mkdir(parents=True, exist_ok=True)
    run.save(state, f"brief accepted: {brief.title!r}, {len(brief.structure)} sections")
    return []


def _prepare_edl(raw: dict, state: RunState, project: Project, cfg: Config, shorts: dict[str, str]) -> EDL:
    to_full = {s: full for full, s in shorts.items()}
    raw["project_dir"] = str(project.clips_dir)
    width, height = canvas_for(state.platform, state.resolution)
    meta = raw.get("meta") or {}
    meta.update(platform=state.platform, width=width, height=height, fps=state.fps,
                loudness_lufs=cfg.output.loudness_lufs, true_peak_db=cfg.output.true_peak_db)
    raw["meta"] = meta
    for seg in raw.get("segments", []):
        cid = seg.get("clip_id", "")
        seg["clip_id"] = to_full.get(cid, cid)
    if state.music:
        music = raw.get("music") or {}
        music["path"] = state.music
        raw["music"] = music
    else:
        raw["music"] = None
    return EDL.model_validate(raw)


def edl_markdown(edl: EDL, timeline: Timeline, shorts: dict[str, str]) -> str:
    fps = timeline.fps
    rows = ["| # | on screen | clip | source | speed | in | audio | role | why |",
            "|---|---|---|---|---|---|---|---|---|"]
    for item in timeline.items:
        s = item.seg
        trans = s.transition_in.type if s.transition_in.type == "cut" else f"{s.transition_in.type} {s.transition_in.duration:.1f}s"
        rows.append(f"| {s.id} | {format_time(item.start / fps)}–{format_time(item.end / fps)} | {shorts.get(s.clip_id, s.clip_id)} | "
                    f"{s.src_in:.2f}–{s.src_out:.2f} | {s.speed:g}x | {trans} | {s.audio.mode} | {s.role} | {s.rationale} |")
    return "\n".join(rows) + "\n"


def _shot_sheets(draft: Path, timeline: Timeline, shorts: dict[str, str], out_dir: Path) -> list[Path]:
    fps = timeline.fps
    times = [(it.start + it.frames / 2) / fps for it in timeline.items]
    frames = grab_frames(draft, times)
    labels = [f"{it.seg.id} {shorts.get(it.seg.clip_id, '')} @{format_time(t)}" for it, t in zip(timeline.items, times)]
    paths = []
    for n, start in enumerate(range(0, len(frames), 16)):
        sheet = build_sheet(frames[start:start + 16], tile_long_edge=400, labels=labels[start:start + 16])
        path = out_dir / f"shots_{n + 1:02d}.jpg"
        sheet.save(path, quality=85)
        paths.append(path)
    return paths


def submit_edl(run: Run, project: Project, cfg: Config) -> SubmitResult:
    state = run.load()
    result = SubmitResult()
    if state.stage != "edit":
        result.problems.append(f"run is at stage '{state.stage}', not 'edit'")
        return result
    it = run.iteration_dir(state.iteration)
    source = it / "edl.json"
    if not source.exists():
        result.problems.append(f"missing {source}")
        return result
    shorts = {cid: s for cid, s in ((e["clip"]["clip_id"], e["short"]) for e in
                                    json.loads((project.root / "catalog.json").read_text(encoding="utf-8"))["clips"])}
    try:
        edl = _prepare_edl(json.loads(source.read_text(encoding="utf-8")), state, project, cfg, shorts)
    except ValidationError as exc:
        result.problems += [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:12]]
        return result
    except ValueError as exc:
        result.problems.append(f"not valid JSON: {exc}")
        return result

    clips = project.clips()
    signals = {cid: s for cid in clips if (s := project.load_signals(cid)) is not None}
    analyses = _analyses(project)
    unusable = {cid: a.unusable for cid, a in analyses.items()}
    issues = validate_edl(edl, clips, signals, unusable=unusable)
    result.problems = [str(i) for i in issues if i.level == "error"]
    if result.problems:
        return result
    music = run.music()
    if music is not None:
        avoid: dict[str, list[Interval]] = {cid: list(r) for cid, r in unusable.items()}
        for cid, sig in signals.items():
            avoid.setdefault(cid, []).extend(sig.bad_intervals)
        result.notes += snap_to_beats(edl, clips, music.beats, avoid=avoid)
        issues = validate_edl(edl, clips, signals, unusable=unusable)
    result.warnings = [str(i) for i in issues if i.level == "warning"]

    timeline = build_timeline(edl, clips)
    write_model(it / "edl.final.json", edl)
    write_text_atomic(it / "edl.md", edl_markdown(edl, timeline, shorts))
    render = render_edl(edl, project, cfg, quality="draft", out_path=it / "draft.mp4")
    result.draft = Path(render.output)
    result.sheets = _shot_sheets(result.draft, timeline, shorts, it)

    lengths = [i.frames / timeline.fps for i in timeline.items]
    on_beat, cuts = beat_alignment(edl, clips, music.beats) if music else (0, 0)
    summary = [
        f"Duration {timeline.duration:.1f}s (target {state.target_seconds:.0f}s), {len(lengths)} shots, "
        f"average {sum(lengths) / len(lengths):.2f}s (shortest {min(lengths):.2f}s, longest {max(lengths):.2f}s)",
        f"Cuts on the beat: {on_beat}/{cuts}" if music else "No music",
    ]
    write_text_atomic(it / "notes.md", "\n".join(
        ["# Iteration notes", *summary, "", "## Warnings", *(f"- {w}" for w in result.warnings or ["none"]),
         "", "## Beat snapping", *(f"- {n}" for n in result.notes or ["n/a"])]) + "\n")
    result.notes = summary + result.notes
    state.stage = "review"
    run.save(state, f"iteration {state.iteration} rendered: {timeline.duration:.1f}s, {len(lengths)} shots")
    return result


def revise(run: Run) -> Path:
    state = run.load()
    if state.stage not in ("review", "done"):
        raise RunError(f"run is at stage '{state.stage}'; revise starts a new iteration after a review")
    previous = run.iteration_dir(state.iteration)
    state.iteration += 1
    nxt = run.iteration_dir(state.iteration)
    nxt.mkdir(parents=True, exist_ok=True)
    if (previous / "edl.final.json").exists():
        shutil.copyfile(previous / "edl.final.json", nxt / "edl.json")
    state.stage = "edit"
    run.save(state, f"iteration {state.iteration} started from iteration {state.iteration - 1}")
    return nxt


def finalize(run: Run, project: Project, cfg: Config, iteration: int | None = None) -> Path:
    state = run.load()
    n = iteration or state.iteration
    edl_path = run.iteration_dir(n) / "edl.final.json"
    if not edl_path.exists():
        raise RunError(f"iteration {n} has no accepted EDL")
    edl = EDL.model_validate_json(edl_path.read_text(encoding="utf-8"))
    edl.meta.loudness_lufs = cfg.output.loudness_lufs  # delivery settings follow the current config
    edl.meta.true_peak_db = cfg.output.true_peak_db
    out_dir = Path.home() / "Videos" / "VidoSuper"
    out = out_dir / f"{project.clips_dir.name}-{state.run_id}-v{n}.mp4"
    render_edl(edl, project, cfg, quality="final", out_path=out)
    state.stage, state.final = "done", str(out)
    run.save(state, f"final rendered from iteration {n}: {out}")
    return out
