// Each kind of shot. A shot is drawn from its own first frame (its Sequence starts where it is first seen).
import React from 'react';
import { AbsoluteFill, interpolate, OffthreadVideo, spring, staticFile, useCurrentFrame, useVideoConfig, Easing } from 'remotion';
import { DISPLAY, MONO } from '../look';
import type { CardShot, NarratorShot, Plan, SceneShot, UiShot } from '../plan';

const ease = Easing.bezier(0.33, 0, 0.2, 1);

// a slow move over the whole shot: zoom from a to b around `focus`
const Move: React.FC<{ frames: number; zoom?: [number, number]; focus?: [number, number]; children: React.ReactNode }> = ({
  frames,
  zoom = [1, 1.06],
  focus = [0.5, 0.45],
  children,
}) => {
  const frame = useCurrentFrame();
  const z = interpolate(frame, [0, Math.max(1, frames)], zoom, { extrapolateRight: 'clamp', easing: ease });
  return (
    <AbsoluteFill style={{ transform: `scale(${z})`, transformOrigin: `${focus[0] * 100}% ${focus[1] * 100}%` }}>
      {children}
    </AbsoluteFill>
  );
};

const cover: React.CSSProperties = { width: '100%', height: '100%', objectFit: 'cover' };

// the narrator plays in real time: wherever his shot starts, his video is where his voice is
export const Narrator: React.FC<{ shot: NarratorShot; plan: Plan; first: number; frames: number }> = ({ shot, plan, first, frames }) => {
  const { fps } = useVideoConfig();
  return (
    <Move frames={frames} zoom={shot.zoom ?? [1.02, 1.07]} focus={shot.focus ?? [0.5, 0.38]}>
      <OffthreadVideo src={staticFile(plan.narrator.video)} muted startFrom={first + Math.round((plan.narrator.trim ?? 0) * fps)} style={cover} />
    </Move>
  );
};

// the narrator cut out of his room, small, standing in the scene's corner: his face carries across the cut
export const Pip: React.FC<{ plan: Plan; first: number }> = ({ plan, first }) => {
  const { fps } = useVideoConfig();
  const frame = useCurrentFrame();
  const enter = spring({ frame, fps, config: { damping: 18, stiffness: 120 } });
  const src = plan.narrator.alpha ?? plan.narrator.video;
  const at = first + Math.round((plan.narrator.trim ?? 0) * fps);
  const style: React.CSSProperties = {
    position: 'absolute',
    left: 46,
    bottom: 330,
    width: 330,
    height: 586,
    transform: `translateY(${(1 - enter) * 60}px)`,
    opacity: enter,
    filter: 'drop-shadow(0 18px 40px rgba(0,0,0,.65))',
  };
  if (plan.narrator.alpha) {
    return <OffthreadVideo src={staticFile(src)} muted transparent startFrom={at} style={{ ...style, objectFit: 'cover' }} />;
  }
  return (
    <div style={{ ...style, borderRadius: 26, overflow: 'hidden', border: '3px solid rgba(255,255,255,.85)' }}>
      <OffthreadVideo src={staticFile(src)} muted startFrom={at} style={cover} />
    </div>
  );
};

export const Scene: React.FC<{ shot: SceneShot; plan: Plan; first: number; frames: number; lead: number }> = ({ shot, plan, first, frames, lead }) => {
  const { fps } = useVideoConfig();
  const frame = useCurrentFrame();
  // the clip starts `lead` frames early (the transition into it), so its chosen moment lands on the cut
  const from = Math.max(0, Math.round(shot.from * fps) - lead);
  const label = shot.label ?? '';
  const typed = Math.floor(interpolate(frame, [lead + 4, lead + 4 + label.length * 1.6], [0, label.length], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }));
  return (
    <AbsoluteFill style={{ background: '#000' }}>
      <Move frames={frames} zoom={shot.zoom} focus={shot.focus}>
        <OffthreadVideo src={staticFile(shot.file)} muted startFrom={from} playbackRate={shot.rate ?? 1} style={cover} />
      </Move>
      {shot.dark ? <AbsoluteFill style={{ background: `rgba(0,0,0,${shot.dark})` }} /> : null}
      {label ? (
        <div style={{ position: 'absolute', top: 260, left: 70, right: 70, fontFamily: MONO, fontSize: 40, letterSpacing: 2, color: 'rgba(255,255,255,.92)', textTransform: 'uppercase', textShadow: '0 2px 18px rgba(0,0,0,.8)' }}>
          {label.slice(0, typed)}
          <span style={{ opacity: frame % 20 < 10 ? 1 : 0 }}>▌</span>
        </div>
      ) : null}
      {shot.pip ? <Pip plan={plan} first={first} /> : null}
    </AbsoluteFill>
  );
};

