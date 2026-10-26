"""Cartoons written as code: a story told as a short animated film, every shot designed and animated by Claude for
that story. Nothing comes from a character kit or a template engine.

1. The board: a director at a desk (Claude Code) reads the story, writes the treatment, decides the look, paints
   the cast's model sheets and a style key frame, and writes the shot list (`board.json`): each shot's time, size,
   angle, what happens, its sounds and the narration in it. `bash check` holds it to the rules (whole film covered,
   cuts on the beat, every character painted).
2. The music is composed for the board (on this machine's GPU, or by ElevenLabs Music; at the board's tempo, the
   cuts on its bars) while the shots are made.
3. Each shot is animated at a desk of its own, the way the scene designer works (motion/scenes.py): painted parts
   in the film's style (`bash paint`, `--like` the model sheets), cut-out puppets rigged in HTML and animated with
   GSAP, painted worlds with parallax, 3D from Blender when it's there (`bash blender`), `bash look` to see its
   frames, `bash review` for a strict animation director. Shots are made side by side.
4. Each shot is rendered frame by frame (HyperFrames); the shots are cut together, and the music, the narration and
   every shot's sounds (each made to its cue) are mixed under them.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import scenes

CARTOON_CRAFT = Path(__file__).with_name("cartoon_craft.md")
SIZES = ("extreme wide", "wide", "full", "medium", "close-up", "extreme close-up", "insert", "over the shoulder", "pov")
SHAPES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}
FPS = 30
MIN_SHOT, MAX_SHOT = 0.8, 12.0
PAGE = threading.Lock()  # scenes builds pages from module-wide assets: one shot's pages at a time in this process


class CartoonError(RuntimeError):
    pass


@dataclass
class Shot:
    n: int
    start: float
    end: float
    size: str
    angle: str
    what: str
    sounds: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)


# ---------------------------------------------------------------- the desks (Claude Code)

def _env() -> dict:
    """Claude Code on the person's own plan (an API key in the environment would be billed instead), unless they
    chose the Anthropic API as the brain, when it is that key that pays."""
    from ..local import plan_env

    env = plan_env() if scenes.VIA != "api" else dict(os.environ)
    return {**env, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "64000", "VIDO_SCENES_VIA": scenes.VIA,
            "BASH_DEFAULT_TIMEOUT_MS": "900000", "BASH_MAX_TIMEOUT_MS": "3600000"}


def _scripts(folder: Path, commands: tuple[str, ...]) -> None:
    """`bash <command> ...` at the desk: this module's command line on that folder."""
    py = Path(sys.executable).as_posix()
    for name in commands:
        (folder / name).write_text(f'#!/bin/bash\n"{py}" -m vidosuper.motion.cartoon {name} "{folder.as_posix()}" "$@"\n',
                                   encoding="utf-8")


def _desk(folder: Path, system: str, prompt: str, commands: tuple[str, ...], budget: float, timeout: float) -> str:
    """A Claude Code session at this desk with its own commands; returns what it said last."""
    from ..local import claude_exe, run_claude

    _scripts(folder, commands)
    (folder / "system.md").write_text(system, encoding="utf-8")
    cmd = [claude_exe(), "-p", "--output-format", "json", "--model", scenes.DESIGN_MODEL, "--system-prompt-file",
           str(folder / "system.md"), "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config",
           "--max-budget-usd", f"{budget:g}", "--tools", "Read,Write,Edit,Bash", "--allowedTools", "Read(./**)",
           "Write(./**)", "Edit(./**)", *[f"Bash(bash {c}:*)" for c in commands]]  # its own folder only
    proc = run_claude(cmd, prompt, folder, cwd=str(folder), timeout=timeout, env=_env())
    (folder / "session.json").write_bytes(proc.stdout or b"")
    try:
        said = str(json.loads(proc.stdout.decode("utf-8", errors="replace")).get("result") or "")
    except ValueError:
        said = ""
    if re.search(r"(session|usage|weekly) limit|hit your limit", said, re.I):
        raise CartoonError(f"the Claude plan's limit is reached: {said[:160]}")
    if proc.returncode != 0 and not said:
        raise CartoonError("the desk stopped: " + proc.stderr.decode("utf-8", errors="replace")[-600:])
    return said


# ---------------------------------------------------------------- 1. the board

