"""Data models shared across the pipeline: clips, measurements, and the edit recipe (EDL)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Clips
# ---------------------------------------------------------------------------


class ClipInfo(BaseModel):
    """Everything ffprobe tells us about one source clip, plus where its proxies live."""

    clip_id: str
    path: str
    rel_path: str
    fingerprint: str
    size_bytes: int = 0
    duration: float = 0.0
    width: int = 0  # display size, after rotation
    height: int = 0
    coded_width: int = 0
    coded_height: int = 0
    rotation: int = 0
    fps: float = 0.0  # average frame rate
    r_fps: float = 0.0  # nominal frame rate
    vfr: bool = False
    frame_count: int | None = None
    vcodec: str = ""
    pix_fmt: str = ""
    bit_depth: int = 8
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_space: str | None = None
    color_range: str | None = None
    hdr: bool = False
    has_audio: bool = False
    audio_codec: str | None = None
    audio_channels: int = 0
    audio_rate: int = 0
    creation_time: datetime | None = None  # UTC
    time_source: Literal["apple", "metadata", "file"] = "file"
    gps: tuple[float, float] | None = None  # (lat, lon)
    device: str | None = None
    chapter: int = 0
    proxy: str | None = None
    audio_wav: str | None = None  # 48 kHz stereo PCM, aligned to clip start
    audio_wav16k: str | None = None  # 16 kHz mono PCM for analysis / speech
    error: str | None = None

    @property
    def usable(self) -> bool:
        return self.error is None and self.duration > 0 and self.width > 0

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0

    @property
    def orientation(self) -> str:
        if not self.height:
            return "unknown"
        ar = self.aspect
        return "landscape" if ar > 1.05 else "portrait" if ar < 0.95 else "square"


class ClipManifest(BaseModel):
    version: int = 1
    clips_dir: str
    updated: datetime
    clips: list[ClipInfo]


# ---------------------------------------------------------------------------
# Local measurements
# ---------------------------------------------------------------------------


class Interval(BaseModel):
    start: float
    end: float
    reason: str = ""


class Window(BaseModel):
    start: float
    end: float
    score: float
    quality: float
    interest: float


class ClipSignals(BaseModel):
    """Summary of local measurements for one clip (time series live in the .npz next to it)."""

    clip_id: str
    version: int
    analysis_fps: float
    duration: float
    shake_median: float  # camera jitter, fraction of frame diagonal per second
    shake_p90: float
    pan_speed_median: float  # intentional camera motion, diagonal fraction per second
    sharpness_median: float  # log10 variance of Laplacian at analysis size
    brightness_mean: float  # 0-255
    clipped_high_pct: float
    clipped_low_pct: float
    motion_median: float  # subject motion after removing camera motion, 0-1
    motion_p90: float
    saturation_mean: float  # 0-255
    audio_rms_db_median: float | None = None
    audio_silence_pct: float | None = None
    quality_mean: float  # 0-1, technical quality over the whole clip
    quality_best: float  # 0-1, best window
    bad_intervals: list[Interval] = Field(default_factory=list)
    best_windows: list[Window] = Field(default_factory=list)
    scene_changes: list[float] = Field(default_factory=list)
    audio_peaks: list[float] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# AI perception (written by clip-analyst subagents, validated by `vido perceive check`)
# ---------------------------------------------------------------------------

MomentKind = Literal["action", "reaction", "people", "dialogue", "funny", "scenic", "establishing", "food",
                     "detail", "motion", "transition", "other"]
MomentUse = Literal["hook", "opener", "establishing", "highlight", "b-roll", "transition", "closer"]


class Moment(BaseModel):
    """A stretch of a clip worth using, with editor-grade in/out points (clip seconds)."""

    start: float
    end: float
    peak: float | None = Field(None, description="The single best instant, if there is one.")
    kind: MomentKind
    description: str
    hook: int = Field(..., ge=1, le=10, description="How well it would grab a viewer in the first 3 seconds.")
    strength: int = Field(..., ge=1, le=10, description="How good it is on its own.")
    best_use: MomentUse
    sound: Literal["keep", "music-only", "either"] = Field(
        "either", description="keep = the original audio is part of the moment (speech, laugh, splash).")
    notes: str = ""


class ClipAnalysis(BaseModel):
    clip_id: str
    summary: str = Field(..., description="One or two sentences: what happens in this clip.")
    scene: str = Field(..., description="e.g. 'harbour waterfront walk', 'city street from a car'.")
    location_guess: str = ""
    subjects: list[str] = Field(default_factory=list)
    camera: str = Field(..., description="e.g. 'gimbal walking', 'static', 'car mount', 'slow pan'.")
    mood: str = ""
    energy: int = Field(..., ge=1, le=5)
    visual_appeal: int = Field(..., ge=1, le=10)
    issues: list[str] = Field(default_factory=list, description="Problems with times, e.g. 'A-pillar blocks frame 6.2-7.6s'.")
    unusable: list[Interval] = Field(default_factory=list)
    moments: list[Moment] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    speed_ideas: str = Field("", description="e.g. '2x hyperlapse of the walk', '0.5x slow motion of the ferry wake'.")


# ---------------------------------------------------------------------------
# Edit Decision List (the "recipe" the editor writes and the renderer executes)
# ---------------------------------------------------------------------------

TransitionType = Literal[
    "cut", "fade", "dissolve", "fadeblack", "fadewhite", "wipeleft", "wiperight",
    "slideleft", "slideright", "smoothleft", "smoothright", "circleopen", "circleclose",
    "zoomin", "hblur",
]


class Transition(BaseModel):
    type: TransitionType = Field("cut", description="'dissolve' and 'fade' are both a smooth crossfade; "
                                                    "'fadeblack'/'fadewhite' dip through black/white.")
    duration: float = Field(0.0, description="Seconds; the shot overlaps the previous one by this much.")


class SegmentAudio(BaseModel):
    mode: Literal["original", "mute"] = "original"
    gain_db: float = 0.0
    duck_music: bool = Field(True, description="Lower the music while this shot's own audio plays.")


class Segment(BaseModel):
    id: str
    clip_id: str
    src_in: float = Field(..., description="Start time in the source clip, seconds.")
    src_out: float = Field(..., description="End time in the source clip, seconds.")
    speed: float = Field(1.0, description="0.25 = 4x slow motion, 2.0 = double speed.")
    transition_in: Transition = Field(default_factory=Transition)
    audio: SegmentAudio = Field(default_factory=SegmentAudio)
    framing: Literal["auto", "fill", "fit", "blur_pad"] = "auto"
    zoom: float = Field(1.0, ge=1.0, le=2.5, description=(
        "Punch-in: 1.25 shows the central 80% of the frame. Hides a pillar, phone mount or railing at the "
        "edge; 4K sources allow up to 2x at 1080p without softness."))
    zoom_center: tuple[float, float] = Field((0.5, 0.5), description=(
        "Where the punched-in frame is centred, as fractions of the source width and height (0-1)."))
    role: str = ""
    rationale: str = ""


class MusicSpec(BaseModel):
    path: str
    start: float = Field(0.0, description="Offset into the track, seconds.")
    gain_db: float = 0.0
    fade_in: float = 0.5
    fade_out: float = 2.0


class EditMeta(BaseModel):
    title: str = ""
    platform: str = "youtube"
    width: int = 1920
    height: int = 1080
    fps: int = 30
    fade_in: float = 0.5
    fade_out: float = 1.0
    loudness_lufs: float = -14.0
    true_peak_db: float = -1.5
    snap_to_beats: bool = Field(True, description="Nudge hard cuts onto music beats (when there is music).")


class EDL(BaseModel):
    version: int = 1
    project_dir: str = Field(..., description="The clips folder this edit was made from.")
    meta: EditMeta = Field(default_factory=EditMeta)
    segments: list[Segment]
    music: MusicSpec | None = None
    notes: str = ""


# ---------------------------------------------------------------------------
# Edit brief (written by the director subagent)
# ---------------------------------------------------------------------------


class BriefSection(BaseModel):
    name: str = Field(..., description="e.g. 'hook', 'setting off', 'harbour walk', 'big reveal', 'ending'.")
    purpose: str = Field(..., description="What this part does for the viewer.")
    start: float = Field(..., description="Planned start on the finished video's timeline, seconds.")
    end: float
    energy: int = Field(..., ge=1, le=5)
    shot_length: str = Field(..., description="Pacing, e.g. '1-2 beats (0.6-1.2 s), quick cuts'.")
    music_section: str = Field("", description="Which part of the track plays here, if there is music.")
    moments: list[str] = Field(..., description="Candidate moment ids (c05#2), best first.")


class EditBrief(BaseModel):
    title: str
    logline: str = Field(..., description="One sentence: what this video is and why someone would watch it.")
    target_seconds: float
    hook: str = Field(..., description="Exactly what the first 1-3 seconds show, and why it grabs attention.")
    structure: list[BriefSection]
    ending: str = Field(..., description="How it ends and why that's satisfying.")
    music_plan: str = Field("", description="Where the track starts, which sections land where, or why no music.")
    must_include: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    style_notes: str = Field("", description="Transitions, speed changes, slow motion, sound, look.")


PLATFORM_CANVAS: dict[str, tuple[int, int]] = {
    "youtube": (1920, 1080),
    "reels": (1080, 1920),
    "shorts": (1080, 1920),
    "tiktok": (1080, 1920),
    "square": (1080, 1080),
}


def canvas_for(platform: str, resolution: str = "1080p") -> tuple[int, int]:
    width, height = PLATFORM_CANVAS[platform]
    if resolution == "4k":
        width, height = width * 2, height * 2
    return width, height
