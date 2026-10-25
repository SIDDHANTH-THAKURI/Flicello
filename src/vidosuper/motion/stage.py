"""The real shot inside the scene's page. A scene is designed, looked at and rendered with the footage itself in it:
the shot as a video at the bottom of #stage, the designer's behind layer over it, the person cut out of the shot over
that, and everything else on top. The browser composites it all, so a glow in `screen` lights the real picture, a
shadow in `multiply` shades it, a backdrop filter sees it, a filter can bend a copy of it, and a camera move on #stage
takes the footage and the graphics together. The render is the finished picture: frame for frame the footage's own
(HyperFrames reads video frames with FFmpeg, as PNGs: under one code value from the source).

The person comes in two passes, both while the design is being written: a quick cut-out first (a few seconds a
second of footage), then the clean one (BiRefNet on its key frames, the picture's own motion between them), which
replaces it when it is made.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

SHOT = "shot.mp4"
PERSON = "person.webm"
COLOUR = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
SHOT_STYLE = "position:absolute;inset:0;width:100%;height:100%;object-fit:cover"


def cut_shot(video: Path, start: float, end: float, out: Path) -> Path:
    """[start, end) of the film as the page's shot: its own size, frame rate and colour, near-lossless, a key frame
    every half second (so any moment of it is found quickly)."""
    from ..ffmpeg import ffmpeg_bin

    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.stem + ".part.mp4")
    done = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(video), "-t",
                           f"{end - start:.3f}", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "8", "-g", "15",
                           *COLOUR, str(part)], capture_output=True)
    if done.returncode != 0 or not part.exists():
        raise RuntimeError("the shot couldn't be cut: " + done.stderr.decode(errors="replace")[-400:])
    part.replace(out)
    return out


def person_clip(shot: Path, matte: Path, out: Path, quick: bool = False) -> Path:
    """The person cut out of the shot: its own pixels with the matte as alpha, as a VP9 WebM (the browser plays it
    and HyperFrames reads its alpha; the same 4:2:0 picture as the shot, so the two agree where they meet)."""
    from ..ffmpeg import ffmpeg_bin

    part = out.with_name(out.stem + ".part" + out.suffix)
    speed = ["-deadline", "realtime", "-cpu-used", "8"] if quick else ["-deadline", "good", "-cpu-used", "4"]
    done = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(shot), "-i", str(matte), "-filter_complex",
                           "[0:v]format=yuva420p[s];[1:v]format=gray[m];[s][m]alphamerge[v]", "-map", "[v]",
                           "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-crf", "12", "-b:v", "0", *speed, "-row-mt",
                           "1", "-auto-alt-ref", "0", *COLOUR, str(part)], capture_output=True)
    if done.returncode != 0 or not part.exists():
        raise RuntimeError("the person couldn't be cut out: " + done.stderr.decode(errors="replace")[-400:])
    part.replace(out)
    return out


def quick_matte(shot: Path, out: Path) -> Path | None:
    """The person's quick matte through the whole shot (MODNet, about 0.2 s a frame, else the segmentation model;
    steadied over time), as a grey video; None: no model here."""
    import cv2
    import numpy as np

    from ..explainer import cutout
    from ..ffmpeg import ffmpeg_bin
    from ..render.behind import people_matte

    find = (lambda rgb: cutout.raw_matte(rgb, full=True)) if cutout.available() else people_matte()
    if find is None:
        return None
    cap = cv2.VideoCapture(str(shot))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    part = out.with_name(out.stem + ".part.mkv")
    enc = subprocess.Popen([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}",
                            "-r", f"{fps}", "-i", "-", "-c:v", "ffv1", "-pix_fmt", "gray", str(part)], stdin=subprocess.PIPE)
    prev = None
    while True:
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
    if not part.exists():
        return None
    part.replace(out)
    return out


def cut(folder: Path) -> None:
    """The person, cut out of the shot in `folder` for the page: the quick cut-out first, then the clean one in its
    place (folder/person.mov, with folder/person.json saying which it is). Both are made from the shot itself, so
    they line up with it frame for frame."""
    import cv2

    from ..graphics import birefnet
    from . import scenes

    shot = folder / SHOT
    better = birefnet.available()  # else the quick cut-out is the best there is
    quick = quick_matte(shot, folder / "quick-matte.mkv")
    if quick is not None:
        person_clip(shot, quick, folder / PERSON, quick=better)
        (folder / "person.json").write_text(json.dumps({"clean": not better, "at": time.time()}), encoding="utf-8")
    if better:
        cap = cv2.VideoCapture(str(shot))
        seconds = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / (cap.get(cv2.CAP_PROP_FPS) or 30)
        cap.release()
        clean = scenes.matte_video(shot, 0.0, seconds, folder / "matte.mkv")
        if clean is not None:
            person_clip(shot, clean, folder / PERSON)
            (folder / "person.json").write_text(json.dumps({"clean": True, "at": time.time()}), encoding="utf-8")
    (folder / "cut.done").write_text("1", encoding="utf-8")


def start_cut(folder: Path) -> subprocess.Popen:
    """`cut` in a process of its own (it holds nothing up in this one); what it says goes to folder/cut.log."""
    with open(folder / "cut.log", "wb") as said:
        return subprocess.Popen([sys.executable, "-m", "vidosuper.motion.stage", "cut", str(folder)],
                                stdout=said, stderr=subprocess.STDOUT)


def clean(folder: Path) -> bool:
    try:
        return bool(json.loads((folder / "person.json").read_text(encoding="utf-8")).get("clean"))
    except (OSError, ValueError):
        return False


def link(src: Path, dst: Path) -> None:
    """`src` at `dst` without copying it (a hard link), or a copy where that can't be."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


