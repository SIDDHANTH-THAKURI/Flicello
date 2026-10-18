// The film: every shot on its own layer (later ones on top), each one's way in overlapping the cut (half before, half
// after), the look over all of it, the captions, and the sound (the voice whole, the music ducked under it).
import React from 'react';
import { AbsoluteFill, Audio, interpolate, Sequence, staticFile, useCurrentFrame, useVideoConfig, Easing, random } from 'remotion';
import { DISPLAY, gradeFilter, Grain, useFonts, useLevels, Vignette } from './look';
import { Card, Narrator, Scene, Ui } from './parts/shots';
import type { Plan, Shot, Transition, Word } from './plan';

const ease = Easing.bezier(0.33, 0, 0.2, 1);

// how a shot comes in, over its first `frames` frames
const Entrance: React.FC<{ t?: Transition; frames: number; children: React.ReactNode }> = ({ t, frames, children }) => {
  const frame = useCurrentFrame();
  if (!t || t.type === 'cut' || frames <= 0) return <AbsoluteFill>{children}</AbsoluteFill>;
  const p = interpolate(frame, [0, frames], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: ease });
  let style: React.CSSProperties = {};
  if (t.type === 'fade') style = { opacity: p };
  if (t.type === 'whip') style = { transform: `translateX(${(1 - p) * 110}%)`, filter: `blur(${(1 - p) * 26}px)` };
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

const Layer: React.FC<{ shot: Shot; next?: Shot; plan: Plan; levels: number[] }> = ({ shot, next, plan, levels }) => {
  const { fps } = useVideoConfig();
  const inHalf = shot.in && shot.in.type !== 'cut' ? Math.round((shot.in.seconds * fps) / 2) : 0;
  const outHalf = next?.in && next.in.type !== 'cut' ? Math.round((next.in.seconds * fps) / 2) : 0;
  const first = Math.max(0, Math.round(shot.start * fps) - inHalf);
  const last = Math.round(shot.end * fps) + outHalf;
  const frames = last - first;
  let body: React.ReactNode = null;
  if (shot.type === 'narrator') body = <Narrator shot={shot} plan={plan} first={first} frames={frames} />;
  if (shot.type === 'scene') body = <Scene shot={shot} plan={plan} first={first} frames={frames} lead={inHalf} />;
  if (shot.type === 'card') body = <Card shot={shot} frames={frames} levels={levels} first={first} />;
  if (shot.type === 'ui') body = <Ui shot={shot} plan={plan} first={first} frames={frames} levels={levels} />;
  return (
    <Sequence from={first} durationInFrames={Math.max(1, frames)}>
      <Entrance t={shot.in} frames={inHalf * 2}>{body}</Entrance>
    </Sequence>
  );
};

// a few words at a time, the spoken one lit, low in the frame; not over cards and screens (they speak for themselves)
const Captions: React.FC<{ words: Word[]; plan: Plan }> = ({ words, plan }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const shot = plan.shots.find((s) => s.start <= t && t < s.end);
  if (!shot || shot.type === 'card' || shot.type === 'ui') return null;
  const pages: Word[][] = [];
  for (const w of words) {
    const page = pages[pages.length - 1];
    if (!page || page.length >= 3 || w.t - page[0].t > 1.3 || /[.?!]$/.test(page[page.length - 1].text)) pages.push([w]);
    else page.push(w);
  }
  const page = pages.find((p) => p[0].t - 0.05 <= t && t < p[p.length - 1].end + 0.25);
  if (!page) return null;
  return (
    <AbsoluteFill style={{ justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 300 }}>
      <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap', justifyContent: 'center', maxWidth: 900, fontFamily: DISPLAY, fontWeight: 800, fontSize: 74, letterSpacing: -1, textTransform: 'lowercase' }}>
        {page.map((w, k) => {
          const lit = w.t <= t;
          const pop = interpolate(t, [w.t, w.t + 0.12], [0.86, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
          return (
            <span key={k} style={{ color: lit ? '#fff' : 'rgba(255,255,255,.42)', transform: `scale(${lit ? pop : 0.9})`, display: 'inline-block', textShadow: '0 4px 24px rgba(0,0,0,.75), 0 0 2px rgba(0,0,0,.9)' }}>
              {w.text.replace(/[.,!?]+$/, '')}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

export const Story: React.FC<{ plan: Plan }> = ({ plan }) => {
  useFonts();
  const { fps } = useVideoConfig();
  const levels = useLevels(plan.loudness);
  const music = plan.music;
  return (
    <AbsoluteFill style={{ background: '#000' }}>
      <AbsoluteFill style={{ filter: gradeFilter(plan.grade) }}>
        {plan.shots.map((s, i) => (
          <Layer key={i} shot={s} next={plan.shots[i + 1]} plan={plan} levels={levels} />
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
          volume={(f) => music.gain * (1 - music.duck * (levels[f] ?? 0))}
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
