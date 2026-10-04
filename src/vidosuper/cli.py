"""`vido` command line."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from . import __version__
from .config import Config, load_config
from .edit import baseline_edl, choose_fps, validate_edl
from .ffmpeg import capabilities, ffmpeg_bin, ffprobe_bin
from .ingest import ingest_folder
from .ingest.chapters import chronological
from .perceive import measure_project
from .render import EDLValidationError, render_edl
from .schemas import EDL, PLATFORM_CANVAS, EditMeta, MusicSpec, canvas_for
from .workspace import Project, write_model

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="VidoSuper: AI video editor toolkit. Most work is driven by the /edit-video skill in Claude Code.")

# When piped (e.g. run by Claude Code), Windows defaults to cp1252; force UTF-8 and a wide layout.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and (_stream.encoding or "").lower().replace("-", "") != "utf8":
        _stream.reconfigure(encoding="utf-8", errors="replace")
console = Console() if sys.stdout.isatty() else Console(width=160)

REQUIRED_ENCODERS = ("libx264", "aac")
REQUIRED_FILTERS = ("zscale", "tonemap", "xfade", "gblur", "fade", "overlay", "tpad", "atempo", "apad",
                    "aresample", "concat", "settb", "eq")
OPTIONAL_FILTERS = ("vidstabdetect", "vidstabtransform", "subtitles", "drawtext")
PACKAGES = (("numpy", True), ("cv2", True), ("scipy", True), ("soundfile", True), ("pyloudnorm", True),
            ("PIL", True), ("pydantic", True), ("librosa", False), ("faster_whisper", False),
            ("scenedetect", False), ("imagehash", False), ("jinja2", False))


def _progress() -> Progress:
    return Progress(SpinnerColumn(), TextColumn("[bold]{task.description}"), BarColumn(), MofNCompleteColumn(),
                    TimeElapsedColumn(), console=console, transient=False)


def _analyze(folder: Path, cfg: Config, force: bool = False) -> Project:
    from .ingest.scan import scan_folder

    total = len(scan_folder(Path(folder).expanduser().resolve(), cfg.ingest.extensions))
    with _progress() as progress:
        task = progress.add_task("Ingest (probe + proxies)", total=total)
        project, clips = ingest_folder(folder, cfg, force=force, on_clip=lambda _: progress.advance(task))
        progress.update(task, completed=total)
        usable = [c for c in clips if c.usable]
        task2 = progress.add_task("Measure (shake, blur, light, motion)", total=len(usable))
        measure_project(project, cfg, force=force, on_clip=lambda _: progress.advance(task2))
        progress.update(task2, completed=len(usable))
    failed = [c for c in clips if not c.usable]
    console.print(f"[green]{len(usable)} clips ready[/] in {escape(str(project.root))}")
    for clip in failed:
        console.print(f"[yellow]skipped[/] {escape(clip.rel_path)}: {escape(clip.error or '')}")
    return project


@app.command()
def doctor() -> None:
    """Check that FFmpeg, Python packages and the workspace are ready."""
    cfg = load_config()
    ok = True
    table = Table(title=f"VidoSuper {__version__} doctor", show_lines=False)
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Details", overflow="fold")

    def row(name: str, good: bool, detail: str, required: bool = True) -> None:
        nonlocal ok
        status = "[green]OK[/]" if good else ("[red]MISSING[/]" if required else "[yellow]optional[/]")
        ok = ok and (good or not required)
        table.add_row(name, status, detail)

    try:
        caps = capabilities()
        row("ffmpeg", caps["major"] >= 7, f"{caps['version']}\n{ffmpeg_bin()}")
        row("ffprobe", True, ffprobe_bin())
        missing_enc = [e for e in REQUIRED_ENCODERS if e not in caps["encoders"]]
        row("encoders", not missing_enc, "missing: " + ", ".join(missing_enc) if missing_enc else ", ".join(REQUIRED_ENCODERS))
        missing_f = [f for f in REQUIRED_FILTERS if f not in caps["filters"]]
        row("filters", not missing_f, "missing: " + ", ".join(missing_f) if missing_f else f"{len(REQUIRED_FILTERS)} required present")
        opt_f = [f for f in OPTIONAL_FILTERS if f not in caps["filters"]]
        row("extra filters", not opt_f, "missing: " + ", ".join(opt_f) if opt_f else "stabilization + captions available", required=False)
        row("Intel QuickSync", "h264_qsv" in caps["encoders"] and "qsv" in caps["hwaccels"],
            "hardware encode/decode available" if "qsv" in caps["hwaccels"] else "not available", required=False)
    except FileNotFoundError as exc:
        row("ffmpeg", False, str(exc))

    for name, required in PACKAGES:
        try:
            module = importlib.import_module(name)
            row(f"python: {name}", True, str(getattr(module, "__version__", "")), required)
        except Exception as exc:  # noqa: BLE001 - report anything that breaks an import
            row(f"python: {name}", False, f"{type(exc).__name__}: {exc}", required)

    root = cfg.workspace_root
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        free_gb = shutil.disk_usage(root).free / 1e9
        detail = f"{root}\n{free_gb:.0f} GB free"
        if "onedrive" in str(root).lower():
            detail += "\nWARNING: inside OneDrive; large files will sync"
        row("workspace", free_gb > 5, detail)
    except OSError as exc:
        row("workspace", False, f"{root}: {exc}")
    row("python", sys.version_info >= (3, 11), sys.version.split()[0])
    row("mode", True, cfg.mode)
    console.print(table)
    if not ok:
        raise typer.Exit(1)


@app.command()
def analyze(folder: Path = typer.Argument(..., help="Folder with your raw clips"),
            force: bool = typer.Option(False, help="Redo everything, ignoring the cache")) -> None:
    """Ingest + measure a clips folder (cached; safe to re-run)."""
    _analyze(folder, load_config(), force)


@app.command()
def clips(folder: Path = typer.Argument(..., help="Folder with your raw clips"),
          as_json: bool = typer.Option(False, "--json", help="Machine-readable output")) -> None:
    """Show what was found in a clips folder, with technical quality."""
    cfg = load_config()
    project = Project(folder, cfg)
    manifest = project.clips()
    if as_json:
        payload = []
        for clip in manifest.values():
            sig = project.load_signals(clip.clip_id)
            payload.append({"clip": clip.model_dump(mode="json"),
                            "signals": sig.model_dump(mode="json") if sig else None})
        print(json.dumps(payload, indent=1))
        return
    table = Table(title=str(project.clips_dir))
    for col in ("clip", "ch", "len", "size", "fps", "flags", "quality", "shake", "sharp", "bright", "bad parts"):
        table.add_column(col)
    for clip in manifest.values():
        if not clip.usable:
            table.add_row(clip.clip_id, "-", "-", "-", "-", "[red]error[/]", escape(clip.error or ""), "", "", "", "")
            continue
        sig = project.load_signals(clip.clip_id)
        flags = " ".join(f for f, on in (("HDR", clip.hdr), ("VFR", clip.vfr), ("no-audio", not clip.has_audio),
                                         (f"rot{clip.rotation}", clip.rotation != 0)) if on)
        bad = ", ".join(f"{b.start:.1f}-{b.end:.1f}s {b.reason}" for b in sig.bad_intervals[:3]) if sig else ""
        table.add_row(
            clip.clip_id, str(clip.chapter), f"{clip.duration:.1f}s", f"{clip.width}x{clip.height}", f"{clip.fps:.0f}",
            flags, f"{sig.quality_mean:.2f}" if sig else "-", f"{sig.shake_median:.3f}" if sig else "-",
            f"{sig.sharpness_median:.2f}" if sig else "-", f"{sig.brightness_mean:.0f}" if sig else "-", escape(bad),
        )
    console.print(table)


@app.command()
def baseline(
    folder: Path = typer.Argument(..., help="Folder with your raw clips"),
    duration: float = typer.Option(60.0, help="Target length in seconds"),
    platform: str = typer.Option("youtube", help="youtube | reels | shorts | tiktok | square"),
    resolution: str = typer.Option("1080p", help="1080p | 4k"),
    music: Path | None = typer.Option(None, help="Optional music file"),
    quality: str = typer.Option("draft", help="draft (fast preview) | final (full quality)"),
    shot: float = typer.Option(3.0, help="Seconds per shot"),
    out: Path | None = typer.Option(None, help="Output file"),
    open_file: bool = typer.Option(False, "--open", help="Open the result when done"),
) -> None:
    """Non-AI montage (best technical window per clip). Tests the whole pipeline end to end."""
    if platform not in PLATFORM_CANVAS:
        raise typer.BadParameter(f"platform must be one of {', '.join(PLATFORM_CANVAS)}")
    cfg = load_config()
    project = _analyze(folder, cfg)
    clip_list = chronological([c for c in project.clips().values() if c.usable])
    signals = {c.clip_id: s for c in clip_list if (s := project.load_signals(c.clip_id)) is not None}
    fps = choose_fps(clip_list) if cfg.output.fps == "auto" else int(cfg.output.fps)
    width, height = canvas_for(platform, resolution)
    meta = EditMeta(title=f"{project.clips_dir.name} (baseline)", platform=platform, width=width, height=height,
                    fps=fps, loudness_lufs=cfg.output.loudness_lufs, true_peak_db=cfg.output.true_peak_db)
    music_spec = MusicSpec(path=str(music.expanduser().resolve())) if music else None
    edl = baseline_edl(clip_list, signals, project_dir=str(project.clips_dir), meta=meta,
                       target_seconds=duration, shot_seconds=shot, music=music_spec)
    if not edl.segments:
        console.print("[red]No usable shots found.[/]")
        raise typer.Exit(1)
    edl_path = project.edls / "baseline.json"
    write_model(edl_path, edl)
    _render(edl, project, cfg, quality, out or project.renders / f"baseline_{platform}_{quality}.mp4", open_file)
    console.print(f"EDL: {escape(str(edl_path))}")


@app.command()
def render(
    edl_file: Path = typer.Argument(..., help="EDL JSON file"),
    quality: str = typer.Option("draft", help="draft | final"),
    out: Path | None = typer.Option(None, help="Output file"),
    open_file: bool = typer.Option(False, "--open", help="Open the result when done"),
) -> None:
    """Render an EDL."""
    cfg = load_config()
    edl = EDL.model_validate_json(edl_file.read_text(encoding="utf-8"))
    project = Project(edl.project_dir, cfg)
    _render(edl, project, cfg, quality, out or project.renders / f"{edl_file.stem}_{quality}.mp4", open_file)


def _render(edl: EDL, project: Project, cfg: Config, quality: str, out: Path, open_file: bool) -> None:
    try:
        with _progress() as progress:
            task = progress.add_task(f"Render {quality}", total=len(edl.segments))
            result = render_edl(edl, project, cfg, quality=quality, out_path=out,
                                on_shot=lambda: progress.advance(task))
    except EDLValidationError as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Rendered[/] {escape(result.output)}\n"
        f"  {result.width}x{result.height} @ {result.fps} fps, {result.duration:.1f}s, {result.shots} shots "
        f"({result.shots_rendered} new), {result.seconds:.0f}s to render"
    )
    if result.loudness_measured is not None:
        console.print(f"  loudness {result.loudness_measured:.1f} LUFS -> {edl.meta.loudness_lufs:.0f} LUFS")
    for warning in result.warnings:
        console.print(f"  [yellow]{escape(warning)}[/]")
    if open_file and os.name == "nt":
        os.startfile(result.output)  # noqa: S606 - opening the user's own render


perceive_app = typer.Typer(no_args_is_help=True, help="Phase 2: material for the clip-analyst subagents.")
app.add_typer(perceive_app, name="perceive")


def _find_clip(project: Project, key: str):
    clips = project.clips()
    if key in clips:
        return clips[key]
    matches = [c for cid, c in clips.items() if key in cid]
    if len(matches) != 1:
        raise typer.BadParameter(f"clip {key!r} matches {len(matches)} clips; use more of the id")
    return matches[0]


@perceive_app.command("prep")
def perceive_prep(folder: Path = typer.Argument(..., help="Folder with your raw clips"),
                  force: bool = typer.Option(False, help="Redo transcripts and sheets")) -> None:
    """Transcribe speech, make contact sheets and a brief per clip, and batch clips for analysts."""
    from .perceive.prep import format_tasks, prepare_perception

    cfg = load_config()
    project = _analyze(folder, cfg)
    usable = [c for c in project.clips().values() if c.usable]
    with _progress() as progress:
        task = progress.add_task("Transcripts + contact sheets", total=len(usable))
        tasks = prepare_perception(project, cfg, force=force, on_clip=lambda _: progress.advance(task))
    console.print(escape(format_tasks(tasks)))
    console.print(f"Tasks file: {escape(str(project.root / 'perception' / 'tasks.json'))}")


@perceive_app.command("check")
def perceive_check(folder: Path = typer.Argument(..., help="Folder with your raw clips"),
                   clip: str | None = typer.Option(None, help="Only this clip (id or unique part of it)")) -> None:
    """Validate the analysts' analysis.json files."""
    from .perceive.catalog import check_all

    project = Project(folder, load_config())
    ids = [_find_clip(project, clip).clip_id] if clip else None
    results = check_all(project, ids)
    bad = {cid: probs for cid, probs in results.items() if probs}
    for cid, probs in bad.items():
        console.print(f"[red]{escape(cid)}[/]")
        for p in probs:
            console.print(f"  - {escape(p)}")
    console.print(f"{len(results) - len(bad)}/{len(results)} analyses valid")
    if bad:
        raise typer.Exit(1)


