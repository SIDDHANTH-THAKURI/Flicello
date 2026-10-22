// A title: a headline (one or more lines), an accent bar and a line under it, in one of four professional moves.
// - rise: letters land one after another from below, out of a blur.
// - slam: the word arrives from oversize on a hit.
// - mask: a wipe reveals it, behind a moving edge.
// - split: two halves meet in the middle.
// It holds with a slow drift, then leaves accelerating. Variables:
// - text ('\n' between lines), sub, bar;
// - style (rise / slam / mask / split);
// - x, y (frame fractions of its centre), align (center / left), size (share of frame height), side (margin share);
// - color, accent, font (display / serif / mono);
// - in_at, out_at (seconds), duration.
window.FlicelloMotion.blocks.title = function (M) {
  const { V, W, H, EASE, ms, anim, split, FONTS } = M;
  const wrap = M.el('div', 'fm-title', M.root);
  const box = M.el('div', 'title-box', wrap);
  const scrim = M.el('div', 'title-scrim', box);
  const main = M.el('div', 'title-main', box);
  const bar = M.el('span', 'title-bar', box);
  const sub = M.el('div', 'title-sub', box);
  const left = V.align === 'left';
  box.style.left = `${(V.x ?? 0.5) * 100}%`;
  box.style.top = `${(V.y ?? 0.42) * 100}%`;
  box.style.fontSize = `${(V.size || 0.085) * H}px`;
  box.style.fontFamily = FONTS[V.font] || FONTS.display;
  if (left) box.classList.add('align-left');
  main.style.color = V.color || '#ffffff';
  sub.style.color = V.accent || '#ffd38a';
  bar.style.background = V.accent || '#ffd38a';
  sub.textContent = V.sub || '';
  if (!V.sub) sub.style.display = 'none';
  if (!V.bar) bar.style.display = 'none';
  const letters = [];
  String(V.text || '').split('\n').forEach((line) => letters.push(...split(M.el('span', 'title-line', main), line)));
  const room = (1 - 2 * (V.side ?? 0.08)) * W; // the headline and its line each fit on their own
  const subSize = parseFloat(getComputedStyle(sub).fontSize) / parseFloat(getComputedStyle(box).fontSize);
  M.fit(main, room, (px) => { // the bar and the line under it follow the headline's own size
    bar.style.fontSize = `${px}px`;
    if (V.sub) {
      sub.style.fontSize = `${px * subSize}px`;
      M.fit(sub, room);
    }
  });

  const at = (V.in_at ?? 0.08) * 1000;
  const out = (V.out_at ?? Math.max(0.6, (V.duration || 2.5) - ms(10) / 1000)) * 1000;
  const style = V.style || 'rise';
  const centre = left ? 'translate(0, -50%)' : 'translate(-50%, -50%)';

  if (style === 'rise') { // letters land one by one, two frames apart, from below and out of a blur
    letters.forEach((s, i) => anim(s, [
      { opacity: 0, transform: 'translateY(0.32em) scale(1.18)', filter: 'blur(0.09em)' },
      { opacity: 1, transform: 'translateY(0) scale(1)', filter: 'blur(0)' },
    ], { duration: ms(11), delay: at + i * ms(2), easing: EASE.softLand }));
  } else if (style === 'slam') { // from oversize onto the hit, with a short shake as it lands
    anim(main, [
      { opacity: 0, transform: 'scale(3.4)', filter: 'blur(0.2em)' },
      { opacity: 1, transform: 'scale(1)', filter: 'blur(0)' },
    ], { duration: ms(8), delay: at, easing: EASE.softLand });
    anim(box, [
      { transform: `${centre} translate(0, 0)` },
      { transform: `${centre} translate(0.03em, -0.02em)` },
      { transform: `${centre} translate(-0.02em, 0.015em)` },
      { transform: `${centre} translate(0, 0)` },
    ], { duration: ms(6), delay: at + ms(8), easing: 'linear' });
  } else if (style === 'mask') { // a wipe from the left, its edge ahead of the letters
    anim(main, [
      { clipPath: 'inset(-20% 100% -20% 0)', transform: 'translateX(-0.12em)' },
      { clipPath: 'inset(-20% 0% -20% 0)', transform: 'translateX(0)' },
    ], { duration: ms(14), delay: at, easing: EASE.softLand });
  } else { // split: the first half from the left, the second from the right, meeting in the middle
    const half = Math.ceil(letters.length / 2);
    letters.forEach((s, i) => anim(s, [
      { opacity: 0, transform: `translateX(${i < half ? -0.6 : 0.6}em)`, filter: 'blur(0.08em)' },
      { opacity: 1, transform: 'translateX(0)', filter: 'blur(0)' },
    ], { duration: ms(12), delay: at + Math.abs(i - half + 0.5) * ms(1), easing: EASE.softLand }));
  }
  if (typeof V.scrim === 'number') M.scrim(scrim, V.scrim); // deeper over a bright picture (measured by the tool)
  anim(scrim, [{ opacity: 0 }, { opacity: 1 }], { duration: ms(10), delay: at, easing: EASE.softLand });
  if (V.bar) anim(bar, [{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }],
    { duration: ms(12), delay: at + ms(8), easing: EASE.softLand });
  if (V.sub) anim(sub, [
    { opacity: 0, letterSpacing: '0.8em' },
    { opacity: 1, letterSpacing: '0.32em' },
  ], { duration: ms(14), delay: at + ms(10), easing: EASE.softLand });
  // a slow drift while it holds, so the frame is never dead
  anim(main, [{ translate: '0 0' }, { translate: '0 -0.04em' }],
    { duration: Math.max(1, out - at), delay: at, easing: EASE.glide, composite: 'add' });
  // leave accelerating: up, out of focus, gone in 9 frames
  anim(wrap, [{ opacity: 1, filter: 'blur(0)', translate: '0 0' }, { opacity: 0, filter: 'blur(0.06em)', translate: '0 -0.28em' }],
    { duration: ms(9), delay: out, easing: EASE.accelExit });
};
