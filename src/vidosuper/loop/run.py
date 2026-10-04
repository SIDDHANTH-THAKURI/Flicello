"""An edit run: intent → brief (director) → music → EDL drafts (editor) → final render.

All state lives on disk, so a usage-limit pause loses nothing, and every agent output is validated
here rather than trusted: the agents do creative work, this module keeps the process honest.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from ..config import Config
from ..edit.resync import resync_edl
from ..edit.snap import beat_alignment, snap_to_beats
from ..edit.timeline import Timeline, build_timeline, choose_fps
from ..edit.validate import validate_edl
from ..music import MusicAnalysis, load_or_analyze
from ..perceive.catalog import build_catalog, check_analysis
from ..perceive.contact_sheet import build_sheet, format_time, grab_frames
from ..render import render_edl
from ..schemas import EDL, ClipAnalysis, EditBrief, Interval, MusicSpec, canvas_for
from ..workspace import Project, write_model, write_text_atomic

RUN_VERSION = 2
MusicMode = Literal["auto", "file", "trending", "none"]

TRENDING_CAVEAT = (
    "TRENDING AUDIO: this track is most likely copyrighted commercial music. Using it inside TikTok or "
    "Instagram Reels through the app's own sound library is covered by the platform's licence, but a video "
    "file with the track baked in, uploaded anywhere else (YouTube, etc.), can be muted, blocked or "
    "demonetised. The final render therefore makes a POST version WITHOUT the track (add the sound in the "
    "app at the start time given) and a PREVIEW with it, for your eyes only."
)


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
    music_mode: MusicMode = "file"
    music: str | None = None
    music_credit: str | None = None
    music_start_hint: float | None = None
    sound_name: str | None = None  # trending mode: the sound's name in the app
    stage: Literal["brief", "music", "edit", "review", "done"] = "brief"
    iteration: int = 0
    final: str | None = None
    scores: dict[int, float] = Field(default_factory=dict)  # iteration -> weighted critic score
    best_iteration: int | None = None
    verdicts: dict[int, str] = Field(default_factory=dict)  # iteration -> first | new | best | inconsistent
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

    @property
    def music_md(self) -> Path:
        return self.dir / "music.md"

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


def _set_music(run: Run, state: RunState, project: Project, path: Path, *, start_hint: float | None = None,
               credit: str | None = None, title: str | None = None) -> MusicAnalysis:
    analysis = load_or_analyze(path, project.music)
    write_model(run.music_json, analysis)
    state.music, state.music_credit, state.music_start_hint = str(path), credit, start_hint
    lines = ["# Music", f"Track: {title or path.name}", analysis.summary()]
    if start_hint is not None:
        lines.append(f"Suggested music.start: {start_hint:.2f}s (a bar start where the track's energy best fits "
                     "the planned dynamics).")
    if credit:
        lines.append(f"Credit line (goes in the video description): {credit}")
    if state.music_mode == "trending":
        lines.append(TRENDING_CAVEAT)
    lines.append("Beats, bar starts and energy per second: music.json.")
    write_text_atomic(run.music_md, "\n\n".join(lines) + "\n")
    return analysis


def start_run(project: Project, cfg: Config, *, intent: str, platform: str, target_seconds: float,
              resolution: str = "1080p", music: Path | None = None, music_mode: MusicMode | None = None,
              sound_name: str | None = None) -> Run:
    catalog = build_catalog(project)
    missing = [e["short"] for e in catalog["clips"] if not e["analysis"]]
    if missing:
        raise RunError(f"{len(missing)} clip(s) not analysed yet ({', '.join(missing[:8])}). Run /analyze-footage first.")
    mode: MusicMode = music_mode or ("file" if music else cfg.music.mode)
    if mode in ("file", "trending") and music is None:
        raise RunError(f"music mode '{mode}' needs a music file (--music)")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run = Run(project.root / "runs" / run_id)
    run.dir.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(project.root / "catalog.md", run.catalog_md)
    clips = [c for c in project.clips().values() if c.usable]
    fps = choose_fps(clips) if cfg.output.fps == "auto" else int(cfg.output.fps)
    state = RunState(run_id=run_id, clips_dir=str(project.clips_dir), created=datetime.now(), intent=intent,
                     platform=platform, resolution=resolution, target_seconds=target_seconds, fps=fps,
                     music_mode=mode, sound_name=sound_name)
    if music is not None:
        music = Path(music).expanduser().resolve()
        if not music.is_file():
            raise RunError(f"Music file not found: {music}")
        from ..music.sources import load_track_info

        info = load_track_info(music)
        _set_music(run, state, project, music, credit=info.attribution if info else None,
                   title=f"{info.title} by {info.artist}" if info else None)
    run.save(state, f"run started: {platform}, {target_seconds:.0f}s, music mode {mode}")
    return run


def next_step(run: Run) -> str:
    state = run.load()
    music = f", {run.music_md}, {run.music_json}" if state.music else ""
    if state.stage == "brief":
        need = " Include music_brief (the track is fetched automatically from it)." if state.music_mode == "auto" else ""
        return (f"STAGE brief. Director: read {run.state_path} (intent, platform, length), {run.catalog_md}{music}; "
                f"write {run.brief_path} (format: `vido schema edit-brief`).{need} Then run "
                f"`vido edit submit-brief \"{run.dir}\"`.")
    if state.stage == "music":
        return (f"STAGE music. Run `vido music find \"{run.dir}\"`: it searches safe-to-post music for the brief's "
                "music_brief, scores the candidates and picks the best fit.")
    if state.stage == "edit":
        it = run.iteration_dir(state.iteration)
        return (f"STAGE edit, iteration {state.iteration}. Editor: read {run.brief_path}, {run.catalog_md}{music}; "
                f"write {it / 'edl.json'} (format: `vido schema edl`), then run `vido edit submit-edl \"{run.dir}\"`.")
    if state.stage == "review":
        from ..config import load_config

        it = run.iteration_dir(state.iteration)
        return (f"STAGE review, iteration {state.iteration}. Draft: {it / 'draft.mp4'}. "
                f"{loop_status(run, load_config())} (Other music any time: `vido edit set-music \"{run.dir}\" <file>`.)")
    return f"STAGE done. Final video: {state.final}"


def _brief_problems(brief: EditBrief, state: RunState, known: set[str], music_needed: bool) -> list[str]:
    problems = []
    referenced = {m for s in brief.structure for m in s.moments} | set(brief.must_include)
    unknown = sorted(m for m in referenced if "#" in m and m not in known)
    if unknown:
        problems.append(f"unknown moment ids: {', '.join(unknown[:10])} (ids look like c05#2; see catalog.md)")
    if not brief.thesis.strip():
        problems.append("thesis is empty: say what this footage is really about (the point of view)")
    if len(brief.dynamics) < 4:
        problems.append("dynamics needs at least 4 points (intensity 0-1 across the video)")
    elif min(brief.dynamics) > 0.45 or max(brief.dynamics) - min(brief.dynamics) < 0.3:
        problems.append("dynamics has no real valley: plan at least one deliberate quiet/slow stretch")
    if not brief.convention_break.strip():
        problems.append("convention_break is empty: name the expected convention this edit breaks, and why")
    if music_needed and brief.music_brief is None:
        problems.append("music_brief is required: the track is fetched automatically from it")
    if not brief.structure:
        problems.append("structure is empty")
    else:
        if brief.structure[0].start > 0.5:
            problems.append("the first section must start at 0")
        for a, b in zip(brief.structure, brief.structure[1:]):
            if abs(b.start - a.end) > 0.5:
                problems.append(f"sections '{a.name}' and '{b.name}' should meet (end {a.end} vs start {b.start})")
        end = brief.structure[-1].end
        if abs(end - state.target_seconds) > 0.15 * state.target_seconds:
            problems.append(f"structure ends at {end:.1f}s but the target is {state.target_seconds:.0f}s (±15%)")
    return problems


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
    music_needed = state.music_mode == "auto" and not state.music
    problems = _brief_problems(brief, state, known, music_needed)
    if problems:
        return problems
    if music_needed:
        state.stage = "music"
    else:
        state.stage, state.iteration = "edit", 1
        run.iteration_dir(1).mkdir(parents=True, exist_ok=True)
    run.save(state, f"brief accepted: {brief.title!r}, {len(brief.structure)} sections")
    return []


def find_music_for_run(run: Run, project: Project, cfg: Config, *, provider: str = "auto", shortlist: int | None = None,
                       pick: int | None = None, log: Callable[[str], None] = print) -> list:
    """Search, score and set the best-fitting safe-to-post track (stage music → edit)."""
    from ..music.select import candidates_markdown, find_music
    from ..music.sources import providers_from_env

    state = run.load()
    brief = EditBrief.model_validate_json(run.brief_path.read_text(encoding="utf-8"))
    if brief.music_brief is None:
        raise RunError("the brief has no music_brief")
    providers = providers_from_env(project.music / "search-cache", provider)
    log(f"Searching music with: {', '.join(p.name for p in providers)}")
    scored = find_music(brief.music_brief, state.target_seconds, providers, cfg.workspace_root / "music" / "library",
                        project.music, shortlist=shortlist or cfg.music.shortlist, log=log)
    if not scored:
        raise RunError("no usable tracks found; try other mood/genre words in music_brief, or pass a file")
    chosen = (pick - 1) if pick else 0
    if not 0 <= chosen < len(scored):
        raise RunError(f"pick must be 1-{len(scored)}")
    write_text_atomic(run.dir / "music_candidates.md", candidates_markdown(scored, chosen))
    best = scored[chosen]
    change_music(run, project, cfg, best.path, start=best.offset, credit=best.candidate.attribution,
                 title=f"{best.candidate.title} by {best.candidate.artist}")
    return scored


def change_music(run: Run, project: Project, cfg: Config, path: Path, *, start: float | None = None,
                 credit: str | None = None, title: str | None = None) -> SubmitResult | None:
    """Set the run's track. If a cut already exists, re-sync it to the new music in a new iteration."""
    state = run.load()
    old = run.music()
    old_spec = None
    if state.stage in ("review", "done"):
        final_edl = run.iteration_dir(state.iteration) / "edl.final.json"
        if final_edl.exists():
            old_spec = EDL.model_validate_json(final_edl.read_text(encoding="utf-8"))
    if credit is None and title is None:
        from ..music.sources import load_track_info

        info = load_track_info(Path(path))
        if info:
            credit, title = info.attribution, f"{info.title} by {info.artist}"
    new = _set_music(run, state, project, Path(path).resolve(), start_hint=start, credit=credit, title=title)
    if state.stage == "music":
        state.stage, state.iteration = "edit", 1
        run.iteration_dir(1).mkdir(parents=True, exist_ok=True)
        run.save(state, f"music chosen: {title or Path(path).name}")
        return None
    if old_spec is None or old is None or old_spec.music is None:
        run.save(state, f"music set: {title or Path(path).name}")
        return None
    clips = project.clips()
    begin = start if start is not None else (new.downbeats[0] if new.downbeats else 0.0)
    notes = resync_edl(old_spec, clips, old, new, str(Path(path).resolve()), begin)
    state.iteration += 1
    nxt = run.iteration_dir(state.iteration)
    nxt.mkdir(parents=True, exist_ok=True)
    write_model(nxt / "edl.json", old_spec)
    state.stage = "edit"
    run.save(state, f"music changed to {title or Path(path).name}; cut re-synced into iteration {state.iteration}")
    result = submit_edl(run, project, cfg)
    result.notes = notes + result.notes
    return result


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
    for layer in raw.get("sounds", []):
        src = layer.get("source", "")
        if src.startswith("clip:") and src[5:] in to_full:
            layer["source"] = "clip:" + to_full[src[5:]]
    if state.music:
        music = raw.get("music") or {}
        music.setdefault("start", state.music_start_hint or 0.0)
        music["path"] = state.music
        raw["music"] = music
    else:
        raw["music"] = None
    return EDL.model_validate(raw)


