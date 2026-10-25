"""Motion design written for the moment: Claude designs each scene as code, the way a motion designer builds one in
After Effects or a creative coder in an artifact. Nothing comes from a kit of templates.

1. `gather` reads the moment: what is said, word by word, with its times; frames of the shot; where the person is (and
   the top of their head) in each frame.
2. `write` has Claude (Opus) design the scene for those words and that frame, as one HyperFrames composition: HTML,
   SVG, CSS and GSAP 3.13 with its pro plugins (line drawing, shape morphing, motion paths, split text, physics), timed
   to the words, with its own look, its beats and its sound cues. Anything meant to sit behind the person goes in a
   `.behind` layer.
3. `look` renders stills of its own scene at its beats with their real shot in the page (motion/stage.py: the shot,
   the behind layer, the person cut out of the shot, the rest on top, all composited by the browser), and Claude
   judges them as a strict motion director: is the idea fresh, does each frame act out what is being said, is it
   designed, is anything broken? `write` fixes what it found, up to a few rounds.
4. `render` makes the scene for real, frame by frame (HyperFrames), shot and all; `finish` puts the person's own sound
   back under it with the scene's sounds, each made to its description (ElevenLabs) and kept.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import REPO_ROOT
from ..local import claude_exe, plan_env, run_claude
from . import stage

DESIGN_MODEL = "claude-opus-5-5"
GSAP_DIR = REPO_ROOT / "frontend" / "node_modules" / "gsap" / "dist"
GSAP_FILES = ("gsap.min.js", "CustomEase.min.js", "CustomBounce.min.js", "CustomWiggle.min.js", "DrawSVGPlugin.min.js",
              "MorphSVGPlugin.min.js", "MotionPathPlugin.min.js", "SplitText.min.js", "Physics2DPlugin.min.js",
              "ScrambleTextPlugin.min.js", "TextPlugin.min.js", "EasePack.min.js")
PLUGINS = ("CustomEase", "CustomBounce", "CustomWiggle", "DrawSVGPlugin", "MorphSVGPlugin", "MotionPathPlugin",
           "SplitText", "Physics2DPlugin", "ScrambleTextPlugin", "TextPlugin")
CRAFT = REPO_ROOT / "motion" / "node_modules" / "hyperframes" / "dist" / "skills" / "hyperframes" / "references" / "motion.md"
SCENE_CRAFT = Path(__file__).with_name("scene_craft.md")  # designed things acting in a real person's video
RIG = Path(__file__).with_name("rig.js")  # the designer's own rigs: physics, follow-through, the person's body
FONTS = ("Inter, Montserrat, Outfit, Nunito, Oswald, League Gothic, Archivo Black, Space Mono, IBM Plex Mono, "
         "JetBrains Mono, EB Garamond, Playfair Display, Source Code Pro, Roboto, Open Sans, Lato, Poppins")


class SceneError(RuntimeError):
    pass


@dataclass
class Brief:
    """One moment to design for: what they asked, what is said there, the frame and where the person is."""
    ask: str
    start: float  # film seconds the scene starts
    end: float
    width: int
    height: int
    fps: int = 30
    style: str = ""
    words: list[tuple[float, float, str]] = field(default_factory=list)  # seconds into the scene
    frames: list[tuple[float, bytes]] = field(default_factory=list)  # (seconds into the scene, jpeg)
    people: list[dict] = field(default_factory=list)  # {t, box: [x0, y0, x1, y1], head: [x, y]} in frame shares
    track: list[dict] = field(default_factory=list)  # every 0.1 s: {t, head: [x, y], shoulders: [[x, y], [x, y]], box}

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)


# ---------------------------------------------------------------- 1. the moment

def _frame_at(video: Path, seconds: float):
    import cv2

    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, seconds) * 1000.0)
    ok, frame = cap.read()
    cap.release()
    return frame if ok else None


def _shoulders(on, top: int) -> list[list[float]] | None:
    """Where the shoulders are on a person's mask: the first row well below the head where the body is much wider
    than the neck; its edges, a little in."""
    import numpy as np

    h, w = on.shape
    widths = on.sum(axis=1)
    neck_rows = range(top + int(0.08 * h), min(h, top + int(0.35 * h)))
    neck = min((widths[r] for r in neck_rows if widths[r] > 0), default=0)
    if not neck:
        return None
    for r in range(top + int(0.12 * h), h):
        if widths[r] > 1.8 * neck:
            xs = np.where(on[r])[0]
            inset = 0.08 * (xs.max() - xs.min())
            return [[round((xs.min() + inset) / w, 3), round(r / h, 3)], [round((xs.max() - inset) / w, 3), round(r / h, 3)]]
    return None


def _person(frame, find) -> dict | None:
    """Where the person is in a frame: their box and the top of their head, as shares of the frame."""
    import numpy as np

    m = find(np.ascontiguousarray(frame[..., ::-1]))
    on = m > 0.5
    if on.mean() < 0.01:
        return None
    ys, xs = np.where(on)
    h, w = on.shape
    top = ys.min()
    head_x = float(xs[ys <= top + max(2, int(0.02 * h))].mean())
    return {"box": [round(xs.min() / w, 3), round(top / h, 3), round(xs.max() / w, 3), round(ys.max() / h, 3)],
            "head": [round(head_x / w, 3), round(top / h, 3)], "shoulders": _shoulders(on, top)}


def _steady(track: list[dict]) -> None:
    """A light moving average over the track, so pinned things don't shiver with the matte's noise."""
    keys = ("head", "shoulders")
    for k in range(len(track)):
        near = track[max(0, k - 2):k + 3]
        for key in keys:
            vals = [n[key] for n in near if n.get(key)]
            if not vals or not track[k].get(key):
                continue
            if key == "head":
                track[k][key] = [round(sum(v[0] for v in vals) / len(vals), 3), round(sum(v[1] for v in vals) / len(vals), 3)]
            else:
                track[k][key] = [[round(sum(v[i][0] for v in vals) / len(vals), 3),
                                  round(sum(v[i][1] for v in vals) / len(vals), 3)] for i in (0, 1)]


def gather(video: Path, words: list[tuple[float, float, str]], start: float, end: float, ask: str, style: str = "",
           frames: int = 6) -> Brief:
    """The moment [start, end) of a rendered film: its words (film seconds in, scene seconds out), frames of it and
    where the person is in each."""
    import cv2

    from ..render.behind import people_matte

    cap = cv2.VideoCapture(str(video))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = min(60, int(round(cap.get(cv2.CAP_PROP_FPS) or 30)))  # the render is the shot itself: at its own rate
    cap.release()
    brief = Brief(ask=ask, start=start, end=end, width=w, height=h, fps=fps, style=style,
                  words=[(round(a - start, 2), round(b - start, 2), t) for a, b, t in words if start <= a < end])
    find = people_matte()
    if find is not None:  # the person through the whole scene, every 0.1 s, steadied
        t = 0.0
        while t < brief.duration:
            frame = _frame_at(video, start + t)
            seen = _person(frame, find) if frame is not None else None
            if seen:
                brief.track.append({"t": round(t, 2), **seen})
            t += 0.1
        _steady(brief.track)
    for k in range(frames):
        t = (k + 0.5) * brief.duration / frames
        frame = _frame_at(video, start + t)
        if frame is None:
            continue
        small = cv2.resize(frame, (360, int(360 * h / w)))
        ok, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            brief.frames.append((round(t, 2), jpg.tobytes()))
        if find is not None:
            seen = _person(frame, find)
            if seen:
                brief.people.append({"t": round(t, 2), **seen})
    return brief


# ---------------------------------------------------------------- 2. the design, written by Claude

def _system() -> str:
    craft = CRAFT.read_text(encoding="utf-8") if CRAFT.exists() else ""
    scene_craft = SCENE_CRAFT.read_text(encoding="utf-8") if SCENE_CRAFT.exists() else ""
    return f"""You are a world-class motion designer and creative coder: the kind whose pieces fill motion-design \
galleries, designed with intent and alive in every frame. You make motion graphics for a real person's video by \
writing them as code: one HyperFrames composition (HTML, SVG, CSS and JavaScript with GSAP 3.13), rendered frame by \
frame and laid over their footage.

