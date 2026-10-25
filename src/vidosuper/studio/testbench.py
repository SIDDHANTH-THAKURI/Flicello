"""The test bench: is the output good, and does it get better? (docs/proposals/flicello-next.md, item 5)

A fixed set of cases (footage + music + request) is made into finished videos through the real studio flow (create,
start, agree with what was understood, pick the first idea, approve the sample, render), each change of the engine
gives a new run, and every run is scored twice: by the automatic checks (beat sync, loudness, black frames, what was
asked for) and by the owner, out of 20. A report shows each run's videos and the scores over time, so a change that
makes things worse is caught before anyone else sees it.

    vido studio testbench                  every case, with the no-AI brain (free; shows the structure)
    vido studio testbench --brain claude_code --case sydney-story   (the owner's Claude plan: the real taste)
    vido studio testbench-rate <run> <case> 12 --note "..."         the owner's rating, out of 20

Only the owner's own footage goes in (never a person's film: they gave it to us for their video only), and a run
spends no money unless asked (--spend): no API brain, no painted looks, no ElevenLabs; the AI only through the
owner's Claude plan. Everything lives in %LOCALAPPDATA%/VidoSuper/bench (cases.yaml, runs/, ratings.jsonl, report).
"""

from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

VIDEO = (".mp4", ".mov", ".m4v", ".mkv")


def bench_root() -> Path:
    base = os.environ.get("VIDO_BENCH_HOME") or str(
        Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".vidosuper") / "VidoSuper" / "bench")
    return Path(base)


@dataclass
class Case:
    id: str
    kind: str = "montage"  # montage | voice
    clips: str = ""  # a folder of the owner's footage (read only)
    limit: int = 20  # at most this many clips (a project takes 20)
    recording: str = ""  # voice: the recording (a .wav), or "tts:<text>" to make one with the computer's voice
    music: str = "made"  # made (our own beat on the bench) | none | a path to a song
    length: float = 30.0
    shapes: str = "9:16"
    pace: str = "balanced"
    style: str = "designed"
    intent: str = ""
    why: str = ""


def default_cases() -> list[Case]:
    """Built from the owner's footage on this PC (only what exists)."""
    videos = Path.home() / "Videos" / "dji"
    out: list[Case] = []
    if any(videos.glob("*.MP4")):
        out += [
            Case("sydney-vague", clips=str(videos), length=30, why="no note at all: the footage has to carry it"),
            Case("sydney-story", clips=str(videos), length=45, pace="calm",
                 intent="A slow day in Sydney that builds to the harbour, warm and a little nostalgic",
                 why="a story asked in words, calm pace"),
            Case("sydney-fast", clips=str(videos), length=20, pace="fast", intent="Fast and energetic, cut on the beat",
                 why="short and fast"),
            Case("sydney-wide", clips=str(videos), length=45, shapes="16:9", intent="The best of the trip, for YouTube",
                 why="landscape"),
        ]
    wollongong = videos / "wollongong"
    if any(wollongong.glob("*.MP4")):
        out += [
            Case("wollongong-calm", clips=str(wollongong), length=40, pace="calm",
                 intent="A peaceful walk by the sea", why="many clips, calm"),
            Case("wollongong-every", clips=str(wollongong), length=20, pace="every", intent="A shot on every beat",
                 why="one shot per beat"),
        ]
    if os.name == "nt":  # the computer's own voice reads a short explainer (nobody's real voice)
        text = ("Why does sleep matter so much for memory? While you sleep, your brain replays what you learned "
                "during the day. People who sleep eight hours remember about forty percent more. So here is one "
                "habit to try tonight: study first, then sleep, and let your brain do the rest.")
        out += [Case("voice-designed", kind="voice", recording=f"tts:{text}", style="designed", music="made",
                     intent="Explain it simply, with pictures of what I say", why="voice to animation"),
                Case("voice-labels", kind="voice", recording=f"tts:{text}", style="labels", music="none",
                     intent="Clean labels for my points", why="voice, clean labels")]
    return out