@app.command()
def inspect(folder: Path = typer.Argument(..., help="Folder with your raw clips"),
            clip: str = typer.Argument(..., help="Clip id (or a unique part of it)"),
            start: float = typer.Argument(..., help="Start, clip seconds"),
            end: float = typer.Argument(..., help="End, clip seconds"),
            fps: float = typer.Option(4.0, help="Frames per second to show (max 24 frames)")) -> None:
    """Dense, timestamped frames of one stretch of a clip, for picking exact in/out points."""
    from .perceive.contact_sheet import build_sheet, grab_frames
    from .perceive.prep import perception_dir

    project = Project(folder, load_config())
    info = _find_clip(project, clip)
    start, end = max(0.0, start), min(info.duration, end)
    if end <= start:
        raise typer.BadParameter("end must be after start (and inside the clip)")
    step = max(1.0 / fps, (end - start) / 24)
    times = [start + i * step for i in range(int((end - start) / step) + 1)]
    out = perception_dir(project, info.clip_id) / f"inspect_{start:.2f}-{end:.2f}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    build_sheet(grab_frames(info.proxy, times), tile_long_edge=400, columns=6).save(out, quality=88)
    console.print(escape(str(out)))


music_app = typer.Typer(no_args_is_help=True, help="Music: beats, bars, sections.")
app.add_typer(music_app, name="music")


