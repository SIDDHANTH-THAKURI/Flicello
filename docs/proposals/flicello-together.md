# Flicello, together: you and your editor on one screen

Status: taken into [flicello-your-editor.md](flicello-your-editor.md) (22 Oct): its principles and research stand, the
order of work there replaces the order below. The engine fixes it builds on are done
(versions.py: pinned shots, numbered strip, changes checked before they are made, continue from any version;
fixes 3-6: one place once, nobody in shot, beat sync as cuts on the hits, landmarks whole, their words win).

## The owner's concern comes first (22 Oct)

> "I don't want to be stuck in the loop trying to explain again and again, and I don't know when it's going to get
> me the right output, and my money would run out."

More conversation is still a loop. The design must need **fewer rounds** and have a **known cost**. What the
Aura Farming film really cost (live database):

| | Old way (21 Oct) | New way (built 22 Oct) |
|---|---|---|
| AI for the whole film | $7.99 (of a $10 per-film cap) | - |
| One change | ~$0.35 (an Opus re-plan of the whole film, often twice) | typed: ~$0.03 (a small model reads it); a button on a shot: $0; "make this part again": ~$0.15 |
| What a change can break | anything (the whole film re-planned) | nothing it doesn't name (shots pinned to their frames) |
| Rounds | 19 | each round only moves forward |

Five things stop the loop, in this order:

1. **Say it once.** The owner's saved rules contradict each other today (short dissolves vs. every cut hard,
   "no 1-second flicks" vs. fast cuts on every beat, three copies of "let the drop breathe", talking-video rules
   applied to music films), so the AI gets mixed orders every time. Clean them into one short list, each rule tied
   to an automatic check, and add a rule only when the person says yes (no more rewriting the profile after every
   rating: 37 learning calls on one film).
2. **It checks itself before you see it.** Every saved rule and every must-have is a check on the plan and on the
   video (beat sync, nobody in shot, no place twice, landmarks whole). A version that fails goes back to the editor
   without costing a round of the person's time; they only see versions that pass.
3. **Pick, don't explain.** When something is a matter of taste (hold the drop 3 s or 5 s? the bridge or the Opera
   House on the drop?) the editor shows two options side by side, made from stills on the real beats (free,
   seconds), and the person taps one. Choosing takes a second; explaining took paragraphs.
