"""Cartoons: a story told as a short animated film. Claude directs every shot; what makes the motion is a real
animation engine, never pictures slid around a page.

Three ways to make it (`engine`):
- `cartoon`: each shot's full start frame is painted (the characters in the scene, on model), then a video model
  (Wan 2.2, motion/videogen.py) brings the whole frame to life from what happens in the shot: bodies, fur, cloth,
  light and the camera all move.
- `anime`: the same, with AniSora V3.2 (Wan 2.2 trained on anime): anime timing, held poses and snappy action.
- `rig`: everything built in Blender by Claude's scripts: each character a real rig (bones, a mesh that bends, a
  face that changes), each shot a set, a cast posed and keyed, lights and a camera, rendered as a whole (toon or
  stylised 3D). No AI video; every frame is exactly what the scene says.
- `drawn`: everything drawn in code (motion/drawn.py, the kit in toon.js): each character a drawing with a rig and
  faces, each shot the whole frame drawn as a function of time, rendered by HyperFrames. No AI video, no 3D, no GPU
  needed; best for graphic, illustrated looks (silhouettes, cel shading, flat colour, paper).

1. The board: a director at a desk (Claude Code) reads the story, writes the treatment, decides the look, paints the
   style key frame, makes the cast (model sheets for the video engines, Blender rigs for `rig`) and writes the shot
   list (`board.json`). `bash check` holds it to the rules.
2. The music is composed for the board (on this machine's GPU, or by ElevenLabs Music) while the shots are made.
3. The shots: a desk per shot, side by side. For the video engines it writes what the shot is (`shot.json`: the
   start frame, the end frame when the shot must land on a pose, the motion, its sounds) and paints its frames; then
   the video model animates every shot, and a director checks each clip against the model sheets (same character,
   the right action, no morphing or looping) and has it made again when it isn't. For `rig` the desk writes the
   shot as a Blender script, renders it, looks at it and fixes it.
4. The clips are cut together at the film's size and rate, and the music, the narration and every shot's sounds
   are mixed under them.

A run picks up where it stopped (the Claude plan's limit, a machine let go): everything finished is kept.
For `drawn` the board draws the style key and the cast in code (each with its model sheet), and each shot desk
draws its shot, looks at stills, renders it and has it reviewed, like `rig`.
"""

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
from dataclasses import dataclass, field
from pathlib import Path

from . import drawn, scenes

CARTOON_CRAFT = Path(__file__).with_name("cartoon_craft.md")
ENGINES = ("cartoon", "anime", "rig", "drawn")
SIZES = ("extreme wide", "wide", "full", "medium", "close-up", "extreme close-up", "insert", "over the shoulder", "pov")
SHAPES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}
FPS = 30
MIN_SHOT, MAX_SHOT = 0.8, 7.0  # a video model makes up to about 7 s in one go
TRIES = int(os.environ.get("VIDO_CARTOON_TRIES", "3"))  # takes per shot for the video engines


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


def engine_for(asked: str = "") -> str:
    """The engine a cartoon is made with: the one asked for (or VIDO_CARTOON_ENGINE), else the video model when it
    can run here, else Blender rigs when Blender is set up, else drawn in code when the motion engine is here."""
    from . import available as motion_ready
    from . import blender, videogen

    want = (asked or os.environ.get("VIDO_CARTOON_ENGINE", "")).strip().lower()
    if want in ENGINES:
        return want
    if videogen.available():
        return "cartoon"
    if blender.find():
        return "rig"
    if motion_ready():
        return "drawn"
    raise CartoonError("nothing here can animate a cartoon: the video engines need an NVIDIA GPU with the `video` "
                       "extra (pip install -e \".[video]\"); the rig engine needs Blender (the Connect page says how); "
                       "the drawn engine needs the motion engine (npm ci in the motion folder)")


def quick() -> bool:
    """VIDO_CARTOON_QUICK=1: one take per shot and no director's checks (much less of the Claude plan per shot)."""
    return os.environ.get("VIDO_CARTOON_QUICK", "").strip() in ("1", "true", "yes")


def parallel_shots() -> int:
    """Shot desks at once: each is a Claude Code session (the plan's pace is the limit, not the machine)."""
    return max(1, min(8, int(os.environ.get("VIDO_CARTOON_PARALLEL", "3"))))


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
story into a cartoon of about {SECONDS} s, {W} x {H} px ({SHAPE}), and plan it so that each shot can be made on \
its own.

{CRAFT}

# How this film is animated
{ENGINE}

# Your desk
- The story is in story.md (and narration.json when the person recorded it: their words with times, which the film \
is cut to). Read it first.
- Write board.md: the logline, the want, the obstacle, the turn, the ending and what it pays off; then three \
different ways to tell it, and the one you pick and why (the obvious version is the one everyone makes).
- Decide the look, unless they named a style (then that is the look): a style a top studio would pick for this \
story, the palette (4 to 6 hex colours), the materials, the light.
{KEY}
{CAST}
- Write board.json (the Write tool), exactly this shape:
  {"title": "...", "logline": "...",
   "look": {"style": "...", "palette": ["#..."], "materials": "...", "light": "..."},
   "cast": [{"id": "<its name>", "who": "<what it is, its shape, colours and details, so it stays on model>"}],
   "music": {"bpm": 60-180, "styles": ["genre", "instruments and how they're played", "mood"], "avoid": [], \
"parts": {"intro": ["..."], "build": ["..."], "drop": ["..."], "breath": ["..."], "ending": ["..."]}},
   "shots": [{"n": 1, "start": 0.0, "end": 2.5, "size": "wide", "angle": "eye level", \
"what": "<what happens in this shot, beat by beat, with the acting and the camera move>", \
"sounds": ["<each sound in it, described so it can be made>"]}, ...]}
  Sizes: {SIZES}. Shots follow each other with no gap from 0 to the end of the film; each lasts {MIN} to {MAX} s. \
