// The film: every shot on its own layer (later ones on top), each one's way in overlapping the cut (half before, half
// after; a swap starts on the cut and holds the shot before it underneath), the look over all of it, the captions,
// and the sound (the voice whole, the music ducked under it).
import React from 'react';
import { AbsoluteFill, Audio, interpolate, Sequence, staticFile, useCurrentFrame, useVideoConfig, Easing, random } from 'remotion';
import { DISPLAY, GradeDefs, Grain, useFonts, useLevels, Vignette } from './look';
import { Card, CORNER, Narrator, Scene, Ui } from './parts/shots';
import type { Plan, Shot, Transition, Word } from './plan';

const ease = Easing.bezier(0.33, 0, 0.2, 1);
const clamp = { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' } as const;

// how a shot comes in, over its first `frames` frames
const Entrance: React.FC<{ t?: Transition; frames: number; shot: Shot; children: React.ReactNode }> = ({ t, frames, shot, children }) => {
  const frame = useCurrentFrame();
  if (!t || t.type === 'cut' || frames <= 0) return <AbsoluteFill>{children}</AbsoluteFill>;
  const p = interpolate(frame, [0, frames], [0, 1], { ...clamp, easing: ease });
  let style: React.CSSProperties = {};
  if (t.type === 'swap') {
    // into a scene: the scene (with him on top, where he already stands) fades in over his room; back to him: he does it
    style = shot.type === 'narrator' ? {} : { opacity: interpolate(frame, [0, frames * 0.5], [0, 1], { ...clamp, easing: ease }) };
  }
  if (t.type === 'fade') style = { opacity: p };
  if (t.type === 'whip') style = { transform: `translateY(${(1 - p) * 105}%)`, filter: p < 0.98 ? `blur(${(1 - p) * 22}px)` : undefined };
  if (t.type === 'iris') style = { clipPath: `circle(${p * 80}% at 50% 44%)` };
  if (t.type === 'wave') style = { clipPath: `inset(${(1 - p) * 50}% 0 ${(1 - p) * 50}% 0)` };
  if (t.type === 'glitch') {
    const on = frame < frames;
    const jx = on ? (random(`gx${frame}`) - 0.5) * 80 * (1 - p) : 0;
    const show = on ? random(`gs${frame}`) < 0.35 + 0.65 * p : true;
    style = { transform: `translateX(${jx}px)`, opacity: show ? 1 : 0, filter: on ? `hue-rotate(${(1 - p) * 90}deg) contrast(${1 + (1 - p) * 0.6})` : undefined };
  }
  return <AbsoluteFill style={style}>{children}</AbsoluteFill>;
};

const span = (t: Transition | undefined, fps: number) => (t && t.type !== 'cut' ? Math.round(t.seconds * fps) : 0);

const Layer: React.FC<{ shot: Shot; prev?: Shot; next?: Shot; plan: Plan; levels: number[] }> = ({ shot, prev, next, plan, levels }) => {
  const { fps } = useVideoConfig();
  const swapIn = shot.in?.type === 'swap';
  const inHalf = swapIn ? 0 : Math.round(span(shot.in, fps) / 2);
  const outHalf = next?.in?.type === 'swap' ? span(next.in, fps) : Math.round(span(next?.in, fps) / 2);
  const first = Math.max(0, Math.round(shot.start * fps) - inHalf);
  const last = Math.round(shot.end * fps) + outHalf;
  const frames = last - first;
  let body: React.ReactNode = null;
  if (shot.type === 'narrator') body = <Narrator shot={shot} plan={plan} first={first} frames={frames} prev={prev} />;
  if (shot.type === 'scene') body = <Scene shot={shot} plan={plan} first={first} frames={frames} lead={inHalf} prev={prev} next={next} />;
  if (shot.type === 'card') body = <Card shot={shot} frames={frames} levels={levels} first={first} />;
  if (shot.type === 'ui') body = <Ui shot={shot} plan={plan} first={first} frames={frames} levels={levels} />;
  return (
    <Sequence from={first} durationInFrames={Math.max(1, frames)}>
      <Entrance t={shot.in} frames={swapIn ? span(shot.in, fps) : inHalf * 2} shot={shot}>{body}</Entrance>
    </Sequence>
  );
};

// a few words at a time, the spoken one lit, in the lower third; not over cards, screens or the film's name (they
// speak for themselves), and to the right of him when he stands in the corner
const Captions: React.FC<{ words: Word[]; plan: Plan }> = ({ words, plan }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const shot = plan.shots.find((s) => s.start <= t && t < s.end);
  // (the arc is the exception: there his words name each part as it is drawn)
  if (!shot || (shot.type === 'card' && shot.kind !== 'arc') || shot.type === 'ui' || (shot.type === 'narrator' && (shot.title || shot.rating !== undefined))) return null;
  // a page ends at three words, at a pause, at a full stop, or where the picture changes
  const starts = plan.shots.map((s) => s.start);
  const pages: Word[][] = [];
  for (const w of words) {
    const page = pages[pages.length - 1];
    const last = page ? page[page.length - 1] : undefined;
    const cut = last ? starts.some((s) => last.t < s && s <= w.t + 0.05) : false;
    if (!page || !last || page.length >= 3 || w.t - last.end > 0.3 || w.t - page[0].t > 1.3 || /[.?!,]$/.test(last.text) || cut) pages.push([w]);
    else page.push(w);
  }
  const page = pages.find((p) => p[0].t - 0.05 <= t && t < p[p.length - 1].end + 0.2);
  // a page begun under a title (where captions are hidden) is not shown late after it
  if (!page || page[0].t < shot.start - 0.3) return null;
  const aside = shot.type === 'scene' && shot.pip;
  const left = aside ? CORNER.faceX + CORNER.width * 0.42 : 60;
  return (
    <AbsoluteFill>
      <div style={{ position: 'absolute', left, right: 60, top: 1395, display: 'flex', gap: 20, flexWrap: 'wrap', justifyContent: 'center', fontFamily: DISPLAY, fontWeight: 800, fontSize: 76, lineHeight: 1.08, letterSpacing: -1, textTransform: 'lowercase' }}>
        {page.map((w, k) => {
          const lit = w.t <= t;
          const pop = interpolate(t, [w.t, w.t + 0.12], [0.86, 1], clamp);
          return (
            <span key={k} style={{ color: lit ? (w.hot ? '#ff3b5c' : '#fff') : 'rgba(255,255,255,.45)', transform: `scale(${lit ? pop : 0.9})`, display: 'inline-block', textShadow: '0 4px 26px rgba(0,0,0,.85), 0 0 3px rgba(0,0,0,.9)' }}>
              {w.text.replace(/[.,!?]+$/, '')}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

// a jolt of the whole frame on a hit, settling in a quarter second
const useShake = (plan: Plan) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  let x = 0, y = 0;
  for (const s of plan.shakes ?? []) {
    const d = t - s.at;
    if (d < 0 || d > 0.25) continue;
    const fall = 1 - d / 0.25;
    x += (random(`sx${frame}`) - 0.5) * 2 * s.amount * fall;
    y += (random(`sy${frame}`) - 0.5) * 2 * s.amount * fall;
  }
  return x || y ? `translate(${x}px, ${y}px) scale(1.02)` : undefined;
};

export const Story: React.FC<{ plan: Plan }> = ({ plan }) => {
  useFonts();
  const { fps, durationInFrames } = useVideoConfig();
  const levels = useLevels(plan.loudness);
  const shake = useShake(plan);
  const music = plan.music;
  const fadeOut = Math.round((music?.fadeOut ?? 0) * fps);
  return (
    <AbsoluteFill style={{ background: '#000' }}>
      <GradeDefs g={plan.grade} />
      <AbsoluteFill style={{ filter: 'url(#grade)', transform: shake }}>
        {plan.shots.map((s, i) => (
          <Layer key={i} shot={s} prev={plan.shots[i - 1]} next={plan.shots[i + 1]} plan={plan} levels={levels} />
        ))}
      </AbsoluteFill>
      <Grain amount={plan.grade.grain} />
      <Vignette amount={plan.grade.vignette} />
      {plan.words ? <Captions words={plan.words} plan={plan} /> : null}
      {plan.narrator.voice ? <Audio src={staticFile(plan.narrator.voice)} /> : null}
      {music ? (
        <Audio
          src={staticFile(music.file)}
          startFrom={Math.round((music.from ?? 0) * fps)}
          volume={(f) => {
            const tail = fadeOut ? interpolate(f, [durationInFrames - fadeOut, durationInFrames - 1], [1, 0], clamp) : 1;
            const head = interpolate(f, [0, 12], [0, 1], clamp);
            const shape = music.shape && music.shape.length > 1 ? interpolate(f / fps, music.shape.map((p) => p[0]), music.shape.map((p) => p[1]), clamp) : 1;
            return music.gain * (1 - music.duck * (levels[f] ?? 0)) * tail * head * shape;
          }}
        />
      ) : null}
      {(plan.sfx ?? []).map((x, i) => (
        <Sequence key={i} from={Math.max(0, Math.round(x.at * fps))}>
          <Audio src={staticFile(x.file)} volume={x.gain} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
