// A callout pinned to something in the shot: it follows that point as the camera moves (V.track, from the scene
// tracker). A ring draws itself round it (or corner brackets close in on it, or an arrow points at it), a leader line
// draws out to a label, and the label pops. On the way out it all undraws. Variables:
// - track [[t, x, y]] (seconds into the block, frame shares), or x, y for a fixed point;
// - text, sub, style (circle / box / arrow), size (the ring's radius, share of the frame's height);
// - side (where the label goes: right / left), accent, duration.
window.FlicelloMotion.blocks.callout = function (M) {
  const { V, W, H, EASE, ms, anim, FONTS } = M;
  const D = (V.duration || 3) * 1000;
  const NS = 'http://www.w3.org/2000/svg';
  const wrap = M.el('div', 'fm-callout', M.root);
  const pin = M.el('div', 'pin', wrap); // the tracked point: everything hangs off it
  const track = (V.track && V.track.length ? V.track : [[0, V.x ?? 0.5, V.y ?? 0.5]]).map(([t, x, y]) => [t * 1000, x * W, y * H]);
  if (track.length === 1) {
    pin.style.transform = `translate(${track[0][1]}px, ${track[0][2]}px)`;
  } else {
    anim(pin, track.map(([t, x, y]) => ({ transform: `translate(${x}px, ${y}px)`, offset: Math.min(1, Math.max(0, t / D)) })),
      { duration: D, easing: 'linear' });
  }
  const accent = V.accent || '#ffd38a';
  const r = (V.size || 0.06) * H;
  // the label goes on the side with room for it all the way along the track (its width estimated from its words)
  const labelW = (Math.max(String(V.text || '').length, String(V.sub || '').length * 0.6) * 0.6 + 1.6) * r * 0.62 + r * 2.3;
  const roomRight = Math.min(...track.map(([, x]) => W * 0.95 - x)) >= labelW;
  const roomLeft = Math.min(...track.map(([, x]) => x - W * 0.05)) >= labelW;
  const right = V.side ? V.side !== 'left' : roomRight || (!roomLeft && track[0][1] < W / 2);
  const style = V.style || 'circle';
  const at = (V.in_at ?? 0.1) * 1000;
  const out = Math.max(at + ms(20), D - ms(12));
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('class', 'mark');
  const S = r * 4;
  svg.setAttribute('width', S);
  svg.setAttribute('height', S);
  svg.setAttribute('viewBox', `${-S / 2} ${-S / 2} ${S} ${S}`);
  svg.style.left = `${-S / 2}px`;
  svg.style.top = `${-S / 2}px`;
  pin.append(svg);
  const stroke = Math.max(3, r * 0.09);
  const draw = (node, len, delay) => {
    node.setAttribute('stroke-dasharray', `${len} ${len}`);
    anim(node, [{ strokeDashoffset: len }, { strokeDashoffset: 0 }], { duration: ms(12), delay, easing: EASE.softLand });
    anim(node, [{ strokeDashoffset: 0 }, { strokeDashoffset: -len }], { duration: ms(9), delay: out, easing: EASE.accelExit, fill: 'forwards' });
  };
  const shape = (tag, attrs) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    n.setAttribute('fill', 'none');
    n.setAttribute('stroke', accent);
    n.setAttribute('stroke-width', stroke);
    n.setAttribute('stroke-linecap', 'round');
    svg.append(n);
    return n;
  };
  if (style === 'box') { // corner brackets that close in on it
    const k = r * 0.45;
    [[-1, -1], [1, -1], [1, 1], [-1, 1]].forEach(([sx, sy], i) => {
      const p = shape('path', { d: `M ${sx * r} ${sy * (r - k)} L ${sx * r} ${sy * r} L ${sx * (r - k)} ${sy * r}` });
      anim(p, [{ transform: `translate(${sx * r * 0.6}px, ${sy * r * 0.6}px)`, opacity: 0 },
        { transform: 'translate(0, 0)', opacity: 1 }], { duration: ms(11), delay: at + i * ms(1), easing: EASE.softLand });
      anim(p, [{ opacity: 1 }, { opacity: 0 }], { duration: ms(8), delay: out, easing: EASE.accelExit, fill: 'forwards' });
    });
  } else if (style === 'arrow') { // an arrow from the label's side onto the point
    const dir = right ? 1 : -1;
    const tail = [dir * r * 1.7, -r * 1.3];
    const line = shape('path', { d: `M ${tail[0]} ${tail[1]} Q ${dir * r * 0.4} ${-r * 1.2} ${dir * r * 0.25} ${-r * 0.25}` });
    draw(line, r * 2.6, at);
    const head = shape('path', { d: `M ${dir * r * 0.55} ${-r * 0.45} L ${dir * r * 0.25} ${-r * 0.25} L ${dir * r * 0.5} ${-r * 0.02}` });
    draw(head, r, at + ms(9));
  } else { // a ring drawn round it, starting at the top and a little past full
    const ring = shape('circle', { cx: 0, cy: 0, r, transform: 'rotate(-90)' });
    draw(ring, 2 * Math.PI * r, at);
    anim(svg, [{ transform: 'rotate(-12deg) scale(1.15)' }, { transform: 'rotate(0) scale(1)' }],
      { duration: ms(14), delay: at, easing: EASE.softLand });
  }
  if (V.text) { // the leader and its label
    const dir = right ? 1 : -1;
    const lead = M.el('div', 'lead', pin);
    const len = r * 1.6;
    lead.style.width = `${len}px`;
    lead.style.height = `${stroke}px`;
    lead.style.background = accent;
    lead.style.left = right ? `${r * 0.75}px` : `${-r * 0.75 - len}px`;
    lead.style.top = `${-r * 0.75}px`;
    lead.style.transformOrigin = right ? '0 50%' : '100% 50%';
    lead.style.rotate = `${dir * -24}deg`;
    anim(lead, [{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], { duration: ms(9), delay: at + ms(8), easing: EASE.softLand });
    anim(lead, [{ opacity: 1 }, { opacity: 0 }], { duration: ms(8), delay: out, easing: EASE.accelExit, fill: 'forwards' });
    const label = M.el('div', 'label', pin);
    label.style.fontSize = `${r * 0.62}px`;
    label.style.fontFamily = FONTS.display;
    label.style.top = `${-r * 0.75 - len * 0.42}px`;
    if (right) label.style.left = `${r * 0.75 + len * 0.92}px`; else label.style.right = `${r * 0.75 + len * 0.92}px`;
    label.style.borderColor = accent;
    const name = M.el('span', 'name', label);
    name.textContent = V.text;
    if (V.sub) {
      const sub = M.el('span', 'sub', label);
      sub.textContent = V.sub;
      sub.style.color = accent;
    }
    anim(label, [{ opacity: 0, transform: `translateY(-50%) translateX(${dir * -0.4}em) scale(0.9)` },
      { opacity: 1, transform: 'translateY(-50%) translateX(0) scale(1)' }], { duration: ms(10), delay: at + ms(14), easing: EASE.softLand });
    anim(label, [{ opacity: 1 }, { opacity: 0 }], { duration: ms(8), delay: out, easing: EASE.accelExit, fill: 'forwards' });
  }
};
