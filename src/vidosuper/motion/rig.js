// rig.js: a motion designer's own rigs for scenes over a person, loaded into every scene (window.rig). Building blocks
// the designer composes into its own design, never a finished effect: physics (a drop with bounces, springs, wobbles,
// particles), follow-through, motion blur, and the person's body to pin things to. Everything is laid onto the scene's
// GSAP timeline at absolute times, so any frame renders on its own when the timeline is seeked.
(function () {
  const FPS = 30;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const round = (v) => Math.round(v * 100) / 100;

  // a seeded random source (mulberry32): the same scene draws the same every time
  function rand(seed = 1) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // ---------------------------------------------------------------- the person (window.PERSON, every 0.1 s)
  function mix(p, q, k) {
    if (Array.isArray(p)) return p.map((v, i) => mix(v, q[i], k));
    return p + (q - p) * k;
  }

  // where they are at scene time t (frame shares), between the samples: {head, shoulders, box, neck, chest}
  function person(t) {
    const P = window.PERSON || [];
    if (!P.length) return null;
    let i = 0;
    while (i + 1 < P.length && P[i + 1].t <= t) i += 1;
    const a = P[i];
    const b = P[Math.min(P.length - 1, i + 1)];
    const k = b.t > a.t ? clamp((t - a.t) / (b.t - a.t), 0, 1) : 0;
    const out = {};
    for (const key of ['head', 'shoulders', 'box']) {
      if (a[key] && b[key]) out[key] = mix(a[key], b[key], k);
      else out[key] = a[key] || b[key] || null;
    }
    if (out.shoulders) {
      const [l, r] = out.shoulders;
      out.neck = [(l[0] + r[0]) / 2, (l[1] + r[1]) / 2];
      out.width = Math.abs(r[0] - l[0]);
    }
    if (out.box) out.chest = [(out.box[0] + out.box[2]) / 2, out.neck ? out.neck[1] + 0.08 : (out.box[1] + out.box[3]) / 2];
    return out;
  }

  // a part of them in px at time t: 'head' (the top of the head), 'neck', 'shoulderL', 'shoulderR', 'chest', 'center'
  function at(part, t, W, H) {
    const p = person(t);
    if (!p) return [W / 2, H / 2];
    const pick = {
      head: p.head, neck: p.neck, chest: p.chest, shoulderL: p.shoulders && p.shoulders[0],
      shoulderR: p.shoulders && p.shoulders[1],
      center: p.box && [(p.box[0] + p.box[2]) / 2, (p.box[1] + p.box[3]) / 2],
    }[part] || p.head;
    return [pick[0] * W, pick[1] * H];
  }

  // keep an element on a part of them through [from, to] (its `left`/`top`, so its own transforms stay free for its
  // own motion): `anchor` is the element's own point that sits there, as shares of its size
  function pin(tl, el, part, { from = 0, to, W, H, dx = 0, dy = 0, anchor = [0.5, 0.5], step = 1 / 15 } = {}) {
    const end = to ?? tl.duration();
    const spot = (t) => {
      const [x, y] = at(part, t, W, H);
      return { left: round(x + dx), top: round(y + dy) };
    };
    // its anchor point sits on the spot whatever its size (an image may still be loading as the timeline is built)
    tl.set(el, { position: 'absolute', xPercent: -anchor[0] * 100, yPercent: -anchor[1] * 100, ...spot(from) }, from);
    const keys = [];
    for (let t = from + step; t < end + 1e-6; t += step) keys.push({ ...spot(Math.min(t, end)), duration: step, ease: 'none' });
    if (keys.length) tl.to(el, { keyframes: keys }, from);
    return tl;
  }

  // ---------------------------------------------------------------- physics
  // a drop with bounces, timed by gravity: from y0 down to the floor y1 (px, down is +), gravity g (px/s²), each
  // bounce keeping `e` of its speed; squashes on every impact in proportion to its speed; drifts x0 -> x1 meanwhile.
  // `shadow`: an element that tightens and darkens as it nears the floor. Returns the impacts [{t, v}] (to hit
  // sounds, dust or a camera bump on them).
  function drop(tl, el, { at: t0 = 0, y0, y1, g = 3200, e = 0.55, bounces = 3, squash = 0.28, x0 = null, x1 = null,
    shadow = null, rest = true } = {}) {
    const impacts = [];
    let t = t0;
    let v = Math.sqrt(2 * g * Math.max(1, y1 - y0));
    const fall = v / g;
    const ys = [{ y: y1, duration: fall, ease: 'power2.in' }];
    t += fall;
    impacts.push({ t: round(t), v });
    for (let k = 0; k < bounces; k += 1) {
      v *= e;
      const up = v / g;
      const peak = (v * v) / (2 * g);
      if (peak < 2) break;
      ys.push({ y: y1 - peak, duration: up, ease: 'power2.out' }, { y: y1, duration: up, ease: 'power2.in' });
      t += 2 * up;
      impacts.push({ t: round(t), v });
    }
    tl.set(el, { y: y0, transformOrigin: '50% 100%' }, t0);
    tl.to(el, { keyframes: ys }, t0);
    if (x0 !== null && x1 !== null) tl.fromTo(el, { x: x0 }, { x: x1, duration: t - t0, ease: 'none' }, t0);
    const v0 = impacts[0].v;
    for (const hit of impacts) { // squash on the floor, stretch back, settle
      const s = squash * (hit.v / v0);
      if (s < 0.02) continue;
      tl.to(el, { scaleY: 1 - s, scaleX: 1 + s * 0.8, duration: 0.05, ease: 'power2.out' }, hit.t - 0.01);
      tl.to(el, { scaleY: 1 + s * 0.35, scaleX: 1 - s * 0.25, duration: 0.09, ease: 'power2.inOut' }, hit.t + 0.04);
      tl.to(el, { scaleY: 1, scaleX: 1, duration: 0.18, ease: 'power3.out' }, hit.t + 0.13);
    }
    if (shadow) { // the shadow follows its height: small and faint up high, tight and dark on the floor
      const h = Math.max(1, y1 - y0);
      tl.set(shadow, { scale: 0.45, opacity: 0.12, transformOrigin: '50% 50%' }, t0);
      const keys = [];
      for (const k of ys) {
        const near = 1 - clamp((y1 - k.y) / h, 0, 1);
        keys.push({ scale: lerp(0.45, 1, near), opacity: lerp(0.12, 0.55, near), duration: k.duration, ease: k.ease });
      }
      tl.to(shadow, { keyframes: keys }, t0);
    }
    if (rest) tl.set(el, { y: y1 }, t);
    return impacts;
  }

  // a spring as an ease: damping ratio z (1 never overshoots, 0.7-0.85 premium, 0.4-0.6 playful, under 0.3 a cartoon)
  // and how many wobbles it has time for; registered once by name, used as `ease: rig.spring('soft', 0.6)`
  const springs = {};
  function spring(name = 'spring', z = 0.7, wobbles = 2) {
    if (springs[name]) return name;
    const zeta = clamp(z, 0.05, 1);
    const wd = Math.PI * 2 * Math.max(0.5, wobbles); // its wobbles, over the tween's length
    // how fast it settles: as a spring of this damping would, and at least enough to come to rest by the end
    const decay = Math.max(6.9, zeta < 0.999 ? (zeta / Math.sqrt(1 - zeta * zeta)) * wd : 6.9);
    const pts = [];
    for (let i = 0; i <= 80; i += 1) {
      const t = i / 80;
      const x = zeta >= 0.999 ? 1 - Math.exp(-decay * t) * (1 + decay * t)
        : 1 - Math.exp(-decay * t) * (Math.cos(wd * t) + (decay / wd) * Math.sin(wd * t));
      pts.push(`${t.toFixed(4)},${x.toFixed(4)}`);
    }
    pts[pts.length - 1] = '1,1';
    window.CustomEase.create(name, `M0,0 L${pts.slice(1).join(' L')}`);
    springs[name] = true;
    return name;
  }

  // secondary motion: a decaying wobble of a property after `at` (a flutter, a sway, a jiggle on landing)
  function wobble(tl, el, { at: t0 = 0, prop = 'rotation', amount = 8, freq = 5, decay = 4, duration = 1.2, base = 0 } = {}) {
    const n = Math.max(2, Math.round(duration * FPS));
    const keys = [];
    for (let i = 1; i <= n; i += 1) {
      const t = (i / n) * duration;
      keys.push({ [prop]: round(base + amount * Math.exp(-decay * t) * Math.sin(Math.PI * 2 * freq * t)), duration: duration / n, ease: 'none' });
    }
    keys.push({ [prop]: base, duration: 0.001 });
    tl.to(el, { keyframes: keys }, t0);
    return tl;
  }

  // follow-through and overlap: parts move one after another (feathers of a wing, layers of a cloud, letters),
  // each `lag` later and a touch further, so the whole thing feels loose and alive
  function cascade(tl, parts, vars, { at: t0 = 0, lag = 0.04, spread = 0.12 } = {}) {
    const list = Array.from(parts);
    list.forEach((el, i) => {
      const k = list.length > 1 ? i / (list.length - 1) : 0;
      const v = { ...vars };
      if (typeof v.rotation === 'number') v.rotation *= 1 + spread * k;
      tl.to(el, v, t0 + i * lag);
    });
    return tl;
  }

  // particles: n bits from (x, y) thrown in direction `dir` (radians, -PI/2 is up) within `spread`, with gravity,
  // drag and a life, each fading and spinning; `make(i, r)` makes and returns the element for bit i (r: its random
  // source), already in the scene. Dust on a landing, sparks, confetti, feathers, droplets.
  function burst(tl, make, { at: t0 = 0, x, y, n = 18, speed = 700, spread = Math.PI * 2, dir = -Math.PI / 2,
    gravity = 1600, drag = 1.6, life = 0.9, seed = 7, spin = 360, scale = [0.6, 1.2] } = {}) {
    const r = rand(seed);
    const steps = Math.max(2, Math.round(life * FPS));
    for (let i = 0; i < n; i += 1) {
      const el = make(i, r);
      const a = dir + (r() - 0.5) * spread;
      let vx = Math.cos(a) * speed * (0.45 + 0.55 * r());
      let vy = Math.sin(a) * speed * (0.45 + 0.55 * r());
      let px = x;
      let py = y;
      const rot = (r() - 0.5) * 2 * spin;
      const sc = lerp(scale[0], scale[1], r());
      const lifeI = life * (0.7 + 0.3 * r());
      const keys = [];
      const dt = lifeI / steps;
      for (let s = 1; s <= steps; s += 1) {
        vx *= Math.exp(-drag * dt);
        vy = vy * Math.exp(-drag * dt) + gravity * dt;
        px += vx * dt;
        py += vy * dt;
        const k = s / steps;
        keys.push({ x: round(px), y: round(py), rotation: round(rot * k), opacity: round(k < 0.6 ? 1 : 1 - (k - 0.6) / 0.4), duration: dt, ease: 'none' });
      }
      tl.set(el, { x, y, opacity: 0, scale: sc, rotation: 0 }, 0);
      tl.set(el, { opacity: 1 }, t0);
      tl.to(el, { keyframes: keys }, t0);
    }
    return tl;
  }

  // motion blur on a fast move: blurred along its direction ('x' or 'y'), peaking in the middle of the move
  let blurs = 0;
  function blur(tl, el, { at: t0 = 0, duration = 0.3, px = 10, dir = 'x' } = {}) {
    const ns = 'http://www.w3.org/2000/svg';
    let defs = document.getElementById('rig-defs');
    if (!defs) {
      const svg = document.createElementNS(ns, 'svg');
      svg.setAttribute('width', '0');
      svg.setAttribute('height', '0');
      svg.style.position = 'absolute';
      defs = document.createElementNS(ns, 'defs');
      defs.id = 'rig-defs';
      svg.append(defs);
      document.body.append(svg);
    }
    blurs += 1;
    const f = document.createElementNS(ns, 'filter');
    f.id = `rig-blur-${blurs}`;
    f.setAttribute('x', '-50%');
    f.setAttribute('y', '-50%');
    f.setAttribute('width', '200%');
    f.setAttribute('height', '200%');
    const g = document.createElementNS(ns, 'feGaussianBlur');
    g.setAttribute('stdDeviation', '0 0');
    f.append(g);
    defs.append(f);
    el.style.filter = `url(#${f.id})`;
    const peak = dir === 'x' ? `${px} 0` : `0 ${px}`;
    tl.to(g, { attr: { stdDeviation: peak }, duration: duration / 2, ease: 'power2.in' }, t0);
    tl.to(g, { attr: { stdDeviation: '0 0' }, duration: duration / 2, ease: 'power2.out' }, t0 + duration / 2);
    return tl;
  }

  // ---------------------------------------------------------------- the camera
  // a shake on an impact, on #stage (their shot and everything on it): seeded jolts that decay back to `base` (its
  // rest x, y, rotation). Scale #stage up first (1 + 2 * amount / width is enough) so the frame's edges never show.
  function shake(tl, el, { at: t0 = 0, amount = 12, duration = 0.35, freq = 16, rotate = 0.4, seed = 5,
    base = { x: 0, y: 0, rotation: 0 } } = {}) {
    const r = rand(seed);
    const phase = [r() * 6.283, r() * 6.283, r() * 6.283];
    const n = Math.max(2, Math.round(duration * FPS));
    const keys = [];
    for (let i = 1; i <= n; i += 1) {
      const t = (i / n) * duration;
      const k = Math.exp((-4 * t) / duration) * (1 - i / (n + 1));
      keys.push({
        x: round((base.x || 0) + amount * k * Math.sin(6.283 * freq * t + phase[0])),
        y: round((base.y || 0) + amount * k * Math.sin(6.283 * freq * 1.31 * t + phase[1])),
        rotation: round((base.rotation || 0) + rotate * k * Math.sin(6.283 * freq * 0.77 * t + phase[2])),
        duration: duration / n, ease: 'none',
      });
    }
    keys.push({ x: base.x || 0, y: base.y || 0, rotation: base.rotation || 0, duration: 0.001 });
    tl.to(el, { keyframes: keys }, t0);
    return tl;
  }

  window.rig = { rand, person, at, pin, drop, spring, wobble, cascade, burst, blur, shake, clamp, lerp, FPS };
})();
