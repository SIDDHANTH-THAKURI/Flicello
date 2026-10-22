// A graphic transition over a cut: shapes cover the whole frame on the cut's own frame, so the picture changes behind
// them, then they clear. The cut sits at V.cut (seconds into the block; default its middle). Styles:
// - panels: three slanted panels whip across, two frames apart;
// - iris: a disc closes on the centre, then opens on the new shot;
// - bars: bars slide in from alternate sides and leave the other way;
// - flash: a streak of light crosses the frame and burns white on the cut.
// Variables: style, cut, colors (three, back to front), duration.
window.FlicelloMotion.blocks.transition = function (M) {
  const { V, W, H, ms, anim } = M;
  const wrap = M.el('div', 'fm-transition', M.root);
  const D = (V.duration || 0.6) * 1000;
  const cut = (V.cut ?? (V.duration || 0.6) / 2) * 1000;
  const colors = V.colors && V.colors.length ? V.colors : ['#ffd38a', '#14161c', '#f4efe6'];
  const style = V.style || 'panels';
  const whip = 'cubic-bezier(0.55, 0, 0.45, 1)';
  if (style === 'panels') {
    colors.forEach((c, i) => {
      const p = M.el('div', 'panel', wrap);
      p.style.background = c;
      const lead = (colors.length - 1 - i) * ms(2); // back panels cross first; the front one covers on the cut exactly
      anim(p, [{ transform: 'translateX(-135%) skewX(-14deg)' }, { transform: 'translateX(0) skewX(-14deg)' },
        { transform: 'translateX(135%) skewX(-14deg)' }], { duration: Math.max(1, (cut - lead) * 2), easing: whip });
    });
  } else if (style === 'iris') {
    const disc = M.el('div', 'disc', wrap);
    disc.style.background = colors[1];
    const ring = M.el('div', 'disc', wrap);
    ring.style.background = colors[0];
    const r = 72; // circle() % is of the diagonal / sqrt(2): 70.7% reaches the corners
    anim(ring, [{ clipPath: 'circle(0% at 50% 50%)' }, { clipPath: `circle(${r}% at 50% 50%)`, offset: 0.5 },
      { clipPath: 'circle(0% at 50% 50%)' }], { duration: cut * 2, delay: 0, easing: whip });
    anim(disc, [{ clipPath: 'circle(0% at 50% 50%)' }, { clipPath: `circle(${r}% at 50% 50%)`, offset: 0.5 },
      { clipPath: 'circle(0% at 50% 50%)' }], { duration: Math.max(1, cut * 2 - ms(4)), delay: ms(2), easing: whip });
  } else if (style === 'bars') {
    const n = 6;
    for (let i = 0; i < n; i += 1) {
      const b = M.el('div', 'bar', wrap);
      b.style.top = `${(i / n) * 100}%`;
      b.style.height = `${100 / n + 0.4}%`;
      b.style.background = colors[i % 2 ? 1 : 0];
      const from = i % 2 ? 105 : -105;
      const lag = Math.abs(i - (n - 1) / 2) * ms(1);
      anim(b, [{ transform: `translateX(${from}%)` }, { transform: 'translateX(0)', offset: 0.5 },
        { transform: `translateX(${-from}%)` }], { duration: Math.max(1, (cut - lag) * 2), delay: lag, easing: whip });
    }
  } else { // flash
    const streak = M.el('div', 'streak', wrap);
    anim(streak, [{ transform: 'translateX(-120%) skewX(-20deg)', opacity: 0.2 }, { opacity: 1, offset: 0.5 },
      { transform: 'translateX(120%) skewX(-20deg)', opacity: 0.2 }], { duration: cut * 2, easing: 'linear' });
    const burn = M.el('div', 'burn', wrap);
    anim(burn, [{ opacity: 0 }, { opacity: 0.95, offset: 0.48 }, { opacity: 0.95, offset: 0.52 }, { opacity: 0 }],
      { duration: Math.max(1, Math.min(D, ms(10))), delay: Math.max(0, cut - ms(5)), easing: 'ease-in-out' });
  }
};