One clear action per shot (a video model and an animator both do one action well). {TIMING}
- Run `bash check` and fix everything it says, until it says the board is ready. Then reply DONE."""

ENGINE_NOTES = {
    "cartoon": "A video model animates every shot from its painted start frame: real motion of bodies, fur, cloth, "
               "light and camera, in the painted look. Write each shot as one continuous action a camera could film.",
    "anime": "An anime video model (AniSora) animates every shot from its painted start frame, with anime timing: "
             "held poses, snappy action, speed lines, anime camera moves. Pick an anime look (cel shading, clean "
             "line art, anime eyes and proportions) unless they asked for another.",
    "rig": "Everything is built and animated in Blender by code: each character a real rig (bones, a mesh that "
           "bends, a face that changes), each shot a set with the cast posed and keyed, lights and a camera. Pick a "
           "look Blender does beautifully: toon shading with ink outlines (Freestyle), or a stylised 3D look like a "
           "modern animated feature. Characters are simple, appealing shapes that a rig can bend well.",
}
ENGINE_NOTES["drawn"] = (
    "Everything is drawn in code, frame by frame: each character a drawing function with its own rig and faces, "
    "each shot the whole frame drawn as a function of time (toon.js is the kit; toon_example.js is the bar to beat: "
    "read both). No AI video and no 3D, so pick a look that drawing does beautifully and that stays consistent: "
    "silhouettes against luminous skies, cel shading in two tones, flat colour with paper grain, limited palettes, "
    "graphic shapes, light as glows and rays. Depth comes from layers moving at different speeds; life from loose "
    "parts (hair, cloth, tails, grass, particles) that the wind and the motion carry.")
KEY_PAINT = ("- Paint the style key frame first: `bash paint style_key \"<one establishing frame of the film: the place, "
             "the main character in it, the light, in the look>\"` (a whole frame of the film). Read it. Paint again "
             "until it is a frame you would put on the poster.")
KEY_DRAWN = ("- Draw the style key frame first: write style_key.js (a shot file: toon.shot({ draw(c, w, h, t) {...} }) "
             "that draws one establishing frame of the film: the place, the main character in it, the light, in the "
             "look), then `bash draw style_key style_key.js` (it becomes assets/style_key.png). Read it. Draw again "
             "until it is a frame you would put on the poster.")
CAST_DRAWN = ("- Draw each character: write cast/<id>.js (the Write tool), which calls toon.cast(\"<id>\", (c, o) => "
              "{...}) and draws the character at o.x, o.y (its feet), o.s px tall, facing o.dir, at o.t, doing "
              "o.action at o.phase with o.pose (toon.pose: legs and arms as two-bone angles), with the face o.face "
              "(neutral, happy, surprised, sad, angry, talk) or all in o.ink. Draw in units of its height, facing "
              "right (c.scale(o.s * o.dir, o.s)), with real shapes and two-tone shading, the way toon_example.js "
              "draws its heroine: never a stick figure for a character. Then `bash cast <id>`: its model sheet "
              "(every action both ways, every face) becomes cast/<id>-sheet.png. Read it and fix the drawing until "
              "every pose and face is appealing and on model.")
CAST_VIDEO = ("- Paint each character's model sheet: `bash paint <id> \"<the character, full body, neutral pose, "
              "three-quarter view, the look>\" --like style_key`. Read each; paint again if it is off model or off "
              "style. Every shot's frames are painted from these sheets, so they keep the cast on model.")
CAST_RIG = ("- Build each character as a Blender rig: write rig_<id>.py (the Write tool), a script that builds, on an "
            "empty scene, the character's mesh (good topology where it bends), an armature with sensible bones "
            "(spine, neck, head, limbs, ears and tail when it has them, a jaw or mouth controls), the mesh parented "
            "to the bones with automatic weights, shape keys for the face (blink, smile, surprise, mouth open), and "
            "its toon materials. The script gets bpy, scene, math and Vector. Then `bash rig <id> rig_<id>.py`: it "
            "is saved as cast/<id>.blend and filmed from the front, three quarters and the side: Read those "
            "stills, and fix the rig until it is appealing and on style.")
BOARD_TIMING_MUSIC = ("Every cut lands on a beat of the music: at the bpm you chose, a beat is 60/bpm s, and each "
                      "shot's start and end are whole beats (check snaps them to the nearest beat).")
BOARD_TIMING_VOICE = ("The film is cut to their narration: it lasts as long as the narration (plus a breath at the "
                      "end), and cuts fall in the pauses between their words, never inside a word. The music sits "
                      "under their voice.")


def board_desk(story: str, folder: Path, seconds: float, shape: str, style: str = "",
               narration: list[tuple[float, float, str]] | None = None, engine: str = "cartoon",
               timeout: float = 5400.0) -> dict:
    """The board, written and painted (or rigged) at the director's desk, checked. Returns board.json."""
    folder.mkdir(parents=True, exist_ok=True)
    w, h = SHAPES[shape]
    (folder / "story.md").write_text(story.strip() + (f"\n\nThe style they asked for: {style}" if style else ""),
                                     encoding="utf-8")
    if narration:
        (folder / "narration.json").write_text(json.dumps([{"start": a, "end": b, "word": t} for a, b, t in narration],
                                                          indent=0), encoding="utf-8")
        seconds = round(narration[-1][1] + 1.0, 2)
    (folder / "settings.json").write_text(json.dumps({"seconds": seconds, "shape": shape, "width": w, "height": h,
                                                      "narration": bool(narration), "engine": engine}),
                                          encoding="utf-8")
    if engine == "drawn":  # the kit and the bar to beat, on the desk to read
        shutil.copyfile(drawn.TOON, folder / "toon.js")
        shutil.copyfile(drawn.EXAMPLE, folder / "toon_example.js")
    system = (BOARD_SYSTEM.replace("{CRAFT}", CARTOON_CRAFT.read_text(encoding="utf-8"))
              .replace("{ENGINE}", ENGINE_NOTES[engine]).replace("{KEY}", KEY_DRAWN if engine == "drawn" else KEY_PAINT)
              .replace("{CAST}", {"rig": CAST_RIG, "drawn": CAST_DRAWN}.get(engine, CAST_VIDEO))
              .replace("{SECONDS}", f"{seconds:g}").replace("{W}", str(w)).replace("{H}", str(h))
              .replace("{SHAPE}", shape).replace("{SIZES}", ", ".join(SIZES)).replace("{MIN}", f"{MIN_SHOT:g}")
              .replace("{MAX}", f"{MAX_SHOT:g}")
              .replace("{TIMING}", BOARD_TIMING_VOICE if narration else BOARD_TIMING_MUSIC))
    commands = {"rig": ("paint", "check", "rig"), "drawn": ("draw", "cast", "check")}.get(engine, ("paint", "check"))
    _desk(folder, system, "Make the board for the story in story.md.", commands, 30, timeout)
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
    engine = settings.get("engine", "cartoon")
    problems: list[str] = []
    for key in ("title", "look", "cast", "music", "shots"):
        if not board.get(key):
            problems.append(f"board.json has no {key}")
    if problems:
        return board, problems
    for c in board["cast"]:
        cid = c.get("id", "")
        if engine == "rig":
            if not (folder / "cast" / f"{cid}.blend").exists():
                problems.append(f"the cast member {cid!r} has no rig: bash rig {cid} rig_{cid}.py")
        elif engine == "drawn":
            if not (folder / "cast" / f"{cid}.js").exists():
                problems.append(f"the cast member {cid!r} isn't drawn: write cast/{cid}.js")
            elif not (folder / "cast" / f"{cid}-sheet.png").exists():
                problems.append(f"the cast member {cid!r} has no model sheet: bash cast {cid}")
        elif not (folder / "assets" / f"{cid}.png").exists():
            problems.append(f"the cast member {cid!r} has no model sheet: bash paint {cid} \"...\" --like style_key")
    if not (folder / "assets" / "style_key.png").exists():
        problems.append("there is no style key frame: " + ("bash draw style_key style_key.js" if engine == "drawn"
                                                            else "bash paint style_key \"...\""))
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


