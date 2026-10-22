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


class ClipRead(BaseModel):
    short: str = Field(..., description="The clip's short name from the log (c01...).")
    sees: str = Field(..., description="What it shows, in under 15 words.")
    best: str = Field("", description="Its single best moment with its time ('the jump at 0:12'), or empty.")


class Question(BaseModel):
    ask: str = Field(..., description="A question whose answer would change the film.")
    options: list[str] = Field(..., description="2-4 short answers they can tap.")


class Understanding(BaseModel):
    """What the editor understood before pitching: shown to the person, who corrects it."""
    story: str = Field(..., description="What the film is about and what it should make a viewer feel, 1-2 sentences.")
    feel: list[str] = Field(default_factory=list, description="2-4 words for the tone.")
    must: list[str] = Field(default_factory=list, description="What they explicitly asked to have.")
    avoid: list[str] = Field(default_factory=list, description="What to leave out.")
    music: str = Field("", description="How the music reads and what it means for the cut, with real times.")
    clips: list[ClipRead] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list, description="0-3 genuinely open questions.")


class PolishedBrief(BaseModel):
    """Their brief, reworded clearly by the AI with nothing added or lost."""
    story: str = Field(..., description="What the film is about, 1-2 clear sentences, their meaning exactly.")
    feel: list[str] = Field(default_factory=list, description="2-4 words for the tone.")
    must: list[str] = Field(default_factory=list, description="What they asked to have, one per item.")
    avoid: list[str] = Field(default_factory=list, description="What to leave out, one per item.")


class TalkPart(BaseModel):
    at: float = Field(..., description="When this part starts, in seconds into the recording (from the transcript).")
    what: str = Field(..., description="What this part says, in under 12 words ('Why most diets fail').")


class Term(BaseModel):
    heard: str = Field(..., description="A name or word exactly as the transcript spells it ('Cloud').")
    written: str = Field(..., description="How it should be written on screen and in the captions ('Claude'); the "
                                          "same as `heard` when the transcript already has it right.")
    why: str = Field("", description="When `written` differs: a few words on why ('the AI assistant they talk about').")


class TalkUnderstanding(BaseModel):
    """What the motion designer understood from a recording before designing anything: shown to the person, who
    corrects it (above all the names: speech-to-text mishears them)."""
    story: str = Field(..., description="What the recording is about and what the video should leave a viewer with, "
                                        "1-2 sentences.")
    feel: list[str] = Field(default_factory=list, description="2-4 words for the tone.")
    parts: list[TalkPart] = Field(default_factory=list, description="The parts of the talk in order (2-8).")
    terms: list[Term] = Field(default_factory=list, description="Names and special words that will be on screen "
                                                                "(0-12), above all ones the transcript may have misheard.")
    must: list[str] = Field(default_factory=list, description="What they explicitly asked to have.")
    avoid: list[str] = Field(default_factory=list, description="What to leave out.")
    questions: list[Question] = Field(default_factory=list, description="0-3 genuinely open questions.")


class BeatIdea(BaseModel):
    """An original instrumental for one film, as a producer reads what the person asked for (see music/made.py: the
    code lays out the parts in whole bars; this says how it sounds)."""
    reading: str = Field(..., description="For the person: what you will make, in 1-2 plain sentences in their "
                                          "terms ('A warm lo-fi beat: soft Rhodes chords, dusty drums and vinyl "
                                          "crackle, growing into a fuller groove').")
    title: str = Field(..., description="A name for the track, 2-4 words.")
    bpm: int = Field(..., description="Beats per minute, 70-120 (for faster music, its half-time tempo).")
    styles: list[str] = Field(..., description="6-10 styles for the whole track, in English: the genre, 2-4 "
                                               "instruments with how they are played, the mood, the production. "
                                               "Never a real artist, band, song or label.")
    avoid: list[str] = Field(default_factory=list, description="0-8 sounds or genres to keep out.")
    intro: list[str] = Field(default_factory=list, description="1-3 styles only for the intro.")
    build: list[str] = Field(default_factory=list, description="1-3 styles only for the build.")
    drop: list[str] = Field(default_factory=list, description="1-3 styles only for the drop (the strongest part).")
    breath: list[str] = Field(default_factory=list, description="1-3 styles only for the breath before the last drop.")
    ending: list[str] = Field(default_factory=list, description="1-3 styles only for the ending (a final hit that "
                                                                "rings out).")


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
    src_in: float | None = Field(None, description="Leave empty: set by the tool for a shot kept from an earlier version.")