@music_app.command("analyze")
def music_analyze(track: Path = typer.Argument(..., help="Music file")) -> None:
    """Tempo, beats, bar starts, sections and drops of a track."""
    from .music import analyze_music

    console.print(escape(analyze_music(track).summary()))


@music_app.command("placeholder")
def music_placeholder(out: Path = typer.Argument(..., help="Where to write the .wav"),
                      bpm: float = typer.Option(100.0, help="Tempo")) -> None:
    """Write a license-free synthesised placeholder track (for testing beat-synced edits)."""
    from .devtools import make_placeholder_music

    console.print(escape(str(make_placeholder_music(out.expanduser().resolve(), bpm=bpm))))


edit_app = typer.Typer(no_args_is_help=True, help="Phase 3: an edit run (brief → drafts → final).")
app.add_typer(edit_app, name="edit")


@edit_app.command("start")
def edit_start(folder: Path = typer.Argument(..., help="Folder with your raw clips"),
               intent: str = typer.Option(..., help="What the video should be, in plain words"),
               platform: str = typer.Option("youtube", help="youtube | reels | shorts | tiktok | square"),
               duration: float = typer.Option(60.0, help="Target length in seconds"),
               resolution: str = typer.Option("1080p", help="1080p | 4k"),
               music: Path | None = typer.Option(None, help="Music track (optional)"),
               music_mode: str | None = typer.Option(None, help="auto | file | trending | none (default: config)"),
               sound_name: str | None = typer.Option(None, help="Trending mode: the sound's name in the app")) -> None:
    """Start an edit run (needs analysed footage)."""
    from .loop import TRENDING_CAVEAT, RunError, next_step, start_run

    if platform not in PLATFORM_CANVAS:
        raise typer.BadParameter(f"platform must be one of {', '.join(PLATFORM_CANVAS)}")
    if music_mode not in (None, "auto", "file", "trending", "none"):
        raise typer.BadParameter("music-mode must be auto, file, trending or none")
    cfg = load_config()
    try:
        run = start_run(Project(folder, cfg), cfg, intent=intent, platform=platform, target_seconds=duration,
                        resolution=resolution, music=music, music_mode=music_mode, sound_name=sound_name)
    except RunError as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(1) from exc
    console.print(f"Run: {escape(str(run.dir))}")
    if run.load().music_mode == "trending":
        console.print(f"[bold yellow]{escape(TRENDING_CAVEAT)}[/]")
    music_info = run.music()
    if music_info:
        console.print(escape(music_info.summary()))
    console.print(escape(next_step(run)))


