# Flicello: make almost anything, made for each person

Status: plan, waiting for the owner's go (2026-10-16). Nothing here is built yet.

## Where we are, honestly

| Result | Rating | What it taught |
|---|---|---|
| Lottie animations behind the talker (v2) | 5/20 | Decoration around a talking head reads as "a PowerPoint". |
| 42 s story test: stock scenes acting out his words | 8/20 | Showing what he describes beats decorating him. |
| The same idea over the full 124 s | 5/20 | Good scenes are not a video. Hard cuts between unrelated sources with no glue read as "cut, other video, cut". The sound hurt: quiet phone voice, harsh synthetic noise peak-normalised above it. |

The gap to a professional edit is not one feature. It is everything that ties a video into one piece:

- a score under the whole thing;
- a cleaned, levelled voice that the music and effects duck under;
- the person composited into the scenes, not cut away from;
- transitions that link shots;
- one grade over every source;
- several layers in every moment (footage, motion text, overlays, light, texture).

Today Flicello has two styles (labels, designed Lottie). It renders with hand-written Python and ffmpeg. It checks its own output from stills only, and every person gets the same defaults.

## What is limiting us (the owner asked to be told)

1. **I cannot watch or hear a video.** I judge from still frames and audio numbers. That is how the ear-hurting mix got shown to you.
   - Fix: a video-understanding model (Gemini watches video with sound) as a critic inside the app.
   - Fix: hard audio measurements (loudness, peaks, harshness) that block a render from being shown.
   - Your ratings stay the final judge.
2. **This PC is the ceiling.** It has 16 GB RAM and no real GPU.
   - It can't run generative or high-quality matting models.
   - Renders take 20+ minutes, and Claude Code's low-memory reaper has killed jobs twice.
   - Fix: cloud GPUs for heavy steps, hosted APIs for generation.
3. **No generation providers are connected.** The Higgsfield connector is gone from this session, and the app has no keys for AI video, AI images, sound effects or stock APIs. Today's options, prices per second of output, to confirm on official pages:
   - Veo 3.1 Lite about $0.03/s; Veo 3.1 Fast about $0.08/s;
   - Kling 3.0 about $0.08/s;
   - Runway Gen-4 Turbo about $0.05/s;
   - the fal.ai aggregator hosts most of these behind one key.
   - Sora's API shut down in September 2026.
4. **I can't create accounts or pay.** You must sign up, add keys to `.env` and set spending caps (list below).
5. **Licences constrain some choices.**
   - Pexels API: free, commercial use OK, show a Pexels link, 200 requests/hour by default, more on request.
   - Pixabay API: free, commercial use OK, results must be cached 24 h.
   - Freesound: licence differs per sound; use CC0 / CC-BY only.
   - ElevenLabs Music: cleared for most commercial uses; check the plan for film/TV.
   - MatAnyone 2 (best video matting) is non-commercial unless licensed. BiRefNet is MIT.
   - Remotion (composition engine) is free for individuals and companies up to 3 people, otherwise a paid company licence.
6. **No real film footage.** The app will never pull trailers or clips of copyrighted films. Reviews get scenes that are made, stocked or generated in the film's spirit.
7. **Real people's faces in AI scenes need that person's consent.** The app asks the uploader and records the answer before it generates anyone's likeness.
8. **"Almost anything" can't be equally good on day one.** Breadth comes from a general planner. Quality comes from depth per kind of video. We build the general machine and deepen the kinds people actually request first.

## The product, in one picture

```
 request + footage
        │
 1 UNDERSTAND   transcript, video-model viewing, who it is for, the person's taste profile
 2 CONCEPTS     2-3 distinct directions with a 10 s preview each; person picks (or mixes)
 3 SCRIPT       timeline plan: per beat → what is seen, heard, written, how it links to the next
 4 SOURCE       capability router finds each asset in real time (below)
 5 COMPOSE      layered timeline in a real motion-graphics engine (Remotion)
 6 MIX          voice chain → score → ducking → effects → loudness standard
 7 SELF-CHECK   measured checks + video critic that watches with sound; fix and re-render before anyone sees it
 8 DELIVER      person rates / comments; revision runs on the same timeline
 9 LEARN        lessons per person and global; recipes and providers re-ranked
```

### 1. Any request: a request map, not a template

- A taxonomy of request kinds. Each kind is a **recipe**: what good looks like, its rubric, its typical layers and which capabilities it needs. Kinds include:
  - reviews / explainers / talking heads;
  - story re-enactments;
  - travel and event montages;
  - sports highlights;
  - music and lyric videos;
  - product ads;
  - tutorials;
  - podcast clips;
  - birthdays and weddings;
  - memes / trends;
  - cartoons;
  - property tours;
  - …
- **Mixed requests** compose recipes, for example a review with a story re-enactment and a ranking card.
- **Unknown requests** fall back to the general planner, which assembles capabilities directly. It also logs the new kind, so it becomes a recipe once it is seen and rated.

### 2. Made for each person, not one default

- Each person gets a **taste profile** built from their ratings, their words, the concepts they pick, what they revise and what they keep. It covers:
  - pace;
  - humour;
  - colour;
  - music;
  - text density;
  - realism vs. stylised;
  - how much of their own face they want on screen.
- New people get a short visual "pick what you like" step: 6 pairs of 3 s clips. That gives them a profile before their first video.
- Concepts are generated against the profile. Global lessons only set the floor (for example "never let effects louder than the voice").