BOARD_SYSTEM = """You are a director and writer of short animated films: the kind whose shorts win festivals and \
go viral, with a clear story, specific funny or moving beats, and shots designed like a film. You turn a person's \
story into a cartoon of about {SECONDS} s, {W} x {H} px ({SHAPE}), and plan it so that animators can make each \
shot on its own.

{CRAFT}

# Your desk
- The story is in story.md (and narration.json when the person recorded it: their words with times, which the film \
is cut to). Read it first.
- Write board.md: the logline, the want, the obstacle, the turn, the ending and what it pays off; then three \
different ways to tell it, and the one you pick and why (the obvious version is the one everyone makes).
- Decide the look, unless they named a style (then that is the look): a style a top studio would pick for this \
story (a painted storybook, a 90s TV cartoon, paper cut-out, gouache, claymation-like, ink and wash, a stylised \
3D look, anime...), the palette (4 to 6 hex colours), the materials, the light.
- Paint the style key frame first: `bash paint style_key "<one establishing frame of the film: the place, the \
main character in it, the light, in the look>"`. Read it. Paint again until it is a frame you would put on the \
poster. Everything else is painted `--like style_key`.
- Paint each character's model sheet: `bash paint <id> "<the character, full body, neutral pose, three-quarter \
view, arms a little away from the body, the look>" --like style_key`. Read each; paint again if it is off model or \
off style.
- Write board.json (the Write tool), exactly this shape:
  {"title": "...", "logline": "...",
   "look": {"style": "...", "palette": ["#..."], "materials": "...", "light": "...", "type": "a font, if any words"},
   "cast": [{"id": "<the name its model sheet was painted under>", "who": "...", "parts": ["head", "torso", ...]}],
   "music": {"bpm": 60-180, "styles": ["genre", "instruments and how they're played", "mood"], "avoid": [], \
"parts": {"intro": ["..."], "build": ["..."], "drop": ["..."], "breath": ["..."], "ending": ["..."]}},
   "shots": [{"n": 1, "start": 0.0, "end": 2.5, "size": "wide", "angle": "eye level", \
"what": "<what happens in this shot, beat by beat, with the acting and the camera move>", \
"sounds": ["<each sound in it, described so it can be made>"]}, ...]}
  Sizes: {SIZES}. Shots follow each other with no gap from 0 to the end of the film; each lasts {MIN} to {MAX} s. \
{TIMING}
- Run `bash check` and fix everything it says, until it says the board is ready. Then reply DONE."""

BOARD_TIMING_MUSIC = ("Every cut lands on a beat of the music: at the bpm you chose, a beat is 60/bpm s, and each "
                      "shot's start and end are whole beats (check snaps them to the nearest beat).")
BOARD_TIMING_VOICE = ("The film is cut to their narration: it lasts as long as the narration (plus a breath at the "
                      "end), and cuts fall in the pauses between their words, never inside a word. The music sits "
                      "under their voice.")


def board_desk(story: str, folder: Path, seconds: float, shape: str, style: str = "",
               narration: list[tuple[float, float, str]] | None = None, timeout: float = 5400.0) -> dict:
    """The board, written and painted at the director's desk, checked. Returns board.json (shots snapped)."""
    folder.mkdir(parents=True, exist_ok=True)
    w, h = SHAPES[shape]
    (folder / "story.md").write_text(story.strip() + (f"\n\nThe style they asked for: {style}" if style else ""),
                                     encoding="utf-8")
    if narration:
        (folder / "narration.json").write_text(json.dumps([{"start": a, "end": b, "word": t} for a, b, t in narration],
                                                          indent=0), encoding="utf-8")
        seconds = round(narration[-1][1] + 1.0, 2)
    (folder / "settings.json").write_text(json.dumps({"seconds": seconds, "shape": shape, "width": w, "height": h,
                                                      "narration": bool(narration)}), encoding="utf-8")
    system = (BOARD_SYSTEM.replace("{CRAFT}", CARTOON_CRAFT.read_text(encoding="utf-8"))
              .replace("{SECONDS}", f"{seconds:g}").replace("{W}", str(w)).replace("{H}", str(h))
              .replace("{SHAPE}", shape).replace("{SIZES}", ", ".join(SIZES)).replace("{MIN}", f"{MIN_SHOT:g}")
              .replace("{MAX}", f"{MAX_SHOT:g}")
              .replace("{TIMING}", BOARD_TIMING_VOICE if narration else BOARD_TIMING_MUSIC))
    _desk(folder, system, "Make the board for the story in story.md.", ("paint", "check"), 30, timeout)
    board, problems = check_board(folder)
    if board is None or problems:
        raise CartoonError("the board isn't ready: " + "; ".join(problems or ["no board.json"])[:600])
    return board


