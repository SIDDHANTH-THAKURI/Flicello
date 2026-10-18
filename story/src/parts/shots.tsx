// Each kind of shot. A shot is drawn from its own first frame (its Sequence starts where it is first seen).
import React from 'react';
import { AbsoluteFill, Easing, Img, interpolate, OffthreadVideo, Sequence, spring, staticFile, useCurrentFrame, useVideoConfig } from 'remotion';
import { DISPLAY, MONO } from '../look';
import type { CardShot, NarratorShot, Plan, SceneShot, Shot, UiShot } from '../plan';

const ease = Easing.bezier(0.33, 0, 0.2, 1);
const clamp = { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' } as const;
const cover: React.CSSProperties = { width: '100%', height: '100%', objectFit: 'cover' };
const RED = '#ff3b5c';

// ---- where he stands ------------------------------------------------------------------------------------------------
// A pose puts a point P of his (full-frame) picture at (tx, ty) + k * P. Full size with a push is a scale around the
// focus; the corner is small, bottom left, clear of the platforms' own buttons and captions.
export type Pose = { k: number; tx: number; ty: number };
// in the corner his face sits at (faceX, faceY); FACE is where his face is in his own picture
export const CORNER = { scale: 0.32, faceX: 196, faceY: 1165, width: 0.32 * 1080 };
const FACE: [number, number] = [0.45, 0.47];
const PUNCH = 1.09;
const NARRATOR_ZOOM: [number, number] = [1.0, 1.05];
const NARRATOR_FOCUS: [number, number] = [0.46, 0.4];

const fullPose = (k: number, focus: [number, number], W: number, H: number): Pose => ({ k, tx: focus[0] * W * (1 - k), ty: focus[1] * H * (1 - k) });
export const cornerPose = (W: number, H: number): Pose => ({ k: CORNER.scale, tx: CORNER.faceX - FACE[0] * W * CORNER.scale, ty: CORNER.faceY - FACE[1] * H * CORNER.scale });

// small, his picture's edges would show as straight lines through his body: they dissolve instead (f: 0 full size, 1 corner)
const edges = (f: number): React.CSSProperties => {
  if (f <= 0.001) return {};
  const m = `linear-gradient(180deg, #000 ${100 - 42 * f}%, rgba(0,0,0,0) ${100 - 8 * f}%), linear-gradient(90deg, rgba(0,0,0,0) 0%, #000 ${10 * f}%, #000 ${100 - 14 * f}%, rgba(0,0,0,0) 100%)`;
  return { WebkitMaskImage: m, maskImage: m, WebkitMaskComposite: 'source-in', maskComposite: 'intersect' } as React.CSSProperties;
};
export const mixPose = (a: Pose, b: Pose, p: number): Pose => ({ k: a.k + (b.k - a.k) * p, tx: a.tx + (b.tx - a.tx) * p, ty: a.ty + (b.ty - a.ty) * p });
const poseStyle = (p: Pose): React.CSSProperties => ({
  position: 'absolute', left: 0, top: 0, width: '100%', height: '100%',
  transform: `translate(${p.tx}px, ${p.ty}px) scale(${p.k})`, transformOrigin: '0 0',
});

// His framing at film time t in a narrator shot: a slow push, and at every jump in his (tightened) talk the framing
// alternates between wide and close, so a jump reads as a cut. Held at the shot's end (a swap starts from there).
export const narratorPose = (shot: NarratorShot, cuts: number[], t: number, W: number, H: number): Pose => {
  const tt = Math.min(t, shot.end);
  const zoom = shot.zoom ?? NARRATOR_ZOOM;
  const p = Math.min(1, Math.max(0, (tt - shot.start) / Math.max(0.01, shot.end - shot.start)));
  const z = zoom[0] + (zoom[1] - zoom[0]) * ease(p);
  const jumps = cuts.filter((c) => c > shot.start + 0.05 && c <= tt).length;
  return fullPose(z * (jumps % 2 ? PUNCH : 1), shot.focus ?? NARRATOR_FOCUS, W, H);
};

// him without his room, in the pose given (his cut-out plays in time with his voice)
const Host: React.FC<{ plan: Plan; first: number; pose: Pose; shadow?: boolean; fade?: number }> = ({ plan, first, pose, shadow = true, fade = 0 }) =>
  plan.narrator.alpha ? (
    <div style={{ ...poseStyle(pose), ...edges(fade), filter: shadow ? 'drop-shadow(0 30px 50px rgba(0,0,0,.7))' : undefined }}>
      <OffthreadVideo src={staticFile(plan.narrator.alpha)} muted transparent startFrom={first} style={cover} />
    </div>
  ) : null;

const Room: React.FC<{ plan: Plan; first: number; pose: Pose; style?: React.CSSProperties }> = ({ plan, first, pose, style }) => (
  <div style={{ ...poseStyle(pose), ...style }}>
    <OffthreadVideo src={staticFile(plan.narrator.video)} muted startFrom={first} style={cover} />
  </div>
);

// ---- him ------------------------------------------------------------------------------------------------------------
export const Narrator: React.FC<{ shot: NarratorShot; plan: Plan; first: number; frames: number; prev?: Shot }> = ({ shot, plan, first, frames, prev }) => {
  const { fps, width: W, height: H } = useVideoConfig();
  const frame = useCurrentFrame();
  const pose = narratorPose(shot, plan.cuts ?? [], (first + frame) / fps, W, H);
  if (shot.in?.type === 'swap' && prev && plan.narrator.alpha) {
    // back from a scene: he grows out of the corner, then his room comes back around him
    const D = Math.round(shot.in.seconds * fps);
    if (frame < D) {
      const grow = interpolate(frame, [0, 0.6 * D], [0, 1], { ...clamp, easing: ease });
      const room = interpolate(frame, [0.45 * D, D], [0, 1], { ...clamp, easing: ease });
      return (
        <AbsoluteFill>
          {room > 0 ? <AbsoluteFill style={{ opacity: room }}><Room plan={plan} first={first} pose={pose} /></AbsoluteFill> : null}
          <Host plan={plan} first={first} pose={mixPose(cornerPose(W, H), pose, grow)} shadow={grow < 0.9} fade={1 - grow} />
        </AbsoluteFill>
      );
    }
  }
  if (shot.title && plan.narrator.alpha) return <TitleBehind shot={shot} plan={plan} first={first} frames={frames} pose={pose} />;
  return (
    <AbsoluteFill>
      <Room plan={plan} first={first} pose={pose} />
    </AbsoluteFill>
  );
};

// The lights go out on the film's name: his room drops to dark with a flicker, the name rises behind him with a red
// glow behind his head; the lights come back at the end.
const FLICKER = [1, 0.3, 0.85, 0.12, 0.55, 0.07, 0.32, 0.1, 0.18];
const TitleBehind: React.FC<{ shot: NarratorShot; plan: Plan; first: number; frames: number; pose: Pose }> = ({ shot, plan, first, frames, pose }) => {
  const { fps, width: W } = useVideoConfig();
  const frame = useCurrentFrame();
  const DARK = 0.17;
  const lit = frame < FLICKER.length ? Math.max(DARK, FLICKER[frame]) : frame > frames - 6 ? interpolate(frame, [frames - 6, frames - 1], [DARK, 1], clamp) : DARK;
  const glow = (1 - lit) / (1 - DARK);
  const out = interpolate(frame, [frames - 7, frames - 1], [1, 0], clamp);
  const n = (shot.title ?? '').length;
  const size = Math.min(210, (W * 0.86) / (n * 0.74 + (n - 1) * 0.04)); // wide caps: about 0.74 em each
  return (
    <AbsoluteFill style={{ background: '#000' }}>
      <Room plan={plan} first={first} pose={pose} style={{ filter: `brightness(${lit}) saturate(${0.6 + 0.4 * lit})` }} />
      <AbsoluteFill style={{ background: `radial-gradient(ellipse 60% 34% at 46% 34%, rgba(255,40,70,${0.3 * glow}) 0%, rgba(0,0,0,0) 70%)` }} />
      <div style={{ position: 'absolute', top: 612 - 0.86 * size, left: 0, right: 0, display: 'flex', justifyContent: 'center', fontFamily: DISPLAY, fontWeight: 800, fontSize: size, lineHeight: 1, letterSpacing: 0.04 * size, color: '#f3efe8', textShadow: `0 0 50px rgba(255,59,92,${0.55 * glow})` }}>
        {(shot.title ?? '').split('').map((ch, k) => {
          const a = spring({ frame: frame - 3 - k * 1.5, fps, config: { damping: 15, stiffness: 150 } });
          return (
            <span key={k} style={{ display: 'inline-block', transform: `translateY(${(1 - a) * 120}px)`, opacity: a * out, filter: `blur(${(1 - a) * 10}px)` }}>
              {ch === ' ' ? ' ' : ch}
            </span>
          );
        })}
      </div>
      <Host plan={plan} first={first} pose={pose} shadow={false} />
    </AbsoluteFill>
  );
};

// ---- the scenes -----------------------------------------------------------------------------------------------------
// a slow move over the whole shot: zoom from a to b around `focus`
const Move: React.FC<{ frames: number; zoom?: [number, number]; focus?: [number, number]; children: React.ReactNode }> = ({ frames, zoom = [1, 1.06], focus = [0.5, 0.45], children }) => {
  const frame = useCurrentFrame();
  const z = interpolate(frame, [0, Math.max(1, frames)], zoom, { ...clamp, easing: ease });
  return <AbsoluteFill style={{ transform: `scale(${z})`, transformOrigin: `${focus[0] * 100}% ${focus[1] * 100}%` }}>{children}</AbsoluteFill>;
};

export const Scene: React.FC<{ shot: SceneShot; plan: Plan; first: number; frames: number; lead: number; prev?: Shot; next?: Shot }> = ({ shot, plan, first, frames, lead, prev, next }) => {
  const { fps, width: W, height: H } = useVideoConfig();
  const frame = useCurrentFrame();
  // the clip starts `lead` frames early (the transition into it), so its chosen moment lands on the cut
  const from = Math.max(0, Math.round(shot.from * fps) - lead);
  const label = shot.label ?? '';
  const typed = label ? Math.floor(interpolate(frame, [lead + 4, lead + 4 + label.length * 1.6], [0, label.length], clamp)) : 0;
  let host: React.ReactNode = null;
  if (shot.pip) {
    let pose = cornerPose(W, H);
    let fade = 1;
    if (shot.in?.type === 'swap' && prev?.type === 'narrator') {
      // from his room: he stays where he was while the room fades into this scene, then steps into the corner
      const D = Math.round(shot.in.seconds * fps);
      fade = interpolate(frame, [0.4 * D, D], [0, 1], { ...clamp, easing: ease });
      pose = mixPose(narratorPose(prev, plan.cuts ?? [], shot.start, W, H), pose, fade);
    }
    const own = Math.round((shot.end - shot.start) * fps) + lead;
    const leaving = next?.in?.type === 'swap' && frame >= own; // the next shot takes him back
    if (!leaving) host = <Host plan={plan} first={first} pose={pose} fade={fade} />;
  }
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
      {host}
    </AbsoluteFill>
  );
};

