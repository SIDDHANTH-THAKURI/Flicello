# Lessons — read this first, every edit

The living memory of what this user rewards and punishes, written after every rated final (Reflexion:
turn each result into a lesson, read the lessons before the next try). Every agent reads this before
its own instructions; the critics and the judge score on the scale below. After each rating, append
the post-mortem (section 6) and the line in `config/ratings.jsonl`.

## 1. The user's scale (score on THIS scale, not a generic one)
- **3** — competent amateur: a sequence of nice shots, some technical slips. (Sydney "Give Way" v5;
  Wollongong cut1 with shaky shots and dull colour.)
- **4** — clean and on the music, nothing wrong, nothing memorable. (Sydney v8.)
- **4.8** — stable, vividly graded, every cut on the beat, original music the user liked, real sound
  design, crazy fast/slow pacing — but still "assembled footage". (Wollongong C, our best film so far.)
- **5** — the level of the creators the user admires.
- **6** — the best edit the user has ever seen. **Above 6: "insanely good, no human has done this".**
- **9** — what the intro/outro reached: a designed idea with a premise and a payoff
  ("Can Claude edit? · Take 2" → "Shot by a human. Made with Claude.").

Our critic panel scored the drafts the user rated ~3 at **6.4–6.9**: a +3 to +4 bias. An edit that is
merely clean and synced is a 4, not a 7. Be harsh; predict the user's number.

## 2. What moved the score (evidence, in order)
1. **Concept with a payoff** — the only 9/10 parts were designed around one idea with a setup and a
   payoff, not assembled from footage. The films plateaued at 4.8 because they had polish but no idea.
   Murch's priorities: emotion 51 %, story 23 %, rhythm 10 %, eye-trace 7 % — we spent most effort on
   rhythm. Decide the feeling and the premise first.
2. **Music** — "music is everything". Stock/free libraries capped scores ("YouTube arts and crafts").
   Original music composed for the cut (ElevenLabs) lifted music to 5 and the user loved having three
   options to pick by ear (C cinematic > B Celtic > A medieval). Never "war"-like epic.
3. **Removing negatives** (shake → vidstab + measured shake per window; dull → adaptive vivid grade; the
   companion, car shots, voices, text → never) took 3 → 4.2. Necessary, not sufficient.
4. **Precision** — cuts must land within half a frame of the beat (a rounding drift once put cuts
   145 ms late and the user heard it: "music beat not syncing"). Beat grids must sit on the kick, and
   drops on the exact beat where the kick enters.
5. **Contrast in pacing** — long slow holds against 1-beat bursts, accelerating builds, a stutter into
   the drop: wanted ("crazy editing: when to go long, when to go short").

## 3. Standing rules (all from the user's own words)
- Each subject/place lives in ONE contiguous stretch; no A→B→A, no flash-forward cold open.
- No on-screen text in the film (the intro/outro titles are the exception the user asked for).
- Never show the user's companion (olive jacket). No shots from inside a car / through a windshield.
  No stray voices: check transcripts before using any clip's sound ("how do you zoom" is in c23 at 8.7 s).
- No shaky shots: pick windows whose measured shake stays low; stabilise everything handheld.
- Real colour: vivid, adaptive per shot; nothing dull.
- Hard cuts only when motivated (beat, action, drop); otherwise short dissolves centred on the beat.
- One continuous action = one shot; no stray fast insert in a calm stretch.
- Music must excite (joy, tension, drama), match the footage, never war/epic-trailer, never generic stock.
- Always: the intro (cold open) and the outro — both rated 9/10.

## 4. Process that works (and what didn't)
- Talk short first; get approval of **concept + music** before clip work (saves the user's tokens).
- Offer **3 music options** to pick by ear; design the cut on the chosen song's **real** structure
  (generated music never puts the drop where the plan says — analyse it).
- Template-first: shot lengths from the song's bars, hand-picked moments, automatic gates, then render.
- Open every result on screen; ask for a 1–10 rating; log it.
- Didn't work: the director/editor/critic loop with stock music (critics inflated, ~300K tokens per
  agent, generic results); autofill picking shots by score alone (bench, clover, selfie).

## 5. Craft to reach for next (research, 2026-10-08)
- **Scenes, not shots.** Viewers called the films "just transitions and cut-paste": a montage of
  interchangeable shots with decorative effects. Build moments as sequences — wide → closer → detail →
  what the person sees (POV) → their reaction — so a cut means "he looks, and this is what he sees".
  Give the person a reason to be there, one specific detail of the place, one surprise, and fewer
  effects. A single voiceover line in the user's own words is a strong option (ask; not stray voices).
- **Hook in 1.5–2 s** (Instagram decides there; 60 %+ 3-s hold → 5–10× reach). Strongest image or the
  question first.
- **Visual beats** (Davis & Agrawala): time in-shot impacts (a wave bursting, a footstep) onto musical
  accents, not just the cuts — footage that "dances".
- **Eye-trace**: in fast montages the next subject sits where the eye already is; centre or match
  positions across 1-beat cuts.
- **In-camera transitions**: cut mid-whip on a real fast pan; match cuts on shape/colour/motion. Keep
  ~90 % simple cuts; flashy transitions only at key moments; one signature style per film.
- **Breathing moments**: let the music drop out and natural sound take over at the emotional turn.
- **Vertical 9:16** for Reels; 30–60 s cuts drive saves/shares, 7–15 s complete best.

## 6. Post-mortems (newest first)
- **2026-10-08 Wollongong C + intro/outro** — film 4.8, intro/outro 9. Lesson: the gap between 4.8
  and 9 is a concept with a payoff. Next film: start from a premise and an emotional arc, not from the
  footage pool.
- **2026-10-07 Wollongong round 3** — 4.8; music C > B > A "matches the video". Lesson: music
  designed for the footage beats any library track; editing rhythm from the song's own bars works.
- **2026-10-07 cut4 Golden Hour** — 4.6, music 5. Lesson: original music helps; house didn't fit.
- **2026-10-07 cut3 Winter Breeze** — 4.2; stock music = "YouTube arts and crafts".
- **2026-10-06 cut1** — 3; shaky, dull, the companion visible. Lesson: gates before taste.
- **2026-10-06 "Closer" agent draft** — "pretty bad" (critics said 6.72): car shot, voice, text,
  ambient music. Lesson: critics were not calibrated; rules must be checked mechanically.
- **2026-10-05/06 Sydney "Give Way"** — 3 → 4; outro 8. Lessons: no repeats (tram ×3), motivated cuts
  only, beat sync must be exact.
