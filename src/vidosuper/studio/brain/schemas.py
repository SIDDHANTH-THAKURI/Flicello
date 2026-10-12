"""What the brain hands back: story pitches, a shot plan on the song's beats, and style rules.

Kept small and flat on purpose: these schemas are what the model must fill in, and every extra field is
one more way to go wrong. The server checks each answer against the footage and the song and sends the
problems back for one more try (see core.py).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class StoryBeat(BaseModel):
    what: str = Field(..., description="What happens in this part of the video, in one sentence.")
    moments: list[str] = Field(..., description="Moment ids from the catalog that carry it, e.g. ['c03#2', 'c07#1'].")
    music: str = Field("", description="Which part of the song it sits on: intro, build, drop, breakdown, outro.")


class StoryPitch(BaseModel):
    title: str = Field(..., description="A short working title (2-6 words).")
    logline: str = Field(..., description="The idea in one line a viewer could say back.")
    feeling: str = Field(..., description="The emotional arc in a few words, e.g. 'quiet -> rush -> warm'.")
    hook: str = Field(..., description="What the first 2 seconds show (name the moment id) and why it stops a scroll.")
    turn: str = Field(..., description="Where and how the video changes direction by its middle.")
    ending: str = Field(..., description="The last beat that lands the idea.")
    beats: list[StoryBeat] = Field(..., description="3 to 6 parts, in order.")
    stills: list[str] = Field(..., description="3 or 4 moment ids whose frames show this idea best.")
    why: str = Field(..., description="One line: why this suits this footage, this song and this person.")


class Pitches(BaseModel):
    pitches: list[StoryPitch]


Kind = Literal["hold", "cut", "hit", "ramp", "stutter"]
TransitionKind = Literal["cut", "dissolve", "fadeblack", "fadewhite", "smoothleft", "smoothright", "zoomin"]


class PoolMoment(BaseModel):
    key: str = Field(..., description="A short name for this moment, used by the plan (e.g. 'wave', 'jump').")
    clip: str = Field(..., description="Catalog short id, e.g. 'c03'.")
    start: float = Field(..., description="Window start, clip seconds.")
    end: float = Field(..., description="Window end, clip seconds.")
    peak: float | None = Field(None, description="The instant of the action, clip seconds (needed for hits, ramps).")


class PlanShot(BaseModel):
    kind: Kind
    beats: float = Field(..., description="Length in beats (for a stutter: the length of each piece).")
    key: str = Field(..., description="Which pool moment fills it.")
    count: int = Field(1, description="Stutter only: how many pieces.")
    transition: TransitionKind = Field("cut", description="How this shot comes in.")
    transition_beats: float = Field(0.0, description="Length of a non-cut transition, in beats (0.5-2).")
    frame_x: float | None = Field(None, description="Vertical videos: where the subject is across the frame, 0-1 "
                                                    "(0.5 = centre). Leave empty when unsure.")
    peak_beat: float | None = Field(None, description=(
        "cut/hold on an action moment: how many beats into the shot its action peak (the swing meeting the ball, the "
        "jump, the splash) lands exactly on a beat. Usually 1 or 2. Empty = decided for you (the second beat)."))
    zoom: float | None = Field(None, description="Leave empty: set by the tool for reused moments.")
    zoom_center: list[float] | None = Field(None, description="Leave empty.")  # a list: tuples make a schema Claude Code refuses
    flip: bool | None = Field(None, description="Leave empty.")
    crack_db: float | None = Field(None, description="Leave empty.")


class DesignPlan(BaseModel):
    name: str
    idea: str = Field(..., description="The story in one line (it should match the chosen pitch).")
    pool: list[PoolMoment]
    plan: list[PlanShot]
    notes: str = Field("", description="How the plan carries the story: hook, turn, drop, ending.")


class StyleRule(BaseModel):
    text: str = Field(..., description="One preference, phrased as advice to the editor: 'Keep shots calm, 2 s or longer'.")
    why: str = Field("", description="The feedback it comes from, briefly.")


class ProfileUpdate(BaseModel):
    add: list[StyleRule] = Field(default_factory=list, description="New preferences this feedback shows.")
    drop: list[str] = Field(default_factory=list, description="Ids of existing rules the feedback contradicts.")
    summary: str = Field("", description="One line on what was learned (shown to the person).")
