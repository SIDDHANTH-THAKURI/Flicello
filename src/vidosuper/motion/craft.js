// craft.js: finished parts for flat motion design, made to the owner's bar (examples/flat_space.html): two-tone
// shading with a crisp rim and one highlight, glow drawn as stepped flat rings, depth in layers, springy births.
// Loaded into every 2D world scene as window.craft. Each part is drawn into an SVG group you give it, seeded (the same
// scene draws the same every time), and its motion is laid onto the scene's GSAP timeline at absolute times, so any
// frame renders on its own when the timeline is seeked. Compose them, recolour them, scale them: they are parts, not
// a finished design.
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  const TAU = Math.PI * 2;

  function rand(seed = 1) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function el(tag, attrs = {}, parent = null) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) n.setAttribute(k, String(v));
    if (parent) parent.appendChild(n);
    return n;
  }
  let uid = 0;
  const id = (p) => `craft-${p}-${(uid += 1)}`;
  function defs(parent) {
    const svg = parent.ownerSVGElement || parent;
    let d = svg.querySelector(':scope > defs');
    if (!d) d = el('defs', {}, null), svg.insertBefore(d, svg.firstChild);
    return d;
  }

  // ---------------------------------------------------------------- colour: one base, its shades
  function hsl(hex) {
    const n = parseInt(hex.replace('#', ''), 16);
    let r = (n >> 16) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
    let h = 0, s = 0;
    const l = (mx + mn) / 2;
    if (mx !== mn) {
      const d = mx - mn;
      s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
      h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
      h /= 6;
    }
    return [h * 360, s * 100, l * 100];
  }
  function hex(h, s, l) {
    s = Math.max(0, Math.min(100, s)) / 100; l = Math.max(0, Math.min(100, l)) / 100; h = ((h % 360) + 360) % 360;
    const k = (n) => (n + h / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
    return `#${[f(0), f(8), f(4)].map((x) => Math.round(x * 255).toString(16).padStart(2, '0')).join('')}`;
  }
  // the body, the shade away from the light (cooler, deeper), the lit face, the rim, the glow
  function tones(base) {
    const [h, s, l] = hsl(base);
    const down = (d, f) => Math.max(l - d, l * f); // dark colours keep their colour instead of going black
    return { body: base, shade: hex(h + 8, s + 6, down(15, 0.72)), deep: hex(h + 14, s + 8, down(26, 0.55)), light: hex(h - 6, s - 4, l + 12),
      rim: hex(h - 4, s - 10, l + 24), glow: hex(h, s + 10, l + 8), dark: hex(h + 10, s - 10, down(40, 0.4)) };
  }

  // ---------------------------------------------------------------- glow: stepped flat rings, breathing
  function glow(parent, { x = 0, y = 0, r = 100, color = '#4fc3ff', rings = 4, spread = 1, opacity = 1 } = {}) {
    const g = el('g', { transform: `translate(${x} ${y})`, opacity }, parent);
    const out = [];
    for (let i = rings - 1; i >= 0; i -= 1) {
      out.push(el('circle', { r: r * (1.17 + i * 0.32 * spread), fill: color, opacity: [0.12, 0.08, 0.05, 0.035, 0.02][i] || 0.02 }, g));
    }
    return {
      g, rings: out,
      breathe(tl, t0, t1, amount = 0.06) {
        out.forEach((c, i) => {
          const per = 0.9 + i * 0.07;
          const n = Math.max(1, Math.floor((t1 - t0) / per) - 1);
          tl.fromTo(c, { scale: 1, transformOrigin: '50% 50%' }, { scale: 1 + amount, duration: per, yoyo: true, repeat: n, ease: 'sine.inOut' }, t0 + i * 0.12);
        });
      },
    };
  }

  // ---------------------------------------------------------------- a sphere / disc: the subject most films need
  // shading from the light (`light`: angle in degrees the light comes FROM, default top-left), rim, one highlight,
  // optional bright core and dashed orbit ring; born from a point of light with a burst, springing open
  function sphere(parent, { x = 0, y = 0, r = 120, color = '#3fa9f5', light = 225, core = false, ring = false, halo = true } = {}) {
    const t = tones(color);
    const g = el('g', { transform: `translate(${x} ${y})` }, parent);
    const haloG = halo ? glow(g, { r, color: t.glow }) : null;
    const body = el('g', {}, g);
    const clip = id('clip');
    el('circle', { r }, el('clipPath', { id: clip }, defs(parent)));
    el('circle', { r, fill: t.body }, body);
    const inner = el('g', { 'clip-path': `url(#${clip})` }, body);
    const lx = Math.cos((light * Math.PI) / 180), ly = Math.sin((light * Math.PI) / 180);
    el('circle', { cx: -lx * r * 0.32, cy: -ly * r * 0.35, r, fill: t.shade }, inner); // the side away from the light
    el('circle', { cx: -lx * r * 0.52, cy: -ly * r * 0.55, r, fill: t.deep, opacity: 0.55 }, inner);
    el('circle', { cx: lx * r * 0.22, cy: ly * r * 0.24, r: r * 0.75, fill: t.light }, inner); // the lit face
    let coreN = null;
    if (core) {
      coreN = el('circle', { r: r * 0.42, fill: t.rim, opacity: 0.85 }, inner);
      el('circle', { r: r * 0.25, fill: '#ffffff', opacity: 0.88 }, inner);
    }
    el('circle', { r, fill: 'none', stroke: t.rim, 'stroke-width': Math.max(2, r * 0.045) }, body);
    el('ellipse', { cx: lx * r * 0.42, cy: ly * r * 0.55, rx: r * 0.22, ry: r * 0.11, fill: '#ffffff', opacity: 0.82,
      transform: `rotate(${light + 90} ${lx * r * 0.42} ${ly * r * 0.55})` }, body);
    const ringN = ring ? el('circle', { r: r * 1.24, fill: 'none', stroke: t.rim, 'stroke-width': Math.max(2, r * 0.02),
      'stroke-dasharray': `${r * 0.12} ${r * 0.15}`, opacity: 0.55 }, g) : null;
    const birth = el('g', {}, g);
    return {
      g, body, halo: haloG, core: coreN, ring: ringN, tones: t,
      // born on a word: a point of light, little sparks, then it pops open with a spring
      born(tl, at) {
        const seed = el('circle', { r: Math.max(6, r * 0.07), fill: '#ffffff' }, birth);
        const sglow = el('circle', { r: Math.max(18, r * 0.2), fill: t.rim, opacity: 0.35 }, birth);
        tl.fromTo([seed, sglow], { scale: 0, transformOrigin: '50% 50%' }, { scale: 1, duration: 0.22, ease: 'back.out(3)' }, at - 0.4);
        tl.to([seed, sglow], { scale: 0, duration: 0.12, ease: 'power2.in' }, at);
        for (let i = 0; i < 6; i += 1) {
          const a = (i / 6) * TAU + 0.3;
          const ln = el('line', { x1: 0, y1: 0, x2: 0, y2: 0, opacity: 0, stroke: '#ffffff', 'stroke-width': Math.max(3, r * 0.035), 'stroke-linecap': 'round' }, birth);
          const r0 = r * 0.3, r1 = r * (0.85 + 0.3 * (i % 2));
          tl.fromTo(ln, { attr: { x1: Math.cos(a) * r0, y1: Math.sin(a) * r0, x2: Math.cos(a) * r0, y2: Math.sin(a) * r0 }, opacity: 1 },
            { attr: { x1: Math.cos(a) * r1 * 0.7, y1: Math.sin(a) * r1 * 0.7, x2: Math.cos(a) * r1, y2: Math.sin(a) * r1 }, opacity: 0, duration: 0.45, ease: 'power3.out' }, at - 0.02);
        }
        tl.fromTo(body, { scale: 0, transformOrigin: '50% 50%' }, { scale: 1, duration: 0.62, ease: 'elastic.out(1, 0.55)' }, at - 0.04);
        if (haloG) tl.fromTo(haloG.g, { scale: 0, opacity: 0, transformOrigin: '50% 50%' }, { scale: 1, opacity: 1, duration: 0.8, ease: 'power3.out' }, at + 0.02);
        if (ringN) tl.fromTo(ringN, { scale: 0, opacity: 0, transformOrigin: '50% 50%' }, { scale: 1, opacity: 0.55, duration: 0.7, ease: 'back.out(1.6)' }, at + 0.1);
      },
      alive(tl, t0, t1) { // the glow breathes, the core pulses, the ring turns
        if (haloG) haloG.breathe(tl, t0, t1);
        if (coreN) tl.fromTo(coreN, { scale: 0.86, transformOrigin: '50% 50%' }, { scale: 1.12, duration: 0.7, yoyo: true, repeat: Math.max(1, Math.floor((t1 - t0) / 0.7) - 1), ease: 'sine.inOut' }, t0);
        if (ringN) tl.fromTo(ringN, { rotation: 0, transformOrigin: '50% 50%' }, { rotation: 40 * (t1 - t0), duration: t1 - t0, ease: 'none' }, t0);
      },
    };
  }

  // ---------------------------------------------------------------- a tree, grown from the ground on a spring
  // kinds: round (broadleaf: overlapping canopy lobes), pine (stacked tiers), oak (wide, uneven). x, y: the foot of
  // the trunk on the ground; h: its height
  function tree(parent, { x = 0, y = 0, h = 300, kind = 'round', color = '#3f9a5a', trunk = '#6b4a34', light = 225, seed = 3 } = {}) {
    const R = rand(seed);
    const t = tones(color), tk = tones(trunk);
    const lx = Math.cos((light * Math.PI) / 180);
    const g = el('g', { transform: `translate(${x} ${y})` }, parent);
    const grow = el('g', {}, g); // scaled from the foot when it grows
    const tw = h * (kind === 'pine' ? 0.06 : 0.075);
    const th = h * (kind === 'pine' ? 0.3 : 0.42);
    // the trunk: two tones, a lit edge, a branch or two
    el('path', { d: `M ${-tw} 0 C ${-tw * 0.8} ${-th * 0.5} ${-tw * 0.5} ${-th * 0.8} ${-tw * 0.4} ${-th} L ${tw * 0.4} ${-th} C ${tw * 0.5} ${-th * 0.8} ${tw * 0.8} ${-th * 0.5} ${tw} 0 Z`, fill: tk.body }, grow);
    el('path', { d: `M ${lx > 0 ? -tw : 0} 0 C ${-tw * 0.4} ${-th * 0.5} ${-tw * 0.2} ${-th * 0.8} ${-tw * 0.1} ${-th} L ${tw * 0.4} ${-th} C ${tw * 0.5} ${-th * 0.8} ${tw * 0.8} ${-th * 0.5} ${tw} 0 Z`, fill: tk.shade, opacity: 0.9 }, grow);
    if (kind !== 'pine') {
      for (const side of [-1, 1]) {
        const by = -th * (0.55 + R() * 0.2);
        el('path', { d: `M 0 ${by} Q ${side * h * 0.08} ${by - h * 0.05} ${side * h * 0.14} ${by - h * 0.12}`, stroke: tk.body, 'stroke-width': tw * 0.55, fill: 'none', 'stroke-linecap': 'round' }, grow);
      }
    }
    const crown = el('g', {}, grow);
    if (kind === 'pine') {
      const tiers = 4;
      for (let i = 0; i < tiers; i += 1) {
        const w = h * (0.34 - i * 0.065), top = -h + i * h * 0.17, base = top + h * 0.3;
        const tier = el('g', {}, crown);
        el('path', { d: `M 0 ${top} L ${w} ${base} Q 0 ${base + h * 0.04} ${-w} ${base} Z`, fill: t.body }, tier);
        el('path', { d: `M 0 ${top} L ${w * (lx > 0 ? -1 : 1)} ${base} Q ${w * 0.2 * (lx > 0 ? -1 : 1)} ${base + h * 0.03} 0 ${base + h * 0.02} Z`, fill: t.shade }, tier);
        el('path', { d: `M 0 ${top} L ${w * 0.55 * (lx > 0 ? 1 : -1)} ${top + (base - top) * 0.55}`, stroke: t.rim, 'stroke-width': Math.max(2, h * 0.008), 'stroke-linecap': 'round', opacity: 0.8 }, tier);
      }
    } else {
      const cx = 0, cy = -th - h * (kind === 'oak' ? 0.2 : 0.26);
      const rw = h * (kind === 'oak' ? 0.42 : 0.31), rh = h * (kind === 'oak' ? 0.27 : 0.3);
      const sx = lx > 0 ? -1 : 1; // the side away from the light
      const lobes = [];
      const n = kind === 'oak' ? 9 : 7;
      for (let i = 0; i < n; i += 1) {
        const a = (i / n) * TAU + R() * 0.4;
        lobes.push([cx + Math.cos(a) * rw * (0.62 + R() * 0.18), cy + Math.sin(a) * rh * (0.58 + R() * 0.2), rw * (0.42 + R() * 0.16)]);
      }
      lobes.push([cx, cy, rw * 0.62]);
      const clip = id('crown');
      const cp = el('clipPath', { id: clip }, defs(parent));
      lobes.forEach(([lx2, ly2, r]) => el('circle', { cx: lx2, cy: ly2, r }, cp));
      lobes.forEach(([lx2, ly2, r]) => el('circle', { cx: lx2, cy: ly2, r: r + Math.max(2, h * 0.008), fill: t.rim }, crown)); // the rim
      const inner = el('g', { 'clip-path': `url(#${clip})` }, crown);
      el('rect', { x: cx - rw * 2, y: cy - rh * 2, width: rw * 4, height: rh * 4, fill: t.body }, inner);
      el('ellipse', { cx: cx + sx * rw * 0.4, cy: cy + rh * 0.5, rx: rw * 1.1, ry: rh * 0.8, fill: t.shade }, inner);
      el('ellipse', { cx: cx + sx * rw * 0.3, cy: cy + rh * 0.75, rx: rw * 0.9, ry: rh * 0.45, fill: t.deep, opacity: 0.55 }, inner);
      lobes.filter(([lx2, ly2]) => ly2 < cy + rh * 0.1 && (lx2 - cx) * sx < rw * 0.25) // lit lobes: the upper side facing the light
        .forEach(([lx2, ly2, r]) => el('circle', { cx: lx2 - sx * r * 0.22, cy: ly2 - r * 0.28, r: r * 0.6, fill: t.light, opacity: 0.9 }, inner));
      for (let i = 0; i < 5; i += 1) { // a few leaf flecks: detail that rewards a second look
        el('ellipse', { cx: cx + (R() - 0.5) * rw * 1.4, cy: cy + (R() - 0.6) * rh * 1.2, rx: h * 0.018, ry: h * 0.01, fill: t.rim, opacity: 0.55, transform: `rotate(${R() * 180})` }, inner);
      }
      el('ellipse', { cx: cx - sx * rw * 0.35, cy: cy - rh * 0.55, rx: rw * 0.16, ry: rh * 0.08, fill: '#ffffff', opacity: 0.4, transform: `rotate(${-25 * sx} ${cx - sx * rw * 0.35} ${cy - rh * 0.55})` }, crown);
    }
    return {
      g,
      // grown on a word: the trunk rises, the crown pops out with overshoot, a squash and settle
      grow(tl, at, over = 0.9) {
        tl.fromTo(grow, { scaleY: 0, scaleX: 0.6, transformOrigin: '50% 100%' }, { scaleY: 1.08, scaleX: 0.94, duration: over * 0.6, ease: 'power3.out' }, at);
        tl.to(grow, { scaleY: 1, scaleX: 1, duration: over * 0.5, ease: 'elastic.out(1, 0.5)' }, at + over * 0.6);
        tl.fromTo(crown, { scale: 0.2, transformOrigin: '50% 100%' }, { scale: 1, duration: over, ease: 'back.out(2.2)' }, at + over * 0.18);
      },
      sway(tl, t0, t1, amount = 1.6) { // the wind: the crown leans and comes back
        const per = 1.6 + R() * 0.8;
        tl.fromTo(crown, { rotation: -amount / 2, transformOrigin: '50% 100%' }, { rotation: amount / 2, duration: per, yoyo: true, repeat: Math.max(1, Math.floor((t1 - t0) / per) - 1), ease: 'sine.inOut' }, t0);
      },
    };
  }

  // ---------------------------------------------------------------- a sky that changes over the film
  // stops: [[time, top colour, bottom colour], ...]: the gradient tweens between them on the timeline
  function sky(parent, tl, { w = 1920, h = 1080, stops = [[0, '#0b1030', '#27306b']] } = {}) {
    const gid = id('sky');
    const lg = el('linearGradient', { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 }, defs(parent));
    const top = el('stop', { offset: 0, 'stop-color': stops[0][1] }, lg);
    const bottom = el('stop', { offset: 1, 'stop-color': stops[0][2] }, lg);
    const rect = el('rect', { x: 0, y: 0, width: w, height: h, fill: `url(#${gid})` }, parent);
    for (let i = 1; i < stops.length; i += 1) {
      const [t0] = stops[i - 1];
      const [t1, c1, c2] = stops[i];
      tl.to(top, { attr: { 'stop-color': c1 }, duration: Math.max(0.1, t1 - t0), ease: 'sine.inOut' }, t0);
      tl.to(bottom, { attr: { 'stop-color': c2 }, duration: Math.max(0.1, t1 - t0), ease: 'sine.inOut' }, t0);
    }
    return { rect, top, bottom };
  }

  // stars: two depths (far small and dim, near brighter), twinkling
  function stars(parent, { x = 0, y = 0, w = 1920, h = 600, count = 90, color = '#cfe0ff', seed = 7 } = {}) {
    const R = rand(seed);
    const g = el('g', {}, parent);
    const list = [];
    for (let i = 0; i < count; i += 1) {
      const near = R() < 0.18;
      list.push(el('circle', { cx: x + R() * w, cy: y + R() * h, r: near ? 1.6 + R() * 1.4 : 0.6 + R() * 0.9, fill: color, opacity: near ? 0.75 : 0.25 + R() * 0.35 }, g));
    }
    return {
      g,
      twinkle(tl, t0, t1) {
        list.forEach((s, i) => { if (i % 4 === 0) tl.to(s, { opacity: 0.1, duration: 0.6 + (i % 7) * 0.13, yoyo: true, repeat: Math.max(1, Math.floor((t1 - t0) / 1.4)), ease: 'sine.inOut' }, t0 + (i % 11) * 0.21); });
      },
    };
  }

  // a flat cloud: overlapping lobes on a flat base, a lighter top, a shade underneath
  function cloud(parent, { x = 0, y = 0, w = 320, color = '#ffffff', seed = 5, opacity = 1 } = {}) {
    const R = rand(seed);
    const t = tones(color);
    const g = el('g', { transform: `translate(${x} ${y})`, opacity }, parent);
    const body = el('g', {}, g);
    const h = w * 0.36;
    const parts = [[-w * 0.3, 0, h * 0.5], [-w * 0.08, -h * 0.25, h * 0.62], [w * 0.18, -h * 0.12, h * 0.55], [w * 0.36, h * 0.05, h * 0.38]];
    parts.forEach(([cx, cy, r]) => el('circle', { cx, cy, r: r * (0.92 + R() * 0.16), fill: t.shade }, body));
    el('rect', { x: -w * 0.45, y: 0, width: w * 0.9, height: h * 0.42, rx: h * 0.21, fill: t.shade }, body);
    parts.forEach(([cx, cy, r]) => el('circle', { cx: cx - r * 0.08, cy: cy - r * 0.14, r: r * 0.86, fill: color }, body));
    el('rect', { x: -w * 0.43, y: -h * 0.06, width: w * 0.86, height: h * 0.36, rx: h * 0.18, fill: color }, body);
    return {
      g,
      drift(tl, t0, t1, dx = 60) { tl.fromTo(body, { x: 0 }, { x: dx, duration: t1 - t0, ease: 'none' }, t0); },
    };
  }

  // ---------------------------------------------------------------- ground: a cross-section or a horizon
  function ground(parent, { y = 760, w = 1920, h = 320, color = '#5a3a26', grass = '#5fae4f', strata = 4, seed = 9 } = {}) {
    const R = rand(seed);
    const t = tones(color);
    const g = el('g', {}, parent);
    const line = (yy, amp) => {
      let d = `M 0 ${yy}`;
      for (let x = 0; x <= w; x += w / 12) d += ` Q ${x + w / 24} ${yy + (R() - 0.5) * amp} ${x + w / 12} ${yy + (R() - 0.5) * amp * 0.4}`;
      return d;
    };
    el('path', { d: `${line(y, 14)} L ${w} ${y + h} L 0 ${y + h} Z`, fill: t.body }, g);
    for (let i = 1; i <= strata; i += 1) {
      const yy = y + (h * i) / (strata + 1);
      el('path', { d: `${line(yy, 20)} L ${w} ${y + h} L 0 ${y + h} Z`, fill: [t.shade, t.body, t.deep, t.shade][(i - 1) % 4], opacity: 0.85 }, g);
    }
    for (let i = 0; i < 46; i += 1) { // pebbles, lit on top
      const px = R() * w, py = y + 30 + R() * (h - 40), pr = 3 + R() * 9;
      el('ellipse', { cx: px, cy: py, rx: pr, ry: pr * 0.7, fill: t.dark, opacity: 0.6 }, g);
      el('ellipse', { cx: px - pr * 0.2, cy: py - pr * 0.25, rx: pr * 0.6, ry: pr * 0.3, fill: t.light, opacity: 0.45 }, g);
    }
    if (grass) {
      const gt = tones(grass);
      el('path', { d: `${line(y, 14)} L ${w} ${y + 12} L 0 ${y + 12} Z`, fill: gt.shade }, g);
      for (let x = 4; x < w; x += 7 + R() * 7) {
        const bh = 8 + R() * 16;
        el('path', { d: `M ${x} ${y + 4} q ${R() * 4 - 2} ${-bh / 2} ${R() * 6 - 3} ${-bh}`, stroke: R() < 0.5 ? gt.body : gt.light, 'stroke-width': 2.2, fill: 'none', 'stroke-linecap': 'round' }, g);
      }
    }
    return { g };
  }

  // water: layered bands that roll, a lighter crest line, glints
  function water(parent, { y = 760, w = 1920, h = 320, color = '#2f7fd6', seed = 13 } = {}) {
    const R = rand(seed);
    const t = tones(color);
    const g = el('g', {}, parent);
    el('rect', { x: 0, y, width: w, height: h, fill: t.body }, g);
    const bands = [];
    for (let i = 0; i < 4; i += 1) {
      const yy = y + 18 + i * (h / 5);
      const amp = 8 + i * 4, len = 160 + i * 40;
      let d = `M ${-len} ${yy}`;
      for (let x = -len; x <= w + len * 2; x += len) d += ` q ${len / 4} ${-amp} ${len / 2} 0 t ${len / 2} 0`;
      bands.push(el('path', { d: `${d} L ${w + len * 2} ${y + h} L ${-len} ${y + h} Z`, fill: i % 2 ? t.shade : t.deep, opacity: 0.5 + i * 0.1 }, g));
    }
    let crest = `M 0 ${y}`;
    for (let x = 0; x <= w; x += 120) crest += ` q 30 -6 60 0 t 60 0`;
    el('path', { d: crest, stroke: t.rim, 'stroke-width': 3, fill: 'none', opacity: 0.7 }, g);
    const glints = [];
    for (let i = 0; i < 14; i += 1) glints.push(el('rect', { x: R() * w, y: y + 12 + R() * h * 0.7, width: 18 + R() * 30, height: 3, rx: 1.5, fill: '#ffffff', opacity: 0 }, g));
    return {
      g,
      flow(tl, t0, t1) {
        bands.forEach((b, i) => tl.fromTo(b, { x: 0 }, { x: (i % 2 ? -1 : 1) * (40 + i * 25), duration: t1 - t0, ease: 'none' }, t0));
        glints.forEach((s, i) => tl.to(s, { opacity: 0.7, duration: 0.5, yoyo: true, repeat: Math.max(1, Math.floor((t1 - t0) / 1.6)), ease: 'sine.inOut' }, t0 + (i % 9) * 0.37));
      },
    };
  }

  // ---------------------------------------------------------------- weather and air
  // rain: streaks falling at an angle, each on its own loop, a splash where it lands
  function rain(parent, { x = 0, y = 0, w = 1920, h = 760, count = 60, color = '#9fd2ff', angle = 12, seed = 17 } = {}) {
    const R = rand(seed);
    const g = el('g', {}, parent);
    const drops = [];
    for (let i = 0; i < count; i += 1) {
      const len = 18 + R() * 26;
      const d = el('line', { x1: 0, y1: 0, x2: Math.sin((angle * Math.PI) / 180) * len, y2: len, stroke: color, 'stroke-width': 2 + R() * 1.5, 'stroke-linecap': 'round', opacity: 0 }, g);
      drops.push([d, x + R() * w, R()]);
    }
    return {
      g,
      fall(tl, t0, t1) {
        drops.forEach(([d, dx, ph]) => {
          const per = 0.55 + ph * 0.4;
          const slide = Math.tan((angle * Math.PI) / 180) * h;
          for (let k = t0 + ph * per; k < t1; k += per) {
            tl.fromTo(d, { x: dx - slide, y: y - 40, opacity: 0.85 }, { x: dx, y: y + h, duration: per, ease: 'none' }, k);
          }
          tl.set(d, { opacity: 0 }, t1);
        });
      },
    };
  }

  // dust in depth: far motes small and slow, near ones large and blurred-looking (a softer, bigger ring)
  function dust(parent, { w = 1920, h = 1080, count = 40, color = '#ffe2b8', seed = 21 } = {}) {
    const R = rand(seed);
    const g = el('g', {}, parent);
    const motes = [];
    for (let i = 0; i < count; i += 1) {
      const near = R() < 0.25;
      const m = el('g', { transform: `translate(${R() * w} ${R() * h})`, opacity: near ? 0.5 : 0.3 + R() * 0.3 }, g);
      el('circle', { r: near ? 5 + R() * 4 : 1.5 + R() * 1.5, fill: color }, m);
      if (near) el('circle', { r: 10 + R() * 6, fill: color, opacity: 0.18 }, m);
      motes.push([m, near]);
    }
    return {
      g,
      drift(tl, t0, t1) {
        motes.forEach(([m, near], i) => tl.to(m, { x: `+=${(near ? 70 : 22) * ((i % 2) * 2 - 1)}`, y: `-=${near ? 40 : 14}`, duration: t1 - t0, ease: 'none' }, t0));
      },
    };
  }

  // film grain over everything (an SVG noise layer whose seed changes every frame from the timeline)
  function grain(container, tl, t1, { opacity = 0.07 } = {}) {
    const svg = el('svg', { viewBox: '0 0 2020 1180', width: 2020, height: 1180 });
    Object.assign(svg.style, { position: 'absolute', inset: '-50px', pointerEvents: 'none', opacity: String(opacity), mixBlendMode: 'overlay' });
    const fid = id('grain');
    const f = el('filter', { id: fid }, svg);
    const turb = el('feTurbulence', { type: 'fractalNoise', baseFrequency: 0.9, numOctaves: 2, seed: 1 }, f);
    el('rect', { width: '100%', height: '100%', filter: `url(#${fid})` }, svg);
    container.appendChild(svg);
    const st = { k: 0 };
    tl.to(st, { k: Math.round(t1 * 30), duration: t1, ease: 'none', onUpdate: () => {
      const k = Math.floor(st.k);
      turb.setAttribute('seed', String(1 + (k % 50)));
      svg.style.transform = `translate(${-((k * 37) % 50)}px, ${-((k * 53) % 50)}px)`;
    } }, 0);
    return svg;
  }

  // a flat motion trail behind something moving along an arc (centre cx,cy, radius r, from a0 to a1 degrees)
  function trail(parent, { cx = 0, cy = 0, r = 200, a0 = -40, a1 = 0, color = '#ffb066', width = 10 } = {}) {
    const p0 = [cx + Math.cos((a0 * Math.PI) / 180) * r, cy + Math.sin((a0 * Math.PI) / 180) * r];
    const p1 = [cx + Math.cos((a1 * Math.PI) / 180) * r, cy + Math.sin((a1 * Math.PI) / 180) * r];
    return el('path', { d: `M ${p0[0]} ${p0[1]} A ${r} ${r} 0 0 1 ${p1[0]} ${p1[1]}`, fill: 'none', stroke: color, 'stroke-width': width, 'stroke-linecap': 'round', opacity: 0.35 }, parent);
  }

  window.craft = { el, rand, tones, glow, sphere, tree, sky, stars, cloud, ground, water, rain, dust, grain, trail };
}());