def edl_markdown(edl: EDL, timeline: Timeline, shorts: dict[str, str]) -> str:
    fps = timeline.fps
    rows = ["| # | on screen | clip | source | speed | in | out | audio | role | why |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for item in timeline.items:
        s = item.seg
        trans = s.transition_in.type if s.transition_in.type == "cut" else f"{s.transition_in.type} {s.transition_in.duration:.1f}s"
        extras = "".join([f" zoom {s.zoom:g}" if s.zoom > 1 else "", f" freeze {s.freeze:g}s" if s.freeze else "",
                          f" J{s.audio.lead:g}" if s.audio.lead else "", f" L{s.audio.tail:g}" if s.audio.tail else ""])
        rows.append(f"| {s.id} | {format_time(item.start / fps)}–{format_time(item.end / fps)} | {shorts.get(s.clip_id, s.clip_id)} | "
                    f"{s.src_in:.2f}–{s.src_out:.2f}{extras} | {s.speed:g}x | {trans} | {s.out_sync} | {s.audio.mode} | "
                    f"{s.role} | {s.rationale} |")
    if edl.texts:
        rows += ["", "Text: " + "; ".join(f"{t.start:.1f}-{t.end:.1f}s \"{t.text}\"" for t in edl.texts)]
    if edl.sounds:
        rows += ["", "Sound layers: " + "; ".join(f"{s.source.split(':')[0]}:{shorts.get(s.source[5:], s.source[4:] or s.source)} "
                                                 f"@{s.at:.1f}s for {s.duration:.1f}s ({s.gain_db:+.0f} dB)" for s in edl.sounds)]
    if edl.music and (edl.music.automation or edl.music.jumps):
        auto = ", ".join(f"{p.t:.1f}s {p.gain_db:+.0f} dB" for p in edl.music.automation)
        jumps = ", ".join(f"at {j.at:.1f}s to {j.to:.1f}s" for j in edl.music.jumps)
        rows += ["", f"Music automation: {auto or 'none'}; music edits: {jumps or 'none'}"]
    return "\n".join(rows) + "\n"


def _shot_sheets(draft: Path, timeline: Timeline, shorts: dict[str, str], out_dir: Path) -> list[Path]:
    fps = timeline.fps
    times = [(it.start + it.frames / 2) / fps for it in timeline.items]
    frames = grab_frames(draft, times)
    labels = [f"{it.seg.id} {shorts.get(it.seg.clip_id, '')} @{format_time(t)}" for it, t in zip(timeline.items, times)]
    for old in out_dir.glob("shots_*.jpg"):
        old.unlink()
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
    shorts = {e["clip"]["clip_id"]: e["short"] for e in
              json.loads((project.root / "catalog.json").read_text(encoding="utf-8"))["clips"]}
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
    if music is not None and edl.music is not None:
        avoid: dict[str, list[Interval]] = {cid: list(r) for cid, r in unusable.items()}
        for cid, sig in signals.items():
            avoid.setdefault(cid, []).extend(sig.bad_intervals)
        result.notes += snap_to_beats(edl, clips, music, avoid=avoid)
        issues = validate_edl(edl, clips, signals, unusable=unusable)
    result.warnings = [str(i) for i in issues if i.level == "warning"]

    timeline = build_timeline(edl, clips)
    write_model(it / "edl.final.json", edl)
    write_text_atomic(it / "edl.md", edl_markdown(edl, timeline, shorts))
    render = render_edl(edl, project, cfg, quality="draft", out_path=it / "draft.mp4")
    result.draft = Path(render.output)
    result.sheets = _shot_sheets(result.draft, timeline, shorts, it)

    lengths = [i.frames / timeline.fps for i in timeline.items]
    summary = [f"Duration {timeline.duration:.1f}s (target {state.target_seconds:.0f}s), {len(lengths)} shots, "
               f"average {sum(lengths) / len(lengths):.2f}s (shortest {min(lengths):.2f}s, longest {max(lengths):.2f}s)"]
    if music is not None and edl.music is not None:
        on, musical, free = beat_alignment(edl, clips, music)
        summary.append(f"Musical cuts on their beat/bar: {on}/{musical}; free cuts (timed to motion/sound): {free}")
    else:
        summary.append("No music")
    write_text_atomic(it / "notes.md", "\n".join(
        ["# Iteration notes", *summary, "", "## Warnings", *(f"- {w}" for w in result.warnings or ["none"]),
         "", "## Beat snapping", *(f"- {n}" for n in result.notes or ["n/a"])]) + "\n")
    result.notes = summary + result.notes
    try:  # evidence for the critics; never let it block a draft
        from .evidence import build_evidence

        brief = EditBrief.model_validate_json(run.brief_path.read_text(encoding="utf-8")) if run.brief_path.exists() else None
        build_evidence(run.dir, it, project, brief, music, shorts)
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"evidence pack failed ({type(exc).__name__}: {exc}); run `vido edit evidence` again")
    state.stage = "review"
    run.save(state, f"iteration {state.iteration} rendered: {timeline.duration:.1f}s, {len(lengths)} shots")
    return result


