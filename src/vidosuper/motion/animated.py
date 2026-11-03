"""Animated films from a brief, in a designed look (a kit: motion/kits/<name>), the way "The Paper Studio" was made
(rated 19/20 by its maker): one story on its music, every idea a little world, every frame checked.

1. The director (Claude Code at a desk) reads the brief, writes the treatment and the plan (story, scenes, the lines
   on paper, the cast, the palette, the music's sound), has the music made, measures it bar by bar and, when its shape
   doesn't serve the story, cuts it again on its bar lines (music/arrange.py); then times every scene on the bars:
   the reveal on a lift, the big moment on a drop (`timing.json`).
2. The person sees the pitch (the story and the music) before anything is built, and can change it.
3. A desk per scene, side by side, writes its scene with the kit (`scenes/<id>/scene.js`) and looks at its own work
   until it is right: stills across the scene, frames of its motion, the frame-timing rules and HyperFrames' layout
   and text checks, the director's review.
4. The scenes become one film page, joined by the timing's transitions (page turns, zooms through a door, folds,
   flashes); the whole film is checked and reviewed, and the scenes the review names go back to their desks once.
5. The film is rendered; the kit's logged events become its sounds (motion/event_sound.py), mixed under the music at
   -14 LUFS; each scene's picture is kept so the film can be edited scene by scene in the studio.

A run picks up where it stopped: every checked step is kept."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import event_sound, kits, scenes
from ..config import REPO_ROOT

SHAPES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}
FPS = 30
TRANSITIONS = ("turn", "zoom", "fold", "flash", "cut")
MUSIC_PARTS = ("soft", "lift", "groove", "breath", "riser", "drop", "ending")
KIT_FIELDS = ("person", "presenter", "fox", "orb", "own")
MIN_SCENE, MAX_SCENE = 2.5, 26.0
REVIEWS = 3  # the director's reviews a scene desk may ask for
PROBE = REPO_ROOT / "motion" / "probe.mjs"


class AnimatedError(RuntimeError):
    pass


def quick() -> bool:
    """VIDO_ANIMATED_QUICK=1: no director's reviews and no second round (much less of the Claude plan)."""
    return os.environ.get("VIDO_ANIMATED_QUICK", "").strip() in ("1", "true", "yes")


def parallel() -> int:
    """Scene desks at once: each is a Claude Code session and a browser for its looks."""
    return max(1, min(6, int(os.environ.get("VIDO_ANIMATED_PARALLEL", "3"))))


# ---------------------------------------------------------------- the film page

PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  @font-face {{ font-family: "Bricolage"; src: url("fonts/bricolage.woff2") format("woff2"); font-weight: 200 800; }}
  @font-face {{ font-family: "Geist Mono"; src: url("fonts/geist-mono.woff2") format("woff2"); }}
  @font-face {{ font-family: "Instrument"; src: url("fonts/instrument-serif-italic.woff2") format("woff2"); font-style: italic; }}
  html, body {{ margin: 0; background: #120d1c; }}
  #root {{ position: relative; width: {W}px; height: {H}px; overflow: hidden; background: #f3e9d8; }}
  #stage {{ position: absolute; left: 0; top: 0; }}
  #grain {{ position: absolute; inset: 0; pointer-events: none; mix-blend-mode: soft-light; opacity: .6; background-size: {W}px {H}px; }}
  #vig {{ position: absolute; inset: 0; pointer-events: none; background: radial-gradient(120% 100% at 50% 45%, transparent 60%, rgba(24, 10, 32, .34)); }}
</style>
</head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{D}" data-width="{W}" data-height="{H}">
  <svg id="stage" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
    <defs>
      <filter id="ps1" x="-10%" y="-12%" width="120%" height="134%" color-interpolation-filters="sRGB"><feDropShadow dx="1.5" dy="3" stdDeviation="3.5" flood-color="#160b20" flood-opacity=".36"/></filter>
      <filter id="ps2" x="-15%" y="-18%" width="130%" height="146%" color-interpolation-filters="sRGB"><feDropShadow dx="3" dy="6" stdDeviation="7" flood-color="#160b20" flood-opacity=".42"/></filter>
      <filter id="ps3" x="-20%" y="-24%" width="140%" height="160%" color-interpolation-filters="sRGB"><feDropShadow dx="5" dy="12" stdDeviation="14" flood-color="#160b20" flood-opacity=".46"/></filter>
      <linearGradient id="curlL" x1="0" x2="1"><stop offset="0" stop-color="#000" stop-opacity=".22"/><stop offset=".22" stop-color="#fff" stop-opacity=".45"/><stop offset="1" stop-color="#000" stop-opacity="0"/></linearGradient>
      <linearGradient id="curlR" x1="1" x2="0"><stop offset="0" stop-color="#000" stop-opacity=".22"/><stop offset=".22" stop-color="#fff" stop-opacity=".45"/><stop offset="1" stop-color="#000" stop-opacity="0"/></linearGradient>
      <radialGradient id="lampLight" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="#ffd59a" stop-opacity=".75"/><stop offset=".45" stop-color="#ffb36b" stop-opacity=".28"/><stop offset="1" stop-color="#ff9a5c" stop-opacity="0"/></radialGradient>
      <radialGradient id="doorLight" cx="50%" cy="40%" r="60%"><stop offset="0" stop-color="#fff3c4"/><stop offset=".6" stop-color="#ffd36b"/><stop offset="1" stop-color="#ffb347"/></radialGradient>
      <linearGradient id="coverShade" x1="0" x2="1"><stop offset="0" stop-color="#000" stop-opacity=".35"/><stop offset=".08" stop-color="#000" stop-opacity="0"/></linearGradient>
    </defs>
    <g id="world"></g>
  </svg>
  <div id="grain" data-layout-ignore></div>
  <div id="vig" data-layout-ignore></div>
</div>
<script>window.FILM = {FILM}; window.AUDIO = {AUDIO};</script>
<script src="gsap/gsap.min.js"></script>
<script src="kit.js"></script>
{SCENES}
<script>
{AFTER}
run();
window.__timelines = window.__timelines || {{}};
window.__timelines["main"] = tl;
</script>
</body>
</html>
"""


def film_of(shape: str, seconds: float, scenes_: list[dict] | None = None) -> dict:
    """The film page's FILM: its size and length, and its scenes with how each arrives (the kit lays the next page
    under the leaving one for as long as the way between them takes)."""
    w, h = SHAPES[shape]
    cuts = [{"id": x["id"], "start": float(x["start"]), "end": float(x["end"]), "in": x.get("in", "cut")}
            for x in scenes_ or []]
    return {"w": w, "h": h, "seconds": round(float(seconds), 3), "fps": FPS, "shape": shape, "cuts": cuts}


def transitions_js(timing: dict) -> str:
    """The ways between the scenes and the drop's hit, as kit calls."""
    lines, sc = [], timing.get("scenes") or []
    for k in range(1, len(sc)):
        kind = sc[k].get("in", "cut")
        if kind in TRANSITIONS and kind != "cut":
            lines.append(f"transition({json.dumps(kind)}, {float(sc[k]['start']):.3f}, {json.dumps(sc[k - 1]['id'])});")
    if timing.get("drop") is not None:
        lines.append(f"sfx({float(timing['drop']):.3f}, 'hit');")
    return "\n".join(lines)


def page(desk: Path, film: dict, scene_files: list[Path], after: str = "", audio: dict | None = None,
         name: str = "page", kit: str = "paper") -> Path:
    """A folder HyperFrames renders: the kit, these scenes in order, the ways between them, the music's data."""
    folder = desk / f".{name}"
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "gsap").mkdir(parents=True)
    shutil.copyfile(scenes.GSAP_DIR / "gsap.min.js", folder / "gsap" / "gsap.min.js")
    shutil.copyfile(kits.folder(kit) / "kit.js", folder / "kit.js")
    (folder / "fonts").mkdir()
    for f in kits.FONTS.glob("*.woff2"):
        shutil.copyfile(f, folder / "fonts" / f.name)
    (folder / "scenes").mkdir()
    tags = []
    for k, f in enumerate(scene_files):
        dest = folder / "scenes" / f"{k:02d}-{f.parent.name if f.name == 'scene.js' else f.stem}.js"
        shutil.copyfile(f, dest)
        tags.append(f'<script src="scenes/{dest.name}"></script>')
    (folder / "index.html").write_text(PAGE.format(
        W=film["w"], H=film["h"], D=f"{film['seconds']:.3f}", FILM=json.dumps(film), AUDIO=json.dumps(audio or None),
        SCENES="\n".join(tags), AFTER=after), encoding="utf-8")
    return folder