def _shot_brief(board: dict, shot: Shot, shots: list[Shot], narration: list[tuple[float, float, str]],
                engine: str) -> str:
    look = board["look"]
    where = {"rig": "cast/<id>.blend, its turnaround stills cast/<id>-turn*.png",
             "drawn": "cast/<id>.js, its model sheet cast/<id>-sheet.png"}.get(engine, "assets/<id>.png")
    cast = "\n".join(f"- {c['id']}: {c.get('who', '')}" for c in board["cast"])
    before = next((s for s in shots if s.n == shot.n - 1), None)
    after = next((s for s in shots if s.n == shot.n + 1), None)
    words = [(round(a - shot.start, 2), round(b - shot.start, 2), t) for a, b, t in narration
             if shot.start <= a < shot.end]
    return (f"The film: {board['title']}. {board.get('logline', '')}\n"
            f"Its look: {look.get('style', '')}; palette {', '.join(look.get('palette') or [])}; materials: "
            f"{look.get('materials', '')}; light: {look.get('light', '')}\n\n"
            f"The cast ({where}):\n{cast}\n\n"
            f"THIS SHOT: shot {shot.n} of {len(shots)}, {shot.duration:.2f} s, {shot.size}, {shot.angle}.\n"
            f"What happens: {shot.what}\n"
            + (f"Its sounds: {'; '.join(shot.sounds)}\n" if shot.sounds else "")
            + ("The narration in it (seconds into the shot): " + " ".join(f"{a:.2f}-{b:.2f} {t}" for a, b, t in words)
               + "\n" if words else "")
            + (f"\nThe shot before (cut from it on action and screen direction): {before.size}, {before.angle}: "
               f"{before.what}\n" if before else "\nThis is the first shot: it opens the film.\n")
            + (f"The shot after: {after.size}, {after.angle}: {after.what}\n" if after else
               "This is the last shot: it ends the film.\n"))


# ---------------------------------------------------------------- 3a. the shots, for the video engines

FRAME_SYSTEM = """You are the director of photography and layout artist of a short animated film. A video model \
will animate each shot from the frame you make; you make this shot's frames and write what happens in it, so the \
model can film it well. The board (the story, the look, the cast) is set; this shot must cut with the ones around it.

{CRAFT}

# Your desk
- brief.md is this shot. The style key frame (assets/style_key.png) and the cast's model sheets (assets/<id>.png) \
are here: Read them all first.
- Paint the start frame: `bash frame start "<the whole frame at the shot's first moment: the shot size and angle, \
where each character is, their pose and expression, the set, the light, in the film's look>" [--with <cast id>]` \
(--with: the character it must match; the main one in the shot). It is painted as a whole frame of the film, \
{W} x {H}. Read it. Paint again until the characters are on model, the composition is strong and it is in style. \
Describe the characters by their model sheets every time (their shape, colours and details).
- When the shot must land on a pose (it hands over to the next shot on an action, or ends on a held pose), paint \
the end frame too: `bash frame end "<the frame at the shot's last moment>" [--with <cast id>]`.
- Write shot.json (the Write tool): {"motion": "<what happens, as one continuous action a camera films: who moves, \
how (with weight, anticipation, follow-through), the expressions, and the camera move; present tense, concrete, no \
cuts>", "end": true or false (whether you painted an end frame), "sounds": [{"t": 0.4, "sound": "<what it is, \
precisely>", "gain": -14}, ...]} (a sound for every action, seconds into the shot; gain -8 loud to -22 subtle).
- Then reply DONE."""

CLIP_REVIEW = """You are a strict animation director reviewing one shot of a short animated film, made by a video \
model from a painted start frame. You see the film's style key frame and the cast's model sheets, then frames of the \
shot in order with their times, and what the shot was meant to show. Judge it:
1. On model: every character stays the same character as its model sheet from the first frame to the last (shape, \
ears, colours, details). Morphing into something else is the worst failure.
2. The action: does it do what the shot asks, clearly, once (no looping back to repeat it), with weight and appeal?
3. Physical sense: no extra limbs, melting parts, objects passing through each other, flicker or sudden jumps.
4. In style: it stays in the film's look; the camera does what the shot asks.
Reply with JSON only: {"score": 0-10 (10 = a festival short), "ok": true when ready (score >= 7 and nothing \
serious), "problems": ["at 1.4 s: the dog's ears turn pointed: keep them floppy", ...]}"""


def frame_desk(board: dict, board_dir: Path, shot: Shot, shots: list[Shot], folder: Path, width: int, height: int,
               narration: list[tuple[float, float, str]], engine: str, timeout: float = 3600.0) -> dict:
    """The shot's frames painted and its motion written at its own desk. Returns shot.json."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "assets").mkdir(exist_ok=True)
    for c in [{"id": "style_key"}, *board["cast"]]:  # the film's style and its cast, on every desk
        src = board_dir / "assets" / f"{c['id']}.png"
        if src.exists():
            shutil.copyfile(src, folder / "assets" / src.name)
    note = ""
    if (folder / "start.png").exists():  # stopped partway: carry on from it
        note = ("\n\nYou were stopped partway through this shot: start.png (and end.png, shot.json if they are "
                "there) hold your work so far. Read them and carry on; do not start over.")
    (folder / "brief.md").write_text(_shot_brief(board, shot, shots, narration, engine) + note, encoding="utf-8")
    (folder / "brief.json").write_text(json.dumps({"n": shot.n, "seconds": shot.duration, "width": width,
                                                   "height": height, "style": board["look"].get("style", "")}),
                                       encoding="utf-8")
    system = (FRAME_SYSTEM.replace("{CRAFT}", CARTOON_CRAFT.read_text(encoding="utf-8"))
              .replace("{W}", str(width)).replace("{H}", str(height)))
    _desk(folder, system, "Make this shot's frames and write shot.json, as brief.md says. Start by reading brief.md "
                          "and every image in assets/.", ("frame",), 20, timeout)
    if not (folder / "start.png").exists():
        raise CartoonError(f"shot {shot.n}: no start frame was painted")
    try:
        spec = json.loads((folder / "shot.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CartoonError(f"shot {shot.n}: shot.json can't be read: {exc}") from exc
    if len(str(spec.get("motion", ""))) < 20:
        raise CartoonError(f"shot {shot.n}: shot.json says no motion")
    return spec


def _strip(clip: Path, seconds: float, n: int = 8, side: int = 320) -> tuple[bytes, list[float]]:
    """`n` frames of a clip side by side (a JPEG) and their times: for a director's eyes."""
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(clip))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    fps = cap.get(cv2.CAP_PROP_FPS) or 16
    tiles, times = [], []
    for k in range(n):
        idx = min(total - 1, int(round(k * (total - 1) / max(1, n - 1))))
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        scale = side / frame.shape[0]
        tile = cv2.resize(frame, (int(frame.shape[1] * scale), side))
        t = round(idx / fps, 2)
        cv2.putText(tile, f"{t:.2f}s", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        tiles.append(tile)
        times.append(t)
    cap.release()
    if not tiles:
        return b"", []
    ok, jpg = cv2.imencode(".jpg", np.hstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 85])
    return (jpg.tobytes() if ok else b""), times