def check_board(folder: Path) -> tuple[dict | None, list[str]]:
    """board.json against the rules, its cuts snapped to the beat (or into the narration's pauses) and written back.
    Returns (the board, the problems left)."""
    path = folder / "board.json"
    try:
        board = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [f"board.json can't be read: {exc}"]
    settings = json.loads((folder / "settings.json").read_text(encoding="utf-8"))
    problems: list[str] = []
    for key in ("title", "look", "cast", "music", "shots"):
        if not board.get(key):
            problems.append(f"board.json has no {key}")
    if problems:
        return board, problems
    for c in board["cast"]:
        if not (folder / "assets" / f"{c.get('id', '')}.png").exists():
            problems.append(f"the cast member {c.get('id')!r} has no model sheet: bash paint {c.get('id')} \"...\" "
                            "--like style_key")
    if not (folder / "assets" / "style_key.png").exists():
        problems.append("there is no style key frame: bash paint style_key \"...\"")
    bpm = float(board["music"].get("bpm") or 0)
    if not 60 <= bpm <= 180:
        problems.append("music.bpm must be between 60 and 180")
        bpm = 100.0
    narration = []
    if settings.get("narration") and (folder / "narration.json").exists():
        narration = [(w["start"], w["end"]) for w in json.loads((folder / "narration.json").read_text(encoding="utf-8"))]
    shots = sorted(board["shots"], key=lambda s: float(s.get("start", 0)))
    total = float(settings["seconds"])
    cuts = [0.0] + [float(s.get("end", 0)) for s in shots]
    snapped = [0.0]
    for t in cuts[1:-1]:
        snapped.append(_snap_pause(t, narration) if narration else round(round(t * bpm / 60) * 60 / bpm, 3))
    end = float(cuts[-1])
    if narration:
        end = max(end, narration[-1][1] + 0.4)
    else:
        end = round(round(end * bpm / 60) * 60 / bpm, 3)
    snapped.append(end)
    for k, s in enumerate(shots):
        s["n"], s["start"], s["end"] = k + 1, snapped[k], snapped[k + 1]
        length = s["end"] - s["start"]
        if not MIN_SHOT - 1e-6 <= length <= MAX_SHOT + 1e-6:
            problems.append(f"shot {k + 1} lasts {length:.2f} s (on the beat): make it {MIN_SHOT:g} to {MAX_SHOT:g} s")
        if str(s.get("size", "")).lower() not in SIZES:
            problems.append(f"shot {k + 1}: size {s.get('size')!r} isn't one of {', '.join(SIZES)}")
        if len(str(s.get("what", ""))) < 40:
            problems.append(f"shot {k + 1}: 'what' must say what happens beat by beat, with the acting and the camera")
    for k in range(1, len(shots)):
        a, b = shots[k - 1], shots[k]
        same = all(str(a.get(f, "")).lower() == str(b.get(f, "")).lower() for f in ("size", "angle"))
        if same and "same framing" not in str(b.get("what", "")).lower():
            problems.append(f"shots {k} and {k + 1} have the same size and angle: vary one (or, when repeating the "
                            f"framing is the point, write 'same framing' and why in shot {k + 1}'s 'what')")
    if abs(end - total) > max(4.0, 0.25 * total):
        problems.append(f"the film lasts {end:.1f} s; it should be about {total:.0f} s")
    board["shots"] = shots
    board["seconds"] = round(end, 3)
    path.write_text(json.dumps(board, indent=1, ensure_ascii=False), encoding="utf-8")
    return board, problems


def _snap_pause(t: float, narration: list[tuple[float, float]]) -> float:
    """The cut moved out of any word it falls in, to the nearer edge of that word's pause."""
    for a, b in narration:
        if a < t < b:
            return round(a if t - a < b - t else b, 3)
    return round(t, 3)


def board_shots(board: dict) -> list[Shot]:
    return [Shot(int(s["n"]), float(s["start"]), float(s["end"]), str(s.get("size", "")), str(s.get("angle", "")),
                 str(s.get("what", "")), [str(x) for x in s.get("sounds") or []]) for s in board["shots"]]


# ---------------------------------------------------------------- 3. the shots

SHOT_SYSTEM = """You are a top character animator and creative coder: you animate one shot of a short animated \
film by writing it as code, one HyperFrames composition (HTML, CSS, SVG and JavaScript with GSAP 3.13) rendered \
frame by frame. The film's director has planned the story, the look and every shot; you make this shot as well as \
the best studio would, so it cuts with the shots around it.

{CRAFT}

# The rig kit (loaded as `window.rig`)
- `rig.drop(tl, el, {{at, y0, y1, g: 3200, e: 0.55, bounces: 3, squash: 0.28, x0, x1, shadow}})` -> impacts \
[{{t, v}}]; `rig.spring(name, damping, wobbles)` -> an ease name; `rig.wobble(tl, el, {{at, prop, amount, freq, \
decay, duration}})`; `rig.cascade(tl, parts, vars, {{at, lag}})` (follow-through down a chain of parts); \
`rig.burst(tl, make, {{at, x, y, n, speed, dir, spread, gravity, drag, life, seed}})`; `rig.blur(tl, el, {{at, \
duration, px, dir}})` (motion blur on a fast move); `rig.shake(tl, el, {{at, amount, duration, freq, rotate, seed, \
base}})` (a camera shake); `rig.rand(seed)` -> a seeded random().

# Motion craft (measured from professional films; your defaults)
{MOTION}

# The technical contract (follow exactly, or the render fails)
- ONE complete HTML document. Body: `<div id="root" data-composition-id="main" data-start="0" \
data-duration="{DURATION}" data-width="{W}" data-height="{H}">`, inside it one wrapper `<div id="stage" class="clip" \
data-start="0" data-duration="{DURATION}">` (`position:absolute; inset:0; overflow:hidden`) that holds everything. \
The whole frame is yours: paint the world edge to edge (no empty or white areas).
- The camera: animate #stage, or a world wrapper inside it, with parallax between layers. Keep the frame covered: \
scale up by more than the move.
- GSAP 3.13 and its plugins are loaded and registered: {PLUGINS}. ONE timeline: `const tl = gsap.timeline({{ paused: \
true }});`, every tween at an absolute time in seconds, `fromTo` (not `from`), then `window.__timelines["main"] = tl;`.
- Everything visible is a function of timeline time: no Date, timers, requestAnimationFrame, network, files outside \
assets/, or unseeded Math.random (use rig.rand). Finite repeats only. Canvas: draw from time in a \
`window.addEventListener("hf-seek", e => draw(e.detail.time))` listener and draw(0) once.
- Painted pieces are `<img src="assets/<name>.png">`. A Blender clip is `<video src="assets/<name>.webm" \
data-start="<s>" data-duration="<s>" muted playsinline>` (time the video, not a wrapper around it).
- Fonts by name: {FONTS}.
- Two JSON blocks in the body: `<script type="application/json" id="beats">[{{"t": 1.2, "what": "..."}}]</script>` \
(the key moments, seconds into the shot: each is checked frame by frame) and `<script type="application/json" \
id="sound">[{{"t": 1.15, "sound": "a soft cloth swish as she spins", "gain": -14}}]</script>` (a sound for every \
action, described so it can be made; gain -8 loud to -22 subtle).

# Your desk
- brief.md is this shot: the film's look, the cast, what happens, the shots before and after it. The film's style \
key frame and the cast's model sheets are in assets/ (style_key.png and <cast id>.png): Read them all first, and \
keep everything on model and in style.
- Paint every piece: `bash paint <name> "<what, its pose and angle, its light>" [<name> "<what>" ...]` paints them \
side by side in the film's style (`--like <name>` to match an asset other than the style key: a character's \
parts `--like` its model sheet). Each comes out as a cleanly cut-out PNG in assets/. Read each one; paint again \
when it is off model, off style or the wrong angle. Code draws only what is truly graphic (effects, lines, light).
{BLENDER}- Write the shot to scene.html. To see it: `bash look 0.8 1.6` (stills at those seconds) or `bash look \
1.2-2.0` (a strip of 6 frames across a stretch, for motion). Read every image. Look at every beat and between them.
- When it is right, `bash review`: a strict animation director judges it (2 to 4 minutes) and prints a score, every \
problem with its time, and the strips it judged. Fix everything, look again, ask again.
- When the director says it is ready, or after its third review with everything fixed as well as you can, reply DONE."""

