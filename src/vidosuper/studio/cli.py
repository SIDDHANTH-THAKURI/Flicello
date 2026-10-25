"""`vido studio ...`: run the studio (web app + worker) on this computer, and run a whole video from the command
line to measure what one costs."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

studio_app = typer.Typer(no_args_is_help=True, help="The studio: Flicello's web app on this computer.")
console = Console(width=150)


def _logging() -> None:
    import logging
    from logging.handlers import RotatingFileHandler

    from .settings import settings as studio_settings

    s = studio_settings()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file = RotatingFileHandler(s.logs_dir / "studio.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file.setFormatter(fmt)
    out = logging.StreamHandler()
    out.setFormatter(fmt)
    root = logging.getLogger("vidosuper")
    root.setLevel(logging.INFO)
    root.handlers = [file, out]


@studio_app.command("serve")
def serve(host: str = typer.Option(None, help="Address to listen on (default from the settings: 127.0.0.1)"),
          port: int = typer.Option(None, help="Port (default 8765)"),
          worker: bool = typer.Option(True, "--worker/--no-worker", help="Run the job worker in this process too"),
          reload: bool = typer.Option(False, "--reload", help="Local testing: restart by itself when the code changes")) -> None:
    """The web app (and, by default, the worker), on this computer only (127.0.0.1)."""
    import uvicorn

    from .service import Studio
    from .settings import settings as studio_settings
    from .web.app import create_app
    from .worker import Worker

    if reload:  # dev.cmd: the app is made (and remade after each code change) by devapp.make
        from pathlib import Path

        s = studio_settings()
        console.print(f"[green]{escape(s.name)}[/] (auto-reload) on http://{host or s.host}:{port or s.port}")
        uvicorn.run("vidosuper.studio.web.devapp:make", factory=True, reload=True,
                    reload_dirs=[str(Path(__file__).resolve().parents[1])], host=host or s.host, port=port or s.port,
                    server_header=False, proxy_headers=False, log_level="warning")
        return
    _logging()
    s = studio_settings()
    studio = Studio(s)
    if worker:
        Worker(studio).start_thread()
    console.print(f"[green]{escape(s.name)}[/] on http://{host or s.host}:{port or s.port}  (data: {escape(str(s.home))})")
    uvicorn.run(create_app(studio), host=host or s.host, port=port or s.port, server_header=False,
                proxy_headers=False, log_level="warning", timeout_keep_alive=30)


@studio_app.command("worker")
def worker_cmd() -> None:
    """Only the job worker (when the web app runs in another process or container)."""
    from .service import Studio
    from .settings import settings as studio_settings
    from .worker import Worker

    _logging()
    Worker(Studio(studio_settings())).run()


@studio_app.command("status")
def status() -> None:
    """Health, queue, disk and spend at a glance."""
    from .service import Studio
    from .settings import settings as studio_settings

    s = studio_settings()
    studio = Studio(s)
    h = studio.health()
    seen = "never" if h["worker_seen"] is None else f"{h['worker_seen']:.0f}s ago"
    console.print(f"worker seen {seen}; jobs: {h['running']} running, {h['queued']} queued, {h['waiting']} paused, "
                  f"{h['failed_24h']} failed (24 h); disk {h['free_gb']} GB free; API spend this month "
                  f"${h['month_usd']:.2f} of ${h['cap_usd']:.2f} (all brains ${h['month_usd_all']:.2f}); data in {s.home}")
    for a in studio.db.all("SELECT * FROM alerts WHERE seen=0 ORDER BY id DESC LIMIT 10"):
        console.print(f"[red]{escape(a['kind'])}[/] {escape(a['message'])}")


@studio_app.command("digest")
def digest(days: float = typer.Option(None, help="Look back this many days (default: since the last digest, else 7)"),
           show: bool = typer.Option(True, "--show/--quiet", help="Print the digest")) -> None:
    """What people said and what broke since the last digest: one page to improve the studio from (see
    docs/improvement.md). Saved under <home>/improve/, never in the repository."""
    from .digest import write_digest
    from .settings import settings as studio_settings
    from .service import Studio

    studio = Studio(studio_settings())
    rules = {}
    for u in studio.db.all("SELECT * FROM users WHERE disabled=0"):
        rules[u["name"]] = [r["text"] for r in studio.profile(u).rules]
    path, _ = write_digest(studio.db, studio.s.home, days, rules)
    if show:
        console.print(escape(path.read_text(encoding="utf-8")))
    console.print(f"Saved: {escape(str(path))}")


@studio_app.command("testbench")
def testbench(case: list[str] = typer.Option(None, "--case", help="Only these cases (repeat it); default: all"),
              brain: str = typer.Option("local", help="local (free: the structure) or claude_code (your Claude plan)"),
              spend: bool = typer.Option(False, help="Allow paid services (painted looks, ElevenLabs, the API)")) -> None:
    """Make the test bench's videos with today's code and score them (testbench.py). Open the report after."""
    from .testbench import run

    if brain not in ("local", "claude_code", "api"):
        raise typer.BadParameter("local, claude_code or api")
    _logging()
    folder = run(brain, case or None, spend, say=lambda m: console.print(escape(m)))
    console.print(f"Report: {escape(str(folder.parent / 'index.html'))}")


@studio_app.command("testbench-rate")
def testbench_rate(run_name: str = typer.Argument(..., help="The run's folder name (see the report)"),
                   case: str = typer.Argument(..., help="The case id"),
                   score: float = typer.Argument(..., help="Out of 20"),
                   note: str = typer.Option("", help="The owner's words")) -> None:
    """Log the owner's rating of one test bench video; the report shows it over time."""
    from .testbench import rate

    rate(run_name, case, score, note)
    console.print(f"[green]Logged[/] {score:g}/20 for {escape(case)} in {escape(run_name)}")


