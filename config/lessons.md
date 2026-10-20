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
- **Out of 20 since 2026-10-11 — same numbers, higher ceiling.** A 7.2/20 is the quality a 7.2/10 was
  ("don't consider it less"). The user has since seen people make "insane stuff" (the Blinkcut VFX
  reel and others), so 10–20 is new room above everything made here; "above 6 = no human has done
  this" no longer holds. Compare old and new scores as they are — never halve or double them. Score
  and predict on /20 (`vido rate` defaults to `--out-of 20`). Best so far: 7.2/20, the Lost Shadow
  cinematic cut with the drawn character and the phone fix.

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

## 5. Craft to reach for next (research, 2026-10-08; details in docs/research/2026-10-08-night.md)
- **A turn by the midpoint, a button at the end.** "Most sixty-second pieces that feel flat are missing
  the turn." Our films went calm → build → drop → more drop; the 9/10 intro and outro both turn
  ("Done." → "Can Claude edit?"; "I didn't edit this" → "Every one of them is Claude") and end on a button.
- **Pacing:** Wollongong C averaged 1.3 s per shot — cinematic-ad speed (music videos 3.5–6 s, films
  4–5 s). Keep the bursts for the peaks, give story moments 3–6 s, and land nearer 2–3 s on average.
  Cut on phrase ends rather than every beat; a **silence beat** right after the fastest montage.
- **Point of view:** tell the place through one person's eyes (Brandon Li: the festival "from the
  point of view of the guy who breathes fire"); the person who shot it can be the character
  ("The Road From Karakol").