BLENDER_LINE = """- 3D: Blender is here. `bash blender <name> <script.py> [<seconds>]` runs your Blender Python script \
(write it with the Write tool) on an empty scene and renders it with a transparent background to \
assets/<name>.webm (and assets/<name>.png, its middle frame: Read it). The script gets `bpy`, `scene`, `W`, `H`, \
`FPS`, `FRAMES`, `SECONDS` and `ASSETS` (the assets folder); it builds the objects, materials, lights, a camera \
(set `scene.camera`) and their keyframes over frames 1 to FRAMES. Use it when the shot needs real 3D (a turning \
object, a fly-through, a set with depth); light it to match the look.
"""

SHOT_REVIEW = """You are a strict animation director reviewing one shot of a short animated film. You see the film's \
style key frame and the cast's model sheets, then a strip of 4 frames around each key moment of the shot (left to \
right, times on them) with what the animator meant to show. Judge it like the director of a top studio:
1. Does it tell its beat of the story clearly, as the board describes (the action, the acting, the camera)?
2. Acting: strong readable poses, anticipation, follow-through, arcs, eyes that lead, expressions that change on \
the beat, timing that gives weight. Stiff puppets sliding around, or poses that don't read, are problems.
3. On model and in style: the characters match their model sheets; everything matches the style key; nothing looks \
like clip art, a flat vector drawing, or a different film.
4. Film grammar: the shot size and angle the board asks for, a composition with one clear focal point, the camera \
move with intent, depth (layers, light, shadows).
5. Anything broken: parts that separate at joints, missing or invisible elements, empty or unpainted areas of the \
frame, things cut off that shouldn't be, leftovers, text you can't read.
Reply with JSON only: {"score": 0-10 (10 = a festival short), "ok": true when ready (score >= 8 and nothing \
serious), "problems": ["at 1.4 s: the arm separates from the shoulder as he waves: move the elbow joint ...", ...]}"""


def _shot_brief(board: dict, shot: Shot, shots: list[Shot], narration: list[tuple[float, float, str]]) -> str:
    look = board["look"]
    cast = "\n".join(f"- {c['id']} (assets/{c['id']}.png): {c.get('who', '')}; parts: {', '.join(c.get('parts') or [])}"
                     for c in board["cast"])
    before = next((s for s in shots if s.n == shot.n - 1), None)
    after = next((s for s in shots if s.n == shot.n + 1), None)
    words = [(round(a - shot.start, 2), round(b - shot.start, 2), t) for a, b, t in narration
             if shot.start <= a < shot.end]
    return (f"The film: {board['title']}. {board.get('logline', '')}\n"
            f"Its look: {look.get('style', '')}; palette {', '.join(look.get('palette') or [])}; materials: "
            f"{look.get('materials', '')}; light: {look.get('light', '')}"
            + (f"; type: {look['type']}" if look.get("type") else "") + "\n\n"
            f"The cast:\n{cast}\n\n"
            f"THIS SHOT: shot {shot.n} of {len(shots)}, {shot.duration:.2f} s (DURATION = {shot.duration:.2f}), "
            f"{shot.size}, {shot.angle}.\nWhat happens: {shot.what}\n"
            + (f"Its sounds: {'; '.join(shot.sounds)}\n" if shot.sounds else "")
            + ("The narration in it (seconds into the shot): " + " ".join(f"{a:.2f}-{b:.2f} {t}" for a, b, t in words)
               + "\n" if words else "")
            + (f"\nThe shot before (cut from it on action and screen direction): {before.size}, {before.angle}: "
               f"{before.what}\n" if before else "\nThis is the first shot: it opens the film.\n")
            + (f"The shot after: {after.size}, {after.angle}: {after.what}\n" if after else
               "This is the last shot: it ends the film.\n"))


