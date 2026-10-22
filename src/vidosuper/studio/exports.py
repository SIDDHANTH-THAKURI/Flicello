"""Where a film goes and what it leaves as: the platforms (their shape and the length that does best there), said once
("make it for Instagram") and remembered for the next films; and the files a version exports to, made from the version
as rendered (no new render):
- captions as an .srt file (the words said in the film, phrase by phrase: for YouTube, LinkedIn, an editor);
- a 4:5 feed cut and a 1:1 square cut;
- a ProRes 422 HQ master (.mov) for an editor's timeline;
- a looping GIF of the opening, a thumbnail (the frame where the title has landed) and the sound alone (.mp3)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..ffmpeg import ffmpeg_bin, ffprobe_json
from ..schemas import EDL

PLATFORMS = {
    "instagram": {"name": "Instagram Reels", "shape": "9:16", "best": (7, 90)},
    "tiktok": {"name": "TikTok", "shape": "9:16", "best": (7, 60)},
    "shorts": {"name": "YouTube Shorts", "shape": "9:16", "best": (15, 60)},
    "youtube": {"name": "YouTube", "shape": "16:9", "best": (60, 900)},
}
WORDS = [(r"\b(?:yt\s+)?shorts\b", "shorts"), (r"\binsta(?:gram)?\b|\breels?\b|\big\b", "instagram"),
         (r"\btik\s?tok\b", "tiktok"), (r"\byou\s?tube\b|\byt\b", "youtube")]
FOR = re.compile(r"\b(?:for|on|to|post(?:ing)?\s+(?:it\s+)?(?:on|to))\b", re.I)

KINDS = {
    "srt": ("Captions (.srt)", "srt"), "feed": ("4:5 feed cut", "mp4"), "square": ("1:1 square cut", "mp4"),
    "prores": ("ProRes master (.mov)", "mov"), "gif": ("Looping GIF", "gif"), "thumb": ("Thumbnail", "jpg"),
    "mp3": ("Sound only (.mp3)", "mp3"),
    "fcpxml": ("Final Cut / Resolve (.fcpxml)", "fcpxml"), "edl": ("Premiere / Avid (.edl)", "edl"),
}


def read_platform(text: str) -> str | None:
    """The platform their words send the film to ('make it for Instagram', 'posting on TikTok'), or None."""
    if not FOR.search(text):
        return None
    for pattern, pid in WORDS:
        if re.search(pattern, text, re.I):
            return pid
    return None


def srt(edl: EDL, words: list | None = None) -> str:
    """The words said in the film as subtitles: up to seven words a line, broken at a pause or a sentence's end."""
    said = words if words is not None else [w for g in edl.motion if g.kind == "captions" for w in g.words]
    lines: list[list] = []
    for w in sorted(said, key=lambda x: x.s):
        cur = lines[-1] if lines else None
        if not cur or len(cur) >= 7 or w.s - cur[-1].e > 0.6 or re.search(r"[.!?]$", cur[-1].w):
            lines.append([w])
        else:
            cur.append(w)

    def clock(t: float) -> str:
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    out = []
    for k, line in enumerate(lines, start=1):
        end = min(line[-1].e + 0.4, lines[k][0].s if k < len(lines) else line[-1].e + 0.4)
        out.append(f"{k}\n{clock(line[0].s)} --> {clock(end)}\n{' '.join(x.w for x in line)}\n")
    return "\n".join(out)


def make(kind: str, video: Path, edl: EDL, out: Path, words: list | None = None) -> Path:
    """One export of a version's video (`edl`: its edit list, for the captions and the title's moment)."""
    if kind not in KINDS:
        raise ValueError(f"no export {kind!r}")
    out.parent.mkdir(parents=True, exist_ok=True)
    if kind == "srt":
        out.write_text(srt(edl, words), encoding="utf-8")
        return out
    v = next(s for s in ffprobe_json(video)["streams"] if s["codec_type"] == "video")
    w, h = int(v["width"]), int(v["height"])
    ff = [ffmpeg_bin(), "-y", "-loglevel", "error"]
    if kind in ("feed", "square"):
        aw, ah = (4, 5) if kind == "feed" else (1, 1)
        cw, ch = (w, w * ah // aw) if w * ah // aw <= h else (h * aw // ah, h)
        cw, ch = cw // 2 * 2, ch // 2 * 2
        size = (1080, 1350) if kind == "feed" else (1080, 1080)
        cmd = [*ff, "-i", str(video), "-vf", f"crop={cw}:{ch},scale={size[0]}:{size[1]}:flags=lanczos,setsar=1",
               "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy",
               "-movflags", "+faststart", str(out)]
    elif kind == "prores":
        cmd = [*ff, "-i", str(video), "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le",
               "-c:a", "pcm_s16le", str(out)]
    elif kind == "gif":
        cmd = [*ff, "-t", "6", "-i", str(video), "-filter_complex",
               "fps=12,scale=480:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=160[p];[b][p]paletteuse=dither=sierra2_4a",
               "-loop", "0", str(out)]
    elif kind == "thumb":
        title = next((g for g in edl.motion if g.kind == "title"), None)
        at = title.start + 0.8 if title else 1.0
        cmd = [*ff, "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)]
    else:  # mp3
        cmd = [*ff, "-i", str(video), "-vn", "-c:a", "libmp3lame", "-b:a", "256k", str(out)]
    done = subprocess.run(cmd, capture_output=True)
    if done.returncode != 0 or not out.exists():
        raise RuntimeError(f"the {KINDS[kind][0]} could not be made: {done.stderr.decode(errors='replace')[-300:]}")
    return out
