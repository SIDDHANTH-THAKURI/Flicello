// A route drawing itself: a smooth line through the stops, a glowing dot travelling at its head, a pin popping at
// the start and at each stop as the line reaches it, with the place names. The picture under it dims a little so
// the line reads. Variables: points [[x, y]] (frame shares), names (one per point; empty ones get no label), accent,
// draw (seconds the line takes; default most of the block), duration.
window.FlicelloMotion.blocks.route = function (M) {
  const { V, W, H, EASE, ms, anim, FONTS } = M;
  const NS = 'http://www.w3.org/2000/svg';
  const D = (V.duration || 4) * 1000;
  const wrap = M.el('div', 'fm-route', M.root);
  const dim = M.el('div', 'dim', wrap);
  const pts = (V.points && V.points.length >= 2 ? V.points : [[0.25, 0.7], [0.75, 0.3]]).map(([x, y]) => [x * W, y * H]);
  const accent = V.accent || '#ffd38a';
  const at = (V.in_at ?? 0.15) * 1000;
  const drawFor = (V.draw ?? Math.max(1, (V.duration || 4) * 0.55)) * 1000;
  const out = Math.max(at + drawFor + ms(10), D - ms(12));
  anim(dim, [{ opacity: 0 }, { opacity: 1, offset: 0.12 }, { opacity: 1, offset: 0.88 }, { opacity: 0 }], { duration: D });
  // a smooth path through the stops (Catmull-Rom as cubic Beziers), with a gentle arc between two stops
  const P = pts.length === 2 ? [pts[0], [(pts[0][0] + pts[1][0]) / 2 - (pts[1][1] - pts[0][1]) * 0.18,
    (pts[0][1] + pts[1][1]) / 2 + (pts[1][0] - pts[0][0]) * 0.18], pts[1]] : pts;
  let d = `M ${P[0][0]} ${P[0][1]}`;
  for (let i = 0; i < P.length - 1; i += 1) {
    const p0 = P[Math.max(0, i - 1)]; const p1 = P[i]; const p2 = P[i + 1]; const p3 = P[Math.min(P.length - 1, i + 2)];
    d += ` C ${p1[0] + (p2[0] - p0[0]) / 6} ${p1[1] + (p2[1] - p0[1]) / 6}, ${p2[0] - (p3[0] - p1[0]) / 6} ${p2[1] - (p3[1] - p1[1]) / 6}, ${p2[0]} ${p2[1]}`;
  }
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('class', 'map');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', W);
  svg.setAttribute('height', H);
  wrap.append(svg);
  const stroke = Math.max(4, H * 0.006);
  const mk = (attrs) => {
    const n = document.createElementNS(NS, 'path');
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    svg.append(n);
    return n;
  };
  const shadow = mk({ d, fill: 'none', stroke: 'rgba(0,0,0,.35)', 'stroke-width': stroke * 2.4, 'stroke-linecap': 'round' });
  const line = mk({ d, fill: 'none', stroke: accent, 'stroke-width': stroke, 'stroke-linecap': 'round' });
  const len = line.getTotalLength();
  for (const n of [shadow, line]) {
    n.setAttribute('stroke-dasharray', `${len} ${len}`);
    anim(n, [{ strokeDashoffset: len }, { strokeDashoffset: 0 }], { duration: drawFor, delay: at, easing: EASE.glide });
    anim(n, [{ opacity: 1 }, { opacity: 0 }], { duration: ms(10), delay: out, easing: EASE.accelExit, fill: 'forwards' });
  }
  // the travelling dot, sampled along the path with the line's own easing
  const dot = M.el('div', 'dot', wrap);
  dot.style.background = accent;
  dot.style.width = dot.style.height = `${stroke * 3.2}px`;
  const ease = (u) => (u < 0.5 ? 2 * u * u : 1 - (-2 * u + 2) ** 2 / 2); // close to EASE.glide
  const frames = [];
  for (let k = 0; k <= 40; k += 1) {
    const q = line.getPointAtLength(len * ease(k / 40));
    frames.push({ transform: `translate(${q.x}px, ${q.y}px) translate(-50%, -50%)`, offset: k / 40 });
  }
  anim(dot, frames, { duration: drawFor, delay: at, easing: 'linear' });
  anim(dot, [{ opacity: 0 }, { opacity: 1 }], { duration: ms(4), delay: at, fill: 'both' });
  anim(dot, [{ opacity: 1 }, { opacity: 0 }], { duration: ms(8), delay: out, easing: EASE.accelExit, fill: 'forwards' });
  // pins and names: each one as the line reaches it
  const names = V.names || [];
  pts.forEach(([x, y], i) => {
    const reach = i === 0 ? 0 : (() => { // when the line's head passes this stop
      let best = 1; let bestD = Infinity;
      for (let k = 0; k <= 60; k += 1) {
        const q = line.getPointAtLength(len * k / 60);
        const dd = Math.hypot(q.x - x, q.y - y);
        if (dd < bestD) { bestD = dd; best = k / 60; }
      }
      let u = best; // invert the easing: the time at which ease(t) = best
      let lo = 0; let hi = 1;
      for (let s = 0; s < 30; s += 1) { const mid = (lo + hi) / 2; if (ease(mid) < u) lo = mid; else hi = mid; }
      return (lo + hi) / 2;
    })();
    const when = at + reach * drawFor;
    const pin = M.el('div', 'pin', wrap);
    pin.style.left = `${x}px`;
    pin.style.top = `${y}px`;
    pin.style.setProperty('--c', accent);
    pin.style.width = pin.style.height = `${stroke * 4.2}px`;
    anim(pin, [{ transform: 'translate(-50%, -50%) scale(0)' }, { transform: 'translate(-50%, -50%) scale(1.25)', offset: 0.6 },
      { transform: 'translate(-50%, -50%) scale(1)' }], { duration: ms(9), delay: when, easing: EASE.softLand });
    anim(pin, [{ opacity: 1 }, { opacity: 0 }], { duration: ms(8), delay: out, easing: EASE.accelExit, fill: 'forwards' });
    if (!names[i]) return;
    const tag = M.el('div', 'name', wrap);
    tag.textContent = names[i];
    tag.style.fontFamily = FONTS.display;
    tag.style.fontSize = `${H * 0.03}px`;
    tag.style.left = `${x}px`;
    tag.style.top = `${y - stroke * 3.6}px`;
    anim(tag, [{ opacity: 0, transform: 'translate(-50%, -60%)' }, { opacity: 1, transform: 'translate(-50%, -100%)' }],
      { duration: ms(10), delay: when + ms(3), easing: EASE.softLand });
    anim(tag, [{ opacity: 1 }, { opacity: 0 }], { duration: ms(8), delay: out, easing: EASE.accelExit, fill: 'forwards' });
  });
};