def revise(run: Run, from_iteration: int | None = None) -> Path:
    """Start the next iteration from an earlier EDL — by default the best one so far (a revision that
    lost to the best is not built upon)."""
    state = run.load()
    if state.stage not in ("review", "done"):
        raise RunError(f"run is at stage '{state.stage}'; revise starts a new iteration after a review")
    base = from_iteration or state.best_iteration or state.iteration
    previous = run.iteration_dir(base)
    state.iteration += 1
    nxt = run.iteration_dir(state.iteration)
    nxt.mkdir(parents=True, exist_ok=True)
    if (previous / "edl.final.json").exists():
        shutil.copyfile(previous / "edl.final.json", nxt / "edl.json")
    for name in ("review.md", "feedback.md"):  # what the editor must address next
        for source in (run.iteration_dir(state.iteration - 1) / name, previous / name):
            if source.exists():
                shutil.copyfile(source, nxt / f"previous_{name}")
                break
    state.stage = "edit"
    run.save(state, f"iteration {state.iteration} started from iteration {base}")
    return nxt


def needs_judgement(state: RunState) -> bool:
    return (state.best_iteration is not None and state.best_iteration != state.iteration
            and state.iteration not in state.verdicts)


def record_review(run: Run, cfg: Config):
    from .review import aggregate, missing_critiques

    state = run.load()
    it = run.iteration_dir(state.iteration)
    missing = missing_critiques(it)
    if missing:
        raise RunError("critiques missing or invalid: " + "; ".join(f"{k}: {v[0]}" for k, v in missing.items()))
    review = aggregate(it, cfg)
    state.scores[state.iteration] = review.score
    if state.best_iteration is None:
        state.best_iteration = state.iteration
        state.verdicts[state.iteration] = "first"
    run.save(state, f"review of iteration {state.iteration}: {review.score:.2f}/10, {review.blockers} blockers, "
                    f"{review.majors} majors -> {review.decision}")
    return review


