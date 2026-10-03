# VidoSuper: AI Video Editor Plan

Status (2026-10-04): **Phases 0–3 done.** The first AI-edited video from the Sydney footage is in
`Videos\VidoSuper`, waiting for your review. Next is Phase 4: critics, adversary and judge looping automatically.
This is the living design doc. The decision log is at the bottom; usage is in [README.md](README.md).

---

## 1. What you'll get

Open Claude Code in this folder and type:

```
/edit-video "D:\Trips\Japan" fun 3-min YouTube vlog of our Japan trip, friends + food
```

(Or just say it in plain English: "edit my Japan trip footage into a 3-minute upbeat YouTube vlog".)

Then:

1. **It watches every clip.** Your laptop measures each one (shake, blur, lighting, motion, faces, loudness, speech). Claude looks at timestamped frames and reads transcripts of what people say. The result is a catalog of the best moments with exact start and end times.
2. **It writes a story outline** for your intent: hook, sections, climax, ending, target length, and how pacing builds.
3. **It cuts a first draft** and renders a quick low-res preview. A panel of specialist AI critics reviews it, plus an adversarial "harsh senior editor" whose only job is to find flaws.
4. **It shows you the rough cut** right in the chat. You give a thumbs up/down and a quick note, and it keeps refining. Each round fixes the most important problems and re-renders. A judge checks that the new version really beats the best one so far. If it doesn't, the change is reverted.
5. **It stops** when the cut clears the quality bar or hits your round or time limit. It renders the final video at full quality and writes a report of every round's changes and reasoning.
6. **It learns your taste** from your feedback, so future edits match you better.

All AI work runs on your **Claude Pro plan** inside Claude Code. No API key is needed. When you hit a usage limit, everything is already saved: come back after the reset and say "continue the edit".

---

## 2. Pipeline at a glance

```
 ┌──────────────┐   ┌─────────────────────────────┐   ┌────────────────────────┐
 │ 1. INGEST    │──►│ 2. PERCEIVE                 │──►│ CLIP CATALOG           │
 │ probe, proxy │   │ local: shake, blur, light,  │   │ moments + timestamps,  │
 │ audio, days  │   │ motion, faces, speech       │   │ scores, tags, quotes   │
 └──────────────┘   │ Claude: looks at frames     │   └───────────┬────────────┘
                    └─────────────────────────────┘               │
  reference videos ─► style profile ──┐                           │
  taste memory ───────────────────────┼──────► 3. DIRECTOR ◄──────┘
  music ─► beats / sections / energy ─┘        story outline ("edit brief")
                                                     │
                                                     ▼
            ┌───────────────────────────► 4. EDITOR (generator) ──► EDL vN
            │                                                               │
            │                                    5. COMPILE + DRAFT RENDER  ▼
            │                                       snap cuts to beats/words, 540p preview
            │                                                               │
            │                  6. CRITIC PANEL (parallel)  +  7. ADVERSARY  ▼
            │                  technical · story · pacing · hook · style · taste
            │                                                               │
            │   top issues     8. ORCHESTRATOR: merge issues, judge vN vs best,
            └───────────────── keep the best, check limits + quality bar ◄──┘
              (+ your notes from                  │ bar cleared / limit hit
               the checkpoint)                    ▼
                                   9. FINAL RENDER ─► final.mp4 + report + taste update
```

---

## 3. How the AI side runs: inside Claude Code, on your Pro plan

| Piece | What it is | Job |
|---|---|---|
| **`vido`** (Python, local) | Toolkit + **state machine** | Measures clips, makes frame grids, renders, computes edit stats, **validates every AI output**, decides the next step, and enforces stop rules and limits |
| **`/edit-video` skill** | The playbook Claude Code follows | Loop: ask `vido` for the next step → do it → hand the result back → repeat |
| **Subagents** (`.claude/agents/`) | Specialist Claudes, each with its own fresh context and rubric | clip analysts, director, editor, critics, adversary, judge |
| **You** | In the chat | Intent, checkpoint feedback, final rating |

