"""A talking or voice film whose motion graphics are designed moment by moment, from zero (no layouts, no blocks).

1. `base` makes the picture the moments are designed on: their own video in the film's frame with their sound (a
   talking film), or an empty frame with their voice (a voice film: every second of its picture is designed).
2. `motion.moments.plan` reads what is said and what they asked for and writes each moment as a contract: BEFORE,
   each CHANGE on its spoken word, AFTER, where, and the words allowed on screen (none unless asked).
3. Each moment goes to the scene designer (`motion.scenes.make`): Claude Code designs it in code at its desk, looks at
   its own frames, and a strict director judges stills at every checkpoint of the contract; it is fixed until they
   match. A moment already designed for the same contract, look and picture is kept, so a change redoes only what
   it touches.
4. `splice_all` lays every moment into the base, frame for frame, in one pass.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..motion import moments as mo

log = logging.getLogger("vidosuper.work")
SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
FPS = 30
AUDIO = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"  # every piece alike
VOID = "0x0b0b10"  # the empty frame of a voice film, before anything is designed on it


def words_of(speech: dict) -> list[tuple[float, float, str]]:
    return sorted((round(float(w["start"]), 3), round(float(w["end"]), 3), str(w["text"]).strip())
                  for u in speech.get("utterances") or [] for w in u.get("words") or [] if str(w.get("text", "")).strip())


def tighten(speech: dict) -> dict:
    """A talking film cut on what is said, the way an editor cuts one: each phrase kept with a breath before its first
    word and a beat after its last; the long pauses and the "um"s on their own between phrases go (studio/speech_cut).
    Returns the speech on the new, shorter timeline: its words moved to where they now fall, `keep` (the stretches of
    the original kept, in seconds) for `base` to cut the picture on, and `tightened` (what went, said back to them).
    Unchanged when there is little to take out (under half a second)."""
    from ..studio import speech_cut

    if not speech.get("video") or speech.get("keep"):
        return speech
    words = words_of(speech)
    said = speech_cut.phrases(words)
    duration = float(speech.get("duration") or 0.0)
    if len(said) < 2 or duration <= 0:
        return speech
    keep: list[list[float]] = []
    for a, b, _ in said:
        lo, hi = max(0.0, a - speech_cut.LEAD), min(duration, b + speech_cut.TAIL)
        if keep and lo <= keep[-1][1] + 0.05:
            keep[-1][1] = max(keep[-1][1], hi)
        else:
            keep.append([lo, hi])
    total = sum(hi - lo for lo, hi in keep)
    if duration - total < 0.5:
        return speech
    moved = []
    for a, b, t in words:
        at = 0.0
        for lo, hi in keep:
            if lo - 1e-6 <= a <= hi + 1e-6:
                moved.append({"start": round(at + max(0.0, a - lo), 3), "end": round(at + min(hi, b) - lo, 3), "text": t})
                break
            at += hi - lo
    fillers = sum(1 for _, _, t in words if re.sub(r"[^a-z]", "", t.lower()) in speech_cut.FILLERS) -         sum(1 for w in moved if re.sub(r"[^a-z]", "", w["text"].lower()) in speech_cut.FILLERS)
    gaps = len(keep) - 1 + (1 if keep[0][0] > 0.3 else 0) + (1 if duration - keep[-1][1] > 0.3 else 0)
    return {**speech, "duration": round(total, 3), "keep": [[round(lo, 3), round(hi, 3)] for lo, hi in keep],
            "utterances": [{"start": 0.0, "end": round(total, 3), "text": " ".join(w["text"] for w in moved),
                            "words": moved}],
            "tightened": {"pauses": gaps, "fillers": max(0, fillers), "from": round(duration, 2), "to": round(total, 2)}}


def parallel() -> int:
    """Moments designed at once: each is a Claude Code desk and, while it renders, a browser."""
    try:
        return max(1, min(4, int(os.environ.get("VIDO_MOMENTS_PARALLEL", "2"))))
    except ValueError:
        return 2


def base(speech: dict, shape: str, out: Path, bed: Path | None = None) -> Path:
    """The picture the moments are designed on, `shape` framed, at 30 fps: their video (filling the frame) with their
    sound, or for a voice film an empty frame with their voice and the music bed under it."""
    from ..ffmpeg import ffmpeg_bin

    if out.exists() and out.stat().st_size > 0:
        return out
    w, h = SIZES.get(shape, SIZES["9:16"])
    dur = float(speech["duration"])
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.stem + ".part.mp4")
    enc = ["-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart"]
    if speech.get("video") and speech.get("keep"):  # cut on what is said: the kept stretches, end to end
        look = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS},setsar=1"
        parts, labels = [], ""
        for k, (lo, hi) in enumerate(speech["keep"]):
            fade = min(0.012, (hi - lo) / 4)  # no click where a cut lands
            parts.append(f"[0:v:0]trim=start={lo:.3f}:end={hi:.3f},setpts=PTS-STARTPTS,{look}[v{k}]")
            parts.append(f"[0:a:0]atrim=start={lo:.3f}:end={hi:.3f},asetpts=PTS-STARTPTS,{AUDIO},"
                         f"afade=t=in:d={fade:.3f},afade=t=out:st={max(0.0, hi - lo - fade):.3f}:d={fade:.3f}[a{k}]")
            labels += f"[v{k}][a{k}]"
        graph = ";".join(parts) + f";{labels}concat=n={len(speech['keep'])}:v=1:a=1[v][a]"
        cmd = [ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(speech["video"]), "-filter_complex", graph,
               "-map", "[v]", "-map", "[a]", "-ar", "48000", "-ac", "2", *enc, str(part)]
    elif speech.get("video"):
        cmd = [ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(speech["video"]), "-t", f"{dur:.3f}",
               "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS},setsar=1",
               "-map", "0:v:0", "-map", "0:a:0", "-ar", "48000", "-ac", "2", *enc, str(part)]
    else:
        cmd = [ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={VOID}:s={w}x{h}:r={FPS}",
               "-i", str(speech["audio"])]
        if bed is not None and Path(bed).exists():
            cmd += ["-stream_loop", "-1", "-i", str(bed), "-filter_complex",
                    "[2:a]volume=-16dB[b];[1:a][b]amix=inputs=2:normalize=0:duration=first[a]", "-map", "0:v",
                    "-map", "[a]"]
        else:
            cmd += ["-map", "0:v", "-map", "1:a"]
        cmd += ["-t", f"{dur:.3f}", "-ar", "48000", "-ac", "2", *enc, str(part)]
    done = subprocess.run(cmd, capture_output=True)
    if done.returncode != 0 or not part.exists():
        raise RuntimeError("the film's picture couldn't be made: " + done.stderr.decode(errors="replace")[-600:])
    part.replace(out)
    return out


def brand_logo(brand: dict, out: Path) -> Path | None:
    """Their brand kit's logo (a PNG kept in their profile as a data URL) as a file, or None."""
    import base64

    data = str((brand or {}).get("logo") or "")
    if not data.startswith("data:image/png;base64,"):
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(data.split(",", 1)[1]))
    return out


