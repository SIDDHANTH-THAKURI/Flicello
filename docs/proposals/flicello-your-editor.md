# Flicello: your editor

One AI editor per person, working with you from the first clip to the final export.

Status: approved and under way (22 Oct 2026). Slice 1 (the editor's backbone: the film spec, the conversation, the
check-ins) is **live** (f405492). After the owner's fuller goal ("Jarvis" on screen, instant edits, pro motion graphics,
generation, virality) and a research pass (`docs/research/flicello-stack-2026-10.md`), the order of work is
**section 9b** below; everything else in this plan stands. This plan takes in `flicello-together.md` (its principles, research and the engine
fixes it lists all stay) and replaces its order of work. It also takes in what `flicello-for-creators.md` (written
the same day in another session, nothing built) adds: cutting by editing the transcript, the song started at its hook,
a licensed music library, the "Ready to post?" verdict, voice clean-up and posting to platforms. One plan should stay.

## 0. The brief

The owner's priorities, in order: **content creators** (the main audience, designed for first), then **marketing
agencies**, then **busy editors** (busy, not lazy: they want speed and control, not hand-holding). What it must do:

- Analyse clips and flag the strong moments; transcribe the speech in them.
- Read the song and cut on its beats.
- Make music, and cut and extend music to fit the video.
- Say back in plain language what it understood, before it edits, so the person can confirm.
- Offer themes and templates: many styles people like, with sample videos of each editing style (not only the
  samples it makes from their clips), and show the finished film next to those samples.
- Download the result in many formats, using the best technology available.
- Check virality at the end.
- Re-edit only the section the person says is wrong.
- Work with the person throughout, not one-shot generation.
- **One persistent main agent per person**, a personal assistant from the first clip to the final export. It holds
  the film spec and the person's style, explains what it understood, checks in at every stage, and spawns
  sub-agents (transcription, beat analysis, the virality check...) to go faster or split the work.
- **Cost is not the main constraint; the goal is the best possible result.**

What the owner said earlier in this conversation also stands:

- An assistant on their side that tells every step how things should look and what should happen where, "so I
  don't have to keep typing and nothing falls out of sync".
- No loops of explaining again and again, and knowing when it will be right.
- Human-friendly, with the human and the AI connected.
- Instagram quality that survives the upload, landscape reels, the trending-audio workflow, and captions with
  hashtags.
- The same prompt shouldn't give a random different film.

## 1. Who it is for, in order

| | Who | They come for | What wins them | Built in |
|---|---|---|---|---|
| 1 | **Content creators** (main) | A reel from today's clips that looks like the styles they see, and does well | Styles they know, beat sync that lands, a reach check, files that survive the upload, a caption, fixes in seconds | Slices 1-6 |
| 2 | **Marketing agencies** | Many on-brand deliverables for clients, approved fast | Brand kits, review links with comments, every shape and length from one edit, licensed music | Slice 7 |
| 3 | **Busy editors** | The grunt work done (logging, selects, transcripts, beat markers, a rough cut), then their own NLE | Speed and control: exact numbers, a keyboard, a timeline that opens in Premiere, Resolve or Final Cut, a quiet assistant | Slice 8 |

Everything is built once in the engine and shown three ways: a brand kit is a style, a client review is a
conversation, an NLE timeline is an export format.

## 2. What exists today, and what is missing

