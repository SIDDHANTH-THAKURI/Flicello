# The paper kit: a film in layered cut paper

The bar is "The Paper Studio" (84 s, 16:9), which its maker rated 19/20 ("so clean and perfect... just wow"). What
made it: every idea was a little paper world, not words on a background; every frame was full (sky, land, things,
a character, words on paper); everything moved like a pop-up book on the music's beats; one look from start to end.
example.js is its first two scenes, written with this kit: read it before you write anything.

## The look
- Layered cut paper: every shape is a paper cut-out (`cutShape`, `cutRect`, `cutCircle`, `cutEllipse`, `hill`,
  `lowpoly`): slightly irregular scissor edges, a pale core along the cut (the kit adds it), a soft shadow on the
  layer behind (`layer(parent, depth)` or `pl(..., depth)`: 1 near-flat, 2 a card, 3 a thick card or a figure).
- Depth comes from layers: a sky of overlapping bands (`skyBands`), far land, middle land, near land, things,
  characters, words on top. Nearer layers are darker and cast bigger shadows; far ones are paler.
- A palette per scene of 5 to 8 colours, saturated but soft (paper is never neon). Change the palette to change the
  mood (`palette(...)` recolours a whole scene: night to dawn, dawn to a grey stuck dusk).
- Paper grain and a soft vignette are over the whole film already. Light is drawn: stepped translucent discs
  (`sun`, `orb`, glows), rays (`sunburst`), never a blur.
- Never a flat empty page: a backdrop always has something on it (stripes of wallpaper, bands, stars, pigeonholes,
  cork dots, a window with a view). Never a plain colour box standing in for a thing: draw the thing.
- The bottom of the frame is never an empty band: a near layer crosses the bottom edge in every scene (grass and
  flowers, a counter with things on it, a railing, reeds, a desk edge, cobbles, waves with a buoy), darker and with a
  bigger shadow than what is behind it. Flat water or a bare road with one small thing on it reads as empty.
- Small details that reward a second look: birds crossing, a REC dot blinking, a file named final_v7_REAL.mp4, a
  bookshelf of coloured spines, a mailbox, a chimney's smoke.

## The story in pictures
- Each idea is a little world you can see: "cuts your clips on the beat" is a film strip snipped by scissors whose
  pieces hop onto a timeline on the beats; "runs on your machine" is a house whose window holds the machine. Words
  name the idea; the picture shows it.
- Every scene says its one line on paper: a cream `strip` (headline), a kraft `tag` on a string (a label), a
  `bubble` (someone speaking), `captions` (words landing on beats). Lines are short (up to 9 words; two strips
  stacked for longer ones, each tilted a little differently). The person's facts are copied exactly (names, numbers,
  links), never paraphrased.
- Characters act: `person` (side view, walks, types, slumps, talks, cheers, points), `presenter` (front, talks),
  `fox`, `orb` (a glowing presence), or your own, built the same way (a `pup` per part, `rig` + poses). Poses move
  on twos (12 a second): stop-motion, not smooth tweening.

## Text: readable on a phone
- Sizes (at a 1080 px short side; multiply by `U` for other shapes): headline strips 72 to 92, second lines 46 to
  60, tags 46 to 52, bubbles 44 to 50, small labels 28 to 34. Nothing a viewer must read under 28 (x U).
- Hold a line on screen at least 0.5 s + 0.3 s a word before it changes; a closing line at least 4 s and 2 s before
  the end. Text never runs off the frame or hides behind another thing (a tag over a planet hides the planet).
- Leave the frame's edge alone: keep reading text 60 px (x U) inside it.

## Motion
- Everything arrives like a pop-up book: layers `popUp` from their base line one after another (0.05 to 0.15 s
  apart, back to front), things `popIn`, labels `stamp`, strips `stripIn`, tags `tagIn`, things `drop` from above.
