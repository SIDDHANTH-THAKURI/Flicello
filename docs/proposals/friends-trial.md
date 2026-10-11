# Friends trial: VidoSuper as a web app

Status: proposal, awaiting the owner's go. Written 2026-10-11.

## 1. Goal and what the trial must prove

Put the system in the hands of about 5 people the owner knows, so they can make a video **without the owner or Claude Code
in the loop**, and learn:

1. Does the flow (upload, story pitch, sample, full video, fixes, rating) work for a stranger?
2. Do people like what comes out, and does the output match what they asked for?
3. What does one finished video really cost (AI calls, server time)?
4. Does a personal style profile make the second video better than the first?

Out of scope for the trial: payments, public sign-up, a native mobile app, the hand-scripted cartoon engine (each
cartoon is ~800 lines of code), multi-language, social-media posting.

## 2. Product shape

A **web app in the cloud**, usable from a phone or a laptop browser through a link. Invite-only. Real-footage editing in
both **9:16 and 16:9** (one edit plan, a separate framing and pacing pass per shape, not a crop).

## 3. The user flow (screens)

1. **Invite and login.** A private link, then a password-less email link. No public sign-up.
2. **New project.** Name, target shape(s) (9:16, 16:9 or both), length (15 / 30 / 60 s).
3. **Upload clips.** Resumable upload with a progress bar. Limits for the trial: 2 GB and 20 clips per project. The server
   makes small proxy copies; the originals stay private.
4. **Music.** Upload a song, or "pick for me" (royalty-free pool, or an original track as an optional paid step).
5. **Analysing.** A short wait while the system watches the clips and the song (progress shown).
6. **Story pitch.** Three story ideas as short text plus 3-4 still frames from the user's own clips. Buttons: *Use this*,
   *Another idea* (asks for a reason in one line), *Write my own*.
7. **Sample.** About 8 seconds of the chosen story, cheap to render. Buttons: *Looks right*, *Not this*, with a reason.
8. **Full video.** Rendered in the chosen shape(s), a link to watch and download.
9. **Feedback.** A rating plus tick-boxes (too fast, wrong music, boring start, bad shots, text I did not want, other) and
   a free-text box. Optional: mark a time on the video ("here, this cut").
10. **Fix round.** The user says what to change; one or two revisions per video in the trial.
11. **My style** page. Shows what the system has learned about them ("prefers calm cuts, no text, no voices") and lets them
    edit or delete any line. Plus **Delete my project** and **Delete everything**.

The gates in steps 6 and 7 stop us from rendering a full video nobody wants. That is the main cost control.

## 4. How it maps onto what already exists

| App step | Existing code | Change needed |
|---|---|---|
| Upload, proxies | `ingest/` | run headless on Linux, resumable upload endpoint |
| Analyse clips | `perceive/` plus the clip-analyst agent | call the model through the API, not Claude Code |
| Music analysis | `music/analysis.py` (librosa) | none |
| Story pitch | director agent | prompt becomes an API call; output is a typed JSON |
| Edit plan, validation | `edit/`, editor agent | same, as an API call |
| Render, QA | `render/`, `qa.py` | Linux paths (it assumes `LOCALAPPDATA`), per-job work folders |
| Critics, review loop | `loop/`, critic/judge agents | fewer critics for the trial (cost) |
| Learning | `learning.py`, `lessons.md`, `ratings.jsonl` | split into global and per-user (section 6) |

The agent definitions in `.claude/agents/` become system prompts. Whether to run them with the plain Messages API or the
Claude Agent SDK is decided in Phase 0 by a small test.

## 5. Architecture (kept deliberately small)

**Hosting decision (2026-10-12, owner): the trial runs on the owner's own PC, not a cloud server; cloud only if the trial
shows the product is ready.** It still runs in Docker (Linux containers through Docker Desktop), so moving to a cloud server
later is a redeploy, not a rewrite. Safeguards for hosting at home: the app runs in a container that sees only its own
uploads folder (never the whole disk) and does not run as administrator; a disk quota per user and a total cap, with
auto-deletion; friends reach it through Tailscale (private, no public port, no upload size limit, friends install it) or a
Cloudflare tunnel (nothing else on the PC is exposed, but its free plan limits each upload request to roughly 100 MB, so
uploads must be chunked; verify the current limit and terms); the PC stays awake and online during agreed trial hours; renders
are limited to one at a time so the owner's own work is not frozen.