def record_judgement(run: Run) -> tuple[str, list[str]]:
    from .review import decide

    state = run.load()
    verdict, reasons = decide(run.iteration_dir(state.iteration) / "judge")
    if verdict is None:
        raise RunError("judge verdicts missing or invalid: " + "; ".join(reasons))
    previous_best = state.best_iteration
    if verdict == "new":
        state.best_iteration = state.iteration
    state.verdicts[state.iteration] = verdict
    run.save(state, f"judge: iteration {state.iteration} vs best {previous_best}: {verdict}")
    return verdict, reasons


def loop_status(run: Run, cfg: Config) -> str:
    """What the review loop needs next, for the orchestrator."""
    from .review import ADVERSARY, CRITICS, missing_critiques

    state = run.load()
    it = run.iteration_dir(state.iteration)
    if not (it / "evidence.md").exists():
        return f"Build the evidence pack: `vido edit evidence \"{run.dir}\"`."
    missing = missing_critiques(it)
    if missing:
        names = [n for n in (*CRITICS, ADVERSARY) if n in missing]
        return (f"Critiques needed for iteration {state.iteration}: {', '.join(names)}. Launch one `critic` subagent per "
                f"rubric ({', '.join(n for n in names if n != ADVERSARY)}) and the `adversary`, in parallel; each writes "
                f"{it / 'critiques'}\\<name>.json. Then `vido edit review \"{run.dir}\"`.")
    if state.iteration not in state.scores:
        return f"All critiques in: run `vido edit review \"{run.dir}\"`."
    if needs_judgement(state):
        jd = it / "judge"
        todo = [f for f in ("ab.json", "ba.json") if not (jd / f).exists()]
        if todo:
            best = run.iteration_dir(state.best_iteration)
            return (f"Judge needed: iteration {state.iteration} vs best iteration {state.best_iteration}. Launch the `judge` "
                    f"subagent twice in parallel: ab.json with A = {best}, B = {it}; ba.json with A = {it}, B = {best}; "
                    f"both written to {jd}. Then `vido edit judge \"{run.dir}\"`.")
        return f"Both verdicts in: run `vido edit judge \"{run.dir}\"`."
    review = json.loads((it / "review.json").read_text(encoding="utf-8"))
    rounds = len(state.scores)
    recent = [state.verdicts.get(n) for n in sorted(state.verdicts)][-cfg.loop.stop_after_no_gain:]
    plateau = len(recent) == cfg.loop.stop_after_no_gain and all(v in ("best", "inconsistent") for v in recent)
    best = state.best_iteration or state.iteration
    if review["decision"] == "approve" or rounds >= cfg.loop.max_rounds or plateau:
        why = ("quality bar reached" if review["decision"] == "approve" else
               f"round limit ({cfg.loop.max_rounds}) reached" if rounds >= cfg.loop.max_rounds else "no gain in the last rounds")
        return (f"Loop done ({why}). Best is iteration {best} (score {state.scores.get(best, 0):.2f}). Show the user that draft; "
                f"on approval `vido edit final \"{run.dir}\" --iteration {best}`.")
    checkpoint = rounds in cfg.loop.checkpoints
    return ((f"CHECKPOINT: show the user iteration {best}'s draft and review.md, and ask for 👍/👎 + a note before revising. "
             if checkpoint else "") +
            f"Revise: `vido edit revise \"{run.dir}\"` (starts from best iteration {best}, copies review.md as "
            f"previous_review.md), then launch the `editor` with the run folder to address the top issues.")