- Things land ON the music: `B(k)` is the k-th beat, `BAR(k)` the k-th bar (the film's measured music); big things
  land on bar lines, small ones on beats. `kickAt(t)` and `envAt(t)` (0..1) make things pulse with the drums.
- The camera never sits still: a slow push in (`to(cam, {s: 1.04}, T, E - T, 'none')`), a move to what matters,
  a `shake` before a big moment, a dive through a door (`aim`).
- Loops (wings, wheels, gears, clocks, waves, a walk) are functions of time in `LIVE` (pure: the same t always gives
  the same picture).
- Characters on twos: `step12(t)`; `walk(fig, a, b, x0, x1, ...)`; `pose(fig, a, b, P_TYPE)`.
- Every scene changes over its time: something arrives, something happens, something leaves. A scene that only
  appears and then holds is unfinished.

## Sound
The kit logs a sound for what it does (a fold, a pop, a stamp, a snip, a page turn, footsteps). Add the rest with
`sfx(t, type, opts)`: a whoosh when something flies (`{dur}`), keys while someone types (`{until}`), ticks of a
clock (`{until}`), a chime when something magical happens, a splash, a clack (a lock), a thump (a book closing), a
drop (something small landing in place: a plop), `'say'` with `{sound: 'a bike bell ringing twice'}` for anything
else. The names are exactly these: flap, flick, turn, snip, stamp, tick, keys, ticks, step, steps, pop, pling,
chime, whoosh, swish, boom, drop, plop, shimmer, splash, spark, clack, thump, chirp, creak, scribble, rumble, hum,
say (`bash lint` names any other). The film's big hit at the drop is added for you: don't add booms of your own on
it. Sounds are levelled under the music for you, so a sound for every small thing is fine; `{gain}` only sets
one sound against the others.

## Writing a scene (scenes/<id>.js)
```js
{ // the scene's own block: names inside it never collide with another scene's
  const { C, cam, T, E } = scene('<id>', <start>, <end>);   // C: its page (draw into it); T/E: its start and end
  to(cam, { s: 1.04 }, T, E - T, 'none');                    // the camera breathes in
  const sky = skyBands(C, [...], [...], 101);
  ...                                                        // back to front: sky, far, middle, near, things, people, words
  const t1 = strip(C, 'The line on paper.', W / 2, 130 * U, 80 * U, { accent: 'on paper.', tilt: -1.2 });
  stripIn(t1, T + 1.4);
}
```
- Draw in film pixels: `W`, `H` (the film's size), `U` (scale for the short side). Everything you add goes under
  `C` (or a layer inside it).
- Times are film seconds: your scene runs from `T` to `E`; use `T + 1.2` or `B(k)` / `BAR(k)` for beats and bars.
- When the way out of your scene is a zoom, say where: `exitAt(scene, x, y)` (a door, a screen, a window): the
  camera dives into that point and the next scene opens through a hole cut there, so make it a thing worth diving
  into, with something inside it (a view, a glow), not a flat dark rectangle.
- The ways between scenes (page turns, zooms, folds) are added for you from the timing: don't draw them. The scenes
  are a stack of pages: yours lies under the one before it for about half a second before T and shows as that page
  is turned, cut open or folded away. So draw it whole at T: anything you set at exactly T (`set`, `show`, `from`)
  is there from the moment your page shows, and the things that pop in after T are hidden until then. Words you
  bring in near T (`stripIn`, `tagIn`, `captions`) wait until your page is all there (about T + 0.5 after a turn).

## Rules that keep every frame exact (the renderer jumps to any frame, in any order)
- Animate ONLY with the kit's tweens (`to`, `from`, `set`, `show`, `life`, `popUp`, `popDown`, `popIn`, `popOut`,
  `stamp`, `focusIn`, `stripIn`, `stripOut`, `tagIn`, `drop`, `walk`, `pose`) or `tl.to(node, {attr: {...}})` for
  colours, or a pure function of time in `LIVE`. Never `gsap.to`, `tl.call`, `setTimeout`, `setInterval`,
  `requestAnimationFrame`, `Date`, `performance.now` or `Math.random` (use `rng(seed)`).
- A LIVE function sets everything it owns from `t` alone, for every t (no "if it already moved"), and owns those
  properties alone (don't also tween them; put a `pup` inside a `pup` when both need to move the same thing).
- Something that is hidden at first must be hidden at time 0 (`hide(g)`, or the `pop*`/`*In` helpers, which do it).
- Never change a shared thing in the middle of the film by a callback: make two things and show one, then the other.
- A tween reads where a thing is when it starts: to move about the page centre, use `dolly`.
- `put` happens for you; never write a `transform` attribute on a puppet yourself.

## The kit (all of it is global)
- Puppets: `pup(parent, {x, y, r, s|sx|sy, o}, attrs)`, `layer(parent, depth, state)`, `pl(parent, baseY, depth)`
  (a layer that pops from its base: draw in `.__in` in page coordinates), `to`, `from`, `set`, `hide`, `show`,
  `life`, `popUp`, `popDown`, `popIn`, `popOut`, `stamp`, `focusIn`, `drop`, `dolly`.
- Camera: `camera(parent)`, `aim(cam, x, y, zoom)` (the camera state that puts a point at the centre), `shake`.
- Paper: `cutShape(parent, pts, fill, seed, amp, extra)`, `cutRect(parent, x, y, w, h, fill, seed, radius)`,
  `cutCircle`, `cutEllipse`, `shape` (exact edges), `hill(parent, fn, x0, x1, fill)`, `bump(base, amp, cx, w)` (a hill
  function), `crest` (light along a hill), `lowpoly(parent, {top, amp, n, col, shade, rim, seed, sunX, snow})`,
  `rock`, `backdrop(parent, col, stripes)`, `skyBands(parent, tops, cols, seed)`, `palette(root, table, times, durs)`,
  `recolor`, `fill(node, col, at)`.
- Sky and land: `sun`, `moon`, `crescent`, `star`, `starfield(parent, n, [x, y, w, h], col, seed)`, `cloud`, `bird`,
  `pine`, `roundTree`, `bush`, `grass`, `flowers`.
- Words: `strip(parent, text, x, y, size, {accent, accentCol, tilt, fill, ink, font, weight, italic})`, `stripIn`,
  `stripOut`, `tag(parent, 'line one\nline two', x, y, size)`, `tagIn`, `bubble(parent, text, x, y, size, {tx, ty})`
  (tail toward tx, ty), `inkStamp(parent, 'TEXT', x, y, size, rot)`, `captions(parent, words, times, x, y, size)`,
  `txt(parent, text, x, y, size, {weight, font, fill, anchor})`, `labelBox(parent, cx, cy, w, h, col, name, sub, icon)`.
  Fonts: 'Bricolage' (display, weights 200-800), 'Geist Mono', 'Instrument' (italic serif).
- Figures: `person(parent, x, y, s, {jacket, scarf, hat: 'beanie'|'cap'|'none', hatCol, hair, skin})` with poses
  `P_WALK P_TYPE P_SLUMP P_TALK P_LOOKUP P_CHEER P_POINT P_WAVE` via `pose(fig, a, b, P)` and `walk(...)`;
  `presenter(parent, x, y, s, {top, hair})` with `g.__talk = (t) => 0..1`; `fox(parent, x, y, s, ink)` with
  `pose(f, a, b, F_RUN([[jump0, jump1]]))`; `orb(parent, x, y, r)`; `rig(fig, parts, rest)` for your own.
- Things: `gear`, `clock(parent, x, y, r, from, speed)`, `tripod`, `scissors(parent, x, y, s, snipTimes, rot)`,
  `burst(parent, x, y, at, n, cols, dist)`, `sunburst(parent, x, y)`, `icon(parent, kind, x, y, s)` (shapes, cube,
  frames, brush, notes, burst, mic, snip, lock, key, gpu, laptop, ram, check), `mini(parent, kind, w, h)` and
  `picture(parent, kind, x, y, w, h)` (little paper landscapes: beach, peaks, city, wave, forest, desert, night,
  lake, mars), `clipRect(parent, x, y, w, h, r)`, and software made of paper: `ui.timeline`, `ui.menu`,
  `ui.plugins`, `ui.render`, `ui.dialog`, `ui.warn`, `ui.sticky`, `ui.file`, `ui.keyframe`, `ui.wheel`.
- Time: `B(k)`, `BAR(k)`, `BEAT`, `envAt(t)`, `kickAt(t)`, `step12(t)`, `ss(a, b, t)` (smoothstep), `lerp`,
  `clamp`, `rng(seed)`, `LIVE.push((t) => ...)`.
- Sound: `sfx(t, type, opts)`.