def splice_all(film: Path, scenes: list[tuple[float, float, Path]], out: Path) -> Path:
    """The film with each [start, end) replaced by its designed scene (picture and sound), in one pass."""
    from ..ffmpeg import ffmpeg_bin

    if not scenes:
        import shutil

        shutil.copyfile(film, out)
        return out
    import cv2

    cap = cv2.VideoCapture(str(film))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    parts, labels, t, n = [], [], 0.0, 1
    for start, end, _ in sorted(scenes):
        if start - t > 0.02:
            k = len(labels)
            parts += [f"[0:v]trim={t:.4f}:{start:.4f},setsar=1,setpts=PTS-STARTPTS[v{k}]",
                      f"[0:a]atrim={t:.4f}:{start:.4f},{AUDIO},asetpts=PTS-STARTPTS[a{k}]"]
            labels.append(f"[v{k}][a{k}]")
        k = len(labels)
        parts += [f"[{n}:v]fps={FPS},scale={w}:{h},setsar=1,setpts=PTS-STARTPTS[v{k}]", f"[{n}:a]{AUDIO},asetpts=PTS-STARTPTS[a{k}]"]
        labels.append(f"[v{k}][a{k}]")
        t, n = end, n + 1
    k = len(labels)
    parts += [f"[0:v]trim=start={t:.4f},setsar=1,setpts=PTS-STARTPTS[v{k}]", f"[0:a]atrim=start={t:.4f},{AUDIO},asetpts=PTS-STARTPTS[a{k}]"]
    labels.append(f"[v{k}][a{k}]")
    graph = ";".join(parts) + f";{''.join(labels)}concat=n={len(labels)}:v=1:a=1[v][a]"
    inputs = ["-i", str(film)]
    for _, _, scene in sorted(scenes):
        inputs += ["-i", str(scene)]
    part = out.with_name(out.stem + ".part.mp4")
    done = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[v]",
                           "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(part)], capture_output=True)
    if done.returncode != 0 or not part.exists():
        raise RuntimeError("laying the moments into the film failed: " + done.stderr.decode(errors="replace")[-600:])
    part.replace(out)
    return out