// ---- cards ----------------------------------------------------------------------------------------------------------
const Wave: React.FC<{ levels: number[]; at: number; width: number; height: number; color: string; count?: number }> = ({ levels, at, width, height, color, count = 48 }) => {
  const bars = [];
  for (let k = 0; k < count; k++) {
    const v = levels.length ? levels[Math.min(levels.length - 1, Math.max(0, at - count + k))] ?? 0 : 0.3 + 0.3 * Math.sin(k);
    bars.push(<div key={k} style={{ width: width / count - 4, height: Math.max(4, v * height), background: color, borderRadius: 4, opacity: 0.35 + 0.65 * (k / count) }} />);
  }
  return <div style={{ display: 'flex', alignItems: 'center', gap: 4, height, width }}>{bars}</div>;
};

export const Card: React.FC<{ shot: CardShot; frames: number; levels: number[]; first: number }> = ({ shot, frames, levels, first }) => {
  const { fps } = useVideoConfig();
  const frame = useCurrentFrame();
  const pop = spring({ frame, fps, config: { damping: 14, stiffness: 140 } });
  const fadeOut = interpolate(frame, [frames - 8, frames], [1, 0.0], clamp);
  if (shot.kind === 'rating') {
    const value = shot.value ?? 0;
    const shown = interpolate(frame, [4, 4 + fps * 0.9], [0, value], { ...clamp, easing: ease });
    const ring = (shown / 10) * 2 * Math.PI * 230;
    return (
      <AbsoluteFill style={{ background: 'radial-gradient(circle at 50% 42%, #2a1015 0%, #07060a 70%)', alignItems: 'center', justifyContent: 'center', opacity: fadeOut }}>
        <svg width={560} height={560} style={{ transform: `scale(${0.85 + 0.15 * pop})` }}>
          <circle cx={280} cy={280} r={230} stroke="rgba(255,255,255,.12)" strokeWidth={22} fill="none" />
          <circle cx={280} cy={280} r={230} stroke={RED} strokeWidth={22} fill="none" strokeLinecap="round" strokeDasharray={`${ring} 2000`} transform="rotate(-90 280 280)" />
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
  return (
    <AbsoluteFill style={{ background: 'radial-gradient(circle at 50% 45%, #15131c 0%, #050407 72%)', alignItems: 'center', justifyContent: 'center', opacity: fadeOut }}>
      <div style={{ display: 'flex', fontFamily: DISPLAY, fontWeight: 800, fontSize: big, color: '#f3efe8', letterSpacing: shot.kind === 'title' ? 18 : 4 }}>
        {shot.text.split('').map((ch, k) => {
          const a = spring({ frame: frame - k * 2, fps, config: { damping: 16, stiffness: 160 } });
          return (
            <span key={k} style={{ display: 'inline-block', transform: `translateY(${(1 - a) * 80}px)`, opacity: a, filter: `blur(${(1 - a) * 8}px)` }}>
              {ch === ' ' ? ' ' : ch}
            </span>
          );
        })}
      </div>
      <div style={{ position: 'absolute', top: '58%' }}>
        <Wave levels={levels} at={first + frame} width={760} height={120} color={RED} />
      </div>
      {shot.sub ? <div style={{ position: 'absolute', top: '66%', fontFamily: MONO, fontSize: 36, letterSpacing: 8, color: 'rgba(243,239,232,.66)', textTransform: 'uppercase' }}>{shot.sub}</div> : null}
    </AbsoluteFill>
  );
};

// ---- screens --------------------------------------------------------------------------------------------------------
const StatusBar: React.FC = () => (
  <div style={{ position: 'absolute', top: 34, left: 70, right: 70, display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontFamily: DISPLAY, fontWeight: 700, fontSize: 34, color: 'rgba(255,255,255,.92)' }}>
    <span>11:48</span>
    <span style={{ display: 'flex', gap: 10, alignItems: 'flex-end' }}>
      {[14, 20, 26, 32].map((h, k) => <span key={k} style={{ width: 8, height: h, borderRadius: 3, background: 'rgba(255,255,255,.9)' }} />)}
      <span style={{ width: 56, height: 28, borderRadius: 8, border: '3px solid rgba(255,255,255,.85)', marginLeft: 14, position: 'relative' }}>
        <span style={{ position: 'absolute', left: 3, top: 3, bottom: 3, width: 22, borderRadius: 4, background: 'rgba(255,255,255,.9)' }} />
      </span>
    </span>
  </div>
);

// other people's posts: bright, blurred, nothing to read
const POSTS = [['#ff9a4d', '#ff4d8d'], ['#4dd8ff', '#5b4dff'], ['#ffe14d', '#ff8a4d'], ['#a64dff', '#ff4dd2'], ['#4dffa6', '#2f8cff'], ['#ff6b6b', '#ffc24d']];

const Skeleton: React.FC<{ widths: number[] }> = ({ widths }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
    {widths.map((w, k) => <div key={k} style={{ width: `${w}%`, height: k ? 22 : 28, borderRadius: 14, background: 'rgba(255,255,255,.14)' }} />)}
  </div>
);

// His feed flies past the film (a lot of people missed it) and settles; then a suggestion of it drops in, and opens
// into a player that plays it.
const Phone: React.FC<{ shot: UiShot; first: number }> = ({ shot, first }) => {
  const { fps, width: W, height: H } = useVideoConfig();
  const frame = useCurrentFrame();
  const title = shot.title ?? '';
  const notifyF = Math.round((shot.notifyAt ?? shot.end + 9) * fps) - first;
  const playF = Math.round((shot.playAt ?? shot.end + 9) * fps) - first;
  const CW = 960, CH = 600, STEP = CH + 44, TOP = 190, FILM = 5;
  const scrollFor = Math.max(0.5, notifyF / fps - 0.15);
  // the film's card crosses the middle at 40% of the scroll, still flying (missed), then the feed settles
  // a flick that slows (ease-out): the film's card passes the middle at about 40% and is off the top when it stops
  const total = TOP + FILM * STEP + CH + 120;
  const p = Math.min(1, frame / fps / scrollFor);
  const offset = total * (1 - Math.pow(1 - p, 2));
  const speed = (2 * total * (1 - p)) / scrollFor; // px per second
  const smear = Math.min(5, speed / 2200);
  const thumbs = shot.thumbs ?? [];
  const cards: React.ReactNode[] = [];
  for (let k = 0; k < FILM + 5; k++) {
    const y = TOP + k * STEP - offset;
    if (y > H || y + CH < 0) continue;
    const isFilm = k === FILM;
    const pic = isFilm ? shot.poster : thumbs.length ? thumbs[k % thumbs.length] : undefined;
    const [c1, c2] = POSTS[k % POSTS.length];
    cards.push(
      <div key={k} style={{ position: 'absolute', left: (W - CW) / 2, top: y, width: CW, height: CH, borderRadius: 40, overflow: 'hidden', background: isFilm ? '#0d0c12' : `radial-gradient(circle at 28% 32%, ${c1} 0%, rgba(0,0,0,0) 58%), radial-gradient(circle at 76% 70%, ${c2} 0%, rgba(0,0,0,0) 62%), #2a2433` }}>
        {pic ? <Img src={staticFile(pic)} style={{ ...cover, filter: isFilm ? 'brightness(.8) contrast(1.2) saturate(.8)' : 'blur(18px) saturate(1.2)', transform: isFilm ? undefined : 'scale(1.15)', opacity: isFilm ? 1 : 0.55 }} /> : null}
        <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(180deg, rgba(0,0,0,0) 45%, rgba(0,0,0,.82) 100%)' }} />
        <div style={{ position: 'absolute', left: 44, right: 44, bottom: 40 }}>
          {isFilm ? (
            <>
              <div style={{ display: 'inline-block', fontFamily: MONO, fontWeight: 700, fontSize: 26, letterSpacing: 4, color: '#fff', background: RED, borderRadius: 10, padding: '6px 14px', marginBottom: 16 }}>NEW</div>
              <div style={{ fontFamily: DISPLAY, fontWeight: 800, fontSize: 96, letterSpacing: 6, color: '#f3efe8', lineHeight: 1 }}>{title}</div>
            </>
          ) : (
            <Skeleton widths={[62, 38]} />
          )}
        </div>
      </div>,
    );
  }
  const nIn = spring({ frame: frame - notifyF, fps, config: { damping: 17, stiffness: 150 } });
  const pIn = spring({ frame: frame - playF, fps, config: { damping: 22, stiffness: 110 } });
  // the banner, then the player it opens into
  const bx = 60, bw = 960;
  const PY = 150; // the player's top: a watch page
  const by = interpolate(nIn, [0, 1], [-240, 96]) + (PY - 96) * pIn;
  const bh = 190 + (540 - 190) * pIn;
  const played = Math.max(0, frame - playF) / fps;
  return (
    <AbsoluteFill style={{ background: '#0b0a10' }}>
      <AbsoluteFill style={{ filter: smear > 0.3 ? `blur(${smear}px)` : undefined }}>{cards}</AbsoluteFill>
      <AbsoluteFill style={{ background: `rgba(5,4,8,${0.3 * nIn + 0.35 * pIn})` }} />
      <StatusBar />
      {nIn > 0.001 ? (
        <div style={{ position: 'absolute', left: bx, top: by, width: bw, height: bh, borderRadius: 44 - 8 * pIn, overflow: 'hidden', background: 'rgba(30,28,38,.96)', border: '1px solid rgba(255,255,255,.09)', boxShadow: '0 30px 80px rgba(0,0,0,.6)' }}>
          {pIn > 0.001 && shot.clip ? (
            <AbsoluteFill style={{ opacity: Math.min(1, pIn * 1.6) }}>
              <Sequence from={playF}>
                <OffthreadVideo src={staticFile(shot.clip.file)} muted startFrom={Math.round(shot.clip.from * fps)} style={cover} />
              </Sequence>
            </AbsoluteFill>
          ) : null}
          <div style={{ position: 'absolute', left: 36, top: 37, display: 'flex', alignItems: 'center', gap: 30, opacity: 1 - Math.min(1, pIn * 2.5) }}>
            <div style={{ width: 116, height: 116, borderRadius: 30, background: RED, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <div style={{ width: 0, height: 0, borderTop: '24px solid transparent', borderBottom: '24px solid transparent', borderLeft: '40px solid #fff', marginLeft: 8 }} />
            </div>
            <div>
              <div style={{ fontFamily: MONO, fontSize: 30, color: '#b3adc4', marginBottom: 8 }}>{shot.note ?? 'Recommended for you'}</div>
              <div style={{ fontFamily: DISPLAY, fontWeight: 800, fontSize: 62, letterSpacing: 4, color: '#fff', lineHeight: 1 }}>{title}</div>
            </div>
          </div>
        </div>
      ) : null}
      {pIn > 0.001 ? (
        <div style={{ position: 'absolute', left: 0, top: PY + 540, width: W, bottom: 0, opacity: pIn, background: '#0b0a10', padding: '34px 60px 0' }}>
          <div style={{ height: 8, borderRadius: 4, background: 'rgba(255,255,255,.16)', overflow: 'hidden' }}>
            <div style={{ width: `${Math.min(100, 4 + played * 9)}%`, height: '100%', background: RED }} />
          </div>
          <div style={{ fontFamily: DISPLAY, fontWeight: 800, fontSize: 76, letterSpacing: 5, color: '#f3efe8', margin: '36px 0 26px' }}>{title}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 22, marginBottom: 44 }}>
            <div style={{ width: 76, height: 76, borderRadius: 38, background: `linear-gradient(135deg, ${RED}, #7a2dff)` }} />
            <div style={{ flex: 1 }}><Skeleton widths={[44, 26]} /></div>
          </div>
          {[0, 1, 2].map((k) => {
            // up next: other posts, coming up from below
            const rise = spring({ frame: frame - playF - 6 - k * 4, fps, config: { damping: 20, stiffness: 120 } });
            const [c1, c2] = POSTS[(k + 2) % POSTS.length];
            return (
              <div key={k} style={{ display: 'flex', gap: 30, marginBottom: 30, opacity: rise, transform: `translateY(${(1 - rise) * 80}px)` }}>
                <div style={{ width: 380, height: 214, borderRadius: 24, flexShrink: 0, background: `radial-gradient(circle at 30% 35%, ${c1} 0%, rgba(0,0,0,0) 60%), radial-gradient(circle at 75% 70%, ${c2} 0%, rgba(0,0,0,0) 62%), #2a2433` }} />
                <div style={{ flex: 1, paddingTop: 12 }}><Skeleton widths={[90, 70, 40]} /></div>
              </div>
            );
          })}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

export const Ui: React.FC<{ shot: UiShot; plan: Plan; first: number; frames: number; levels: number[] }> = ({ shot, plan, first, frames, levels }) => {
  const { fps, width: W, height: H } = useVideoConfig();
  const frame = useCurrentFrame();
  if (shot.kind === 'phone') return <Phone shot={shot} first={first} />;
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
            <div style={{ fontFamily: MONO, fontSize: 28, color: RED, marginBottom: 28 }}>{n} attachments</div>
            {Array.from({ length: n }).map((_, k) => {
              const at = 8 + k * Math.max(2, Math.floor((frames - 20) / Math.max(1, n)));
              const a = spring({ frame: frame - at, fps, config: { damping: 18, stiffness: 160 } });
              return (
                <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 22, padding: '14px 0', borderTop: '1px solid #2a2636', opacity: a, transform: `translateX(${(1 - a) * 60}px)` }}>
                  <div style={{ width: 54, height: 54, borderRadius: 14, background: '#2a2636', color: RED, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 26 }}>▶</div>
                  <div style={{ fontFamily: MONO, fontSize: 30, color: '#d8d3e6', flex: 1 }}>clip_{String(k + 1).padStart(2, '0')}.wav</div>
                  <Wave levels={levels} at={first + frame - k * 7} width={220} height={40} color={RED} count={16} />
                </div>
              );
            })}
          </>
        ) : (
          <>
            <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 50, color: '#fff', marginBottom: 24 }}>{shot.title}</div>
            {(shot.lines ?? []).map((line, k) => {
              const a = spring({ frame: frame - 6 - k * 6, fps, config: { damping: 18, stiffness: 150 } });
              return <div key={k} style={{ fontFamily: MONO, fontSize: 32, color: '#d8d3e6', padding: '14px 0', borderTop: '1px solid #2a2636', opacity: a }}>{line}</div>;
            })}
          </>
        )}
      </div>
      {shot.pip ? <Host plan={plan} first={first} pose={cornerPose(W, H)} fade={1} /> : null}
    </AbsoluteFill>
  );
};