- **Sound bridges:** at every change of place, the next place's sound arrives first (J-cut).
- **Colour:** match shots to one reference shot before the look; a per-shot boost can make neighbours
  inconsistent.
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
- **2026-10-20 horn-ok-please-cartoon Horn OK Please v1: 86.7 s 9:16, 27 shots on score D long, folk/kidart/truck engines, code-made sound** — 7.5/20. Story carried it again: a wordless emotional premise with a clear visual device (her drawings growing up with her, alive at night) and the score shaped to the beats (silence at the monsoon, the burst on the groove) rated 7.5, the same as the best talk test and above Lost Shadow's 7.2. The ceiling is execution: code-drawn rigs move stiffly, so more polish on this engine will not climb. Check every prop a pose implies (hands gripping a wheel need the wheel in frame), and draw layered images (a child on a father's shoulders) in true overlap order: her body behind his head, her legs over his shoulders in front, his hands over her shins. Next leap needs real character animation capability (AI video or a proper 3D rig), not more code polish.
- **2026-10-19 Undertone talk (friend) phase 1 style test: first 30 s in the Remotion story engine (tightened talk, room->scene swap with him in the corner, title behind his head, phone UI, SVG grade, music B bed)** — 7.5/20. Style accepted (nice): tightening the talk (124->93 s), carrying him across cuts (his room turns into the scene, he steps into the corner, grows back) and a clean -16 LUFS mix took the full-talk style from 5 to 7.5. Still under the 8 of the plot-only story test: the opening is talky and the stock scenes generic. Next raises: sound on every hit, escalation to the horror climax, a loop payoff, key words lit in the captions.
- **2026-10-17 flicello.com 'JEPA Voice' voice explainer (63 s, 16:9, designed, first real site video)** — owner: matched 3/5, liked 4/10, 'bad shots'. Causes found in the frames: 4-6 s empty screens while a layout waited for its first item; 4 of 7 picked animations never drawn (only title/keyword layouts drew them); animations sized by their canvas (a checkmark became a dot, a rocket ran into its text); 8 scenes of ~8 s each; 'Jeff' for JEPA in the title; the owner's FILM rules ('no on-screen text') shaping an explainer. Fixed in code: every layout shows its animation from the first frame and glides it aside when parts arrive, animations cropped to their drawing, 3-6 s scenes with something new every 4 s (checked), names corrected from context (+ captions via `respell`), one animation per video, rules scoped films/talks, large-v3-turbo for speech. Still weak: LottieFiles animations come in mixed styles, and emoji fall back where nothing fits.
- **2026-10-16 Undertone talk (friend) story full 124 s: 16 stock clips + phone/email screens + title + rating card, hard cuts, synthetic sfx** — 5/20. Good scenes are not a video. 8/20 on a 42 s test fell to 5/20 at full length: hard cuts between unrelated sources with no glue (no score, no transitions, him never inside the scenes, no layered elements) read as 'cut, other video, cut'. Audio: peak-normalising a quiet phone voice pushed harsh synthetic noise bursts (2-9 kHz) far above the voice - hurts ears. Next: one continuous score, voice cleaned + levelled, sfx ducked and quieter than voice, -14 LUFS + limiter; him composited into scenes, linking transitions, a single grade over everything incl. him, several layers per moment.
- **2026-10-16 Undertone talk (friend) story test 82-124 s: stock-footage story scenes + own email UI + horror grade + sound design** — 8/20. Acting out what he says beats decorating him (5 -> 8/20, new best). Story cutaways: one stock shoot for the characters (same two people, same room), cut on his exact words, our own diegetic UI (the email) for what stock can't show, one horror grade + grain over all stock, sound design under his voice (drone, ding, click, static burst, riser, hard cut to black). Real footage graded as one film reads 'realistic' without AI video.
- **2026-10-16 Undertone talk (friend) v2 designed: MODNet cut-out + Lottie behind him, full 124 s** — 5/20. Icons/Lottie stickers behind a talking head read as a slideshow, even when well cut out and on the word (5/20). The viewer wants to SEE what he describes: cutaways to full-screen cinematic/realistic animated scenes acting out his words (a story), with the talking head cutting away and back, not decorating him 100% of the time. Next: script beats -> scene shots (AI-generated realistic/animated clips), the talk as a narrator who appears between scenes.
- **2026-10-16 flicello-friend-undertone Undertone talk 'wow' sample (cut-out + emoji pops + big words + glitch)** — 1/20. Emoji pops, big words and glitch effects are NOT motion graphics. Motion graphics means designed, animated visual elements (shapes, lines drawing on, morphing illustrations, animated scenes) that explain the topic; build with a real motion-graphics toolset, not Pillow pops.
- **2026-10-11 lost-shadow-cartoon Lost Shadow cinematic cut, ink character + phone fix** — 7.2/20. One drawn ink-and-paint figure plus a real phone grip took it from 7.1 to 7.2. The grip is an IK arm, palm on the back, fingers round the edge, and a thumb scrolling a visible feed. The phone has a mint case so it reads. It goes into the pocket instead of vanishing. The dark screen mirrors the sunset. Fixing what looked broken removes a negative; it barely adds. The 1/4-scale contact sheets hid the floating phone and the palm-out hand, so check every close-up's hands and props at full size before showing. The scale is now /20 with the same numbers. The user has seen people make 'insane stuff', so 10-20 is new headroom. More polish on this engine will not climb there. The next jump needs a bigger leap: acting and motion with life (arcs, follow-through, squash, secondary motion on hair and hood) and richer worlds, judged against the best work out there.
- **2026-10-11 lost-shadow-cartoon Lost Shadow cinematic cut (29 shots, 3D puppet drawn flat)** — 7.1/10. Film-style coverage (many angles, inserts, OTS, cranes, reverse angles cut on beats) lifted the cartoon from 7.0 to 7.1, but the character is the ceiling: built as separate outlined volumes (tubes, hulls, ellipsoids) each with its own outline and shade crescent, so seams and overlaps show (shoulder over torso, neck pole, collar ring); forearms pointing at the camera become end-on discs; paddle hands; convex hulls give faceted, curveless shapes; the 3/4 nose read as a dark circle. Next: one continuous silhouette with one outline and only meaningful inner lines, smooth spline shapes, proper sleeves and finger hands, neck hidden by the hood, one light across the whole body - and show a close-up test before re-rendering.
- **2026-10-11 lost-shadow-cartoon Lost Shadow cartoon (38.6 s, 9:16, toon engine, music B)** — 7/10. Cartoon test: story, staging, shadow gags, light and music structure carried it to 7/10 on the first try. The character lost points: capsule limbs, box torso and rigid joints read as blocky. Next animated piece: design the character first (organic tapered silhouettes, overlapping soft shapes, appealing proportions, hands and hair with life), show a character sheet for approval before any scene. Scale change: future video-based edits are rated out of 20.
- **2026-10-10 "Shortcut" 10 s test (Portal x Time Bender)** - "didn't come out well ... wasn't arranged in the right way" (no score; the skills were liked). Lesson: one idea per short piece with room to land; seven tricks in 10 s (bend, freeze, portal, dive, forward, rewind, slow-mo, closing portal) gave each 1-2 s, so none landed and the story read as a list of effects. The music must drive the moments (this track had no structure to hang them on), and these concepts need footage shot for them.
- **2026-10-10 sydney+wollongong The Sketchbook Film v2 (77 s landscape, TAKE 3 intro)** — 7/10. Landscape film held 7 after fixing the user's 6 notes: real artist sketches (opensketch line art, drawn stroke by stroke, exact on the footage) instead of traced edges; calm 3 s page turns instead of 1 s flicks; a detailed hand-lettered travel map; a designed cover; two full original songs with no repeated section (breath = filter, not a reused bar). Lesson: recognisability of the drawing is the core of this style; never repeat music sections; keep transitions at a pace the eye can follow.
- **2026-10-10 wollongong The Sketchbook (21.5 s, 4 pages)** — 7/10. Best so far. What moved it: a guide character (the drawn gull) linking four never-used places into one story; one device repeated and escalating (draw, wash, dive in, real; pull back, page turn); the person as the emotional beat with the gull beside him; a colour payoff (grey pages to the sunny finale); music re-cut on its bars so every page lands on a moment; tracking so the drawn gull lives in the real shot. 'This is one way': next time bring a different approach, not more of the same.
- **2026-10-09 wollongong Drawn to Real (5 s, c32 harbour)** — 6.5/10, provisional ("just 5 sec, can't judge so fast, but this idea was nice"). Best so far and the first above 6. The idea is proven; a full-length piece has to hold it. Story pitched first with stills of a never-used clip, OK'd, then built. What moved it: one clear transformation (ink drawing, watercolour, real) with a payoff (the drawn gull stays alive in the real sky); graphics derived from the footage itself (strokes on the real edges, hatching on the real ripples, so the drawing becomes the shot exactly); the intro/outro look as the bridge into live video; sound that follows the picture (pen scratch driven by stroke activity, the hit on the reveal). 5 s kept it tight. Weak: the gull reads a bit like a paper plane; the first 0.25 s is nearly silent.
- **2026-10-09 "The crew steps out" reel (preview)** — "total disgusting crap". Second time the same
  mistake: the same Wollongong selects in the same story order (pool, lagoon, walk, wave, edge, him)
  with the outro crew pasted on top. Lesson: a new reel is new footage (clips no edit has used), a
  new story pitched shot by shot with the real clips BEFORE building, and graphics designed for
  that footage, not stickers carried over from the outro.
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
- **2026-10-16 Undertone talk, Lottie sample** — "better but not best"; face cut on the left. Lessons: designer
  animations (Lottie) landing on the words read as real motion graphics (the emoji/glitch version was "worst"); a cut-out
  must come from a real portrait-matting model, never a fixed head area; one hero visual at a time, top zone, on the word.