@edit_app.command("status")
def edit_status(run_dir: Path = typer.Argument(..., help="Run folder")) -> None:
    """Where the run is and what to do next."""
    from .loop import Run, next_step

    run = Run(run_dir)
    state = run.load()
    console.print(f"{escape(state.intent)} | {state.platform} {state.target_seconds:.0f}s | iteration {state.iteration}")
    for line in state.history[-5:]:
        console.print(f"  {escape(line)}")
    console.print(escape(next_step(run)))


@edit_app.command("submit-brief")
def edit_submit_brief(run_dir: Path = typer.Argument(..., help="Run folder")) -> None:
    """Validate the director's brief.json and move on to editing."""
    from .loop import Run, next_step, submit_brief

    run = Run(run_dir)
    state = run.load()
    problems = submit_brief(run, Project(state.clips_dir, load_config()))
    if problems:
        for p in problems:
            console.print(f"[red]- {escape(p)}[/]")
        raise typer.Exit(1)
    console.print("[green]Brief accepted[/]")
    console.print(escape(next_step(run)))


@edit_app.command("submit-edl")
def edit_submit_edl(run_dir: Path = typer.Argument(..., help="Run folder")) -> None:
    """Validate the editor's edl.json, snap cuts to the beat and render the draft."""
    from .loop import Run, next_step, submit_edl

    cfg = load_config()
    run = Run(run_dir)
    state = run.load()
    result = submit_edl(run, Project(state.clips_dir, cfg), cfg)
    if result.problems:
        console.print("[red]EDL rejected:[/]")
        for p in result.problems:
            console.print(f"[red]- {escape(p)}[/]")
        raise typer.Exit(1)
    for n in result.notes:
        console.print(escape(n))
    for w in result.warnings:
        console.print(f"[yellow]warning: {escape(w)}[/]")
    console.print(f"[green]Draft:[/] {escape(str(result.draft))}")
    for s in result.sheets:
        console.print(f"Shots: {escape(str(s))}")
    console.print(escape(next_step(run)))