def stills(page_dir: Path, times: list[float], out: Path) -> list[Path]:
    """PNGs of the page at these film seconds, in order."""
    got = scenes.stills(page_dir, times, out / ".snap")
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for k, t in enumerate(times):
        dest = out / f"still-{k:02d}.png"
        shutil.copyfile(got[t], dest)
        paths.append(dest)
    shutil.rmtree(out / ".snap", ignore_errors=True)
    return paths


def grid(pngs: list[Path], times: list[float], out: Path, cols: int = 4, tile: int = 360) -> Path:
    """Frames side by side (rows of `cols`), each with its time: for eyes, a desk's or a director's."""
    import cv2
    import numpy as np

    tiles = []
    for png, t in zip(pngs, times):
        img = cv2.imread(str(png))
        if img is None:
            continue
        scale = tile / max(img.shape[:2])
        img = cv2.resize(img, (int(img.shape[1] * scale), int(img.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        h0 = img.shape[0]  # the time, small, in the bottom corner (never over what is being judged at the top)
        cv2.rectangle(img, (0, h0 - 20), (70, h0), (0, 0, 0), -1)
        cv2.putText(img, f"{t:.2f}s", (4, h0 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        tiles.append(img)
    if not tiles:
        raise AnimatedError("no frames were drawn")
    h, w = tiles[0].shape[:2]
    rows = [tiles[i:i + cols] for i in range(0, len(tiles), cols)]
    blank = np.full((h, w, 3), 24, np.uint8)
    sheet = np.vstack([np.hstack([*r, *([blank] * (cols - len(r)))]) for r in rows])
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), sheet, [cv2.IMWRITE_JPEG_QUALITY, 86])
    return out


def _browser() -> str:
    from . import scenes as sc

    hf = sc._hf()
    env = {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1"}
    done = subprocess.run([*hf, "browser", "path"], capture_output=True, timeout=120, env=env)
    path = done.stdout.decode("utf-8", errors="replace").strip().splitlines()
    path = path[-1].strip() if path else ""
    if not path or not Path(path).exists():
        subprocess.run([*hf, "browser", "ensure"], capture_output=True, timeout=900, env=env)
        done = subprocess.run([*hf, "browser", "path"], capture_output=True, timeout=120, env=env)
        path = done.stdout.decode("utf-8", errors="replace").strip().splitlines()[-1].strip()
    return path


def probe(page_dir: Path, timeout: float = 180.0) -> dict:
    """What a render can't show: script errors, whether the timeline is there, the scenes' windows and the sound
    events the kit logged. {errors, timeline, duration, events, scenes}."""
    node = shutil.which("node")
    if not node or not PROBE.exists():
        return {"errors": [], "timeline": True, "duration": 0, "events": [], "scenes": {}, "skipped": True}
    done = subprocess.run([node, str(PROBE), str((page_dir / "index.html").resolve()), _browser()],
                          capture_output=True, timeout=timeout, cwd=str(PROBE.parent))
    text = done.stdout.decode("utf-8", errors="replace").strip().splitlines()
    try:
        return json.loads(text[-1])
    except (ValueError, IndexError):
        tail = done.stderr.decode("utf-8", errors="replace")[-500:]
        return {"errors": [f"the page couldn't be opened: {tail}"], "timeline": False, "duration": 0, "events": [],
                "scenes": {}}


RULES = [
    (r"\bgsap\.(to|from|fromTo|set|timeline)\s*\(", "gsap.to/from/set outside the film's timeline: use the kit's to, from, set (they are seek-safe)"),
    (r"\btl\.call\s*\(", "tl.call doesn't run when the renderer jumps to a frame: show one thing, then another, with show/set"),
    (r"\b(setTimeout|setInterval|requestAnimationFrame)\s*\(", "timers don't follow the film's time: use the timeline or LIVE"),
    (r"\bMath\.random\s*\(", "Math.random gives a different film every render: use rng(seed)"),
    (r"\b(Date\.now|new Date|performance\.now)\b", "the clock isn't the film's time: use t (LIVE) or the timeline"),
]


def static_problems(text: str, scene_id: str | None = None) -> list[str]:
    """The frame-timing rules a scene file breaks, said plainly with the line."""
    out = []
    lines = text.splitlines()
    for pattern, said in RULES:
        for i, line in enumerate(lines, 1):
            code = line.split("//")[0]
            if re.search(pattern, code):
                out.append(f"line {i}: {said}")
    for i, line in enumerate(lines, 1):  # a sound asked for by a name that isn't one would be silent, or the wrong one
        for _, kind in re.findall(r"\bsfx\(((?:[^'\"();]|\([^()]*\))*?)['\"]([A-Za-z_]+)['\"]", line.split("//")[0]):
            if kind not in event_sound.KINDS:
                out.append(f"line {i}: sfx '{kind}' isn't one of the sounds ({', '.join(sorted(event_sound.KINDS))}); "
                           f"for anything else use 'say' with {{sound: 'a bike bell ringing twice'}}")
    if scene_id is not None:
        calls = re.findall(r"\bscene\(\s*['\"]([^'\"]+)['\"]", text)
        if calls != [scene_id]:
            out.append(f"the file must call scene('{scene_id}', start, end) once (it calls: {calls or 'none'})")
    return out


def hf_check(page_dir: Path, start: float, end: float, samples: int = 9, timeout: float = 600.0) -> list[str]:
    """HyperFrames' checks between two film seconds (layout, text, contrast, motion, script errors), as lines."""
    env = {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1"}
    args = [*scenes._hf(), "check", str(page_dir), "--json", f"--range={max(0.0, start):.2f},{end:.2f}",
            f"--samples={samples}", "--max-issues=60"]
    try:
        done = subprocess.run(args, capture_output=True, timeout=timeout, env=env, cwd=str(page_dir))
    except subprocess.TimeoutExpired:
        return ["the checks took too long (the scene may be very heavy: fewer shapes, fewer filters)"]
    text = done.stdout.decode("utf-8", errors="replace")
    try:
        report = json.loads(text[text.index("{"):])
    except ValueError:
        return []
    return findings(report)


SKIP = {"film_silent", "missing_audio", "text_occluded_by_overlay",  # the sound is mixed after the render
        "rotation_pivot_drift", "off_pivot_rotation"}  # limbs, hands and wheels turn about their joints on purpose


def findings(report: dict) -> list[str]:
    """A HyperFrames check report as short lines a designer acts on (most serious first)."""
    out = []
    for part in ("lint", "runtime", "layout", "motion", "contrast", "craft"):
        sec = report.get(part) or {}
        items = sec.get("findings") or sec.get("issues") or []
        for f in items:
            code = str(f.get("code") or f.get("rule") or "")
            if code in SKIP or code.startswith("missing_timeline"):
                continue
            sev = str(f.get("severity") or "warning")
            if sev == "info" and code not in ("canvas_overflow", "text_box_overflow"):
                continue
            if "grain" in json.dumps(f) or "#vig" in json.dumps(f):
                continue
            t = f.get("time")
            where = f"{float(t):.2f} s " if isinstance(t, (int, float)) else ""
            words = f.get("text")
            what = f" \"{str(words)[:40]}\"" if words else ""
            msg = str(f.get("message") or f.get("fix") or "")[:220]
            out.append((0 if sev == "error" else 1, f"{where}{code}{what}: {msg}"))
    return [line for _, line in sorted(out, key=lambda x: x[0])][:40]


# ---------------------------------------------------------------- the desks (Claude Code)

def _env() -> dict:
    from ..local import plan_env

    env = plan_env() if scenes.VIA != "api" else dict(os.environ)
    return {**env, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "64000", "VIDO_SCENES_VIA": scenes.VIA,
            "BASH_DEFAULT_TIMEOUT_MS": "900000", "BASH_MAX_TIMEOUT_MS": "3600000"}


def _scripts(folder: Path, commands: tuple[str, ...]) -> None:
    """`bash <command> ...` at the desk: this module's command line on that folder."""
    py = Path(sys.executable).as_posix()
    for name in commands:
        (folder / name).write_text(f'#!/bin/bash\n"{py}" -m vidosuper.motion.animated {name} "{folder.as_posix()}" "$@"\n',
                                   encoding="utf-8")


def _desk(folder: Path, system: str, prompt: str, commands: tuple[str, ...], budget: float, timeout: float) -> str:
    """A Claude Code session at this desk with its own commands; returns what it said last."""
    from ..local import claude_exe, run_claude

    _scripts(folder, commands)
    (folder / "system.md").write_text(system, encoding="utf-8")
    cmd = [claude_exe(), "-p", "--output-format", "json", "--model", scenes.design_model(), "--system-prompt-file",
           str(folder / "system.md"), "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config",
           "--max-budget-usd", f"{budget:g}", "--tools", "Read,Write,Edit,Bash", "--allowedTools", "Read(./**)",
           "Write(./**)", "Edit(./**)", *[f"Bash(bash {c}:*)" for c in commands]]
    proc = run_claude(cmd, prompt, folder, cwd=str(folder), timeout=timeout, env=_env())
    (folder / "session.json").write_bytes(proc.stdout or b"")
    try:
        said = str(json.loads(proc.stdout.decode("utf-8", errors="replace")).get("result") or "")
    except ValueError:
        said = ""
    if re.search(r"(session|usage|weekly|5-hour) limit|hit your (\S+ )?limit", said, re.I):
        raise AnimatedError(f"the Claude plan's limit is reached: {said[:160]}")
    if proc.returncode != 0 and not said:
        raise AnimatedError("the desk stopped: " + proc.stderr.decode("utf-8", errors="replace")[-600:])
    return said


def _kit_files(folder: Path, kit: str) -> None:
    src = kits.folder(kit)
    for name in ("kit.js", "craft.md", "example.js"):
        shutil.copyfile(src / name, folder / name)


# ---------------------------------------------------------------- 1. the director: story, music, timing

DIRECTOR_SYSTEM = """You are the director and writer of a short animated film: about {SECONDS} s, {W} x {H} px \
({SHAPE}), made in the {KIT} look by scene designers who each build one of your scenes in code with the kit. It must \
reach the bar in craft.md: a film its maker rated 19/20 ("so clean and perfect... just wow"), where every idea was a \
little world you could see, every frame was full, and everything moved on the music.

What the person asked for is in brief.md: read it first (facts, names, numbers and links in it are copied exactly, \
never invented or changed). Then read craft.md (the look, the rules, the kit's parts) and example.js (two scenes of \
the bar, written with the kit).

1. Write treatment.md: what the film is for and who it is for; the hook (the first 3 seconds must stop someone \
scrolling, in pictures); the story in beats (a want, a problem, a turn, a payoff, a close); for each scene: what we \
SEE, beat by beat (a little world with things happening in it; never words standing in for a picture), its one line \
on paper (up to 9 words; two short lines at most), how it moves, how it hands over to the next; the cast; the palette \
and how it changes with the mood; the music's arc. The tone is the person's (brief.md): never an advert unless they \
asked; honest, warm, clear, a little playful.
2. Write plan.json (the Write tool), exactly this shape:
  {{"title": "...", "logline": "...", "look": {{"palette": ["#hex", ... 4 to 8], "notes": "..."}},
   "cast": [{{"id": "...", "who": "what it is and looks like", "kit": "person|presenter|fox|orb|own"}}],
   "music": {{"bpm": 70-150, "styles": ["genre", "instruments and how they are played", "mood", "production"], \
"avoid": [], "arc": "soft intro, a lift, the groove, a breath, a riser, the drop, the ending: in your words"}},
   "scenes": [{{"id": "a-slug", "what": "what we see, beat by beat", "lines": ["the words on paper"], \
"seconds": about how long, "music": "soft|lift|groove|breath|riser|drop|ending"}}]}}
  Then `bash check plan`, and fix what it says until the plan is ready.
3. `bash compose`: the music is made for your plan (a few minutes) and measured bar by bar: its sections, the drops \
(where the drums come in) and the lifts. Read what it made: a music model does NOT put the drop where it was told. \
When its shape doesn't serve your story (the big moment must land on a drop; the film needs a quiet stretch to \
breathe), cut it on its bar lines: `bash arrange '[[0, 15], [32, 39], [16, 31], [40, 42]]'` (the bars kept, in \
order) and read the new measure (`bash music` prints it again). Keep the music natural: its intro first, its ending \
last, whole phrases (4 or 8 bars), no more cuts than the story needs. When nothing here can make music, `bash compose` \
says so: then time the film to a steady grid at your bpm and say so in timing.json ("music": null).
4. Write timing.json:
  {{"music": "arranged.wav" or "music.mp3" (or null), "seconds": the music's length,
   "scenes": [{{"id": "...", "start": 0.0, "end": 8.13, "in": "cut|turn|zoom|fold|flash"}}],
   "moments": [{{"t": 32.6, "what": "the drums come in: the reveal"}}], "drop": the big moment's time or null}}
  The plan's scenes in order, back to back from 0 to the end; every start on a bar line of the measure; the reveal on \
a lift, the big moment on a drop, the close on the ending. "in" is how a scene arrives (the first is "cut"): pick ONE \
way for most of the film (page turns for a storybook, folds for a pop-up book) so the hand-overs read as one book, and \
use a zoom only where the story goes inside something of the scene before (a door, a window, a screen, a cup's \
steam: that scene's designer says where); a flash at most once, for a magic moment. Then `bash check timing` until \
it is ready.
Reply DONE when both are ready."""

PITCH_NOTE = """The person saw your pitch (the story and the music) and said:

{NOTES}

Change what they asked (the treatment, the plan, the music or the timing: keep what they didn't ask to change), check \
the plan and the timing again, and reply DONE."""


def director_desk(brief: str, folder: Path, seconds: float, shape: str, kit: str, notes: str = "",
                  timeout: float = 7200.0) -> dict:
    """The treatment, the plan, the music and the timing, written and checked at the director's desk."""
    folder.mkdir(parents=True, exist_ok=True)
    _kit_files(folder, kit)
    w, h = SHAPES[shape]
    (folder / "brief.md").write_text(f"# What they asked for\n\n{brief.strip()}\n\n# The film\n\n- About {seconds:g} s, "
                                     f"{w} x {h} px ({shape}), the {kit} look.\n", encoding="utf-8")
    (folder / "settings.json").write_text(json.dumps({"seconds": seconds, "shape": shape, "kit": kit, "w": w, "h": h}),
                                          encoding="utf-8")
    system = (DIRECTOR_SYSTEM.replace("{SECONDS}", f"{seconds:g}").replace("{W}", str(w)).replace("{H}", str(h))
              .replace("{SHAPE}", shape).replace("{KIT}", kit).replace("{{", "{").replace("}}", "}"))
    if notes:
        prompt = PITCH_NOTE.replace("{NOTES}", notes.strip())
    elif (folder / "plan.json").exists():
        prompt = ("You were stopped partway: your files so far are on the desk (treatment.md, plan.json, the music, "
                  "timing.json). Read them and carry on; don't start over. Reply DONE when the plan and the timing are "
                  "ready.")
    else:
        prompt = "Direct the film described in brief.md. Start by reading brief.md, craft.md and example.js."
    _desk(folder, system, prompt, ("check", "compose", "music", "arrange"), 60, timeout)
    plan, problems = check_plan(folder)
    timing, tproblems = check_timing(folder)
    if problems or tproblems:
        raise AnimatedError("the director left the film unready: " + "; ".join((problems + tproblems)[:6]))
    return {"plan": plan, "timing": timing}


def _settings(folder: Path) -> dict:
    return json.loads((folder / "settings.json").read_text(encoding="utf-8"))


def check_plan(folder: Path) -> tuple[dict | None, list[str]]:
    path = folder / "plan.json"
    if not path.exists():
        return None, ["write plan.json first"]
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return None, [f"plan.json isn't valid JSON: {exc}"]
    s, p = _settings(folder), []
    for key in ("title", "logline", "look", "cast", "music", "scenes"):
        if key not in plan:
            p.append(f"plan.json needs \"{key}\"")
    if p:
        return plan, p
    pal = (plan.get("look") or {}).get("palette") or []
    if not 4 <= len(pal) <= 8 or not all(re.fullmatch(r"#[0-9a-fA-F]{6}", str(c)) for c in pal):
        p.append("look.palette: 4 to 8 colours as #rrggbb")
    for c in plan["cast"]:
        if c.get("kit") not in KIT_FIELDS:
            p.append(f"cast {c.get('id')}: kit is one of {', '.join(KIT_FIELDS)}")
    m = plan["music"]
    if not 60 <= float(m.get("bpm") or 0) <= 170:
        p.append("music.bpm: 60 to 170")
    if len(m.get("styles") or []) < 3:
        p.append("music.styles: at least 3 (genre, instruments and how they're played, mood)")
    sc = plan["scenes"]
    ids = [x.get("id") for x in sc]
    if len(sc) < 2:
        p.append("at least 2 scenes")
    if len(set(ids)) != len(ids) or not all(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,30}", str(i or "")) for i in ids):
        p.append("scene ids: unique, lower-case slugs (letters, numbers, dashes)")
    total = 0.0
    for x in sc:
        what, lines = str(x.get("what") or ""), x.get("lines") or []
        if len(what) < 60:
            p.append(f"scene {x.get('id')}: 'what' says too little: what do we SEE, beat by beat?")
        for ln in lines:
            if len(str(ln).split()) > 12:
                p.append(f"scene {x.get('id')}: the line \"{str(ln)[:40]}...\" is too long for paper (12 words at most; split it)")
        if x.get("music") not in MUSIC_PARTS:
            p.append(f"scene {x.get('id')}: music is one of {', '.join(MUSIC_PARTS)}")
        total += float(x.get("seconds") or 0)
    want = float(s["seconds"])
    if not 0.8 * want <= total <= 1.2 * want:
        p.append(f"the scenes add up to {total:.0f} s; the film is about {want:g} s")
    return plan, p


def check_timing(folder: Path) -> tuple[dict | None, list[str]]:
    path = folder / "timing.json"
    if not path.exists():
        return None, ["write timing.json (after bash compose)"]
    try:
        timing = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return None, [f"timing.json isn't valid JSON: {exc}"]
    plan, _ = check_plan(folder)
    p = []
    music = timing.get("music")
    bars: list[float] = []
    if music:
        if not (folder / music).exists():
            p.append(f"there's no {music} on your desk")
        else:
            m = _measure(folder, folder / music)
            bars = [b["start"] for b in m["bars"]]
            if abs(float(timing.get("seconds") or 0) - m["duration"]) > 0.6:
                p.append(f"seconds must be the music's length: {m['duration']:.2f}")
    sc = timing.get("scenes") or []
    want_ids = [x["id"] for x in (plan or {}).get("scenes", [])]
    if [x.get("id") for x in sc] != want_ids:
        p.append(f"the scenes, in the plan's order: {', '.join(want_ids)}")
    end = 0.0
    for k, x in enumerate(sc):
        a, b = float(x.get("start", -1)), float(x.get("end", -1))
        if abs(a - end) > 0.02:
            p.append(f"scene {x.get('id')} starts at {a:.2f}: it must start where the last ended ({end:.2f})")
        if not MIN_SCENE <= b - a <= MAX_SCENE:
            p.append(f"scene {x.get('id')} lasts {b - a:.1f} s: {MIN_SCENE:g} to {MAX_SCENE:g}")
        if k and bars and min(abs(a - t) for t in bars) > 0.06:
            p.append(f"scene {x.get('id')} starts at {a:.2f}, not on a bar line (nearest: "
                     f"{min(bars, key=lambda t: abs(a - t)):.2f})")
        if x.get("in", "cut") not in TRANSITIONS:
            p.append(f"scene {x.get('id')}: 'in' is one of {', '.join(TRANSITIONS)}")
        end = b
    if sc and abs(end - float(timing.get("seconds") or 0)) > 0.05:
        p.append(f"the last scene must end at the film's end ({timing.get('seconds')})")
    drop = timing.get("drop")
    if drop is not None and not 0 < float(drop) < float(timing.get("seconds") or 0):
        p.append("drop: a time inside the film, or null")
    return timing, p


def _measure(folder: Path, track: Path) -> dict:
    """The track's measure, kept beside it (measuring takes half a minute)."""
    from ..music import arrange

    cache = track.with_suffix(track.suffix + ".measure.json")
    if cache.exists() and cache.stat().st_mtime >= track.stat().st_mtime:
        return json.loads(cache.read_text(encoding="utf-8"))
    m = arrange.measure(track)
    data = {**m, "bars": arrange.bars_json(m)}
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def _describe(m: dict) -> str:
    from ..music import arrange

    return arrange.describe({**m, "bars": [arrange.Bar(**b) for b in m["bars"]]})


def check_command(folder: Path, args: list[str]) -> None:
    what = (args[0] if args else "plan").lower()
    if what == "timing":
        _, problems = check_timing(folder)
        print("The timing is ready." if not problems else "Not ready:\n- " + "\n- ".join(problems))
        return
    _, problems = check_plan(folder)
    print("The plan is ready: now bash compose." if not problems else "Not ready:\n- " + "\n- ".join(problems))


def compose_command(folder: Path) -> None:
    from ..music import compose, made

    plan, problems = check_plan(folder)
    if problems:
        print("Make the plan ready first:\n- " + "\n- ".join(problems))
        return
    if not compose.available():
        print("Nothing here can make music (no local music model, no ElevenLabs key): time the film to a steady grid "
              f"at {plan['music']['bpm']} bpm and write \"music\": null in timing.json.")
        return
    s = _settings(folder)
    m = plan["music"]
    bpm = made.fit_bpm(int(round(float(m.get("bpm") or 100))), "cut")
    sound = made.Sound(title=plan["title"], bpm=bpm, styles=[str(x) for x in m.get("styles") or []][:20],
                       avoid=[str(x) for x in m.get("avoid") or []][:20],
                       parts={"intro": ["soft", "sparse"], "build": [str(m.get("arc") or "")[:120]]})
    out = folder / "music.mp3"
    print(f"Making the music ({bpm} bpm, about {s['seconds']:g} s): a few minutes...", flush=True)
    try:
        compose.compose_plan(made.plan(made.layout(float(s["seconds"]), bpm, "cut"), sound, "cut"), out)
    except compose.ComposeError as exc:
        print(f"The music couldn't be made: {exc}")
        return
    from .cartoon import music_is_music

    if not music_is_music(out):
        print("The track came out as noise, not music: run bash compose again.")
        return
    print("music.mp3 is made. Measured:\n" + _describe(_measure(folder, out)))


def music_command(folder: Path, args: list[str]) -> None:
    name = args[0] if args else ("arranged.wav" if (folder / "arranged.wav").exists() else "music.mp3")
    track = folder / name
    if not track.exists():
        print(f"There's no {name}: bash compose first.")
        return
    print(f"{name}:\n" + _describe(_measure(folder, track)))


def arrange_command(folder: Path, args: list[str]) -> None:
    from ..music import arrange

    src = folder / "music.mp3"
    if not src.exists():
        print("There's no music yet: bash compose first.")
        return
    try:
        ranges = json.loads(" ".join(args))
    except ValueError:
        print("Give the bars to keep as JSON: bash arrange '[[0, 15], [32, 39], [16, 31], [40, 42]]'")
        return
    m = _measure(folder, src)
    try:
        out = arrange.arrange(src, ranges, folder / "arranged.wav",
                              {**m, "bars": [arrange.Bar(**b) for b in m["bars"]]})
    except arrange.ArrangeError as exc:
        print(f"Can't cut it like that: {exc}")
        return
    print("arranged.wav is made from music.mp3's bars " + ", ".join(f"{a}-{b}" for a, b in ranges)
          + ". Measured:\n" + _describe(_measure(folder, out)))


# ---------------------------------------------------------------- 2. the scenes

SCENE_SYSTEM = """You are a motion designer building ONE scene of a short animated film in the {KIT} look, in code, \
with the kit (kit.js). Your scene must reach the bar in craft.md: read all of it, and example.js (two scenes at that \
bar, written with this kit). brief.md is your scene: what we see, its words on paper, its time in the film (film \
seconds T to E), the music under it (its bar lines and its moments), the film's palette and cast, and the scenes \
around it.

Write scene.js (the Write tool). Then look at your own work and keep making it better until it is as good as the bar:
- `bash look` (stills across your scene) or `bash look 31.2 33.5` (at those film seconds): then Read look.jpg.
- `bash motion 31 33` (frames between two film seconds): Read motion.jpg (the pop-ups, the timing, the acting).
- `bash lint`: script errors, the frame-timing rules, text off the frame, hidden or too small, a line held too short.
{REVIEW}
Every frame full and alive: depth in layers, nothing flat or empty (a near layer crosses the bottom edge), no plain \
box standing in for a thing; the idea shown as a picture with its line on paper; text readable on a phone; things \
land on the beats; the scene changes over its whole time. When lint is clean{READY}, reply DONE and one line on what \
the scene shows."""
REVIEW_LINE = ("- `bash review`: the director's verdict (a score and what to fix): at most "
               f"{REVIEWS} reviews, so ask when you think it's ready.")

SCENE_REVIEW = """You are the director of an animated film in a layered cut-paper look, reviewing one of its scenes \
against the bar: a film its maker rated 19/20, where every idea was a little paper world (never words standing in for \
a picture), every frame was full (sky, land, things, characters, words on paper), things popped up like a pop-up book \
on the beats, and every line was easy to read. You see what the scene must show, then stills across it and frames of \
its motion, with their film times. Judge it:
1. Does it SHOW its idea as a picture, clearly, with its line on paper?
2. Is every frame full and composed: depth in layers, no empty or flat stretches (the bottom of the frame too: a near \
layer, not a bare band of water or road), no plain boxes standing in for things, nothing important cut by the frame?
3. Text: readable on a phone, nothing overlapping or cut off by the frame, held long enough to read?
4. Motion: things arrive and act on the beats; something happens over the whole scene; characters act?
5. Craft: in the look (paper, shadows, one palette), clean, appealing, nothing broken or odd?
Reply with JSON only: {"score": 0-10 (10 = the bar), "ok": true when ready (score >= 8 and nothing serious), \
"problems": ["at 12.4 s: the tag hides the sun: move the tag left", ...]}"""


def scene_brief(plan: dict, timing: dict, scene_id: str, film: dict, bars: list[float], notes: str = "") -> str:
    sc = {x["id"]: x for x in plan["scenes"]}
    tm = {x["id"]: x for x in timing["scenes"]}
    me, t = sc[scene_id], tm[scene_id]
    order = [x["id"] for x in timing["scenes"]]
    k = order.index(scene_id)
    a, b = float(t["start"]), float(t["end"])
    inside = [round(x, 3) for x in bars if a - 0.01 <= x < b]
    moments = [m for m in timing.get("moments") or [] if a <= float(m["t"]) < b]
    out_kind = tm[order[k + 1]].get("in", "cut") if k + 1 < len(order) else "the film ends"
    lines = [f"# Scene '{scene_id}' of '{plan['title']}'", "",
             f"The film: {plan['logline']}",
             f"Size: {film['w']} x {film['h']} px ({film['shape']}), U = {min(film['w'], film['h']) / 1080:g}. "
             f"The film is {film['seconds']:.2f} s.",
             f"Palette: {', '.join(plan['look']['palette'])}. Look notes: {plan['look'].get('notes', '')}",
             "Cast: " + "; ".join(f"{c['id']} ({c['kit']}): {c['who']}" for c in plan["cast"]), "",
             f"## Your scene: film seconds {a:.3f} to {b:.3f} ({b - a:.2f} s)",
             f"Call: const {{ C, cam, T, E }} = scene('{scene_id}', {a:.3f}, {b:.3f});",
             f"What we see: {me['what']}",
             "Words on paper: " + (" / ".join(f'"{x}"' for x in me.get("lines") or []) or "none"),
             f"The music under it: {me.get('music')}. Bar lines in your scene (film seconds): "
             + ", ".join(f"{x:.3f}" for x in inside) + f". A beat is {timing.get('beat', 0) or 'BEAT'} s (BEAT in the kit).",
             ]
    if moments:
        lines.append("Moments in your scene: " + "; ".join(f"{float(m['t']):.3f} s: {m['what']}" for m in moments))
    if timing.get("drop") is not None and a <= float(timing["drop"]) < b:
        lines.append(f"THE DROP is in your scene at {float(timing['drop']):.3f} s: the film's big moment lands here.")
    lines.append(f"It arrives by: {t.get('in', 'cut')} (drawn for you; your page lies under the one before it until "
                 f"then, so have it whole at T). It hands over by: {out_kind}"
                 + (" (zoom: call exitAt(scene, x, y) for where the camera dives in: a door, a window, a screen, "
                    "with something inside it; the next scene opens through a hole cut there)."
                    if out_kind == "zoom" else "."))
    lines += ["", "## The scenes around it"]
    for j in range(max(0, k - 1), min(len(order), k + 2)):
        if j != k:
            o = sc[order[j]]
            lines.append(f"- {order[j]} ({float(tm[order[j]]['start']):.2f}-{float(tm[order[j]]['end']):.2f} s): {o['what'][:300]}")
    if notes:
        lines += ["", "## Fix these (from the director, after seeing the whole film)", notes]
    return "\n".join(lines) + "\n"


def scene_desk(plan: dict, timing: dict, scene_id: str, folder: Path, film: dict, audio: dict, kit: str,
               notes: str = "", timeout: float = 5400.0) -> Path:
    """One scene written, looked at, checked and reviewed at its own desk. Returns scene.js."""
    folder.mkdir(parents=True, exist_ok=True)
    _kit_files(folder, kit)
    (folder / "audio.json").write_text(json.dumps(audio), encoding="utf-8")
    tm = {x["id"]: x for x in timing["scenes"]}[scene_id]
    (folder / "brief.md").write_text(scene_brief(plan, timing, scene_id, film, audio.get("bars") or [], notes),
                                     encoding="utf-8")
    me = {x["id"]: x for x in plan["scenes"]}[scene_id]
    (folder / "brief.json").write_text(json.dumps({"id": scene_id, "start": tm["start"], "end": tm["end"], "film": film,
                                                   "kit": kit, "what": me["what"], "lines": me.get("lines") or []}),
                                       encoding="utf-8")
    (folder / ".reviews").write_text("0", encoding="utf-8")
    review = not quick()
    system = (SCENE_SYSTEM.replace("{KIT}", kit).replace("{REVIEW}", REVIEW_LINE if review else "")
              .replace("{READY}", " and the review says ready (or you fixed all it found)" if review else ""))
    if notes and (folder / "scene.js").exists():
        prompt = ("scene.js is your scene from before. The director saw the whole film: read the fixes at the end of "
                  "brief.md and make them (keep what works). Check again (look, lint) and reply DONE.")
    elif (folder / "scene.js").exists():
        prompt = "You were stopped partway: scene.js holds your work so far. Read it and brief.md and carry on."
    else:
        prompt = "Build the scene described in brief.md. Start by reading brief.md, craft.md, example.js and kit.js."
    commands = ("look", "motion", "lint", "review") if review else ("look", "motion", "lint")
    _desk(folder, system, prompt, commands, 40, timeout)
    if not (folder / "scene.js").exists():
        raise AnimatedError(f"scene {scene_id}: the designer left no scene.js")
    return folder / "scene.js"


def _desk_page(folder: Path, name: str = "page") -> tuple[Path, dict]:
    info = json.loads((folder / "brief.json").read_text(encoding="utf-8"))
    audio = json.loads((folder / "audio.json").read_text(encoding="utf-8")) if (folder / "audio.json").exists() else None
    pg = page(folder, info["film"], [folder / "scene.js"], "", audio, name, info.get("kit", "paper"))
    return pg, info


def _times(args: list[str], a: float, b: float, n: int) -> list[float]:
    try:
        given = [float(x) for x in args]
    except ValueError:
        given = []
    if given:
        return [max(0.0, min(b, t)) for t in given[:12]]
    return [round(a + (b - a) * (k + 0.5) / n, 3) for k in range(n)]


def look_command(folder: Path, args: list[str]) -> None:
    if not (folder / "scene.js").exists():
        print("Write scene.js first.")
        return
    pg, info = _desk_page(folder)
    times = _times(args, float(info["start"]), float(info["end"]), 8)
    try:
        pngs = stills(pg, times, folder / ".stills")
    except scenes.SceneError as exc:
        print(f"IT DIDN'T DRAW: {exc}\nRun bash lint to see the script errors.")
        return
    grid(pngs, times, folder / "look.jpg", 4 if info["film"]["w"] >= info["film"]["h"] else 6)
    print("look.jpg: your scene at " + ", ".join(f"{t:.2f}" for t in times) + " s (film seconds). Read it.")


def motion_command(folder: Path, args: list[str]) -> None:
    if not (folder / "scene.js").exists():
        print("Write scene.js first.")
        return
    pg, info = _desk_page(folder)
    try:
        a, b = float(args[0]), float(args[1])
    except (IndexError, ValueError):
        print("Give two film seconds: bash motion 31 33")
        return
    times = [round(a + (b - a) * k / 7, 3) for k in range(8)]
    try:
        pngs = stills(pg, times, folder / ".motion")
    except scenes.SceneError as exc:
        print(f"IT DIDN'T DRAW: {exc}")
        return
    grid(pngs, times, folder / "motion.jpg", 4 if info["film"]["w"] >= info["film"]["h"] else 8, 300)
    print(f"motion.jpg: 8 frames from {a:.2f} to {b:.2f} s, left to right, top to bottom. Read it.")


def lint_command(folder: Path) -> None:
    script = folder / "scene.js"
    if not script.exists():
        print("Write scene.js first.")
        return
    info = json.loads((folder / "brief.json").read_text(encoding="utf-8"))
    problems = static_problems(script.read_text(encoding="utf-8"), info["id"])
    from .drawn import check_js

    problems += check_js([script])
    pg, _ = _desk_page(folder, "lint")
    seen = probe(pg)
    problems += [f"script error: {e}" for e in seen.get("errors") or []]
    if not seen.get("skipped"):
        win = (seen.get("scenes") or {}).get(info["id"])
        if not win:
            problems.append(f"scene('{info['id']}', ...) never ran")
        elif abs(float(win[0]) - float(info["start"])) > 0.01 or abs(float(win[1]) - float(info["end"])) > 0.01:
            problems.append(f"the scene runs {win[0]}-{win[1]}: it must run {info['start']}-{info['end']}")
    if not problems:
        problems = hf_check(pg, float(info["start"]), float(info["end"]))
    print("Clean." if not problems else "Fix these:\n- " + "\n- ".join(problems))


def review_command(folder: Path) -> None:
    if quick():
        print("There are no reviews in quick mode.")
        return
    used = int((folder / ".reviews").read_text() or "0") if (folder / ".reviews").exists() else 0
    if used >= REVIEWS:
        print(f"You've had your {REVIEWS} reviews: fix what the last one said, check with look and lint, then DONE.")
        return
    if not (folder / "scene.js").exists():
        print("Write scene.js first.")
        return
    pg, info = _desk_page(folder, "review")
    a, b = float(info["start"]), float(info["end"])
    times = _times([], a, b, 8)
    try:
        pngs = stills(pg, times, folder / ".review")
    except scenes.SceneError as exc:
        print(f"IT DIDN'T DRAW: {exc}")
        return
    cols = 4 if info["film"]["w"] >= info["film"]["h"] else 6
    sheet = grid(pngs, times, folder / ".review" / "sheet.jpg", cols)
    verdict = _review(SCENE_REVIEW, f"The scene ({a:.2f}-{b:.2f} s): {info['what']}\nIts words on paper: "
                      + " / ".join(info.get("lines") or []), [("Stills across the scene, in order:", sheet)],
                      "animated-scene-review")
    (folder / ".reviews").write_text(str(used + 1), encoding="utf-8")
    (folder / f"review-{used + 1}.json").write_text(json.dumps(verdict, indent=1), encoding="utf-8")
    print(f"The director: {verdict['score']}/10" + (", ready." if verdict["ok"] else ", not ready yet.")
          + "".join(f"\n- {p}" for p in verdict["problems"]))


def _review(system: str, text: str, images: list[tuple[str, Path]], task: str) -> dict:
    content: list[dict] = [{"type": "text", "text": text}]
    for label, img in images:
        content.append({"type": "text", "text": label})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(img.read_bytes()).decode("ascii")}})
    reply = scenes._ask(scenes._client(), system, content, 8000, None, task, "high")
    m = re.search(r"\{.*\}", reply, re.S)
    try:
        v = json.loads(m.group(0)) if m else {}
    except ValueError:
        v = {}
    return {"score": v.get("score", 0), "ok": bool(v.get("ok")), "problems": [str(x) for x in v.get("problems") or []],
            **({"scenes": v["scenes"]} if isinstance(v.get("scenes"), dict) else {})}


# ---------------------------------------------------------------- 3. the film

FILM_REVIEW = """You are the director of an animated film in a layered cut-paper look, watching the whole film as \
frames: for each scene, stills across it; then the moments where scenes hand over. The bar: a film its maker rated \
19/20 (every idea a little paper world, every frame full, things popping up on the beats, readable words, one look). \
Name what keeps this film from the bar, scene by scene, so each scene's designer can fix theirs: a picture that \
doesn't show its idea, an empty or flat frame, a plain box standing in for a thing, text cut off, overlapping or too \
small, a scene that just holds, a look that drifts from the others, a hand-over that jars. Only real problems; a \
scene with none gets none.
Reply with JSON only: {"score": 0-20 (20 = better than the bar), "scenes": {"<scene id>": ["at 31.2 s: ...", ...]}, \
"problems": ["for the whole film", ...]}"""


def film_review(film_dir: Path, plan: dict, timing: dict, work: Path, film: dict) -> dict:
    """The whole film judged from frames: {score, scenes: {id: [problems]}, problems}."""
    sheets = []
    for x in timing["scenes"]:
        a, b = float(x["start"]), float(x["end"])
        times = [round(a + (b - a) * (k + 0.5) / 4, 3) for k in range(4)]
        pngs = stills(film_dir, times, work / ".review" / x["id"])
        sheets.append((f"Scene '{x['id']}' ({a:.2f}-{b:.2f} s): {next(s['what'] for s in plan['scenes'] if s['id'] == x['id'])[:400]}",
                       grid(pngs, times, work / ".review" / f"{x['id']}.jpg", 4, 300)))
    cuts = [float(x["start"]) for x in timing["scenes"][1:]]
    if cuts:
        times = sorted({round(max(0.0, c + d), 3) for c in cuts for d in (-0.5, -0.15, 0.15, 0.5)})
        pngs = stills(film_dir, times, work / ".review" / "cuts")
        sheets.append(("The hand-overs (around each scene change):", grid(pngs, times, work / ".review" / "cuts.jpg", 4, 300)))
    v = _review(FILM_REVIEW, f"The film: {plan['title']}: {plan['logline']}", sheets, "animated-film-review")
    v.setdefault("scenes", {})
    return v


def make(brief: str, work: Path, seconds: float = 60.0, shape: str = "16:9", kit: str = "paper", log=print,
         pitch=None, library: Path | None = None) -> dict:
    """The whole film: directed, pitched, every scene built and checked, rendered, its sound made and mixed.
    `pitch(summary) -> None | str`: shown to the person before the scenes are built; None goes on, words change it.
    Returns {video, picture, plan, timing, music, effects, scenes: [{id, start, end, picture}], review}."""
    if shape not in SHAPES:
        raise AnimatedError(f"the shape must be one of {', '.join(SHAPES)}")
    if len(" ".join(brief.split())) < 20:
        raise AnimatedError("say what the film is about (a few sentences at least)")
    kits.folder(kit)
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    d = work / "director"
    if not (d / ".ready").exists():
        log("Writing the story, making the music and timing every scene on it")
        director_desk(brief, d, seconds, shape, kit)
        (d / ".ready").write_text("checked", encoding="utf-8")
    plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    timing = json.loads((d / "timing.json").read_text(encoding="utf-8"))
    if pitch is not None and not (d / ".approved").exists():
        while True:
            notes = pitch(pitch_of(d, plan, timing))
            if not notes:
                break
            log("Changing the pitch as they asked")
            director_desk(brief, d, seconds, shape, kit, notes=notes)
            plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
            timing = json.loads((d / "timing.json").read_text(encoding="utf-8"))
        (d / ".approved").write_text("yes", encoding="utf-8")
    total = float(timing["seconds"])
    film = film_of(shape, total, timing["scenes"])
    music = d / timing["music"] if timing.get("music") else None
    audio_path = work / "audio.json"
    if not audio_path.exists():
        from ..music import arrange

        audio = (arrange.motion_data(music, FPS) if music else
                 {"env": [], "kick": [], "beats": [], "bars": [], "period": 60.0 / float(plan["music"]["bpm"]),
                  "offset": 0.0, "drop": timing.get("drop"), "fps": FPS})
        audio["drop"] = timing.get("drop")
        audio["beat"] = audio.get("period")
        audio_path.write_text(json.dumps(audio), encoding="utf-8")
    audio = json.loads(audio_path.read_text(encoding="utf-8"))
    timing.setdefault("beat", round(float(audio.get("period") or 0.5), 4))
    order = [x["id"] for x in timing["scenes"]]
    log(f"'{plan['title']}': {len(order)} scenes over {total:.1f} s; designing them, {parallel()} at a time")

    def build(sid: str, notes: str = "") -> None:
        folder = work / "scenes" / sid
        if (folder / (".fixed" if notes else ".done")).exists():
            return
        scene_desk(plan, timing, sid, folder, film, audio, kit, notes)
        (folder / ".done").write_text("checked", encoding="utf-8")
        if notes:  # a fix round stopped partway picks up with the scenes it hadn't fixed yet
            (folder / ".fixed").write_text("fixed", encoding="utf-8")
        log(f"Scene '{sid}' {'fixed' if notes else 'designed'}")

    with ThreadPoolExecutor(max_workers=parallel()) as desks:
        list(desks.map(build, order))
    fd = work / "film"
    fd.mkdir(exist_ok=True)
    after = transitions_js(timing)
    files = [work / "scenes" / sid / "scene.js" for sid in order]
    review = {}
    if not quick() and not (fd / ".reviewed").exists():
        if (fd / "review.json").exists():  # watched before and stopped while fixing: the same notes, not a new look
            kept = json.loads((fd / "review.json").read_text(encoding="utf-8"))
            review, problems = kept["review"], kept["checks"]
        else:
            log("Watching the whole film")
            film_dir = page(fd, film, files, after, audio, "review", kit)
            problems = hf_check(film_dir, 0.0, total, samples=max(9, len(order) * 3))
            review = film_review(film_dir, plan, timing, fd, film)
        by_scene = {sid: list(review.get("scenes", {}).get(sid) or []) for sid in order}
        for line in problems:  # the checks' problems go to the scene they happen in
            m = re.match(r"(\d+(?:\.\d+)?) s ", line)
            if m:
                t = float(m.group(1))
                sid = next((x["id"] for x in timing["scenes"] if float(x["start"]) <= t < float(x["end"])), order[-1])
                by_scene[sid].append(line)
        (fd / "review.json").write_text(json.dumps({"review": review, "checks": problems}, indent=1), encoding="utf-8")
        again = [sid for sid in order if by_scene[sid]]
        if again:
            log(f"Fixing what the review found in {len(again)} scene(s)")
            with ThreadPoolExecutor(max_workers=parallel()) as desks:
                list(desks.map(lambda sid: build(sid, "\n".join(f"- {p}" for p in by_scene[sid][:12])), again))
        (fd / ".reviewed").write_text("yes", encoding="utf-8")
    film_dir = page(fd, film, files, after, audio, "film", kit)
    seen = probe(film_dir)
    if seen.get("errors"):
        raise AnimatedError("the film page has script errors: " + "; ".join(seen["errors"][:3]))
    picture = fd / "picture.mp4"
    if not picture.exists():
        log("Rendering the film")
        scenes.render(film_dir, picture, fps=FPS)
    log("Making its sounds and mixing them under the music")
    effects = event_sound.render(seen.get("events") or [], total, fd / "effects.wav", library, music=music)
    video = event_sound.mix(picture, music, fd / "effects.wav", work / "film.mp4", total)
    parts = []
    for x in timing["scenes"]:
        clip = work / "scenes" / x["id"] / "picture.mp4"
        _cut(picture, float(x["start"]), float(x["end"]), clip)
        parts.append({"id": x["id"], "start": float(x["start"]), "end": float(x["end"]), "picture": clip})
    log(f"Done in {time.monotonic() - t0:.0f} s: {video}")
    return {"video": video, "picture": picture, "plan": plan, "timing": timing, "music": music,
            "effects": fd / "effects.wav", "sounds": effects, "scenes": parts, "review": review, "film": film}


def pitch_of(d: Path, plan: dict, timing: dict) -> dict:
    """What the person sees before anything is built: the story, the scenes and their lines, the music."""
    music = d / timing["music"] if timing.get("music") else None
    return {"title": plan["title"], "logline": plan["logline"], "seconds": float(timing["seconds"]),
            "scenes": [{"id": s["id"], "what": s["what"], "lines": s.get("lines") or [],
                        "start": next(float(x["start"]) for x in timing["scenes"] if x["id"] == s["id"]),
                        "end": next(float(x["end"]) for x in timing["scenes"] if x["id"] == s["id"])}
                       for s in plan["scenes"]],
            "music": str(music) if music else None, "drop": timing.get("drop"),
            "treatment": (d / "treatment.md").read_text(encoding="utf-8") if (d / "treatment.md").exists() else ""}


def _cut(picture: Path, a: float, b: float, out: Path) -> Path:
    from ..ffmpeg import ffmpeg_bin

    done = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{a:.3f}", "-i", str(picture), "-t",
                           f"{b - a:.3f}", "-an", "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt",
                           "yuv420p", str(out)], capture_output=True)
    if done.returncode != 0:
        raise AnimatedError("a scene couldn't be cut from the film: " + done.stderr.decode(errors="replace")[-300:])
    return out


if __name__ == "__main__":  # the desks' commands: python -m vidosuper.motion.animated <command> <folder> ...
    if len(sys.argv) >= 3:
        cmd, desk_dir, rest = sys.argv[1], Path(sys.argv[2]), sys.argv[3:]
        if cmd == "check":
            check_command(desk_dir, rest)
        elif cmd == "compose":
            compose_command(desk_dir)
        elif cmd == "music":
            music_command(desk_dir, rest)
        elif cmd == "arrange":
            arrange_command(desk_dir, rest)
        elif cmd == "look":
            look_command(desk_dir, rest)
        elif cmd == "motion":
            motion_command(desk_dir, rest)
        elif cmd == "lint":
            lint_command(desk_dir)
        elif cmd == "review":
            review_command(desk_dir)
        else:
            print(f"There's no command {cmd} here.")