## 2026-10-17 · Looks + painted backdrops (voice explainers)
- The user: "same standard background" made every video read as one slide; "everything is based on how well your prompts are", and most people can't write one. So voice and designed talk videos now start with LOOKS: the art director (Opus) writes 3 very different worlds from the recording, each with a painted preview (gemini-nano-banana-2.1, $0.034 at 1K, about 25 s). People pick, "Repaint it" with a note, or describe their own. The pick paints a plate per place (2K, $0.05; JEPA 63 s = 7 places), with the preview as the style reference, and the camera moves through it.
- The looks are personal: every pick, pass, change and rating goes into profile.taste; look 1 is built from that taste in a new world, look 2 is a step away, look 3 is a surprise.
- First paper render: a 45% wash to white for dark ink killed the painting (it looked faded). Lift the paper palette only about 14-26%, most in the middle. Split-scene headlines in small accent red were unreadable over a painting: draw them in ink, bigger. Respell "Jeff's" -> "JEPA's" doubled the 's.
- The look's `camera` line must only promise moves the renderer does (push, pull, drift, rise, still, dissolve); the first looks promised whip-pans.
- Still weak: LottieFiles stickers (flat vector) clash with the painted world. Next: paint the props in the look too (image model + cut-out), so the objects match the world.
- Painted props (same day): each scene's `visual` is painted in the look on flat #00FF00 and cut out (ground found from the border, so green-ish holes inside stay; spill removed); stock Lottie only where a prop fails. JEPA paper look: brain on corkboard, scale, toggles all matched the world. A drawn contact shadow read as a grey bar under the frosted backing: props float. Orbiting stock emoji around a painted prop clashed: off when a prop is drawn. Small item icons (chips, compare cards) are still emoji/stock.
- Frosted backing over the whole frame made drawing 4.5x slower (560 s for 63 s); working only around what is drawn brought it to about 3x the bare frame.

