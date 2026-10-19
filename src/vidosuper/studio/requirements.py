"""Did the video do what the person asked? Checked after every sample and every finished video, for everyone.

The person's note, the pace they picked and the choices on their project are turned into a few plain, checkable
requirements, each compared with what was really made. Nothing here calls an AI: it is counting. An unmet requirement is
written into the video's findings (so the digest and the stage watcher see it, and the improvement routine can work on
it) and shown to the owner; people only ever see the sample, with "what I changed".
"""

from __future__ import annotations

import re

from .brain.context import BRIGHT_WORDS, HITS_WORDS, REUSE_WORDS

NO_MUSIC = re.compile(r"\b(no|without|don'?t (?:add|use|put)|do not (?:add|use|put)|remove)\b[^.]{0,20}\bmusic\b", re.I)
FULL_LENGTH = re.compile(r"\b(full|whole|entire)\b[^.]{0,12}\b(length|video|song)\b", re.I)
GRAPHICS = re.compile(r"\b(motion\s*graphics?|animations?|animated|graphics)\b", re.I)
CAPTIONS = re.compile(r"\b(captions?|subtitles?)\b", re.I)


def wants_no_music(note: str) -> bool:
    return bool(NO_MUSIC.search(note or ""))


# Their words asking for the clips' own sound to go: "no audio of that video", "mute the clips", "no original sound",
# "music only", "remove the clip sound". Narrow on purpose, since a wrong match silences a film: the words must sit
# right next to each other, a negation before them ("don't remove the sound from the clips") never counts, and
# neither do "noise" (that's a clean-up, not silence), the music's drop or "cut to" (editing words).
_NOT = r"(?<!n't )(?<!not )(?<!never )"
_OFF = (r"(?:no|without|remove|mute|turn\s+off|switch\s+off|get\s+rid\s+of|take\s+out|strip|"
        r"(?:don'?t|do\s+not|never)\s+(?:use|keep|want|add|include|play))")
_SOUND = r"(?:audio|sound|sounds|voices?|talking|speech)"
_CLIPS = r"(?:video|videos|clip|clips|footage|recordings?|shots?)"
NO_CLIP_SOUND = re.compile(
    # "no audio of that video", "remove all the sound from my clips", "don't use any sound in the footage"
    rf"{_NOT}\b{_OFF}\s+(?:(?:any|all|the|of|more|original|natural|real|their|its|my)\s+){{0,3}}{_SOUND}\b"
    rf"(?:\s+at\s+all)?\s+(?:of|from|in|on)\s+(?:(?:the|that|this|my|those|these|all|any|each|every)\s+)?{_CLIPS}\b"
    # "no clip sound", "without the original audio", "mute the camera sound"
    rf"|{_NOT}\b{_OFF}\s+(?:(?:any|all|the|their|its|my)\s+){{0,2}}"
    rf"(?:original|natural|clips?'?|video'?s?|footage|camera|ambient|real)\s+{_SOUND}\b"
    # "mute the clips", "mute all my videos", "the clips should be muted"
    rf"|{_NOT}\bmute\s+(?:(?:the|all|my|every|each)\s+){{0,2}}{_CLIPS}\b"
    rf"|\b{_CLIPS}\s+(?:should|must)\s+be\s+(?:muted|silent|silenced)\b"
    # "music only", "only the music", "just music" (not "only music at the start", "just music and the waves")
    r"|\b(?:only|just)\s+(?:the\s+)?music\b(?!\s*(?:and|with|plus|&|\+|over|under|at|for|in|during|until|till|on|from"
    r"|when|while|starts?|plays?|comes?|then|before|after)\b)"
    r"|\bmusic\s+only\b(?!\s*(?:at|for|in|during|until|till|on|from|when|while|then|before|after)\b)"
    # "no sound except the music"
    rf"|{_NOT}\bno\s+(?:audio|sound|sounds)\s*(?:at\s+all\s*)?,?\s*(?:except|but|apart\s+from|other\s+than|besides)\s+"
    r"(?:the\s+)?music\b",
    re.I)

# "keep the sound of the waves", "with the original audio", "every hit with its sound": this film wants its clips heard
CLIP_SOUND = re.compile(
    rf"{_NOT}\b(?:keep|with|hear|use|add|include|play)\s+(?:(?:the|their|its|my|all|some)\s+){{0,2}}"
    r"(?:(?:own|original|natural|real|clips?'?|ambient|camera)\s+)?(?:sound|sounds|audio)\b", re.I)


def wants_no_clip_sound(note: str) -> bool:
    """They asked for the clips' own sound to go (music only, or silence)."""
    return bool(NO_CLIP_SOUND.search(note or ""))