| Brief item | Today | Where | Missing |
|---|---|---|---|
| Analyse clips, flag strong moments | **Engine: yes.** Shake, blur, light and motion measured; hits found in the sound to the millisecond; an AI log of every moment (strength, hook, peak, place, people, best frame). **Studio: partly.** "What I understood" names each clip's single best moment in words; whole clips can be left out. | `perceive/*`, `studio/pipeline.py`, `brain/prompts/analyst.md` | All flagged moments shown as stills with why; star (must use) and ban (never) per moment. |
| Transcribe speech | **Engine: yes** (faster-whisper, word timings, noise flagged). **Studio: partly.** Voice and talking videos show their parts and the names to spell; films use speech only to keep stray voices out. | `perceive/transcribe.py` | A transcript per clip; "use this line"; captions from speech in films; SRT files. |
| Read songs, cut on beats | **Yes.** Beats, bars set by the kick/808, drops, booms, hits; plans laid on the beat grid; "beat sync" read as a rule and checked (cuts on the hits, density after the drop). | `music/analysis.py`, `brain/core.py`, `qa.py` | The song's marks drawn for the person (sections, drop, hits); choosing where the song starts and ends. |
| Make music; cut and extend it to fit | **Yes.** ElevenLabs tracks made for the film (a taste, then the whole track), our house beat as fallback; a short song extended on whole bars; a safe-to-post search in the CLI. | `music/compose.py`, `studio/service.py`, `studio/songfit.py`, `music/select.py` | Ending on the song's own phrase rather than a fade; a version without the music for the platform's own track. |
| Say back what it understood | **Yes, as a form.** "What I understood": story, feel, must, avoid, how the music reads, each clip in a line, 0-3 tap questions, polish and undo. Changes are restated and confirmed (built 22 Oct, not live). | `studio/service.py`, `studio/versions.py` | A conversation; "what happens where" before the first cut; check-ins after every step. |
| Themes and templates | **CLI only.** Rhythm templates designed on a song, filled and retargeted to new music; 10 colour grades; 5 paces; painted looks for voice and talking videos. | `template.py`, `render/looks.py` | A style library in the studio, with styles people know, picked like a card and followed by the editor. |
| Sample videos of editing styles | **No.** | - | One sample per style, made from one shared demo footage set. |
| The film shown next to the samples | **No.** | - | "Your film" beside "the style", and "try this style" on your own clips. |
| Downloads in many formats | **MP4 H.264 only** (libx264 CRF 18, or Intel QuickSync), in 9:16, 16:9 or both; loudness at -14 LUFS. | `render/assemble.py`, `render/render.py` | Presets (Reels/TikTok/Shorts, 4:5 feed, 1:1, 3:4, YouTube 1080p/4K, landscape inside 9:16); 60 fps; ProRes, HEVC, WebM, GIF; SRT; stems; a no-music version; a cover image; NLE timelines. |
| Virality check | **No.** The CLI's review loop has a hook critic (first frame, first-second motion, curiosity, retention risks, loop). | `config/rubrics/hook.md`, `loop/*` | A reach check at the end, with reasons and fixes. |
| Re-edit only the wrong section | **Built 22 Oct, not live.** Shots are numbered and pinned to their frames. Remove, replace, swap, move, longer, shorter and speed are made by code; "make this part again" is made by the AI. You can continue from any version. | `studio/versions.py` | Picking a section by time or by dragging; a preview of only that section; proof the rest is unchanged. |
| Human and AI throughout | **Partly.** Tap questions, three stories, a storyboard and sample to check, changes with a confirm step, ratings. | `studio/service.py`, `web/templates/project.html` | One conversation through every stage. |
| One persistent main agent, with sub-agents | **Partly.** Each person has a style profile (rules and taste, editable on "My style"). The specialist AI steps (analyst, director, editor, coach) run as separate jobs, and clips are watched in parallel. | `studio/profiles.py`, `studio/brain/*` | The agent itself: one editor that holds the spec and the person's style, talks, checks in, runs the specialists and checks their work. |
| Agencies | **No** (one person per account). | - | Teams, brand kits, review links, deliverables. |
| Busy editors | **No.** | - | NLE timelines, markers, keyboard, batch, a quiet mode. |

## 3. The design in one picture

```
                YOU: type · speak · tap · point (a shot, a stretch, a moment, a music mark)
                 │                                                   ▲
                 ▼                                                   │ check-ins, questions, previews, checklists
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │  YOUR EDITOR: the main agent, one per person (Opus 5.5)                                     │
 │  remembers: your style rules · your taste · your platforms and brands · your earlier films  │
 │  holds:     the FILM SPEC: one list both of you see; every rule has a check                 │
 │  does:      understands · says it back · asks · plans the work · runs the specialists ·      │
 │             checks their work against the spec before you see it · remembers on your yes    │
 └───────────────┬────────────────────────────────────────────────────────▲───────────────────┘
                 │ tasks, in parallel                                     │ results + one-line summaries
                 ▼                                                        │
   watcher ×N clips · transcriber · music analyst · composer · style matcher · director · editor ·
   section editor · checker · reach panel (hook, hold, share, platform) · exporter · copywriter · coach
                 │
                 ▼
   ENGINE: perceive · music · design on the beat grid · render (per-shot cache) · QA · versions
   RUNS ON: the server (the editor's own turns) · the PC lanes and Modal (the heavy work)
```

## 4. Your editor (the main agent)

**What it is.** One editor per person, the same across every film, and the only one that talks to you. The
specialists behind it do the work.

**Persistent.** Not a process left running but a stored identity. Each turn it reads:

- what it knows about you: your style rules with their checks, your taste, your platforms and brands, how your last
  films went and what you changed in them;
- this film's spec and its versions;
- where the film is and what is on screen;
- the conversation: recent messages in full, older ones summarised.

The stable part (its instructions, your memory, the spec) is cached between turns, and long threads are compacted.

**What it does.**

1. **Understands.** It merges what you say, what the footage holds and what the music does into the spec, and says
   it back in plain words.
2. **Asks only what changes the film:** one question at a time, with tap answers.
3. **Plans the work and runs the specialists**, in parallel where they don't depend on each other, and tells you
   what is running and for how long.
4. **Checks before you see anything.** Every specialist's result is checked against the spec: code checks first,
   then its own look. What fails goes back to the specialist and never costs you a round.
