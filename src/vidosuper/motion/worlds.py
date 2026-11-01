"""Worlds: Flicello's motion design. The WHOLE frame is designed, the way a motion designer makes a piece: a world
fills the frame, the person (cut out of their own video) stands in it, a camera moves through it, and the things they
describe happen in it on their words. Nothing is pasted on top of their untouched footage (that reads as stickers).

Two engines, one way of working:
- 2d (HyperFrames): flat, illustrated motion design (flat shapes with two-tone shading, stepped flat glow, springy
  motion, parallax, grain), written as one HTML/SVG/GSAP page per section with their cut-out in it.
- 3d (Blender): real light, materials and depth, written as a Blender Python scene per section with the kit
  (blender_kit.py), their cut-out on a card in the world.

1. `sections` splits the film at the quiet places between its planned moments into sections of up to SECTION_MAX
   seconds (the first is the sample).
2. `prepare` cuts each section's shot out of the base picture and the person out of it, every frame, on the GPU, with
   stray pieces the cut-out catches (a pillow, a chair at the edge) cleaned away.
3. `design` sends a section to Claude Code at a desk: its brief (the plan's moments in section seconds, the world, the
   look, the words), the kit for its engine, `bash look` to see frames, `bash review` for the director's verdict. It
   stops when the scene is ready or stops getting better. The first section writes the world's bible (world.md) and
   later sections are built to it.
4. `render` makes each section (HyperFrames or Blender) with their own sound and the scene's sound cues, and `film`
   joins them, the voice levelled.
"""

from __future__ import annotations

import glob
import hashlib
import json
import logging
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

from . import moments as mo
from . import scenes, stage

log = logging.getLogger("vidosuper.worlds")
SECTION_MAX = 24.0  # seconds a desk designs at once
SECTION_MIN = 6.0
KIT_2D = Path(__file__).with_name("worlds_2d.md")
KIT_3D = Path(__file__).with_name("worlds_3d.md")
BLENDER_KIT = Path(__file__).with_name("blender_kit.py")
EXAMPLE_2D = Path(__file__).parent / "examples" / "flat_space.html"
DESK_MINUTES = 50  # a desk's time for one section, its reviews included
_PROJECT = threading.Lock()


class WorldError(RuntimeError):
    pass


@dataclass
class Section:
    start: float
    end: float
    moments: list[int] = field(default_factory=list)  # indices into the design's moments

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)


# ---------------------------------------------------------------- 1. sections

def sections(design: mo.FilmDesign, duration: float, most: float = SECTION_MAX) -> list[Section]:
    """Contiguous sections covering the whole film, cut halfway through the gaps between moments, each at most `most`
    seconds unless one moment is longer (never cut inside a moment), none shorter than SECTION_MIN where it can be
    joined to its neighbour."""
    ms = sorted(enumerate(design.moments), key=lambda km: km[1].start)
    cuts = [round((a.end + b.start) / 2, 3) for (_, a), (_, b) in zip(ms, ms[1:]) if b.start >= a.end]
    cuts = [c for c in cuts if 0 < c < duration]
    out: list[Section] = []
    start = 0.0
    while start < duration - 1e-6:
        allowed = [c for c in cuts if start < c <= start + most]
        if start + most >= duration:
            end = duration
        elif allowed:
            end = allowed[-1]
        else:  # a long moment (or none at all): never cut inside a moment; to the gap after it, or a plain split
            end = start + most
            moved = True
            while moved:
                moved = False
                for _, m in ms:
                    if m.start < end < m.end:
                        end, moved = m.end, True
            later = [c for c in cuts if c >= end]
            end = min(duration, later[0] if later else end)
        out.append(Section(round(start, 3), round(end, 3)))
        start = end
    while len(out) > 1 and out[-1].duration < SECTION_MIN:  # a short tail joins the one before
        last = out.pop()
        out[-1] = Section(out[-1].start, last.end)
    while len(out) > 1 and out[0].duration < SECTION_MIN:
        nxt = out.pop(1)
        out[0] = Section(out[0].start, nxt.end)
    for s in out:
        s.moments = [k for k, m in ms if s.start <= m.start < s.end]
    return out


# ---------------------------------------------------------------- 2. the stage: their shot, and them cut out of it

def clean_matte(matte: Path, out: Path) -> Path:
    """The person's matte with what isn't them taken away: the biggest piece is them; other pieces stay only when they
    don't touch the frame's bottom or side edges (a raised hand stays, a pillow or chair at the edge goes)."""
    import cv2
    import numpy as np

    from ..ffmpeg import ffmpeg_bin

    cap = cv2.VideoCapture(str(matte))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    part = out.with_name(out.stem + ".part.mkv")
    enc = subprocess.Popen([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s",
                            f"{w}x{h}", "-r", f"{fps}", "-i", "-", "-c:v", "ffv1", "-pix_fmt", "gray", str(part)],
                           stdin=subprocess.PIPE)
    kernel = np.ones((9, 9), np.uint8)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        m = frame[..., 0] if frame.ndim == 3 else frame
        enc.stdin.write(keep_person(m, kernel).tobytes())
    cap.release()
    enc.stdin.close()
    enc.wait()
    if not part.exists():
        raise WorldError("the cut-out couldn't be cleaned")
    part.replace(out)
    return out


