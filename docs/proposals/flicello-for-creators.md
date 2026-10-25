# Flicello redesign: creators first, then agencies, then busy editors

Status: plan (2026-10-22), waiting for the owner's go. Nothing in here is built yet. It builds on
`flicello-together.md` (one screen, one conversation, point and say), which becomes the workflow of this redesign
rather than a separate project.

## Who it is for, in order

1. **Content creators.** One person, a phone, a deadline, a feed. They want a short vertical video that stops the
   scroll, with captions, on a sound that fits, in minutes, and they want it to look like *them*, not like a
   template. They judge us on the first video.
2. **Marketing agencies.** Many clients, many brands, many formats. They want the same idea cut for every
   platform, in the client's colours, logo and fonts, a review link the client can comment on, and a clear
   record of what was approved.
3. **Busy editors (not lazy ones).** They can do it all themselves and don't have time. They want the boring 80%
   done well (logging, selects, rough cut, beat grid, captions) and then the timeline in *their* editor
   (Premiere, Resolve, Final Cut) with nothing locked. They will not accept a black box.

The same engine serves all three. What changes is the front door and what "done" means: a creator is done when
the video is posted, an agency when the client approves, an editor when the timeline lands in their tool.

## What exists today and what is missing

✓ works today · ◐ partly there · ✗ missing

### Analysis and understanding

| Feature | State | What is there / what is missing |
|---|---|---|
| Analyse clips | ✓ | `perceive/` (motion, video and audio signals, contact sheets, catalog), AI analyst logs moments with in/out times, places and people. |
| Transcribe speech in clips | ◐ | `perceive/transcribe.py` transcribes every clip and the talking-video kind has captions. **Missing:** captions in music films (montage), a transcript the person can read and edit, and cutting by editing the text (delete a sentence, the shot goes). |
| Song analysis and beat sync | ✓ | `music/analysis.py`: beat grid, bars, sections, the drop, the strong hits (808s); cuts land on the hits; "beat sync" is a rule. |
| Flag strong moments | ◐ | Found and used inside the engine (moments, hit detection). **Missing:** shown to the person: a "best moments" row they can star, ban or drag into the film. |
| Explain what it understood | ◐ | "What I understood" (`understand.md`, polish and undo). **Missing:** it is a form, not a reply; it should be one short paragraph in the conversation with tap answers ("Keep those?"). Planned in `flicello-together.md`. |

### Music

| Feature | State | What is there / what is missing |
|---|---|---|
| Generate music | ✓ | ElevenLabs composer (`music/compose.py`, `made.py`), three options to pick by ear. |
| Cut music to the video | ◐ | `songfit.py` makes a short song long (repeats whole bars, keeps the real ending). **Missing:** making a long song short on a phrase end, starting the song at its best part (the hook) instead of 0:00, fading under speech, and a waveform with the drop and hits drawn on the timeline. |
| Licensed music library | ✗ | Creators want a known sound. Out of reach legally for trending tracks; possible with a royalty-free library (e.g. a licensed API) beside our own composed tracks. Decision for the owner. |

### Templates and styles

| Feature | State | What is there / what is missing |
|---|---|---|
| Themes / templates | ◐ | Rhythm templates on the song (`template.py`), 10 colour looks (`render/looks.py`), AI-painted looks. **Missing:** a gallery people choose from by name and by seeing it: "Travel cinematic", "Fast hype", "Day in my life vlog", "Product launch", "Talking head with captions", "Before / after", "Recipe", "Event recap". Each is a full recipe: pace, cut style, transitions, captions style, title style, look, music mood, length per platform. |
| Sample videos of general editing styles | ✗ | Nothing. Each style needs a short sample reel that shows the *style*, not the person's footage, so they can pick by eye. We must own them: made by us from the owner's footage and licensed stock (Pexels and Pixabay allow this), never copied from other creators. |
| Brand kits (agencies) | ✗ | Logo, colours, fonts, intro and end card per client, applied to every video. Today a logo request is a known gap (`abilities.py`). |

### Output

