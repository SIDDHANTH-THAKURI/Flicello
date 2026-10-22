// Frame accents: graphic finish laid over a stretch of the film. Styles:
// - brackets: camera viewfinder corners close in, a recording dot blinks and a thin frame line draws round;
// - sweep: a band of light crosses the frame on a diagonal (a sheen over a reveal);
// - leak: warm light leaks drift in from the edges and burn out (film light hitting the gate);
// - lines: thin parallel lines draw across and away, on the beat (V.beats).
// Variables: style, accent, beats (seconds into the block), duration.
window.FlicelloMotion.blocks.accent = function (M) {
  const { V, W, H, EASE, ms, anim } = M;
  const D = (V.duration || 2) * 1000;
  const wrap = M.el('div', 'fm-accent', M.root);
  const accent = V.accent || '#ffd38a';
  const style = V.style || 'brackets';
  const out = Math.max(ms(20), D - ms(10));
  if (style === 'brackets') {
    const m = Math.min(W, H) * 0.07;
    const arm = Math.min(W, H) * 0.09;
    const t = Math.max(3, Math.min(W, H) * 0.006);
    [['left', 'top'], ['right', 'top'], ['right', 'bottom'], ['left', 'bottom']].forEach(([x, y], i) => {
      const c = M.el('div', 'corner', wrap);
      Object.assign(c.style, { [x]: `${m}px`, [y]: `${m}px`, width: `${arm}px`, height: `${arm}px`,
        [`border${y[0].toUpperCase()}${y.slice(1)}`]: `${t}px solid #fff`, [`border${x[0].toUpperCase()}${x.slice(1)}`]: `${t}px solid #fff` });
      const dx = x === 'left' ? -1 : 1; const dy = y === 'top' ? -1 : 1;
      anim(c, [{ transform: `translate(${dx * m}px, ${dy * m}px) scale(1.4)`, opacity: 0 }, { transform: 'translate(0, 0) scale(1)', opacity: 1 }],
        { duration: ms(12), delay: ms(2) + i * ms(1), easing: EASE.softLand });
      anim(c, [{ opacity: 1 }, { opacity: 0 }], { duration: ms(8), delay: out, easing: EASE.accelExit, fill: 'forwards' });
    });
    const rec = M.el('div', 'rec', wrap);
    Object.assign(rec.style, { left: `${m + arm * 0.5}px`, top: `${m + arm * 0.5}px`, fontSize: `${Math.min(W, H) * 0.035}px` });
    rec.textContent = 'REC';
    const blink = [];
    for (let k = 0; k <= 10; k += 1) blink.push({ opacity: k % 2 ? 0.25 : 1, offset: k / 10, easing: 'steps(1, end)' });
    anim(rec, blink, { duration: Math.max(1, out - ms(14)), delay: ms(14) });
    anim(rec, [{ opacity: 1 }, { opacity: 0 }], { duration: ms(6), delay: out, fill: 'forwards' });
  } else if (style === 'sweep') {
    const band = M.el('div', 'band', wrap);
    anim(band, [{ transform: 'translateX(-130%) rotate(18deg)' }, { transform: 'translateX(130%) rotate(18deg)' }],
      { duration: Math.min(D, ms(26)), delay: ms(2), easing: 'cubic-bezier(0.45, 0, 0.25, 1)' });
  } else if (style === 'leak') {
    const spots = [['-10%', '20%', '#ff9a3c'], ['105%', '75%', '#ff5f6d'], ['40%', '-15%', '#ffd38a']];
    spots.forEach(([x, y, c], i) => {
      const s = M.el('div', 'spot', wrap);
      Object.assign(s.style, { left: x, top: y, width: `${Math.max(W, H) * 0.8}px`, height: `${Math.max(W, H) * 0.8}px`,
        background: `radial-gradient(closest-side, ${c}cc, ${c}55 45%, ${c}00)` });
      anim(s, [{ opacity: 0, transform: 'translate(-50%, -50%) scale(0.7)' },
        { opacity: 0.85, transform: 'translate(-50%, -50%) scale(1.05)', offset: 0.45 },
        { opacity: 0, transform: 'translate(-50%, -50%) scale(1.25)' }], { duration: D * (0.75 + 0.1 * i), delay: i * D * 0.08, easing: EASE.glide });
    });
  } else { // lines on the beat
    const beats = (V.beats && V.beats.length ? V.beats : [0.1, 0.6, 1.1]).map((b) => b * 1000);
    beats.forEach((b, k) => { // a group of three lines a beat, thick to thin, two frames apart
      const top = 18 + ((k * 37) % 58);
      [0.016, 0.007, 0.003].forEach((th, j) => {
        const l = M.el('div', 'line', wrap);
        Object.assign(l.style, { top: `${top + j * 2.4}%`, height: `${Math.max(3, H * th)}px`, background: j === 1 ? '#fff' : accent });
        const from = k % 2 ? '100%' : '0%';
        l.style.transformOrigin = `${from} 50%`;
        anim(l, [{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)', offset: 0.45 },
          { transform: 'scaleX(0)', transformOrigin: `${k % 2 ? '0%' : '100%'} 50%` }],
        { duration: ms(14), delay: b + j * ms(2), easing: EASE.softLand });
      });
    });
  }
};