def wants_clip_sound(note: str) -> bool:
    """This film's words ask to hear its clips (their sound, or the hits): a style answer of 'music only' gives way."""
    note = note or ""
    return not wants_no_clip_sound(note) and bool(CLIP_SOUND.search(note) or HITS_WORDS.search(note))


def _row(ok: bool, what: str, detail: str) -> dict:
    return {"level": "ok" if ok else "warn", "check": "requirement", "detail": f"{what}: {detail}"}


def montage(project: dict, *, mean_shot: float, shots: int, hits_total: int, hits_used: int, seconds: float | None,
            music_playing: bool, look: str) -> list[dict]:
    note, pace = project["intent"] or "", project["pace"]
    rows = []
    if pace == "every":
        rows.append(_row(mean_shot <= 1.0, "one shot per beat", f"{shots} shots, {mean_shot:.2f} s on average"))
    elif pace == "fast":
        rows.append(_row(mean_shot <= 1.7, "fast cuts", f"{mean_shot:.2f} s per shot on average"))
    elif pace == "calm":
        rows.append(_row(mean_shot >= 1.5, "calm cutting", f"{mean_shot:.2f} s per shot on average"))
    if HITS_WORDS.search(note) and hits_total:
        rows.append(_row(hits_used >= 0.8 * min(hits_total, shots), "the hits you asked for",
                         f"{hits_used} of {hits_total} real hits are in the video"))
    if wants_no_music(note):
        rows.append(_row(not music_playing, "no music", "none plays" if not music_playing else "music is playing"))
    if FULL_LENGTH.search(note) and seconds:
        rows.append(_row(seconds >= 0.9 * project["length"], "the full length",
                         f"{seconds:.0f} s of the {project['length']:.0f} s asked for"))
    if BRIGHT_WORDS.search(note):
        rows.append(_row(look in ("bright", "vivid"), "a bright look", f"look: {look}"))
    return rows


BEHIND = re.compile(r"\b(behind me|in the back(?:ground)?|background|behind)\b", re.I)


def explainer(project: dict, *, scenes: int, callouts: int, cutaways: int, captions: bool, seconds: float,
              duration: float, music_playing: bool, audio_kept: bool, style: str = "labels", animated: int = 0,
              cut_out: bool = False) -> list[dict]:
    note, kind = project["intent"] or "", project["kind"]
    rows = []
    if kind == "voice":
        rows.append(_row(scenes >= max(2, int(duration // 14)), "a picture for every point",
                         f"{scenes} scenes for {duration:.0f} s of voice"))
        rows.append(_row(abs(seconds - duration) < 1.0, "the whole recording", f"{seconds:.0f} s of {duration:.0f} s"))
    else:
        rows.append(_row(callouts + cutaways >= max(1, int(duration // 16)), "motion graphics on the topics",
                         f"{callouts} callouts and {cutaways} cutaways for {duration:.0f} s"))
        rows.append(_row(audio_kept, "your own sound kept", "unchanged" if audio_kept else "changed"))
    if style == "designed":
        rows.append(_row(animated >= max(1, (scenes + callouts) // 3), "designer animations",
                         f"{animated} animated moments"))
    if kind == "talk" and BEHIND.search(note):
        rows.append(_row(cut_out, "graphics behind you", "you are cut out in front of them" if cut_out else
                         "drawn over your video (the cut-out was not available)"))
    if wants_no_music(note) or kind == "talk":
        rows.append(_row(not music_playing, "no music added", "none" if not music_playing else "music is playing"))
    if CAPTIONS.search(note) and not re.search(r"\bno (captions?|subtitles?)\b", note, re.I):
        rows.append(_row(captions, "captions", "on" if captions else "off"))
    return rows


def unmet(rows: list[dict]) -> list[str]:
    return [r["detail"] for r in rows if r["level"] != "ok"]


TALKING_NOTE = re.compile(r"\b(as i (?:speak|talk)|while i(?:'m| am)? (?:speak|talk)|i(?:'m| am) (?:explain|talk|speak|review)\w*|"
                          r"me (?:explain|talk|speak)\w*|my (?:voice|talk)|voice[- ]?over|narrat\w+)\b", re.I)


def is_talking_video_request(note: str, clips: int) -> bool:
    """One video, and the note says the person speaks in it and wants graphics for it: that is the talking-video mode,
    not a montage cut to music (a friend picked the montage form for exactly this)."""
    return clips == 1 and bool(GRAPHICS.search(note or "")) and bool(TALKING_NOTE.search(note or ""))