### 3. Capability router: real-time, best source per shot

For each beat the router asks: what must be seen? Then it tries sources in order of fit, cost and the person's budget.

| Need | Sources (tried in order the recipe sets) |
|---|---|
| Realistic scene | stock (Pexels, Pixabay; paid Storyblocks later) → AI video (Veo / Kling / Runway / Seedance via fal.ai) → AI image + image-to-video |
| Same character across shots | one stock shoot (same creator, neighbouring ids) → AI with a character reference image |
| The person inside the scene | matting (BiRefNet / RVM; MatAnyone 2 if licensed) on a cloud GPU |
| Screens, cards, charts | own template library (phone, email, chat, rating, title, map, chart, lower third) filled by the AI |
| Motion graphics | Lottie library, Remotion components, kinetic type |
| Score | ElevenLabs Music composed to the cut's structure |
| Sound effects | ElevenLabs SFX + Freesound CC0, a curated and loudness-matched library |
| Voice | the person's own (cleaned); ElevenLabs only when asked |

- AI picks candidates from preview sheets, as it already does for Lottie, and checks continuity.
- A **provider scout** runs weekly:
  - it searches for new models and APIs;
  - it runs each one on a fixed benchmark set of shots;
  - it adds a provider only when it beats the current one for the same cost, and records why.
- This is the "look for other opportunities in real time" loop.

### 5. Composition: Remotion instead of hand-made Python frames

- A React timeline with layers, masks, blend modes, keyframes, transitions, motion text and templates, rendered headless. A cloud render costs about a tenth of a cent per short clip on Lambda.
- It is far richer than what we can draw by hand, and its components are reusable across recipes.
- The Python pipeline stays for analysis, matting and audio. Remotion does the picture.

### 6. Sound that never hurts

- **Voice chain:** denoise → high-pass → EQ → compressor → de-esser.
- **Score:** sits under the voice with sidechain ducking.
- **Effects:** sit at least 6 dB below the voice, with no harsh bands above the score's level.
- **Loudness target:** -14 LUFS integrated, true peak -1 dBTP.
- **Hard checks block a render from being shown** when any of these fail:
  - loudness;
  - peak;
  - short-term spikes;
  - voice-to-bed ratio;
  - harshness in the 2–9 kHz band.

### 7. Self-check before anyone sees it

- **Measured checks:**
  - faces never cut;
  - text never overlaps;
  - no black or frozen frames;
  - pace matches the recipe;
  - every number spoken is shown;
  - plus the audio checks above.
- **A video-model critic** (Gemini) watches the render with sound against the recipe's rubric and the person's profile, and returns timed notes.
- The app fixes and re-renders up to N times.
- Critic scores are calibrated against owner ratings. Today the critics are 4 points too generous.

## Cloud and money

- **Web app + database + storage:** a small VM plus managed Postgres plus object storage (S3 or R2). About $30–80/month.
- **GPU work** (matting, upscaling): serverless GPUs billed per second. Modal L4 is about $0.80/h, A100 about $2.50/h.
- **Picture renders:** Remotion on Lambda.
- **Rough cost per 2-minute video:**
  - stock-only: about $1–2 (Claude planning plus a little compute);
  - with 6 AI-generated scenes: about $3–10 (5 s × 6 × $0.08–0.20, plus music and compute).
  - Every person has a cap; the owner sets the totals.
- The PC stays the development machine. The app moves off it once the new pipeline is proven.

## Build order (each phase ends with a rated result, not just code)

| Phase | What | Proof |
|---|---|---|
| **1. Craft first** | Remotion composition + the sound engine + score. Rebuild the Undertone video with glue: him inside the scenes, linking transitions, layered motion text, one grade, a real mix. Hand-run, no app plumbing. | Owner rates it above 8/20 |
| **2. Story recipe in the app** | Planner → router (Pexels / Pixabay APIs, templates, fal.ai) → Remotion render → audio checks + video critic → delivery. Friend's video made end to end by Flicello. | Same or better rating with no hand work |
| **3. Breadth** | Request map with 8–10 recipes (from what friends actually ask), mixed requests, unknown-request fallback, template library. | Each recipe rated once by the owner |
| **4. Per person** | Taste profiles, first-time "pick what you like", concepts aimed at the profile, revision memory. | Second video beats the first for each tester |
| **5. Cloud** | Move the app, queue, storage and GPU work; spend caps; monitoring, which already exists, extended. | Friends use it with the PC off |
| **6. Always improving** | Weekly provider scout + benchmark; lessons → recipe updates; critic calibration. | Ratings trend up week on week |

## What the owner needs to do (I can't sign up or pay)

| Account | For | When |
|---|---|---|
| fal.ai (or Google AI / Kling direct) + spending cap | AI video and images | Phase 1–2 |
| Google AI Studio key (Gemini) | the video critic that watches with sound | Phase 1 |
| Pexels API key, Pixabay API key, Freesound key | stock and sound effects | Phase 2 |
| ElevenLabs: confirm the plan covers commercial music | score | Phase 1 |
| Remotion company licence, if Flicello becomes a company over 3 people | composition | later |
| Cloud: AWS (Lambda + S3) or Cloudflare R2, Modal, a VM host | hosting | Phase 5 |

## Decisions for the owner

1. Use Remotion as the picture engine (recommended)?
2. Budget: per video for friends, and per month in total?
3. Start Phase 1 now on the Undertone video?
