---
name: analyze-footage
description: VidoSuper Phase 2 - analyze a folder of raw video clips. Local measurements, speech transcripts and contact sheets, then clip-analyst subagents log every clip (moments with in/out times, problems). Ends with a browsable catalog. Use when the user asks to analyze, log or catalog footage, or as the first step of an edit.
---

# Analyze footage

Input: the folder with the raw clips (ask for it if it wasn't given). Run every command from the
repository root; use `./vido` in Bash (or `.\vido.cmd` in PowerShell).

1. **Prepare.** `./vido perceive prep "<folder>"` — ingests and measures if needed (cached), then
   writes transcripts, contact sheets and a brief per clip, and prints the analyst batches. The batch
   list with every clip's brief and output path is in `perception/tasks.json` under the project folder
   it prints.

2. **Skip finished work.** `./vido perceive check "<folder>"` lists clips whose analysis is missing or
   invalid. Only those need analysts; keep the batch grouping from `tasks.json`, dropping clips that
   are already valid.

3. **Dispatch analysts in parallel.** Launch one `clip-analyst` subagent per batch, all in the same
   message. Give each: the clips folder, and for each of its clips the clip id, the brief path and the
   analysis output path (copy them from `tasks.json`). Ask it to validate each clip with
   `./vido perceive check`.

4. **Verify.** Run `./vido perceive check "<folder>"`. Send any clips still missing or invalid back to
   one more `clip-analyst`, including the reported problems. Don't retry more than once; report what is
   still missing.

5. **Catalog.** `./vido catalog "<folder>"` builds `catalog.html` (a viewer) and `catalog.json`. Give
   the user the HTML path and a short overview: what the footage covers (places / chapters), the
   5–8 standout moments (clip + time + why), and notable problems (obstructions, shake, blur).

## Notes
- Analysts run on Sonnet to save the user's Pro plan usage. If a usage limit interrupts the run,
  everything finished is saved; re-running this skill later redoes only what is missing.
- Never write into the user's footage folder; all outputs live in the project workspace.