def shot_desk(board: dict, board_dir: Path, shot: Shot, shots: list[Shot], folder: Path, width: int, height: int,
              narration: list[tuple[float, float, str]], problems: list[str] | None = None,
              timeout: float = 5400.0) -> str:
    """The shot animated at its own desk (painted pieces, look, review). Returns its scene.html."""
    from . import blender

    folder.mkdir(parents=True, exist_ok=True)
    (folder / "assets").mkdir(exist_ok=True)
    for c in [{"id": "style_key"}, *board["cast"]]:  # the film's style and its cast, on every desk
        src = board_dir / "assets" / f"{c['id']}.png"
        if src.exists():
            shutil.copyfile(src, folder / "assets" / src.name)
    note = ""
    if problems:
        note = ("\n\nscene.html holds your previous version. The director judged it and found:\n- "
                + "\n- ".join(problems) + "\nFix every one of these, keep what works.")
    (folder / "brief.md").write_text(_shot_brief(board, shot, shots, narration) + note, encoding="utf-8")
    (folder / "brief.json").write_text(json.dumps({"ask": f"Shot {shot.n}: {shot.what}", "start": 0.0,
                                                   "end": shot.duration, "width": width, "height": height,
                                                   "fps": FPS, "style": board["look"].get("style", ""),
                                                   "words": [], "cast": [c["id"] for c in board["cast"]]}),
                                       encoding="utf-8")
    has_blender = bool(blender.find())
    system = (SHOT_SYSTEM.replace("{CRAFT}", CARTOON_CRAFT.read_text(encoding="utf-8"))
              .replace("{MOTION}", scenes.CRAFT.read_text(encoding="utf-8") if scenes.CRAFT.exists() else "")
              .replace("{PLUGINS}", ", ".join(scenes.PLUGINS)).replace("{FONTS}", scenes.FONTS)
              .replace("{BLENDER}", BLENDER_LINE if has_blender else "")
              .replace("{DURATION}", f"{shot.duration:.2f}").replace("{W}", str(width)).replace("{H}", str(height))
              .replace("{{", "{").replace("}}", "}"))
    commands = ("look", "paint", "review", *(("blender",) if has_blender else ()))
    _desk(folder, system, "Animate the shot described in brief.md. Start by reading brief.md and every image in "
                          "assets/.", commands, 40, timeout)
    html = (folder / "scene.html").read_text(encoding="utf-8") if (folder / "scene.html").exists() else ""
    if "data-composition-id" not in html:
        raise CartoonError(f"shot {shot.n}: the animator left no scene")
    return html


def review(folder: Path, html: str, work: Path) -> dict:
    """The shot around its beats, judged by the animation director with the style key and the model sheets beside
    it. {score, ok, problems, sheet}."""
    info = json.loads((folder / "brief.json").read_text(encoding="utf-8"))
    duration = float(info["end"])
    beats = scenes.beats_of(html, duration)
    keys = sorted({round(min(duration - 0.05, float(b["t"]) + 0.2), 2) for b in beats})
    if len(keys) < 3:
        keys = sorted(set(keys) | {round(duration * f, 2) for f in (0.2, 0.5, 0.8)})
    keys = keys[:8]
    around = {k: [round(min(duration - 0.03, max(0.0, k + d)), 2) for d in (-0.3, -0.05, 0.2, 0.5)] for k in keys}
    with PAGE:
        _stage(folder)
        seen = scenes.moments(html, work, sorted({t for ts in around.values() for t in ts}))
    content: list[dict] = [{"type": "text", "text": f"The shot: {info['ask']}\nIts look: {info.get('style', '')}\n"
                            "The film's style key frame, then the cast's model sheets:"}]
    for name in ["style_key", *info.get("cast", [])]:
        img = folder / "assets" / f"{name}.png"
        if img.exists():
            content.append({"type": "text", "text": name})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.b64encode(_jpeg(img)).decode("ascii")}})
    sheet = []
    for k in keys:
        jpg = scenes.strip([(t, seen.get(t)) for t in around[k]], 360)
        if jpg is None:
            continue
        sheet.append((k, jpg))
        meant = "; ".join(str(b.get("what", "")) for b in beats if abs(float(b["t"]) + 0.2 - k) < 0.05)
        content.append({"type": "text", "text": f"Around {k:.2f} s ({around[k][0]:.2f} to {around[k][-1]:.2f} s). "
                                                f"The animator meant: {meant or '(between beats)'}"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(jpg).decode("ascii")}})
    reply = scenes._ask(scenes._client(), SHOT_REVIEW, content, 16000, None, "cartoon-review", "high")
    m = re.search(r"\{.*\}", reply, re.S)
    try:
        verdict = json.loads(m.group(0)) if m else {}
    except ValueError:
        verdict = {}
    return {"score": verdict.get("score", 0), "ok": bool(verdict.get("ok")), "problems": verdict.get("problems") or [],
            "sheet": sheet}