# How you design
- Everything comes from this moment: their words, their frame, what they asked. Never a template, never a stock \
"lower third", never clip art. If they describe something happening, it happens on screen, as real moving objects \
with weight: anticipation before a move, follow-through after it, arcs, squash and stretch where it is physical, \
shadows and light that agree, secondary motion (a puff of dust where it lands, a ripple, a trail). Words on screen \
only where words are the point or they ask for them.
- First decide the look and write it in a comment at the top of the file: palette (3 to 5 hex colours), type (from \
the fonts below), the material (flat graphic, glossy 3D-ish, paper cut-out, line art, neon glow, chrome, risograph...), \
how light falls, how deep the world is. Hold it everywhere. If they named a style, that is the look.
- Then the beats: each thing they say that should show becomes a beat, timed so its key frame lands on its word \
(start the anticipation 3 to 6 frames before the word; the hit lands on the word). Transitions between beats have a \
cause (an object becomes the next thing, morphs, is carried across); things that are no longer needed leave with \
intent rather than vanishing.
- Compose with the person in the frame: their box and the top of their head are given for sampled moments. Keep \
their face clear unless the idea needs them (something lands on their head, wings open behind them). Use their \
position when something comes from them. Leave negative space; one focal point at a time.
- Scale and presence: hero objects are big and confident (15 to 35% of the frame's width), lit, with depth and a \
shadow that agrees with the light. Small timid elements in a corner read as amateur. When the story needs a world \
(a ground to land on, a sky to fly into, a horizon, a room), build it: a designed ground plane or horizon, depth, \
atmosphere, but keep the person visible (worlds usually go in the behind layer, or frame them).
- Nothing unintended is ever visible: no guides, placeholders, outlines of where something will be, debug shapes, \
markers or test circles. An element is invisible until its beat.
- Quality bar: every frame should look like a still from a professional motion-design reel (Buck, Ordinary Folk, \
Animade, a top creative coder). Rich enough to delight, never cluttered.
- Before the code, write the design plan in an HTML comment at the top: the look (palette, type, material, light), \
then each beat: its time, what appears, where (px), how big, the move and its ease, and the sound. Then build exactly \
that.

{scene_craft}

# The rig kit (loaded as `window.rig`; building blocks, not effects)
- `rig.at(part, t, W, H)` -> [x, y] px of 'head' (its top), 'neck', 'shoulderL', 'shoulderR', 'chest' or 'center' at \
scene time t; `rig.person(t)` -> {{head, shoulders, box, neck, chest, width}} in frame shares.
- `rig.pin(tl, el, part, {{from, to, W, H, dx, dy, anchor: [0.5, 1]}})`: the element's anchor point follows that part.
- `rig.drop(tl, el, {{at, y0, y1, g: 3200, e: 0.55, bounces: 3, squash: 0.28, x0, x1, shadow}})` -> impacts [{{t, v}}].
- `rig.spring(name, damping, wobbles)` -> an ease name; `rig.wobble(tl, el, {{at, prop, amount, freq, decay, \
duration}})`; `rig.cascade(tl, parts, vars, {{at, lag}})`; `rig.burst(tl, make, {{at, x, y, n, speed, dir, spread, \
gravity, drag, life, seed}})`; `rig.blur(tl, el, {{at, duration, px, dir}})`; `rig.shake(tl, el, {{at, amount, \
duration, freq, rotate, seed, base}})`: a camera shake (on #stage), decaying; `rig.rand(seed)` -> a seeded random().

# Motion craft (measured from professional films; your defaults)
{craft}

# The technical contract (follow exactly, or the render fails)
- Output ONE complete HTML document. Body: `<div id="root" data-composition-id="main" data-start="0" \
data-duration="{{DURATION}}" data-width="{{W}}" data-height="{{H}}">` (no background on html, body or root). Inside \
it one wrapper `<div id="stage" class="clip" data-start="0" data-duration="{{DURATION}}">` that holds everything; \
size it with `position:absolute; inset:0`.
- Their real shot is in the page. `<video id="shot">` (their footage, full frame) is put in as the first thing inside \
#stage, and when you have a behind layer, `<video id="person">` (the person cut out of the shot, frame by frame) is \
put in right after it. The picture is built from the bottom up: the shot, your `<div class="behind">` (a direct \
child of #stage, `position:absolute; inset:0`, for anything that must appear BEHIND the person), the person, then \
everything else you write. Don't write, time, hide or restyle #shot or #person (#stage carries them: see the camera).
- The behind layer may be a whole opaque world: a full-frame background there REPLACES their real background and \
they stand inside your design (a black void that turns purple, a sky, a grid, a stage). Use that when they talk \
about the background, or when the piece is better as a designed world around them than as things floating over \
their room.
- Light and shade are real, because the footage is in the page: a glow in `mix-blend-mode: screen` (or \
`plus-lighter`) brightens what is under it the way light does (their face, their wall), `multiply` shades it, \
`overlay`, `soft-light` and `color` tint it, and `backdrop-filter` blurs or tints what is behind a panel. A blend \
mixes with everything painted before it unless an ancestor between it and #stage isolates it (opacity below 1, a \
transform, a filter or `isolation` on that ancestor): put the blend mode on the outermost element that moves, and \
animate the children of `.behind`, never `.behind` itself.
- The camera is yours: animate #stage (scale, x, y, rotation) to push in on a hit, shake on an impact \
(`rig.shake`), drift with a reveal; their shot and everything on it move together, as with a real camera. Keep \
the frame covered: scale up by more than the move.
- Their footage can be part of an effect: another `<video src="assets/shot.mp4" muted playsinline>` (untimed, \
inside #stage, `position:absolute; inset:0; width:100%; height:100%; object-fit:cover`) under a filter and a mask \
or clip-path shows their real picture changed there: bent by an SVG `feDisplacementMap` (heat haze, a shockwave \
ring, a ripple, a lens), recoloured, blurred or pixelated.
- GSAP 3.13 and these plugins are already loaded and registered (do not load or register them): {", ".join(PLUGINS)}. \
Build ONE timeline: `const tl = gsap.timeline({{ paused: true }});` with every tween at an absolute time in seconds \
(`tl.fromTo(el, {{...}}, {{..., duration: d}}, 1.52)`), then `window.__timelines["main"] = tl;` at the end. Use \
`fromTo` (not `from`). Set start states in the tween, not in CSS transforms.
- Everything visible is a function of timeline time: no Date, no timers, no requestAnimationFrame, no network, no \
external files or images, unseeded Math.random (use a small seeded PRNG such as mulberry32). Finite repeats only. \
Canvas or generative drawing: draw from time in a `window.addEventListener("hf-seek", e => draw(e.detail.time))` \
listener and draw(0) once; prefer SVG.
- Coordinates: the stage is {{W}} x {{H}} px. Draw with SVG (paths, gradients, filters, masks) and HTML. Fonts \
available by name: {FONTS}.
- Elements start invisible where they should not show yet (opacity 0 or scale 0 in the first tween's from-state).
- Put two JSON blocks in the body:
  `<script type="application/json" id="beats">[{{"t": 1.6, "what": "the red ball appears at the top"}}, ...]</script>` \
the key moments, seconds into the scene, one per idea (these are checked frame by frame), and
  `<script type="application/json" id="sound">[{{"t": 1.55, "sound": "a short airy whoosh as the ball starts to \
fall", "gain": -14}}, ...]</script>` a sound designer's cues: one for each visible action (a landing, a pop, a draw, \
a whoosh, a shimmer), described in a few words so it can be made, gain in dB (-8 loud to -22 subtle); they sit \
under the person's voice, so never on top of a word unless it is the hit.
- Reply with the whole document in one ```html block and nothing after it."""


def _content(brief: Brief, previous: str | None, problems: list[str] | None) -> list[dict]:
    said = " ".join(f"{a:.2f}-{b:.2f} {t}" for a, b, t in brief.words) or "(nobody speaks here)"
    people = "; ".join(f"at {p['t']:.1f}s box {p['box']} head top {p['head']}" for p in brief.people) or "no one found"
    text = (f"What they asked for: {brief.ask}\n"
            + (f"Style they asked for: {brief.style}\n" if brief.style else "")
            + f"\nThe scene: {brief.width} x {brief.height} px, {brief.fps} fps, {brief.duration:.2f} s long "
            f"(DURATION = {brief.duration:.2f}, W = {brief.width}, H = {brief.height}).\n"
            f"What is said, word by word (seconds into the scene, start-end word):\n{said}\n\n"
            f"Where the person is (frame shares, x0 y0 x1 y1; head top x y): {people}\n"
            f"The person is tracked through the whole scene every 0.1 s ({len(brief.track)} samples: head top, left "
            "and right shoulder, box, in frame shares). It is in the composition as `window.PERSON` (an array of "
            "{t, head:[x,y], shoulders:[[x,y],[x,y]], box:[x0,y0,x1,y1]}). Anything attached to them (wings on the "
            "shoulders, a halo on the head, a ball dropping from their head) must follow it: sample it at the "
            "timeline time (interpolate between samples) inside a draw(t) driven by the timeline, never a fixed "
            "position, because they move.\n"
            "Frames of the shot follow, each labelled with its time.")
    out: list[dict] = [{"type": "text", "text": text}]
    for t, jpg in brief.frames:
        out.append({"type": "text", "text": f"Frame at {t:.1f} s:"})
        out.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                "data": base64.b64encode(jpg).decode("ascii")}})
    if previous is not None:
        out.append({"type": "text", "text": "Your previous version of this scene:\n```html\n" + previous + "\n```\n"
                    "A strict motion director looked at its frames over the footage and found:\n- "
                    + "\n- ".join(problems or ["(nothing listed)"])
                    + "\nFix every one of these, keep what works, and send the whole improved document."})
    return out


def _client():
    if VIA == "claude_code":
        return None
    import anthropic

    from ..music.compose import _load_dotenv

    _load_dotenv()
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise SceneError("Designing scenes needs ANTHROPIC_API_KEY.")
    return anthropic.Anthropic(api_key=key)


LAST: dict = {}  # the last raw reply and why it stopped, kept for the work folder (debugging what it sent)
# who designs: "claude_code" (Claude Code at a desk, on the person's own Claude plan: the default brain) or "api"
# (the Anthropic API, billed per call, when they chose it as the brain). VIDO_SCENES_VIA chooses.
VIA = os.environ.get("VIDO_SCENES_VIA", "claude_code")


def _ask_claude_code(system: str, content: list[dict], task: str, timeout: float = 2400.0) -> str:
    """The same question through `claude -p` on the owner's plan: the pictures are written to files it reads, and a
    scene is written by it to a file (no reply-length limit), then read back."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="vido-scene-") as tmp:
        folder = Path(tmp)
        parts, n = [], 0
        for block in content:
            if block.get("type") == "text":
                parts.append(block["text"])
            elif block.get("type") == "image":
                n += 1
                img = folder / f"frame-{n:02d}.jpg"
                img.write_bytes(base64.b64decode(block["source"]["data"]))
                parts.append(f"[image: read {img} with the Read tool]")
        out = folder / "answer.txt"
        prompt = "\n\n".join(parts) + (
            f"\n\nRead every image listed above with the Read tool before you answer. Then write your complete "
            f"answer (exactly what you would reply, including any ```html block) to the file {out} with the Write "
            "tool, and reply only DONE.")
        (folder / "system.md").write_text(system, encoding="utf-8")
        cmd = [claude_exe(), "-p", "--output-format", "json", "--model", DESIGN_MODEL, "--system-prompt-file",
               str(folder / "system.md"), "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config",
               "--max-budget-usd", "25", "--tools", "Read,Write", "--allowedTools", "Read(./**)", "Write(./**)",
               "--add-dir", str(folder)]
        env = {**plan_env(), "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "64000"}  # the person's plan, never an API key
        proc = run_claude(cmd, prompt, folder, cwd=str(folder), timeout=timeout, env=env)
        text = out.read_text(encoding="utf-8") if out.exists() else ""
        if not text:
            try:
                text = str(json.loads(proc.stdout.decode("utf-8", errors="replace")).get("result") or "")
            except ValueError:
                text = ""
        LAST.update(task=task, stop="claude_code exit %s" % proc.returncode, text=text,
                    tokens=0, err=proc.stderr.decode("utf-8", errors="replace")[-800:])
        return text


def _ask(client, system: str, content: list[dict], max_tokens: int, spend=None, task: str = "scene",
         effort: str = "max") -> str:
    """Claude, thinking as long as the task needs (adaptive thinking at this effort)."""
    if VIA == "claude_code":
        return _ask_claude_code(system, content, task)
    t0 = time.monotonic()
    with client.messages.stream(model=DESIGN_MODEL, max_tokens=max_tokens, system=system,
                                thinking={"type": "adaptive"}, extra_body={"output_config": {"effort": effort}},
                                messages=[{"role": "user", "content": content}]) as stream:
        msg = stream.get_final_message()
    if spend is not None:
        try:
            from ..studio.brain.backends import Usage

            u = msg.usage
            spend(Usage.priced(task, DESIGN_MODEL, "api", u.input_tokens, u.output_tokens,
                               getattr(u, "cache_creation_input_tokens", 0) or 0,
                               getattr(u, "cache_read_input_tokens", 0) or 0, time.monotonic() - t0))
        except Exception:  # noqa: BLE001 - the books are a help; the scene goes on
            pass
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    LAST.update(task=task, stop=getattr(msg, "stop_reason", ""), text=text,
                tokens=getattr(msg.usage, "output_tokens", 0))
    return text


def write(brief: Brief, client=None, previous: str | None = None, problems: list[str] | None = None,
          spend=None) -> str:
    """The scene as one HyperFrames composition, designed by Claude for this moment (a revision when `previous` and
    `problems` are given)."""
    client = client or _client()
    system = (_system().replace("{DURATION}", f"{brief.duration:.2f}").replace("{W}", str(brief.width))
              .replace("{H}", str(brief.height)))
    reply = _ask(client, system, _content(brief, previous, problems), 64000, spend, "scene", "high")
    m = re.search(r"```html\s*(.*?)```", reply, re.S) or re.search(r"(<!doctype html.*</html>)", reply, re.S | re.I)
    if not m:
        why = "it ran out of room before finishing" if LAST.get("stop") == "max_tokens" else \
            f"its reply had no html document ({len(reply)} characters, stopped: {LAST.get('stop')})"
        raise SceneError(f"the designer sent no composition: {why}")
    return m.group(1).strip()


# ---------------------------------------------------------------- 2b. the designer at their desk (Claude Code)

DESK = """You are at your desk with the scene's files. Work the way a motion designer works: build, look, fix, look \
again.
- The brief is in brief.md and the frames of the shot are in frames/ (read them all with the Read tool first).
- Find the idea before you build. In concepts.md write three different ideas for this moment, a few lines each: \
each a different metaphor or physical event, not variations of one. The first idea that comes to mind is the one \
everyone makes, so at least two must surprise. Pick the one that most physically acts out their words, uses their \
space (their body, their room, their light) and would stop someone scrolling, and say why in a line. Never a UI \
card, notification, app screen, chart, counter widget, progress bar, lower third or infographic unless they asked \
for one: those are templates. Numbers and words can be in it as designed things in their world (huge, made of \
something, lit by their light, touching them), never inside a widget.
- Write the composition to scene.html (the Write tool; the Edit tool for changes).
- Real things are painted, never drawn: people, crowds, hands, animals, objects, plants, food, buildings, weather \
and worlds. A hand-drawn SVG version of one (a silhouette, a blob, an outline, an emoji-like shape) is clip art \
however it is styled. Code draws only what is truly graphic: lines, type, light, particles, masks, graphic marks.
- To paint: `bash paint <name> "<what, its look, its light>"` (e.g. \
`bash paint wing_left "a huge white angel wing seen from behind, spread up and out to the left, layered coverts, \
secondaries and long primaries, warm golden rim light from the upper left, soft painterly realism"`). It makes a \
transparent PNG in assets/, painted to match the light and colour of the shot itself (Read it to check it; paint \
again with a better description if it's wrong), used as `<img src="assets/<name>.png">` and animated with \
transforms, masks, filters and GSAP. Paint anything that should look real or rich: wings, clouds, objects, creatures, \
textures, light, a world behind them. Code does the motion, the timing, the type and the graphic shapes; paintings do \
the things. Paint each part that moves on its own separately (a wing, a feather, a cloud), mirror with scaleX(-1) \
where that is enough. Paintings come out cleanly cut out (the background colour they were painted on is fully \
gone, even in soft edges): use them as they are, without colour filters to fix them; grade them only to light them.
- Use the rig kit (`window.rig`) where it fits rather than writing your own: `rig.pin` for anything attached to them \
(it follows their body every frame), `rig.drop` for falls and bounces, `rig.burst` for particles, `rig.spring` and \
`rig.wobble` for overshoot and settle, `rig.blur` for fast moves. It is frame-exact and tested; write your own only \
for what it doesn't do.
- Paint several at once, in one command (they are painted side by side, about 20 seconds for all): `bash paint \
cloud_a "..." cloud_b "..." feather "..."`. Add `--like <name>` to paint them in the same style and light as an \
asset you already have (parts of one thing, a set that belongs together).
- To see it: `bash look 0.8 1.6 2.4` (any seconds into the scene, up to 8) renders those moments of the page \
(their shot, your layers, the person) exactly as the video will show them, and prints the image paths: Read every \
one. To see motion, give a stretch: `bash look 1.2-2.0` makes one strip of 6 frames across it, left \
to right (arcs, spacing, overlap and settling show there, not in single stills). If the scene fails to run, it prints \
the error instead.
- Look at every beat at least once, and at the moments between beats. Be your own harshest critic: does each frame \
act out what is being said at that moment, is it designed and alive (not clip art, not a template, not timid), is \
anything broken, cut off, covering the face, or leftover? Fix and look again. Use as many looks as it takes.
- When you think it's right, show it to the director: `bash review` (it takes 2 to 4 minutes) shows the scene to \
the strict motion director who judges it at the end. It prints a score out of 10, every problem with its time, and \
the strips it judged (Read them). Fix every problem, look to check each fix, and ask again. The director is harsher \
than you: when it and your own eye disagree, it is usually right.
- When the director says it's ready, or after its third review with everything it found fixed as well as you can, \
reply DONE (and nothing else)."""


def _look_script(folder: Path) -> Path:
    import sys

    py = Path(sys.executable).as_posix()
    script = folder / "look"
    script.write_text(f'#!/bin/bash\n"{py}" -m vidosuper.motion.scenes look "{folder.as_posix()}" "$@"\n',
                      encoding="utf-8")
    (folder / "paint").write_text(f'#!/bin/bash\n"{py}" -m vidosuper.motion.scenes paint "{folder.as_posix()}" "$@"\n',
                                  encoding="utf-8")
    (folder / "review").write_text(f'#!/bin/bash\n"{py}" -m vidosuper.motion.scenes review "{folder.as_posix()}"\n',
                                   encoding="utf-8")
    return script


def desk(brief: Brief, video: Path, folder: Path, previous: str | None = None, problems: list[str] | None = None,
         timeout: float = 3600.0) -> str:
    """The scene designed by Claude Code at a desk: the brief and frames as files, `bash look <seconds>` to see its
    work over the footage, as many times as it needs (on the owner's plan)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "frames").mkdir(exist_ok=True)
    texts = []
    for block in _content(brief, None, None):
        if block.get("type") == "text":
            texts.append(block["text"])
    for k, (t, jpg) in enumerate(brief.frames):
        (folder / "frames" / f"frame-{k + 1:02d}-at-{t:05.2f}s.jpg").write_bytes(jpg)
    note = ""
    if previous is not None:
        (folder / "scene.html").write_text(previous, encoding="utf-8")
        note = ("\n\nscene.html already holds your previous version. A strict motion director judged it and found:\n- "
                + "\n- ".join(problems or []) + "\nFix every one of these (look to check each fix), keep what works.")
    (folder / "brief.md").write_text("\n\n".join(texts) + note, encoding="utf-8")
    (folder / "person.json").write_text(json.dumps(brief.track), encoding="utf-8")
    (folder / "brief.json").write_text(json.dumps({k: getattr(brief, k) for k in ("ask", "start", "end", "width",
                                                                                  "height", "fps", "style", "words")}),
                                       encoding="utf-8")  # for its reviews
    _look_script(folder)
    system = (_system().replace("{DURATION}", f"{brief.duration:.2f}").replace("{W}", str(brief.width))
              .replace("{H}", str(brief.height))
              .replace("- Reply with the whole document in one ```html block and nothing after it.",
                       "- Write the whole document to scene.html.")) + "\n\n# Your desk\n" + DESK
    (folder / "system.md").write_text(system, encoding="utf-8")
    cmd = [claude_exe(), "-p", "--output-format", "json", "--model", DESIGN_MODEL, "--system-prompt-file",
           str(folder / "system.md"), "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config",
           "--max-budget-usd", "40", "--tools", "Read,Write,Edit,Bash", "--allowedTools", "Read(./**)", "Write(./**)", "Edit(./**)",
           "Bash(bash look:*)", "Bash(bash paint:*)", "Bash(bash review:*)"]  # its own desk folder only
    env = {**plan_env(), "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "64000", "VIDO_SCENES_VIA": "claude_code",
           "BASH_DEFAULT_TIMEOUT_MS": "600000", "BASH_MAX_TIMEOUT_MS": "900000"}  # the plan; a review takes minutes
    prompt = "Design the scene described in brief.md. Start by reading brief.md and every frame in frames/."
    proc = run_claude(cmd, prompt, folder, cwd=str(folder), timeout=timeout, env=env)
    (folder / "session.json").write_bytes(proc.stdout or b"")
    try:
        said = str(json.loads(proc.stdout.decode("utf-8", errors="replace")).get("result") or "")
    except ValueError:
        said = ""
    if re.search(r"(session|usage|weekly) limit|hit your limit", said, re.I):
        raise SceneError(f"the Claude plan's limit is reached: {said[:160]}")
    html = (folder / "scene.html").read_text(encoding="utf-8") if (folder / "scene.html").exists() else ""
    if "<html" not in html.lower() and "data-composition-id" not in html:
        raise SceneError("the designer left no scene: " + proc.stderr.decode("utf-8", errors="replace")[-600:])
    return html


PAINT_MODEL = "gemini-3-pro-image"


def paint(what: str, out: Path, light: Path | None = None, like: Path | None = None) -> tuple[int, int]:
    """A painted asset of `what`, cut out (transparent PNG). `light`: a frame of the shot, whose light and colour it
    is painted in; `like`: an asset whose style it matches. Returns its size in px."""
    import cv2
    import numpy as np

    # painted on a flat key colour to cut it out: magenta, or green when the thing itself is pink or purple
    green = bool(re.search(r"\b(pink|magenta|purple|violet|fuchsia|lilac|lavender|mauve|plum|rose)\w*", what, re.I))
    which = painter_kind()
    if which == "local":
        img = _paint_local(what, green, light, like, out)
    else:
        img = _paint_gemini(what, green, light, like)
    if img is None:
        raise SceneError("the painter's picture couldn't be read")
    (out.parent / ".raw").mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out.parent / ".raw" / out.name), img)  # as painted: keyed again later without painting again
    rgba = key_out(img, green)
    ys, xs = np.where(rgba[..., 3] > 0.05)
    if ys.size == 0:
        raise SceneError("the painted picture was all background")
    pad = 4
    y0, y1 = max(0, ys.min() - pad), min(img.shape[0], ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(img.shape[1], xs.max() + pad + 1)
    crop = (rgba[y0:y1, x0:x1] * 255).astype(np.uint8)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), crop)
    return crop.shape[1], crop.shape[0]


def painter_kind() -> str:
    """Who paints: "local" (the open model on this machine's GPU, motion/painter.py) or "gemini" (Gemini's image
    model with their key). VIDO_PAINTER chooses; by default the local painter where it can run, else Gemini."""
    from . import painter

    want = os.environ.get("VIDO_PAINTER", "auto").strip().lower()
    if want in ("local", "gemini"):
        return want
    if painter.available():
        return "local"
    from ..music.compose import _load_dotenv

    _load_dotenv()
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return "gemini"
    raise SceneError("nothing here can paint: the local painter needs an NVIDIA GPU or Apple Silicon with the `paint` "
                     "extra installed (pip install -e \".[paint]\"), or add a Gemini key on the Connect page")


def _paint_gemini(what: str, green: bool, light: Path | None, like: Path | None):
    import cv2
    import numpy as np
    from google import genai

    from ..music.compose import _load_dotenv

    _load_dotenv()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise SceneError("painting with Gemini needs GEMINI_API_KEY")
    client = genai.Client(api_key=key)
    from google.genai import types

    key_name = "pure green background (#00FF00)" if green else "pure magenta background (#FF00FF)"
    prompt = (f"{what}. A single isolated element for a motion graphics composite: the whole of it in frame, nothing "
              f"cropped, centred, on a perfectly flat {key_name} with no shadow, gradient or texture on the "
              "background, no ground, no text, no border. Premium, richly lit, crisp edges.")
    parts: list = []
    if light is not None and light.exists():
        parts += [types.Part.from_bytes(data=light.read_bytes(), mime_type="image/jpeg"),
                  "The photo above is the shot this element will be composited into, next to the person in it: paint "
                  "the element lit by the light that falls on the person (the key light on their face: its direction, "
                  "its warmth, how soft it is), not by the colour of the background behind them, so it sits in the "
                  "same light as they do. Do not paint anything from the photo itself."]
    if like is not None and like.exists():
        parts += [types.Part.from_bytes(data=like.read_bytes(), mime_type="image/png"),
                  "The image above is another asset of the same piece: match its painting style, detail and light "
                  "exactly, as a part of the same set."]
    resp = client.models.generate_content(model=PAINT_MODEL, contents=[*parts, prompt])
    for cand in resp.candidates or []:
        for part in (cand.content.parts if cand.content else []) or []:
            if getattr(part, "inline_data", None) is not None and part.inline_data.data:
                return cv2.imdecode(np.frombuffer(part.inline_data.data, np.uint8), cv2.IMREAD_COLOR)
    raise SceneError("the painter sent no picture")


def _paint_local(what: str, green: bool, light: Path | None, like: Path | None, out: Path):
    """The open model on this machine (motion/painter.py): image 1 is the empty key-coloured canvas, then the shot
    whose light it follows and the asset whose style it matches."""
    import cv2

    from . import painter

    colour, rgb = ("pure green (#00FF00)", (0, 255, 0)) if green else ("pure magenta (#FF00FF)", (255, 0, 255))
    refs, notes = [], []
    if light is not None and light.exists():
        refs.append(light)
        notes.append(f"Light it the way the person in image {len(refs) + 1} is lit (the direction, warmth and softness "
                     f"of the key light on their face); paint nothing from image {len(refs) + 1} itself.")
    if like is not None and like.exists():
        flat = out.parent / ".raw" / f".like-{like.stem}.png"  # its transparency on the key colour, as the model sees it
        _on_key(like, rgb, flat)
        refs.append(flat)
        notes.append(f"Match the painting style, detail, palette and light of image {len(refs) + 1} exactly, as a part "
                     "of the same set.")
    prompt = (f"Image 1 is an empty canvas of flat {colour}. Paint on it: {what}. One single isolated element, whole "
              "and centred, nothing cropped, premium and richly lit with crisp edges. Everything around it stays "
              f"perfectly flat {colour}: no shadow, ground, gradient, texture, text or border. " + " ".join(notes))
    raw = out.parent / ".raw" / f".painted-{out.stem}.png"
    try:
        painter.paint(prompt, rgb, refs, raw, seed=int(hashlib.sha1(what.encode()).hexdigest()[:8], 16))
    except painter.PainterError as exc:
        raise SceneError(str(exc)) from exc
    return cv2.imread(str(raw), cv2.IMREAD_COLOR)


def _on_key(png: Path, rgb: tuple[int, int, int], out: Path) -> None:
    """A transparent asset flattened onto the key colour (the local painter's reference images have no alpha)."""
    import cv2
    import numpy as np

    img = cv2.imread(str(png), cv2.IMREAD_UNCHANGED)
    out.parent.mkdir(parents=True, exist_ok=True)
    if img is None:
        return
    if img.ndim == 3 and img.shape[2] == 4:
        a = img[..., 3:4].astype(np.float32) / 255.0
        bg = np.array(rgb[::-1], np.float32)[None, None, :]
        img = (img[..., :3].astype(np.float32) * a + bg * (1 - a)).astype(np.uint8)
    cv2.imwrite(str(out), img)


def key_out(img, green: bool = False):
    """A painting on a flat key colour, cut out: each pixel's share of the key (measured against the key as the
    painter really drew it, the median of the picture's border) is its transparency, and where the thing was blended
    with the key (fluffy, soft, see-through edges) its own colour is taken back out of the mix, so no fringe of the key
    is left. Returns float RGBA (BGR order, 0..1)."""
    import cv2
    import numpy as np

    f = img.astype(np.float32) / 255.0
    edge = np.concatenate([f[:6].reshape(-1, 3), f[-6:].reshape(-1, 3), f[:, :6].reshape(-1, 3), f[:, -6:].reshape(-1, 3)])
    key = np.median(edge, axis=0)  # B, G, R
    b, g, r = f[..., 0], f[..., 1], f[..., 2]
    kb, kg, kr = (float(x) for x in key)
    if green:  # green high, red and blue low
        amount, full = g - np.maximum(r, b), kg - max(kr, kb)
    else:  # magenta: red and blue high, green low
        amount, full = np.minimum(r, b) - g, min(kr, kb) - kg
    share = np.clip(amount / max(0.25, full), 0.0, 1.0)  # how much of the pixel is the key
    alpha = np.clip((1.0 - share - 0.03) / 0.94, 0.0, 1.0)  # a whisper of key: gone; nearly none: solid
    alpha = cv2.GaussianBlur(alpha, (0, 0), 0.5)
    safe = np.maximum(alpha, 0.04)[..., None]
    fg = np.clip((f - (1.0 - alpha)[..., None] * key[None, None, :]) / safe, 0.0, 1.0)  # its own colour, unmixed
    fg[alpha < 0.03] = 0.0
    return np.dstack([fg, alpha])


def paint_command(folder: Path, args: list[str]) -> None:
    """`bash paint <name> "<what>" [<name> "<what>" ...] [--like <name>]`: each painted side by side, in the light of
    the shot (its middle frame), optionally in the style of an asset already on the desk."""
    from concurrent.futures import ThreadPoolExecutor

    like = None
    if "--like" in args:
        k = args.index("--like")
        like = folder / "assets" / f"{re.sub(r'[^a-z0-9_-]', '', args[k + 1].lower())}.png" if k + 1 < len(args) else None
        args = args[:k] + args[k + 2:]
    jobs = [(re.sub(r"[^a-z0-9_-]", "", args[i].lower())[:40] or "asset", args[i + 1]) for i in range(0, len(args) - 1, 2)]
    frames = sorted((folder / "frames").glob("*.jpg"))
    light = frames[len(frames) // 2] if frames else None

    def one(job):
        name, what = job
        out = folder / "assets" / f"{name}.png"
        try:
            w, h = paint(what, out, light, like if like is not None and like.exists() else None)
        except Exception as exc:  # noqa: BLE001 - said to the designer, who draws it another way
            from ..local import health

            health.failed("gemini", exc)
            return f"{name}: PAINTING FAILED: {exc}"
        return f'assets/{name}.png  {w}x{h} px (transparent PNG). Read it to see it; use it as <img src="assets/{name}.png">'

    if not jobs:
        print('Say what to paint: bash paint <name> "<what>" [<name> "<what>" ...]')
        return
    with ThreadPoolExecutor(max_workers=4) as pool:
        for line in pool.map(one, jobs[:8]):
            print(line)


def look_command(folder: Path, asked: list[str]) -> None:
    """`bash look <seconds> ... | <from>-<to>`: those moments of the desk's scene.html with their shot in it, as the
    video will show them (a stretch as one strip of 6 frames across it), printed as image paths."""
    html = (folder / "scene.html").read_text(encoding="utf-8")
    if (folder / "person.json").exists():
        PERSON_TRACK[:] = json.loads((folder / "person.json").read_text(encoding="utf-8"))
    ASSETS[:] = [folder / "assets"]
    STAGE[:] = [folder.parent / "stage"]
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
                print(f"Not a time: {a} (give seconds into the scene, or a stretch like 1.2-2.0)")
    times = sorted(set(singles) | {t for s in strips for t in s})
    if not times:
        return
    try:
        seen = moments(html, folder / ".look", times)
    except SceneError as exc:
        print(f"THE SCENE FAILED TO RUN: {exc}")
        return
    out = folder / "looks"
    out.mkdir(exist_ok=True)
    for t in singles:
        if seen.get(t):
            path = out / f"look-at-{t:05.2f}s.jpg"
            path.write_bytes(seen[t])
            print(path)
    for s in strips:
        jpg = strip([(t, seen.get(t)) for t in s])
        if jpg:
            path = out / f"strip-{s[0]:05.2f}-{s[-1]:05.2f}s.jpg"
            path.write_bytes(jpg)
            print(f"{path}  (6 frames, {s[0]:.2f} s to {s[-1]:.2f} s, left to right)")
    note = person_note(html, "you")
    if note:
        print(note)


def review_command(folder: Path) -> None:
    """`bash review`: the desk's scene.html judged by the director who judges it at the end (`look`), printed: the
    score, every problem, and the strips it judged (kept in reviews/)."""
    html = (folder / "scene.html").read_text(encoding="utf-8") if (folder / "scene.html").exists() else ""
    if not html:
        print("There is no scene.html to review yet.")
        return
    info = json.loads((folder / "brief.json").read_text(encoding="utf-8"))
    brief = Brief(**{**info, "words": [tuple(w) for w in info["words"]]})
    if (folder / "person.json").exists():
        PERSON_TRACK[:] = json.loads((folder / "person.json").read_text(encoding="utf-8"))
    ASSETS[:] = [folder / "assets"]
    STAGE[:] = [folder.parent / "stage"]
    out = folder / "reviews"
    out.mkdir(exist_ok=True)
    n = len(list(out.glob("review-*.md"))) + 1
    try:
        seen = look(brief, html, None, folder / ".review" / str(n))
    except SceneError as exc:
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
    (out / f"review-{n}.json").write_text(json.dumps({"scene": _digest(html), "score": seen["score"], "ok": seen["ok"],
                                                      "problems": seen["problems"]}), encoding="utf-8")
    print(said)
    print("\nThe strips it judged (4 frames around each key moment, left to right):\n" + "\n".join(strips))


def _digest(html: str) -> str:
    return hashlib.sha1(html.encode("utf-8")).hexdigest()


def last_review(folder: Path, html: str) -> dict | None:
    """The desk's own review of exactly this scene, if the director judged it there: {score, ok, problems, sheet}."""
    reviews = sorted((folder / "reviews").glob("review-*.json"), key=lambda p: int(p.stem.split("-")[1]))
    for path in reversed(reviews):
        seen = json.loads(path.read_text(encoding="utf-8"))
        if seen.get("scene") != _digest(html):
            continue
        n = path.stem.split("-")[1]
        sheet = []
        for jpg in sorted(path.parent.glob(f"review-{n}-around-*.jpg")):
            m = re.search(r"around-([\d.]+)s", jpg.name)
            if m:
                sheet.append((float(m.group(1)), jpg.read_bytes()))
        return {"score": seen.get("score", 0), "ok": bool(seen.get("ok")), "problems": seen.get("problems") or [],
                "sheet": sheet}
    return None


def moments(html: str, folder: Path, times: list[float]) -> dict[float, bytes]:
    """The scene at these times as the video will show them (their shot in the page), as JPEGs."""
    import cv2

    shots = stills(project(html, folder / "page"), times, folder / "stills")
    seen = {}
    for t, png in shots.items():
        img = cv2.imread(str(png), cv2.IMREAD_COLOR)
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82]) if img is not None else (False, None)
        if ok:
            seen[t] = jpg.tobytes()
    return seen


def person_note(html: str, to: str) -> str:
    """What these looks can't show yet about the person's cut-out, said plainly (`to` "you": the designer; else the
    reviewer); empty when they show it as the video will."""
    if not has_behind(html) or not STAGE:
        return ""
    you = to == "you"
    if not (STAGE[0] / stage.PERSON).exists():
        return ("(The person's cut-out isn't ready yet, so the behind layer shows over them in these frames; "
                + ("in the next looks they are in front of it.)" if you else
                   "in the video they are in front of it, so don't count that.)"))
    if not stage.clean(STAGE[0]):
        return ("(The person's cut-out here is the quick one: rough edges or halos right at their outline are clean in "
                "the video" + (".)" if you else ", so don't count them.)"))
    return ""


def strip(frames: list[tuple[float, bytes | None]], height: int = 480) -> bytes | None:
    """Frames side by side, each labelled with its time: motion seen at a glance."""
    import cv2
    import numpy as np

    tiles = []
    for t, jpg in frames:
        if not jpg:
            continue
        img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        img = cv2.resize(img, (int(img.shape[1] * height / img.shape[0]), height), interpolation=cv2.INTER_AREA)
        cv2.rectangle(img, (0, 0), (92, 28), (0, 0, 0), -1)
        cv2.putText(img, f"{t:.2f}s", (6, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(img)
        tiles.append(np.full((height, 4, 3), 20, np.uint8))
    if not tiles:
        return None
    ok, out = cv2.imencode(".jpg", np.hstack(tiles[:-1]), [cv2.IMWRITE_JPEG_QUALITY, 84])
    return out.tobytes() if ok else None


# ---------------------------------------------------------------- 3. the project, the stills, the render

PERSON_TRACK: list[dict] = []  # the brief's track, put into every composition this process builds


ASSETS: list[Path] = []  # the desk's painted assets folder, copied into every composition this process builds
STAGE: list[Path] = []  # the scene's shot and person (motion/stage.py), put into every composition this process builds


def project(html: str, folder: Path) -> Path:
    """A render folder for the scene: its document with GSAP and the rigs loaded first and, when the scene has its
    shot (STAGE), the shot and the person put into #stage (motion/stage.py)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "gsap").mkdir(exist_ok=True)
    for src in ASSETS:  # its painted assets beside it
        if src.exists():
            shutil.copytree(src, folder / "assets", dirs_exist_ok=True)
    for name in GSAP_FILES:
        src = GSAP_DIR / name
        if src.exists() and not (folder / "gsap" / name).exists():
            shutil.copyfile(src, folder / "gsap" / name)
    libs = "".join(f'<script src="gsap/{n}"></script>' for n in GSAP_FILES if (GSAP_DIR / n).exists())
    libs += "<script>gsap.registerPlugin(...[" + ", ".join(f"window.{p}" for p in PLUGINS) + "].filter(Boolean));</script>"
    if RIG.exists():  # the designer's rigs (window.rig)
        shutil.copyfile(RIG, folder / "rig.js")
    doc = html
    if STAGE and (STAGE[0] / stage.SHOT).exists():  # their shot, and the person when something goes behind them
        person = (STAGE[0] / stage.PERSON).exists() and has_behind(doc)
        doc = stage.place(doc, person)
        for name in (stage.SHOT, stage.PERSON) if person else (stage.SHOT,):
            stage.link(STAGE[0] / name, folder / "assets" / name)
    if PERSON_TRACK:  # the person's track, for anything pinned to them
        libs += "<script>window.PERSON = " + json.dumps(PERSON_TRACK) + ";</script>"
    if RIG.exists():
        libs += '<script src="rig.js"></script>'
    if re.search(r"<head[^>]*>", doc, re.I):
        doc = re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + libs, doc, count=1, flags=re.I)
    else:
        doc = libs + doc
    (folder / "index.html").write_text(doc, encoding="utf-8")
    return folder


def _hf() -> list[str]:
    from . import engine

    return engine()


def stills(folder: Path, times: list[float], out: Path, timeout: float = 240.0) -> dict[float, Path]:
    """The scene's frames at these times, PNGs (HyperFrames snapshot)."""
    folder, out = Path(folder).resolve(), Path(out).resolve()
    shutil.rmtree(out, ignore_errors=True)
    env = {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1"}
    at = ",".join(f"{t:.3f}" for t in times)
    proc = subprocess.run([*_hf(), "snapshot", str(folder), "--at", at, "--no-end", "--json", "--describe", "false",
                           "-o", str(out)], capture_output=True, timeout=timeout, env=env, cwd=str(folder))
    shots = sorted(out.glob("frame-*.png"))
    if proc.returncode != 0 or not shots:
        tail = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")[-1200:]
        raise SceneError(f"the scene didn't draw: {tail}")
    return {t: p for t, p in zip(times, shots)}


def render_workers() -> int:
    """Render browsers the free memory holds: each takes up to about 2 GB with the shot's frames, and room is left
    for everything else on the machine (left to itself the renderer took 15 browsers and starved a 16 GB machine,
    no faster than 2)."""
    try:
        import psutil
    except ImportError:
        return 2
    free = psutil.virtual_memory().available / 1024 ** 3
    return max(1, min(6, int((free - 2.5) // 2)))


def render(folder: Path, out: Path, fps: int = 30, timeout: float = 3600.0) -> Path:
    """The scene for real, frame by frame, its shot and all: an mp4 (the shot's frames are read as PNGs, so it
    keeps their colour)."""
    folder, out = Path(folder).resolve(), Path(out).resolve()  # it runs in the folder: relative paths would double
    env = {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1"}
    part = out.with_name(out.stem + ".part.mp4")
    proc = subprocess.run([*_hf(), "render", str(folder), "--format", "mp4", "--crf", "10", "--video-frame-format",
                           "png", "--fps", str(fps), "--workers", str(render_workers()), "-o", str(part), "--quiet"],
                          capture_output=True, timeout=timeout, env=env, cwd=str(folder))
    if proc.returncode != 0 or not part.exists():
        tail = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")[-1200:]
        raise SceneError(f"the scene couldn't be rendered: {tail}")
    part.replace(out)
    return out


def has_behind(html: str) -> bool:
    return bool(re.search(r"class=[\"'][^\"']*\bbehind\b", html))


def beats_of(html: str, duration: float) -> list[dict]:
    m = re.search(r'<script[^>]*id=["\']beats["\'][^>]*>(.*?)</script>', html, re.S)
    try:
        beats = json.loads(m.group(1)) if m else []
    except ValueError:
        beats = []
    out = [b for b in beats if isinstance(b, dict) and 0 <= float(b.get("t", -1)) <= duration]
    return sorted(out, key=lambda b: float(b["t"]))[:24]


def sounds_of(html: str, duration: float) -> list[dict]:
    m = re.search(r'<script[^>]*id=["\']sound["\'][^>]*>(.*?)</script>', html, re.S)
    try:
        cues = json.loads(m.group(1)) if m else []
    except ValueError:
        cues = []
    return [c for c in cues if isinstance(c, dict) and c.get("sound") and 0 <= float(c.get("t", -1)) <= duration][:40]


# ---------------------------------------------------------------- 4. how it looks over the footage, judged

REVIEW = """You are a strict motion director reviewing a motion designer's scene in a real person's video (their own \
shot with the design in it). For each of its key moments you see a strip of 4 frames around it (left to right, times \
on them), with what is being said then and what the designer meant to show. Judge the motion across each strip as \
well as each frame: arcs, weight, spacing (fast moves far apart, settling close together), overlap and settling, \
things attached to the person staying attached. Judge like the best director you know:
1. The idea: is it a fresh, physical idea a top studio would make for these words, or a template (a UI card or \
notification, an app screen, a counter widget, a progress bar, a chart, a stock icon, an infographic, a lower \
third)? A template idea scores 4 at most however polished it is, and then the first problem names a better idea.
2. Does each frame act out what is being said, at that moment, as described (the right object, colour, place, \
motion state)? Something said but not shown, or shown wrong, is a problem.
3. Is it designed: a coherent look, real materials and light, intentional composition? Anything that looks like \
clip art, a default font with no care, or random placement is a problem. Real things (people, a crowd, animals, \
objects, weather) drawn as flat vector shapes or silhouettes are clip art: say they must be painted.
4. Is anything broken: missing or invisible elements, things cut off at the frame's edge, overlaps that hide what \
matters, text you can't read, the person's face covered without reason, ugly artifacts, halos or holes around the \
person's cut-out?
5. Does it belong to the shot: its light from the same side and as warm as the footage's (glows that light what is \
near them, shadows that darken it), contact shadows where things touch, scale right against the person, edges and \
grain that sit in the footage rather than pasted over it?
Reply with JSON only: {"score": 0-10 (10 = gallery-grade), "ok": true when it is ready to show (score >= 8 and no \
serious problem), "problems": ["at 7.2 s: the ball is still in the air but he says it hits the ground: land it on \
'hits' and squash it", ...]} — each problem concrete and fixable, with its time."""


def look(brief: Brief, html: str, video: Path, work: Path, client=None, spend=None) -> dict:
    """The scene around its beats over the footage (a strip of frames at each), judged. {score, ok, problems, sheet}."""
    client = client or _client()
    beats = beats_of(html, brief.duration)
    keys = sorted({round(min(brief.duration - 0.05, float(b["t"]) + 0.2), 2) for b in beats})
    if len(keys) < 3:
        keys = sorted(set(keys) | {round(brief.duration * f, 2) for f in (0.2, 0.5, 0.8)})
    keys = keys[:8]
    around = {k: [round(min(brief.duration - 0.03, max(0.0, k + d)), 2) for d in (-0.3, -0.05, 0.2, 0.5)] for k in keys}
    times = sorted({t for ts in around.values() for t in ts})
    seen = moments(html, work, times)
    note = person_note(html, "reviewer")
    content: list[dict] = [{"type": "text", "text": f"What they asked for: {brief.ask}"
                            + (f"\nStyle: {brief.style}" if brief.style else "") + (f"\n{note}" if note else "")}]
    sheet = []
    for k in keys:
        jpg = strip([(t, seen.get(t)) for t in around[k]], 360)
        if jpg is None:
            continue
        sheet.append((k, jpg))
        said = " ".join(w for a, b, w in brief.words if a <= k + 0.6 and b >= k - 1.8)
        meant = "; ".join(str(b.get("what", "")) for b in beats if abs(float(b["t"]) + 0.2 - k) < 0.05)
        content.append({"type": "text", "text": f"Around {k:.2f} s ({around[k][0]:.2f} to {around[k][-1]:.2f} s). "
                        f"Being said: \"{said}\". The designer meant: {meant or '(between beats)'}"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(jpg).decode("ascii")}})
    reply = _ask(client, REVIEW, content, 16000, spend, "scene-review", "high")
    m = re.search(r"\{.*\}", reply, re.S)
    try:
        verdict = json.loads(m.group(0)) if m else {}
    except ValueError:
        verdict = {}
    return {"score": verdict.get("score", 0), "ok": bool(verdict.get("ok")), "problems": verdict.get("problems") or [],
            "sheet": sheet}


# ---------------------------------------------------------------- 5. sound made to each cue

def sound_for(description: str, seconds: float, library: Path) -> Path | None:
    """A sound made to its description (ElevenLabs sound generation), kept by its words."""
    import urllib.request

    from ..music.compose import _load_dotenv

    _load_dotenv()
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    name = hashlib.sha1(f"{description}|{seconds:.1f}".encode()).hexdigest()[:16]
    path = library / "made" / f"{name}.wav"
    if path.exists() and path.stat().st_size > 1000:
        return path
    if not key:
        return None
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/sound-generation",
        data=json.dumps({"text": f"{description}, clean, no music, no voice", "duration_seconds": max(0.5, min(4.0, seconds)),
                         "prompt_influence": 0.6}).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            audio = r.read()
    except Exception as exc:  # noqa: BLE001 - that cue stays silent (and the reason is noted)
        from ..local import health

        health.failed("elevenlabs", exc)
        return None
    if len(audio) < 1000:
        return None
    from ..ffmpeg import ffmpeg_bin

    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.with_suffix(".mp3")
    raw.write_bytes(audio)
    done = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(raw), "-ar", "48000", "-ac", "2", str(path)],
                          capture_output=True)
    raw.unlink(missing_ok=True)
    return path if done.returncode == 0 and path.exists() else None


# ---------------------------------------------------------------- 6. laid on the footage

MATTE_EVERY = 4  # on a CPU, frames between the clean cut-outs; the frames between follow the picture's own motion


def _scene_frames(video: Path, start: float, end: float):
    """(index, frame) through [start, end) of the video, in order, and its fps."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
    n = int(round((end - start) * fps))

    def gen():
        try:
            for i in range(n):
                ok, frame = cap.read()
                if not ok:
                    return
                yield i, frame
        finally:
            cap.release()
    return gen(), fps, n


def matte_keys(video: Path, start: float, end: float, folder: Path, every: int | None = None) -> int:
    """The clean cut-out (BiRefNet) of every `every`-th frame of the scene (on a GPU every frame, on a CPU every
    MATTE_EVERY-th), kept as PNGs in `folder` (made once: a scene's looks, its review and its render all use them).
    Returns how many there are."""
    import cv2
    import numpy as np

    from ..graphics import birefnet

    if not birefnet.available():
        return 0
    every = every or (1 if birefnet.on_gpu() else MATTE_EVERY)
    folder.mkdir(parents=True, exist_ok=True)
    frames, fps, n = _scene_frames(video, start, end)
    (folder / "meta.json").write_text(json.dumps({"fps": fps, "every": every, "n": n, "start": start}), encoding="utf-8")
    made = 0
    for i, frame in frames:
        if i % every and i != n - 1:
            continue
        path = folder / f"k{i:05d}.png"
        if not path.exists():
            m = birefnet.person_matte(np.ascontiguousarray(frame[..., ::-1]))
            cv2.imwrite(str(path.with_suffix(".part.png")), (np.clip(m, 0, 1) * 255).astype(np.uint8))
            path.with_suffix(".part.png").replace(path)
        made += 1
    (folder / "done").write_text("1", encoding="utf-8")
    return made


def _flow_to(src_grey, dst_grey, matte):
    """`matte` (aligned with the frame of `src_grey`) moved to the frame of `dst_grey` along the picture's motion."""
    import cv2
    import numpy as np

    h, w = matte.shape
    small = (w // 2, h // 2)
    a = cv2.resize(dst_grey, small, interpolation=cv2.INTER_AREA)
    b = cv2.resize(src_grey, small, interpolation=cv2.INTER_AREA)
    flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM).calc(a, b, None)  # each dst pixel: where in src
    flow = cv2.resize(flow, (w, h), interpolation=cv2.INTER_LINEAR) * 2.0
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    return cv2.remap(matte, gx + flow[..., 0], gy + flow[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _between(i: int, frame, keys: dict, greys: dict, matte_of) -> "np.ndarray":
    """The matte of frame i: its own clean cut-out if it is a key, else the two nearest keys' moved along the
    picture's motion to this frame and blended by nearness, snapped to its own edges."""
    import cv2

    from ..explainer import cutout

    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if i in keys:
        return matte_of(i)
    below = [k for k in keys if k < i]
    above = [k for k in keys if k > i]
    a = max(below) if below else None
    b = min(above) if above else None
    parts = []
    for k in (a, b):
        if k is not None and k in greys:
            parts.append((abs(i - k), _flow_to(greys[k], grey, matte_of(k))))
    if not parts:
        return matte_of(a if a is not None else b)
    if len(parts) == 1:
        m = parts[0][1]
    else:
        (da, ma), (db, mb) = parts
        m = (ma * db + mb * da) / max(1, da + db)
    return cutout.guided(grey.astype("float32") / 255.0, m.astype("float32"))


def matte_video(video: Path, start: float, end: float, out: Path) -> Path | None:
    """The person's matte for [start, end) of the video, as a grey video (None: no matte model here)."""
    import cv2

    from ..ffmpeg import ffmpeg_bin
    from ..render.behind import people_matte

    from ..graphics import birefnet

    find = people_matte()
    if find is None and not birefnet.available():
        return None
    if birefnet.available():  # the clean cut-out every few frames, motion-tracked between, snapped to each frame's edges
        return _matte_birefnet(video, start, end, out)
    cap = cv2.VideoCapture(str(video))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
    enc = subprocess.Popen([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}",
                            "-r", f"{fps}", "-i", "-", "-c:v", "ffv1", "-pix_fmt", "gray", str(out)], stdin=subprocess.PIPE)
    import numpy as np

    prev = None
    for _ in range(int(round((end - start) * fps))):
        ok, frame = cap.read()
        if not ok:
            break
        m = find(np.ascontiguousarray(frame[..., ::-1])).astype(np.float32)
        if prev is not None:  # steadied over time: no flicker at the edges
            m = 0.6 * m + 0.4 * prev
        prev = m
        enc.stdin.write((np.clip(m, 0, 1) * 255).astype(np.uint8).tobytes())
    cap.release()
    enc.stdin.close()
    enc.wait()
    return out if out.exists() else None


def _matte_birefnet(video: Path, start: float, end: float, out: Path, every: int | None = None) -> Path | None:
    """The person's matte, frame by frame: the scene's clean cut-outs (made now if they aren't yet) and the frames
    between them moved along the picture's own motion, snapped to each frame's edges."""
    import cv2
    import numpy as np

    from ..ffmpeg import ffmpeg_bin

    keys_dir = out.parent / "mattes"
    if not (keys_dir / "done").exists():
        matte_keys(video, start, end, keys_dir, every)
    have = {int(p.stem[1:]): p for p in keys_dir.glob("k*.png") if ".part" not in p.name}
    if not have:
        return None
    every = int(json.loads((keys_dir / "meta.json").read_text(encoding="utf-8"))["every"])

    def matte_of(k):
        return cv2.imread(str(have[k]), cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0

    frames, fps, n = _scene_frames(video, start, end)
    cap = cv2.VideoCapture(str(video))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    greys: dict[int, np.ndarray] = {}
    pending: list[tuple[int, np.ndarray]] = []  # frames waiting for the key after them
    enc = subprocess.Popen([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}",
                            "-r", f"{fps}", "-i", "-", "-c:v", "ffv1", "-pix_fmt", "gray", str(out)], stdin=subprocess.PIPE)

    def write(i, frame):
        m = _between(i, frame, set(have), greys, matte_of)
        enc.stdin.write((np.clip(m, 0, 1) * 255).astype(np.uint8).tobytes())

    for i, frame in frames:
        if i in have:
            greys[i] = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            for j, f in pending:  # the frames before this key: now both of their keys are known
                write(j, f)
            pending.clear()
            write(i, frame)
            for k in [k for k in greys if k < i - 2 * every]:  # only the near keys are kept
                del greys[k]
        else:
            pending.append((i, frame))
    for j, f in pending:
        write(j, f)
    enc.stdin.close()
    enc.wait()
    return out if out.exists() else None


def finish(video: Path, start: float, end: float, picture: Path, cues: list[dict], library: Path, out: Path) -> Path:
    """The scene's picture (its render: the shot with the design in it) with the person's own sound of [start, end)
    under it and the scene's sounds, each made to its cue."""
    from ..ffmpeg import ffmpeg_bin

    dur = end - start
    args = [ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(picture), "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
            "-i", str(video)]
    n = 2
    graph = []
    mix = ["[1:a]"]
    for k, c in enumerate(cues):
        path = sound_for(str(c["sound"]), float(c.get("seconds", 1.2)), library)
        if path is None:
            continue
        args += ["-i", str(path)]
        ms = int(float(c["t"]) * 1000)
        gain = max(-30.0, min(-4.0, float(c.get("gain", -14))))
        graph.append(f"[{n}:a]adelay={ms}|{ms},volume={gain}dB[s{k}]")
        mix.append(f"[s{k}]")
        n += 1
    graph.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first[a]")
    args += ["-filter_complex", ";".join(graph), "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a",
             "192k", str(out)]
    done = subprocess.run(args, capture_output=True)
    if done.returncode != 0:
        raise SceneError("laying the scene on the footage failed: " + done.stderr.decode(errors="replace")[-800:])
    return out


# ---------------------------------------------------------------- the whole loop

def make(video: Path, words: list[tuple[float, float, str]], start: float, end: float, ask: str, work: Path,
         style: str = "", rounds: int = 3, library: Path | None = None, log=print, spend=None) -> dict:
    """Design, look, fix (up to `rounds` designs), then render and lay the scene on the footage. Returns the files,
    the scores of each round and the problems found."""
    work.mkdir(parents=True, exist_ok=True)
    client = _client()
    brief = gather(video, words, start, end, ask, style)
    PERSON_TRACK[:] = brief.track
    log(f"brief: {brief.duration:.1f} s, {len(brief.words)} words, {len(brief.frames)} frames, person in {len(brief.people)}")
    # their shot, put into the scene's page, and the person cut out of it while the design is being written (the
    # looks have the quick cut-out until the clean one replaces it), in a process of its own
    shot_dir = work / "stage"
    stage.cut_shot(video, start, end, shot_dir / stage.SHOT)
    STAGE[:] = [shot_dir]
    cutting = stage.start_cut(shot_dir)
    history = []
    html, problems = None, None
    best = None
    for r in range(1, rounds + 1):
        t0 = time.monotonic()
        try:
            if VIA == "claude_code":  # at the desk: it looks at its own work as it builds
                prev_assets = work / f"desk-r{r - 1}" / "assets"
                if prev_assets.exists():  # what it painted before stays on the desk
                    shutil.copytree(prev_assets, work / f"desk-r{r}" / "assets", dirs_exist_ok=True)
                html = desk(brief, video, work / f"desk-r{r}", html, problems)
                ASSETS[:] = [work / f"desk-r{r}" / "assets"]
            else:
                html = write(brief, client, html, problems, spend)
        except SceneError as exc:
            log(f"round {r}: {exc}")
            (work / f"reply-r{r}.txt").write_text(f"stop: {LAST.get('stop')} tokens: {LAST.get('tokens')}\n\n"
                                                  + str(LAST.get("text", "")), encoding="utf-8")
            continue
        (work / f"scene-r{r}.html").write_text(html, encoding="utf-8")
        seen = last_review(work / f"desk-r{r}", html) if VIA == "claude_code" else None  # judged at the desk already
        try:
            seen = seen or look(brief, html, video, work / f"r{r}", client, spend)
        except SceneError as exc:  # it doesn't draw: the error is the critique
            seen = {"score": 0, "ok": False, "problems": [f"the scene fails to run: {exc}"], "sheet": []}
        for t, jpg in seen["sheet"]:
            (work / f"r{r}").mkdir(exist_ok=True)
            (work / f"r{r}" / f"look-{t:05.2f}.jpg").write_bytes(jpg)
        history.append({"round": r, "score": seen["score"], "ok": seen["ok"], "problems": seen["problems"],
                        "seconds": round(time.monotonic() - t0, 1)})
        log(f"round {r}: score {seen['score']}, {len(seen['problems'])} problems ({time.monotonic() - t0:.0f} s)")
        for p in seen["problems"][:8]:
            log(f"   - {p}".encode("ascii", "replace").decode("ascii"))
        if best is None or float(seen["score"] or 0) >= float(best[1] or 0):
            best = (html, seen["score"])
        if seen["ok"]:
            break
        problems = seen["problems"]
    if best is None:
        raise SceneError("no scene could be designed")
    html = best[0]
    (work / "scene.html").write_text(html, encoding="utf-8")
    behind = has_behind(html)
    if behind and cutting.poll() is None:  # the clean cut-out first: it and the render's browsers never share the memory
        t0 = time.monotonic()
        cutting.wait()
        log(f"the cut-out finished ({time.monotonic() - t0:.0f} s more)")
    elif not behind and cutting.poll() is None:  # nothing goes behind them: the cut-out isn't needed
        cutting.terminate()
    if behind and not (shot_dir / stage.PERSON).exists():
        raise SceneError("the person couldn't be cut out of the shot, so nothing can go behind them")
    log("rendering")
    t0 = time.monotonic()
    picture = render(project(html, work / "render"), work / "picture.mp4", brief.fps)
    log(f"rendered in {time.monotonic() - t0:.0f} s")
    t0 = time.monotonic()
    cues = sounds_of(html, brief.duration)
    library = library or (Path(os.environ.get("LOCALAPPDATA", str(work))) / "VidoSuper" / "sfx")
    out = finish(video, start, end, picture, cues, library, work / "scene.mp4")
    log(f"its sound laid in {time.monotonic() - t0:.0f} s; done: {out}")
    return {"video": out, "html": work / "scene.html", "rounds": history, "sounds": len(cues)}


if __name__ == "__main__":  # `python -m vidosuper.motion.scenes look <folder> <seconds>...` (or paint)
    import sys

    if len(sys.argv) >= 3 and sys.argv[1] == "look":
        look_command(Path(sys.argv[2]), sys.argv[3:])
    elif len(sys.argv) >= 3 and sys.argv[1] == "review":
        review_command(Path(sys.argv[2]))
    elif len(sys.argv) >= 5 and sys.argv[1] == "paint":
        paint_command(Path(sys.argv[2]), sys.argv[3:])
