// The story plan: everything a film is made of, as data (written by the editor, later by Flicello's AI).
// Times are seconds on the film's timeline; files are names in the public folder.

// 'swap': between him in his room and a scene: the room turns into the scene around him while he stays, then he steps
// into the corner (or back: he grows out of the corner and his room comes back around him).
export type Transition = { type: 'cut' | 'fade' | 'whip' | 'glitch' | 'wave' | 'iris' | 'swap'; seconds: number };

type Base = { start: number; end: number; in?: Transition };

export type NarratorShot = Base & {
  type: 'narrator';
  zoom?: [number, number]; // a slow push over the shot
  focus?: [number, number]; // where the push is centred
  title?: string; // the lights go out and this stands behind him, big
};

export type SceneShot = Base & {
  type: 'scene';
  file: string;
  from: number; // seconds into the clip
  rate?: number;
  zoom?: [number, number];
  focus?: [number, number];
  pip?: boolean; // the narrator, cut out, small in the corner: his voice and face carry across the scene
  label?: string; // a few words on the scene, typed on
  dark?: number; // 0..1 extra darkness (night, dread)
};

export type CardShot = Base & {
  type: 'card';
  kind: 'title' | 'rating' | 'genre' | 'end';
  text: string;
  sub?: string;
  value?: number; // rating: out of 10
};

export type UiShot = Base & {
  type: 'ui';
  kind: 'email' | 'feed' | 'player' | 'phone';
  title?: string;
  lines?: string[];
  count?: number; // email: audio clips attached
  pip?: boolean;
  // phone: his feed flies past `title` (missed), a suggestion of it drops in at `notifyAt`, it plays at `playAt`
  note?: string;
  notifyAt?: number;
  playAt?: number;
  poster?: string; // the film's picture in the feed
  thumbs?: string[]; // the other posts' pictures (shown blurred: nothing to read)
  clip?: { file: string; from: number };
};

export type Shot = NarratorShot | SceneShot | CardShot | UiShot;

export type Word = { t: number; end: number; text: string };

export type Plan = {
  width: number;
  height: number;
  fps: number;
  duration: number;
  narrator: { video: string; alpha?: string; voice: string };
  cuts?: number[]; // where his talk was tightened (a jump in his picture): the framing alternates there
  music?: { file: string; gain: number; duck: number; from?: number; fadeOut?: number };
  loudness?: string; // per-frame voice level (0..1), for ducking and the waveform: a JSON array in public/
  grade: { contrast: number; saturate: number; brightness: number; warmth: number; grain: number; vignette: number };
  shots: Shot[];
  words?: Word[];
  sfx?: { at: number; file: string; gain: number }[];
  shakes?: { at: number; amount: number }[]; // a jolt of the whole frame (a hit)
};
