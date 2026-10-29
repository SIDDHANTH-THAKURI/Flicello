---
name: edit-video
description: VidoSuper - turn a folder of raw clips into an edited video with a point of view. Concept first, then original music composed for it (three options the user picks by ear), then a cut designed on the chosen song's real structure with hand-picked, gate-checked footage, sound design, the intro and the outro; every result is opened, rated 1-10 and turned into a lesson. Use when the user asks to edit or make a video from their footage.
---

# Edit video

Run every command from the repository root (`./vido` in Bash, `.\vido.cmd` in PowerShell).
**Read `config/mine/lessons.md` (or `config/lessons.md` until you have your own) first** — the user's scale (5 = creators they admire, 6 = the best edit
they've seen), what has moved it, and their rules. The user is token-conscious: talk in short lines,
get approval at the two gates below, and don't render what they haven't agreed to.

## The path that works (template-first)
1. **Footage analysed?** `./vido perceive check "<folder>"`; if not, run the /analyze-footage steps.
2. **Concept gate (ask):** in ~5 short lines propose the premise (one line a viewer could repeat),
   the emotional arc, the hook for the first 1.5–2 s and the payoff at the end, plus the places in
   order (each place once). Wait for the user's OK. A concept with a payoff is the biggest lever
   (lessons §2); polish alone plateaus at 4.8.
3. **Music gate (ask):** write ElevenLabs composition plans for **three** contrasting instrumental
   options that fit the concept (never war/epic-trailer, never generic stock), generate them
   (`./vido music compose <plan.json> "<out.mp3>"`, each costs the user's ElevenLabs credits), analyse
   each (drop, sections, tempo) and open a page with the three players. The user picks by ear. The
   model never puts the drop where the plan says: always work from the analysis.
4. **Design the cut on the song's real bars:** write a design file (song, a pool of hand-picked
   moments, a shot plan of hold / cut / hit / ramp / stutter in beats — format in
   `src/vidosuper/design.py`) and run `./vido template design <name>.design.json "<clips folder>"`;
   it picks the steadiest window or the action peak of every moment and adds the sound design. The plan:
   long slow holds where the music breathes, a build whose cuts accelerate into a stutter, the drop
   on the hero moment (slow motion + flash), 1-beat bursts against slow-motion holds and speed ramps,
   a breathing moment, the strongest moments last. Hand-pick every window and check the gates:
   no companion, no car/windshield shots, no voices in any clip sound you use (transcripts), low
   measured shake (stabilise anyway), vivid look, each place in one stretch. Add sound design
   (`sounds`: the shots' own ambience under calm stretches, a real crash + impact on the drop,
   whooshes on whips, natural sound carrying the ending) and `intro` (the cold open; title and take
   number agreed with the user). Use `./vido template retarget` to move an approved template onto a
   new song.
5. **Render:** `./vido template preview <template.json> "<clips folder>" --final --outro full`
   (`--4k` for 3840×2160). Cuts land within half a frame of their beat automatically.
6. **Check before showing:** final renders run `./vido check` automatically (also by hand:
   `./vido check <video> "<clips folder>" --template <t.json> --tail`): cuts against the beat grid,
   black frames, loudness, voices in used clip sound, shots logged with the companion or a car, shake.
   Fix every FAIL; look at the contact sheet it writes for anything no number catches.
7. **Show and rate:** open the result on screen (a comparison page when there are options). Ask for a
   1–10 rating, and a separate music rating when the music changed.
8. **Learn (every time):** `./vido rate <score> --version "<what>" --project <trip> [--music N]
   [--intro-outro N] [--critics N] --note "<their words>" --lesson "<what moved the score, what didn't,
   the rule it implies>"` — it logs `config/mine/ratings.jsonl`, puts the post-mortem on top of
   `config/mine/lessons.md` and prints the critics' bias. Update the user-taste memory. The next edit starts
   by reading the lessons. Every ~3 ratings: a short retro (promote what mattered, prune what didn't).

## The agent loop (when the user asks for it, or for long-form)
`./vido edit start` → `director` → music → `editor` → critic panel + `adversary` → `./vido edit
review` → `judge` from round 2 → revise → `./vido edit final --open`. `./vido edit status "<run>"`
always says what comes next. The critics now score on the user's scale (lessons §1); they used to be
3–4 points too generous, so log their prediction next to the user's rating and watch the gap. Each
Opus agent costs ~300K tokens of the user's Pro usage: don't launch extra rounds.

**Always show, don't just tell:** open every draft, checkpoint and final on the user's screen.