def review_clip(folder: Path, clip: Path, ask: str, refs: list[Path], seconds: float) -> dict:
    """A clip judged by the animation director, beside the style key and the cast's model sheets (or rig stills).
    {score, ok, problems}."""
    jpg, _ = _strip(clip, seconds)
    content: list[dict] = [{"type": "text", "text": f"The shot: {ask}\nThe style key frame, then the cast:"}]
    for img in refs:
        if img.exists():
            content.append({"type": "text", "text": img.stem})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.b64encode(_jpeg(img)).decode("ascii")}})
    content.append({"type": "text", "text": "The shot, frames in order (times on them):"})
    content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                "data": base64.b64encode(jpg).decode("ascii")}})
    reply = scenes._ask(scenes._client(), CLIP_REVIEW, content, 8000, None, "cartoon-review", "high")
    m = re.search(r"\{.*\}", reply, re.S)
    try:
        verdict = json.loads(m.group(0)) if m else {}
    except ValueError:
        verdict = {}
    return {"score": verdict.get("score", 0), "ok": bool(verdict.get("ok")), "problems": verdict.get("problems") or []}


def animate_shot(folder: Path, board: dict, shot: Shot, spec: dict, engine: str, log=print) -> dict:
    """The shot brought to life by the video model from its start frame (and end frame), checked by the director
    and made again with a new take when it drifts, loops or misses the action. Returns {clip, score}."""
    from . import videogen

    look = "anime" if engine == "anime" else "cartoon"
    style = board["look"].get("style", "")
    cast = "; ".join(f"{c['id']}: {c.get('who', '')}" for c in board["cast"] if c["id"].lower() in
                     (shot.what + spec["motion"]).lower())
    prompt = f"{spec['motion']} {style}." + (f" The characters stay exactly as they are: {cast}." if cast else "")
    refs = [folder / "assets" / "style_key.png", *[folder / "assets" / f"{c['id']}.png" for c in board["cast"]]]
    end = folder / "end.png" if spec.get("end") and (folder / "end.png").exists() else None
    tries = 1 if quick() else max(1, TRIES)
    best, notes = None, []
    for k in range(1, tries + 1):
        clip = folder / f"take{k}.mp4"
        if not clip.exists():
            asked = prompt + (" Avoid: " + "; ".join(notes) if notes else "")
            videogen.animate(folder / "start.png", asked, clip, seconds=shot.duration + 0.25, look=look,
                             last_image=end, seed=shot.n * 101 + k)
        if quick():
            return {"clip": clip, "score": None}
        seen = review_clip(folder, clip, f"Shot {shot.n}: {shot.what}", refs, shot.duration)
        log(f"Shot {shot.n}: take {k} scored {seen['score']}")
        if best is None or float(seen["score"] or 0) > float(best["score"] or 0):
            best = {"clip": clip, "score": seen["score"]}
        if seen["ok"]:
            break
        notes = [p.split(":", 1)[-1].strip() for p in seen["problems"]][:4]
    return best


# ---------------------------------------------------------------- 3b. the shots, for the rig engine

RIG_SHOT_SYSTEM = """You are a top character animator working in Blender: you animate one shot of a short animated \
film by writing it as a Blender Python script. The cast are real rigs (bones, a bending mesh, face shape keys); \
you build the set, bring the cast in, pose and key them, light the shot and film it. The board (the story, the \
look, the cast) is set; this shot must cut with the ones around it.

{CRAFT}

# The script (shot.py)
- It runs on an empty scene and gets: bpy, scene, math, Vector, W, H, FPS, FRAMES (the shot's frames, from 1), \
SECONDS, `cast("<id>")` (appends that character's rig, returns its collection) and `armature(collection)` (its \
armature object). Pose bones in pose mode data (arm.pose.bones["..."]) and key them with keyframe_insert; key \
shape keys for the face; move and key the camera; set scene.camera.
- Build the set from real geometry (floors, walls, furniture, props) with materials in the film's look. Light it \
with the film's light. Toon look: Freestyle lines on, flat or ramp shading; stylised 3D: soft key, fill, rim.
- Animate like a professional: strong key poses, anticipation, overshoot and settle, follow-through on ears, tails \
and loose parts, arcs, eases, holds that breathe (a blink, a weight shift). No straight sliding.

# Your desk
- brief.md is this shot. The style key frame (assets/style_key.png) and the cast's turnarounds \
(cast/<id>-turn*.png) are here: Read them first. The rigs are cast/<id>.blend.
- Write shot.py (the Write tool), then `bash render`: the whole shot is rendered to clip.mp4 and you get a strip \
of its frames (Read it). Look at every key moment; fix and render again.
{REVIEW}- Write sounds.json: [{"t": 0.4, "sound": "<what it is, precisely>", "gain": -14}, ...] (a sound for every \
action, seconds into the shot).
- When it is right, reply DONE."""

RIG_REVIEW_LINE = ("- When it looks right, `bash review`: a strict animation director judges it and lists every "
                   "problem with its time. Fix them, render again, and ask again (up to three times).\n")