def keep_person(m, kernel=None):
    """One matte frame (uint8) with only the person kept (see clean_matte)."""
    import cv2
    import numpy as np

    n, lab, st, _ = cv2.connectedComponentsWithStats((m > 96).astype(np.uint8), 8)
    if n <= 2:
        return m
    big = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    keep = np.zeros(n, bool)
    keep[big] = True
    hh, ww = m.shape
    for c in range(1, n):
        x, y, cw, ch, area = st[c]
        edge = y + ch >= hh - 2 or x <= 1 or x + cw >= ww - 2
        if c != big and not edge and area > 0.0002 * hh * ww:
            keep[c] = True
    region = cv2.dilate(keep[lab].astype(np.uint8), kernel if kernel is not None else np.ones((9, 9), np.uint8))
    return (m * region).astype(np.uint8)


def prepare(base: Path, section: Section, folder: Path, person: bool, engine: str) -> Path:
    """The section's stage: shot.mp4 (the base picture for [start, end)) and, with a person, them cut out of it
    (person.webm for 2d, person/p_####.png frames with alpha for 3d). Made once."""
    st = folder / "stage"
    st.mkdir(parents=True, exist_ok=True)
    shot = st / stage.SHOT
    if not shot.exists():
        stage.cut_shot(base, section.start, section.end, shot)
    if not person:
        return st
    clean = st / "matte-clean.mkv"
    if not clean.exists():
        raw = scenes.matte_video(shot, 0.0, section.duration, st / "matte.mkv")
        if raw is None:
            raise WorldError("there is no cut-out model here to place them in the world")
        clean_matte(raw, clean)
    if engine == "2d" and not (st / stage.PERSON).exists():
        stage.person_clip(shot, clean, st / stage.PERSON)
    if engine == "3d" and not (st / "person" / "p_0001.png").exists():
        person_frames(shot, clean, st / "person")
    (st / "person.json").write_text(json.dumps({"clean": True}), encoding="utf-8")
    return st


def person_frames(shot: Path, matte: Path, out: Path) -> Path:
    """The person's frames for Blender: RGBA PNGs, their picture with the matte as alpha (a soft lens edge)."""
    import cv2
    import numpy as np

    out.mkdir(parents=True, exist_ok=True)
    a, b = cv2.VideoCapture(str(shot)), cv2.VideoCapture(str(matte))
    i = 0
    while True:
        ok, frame = a.read()
        ok2, m = b.read()
        if not ok or not ok2:
            break
        m = m[..., 0] if m.ndim == 3 else m
        m = cv2.GaussianBlur(cv2.resize(m, (frame.shape[1], frame.shape[0])), (3, 3), 0)
        cv2.imwrite(str(out / f"p_{i + 1:04d}.png"), np.dstack([frame, m]))
        i += 1
    a.release()
    b.release()
    if not i:
        raise WorldError("the person's frames couldn't be made")
    return out


# ---------------------------------------------------------------- 3. the brief and the desk

def checkpoints(design: mo.FilmDesign, section: Section) -> list[dict]:
    """Every moment's checkpoints, in section seconds."""
    out = []
    for k in section.moments:
        m = design.moments[k]
        for p in mo.checkpoints(m):
            t = round(p["t"] + m.start - section.start, 2)
            if 0 < t < section.duration:
                out.append({"t": t, "expect": p["expect"]})
    return sorted(out, key=lambda p: p["t"])[:12]


def brief_text(design: mo.FilmDesign, section: Section, words, ask: str, kind: str, first: bool,
               bible: str = "", w: int = 1920, h: int = 1080, place: str = "") -> str:
    """The section as the designer reads it: the world, the moments in it (section seconds), every word."""
    off = section.start
    said = " ".join(f"[{a - off:.2f}] {t}" for a, _, t in words if section.start <= a < section.end) or "(silence)"
    lines = [f"You design {section.start:.2f}-{section.end:.2f} s of their film ({section.duration:.2f} s, {w} x {h} "
             f"px). Times below are seconds into this section.",
             f"ENGINE: {'3d (Blender, with the kit)' if design.engine == '3d' else '2d (HyperFrames page)'}",
             f"THE WORLD (the whole frame, for the whole film): {design.world or 'yours to decide, from the look'}",
             f"THE LOOK: {design.look}"]
    if design.idea:
        lines.append(f"THE IDEA: {design.idea}")
    if kind == "voice":
        lines.append("THE PERSON: none: a voice-only film, every pixel is yours.")
    elif kind == "montage":
        lines.append("THEIR FOOTAGE: this is a stretch of their own film (clips cut to music); its shot is on your "
                     "desk (frames/). Their footage stays the hero unless they asked for the whole frame: the design "
                     "lives in it (built into the scene, framed by it, growing out of it), never stickers floating on "
                     "top. If they asked for a whole-frame moment (a map, a world, a 3D scene), the stretch is yours "
                     "and hands back to their footage at its end. Changes land on the music's marks below.")
    else:
        lines.append("THE PERSON: their cut-out stands in your world the whole time (see the kit for placing them). "
                     "Their face stays clear and comfortably big; nothing covers it.")
    if section.moments:
        lines.append("WHAT HAPPENS (a contract, checked frame by frame; each change lands on its word):")
        for k in section.moments:
            m = design.moments[k]
            lines.append(f"- {m.start - off:.2f}-{m.end - off:.2f} s: starts: {m.before}")
            for c in m.changes:
                lines.append(f"    on \"{c.word}\" at {c.at - off:.2f} s, over about {c.over:g} s: {c.becomes}")
            lines.append(f"    ends: {m.after}" + (f"  (where: {m.where})" if m.where else ""))
    else:
        lines.append("WHAT HAPPENS: nothing they describe falls here: keep the world alive (camera, light, depth) "
                     "around them, carrying what was built before.")
    allowed = [design.moments[k].text for k in section.moments if design.moments[k].text.strip()]
    lines.append("WORDS ON SCREEN: only " + "; ".join(f'"{t}"' for t in allowed) if allowed else
                 "WORDS ON SCREEN: none at all (no captions, labels, titles, numbers or letters).")
    if ask:
        lines.append(f"WHAT THEY ASKED FOR: {ask}")
    if first:
        lines.append("You are the first section: before building, write world.md, the world's bible every later "
                     "section is built to (palette in hex, how shapes are shaded, the glow, the textures, where the "
                     "person stands and how big, the camera's habits, the grain).")
    elif bible:
        lines.append("THE WORLD'S BIBLE (from the first section; build to it exactly so the film is one piece):\n"
                     + bible.strip())
    if place and not first:
        lines.append("THE PERSON'S PLACE, held for the whole film (Flicello puts it back if it changes): "
                     + (f"their card is person({place})" if design.engine == "3d" else place)
                     + ". Design around them there; move the camera, never them.")
    lines.append(f"EVERY WORD, with its time:\n{said}")
    return "\n".join(lines)


