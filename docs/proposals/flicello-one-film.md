# One film, any tool on any part (the flow the owner described)

Status: PLAN, decided with the owner 2026-11-03 (four decisions below). Slice 1 ("edit any part") is next, on the
owner's go. Nothing here is built yet. A clickable sketch of slice 1 on the owner's own film "Give Way" (v8):
private/sketches/one-film.html (its part 2 made for real as a paper scene with the paper kit).

## What the owner described

1. **Understanding first, done well.** Flicello watches what they give it and says back ONE understanding (not
   several options to pick from): "this is what I think your film is". They can correct it, tell it to look again,
   or skip the looking and simply tell it what the film is. Clips can be added or swapped in the middle (a clip
   added by mistake, a better take, a missing piece) without starting over.
2. **Who leads.** Instead of "which style?", it first asks: should I take over and bring ideas, or do you have
   specific ideas? Then, from the footage and their answer, it says what it can do for THIS film ("I can cut it on
   the beat, add designed motion graphics where you talk about X, make a 3D world for the opening, write the music,
   add sounds") and they pick some, all, or say something else.
3. **A quick sample before the whole thing.** A short taste of what the film will feel like; if it's off, they say
   why and get another taste; when it's right, the film is made.
4. **The film is editable in parts, on screen.** "Scene 6: add motion graphics of this", "a different song for this
   part", "a whoosh when I jump", "make this real stretch animated, then back to real, in one flow", and every
   change touches only that part (no waiting for the whole film to be made again).
5. **Settings up front.** The shape and the few real settings are set at the start; everything else is talk.
6. **Cello does all of this** by conversation.

## Decided (2026-11-03)

1. **One idea.** When Cello leads, it brings one proposal (the story and what it will use for this film). "Another
   idea" brings one more, one at a time. The pick-one-of-three step goes.
2. **Length from the footage.** Cello decides the length from the clips (or the brief) and says it in its
   understanding; they change it by saying so. The shape is the only setting asked at the start.
3. **Build first: edit any part** (slice 1 below).
4. **Real -> animated -> real: both ways, paper first.** First the part is redrawn in paper with the animated
   engine (no GPU); the video-model restyle comes later, after a test on a real GPU.

## What Flicello is today (from the code, 2026-11-03)

- **Films have one fixed kind** at creation: `montage` (clips cut to music), `talk` (cut on their words, designed
  moments behind them), `voice` (a recording made into animation), and as new films from Cello's hands, `cartoon`
  and `animated` (a film from a brief with no footage, built 2026-11-03: one desk per scene, scenes as a stack of
  pages, sound from what happens on screen; rated 15/20 and 18/20 on unseen briefs).
- **Stages** (service.py): setup -> preparing -> understood -> choose -> sampling -> review -> rendering -> done
  (-> reading -> confirm -> revising). `understood` already shows one understanding that can be polished, undone
  and confirmed, with parts, spellings and must-haves. `choose` pitches three story ideas.
- **Pieces that already work on one part at a time:**
  - montage shots render to cached segments keyed by everything that affects them (render/segments.py);
  - a change names shots and only those change; the others are pinned frame for frame (studio/versions.py);
  - talk and voice moments are kept when their contract, look and picture are the same (work/moments_film.py);
  - designed 2D/3D scenes are spliced into a stretch frame for frame, kept in the plan as a `scene` graphic and laid
    back in when the cut under them is the same (work/scenes_in_film.py);
  - the timeline's exact edits make a draft at once (studio/handedit.py);
  - an animated film's scenes are separate files, each re-made at its own desk with notes (motion/animated.py).
- **The film page's conversation** (studio/agent.py `turn`): exact quick edits first (`fast_path`); then Claude Code
  only when a regex calls the words creative AND the film is finished (`hand_over`); everything else goes to the
  editor's fixed set of actions. This is the "Cello doesn't understand" path. Cello's dock has Claude Code as its
  `work` tool.
- **What's missing for the flow:** no one list of parts across kinds; no animated (paper) insert inside a footage
  film; a change to a designed scene re-encodes the whole film (`splice`); one music track per film; sounds only
  inside designed scenes and cartoons.

## Slice 1: edit any part

### The film as parts
- Every film gets one list of parts, whatever its kind: `parts.json` beside its plan. A part is
  `{id, n, start, end, what, treatment, inputs, digest, made}`: `n` is the number they see, `what` one line ("the
  jump", "she pours the tea"), `treatment` how it is made, `inputs` what it is made from (shots, moments, a scene
  folder), `digest` everything that affects its picture and sound, `made` the file of its stretch.
- Parts come from the plan the film already has, by code: a montage's story beats (its shots grouped, 3 to 12 s
  each), a talk or voice film's sentence groups (its designed moments inside them), an animated film's scenes. A
  part border is always a cut on a beat (or between words, in a talk). Claude Code can split or merge parts.
- On screen: the film strip under the player shows parts (number, a frame, its line). They say "part 3 ...", or tap
  a part and talk, or say "when I jump" (found with `find`).

### Treatments: the engines we have, wrapped as part makers, and two new ones