def rig_desk(board: dict, board_dir: Path, shot: Shot, shots: list[Shot], folder: Path, width: int, height: int,
             narration: list[tuple[float, float, str]], timeout: float = 5400.0) -> Path:
    """The shot built, animated and rendered in Blender at its own desk. Returns its clip."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "assets").mkdir(exist_ok=True)
    shutil.copyfile(board_dir / "assets" / "style_key.png", folder / "assets" / "style_key.png")
    if (board_dir / "cast").exists():
        shutil.copytree(board_dir / "cast", folder / "cast", dirs_exist_ok=True)
    note = ("\n\nYou were stopped partway: shot.py holds your work so far. Read it and carry on; do not start over."
            if (folder / "shot.py").exists() else "")
    (folder / "brief.md").write_text(_shot_brief(board, shot, shots, narration, "rig") + note, encoding="utf-8")
    (folder / "brief.json").write_text(json.dumps({"n": shot.n, "seconds": shot.duration, "width": width,
                                                   "height": height, "fps": FPS, "ask": f"Shot {shot.n}: {shot.what}",
                                                   "cast": [c["id"] for c in board["cast"]]}), encoding="utf-8")
    system = (RIG_SHOT_SYSTEM.replace("{CRAFT}", CARTOON_CRAFT.read_text(encoding="utf-8"))
              .replace("{REVIEW}", "" if quick() else RIG_REVIEW_LINE))
    commands = ("render",) if quick() else ("render", "review")
    _desk(folder, system, "Animate the shot described in brief.md. Start by reading brief.md and the images.",
          commands, 40, timeout)
    if not (folder / "clip.mp4").exists():
        raise CartoonError(f"shot {shot.n}: the animator left no rendered shot")
    return folder / "clip.mp4"


# ---------------------------------------------------------------- 3c. the shots, for the drawn engine

DRAWN_SHOT_SYSTEM = """You are a top 2D animator and layout artist: you animate one shot of a short animated film by \
drawing it in code. The shot is the whole frame drawn as a function of time on a canvas (shot.js); the cast are \
drawing functions (cast/<id>.js) with rigs and faces; toon.js is your kit. The board (the story, the look, the cast) \
is set; this shot must cut with the ones around it.

{CRAFT}

# The shot file (shot.js)
- It calls toon.shot({ blur: 2, draw(c, w, h, t) { ... } }): draw the WHOLE frame at time t (seconds into the shot), \
w x h px, from scratch every time. Everything is a function of t: no Date, no Math.random (toon.rng(seed) and \
toon.hash(n) are seeded), no timers, no images, no network. blur (0 to 8) is motion blur: sub-frames blended per \
frame; 2 is a good default, 0 for crisp anime holds.
- The kit (read toon.js; toon_example.js shows a lead character and a whole shot at the level to beat):
  time: toon.key(t, [[0, v0], [0.4, v1, "back"], ...]) for key poses with eases (numbers, arrays or whole poses), \
toon.ease.*, toon.between(t, a, b), toon.twos(t) (characters on twos: 12 drawings a second, the camera smooth), \
toon.shake(t, amount); the camera: toon.camera(c, w, h, {x, y, zoom, rot, shake}, () => { ...the world... }); \
brushes: sky, glow, disc, stars, clouds, puff, birds, ridge, skyline, streaks, speedLines, particles (dust, petal, \
rain, snow), grass, ribbon (hair, scarves, tails, smoke), rays, vignette, grain, letterbox; the cast: \
toon.draw("<id>", c, {x, y, s, dir, t, action, phase, pose, face, ink}); poses: toon.pose.stand/walk/run/jump/sit/\
crouch/point/wave and toon.pose.blend(a, b, u); toon.person for background people.
- Draw like a film: layers at different depths moving at different speeds; a light that shapes everything (glows in \
"lighter", shade in "multiply"); strong silhouettes; a camera that moves with intent (push in on a feeling, shake on \
an impact, follow the action); characters in key poses with anticipation, overshoot and settle, arcs and eases, \
holds that still breathe (a blink, hair in the wind); every loose part trailing the motion. No text in the frame \
unless the story has text in its world.

# Your desk
- brief.md is this shot. The style key frame (assets/style_key.png) and the cast's model sheets \
(cast/<id>-sheet.png) are here: Read them, toon.js and toon_example.js first.
- Write shot.js (the Write tool). `bash still 0.5 1.2 2.0` draws frames at those times in seconds (fast: Read them \
while you work). A red frame is an error in your code, said on it. When the frames are right, `bash render`: the \
whole shot is rendered to clip.mp4 and you get a strip of its frames (Read it). Look at every key moment; fix and \
render again.
{REVIEW}- Write sounds.json: [{"t": 0.4, "sound": "<what it is, precisely>", "gain": -14}, ...] (a sound for every \
action, seconds into the shot).
- When it is right, reply DONE."""


def drawn_desk(board: dict, board_dir: Path, shot: Shot, shots: list[Shot], folder: Path, width: int, height: int,
               narration: list[tuple[float, float, str]], timeout: float = 5400.0) -> Path:
    """The shot drawn in code, rendered and checked at its own desk. Returns its clip."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "assets").mkdir(exist_ok=True)
    shutil.copyfile(board_dir / "assets" / "style_key.png", folder / "assets" / "style_key.png")
    (folder / "cast").mkdir(exist_ok=True)
    for f in [*(board_dir / "cast").glob("*.js"), *(board_dir / "cast").glob("*-sheet.png")]:
        shutil.copyfile(f, folder / "cast" / f.name)
    shutil.copyfile(drawn.TOON, folder / "toon.js")
    shutil.copyfile(drawn.EXAMPLE, folder / "toon_example.js")
    note = ("\n\nYou were stopped partway: shot.js holds your work so far. Read it and carry on; do not start over."
            if (folder / "shot.js").exists() else "")
    (folder / "brief.md").write_text(_shot_brief(board, shot, shots, narration, "drawn") + note, encoding="utf-8")
    (folder / "brief.json").write_text(json.dumps({"n": shot.n, "seconds": shot.duration, "width": width,
                                                   "height": height, "fps": FPS, "ask": f"Shot {shot.n}: {shot.what}",
                                                   "engine": "drawn", "cast": [c["id"] for c in board["cast"]]}),
                                       encoding="utf-8")
    system = (DRAWN_SHOT_SYSTEM.replace("{CRAFT}", CARTOON_CRAFT.read_text(encoding="utf-8"))
              .replace("{REVIEW}", "" if quick() else RIG_REVIEW_LINE))
    commands = ("still", "render") if quick() else ("still", "render", "review")
    _desk(folder, system, "Animate the shot described in brief.md. Start by reading brief.md, toon.js, "
                          "toon_example.js and the images.", commands, 40, timeout)
    if not (folder / "clip.mp4").exists():
        raise CartoonError(f"shot {shot.n}: the animator left no rendered shot")
    return folder / "clip.mp4"


