// Flicello's motion runtime, shared by every block. It holds the measured professional timing (motionmaxxing's
// frame-by-frame study of 53 professional moves):
// - a move takes about 10 frames;
// - an entry takes 11-12, an opacity change 6, a blur 7-8;
// - arrivals decelerate from oversize and departures accelerate;
// - overshoot is rare and late.
// Everything is a Web Animation, so it seeks frame-exactly:
// - The render: HyperFrames steps the page's animations frame by frame.
// - The instant preview: the player mounts the same block in a stage of its own and calls seek() from its clock.
(function () {
  const EASE = {
    softLand: 'cubic-bezier(0.16, 1, 0.3, 1)', // arrivals decelerate into place
    snapSettle: 'cubic-bezier(0.2, 1.3, 0.36, 1)', // a small, late overshoot: for a hit, rarely
    accelExit: 'cubic-bezier(0.7, 0, 0.84, 0)', // departures accelerate away
    glide: 'cubic-bezier(0.45, 0, 0.55, 1)', // a held drift
  };
  const FONTS = { display: "'FlicelloDisplay', sans-serif", serif: "'FlicelloSerif', serif",
    mono: "'FlicelloMono', monospace" };

  // A block's toolkit on its stage (W x H, the film's size): its values, the timing, and every animation it makes,
  // so the preview can seek them all.
  function make(stage, V, W, H) {
    const FPS = V.fps || 30;
    const all = [];
    stage.classList.add('fm-stage');
    stage.style.width = `${W}px`;
    stage.style.height = `${H}px`;
    const ms = (frames) => (frames / FPS) * 1000;
    const anim = (node, keys, opts) => {
      const a = node.animate(keys, { fill: 'both', ...opts });
      all.push(a);
      return a;
    };
    const el = (tag, cls, parent) => {
      const node = document.createElement(tag);
      if (cls) node.className = cls;
      if (parent) parent.append(node);
      return node;
    };
    const split = (node, text, cls = 'ch') => [...text].map((c) => {
      const s = el('span', cls, node);
      s.textContent = c === ' ' ? ' ' : c;
      return s;
    });
    // shrink type to fit a width, measured again once its own font is in (until then the fallback's wider letters
    // would shrink it for good); layout sizes, so a scaled preview measures the same as the render
    const fit = (node, maxWidth, after) => {
      const base = parseFloat(node.style.fontSize) || parseFloat(getComputedStyle(node).fontSize);
      const go = () => {
        node.style.fontSize = `${base}px`;
        const w = node.offsetWidth;
        if (w > maxWidth) node.style.fontSize = `${base * (maxWidth / w)}px`;
        if (after) after(parseFloat(node.style.fontSize));
      };
      go();
      if (document.fonts) {
        document.fonts.load(`800 ${base}px ${getComputedStyle(node).fontFamily}`, node.textContent || 'A').then(go, () => {});
      }
    };
    // the soft dark pad behind type, `depth` dark at its heart and falling away gently (0.34 is the stylesheet's own)
    const scrim = (node, depth) => {
      const d = Math.max(0, Math.min(1, depth));
      const a = (k) => (d * k).toFixed(3);
      node.style.background = `radial-gradient(closest-side, rgba(0, 0, 0, ${d}), rgba(0, 0, 0, ${a(0.6)}) 40%, `
        + `rgba(0, 0, 0, ${a(0.2)}) 72%, rgba(0, 0, 0, 0))`;
    };
    const seek = (seconds) => {
      for (const a of all) {
        a.pause();
        a.currentTime = seconds * 1000;
      }
    };
    return { V, W, H, FPS, EASE, FONTS, ms, anim, el, split, fit, scrim, seek, root: stage };
  }

  const prior = window.FlicelloMotion;
  window.FlicelloMotion = { EASE, FONTS, make, blocks: (prior && prior.blocks) || {} };
}());