def cut(film: Path, start: float, end: float, out: Path) -> Path:
    """[start, end) of the film on its own (a sample of one moment, with a breath either side)."""
    from ..ffmpeg import ffmpeg_bin

    done = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(film), "-t",
                           f"{end - start:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt",
                           "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)],
                          capture_output=True)
    if done.returncode != 0:
        raise RuntimeError("the sample couldn't be cut: " + done.stderr.decode(errors="replace")[-400:])
    return out


ROUND = re.compile(r"round (\d+): score ([\d.]+), (\d+) problems")


def say(line: str, rounds: int) -> str | None:
    """A line of the designer's log as the one live line the person sees, or None: the director's notes, timings and
    paths stay in the log (one line per note made the step list grow without end)."""
    if line.startswith("brief:"):
        return f"round 1 of {rounds}: designing it and checking its frames"
    m = ROUND.match(line)
    if m:
        r, score, n = int(m[1]), m[2], int(m[3])
        if r >= rounds:
            return f"round {r} of {rounds} scored {score}/10: keeping the best round"
        return f"round {r} of {rounds} scored {score}/10: round {r + 1} fixes {n} thing{'' if n == 1 else 's'}"
    if re.match(r"round \d+: ", line):
        return f"round {line[6:].split(':')[0]} of {rounds} didn't draw; trying again"
    if line == "rendering":
        return "rendering it"
    if line.startswith("its sound laid"):
        return "done"
    return None