@edit_app.command("revise")
def edit_revise(run_dir: Path = typer.Argument(..., help="Run folder")) -> None:
    """Start the next iteration from the current EDL."""
    from .loop import Run, next_step, revise

    run = Run(run_dir)
    path = revise(run)
    console.print(f"New iteration: {escape(str(path))}")
    console.print(escape(next_step(run)))


@edit_app.command("final")
def edit_final(run_dir: Path = typer.Argument(..., help="Run folder"),
               iteration: int | None = typer.Option(None, help="Which iteration (default: latest)"),
               open_file: bool = typer.Option(False, "--open", help="Open the result")) -> None:
    """Render the full-quality video from an accepted iteration."""
    from .loop import TRENDING_CAVEAT, Run, finalize

    cfg = load_config()
    run = Run(run_dir)
    state = run.load()
    outputs = finalize(run, Project(state.clips_dir, cfg), cfg, iteration)
    for out in outputs:
        console.print(f"[green]Final video:[/] {escape(str(out))}")
    state = run.load()
    if state.music_mode == "trending":
        console.print(f"[bold yellow]{escape(TRENDING_CAVEAT)}[/]")
    elif state.music_credit:
        console.print(f"Music credit for the description: {escape(state.music_credit)}")
    if open_file and os.name == "nt":
        os.startfile(outputs[0])  # noqa: S606 - the user's own render


