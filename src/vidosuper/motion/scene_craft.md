# Scene craft: designed things acting in a real person's video

## The idea first
- The first idea that comes to mind is everyone's. Write three, each a different metaphor or physical event, and
  pick the one that acts the words out most physically, uses their space and would stop a scroll.
- The things they speak of become things on screen, not words: followers are people, money is notes and coins
  that move, time is light and clocks, a city is buildings. Words or numbers on screen are the last resort, used
  only when the words themselves are the point (or they ask for text); the idea that shows the thing beats the one
  that writes it.
- Templates are never the idea: no UI cards, notifications, app screens, counters in boxes, progress bars, charts,
  stock icons, infographics or lower thirds unless they asked for one. When a number must be seen, it is a thing in
  their world: huge, made of something, lit by their light, landing on or around them.
- Good ideas turn the words into an event in their space: "my inbox exploded" as letters bursting out of their hands
  and fluttering down around them; "I finally slept" as the room's light sinking to night with stars coming out on
  the wall behind them; "my heart was racing" as a red pulse thumping through their chest and the whole frame on
  every beat.

## Focus
- One hero idea, executed perfectly, beats a busy scene. Decide the one thing the viewer must see (the wings opening
  on "wings"; the ball landing on "ground") and make every frame of it right first: its timing on the word, its
  anchor on the body, its arc, its weight, its light. Supporting things (a feather, a world behind, particles) come
  only after the hero is perfect, and each must read in a single still as what it is.
- A few big, readable things move better than many small ones: 5-7 particles that are clearly feathers or petals,
  not 20 tiny blurred bits that read as noise.
- The hero is whole in the frame at its biggest moment (wings fully open, the ball at the top of its bounce): check
  its extremes then, with a margin from the edges, unless it is meant to run past them (a world, a sky).
- A thing never drops out mid-move: once it is on, it stays fully drawn until it leaves on purpose. Look at the
  moments between beats for gaps.

## Make it belong to their shot
- **Light**: read the frames before painting. Where is the light on the person coming from, how warm is it, how hard?
  Match that key light on them, not the colour of the wall behind them (a warm face against a cool curtain means warm
  light). Every painted asset is described with that light ("warm window light from the upper left, soft") and its
  shadows fall the same way.
  A glowing object lights what is near it: a soft `mix-blend-mode: screen` radial glow on the person or the wall.
- **Contact**: anything that touches something casts a contact shadow (a soft dark ellipse, `multiply`, tight and dark
  at contact, wider and fainter as it lifts). Floating things get a faint, offset shadow or none; never a hard drop
  shadow on a photo.
- **Depth**: far things are smaller, softer (`blur(1-3px)`), lower in contrast and tinted towards the air colour; near
  things are crisp and saturated. Things that pass behind the person go in the behind layer; things that pass in
  front of them go in front, and may blur a little (near depth of field).
- **Grain and edges**: the footage has grain and soft edges; a perfectly clean vector over it looks pasted. Give
  painted and drawn things a whisper of grain (an SVG `feTurbulence` overlay at 4-8% opacity) and slightly soft edges.
- **Scale**: things attached to them are sized from them: wings span 1.6-2.4 x their shoulder width, a halo is 0.6-0.8
  x their head width, a ball dropped from their head is 0.3-0.5 x the head. `rig.person(t).width` is their shoulder
  width in frame shares.

## Light, shade and the camera (the footage is in the page)
- Light adds: glows, flares, sparks, beams and the light from anything that glows go in `mix-blend-mode: screen` or
  `plus-lighter` and brighten the footage under them. A glowing thing near them lights their face and the wall on
  that side (a large, soft radial glow in screen, in its colour, fading with distance).
- Shade multiplies: contact shadows, a darkening world, a vignette go in `multiply` (a coloured multiply tints).
- Grade the shot when the moment calls for it: a wash in `color` or `soft-light`, or a filter on a copy of the shot
  (night, a flashback, a dream, a memory), crossfaded in and out.
