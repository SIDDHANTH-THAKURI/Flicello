---
name: edit-video
description: VidoSuper - turn a folder of raw clips into an edited video with a point of view. Analyses the footage if needed, a director subagent plans the story (thesis, dynamics, motifs, convention break), safe-to-post music is fetched automatically, an editor subagent cuts it with separate rhythms and a dynamic mix, the user reviews, then the final renders. Use when the user asks to edit or make a video from their footage.
---

# Edit video

Run every command from the repository root (`./vido` in Bash, `.\vido.cmd` in PowerShell).

## Inputs (ask only for what's missing; otherwise use the defaults)
- clips folder (required)
- intent: what kind of video, the feeling, anything that must be in or out (default: "a short film of
  the day with a point of view")
- platform: `youtube` (16:9) or `reels` / `shorts` / `tiktok` (9:16) or `square` (default `youtube`)
- length in seconds (default 60)
- music: by default (`auto`) it is fetched automatically: safe-to-post Creative Commons music matched
  to the director's music brief. Or a file the user gives (`--music`), `none`, or `trending` (a
  trending sound the user supplies; the tool prints a copyright caveat and the final is rendered as
  a no-music POST version plus a preview — tell the user this plainly).

## Steps
1. **Footage analysed?** `./vido perceive check "<folder>"`; if anything is missing or invalid, run the
   /analyze-footage steps first.
2. **Start:** `./vido edit start "<folder>" --intent "<intent>" --platform <p> --duration <s>
   [--music-mode auto|file|trending|none] [--music "<file>"] [--sound-name "<name in the app>"]`.
   Note the run folder. In trending mode, relay the caveat it prints.
3. **Director:** launch one `director` subagent with the run folder. Afterwards
   `./vido edit status "<run>"` reports STAGE music (auto mode) or STAGE edit.
4. **Music (auto mode):** `./vido music find "<run>"` searches, downloads a shortlist, scores each
   track against the music brief and picks the best (alternatives in `music_candidates.md`). Tell
   the user which track was chosen and its credit line.
5. **Editor:** launch one `editor` subagent with the run folder. Afterwards status reports STAGE review.
6. **Checkpoint — the user's taste decides.** Show the draft (`iterations/NN/draft.mp4`), the shot
   sheets and a short description: thesis, dynamics, the convention break, musical vs free cuts,
   sound design, length. Ask for 👍 / 👎 and a note.
   - 👍 → `./vido edit final "<run>" --open`. Give the user the output path(s) in Videos\VidoSuper and,
     with fetched music, the credit line for the video description (also saved as a -credits.txt).
   - A note or 👎 → save it to `<run>/iterations/NN/feedback.md`, run `./vido edit revise "<run>"`, and
     launch the editor again with the run folder and the feedback file. If the point of view itself is
     wrong, have the director revise `brief.json` first.
   - Other music → `./vido music find "<run>" --pick N` (a listed candidate) or
     `./vido edit set-music "<run>" "<file>"`: the cut is re-synced to the new track automatically
     (every cut keeps its beat) and a new draft is rendered.

Phase 4 (critic panel, adversary and judge looping automatically) is not built yet; until then the
checkpoint above is the review.