@edit_app.command("set-music")
def edit_set_music(run_dir: Path = typer.Argument(..., help="Run folder"),
                   track: Path = typer.Argument(..., help="Music file"),
                   start: float | None = typer.Option(None, help="Start offset in the track (default: first bar)")) -> None:
    """Use a different track; an existing cut is re-synced to it automatically (new iteration + draft)."""
    from .loop import Run, change_music, next_step

    cfg = load_config()
    run = Run(run_dir)
    state = run.load()
    result = change_music(run, Project(state.clips_dir, cfg), cfg, track.expanduser().resolve(), start=start)
    if result is not None:
        for n in result.notes:
            console.print(escape(n))
        for p in result.problems:
            console.print(f"[red]- {escape(p)}[/]")
        if result.draft:
            console.print(f"[green]Re-synced draft:[/] {escape(str(result.draft))}")
    console.print(escape(next_step(run)))


@music_app.command("find")
def music_find(run_dir: Path = typer.Argument(..., help="Run folder (its brief has the music_brief)"),
               provider: str = typer.Option("auto", help="auto | jamendo | openverse"),
               shortlist: int | None = typer.Option(None, help="Tracks to download and analyse"),
               pick: int | None = typer.Option(None, help="Use candidate N instead of the best")) -> None:
    """Search safe-to-post music for a run's brief, score candidates, pick the best (re-syncs an existing cut)."""
    from .loop import Run, RunError, find_music_for_run, next_step

    cfg = load_config()
    run = Run(run_dir)
    state = run.load()
    try:
        scored = find_music_for_run(run, Project(state.clips_dir, cfg), cfg, provider=provider,
                                    shortlist=shortlist, pick=pick, log=lambda m: console.print(escape(m)))
    except RunError as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(1) from exc
    for i, s in enumerate(scored[:6]):
        mark = "*" if i == (pick - 1 if pick else 0) else " "
        console.print(f"{mark} {i + 1}. {escape(s.candidate.title)} - {escape(s.candidate.artist)} "
                      f"({s.candidate.license.upper()}) score {s.total:.2f}: {escape('; '.join(s.reasons))}")
    console.print(f"Candidates: {escape(str(run.dir / 'music_candidates.md'))}")
    console.print(escape(next_step(run)))