def _jpeg(png: Path, side: int = 640) -> bytes:
    """A painted asset as a JPEG on a mid grey (its transparency shown flat), for a reviewer's eyes."""
    import cv2
    import numpy as np

    img = cv2.imread(str(png), cv2.IMREAD_UNCHANGED)
    if img is None:
        return b""
    if img.ndim == 3 and img.shape[2] == 4:
        a = img[..., 3:4].astype(np.float32) / 255.0
        img = (img[..., :3].astype(np.float32) * a + 128.0 * (1 - a)).astype(np.uint8)
    scale = side / max(img.shape[:2])
    if scale < 1:
        img = cv2.resize(img, (int(img.shape[1] * scale), int(img.shape[0] * scale)), interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return jpg.tobytes() if ok else b""


def _stage(folder: Path) -> None:
    """This process builds the shot's pages from its desk: its painted assets, no footage, no person."""
    scenes.ASSETS[:] = [folder / "assets"]
    scenes.STAGE[:] = []
    scenes.PERSON_TRACK[:] = []


# ---------------------------------------------------------------- the desk's commands

def paint_command(folder: Path, args: list[str]) -> None:
    """`bash paint <name> "<what>" [...] [--like <name>]`: painted side by side in the film's style (the style key,
    or the asset named with --like)."""
    like = "style_key"
    if "--like" in args:
        k = args.index("--like")
        like = args[k + 1] if k + 1 < len(args) else like
        args = args[:k] + args[k + 2:]
    like_path = folder / "assets" / f"{re.sub(r'[^a-z0-9_-]', '', like.lower())}.png"
    jobs = [(re.sub(r"[^a-z0-9_-]", "", args[i].lower())[:40] or "asset", args[i + 1]) for i in range(0, len(args) - 1, 2)]
    if not jobs:
        print('Say what to paint: bash paint <name> "<what>" [<name> "<what>" ...] [--like <name>]')
        return

    def one(job):
        name, what = job
        ref = like_path if like_path.exists() and name != like_path.stem else None
        try:
            w, h = scenes.paint(what, folder / "assets" / f"{name}.png", None, ref)
        except Exception as exc:  # noqa: BLE001 - said to the painter at the desk, who tries again
            from ..local import health

            health.failed("gemini", exc)
            return f"{name}: PAINTING FAILED: {exc}"
        return (f'assets/{name}.png  {w}x{h} px (transparent PNG' + (f", in the style of {like_path.stem}" if ref else "")
                + f'). Read it to see it; use it as <img src="assets/{name}.png">')

    with ThreadPoolExecutor(max_workers=6) as pool:
        for line in pool.map(one, jobs[:12]):
            print(line)


def check_command(folder: Path) -> None:
    board, problems = check_board(folder)
    if board is None or problems:
        print("The board isn't ready yet:\n- " + "\n- ".join(problems or ["write board.json"]))
        return
    cuts = ", ".join(f"{s['start']:.2f}" for s in board["shots"][1:])
    print(f"The board is ready: {len(board['shots'])} shots, {board['seconds']:.2f} s, cuts at {cuts} s.")


def look_command(folder: Path, asked: list[str]) -> None:
    _stage(folder)
    scenes.look_command(folder, asked)


def review_command(folder: Path) -> None:
    html = (folder / "scene.html").read_text(encoding="utf-8") if (folder / "scene.html").exists() else ""
    if not html:
        print("There is no scene.html to review yet.")
        return
    out = folder / "reviews"
    out.mkdir(exist_ok=True)
    n = len(list(out.glob("review-*.md"))) + 1
    try:
        seen = review(folder, html, folder / ".review" / str(n))
    except scenes.SceneError as exc:
        print(f"THE SCENE FAILED TO RUN: {exc}")
        return
    strips = []
    for t, jpg in seen["sheet"]:
        path = out / f"review-{n}-around-{t:05.2f}s.jpg"
        path.write_bytes(jpg)
        strips.append(str(path))
    said = (f"The director's review {n}: {seen['score']}/10" + (", ready." if seen["ok"] else ", not ready yet.")
            + "".join(f"\n- {p}" for p in seen["problems"]))
    (out / f"review-{n}.md").write_text(said, encoding="utf-8")
    (out / f"review-{n}.json").write_text(json.dumps({"scene": scenes._digest(html), "score": seen["score"],
                                                      "ok": seen["ok"], "problems": seen["problems"]}),
                                          encoding="utf-8")
    print(said)
    print("\nThe strips it judged (4 frames around each key moment, left to right):\n" + "\n".join(strips))


def blender_command(folder: Path, args: list[str]) -> None:
    """`bash blender <name> <script.py> [<seconds>]`: the script's 3D rendered to assets/<name>.webm."""
    from . import blender

    if len(args) < 2:
        print("Say what to render: bash blender <name> <script.py> [<seconds>]")
        return
    name = re.sub(r"[^a-z0-9_-]", "", args[0].lower())[:40] or "blender"
    script = (folder / args[1]).resolve()
    if folder.resolve() not in script.parents or not script.exists():
        print(f"There's no script {args[1]} on your desk.")
        return
    info = json.loads((folder / "brief.json").read_text(encoding="utf-8"))
    seconds = float(args[2]) if len(args) > 2 else float(info["end"])
    t0 = time.monotonic()
    try:
        out = blender.render(script, folder / "assets" / f"{name}.webm", width=int(info["width"]),
                             height=int(info["height"]), fps=int(info["fps"]), seconds=seconds,
                             assets=folder / "assets")
    except (blender.BlenderError, subprocess.TimeoutExpired) as exc:
        print(f"BLENDER FAILED: {exc}")
        return
    print(f"assets/{name}.webm ({seconds:.2f} s, transparent) in {time.monotonic() - t0:.0f} s; its middle frame: "
          f"{out.with_suffix('.png')} (Read it). Use it as <video src=\"assets/{name}.webm\" data-start=\"<s>\" "
          f"data-duration=\"{seconds:.2f}\" muted playsinline>.")


# ---------------------------------------------------------------- 2. the music

def music_for(board: dict, seconds: float, out: Path, under_voice: bool) -> Path | None:
    """An original track for the film at the board's tempo, its bars laid out for its length (None: nothing here can
    make music, or it failed: the film is made without, and that is said)."""
    from ..music import compose, made

    if not compose.available():
        return None
    m = board["music"]
    bpm = made.fit_bpm(int(round(float(m.get("bpm") or 100))), "bed" if under_voice else "cut")
    sound = made.Sound(title=board["title"], bpm=bpm, styles=[str(x) for x in m.get("styles") or []][:20],
                       avoid=[str(x) for x in m.get("avoid") or []][:20],
                       parts={k: [str(x) for x in v][:10] for k, v in (m.get("parts") or {}).items()})
    sections = made.layout(seconds, bpm, "bed" if under_voice else "cut")
    try:
        compose.compose_plan(made.plan(sections, sound, "bed" if under_voice else "cut"), out)
    except compose.ComposeError:
        return None
    return out if out.exists() else None


# ---------------------------------------------------------------- 4. the film

def render_shot(folder: Path, html: str, out: Path) -> Path:
    with PAGE:
        _stage(folder)
        page = scenes.project(html, folder / "render")
    return scenes.render(page, out, FPS)


def cut_together(pictures: list[Path], out: Path) -> Path:
    """The shots' pictures one after another, frame for frame."""
    from ..ffmpeg import ffmpeg_bin

    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in pictures), encoding="utf-8")
    done = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                           "-c:v", "libx264", "-preset", "slow", "-crf", "14", "-pix_fmt", "yuv420p", "-r", str(FPS),
                           "-movflags", "+faststart", str(out)], capture_output=True)
    if done.returncode != 0:
        raise CartoonError("the shots couldn't be cut together: " + done.stderr.decode(errors="replace")[-600:])
    return out