def design_one(m: mo.Moment, design: mo.FilmDesign, picture: Path, words, ask: str, kind: str, folder: Path,
               library: Path, rounds: int = 3, progress=lambda msg: None, logo: Path | None = None) -> dict:
    """One moment designed on the picture (or the one already designed for the same contract, kept), in a process
    of its own (the designer keeps the scene it works on in module state). Returns its meta: {digest, start, end,
    video, rounds, score, kept}."""
    import sys

    from ..motion import scenes

    key = mo.digest(m, design, f"{picture.name}:{picture.stat().st_size}"
                    + (f":logo{logo.stat().st_size}" if logo is not None and logo.exists() else ""))
    here = folder / key
    meta_path = here / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if (here / meta["video"]).exists():
            return {**meta, "video": str(here / meta["video"]), "kept": True}
    here.mkdir(parents=True, exist_ok=True)
    (here / "moment.json").write_text(m.model_dump_json(indent=1), encoding="utf-8")
    brief = mo.brief_text(m, design, ask, kind)
    if logo is not None and logo.exists():  # their own logo on the desk (the first round starts from desk-r0's assets)
        import shutil

        (here / "desk-r0" / "assets").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(logo, here / "desk-r0" / "assets" / "logo.png")
        brief += ("\nTheir own logo is on your desk as assets/logo.png (`<img src=\"assets/logo.png\">`). When the "
                  "moment shows their logo, use exactly this picture, never a drawn or painted stand-in.")
    (here / "job.json").write_text(json.dumps({
        "picture": str(picture), "words": words, "start": m.start, "end": m.end, "look": design.look,
        "ask": brief, "rounds": rounds, "library": str(library),
        "spec": mo.checkpoints(m), "text": m.text, "digest": key}), encoding="utf-8")
    env = {**os.environ, "VIDO_SCENES_VIA": scenes.VIA, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen([sys.executable, "-m", "vidosuper.work.moments_film", "one", str(here)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True, encoding="utf-8",
                            errors="replace")
    tail: list[str] = []
    for line in proc.stdout:
        line = line.strip()
        if line:
            tail = (tail + [line])[-12:]
            log.info("%s: %s", here.name, line[:500])
            shown = say(line, rounds)
            if shown:
                progress(shown)
    proc.wait()
    if proc.returncode != 0 or not meta_path.exists():
        raise RuntimeError("the designer stopped: " + " | ".join(tail[-4:])[-500:])
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return {**meta, "video": str(here / meta["video"]), "kept": False}


def _one(here: Path) -> None:
    """`python -m vidosuper.work.moments_film one <folder>`: design the moment in <folder>/job.json."""
    from ..motion import scenes

    job = json.loads((here / "job.json").read_text(encoding="utf-8"))
    result = scenes.make(Path(job["picture"]), [tuple(w) for w in job["words"]], job["start"], job["end"], job["ask"],
                         here, style=job["look"], rounds=job["rounds"], library=Path(job["library"]),
                         log=lambda msg: print(msg, flush=True), spec=job["spec"], text=job["text"])
    score = result["rounds"][-1]["score"] if result["rounds"] else None
    meta = {"digest": job["digest"], "start": job["start"], "end": job["end"], "video": Path(result["video"]).name,
            "rounds": result["rounds"], "score": score}
    (here / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")


def design_all(design: mo.FilmDesign, picture: Path, words, ask: str, kind: str, folder: Path, library: Path,
               only: list[int] | None = None, progress=lambda done, total, msg: None,
               logo: Path | None = None) -> tuple[list[dict], list[str]]:
    """Every moment (or those in `only`), `parallel()` at a time. Returns (made, notes): what was designed, in film
    order, and what couldn't be, said plainly (the film goes on without it)."""
    todo = [k for k in range(len(design.moments)) if only is None or k in only]
    made: dict[int, dict] = {}
    notes: list[str] = []
    done = [0]

    def one(k: int):
        m = design.moments[k]
        tag = f"moment {k + 1} of {len(design.moments)}"
        try:
            meta = design_one(m, design, picture, words, ask, kind, folder, library,
                              progress=lambda msg: progress(done[0], len(todo), f"Designing {tag}: {msg}"),
                              **({"logo": logo} if logo is not None else {}))
            made[k] = meta
        except Exception as exc:  # noqa: BLE001 - one moment failing never loses the others
            log.warning("%s failed", tag, exc_info=True)
            notes.append(f"{tag.capitalize()} ({_mmss(m.start)}) couldn't be designed: {type(exc).__name__}: "
                         f"{str(exc)[:200]}")
        done[0] += 1
        progress(done[0], len(todo), f"Designed {done[0]} of {len(todo)} moments")

    with ThreadPoolExecutor(parallel()) as ex:
        list(ex.map(one, todo))
    return [made[k] for k in sorted(made)], notes


def requirements(design: mo.FilmDesign, made: list[dict], notes: list[str], ask: str) -> list[dict]:
    """The checks shown with the film: every planned moment designed, each matched its checkpoints, no words on
    screen nobody asked for."""
    from ..studio.requirements import _row

    rows = [_row(len(made) == len(design.moments), "every moment you described, designed",
                 f"{len(made)} of {len(design.moments)} moments" + (f"; {notes[0]}" if notes else ""))]
    ready = [m for m in made if m.get("rounds") and m["rounds"][-1].get("ok")]
    rows.append(_row(len(ready) == len(made), "each moment checked against your words, start to end",
                     f"{len(ready)} of {len(made)} passed the director's checks"))
    words_on = sum(1 for m in design.moments if m.text.strip())
    rows.append(_row(words_on == 0 or mo.wants_text(ask), "no words on screen you didn't ask for",
                     "none" if not words_on else f"words in {words_on} moments, as you asked"))
    return rows


def _mmss(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "one":
        _one(Path(sys.argv[2]))