One server (the owner's PC for the trial), one `docker compose`:

- **web**: FastAPI with simple server-rendered pages (Jinja2, which is already a dependency) and a little JavaScript. No
  heavy front-end framework for the trial.
- **worker**: a job queue (SQLite-backed to start) that runs the pipeline stages one job at a time per project.
- **storage**: local disk, one folder per project, with an automatic deletion timer (default 14 days after the last
  activity).
- **database**: SQLite for the trial (users, projects, jobs, ratings, profiles). Move to Postgres only if needed.
- **access**: a Cloudflare tunnel or a reverse proxy with HTTPS; invite links; per-user caps (section 7).
- **AI**: Anthropic API with the owner's key, kept only in server environment variables, never in code or chat.

Rendering is CPU work (ffmpeg, OpenCV, cairo); no GPU is needed for real-footage edits.

## 6. Learning: global, personal, per project

- **Global craft rules** (`config/lessons.md` today): objective quality only (stability, beat sync, loudness, hook,
  payoff). Read-only for the app; only the owner promotes a rule into it.
- **Personal style profile** (`users/<id>/profile.md` plus `ratings.jsonl`): that user's taste. After each rating, an AI
  step turns the ratings, tick-boxes and text into short rules; the next project reads the profile first.
- **Project memory**: what this user chose and rejected in this video.
- Cold start: 3-5 style questions at sign-up, or two sample cuts ("which would you post?").
- Rules move to the global layer only when they raise ratings for several users. The system learns from ratings and
  metadata, **never from the footage itself**.

## 7. Cost control

Prices checked 2026-10-11: Claude Sonnet 5.5 is $2 per million input tokens and $10 per million output tokens; Opus 5.5 is
$4 and $20; Haiku 4.5 is $1 and $5. Prompt-cache hits are far cheaper.

Rough per-video estimate (to be **measured in Phase 0**, not trusted): clip analysis, story, edit plan and one review at
Sonnet-class prices come to roughly **$1-3 per video**, about double with Opus. Our current Claude Code sessions use far
more tokens (the director and editor agents alone run 300K+ tokens each), so the app needs a leaner pipeline: fewer
critics, cached prompts, small images in the contact sheets.

Guard rails:
- invite-only, 5 users, 3 videos each, 1-2 revisions per video;
- a hard monthly spend limit set in the Anthropic console, and a spend counter in the app that stops new jobs near the limit;
- the user supplies the song (an original ElevenLabs track is an optional, capped step);
- the cheap sample (step 7) before every full render;
- proxies, not originals, are what the AI looks at.

Indicative trial budget: 15 videos x about $3 = about $45 of AI calls. With the trial hosted on the owner's PC there is no
server bill; a domain (about $12 a year) is optional because Tailscale needs none. **About $45-60 in total**, and the AI
calls are the one cost that cannot be avoided (the owner's Claude plan covers building, not the friends' videos). These are
estimates; Phase 0 replaces them with measured numbers.

## 8. Privacy and trust

- Private storage per project; no one else (including other trial users) can see anyone's clips or videos.
- Footage is deleted automatically after the retention time; a **Delete everything** button works immediately.
- A plain-language promise on the invite page: we never train on your footage; we keep only your ratings and your style
  rules; the model provider receives proxy frames and text to do the edit, and what it does with them is governed by its API terms (linked).
- Faces: no recognition and no cataloguing. Per-user rules such as "never show this person" live in the style profile.
- Security basics: HTTPS, invite tokens that expire, upload size and type checks, no secrets in the repo, logs without footage.

## 9. Build phases (each ends with something you can see)

| Phase | What | Done when |
|---|---|---|
| 0. Foundations | Split memory into global and per-user; run the pipeline in Docker on Linux; replace the agent calls with API calls; measure real tokens and render time per video | One real project runs end to end from the command line on Linux, with a measured cost |
| 1. Headless flow | The whole flow as a job chain with typed JSON between steps (analyse, pitch, sample, full, fix) | A friend's clips produce a sample and a full video with no Claude Code session |
| 2. Web app | The 11 screens, resumable upload, progress, ratings, style page, deletion | The owner uses it from a phone browser with no help |
| 3. Hardening | Caps, spend limit, retention timer, error pages, backups, a privacy page | A failed render or a dropped upload recovers cleanly |
| 4. Friends | Invite 2 people first, then the rest; watch how they use it | The success checks in section 10 |

Each phase gets a short demo and the owner's go before the next one starts.

## 10. How we will judge the trial

- At least 4 of 5 friends finish a video **without asking the owner for help**.
- Match between what they asked for and what they got is rated at least 4 out of 5 by them.
- Their second video is rated higher than their first, on average (does the profile help?).
- Measured cost per finished video stays under the budget cap.
- Written down: the three most common complaints, and what they say they would pay for.

## 11. Risks

- **Per-video cost and render time** may be higher than estimated: measured in Phase 0 before anything else.
- **Large phone uploads** fail on bad connections: resumable uploads and a size cap.
- **Cold start**: the first video is generic: the style questions and the sample gate soften this.
- **Taste vs match**: friends will like different things; we separate "did it do what I asked" from "do I like it".
- **Windows to Linux port** hides small bugs (paths, fonts, ffmpeg builds): Phase 0 catches them.
- **Terms and legal**: check the AI provider's terms for serving other people's content, and have a short terms and
  privacy page before inviting anyone.

## 12. What the owner needs to do

Creating the cloud account, entering payment details, setting up the API spend limit and the domain have to be done by the
owner personally. We can walk through each step. The assistant does not create accounts, enter payment details or handle passwords.

## 13. Open questions

1. Real footage only for the trial, or also one hand-built cartoon per friend?
2. Cloud choice: one small VPS (cheapest), or a managed service with more setup but fewer chores?
3. Domain and brand name: decide before the invite page.
4. Do you want a sign-up fee or "pay per extra video" later, or free until the trial ends?
