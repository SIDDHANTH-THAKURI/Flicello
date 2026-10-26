# Flicello, open source: the best video studio on your own machine

Status (2026-10-26): steps 1 to 6 built, committed and pushed to main (the repository stays private until the
owner publishes it). Since then every paid service is optional: painting (Qwen-Image-Edit), Cello's voice
(Whisper, Claude, Kokoro), watching films (Claude), music (ACE-Step 1.5 XL) and sound effects (Stable Audio 3)
run on the person's own machine. The GPU parts are unit-tested with the models faked; none has run on a real
GPU yet.

- 1 Local mode, Connect page, keys in the keychain, live health, the setup prompt: done.
- 2 Cello on Claude Code agent sessions (`bash fl ...`): done.
- 3 The editor timeline and music editing: done.
- 4 The scene designer: the footage in the page (light, shade and the camera real), GPU models where there is one,
  the idea step, the director at the desk, paint-never-draw. Quality proofs scored 4 to 6 out of 10; the latest
  proof could not paint (the Gemini key's prepaid credit ran out) and was stopped for low memory. Proofs continue
  once painting works.
- 5 Cartoons as code (motion/cartoon.py: the board desk, a desk per shot, the animation director, the cut, music and
  sound) and the Blender connector (motion/blender.py, `bash blender` at the desks): built and unit-tested; no full
  cartoon run yet (it needs Gemini for painting).
- 6 The hosted layer is gone (accounts, invites, credits, admin, analytics, email, legal pages, cloud and remote
  renderers, deploy files); the app answers only on 127.0.0.1. README and THIRD_PARTY.md (model licences, GSAP's
  terms) written. flicello.com shows the holding page from `site/` (Vercel); the full page and gallery wait for
  results worth showing.

## What it is

An open-source repo. People clone it, run one command, and get Flicello in their browser at `localhost`: a full
video studio driven by Cello, running on **their** machine with **their** Claude Code plan and **their** keys.
flicello.com becomes the marketing page: the repo, a gallery of what it made, docs.

Built for high-end machines and the best possible result. No fallbacks for weak machines, no cheap modes. The first
screen says what it needs:

- 32 GB RAM or more, a modern NVIDIA GPU with 12 GB+ VRAM (Apple Silicon M2 Pro/M3 Max and up), 100 GB free disk.
- Claude Code installed and logged in (Pro or Max plan; Max recommended for heavy design work).
- Below that it still starts, with a clear warning: "your machine is below what Flicello is made for".

## Setup: clone, run, connect

1. `git clone` and `./flicello` (or `flicello.cmd` on Windows): installs Python deps, opens the local site.
2. The **Connect** page shows every piece, each green / red / not set, checked live:
   - Claude Code (installed, logged in, which plan): the brain for everything by default.
   - Gemini key (optional since 2026-10-26): Cello's live voice, watching footage, painting assets, when this
     machine can't (local voice, Claude watcher, local painter).
   - ElevenLabs key (optional since 2026-10-26): music and sound effects, when this machine can't make them
     (music/maker.py: ACE-Step 1.5 XL for music, Stable Audio 3 Small-SFX for effects, on the GPU).
   - Optional: Anthropic API key (instead of Claude Code), Higgsfield / other video generation, Blender (3D).
   - Tools: ffmpeg, Node 22, the HyperFrames engine and its Chrome, the AI models (cut-out, depth, matting,
     speech, upscale, object removal).
3. A **copy this prompt into Claude Code** box: one prompt that has Claude Code check this machine, install what's
   missing (ffmpeg, Node, models, Blender if wanted), and fix anything red, explaining as it goes.
4. Keys are stored only on their machine (OS keychain, never in the repo, never sent anywhere but the provider).
5. Live health: each key and service is watched. Out of credit, invalid, rate limited or down: the Connect page and
   Cello say so plainly, with what to do ("your ElevenLabs credit is out: top up or I'll make the music when it's back").

## Cello: the way you use all of it

- On screen from the start (the orb), voice or typing, can be turned off.
- Not predefined prompts: Cello understands the request and does it. Simple actions run instantly through tools;
  anything creative or complex is handed to a **Claude Code agent session** with the app's tools (design a scene,
  write a cartoon, rework an edit, build a custom effect), which works until it's right and reports back.
- Asks a short question when something is unclear; otherwise acts.
- Complete control: every button, setting and panel in the app is a tool Cello can use; it sees the screen, the
  cursor, the film and the timeline.
- Fast where it can be (instant actions, instant drafts); slow where quality needs it (designed scenes, cartoons),
  always saying what it is doing and how long.

## What it can make (all designed, no templates)

- Edits from clips in any style and format (vertical, wide, square; montage, documentary, hype, cinematic...).
- Talking videos cut on the words, with designed motion graphics and the person cut out cleanly.
- Designed motion graphics and motion-design pieces: Claude writes each scene as code (HyperFrames + GSAP), paints
  real assets (Gemini image), tracks the person, looks at its own frames, fixes, and passes a strict review.
- Cartoons and animation written as code by Claude for that story (not template engines), and 3D in Blender when
  connected (Blender driven by Claude through its Python, using an existing open-source Blender MCP bridge).
- Video generation (Higgsfield or others) mixed into edits.
- From only a voice recording, only a written story, or only silent footage: it builds the rest (music, voice,
  visuals, sound).
- Music: made for the film, and edited (sections, tempo, intensity, regenerate a stretch, stems, ducking under
  speech).

## What gets fixed or built

1. **Manual editing (currently weak)**: a real editor timeline: multiple video, graphics and audio tracks; trim,
   split, ripple, drag, snap to beats and words; keyframes; speed; crop and reframe; undo. Cello and the hands
   work on the same timeline.
2. **Music editing (currently missing)**: a music track view: sections, waveform, beat grid, cut / extend / loop,
   regenerate a stretch, mix levels, ducking.
3. **The scene designer** (from today's work): faster first drafts, better motion, fewer looks, the clean cut-out
   on the GPU, more styles, proven on many unseen lines.
4. **Cartoons as code**: Claude writes the characters, rigs and animation per story, with the same look-and-fix loop.
5. **Blender**: optional connector (download Blender, point Flicello at it); scenes Claude builds in 3D.
6. **Cello everywhere**: every action a tool; agent sessions for creative work; clarifying questions.
7. **Connect page + setup prompt + live health**.

## What goes away (local, single user)

- Cloud rendering (Modal), accounts, invites, credits, billing, admin analytics, legal/consent flows, the sticker
  kit and template-first graphics as defaults. (Captions and simple titles stay as quick basics, but designed
  scenes are the main path.)

## Order of work

1. Repo shape: local-only mode, Connect page, keys on the machine, live health, the setup prompt.
2. Cello on Claude Code agent sessions with the full tool set (the backbone everything else uses).
3. Manual editor timeline and music editing.
4. Scene designer quality and speed (GPU cut-out, parallel looks) and the proof set.
5. Cartoons as code; Blender connector.
6. Gallery and flicello.com marketing page; README, install guide, licence check (GSAP's free licence terms,
   models' licences).
