// The film's one look, laid over everything: the grade, moving film grain, a vignette; the fonts; the voice levels.
import React, { useEffect, useState } from 'react';
import { AbsoluteFill, continueRender, delayRender, staticFile, useCurrentFrame } from 'remotion';
import type { Plan } from './plan';

export const DISPLAY = 'Bricolage';
export const MONO = 'GeistMono';

let fontsWanted = false;
export const useFonts = () => {
  const [handle] = useState(() => (fontsWanted ? null : delayRender('fonts')));
  useEffect(() => {
    if (handle === null) return;
    fontsWanted = true;
    const faces = [
      new FontFace(DISPLAY, `url(${staticFile('fonts/bricolage.woff2')})`, { weight: '200 800' }),
      new FontFace(MONO, `url(${staticFile('fonts/geist-mono.woff2')})`, { weight: '100 900' }),
    ];
    Promise.all(faces.map((f) => f.load()))
      .then((loaded) => loaded.forEach((f) => document.fonts.add(f)))
      .finally(() => continueRender(handle));
  }, [handle]);
};

// per-frame voice level (0..1), measured from the voice track before the render (prepare.py)
export const useLevels = (file?: string): number[] => {
  const [levels, setLevels] = useState<number[]>([]);
  const [handle] = useState(() => (file ? delayRender('levels') : null));
  useEffect(() => {
    if (!file || handle === null) return;
    fetch(staticFile(file))
      .then((r) => r.json())
      .then((data: number[]) => setLevels(data))
      .finally(() => continueRender(handle));
  }, [file, handle]);
  return levels;
};

// One grade for everything (his room, his cut-out, the scenes, the screens), as an SVG filter: colour pulled down,
// then per-channel curves: an S for contrast, shadows cooled, highlights warmed (skin stays warm, rooms go moody).
export const GradeDefs: React.FC<{ g: Plan['grade'] }> = ({ g }) => {
  const curve = (lift: number, gain: number) => {
    const pts = [0, 0.1, 0.25, 0.42, 0.6, 0.78, 0.92, 1];
    const s = (x: number) => x - (g.contrast - 1) * 0.35 * Math.sin(2 * Math.PI * x); // an S around the middle
    return pts.map((x) => Math.min(1, Math.max(0, lift * (1 - x) + s(x) * gain * g.brightness)).toFixed(3)).join(' ');
  };
  const w = g.warmth;
  return (
    <svg width="0" height="0" style={{ position: 'absolute' }}>
      <filter id="grade" colorInterpolationFilters="sRGB" x="0" y="0" width="100%" height="100%">
        <feColorMatrix type="saturate" values={String(g.saturate)} />
        <feComponentTransfer>
          <feFuncR type="table" tableValues={curve(0.0, 1 + 0.04 * w)} />
          <feFuncG type="table" tableValues={curve(0.012, 1)} />
          <feFuncB type="table" tableValues={curve(0.05, 1 - 0.05 * w)} />
        </feComponentTransfer>
      </filter>
    </svg>
  );
};

export const Grain: React.FC<{ amount: number }> = ({ amount }) => {
  const frame = useCurrentFrame();
  if (amount <= 0) return null;
  // a new grain every frame (seeded by the frame, so a render is always the same)
  return (
    <AbsoluteFill style={{ mixBlendMode: 'overlay', opacity: amount, pointerEvents: 'none' }}>
      <svg width="100%" height="100%">
        <filter id={`g${frame}`}>
          <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" seed={frame % 97} stitchTiles="stitch" />
          <feColorMatrix type="saturate" values="0" />
        </filter>
        <rect width="100%" height="100%" filter={`url(#g${frame})`} />
      </svg>
    </AbsoluteFill>
  );
};

// darker edges, and the top of the frame held down a little (a bright ceiling or sky pulls the eye off a face)
export const Vignette: React.FC<{ amount: number }> = ({ amount }) =>
  amount <= 0 ? null : (
    <AbsoluteFill
      style={{
        pointerEvents: 'none',
        background: `radial-gradient(ellipse 72% 58% at 50% 50%, rgba(0,0,0,0) 48%, rgba(0,0,0,${amount}) 100%), linear-gradient(180deg, rgba(0,0,0,${amount * 0.8}) 0%, rgba(0,0,0,0) 30%)`,
      }}
    />
  );