class _Find(HTMLParser):
    """Where #stage's opening tag ends and where its `.behind` child closes, as offsets in the document."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self.lines = [0]
        for line in text.splitlines(keepends=True):
            self.lines.append(self.lines[-1] + len(line))
        self.stack: list[tuple[str, bool]] = []  # (tag, is .behind under #stage)
        self.stage_at = self.behind_end = None
        self.stage_depth = None

    def _offset(self) -> int:
        line, col = self.getpos()
        return self.lines[line - 1] + col

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.VOID:
            return
        behind = (self.stage_depth is not None and len(self.stack) == self.stage_depth + 1
                  and "behind" in (a.get("class") or "").split() and self.behind_end is None)
        self.stack.append((tag, behind))
        if a.get("id") == "stage" and self.stage_at is None:
            self.stage_depth = len(self.stack) - 1
            self.stage_at = self._offset() + len(self.get_starttag_text() or "")

    def handle_startendtag(self, tag, attrs):
        if dict(attrs).get("id") == "stage" and self.stage_at is None:
            self.stage_at = self._offset() + len(self.get_starttag_text() or "")

    def handle_endtag(self, tag):
        for k in range(len(self.stack) - 1, -1, -1):  # forgiving: close up to the matching tag
            if self.stack[k][0] == tag:
                if self.stack[k][1]:
                    rest = self.rawdata[self._offset():]
                    close = re.match(r"</\s*" + re.escape(tag) + r"\s*>", rest, re.I)
                    self.behind_end = self._offset() + (close.end() if close else 0)
                del self.stack[k:]
                return


def place(html: str, person: bool) -> str:
    """The document with the shot as the first thing in #stage and (`person`) the person right after its `.behind`
    layer. Unchanged when it has no #stage."""
    found = _Find(html)
    found.feed(html)
    found.close()
    if found.stage_at is None:
        return html
    shot = f'<video id="shot" src="assets/{SHOT}" muted playsinline style="{SHOT_STYLE}"></video>'
    who = f'<video id="person" src="assets/{PERSON}" muted playsinline style="{SHOT_STYLE}"></video>'
    out = html
    if person and found.behind_end is not None:
        out = out[:found.behind_end] + who + out[found.behind_end:]
    return out[:found.stage_at] + shot + out[found.stage_at:]


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "cut":
        cut(Path(sys.argv[2]))
