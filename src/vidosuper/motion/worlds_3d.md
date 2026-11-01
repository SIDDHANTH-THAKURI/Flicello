# The 3D kit: a whole frame in Blender, with the person in it

You write `scene.py`, a Blender 4.5 Python script built on the kit (`blender_kit.py`, beside it: read it once). It is
run as `blender -b -P scene.py -- <desk> stills 0.8,2.0` for a look and `... render <folder>` for every frame. Times
are seconds into the section; `k.f(t)` turns them into frames.

```python
import blender_kit as k
import math

k.begin()                                   # size, fps, length, EEVEE, motion blur, bloom, a black world
cam, aim = k.camera([(0.0, (0.3, -14.6, 0.25)), (4.55, (0.25, -13.2, 0.2))],   # glides
                    cuts=[(4.6, (2.1, -12.6, -0.7))])                           # a hard cut to a new angle
him = k.person(x=-2.55, z=-0.22)             # their cut-out on a card, sized to the frame, faded at the bottom
k.backdrop(wake=(1.2, 2.1))                  # a faint deep glow far behind, waking with the subject
k.dust(born=(1.2, 2.0))                      # motes of light in the space, born with the subject
# ... the world's things: meshes, k.emissive / k.lacquer / k.glass materials, k.light, k.keys for every move ...
k.end()                                      # renders what was asked
```

## What makes it real motion design (worked out by hand; hold to it)
- The camera looks along +Y. The person's card stands at y=0 facing it; the world goes behind (y > 0) and in front.
  Place them left or right of centre and give the subject the space they leave. Keep camera moves gentle (a few
  degrees of angle): the card is flat.
- The world fills the frame: a deep dark space (the backdrop glow far behind, dust for depth), never their room.
- Light belongs to things: a glowing subject gets a point light inside or just in front of it (its colour) so it
  lights the person (the card takes the world's light); a soft rim area light on the person's near side. Keep the
  person's light gentle (watts in the hundreds): they must stay natural, never white-hot.
- Glowing things: an emissive ring (a torus) and a core (a filled circle with a spherical gradient and slowly
  flowing noise), bloom does the rest. Solid things: `k.lacquer` (bevel modifier on every hard edge, 3-4 segments).
- Births are events: from scale 0 to a point of light, then open with overshoot (0 -> 0.95 -> 0.80 -> 0.86).
  Everything eases; nothing pops linearly. Each change lands on its word.
- Orbits: parent things to an empty that faces the camera (rotation X 90 degrees); for the camera, clockwise is a
  POSITIVE turn about +Y. Each member also tumbles on its own axis.
- Hidden things must be scale 0 (unlit dust shows as black dots).
- Write beats.json ([{"t": 1.7, "what": "the circle opens"}, ...]) and sound.json ([{"t": 1.6, "sound": "a soft
  round pop with a shimmer", "gain": -14}, ...]) beside the scene: the director reads the beats, the film gets the
  sounds.

## Parts already made (in the kit)
Use them instead of rebuilding them; restyle freely. Materials (each returns a material to append to a mesh):
- `k.lacquer` glossy paint, `k.glass` clear, `k.frosted` blurred glass, `k.metal(name, rgb, roughness, brushed)` gold
  by default (chrome, copper by colour), `k.clay(name, rgb)` soft matte toy/clay, `k.iridescent(name)` thin-film
  sheen that shifts colour with the angle, `k.emissive` light itself.
- `k.toon(name, rgb, sun=(x, y, z))` cel shading in flat bands lit from a fixed drawn direction, with a crisp rim
  (a drawn look in 3D, the same under any lights), and
  `k.outline(obj)` an ink line around its silhouette.
Real things before primitives: a box, a cylinder or a cone standing in for a boat, a lighthouse or a chair reads as
unfinished (a lighthouse built from a plain cylinder scored 4/10). First look in the free library:
- `bash assets lighthouse rocks` lists ready-made models that match (id, name, real size, tags); `bash assets --hdri
  night sea` lists real skies and places. They are CC0 (free for anything).
- `k.model("<id or words>", loc=(x, y, z), height=<scene units>, rot_z=<degrees>)` downloads it once, imports it with
  its own textures, stands its base on `loc` and returns one empty to animate (`k.keys(m, "location", ...)`). None when
  nothing matches: then build it, well (bevelled, real materials, detail), from the kit's shapes.
- `k.hdri("<id or words>", strength=0.4, rotation=<degrees>, visible=True)` lights the whole scene from a real sky
  (metal and glass need something to reflect); `visible=False` lights without showing it.
Shapes and places:
- `k.rounded_box(name, size, loc, radius, material)`, `k.bevel(obj)` rounded edges on anything, `k.smooth(obj)`.
- `k.cyc(rgb, y, z)` a studio cyclorama: a floor curving up into a wall with no seam (products, objects on show,
  soft shadows), lit by `k.studio(target)`: a soft key, a cool fill and a rim light aimed at the subject.
- `k.sky(top, horizon, strength, changes=[(t, top, horizon)])` the world as a sky gradient that also lights the
  scene and can move from night to dawn over the section.
- `k.terrain(size, height, loc, seed, low, mid, high, flat)` rolling land coloured by height (faceted or smooth);
  `k.ocean(size, loc, rgb, waves, chop, speed)` a real moving sea.
- `k.aim_at(obj, target)` points a light or thing at a point.

## Checks that matter
- Look at frames before, during and after every change, and a strip across each move.
- Nothing cut off at the edges after a camera cut, nothing over the face, no hard edge where their video ends.
  `bash review` MEASURES the person on their cut-out (anything in front of their face, them cut off at an edge their
  shot isn't, them shrunk below about 2/3 of their size): those problems are certain. Keep the card named "person"
  and make it with one `k.person(...)` call: later sections hold that call's place and size.
- Text only if the brief allows it (avoid text objects otherwise).
