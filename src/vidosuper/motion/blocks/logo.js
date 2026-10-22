// A brand's logo in a corner of the film: it settles in from a little larger, holds at a quiet opacity, and leaves
// with the film. Variables: image (a data URI), x, y (frame shares of its centre), size (share of the frame's width),
// opacity, duration.
window.FlicelloMotion.blocks.logo = function (M) {
  const { V, W, EASE, ms, anim } = M;
  const wrap = M.el('div', 'fm-logo', M.root);
  const img = M.el('img', 'logo', wrap);
  img.src = V.image || '';
  img.alt = '';
  img.style.width = `${(V.size || 0.12) * W}px`;
  img.style.left = `${(V.x ?? 0.85) * 100}%`;
  img.style.top = `${(V.y ?? 0.17) * 100}%`;
  const D = (V.duration || 5) * 1000;
  anim(img, [{ opacity: 0, transform: 'translate(-50%, -50%) scale(1.15)' },
    { opacity: V.opacity ?? 0.9, transform: 'translate(-50%, -50%) scale(1)' }], { duration: ms(14), delay: ms(6), easing: EASE.softLand });
  anim(wrap, [{ opacity: 1 }, { opacity: 0 }], { duration: ms(10), delay: Math.max(0, D - ms(10)), easing: EASE.accelExit, fill: 'forwards' });
};
