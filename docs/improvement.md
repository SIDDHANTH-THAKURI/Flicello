# How the studio gets better every day

Flicello learns in three ways. Only the first two run by themselves; the third is this routine.

1. **Per person (automatic, in the app).** After each rating the coach turns their words into style rules
   (`profile.json`, shown to the AI next time).
2. **Shared craft rules (by hand).** `config/studio/craft.md` and `src/vidosuper/studio/brain/prompts/*.md`.
3. **The code itself (this routine).** Skills the AI cannot get from a prompt: finding hits from sound, planning on the
   song's strikes, mirroring, framing. Each fix is code with a test.

## The loop (daily, or when someone rates a video)

1. `vido studio digest` -> a page under `<studio home>/improve/` (not in the repository: it quotes people's words).
   Counts of tags, every rating with its note, the plan that was made, what broke, what is stuck, who forgot to rate.
2. Pick at most TWO root causes, the ones that most people hit or that broke a video. Reproduce each on the stored
   project (its clips, song, note and pace are on disk). If you cannot reproduce it, say so and stop: no guessing.
3. Fix it as code (or a rule in `craft.md` when it is taste, not skill). Write the test first, watch it fail.
4. `vido studio bench` -> replays every finished video with the new code and appends the numbers to `bench.jsonl`
   with the commit and the original rating. A change must not make the videos people liked worse.
5. Run the whole test suite. Render ONE real video of the worst-rated project and look at the contact sheet.
6. Commit on a branch `improve/YYYY-MM-DD`; push the branch, never `main`. Leave the owner a note:
   `vido studio notify "what I changed, why, what to look at"`. The owner merges and restarts the studio.
7. Update `config/studio/craft.md` only with rules that more than one person's feedback supports.

## Rules the routine never breaks

- Never write into footage folders. Never touch another person's files except to read them for a replay.
- No purchases, no new accounts, no keys in chat or in the repository. Spend only what the caps allow
  (`vido studio status` shows the month so far); the routine itself replays with no AI.
- Never push `main`, never restart the live studio, never change the Funnel or the firewall.
- Heavy renders one at a time, under 10 minutes each. If memory is short, stop and say so.
- Tell the truth in the note: what is fixed and checked, what is a guess, what still looks bad.
- Friends' words stay in the studio home. Quote them in the note only without names.

## What counts as "better"

Not the model's opinion: the people's. Their rating (liked 1-10, match 1-5) and tags are the score. `bench.jsonl` and
`metrics.jsonl` show whether the plan statistics (shot length, how many hits land on a beat, repeats, mirrored shots,
folded shots) and the ratings move the right way over weeks.

## Skills to build next (in order of how many videos they would help)

See the table in the owner's last report: subject tracking for auto-reframe, upscaling and frame interpolation for soft
clips, a visual swing/jump detector, stem-based music strikes, captions, a licensed sound-effect set.