@studio_app.command("bench")
def bench(project: str = typer.Option(None, help="Only this project id")) -> None:
    """Plan every finished video again with today's code (no AI, no render) and record the numbers next to the git
    commit and the rating the original got: the check that a change helps (docs/improvement.md)."""
    from ..config import REPO_ROOT
    from .bench import run_bench
    from .service import Studio
    from .settings import settings as studio_settings

    studio = Studio(studio_settings())
    table = Table("project", "pace", "shots", "mean s", "<0.5 s", "windows", "mirrored", "hits", "rated")
    for r in run_bench(studio, REPO_ROOT, project):
        if "error" in r:
            console.print(f"[red]{escape(r['project'])}[/] {escape(r['error'])}")
            continue
        table.add_row(r["project"], r["pace"], str(r["shots"]), str(r["mean_shot"]), f"{r['under_half_second']:.0%}",
                      str(r["distinct_windows"]), str(r["mirrored"]), r["hits"] or "-", str(r["rated_liked"]))
    console.print(table)


@studio_app.command("notify")
def notify(message: str = typer.Argument(..., help="A line for the owner (shown by `studio status`; sent to the phone if an alert address is set)"),
           kind: str = typer.Option("improve", help="alert kind")) -> None:
    """Leave the owner a note, e.g. what the daily improvement routine did."""
    from .alerts import alert
    from .settings import settings as studio_settings
    from .service import Studio

    studio = Studio(studio_settings())
    alert(studio.db, studio.s, kind, message, level="info")
    console.print("Noted.")