def load_cases(root: Path | None = None) -> list[Case]:
    root = root or bench_root()
    path = root / "cases.yaml"
    if not path.exists():
        root.mkdir(parents=True, exist_ok=True)
        cases = default_cases()
        path.write_text("# The test bench's cases (the owner's own footage only). Edit freely; ids must stay unique.\n"
                        + yaml.safe_dump([asdict(c) for c in cases], sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
        return cases
    return [Case(**c) for c in (yaml.safe_load(path.read_text(encoding="utf-8")) or [])]


def speak(text: str, out: Path) -> Path:
    """A WAV of the computer's own voice reading `text` (Windows speech; nobody's real voice)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        return out
    script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$s.Rate = 0; $s.SetOutputToWaveFile($env:VIDO_TTS_OUT); $s.Speak($env:VIDO_TTS_TEXT); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, timeout=120,
                   env={**os.environ, "VIDO_TTS_OUT": str(out), "VIDO_TTS_TEXT": text})
    return out


def _link(src: Path, dst: Path) -> None:
    """The footage in the project without copying it (a hard link; a copy across drives). The pipeline only reads it."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def commit_id() -> str:
    from ..config import REPO_ROOT

    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, timeout=10)
        dirty = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return (out.stdout.strip() or "?") + ("+" if dirty else "")
    except Exception:  # noqa: BLE001
        return "?"


def auto_score(findings: list[dict]) -> int:
    """0-100 from the automatic checks: every check counts, a failure twice a warning."""
    if not findings:
        return 0
    lost = sum(2 if f.get("level") == "fail" else 1 if f.get("level") == "warn" else 0 for f in findings)
    return max(0, round(100 * (1 - lost / (2 * len(findings)))))


@dataclass
class Result:
    case: str
    ok: bool
    video: str = ""
    seconds: float = 0.0
    took: float = 0.0
    usd: float = 0.0
    score: int = 0
    findings: list[dict] = field(default_factory=list)
    error: str = ""
    why: str = ""


def _studio(home: Path, brain: str, spend: bool):
    from .service import Studio
    from .settings import load_settings

    s = load_settings(home, {"brain": brain if brain != "claude_code" else "local", "owner_brain": brain,
                             "jobs": "local", "render_preset": "medium", "render_processes": 2})
    s.limits.min_free_gb = 1.0
    s.limits.monthly_usd = s.limits.video_usd = 1000.0
    s.limits.clip_seconds = s.limits.project_seconds = 4 * 3600.0  # the owner's long trip clips
    if not spend:  # nothing on the bench costs money unless asked
        for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY"):
            os.environ[key] = ""
    return Studio(s)


def _owner(studio) -> dict:
    from . import accounts

    return accounts.owner(studio.db)


def _drain(studio, limit: int = 40) -> None:
    from .worker import Worker

    worker = Worker(studio)
    for _ in range(limit):
        if not worker.tick():
            return


def _footage(case: Case) -> list[Path]:
    folder = Path(case.clips)
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO)
    return files[:case.limit]


