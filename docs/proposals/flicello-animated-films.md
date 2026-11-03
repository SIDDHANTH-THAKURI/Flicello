# Animated films from a brief (the "Paper Studio" way)

Status: built 2026-11-02/03 (`motion/animated.py`, `motion/kits/paper/`, `motion/event_sound.py`,
`music/arrange.py`, `work/animated_in_studio.py`, `fl animated`). Proven on two briefs it had never seen: "Kettle & Crow"
(9:16, 45 s) rated 15/20; then, after scenes became a stack of pages (peel, dive through a hole, fold), words waiting
for their page and effects levelled under the music, "The Keeper of Wishes" (16:9, 46 s) rated 18/20. Not built yet
from this plan: the kit's model sheet (`sheet.js`), other shapes re-laid by the desks, the Ready-to-post checks for
this kind.

## Why

"The Paper Studio" (84 s, 16:9, cut paper; Videos/Flicello/paper-studio) was rated 19/20, the best result yet. It was
made by hand in one session, not by Flicello. Flicello today cannot make it: its 2D designer starts every frame from
zero (thin, drifting look), it plans moment by moment over footage instead of one story on the music, it reviews once
from still frames, and it places sounds from a plan rather than from what actually moves. This plan turns how that
film was made into a system, so anyone can ask for "an animated explainer / promo / story about X" and get that
quality, and later other looks besides paper.

## What made the film work (the things to make repeatable)

1. **A kit, not blank pages.** `lib.js` + `parts.js`: scissor-cut edges, layer shadows, paper grain, pop-ups, page
   turns, strips and tags for words, puppets whose every pose is a function of time, a camera, low-poly ridges,
   skies that recolour, Cello as layered paper discs. Every scene was built from the same parts, so the look held.
2. **One story on the music.** Brief -> story -> scenes placed on the music's measured structure (soft intro, lift,
   drums in, drums out, riser, drop, ending), each scene on bar lines, text timed for reading, one transition language.
3. **Music fitted, not hoped for.** The score was composed, then measured (beats, sections, drop), then re-arranged
   on bar lines so the drop landed where the story needed it.
4. **Many check rounds, on motion.** Stills sheet -> fix -> motion strips at every key event -> fix -> render -> spot
   check. Each round found real bugs (cliffs vanishing on a dolly, a figure squashed by a pop-up, a jumper leaving
   the monitor, a dark blink on a swap).
5. **Seek-safe by construction.** The renderer draws frames out of order; the kit's rules (state puppets, sets at 0
   applied at once, no callbacks, poses as pure functions of time, blend modes on the group with the opacity) make
   every frame exact.
6. **Sound from the film's own events.** Every pop-up, page turn, snip, stamp and plug got its sound at the moment
   the timeline does it, levelled under the music, loudness -14 LUFS.

## The plan

### 1. The paper kit (and the kit format)
- Turn `lib.js`/`parts.js` into `motion/kits/paper/` : `kit.js` (parts), `craft.md` (how to use them, the look's
  rules, the seek-safety rules), `example.html` (a short scene using most parts), `sheet.js` (draws every part on
  one page for a model sheet).
- A kit format other looks can follow later (flat vector, chalk/sketchbook, felt/clay look, blueprint, ink):
  same API names (`pup`, `to`, `popIn`, `strip`, `camera`, `pose`, transitions), different drawing.
- Every kit part logs its events (`popUp`, `turn`, `snip`, `stamp`, `land`, ...) with times, for sound (step 6).

### 2. A new film kind: "animated" (no footage needed)
- Asked from Cello in plain words ("an 80 s animated explainer of my app", "a paper-cut story about ..."), with
  optional facts to cover (features, names, links) and a shape (16:9, 9:16, 1:1, 4:5).
- Lives beside the cartoon engines (`cartoon.py` board -> shots), reusing its run/resume and desks.

### 3. Story and music first
- A director desk writes the treatment: hook, story beats, what each scene shows (a little world per idea, never
  text standing in for graphics), the lines on screen, the cast (made once from kit puppets), the palette.
- Music: compose (ACE-Step locally, or ElevenLabs if connected), measure it (beats, sections, drop, key), and
  re-arrange on bar lines to the story's needs (automating what was done by hand for this film).
- The scene list is then snapped to the measured bars: scene starts, the drop, the reveal, the ending ring.
- Shown to the person as a short pitch (story + music) before any scene is built, as now.

### 4. Scenes at desks, one film page
- One desk per scene, side by side, each writing its scene file with the kit and the film's bible (palette, cast,
  transition language, text style). Scenes are blocks of one page (like `s_open.js`, `s_spreads.js`, `s_home.js`), so
  transitions (page turns, zoom through a door, a window that becomes the next scene) cross scene borders.

### 5. Checks that look at motion, several rounds
- `bash look`: a stills sheet at the times the scene names. `bash motion`: strips of 8 frames over each key event
  (pop-ups, transitions, the drop). `bash lint`: the seek-safety rules and layout checks (text off the frame, text
  over text, a thing hidden behind the tag, a figure out of its frame, an empty or flat frame).
- A director review per scene, then over the whole film from strips; fix rounds until it stops finding problems
  (capped), not one pass.

### 6. Sound from events
- The rendered page reports its kit events; each event type has a sound (code-made like this film's `sfx.py`, or
  Stable Audio 3 for named sounds), panned by where it happens, mixed under the music, -14 LUFS, true peak -1.

### 7. Render and deliver
- One render of the whole page (workers by free memory), the mix, the mux; the Ready-to-post checks; other shapes
  re-laid by the desks when asked (not cropped).

### 8. Prove it on briefs it has never seen
- Two new briefs, not this film: e.g. a 60 s 9:16 explainer for an unrelated product, and a 45 s paper story.
  The owner rates them /20; the bar is this film's 19.

## Order

1 kit -> 2 kind + 3 story/music -> 4 desks -> 5 checks -> 6 sound -> 7 render -> 8 proof. Then more kits.

## Later (separate discussion)

Making Flicello itself simple: fewer settings, one clear way to ask, and output that matches what these hand-made
films do. To be designed with the owner once this works.