- The camera: push in on the hero beat (#stage scale 1.0 to 1.06-1.15 over 0.4-1 s, `power2.out`); `rig.shake` on
  #stage on impacts (8-20 px, 0.25-0.4 s, with #stage scaled up enough to cover it); a slow drift on calm lines.
- The footage as material: a copy of the shot under `feDisplacementMap` bends the real picture (heat haze over
  something hot, a shockwave ring on an impact, a ripple, a lens), masked to where the effect is.

## Attach things to them
- The body is tracked every 0.1 s: `rig.at('head' | 'neck' | 'shoulderL' | 'shoulderR' | 'chest' | 'center', t, W, H)`
  gives px. `rig.pin(tl, el, 'neck', { from, to, W, H, anchor: [0.5, 0.9] })` keeps an element's anchor point there
  as they move (it animates `left`/`top`; animate the element's own transforms for its own motion, or pin a wrapper
  and move the child).
- Wings root between the shoulder blades, behind the neck, and open outward and up in an arc about that root
  (`transformOrigin` at the root), not by scaling from the middle. A pair is one wing painted and the other mirrored
  (`scaleX(-1)` on a wrapper), opening a few frames apart for life.
- Something "from" a part of them (from the head, out of the hand) starts exactly there at its first frame.

## Motion with weight
- **Gravity**: falling things accelerate (`power2.in`), rising ones decelerate (`power2.out`), and bounce lower each
  time. `rig.drop(tl, el, { at, y0, y1, g, e, bounces, shadow })` times a fall with bounces from gravity and squashes it
  on every impact; it returns the impacts, so hit sounds, dust (`rig.burst`) and a tiny camera bump land on them.
- **Anticipation and follow-through**: before a big move, a small move the other way (3-6 frames); after it, the parts
  settle late (`rig.cascade` on the parts: feathers, petals, layers, letters) and the whole thing overshoots and
  settles on a spring (`ease: rig.spring('soft', 0.6)`; `0.75` premium, `0.45` playful).
- **Secondary motion**: when something lands or stops, what hangs off it keeps going a moment: `rig.wobble(tl, el,
  { at, prop: 'rotation', amount: 6, freq: 4 })`. Feathers flutter, a flag ripples, a liquid sloshes.
- **Arcs**: thrown and swinging things travel on curves (`MotionPathPlugin` or x and y on different eases), never in
  straight lines.
- **Speed**: fast moves get motion blur along their direction (`rig.blur(tl, el, { at, duration, px, dir })`); a hit
  can add 2-3 frames of a white flash or a tiny shake; slow, big things (a world opening) take 0.8-1.6 s on
  `power2.inOut`.
- **Particles**: dust on a landing, sparks, confetti, feathers, droplets: `rig.burst(tl, make, { at, x, y, n, speed,
  dir, spread, gravity, drag, life, seed })` with `make(i, r)` returning each bit (a small painted or drawn element).
  Seeded: every render is the same.

## Time it to their words
- The hit lands on the word (its first syllable); the anticipation starts 3-6 frames before it.
- Don't cover their face or mouth while they speak; things that cross the face go behind them or past quickly.
- One idea at a time: when the next thing arrives, the last one settles, leaves with intent, or becomes the next.

## Effects, built properly
- **Glow and light**: radial gradients with `mix-blend-mode: screen`; light rays are long soft gradients fanning from
  the source, rotating slowly; bloom is a blurred copy behind the bright thing.
- **Heat, magic, water**: SVG `feTurbulence` + `feDisplacementMap`, its `baseFrequency` or `seed` tweened with time.
- **Reveals**: `clip-path` or masks with a soft edge, growing from where the thing comes from.
- **Smoke and clouds**: painted puffs, several layers drifting at different speeds and scales, slowly fading.

## Painting well
- Paint each piece that moves on its own: a crowd as three rows (back, middle, front) that rise at different times
  and depths, a character's parts, each cloud.
- Describe it the way an illustrator or photographer would: what it is, its pose and the angle it is seen from (as
  their camera sees things), its material, its light (the shot's key light), its style.
- Read every painting before using it, and paint it again when it is wrong: the wrong angle, the wrong light, or a
  clip-art look.

## Looks, and how each is built (what they asked for, or the one the idea needs)
A look is made of real material, so most looks are painted (`bash paint`), not drawn: hand-drawn SVG reads as clip
art the moment it tries to be paper, clay, chrome or a creature. Code draws what is truly graphic: lines, type, grids,
light, particles, masks.
- **Painterly realism**: paint everything that is a thing (wings, creatures, weather, worlds) in the shot's own light,
  each moving part apart; soft edges and the footage's grain over them.
- **Paper cut-out**: paint every piece as cut paper ("... cut from matte coloured paper, scissor-cut edges with a thin
  torn white core, visible fibres, a slight curl, lit by warm light from the left"), several pieces per thing so it
  can move (a dog's head, body, tail, ears). Stack the layers with a soft shadow under each (a blurred dark copy in
  `multiply`, 4-10 px down and away from the light) so you see the gaps between them. Motion on twos (hold each
  pose 2 frames: `ease: "steps(N)"` over the move), small paper wobbles, pieces that fold or slide in rather than
  fade.
- **Clay**: paint the pieces in plasticine ("soft rounded clay, fingerprints, warm subsurface glow"); squash and
  stretch on every move, bouncy springs (`rig.spring('clay', 0.5)`), on twos.
- **Ink and wash**: painted ink strokes and washes on paper, each revealed by a mask that grows along the stroke
  with a wet, turbulent edge; splashes on the hits; a paper texture in `multiply` at low strength over the shot.
- **Watercolour**: painted washes ("watercolour on cold-press paper, soft blooms, pigment granulation"), revealed by a
  turbulence-edged mask that spreads like wet paint; colours that bleed into each other.
- **Neon line**: SVG strokes drawing on (`DrawSVGPlugin`), each line twice: a hot core (2-4 px, nearly white) and a
  wide blurred copy in its colour, both in `screen`; a flicker on the hit; it lights their face and wall (a large soft
  glow in `screen`, its colour); darken the shot a little around it (`multiply`) so it glows.
- **Chrome and glass**: paint chrome objects in the shot's reflections ("polished chrome, reflecting a warm room"), or
  build glass from `backdrop-filter: blur()` with a bright rim and a highlight that sweeps across on each move; crisp
  edges, real reflections of their light.
- **Retro pixel**: pixel art drawn small (a canvas or SVG at 1/6-1/10 size, `shape-rendering: crispEdges`) and scaled
  up with `image-rendering: pixelated`; positions snapped to the pixel grid, motion on twos, a 6-12 colour palette,
  chunky particles, no blur and no soft shadows.
- **Comic**: bold ink outlines, halftone shading (a dot pattern in `multiply`), speed lines, an impact frame (1-2
  frames of flat colour with their silhouette), the camera punching in on hits.
- **Swiss / editorial**: a strict grid, one family in one or two weights (Inter, Archivo Black), one accent colour;
  words and numbers move as solid blocks revealed by masks on `expo.out`, numerals huge, nothing ornamental.
- **Risograph**: two or three fluorescent inks (pink #FF48B0, blue #0078BF, yellow #FFE800) layered in `multiply`, a
  halftone grain, 1-3 px misregistration between inks, a paper texture.
- **Isometric 3D**: objects built from three faces lit from one side (three tones of each colour) in a 30-degree
  projection, rising out of a floor plane on springs, with soft contact shadows.
- **Cinematic VFX**: photoreal painted elements in the shot's light, lens flares and light wrap in `screen`, dust and
  embers with depth of field, heat haze or shockwaves bending the real footage, the camera reacting to every impact.
