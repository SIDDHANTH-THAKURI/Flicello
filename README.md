# VidoSuper

AI video editor for raw trip footage. Design and roadmap: [PLAN.md](PLAN.md).

## Setup (once)

```powershell
winget install Gyan.FFmpeg                                   # FFmpeg 9 full build
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1   # Python env in %LOCALAPPDATA%\VidoSuper\venv
.\vido.cmd doctor                                            # everything should say OK
```

From Git Bash use `./vido` instead of `.\vido.cmd`.

## In Claude Code

| Say / type | What happens |
|---|---|
| `/analyze-footage <clips folder>` | Phase 2: transcripts + contact sheets, `clip-analyst` subagents log every clip, then a catalog (`catalog.html`) of moments and problems |
| `/edit-video <clips folder> ...` | Phase 3: `director` plans the story, `editor` cuts a beat-synced draft, you review, final render lands in `Videos\VidoSuper` |

## Commands (Phase 3: editing)

| Command | What it does |
|---|---|
| `vido music find <run> [--provider auto\|jamendo\|openverse] [--pick N]` | Searches safe-to-post music (CC0 / CC BY only) for the brief's music brief, downloads a shortlist, scores tempo + energy-arc fit, picks the best; re-syncs an existing cut |
| `vido edit set-music <run> <file>` | Use a different track; the cut is re-timed so every cut keeps its beat, and a new draft renders |
| `vido sfx [--out-dir DIR]` | Built-in synthesised sound effects (whoosh, riser, impact, subdrop, swell, tick, shutter) |
| `vido music analyze <track>` | Tempo, beat grid, bar starts, sections (intro/build/peak/break/outro), drops |
| `vido music placeholder <out.wav>` | Synthesised license-free placeholder track for testing beat-synced edits |
| `vido edit start <clips folder> --intent "..." [--platform youtube] [--duration 60] [--music track]` | Starts an edit run (folder with run.json, catalog.md, music.json) |
| `vido edit status <run>` | Stage, history and the exact next step |
| `vido edit submit-brief <run>` | Validates the director's brief.json |
| `vido edit submit-edl <run>` | Validates the editor's edl.json, snaps cuts to beats, renders the draft + shot sheets + notes |
| `vido edit revise <run>` | Starts the next iteration from the current EDL |
| `vido edit final <run> [--resolution 1080p\|4k] [--outro full\|short\|none] [--open]` | Full-quality render to `%USERPROFILE%\Videos\VidoSuper\`, with the outro (the agent crew + the run's real numbers) after the film |
| `vido edit outro <run> [--style full\|short] [--open]` | Just the outro as its own clip (a preview, or a second slide) |
| `vido schema edit-brief` / `vido schema edl` | Formats the agents follow |

## Commands (Phase 4: review loop)

| Command | What it does |
|---|---|
| `vido edit evidence <run>` | Evidence pack (built automatically after each draft): cut pairs, opening frames, shot sheets, dynamics chart, stats |
| `vido edit check-critique <run> <name>` | Validates one critic's JSON (story, rhythm, hook, craft, sound, adversary) |
| `vido edit review <run>` | Weighted score, ranked issues (`review.md`), approve or revise |
| `vido edit judge <run>` | Records the judge's two verdicts (order swapped); a revision replaces the best only if it wins both |
| `vido edit revise <run> [--from N]` | Next iteration from the best cut, carrying the review and your feedback |

Critic rubrics are plain English in `config/rubrics/` — edit them to change what the panel cares about.

## Commands (Phase 2: perception)

| Command | What it does |
|---|---|
| `vido perceive prep <clips folder>` | Speech transcripts (local Whisper), timestamped contact sheets and a brief per clip; batches for analysts |
| `vido perceive check <clips folder> [--clip ID]` | Validates the analysts' `analysis.json` files, explaining any problem |
| `vido inspect <clips folder> <clip> <start> <end>` | Dense timestamped frames of one stretch (exact in/out points) |
| `vido catalog <clips folder> [--open]` | Builds `catalog.json` + `catalog.html` from measurements and analyses |
| `vido schema clip-analysis` | JSON schema the analysts follow |

## Commands (Phase 1)

| Command | What it does |
|---|---|
| `vido doctor` | Checks FFmpeg, Python packages, workspace |
| `vido analyze <clips folder>` | Probes every clip, makes proxies, measures shake/blur/exposure/motion/sound (cached) |
| `vido clips <clips folder> [--json]` | Table of clips with technical quality and bad ranges |
| `vido baseline <clips folder> [--duration 60] [--platform youtube\|reels] [--music track.mp3] [--quality draft\|final] [--open]` | Non-AI montage of each clip's best window; tests the whole pipeline |
| `vido render <edl.json> [--quality draft\|final] [--out file] [--open]` | Renders an edit recipe |
| `vido validate <edl.json>` | Checks an edit recipe without rendering |
| `vido sheet <video> [--every 1]` | Timestamped frame grids of any video, for eyeballing an edit |
| `vido samples <folder>` | Writes synthetic test clips (shaky, blurry, dark, HDR, rotated, 60 fps, no audio) |

Working files (proxies, audio, measurements, shot cache, renders) live in
`%LOCALAPPDATA%\VidoSuper\projects\<clips-folder>-<hash>\`, outside OneDrive.

## Settings

`config/default.yaml` holds every knob. Personal overrides go in `%USERPROFILE%\.vidosuper\config.yaml`
(same keys). For faster final renders on Intel laptops set `render.final.encoder: h264_qsv`
(about 3x faster than x264, slightly larger files at equal quality).

## Tests

```powershell
& "$env:LOCALAPPDATA\VidoSuper\venv\Scripts\python.exe" -m pytest -q
```

## Developer notes

- FFmpeg 9 removed `-filter_complex_script`; filter graphs are passed with `-/filter_complex <file>`.
- GPU decode: `-hwaccel qsv` alone leaves frames on the GPU (software filters then fail), and
  `-hwaccel auto` picks a slower path on this laptop. We use `-hwaccel qsv -hwaccel_output_format qsv`
  + `scale_qsv` + `hwdownload` (about 5x faster for 4K HEVC). Rotated clips use software decoding.
- This FFmpeg build's `drawtext` crashes without an explicit `fontfile=` (no fontconfig on Windows);
  draw text with Pillow or ASS subtitles instead.
- Bump `SIGNALS_VERSION` (perceive/summarize.py) or `SEGMENT_CACHE_VERSION` (render/segments.py)
  whenever the maths changes, so cached results are recomputed.