@app.command()
def sfx(out_dir: Path | None = typer.Option(None, help="Write the effects here to listen to them")) -> None:
    """List the built-in sound effects (use them in an EDL as sounds: source 'sfx:<name>')."""
    from .render.sfx import SFX_NAMES, sfx_path

    for name in SFX_NAMES:
        line = f"sfx:{name}"
        if out_dir:
            line += f"  {sfx_path(name, out_dir.expanduser().resolve())}"
        console.print(escape(line))


@app.command()
def schema(name: str = typer.Argument(..., help="clip-analysis | edit-brief | edl")) -> None:
    """Print the JSON schema an agent must follow."""
    from .schemas import ClipAnalysis, EditBrief

    models = {"clip-analysis": ClipAnalysis, "edit-brief": EditBrief, "edl": EDL}
    if name not in models:
        raise typer.BadParameter(f"one of: {', '.join(models)}")
    print(json.dumps(models[name].model_json_schema(), indent=1))


@app.command()
def catalog(folder: Path = typer.Argument(..., help="Folder with your raw clips"),
            open_file: bool = typer.Option(False, "--open", help="Open the HTML viewer")) -> None:
    """Assemble the clip catalog (catalog.json + catalog.html) from measurements and analyses."""
    from .perceive.catalog import build_catalog

    project = Project(folder, load_config())
    result = build_catalog(project)
    done = sum(1 for e in result["clips"] if e["analysis"])
    html_path = project.root / "catalog.html"
    console.print(f"{done}/{len(result['clips'])} clips analysed\n{escape(str(html_path))}")
    if open_file and os.name == "nt":
        os.startfile(html_path)  # noqa: S606 - the user's own report


@app.command()
def sheet(
    video: Path = typer.Argument(..., help="Any video file (a render, a proxy, an original)"),
    every: float = typer.Option(1.0, help="Seconds between frames"),
    per_sheet: int = typer.Option(16, help="Frames per sheet image"),
    out_dir: Path | None = typer.Option(None, help="Where to write the sheets (default: next to the video)"),
) -> None:
    """Timestamped frame grids of a video, for eyeballing an edit."""
    from .ffmpeg import ffprobe_json
    from .perceive.contact_sheet import sheets_for_video

    duration = float(ffprobe_json(video)["format"]["duration"])
    paths = sheets_for_video(video, duration, out_dir or video.parent, video.stem + "_sheet", every=every,
                             per_sheet=per_sheet, max_frames=400)
    for path in paths:
        console.print(escape(str(path)))


@app.command()
def samples(folder: Path = typer.Argument(..., help="Where to write the synthetic test clips")) -> None:
    """Write synthetic test clips (shaky, blurry, dark, HDR, rotated, 60 fps, no audio) + a music track."""
    from .devtools import make_synthetic_clips

    clips_dir, music = make_synthetic_clips(folder.expanduser().resolve())
    console.print(f"Clips: {escape(str(clips_dir))}\nMusic: {escape(str(music))}")


@app.command()
def validate(edl_file: Path = typer.Argument(..., help="EDL JSON file")) -> None:
    """Check an EDL without rendering it."""
    cfg = load_config()
    edl = EDL.model_validate_json(edl_file.read_text(encoding="utf-8"))
    project = Project(edl.project_dir, cfg)
    clip_map = project.clips()
    signals = {cid: s for cid in clip_map if (s := project.load_signals(cid)) is not None}
    issues = validate_edl(edl, clip_map, signals, repair=False)
    for issue in issues:
        console.print(("[red]" if issue.level == "error" else "[yellow]") + escape(str(issue)) + "[/]")
    if not issues:
        console.print("[green]EDL OK[/]")
    if any(i.level == "error" for i in issues):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