4. **The price before every step, and a cap.** Each action says what it costs before it runs (free / ~3 cents /
   uses one change); the film shows what it has used; it never passes its cap without asking. The owner's own
   films can run on their Claude Pro plan instead of the paid key (no money per film; Pro's limits apply).
5. **"Done" is visible.** The person's asks as a checklist on every version: all ✓ and they like it, it is done.

## Why: the honest diagnosis

The Aura Farming film (21 Oct) took 19 versions and about 7 hours. The best version (v16, 9/10) came from luck,
and the next three were worse. The engine faults behind that are fixed. The deeper fault is the product's shape:

- **It is a row of forms, not a conversation.** Clips, then a form; the story, then a form; the sample, then a
  form; the film, then a rating form and a change box. Each step waits minutes and the AI never talks back. It
  never says what it understood, never asks, never explains.
- **We don't share a language.** The owner said "8th cut", "beat sync", "duplicate", "that guy". The AI had no
  shot numbers to match, read "beat sync" as "any beat", "duplicate" as "the same seconds of one clip", and had no
  idea who "that guy" was. There was no way to point at the thing on screen.
- **Seeing costs five minutes.** Every idea had to become a whole new film before anyone could see it. So the
  owner typed long paragraphs to make each round count, and long paragraphs are exactly what the AI misread.
- **Nothing is visible to both of us.** What the AI agreed to (the brief), what the music does (the drop, the big
  hits), which places the footage has, who is in which shot: all of it lived inside the AI's prompt, invisible to
  the person, so neither side could check the other.

## What the best do (research, Oct 2026)

- **A chat beside a visible, editable timeline.** Descript's Underlord sits in a sidebar of the editor and edits
  the same project the person sees ([Descript](https://help.descript.com/hc/en-us/articles/36803785502221-Underlord-beta-Your-AI-co-editor-in-Descript));
  ChatCut docks its chat next to a timeline and every AI edit lands as a normal, undoable step
  ([ChatCut](https://chatcut.io/docs/editor-overview)). Runway Agent (May 2026) takes a brief in conversation and
  returns an edited, sound-designed video ([Runway Agent](https://hunted.space/dashboard/runway-agent)).
- **Point and say.** People mix words with pointing at the frame ("make *this* brighter"); ExpressEdit combines
  language with sketching on the video and found novices could express and carry out their ideas
  ([ExpressEdit](https://arxiv.org/html/2403.17693v1)); in a text-driven video study, participants anchored requests
  on a frame and asked to annotate frames directly ([Rewriting Video](https://arxiv.org/pdf/2601.08565)).
- **The human leads; back and forth, not a pipeline.** Studies of human-AI co-creation recommend keeping the
  person in charge of the ideas and warn that model-led tools reduce ownership
  ([arXiv 2510.23324](https://arxiv.org/pdf/2510.23324)); step-by-step tools clash with how creative people work,
  and an AI that argues its view reads as an opinionated colleague rather than an executor
  ([arXiv 2401.07312](https://arxiv.org/html/2401.07312v1)).

## The idea in one line

**One screen, one conversation.** The film on one side, your editor on the other. You talk (or tap a shot, or point
at a frame); it answers by showing you, in seconds, and nothing is made until you say so.

## The experience

1. **Start.** Drop the clips and the song and say what it is, in one line or by voice. The editor watches the
   footage, then talks: *"A dusk ferry ride: the Opera House and the Harbour Bridge are your stars, and the 808 in
   this song hits every beat after 0:17. From your last films I'll keep: nobody in shot, cuts on the 808s, no place
   twice. Keep those?"* Tap answers, or type.
2. **The story.** Three ideas arrive as cards with real stills, in the conversation. *"Like 2, but start slow"*:
   it merges them and shows the new card.
3. **Music.** Track options play inline; *"this one, drop a bit later"*.
4. **First look in about a minute.** A preview cut (lower quality, real footage) plays in the player. Under it,
   the strip: numbered shots, their places ("Opera House"), and the music's marks (the drop, the big hits) drawn
   on the same line, so both of you see what lands where.
5. **Changing, by pointing.** Tap shot 9 and say *"longer, till the next big hit"*. Your message carries a chip:
   *[Shot 9 · Opera House]*. The editor answers: *"Shot 9 now holds to 0:21.4, the next 808; shot 10 starts
   there. Here it is:"* and the preview updates in seconds, with a before/after toggle. *"Keep."*
6. **It argues, briefly, when it should.** *"Holding 9 that long leaves the bridge only half a second. Shorten
   the bridge or drop it?"* It asks when it isn't sure, and says what it can't do.
7. **Final.** Full quality renders in the background. The brief's items show as checks against the real video:
   ✓ beat sync (14 of 16 cuts on the 808s), ✓ nobody in shot, ✓ no place twice, ✓ 30 s.
8. **Taste.** *"What I learned: you like the drop to breathe about 3 s, then quick cuts. Keep that for next
   time?"* Learning comes from the conversation, not from a rating form.

## Principles

1. **Talk, don't fill forms.** One question at a time, with tap answers; free text and voice always work.
2. **Point and say.** Any shot, frame or music mark can be tapped into a message; the AI gets exactly what you
   meant.
3. **Show before making.** Every proposal is a preview in seconds; the full film is made only when you say so.
4. **Say what you understood, and ask.** The editor restates a change before doing it, explains its own
   choices in one line, and asks when unsure. It never silently re-plans.
5. **One brief, visible to both.** What you agreed on is always on screen, editable by talking, and every item has
   a check on the real video.
6. **The same words on both sides.** Shot numbers, place names, the drop, the big hits, who is in a shot: shown in
   the interface and given to the AI.
7. **Your words win.** A rule of taste never overrides what you asked; it may warn you once.
8. **Nothing good is lost.** Every version stays; continue from any; undo any change.
9. **Fast.** Under 30 seconds from saying a small change to seeing it.
10. **It remembers you.** Your style carries into the next film as visible starting rules you can switch off.

## How it works

- **The film's state, in one place.** The brief (what we agreed, with a check per item), the version graph, each
  version's shots (numbered, pinned to their frames), the music's marks, the footage's moments with places and
  people. Every screen is a view of this; the AI reads the same thing.
- **The editor is an agent with tools, not a pipeline.** A conversation per film. Tools: look (the state),
  change (exact edits on numbered shots: versions.py), make again (a stretch, by the creative model), preview,
  render, music, ask (a card with choices), remember (a style rule). A fast model talks; the creative model is
  called only to design or re-imagine a stretch.
- **Previews in seconds.** Unchanged shots keep their exact frames now, so the renderer's per-shot cache already
  covers most of a changed film. A preview tier (lower resolution, only the changed shots rendered, the rest from
  cache) makes the loop seconds, not minutes. The full render runs in the background when you say so.
- **Checks that match the brief.** Each brief item maps to a check on the plan and on the video (beat sync,
  nobody in shot, no place twice, length, music). A failed check goes back to the editor before you see the
  result; an item with no check yet says so instead of being silently ignored.
- **Taste from conversation.** Style rules are proposed by the editor from what you said and kept only when
  you agree, so the profile stops rewriting itself after every rating.

## Order

| Phase | What | Why first |
|---|---|---|
| 0 | Push the engine fixes (pinned shots, numbered strip, checked changes, any version, one place once, nobody in shot, sync on the hits, landmarks whole). | Changes cost cents and never break what works. |
| 1 | Say it once + the price: clean the saved rules into one list tied to checks (the person confirms it), learn only on a yes; show the cost before each step, a per-film cap, and the owner's films on Claude Pro if they want. | Stops the mixed orders and the surprise bill. |
| 2 | It checks itself: every version is checked against the rules and must-haves before it is shown; failures go back to the editor. A checklist of their asks on every version. | They only see versions that pass. |
| 3 | Pick, don't explain: two options from stills on the real beats, side by side, for any matter of taste. | A tap instead of a paragraph. |
| 4 | Fast previews of changes (partial renders from the shot cache) and the conversation beside the film (point at a shot, the editor restates, shows, then makes). | Seeing in seconds. |
| 5 | The whole flow as one conversation; phone layout and voice; an editor that spots problems before you do. | The colleague, not the tool. |

## Cost and speed

- Talking uses a fast model (a few cents a film, or Pro usage on the owner's PC); the creative model is called
  only for a new design or a stretch made again, as now.
- Previews cost render time on the PC or Modal, not AI; pinned shots make most of each preview a cache hit.
- Fewer rounds: a change you can see in seconds and point at precisely should take 3-5 rounds, not 19.

## What stays

The engine: analysis, beat grid, design, render, checks, versions, credits, accounts, the PC and Modal renderers,
the light and dark themes. The new design replaces the screens on top of them, one phase at a time; the current
pages keep working until each phase replaces them.

## Decisions for the owner

1. Push the engine fixes now, then phases 1-3 (say it once + price, self-check, pick don't explain) before the
   conversation? Recommended.
2. Your own films: Claude Pro plan (no money per film; its limits apply) or the paid key (fast, ~$0.03 a change
   now)?
3. Your saved rules: shall I clean them into one list for you to approve (about 8 rules from 19)?
