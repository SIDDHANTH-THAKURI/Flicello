// toon.js: the drawn engine's kit (motion/drawn.py). A shot is drawn on a canvas as a pure function of time, then
// rendered frame by frame by HyperFrames. Everything here is deterministic: the same t always draws the same frame.
//
// A shot file calls toon.shot({ draw(c, w, h, t) {...}, blur: 3 }). A cast file calls toon.cast("<id>", draw)
// where draw(c, o) paints that character at o.x, o.y (its feet), o.s (its height in px), facing o.dir (1 right,
// -1 left), at time o.t, doing o.action ("stand", "walk", "run", "jump", "sit", "point", "wave", "crouch" or one
// of its own) at o.phase (0..1 through the action), with o.pose (a two-bone pose for that action, see toon.pose),
// o.face ("neutral", "happy", "surprised", "sad", "angry", "talk") and o.ink (one colour for everything: impact
// frames, silhouettes, shadows). A shot draws a cast member with toon.draw("<id>", c, {...}).
(() => {
  const TAU = Math.PI * 2;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const hash = (n) => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
  function rng(seed) { let s = (seed * 2654435761) >>> 0 || 1; return () => { s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; }; }
  const rgba = (hex, a) => { const n = parseInt(hex.slice(1, 7), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; };
  function mix(h1, h2, u) {
    const a = parseInt(h1.slice(1, 7), 16), b = parseInt(h2.slice(1, 7), 16), ch = (s) => Math.round(lerp((a >> s) & 255, (b >> s) & 255, u));
    return `#${((ch(16) << 16) | (ch(8) << 8) | ch(0)).toString(16).padStart(6, "0")}`;
  }

  // ---------------------------------------------------------------- time: eases, keys, twos
  const ease = {
    linear: (u) => u, in: (u) => u * u * u, out: (u) => 1 - Math.pow(1 - u, 3), inOut: (u) => (u < .5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2),
    smooth: (u) => u * u * (3 - 2 * u), back: (u) => { const k = 1.70158; return 1 + (k + 1) * Math.pow(u - 1, 3) + k * Math.pow(u - 1, 2); },
    anticipate: (u) => { const k = 1.8; return u * u * ((k + 1) * u - k); },
    elastic: (u) => (u === 0 || u === 1 ? u : Math.pow(2, -10 * u) * Math.sin((u * 10 - .75) * (TAU / 3)) + 1),
    bounce: (u) => { const n = 7.5625, d = 2.75; if (u < 1 / d) return n * u * u; if (u < 2 / d) return n * (u -= 1.5 / d) * u + .75; if (u < 2.5 / d) return n * (u -= 2.25 / d) * u + .9375; return n * (u -= 2.625 / d) * u + .984375; },
    hold: () => 0,
  };
  const blendValue = (a, b, u) => {
    if (typeof a === "number") return lerp(a, b, u);
    if (Array.isArray(a)) return a.map((v, i) => blendValue(v, b[i], u));
    if (a && typeof a === "object") { const o = {}; for (const k in a) o[k] = k in b ? blendValue(a[k], b[k], u) : a[k]; return o; }
    return u < 1 ? a : b;
  };
  // key(t, [[0, 0], [0.4, 120, "back"], [1.2, 80, "inOut"]]): the value at t through key poses; the ease of a key
  // shapes the move INTO it. Values may be numbers, arrays or objects (poses), blended part by part.
  function key(t, keys) {
    if (t <= keys[0][0]) return keys[0][1];
    for (let i = 1; i < keys.length; i++) {
      const [t1, v1, e] = keys[i], [t0, v0] = keys[i - 1];
      if (t <= t1) return blendValue(v0, v1, (ease[e || "inOut"] || ease.inOut)((t - t0) / Math.max(1e-6, t1 - t0)));
    }
    return keys[keys.length - 1][1];
  }
  const twos = (t, fps = 12) => Math.floor(t * fps + 1e-6) / fps; // animate a character on twos (12 drawings a second)
  const between = (t, a, b) => clamp((t - a) / (b - a), 0, 1);
  function shake(t, amount, seed = 1, freq = 22) { return [amount * (Math.sin(t * freq + seed) * .6 + Math.sin(t * freq * 2.3 + seed * 3) * .4), amount * (Math.cos(t * freq * 1.3 + seed) * .6 + Math.sin(t * freq * 1.7 + seed * 5) * .4)]; }

  // ---------------------------------------------------------------- the camera
  // camera(c, w, h, {x, y, zoom, rot, shake: [dx, dy]}, () => { ...draw the world... }): x, y are where the camera
  // looks (in px of the frame, from its centre); zoom > 1 pushes in. Draw anything that must not move (grain,
  // letterbox) after it.
  function camera(c, w, h, cam, fn) {
    const { x = 0, y = 0, zoom = 1, rot = 0 } = cam || {}, [sx, sy] = cam?.shake || [0, 0];
    c.save(); c.translate(w / 2 + sx, h / 2 + sy); c.rotate(rot); c.scale(zoom, zoom); c.translate(-w / 2 - x, -h / 2 - y);
    fn(); c.restore();
  }

  // ---------------------------------------------------------------- brushes
  function sky(c, w, h, stops, y0 = 0, y1 = h) { const g = c.createLinearGradient(0, y0, 0, y1); for (const [o, col] of stops) g.addColorStop(o, col); c.fillStyle = g; c.fillRect(-w, y0, w * 3, y1 - y0); }
  function glow(c, x, y, r, col, a = 1) {
    const g = c.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, rgba(col, a)); g.addColorStop(.35, rgba(col, a * .35)); g.addColorStop(1, rgba(col, 0));
    c.fillStyle = g; c.fillRect(x - r, y - r, r * 2, r * 2);
  }
  function disc(c, x, y, r, col) { c.fillStyle = col; c.beginPath(); c.arc(x, y, Math.max(0, r), 0, TAU); c.fill(); }
  function stars(c, w, h, n, seed, t, maxY = .6) {
    const r = rng(seed); c.fillStyle = "#fff";
    for (let i = 0; i < n; i++) { const x = r() * w, y = r() * h * maxY, s = (r() * 1.3 + .4) * h / 420, sp = 1 + r() * 3, base = r(); c.globalAlpha = .15 + .7 * base * (.5 + .5 * Math.sin(t * sp + i)); c.fillRect(x, y, s, s); }
    c.globalAlpha = 1;
  }
  function clouds(c, w, h, t, seed, col, a, y0, y1, speed = .012) {
    const r = rng(seed), L = w + h * 1.6; c.fillStyle = col;
    for (let i = 0; i < 7; i++) { const x = ((r() * L + t * speed * h * (1 + r())) % L) - h * .8, y = h * (y0 + r() * (y1 - y0)), sw = h * (.25 + r() * .45), sh = h * (.012 + r() * .022); c.globalAlpha = a * (.45 + r() * .55); c.beginPath(); c.ellipse(x, y, sw, sh, 0, 0, TAU); c.fill(); }
    c.globalAlpha = 1;
  }
  function puff(c, x, y, s, col, shade) { // a cel-shaded cumulus
    const blobs = [[0, 0, 1], [-.9, .25, .7], [.9, .25, .75], [-.4, -.35, .75], [.45, -.3, .7], [1.6, .4, .5], [-1.6, .45, .45]];
    if (shade) for (const [dx, dy, r] of blobs) disc(c, x + dx * s, y + dy * s + s * .2, r * s, shade);
    for (const [dx, dy, r] of blobs) disc(c, x + dx * s, y + dy * s, r * s, col);
  }
  function birds(c, w, h, t, n, seed, col) {
    const r = rng(seed); c.strokeStyle = col; c.lineWidth = Math.max(1, h * .004); c.lineCap = "round";
    for (let i = 0; i < n; i++) { const sp = .025 + r() * .02, x = (((r() + t * sp) % 1.2) - .1) * w, y = h * (.18 + r() * .22) + Math.sin(t * 1.3 + i) * h * .012, s = h * (.01 + r() * .008), f = Math.sin(t * 9 + i * 2); c.beginPath(); c.moveTo(x - s, y - s * f * .7); c.quadraticCurveTo(x - s * .4, y - s * .15 * f, x, y); c.quadraticCurveTo(x + s * .4, y - s * .15 * f, x + s, y - s * f * .7); c.stroke(); }
  }
  function ridge(c, w, h, base, seed, col, amp, off = 0) {
    c.fillStyle = col; c.beginPath(); c.moveTo(-w, h * 2);
    for (let x = -w; x <= w * 2; x += w / 80) { const u = x / h + off + seed; c.lineTo(x, base - amp * h * (.5 + .3 * Math.sin(u * 2.1) + .2 * Math.sin(u * 5.3 + 1) + .08 * Math.sin(u * 13.7))); }
    c.lineTo(w * 2, h * 2); c.closePath(); c.fill();
  }
  const cities = {};
  function skyline(c, w, h, { seed = 1, minH = .1, maxH = .3, off = 0, base = h * .9, col = "#100818", win = "#ffd27a", lit = .15, t = 0 } = {}) {
    const key0 = `${seed}:${minH}:${maxH}`;
    if (!cities[key0]) { const r = rng(seed), b = []; let x = 0; while (x < 3) { const bw = .05 + r() * .13; b.push({ x, w: bw, h: minH + r() * (maxH - minH), ant: r() < .35, tank: r() < .18, step: r() < .3, s: (r() * 997) | 0 }); x += bw + r() * .012; } cities[key0] = { b, len: x }; }
    const cy = cities[key0], L = cy.len * h, o = (((off * h) % L) + L) % L, vis = [];
    c.fillStyle = col;
    for (let rep = 0; rep * L - o < w; rep++) for (const B of cy.b) {
      const x = B.x * h + rep * L - o, bw = B.w * h; if (x > w || x + bw < 0) continue;
      const top = base - B.h * h, st = B.step ? B.h * h * .08 : 0;
      c.fillRect(x, top, bw + .6, h * 2 - top); if (st) c.fillRect(x + bw * .2, top - st, bw * .6, st + 1);
      if (B.ant) c.fillRect(x + bw * .7, top - st - h * .07, Math.max(1, h * .004), h * .07);
      if (B.tank) c.fillRect(x + bw * .12, top - h * .035, bw * .3, h * .035);
      vis.push([x, bw, top, B]);
    }
    if (!lit) return;
    c.fillStyle = win; const ww = h * .009, wh = h * .012, gx = h * .022, gy = h * .03;
    for (const [x, bw, top, B] of vis) { let row = 0; for (let yy = top + gy * .6; yy < base - gy * .4; yy += gy, row++) { let col2 = 0; for (let xx = x + gx * .4; xx < x + bw - ww - gx * .2; xx += gx, col2++) { const k = hash(B.s + col2 * 7.1 + row * 13.7); if (k < lit) { c.globalAlpha = .5 + .5 * Math.sin(t * .6 + k * 60) ** 2; c.fillRect(xx, yy, ww, wh); } } } }
    c.globalAlpha = 1;
  }
  function streaks(c, w, h, t, speed, a = .14, maxY = .78) {
    c.strokeStyle = `rgba(255,255,255,${a})`; c.lineWidth = Math.max(1, h * .003);
    for (let i = 0; i < 16; i++) { const y = hash(i) * h * maxY, sp = speed * (.5 + hash(i + 3)), x = (((hash(i + 9) - t * sp) % 1 + 1) % 1) * w * 1.4 - w * .2; c.beginPath(); c.moveTo(x, y); c.lineTo(x + h * (.15 + hash(i + 5) * .2), y); c.stroke(); }
  }
  function speedLines(c, w, h, cx, cy, { n = 70, inner = .3, col = "#f4f1ea", seed = 1 } = {}) { // the anime burst
    c.strokeStyle = col; c.lineCap = "round";
    for (let i = 0; i < n; i++) { const a = hash(i + seed) * TAU, r0 = h * (inner + hash(i + 1 + seed) * .2); c.lineWidth = h * (.003 + hash(i + 2 + seed) * .012); c.beginPath(); c.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0); c.lineTo(cx + Math.cos(a) * w * 1.5, cy + Math.sin(a) * w * 1.5); c.stroke(); }
  }
  // drifting things: petals, snow, rain, dust, embers. Deterministic in t.
  function particles(c, w, h, t, { n = 40, seed = 1, kind = "dust", col = "#ffffff", size = .008, wind = .15, fall = .05, a = .8 } = {}) {
    c.fillStyle = col; c.strokeStyle = col;
    for (let i = 0; i < n; i++) {
      const sp = wind * (.6 + hash(i + seed) * .8), x = ((((hash(i + 2 + seed) + t * sp) % 1.1) + 1.1) % 1.1 - .05) * w;
      const y = ((((hash(i + 3 + seed) + t * fall * (.6 + hash(i + 7 + seed))) % 1.1) + 1.1) % 1.1 - .05) * h + Math.sin(t * 2 + i) * h * .02;
      const r = h * size * (.6 + hash(i + 6 + seed) * .8); c.globalAlpha = a * (.5 + .5 * hash(i + 9 + seed));
      if (kind === "petal") { c.save(); c.translate(x, y); c.rotate(t * 3 + i); c.beginPath(); c.ellipse(0, 0, r * 1.6, r, 0, 0, TAU); c.fill(); c.restore(); }
      else if (kind === "rain") { c.lineWidth = Math.max(1, r * .3); c.beginPath(); c.moveTo(x, y); c.lineTo(x - r * 2 * wind / .15, y + r * 8); c.stroke(); }
      else disc(c, x, y, r, col);
    }
    c.globalAlpha = 1;
  }
  function grass(c, w, h, t, { n = 90, seed = 1, y0 = .8, y1 = 1, len = .05, cols = ["#3f8f5c", "#5fb06f"], wind = 1 } = {}) {
    c.lineCap = "round";
    for (let i = 0; i < n; i++) { const x = hash(i + seed) * w, y = h * (y0 + hash(i + 2 + seed) * (y1 - y0)), l = h * len * (.6 + hash(i + 4 + seed)), b = Math.sin(t * 2 + x * .02) * l * .5 * wind; c.strokeStyle = cols[i % cols.length]; c.lineWidth = h * .008; c.beginPath(); c.moveTo(x, y); c.quadraticCurveTo(x + b * .3, y - l * .6, x + b, y - l); c.stroke(); }
  }
  // a ribbon that trails behind (towards -x): scarves, hair, tails, flags, smoke
  function ribbon(c, x, y, len, wd, t, col, lift = .1, speed = 9) {
    const n = 12, pts = [];
    for (let i = 0; i <= n; i++) { const u = i / n; pts.push([x - u * len, y + u * len * lift + Math.sin(t * speed - u * 5) * len * .13 * u]); }
    c.fillStyle = col; c.beginPath();
    pts.forEach(([px, py], i) => { const q = wd * (1 - .65 * i / n) / 2; i ? c.lineTo(px, py - q) : c.moveTo(px, py - q); });
    for (let i = n; i >= 0; i--) { const [px, py] = pts[i], q = wd * (1 - .65 * i / n) / 2; c.lineTo(px, py + q); }
    c.closePath(); c.fill();
  }
  function rays(c, w, h, x, y, { n = 9, col = "#fff4d6", a = .12, t = 0, spread = 1.2 } = {}) { // god rays from a light
    c.save(); c.globalCompositeOperation = "lighter";
    for (let i = 0; i < n; i++) { const ang = Math.PI / 2 + (i / (n - 1) - .5) * spread + Math.sin(t * .3 + i) * .02, wd = .03 + hash(i) * .05; const g = c.createLinearGradient(x, y, x + Math.cos(ang) * h * 1.5, y + Math.sin(ang) * h * 1.5); g.addColorStop(0, rgba(col, a)); g.addColorStop(1, rgba(col, 0)); c.fillStyle = g; c.beginPath(); c.moveTo(x, y); c.lineTo(x + Math.cos(ang - wd) * h * 2, y + Math.sin(ang - wd) * h * 2); c.lineTo(x + Math.cos(ang + wd) * h * 2, y + Math.sin(ang + wd) * h * 2); c.fill(); }
    c.restore();
  }
  function vignette(c, w, h, a = .5) { const g = c.createRadialGradient(w / 2, h / 2, Math.min(w, h) * .3, w / 2, h / 2, Math.hypot(w, h) * .6); g.addColorStop(0, "rgba(0,0,0,0)"); g.addColorStop(1, `rgba(0,0,0,${a})`); c.fillStyle = g; c.fillRect(0, 0, w, h); }
  let grainTile;
  function grain(c, w, h, { a = .1, t = 0, mode = "overlay" } = {}) { // film or paper grain, moving on twos
    if (!grainTile) { grainTile = document.createElement("canvas"); grainTile.width = grainTile.height = 192; const x = grainTile.getContext("2d"), d = x.createImageData(192, 192), r = rng(9); for (let i = 0; i < d.data.length; i += 4) { const v = 110 + r() * 145; d.data[i] = d.data[i + 1] = d.data[i + 2] = v; d.data[i + 3] = 255; } x.putImageData(d, 0, 0); }
    const k = Math.floor(t * 12); c.save(); c.globalCompositeOperation = mode; c.globalAlpha = a; c.translate((hash(k) * 192) | 0, (hash(k + 1) * 192) | 0); c.fillStyle = c.createPattern(grainTile, "repeat"); c.fillRect(-192, -192, w + 384, h + 384); c.restore();
  }
  function letterbox(c, w, h, amount = .1, col = "#000") { c.fillStyle = col; c.fillRect(0, 0, w, h * amount); c.fillRect(0, h * (1 - amount), w, h * amount); }

  // ---------------------------------------------------------------- a two-bone rig for people and creatures
  // angles are from straight down; positive swings forward (towards +x when facing right)
  function bone(c, x, y, a, l) { const nx = x + Math.sin(a) * l, ny = y + Math.cos(a) * l; c.lineTo(nx, ny); return [nx, ny]; }
  function limb(c, x, y, a1, l1, a2, l2) { c.beginPath(); c.moveTo(x, y); const [kx, ky] = bone(c, x, y, a1, l1); bone(c, kx, ky, a2, l2); c.stroke(); return [kx, ky]; }
  const joint = (x, y, a, l) => [x + Math.sin(a) * l, y + Math.cos(a) * l];
  const pose = {
    stand(t = 0, k = 0) { const b = Math.sin(t * 1.6 + k) * .03; return { lean: .02 + b * .3, bob: Math.sin(t * 1.6 + k) * .006, legs: [[.09, .06], [-.08, -.05]], arms: [[.12 + b, .08 + b], [-.14 - b, -.05]] }; },
    walk(ph) { const L = (q) => { const th = .38 * Math.sin(q); return [th, th - .3 * Math.max(0, Math.cos(q))]; }, A = (q) => { const u = -.3 * Math.sin(q); return [u, u + .25]; }; ph *= TAU; return { lean: .04, bob: Math.abs(Math.sin(ph)) * .015, legs: [L(ph + Math.PI), L(ph)], arms: [A(ph), A(ph + Math.PI)] }; },
    run(ph, amp = 1) { const L = (q) => { const th = .85 * amp * Math.sin(q); return [th, th - (.2 + 1.45 * amp * Math.max(0, Math.cos(q)))]; }, A = (q) => { const u = -.95 * amp * Math.sin(q); return [u, u + 1.5 * amp]; }; ph *= TAU; return { lean: .26 * amp, bob: Math.abs(Math.sin(ph)) * .045 * amp, legs: [L(ph + Math.PI), L(ph)], arms: [A(ph), A(ph + Math.PI)] }; },
    jump(u) { const k = Math.sin(clamp(u, 0, 1) * Math.PI); return { lean: .32, legs: [[.6 + .9 * k, .2 - .3 * k], [-.45 - .2 * k, -1.2 - .6 * k]], arms: [[2.3 + .4 * k, 2.6 + .4 * k], [1.9 + .3 * k, 2.2]] }; },
    sit(t = 0, k = 0) { const sw = Math.sin(t * 2.2 + k) * .25; return { lean: -.06, hipY: 0, legs: [[Math.PI / 2, .15 + sw], [Math.PI / 2 - .1, .05 - sw * .8]], arms: [[-.55, -.35], [-.45, -.25]] }; },
    crouch(u = 1) { return { lean: .35 * u, bob: -.12 * u, legs: [[.9 * u, -.6 * u], [.5 * u, -.9 * u]], arms: [[.6 * u, 1.2 * u], [.3 * u, .9 * u]] }; },
    point(t = 0) { const p = pose.stand(t, 1); p.arms[0] = [2.55 + Math.sin(t * 2) * .05, 2.7]; return p; },
    wave(t = 0) { const p = pose.stand(t, 2); p.arms[0] = [2.5, 2.9 + Math.sin(t * 9) * .45]; return p; },
    blend: (a, b, u) => blendValue(a, b, u),
  };
  // a ready-made silhouette person: a good start for silhouette films and crowds; a film's leads get their own
  // drawing (see the heroine in toon_example.js)
  function person(c, x, y, s, p, col, o = {}) {
    c.save(); c.translate(x, y); c.scale(o.dir || 1, 1);
    c.strokeStyle = col; c.fillStyle = col; c.lineCap = "round"; c.lineJoin = "round";
    const leg = s * .25, lean = p.lean || 0, torso = s * .3, hx = 0, hy = (p.hipY ?? -s * .5) - (p.bob || 0) * s;
    const nx = hx + Math.sin(lean) * torso, ny = hy - Math.cos(lean) * torso;
    c.lineWidth = s * .085; for (const [a1, a2] of p.legs) limb(c, hx, hy, a1, leg, a2, leg);
    c.lineWidth = s * .115; c.beginPath(); c.moveTo(hx, hy); c.lineTo(nx, ny); c.stroke();
    const sx = hx + Math.sin(lean) * torso * .86, sy = hy - Math.cos(lean) * torso * .86;
    c.lineWidth = s * .068; for (const [a1, a2] of p.arms) limb(c, sx, sy, a1, s * .16, a2, s * .15);
    const hr = s * .078; disc(c, nx + Math.sin(lean) * hr * 1.2, ny - Math.cos(lean) * hr * 1.2, hr, col);
    if (o.scarf) ribbon(c, nx - s * .01, ny + s * .02, s * .38, s * .06, o.t || 0, o.scarf, .05);
    c.restore();
  }

  // ---------------------------------------------------------------- the cast and the shot
  const castDraw = {};
  function cast(id, fn) { castDraw[id] = fn; }
  function draw(id, c, o = {}) {
    const fn = castDraw[id]; if (!fn) throw new Error(`no cast member "${id}"`);
    const t = o.t || 0, action = o.action || "stand", phase = o.phase ?? 0;
    const p = o.pose || (pose[action] ? pose[action](action === "stand" || action === "sit" || action === "point" || action === "wave" ? t : phase) : pose.stand(t));
    c.save(); fn(c, { x: 0, y: 0, s: 100, dir: 1, face: "neutral", ...o, t, action, phase, pose: p }); c.restore();
  }
  let theShot = null;
  function shot(def) { theShot = def; }

  // the runner (motion/drawn.py puts the page together): draws the shot (with motion blur) or a still at each seek
  function run(canvas, cfg) {
    const c = canvas.getContext("2d"), w = canvas.width, h = canvas.height;
    const acc = document.createElement("canvas"); acc.width = w; acc.height = h; const a = acc.getContext("2d");
    const one = (ctx, t) => { ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, w, h); ctx.fillStyle = "#000"; ctx.fillRect(0, 0, w, h); ctx.restore(); ctx.save(); paint(ctx, t); ctx.restore(); };
    const paint = cfg.mode === "sheet" ? (ctx, t) => sheet(ctx, w, h, cfg.id, t) : (ctx, t) => theShot.draw(ctx, w, h, t);
    const blur = cfg.mode === "sheet" ? 0 : clamp(Math.round(theShot?.blur ?? 2), 0, 8), shutter = (theShot?.shutter ?? .5) / (cfg.fps || 30);
    function frame(t) {
      t = Math.max(0, t);
      if (!blur) return one(c, t);
      one(c, t - shutter / 2);
      for (let k = 1; k <= blur; k++) { one(a, t - shutter / 2 + shutter * k / blur); c.globalAlpha = 1 / (k + 1); c.drawImage(acc, 0, 0); c.globalAlpha = 1; }
    }
    if (!theShot && cfg.mode !== "sheet") throw new Error("the shot file never called toon.shot({ draw(c, w, h, t) { ... } })");
    const fail = cfg.fail || ((where, err) => { throw err; });
    const safe = (t) => { try { frame(t); } catch (err) { fail(`at ${(+t).toFixed(2)} s`, err); } };
    window.addEventListener("hf-seek", (e) => safe(e.detail.time));
    safe(cfg.at || 0);
    window.__draw = safe; // for stills taken without the engine
  }

  // a model sheet: the character in its actions, both ways, and its faces, on a neutral ground
  function sheet(c, w, h, id, t) {
    sky(c, w, h, [[0, "#e9e6ef"], [1, "#cfcad9"]]);
    c.strokeStyle = "rgba(60,50,80,.12)"; c.lineWidth = 1; for (let y = h * .06; y < h; y += h * .06) { c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke(); }
    const rows = [[["stand", 0, 1], ["stand", 0, -1], ["walk", .25, 1], ["run", .3, 1], ["jump", .5, 1], ["sit", 0, 1]], [["neutral"], ["happy"], ["surprised"], ["sad"], ["angry"], ["talk"]]];
    const cols = 6, cw = w / cols, s = Math.min(h * .38, cw * 1.5);
    c.font = `600 ${Math.round(h * .022)}px ui-monospace, monospace`; c.fillStyle = "#4a4060";
    rows[0].forEach(([action, phase, dir], i) => { const x = cw * (i + .5), y = h * .46; c.fillStyle = "rgba(40,30,60,.14)"; c.beginPath(); c.ellipse(x, y, s * .2, s * .03, 0, 0, TAU); c.fill(); draw(id, c, { x, y, s, dir, t, action, phase, face: "neutral" }); c.fillStyle = "#4a4060"; c.fillText(`${action}${dir < 0 ? " (left)" : ""}`, x - cw * .4, h * .5); });
    const big = h * .9; // the faces close: the head of a character as tall as the sheet, cut to its cell
    rows[1].forEach(([face], i) => { const x = cw * (i + .5); c.save(); c.beginPath(); c.rect(cw * i + 2, h * .54, cw - 4, h * .42); c.clip(); draw(id, c, { x, y: h * .76 + big * .835, s: big, dir: 1, t, action: "stand", face }); c.restore(); c.fillStyle = "#4a4060"; c.fillText(face, x - cw * .4, h * .98); });
  }

  window.toon = {
    TAU, clamp, lerp, hash, rng, rgba, mix, ease, key, twos, between, shake, camera,
    sky, glow, disc, stars, clouds, puff, birds, ridge, skyline, streaks, speedLines, particles, grass, ribbon, rays,
    vignette, grain, letterbox, bone, limb, joint, pose, person, cast, draw, shot, run, sheet,
  };
})();
