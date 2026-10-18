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

export const gradeFilter = (g: Plan['grade']) =>
  `contrast(${g.contrast}) saturate(${g.saturate}) brightness(${g.brightness}) sepia(${Math.max(0, g.warmth)})` +
  (g.warmth < 0 ? ` hue-rotate(${g.warmth * 40}deg)` : '');

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

export const Vignette: React.FC<{ amount: number }> = ({ amount }) =>
  amount <= 0 ? null : (
    <AbsoluteFill
      style={{
        pointerEvents: 'none',
        background: `radial-gradient(ellipse 75% 62% at 50% 46%, rgba(0,0,0,0) 55%, rgba(0,0,0,${amount}) 100%)`,
      }}
    />
  );