# ---------------------------------------------------------------- the desk's commands

def _jpeg(png: Path, side: int = 640) -> bytes:
    """An image as a JPEG on a mid grey (any transparency shown flat), for a reviewer's eyes."""
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


def _aspect(folder: Path) -> float:
    for settings in (folder / "settings.json", folder / "brief.json", folder.parent.parent / "board" / "settings.json"):
        if settings.exists():
            st = json.loads(settings.read_text(encoding="utf-8"))
            if st.get("width") and st.get("height"):
                return float(st["width"]) / float(st["height"])
    return 9 / 16


def paint_command(folder: Path, args: list[str]) -> None:
    """`bash paint <name> "<what>" [...] [--like <name>] [--whole]` (the board): painted side by side in the film's
    style. The style key and any background are whole frames; a model sheet is the character on its own."""
    whole_all = "--whole" in args
    args = [a for a in args if a != "--whole"]
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
    aspect = _aspect(folder)

    def one(job):
        name, what = job
        ref = like_path if like_path.exists() and name != like_path.stem else None
        whole = whole_all or scenes.is_whole(name, what)
        try:
            w, h = scenes.paint(what, folder / "assets" / f"{name}.png", None, ref, whole=whole, aspect=aspect)
        except Exception as exc:  # noqa: BLE001 - said to the desk, which tries again
            return f"{name}: PAINTING FAILED: {exc}"
        return (f"assets/{name}.png  {w}x{h} px (" + ("a whole frame" if whole else "the character on its own")
                + (f", in the style of {like_path.stem}" if ref else "") + "). Read it to see it.")

    with ThreadPoolExecutor(max_workers=4) as pool:
        for line in pool.map(one, jobs[:8]):
            print(line)


def frame_command(folder: Path, args: list[str]) -> None:
    """`bash frame start|end "<the whole frame>" [--with <cast id>]`: the shot's start (or end) frame, a whole frame
    of the film painted in its style, the character given with --with kept on its model sheet."""
    if len(args) < 2 or args[0] not in ("start", "end"):
        print('Say which frame and what is in it: bash frame start "<the whole frame>" [--with <cast id>]')
        return
    which, what = args[0], args[1]
    ref = folder / "assets" / "style_key.png"
    if "--with" in args:
        k = args.index("--with")
        cid = re.sub(r"[^a-z0-9_-]", "", (args[k + 1] if k + 1 < len(args) else "").lower())
        if (folder / "assets" / f"{cid}.png").exists():
            ref = folder / "assets" / f"{cid}.png"
    try:
        w, h = scenes.paint(what, folder / f"{which}.png", None, ref if ref.exists() else None, whole=True,
                            aspect=_aspect(folder))
    except Exception as exc:  # noqa: BLE001 - said to the desk, which tries again
        print(f"PAINTING FAILED: {exc}")
        return
    print(f"{which}.png  {w}x{h} px, a whole frame in the style of {ref.stem}. Read it to see it.")


def check_command(folder: Path) -> None:
    board, problems = check_board(folder)
    if board is None or problems:
        print("The board isn't ready yet:\n- " + "\n- ".join(problems or ["write board.json"]))
        return
    cuts = ", ".join(f"{s['start']:.2f}" for s in board["shots"][1:])
    print(f"The board is ready: {len(board['shots'])} shots, {board['seconds']:.2f} s, cuts at {cuts} s.")


def rig_command(folder: Path, args: list[str]) -> None:
    """`bash rig <id> <script.py>`: the character built by its script, saved as cast/<id>.blend, filmed from 3 sides."""
    from . import blender

    if len(args) < 2:
        print("Say which character and its script: bash rig <id> rig_<id>.py")
        return
    name = re.sub(r"[^a-z0-9_-]", "", args[0].lower())[:40]
    script = (folder / args[1]).resolve()
    if folder.resolve() not in script.parents or not script.exists():
        print(f"There's no script {args[1]} on your desk.")
        return
    try:
        stills = blender.rig(script, name, folder / "cast", assets=folder / "assets")
    except (blender.BlenderError, subprocess.TimeoutExpired) as exc:
        print(f"THE RIG FAILED: {exc}")
        return
    print(f"cast/{name}.blend saved. Read its turnaround: " + ", ".join(str(s.relative_to(folder)) for s in stills))


def _film_size(folder: Path) -> tuple[int, int]:
    settings = json.loads((folder / "settings.json").read_text(encoding="utf-8"))
    return int(settings["width"]), int(settings["height"])


def draw_command(folder: Path, args: list[str]) -> None:
    """`bash draw <name> <file.js> [--at <seconds>]` (the board): a still of a shot file, drawn with the film's cast,
    saved as assets/<name>.png."""
    if len(args) < 2:
        print("Say what to call it and its file: bash draw style_key style_key.js [--at 0.5]")
        return
    name = re.sub(r"[^a-z0-9_-]", "", args[0].lower())[:40] or "still"
    script = (folder / args[1]).resolve()
    if folder.resolve() not in script.parents or not script.exists():
        print(f"There's no file {args[1]} on your desk.")
        return
    at = float(args[args.index("--at") + 1]) if "--at" in args and args.index("--at") + 1 < len(args) else 0.5
    w, h = _film_size(folder)
    try:
        got = drawn.stills(folder, script, [at], folder / ".stills", width=w, height=h, seconds=at + 1)
    except (drawn.DrawnError, subprocess.TimeoutExpired) as exc:
        print(f"IT DIDN'T DRAW: {exc}")
        return
    (folder / "assets").mkdir(exist_ok=True)
    shutil.copyfile(got[0], folder / "assets" / f"{name}.png")
    print(f"assets/{name}.png  {w}x{h} px, drawn at {at:g} s. Read it to see it (a red frame is an error in the code).")


def cast_command(folder: Path, args: list[str]) -> None:
    """`bash cast <id>` (the board): the character's model sheet (every action both ways, every face), drawn from
    cast/<id>.js and saved as cast/<id>-sheet.png."""
    if not args:
        print("Say which character: bash cast <id>")
        return
    cid = re.sub(r"[^a-z0-9_-]", "", args[0].lower())[:40]
    if not (folder / "cast" / f"{cid}.js").exists():
        print(f"Write cast/{cid}.js first: it calls toon.cast(\"{cid}\", (c, o) => {{ ... }}).")
        return
    try:
        got = drawn.stills(folder, None, [0.5], folder / ".stills", width=1920, height=1080, seconds=1, sheet=cid)
    except (drawn.DrawnError, subprocess.TimeoutExpired) as exc:
        print(f"IT DIDN'T DRAW: {exc}")
        return
    shutil.copyfile(got[0], folder / "cast" / f"{cid}-sheet.png")
    print(f"cast/{cid}-sheet.png: its actions (top row: stand, stand facing left, walk, run, jump, sit) and its faces "
          "(bottom row). Read it.")