| Treatment | Made by | Status |
|---|---|---|
| the footage, cut | montage planner + segments, talk speech cut | exists |
| graphics on it (captions, titles, things) | overlays | exists |
| designed moments (talk, voice) | moments_film | exists |
| a designed 2D or 3D scene | worlds (scenes_in_film) | exists |
| an AI clip | create/clips | exists |
| a paper scene: an animated insert in the cut-paper look | the animated engine's scene desk, given the part's length, the music under it and the frames either side | new, slice 1 |
| real -> paper -> real | the part's own frames redrawn in paper (slice 4) | new, slice 4 |
| a cartoon shot | the cartoon engines | later |

The kinds stop being locks and become starting presets: a talk starts as "cut on words + designed moments", a
montage as "cut on the beat", an animated film as "paper scenes on made music". Any part can then take any treatment.

### Every request goes to Claude Code
- The film page's conversation keeps the instant exact edits ("shot 3 out", a trim, a caption's words) and sends
  everything else to the hands (Claude Code with `bash fl`) at any stage once the film exists. The regex gate and the
  editor's fixed actions go. Cello's dock and the page reach the same hands (one session per film, as now).
- New commands for the hands: `fl parts` (the list with frames), `fl part <n> "<what>"` (re-make that part, choosing
  its treatment), `fl part split|merge`. The music and sound commands come in slice 3.

### Only that part is made again
- Each part's stretch (picture and its own sound) is kept by its digest. A change re-makes only the parts whose
  digest changed; the film is joined from the kept stretches; the music and sounds are mixed again over the whole
  film (cheap).
- Joining without re-encoding the whole film: every part is encoded as its own closed piece with the same codec
  settings (a keyframe on its first frame) and the pieces are joined by stream copy. A transition that crosses a
  border (a page peel, a dive through a window) belongs to the part it leads into, which keeps the last frames of the
  part before it.
- Targets on a high-end machine: a change that needs no designing (a cut, a caption, a sound) in under a minute; a
  designed part (a scene, a paper insert) takes its desk's time (10 to 25 min) and nothing else is touched.
- Every change is still a new version (undo stays simple). A version is a list of part digests, so versions cost
  little disk.

### Slice 1 is done when
- On footage it has never seen (not the owner's test clip): a clip film made the normal way, then five asks, each on
  one part: "part 2: a paper animation of the route we drove", "part 4 shorter", "a title on part 1", "part 5 as a
  3D world", "the ending calmer". Every part not named stays frame-identical (checked by hash), the time of each
  change is measured, and the owner rates the film before and after.
- Tests: parts from each kind, stable digests, one change touches one part, the join is frame-exact, every request
  reaches the hands.

## Slice 2: the new start
- The shape is the only setting; the length comes from the footage.
- One understanding (exists), plus "look again at X" (the named clips watched again with that focus) and "skip that,
  here's what it is" (their words become the understanding). Clips can be added or swapped at any point: the new
  clip is watched on its own and folded in, and Cello says what it now understands differently.
- "Shall I take over, or do you have ideas?" Take over: ONE proposal, the story plus the list of what it will use
  for this film (a beat cut, designed moments at X, a paper opening, made music with the drop on Y, sounds on the
  hits) for them to trim; "another idea" brings one more. Their ideas: Cello follows them and adds what fits.
- A quick taste (about 8 s) with the chosen treatments in it, again until it's right; then the film, made as parts
  from the start.

## Slice 3: music and sound per part
- A music lane: one or more cues over the parts (made here, picked, or a stretch made again), crossfaded on bar
  lines; "a different song for the ending" changes one cue.
- A sound lane: sounds pinned to found moments ("a whoosh when I jump": the jump is found, the sound is made from
  the words and placed on its frame).

## Slice 4: real -> animated -> real
- Paper first: the part's frames go to a paper desk with the animated kit, which redraws the same framing and action
  in paper (the person as a paper figure in their own colours, the place as a paper world) and peels in from the
  real frame and back out. Needs no GPU; proven on unseen footage and rated by the owner.
- Then the video-model restyle (the footage's exact motion, repainted, e.g. anime or toon): the best open model at
  that time, tested on one real GPU first, with no spend without the owner's go.

## Slice 5: the screens, simplified (with the owner)

## What could break, and how not to
- **The tuned flows** (talk designed moments, voice explainers, montage stories) keep working as presets; they are
  wrapped, not replaced. Ship behind a switch; old films open as they are.
- **A designed part that changes length** moves everything after it: a part keeps its length unless they ask, and
  the music re-fits on bar lines when it changes.
- **The plan must stay readable to Claude Code** (one JSON with clear fields), or the hands will make mistakes.
- **Joins**: parts share codec settings and frame timing so joins are seamless; sound is always mixed again whole.
- **Real -> animated -> real** with a video model is GPU-heavy and unproven: research and one real GPU test first.
- **Versions**: every part change is still a new version.

## Order

1. Slice 1: parts for every kind, the part cache and stream-copy join, `fl parts` / `fl part`, the paper insert, and
   every request to the hands (the regex gate and the fixed actions removed). Behind a switch.
2. Slice 2: the new start (shape only, one understanding with look again / just tell, who leads, one proposal with
   its treatment list, the taste).
3. Slice 3: music and sound lanes.
4. Slice 4: real -> paper -> real; then the video-model restyle after a GPU test.
5. Slice 5: the screens, with the owner.