- **Why this split:** Claude only does creative judgment. Python keeps the loop honest: it can't forget a step, skip validation, or exceed the round limit. Since every step is written to disk, a usage-limit pause loses nothing.
- **Usage limits:** each step saves its results before the next one starts. If the Pro limit hits mid-step, that one step is redone after the reset.
- **Models:** clip analysts and critics use Sonnet, which saves Pro usage. The director, editor, adversary, and judge use Opus. All of these are configurable.
- **Why this is allowed:** this is ordinary use of Claude Code with your own login. Anthropic's terms say a subscription login is for using Claude Code itself, and apps that call Claude from scripts should use API keys. So we deliberately do **not** drive your Pro login from a background script.
- **API mode (optional, later):** the same steps, prompts, and rubrics run through an API key instead. That allows hands-off and faster runs, paid per run. See section 12.

---

## 4. Key design decisions

| Decision | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Best ecosystem for video and audio analysis (OpenCV, librosa, faster-whisper). |
| Mechanical video work | FFmpeg 9 + OpenCV, free and local | Handles every phone, GoPro, and drone format. No paid cloud rendering. |
| AI reasoning | Claude Code subagents on your Pro plan | $0 extra. Subagents are real specialist agents with separate contexts. |
| How Claude "sees" video | Timestamped **contact sheets** (grids of frames) + local measurements + speech transcripts | Claude takes images and text, not video or audio. Dense frame sampling, motion and audio stats, and on-demand "zoom in" on any moment make up for it. |
| The edit itself | An **EDL** (Edit Decision List): a JSON "recipe" | The AI edits the recipe and FFmpeg renders it. Recipes can be diffed, explained, versioned, and re-rendered exactly. |
| Creative vs. mechanical split | Claude decides selection, order, cut points, pacing, music fit, transitions, and look. Code measures, enforces hard rules, and renders. | Real reasoning, while hard rules (never cut mid-word, cut exactly on the beat, stay inside clip bounds) are guaranteed. |
| Maker vs. judges | The editor runs on Opus, most critics on Sonnet, plus an optional Gemini watcher | Lowers the chance of a model grading its own work too kindly. |
| Where heavy files live | `%LOCALAPPDATA%\VidoSuper\` (Python env, proxies, renders), outside OneDrive | These are many GB, and OneDrive would try to sync them all. |
| Hardware | CPU only (no NVIDIA GPU) + Intel QuickSync for faster 4K decode/encode | All the AI thinking runs on Anthropic's servers. Local work is CPU-friendly. No VM needed. |

---

## 5. Stage details

### 5.1 Ingest (local, free)
- `ffprobe` every file for duration, frame rate (including 60/120/240 fps slow-mo sources), resolution and orientation, rotation, HDR, audio, and recording time and GPS when present. iPhone footage is often HDR, so it's converted to SDR to avoid looking washed out.
- Make small **proxy** copies (640 px) for fast analysis and previews, and extract the audio.
- Group clips into **chapters** (Day 1, Kyoto, Beach, ...) by gaps in recording time and GPS.
- Everything is cached. Re-runs skip work that's already done.

### 5.2 Perception (local measurements + Claude vision)
Local, measured ~10× per second per clip:
- **Stability**: shake, separated from intentional pans. **Focus/blur. Exposure**: blown highlights, crushed shadows. **Motion energy. Faces. Loudness, sound peaks, speech segments.**
- **Transcript** with word-level timing (faster-whisper, local). Cuts never chop a word, and funny or meaningful lines are easy to find.
- **Near-duplicate grouping**: of five takes of the same view, only the best one competes.

**Clip-analyst subagents** each take a batch of clips. They look at the contact sheets with the measurements and transcript, and write per clip:
- what's happening (scene type, subjects, action, mood, energy)
- **notable moments** with start, peak, and end times, each rated for hook potential and standalone strength
- usable vs. unusable ranges
- suggestions such as "good 2× hyperlapse" or "real slow-mo candidate (120 fps)"

**Lazy zoom:** `vido inspect <clip> <start> <end>` makes a dense 4–8 fps strip of any window so the editor can pick exact in/out frames.

Output: a **clip catalog** plus an HTML viewer showing what the AI "saw".

### 5.3 Director (story)
Inputs: your intent, the catalog, chapters, the music structure, the style profile from your references, and your taste profile.
Output: an **edit brief** with structure (hook → intro → sections → climax → ending), target length, energy curve, pacing per section, candidate moments, must-include and avoid lists, and a music plan.

### 5.4 Editor (the generator)
Builds the EDL from the brief using `vido` tools:
- `search` moments
- `inspect` (dense frames of any window)
- `beats` (where the music's beats and sections fall)
- `validate` (catch rule violations before submitting)

On revisions it gets the previous EDL plus a prioritized issue list. It must answer every issue: either **fixed** (and how) or **rejected** (and why).

### 5.5 Compile + draft render
Deterministic code first validates and tightens the EDL: cuts snap exactly onto beats or gaps between words, and bounds are checked. It then renders a 540p preview from the proxies. Shots are cached, so only changed ones re-render.

It also builds the **evidence pack** for the critics:
- a filmstrip of every shot (first, middle, and last frame)
- **cut pairs**: the last frame before and first frame after each cut
- the first 5 s at high frame density (the hook)
- measured stats: shot lengths, how many cuts land on the beat, energy curve vs. music, repetition, technical flags, and strong moments that went unused

### 5.6 Critic panel (parallel subagents, independent)

| Critic | Judges | Main evidence |
|---|---|---|
| **Technical** | shake, blur, exposure, and color consistency of the footage actually used | measurements + frames |
| **Story / content** | is every shot interesting, variety vs. redundancy, narrative logic, are the best moments missing? | catalog + filmstrip |
| **Pacing / rhythm** | shot-length variety, drags and rushes, energy arc, cuts vs. beat, ending | measured rhythm + filmstrip |
| **Hook & shareability** (viral/trend fit) | first frame, first 1–3 s, points where viewers might drop off, captions/text, loopability for Reels | hook frames + structure |
| **Style match** (if you give reference videos) | gap from the references' pacing, look, and structure | style profile vs. stats |
| **Taste** (once memory exists) | fit with your learned preferences | taste profile |

Each critic returns 0–10 scores per criterion. The rubrics are anchored: they spell out what a 4, a 7, and a 9 look like. Each critic also returns specific issues with severity (blocker/major/minor), the segment, the evidence, and a suggested fix. The rubrics live in `config/rubrics/*.md` as plain English you can edit.

### 5.7 Adversary (the generator/critic pair)
A separate Opus subagent plays a harsh senior editor reviewing a junior's cut. It hunts for the 3–5 things most likely to make a viewer scroll away, must back each claim with evidence, and can zoom into frames to check. The editor must fix or rebut every point, and both sides are logged.

### 5.8 Orchestrator (the `vido` state machine + the main session)
- Merges duplicate issues and ranks them by severity × critic weight × confidence. The top ~6 go to the editor, because fixing everything at once causes regressions.
- **Pairwise judge:** the new cut is compared against the best so far, run twice with the order swapped to cancel position bias. If the new cut wins, it becomes the best. If it loses, it's reverted and the failed fix is recorded so the editor tries something else.
- **Stops** when the weighted score reaches the target (default 8.5/10) with no blockers and at most one major issue. It also stops at max rounds, the time limit, or 2 rounds without improvement. It always returns the **best** version, never just the latest.

### 5.9 Reasoning trail
Each round's folder contains `edl.json`, a readable `edl.md`, `draft.mp4`, `critiques.md`, `adversary.md` (attacks and rebuttals), `changelog.md`, and `storyboard.html` (shot thumbnails with the reason for each choice). A run report charts scores across rounds. Example changelog:

```
Round 3  (score 7.4 → 8.1; judge prefers v3 in both orders)
- [HOOK-2] Open on the cliff-jump splash (IMG_4410 0:06.2–0:07.9) instead of the airport
  drone shot; the old opener had no motion for 2.5 s.
- [TECH-5] Replaced shot 9 (IMG_4388, shaky handheld) with the steadier take IMG_4391 0:03–0:06.
- [PACE-1] Tightened the market sequence from 14 s to 9 s (6 shots → 5) so it doesn't drag
  before the music drop at 0:42.
- Rejected [STORY-3] "move hotel-room shot to the start": breaks chronology, and the train
  shot already establishes arrival.
```

---

## 6. Human checkpoint
- After round 1 (configurable: any rounds, the story outline, or never), the rough cut and storyboard open and Claude asks you in the chat:
  - **👍 / 👎**
  - an optional **note** (e.g. "too slow at the start, more food, less hotel room")
  - optional per-shot calls ("drop 3 and 7, love 12, must include IMG_2041")
- Your note becomes a top-priority director's note. A 👎 sends the cut back to the director to re-plan the story, not just to tweaks.
- At the end you rate the final video (1–5). Want more? Say "keep refining: <note>".

## 7. Taste memory (local, plain files)
- Stored in `%USERPROFILE%\.vidosuper\taste\`:
  - `events.jsonl`: every verdict, note, keep/drop, and rating, plus the features of the shots involved
  - `profile.json`: learned stats
  - `profile.md`: a short plain-English summary of your preferences that you can read and edit
- It learns things like "you keep most food shots but cut static landscapes longer than 3 s" or "you like faster pacing than the critics suggest."
- Used three ways: as guidance for the director and editor, as the basis for a taste critic, and as a small boost or penalty when ranking moments.

## 8. Reference videos (optional, 1–3)
- The tool detects every shot cut, then measures the shot-length distribution and its shape over time, hook length, cuts per minute, transition types, use of text and captions, color and look, music tempo, and how often cuts land on the beat.
- A subagent reviews frames from each shot and writes a style guide, including signature moves (e.g. "speed-ramps into the drop", "location title cards at 0:03").
- The result is a **style profile**, a concrete target for the director, editor, pacing critic, and style-match critic.

## 9. Music & audio
- Input: one track, or a folder of tracks the director picks from by tempo, energy, length, and mood. We **start with free royalty-free music** (YouTube Audio Library, Pixabay Music).
- Local analysis (librosa): tempo, beats, bar starts, sections (intro, build, drop, outro), and energy curve.
- How the edit uses it:
  - cuts land on beats, mixing 1-, 2-, and 4-beat shot lengths
  - the biggest moment hits the drop
  - the ending lands on a musical resolution
- Mix:
  - original audio is kept where it matters (talking, laughter, a splash) with the music lowered underneath
  - J/L-cuts, so audio can lead or trail a cut
  - click-free fades
  - final loudness of −14 LUFS, the level YouTube and Instagram expect
- Instagram: for popular songs, the tool exports a no-music version so you add the song inside Instagram, where it's licensed.

## 10. Rendering & output
- **Drafts:** 540p from proxies, with each shot cached. **Final:** full resolution from the originals with high-quality x264 (or QuickSync), 1080p or 4K, in 16:9, 9:16, or 1:1.
- **Handles:**
  - HDR→SDR conversion, rotation, and variable frame rate
  - vertical clips in horizontal videos (blurred-background fill) and the reverse (face-aware crop)
  - true slow-mo from high-fps clips, and speed-ups
  - stabilization (vidstab)
  - matching color across clips, plus look presets
  - transitions: mostly clean cuts
  - title cards and captions
- **Extra outputs:** `captions.srt`, an OTIO timeline (opens in DaVinci Resolve, free), and thumbnail candidates. v1 is video-only; photos come later.

---

## 11. Configuration (all knobs, none hardcoded)

`config/default.yaml`; every value can be overridden per run.

```yaml
mode: claude_code                # claude_code (Pro plan) | api (optional, later)
agents:                          # model per role
  clip_analyst: sonnet
  director: opus
  editor: opus
  critics: sonnet
  adversary: opus
  judge: opus
loop:
  max_rounds: 5
  target_score: 8.5              # weighted, 0-10
  max_blockers: 0
  max_major_issues: 1
  stop_after_no_gain: 2          # rounds without improvement
  issues_per_round: 6
  checkpoints: [1]               # pause for your review after round 1; [] = never
limits:
  max_minutes: 180               # active work time (usage-limit pauses don't count)
  max_usd: 15                    # API mode only
critic_weights: {hook: 0.20, story: 0.25, pacing: 0.25, technical: 0.15, style: 0.10, taste: 0.05}
output:
  platform: youtube              # youtube | reels | shorts | square
  resolution: 1080p              # 1080p | 4k
  fps: auto
  loudness_lufs: -14
  captions: auto                 # on for Reels/Shorts, off for YouTube
analysis:
  frames_per_second: 0.5         # contact-sheet sampling, denser around detected events
  whisper_model: small
```

## 12. Usage & cost
- **Pro mode (default): $0 extra.** It uses your Pro allowance, which is shared with claude.ai and with our build sessions.
  - Rough guess: a first full edit of 30–40 min of footage takes 1–3 usage windows. Re-edits take less because the analysis is cached. We'll measure this in Phase 4.
  - To stretch it: Sonnet for analysts and critics (the default), a lower `/effort` for edit sessions, or fewer rounds.
  - If you keep hitting limits: wait for the reset, turn on Pro usage credits (pay-as-you-go at API rates), upgrade to Max, or use API mode.
- **API mode (optional), rough cost** for ~30–40 min of footage → ~3 min video (Sonnet 5.5 $2/$10, Opus 5.5 $4/$20 per million tokens input/output):

| Profile | Models | Rounds | First run | Re-run |
|---|---|---|---|---|
| economy | Sonnet everywhere | 3 | ~$3–6 | ~$2–4 |
| quality | Sonnet analysis + critics, Opus for the rest | 5 | ~$8–15 | ~$6–12 |
| max | Fable 5.1 director, editor, adversary | 8 | ~$25–45 | ~$20–40 |

## 13. Paid options (all optional)

| Option | Gain | Rough cost | Status |
|---|---|---|---|
| Anthropic API key | Hands-off and faster runs (API mode) | Section 12 | Not needed. Optional later. |
| Gemini "watcher" critic | Actually watches and listens to the draft. Claude can't hear audio or see motion. | ~$0.05–0.10 per round | **Revisit in Phase 4.** Paid tier only: the free tier may use uploads for training and human review. |
| Licensed music (Epidemic Sound, Artlist) | Bigger catalog, monetization-safe. With Epidemic, videos published while subscribed stay cleared forever. | ~$10–20/month | Start free. Subscribe for a month when publishing a batch, if wanted. |
| AI-generated music (ElevenLabs Music, Suno) | Track composed to the edit's exact length | ~$10–25/month | Later, optional |
| Topaz Video AI | Low-light denoise, upscaling, smooth slow-mo from 30 fps | a few hundred $ | Skip |

---

## 14. Project structure

```
VidoSuperIntelligenceEditor/
├── PLAN.md                    ← this document
├── pyproject.toml
├── vido.cmd, vido             ← launchers (PowerShell/cmd, Git Bash)
├── .env.example               ← only for optional keys later; the real .env is git-ignored
├── .claude/
│   ├── skills/edit-video/     ← the playbook Claude Code follows (Phase 3)
│   └── agents/                ← clip-analyst, director, editor, critics, adversary, judge
├── config/
│   ├── default.yaml           ← every knob
│   └── rubrics/*.md           ← critic rubrics in plain English
├── src/vidosuper/
│   ├── cli.py                 ← the `vido` command
│   ├── config.py, schemas.py  ← settings + data models (clips, catalog, EDL, critiques, ...)
│   ├── ffmpeg.py, workspace.py
│   ├── ingest/                ← scan, ffprobe, proxies, audio, chapters
│   ├── perceive/              ← stability/blur/exposure/motion, audio, transcripts,
│   │                            duplicates, contact sheets
│   ├── music/                 ← beats, bars, sections, track library
│   ├── reference/             ← reference videos → style profile
│   ├── edit/                  ← EDL validation, timeline math, beat/word snapping, diff,
│   │                            stats, baseline
│   ├── render/                ← framing, shot cache, assembly, transitions, audio mix,
│   │                            loudness, captions, color, filmstrips
│   ├── loop/                  ← state machine, stop rules, checkpoints
│   ├── memory/                ← taste memory
│   └── report/                ← storyboard + run report (HTML)
└── tests/                     ← unit tests + synthetic-clip render tests

%LOCALAPPDATA%\VidoSuper\
    venv\                      ← Python environment (kept out of OneDrive)
    projects\<clips-folder>\   ← proxies, audio, signals, shot cache, runs, renders
%USERPROFILE%\.vidosuper\taste\  ← taste memory
```

## 15. Build phases

| # | Phase | What gets built | How you can check it |
|---|---|---|---|
| 0 | Setup | FFmpeg 9 (winget), Python env, git, config, `vido doctor` | `vido doctor` is all green |
| 1 | **Mechanical backbone** ($0) | Ingest, local measurements, EDL format, renderer (cuts, fades, audio mix, loudness, HDR/rotation/vertical), non-AI "best windows" baseline | A watchable but dumb montage of your footage renders correctly |
| 2 | **AI perception** | Contact sheets, transcripts, duplicates, clip-analyst subagents → catalog + viewer | Does the AI understand your clips? |
| 3 | **AI editing** | `/edit-video` skill, state machine, director + editor subagents, `vido` editor tools, beat sync, basic captions | First AI draft vs. the baseline |
| 4 | **Refinement loop** | Critics, adversary, judge, evidence packs, stop rules, changelogs, run report; Gemini decision | Multi-round run with a readable reasoning trail |
| 5 | **You in the loop** | Checkpoint in chat, taste memory | Your notes change the next round |
| 6 | **Reference grounding** | Style profiles from example videos | Pacing and look move toward your references |
| 7 | Polish | Vertical smart-reframe, stabilization, looks, titles, word-by-word captions, auto vertical cut-down, Resolve export, publish kit, optional API mode | — |

I pause for your review after Phases 1, 2, and 3.

## 16. Risks & honest limitations
1. **Claude can't watch motion or hear audio.** It sees frames, measurements, and transcripts. Mitigations: dense sampling, cut pairs, rhythm stats, and the optional Gemini watcher.
2. **AI critics are noisy and tend to be generous.** Mitigations: anchored rubrics, measured stats, pairwise judging in both orders, keep-best, and your checkpoint.
3. **"Viral" can't be predicted.** The hook critic encodes known retention patterns, not guarantees, and there's no live trend data in v1.
4. **Phone-footage quirks** (HDR, variable frame rate, rotation, mixed orientation) are the main mechanical risk. Phase 1 targets them.
5. **Time:** the first analysis of ~40 min of footage takes about 15–30 min, then it's cached. A round takes about 5–10 min. A 1080p final render takes a few minutes; 4K takes longer. Pro usage resets add calendar time.
6. Music copyright is your responsibility. The tool can't verify licenses.

---

## Decision log
- **2026-10-03:** AI runs inside Claude Code on your Pro plan with subagents, so no API key. API mode is an optional later add-on.
- **2026-10-03:** No GPU or VM. CPU plus Intel QuickSync.
- **2026-10-03:** Music starts with free royalty-free libraries. Paid libraries are optional per batch.
- **2026-10-03:** Gemini watcher gets revisited in Phase 4, paid tier only.
- **2026-10-03:** Python env and heavy files go in `%LOCALAPPDATA%\VidoSuper` (outside OneDrive). Code stays here.
- **2026-10-03:** Final renders default to x264 (best quality). Intel QuickSync works on this laptop and is about 3× faster; it's available as a setting.
- **2026-10-03:** Decoding happens on the GPU (QuickSync decode + shrink) when possible. Analysis of 7.9 min of 4K60 DJI footage takes 2 min, down from 4 min. Rotated phone clips stay on software decoding so they never come out sideways.
- **2026-10-03:** Real-footage check: flagged "shaky" and "blurry" stretches matched the frames. Fast pans now get their own label ("fast camera move") because an editor may still use them as whip transitions.
- **2026-10-04:** Phase 2 on the Sydney footage: 16/16 clips logged by Sonnet analysts. The vision pass corrected local labels; for example, digital zoom jumps had been mislabeled as shake.
- **2026-10-04:** Phase 3 on the same footage: the director and editor (Opus) produced a 60 s cut with 28 shots and all 26 cuts on the beat. The editor's draft review found a renderer bug ("dissolve" was a speckled pixel dissolve), now mapped to a smooth crossfade. The director asked for punch-ins, which now exist (zoom up to 2.5×). Loudness ceiling is now −1.5 dBTP, checked after AAC encoding.
- **2026-10-04:** Until you supply music, edits use a synthesised placeholder track at 100 BPM. Real music needs a re-cut because tempo and sections change.

## Glossary
- **EDL (Edit Decision List):** the recipe for the video: which clip, from which second to which second, in what order, with what transitions, music, and text.
- **Proxy:** a small, fast copy of a clip used for analysis and previews.
- **Contact sheet:** a grid of frames from a clip with timestamps printed on them.
- **Subagent:** a separate Claude worker with its own instructions and memory, used for one specialist job.
- **Shot length / pacing:** how long each shot stays on screen. This is the main measure of how fast an edit feels.
- **J-cut / L-cut:** the audio starts before (J) or continues after (L) the picture cut.
- **LUFS:** a unit of loudness. YouTube and Instagram normalize to about −14 LUFS.
- **HDR → SDR:** converting iPhone HDR footage so it doesn't look washed out on normal screens.
- **Hook:** the first 1–3 seconds, which decide whether viewers keep watching.
