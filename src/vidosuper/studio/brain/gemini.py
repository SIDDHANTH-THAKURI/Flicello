"""The clip watcher: Gemini watches each clip itself, picture and sound together, and logs it in the same form as the
contact-sheet analyst (ClipAnalysis), so everything after it is unchanged. It sees what stills can't (motion, a camera
that falls, a light turning green) and hears what only sound tells (a laugh, a splash, a line worth keeping).

- **What it gets:** the clip's light copy (preview.py: small, with its sound) and the same brief the analyst reads
  (technical facts, measurements, transcript).
- **How the answer is checked:** the answer is checked like the analyst's (`check_analysis`) and asked again with the
  problems, at most twice.
- **What it costs:** spend is recorded per clip.
- **After the answer:** the upload is deleted at once, so footage never stays with Google.
- **If it fails:** a clip it can't log falls back to the contact-sheet analyst (Pipeline.analyse).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ...schemas import ClipAnalysis, ClipInfo
from .backends import BadReply, BrainError, SpendFn, Usage
from .context import prompt

SHEETS = ("One clip: its brief (technical facts, automatic measurements, a speech transcript) and its contact sheets,\n"
          "images of frames in order. Each frame carries a yellow label with the clip time (m:ss.s); convert it to\n"
          "seconds (1:05.5 -> 65.5). Look at every sheet before you answer.")
WATCH = ("One clip itself, with its sound, and its brief (technical facts, automatic measurements, a speech "
         "transcript): watch and listen to all of it before you answer. Times are seconds from the clip's start, to a "
         "tenth of a second where you can.")


def watcher_prompt() -> str:
    text = prompt("analyst")
    if SHEETS not in text:  # the analyst's instructions changed: say so loudly rather than send stills instructions
        raise BrainError("the analyst prompt's 'What you get' paragraph moved: update gemini.SHEETS")
    return text.replace(SHEETS, WATCH)


def frames_per_second(seconds: float) -> float:
    """How often Gemini looks: closely for short clips (cuts are timed to a tenth of a second), less for long ones."""
    return 2.0 if seconds <= 120 else 1.0 if seconds <= 600 else 0.5


class GeminiWatcher:
    kind = "gemini"

    def __init__(self, model: str, spend: SpendFn, log_dir: Path | None = None, client=None):
        if client is None:
            from google import genai

            key = os.environ.get("GEMINI_API_KEY")
            if not key:
                raise BrainError("No GEMINI_API_KEY for the clip watcher.")
            client = genai.Client(api_key=key)
        self.client = client
        self.model = model
        self.spend = spend
        self.log_dir = log_dir
        self.parallel = 6

    def _upload(self, video: Path):
        f = self.client.files.upload(file=str(video))
        deadline = time.monotonic() + 300
        while getattr(getattr(f, "state", None), "name", "") == "PROCESSING":
            if time.monotonic() > deadline:
                raise BrainError("Gemini took too long to take the clip")
            time.sleep(1.5)
            f = self.client.files.get(name=f.name)
        if getattr(getattr(f, "state", None), "name", "") == "FAILED":
            raise BrainError("Gemini could not read the clip")
        return f

    def analyse_clip(self, clip: ClipInfo, brief: str, video: Path) -> ClipAnalysis:
        from google.genai import types

        from .core import check_analysis

        f = self._upload(video)
        return self._watch(clip, brief, f, types, check_analysis)

    def point_at(self, jpeg: bytes, what: str) -> tuple[float, float] | None:
        """Where `what` ("the Opera House", "the ferry") is in a still: its centre as shares of the width and the height,
        or None when it isn't there. For a callout that follows it."""
        from google.genai import types
        from pydantic import BaseModel

        class Point(BaseModel):
            found: bool
            x: int  # 0-1000 across
            y: int  # 0-1000 down

        config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=Point)
        t0 = time.monotonic()
        resp = self.client.models.generate_content(model=self.model, config=config, contents=[
            types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"),
            f"Find {what} in this picture. Return found=false if it is not clearly in it. Otherwise x and y of the centre "
            "of it, from 0 to 1000 (x across from the left, y down from the top)."])
        u = getattr(resp, "usage_metadata", None)
        self.spend(Usage.priced("point", self.model, "gemini", int(getattr(u, "prompt_token_count", 0) or 0),
                                int((getattr(u, "candidates_token_count", 0) or 0) + (getattr(u, "thoughts_token_count", 0) or 0)),
                                0, 0, time.monotonic() - t0))
        try:
            p = Point.model_validate_json(resp.text or "")
        except ValueError:
            return None
        if not p.found or not (0 <= p.x <= 1000 and 0 <= p.y <= 1000):
            return None
        return p.x / 1000.0, p.y / 1000.0

    def find_box(self, jpeg: bytes, what: str) -> tuple[float, float, float, float] | None:
        """The box round `what` in a still: centre x, y and width, height as shares of the frame (None: not there)."""
        from google.genai import types
        from pydantic import BaseModel

        class Box(BaseModel):
            found: bool
            ymin: int  # 0-1000
            xmin: int
            ymax: int
            xmax: int

        config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=Box)
        t0 = time.monotonic()
        resp = self.client.models.generate_content(model=self.model, config=config, contents=[
            types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"),
            f"Find {what} in this picture. Return found=false if it is not clearly in it. Otherwise its bounding box, "
            "tight round the whole of it, each edge from 0 to 1000 (x from the left, y from the top)."])
        u = getattr(resp, "usage_metadata", None)
        self.spend(Usage.priced("point", self.model, "gemini", int(getattr(u, "prompt_token_count", 0) or 0),
                                int((getattr(u, "candidates_token_count", 0) or 0) + (getattr(u, "thoughts_token_count", 0) or 0)),
                                0, 0, time.monotonic() - t0))
        try:
            b = Box.model_validate_json(resp.text or "")
        except ValueError:
            return None
        if not b.found or b.xmax <= b.xmin or b.ymax <= b.ymin:
            return None
        return ((b.xmin + b.xmax) / 2000, (b.ymin + b.ymax) / 2000, (b.xmax - b.xmin) / 1000, (b.ymax - b.ymin) / 1000)

    def review(self, video: Path, platform: str = "Instagram Reels and TikTok") -> dict | None:
        """A viewer's honest look at a finished film: would it stop the scroll, where would people leave, what is the
        best and the weakest moment (seconds), and why, in one or two plain sentences each."""
        from google.genai import types
        from pydantic import BaseModel

        class Review(BaseModel):
            stop: int  # 0-10: how likely a scrolling viewer stops in the first two seconds
            hold: int  # 0-10: how likely they watch to the end
            leave_at: float  # seconds: where most would scroll away (the end if nowhere)
            best: str
            weakest: str
            why: str

        f = self._upload(video)
        try:
            config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=Review)
            t0 = time.monotonic()
            resp = self.client.models.generate_content(model=self.model, config=config, contents=[
                types.Part(file_data=types.FileData(file_uri=f.uri, mime_type="video/mp4")),
                f"You are a typical viewer scrolling {platform}. Watch this video with its sound and judge it honestly, "
                "as the feed would: stop (0-10) is how likely you stop scrolling in its first two seconds; hold (0-10) "
                "how likely you watch to the end; leave_at the second most people would scroll away (its length if they "
                "wouldn't); best and weakest name the moments with their seconds; why says what would make it travel "
                "further, concretely, in one or two sentences."])
            u = getattr(resp, "usage_metadata", None)
            self.spend(Usage.priced("review", self.model, "gemini", int(getattr(u, "prompt_token_count", 0) or 0),
                                    int((getattr(u, "candidates_token_count", 0) or 0)
                                        + (getattr(u, "thoughts_token_count", 0) or 0)), 0, 0, time.monotonic() - t0))
            r = Review.model_validate_json(resp.text or "")
            return {"stop": max(0, min(10, r.stop)), "hold": max(0, min(10, r.hold)), "leave_at": round(r.leave_at, 1),
                    "best": r.best[:200], "weakest": r.weakest[:200], "why": r.why[:400]}
        except ValueError:
            return None
        finally:
            try:
                self.client.files.delete(name=f.name)
            except Exception:  # noqa: BLE001 - it expires on its own
                pass

    def _watch(self, clip, brief, f, types, check_analysis):
        try:
            text = (brief + f"\n\nReturn the log of this clip as JSON. Use clip_id \"{clip.clip_id}\". The clip is "
                            f"{clip.duration:.2f} s long, recorded at {clip.fps:.2f} fps.")
            config = types.GenerateContentConfig(system_instruction=watcher_prompt(),
                                                 response_mime_type="application/json", response_schema=ClipAnalysis)
            part = types.Part(file_data=types.FileData(file_uri=f.uri, mime_type="video/mp4"),
                              video_metadata=types.VideoMetadata(fps=frames_per_second(clip.duration)))
            correction, problems = None, []
            for attempt in range(3):
                t0 = time.monotonic()
                contents = [part, text] + ([correction] if correction else [])
                resp = self.client.models.generate_content(model=self.model, contents=contents, config=config)
                u = getattr(resp, "usage_metadata", None)
                usage = Usage.priced("watch", self.model, "gemini", int(getattr(u, "prompt_token_count", 0) or 0),
                                     int((getattr(u, "candidates_token_count", 0) or 0)
                                         + (getattr(u, "thoughts_token_count", 0) or 0)), 0, 0,
                                     time.monotonic() - t0)
                self.spend(usage)
                try:
                    data = json.loads(resp.text or "")
                    if self.log_dir is not None:
                        self.log_dir.mkdir(parents=True, exist_ok=True)
                        (self.log_dir / f"watch-{clip.clip_id}-{attempt + 1}.json").write_text(json.dumps(data, indent=1),
                                                                                             encoding="utf-8")
                    analysis = ClipAnalysis.model_validate(data)
                except (ValueError, TypeError) as exc:
                    problems = [f"no valid JSON log: {exc}"[:300]]
                else:
                    analysis.clip_id = clip.clip_id
                    problems = check_analysis(analysis, clip)
                    if not problems:
                        return analysis
                correction = ("Your previous answer had these problems. Fix all of them and return the complete "
                              "corrected JSON:\n- " + "\n- ".join(problems[:20]))
            raise BadReply(f"watch: still not valid after 3 tries: {'; '.join(problems[:4])}")
        finally:
            try:
                self.client.files.delete(name=f.name)  # their footage never stays with Google
            except Exception:  # noqa: BLE001 - it expires on its own within two days
                pass