def still_command(folder: Path, args: list[str]) -> None:
    """`bash still <t> [<t> ...]` (a drawn shot): frames of shot.js at those times, side by side, to Read."""
    import cv2
    import numpy as np

    script = folder / "shot.js"
    if not script.exists():
        print("Write shot.js first.")
        return
    info = json.loads((folder / "brief.json").read_text(encoding="utf-8"))
    try:
        times = [max(0.0, min(float(info["seconds"]), float(a))) for a in args[:6]] or [0.0, float(info["seconds"]) / 2]
    except ValueError:
        print("Give times in seconds: bash still 0.5 1.2 2.0")
        return
    try:
        got = drawn.stills(folder, script, times, folder / ".stills", width=int(info["width"]),
                           height=int(info["height"]), seconds=float(info["seconds"]), fps=int(info.get("fps", FPS)))
    except (drawn.DrawnError, subprocess.TimeoutExpired) as exc:
        print(f"IT DIDN'T DRAW: {exc}")
        return
    tiles = []
    for t, png in zip(times, got):
        img = cv2.imread(str(png))
        scale = 540 / img.shape[0]
        img = cv2.resize(img, (int(img.shape[1] * scale), 540), interpolation=cv2.INTER_AREA)
        cv2.putText(img, f"{t:.2f}s", (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        tiles.append(img)
    cv2.imwrite(str(folder / "stills.jpg"), np.hstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 88])
    print("stills.jpg: frames at " + ", ".join(f"{t:.2f}" for t in times) + " s, left to right. Read it.")


def render_command(folder: Path) -> None:
    """`bash render`: the shot (shot.py in Blender, or shot.js drawn) rendered whole to clip.mp4, and a strip of its
    frames to Read."""
    from . import blender

    info = json.loads((folder / "brief.json").read_text(encoding="utf-8"))
    is_drawn = info.get("engine") == "drawn"
    script = folder / ("shot.js" if is_drawn else "shot.py")
    if not script.exists():
        print(f"Write {script.name} first.")
        return
    t0 = time.monotonic()
    try:
        if is_drawn:
            clip = drawn.render(folder, script, folder / "clip.mp4", width=int(info["width"]),
                                height=int(info["height"]), fps=FPS, seconds=float(info["seconds"]))
        else:
            clip = blender.render(script, folder / "clip.mp4", width=int(info["width"]), height=int(info["height"]),
                                  fps=FPS, seconds=float(info["seconds"]), assets=folder / "assets",
                                  cast=folder / "cast")
    except (blender.BlenderError, drawn.DrawnError, subprocess.TimeoutExpired) as exc:
        label = "IT DIDN'T RENDER" if is_drawn else "BLENDER FAILED"
        print(f"{label}: {exc}")
        return
    jpg, times = _strip(clip, float(info["seconds"]))
    (folder / "strip.jpg").write_bytes(jpg)
    print(f"clip.mp4 rendered in {time.monotonic() - t0:.0f} s. Read strip.jpg: its frames at "
          + ", ".join(f"{t:.2f}" for t in times) + " s.")


def review_command(folder: Path) -> None:
    clip = folder / "clip.mp4"
    if not clip.exists():
        print("Render the shot first (bash render).")
        return
    info = json.loads((folder / "brief.json").read_text(encoding="utf-8"))
    refs = [folder / "assets" / "style_key.png", *sorted((folder / "cast").glob("*-turn1.png")),
            *sorted((folder / "cast").glob("*-sheet.png"))]
    seen = review_clip(folder, clip, info.get("ask", ""), refs, float(info["seconds"]))
    print(f"The director: {seen['score']}/10" + (", ready." if seen["ok"] else ", not ready yet.")
          + "".join(f"\n- {p}" for p in seen["problems"]))


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


def music_is_music(path: Path) -> bool:
    """Whether a made track is music and not noise: real music is tonal (its spectrum has peaks); a track that came
    out as a hiss or a buzz is flat across the spectrum. Measured, because nobody may be listening."""
    import numpy as np

    from ..ffmpeg import ffmpeg_bin

    raw = subprocess.run([ffmpeg_bin(), "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", "22050", "-f",
                          "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32)
    if x.size < 22050:
        return False
    flat = []
    for i in range(0, x.size - 2048, 11025):
        f = np.abs(np.fft.rfft(x[i:i + 2048] * np.hanning(2048))) + 1e-9
        flat.append(float(np.exp(np.mean(np.log(f))) / np.mean(f)))
    return float(np.median(flat)) < 0.25


# ---------------------------------------------------------------- 4. the film

def fit_clip(clip: Path, seconds: float, width: int, height: int, out: Path, interpolate: bool = True) -> Path:
    """A shot's clip at the film's size and rate and exactly its length: filled to the frame, its motion
    interpolated to 30 fps when it was made slower (a video model makes 16; rendered shots are made at 30 and
    keep their own frames), held on its last frame if it is short."""
    from ..ffmpeg import ffmpeg_bin

    rate = f"minterpolate=fps={FPS}:mi_mode=mci:mc_mode=aobmc:vsbmc=1" if interpolate else f"fps={FPS}"
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
          f"{rate},tpad=stop_mode=clone:stop_duration=2,"
          f"trim=duration={seconds:.3f},setpts=PTS-STARTPTS")
    done = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(clip), "-an", "-vf", vf, "-c:v",
                           "libx264", "-preset", "slow", "-crf", "14", "-pix_fmt", "yuv420p", "-r", str(FPS),
                           str(out)], capture_output=True)
    if done.returncode != 0:
        raise CartoonError("a shot couldn't be fitted to the film: " + done.stderr.decode(errors="replace")[-400:])
    return out


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
    shot's sounds, made to their cues. The music sits under the sounds; nothing clips. Returns the film and the
    sounds laid ({file, at, seconds, gain})."""
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
        chain = f"[{n}:a]aresample=48000,atrim=0:{seconds:.3f},afade=t=out:st={fade:.3f}:d=2,volume=-8dB"
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
        gain = max(-30.0, min(-6.0, float(c.get("gain", -14))))
        graph.append(f"[{n}:a]aresample=48000,adelay={ms}|{ms},volume={gain}dB[s{k}]")
        mixes.append(f"[s{k}]")
        laid.append({"file": str(path), "at": round(float(c["t"]), 3), "seconds": float(c.get("seconds", 1.2)),
                     "gain": gain})
        n += 1
    if not mixes:
        shutil.copyfile(picture, out)
        return out, laid
    graph.append(f"{''.join(mixes)}amix=inputs={len(mixes)}:normalize=0:duration=longest,"
                 f"atrim=0:{seconds:.3f},loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.84[a]")
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


def _sounds(folder: Path) -> list[dict]:
    for name in ("shot.json", ".frames.json", "sounds.json"):
        path = folder / name
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            items = data.get("sounds") if isinstance(data, dict) else data
            return [s for s in items or [] if isinstance(s, dict) and s.get("sound")]
    return []


def make(story: str, work: Path, seconds: float = 40.0, shape: str = "9:16", style: str = "",
         narration: Path | None = None, engine: str = "", library: Path | None = None, log=print) -> dict:
    """The whole cartoon: board, music, every shot made and checked, cut and mixed. Returns
    {video, board, engine, shots: [{n, start, end, picture, score}], music, sounds, narration}.

    It picks up where it stopped: run again on the same `work` folder and everything finished is kept."""
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
    ready = work / "board" / ".ready"
    if ready.exists() and (work / "board" / "board.json").exists():
        board = json.loads((work / "board" / "board.json").read_text(encoding="utf-8"))
        engine = json.loads((work / "board" / "settings.json").read_text(encoding="utf-8")).get("engine") or engine
        log("The board from before is kept")
    else:
        engine = engine_for(engine)
        log(f"Writing the board ({engine}): the story, the look, the cast, the shots")
        board = board_desk(story, work / "board", seconds, shape, style, words or None, engine)
        ready.parent.mkdir(parents=True, exist_ok=True)
        ready.write_text("checked", encoding="utf-8")
    shots = board_shots(board)
    total = float(board["seconds"])
    log(f"Board ready: '{board['title']}', {len(shots)} shots, {total:.1f} s, made with {engine} "
        f"({time.monotonic() - t0:.0f} s)")

    music = work / "music.mp3"
    if not (music.exists() and music.stat().st_size > 1000):
        made_music = music_for(board, total, music, under_voice=bool(words))
        if made_music is not None and not music_is_music(made_music):  # a hiss or a buzz, not music: once more
            log("The track came out as noise, not music: making it again")
            made_music = music_for(board, total, music, under_voice=bool(words))
            if made_music is not None and not music_is_music(made_music):
                log("The track is noise again: the film is made without music (say so)")
                music.unlink(missing_ok=True)
                made_music = None
        music = made_music
    if music is None:
        log("No music: nothing here can make it, or it couldn't be made as music")

    def make_frames(shot: Shot) -> None:
        folder = work / "shots" / f"s{shot.n:02d}"
        if (folder / ".frames.json").exists():
            return
        spec = frame_desk(board, work / "board", shot, shots, folder, width, height, words, engine)
        (folder / ".frames.json").write_text(json.dumps(spec), encoding="utf-8")
        log(f"Shot {shot.n}: frames painted")

    def make_rig_shot(shot: Shot) -> None:
        folder = work / "shots" / f"s{shot.n:02d}"
        if (folder / ".done.json").exists():
            return
        (drawn_desk if engine == "drawn" else rig_desk)(board, work / "board", shot, shots, folder, width, height,
                                                         words)
        (folder / ".done.json").write_text(json.dumps({"clip": "clip.mp4"}), encoding="utf-8")
        log(f"Shot {shot.n} finished")

    left = sum(1 for s in shots if not (work / "shots" / f"s{s.n:02d}" / ".done.json").exists())
    log(f"Making {left} of {len(shots)} shots, {parallel_shots()} desks at a time")
    with ThreadPoolExecutor(max_workers=parallel_shots()) as desks:
        list(desks.map(make_rig_shot if engine in ("rig", "drawn") else make_frames, shots))
    if engine not in ("rig", "drawn"):  # every frame painted first: then the video model holds the GPU for all the shots
        from . import videogen

        for shot in shots:
            folder = work / "shots" / f"s{shot.n:02d}"
            if (folder / ".done.json").exists():
                continue
            spec = json.loads((folder / ".frames.json").read_text(encoding="utf-8"))
            started = time.monotonic()
            best = animate_shot(folder, board, shot, spec, engine, log)
            (folder / ".done.json").write_text(json.dumps({"clip": best["clip"].name, "score": best["score"]}),
                                               encoding="utf-8")
            log(f"Shot {shot.n} animated ({time.monotonic() - started:.0f} s)")
        videogen.release()
    log("Cutting the film")
    pictures, cues, made_shots = [], [], []
    for shot in shots:
        folder = work / "shots" / f"s{shot.n:02d}"
        done = json.loads((folder / ".done.json").read_text(encoding="utf-8"))
        picture = folder / "picture.mp4"
        if not picture.exists():
            fit_clip(folder / done["clip"], shot.duration, width, height, picture,
                     interpolate=engine not in ("rig", "drawn"))
        pictures.append(picture)
        for c in _sounds(folder):
            cues.append({**c, "t": round(shot.start + float(c.get("t", 0)), 3)})
        made_shots.append({"n": shot.n, "start": shot.start, "end": shot.end, "picture": picture,
                           "score": done.get("score")})
    picture = cut_together(pictures, work / "picture.mp4")
    library = library or (Path(os.environ.get("LOCALAPPDATA", str(work))) / "VidoSuper" / "sfx")
    out, laid = mix(picture, total, music, narration, cues, library, work / "cartoon.mp4")
    log(f"Done in {time.monotonic() - t0:.0f} s: {out}")
    return {"video": out, "board": board, "engine": engine, "music": music, "sounds": laid, "narration": narration,
            "shots": made_shots}


if __name__ == "__main__":  # the desks' commands: python -m vidosuper.motion.cartoon <command> <folder> ...
    if len(sys.argv) >= 3:
        cmd, desk_dir, rest = sys.argv[1], Path(sys.argv[2]), sys.argv[3:]
        if cmd == "paint":
            paint_command(desk_dir, rest)
        elif cmd == "frame":
            frame_command(desk_dir, rest)
        elif cmd == "check":
            check_command(desk_dir)
        elif cmd == "rig":
            rig_command(desk_dir, rest)
        elif cmd == "render":
            render_command(desk_dir)
        elif cmd == "draw":
            draw_command(desk_dir, rest)
        elif cmd == "cast":
            cast_command(desk_dir, rest)
        elif cmd == "still":
            still_command(desk_dir, rest)
        elif cmd == "review":
            review_command(desk_dir)