def _project_for(studio, owner: dict, case: Case) -> dict:
    """One project per footage (or recording), kept between cases and runs: its clips are linked and measured once,
    and every later case reuses what was already measured and logged. Reset to a fresh setup for each case."""
    import hashlib

    from .brain.context import pace_from_note
    from .storage import _probe, workdir

    source = case.recording if case.kind == "voice" else f"{case.clips}|{case.limit}"
    title = f"{case.kind}-{hashlib.sha1(source.encode('utf-8')).hexdigest()[:10]}"
    row = studio.db.one("SELECT * FROM projects WHERE user_id=? AND title=? AND deleted=0", (owner["id"], title))
    if row is None:
        row = studio.create_project(owner, title, case.shapes, case.length, case.intent, case.pace, case.kind, case.style)
        work = workdir(studio.s, owner["id"], row["id"]).ensure()
        if case.kind == "voice":
            rec = Path(case.recording)
            if case.recording.startswith("tts:"):
                rec = speak(case.recording[4:], bench_root() / "voices" / f"{title}.wav")
            _link(rec, work.music / f"song{rec.suffix.lower()}")
            meta = _probe(rec, "song", studio.s)
            studio.db.run("UPDATE projects SET song=?, song_seconds=? WHERE id=?", (rec.name, meta["seconds"], row["id"]))
        else:
            for k, src in enumerate(_footage(case)):
                cid = f"c{k:03d}{os.urandom(4).hex()}"
                dst = work.clips / f"{cid}{src.suffix.lower()}"
                _link(src, dst)
                meta = _probe(dst, "clip", studio.s)
                studio.db.run("INSERT INTO clips(id, project_id, name, file, bytes, seconds, width, height, created) "
                              "VALUES (?,?,?,?,?,?,?,?,?)", (cid, row["id"], src.name, dst.name, src.stat().st_size,
                                                              meta["seconds"], meta["width"], meta["height"], time.time()))
    pid = row["id"]
    work = workdir(studio.s, owner["id"], pid).ensure()
    for table in ("pitches", "plans", "renders", "beats", "feedback"):
        studio.db.run(f"DELETE FROM {table} WHERE project_id=?", (pid,))
    if case.kind != "voice":
        for old in work.music.glob("song.*"):  # last case's beat was made for another length
            old.unlink()
    studio.db.run("UPDATE projects SET stage='setup', chosen=NULL, pitch_round=0, sample_tries=0, understood=NULL, "
                  "brief=NULL, version=0, revisions=0, error=NULL, note='', credits=0, beat=NULL, length=?, shapes=?, "
                  "pace=?, intent=?, style=?, music=?, sound='natural', song=CASE WHEN kind='voice' THEN song ELSE NULL END "
                  "WHERE id=?", (case.length, case.shapes, pace_from_note(case.pace, case.intent), case.intent, case.style,
                                 "none" if case.music == "none" else "made", pid))
    if case.kind != "voice" and case.music not in ("made", "none"):
        song = Path(case.music)
        _link(song, work.music / f"song{song.suffix.lower()}")
        meta = _probe(song, "song", studio.s)
        studio.db.run("UPDATE projects SET song=?, song_seconds=? WHERE id=?", (song.name, meta["seconds"], pid))
    return studio.db.project(pid)


def run_case(studio, case: Case, out_dir: Path, say: Callable[[str], None] = print) -> Result:
    """One case through the real flow, start to finished video. Never raises: a broken case is a result too."""
    from .storage import workdir

    t0 = time.time()
    owner = _owner(studio)
    try:
        p = _project_for(studio, owner, case)
        work = workdir(studio.s, owner["id"], p["id"])
        spent_before = studio.db.value("SELECT COALESCE(SUM(usd), 0) FROM spend WHERE project_id=?", (p["id"],), 0) or 0
        studio.start(p)
        for _ in range(8):  # the steps a person would click through, taking what the AI proposed
            _drain(studio)
            p = studio.db.project(p["id"])
            if p["stage"] == "understood":
                studio.confirm_understanding(p, {"story": (studio.understanding(p) or {}).get("story", "")})
            elif p["stage"] == "choose":
                first = next(x for x in studio.pitches(p) if not x["rejected"])
                studio.choose(p, first["id"])
            elif p["stage"] == "review":
                studio.sample_verdict(owner, p, True)
            elif p["stage"] in ("done", "failed", "paused"):
                break
            say(f"  {case.id}: {p['stage']}")
        p = studio.db.project(p["id"])
        if p["stage"] != "done":
            events = studio.db.all("SELECT message FROM events WHERE project_id=? AND level='error' ORDER BY id DESC LIMIT 1",
                                   (p["id"],))
            err = events[0]["message"] if events else (p.get("error") or p.get("note") or p["stage"])
            return Result(case.id, False, took=round(time.time() - t0, 1), why=case.why, error=str(err)[:300])
        final = sorted(studio.renders(p, "final"), key=lambda r: r["shape"] != case.shapes.split("+")[0])[0]
        src = work.out / final["file"]
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / f"{case.id}{src.suffix}"
        shutil.copy2(src, dst)
        findings = json.loads(final["findings"] or "[]")
        usd = (studio.db.value("SELECT COALESCE(SUM(usd), 0) FROM spend WHERE project_id=?", (p["id"],), 0) or 0) - spent_before
        return Result(case.id, True, dst.name, final["seconds"] or 0.0, round(time.time() - t0, 1), round(usd, 4),
                      auto_score(findings), findings, why=case.why)
    except Exception as exc:  # noqa: BLE001 - one broken case must not stop the bench
        return Result(case.id, False, took=round(time.time() - t0, 1), error=f"{type(exc).__name__}: {exc}"[:300],
                      why=case.why)


