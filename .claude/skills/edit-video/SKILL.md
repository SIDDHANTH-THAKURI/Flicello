---
name: edit-video
description: VidoSuper - turn a folder of raw clips into an edited video with a point of view. Analyses the footage if needed, a director subagent plans the story, safe-to-post music is fetched automatically, an editor subagent cuts it, a critic panel plus an adversary review every draft and a judge keeps only real improvements, looping until the bar or the round limit; the user reviews at checkpoints, then the final renders. Use when the user asks to edit or make a video from their footage.
---

# Edit video

Run every command from the repository root (`./vido` in Bash, `.\vido.cmd` in PowerShell).
`./vido edit status "<run>"` always says exactly what comes next — follow it.

## Inputs (ask only for what's missing; otherwise use the defaults)
- clips folder (required)
- intent: what kind of video, the feeling, anything that must be in or out (default: "a short film of
  the day with a point of view")
- platform: `youtube` (16:9) or `reels` / `shorts` / `tiktok` (9:16) or `square` (default `youtube`)
- length in seconds (default 60)
- music: `auto` by default (safe-to-post Creative Commons music fetched and matched to the director's
  music brief), or a file (`--music`), `none`, or `trending` (a trending sound the user supplies; a
  copyright caveat is printed and the final is a no-music POST version plus a preview — say so plainly).

## Steps
1. **Footage analysed?** `./vido perceive check "<folder>"`; if not, run the /analyze-footage steps.
2. **Start:** `./vido edit start "<folder>" --intent "<intent>" --platform <p> --duration <s>
   [--music-mode ...] [--music "<file>"] [--sound-name "<name>"]`. Relay any caveat it prints.
3. **Director:** one `director` subagent with the run folder.
4. **Music (auto mode):** `./vido music find "<run>"`; tell the user the chosen track and its credit.
5. **Editor:** one `editor` subagent with the run folder. Submitting renders the draft and the
   evidence pack.
6. **Review loop** — repeat until status says the loop is done:
   - **Critics:** in one message, launch five `critic` subagents (rubrics story, rhythm, hook, craft,
     sound) and one `adversary`, each with the run folder and the iteration folder.
   - `./vido edit review "<run>"` → weighted score, ranked issues (`review.md`), approve / revise.
   - **Judge** (from the second round on): when status asks, launch two `judge` subagents in parallel
     with the A/B folders and verdict paths it gives, then `./vido edit judge "<run>"`. The new cut
     replaces the best only if it wins both orders.
   - **Checkpoint** (after round 1 by default): show the user the best draft, the dynamics chart and a
     short summary of review.md; ask for 👍 / 👎 and a note. Save notes to
     `<run>/iterations/NN/feedback.md` (they outrank the critics). A 👎 on the point of view means the
     director revises `brief.json` before the editor continues.
   - **Revise:** `./vido edit revise "<run>"` (starts from the best cut, carries the review and
     feedback), then the `editor` again.
7. **Final:** when the loop is done and the user approves, `./vido edit final "<run>" --iteration <best>
   --open`. Give the output path(s) in Videos\VidoSuper and, with fetched music, the credit line for
   the video description (also saved as -credits.txt).

Other music at any point: `./vido music find "<run>" --pick N` or `./vido edit set-music "<run>" "<file>"`
re-times the current cut to the new track (every cut keeps its beat) and renders a new draft.

Keep the user informed in short lines between steps (what ran, score, what's next); their Pro usage
pays for the subagents, so don't launch extra rounds beyond what status asks for.