## 2026-10-18 · Reliability and render speed (from the user's 10-point list)
- A friend's 2-minute designed talk took an hour, and everybody else's video sat at "Waiting for its turn" behind it with "about 10 min left". The renderer now works in two lanes (whole films / everything short), the waiting time counts the jobs ahead, and a watchdog takes back jobs whose renderer vanished or that stopped moving (25 min), failing them in plain words after 3 tries. Never let a person wait on a job that is not really running.
- The loader that "makes me anxious": a white shine sliding inside a 14-px fill, snapping back every 1.6 s, reads as broken. Waiting = a soft light drifting across the whole track; working = a fill that only grows, glow breathing at its tip.
- Render speed, measured on the owner's laptop (realistic designed talk, 1080x1920): drawing 670 -> 235 ms/frame with the same picture (PSNR >= 51 dB): matte refinement at half size, the person composited only inside their box, cv2 instead of numpy broadcasts, cached head glow, no person drawn under a full-screen scene, plate pixels without PIL round trips. The MODNet cut-out on the Iris Xe through DirectML: 197 vs 361 ms and the cores stay free. 2 drawing processes are best (4.8 frames/s); 3 fight over the GPU (2.3). Always profile a realistic script, not a bare one: the bare one hid the backdrop and scene costs.
- Uploads (same day): the owner's line uploads at 19 Mbit/s and downloads at 92; the app added ~1.5% on top. The fix is sending less: clips are re-encoded on the device (WebCodecs/Mediabunny) to the film's size, streamed as fragmented MP4 into the resumable upload. Browsers differ: one encoder honoured the bitrate, another made a 4K file bigger than the original, so the guard judges the outcome (projected size vs the original, finishing time vs sending the original), never the settings. Measuring quality: compare frames by index; ffmpeg's setpts=N counts in each file's own time base and made a good copy look like 20 dB.

## 2026-10-18 · Music made for the film, 'what I understood' for talks, credits
- ElevenLabs Music (music_v2_5, measured with small real calls): the song id is the `song-id` response header (also on
  /v1/music with store_for_inpainting). A short standalone chunk (a 6 s "[Intro]") comes out as one hit that decays to
  silence, and extending it kept that dip: a taste must be a real groove (about 30 s: intro, groove, ending). An
  audio-reference chunk keeps the stored audio (same loudness, onsets line up) but re-decoded, not bit-identical.
  `conditioning_ref` on the first chunk (strength high) gave the full track the taste's tempo and harmony (chroma
  likeness 0.97). A 40 s plan at 96 BPM came back at 95.1 BPM with a clean ending, but the drop at 7.6 s instead of
  the planned 12.5 s: measure the real drop, never trust the plan (as on 2026-10-07). Price: $0.15 per minute.
- So the code owns the structure (whole bars, a two-bar ending that rings out so the film ends on it) and the AI owns
  the sound (styles, tempo 70-120 so the beat tracker reads it). Short tracks are made whole on every try (what they
  hear is what they get); long ones start as a taste and the full track is made once, from it.
- A form inside another form is dropped by the HTML parser (its fields join the outer form). To put a section with its
  own forms between a form's fields and its submit button, close the form and give the button `form="id"`.
- 'What I understood' for recordings: names must be copied exactly from the transcript (checked), so a correction the
  person makes ("Cloud" is "Claude") is applied by code to every caption and on-screen text; never pay for an AI retry
  to fix a spelling. Captions now take multi-word fixes ("get hub" -> "GitHub").
- Credits: a film uses them by its length, taken at start (the difference if started again), all back if it is
  deleted unfinished, and a refund for an earlier month becomes extra credits so nothing is lost.

## 2026-10-18 · The cloud renderer (Modal)
- Same worker as the PC, `cloud=True`, in 8-core/16 GiB containers the site wakes (auto: PC first; cloud when the PC
  is off or a step waited 45 s); the owner's Claude Code steps never go there. A self-test with synthetic footage
  (selftest.py) found two real bugs before any person's video did:
  - forked drawing processes hang on Linux when the parent already runs model thread pools (speech model, cut-out):
    use the "spawn" start method (Windows always spawns, so the PC never showed it);
  - os.cpu_count() in a container is the host's (24 for 16 vCPUs paid for): every part sized its threads from it and
    they fought. Sizing to the paid CPUs (VIDO_CPUS=16) took a designed talk from 7.7 to 14.6 frames/s (PC: ~4.5),
    and keeps Modal's bill (it charges for bursting past the request) equal to what the books record.
- Four drawing processes beat six (14.6 vs 11.7 frames/s) on 16 vCPUs. CPU only is enough for now; the cut-out is
  274 ms a frame there, so a GPU is the next step if designed talks grow.
