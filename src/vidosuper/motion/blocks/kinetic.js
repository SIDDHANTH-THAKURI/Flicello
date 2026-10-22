// Kinetic type on the beat: one word at a time, each cut in on its beat (type cuts like a picture does: no fades),
// landing from oversize out of a blur with a short punch, every other word in the serif italic and the accent for
// contrast. The last word holds, then leaves accelerating. Variables: text, beats (seconds into the block, one per
// word; missing ones follow on at the same spacing), y, size, color, accent, duration.
window.FlicelloMotion.blocks.kinetic = function (M) {
  const { V, W, H, EASE, ms, anim, FONTS } = M;
  const wrap = M.el('div', 'fm-kinetic', M.root);
  const words = String(V.text || '').split(/\s+/).filter(Boolean);
  const D = (V.duration || 2) * 1000;
  const beats = (V.beats || []).map((b) => b * 1000);
  const step = beats.length > 1 ? beats[1] - beats[0] : Math.max(ms(8), (D - ms(14)) / Math.max(1, words.length));
  while (beats.length < words.length) beats.push((beats.length ? beats[beats.length - 1] + step : ms(2)));
  const size = (V.size || 0.12) * H;
  words.forEach((w, k) => {
    const node = M.el('div', 'word', wrap);
    node.textContent = w;
    const alt = k % 2 === 1;
    node.style.top = `${(V.y ?? 0.45) * 100}%`;
    node.style.fontSize = `${alt ? size * 0.92 : size}px`;
    node.style.fontFamily = alt ? FONTS.serif : FONTS.display;
    node.style.fontWeight = alt ? '400' : '800';
    node.style.color = alt ? (V.accent || '#ffd38a') : (V.color || '#ffffff');
    M.fit(node, W * (1 - 2 * (V.side ?? 0.08)));
    const on = beats[k];
    const off = k + 1 < words.length ? beats[k + 1] : D - ms(9);
    // on screen exactly from its beat to the next one (a cut, not a fade)
    anim(node, [{ visibility: 'visible' }, { visibility: 'visible' }],
      { duration: Math.max(1, off - on), delay: on, fill: 'none' });
    anim(node, [{ transform: 'translate(-50%, -50%) scale(1.45)', filter: 'blur(0.06em)' },
      { transform: 'translate(-50%, -50%) scale(0.97)', filter: 'blur(0)', offset: 0.55 },
      { transform: 'translate(-50%, -50%) scale(1)' }], { duration: ms(7), delay: on, easing: EASE.softLand });
    if (k === words.length - 1) { // the last word leaves accelerating
      anim(node, [{ opacity: 1, letterSpacing: '0em' }, { opacity: 0, letterSpacing: '0.25em' }],
        { duration: ms(9), delay: off, easing: EASE.accelExit });
      anim(node, [{ visibility: 'visible' }, { visibility: 'visible' }], { duration: ms(9), delay: off, fill: 'none' });
    }
  });
};
