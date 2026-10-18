import React from 'react';
import { Composition } from 'remotion';
import { Story } from './Story';
import type { Plan } from './plan';

const EMPTY: Plan = {
  width: 1080,
  height: 1920,
  fps: 30,
  duration: 5,
  narrator: { video: '', voice: '' },
  grade: { contrast: 1, saturate: 1, brightness: 1, warmth: 0, grain: 0, vignette: 0 },
  shots: [{ type: 'card', kind: 'title', text: 'Flicello', start: 0, end: 5 }],
};

export const Root: React.FC = () => (
  <Composition
    id="Story"
    component={Story}
    durationInFrames={150}
    fps={30}
    width={1080}
    height={1920}
    defaultProps={{ plan: EMPTY }}
    calculateMetadata={({ props }) => ({
      durationInFrames: Math.round(props.plan.duration * props.plan.fps),
      fps: props.plan.fps,
      width: props.plan.width,
      height: props.plan.height,
    })}
  />
);