const Wave: React.FC<{ levels: number[]; at: number; width: number; height: number; color: string; count?: number }> = ({ levels, at, width, height, color, count = 48 }) => {
  const bars = [];
  for (let k = 0; k < count; k++) {
    const v = levels.length ? levels[Math.min(levels.length - 1, Math.max(0, at - count + k))] ?? 0 : 0.3 + 0.3 * Math.sin(k);
    const h = Math.max(4, v * height);
    bars.push(<div key={k} style={{ width: width / count - 4, height: h, background: color, borderRadius: 4, opacity: 0.35 + 0.65 * (k / count) }} />);
  }
  return <div style={{ display: 'flex', alignItems: 'center', gap: 4, height, width }}>{bars}</div>;
};

export const Card: React.FC<{ shot: CardShot; frames: number; levels: number[]; first: number }> = ({ shot, frames, levels, first }) => {
  const { fps } = useVideoConfig();
  const frame = useCurrentFrame();
  const pop = spring({ frame, fps, config: { damping: 14, stiffness: 140 } });
  const fadeOut = interpolate(frame, [frames - 8, frames], [1, 0.0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  if (shot.kind === 'rating') {
    const value = shot.value ?? 0;
    const shown = interpolate(frame, [4, 4 + fps * 0.9], [0, value], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: ease });
    const ring = (shown / 10) * 2 * Math.PI * 230;
    return (
      <AbsoluteFill style={{ background: 'radial-gradient(circle at 50% 42%, #2a1015 0%, #07060a 70%)', alignItems: 'center', justifyContent: 'center', opacity: fadeOut }}>
        <svg width={560} height={560} style={{ transform: `scale(${0.85 + 0.15 * pop})` }}>
          <circle cx={280} cy={280} r={230} stroke="rgba(255,255,255,.12)" strokeWidth={22} fill="none" />
          <circle cx={280} cy={280} r={230} stroke="#ff3b5c" strokeWidth={22} fill="none" strokeLinecap="round" strokeDasharray={`${ring} 2000`} transform="rotate(-90 280 280)" />
        </svg>
        <div style={{ position: 'absolute', fontFamily: DISPLAY, fontWeight: 800, fontSize: 210, color: '#fff', letterSpacing: -6 }}>
          {shown.toFixed(1)}
          <span style={{ fontSize: 80, opacity: 0.55 }}>/10</span>
        </div>
        {shot.sub ? <div style={{ position: 'absolute', bottom: 520, fontFamily: MONO, fontSize: 38, letterSpacing: 6, color: 'rgba(255,255,255,.7)', textTransform: 'uppercase' }}>{shot.sub}</div> : null}
      </AbsoluteFill>
    );
  }
  const big = shot.kind === 'title' ? 190 : shot.kind === 'genre' ? 150 : 110;
  const letters = shot.text.split('');
  return (
    <AbsoluteFill style={{ background: 'radial-gradient(circle at 50% 45%, #15131c 0%, #050407 72%)', alignItems: 'center', justifyContent: 'center', opacity: fadeOut }}>
      <div style={{ display: 'flex', fontFamily: DISPLAY, fontWeight: 800, fontSize: big, color: '#f3efe8', letterSpacing: shot.kind === 'title' ? 18 : 4 }}>
        {letters.map((ch, k) => {
          const a = spring({ frame: frame - k * 2, fps, config: { damping: 16, stiffness: 160 } });
          return (
            <span key={k} style={{ display: 'inline-block', transform: `translateY(${(1 - a) * 80}px)`, opacity: a, filter: `blur(${(1 - a) * 8}px)` }}>
              {ch === ' ' ? ' ' : ch}
            </span>
          );
        })}
      </div>
      <div style={{ position: 'absolute', top: '58%' }}>
        <Wave levels={levels} at={first + frame} width={760} height={120} color="#ff3b5c" />
      </div>
      {shot.sub ? <div style={{ position: 'absolute', top: '66%', fontFamily: MONO, fontSize: 36, letterSpacing: 8, color: 'rgba(243,239,232,.66)', textTransform: 'uppercase' }}>{shot.sub}</div> : null}
    </AbsoluteFill>
  );
};

export const Ui: React.FC<{ shot: UiShot; plan: Plan; first: number; frames: number; levels: number[] }> = ({ shot, plan, first, frames, levels }) => {
  const { fps } = useVideoConfig();
  const frame = useCurrentFrame();
  const lift = spring({ frame, fps, config: { damping: 18, stiffness: 110 } });
  const n = shot.count ?? (shot.lines?.length ?? 0);
  return (
    <AbsoluteFill style={{ background: 'linear-gradient(180deg, #0b0a10 0%, #121019 100%)', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ width: 860, borderRadius: 48, background: '#17151f', border: '2px solid #2c2838', boxShadow: '0 40px 120px rgba(0,0,0,.6)', padding: '56px 56px 40px', transform: `translateY(${(1 - lift) * 140}px) scale(${0.94 + 0.06 * lift})`, opacity: lift }}>
        {shot.kind === 'email' ? (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 26 }}>
              <div style={{ width: 92, height: 92, borderRadius: 46, background: '#2a2636', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: DISPLAY, fontSize: 48, color: '#8c86a0' }}>?</div>
              <div>
                <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 44, color: '#f3efe8' }}>Anonymous</div>
                <div style={{ fontFamily: MONO, fontSize: 28, color: '#8c86a0' }}>no reply address</div>
              </div>
            </div>
            <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 52, color: '#fff', margin: '40px 0 10px' }}>{shot.title ?? 'listen.'}</div>
            <div style={{ fontFamily: MONO, fontSize: 28, color: '#ff3b5c', marginBottom: 28 }}>{n} attachments</div>
            {Array.from({ length: n }).map((_, k) => {
              const at = 8 + k * Math.max(2, Math.floor((frames - 20) / Math.max(1, n)));
              const a = spring({ frame: frame - at, fps, config: { damping: 18, stiffness: 160 } });
              return (
                <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 22, padding: '14px 0', borderTop: '1px solid #2a2636', opacity: a, transform: `translateX(${(1 - a) * 60}px)` }}>
                  <div style={{ width: 54, height: 54, borderRadius: 14, background: '#2a2636', color: '#ff3b5c', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 26 }}>▶</div>
                  <div style={{ fontFamily: MONO, fontSize: 30, color: '#d8d3e6', flex: 1 }}>clip_{String(k + 1).padStart(2, '0')}.wav</div>
                  <Wave levels={levels} at={first + frame - k * 7} width={220} height={40} color="#ff3b5c" count={16} />
                </div>
              );
            })}
          </>
        ) : (
          <>
            <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 50, color: '#fff', marginBottom: 24 }}>{shot.title}</div>
            {(shot.lines ?? []).map((line, k) => {
              const a = spring({ frame: frame - 6 - k * 6, fps, config: { damping: 18, stiffness: 150 } });
              return (
                <div key={k} style={{ fontFamily: MONO, fontSize: 32, color: '#d8d3e6', padding: '14px 0', borderTop: '1px solid #2a2636', opacity: a }}>
                  {line}
                </div>
              );
            })}
          </>
        )}
      </div>
      {shot.pip ? <Pip plan={plan} first={first} /> : null}
    </AbsoluteFill>
  );
};