def finalize(run: Run, project: Project, cfg: Config, iteration: int | None = None) -> list[Path]:
    """Full-quality render(s) into Videos\\VidoSuper, plus a credits file when the music needs one."""
    state = run.load()
    n = iteration or state.iteration
    edl_path = run.iteration_dir(n) / "edl.final.json"
    if not edl_path.exists():
        raise RunError(f"iteration {n} has no accepted EDL")
    edl = EDL.model_validate_json(edl_path.read_text(encoding="utf-8"))
    edl.meta.loudness_lufs = cfg.output.loudness_lufs  # delivery settings follow the current config
    edl.meta.true_peak_db = cfg.output.true_peak_db
    out_dir = Path.home() / "Videos" / "VidoSuper"
    stem = f"{project.clips_dir.name}-{state.run_id}-v{n}"
    outputs: list[Path] = []
    if state.music_mode == "trending" and edl.music is not None:
        post = edl.model_copy(deep=True)
        post.music = None
        outputs.append(Path(render_edl(post, project, cfg, quality="final",
                                       out_path=out_dir / f"{stem}-POST-add-sound-in-app.mp4").output))
        outputs.append(Path(render_edl(edl, project, cfg, quality="final",
                                       out_path=out_dir / f"{stem}-PREVIEW-ONLY-with-sound.mp4").output))
        sound = state.sound_name or Path(state.music or "").stem
        write_text_atomic(out_dir / f"{stem}-how-to-post.txt", (
            f"{TRENDING_CAVEAT}\n\nTo post: upload {outputs[0].name} in TikTok / Instagram Reels, add the sound "
            f"\"{sound}\" from the app's library and set its start to {edl.music.start:.1f}s into the song, so the "
            "cuts land on the music as edited.\n"))
    else:
        outputs.append(Path(render_edl(edl, project, cfg, quality="final", out_path=out_dir / f"{stem}.mp4").output))
        if state.music_credit:
            write_text_atomic(out_dir / f"{stem}-credits.txt",
                              f"Music: {state.music_credit}\n\n(Paste this line into your video's description.)\n")
    state.stage, state.final = "done", str(outputs[0])
    run.save(state, f"final rendered from iteration {n}: {', '.join(p.name for p in outputs)}")
    return outputs