def run(brain: str = "local", only: list[str] | None = None, spend: bool = False,
        say: Callable[[str], None] = print) -> Path:
    """Run the bench (or some cases); returns the run's folder. The report is rebuilt after every case."""
    root = bench_root()
    cases = [c for c in load_cases(root) if not only or c.id in only]
    if not cases:
        raise SystemExit("No cases: edit " + str(root / "cases.yaml"))
    studio = _studio(root / "home", brain, spend)
    name = time.strftime("%Y%m%d-%H%M") + f"-{commit_id()}-{brain}"
    folder = root / "runs" / name
    folder.mkdir(parents=True, exist_ok=True)
    meta = {"run": name, "commit": commit_id(), "brain": brain, "at": time.time(), "results": []}
    for case in cases:
        say(f"{case.id} ({case.why})")
        result = run_case(studio, case, folder, say)
        meta["results"].append(asdict(result))
        (folder / "run.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        say(f"  -> {'ok' if result.ok else 'FAILED'} {result.score}/100 in {result.took:.0f} s"
            + (f": {result.error}" if result.error else ""))
        report(root)
    return folder


def rate(run_name: str, case: str, score: float, note: str = "") -> None:
    """The owner's rating of one video of one run, out of 20 (the same scale as every other rating)."""
    root = bench_root()
    if not (root / "runs" / run_name / "run.json").exists():
        raise SystemExit(f"No run called {run_name} (see {root / 'runs'})")
    if not 0 <= score <= 20:
        raise SystemExit("Ratings are out of 20.")
    with (root / "ratings.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"run": run_name, "case": case, "score": score, "note": note, "at": time.time()}) + "\n")
    report(root)


def _runs(root: Path) -> list[dict]:
    runs = []
    for f in sorted((root / "runs").glob("*/run.json")):
        try:
            runs.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return runs


def _ratings(root: Path) -> dict[tuple[str, str], dict]:
    out = {}
    path = root / "ratings.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                out[(r["run"], r["case"])] = r
            except (ValueError, KeyError):
                pass
    return out


def _chart(points: list[tuple[str, float | None, float | None]]) -> str:
    """An SVG line chart: the automatic score (0-100) and the owner's average rating (0-20, drawn on the same
    height) for each run, oldest first."""
    if not points:
        return ""
    w, h, pad = 760, 220, 34
    n = len(points)
    x = (lambda k: pad + (w - 2 * pad) * (k / max(1, n - 1)))  # noqa: E731
    y = (lambda v: h - pad - (h - 2 * pad) * v)  # noqa: E731
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Scores over time">',
             f'<line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{h - pad}" stroke="#444"/>']
    for label, colour, k in (("automatic checks /100", "#ff8a3d", 1), ("owner's rating /20", "#9f7bff", 2)):
        pts = [(x(i), y(p[k] / (100 if k == 1 else 20))) for i, p in enumerate(points) if p[k] is not None]
        if pts:
            parts.append(f'<polyline fill="none" stroke="{colour}" stroke-width="2.5" points="'
                         + " ".join(f"{a:.1f},{b:.1f}" for a, b in pts) + '"/>')
            parts += [f'<circle cx="{a:.1f}" cy="{b:.1f}" r="3.5" fill="{colour}"/>' for a, b in pts]
        parts.append(f'<text x="{pad + (0 if k == 1 else 230)}" y="16" fill="{colour}" font-size="13">{label}</text>')
    for i, p in enumerate(points):
        parts.append(f'<text x="{x(i):.1f}" y="{h - 10}" fill="#999" font-size="10" text-anchor="middle">'
                     f'{html.escape(p[0][:13])}</text>')
    return "".join(parts) + "</svg>"


