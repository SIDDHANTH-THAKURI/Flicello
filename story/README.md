# Flicello's story engine (Remotion)

A talk told as a story: the speaker narrates, and the film cuts away to scenes that act out what they say, with the
speaker cut out of their room and standing in the corner of each scene, so their face and voice carry across the cut.
One grade, grain and vignette over everything; title, rating and genre cards drawn from the voice's own waveform;
screens (an inbox, a feed) built as real UI; captions word by word; the voice cleaned and the music ducked under it.

Phase 1 of docs/proposals/flicello-anything.md (decided 2026-10-18): first hand-run on one video until the owner rates
it above 8/20, then into the app. Remotion is free for Flicello now (an individual or a company of up to 3 people).

```
npm install                                   # once, in story/ (then: npx remotion browser ensure)
python tools/prepare.py <talk> <speech.json> <scene clips> --edit plans/undertone-edit.json --alpha
python tools/listen.py --expect "what he should say"   # re-listen: no word lost at a cut, fillers left
python plans/undertone.py                     # the plan, placed on his words; prints the props file
npx remotion render src/index.ts Story <out>.mp4 --props=<props file>
python tools/check.py <out>.mp4               # frame sheet + loudness / true peak, before anyone sees it
```

prepare.py tightens the talk first, the way an editor would: long silences shortened to a breath, the stumbles named in
the edit file cut on the quietest moment near them, and fillers cut only where they stand alone (a cut inside a word is
heard). Python tools run with the studio's venv.

`src/plan.ts` is the whole film as data (shots with their times, cards, screens, captions, music, sound effects);
The media a plan names lives in %LOCALAPPDATA%/VidoSuper/story/public (or `VIDO_STORY_PUBLIC`), outside the repo,
which sits in a synced folder. Only footage the owner may use goes in: the talk's owner's own
film, and stock that is free to use (Pexels) until generated scenes (Higgsfield) arrive.