def mix(picture: Path, seconds: float, music: Path | None, narration: Path | None, cues: list[dict], library: Path,
        out: Path) -> tuple[Path, list[dict]]:
    """The film with its sound: the music (ducked under the narration, faded at the end), the narration, and each
    shot's sounds, made to their cues. Returns the film and the sounds laid ({file, at, seconds, gain})."""
    from ..ffmpeg import ffmpeg_bin

    args = [ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(picture)]
    graph, mixes, n = [], [], 1
    laid = []
    voice_label = None
    if narration is not None:
        args += ["-i", str(narration)]
        graph.append(f"[{n}:a]aresample=48000,asplit=2[voice][key]")
        voice_label = "[voice]"
        mixes.append(voice_label)
        n += 1
    if music is not None:
        args += ["-i", str(music)]
        fade = max(0.0, seconds - 2.0)
        chain = f"[{n}:a]aresample=48000,atrim=0:{seconds:.3f},afade=t=out:st={fade:.3f}:d=2"
        if voice_label:
            graph.append(chain + ",volume=-6dB[bed]")
            graph.append("[bed][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[music]")
        else:
            graph.append(chain + "[music]")
        mixes.append("[music]")
        n += 1
    elif voice_label:
        graph.append("[key]anullsink")
    for k, c in enumerate(cues):
        path = scenes.sound_for(str(c["sound"]), float(c.get("seconds", 1.2)), library)
        if path is None:
            continue
        args += ["-i", str(path)]
        ms = int(float(c["t"]) * 1000)
        gain = max(-30.0, min(-4.0, float(c.get("gain", -14))))
        graph.append(f"[{n}:a]aresample=48000,adelay={ms}|{ms},volume={gain}dB[s{k}]")
        mixes.append(f"[s{k}]")
        laid.append({"file": str(path), "at": round(float(c["t"]), 3), "seconds": float(c.get("seconds", 1.2)),
                     "gain": gain})
        n += 1
    if not mixes:
        shutil.copyfile(picture, out)
        return out, laid
    graph.append(f"{''.join(mixes)}amix=inputs={len(mixes)}:normalize=0:duration=longest,"
                 f"atrim=0:{seconds:.3f},alimiter=limit=0.89[a]")
    args += ["-filter_complex", ";".join(graph), "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
             "-b:a", "256k", "-movflags", "+faststart", str(out)]
    done = subprocess.run(args, capture_output=True)
    if done.returncode != 0:
        raise CartoonError("the sound couldn't be mixed: " + done.stderr.decode(errors="replace")[-800:])
    return out, laid