| Feature | State | What is there / what is missing |
|---|---|---|
| Show a sample before the film | ✓ | Sample, storyboard with alternatives, then the whole film; versions you can continue from. |
| Downloads in different formats | ◐ | MP4 (H.264) in 9:16 or 16:9, one download link. **Missing:** 1:1 and 4:5, every shape from one edit at once (reframed, `render/framing.py` already does the reframing), HEVC (smaller), MOV ProRes 422 (editors), WebM, a GIF or short teaser, the music and voice as separate WAVs, captions as SRT/VTT, a cover image, and the timeline for editors as FCPXML / Premiere XML / CMX EDL (the engine already has an EDL; this writes it out). |
| Use the available technologies | ◐ | Claude for planning, Whisper for speech, ElevenLabs for music, FFmpeg, the PC and Modal GPU renderers. **Missing:** hardware encoding on the GPU for speed, voice cleanup (noise removal) for talking clips, and posting straight to platforms (later, only with the person's OK each time). |

### Quality and control

| Feature | State | What is there / what is missing |
|---|---|---|
| Virality check | ✗ | Nothing. See the design below. |
| Point at a section, re-edit only that | ◐ | The engine is built (`versions.py`): numbered shots pinned to their frames, exact changes on named shots, "make this part again" for a stretch, changes checked before they are made. **Missing:** pointing on the *player*: drag over a stretch of the timeline (or tap a frame) and say what is wrong; a preview of only that stretch in seconds; before/after toggle. |

### Workflow

| Feature | State | What is there / what is missing |
|---|---|---|
| Human and AI together at each stage | ◐ | Every stage waits for the person (understood, story ideas, music, sample, film, changes). **Missing:** it is a row of forms; the AI never talks back. `flicello-together.md` is the plan: one conversation beside the film, the price before each step, two options to tap instead of paragraphs to type, the brief as a checklist. |

## The virality check, honestly

No tool can promise a video will go viral: reach depends on the account, timing and luck. What we *can* check is
everything the platforms reward and creators get wrong, and say it plainly. So the check is named **"Ready to
post?"** and gives a verdict (Strong / Could be stronger / Weak) with the three fixes that matter most, each a
button that makes the fix.

What it checks (all on the real video, most of them free, no AI):

1. **The hook (first 1.5 s).** Is there motion, a face, or a question on screen at 0:00? Does the first cut come
   fast? A slow logo or a black frame at the start is the most common reason people scroll.
2. **Holds attention.** Longest stretch with no new shot, no motion and no new words; pace against the style.
3. **Understood with the sound off.** Captions present where people speak; text inside the platform's safe zone
   (not under the like buttons or the caption bar).
4. **Sound.** Loudness near the platform's target (about -14 LUFS), speech above the music, the music's drop on
   a payoff.
5. **The ending.** Does it loop cleanly or end on a payoff, instead of fading out (rewatches count)?
6. **Fits the platform.** Shape and length for TikTok, Reels, Shorts, YouTube, LinkedIn.
7. **A second opinion.** One AI viewer (fast model, a few cents) watches the stills and says what it would scroll
   past and why. We already have critic and adversary rubrics for this.

Later, when a person connects their account's analytics, we compare the verdict with how their videos really did
and tune the check to them. Until then it says "likely to hold attention", never "will go viral".

## The redesign

### Front door per audience

- **Creator:** "Drop your clips, pick a style, we'll make it." Phone first, vertical by default, captions on,
  three styles with sample reels to tap, a finished video in minutes, then talk to change it.
- **Agency:** a workspace per client with its brand kit; "one idea, every format" (9:16, 1:1, 4:5, 16:9 from one
  edit); a review link where the client comments on a moment of the video; each comment becomes a pointed change;
  versions marked "approved".
- **Busy editor:** "Give me a rough cut on the beat." Selects and transcripts first, a rough cut on the music's
  grid, then export the timeline to their editor with every clip linked to the original files. Keyboard
  shortcuts, frame numbers, no hand-holding text.

### One screen

The film on one side, the conversation on the other (from `flicello-together.md`). Under the player, one strip:
numbered shots with their places, the music's waveform with the drop and big hits, and the captions. Anything on
the strip can be tapped or dragged into a message. Above it, the brief as a checklist and the "Ready to post?"
verdict.

### The stages, with the person steering at each

1. **Clips and goal** → the editor says in a paragraph what it saw and understood, and asks what it is not sure
   of (tap answers).
2. **Style** → three styles chosen for this footage, each with its sample reel; or "like my last one".
3. **Music** → three composed tracks, or theirs; it says where the drop is and where it will put it.
4. **First look** → a quick preview in about a minute; best moments row beside it to star or ban.
5. **Steering** → point at a shot, a stretch or a frame and say what is wrong; only that part changes; preview
   in seconds; before/after.
6. **Ready to post?** → the verdict and its fixes.
7. **Export** → every shape and format they need, captions, timeline for editors, cover image.

## Order and cost

Each phase is something a person can use on its own. Cost is the AI time to build it with me in Claude Code
(Pro usage) and what it adds to the running cost per film.

| Phase | What | Build effort | Running cost per film |
|---|---|---|---|
| 0 | Push the engine fixes already built (pinned shots, checked changes, sync on the hits). | small | lower than today |
| 1 | **Exports**: every shape from one edit, 1:1 and 4:5, HEVC, ProRes, WebM, GIF teaser, SRT/VTT, stems, cover image, FCPXML/EDL. | medium | render time only |
| 2 | **Ready to post?** check with fixes as buttons (rules first, then the AI viewer). | medium | free, + ~$0.02 with the AI viewer |
| 3 | **Captions in every film** and an editable transcript; music trimmed to a phrase end and started at its hook. | medium | free (Whisper runs already) |
| 4 | **Style gallery** with 8 named styles and a sample reel for each (made by us, owned footage and licensed stock). | large (the reels are real edits) | free |
| 5 | **Together**: conversation beside the film, point at a stretch on the player, previews in seconds, best moments row (the `flicello-together.md` phases 1-4). | large | a few cents a change |
| 6 | **Agencies**: brand kits, client workspaces, review links with comments on moments, approvals. | large | free |
| 7 | **Editors**: transcript-based selects, rough cut on the grid, timeline export round trip, keyboard flow. | medium (export lands in phase 1) | free |

Phases 1-3 are where creators feel the difference fastest and cost little; 4 is the biggest taste risk, so its
first two styles get rated before the other six are made.

## Decisions for the owner

1. **Order:** phases 0-3 first (exports, "Ready to post?", captions and music fit), then the style gallery, then
   the conversation? Recommended.
2. **Sample reels:** made from your own footage plus licensed stock (Pexels / Pixabay)? Recommended; no other
   creators' videos.
3. **Music:** composed tracks only, or also a paid royalty-free library so creators can pick known-sounding songs?
4. **Agency features:** build now or after creators are happy (recommended: after)?