DESK = """You are at your desk. Work the way a top motion designer works: idea, build, look, fix, look again.
- Read brief.md, every frame in frames/, and the kit (kit.md). {reference}
- The WHOLE FRAME is yours: a world fills it, the person stands in it, the camera moves through it. Never a few \
things floating on top of their untouched footage (that reads as stickers): the one thing that must not happen.
- Write the scene to {scene} (Write, then Edit for changes).
- See it: `bash look 0.8 2.4 5.1` (up to 8 seconds into the section) renders those frames as the video will show them \
and prints their paths: Read every one. A stretch (`bash look 1.2-2.0`) makes one strip of 6 frames to judge motion.
- Check every checkpoint and every beat in the brief with looks. Be your harshest critic: does each frame act out \
what is said at that moment, exactly as the contract says? Is it designed and alive, never timid, never clip art? \
Anything broken, cut off at the edges, covering the face, leftover?
- When you believe it is finished, `bash review` ONCE (a few minutes): a strict director's deep review, the score out \
of 10 and every problem with its time. There is one review, no second: fix every problem it lists, look to check each \
fix, then reply DONE and nothing else. Fix what is there; don't add objects the brief doesn't ask for. A person \
watches the film next and gives their own notes."""


def desk_prompt(engine: str, has_reference: bool) -> str:
    ref = ("reference.* is the first section's finished scene: reuse its code and look so the film is one piece."
           if has_reference else "")
    if engine == "2d":
        ref += " Read example.html too: the craft level the owner asked for (see kit.md)."
    return DESK.format(reference=ref, scene="scene.py" if engine == "3d" else "scene.html")


def _system(engine: str, duration: float, w: int, h: int) -> str:
    kit = (KIT_3D if engine == "3d" else KIT_2D).read_text(encoding="utf-8")
    if engine == "2d":  # the 2d kit sits on HyperFrames' own contract (the rigs, the craft, the technical rules)
        contract = scenes._system().split("# The rig kit", 1)[-1]
        contract = ("# The rig kit" + contract).replace("- Reply with the whole document in one ```html block and "
                                                         "nothing after it.", "- Write the whole document to scene.html.")
        kit = kit + "\n\n" + contract
    kit = kit.replace("{DURATION}", f"{duration:.2f}").replace("{W}", str(w)).replace("{H}", str(h))
    return ("You are a world-class motion designer: the kind whose pieces fill motion-design galleries and whose "
            "explainers people watch for the animation itself. You design the whole frame of a section of a real "
            "person's film.\n\n" + kit)


def _scripts(desk: Path, engine: str) -> None:
    py = Path(sys.executable).as_posix()
    if engine == "3d":
        for name, cmd in (("look", "look3d"), ("review", "review3d")):
            (desk / name).write_text(f'#!/bin/bash\n"{py}" -m vidosuper.motion.worlds {cmd} "{desk.as_posix()}" "$@"\n',
                                     encoding="utf-8")
        # ready-made models and skies (Poly Haven, CC0), searched by words
        (desk / "assets").write_text(f'#!/bin/bash\n"{py}" -m vidosuper.motion.assets find "$@"\n', encoding="utf-8")
    else:
        scenes._look_script(desk)  # look, paint and review on the page (motion/scenes.py)


