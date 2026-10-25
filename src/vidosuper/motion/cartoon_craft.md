# Cartoon craft: a story told as a film, animated by hand in code

## The story
- One clear want, one obstacle, one turn, one ending that pays off something set up early. A 30-60 s cartoon has
  room for a setup (who, where, what they want), two or three escalating beats, a turn, and a button (the last
  laugh or the last feeling). Cut everything else.
- Show, don't tell. A character's want reads from what they do: reaching, staring, sneaking, hiding a thing behind
  their back. No captions explaining the story, no title cards in the middle, no speech bubbles unless the story
  is about words. Sound and acting carry it.
- Specific beats beat generic ones: "the cat's tail knocks the cup off the shelf, it hangs on the edge, the cat
  looks at the camera" is a beat; "the cat is naughty" is not.

## Shot like a film, not a slideshow
- Every shot has a size (extreme wide, wide, full, medium, close-up, extreme close-up, insert), an angle (eye
  level, low, high, bird's eye, over the shoulder, POV, dutch) and a reason for both. Vary them: a wide to place
  us, a close-up for a feeling, an insert for the important object, a low angle for power, a high angle for
  smallness. Never two shots in a row with the same size and angle unless that is the joke.
- Cut on action: a move that starts in one shot finishes in the next. Keep screen direction (a character walking
  right keeps walking right across the cut, unless the turn is the point). Respect the 180-degree line.
- Shot length follows the beat: 1 to 3 s for action and reactions, 3 to 6 s for a moment that lands, longer only
  when something keeps happening on screen. Cuts land on the music's beats (or the narration's pauses).
- The camera moves with intent: a slow push in as a feeling builds, a whip pan between two characters, a shake
  on impact, a pull out to reveal. Animate it (#stage or a world wrapper) with parallax: the far layers move
  less than the near ones.

## Acting
- Pose to pose: strong key poses with clear silhouettes (readable in solid black), then the in-betweens. Hold a
  pose long enough to read (8 to 12 frames), then move.
- Anticipation before every action (a crouch before the jump, a wind-up before the throw), overshoot and settle
  after it, follow-through on loose parts (ears, tails, hair, clothes) lagging a few frames behind the body.
- Arcs, never straight lines, for heads, hands and thrown things. Ease in and out of poses; snap only for a hit.
- The eyes lead: a character looks at a thing before reaching for it, a head turn starts with the eyes. A blink
  in a hold keeps a character alive. Expressions change on the beat: mouth and brows swap or morph.
- Squash and stretch on anything soft or comic, in proportion to the style (a lot for rubber-hose, a little for
  a storybook painting).
- Timing tells weight: big things take longer to start and stop, small things are quick.

## Building characters as cut-out puppets (how it is done in code)
- Each character is painted once as a model sheet (full body, neutral pose, three-quarter view), and every part
  that moves on its own is painted as its own piece in that same style (`--like` the model sheet): head, torso,
  upper arms, forearms, hands, thighs, shins, feet, tail, ears; plus the mouth shapes and eyes the shot needs
  (open, closed, smile, frown, surprised), each a separate piece swapped or cross-faded on the beat.
- Assemble the parts in nested elements: each joint is a wrapper div with `transform-origin` at the joint (the
  shoulder, the elbow, the hip), so rotating the upper arm carries the forearm and hand. Measure where joints are
  by looking (`bash look`), then nudge.
- For a big change of pose or a turn (front to profile), paint the whole character in that pose too and swap at
  the fastest point of the move, hidden by motion blur or a smear frame. Painted key poses swapped on twos (every
  2 frames, `steps()` or a `tl.set` per frame) give the handmade feel of drawn animation.
- Keep characters on model: same proportions, colours and details in every shot (read the model sheets before
  painting anything new for them).

## The world
- Backgrounds are painted, layered for depth (sky, far, middle, near, foreground framing), with the light of the
  scene's time of day. The foreground layer (a branch, a doorway, a plant) gives depth when the camera moves.
- Light and shadow agree: one key light direction per scene, contact shadows under feet and objects, rim light on
  characters against a dark background.
- Effects (dust, smoke, splashes, sparkles, speed lines, impact stars) are drawn in code in the style of the
  world, with timing like a hand-drawn effect (fast in, slow dissipate).

## Sound
- Every action has a sound (footsteps, a cloth swish, the thud of a landing, a cartoon zip for a fast exit,
  a squeak), described precisely so it can be made. Silence before a big hit makes the hit bigger.