def report(root: Path | None = None) -> Path:
    """runs/index.html: the scores over time, then each run (newest first) with its videos, checks and ratings."""
    root = root or bench_root()
    runs = _runs(root)
    ratings = _ratings(root)
    points = []
    for r in runs:
        done = [x for x in r["results"] if x["ok"]]
        auto = sum(x["score"] for x in done) / len(done) if done else None
        rated = [ratings[(r["run"], x["case"])]["score"] for x in r["results"] if (r["run"], x["case"]) in ratings]
        points.append((r["run"], auto, sum(rated) / len(rated) if rated else None))
    e = html.escape
    body = [f"<h1>Flicello test bench</h1><p>{len(runs)} run(s). Rate a video with "
            "<code>vido studio testbench-rate &lt;run&gt; &lt;case&gt; &lt;score out of 20&gt; --note \"...\"</code></p>",
            _chart(points)]
    for r in reversed(runs):
        body.append(f"<h2>{e(r['run'])}</h2><p>commit {e(r['commit'])} · brain {e(r['brain'])} · "
                    f"{time.strftime('%d %b %Y %H:%M', time.localtime(r['at']))}</p><div class='grid'>")
        for x in r["results"]:
            rating = ratings.get((r["run"], x["case"]))
            bad = [f for f in x["findings"] if f.get("level") != "ok"]
            body.append(
                "<article>" + (f"<video src='{e(r['run'])}/{e(x['video'])}' controls preload='none'></video>" if x["ok"] else "")
                + f"<h3>{e(x['case'])}</h3><p class='why'>{e(x.get('why', ''))}</p>"
                + (f"<p><b>{x['score']}/100</b> automatic · {x['seconds']:.0f} s video · made in {x['took']:.0f} s"
                   + (f" · ${x['usd']:.2f}" if x['usd'] else "") + "</p>" if x["ok"] else f"<p class='fail'>Failed: {e(x['error'])}</p>")
                + (f"<p class='rated'>Owner: <b>{rating['score']:g}/20</b> {e(rating.get('note', ''))}</p>" if rating else "")
                + ("<ul>" + "".join(f"<li class='{e(f['level'])}'>{e(f['check'])}: {e(str(f['detail']))[:160]}</li>"
                                     for f in bad[:6]) + "</ul>" if bad else "")
                + "</article>")
        body.append("</div>")
    page = ("<!doctype html><meta charset='utf-8'><title>Flicello test bench</title><style>"
            "body{font:15px/1.5 system-ui,sans-serif;background:#0b0b10;color:#eee;max-width:1200px;margin:2rem auto;padding:0 1rem}"
            "h2{margin-top:2.5rem;border-top:1px solid #333;padding-top:1rem}.grid{display:grid;grid-template-columns:"
            "repeat(auto-fill,minmax(260px,1fr));gap:1rem}article{background:#15151d;border:1px solid #2a2a35;"
            "border-radius:14px;padding:.8rem}video{width:100%;border-radius:10px;background:#000;max-height:420px}"
            "h3{margin:.5rem 0 .2rem}.why{color:#999;margin:0}.fail{color:#ff8a9a}.rated{color:#c9b6ff}"
            "li.fail{color:#ff8a9a}li.warn{color:#ffcf7a}code{background:#222;padding:.1rem .3rem;border-radius:4px}"
            "svg{width:100%;max-width:760px;background:#111118;border-radius:12px}</style>" + "".join(body))
    (root / "runs").mkdir(parents=True, exist_ok=True)
    out = root / "runs" / "index.html"
    out.write_text(page, encoding="utf-8")
    return out