def narration_words(audio: Path, work: Path) -> list[tuple[float, float, str]]:
    """Their recorded narration, word by word with times (Whisper)."""
    from ..config import load_config
    from ..ffmpeg import ffmpeg_bin
    from ..perceive.transcribe import transcribe

    cfg = load_config()
    wav = work / "narration-16k.wav"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(audio), "-ac", "1", "-ar", "16000", str(wav)],
                   check=True, capture_output=True)
    t = transcribe("narration", wav, cfg.analysis.whisper_model, cfg.models_root / "whisper")
    return [(round(w.start, 3), round(w.end, 3), w.text.strip()) for u in t.utterances if u.confident
            for w in u.words if w.text.strip()]


def parallel_shots() -> int:
    """Shot desks at once: each is a Claude Code session (the plan's pace is the limit, not the machine)."""
    return max(1, min(8, int(os.environ.get("VIDO_CARTOON_PARALLEL", "3"))))


def make(story: str, work: Path, seconds: float = 40.0, shape: str = "9:16", style: str = "",
         narration: Path | None = None, rounds: int = 2, library: Path | None = None, log=print) -> dict:
    """The whole cartoon: board, music, every shot animated and reviewed, rendered, cut and mixed. Returns
    {video, board, shots: [{n, start, end, picture, score}], music, sounds, narration}."""
    if shape not in SHAPES:
        raise CartoonError(f"the shape must be one of {', '.join(SHAPES)}")
    if len(" ".join(story.split())) < 10 and narration is None:
        raise CartoonError("tell the story (a few sentences at least), or give a narration recording")
    work.mkdir(parents=True, exist_ok=True)
    width, height = SHAPES[shape]
    words = narration_words(narration, work) if narration is not None else []
    if narration is not None and not story.strip():
        story = " ".join(w for _, _, w in words)
    t0 = time.monotonic()
    log("Writing the board: the story, the look, the cast, the shots")
    board = board_desk(story, work / "board", seconds, shape, style, words or None)
    shots = board_shots(board)
    total = float(board["seconds"])
    log(f"Board ready: '{board['title']}', {len(shots)} shots, {total:.1f} s ({time.monotonic() - t0:.0f} s)")

    music_box: dict = {}
    pool = ThreadPoolExecutor(max_workers=1)
    music_job = pool.submit(lambda: music_box.setdefault("path", music_for(board, total, work / "music.mp3",
                                                                            under_voice=bool(words))))

    def animate(shot: Shot) -> dict:
        folder = work / "shots" / f"s{shot.n:02d}"
        problems, best = None, None
        for r in range(1, rounds + 1):
            started = time.monotonic()
            html = shot_desk(board, work / "board", shot, shots, folder, width, height, words, problems)
            seen = scenes.last_review(folder, html) or review(folder, html, folder / ".review" / f"final-{r}")
            log(f"Shot {shot.n}: round {r} scored {seen['score']} ({time.monotonic() - started:.0f} s)")
            if best is None or float(seen["score"] or 0) >= float(best["score"] or 0):
                best = {"html": html, "score": seen["score"]}
            if seen["ok"]:
                break
            problems = seen["problems"]
        (folder / "final.html").write_text(best["html"], encoding="utf-8")
        return {"n": shot.n, "start": shot.start, "end": shot.end, "html": best["html"], "score": best["score"],
                "folder": folder}

    log(f"Animating {len(shots)} shots, {parallel_shots()} at a time")
    with ThreadPoolExecutor(max_workers=parallel_shots()) as desks:
        made_shots = list(desks.map(animate, shots))
    log("Rendering the shots")
    pictures, cues = [], []
    for s in made_shots:
        started = time.monotonic()
        picture = render_shot(s["folder"], s["html"], s["folder"] / "picture.mp4")
        s["picture"] = picture
        pictures.append(picture)
        for c in scenes.sounds_of(s["html"], s["end"] - s["start"]):
            cues.append({**c, "t": round(s["start"] + float(c["t"]), 3)})
        log(f"Shot {s['n']} rendered ({time.monotonic() - started:.0f} s)")
    picture = cut_together(pictures, work / "picture.mp4")
    music_job.result()
    pool.shutdown()
    music = music_box.get("path")
    if music is None:
        log("No music: nothing here can make it (no GPU for the music model, no ElevenLabs key), or it failed")
    library = library or (Path(os.environ.get("LOCALAPPDATA", str(work))) / "VidoSuper" / "sfx")
    out, laid = mix(picture, total, music, narration, cues, library, work / "cartoon.mp4")
    log(f"Done in {time.monotonic() - t0:.0f} s: {out}")
    return {"video": out, "board": board, "music": music, "sounds": laid, "narration": narration,
            "shots": [{k: s[k] for k in ("n", "start", "end", "picture", "score")} for s in made_shots]}


if __name__ == "__main__":  # the desks' commands: python -m vidosuper.motion.cartoon <command> <folder> ...
    if len(sys.argv) >= 3:
        cmd, desk_dir, rest = sys.argv[1], Path(sys.argv[2]), sys.argv[3:]
        if cmd == "paint":
            paint_command(desk_dir, rest)
        elif cmd == "check":
            check_command(desk_dir)
        elif cmd == "look":
            look_command(desk_dir, rest)
        elif cmd == "review":
            review_command(desk_dir)
        elif cmd == "blender":
            blender_command(desk_dir, rest)