def design(design_: mo.FilmDesign, section: Section, folder: Path, words, ask: str, kind: str, first: bool,
           bible: str = "", reference: Path | None = None, timeout: float = DESK_MINUTES * 60.0,
           progress=lambda msg: None, place: str = "") -> Path:
    """The section designed by Claude Code at its desk (folder/desk). Returns the scene file (scene.html or
    scene.py)."""
    engine = design_.engine
    desk = folder / "desk"
    (desk / "frames").mkdir(parents=True, exist_ok=True)
    w, h = _size(folder / "stage" / stage.SHOT)
    for k, t in enumerate([section.duration * (j + 0.5) / 6 for j in range(6)]):
        frame = scenes._frame_at(folder / "stage" / stage.SHOT, t)
        if frame is not None:
            import cv2

            ok, jpg = cv2.imencode(".jpg", cv2.resize(frame, (480, int(480 * h / w))), [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                (desk / "frames" / f"frame-{k + 1:02d}-at-{t:05.2f}s.jpg").write_bytes(jpg.tobytes())
    (desk / "brief.md").write_text(brief_text(design_, section, words, ask, kind, first, bible, w, h, place), encoding="utf-8")
    spec = checkpoints(design_, section)
    sec_words = [(round(a - section.start, 2), round(b - section.start, 2), t) for a, b, t in words
                 if section.start <= a < section.end]
    (desk / "brief.json").write_text(json.dumps({
        "ask": ask, "start": section.start, "end": section.end, "width": w, "height": h, "fps": 30,
        "style": design_.look, "words": sec_words, "spec": spec,
        "text": "; ".join(design_.moments[k].text for k in section.moments if design_.moments[k].text.strip())}),
        encoding="utf-8")
    (desk / "kit.md").write_text((KIT_3D if engine == "3d" else KIT_2D).read_text(encoding="utf-8"), encoding="utf-8")
    if engine == "3d":
        shutil.copyfile(BLENDER_KIT, desk / "blender_kit.py")
    elif EXAMPLE_2D.exists():  # a finished piece at the owner's bar: its craft to learn from, never its content
        shutil.copyfile(EXAMPLE_2D, desk / "example.html")
    if reference is not None and reference.exists():
        shutil.copyfile(reference, desk / f"reference{reference.suffix}")
    _scripts(desk, engine)
    (desk / "review-limit").write_text(str(REVIEWS), encoding="utf-8")
    (desk / "system.md").write_text(_system(engine, section.duration, w, h) + "\n\n# Your desk\n"
                                    + desk_prompt(engine, reference is not None), encoding="utf-8")
    scene = desk / ("scene.py" if engine == "3d" else "scene.html")
    progress("designing the world" if first else "designing it to the world's bible")
    ask_ = "Design the section described in brief.md. Start by reading brief.md, kit.md and every frame in frames/."
    if scene.exists() and scene.stat().st_size >= 200:  # an earlier sitting was stopped (a plan limit, a restart)
        ask_ += (f" An earlier sitting already built {scene.name} (and world.md, reviews/ if there): continue from it, "
                 "don't start over.")
    _at_desk(desk, ask_, timeout, "session.json")
    if not scene.exists() or scene.stat().st_size < 200:
        raise WorldError("the designer left no scene (see its session.json)")
    return scene


def _at_desk(desk: Path, prompt: str, timeout: float, log_name: str) -> None:
    """One sitting of Claude Code at the desk (its system.md, its look and review), its session kept in log_name.
    WorldError when the plan's limit stops it."""
    from ..local import claude_exe, plan_env, run_claude

    cmd = [claude_exe(), "-p", "--output-format", "json", "--model", scenes.design_model(), "--system-prompt-file",
           str(desk / "system.md"), "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config",
           "--max-budget-usd", "60", "--tools", "Read,Write,Edit,Bash", "--allowedTools", "Read(./**)", "Write(./**)",
           "Edit(./**)", "Bash(bash look:*)", "Bash(bash review:*)", "Bash(bash paint:*)", "Bash(bash assets:*)"]
    env = {**plan_env(), "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "64000", "VIDO_SCENES_VIA": "claude_code",
           "BASH_DEFAULT_TIMEOUT_MS": "900000", "BASH_MAX_TIMEOUT_MS": "1200000"}
    try:
        proc = run_claude(cmd, prompt, desk, cwd=str(desk), timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:  # its time is up: what it built so far is still judged and used
        (desk / log_name).write_bytes(exc.stdout or b"")
        log.warning("the desk at %s ran out of its %d minutes", desk, int(timeout // 60))
        return
    (desk / log_name).write_bytes(proc.stdout or b"")
    said = ""
    try:
        said = str(json.loads(proc.stdout.decode("utf-8", errors="replace")).get("result") or "")
    except ValueError:
        pass
    if re.search(r"(session|usage|weekly|5-hour) limit|hit your (\S+ )?limit", said, re.I):
        raise WorldError(f"the Claude plan's limit is reached: {said[:160]}")


REVIEWS = 1  # one deep review per section, then a person's own notes: review loops bounced 6-7 and broke scenes
FIX_MINUTES = 25  # the sitting that fixes what the one review found, or what the person's notes say
FIX = """Your scene had its one review, and it is not ready yet: {score}/10.
{problems}
Fix every problem (the MEASURED ones are measured on their cut-out, so they are certain) without adding objects the \
brief doesn't ask for, and look to check each fix. There is no second review. Then reply DONE and nothing else."""
NOTES = """The person watched the film and says:
{notes}
Times they give are seconds into the whole film; this section runs {start:.2f}-{end:.2f} s of it, so {start:.2f} s \
there is 0 s in your scene. Change exactly what they ask, in this section, and keep everything else as it is (their \
words win over the brief). Look to check each change. There is no review: they watch it again. Then reply DONE and \
nothing else."""


def reviews(desk: Path) -> list[dict]:
    out = []
    for path in sorted(desk.glob("reviews/review-*.json"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0)):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def review(desk: Path) -> dict | None:
    """The section's one review: the one its desk asked for, or (when it asked for none) taken now."""
    if not reviews(desk) and scene_digest(desk):
        done = subprocess.run(["bash", "review"], cwd=str(desk), capture_output=True, timeout=1800)
        if done.returncode != 0:
            log.warning("review at %s failed: %s", desk, done.stderr.decode(errors="replace")[-400:])
    found = reviews(desk)
    return found[-1] if found else None


def settle(desk: Path, engine: str, place: str = "", minutes: float = FIX_MINUTES, progress=lambda msg: None) -> dict:
    """No section leaves its desk unjudged: it is held to the film's place for the person (`place`, from the first
    section) and has its one deep review; when its desk didn't ask for it, it is taken now and what it found is fixed
    in one more sitting (no second review: a person watches next). Returns that review's verdict ({score, ok,
    problems}, score None when it couldn't be judged)."""
    if place:
        hold_place(desk, engine, place)
    asked = bool(reviews(desk))  # its desk had its review and fixed what it found
    seen = review(desk)
    if seen and not seen.get("ok") and not asked:
        progress("fixing what the review found")
        problems = "\n".join(f"- {p}" for p in seen.get("problems") or []) or "- (no detail given: raise the craft)"
        _at_desk(desk, FIX.format(score=seen.get("score"), problems=problems), minutes * 60.0, "session-fix.json")
        if place:
            hold_place(desk, engine, place)
    return seen or {"score": None, "ok": False, "problems": ["it couldn't be judged"]}


# ---------------------------------------------------------------- the person's place, held for the whole film

_PLACE_2D = re.compile(r"#person\s*\{[^{}]*\}")
_PLACE_3D = re.compile(r"\bperson\(([^()]*)\)")


def place_of(desk: Path, engine: str) -> str:
    """Where the first section put the person (their #person CSS rule, or the arguments of its person() card), so
    every section after it holds them in the same place and size."""
    scene = desk / ("scene.py" if engine == "3d" else "scene.html")
    if not scene.exists():
        return ""
    m = (_PLACE_3D if engine == "3d" else _PLACE_2D).search(scene.read_text(encoding="utf-8"))
    return (m.group(1) if engine == "3d" else m.group(0)) if m else ""


def hold_place(desk: Path, engine: str, place: str) -> bool:
    """The scene's person put where the film holds them (`place`). True when it had to be moved."""
    scene = desk / ("scene.py" if engine == "3d" else "scene.html")
    if not place or not scene.exists():
        return False
    text = scene.read_text(encoding="utf-8")
    if engine == "3d":
        fixed = _PLACE_3D.sub(lambda m: f"person({place})", text, count=1)
    elif _PLACE_2D.search(text):
        fixed = _PLACE_2D.sub(lambda m: place, text, count=1)
    elif re.search(r"</style>", text, re.I):
        fixed = re.sub(r"(</style>)", lambda m: place + m.group(1), text, count=1, flags=re.I)
    else:
        fixed = re.sub(r"(</head>)", lambda m: f"<style>{place}</style>" + m.group(1), text, count=1, flags=re.I)
    if fixed == text:
        return False
    scene.write_text(fixed, encoding="utf-8")
    return True


def _size(video: Path) -> tuple[int, int]:
    import cv2

    cap = cv2.VideoCapture(str(video))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920, int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
    cap.release()
    return w, h


# ---------------------------------------------------------------- 3b. the 3d desk's look and review

def blender_exe() -> str:
    """Blender: BLENDER_PATH, on the PATH, installed, unpacked by Flicello, or on a pod's volume."""
    from ..local import checks

    if sys.platform.startswith("linux"):  # without the GL loader Blender quietly renders on the CPU: minutes a frame
        import ctypes.util

        if not ctypes.util.find_library("EGL"):
            raise WorldError("3D can't reach the GPU here: install libegl1 (apt-get install libegl1 libgl1)")
    found = os.environ.get("BLENDER_PATH") or shutil.which("blender") or checks._find_blender()
    if found:
        return found
    for pattern in (os.path.join(os.environ.get("LOCALAPPDATA", ""), "VidoSuper", "blender", "*", "blender.exe"),
                    os.path.join(os.environ.get("FLICELLO_VOLUME", "/workspace"), "blender", "blender")):
        hits = sorted(glob.glob(pattern), reverse=True)
        if hits:
            return hits[0]
    raise WorldError("3D needs Blender 4.2 or newer (install it, or set BLENDER_PATH)")


def blender(desk: Path, mode: str, asked: str = "", timeout: float = 3600.0) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(desk)}  # the scene imports blender_kit from its desk
    return subprocess.run([blender_exe(), "-b", "--python-use-system-env", "-P", str(desk / "scene.py"), "--", str(desk),
                           mode, asked],
                          capture_output=True, timeout=timeout, env=env, cwd=str(desk))


def stills3d(desk: Path, times: list[float]) -> dict[float, bytes]:
    """The 3d scene at these times, as JPEGs (or SceneError with Blender's own error)."""
    import cv2

    seen = {}
    for t, img in _blender_stills(desk, "stills", times).items():
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 84])
        if ok:
            seen[t] = jpg.tobytes()
    return seen


def _blender_stills(desk: Path, mode: str, times: list[float]) -> dict:
    """The scene's frames at these times as arrays (mode "stills": BGR; "alone": the person alone, BGRA)."""
    import cv2

    out = desk / (".look3d" if mode == "stills" else ".alone3d")
    shutil.rmtree(out, ignore_errors=True)
    proc = blender(desk, mode, ",".join(f"{t:.3f}" for t in times), timeout=1800)
    seen = {}
    for t in times:
        png = out / f"t{t:07.3f}.png"
        img = cv2.imread(str(png), cv2.IMREAD_COLOR if mode == "stills" else cv2.IMREAD_UNCHANGED) if png.exists() else None
        if img is not None:
            seen[t] = img
    if not seen:
        tail = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")
        err = re.findall(r"(Traceback.*|Error.*)", tail, re.S)
        raise scenes.SceneError((err[-1] if err else tail)[-1500:])
    return seen


def measured3d(desk: Path, duration: float) -> list[str]:
    """The person in the 3d scene measured (motion/framing.py), when the film has one."""
    import numpy as np

    from . import framing

    matte = desk.parent / "stage" / "matte-clean.mkv"
    if not matte.exists() or not (desk.parent / "stage" / "person" / "p_0001.png").exists():
        return []
    times = framing.sample_times(duration)
    full = _blender_stills(desk, "stills", times)
    alone = _blender_stills(desk, "alone", times)
    source = framing.source_masks(matte, times)
    seen = []
    for t in times:
        f, a = full.get(t), alone.get(t)
        if f is None or a is None or a.ndim != 3 or a.shape[2] != 4:
            continue
        alpha = a[..., 3]
        rgb = (a[..., :3].astype(np.float32) * (alpha[..., None] / 255.0)).astype(np.uint8)
        seen.append(framing.measure(t, f, rgb, alpha, source.get(t)))
    return framing.problems(seen)


def look3d_command(desk: Path, asked: list[str]) -> None:
    singles, strips = [], []
    for a in asked[:8]:
        m = re.fullmatch(r"\s*([\d.]+)\s*-\s*([\d.]+)\s*", a)
        if m:
            lo, hi = sorted((float(m.group(1)), float(m.group(2))))
            strips.append([round(lo + (hi - lo) * k / 5, 2) for k in range(6)])
        else:
            try:
                singles.append(round(float(a), 2))
            except ValueError:
                print(f"Not a time: {a}")
    times = sorted(set(singles) | {t for s in strips for t in s})
    if not times:
        return
    try:
        seen = stills3d(desk, times)
    except (scenes.SceneError, subprocess.TimeoutExpired) as exc:
        print(f"THE SCENE FAILED TO RUN: {exc}")
        return
    out = desk / "looks"
    out.mkdir(exist_ok=True)
    for t in singles:
        if seen.get(t):
            (out / f"look-at-{t:05.2f}s.jpg").write_bytes(seen[t])
            print(out / f"look-at-{t:05.2f}s.jpg")
    for s in strips:
        jpg = scenes.strip([(t, seen.get(t)) for t in s])
        if jpg:
            path = out / f"strip-{s[0]:05.2f}-{s[-1]:05.2f}s.jpg"
            path.write_bytes(jpg)
            print(f"{path}  (6 frames, {s[0]:.2f} s to {s[-1]:.2f} s, left to right)")


def _beats3d(desk: Path, duration: float) -> list[dict]:
    try:
        beats = json.loads((desk / "beats.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        beats = []
    return sorted([b for b in beats if isinstance(b, dict) and 0 <= float(b.get("t", -1)) <= duration],
                  key=lambda b: float(b["t"]))[:24]


def review3d_command(desk: Path) -> None:
    info = json.loads((desk / "brief.json").read_text(encoding="utf-8"))
    brief = scenes.Brief(**{**info, "words": [tuple(w) for w in info["words"]]})
    out = desk / "reviews"
    out.mkdir(exist_ok=True)
    n = len(list(out.glob("review-*.md"))) + 1
    allowed = scenes.review_allowance(desk)
    if allowed is not None and n > allowed:
        print(scenes.USED_UP)
        return
    try:
        seen = scenes.judge(brief, _beats3d(desk, brief.duration), lambda times, sub="": stills3d(desk, times),
                            most=scenes.DEEP if allowed is not None else 8)
        seen = scenes.with_measured(seen, measured3d(desk, brief.duration))
    except (scenes.SceneError, subprocess.TimeoutExpired) as exc:
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
    (out / f"review-{n}.json").write_text(json.dumps({"scene": scene_digest(desk), "score": seen["score"],
                                                      "ok": seen["ok"], "problems": seen["problems"]}), encoding="utf-8")
    print(said)
    print("\nThe strips it judged:\n" + "\n".join(strips))


# ---------------------------------------------------------------- 4. render, sound, the film

def render(section: Section, folder: Path, base: Path, engine: str, library: Path) -> Path:
    """The section for real, with their own sound under it and the scene's sound cues: folder/section.mp4."""
    desk = folder / "desk"
    picture = folder / "picture.mp4"
    if engine == "3d":
        frames = folder / "frames"
        shutil.rmtree(frames, ignore_errors=True)
        proc = blender(desk, "render", str(frames), timeout=4 * 3600)
        pngs = sorted(frames.glob("f_*.png"))
        if not pngs:
            raise WorldError("Blender rendered nothing: " + (proc.stderr or b"").decode(errors="replace")[-600:])
        from ..ffmpeg import ffmpeg_bin

        done = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-framerate", "30", "-i",
                               str(frames / "f_%04d.png"), "-c:v", "libx264", "-crf", "14", "-preset", "slow",
                               "-pix_fmt", "yuv420p", str(picture)], capture_output=True)
        if done.returncode != 0:
            raise WorldError("the frames couldn't be joined: " + done.stderr.decode(errors="replace")[-400:])
        try:
            cues = json.loads((desk / "sound.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cues = []
    else:
        html = (desk / "scene.html").read_text(encoding="utf-8")
        with _PROJECT:  # sections render side by side: each builds its page with its own stage, never another's
            scenes.STAGE[:] = [folder / "stage"]
            scenes.ASSETS[:] = [desk / "assets"]
            scenes.PERSON_TRACK[:] = []
            page = scenes.project(html, folder / "render")
        scenes.render(page, picture)
        cues = scenes.sounds_of(html, section.duration)
    cues = [c for c in cues if isinstance(c, dict) and c.get("sound") and 0 <= float(c.get("t", -1)) <= section.duration]
    return scenes.finish(base, section.start, section.end, picture, cues[:40], library, folder / "section.mp4")


def join(parts: list[Path], out: Path) -> Path:
    """The sections end to end, the voice levelled for listening (-15 LUFS)."""
    from ..ffmpeg import ffmpeg_bin

    out.parent.mkdir(parents=True, exist_ok=True)
    args = [ffmpeg_bin(), "-y", "-loglevel", "error"]
    for p in parts:
        args += ["-i", str(p)]
    n = len(parts)
    graph = "".join(f"[{k}:v][{k}:a]" for k in range(n)) + f"concat=n={n}:v=1:a=1[v][a0];" \
            "[a0]highpass=f=70,loudnorm=I=-15:TP=-1.5:LRA=9,aresample=48000[a]"
    # grain over a whole designed frame costs a lot of bits: capped, so 26 s isn't 200 MB, still above what video
    # sites keep for 1080p
    done = subprocess.run(args + ["-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf",
                                  "18", "-maxrate", "20M", "-bufsize", "40M", "-preset", "medium", "-pix_fmt",
                                  "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)],
                          capture_output=True)
    if done.returncode != 0 or not out.exists():
        raise WorldError("the sections couldn't be joined: " + done.stderr.decode(errors="replace")[-600:])
    return out


def digest(design_: mo.FilmDesign, section: Section, base: Path, ask: str) -> str:
    raw = json.dumps([design_.engine, design_.world, design_.look, design_.idea, section.start, section.end,
                      [design_.moments[k].model_dump(exclude={"why"}) for k in section.moments], ask,
                      base.name, base.stat().st_size], sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]


def parallel() -> int:
    try:
        return max(1, min(6, int(os.environ.get("VIDO_WORLDS_PARALLEL", "3"))))
    except ValueError:
        return 3


def make(design_: mo.FilmDesign, base: Path, words, ask: str, kind: str, folder: Path, library: Path,
         only_first: bool = False, progress=lambda done, total, msg: None) -> dict:
    """The film (or, `only_first`, its first section as the sample) designed and rendered. Returns {video, sections:
    [{start, end, video, kept, score}], notes}. A section made before for the same plan, world and picture is kept."""
    folder.mkdir(parents=True, exist_ok=True)
    duration = _duration(base)
    parts = sections(design_, duration)
    if only_first:
        parts = parts[:1]
    person = kind not in ("voice", "montage")  # a montage's stretch: their footage, nobody cut out of it
    done = [0]
    notes: list[str] = []
    results: dict[int, dict] = {}

    def one(k: int, bible: str = "", reference: Path | None = None, place: str = "") -> dict:
        s = parts[k]
        here = folder / digest(design_, s, base, ask)
        tag = f"section {k + 1} of {len(parts)}"
        out = here / "section.mp4"
        if out.exists():
            return {"start": s.start, "end": s.end, "video": out, "kept": True, "dir": here, "score": score(here / "desk")}
        say = lambda msg: progress(done[0], len(parts), f"{tag}: {msg}")  # noqa: E731
        say("cutting you out of the picture" if person else "setting up")
        prepare(base, s, here, person, design_.engine)
        design(design_, s, here, words, ask, kind, first=(k == 0), bible=bible, reference=reference, progress=say,
               place=place)
        seen = settle(here / "desk", design_.engine, place, progress=say)
        if not seen.get("ok"):
            notes.append(f"Section {k + 1} ({s.start:.0f}-{s.end:.0f} s) left its desk not ready"
                         + (f" ({seen['score']:g}/10)" if isinstance(seen.get("score"), (int, float)) else "")
                         + (f": {seen['problems'][0]}" if seen.get("problems") else ""))
        say("rendering it")
        render(s, here, base, design_.engine, library)
        return {"start": s.start, "end": s.end, "video": out, "kept": False, "dir": here, "score": score(here / "desk")}

    results[0] = one(0)
    done[0] = 1
    progress(1, len(parts), f"section 1 of {len(parts)} made")
    first_dir = results[0]["dir"] / "desk"
    bible = (first_dir / "world.md").read_text(encoding="utf-8") if (first_dir / "world.md").exists() else ""
    reference = next((p for p in (first_dir / "scene.py", first_dir / "scene.html") if p.exists()), None)
    place = place_of(first_dir, design_.engine) if person else ""

    def rest(k: int) -> None:
        try:
            results[k] = one(k, bible, reference, place)
        except Exception as exc:  # noqa: BLE001 - one section failing is said; the film is made without its design
            log.warning("section %d failed", k + 1, exc_info=True)
            notes.append(f"Section {k + 1} ({parts[k].start:.0f}-{parts[k].end:.0f} s) couldn't be designed: "
                         f"{type(exc).__name__}: {str(exc)[:200]}")
        done[0] += 1
        progress(done[0], len(parts), f"{done[0]} of {len(parts)} sections made")

    with ThreadPoolExecutor(parallel()) as ex:
        list(ex.map(rest, range(1, len(parts))))
    made = [results[k] for k in sorted(results)]
    if len(made) < len(parts):  # a failed section: the plain picture with their sound keeps the film whole
        for k, s in enumerate(parts):
            if k not in results:
                plain = folder / f"plain-{s.start:.2f}-{s.end:.2f}.mp4"
                _plain(base, s, plain)
                made.append({"start": s.start, "end": s.end, "video": plain, "kept": False, "dir": None})
        made.sort(key=lambda r: r["start"])
    video = made[0]["video"] if only_first else join([r["video"] for r in made], folder / "film.mp4")
    return {"video": video, "sections": [{k: v for k, v in r.items() if k != "dir"} for r in made], "notes": notes,
            "engine": design_.engine}


def scene_digest(desk: Path) -> str:
    scene = next((p for p in (desk / "scene.html", desk / "scene.py") if p.exists()), None)
    return scenes._digest(scene.read_text(encoding="utf-8")) if scene else ""


_TIME = re.compile(r"\b(?:(\d+):(\d{1,2}(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds)\b)", re.I)


def times_in(notes: str) -> list[float]:
    """The moments a person's notes point at ("at 0:12", "12 s", "1:05.5"), in seconds."""
    out = []
    for m in _TIME.finditer(notes):
        out.append(int(m.group(1)) * 60 + float(m.group(2)) if m.group(1) else float(m.group(3)))
    return out


def revise(design_: mo.FilmDesign, base: Path, words, ask: str, kind: str, folder: Path, library: Path, notes: str,
           progress=lambda done, total, msg: None) -> dict:
    """The film changed by a person's notes: only the sections their times point at (every section when they give
    none) go back to their desks with the notes, are re-rendered, and the film is joined again. Nothing is
    re-planned or designed from zero. Same result as `make`; WorldError when the film was never made here."""
    duration = _duration(base)
    parts = sections(design_, duration)
    dirs = [folder / digest(design_, s, base, ask) for s in parts]
    if not all((d / "section.mp4").exists() and (d / "desk").exists() for d in dirs):
        raise WorldError("this film's sections aren't all here to change: make it first")
    at = times_in(notes)
    touched = [k for k, s in enumerate(parts) if not at or any(s.start <= t < s.end or (t == duration and
                                                                                      s.end == duration) for t in at)]
    touched = touched or list(range(len(parts)))
    place = place_of(dirs[0] / "desk", design_.engine) if kind != "voice" else ""
    done = [0]

    def one(k: int) -> None:
        s, here = parts[k], dirs[k]
        progress(done[0], len(touched), f"section {k + 1} of {len(parts)}: making your changes")
        _at_desk(here / "desk", NOTES.format(notes=notes.strip(), start=s.start, end=s.end), FIX_MINUTES * 60.0,
                 f"session-notes-{int(time.time())}.json")
        if place:
            hold_place(here / "desk", design_.engine, place)
        render(s, here, base, design_.engine, library)
        done[0] += 1
        progress(done[0], len(touched), f"{done[0]} of {len(touched)} sections changed")

    with ThreadPoolExecutor(parallel()) as ex:
        list(ex.map(one, touched))
    film = join([d / "section.mp4" for d in dirs], folder / "film.mp4")
    return {"video": film, "engine": design_.engine, "notes": [], "changed": [k + 1 for k in touched],
            "sections": [{"start": s.start, "end": s.end, "video": d / "section.mp4", "kept": k not in touched,
                          "score": score(d / "desk")} for k, (s, d) in enumerate(zip(parts, dirs))]}


def score(desk: Path) -> float | None:
    """The score of the section's review (None: it was never reviewed)."""
    found = reviews(desk)
    try:
        return float(found[-1]["score"]) if found else None
    except (KeyError, TypeError, ValueError):
        return None


def requirements(design_: mo.FilmDesign, result: dict, ask: str, total: int) -> list[dict]:
    """The checks shown with the film: every section designed as a whole world, the director's verdicts, no words on
    screen nobody asked for."""
    from ..studio.requirements import _row

    secs = result["sections"]
    designed = [s for s in secs if s.get("score") is not None or s.get("kept")]
    failed = [n for n in result["notes"] if "couldn't be designed" in n]
    rows = [_row(len(designed) == total and not failed, "the whole frame designed: a world with you in it",
                 f"{len(designed)} of {total} sections" + (f"; {failed[0]}" if failed else ""))]
    scores = [s["score"] for s in secs if s.get("score") is not None]
    rows.append(_row(bool(scores) and min(scores) >= 7, "checked by the director against your words",
                     ", ".join(f"{x:g}/10" for x in scores) if scores else "not reviewed"))
    words_on = sum(1 for m in design_.moments if m.text.strip())
    rows.append(_row(words_on == 0 or mo.wants_text(ask), "no words on screen you didn't ask for",
                     "none" if not words_on else f"words in {words_on} moments, as you asked"))
    return rows


def _plain(base: Path, s: Section, out: Path) -> Path:
    from ..ffmpeg import ffmpeg_bin

    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{s.start:.3f}", "-i", str(base), "-t",
                    f"{s.duration:.3f}", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
                   capture_output=True)
    return out


def _duration(video: Path) -> float:
    import cv2

    cap = cv2.VideoCapture(str(video))
    n, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0, cap.get(cv2.CAP_PROP_FPS) or 30
    cap.release()
    return round(n / fps, 3)


def latest_look(folder: Path) -> Path | None:
    """The newest frame any desk under `folder` has looked at: shown to the person while it works."""
    found = [Path(p) for p in glob.glob(str(folder / "*" / "desk" / "looks" / "*.jpg"))]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "look3d":
        look3d_command(Path(sys.argv[2]), sys.argv[3:])
    elif len(sys.argv) >= 3 and sys.argv[1] == "review3d":
        review3d_command(Path(sys.argv[2]))