5. **Checks in after every step:** what happened, the one thing to look at, the decision it needs.
6. **Translates** your words, taps and pointing into exact operations: spec changes, shot edits, a stretch made again.
7. **Remembers on your yes.** It offers a style rule from what you said ("Keep 'cuts on the 808s' for your next
   films?") and saves it only if you agree.
8. **Guards.** It never changes what you approved without saying so, never re-plans silently, asks before anything
   long or irreversible, and says plainly what failed or what it can't do yet.

**Models.**

- **Its own replies:** Opus 5.5 at high effort, so a reply takes seconds.
- **Gates and plans:** max effort.
- **Creative specialists:** Opus 5.5 at max effort.
- **Perception at scale** (one watcher per clip, the places pass, the change reader): Sonnet 5.5, in parallel.
  Anthropic's own research system found an Opus lead with Sonnet workers beat a single Opus agent by 90%.
- **Any step can move to Opus with one setting:** cost is not the limit.

**When it acts and when it asks.**

- **It acts by itself** on work that is needed anyway and changes nothing you've seen: watching new clips,
  transcribing, reading a song, checking.
- **It asks first** for anything that makes or changes a film, spends notable money, or can't be undone. You get a
  card naming what will happen and how long it takes.

## 5. The specialists (sub-agents)

| Specialist | Job | Made of | Runs | Returns to the editor |
|---|---|---|---|---|
| Watcher (one per clip, in parallel) | Logs every moment: what, strength, hook, peak, place, people, best frame | Code signals + Sonnet 5.5 vision on contact sheets | PC lanes / Modal | Moments + "41 strong moments; best: ..." |
| Transcriber | Speech with word times, language and noise flags; lines worth keeping | faster-whisper (large-v3-turbo for talk) + a short AI pass | PC lanes | Lines per clip |
| Music analyst | Tempo, bars from the 808, the drop, booms, sections, hit density; where the song should start and end | `music/analysis.py` + a plain reading | PC lanes | Marks + "drop at 0:17, an 808 on every beat after" |
| Composer | Music made to the film's plan (the drop at the hero moment), extended or cut to fit | ElevenLabs + the house beat + `songfit` | Server / lanes | Tracks to pick |
| Style matcher | The 3 styles that suit this footage, song and person | Style tags + Sonnet 5.5 | Server | 3 style cards |
| Director | Three stories, each with its section map ("what happens where") | Opus 5.5, max effort | Lanes | Pitches with stills |
| Editor | The shot plan on the beat grid, following the chosen style's recipe | Opus 5.5, max effort + plan checks | Lanes | A plan that passes the checks |
| Section editor | Re-makes only the named stretch; everything else stays pinned | Code ops (`versions.py`) + Opus for "make it again" | Lanes | The new stretch |
| Checker | The plan and the video against every spec item | QA, requirements, pin checks + Opus vision on frames | Lanes | A checklist with evidence |
| Reach panel (4 critics in parallel) | Hook, hold, share, platform fit | Code measures + Opus 5.5 vision | Lanes | A score, reasons, three fixes |
| Exporter | Every format and preset; NLE timelines; captions; stems | ffmpeg (QuickSync / NVENC), OpenTimelineIO | Lanes / Modal | Files |
| Copywriter | Post caption, hashtags, title, hook-text options | Sonnet 5.5 | Server | Text to copy |
| Coach | Style rules offered from what you said and rated | Opus 5.5 | Server | "Keep this for next time?" |

## 6. The film spec: one source of truth

One list per film that both sides see and every specialist follows. It is built from everything you said: the note,
the cut note, what you confirmed on "what I understood", your answers, your saved style, and whatever you add by
talking. Each item shows who said it (**you**, **your style**, or **the editor**, whose suggestions are kept only on
your yes), the words that made it, its automatic check if it has one, and an on/off switch.

| Part | Example |
|---|---|
| Goal | "A dusk ferry ride: the Opera House and the Bridge are the stars"; feel: calm → rush |
| Deliver | 9:16 for Reels, 30 s, 30 fps, MP4 for Instagram |
| Style | Phonk / aura edit: cuts on the 808s, a flash on the booms, a dark punchy grade |
| Music | "Night Drive" (uploaded): starts at 0:00.11, the drop at 0:17, ends on its last bar |
| What happens where | 0-2 s hook: the bridge reveal · 2-17 s build: slow pushes · 17 s drop: the Opera House, held 3 s · then one cut per 808 · last 2 s: back to the bridge, so it loops |
| Must | Cuts on the 808s (check: beat sync) |
| Never | Nobody in shot (check: people) · No place twice (check: places) · No car shots |
| Picks | Starred: c03 0:12 the jump · Banned: c09 |

- **It is the authority.** An item switched on turns its check on, and an item switched off turns it off. Every
  AI step reads the spec, so nothing falls out of sync between steps.
- **It is the checklist.** After each version every checked item shows ✓ or ✗ with evidence ("14 of 16 cuts on the
  808s"). All ✓ and you like it: it is done.

## 7. How you talk to your editor, stage by stage

At any point you can type (or later speak), tap an answer, or point: tap a shot, drag across shots or a stretch of
time, or tap a moment or a music mark. Whatever you point at rides in your message as a chip:
*[Shot 9 · Opera House]*.

| Stage | Your editor says and shows | You | Specialists at work |
|---|---|---|---|
| 0. Before you shoot (optional) | A concept and a shot list for the idea ("the jump at golden hour, at 60 fps") | Tick off what you got | Director |
| 1. Hello | "What's it for, and where will you post it?" Only what it can't take from your memory | One line, or voice | - |
| 2. Clips land | Each clip's progress as it is watched | Keep uploading | Watcher ×N, transcriber, music analyst |
| 3. What I understood | Story, feel, flagged moments as stills, speech it found, how the song reads, the rules it will keep from your style, 0-3 questions | Tap, star or ban moments, correct it by talking | - |
| 4. Style | 3 styles that fit, each with its sample video, plus your usual | Tap one, or "like 2 but slower" | Style matcher |
| 5. Story | 3 stories with stills and their section maps | Pick, or merge ("1 with the ending of 3") | Director |
| 6. First look | A preview next to the style's sample; the numbered strip with the song's marks; the checklist | Watch | Editor, checker (sent back until it passes) |
| 7. Fix a section | Your fix said back, then only that section before and after | "Keep", or say it again | Section editor, checker |
| 8. Final | Full quality in every shape; the checklist on the real video | - | Renderer, checker |
| 9. Reach check | A score, why, three fixes (each a one-tap section fix) | Apply, or skip | Reach panel |
| 10. Export | Files for each platform; the caption and hashtags; the no-music version with where to start the platform's track; the NLE timeline | Download, copy | Exporter, copywriter |
| 11. Learn | "Keep these for next time?" | Yes / no | Coach |

## 8. The features

### 8.1 Flagged moments and speech
- Every flagged moment is shown as a still with its strength, why it was flagged and its time in the clip, grouped by
  place, with who is in shot. Moments are flagged for action (hits, peaks), beauty (light, a landmark shown whole)
  and speech (a line worth keeping).
- Star a moment and it must be used; ban it and it never is. Both are spec items with checks.
- Every clip gets a transcript. "Use this line" makes a line a moment, its sound kept.
- **Cut by editing the text:** delete a sentence in the transcript and its shot goes (talking videos, and busy
  editors' selects).
- Captions are made from the words, word by word or by phrase, in your brand font, in music films too wherever
  someone speaks. SRT and VTT files export too.

### 8.2 The song: beats, the drop, cutting it to fit
- The strip shows the song's sections, the drop and the big hits as marks.
- **Start.** The film starts where the song should: at its hook (its best part) when that suits the film, at its
  first downbeat, or at a point you tap. The exact offset is shown, so a platform's copy of the same track can be
  lined up.
- **Under speech.** The music dips wherever someone speaks.
- **End.** The film ends on the song's own last bar or phrase, not on a fade, unless you ask for one.
- **Extend and trim.** A short song repeats its strongest bars (built). A long song is cut on a bar and keeps its
  ending.
- **Trending audio** (from the owner's Instagram questions). The film exports without the music but with every sound
  effect and natural sound kept, plus where in the platform's track to start it. You post with the platform's
  licensed track and your own effects. Or you post our full mix with the platform's track at 0%.

### 8.3 Music made for the film
- The composer is briefed from the spec: the feel, the tempo the style wants, the drop placed at the hero moment, and
  a real ending. The cut always follows the track's real drop, because the model ignores a planned drop time.
- You pick from two takes, then change it in words ("darker, drop later").
- **A licensed library** (a royalty-free catalogue with an API) beside the made tracks, so creators can pick
  known-sounding songs; trending platform audio stays the platform's (see 8.2). A decision for the owner (§11).

### 8.4 Saying it back
- "What I understood" becomes the editor's first check-in, in conversation.
- Before the first cut, the section map ("what happens where") is part of the spec and of every story card.
- Every change is said back before it is made (built).

### 8.5 Styles, templates and sample videos
- **A style library**, 14 to start, from what creators make now. CapCut's 2026 list: AI one-click templates, velocity
  edits, beat sync, cinematic grading, glitch, photo montages.
  1. Beat Sync
  2. Velocity (speed ramps into the action)
  3. Cinematic Travel
  4. Phonk / Aura
  5. Calm Aesthetic
  6. Day in the Life (a vlog with speech and captions)
  7. Photo-dump Recap
  8. Hype / Glitch
  9. Retro Film
  10. Clean Minimal
  11. Story with Captions (talking)
  12. Product Promo (agencies)
  13. Before / After
  14. Seamless Loop
- **Each style is a recipe** that the editor follows and the checker checks: the pace in each section, shot kinds,
  transitions, effects and how many, speed ramps, the grade, text and captions, sound design, the music it wants,
  its best length and its platforms. Some styles need new engine pieces: glitch and RGB-split transitions, a white
  flash, film gate weave, motion blur on ramps, and word-by-word captions in films.
- **Sample videos, one per style.** All are made by the engine from the same demo footage and made music, so you see
  the style, not the content ("same clips, 14 styles"). None are copied from other creators. Where the demo footage
  comes from is a decision for the owner (§11).
- **Your film next to the style.** After a film is made it plays beside the style's sample. Two other suggested
  styles offer "try on my clips": a preview that keeps your picks and re-times them to that style.
- **Your own templates.** "Save this as my style" keeps a film's recipe and rhythm for new footage (the CLI's
  retarget already does the rhythm part).

### 8.6 Fixing only the wrong section
- **Built:** numbered shots pinned to their frames, edits by code, "make this part again" by the AI.
- **To add:**
  - pick a section by dragging across shots, by time ("0:05 to 0:09"), or by tapping the music mark it sits on;
  - tap a reason (boring, wrong place, off the beat, someone in shot, too fast, too slow), or say it;
  - a preview of only that section, before and after, rendered from the shot cache in seconds;
  - a pin check after every change proving every frame outside the section is unchanged.

### 8.7 Reach check (virality)
- **Honest framing.** No tool can promise a video goes viral. The check covers what you control and shows the
  evidence. The platforms say what they rank on: Instagram's head names watch time, sends per reach and likes per
  reach, and 2026 favours original, human content.
- **Code measures:**
  - time to the first cut and the first motion;
  - how strong the first frame is;
  - dips (stretches with nothing new);
  - beat sync on the hits;
  - the loop seam (the last frame against the first);
  - loudness;
  - exposure, shake and blur;
  - length against the platform's sweet spot;
  - text inside the platform's safe zones;
  - captions on speech;
  - repeats;
  - whether the footage is your own.
- **An Opus 5.5 panel of four critics** (hook, hold, share, platform), built on the CLI's hook rubric, watches the
  first 3 s at 5 fps and every shot's frames. Each critic scores with reasons and times.
- **The result**, named **"Ready to post?"**: a verdict (Strong / Could be stronger / Weak) with the reach score
  behind it, a grade per factor, the best cover frame, and three fixes ranked by likely effect. Each fix is a
  one-tap section fix or setting ("start on the jump at 0:04, not the wide"). It says "likely to hold attention",
  never "will go viral".
- **Later:** you can give your post's real numbers so the check calibrates against them. It uses numbers only,
  never your footage.
- **Option: an outside second opinion.** Higgsfield's Virality Predictor (May 2026) scores clips up to 15 s for hook
  and hold. It sends the video to a third party, so it needs the owner's yes.

### 8.8 Downloads
- **Presets:**
  - Reels / TikTok / Shorts: 1080×1920, 30 or 60 fps, H.264 High at a bitrate that survives the platform's
    re-encode, AAC 48 kHz;
  - the 4:5 feed (1080×1350), square 1:1, 3:4, and HD, 2K or 4K at 24, 30 or 60 fps (the Edits app's own choices);
  - YouTube 1080p and 4K;
  - landscape inside 9:16, with a blurred fill or bars and room for a title.
- **Formats:**
  - video: MP4 H.264; MP4 HEVC 10-bit (smaller); MOV ProRes 422 HQ (masters for editors and agencies); WebM
    (VP9/AV1); GIF or animated WebP previews;
  - sound: WAV or MP3 of the mix; stems (music, clip sound, effects); the no-music version;
  - text and images: SRT/VTT captions; a cover image;
  - NLE timelines: FCP7 XML (Premiere, Resolve), FCPXML (Final Cut), OTIO (Premiere's beta imports it) and a CMX3600
    EDL, with markers on the strong moments, the beats and the drop;
  - everything as one zip;
  - every shape from one edit at once, each reframed on its subject (`render/framing.py` reframes already).
- **Technology:**
  - hardware encoders: QuickSync now, NVENC or AMF where present, Modal GPUs;
  - 4K60 masters from 4K60 sources;
  - low-light clean-up before encoding (evening footage loses the most on upload), and sharpening after a downscale;
  - 2-pass encodes for size limits;
  - -14 LUFS loudness (built);
  - voice clean-up (noise removal) for talking clips;
  - later: posting straight to a platform, only with your OK each time.

### 8.9 Marketing agencies
- Workspaces with team seats and roles (owner, editor, reviewer).
- A brand kit per client: logo, colours, fonts, an end card and call to action, music rules, do's and don'ts. It is
  applied as a style and checked like one.
- Client review links: the client watches, comments at a time and approves. Each comment reaches the editor as a
  section fix.
- Deliverables from one approved edit: every shape, cutdowns (6, 15 and 30 s) and A/B hooks, each listed with its
  state.
- Licensed music only (made music or a licensed library; never a platform's trending audio in ads), captions in the
  brand font, version names and an approval log.

### 8.10 Busy editors
- **A fast lane:** upload → selects (the flagged moments), transcripts, beat markers and a rough cut → the NLE
  timeline. No questions unless something blocks.
- **A quiet mode:** the editor speaks only when asked.
- **Keyboard on the strip:** J/K/L, numbers to jump, [ and ] to set a section, R to remake, Del to remove, S to swap,
  arrows to nudge by a beat.
- **Exact numbers everywhere:** timecode, frames, beats.
- **Batch:** one spec, many films. The original file names are kept, so editors can relink their own masters.

### 8.11 Same input, same film
The same footage, music and spec give the same film: plans are kept, shots pinned, the code deterministic. New takes
come only when you ask ("3 variations"); a new idea means new, not a reskin.

### 8.12 Built on real feedback
The Aura Farming film's 19 versions are a regression set run on every change: its 4 real change requests, its places,
people and sync problems. Every rated film adds checks. Nothing is ever trained on footage.

## 9. Build order

| Slice | What | For |
|---|---|---|
| **1 (built, local)** | Your editor's backbone: the film spec with checks and a checklist per version; one conversation on every stage; a check-in after every step; talk at any time (Opus 5.5 answers, changes the spec, offers the next step as a card you tap); your style and earlier films as its memory | Everyone |
| 2 | The gate: a sample or version that fails a check of the brief goes back to the editor before it is shown (what still fails after its tries is said plainly). Say it once: the coach offers rules ("Keep this for next time?") instead of rewriting your style after every rating, and your saved rules become one clean list. Clips and music you can see: flagged moments as stills (star / ban), transcripts (use this line), the song's marks, start and end choices; the specialists start as clips land, in parallel, with progress in the conversation | Creators |
| 3 | Styles: the library, recipes followed by the editor and the checker, sample videos (after the footage decision), your film next to the sample, try a style on your clips | Creators |
| 4 | Sections: pick by dragging or by time, reasons as taps, section previews in seconds, the pin check | Creators |
| 5 | Reach check: code measures + the Opus panel; fixes as one-tap section changes | Creators |
| 6 | Export: presets, formats, the no-music version, caption and hashtags, cover, NLE timelines | Creators, editors |
| 7 | Agencies: workspaces, brand kits, review links, deliverables, licensed music | Agencies |
| 8 | Busy editors: fast lane, quiet mode, keyboard, batch, markers | Editors |
| 9 | Voice, a phone-first layout, a proactive editor (it spots problems first), shot lists before shoots, learning from real post numbers | Everyone |

Each slice is tested (the regression set, plus a walkthrough on a throwaway server) and goes live only on "push to
live".

## 9b. Order of work after the research (22 Oct, replaces the order above)

The owner's words that set it:
- "Jarvis on the screen beside you, you say it and it's done right away";
- "super fast low-quality outputs first, then the perfect one";
- "motion graphics like the insane stuff people make";
- "a virality score from a real trained model";
- "accuracy is the main thing".

| Phase | What | Why now |
|---|---|---|
| **A1. Instant preview** | The edit is data: the timeline plays in the browser from small proxies the moment it changes. The full-quality render runs in the background only when you say "make it". | Speed is the product. Every later feature previews this way. |
| **A2. Jarvis fast path** | Plain asks become exact edits by code in milliseconds, with no AI: cut, trim, swap, move, longer, shorter, speed, mute, brighter, "for Instagram". They're applied to a draft and shown at once, with undo. A fast model (Haiku 5.5, low effort) routes what code can't read; Opus only for creative work. | "Say it, it's done." |
| **A3. The index** | On upload: Gemini Flash watches every clip (picture and sound); SAM 3 tracks people and things named in words; faces are grouped (SFace); search embeddings; word-level speech with speakers. Questions become lookups. | "Cut where my face shows", answered in a second. |
| **B1. Pro motion graphics** | HyperFrames (HTML to video, Apache 2.0) as the graphics layer: titles, kinetic text, word-by-word captions, lower thirds, transitions. Placed by the face and subject masks, the word timings, the beats and the safe zones. The measured pro-motion rules and quality gates; the agent renders stills and looks before you see it. | The biggest quality gap today. |
| **B2. Styles and samples** | The style library on HyperFrames blocks and our recipes, with sample videos and "your film next to the style". | Pick, don't explain. |
| **C. Ready to post?** | Higgsfield's Virality Predictor (via their CLI/MCP; API access requested), our own model trained on SnapUGC engagement data, the rule checks and the critic panel. One-tap fixes. | Reach is why they pay. |
| **D. Create** | AI video (Higgsfield API: a Wan or LTX draft, then Kling, Seedance or Veo final), music v2 section edits and stems, SFX matched to the picture, VFX (remove an object, upscale, 3D parallax). | The "crazy" part. |
| **E. Hands-on and phone** | A timeline editor for manual tweaks (study OpenReel, MIT) that Jarvis watches and learns from; a phone layout; Jarvis by voice (streaming speech-to-text plus a voice). | Portable, and for people who want control. |
| **F. Agencies and editors** | Brand kits, review links, deliverables, NLE export (section 8.9-8.10). | Next audiences. |
| **Exports** | Presets and formats (section 8.8), with settings Jarvis remembers. | Small; built alongside A2. |

### Built so far (23 Oct): A1, A2, A3 (faces, the Gemini watcher) and B1's first blocks
- **A1, the instant preview** (`studio/preview.py`, `frontend/src/app/player.js`). Light copies of the clips (540 px).
  The edit list becomes a timeline the browser plays at once with two video layers. A plain ask makes a draft (the edit
  list, no render) in seconds; "make it" renders exactly that draft. A new ask is made on the draft on screen, with its
  numbers, so "add a title" then "add captions" gives both.
- **A2, plain asks without a model.** Swap, remove, extend, shorten and replace; titles, captions, and taking them away
  (`versions.read_simple`). A longer ask goes to the change reader, which now knows the graphics too.
- **A3, who is in the footage** (`studio/faces.py`). YuNet finds faces and SFace groups them into people. "Cut where my
  face shows" or "never show person 2" is answered from the index; a tap on a face settles which one is them.
- **A3, the watcher** (`studio/brain/gemini.py`). Gemini watches each clip's light copy with its sound. The contact-sheet
  reader takes over if it fails.
- **B1, motion graphics** (`vidosuper/motion`). Three blocks so far, each a Web Animations page with the measured
  professional timing (about 10 frames a move, arrivals easing in from oversize, exits accelerating, little overshoot):
  - a title (rise, slam, mask or split);
  - word-by-word captions (pop, karaoke or box), with phrases that break at pauses and never a blank frame between them;
  - a lower third.

  HyperFrames renders each one in headless Chrome to ProRes 4444 with transparency, cached, and FFmpeg lays it over the
  film at its time.

  **Placement** (`motion/place.py`):
  - the apps' safe zones per frame shape;
  - off the near faces in the shots under a graphic;
  - captions from the words said in shots that play their own sound;
  - long titles broken in two;
  - a place name moves to the top left in a tall frame when captions are on;
  - the picture under each title and name is looked at before anyone sees it: its brightness, from the proxies, sets
    how deep the soft pad behind the type is (a bright sky gets a deeper one).

  The preview mounts the same blocks in the player (shadow DOM, film-sized stage, seeked by the player's clock), so it
  shows what the render makes.

  **In the studio:**
  - The plan carries the graphics: a title keeps its place in the film, and a name follows its picture through swaps,
    moves and removals.
  - Captions on a film where nobody is heard are reported plainly.
  - Without the engine, a title falls back to still text and the render notes say so.

  **Real motion graphics (23 Oct, second round)**, beyond type:
  - designed transitions on cuts (panels, iris, bars, flash), covering the frame on the cut's own frame;
  - kinetic words cut in one per beat;
  - callouts (ring, corner box, arrow) on something in a shot, found by Gemini from their words and followed as the
    camera moves (the scene tracker: KLT and RANSAC), ending when it leaves the frame;
  - a route drawing itself between places, with pins and names as the line reaches them;
  - finishes: viewfinder brackets, a light sweep, light leaks, beat lines;
  - a title behind the people in the shot: huge, placed at the top of the person (u2net's human segmentation), with
    the people cut back over it in the render (u2net plus the guided filter, steadied over time).

  Cello reads them in plain words. A name, callout or finish follows its picture through changes, by the moment it
  shows; a callout whose picture is replaced goes.

  **Still to come:** the preview shows a behind-title in front of the person (the matte is made in the render).

### Built 23 Oct, later the same day: the rest of the list, without approval stops (the owner's word)
- **B2, styles:**
  - Six whole edit styles in `studio/styles.py`: Clean cinematic, Hype, Travel diary, Soft and warm, Documentary and
    Camcorder.
  - Each style sets the grade, the title move, the colour, transitions where the film changes place, what happens on
    the drop, a finish, and place names or a route.
  - A style is applied to their own film as a draft in seconds, and graphics they asked for themselves stay.
  - The film page shows a card per style, with a live sample of its title over a still of their film.
- **C, ready to post** (`studio/ready.py`):
  - A score out of 100 from measured parts: the hook, words on screen early, pace, length, captions when people speak,
    a person early, on the beat, and a loop.
  - Each weak part has a one-tap fix (open on the stronger shot, a title, captions) that plays as a draft.
  - Gemini also watches the version as a viewer: would it stop the scroll, would it hold, where people would leave,
    and why.
- **D, create:**
  - AI clips through Higgsfield's API (Kling 3, `create/higgsfield.py`): "make an AI clip of X for shot 4" makes the
    clip, logs it, and puts it in that shot's place as a draft.
  - Effects that make a new clip (`create/effects.py`), each swapped in the same way:
    - a 3D photo (Depth Anything V2 Small parallax);
    - an object removed (Gemini finds its box, the scene tracker follows it, LaMa fills it);
    - an upscale (Real-ESRGAN in tiles).
  - Sound for every graphic's move (`motion/sound.py`), from a pro library generated once with ElevenLabs sound
    generation, with the synthesized effects as the fallback.
  - A stretch of the music made again ("make the ending calmer"), the rest kept note for note (ElevenLabs inpainting,
    `music/sections.py`; full tracks are now stored for it).
- **E, hands-on:**
  - A timeline under the film: drag a shot to move it, drag its edge to change its length in half beats, tap a shot
    for its changes, and drag graphics between shots. Every change is a draft at once.
  - Hand changes are kept as feedback that the learning pass reads.
  - Cello can be spoken to (the browser's speech recognition), and there is a hands-free mode that reads replies
    aloud and listens again.
  - The microphone is allowed for our own pages only.
- **F, agencies and editors:**
  - Review links: a private link to a version; a client watches it without an account and leaves notes pinned to a
    second. The notes show on the film page and Cello reads them.
  - A brand kit on My style: colour, type and logo. Every graphic takes the colour and type, and the logo sits in a
    corner, embedded in the edit so the render and the preview match.
  - The cut for editors: FCPXML 1.10 and CMX 3600 EDL on the original clips.
- **Exports:**
  - SRT captions, 4:5 and 1:1 cuts, a ProRes 422 HQ master, a GIF, a thumbnail and MP3, from the version as made.
  - "Make it for Instagram / TikTok / YouTube / Shorts" sets the shape, makes the version in it, and is remembered
    for new films.

**Deploy needs:**
- `modal volume put flicello-models` the depth, inpaint and upscale models;
- HF_API_KEY_ID and HF_API_KEY_SECRET in .env and the Modal secret once the owner makes the Higgsfield key;
- GEMINI_API_KEY in the Modal secret.
  - **The engine on Modal:** the image installs Node 22 and the engine; the self-test renders a title. Both are first
    exercised on the next deploy.

### Slice 1 in detail (what is built)
- **The film spec** (`studio/spec.py`). It is built from the note, the cut note, the brief they confirmed, their answers,
  their saved style rules, and the items they add or switch off by talking. Each item carries its source, their words
  and its check: beat sync, nobody in shot, no place twice, clips left out. It is authoritative: the checks
  every AI step obeys come from it, and the items added in conversation reach every AI step.
- **A checklist on every version.** After each sample and version, each checked item is ✓ or ✗ with evidence (cuts on
  the hits, shots with people, places shown twice, length, pace).
- **The conversation** (`studio/agent.py`, `brain/prompts/editor_chat.md`). One thread per film, on every stage. The editor
  checks in after every step, written from the step's real results (exact numbers, no AI cost). The person can write at
  any time. Opus 5.5 answers from their memory (style, taste, earlier films and how they were rated), the spec, the
  stage and what is on screen. It can change the spec, ask one question with tap answers, offer the next step as a
  card (nothing runs until they tap it), and offer to remember a rule for their next films (kept only on a yes).
- **Where it runs.** The editor's turns run on the server, not on a render lane: replies take seconds even while a film
  renders. The steps it offers are the existing jobs (watching, story, sample, film, changes), now its specialists.

## 10. Research behind it (Oct 2026)

- **Instagram ranking:** watch time, likes per reach and sends per reach (Mosseri, Jan 2025, as reported);
  originality and human content favoured in 2026.
  [SocialPilot](https://www.socialpilot.co/de/blog/instagram-reels-algorithm),
  [eclincher](https://www.eclincher.com/articles/how-the-instagram-algorithm-works-in-2026)
- **Virality tools:**
  - OpusClip's 0-99 score from hook, flow, value and trend
    ([help](https://help.opus.pro/docs/article/virality-score));
  - Higgsfield's Virality Predictor (hook score and hold rate, clips up to 15 s, May 2026)
    ([article](https://pasqualepillitteri.it/en/news/2273/higgsfield-virality-predictor-hook-score-hold-rate-2026));
  - a rubric-based vision-language model scoring short videos ([arXiv 2512.21402](https://arxiv.org/abs/2512.21402v1)).

  None publishes validated accuracy: ours is a checklist with evidence, calibrated later on real numbers.
- **Multi-agent:** Anthropic's research system (an Opus lead with Sonnet subagents, +90.2% over a single Opus agent,
  about 15× the tokens; the lead keeps its plan in memory).
  [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)
- **Claude platform:** effort levels, prompt caching, server-side compaction and context editing for long threads, a
  memory tool. [Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing),
  [memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)
- **Editing trends 2026:** AI one-click templates, velocity and slow motion, beat sync, cinematic grading, glitch.
  [CapCut](https://www.capcut.com/help/latest-capcut-editing-trends)
- **NLE interchange:** Premiere's OTIO import and export (beta); FCP7 XML for broad compatibility; Resolve imports XML
  timelines. [Adobe community](https://community.adobe.com/t5/premiere-pro-beta-discussions/new-in-beta-otio-import-and-export/td-p/14937493)
- **Instagram specs:** 1080×1920 H.264 MP4 with AAC at 30 fps (60 supported), downscaled to 1080p on upload; the app
  has an "upload at highest quality" setting. [Argil](https://argil.ai/blog/instagram-reel-size-e350f)
- Earlier research (human-AI co-creation, point-and-say editing, chat beside a timeline) is in
  `flicello-together.md`.

## 11. Decisions for the owner

1. **Approve this plan and its order?** Clip work starts only after.
2. **Sample videos: whose footage?**
   - (a) Yours, with nobody in shot.
   - (b) Free-licence stock (Pexels or Pixabay; I download only with your yes).
   - (c) Commissioned.
3. **Models.**
   - Opus 5.5 for the editor and every creative specialist (recommended).
   - Sonnet 5.5 for watching each clip, or Opus everywhere?
4. **The per-film AI cap** ($10 now): raise it for the best result (for example to $25)?
5. **Reach check.**
   - Score out of 100, or out of 20 like your ratings?
   - An outside second opinion (Higgsfield): yes or no?
6. **The order of slices 2-6** as above, or the reach check and export earlier? (The other session's plan put
   exports, "Ready to post?" and captions first and the conversation last; this one starts with the editor, as your
   latest message asked.)
7. **Music:** made tracks only, or also a paid royalty-free library?
8. **Your saved style:** your 19 rules contradict each other (short dissolves vs. every cut hard, "no 1-second flicks" vs.
   fast cuts on every beat, three copies of "let the drop breathe"). Shall I clean them into one list of about 8 for
   you to approve? Your editor reads them every turn, so mixed orders cost every film.
9. **Which plan stays:** this one (it includes the other's additions) or `flicello-for-creators.md`? If that session is
   still open, building in both would collide in the same files.