class DesignPlan(BaseModel):
    name: str
    idea: str = Field(..., description="The story in one line (it should match the chosen pitch).")
    pool: list[PoolMoment]
    plan: list[PlanShot]
    notes: str = Field("", description="How the plan carries the story: hook, turn, drop, ending.")
    start_at: float | None = Field(None, description="Leave empty: set by the tool (the beat an earlier version started on).")


class RangePlan(BaseModel):
    """New shots for one stretch of a film whose other shots stay exactly as they are."""
    pool: list[PoolMoment] = Field(..., description="The moments the new shots use (new keys only).")
    plan: list[PlanShot] = Field(..., description="The new shots for the stretch, in order.")
    notes: str = Field("", description="What the stretch does now, in one line.")


class PlaceTag(BaseModel):
    id: str = Field(..., description="The moment id, as listed (e.g. 'c03#2').")
    place: str = Field("", description="The place or landmark it shows, named the same for every moment of it; empty if none.")
    people: Literal["unknown", "none", "far", "near", "face"] = Field("unknown", description="Who the log says is in it.")


class PlaceTags(BaseModel):
    tags: list[PlaceTag]


ChangeKind = Literal["remove", "replace", "swap", "move", "longer", "shorter", "speed", "redo"]


class ChangeOp(BaseModel):
    op: ChangeKind = Field(..., description=(
        "remove: take the shot out (a neighbour holds on). replace: show something else in its place. swap: two shots "
        "trade pictures. move: its picture goes to another place. longer / shorter: by some beats (the cut next to "
        "it moves). speed: how it plays. redo: make a stretch of shots again from what they want (the only change "
        "that goes back to the editor)."))
    shot: int = Field(..., description="The shot it is about: its number in the version they watched (1 = first).")
    other: int | None = Field(None, description=(
        "swap: the other shot. move: the number of the shot whose place it takes. redo: the last shot of the stretch "
        "(empty: only `shot`)."))
    beats: float | None = Field(None, description="longer / shorter: by how many beats, in steps of 0.5.")
    side: Literal["next", "previous"] | None = Field(None, description=(
        "longer: take the beats from the next shot (default) or the previous one. shorter: give them to the next "
        "(default) or the previous one. remove: which neighbour holds on in its place (default: the previous)."))
    moment: str | None = Field(None, description=(
        "replace: the moment to show instead, an id like 'c02#5' from the moments listed (unused ones first)."))
    from_version: int | None = Field(None, description=(
        "replace: take a shot exactly as it was in this earlier version (with from_shot), e.g. 'the Opera House shot "
        "from before, the centred one'."))
    from_shot: int | None = Field(None, description="replace: that shot's number in `from_version`.")
    kind: Literal["hold", "cut", "hit", "ramp", "stutter"] | None = Field(None, description=(
        "speed: hold = slow motion, cut = normal speed, ramp = rushes in fast then slows, hit = an impact with a "
        "flash, stutter = quick repeated pieces."))
    want: str = Field("", description="redo / replace without a moment: what they want there, in their words.")
    never_again: bool = Field(False, description=(
        "remove / replace: they don't want that scene in this film at all (it never comes back)."))


class ChangeRequest(BaseModel):
    ops: list[ChangeOp] = Field(..., description="The changes, in the order they asked for them.")
    said: list[str] = Field(..., description=(
        "One line per change, in the same order, in plain words with shot numbers, as the person will read it "
        "before it is made ('Shot 8 holds 1 beat longer; shot 9 starts a beat later')."))
    unclear: str = Field("", description=(
        "A short question for anything you could not map to a change (empty: all clear). Make the clear parts anyway."))


# ---------------------------------------------------------------- your editor (the main agent) answers the person

class SpecAdd(BaseModel):
    text: str = Field(..., description=(
        "The item in a few plain words, as something the film must do or must not do ('Nobody in shot', 'End on the "
        "bridge at sunset', 'Cuts on the 808s'). Only what they said."))
    part: Literal["goal", "deliver", "style", "music", "must", "never"] = Field(..., description=(
        "goal: what the film is about (replaces the story line); deliver: shape, length, where it is posted; style: "
        "how it is cut or coloured; music: the song or how it is used; must: something it must have; never: "
        "something it must not have."))
    quote: str = Field("", description="Their words that ask for it, exactly as they wrote them.")
    check: Literal["sync", "no_people", "no_repeats", "none"] = Field("none", description=(
        "The automatic check that matches it, if one does: sync (cuts on the music's hits), no_people (nobody in "
        "shot), no_repeats (no place shown twice); none otherwise."))