@studio_app.command("run")
def run(clips: Path = typer.Argument(..., help="A folder of clips (only read, never written)"),
        song: Path = typer.Option(None, help="The song to cut on (none: a beat is made for the video)"),
        shape: str = typer.Option("9:16", help="9:16, 16:9 or both"),
        length: float = typer.Option(30.0, help="Seconds"),
        intent: str = typer.Option("", help="What the video should be about (optional)"),
        pace: str = typer.Option("balanced", help="calm | balanced | fast | every: how quickly it cuts (every: one shot per hit)"),
        brain: str = typer.Option("local", help="local | claude_code | api"),
        pick: int = typer.Option(1, help="Which of the three pitches to make"),
        out: Path = typer.Option(None, help="Working folder (default: a new one under the studio home)"),
        sample_only: bool = typer.Option(False, help="Stop after the sample"),
        owner: bool = typer.Option(True, help="Apply the owner's style rules")) -> None:
    """One video end to end without the web app: analyse, pitch, design, sample, render, check - with the
    time and the AI cost of every step."""
    from ..config import REPO_ROOT
    from .brain import Usage, make_brain
    from .brain.schemas import StoryPitch
    from .pipeline import Pipeline, Workdir, plan_json, studio_config
    from .profiles import owner_seed_rules
    from .settings import settings as studio_settings

    s = studio_settings()
    if brain not in ("local", "claude_code", "api"):
        raise typer.BadParameter("brain is local, claude_code or api")
    shapes = ["9:16", "16:9"] if shape == "both" else [shape]
    if any(x not in ("9:16", "16:9") for x in shapes):
        raise typer.BadParameter("shape is 9:16, 16:9 or both")
    root = out or s.home / "runs" / time.strftime("%Y%m%d-%H%M%S")
    work = Workdir(root, clips_dir=clips.expanduser().resolve()).ensure()
    if song is not None:
        shutil.copy2(song, work.music / f"song{song.suffix.lower()}")
    usages: list[Usage] = []
    stages: list[tuple[str, float]] = []

    def progress(f: float, msg: str) -> None:
        console.print(f"  [dim]{f * 100:3.0f}%[/] {escape(msg)}")

    pipeline = Pipeline(work, studio_config(s.workspace, s.render_preset, s.models_dir), None, progress, owner=owner)
    pipeline.brain = make_brain(brain, s, usages.append, work.brain, pipeline.project.load_signals)
    rules = owner_seed_rules() if owner else []

    def stage(name: str, fn):
        console.print(f"[bold]{name}[/]")
        t0 = time.monotonic()
        result = fn()
        stages.append((name, time.monotonic() - t0))
        return result

    info = stage("analyse", lambda: pipeline.analyse(length, pace))
    console.print(escape(json.dumps(info)))
    ctx = pipeline.context(shapes, length, intent, rules, pace)
    console.print(escape(ctx.beats.text(ctx.song)))
    pitches = stage("pitch", lambda: pipeline.pitch(ctx))
    for i, p in enumerate(pitches, 1):
        console.print(f"[cyan]{i}. {escape(p['pitch']['title'])}[/] - {escape(p['pitch']['logline'])}")
    chosen = StoryPitch.model_validate(pitches[pick - 1]["pitch"])
    (root / "pitches.json").write_text(json.dumps(pitches, indent=1), encoding="utf-8")
    plan = stage("design", lambda: pipeline.design(ctx, chosen))
    (root / "plan-v1.json").write_text(plan_json(plan), encoding="utf-8")
    sample = stage("sample", lambda: pipeline.sample(ctx, plan, shapes[0], "sample-v1"))
    console.print(f"Sample: {escape(str(work.out / sample.path))} ({sample.seconds:.1f}s, rendered in {sample.render_seconds:.0f}s)")
    renders = []
    if not sample_only:
        renders = stage("render", lambda: pipeline.render(ctx, plan, shapes, 1))
        for r in renders:
            console.print(f"[green]{r.shape}[/] {escape(str(work.out / r.path))} ({r.seconds:.1f}s, rendered in {r.render_seconds:.0f}s)")
            for f in r.findings:
                colour = {"ok": "green", "warn": "yellow", "fail": "red"}[f["level"]]
                console.print(f"   [{colour}]{f['level'].upper():4}[/] {f['check']}: {escape(f['detail'])}")

    table = Table(title="Brain calls")
    for col in ("task", "model", "backend", "in", "out", "cache w", "cache r", "USD", "billed", "s"):
        table.add_column(col)
    for u in usages:
        table.add_row(u.task, u.model, u.backend, str(u.input_tokens), str(u.output_tokens), str(u.cache_write),
                      str(u.cache_read), f"{u.usd:.4f}", "yes" if u.billed else "no", f"{u.seconds:.0f}")
    console.print(table)
    total = sum(u.usd for u in usages)
    console.print("Stages: " + ", ".join(f"{n} {t:.0f}s" for n, t in stages))
    note = {"claude_code": " (API-equivalent; it ran on your Claude plan)", "local": " (no AI used)"}.get(brain, "")
    console.print(f"AI cost for this video: ${total:.3f}{note}")
    report = {"info": info, "stages": stages, "usage": [u.__dict__ for u in usages], "usd": total,
              "renders": [r.__dict__ for r in renders], "sample": sample.__dict__, "repo": str(REPO_ROOT)}
    (root / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    console.print(f"Report: {escape(str(root / 'report.json'))}")
