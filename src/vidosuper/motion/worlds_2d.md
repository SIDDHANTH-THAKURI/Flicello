# The 2D kit: a whole frame of flat motion design, with the person in it

The bar: the best flat science-animation channels (the kind people watch for the animation itself). Make an original
piece in that spirit, never a copy of anyone's characters or frames.

## The frame, from the bottom up
- `.behind` (a direct child of #stage) is an OPAQUE world that fills the frame: it replaces their room. Build it in
  layers with depth: a deep background (a rich gradient, never pure black or a flat colour), soft colour clouds
  (big blurred blobs), far small dots, near sparkles, each layer drifting at its own speed (parallax).
- Then the person (`#person`, their cut-out, put in by Flicello). You may place them with ONE static CSS rule, nothing
  animated (this is the one exception to "don't restyle #person" in the technical contract below): `#person { transform: translate(-330px, 70px) scale(.86); transform-origin: 50% 100%;
  -webkit-mask-image: linear-gradient(to top, transparent 0%, #000 14%); mask-image: linear-gradient(to top,
  transparent 0%, #000 14%); }` puts them left of centre, a little smaller, their body fading into the world at the
  bottom (no hard edge where their video ends). Give the world's main subject the space they leave. Their face stays
  clear and big.
- Things that pass in front of them go after `.behind`'s closing tag (on top). Most of the world sits behind.
- On top of everything: a light-spill layer (`mix-blend-mode: screen`, a radial gradient of the subject's colour,
  so its light falls on them), film grain (an SVG feTurbulence rect, `mix-blend-mode: overlay`, opacity about .07,
  its seed and offset changed every frame from the timeline), and a soft vignette.

## The look of things
- Flat shapes with TWO-TONE shading: the body colour, a darker shade on the side away from the light (a clipped
  offset shape), a lighter face toward it, a crisp rim stroke a shade lighter, one small white highlight ellipse.
- Glow is drawn, never blurred: STEPPED FLAT RINGS around the thing (3-5 concentric circles of its colour at
  opacity .03-.12), breathing gently.
- Depth: overlapping layers, things nearer move faster, small things far away. Details that reward a second look:
  a dashed orbit ring turning slowly, a core that pulses, tiny sparks.
- One palette for the film (from world.md), saturated against a deep background.

## Motion
- Everything springs: anticipation before a move, overshoot and settle (`back.out`, `elastic.out(1, .55)`), squash on
  impacts, follow-through. Births are events: a point of light, a burst of little sparks, then it pops open.
- Things that move fast get a flat motion trail (an arc stroke of the same colour behind them, low opacity).
- Rotation that "goes clockwise" on screen = increasing SVG rotation. Orbits: a group rotated about its centre
  (`svgOrigin`), each member also turning on its own axis.
- The camera is #stage: a slow push, a drift, and on a pause in their speech a HARD CUT to a new framing
  (`tl.set(stage, {scale, x, y}, t)`), never a slide. Keep every orbit and subject inside the frame after a cut.
- Each change lands on its word (the hit on the word, anticipation 3-6 frames before).

## The bar, from the owner's own verdicts
- example.html on your desk is a finished piece the owner called "exactly how I wanted": study its CRAFT (the layered
  space, the clouds, the two-tone shading, the stepped glow, the births, the springs, the trails, the grain, the hard
  cut) and hold yourself to that level or above. Never copy its content or layout: your film is about their words.
- A "black" or "dark" world is never empty: a deep gradient (navy/indigo near black, never #000), big soft colour
  clouds, light falling off around the subject, dust in two depths. Empty black reads as unfinished.
- The subject arrives BIG and confident: once born it is 25-40% of the frame's height, not a small dot that grows
  late. Small timid shapes read as amateur.
- The person belongs in the world: grade them gently (`filter: brightness(1.04) contrast(1.06) saturate(1.06)` in
  their one CSS rule), and let the subject's light fall on them (the spill layer). They must never look grey or
  washed out.
- Depth everywhere: orbits are tilted ellipses whose members pass BEHIND the subject on the far side (smaller and
  dimmer there, drawn under it) and in front on the near side; things far away are smaller and paler. Flat things
  in one plane read as stickers.
- On the word, not after it: a thing named on a word is visibly born (its flash, its first frame) within 0.1 s of
  that word's start, and has popped open within about 0.4 s. A beat late reads as out of sync.
- Things have weight: a push and recoil when they arrive, a squash on landing, light that touches what is near.

## Checks that matter
- Look at the frame before the subject appears: the world must already be beautiful and alive.
- Nothing cut off at the frame's edges (orbits included), nothing over the face. `bash review` MEASURES the person
  on their cut-out (anything drawn over their face, them cut off at an edge their shot isn't, them shrunk below about
  2/3 of their size): those problems are certain, and the section isn't ready until they're gone. A line or shape that
  crosses the frame passes behind them (inside .behind) or clear of their head.
- Text only if the brief allows it.
- Sound cues for every visible action (`<script id="sound">`), under the voice.

## Parts already made to this bar (window.craft)
`craft.js` is loaded into every scene. Its parts are drawn in this exact craft, seeded (same scene, same drawing), and
lay their motion on your timeline at absolute times, so seeking any frame works. Use them as parts: recolour, scale,
compose, add your own detail on top. Each takes an SVG group to draw into (`parent`), and returns its nodes.
- `craft.sphere(g, {x, y, r, color, light: 225, core, ring, halo})` a shaded subject (two tones, rim, highlight,
  stepped glow, optional bright core and dashed ring); `.born(tl, at)` point of light + sparks + elastic pop on the
  word; `.alive(tl, t0, t1)` glow breathes, core pulses, ring turns.
- `craft.tree(g, {x, y, h, kind: 'round'|'pine'|'oak', color, trunk, seed})` (x, y = the foot on the ground);
  `.grow(tl, at)` rises from the ground with overshoot; `.sway(tl, t0, t1)`.
- `craft.sky(g, tl, {w, h, stops: [[t, top, bottom], ...]})` a gradient that changes over the film (night to dawn).
- `craft.stars(g, {...}).twinkle(tl, t0, t1)`, `craft.cloud(g, {x, y, w, color}).drift(tl, t0, t1, dx)`.
- `craft.ground(g, {y, w, h, color, grass, strata})` a cross-section with strata and pebbles (grass: false for bare);
  `craft.water(g, {y, w, h, color}).flow(tl, t0, t1)`.
- `craft.rain(g, {...}).fall(tl, t0, t1)`, `craft.dust(g, {...}).drift(tl, t0, t1)` (two depths),
  `craft.grain(container, tl, end)` the per-frame grain layer, `craft.trail(g, {cx, cy, r, a0, a1, color})`.
- `craft.glow(g, {x, y, r, color}).breathe(tl, t0, t1)`, `craft.tones(hex)` a colour's shade/deep/light/rim/glow.
They are a floor, not the design: the idea, the staging, the camera and the timing on their words are yours.