class EditorAsk(BaseModel):
    question: str = Field(..., description="One short question whose answer changes the film.")
    options: list[str] = Field(..., description="2-4 short answers they can tap (a few words each).")


class EditorOffer(BaseModel):
    action: str = Field(..., description="One id from 'What you can offer now', exactly as listed.")
    why: str = Field(..., description="One line for the card: what it will do, in their terms.")
    pitch: int | None = Field(None, description="pick: the story's number as listed (1 = first).")
    version: int | None = Field(None, description="continue_from: the version's number.")
    text: str = Field("", description=(
        "change: what to change, in their words with shot numbers. sample_again: what the new sample must do "
        "differently. another: what the new story idea should be (their idea, or what was missing)."))


class EditorRemember(BaseModel):
    text: str = Field(..., description="A preference for all their next films, as advice to the editor.")
    why: str = Field("", description="Their words it comes from.")


class EditorReply(BaseModel):
    say: str = Field(..., description=(
        "What you say back, in plain words: 1-4 short sentences. Say what you understood and what happens next."))
    add: list[SpecAdd] = Field(default_factory=list, description="Items to add to the film's spec (only what they said).")
    off: list[str] = Field(default_factory=list, description="Ids of spec items to switch off (only when they asked).")
    on: list[str] = Field(default_factory=list, description="Ids of switched-off spec items to switch on again.")
    ask: EditorAsk | None = Field(None, description="At most one question, only when the answer changes the film.")
    offer: EditorOffer | None = Field(None, description="The next step, as a card they tap; nothing runs without it.")
    remember: list[EditorRemember] = Field(default_factory=list, description=(
        "At most one preference to offer keeping for their next films, only when they said it lasts ('always', "
        "'never', 'I like...')."))


class StyleRule(BaseModel):
    text: str = Field(..., description="One preference, phrased as advice to the editor: 'Keep shots calm, 2 s or longer'.")
    why: str = Field("", description="The feedback it comes from, briefly.")


class ProfileUpdate(BaseModel):
    add: list[StyleRule] = Field(default_factory=list, description="New preferences this feedback shows.")
    drop: list[str] = Field(default_factory=list, description="Ids of existing rules the feedback contradicts.")
    summary: str = Field("", description="One line on what was learned (shown to the person).")


class VisualPick(BaseModel):
    need: int = Field(..., description="The need's number.")
    choice: int = Field(..., description="The animation's number on its sheet, or -1 for none.")
    why: str = Field("", description="A few words.")


class VisualPicks(BaseModel):
    picks: list[VisualPick]


class VisualIdea(BaseModel):
    visual: str = Field(..., description="A simpler, concrete picture that still shows the idea.")
    topics: list[str] = Field(..., description="2-3 common one-word search terms.")


class VisualIdeas(BaseModel):
    ideas: list[VisualIdea]


class Direction(BaseModel):
    """A look for a voice or talking video, pitched before anything is drawn: the world behind the words and how it
    is drawn. The one picked is the brief every scene's backdrop is made from."""

    title: str = Field(..., description="A name for the look in 2-5 words ('Neon server city').")
    pitch: str = Field(..., description="One line on how the video will feel to watch.")
    world: str = Field(..., description="The place the story lives in, concretely: what is there, the light, the "
                                        "time of day, the depth (foreground, middle, far away).")
    art_style: str = Field(..., description="How it is drawn, as an illustrator would brief it: medium, shapes, "
                                            "texture, rendering ('flat vector with soft grain and long shadows').")
    colours: list[str] = Field(..., description="3-5 hex colours of the look, darkest first.")
    palette: Literal["midnight", "sunrise", "mint", "paper", "berry"] = Field(
        ..., description="The text and card colours that sit best on it: midnight (dark cool), sunrise (warm), mint "
                         "(green), paper (neutral), berry (pink/purple).")
    camera: str = Field(..., description="How the camera moves through it, using only slow push-ins, pull-backs, "
                                         "sideways drifts, rising moves and still holds ('slow push-ins, a drift "
                                         "sideways on each new topic').")
    music: str = Field("", description="The mood of the music bed in a few words.")
    preview: str = Field(..., description="The key frame that sells this look: one scene of this video, described as "
                                          "a full image prompt (the subject, where things are, the light). No text "
                                          "in it.")
    why: str = Field("", description="One line: why this suits what they said and how they said it.")


class Directions(BaseModel):
    directions: list[Direction]
