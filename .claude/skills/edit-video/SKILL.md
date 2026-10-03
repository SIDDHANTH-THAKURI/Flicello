---
name: edit-video
description: VidoSuper - turn a folder of raw clips into an edited video. Analyses the footage if needed, has a director subagent plan the story and an editor subagent cut a beat-synced draft, shows the draft for review, then renders the final. Use when the user asks to edit or make a video from their footage.
---

# Edit video

Run every command from the repository root (`./vido` in Bash, `.\vido.cmd` in PowerShell).

## Inputs (ask only for what's missing; otherwise use the defaults)
- clips folder (required)
- intent: what kind of video, the vibe, anything that must be in or out (default: "a fun highlight
  reel of the day")
- platform: `youtube` (16:9), `reels` / `shorts` / `tiktok` (9:16) or `square` (default `youtube`)
- length in seconds (default 60)
- music file (optional; without it the edit uses the original sound)

## Steps
1. **Footage analysed?** `./vido perceive check "<folder>"`. If anything is missing or invalid, run
   the /analyze-footage steps first.
2. **Start the run:** `./vido edit start "<folder>" --intent "<intent>" --platform <p> --duration <s>
   [--music "<file>"]`. Note the run folder it prints.
3. **Director:** launch one `director` subagent with the run folder. When it's done,
   `./vido edit status "<run>"` must report STAGE edit.
4. **Editor:** launch one `editor` subagent with the run folder. When it's done, status must report
   STAGE review.
5. **Checkpoint — the user's taste decides.** Show the draft (`iterations/NN/draft.mp4`), the shot
   sheet(s) and a short description: hook, structure, ending, length, cuts on the beat. Ask for
   👍 / 👎 and a note.
   - 👍 → `./vido edit final "<run>" --open`; give the user the final video's path (it goes to their
     Videos\VidoSuper folder).
   - A note or 👎 → save the feedback to `<run>/iterations/NN/feedback.md`, run
     `./vido edit revise "<run>"` and launch the editor again with the run folder and the feedback
     file. If the story itself is wrong, have the director revise `brief.json` first.

Phase 4 (critic panel, adversary and judge looping automatically) is not built yet; until then the
checkpoint above is the review.
