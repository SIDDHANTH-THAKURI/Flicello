// Flicello's paper kit: a whole film in layered cut paper (motion/kits/paper/craft.md says how to design with it).
// Everything is SVG at the film's size. Every moving part keeps its own state (x, y, r, sx, sy, o) and writes its
// transform itself, so a seek to any frame is exact; things that loop (waves, gears, wings, walks) are functions of
// time in LIVE, run every frame. The kit logs what happens (a fold, a page turn, a snip) in EVENTS, and the film's
// sounds are made from them.
const NS = 'http://www.w3.org/2000/svg';
const FILM = window.FILM || { w: 1920, h: 1080, seconds: 60, fps: 30 };
const W = FILM.w, H = FILM.h, FPS = FILM.fps || 30, TAU = Math.PI * 2;
const U = Math.min(W, H) / 1080; // sizes scale with the film's short side (1 at 1080)
const tl = gsap.timeline({ paused: true, defaults: { immediateRender: false } });
const LIVE = [], REFIT = [], EVENTS = [];
window.__events = EVENTS;
let CUR = null; // the scene being drawn (scene() sets it)
const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const lerp = (a, b, u) => a + (b - a) * u;
const ss = (a, b, t) => { const u = clamp((t - a) / (b - a)); return u * u * (3 - 2 * u); };
function rng(seed) { let a = seed >>> 0; return () => { a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
const AUD = window.AUDIO || { env: [], kick: [], beats: [], bars: [], period: 0.5, offset: 0, drop: null };
const BEAT = AUD.period || 0.5;
const B = (k) => (AUD.offset || 0) + BEAT * k; // the k-th beat of the music (0 = its first beat)
const BAR = (k) => (AUD.bars && AUD.bars[k] !== undefined ? AUD.bars[k] : B(k * 4)); // the k-th bar's start
const envAt = (t) => (AUD.env.length ? AUD.env[clamp(Math.round(t * FPS), 0, AUD.env.length - 1)] : 0.5);
const kickAt = (t) => (AUD.kick.length ? AUD.kick[clamp(Math.round(t * FPS), 0, AUD.kick.length - 1)] : 0);
const step12 = (t) => Math.floor(t * 12 + 1e-6) / 12; // stop-motion: puppets move on twos (12 poses a second)

// ---------------------------------------------------------------- sounds: what happens, when (the film's sound is made from these)
// type: flap, flick, turn, snip, stamp, tick, keys (t..until), ticks (t..until), steps (t..until), step, pop, pling,
// chime, whoosh, swish, boom, drop (something small landing: a plop), plop, shimmer, splash, spark, clack, thump,
// chirp, creak, scribble, rumble, hum (t..until), hit (the film's big moment: added for you at the drop),
// or say (opts.sound: a sound described in words, made by the sound model when there is one)
function sfx(t, type, opts = {}) { if (t >= 0 && t <= FILM.seconds + 1) EVENTS.push({ t: Math.round(t * 1000) / 1000, type, ...opts }); }

// ---------------------------------------------------------------- elements and their state
function el(tag, attrs = {}, parent = null) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null && k !== 'rim') n.setAttribute(k, String(v));
  if (parent) parent.appendChild(n);
  return n;
}
// a puppet: a group whose transform is its state: translate(x y) rotate(r) scale(sx sy), about its own origin
function pup(parent, st = {}, attrs = {}) {
  const g = el('g', attrs, parent);
  g.__s = { x: 0, y: 0, r: 0, sx: 1, sy: 1, o: 1, ...norm(st) };
  put(g);
  return g;
}
function put(g) {
  const s = g.__s;
  g.setAttribute('transform', `translate(${s.x.toFixed(2)} ${s.y.toFixed(2)}) rotate(${s.r.toFixed(3)}) scale(${s.sx.toFixed(4)} ${s.sy.toFixed(4)})`);
  const o = clamp(s.o);
  g.setAttribute('opacity', o.toFixed(3));
  const vis = o > 0.002 && Math.abs(s.sx) > 0.002 && Math.abs(s.sy) > 0.002; // what can't be seen isn't drawn at all
  if (g.__vis !== vis) { g.__vis = vis; g.style.display = vis ? '' : 'none'; }
}
const upd = (g) => () => put(g);
function norm(v) { const o = { ...v }; if ('s' in o) { o.sx = o.s; o.sy = o.s; delete o.s; } return o; }
function to(g, vars, at, dur = 0.5, ease = 'power3.out') { tl.to(g.__s, { ...norm(vars), duration: dur, ease, onUpdate: upd(g) }, at); }
// from these values to where it is now
function from(g, vars, at, dur = 0.5, ease = 'back.out(1.8)') {
  at = opening(at);
  const end = {}, fv = norm(vars); for (const k of Object.keys(fv)) end[k] = g.__s[k];
  tl.fromTo(g.__s, fv, { ...end, duration: dur, ease, onUpdate: upd(g) }, at);
}
// a set at time 0 is also made at once: the renderer doesn't apply sets while the playhead sits exactly on 0
function set(g, vars, at) {
  at = opening(at);
  const v = norm(vars);
  if (at <= 0) { Object.assign(g.__s, v); put(g); }
  tl.set(g.__s, { ...v, onUpdate: upd(g) }, Math.max(0, at));
}
const hide = (g) => set(g, { o: 0 }, 0);
// on at a, off at b, at once
function show(g, a, b) { if (a > 0) { hide(g); set(g, { o: 1 }, a); } if (b !== undefined) set(g, { o: 0 }, b); }
// fades in at a and out at b
function life(g, a, b, fin = 0.25, fout = 0.25) { if (a > 0) hide(g); to(g, { o: 1 }, a, fin, 'power1.out'); if (b !== undefined) to(g, { o: 0 }, b - fout, fout, 'power1.in'); }
// folds up from the page like a pop-up (scale y from its base), with an overshoot; it keeps its own scale
function popUp(g, at, dur = 0.55, ease = 'back.out(2)') { const s1 = g.__s.sy; set(g, { sy: 0.001, o: 0 }, 0); set(g, { o: 1 }, at); to(g, { sy: s1 }, at, dur, ease); sfx(at, 'flap', { big: !!g.__big }); }
function popDown(g, at, dur = 0.35) { to(g, { sy: 0.001 }, at, dur, 'back.in(1.6)'); set(g, { o: 0 }, at + dur); sfx(at + dur * 0.6, 'flap', { big: !!g.__big }); }
function popIn(g, at, dur = 0.5, ease = 'back.out(2.4)') { const s1 = g.__s.sx, s2 = g.__s.sy; set(g, { sx: 0.001, sy: 0.001, o: 0 }, 0); set(g, { o: 1 }, at); to(g, { sx: s1, sy: s2 }, at, dur, ease); sfx(at, 'pop'); }
function popOut(g, at, dur = 0.3) { to(g, { sx: 0.001, sy: 0.001 }, at, dur, 'back.in(1.8)'); set(g, { o: 0 }, at + dur); }
// a stamp: lands from big, with a thump
function stamp(g, at, dur = 0.28) { const s1 = g.__s.sx, s2 = g.__s.sy, r1 = g.__s.r; set(g, { o: 0 }, 0); set(g, { o: 1 }, at); tl.fromTo(g.__s, { sx: s1 * 1.6, sy: s2 * 1.6, r: r1 - 8 }, { sx: s1, sy: s2, r: r1, duration: dur, ease: 'power4.in', onUpdate: upd(g) }, at); sfx(at + dur, 'stamp'); }
// zooms in from a little bigger, like a camera finding focus
function focusIn(g, at, dur = 0.55, from_ = 1.25) { const s1 = g.__s.sx; set(g, { o: 0 }, 0); set(g, { o: 1 }, at); tl.fromTo(g.__s, { sx: s1 * from_, sy: s1 * from_ }, { sx: s1, sy: s1, duration: dur, ease: 'power3.out', onUpdate: upd(g) }, at); }
// a layer that pops from its base line: the outer group sits on the base, its content is drawn in page coordinates
function pl(parent, baseY, depth = 2, x = 0) {
  const o = pup(parent, { x, y: baseY }, depth ? { filter: shadow(depth) } : {});
  o.__in = el('g', { transform: `translate(${-x} ${-baseY})` }, o);
  o.__big = true;
  return o;
}
// a camera: content in cam.__in, page coordinates; cam.x/y is where the page centre is, cam.s the zoom
function camera(parent, cx = W / 2, cy = H / 2) {
  const cam = pup(parent, { x: cx, y: cy });
  cam.__shake = pup(cam);
  cam.__in = el('g', { transform: `translate(${-cx} ${-cy})` }, cam.__shake);
  cam.__c = [cx, cy];
  return cam;
}
// the camera position that puts page point (px, py) at screen point (sx, sy) at zoom s
const aim = (cam, px, py, s, sx = W / 2, sy = H / 2) => ({ x: sx - s * (px - cam.__c[0]), y: sy - s * (py - cam.__c[1]), s });
function shake(cam, a, b, amp = 4) {
  LIVE.push((t) => {
    const k = t > a && t < b ? amp * Math.sin(Math.PI * (t - a) / (b - a)) : 0, q = step12(t);
    cam.__shake.__s.x = k * Math.sin(q * 91.7); cam.__shake.__s.y = k * Math.cos(q * 73.3); put(cam.__shake);
  });
  sfx(a, 'rumble', { until: b });
}
// scales a layer about the middle of the page (a dolly in); s0: its own scale at rest
const dolly = (g, k, dx, at, dur, s0 = 1) => { const s = g.__s; to(g, { x: W / 2 + k * (s.x - W / 2) + dx, y: H / 2 + k * (s.y - H / 2), sx: s0 * k, sy: s0 * k }, at, dur, 'power2.inOut'); };

// ---------------------------------------------------------------- scenes and the ways between them
const WORLD = document.getElementById('world');
const SCENE_LAYER = el('g', { id: 'scenes' }, WORLD);
const TOP = el('g', { id: 'top' }, WORLD);
const SCENES = {};
// The scenes are a stack of pages: the one leaving lies on the one coming, which is already there under it, so a way
// between them only takes the top page away (peels it over, cuts a hole in it, folds it down) and never shows an empty
// sheet. WAYS: how long the next page is under the leaving one before the cut, and the leaving one stays after it.
const WAYS = { turn: [0.42, 0.42], zoom: [0.42, 0.02], fold: [0.5, 0.02], flash: [0, 0], cut: [0, 0] };
// when a scene's words may come in: once its way in is over
const SETTLE = { turn: 0.5, zoom: 0.3, fold: 0.15, flash: 0.25, cut: 0 };
// a scene of the film, from start to end: returns { C: its page (draw here), cam: its camera, root, T: start, E: end }
function scene(id, start, end) {
  CUR = null;
  const cuts = FILM.cuts || [], k = cuts.findIndex((c) => c.id === id);
  const wayIn = k > 0 ? cuts[k].in || 'cut' : 'cut', wayOut = k >= 0 && k + 1 < cuts.length ? cuts[k + 1].in || 'cut' : 'cut';
  const shown = Math.max(0, start - (WAYS[wayIn] || WAYS.cut)[0]);
  const root = pl(SCENE_LAYER, H, 0);
  SCENE_LAYER.insertBefore(root, SCENE_LAYER.firstChild);
  show(root, shown, end + (WAYS[wayOut] || WAYS.cut)[1]);
  const lens = pup(root.__in, { x: W / 2, y: H / 2 }); // the whole page, about its middle (a way in can settle it)
  const cam = camera(el('g', { transform: `translate(${-W / 2} ${-H / 2})` }, lens));
  const sc = { id, root, lens, cam, C: cam.__in, T: start, E: end, exit: [W / 2, H / 2], wayIn, shown,
               settle: start + (SETTLE[wayIn] || 0) };
  SCENES[id] = sc;
  (window.__scenes = window.__scenes || {})[id] = [start, end];
  CUR = sc;
  return sc;
}
// what a scene sets or starts at its very first moment is there from when its page shows (under the leaving one)
function opening(at) { return CUR && Math.abs(at - CUR.T) < 1e-3 ? CUR.shown : at; }
// words never come in while their page is still arriving: a line asked for then waits until it's there
function wordAt(at) { return CUR && at > CUR.T - 0.3 && at < CUR.settle ? CUR.settle : at; }
// where the camera dives in when the way out is 'zoom' (a door, a window, a screen)
function exitAt(sc, x, y) { sc.exit = [x, y]; }
// the way from one scene to the next, at the cut: 'turn' (the page is turned over), 'zoom' (the camera dives into the
// leaving scene's exit point and the next scene opens through a hole cut there), 'fold' (the leaving page folds down
// flat onto the next), 'flash', 'cut'
function transition(kind, at, outId, opts = {}) {
  CUR = null;
  const out = SCENES[outId], inc = Object.values(SCENES).find((s) => s !== out && Math.abs(s.T - at) < 0.02);
  if (kind === 'turn' && out) peel(out, at, opts.dur || WAYS.turn[0] * 2);
  else if (kind === 'flash') flash(TOP, at, opts.color || '#fff6e0', 0.95, 0.12, 0.5);
  else if (kind === 'zoom' && out) {
    const d = opts.dur || 1.2, depth = opts.depth || 10;
    to(out.cam, aim(out.cam, out.exit[0], out.exit[1], depth), at - d, d, 'power3.in');
    iris(out, at, WAYS.zoom[0], d, depth);
    if (inc) tl.fromTo(inc.lens.__s, { sx: 1.18, sy: 1.18 }, { sx: 1, sy: 1, duration: 0.9, ease: 'power2.out', onUpdate: upd(inc.lens) }, at - WAYS.zoom[0]);
    sfx(at - d, 'whoosh', { dur: d, rise: true });
  } else if (kind === 'fold' && out) foldDown(out, at, WAYS.fold[0]);
}
let WAYN = 0;
const easeIO = (u) => (u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2);
// the leaving page turned over from its right edge: it is cut along the fold as the fold moves left, its back curls
// over it, and it throws a shadow on the page underneath
function peel(sc, at, dur = 0.84) {
  const id = `way${WAYN++}`, defs = document.querySelector('#stage defs');
  const cp = el('clipPath', { id, clipPathUnits: 'userSpaceOnUse' }, defs), keep = el('path', {}, cp);
  const grad = el('linearGradient', { id: `${id}s`, x1: 0, x2: 1 }, defs);
  el('stop', { offset: 0, 'stop-color': '#160b20', 'stop-opacity': 0.34 }, grad);
  el('stop', { offset: 1, 'stop-color': '#160b20', 'stop-opacity': 0 }, grad);
  const cast = el('path', { fill: `url(#${id}s)` }, TOP); // the shadow on the page underneath
  const flap = el('g', { filter: shadow(3) }, TOP);
  const back = el('path', { fill: '#efe3cf' }, flap), sheen = el('path', { fill: 'url(#curlR)' }, flap);
  const a = at - dur / 2, b = at + dur / 2, k = 0.12 * H, c = 0.42, P = 60;
  LIVE.push((t) => {
    const on = t >= a && t < b;
    if (!on) { sc.root.__in.removeAttribute('clip-path'); for (const n of [cast, back, sheen]) n.setAttribute('d', ''); return; }
    const u = lerp(W + k + 40, -k - 40, easeIO((t - a) / dur)), xt = u + k, xb = u - k; // the fold at the top and the bottom
    keep.setAttribute('d', poly([[-P, -P], [xt, -P], [xb, H + P], [-P, H + P]]));
    sc.root.__in.setAttribute('clip-path', `url(#${id})`);
    const ft = xt - c * (W + P - xt), fb = xb - c * (W + P - xb), s = 40 + 0.05 * W;
    const flapPts = poly([[xt, -P], [ft, -P], [fb, H + P], [xb, H + P]]);
    back.setAttribute('d', flapPts); sheen.setAttribute('d', flapPts);
    cast.setAttribute('d', poly([[xt, -P], [xt + s, -P], [xb + s, H + P], [xb, H + P]]));
  });
  sfx(a, 'turn', { dur });
}
// a hole cut in the leaving page at its exit point (in the page, so it rides the camera's dive) that opens until the
// next scene, under it, is all there is
function iris(sc, at, up, dive, depth) {
  const id = `way${WAYN++}`, defs = document.querySelector('#stage defs');
  const m = el('mask', { id, maskUnits: 'userSpaceOnUse', x: -1e5, y: -1e5, width: 2e5, height: 2e5 }, defs);
  el('rect', { x: -1e5, y: -1e5, width: 2e5, height: 2e5, fill: '#fff' }, m);
  const hole = el('path', { fill: '#000' }, m);
  const rim = el('path', { fill: 'none', stroke: '#f6eedd', 'stroke-linejoin': 'round', filter: shadow(1) }, sc.C);
  const [x, y] = sc.exit, R = 0.62 * Math.hypot(W, H), r0 = rng(77), ph = [r0() * TAU, r0() * TAU, r0() * TAU];
  const wob = Array.from({ length: 120 }, (_, i) => { const a = (i / 120) * TAU; return 1 + 0.02 * Math.sin(3 * a + ph[0]) + 0.012 * Math.sin(7 * a + ph[1]) + 0.006 * Math.sin(13 * a + ph[2]); }); // a hand-cut round
  LIVE.push((t) => {
    const v = (t - (at - up)) / up;
    if (v <= 0 || v >= 1.2) { sc.C.removeAttribute('mask'); hole.setAttribute('d', ''); rim.setAttribute('d', ''); return; }
    const s = 1 + (depth - 1) * Math.pow(clamp((t - (at - dive)) / dive), 3); // the camera's zoom on its dive
    const r = (R * Math.pow(clamp(v), 2)) / s, pts = wob.map((w, i) => [x + Math.cos((i / 120) * TAU) * r * w, y + Math.sin((i / 120) * TAU) * r * w]);
    hole.setAttribute('d', poly(pts)); rim.setAttribute('d', poly(pts)); rim.setAttribute('stroke-width', (10 / s).toFixed(2));
    sc.C.setAttribute('mask', `url(#${id})`);
  });
  sfx(at - up * 0.6, 'flap', { big: true });
}
// the leaving page folds down flat onto the next one, from its top edge to its base
function foldDown(sc, at, dur) {
  const id = `way${WAYN++}`, defs = document.querySelector('#stage defs');
  const grad = el('linearGradient', { id, x1: 0, x2: 0, y1: 1, y2: 0 }, defs);
  el('stop', { offset: 0, 'stop-color': '#160b20', 'stop-opacity': 0.3 }, grad);
  el('stop', { offset: 1, 'stop-color': '#160b20', 'stop-opacity': 0 }, grad);
  const cast = el('rect', { x: -20, width: W + 40, height: 0, fill: `url(#${id})` }, TOP); // its shadow on the next page
  const dim = el('rect', { x: -20, y: -20, width: W + 40, height: H + 40, fill: '#160b20', opacity: 0 }, sc.root.__in); // tipping away from the light
  const a = at - dur;
  LIVE.push((t) => {
    const v = clamp((t - a) / dur), sy = t < a ? 1 : Math.max(0.001, 1 - v * v);
    sc.root.__s.sy = sy; put(sc.root);
    const top = H * (1 - sy), sh = t > a && t < at ? 30 + 90 * v : 0;
    cast.setAttribute('y', (top - sh).toFixed(1)); cast.setAttribute('height', sh.toFixed(1));
    dim.setAttribute('opacity', (0.38 * v).toFixed(3));
  });
  sfx(a + dur * 0.6, 'flap', { big: true });
}

// ---------------------------------------------------------------- paper: cut edges, shadows
const shadow = (d) => `url(#ps${d})`;
const RIM = { stroke: 'rgba(255,246,230,.2)', 'stroke-width': 1.3, 'stroke-linejoin': 'round' }; // the paper's pale core at the cut
function layer(parent, depth = 2, st = {}) { return pup(parent, st, { filter: shadow(depth) }); }
// points along a polygon's edges, each nudged a little: a scissor-cut edge
function cutEdge(pts, amp = 2.2, seed = 1, step = 26, closed = true) {
  const r = rng(seed), out = [];
  const n = pts.length, m = closed ? n : n - 1;
  for (let i = 0; i < m; i++) {
    const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % n];
    const len = Math.hypot(x2 - x1, y2 - y1), k = Math.max(1, Math.round(len / step));
    const nx = -(y2 - y1) / (len || 1), ny = (x2 - x1) / (len || 1);
    for (let j = 0; j < k; j++) { const u = j / k, w = (r() - 0.5) * 2 * amp * (j === 0 ? 0.3 : 1); out.push([x1 + (x2 - x1) * u + nx * w, y1 + (y2 - y1) * u + ny * w]); }
  }
  if (!closed) out.push(pts[n - 1]);
  return out;
}
const poly = (pts) => 'M' + pts.map((p) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' L') + ' Z';
function shape(parent, pts, fill, extra = {}) { return el('path', { d: poly(pts), fill, ...extra }, parent); }
const rimmed = (extra) => (extra.rim === false || extra.stroke ? extra : { ...RIM, ...extra });
function cutShape(parent, pts, fill, seed = 1, amp = 2.2, extra = {}) { return shape(parent, cutEdge(pts, amp, seed), fill, rimmed(extra)); }
function cutCircle(parent, cx, cy, r, fill, seed = 1, amp = 1.6, extra = {}) {
  const k = Math.max(18, Math.round(r / 5)), rr = rng(seed), pts = [];
  for (let i = 0; i < k; i++) { const a = (i / k) * TAU, w = r + (rr() - 0.5) * 2 * amp; pts.push([cx + Math.cos(a) * w, cy + Math.sin(a) * w]); }
  return shape(parent, pts, fill, rimmed(extra));
}
function cutEllipse(parent, cx, cy, rx, ry, fill, seed = 1, amp = 1.6, rot = 0, extra = {}) {
  const k = Math.max(20, Math.round((rx + ry) / 8)), rr = rng(seed), pts = [];
  const c = Math.cos(rot), s = Math.sin(rot);
  for (let i = 0; i < k; i++) { const a = (i / k) * TAU, wx = rx + (rr() - 0.5) * 2 * amp, wy = ry + (rr() - 0.5) * 2 * amp; const px = Math.cos(a) * wx, py = Math.sin(a) * wy; pts.push([cx + px * c - py * s, cy + px * s + py * c]); }
  return shape(parent, pts, fill, rimmed(extra));
}
function roundRectPts(x, y, w, h, r, seg = 5) {
  r = Math.min(r, w / 2, h / 2);
  const pts = [], cs = [[x + w - r, y + r, -Math.PI / 2], [x + w - r, y + h - r, 0], [x + r, y + h - r, Math.PI / 2], [x + r, y + r, Math.PI]];
  for (const [cx, cy, a0] of cs) for (let i = 0; i <= seg; i++) { const a = a0 + (i / seg) * (Math.PI / 2); pts.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r]); }
  return pts;
}
function cutRect(parent, x, y, w, h, fill, seed = 1, r = 6, amp = 1.8, extra = {}) { return shape(parent, cutEdge(roundRectPts(x, y, w, h, r), amp, seed, 30), fill, rimmed(extra)); }
// a soft-edged hill along x (y = fn(x)), closed to the bottom
function hill(parent, fn, x0, x1, fill, seed = 1, bottom = H + 60, extra = {}) {
  const pts = [[x0, bottom]];
  for (let x = x0; x <= x1 + 1; x += 20) pts.push([x, fn(x)]);
  pts.push([x1, bottom]);
  return cutShape(parent, pts, fill, seed, 2, extra);
}
// the light along a hill's crest
function crest(parent, fn, x0, x1, stroke, width = 3, opacity = 0.8) {
  const pts = []; for (let x = x0; x <= x1 + 1; x += 20) pts.push(`${x} ${(fn(x) + 1).toFixed(1)}`);
  return el('path', { d: 'M' + pts.join(' L'), stroke, 'stroke-width': width, fill: 'none', opacity }, parent);
}
const bump = (base, amp, cx, w, wob = 0, f = 0.006) => (x) => base - amp * Math.exp(-(((x - cx) / w) ** 2)) - wob * Math.sin(x * f);
// low-poly ridge: peaks and valleys; each peak has a face toward the sun, one in shade, a facet between and a lit edge
function lowpoly(parent, o) {
  const { x0 = -140, x1 = W + 140, base = H + 60, top, amp, col, shade, rim, n, seed, sunX = W / 2, snow = null } = o;
  const r = rng(seed), g = el('g', {}, parent), ridge = [];
  for (let i = 0; i <= n * 2; i++) {
    const x = x0 + ((x1 - x0) * i) / (n * 2) + (i > 0 && i < n * 2 ? (r() - 0.5) * 50 : 0), peak = i % 2 === 1;
    ridge.push([x, peak ? top - amp * (0.55 + r() * 0.45) : top - amp * (0.05 + r() * 0.25)]);
  }
  cutShape(g, [[x0, base], ...ridge, [x1, base]], col, seed, 1.6);
  for (let i = 1; i < ridge.length - 1; i += 2) {
    const L = ridge[i - 1], P = ridge[i], R = ridge[i + 1], foot = [P[0] + (r() - 0.5) * 60, base];
    const litLeft = P[0] > sunX;
    shape(g, litLeft ? [P, R, foot] : [L, P, foot], shade);
    const mid = [lerp(P[0], foot[0], 0.42) + (litLeft ? -1 : 1) * (20 + r() * 30), lerp(P[1], base, 0.42)];
    shape(g, litLeft ? [L, P, mid] : [P, R, mid], rim, { opacity: 0.17 });
    const E = litLeft ? L : R;
    el('path', { d: `M ${P[0].toFixed(1)} ${P[1].toFixed(1)} L ${lerp(P[0], E[0], 0.62).toFixed(1)} ${lerp(P[1], E[1], 0.62).toFixed(1)}`, stroke: rim, 'stroke-width': 3.2, 'stroke-linecap': 'round', fill: 'none', opacity: 0.9 }, g);
    if (snow && P[1] < top - amp * 0.72) {
      const k = 0.2, a = [lerp(P[0], L[0], k), lerp(P[1], L[1], k)], b = [lerp(P[0], R[0], k), lerp(P[1], R[1], k)];
      shape(g, [P, b, [lerp(P[0], b[0], 0.5) + 4, b[1] - 8], [P[0] + (r() - 0.5) * 12, P[1] + (b[1] - P[1]) * 0.9], [lerp(P[0], a[0], 0.5) - 4, a[1] - 6], a], snow, { opacity: 0.95 });
    }
  }
  return g;
}

// ---------------------------------------------------------------- the sky and the land
function sun(parent, cx, cy, r, cols = ['#ffd36b', '#ffb347', '#ff8a5c']) {
  const g = pup(parent, { x: cx, y: cy });
  cutCircle(g, 0, 0, r * 2.3, cols[0], 2, 3, { opacity: 0.08, rim: false });
  cutCircle(g, 0, 0, r * 1.65, cols[0], 1, 2.5, { opacity: 0.14, rim: false });
  const rays = pup(g);
  for (let i = 0; i < 16; i++) { const a = (i / 16) * TAU, w = 0.075; shape(rays, [[Math.cos(a - w) * r * 1.22, Math.sin(a - w) * r * 1.22], [Math.cos(a) * r * (i % 2 ? 1.5 : 1.7), Math.sin(a) * r * (i % 2 ? 1.5 : 1.7)], [Math.cos(a + w) * r * 1.22, Math.sin(a + w) * r * 1.22]], cols[2], { opacity: 0.8 }); }
  const discs = el('g', { filter: shadow(1) }, g);
  cutCircle(discs, 0, 0, r * 1.16, cols[1], 3, 1.5, { opacity: 0.95 });
  cutCircle(discs, 0, 0, r, cols[0], 4, 1.4);
  cutCircle(discs, -r * 0.24, -r * 0.24, r * 0.46, '#fff0b8', 5, 1, { opacity: 0.8 });
  LIVE.push((t) => { rays.__s.r = t * 5; put(rays); });
  return g;
}
function star(parent, x, y, r, col, seed = 1, extra = {}) {
  const pts = []; for (let i = 0; i < 8; i++) { const a = (i / 8) * TAU - Math.PI / 2, k = i % 2 ? 0.34 : 1; pts.push([x + Math.cos(a) * r * k, y + Math.sin(a) * r * k]); }
  return shape(parent, pts, col, extra);
}
// stars that twinkle, in box [x, y, w, h]
function starfield(parent, n, box, col, seed, rmin = 2, rmax = 6) {
  const g = pup(parent), r = rng(seed), list = [];
  for (let i = 0; i < n; i++) { const p = star(g, box[0] + r() * box[2], box[1] + r() * box[3], rmin + r() * (rmax - rmin), col, i); list.push([p, r() * 6, 0.8 + r() * 2]); }
  LIVE.push((t) => { for (const [p, ph, f] of list) p.setAttribute('opacity', (0.55 + 0.45 * Math.sin(t * f + ph)).toFixed(2)); });
  return g;
}
// a cloud: round puffs over a flat underside, the outline traced round them from the middle
function cloud(parent, x, y, w, col = '#fff7ea', seed = 1) {
  const g = layer(parent, 1, { x, y });
  const r = rng(seed), h = w * 0.36, base = h * 0.34;
  const puffs = [[-w * 0.33, h * 0.08, h * 0.4], [-w * 0.13, -h * 0.18, h * (0.56 + r() * 0.1)], [w * 0.11, -h * 0.1, h * (0.5 + r() * 0.1)], [w * 0.31, h * 0.08, h * 0.38], [0, h * 0.1, h * 0.42]];
  const pts = [];
  for (let i = 0; i < 96; i++) {
    const a = (i / 96) * TAU, dx = Math.cos(a), dy = Math.sin(a);
    let t = 0;
    for (const [cx, cy, R] of puffs) { const dc = dx * cx + dy * cy, disc = dc * dc - (cx * cx + cy * cy) + R * R; if (disc >= 0) t = Math.max(t, dc + Math.sqrt(disc)); }
    if (dy > 0 && t * dy > base) t = base / dy;
    pts.push([dx * t, dy * t]);
  }
  g.__path = shape(g, cutEdge(pts, 1.2, seed, 14), col, RIM);
  return g;
}
// a crescent moon, cut as one piece
function crescent(parent, x, y, r, col, seed = 1, off = 0.42) {
  const g = pup(parent, { x, y }, { filter: shadow(1) }), pts = [];
  const ox = r * off, oy = -r * off * 0.6, ri = r * 0.92;
  const lit = (a) => Math.hypot(Math.cos(a) * r - ox, Math.sin(a) * r - oy) > ri;
  const a0 = Math.atan2(oy, ox) + Math.PI;
  let lo = a0, hi = a0;
  while (lit(lo - 0.01) && lo > a0 - Math.PI) lo -= 0.01;
  while (lit(hi + 0.01) && hi < a0 + Math.PI) hi += 0.01;
  for (let i = 0; i <= 48; i++) { const a = lo + ((hi - lo) * i) / 48; pts.push([Math.cos(a) * r, Math.sin(a) * r]); }
  const E = pts[pts.length - 1], S = pts[0], aE = Math.atan2(E[1] - oy, E[0] - ox), aS = Math.atan2(S[1] - oy, S[0] - ox);
  let d = (aS - aE + TAU * 3) % TAU; const mid = aE + d / 2;
  if (Math.hypot(ox + Math.cos(mid) * ri, oy + Math.sin(mid) * ri) > r) d -= TAU;
  for (let i = 1; i < 48; i++) { const a = aE + (d * i) / 48; pts.push([ox + Math.cos(a) * ri, oy + Math.sin(a) * ri]); }
  cutShape(g, pts, col, seed, 0.8);
  return g;
}
// a full moon with its craters
function moon(parent, x, y, r, col = '#f6e7c1', crater = '#e2cfa6', seed = 1) {
  const g = pup(parent, { x, y }, { filter: shadow(2) });
  cutCircle(g, 0, 0, r * 2, col, seed, 2, { opacity: 0.07, rim: false }); cutCircle(g, 0, 0, r * 1.45, col, seed + 1, 2, { opacity: 0.12, rim: false });
  cutCircle(g, 0, 0, r, col, seed + 2, 1.5);
  for (const [cx, cy, k] of [[-0.31, -0.24, 0.18], [0.29, 0.21, 0.13], [0.36, -0.4, 0.09], [-0.1, 0.43, 0.11]]) cutCircle(g, cx * r, cy * r, k * r, crater, seed + 3, 0.6, { rim: false });
  return g;
}
function bird(parent, x, y, s = 1, col = '#2d2a4a', seed = 1) {
  const g = pup(parent, { x, y, sx: s, sy: s });
  const p = el('path', { fill: col }, g);
  const ph = rng(seed)() * 6;
  const up = 'M -26 -10 Q -12 -2 0 4 Q 12 -2 26 -10 Q 12 2 0 10 Q -12 2 -26 -10 Z', dn = 'M -26 10 Q -12 0 0 4 Q 12 0 26 10 Q 12 6 0 10 Q -12 6 -26 10 Z';
  p.setAttribute('d', up);
  LIVE.push((t) => { p.setAttribute('d', Math.floor((t + ph) * 8) % 2 === 0 ? up : dn); });
  return g;
}
function pine(parent, x, y, h, col, shade, seed = 1) {
  const g = layer(parent, 1, { x, y });
  cutRect(g, -h * 0.04, -h * 0.18, h * 0.08, h * 0.2, '#3a2418', seed, 2, 1);
  for (let i = 0; i < 3; i++) { const w = h * (0.34 - i * 0.08), top = -h + i * h * 0.22, base = top + h * 0.42; cutShape(g, [[0, top], [w, base], [-w, base]], col, seed + i, 1.6); cutShape(g, [[0, top], [w, base], [w * 0.12, base]], shade, seed + i + 9, 1.2, { opacity: 0.6, rim: false }); }
  return g;
}
function roundTree(parent, x, y, h, col, shade, seed = 1) {
  const g = layer(parent, 1, { x, y });
  cutRect(g, -h * 0.05, -h * 0.45, h * 0.1, h * 0.46, '#6b4a34', seed, 2, 1);
  cutCircle(g, 0, -h * 0.68, h * 0.32, col, seed + 1, 2);
  cutCircle(g, h * 0.12, -h * 0.6, h * 0.22, shade, seed + 2, 2, { opacity: 0.85 });
  cutCircle(g, -h * 0.13, -h * 0.8, h * 0.11, '#ffffff', seed + 3, 1, { opacity: 0.2, rim: false });
  return g;
}
function bush(parent, x, y, w, col, shade, seed = 1) {
  const g = layer(parent, 1, { x, y }), r = rng(seed);
  for (let i = 0; i < 4; i++) cutCircle(g, (i - 1.5) * w * 0.24 + (r() - 0.5) * 8, -w * (0.16 + r() * 0.12), w * (0.2 + r() * 0.08), i % 2 ? shade : col, seed + i, 1.5);
  return g;
}
function grass(parent, x0, x1, fn, col, seed = 1, gap = 26) {
  const r = rng(seed), g = el('g', {}, parent);
  for (let x = x0; x < x1; x += gap + r() * gap) { const y = typeof fn === 'function' ? fn(x) + 4 : fn, h = 12 + r() * 18; shape(g, [[x - 7, y], [x - 2, y - h], [x + 2, y - h * 0.45], [x + 6, y - h * 0.95], [x + 10, y]], col); }
  return g;
}
function flowers(parent, n, box, cols, seed, avoid = null) {
  const g = el('g', {}, parent), r = rng(seed);
  for (let i = 0; i < n; i++) { const x = box[0] + r() * box[2], y = box[1] + r() * box[3]; if (avoid && avoid(x, y)) continue; cutCircle(g, x, y, 6 + r() * 4, cols[i % cols.length], seed + i, 0.6); cutCircle(g, x, y, 2.5, '#ffb347', seed + 50 + i, 0.3, { rim: false }); }
  return g;
}
function rock(parent, x, y, w, col, lit, dark, seed = 1) {
  const g = el('g', {}, parent);
  cutShape(g, [[x - w, y], [x - w * 0.82, y - w * 0.42], [x - w * 0.3, y - w * 0.72], [x + w * 0.34, y - w * 0.64], [x + w * 0.86, y - w * 0.3], [x + w, y]], col, seed, 1.4);
  shape(g, [[x - w * 0.82, y - w * 0.42], [x - w * 0.3, y - w * 0.72], [x + w * 0.34, y - w * 0.64], [x - w * 0.06, y - w * 0.32]], lit, { opacity: 0.95 });
  shape(g, [[x + w * 0.34, y - w * 0.64], [x + w * 0.86, y - w * 0.3], [x + w * 0.3, y - w * 0.2]], dark, { opacity: 0.9 });
  return g;
}
// horizontal paper bands, each overlapping the one above it; the paths, to recolour
function skyBands(parent, tops, cols, seed, ampMin = 10) {
  return tops.map((y0, i) => {
    const g = el('g', i ? { filter: shadow(1) } : {}, parent);
    const r = rng(seed + i), pts = [];
    const amp = ampMin + r() * 12, f = 0.002 + r() * 0.003, ph = r() * 6;
    for (let x = -140; x <= W + 140; x += 24) pts.push([x, y0 + amp * Math.sin(x * f + ph) + amp * 0.4 * Math.sin(x * f * 3.1 + ph * 2)]);
    pts.push([W + 140, H + 80], [-140, H + 80]);
    return cutShape(g, pts, cols[i], seed * 7 + i, 1.5, i ? {} : { rim: false });
  });
}
// a flat paper page behind everything
function backdrop(parent, col, stripes = null, every = 120, width = 54) {
  const g = el('g', {}, parent);
  shape(g, [[-60, -60], [W + 60, -60], [W + 60, H + 60], [-60, H + 60]], col);
  if (stripes) for (let x = every / 4; x < W; x += every) shape(g, [[x, -60], [x + width, -60], [x + width, H + 60], [x, H + 60]], stripes, { opacity: 0.8 });
  return g;
}
// recolours everything under root drawn in table[i][0] through table[i][1], table[i][2], ... at times[0], times[1], ...
function palette(root, table, times, durs) {
  const nodes = [...root.querySelectorAll('*')];
  for (const row of table) {
    const key = row[0].toLowerCase();
    const hf = nodes.filter((n) => (n.getAttribute('fill') || '').toLowerCase() === key);
    const hs = nodes.filter((n) => (n.getAttribute('stroke') || '').toLowerCase() === key);
    times.forEach((t, i) => {
      if (hf.length) tl.to(hf, { attr: { fill: row[i + 1] }, duration: durs[i], ease: 'sine.inOut' }, t);
      if (hs.length) tl.to(hs, { attr: { stroke: row[i + 1] }, duration: durs[i], ease: 'sine.inOut' }, t);
    });
  }
}
function recolor(paths, cols, at, dur = 1.2, ease = 'sine.inOut') { paths.forEach((p, i) => { if (cols[i]) tl.to(p, { attr: { fill: cols[i] }, duration: dur, ease }, at); }); }
const fill = (node, col, at, dur = 0.4, ease = 'sine.inOut') => tl.to(node, { attr: { fill: col }, duration: dur, ease }, at);

// ---------------------------------------------------------------- words on paper
const MC = document.createElement('canvas').getContext('2d');
function measure(text, size, weight = 800, family = 'Bricolage', spacing = 0, italic = false) {
  MC.font = `${italic ? 'italic ' : ''}${weight} ${size}px "${family}"`;
  try { MC.letterSpacing = `${spacing}px`; } catch (e) { /* older canvas: no spacing */ }
  return MC.measureText(text).width;
}
function fitAll() { REFIT.forEach((f) => f()); }
function txt(parent, text, x, y, size, opts = {}) {
  const { weight = 800, font = 'Bricolage', fill = '#2b2236', anchor = 'middle', italic = false, spacing = italic ? 0 : -size * 0.025, upper = false } = opts;
  const t = el('text', { x, y, 'font-family': font, 'font-weight': weight, 'font-size': size, fill, 'text-anchor': anchor, 'letter-spacing': spacing.toFixed(2), 'font-style': italic ? 'italic' : 'normal' }, parent);
  t.textContent = upper ? text.toUpperCase() : text;
  return t;
}
// a strip of cream paper with words on it, cut to the words
function strip(parent, text, x, y, size = 72, opts = {}) {
  const { fill = '#fbf6ec', ink = '#2b2236', accent = null, accentCol = '#e5533d', tilt = 0, font = 'Bricolage', weight = 800, pad = 0.42, seed = 7, italic = false, depth = 2 } = opts;
  const g = pup(parent, { x, y, r: tilt }, { filter: shadow(depth) });
  const paper = el('path', { fill, ...RIM }, g);
  const spacing = italic ? 0 : -size * 0.025;
  const t = el('text', { x: 0, y: size * 0.35, 'font-family': font, 'font-weight': weight, 'font-size': size, fill: ink, 'text-anchor': 'middle', 'letter-spacing': spacing.toFixed(2), 'font-style': italic ? 'italic' : 'normal' }, g);
  if (accent && text.includes(accent)) { const i = text.indexOf(accent); t.appendChild(document.createTextNode(text.slice(0, i))); const s = el('tspan', { fill: accentCol }, t); s.textContent = accent; t.appendChild(document.createTextNode(text.slice(i + accent.length))); } else t.textContent = text;
  const fit = () => { const w = measure(text, size, weight, font, spacing, italic), ph = size * pad, h = size * 1.3; paper.setAttribute('d', poly(cutEdge(roundRectPts(-w / 2 - ph, -h / 2, w + ph * 2, h, 4, 2), 2.4, seed, 34))); };
  fit(); REFIT.push(fit);
  g.__w = () => measure(text, size, weight, font, spacing, italic) + size * pad * 2;
  g.__paper = paper; g.__text = t;
  return g;
}
// a strip flips up into place, and folds away
function stripIn(g, at, dur = 0.45) { at = wordAt(at); const r1 = g.__s.r; set(g, { o: 0 }, 0); set(g, { o: 1 }, at); tl.fromTo(g.__s, { sy: 0.001, r: r1 - 6 }, { sy: 1, r: r1, duration: dur, ease: 'back.out(2.2)', onUpdate: upd(g) }, at); sfx(at, 'flick'); }
function stripOut(g, at, dur = 0.3) { to(g, { sy: 0.001 }, at, dur, 'back.in(1.6)'); set(g, { o: 0 }, at + dur); sfx(at, 'flick', { gain: -6 }); }
// a kraft paper tag on a string from the top of the page, swinging a little; lines split on \n
function tag(parent, text, x, y, size = 50, seed = 3, col = '#dcbf92') {
  const lines = text.split('\n');
  const g = pup(parent, { x, y });
  const swing = pup(g);
  el('line', { x1: 0, y1: -y - 20, x2: 0, y2: 2, stroke: '#7a5a3a', 'stroke-width': 3 }, swing);
  const card = el('g', { filter: shadow(2) }, swing);
  const paper = el('path', { fill: col, ...RIM }, card);
  const lh = size * 1.1, top = 30;
  lines.forEach((ln, i) => txt(card, ln, 0, top + size * 0.95 + i * lh, size, { fill: '#3b2a1a', weight: 750 }));
  el('circle', { cx: 0, cy: 12, r: 8, fill: '#7a5a3a' }, card);
  el('circle', { cx: 0, cy: 12, r: 4.5, fill: '#3b2a1a' }, card);
  const fit = () => {
    const w = Math.max(...lines.map((l) => measure(l, size, 750, 'Bricolage', -size * 0.025)));
    const hw = w / 2 + size * 0.7, hb = top + size * 0.95 + (lines.length - 1) * lh + size * 0.45;
    paper.setAttribute('d', poly(cutEdge([[-hw + 34, -4], [hw - 34, -4], [hw, 30], [hw, hb], [-hw, hb], [-hw, 30]], 2, seed, 30)));
  };
  fit(); REFIT.push(fit);
  LIVE.push((t) => { swing.__s.r = Math.sin(t * 1.5 + seed) * 2; put(swing); });
  return g;
}
// a tag drops in on its string
function tagIn(g, at) { at = wordAt(at); const y1 = g.__s.y; set(g, { o: 0 }, 0); set(g, { o: 1 }, at); tl.fromTo(g.__s, { y: y1 - 320 }, { y: y1, duration: 0.7, ease: 'elastic.out(1, 0.55)', onUpdate: upd(g) }, at); sfx(at + 0.2, 'flick'); }
// a paper speech bubble; its tail points at (tx, ty), relative to the bubble's centre
function bubble(parent, text, x, y, size = 48, opts = {}) {
  const { fill = '#ffffff', ink = '#2b2236', tx = -160, ty = 140, seed = 11, accent = null, accentCol = '#e5533d' } = opts;
  const g = pup(parent, { x, y }, { filter: shadow(2) });
  const tail = el('path', { fill, ...RIM }, g), body = el('path', { fill, ...RIM }, g);
  const t = el('text', { x: 0, y: size * 0.36, 'font-family': 'Bricolage', 'font-weight': 750, 'font-size': size, fill: ink, 'text-anchor': 'middle', 'letter-spacing': (-size * 0.02).toFixed(2) }, g);
  if (accent && text.includes(accent)) { const i = text.indexOf(accent); t.appendChild(document.createTextNode(text.slice(0, i))); const s = el('tspan', { fill: accentCol }, t); s.textContent = accent; t.appendChild(document.createTextNode(text.slice(i + accent.length))); } else t.textContent = text;
  const fit = () => {
    const w = measure(text, size, 750, 'Bricolage', -size * 0.02), hw = w / 2 + size * 0.8, hh = size * 0.92;
    body.setAttribute('d', poly(cutEdge(roundRectPts(-hw, -hh, hw * 2, hh * 2, hh * 0.95, 6), 1.8, seed, 34)));
    const bx = clamp(tx * 0.45, -hw + hh, hw - hh), ang = Math.atan2(ty, tx - bx), nx = Math.cos(ang + Math.PI / 2) * 22, ny = Math.sin(ang + Math.PI / 2) * 22;
    tail.setAttribute('d', poly([[bx + nx, hh * 0.4 + ny], [tx, ty], [bx - nx, hh * 0.4 - ny]]));
  };
  fit(); REFIT.push(fit);
  return g;
}
// red rubber-stamp words, slammed on (call stamp(g, at) to land it)
function inkStamp(parent, text, x, y, size = 50, rot = -12, col = '#d23c2e', seed = 5) {
  const g = pup(parent, { x, y, r: rot });
  const w = text.length * size * 0.62 + size;
  cutRect(g, -w / 2, -size * 0.85, w, size * 1.7, 'none', seed, 8, 2, { stroke: col, 'stroke-width': size * 0.14 });
  txt(g, text, 0, size * 0.34, size, { fill: col, spacing: size * 0.06 });
  const r = rng(seed); for (let i = 0; i < 4; i++) el('circle', { cx: (r() - 0.5) * w * 1.2, cy: (r() - 0.5) * size * 2.4, r: 3 + r() * 3, fill: col, opacity: 0.8 }, g);
  return g;
}
// words that land on the beat one at a time, the one being said lit (captions); returns the strips
function captions(parent, words, times, x, y, size = 64, opts = {}) {
  const { lit = '#ffd23f', paper = '#fbf6ec', gap = 12 } = opts;
  if (times.length) { const lag = wordAt(times[0]) - times[0]; times = times.map((ct) => ct + lag); }
  const caps = words.map((w, i) => strip(parent, w, 0, y, size, { seed: 740 + i, fill: paper }));
  let xs = [];
  const layout = () => { const ws = caps.map((p) => p.__w()), tot = ws.reduce((a, b) => a + b, 0) + gap * (ws.length - 1); let cx = x - tot / 2; xs = ws.map((w) => { const c = cx + w / 2; cx += w + gap; return c; }); };
  layout(); REFIT.push(layout);
  LIVE.push((t) => {
    let act = -1; times.forEach((ct, i) => { if (t >= ct) act = i; });
    caps.forEach((p, i) => {
      const s = p.__s, u = clamp((t - times[i]) / 0.2);
      s.x = xs[i]; s.y = y - 16 * Math.sin(u * Math.PI); s.o = t < times[i] ? 0 : 1; s.sx = s.sy = lerp(0.55, 1, 1 - Math.pow(1 - u, 3));
      p.__paper.setAttribute('fill', i === act ? lit : paper);
      put(p);
    });
  });
  times.forEach((ct, i) => sfx(ct, 'pop', { pitch: i }));
  return caps;
}

// ---------------------------------------------------------------- figures: a pose is a function of time; one LIVE per figure
// sets every part from its rest pose plus the poses active now, so any frame is exact
function rig(fig, parts, rest) {
  fig.__parts = parts; fig.__poses = [];
  LIVE.push((t) => {
    rest(parts);
    for (const q of fig.__poses) if (t >= q.a && t <= q.b) q.fn(t - q.a, parts, t);
    for (const p of Object.values(parts)) if (p && p.__s) put(p);
  });
}
const pose = (fig, a, b, fn) => fig.__poses.push({ a, b, fn });
const SKIN = '#f2c09a', INK = '#2b2236';
// a person seen from the side, facing right, feet at 0,0 (about 205 px tall at s = 1); colours and hat can change
function person(parent, x, y, s = 1, o = {}) {
  const { jacket = '#ee6f57', jacketDark = '#d4553f', arm = '#c9503f', legs = '#4a4573', legsBack = '#3b3760', shoes = '#24203a', scarf = '#2f8f8a', hat = 'beanie', hatCol = '#ffb347', band = '#e5533d', hair = '#3b2420', skin = SKIN } = o;
  const g = pup(parent, { x, y, sx: s, sy: s }, { filter: shadow(2) });
  const body = pup(g);
  const limb = (px, py, d, col, w) => { const p = pup(body, { x: px, y: py }); el('path', { d, stroke: col, 'stroke-width': w, 'stroke-linecap': 'round', fill: 'none' }, p); return p; };
  const armB = limb(-6, -112, 'M 0 0 L 4 46', arm, 14);
  const legB = limb(-6, -58, 'M 0 0 L 0 52', legsBack, 17);
  el('ellipse', { cx: 8, cy: 56, rx: 15, ry: 7, fill: '#1f1b2e' }, legB);
  cutShape(body, [[-26, -126], [20, -126], [26, -60], [-24, -58]], jacket, 21, 1.4);
  cutShape(body, [[-26, -98], [26, -98], [26, -86], [-26, -86]], jacketDark, 22, 1, { opacity: 0.7, rim: false });
  const legF = limb(6, -58, 'M 0 0 L 0 52', legs, 17);
  el('ellipse', { cx: 10, cy: 56, rx: 16, ry: 7, fill: shoes }, legF);
  if (scarf) { cutShape(body, [[-22, -132], [22, -132], [24, -118], [-20, -114]], scarf, 23, 1.2); cutShape(body, [[-20, -122], [-6, -122], [-14, -92], [-24, -96]], scarf, 24, 1); }
  const head = pup(body, { x: 0, y: -160 });
  cutCircle(head, 0, 0, 30, skin, 25, 1.2);
  cutCircle(head, -14, 2, 7, '#e3a984', 26, 0.6, { rim: false });
  cutShape(head, [[-30, -6], [-26, -24], [-8, -32], [14, -30], [26, -18], [8, -14], [-14, -12], [-24, 6]], hair, 27, 1.2);
  if (hat === 'beanie') { cutShape(head, [[-32, -10], [-24, -34], [0, -42], [24, -34], [32, -12], [-32, -10]], hatCol, 28, 1.4); cutRect(head, -33, -16, 66, 10, band, 29, 3, 1); cutCircle(head, 2, -46, 9, band, 30, 0.8); }
  else if (hat === 'cap') { cutShape(head, [[-30, -8], [-24, -30], [4, -36], [26, -24], [30, -10]], hatCol, 28, 1.2); cutShape(head, [[24, -14], [50, -10], [48, -4], [22, -6]], band, 29, 0.8); }
  el('ellipse', { cx: 16, cy: -2, rx: 3.6, ry: 4.6, fill: INK }, head);
  el('circle', { cx: 13, cy: 10, r: 6, fill: '#f08f7a', opacity: 0.55 }, head);
  el('path', { d: 'M 28 -2 q 6 6 0 10', stroke: '#e3a984', 'stroke-width': 4, fill: 'none', 'stroke-linecap': 'round' }, head);
  const mouth = pup(head, { x: 22, y: 15 });
  el('ellipse', { cx: 0, cy: 0, rx: 4.5, ry: 3, fill: '#7a2e3a' }, mouth);
  const armF = limb(8, -112, 'M 0 0 L 4 46', jacket, 15);
  el('circle', { cx: 4, cy: 50, r: 8, fill: skin }, armF);
  rig(g, { body, head, armB, armF, legB, legF, mouth }, (P) => {
    for (const k of ['armB', 'armF', 'legB', 'legF']) P[k].__s.r = 0;
    P.body.__s.y = 0; P.body.__s.r = 0; P.head.__s.r = 0; P.mouth.__s.sy = 0.35;
  });
  return g;
}
// poses for person()
const P_WALK = (dt, P) => { const k = Math.floor(dt * 12) % 4, sw = [0, 1, 0, -1][k]; P.legF.__s.r = sw * 26; P.legB.__s.r = -sw * 26; P.armF.__s.r = -sw * 22; P.armB.__s.r = sw * 22; P.body.__s.y = sw ? -4 : 0; };
const P_TYPE = (dt, P) => { const k = Math.floor(dt * 12); P.armF.__s.r = -58 + (k % 2 ? 7 : -3); P.armB.__s.r = -52 + (k % 2 ? -4 : 6); P.head.__s.r = 12; };
const P_SLUMP = (dt, P) => { P.head.__s.r = 12 + 12 * ss(0, 0.6, dt); P.body.__s.y = 4 * ss(0, 0.6, dt); P.body.__s.r = 3 * ss(0, 0.6, dt); };
const P_TALK = (dt, P) => { const k = Math.floor(dt * 12); P.mouth.__s.sy = [1.4, 0.6, 1.8, 0.9, 1.2, 0.5][k % 6]; P.head.__s.r = -6 + (k % 3) * 1.5; };
const P_LOOKUP = (dt, P) => { P.head.__s.r = -14 * ss(0, 0.3, dt); };
const P_CHEER = (dt, P) => { const k = Math.floor(dt * 12) % 2; P.armF.__s.r = -150 + k * 10; P.armB.__s.r = -140 - k * 10; P.head.__s.r = -10; P.body.__s.y = k ? -6 : 0; P.mouth.__s.sy = 1.6; };
const P_POINT = (dt, P) => { P.armF.__s.r = -95 * ss(0, 0.25, dt); P.head.__s.r = -4; };
const P_WAVE = (dt, P) => { const k = Math.floor(dt * 12) % 4; P.armF.__s.r = -150 + [0, 18, 0, -18][k]; P.head.__s.r = -6; P.mouth.__s.sy = 1.4; };
// walks from (x0, y0) to (x1, y1), scaling from s0 to s1 (going away up a path), with its footsteps
function walk(fig, a, b, x0, x1, y0 = null, y1 = null, s0 = null, s1 = null) {
  const v0 = { x: x0 }, v1 = { x: x1 };
  if (y0 !== null) { v0.y = y0; v1.y = y1; }
  if (s0 !== null) { v0.sx = v0.sy = s0; v1.sx = v1.sy = s1; }
  set(fig, v0, a); to(fig, v1, a, b - a, 'none');
  pose(fig, a, b, P_WALK);
  sfx(a, 'steps', { until: b, every: 1 / 3 });
}
// someone talking to camera (front, head and shoulders; the chin at 0,0); g.__talk(t) says how open the mouth is (0..1)
function presenter(parent, x, y, s = 1, o = {}) {
  const { top = '#2f8f8a', topShade = '#26777a', hair = '#4a2c44', hairHi = '#6b4262', skin = SKIN, collar = '#fbf0dc' } = o;
  const g = pup(parent, { x, y, sx: s, sy: s });
  const back = el('g', { filter: shadow(2) }, g);
  cutShape(back, [[-150, -20], [-172, -170], [-112, -262], [0, -292], [112, -262], [172, -170], [150, -20]], hair, 51, 2);
  const torso = el('g', { filter: shadow(3) }, g);
  cutShape(torso, [[-340, 460], [-300, 150], [-200, 80], [-70, 50], [70, 50], [200, 80], [300, 150], [340, 460]], top, 52, 2.2);
  cutShape(torso, [[60, 50], [200, 80], [300, 150], [340, 460], [120, 460]], topShade, 53, 2, { opacity: 0.85, rim: false });
  cutShape(torso, [[-80, 46], [80, 46], [50, 110], [0, 130], [-50, 110]], collar, 54, 1.6);
  cutShape(torso, [[-46, -20], [46, -20], [52, 70], [-52, 70]], '#e2a983', 55, 1.4);
  const head = pup(g, { x: 0, y: -150 }, { filter: shadow(2) });
  cutEllipse(head, -124, 10, 22, 34, '#e9ad86', 56, 1);
  cutEllipse(head, 124, 10, 22, 34, '#e9ad86', 57, 1);
  cutEllipse(head, 0, 0, 122, 150, skin, 58, 1.8);
  cutShape(head, [[-128, -40], [-110, -120], [-40, -165], [40, -168], [112, -122], [128, -40], [90, -96], [20, -110], [-60, -96]], hair, 59, 1.8);
  el('path', { d: 'M -86 -118 Q -30 -156 46 -150', stroke: hairHi, 'stroke-width': 9, fill: 'none', 'stroke-linecap': 'round', opacity: 0.85 }, head);
  el('ellipse', { cx: -48, cy: 18, rx: 22, ry: 14, fill: '#f08f7a', opacity: 0.45 }, head);
  el('ellipse', { cx: 48, cy: 18, rx: 22, ry: 14, fill: '#f08f7a', opacity: 0.45 }, head);
  const eyes = pup(head, { x: 0, y: -24 });
  for (const ex of [-44, 44]) { el('ellipse', { cx: ex, cy: 0, rx: 13, ry: 17, fill: INK }, eyes); el('circle', { cx: ex + 5, cy: -6, r: 5, fill: '#fff' }, eyes); }
  for (const ex of [-44, 44]) el('path', { d: `M ${ex - 22} -54 q 22 -12 44 0`, stroke: hair, 'stroke-width': 8, fill: 'none', 'stroke-linecap': 'round' }, head);
  el('path', { d: 'M -4 0 q 10 18 -2 24', stroke: '#d9946f', 'stroke-width': 6, fill: 'none', 'stroke-linecap': 'round' }, head);
  const mouth = el('path', { fill: '#7a2e3a' }, head);
  g.__talk = () => 0;
  LIVE.push((t) => {
    eyes.__s.sy = (t % 3.3) < 0.1 ? 0.12 : 1; put(eyes);
    const open = g.__talk(t), k = step12(t), m = open > 0.05 ? (0.3 + 0.7 * Math.abs(Math.sin(k * 9.1))) * open : 0;
    mouth.setAttribute('d', m > 0.05 ? `M -30 62 Q 0 ${(62 + 40 * m).toFixed(1)} 30 62 Q 0 ${(58 - 6 * m).toFixed(1)} -30 62 Z` : 'M -30 62 Q 0 82 30 62 Q 0 74 -30 62 Z');
    head.__s.r = Math.sin(t * 1.3) * 1.5 + (m > 0.05 ? Math.sin(k * 3) * 1.2 : 0); put(head);
  });
  g.__head = head;
  return g;
}
// a fox, side view facing right, feet at 0,0; drawn in pencil lines when ink
function fox(parent, x, y, s = 1, ink = false, col = '#f07a2a') {
  const g = pup(parent, { x, y, sx: s, sy: s }, ink ? {} : { filter: shadow(2) });
  const O = ink ? 'none' : col, CR = ink ? 'none' : '#fbecd5';
  const st = ink ? { stroke: '#5a5560', 'stroke-width': 3, 'stroke-linejoin': 'round', rim: false } : {};
  const body = pup(g);
  const tail = pup(body, { x: -78, y: -70 });
  cutShape(tail, [[0, 0], [-60, -10], [-120, -50], [-140, -90], [-110, -80], [-70, -60], [-20, -34]], O, 61, 1.6, st);
  cutShape(tail, [[-120, -50], [-140, -90], [-112, -82]], CR, 62, 1, st);
  const legs = [];
  for (const [lx, back] of [[-50, 1], [-30, 1], [44, 0], [62, 0]]) { const l = pup(body, { x: lx, y: -46 }); el('path', { d: 'M 0 0 L 0 46', stroke: ink ? '#5a5560' : (back ? '#2a160d' : '#3a2216'), 'stroke-width': ink ? 3 : 13, 'stroke-linecap': 'round' }, l); legs.push(l); }
  cutEllipse(body, 0, -70, 92, 42, O, 63, 1.6, 0, st);
  cutEllipse(body, 18, -52, 52, 20, CR, 64, 1.2, 0, st);
  const head = pup(body, { x: 80, y: -98 });
  cutShape(head, [[-26, 10], [-10, -26], [30, -30], [70, -6], [74, 10], [30, 26], [-14, 28]], O, 65, 1.4, st);
  cutShape(head, [[20, 12], [72, 6], [74, 12], [30, 26]], CR, 66, 1, st);
  for (const ex of [-6, 14]) { cutShape(head, [[ex - 10, -18], [ex, -56], [ex + 12, -18]], O, 67 + ex, 1, st); if (!ink) cutShape(head, [[ex - 5, -20], [ex, -44], [ex + 6, -20]], '#5a2a12', 68 + ex, 0.6, { rim: false }); }
  if (ink) { el('circle', { cx: 34, cy: -6, r: 4, fill: '#5a5560' }, head); } else { el('circle', { cx: 34, cy: -6, r: 5, fill: INK }, head); el('circle', { cx: 74, cy: 7, r: 5, fill: INK }, head); }
  rig(g, { body, tail, head, l0: legs[0], l1: legs[1], l2: legs[2], l3: legs[3] }, (P) => {
    for (const k of ['l0', 'l1', 'l2', 'l3']) P[k].__s.r = 0;
    P.body.__s.y = 0; P.body.__s.sy = 1; P.body.__s.sx = 1; P.body.__s.r = 0; P.tail.__s.r = 0; P.head.__s.r = 0;
  });
  return g;
}
// the fox runs; jumps = [[t0, t1], ...] in film time
const F_RUN = (jumps = []) => (dt, P, t) => {
  const k = Math.floor(t * 12) % 4, sw = [1, 0, -1, 0][k];
  let lift = 0, squash = 1, tilt = 0;
  for (const [j0, j1] of jumps) { if (t >= j0 && t <= j1) { const u = (t - j0) / (j1 - j0); lift = Math.sin(u * Math.PI) * 130; squash = 1.06; tilt = lerp(-14, 14, u); } if (t > j1 && t < j1 + 0.17) squash = 0.84; }
  ['l0', 'l1', 'l2', 'l3'].forEach((n, i) => { P[n].__s.r = (i % 2 ? -1 : 1) * sw * (lift > 0 ? 44 : 30); });
  P.body.__s.y = -lift - (sw ? 5 : 0); P.body.__s.sy = squash; P.body.__s.sx = 1 / Math.sqrt(squash); P.body.__s.r = tilt; P.tail.__s.r = sw * 9;
};
// a glowing paper orb (Flicello's Cello: violet, rose, amber and cream discs), breathing, pulsing on the kick
function orb(parent, x, y, r = 60, cols = ['#8f5bff', '#b84bc4', '#ff2e7e', '#ff6a4d', '#ffb347', '#fff6e0'], glowCol = '#ffd08a') {
  const g = pup(parent, { x, y });
  const glow = pup(g);
  [[3.1, 0.05], [2.4, 0.08], [1.85, 0.12], [1.45, 0.2]].forEach(([k, o], i) => cutCircle(glow, 0, 0, r * k, i < 2 ? '#ffae6b' : glowCol, 80 + i, 2.5, { opacity: o, rim: false }));
  const bob = pup(g);
  const body = el('g', { filter: shadow(2) }, bob);
  const ks = [[1, 0], [0.86, 0.07], [0.71, 0.14], [0.54, 0.22], [0.38, 0.28], [0.18, 0.35]];
  cols.forEach((col, i) => { const [k, off] = ks[Math.min(i, ks.length - 1)]; const d = el('g', i ? { filter: shadow(1) } : {}, body); cutCircle(d, -r * off, -r * off * 1.1, r * k, col, 84 + i, 1.1); });
  LIVE.push((t) => {
    const p = 1 + 0.04 * Math.sin(t * 2.2) + 0.09 * kickAt(t);
    glow.__s.sx = glow.__s.sy = p; put(glow);
    bob.__s.y = Math.sin(t * 1.7) * 6; bob.__s.r = Math.sin(t * 1.1) * 3; put(bob);
  });
  g.__glow = glow;
  return g;
}
// a paper sunburst behind something (rays turning slowly); bloom it with popIn or a scale tween
function sunburst(parent, x, y, n = 24, len = 1500, cols = ['#ffe3b0', '#ffd08a'], op = [0.26, 0.18]) {
  const g = pup(parent, { x, y });
  for (let i = 0; i < n; i++) { const a = (i / n) * TAU, w = TAU / (n * 2); shape(g, [[Math.cos(a - w) * 70, Math.sin(a - w) * 70], [Math.cos(a - w * 0.8) * len, Math.sin(a - w * 0.8) * len], [Math.cos(a + w * 0.8) * len, Math.sin(a + w * 0.8) * len], [Math.cos(a + w) * 70, Math.sin(a + w) * 70]], cols[i % 2], { opacity: op[i % 2] }); }
  LIVE.push((t) => { g.__s.r = t * 5; put(g); });
  return g;
}

// ---------------------------------------------------------------- props
function gear(parent, x, y, r, teeth, col, speed, seed = 1) {
  const g = pup(parent, { x, y }, { filter: shadow(2) });
  const spin = pup(g), pts = [], n = teeth * 4;
  for (let i = 0; i < n; i++) { const a = (i / n) * TAU, rr = i % 4 < 2 ? r : r * 0.8; pts.push([Math.cos(a) * rr, Math.sin(a) * rr]); }
  shape(spin, pts, col, RIM);
  cutCircle(spin, 0, 0, r * 0.56, '#000', seed, 1, { opacity: 0.14, rim: false });
  cutCircle(spin, 0, 0, r * 0.2, '#fbf6ec', seed + 1, 0.8);
  for (let i = 0; i < 4; i++) { const a = (i / 4) * TAU; cutCircle(spin, Math.cos(a) * r * 0.38, Math.sin(a) * r * 0.38, r * 0.08, '#000', seed + 2 + i, 0.4, { opacity: 0.2, rim: false }); }
  LIVE.push((t) => { spin.__s.r = t * speed; put(spin); });
  return g;
}
// a clock whose hands run (fast = time flying) from `from` on
function clock(parent, x, y, r = 120, from = 0, speed = 420) {
  const g = pup(parent, { x, y }, { filter: shadow(2) });
  cutCircle(g, 0, 0, r * 1.03, '#2b2236', 201, 1.5);
  cutCircle(g, 0, 0, r * 0.9, '#fbf6ec', 202, 1.2);
  for (let i = 0; i < 12; i++) { const a = (i / 12) * TAU, r1 = i % 3 ? r * 0.75 : r * 0.67; el('line', { x1: (Math.cos(a) * r1).toFixed(1), y1: (Math.sin(a) * r1).toFixed(1), x2: (Math.cos(a) * r * 0.82).toFixed(1), y2: (Math.sin(a) * r * 0.82).toFixed(1), stroke: '#2b2236', 'stroke-width': i % 3 ? 4 : 8, 'stroke-linecap': 'round' }, g); }
  const hourH = pup(g), minH = pup(g);
  cutRect(hourH, -6, -r * 0.5, 12, r * 0.58, '#2b2236', 203, 5, 0.6);
  cutRect(minH, -4, -r * 0.77, 8, r * 0.87, '#e5533d', 204, 4, 0.5);
  cutCircle(g, 0, 0, 11, '#2b2236', 205, 0.5);
  LIVE.push((t) => { const u = Math.max(0, t - from); minH.__s.r = u * speed; hourH.__s.r = 30 + u * speed / 12; put(minH); put(hourH); });
  return g;
}
// a camera on a tripod, facing right, feet at 0,0
function tripod(parent, x, y, s = 1) {
  const g = pup(parent, { x, y, sx: s, sy: s }, { filter: shadow(2) });
  for (const x2 of [-34, 0, 34]) el('line', { x1: 0, y1: -70, x2, y2: 0, stroke: '#3b3760', 'stroke-width': 6, 'stroke-linecap': 'round' }, g);
  cutRect(g, -36, -118, 66, 46, '#2b2236', 41, 8, 1);
  cutCircle(g, 40, -95, 18, '#4d6b8a', 42, 0.8);
  cutCircle(g, 44, -99, 6, '#bfe3ff', 43, 0.5);
  cutRect(g, -20, -132, 30, 14, '#2b2236', 44, 4, 0.8);
  const rec = el('circle', { cx: -24, cy: -108, r: 5, fill: '#ff4a4a' }, g);
  LIVE.push((t) => rec.setAttribute('opacity', Math.floor(t * 2) % 2 ? '0.25' : '1'));
  return g;
}
// a paper pair of scissors; the blades close on each time in snips
function scissors(parent, x, y, s = 1, snips = [], r0 = 0) {
  const g = pup(parent, { x, y, sx: s, sy: s, r: r0 }, { filter: shadow(2) });
  const A = pup(g), Bh = pup(g);
  for (const [half, sgn] of [[A, 1], [Bh, -1]]) {
    cutShape(half, [[0, -5 * sgn], [118, -16 * sgn], [126, -9 * sgn], [6, 7 * sgn]], '#dfe4ea', 70 + sgn, 1);
    shape(half, [[10, 2 * sgn], [118, -12 * sgn], [122, -10 * sgn], [10, 6 * sgn]], '#aab2bd', { opacity: 0.7 });
    cutShape(half, [[0, -6 * sgn], [-30, 10 * sgn], [-42, 16 * sgn], [-12, 4 * sgn]], '#c63d2f', 72 + sgn, 1);
    cutEllipse(half, -66, 24 * sgn, 30, 19, '#e5533d', 74 + sgn, 1, 0.35 * sgn);
    cutEllipse(half, -66, 24 * sgn, 17, 9, '#7a2a20', 76 + sgn, 0.6, 0.35 * sgn, { rim: false });
  }
  el('circle', { cx: 0, cy: 0, r: 7, fill: '#6b6f84' }, g);
  el('circle', { cx: 0, cy: 0, r: 3, fill: '#d9dee6' }, g);
  LIVE.push((t) => {
    let a = 16;
    for (const ts of snips) { if (t > ts - 0.09 && t <= ts) a = Math.min(a, 16 * (ts - t) / 0.09); else if (t > ts && t < ts + 0.2) a = Math.min(a, 16 * (t - ts) / 0.2); }
    A.__s.r = -a; Bh.__s.r = a; put(A); put(Bh);
  });
  snips.forEach((ts) => sfx(ts, 'snip'));
  return g;
}
// confetti of paper bits from (x, y) at time at
function burst(parent, x, y, at, n = 10, cols = ['#ffd36b', '#ff6a5c', '#8f5bff', '#2f8f8a', '#fbf6ec'], dist = 160, seed = 1, dur = 0.8) {
  const r = rng(seed), out = [];
  for (let i = 0; i < n; i++) {
    const b = pup(parent, { x, y }, {}), a = (i / n) * TAU + r() * 0.6, d = dist * (0.6 + r() * 0.6), w = 8 + r() * 12;
    if (r() < 0.5) cutRect(b, -w / 2, -w * 0.3, w, w * 0.6, cols[i % cols.length], seed + i, 1, 0.6, { rim: false }); else shape(b, [[-w / 2, w * 0.4], [0, -w * 0.5], [w / 2, w * 0.4]], cols[i % cols.length]);
    hide(b); set(b, { o: 1 }, at);
    to(b, { x: x + Math.cos(a) * d, y: y + Math.sin(a) * d + 40, r: (r() - 0.5) * 540 }, at, dur, 'power3.out');
    to(b, { o: 0 }, at + dur * 0.55, dur * 0.45, 'power1.in');
    out.push(b);
  }
  sfx(at, 'spark', { gain: -8 });
  return out;
}
// a full-page paper sheet that sweeps across: a page turn; the scenes swap at `at`, when it covers the page
function pageTurn(parent, at, dur = 0.8, col = '#f3e9d8', dir = 1) {
  const SW = W * 1.35 + 100;
  const x0 = dir > 0 ? W + 60 : -SW - 60, x1 = dir > 0 ? -SW - 100 : W + 100;
  const g = pup(parent, { x: x0 }, { filter: shadow(3) });
  shape(g, cutEdge([[0, -80], [SW, -80], [SW, H + 80], [0, H + 80]], 5, 91, 60), col);
  for (let i = 0; i < 9; i++) el('line', { x1: SW * 0.12 + i * SW * 0.09, y1: -80, x2: SW * 0.12 + i * SW * 0.09 + 40, y2: H + 80, stroke: '#000', 'stroke-opacity': 0.025, 'stroke-width': 60 }, g);
  el('rect', { x: dir > 0 ? 0 : SW - 160, y: -80, width: 160, height: H + 160, fill: dir > 0 ? 'url(#curlL)' : 'url(#curlR)' }, g);
  hide(g); set(g, { o: 1 }, at - dur / 2);
  to(g, { x: x1 }, at - dur / 2, dur, 'power2.inOut');
  set(g, { o: 0 }, at + dur / 2 + 0.01);
  sfx(at - dur / 2, 'turn', { dur });
  return g;
}
// a flash of light over everything
function flash(parent, at, col = '#fff6e0', peak = 0.9, up = 0.06, down = 0.6) {
  const g = pup(parent, { o: 0 });
  el('rect', { x: -20, y: -20, width: W + 40, height: H + 40, fill: col }, g);
  set(g, { o: 0 }, 0); to(g, { o: peak }, at - up, up, 'power1.in'); to(g, { o: 0 }, at, down, 'power2.out');
  sfx(at - up, 'shimmer', { dur: up + 0.3, gain: -4 });
  return g;
}
let CLIPN = 0;
// a group clipped to a rectangle; g.__rect is the rectangle (its size can be tweened)
function clipRect(parent, x, y, w, h, r = 0) {
  const id = `clp${CLIPN++}`;
  const cp = el('clipPath', { id }, document.querySelector('#stage defs'));
  const rect = el('rect', { x, y, width: w, height: h, rx: r }, cp);
  const g = el('g', { 'clip-path': `url(#${id})` }, parent);
  g.__rect = rect;
  return g;
}
// a little paper landscape filling (-w/2, -h/2, w, h): beach, peaks, city, wave, forest, desert, night, lake, mars
function mini(parent, kind, w, h, seed = 1) {
  const g = el('g', {}, parent), x0 = -w / 2, y0 = -h / 2, a = Math.max(0.5, h / 150), r = rng(seed);
  const X = (u) => x0 + w * u, Y = (u) => y0 + h * u;
  const band = (u, col, amp = 0.03, f = 3, ph = 0, sd = 0) => {
    const pts = [[x0 - 6, y0 + h + 6]];
    for (let i = 0; i <= 28; i++) { const v = i / 28; pts.push([X(v) + (i === 0 ? -6 : i === 28 ? 6 : 0), Y(u) + h * amp * Math.sin(v * f * Math.PI + ph)]); }
    pts.push([x0 + w + 6, y0 + h + 6]);
    return cutShape(g, pts, col, seed + sd, a);
  };
  const sky = (c1, c2, c3) => { shape(g, [[x0 - 6, y0 - 6], [x0 + w + 6, y0 - 6], [x0 + w + 6, y0 + h + 6], [x0 - 6, y0 + h + 6]], c1); band(0.3, c2, 0.02, 2, 1, 1); if (c3) band(0.46, c3, 0.02, 2.4, 2, 2); };
  const disc = (u, v, k, col, extra = {}) => cutCircle(g, X(u), Y(v), h * k, col, seed + 9, a * 0.8, extra);
  const peaks = (base, list, col, snow) => { for (const [u, hk, wk] of list) { const px = X(u), py = Y(base - hk), bw = w * wk; cutShape(g, [[px - bw, Y(base) + 4], [px, py], [px + bw, Y(base) + 4]], col, seed + Math.round(u * 100), a); if (snow) cutShape(g, [[px - bw * 0.22, py + h * hk * 0.22], [px, py], [px + bw * 0.22, py + h * hk * 0.22], [px + bw * 0.06, py + h * hk * 0.16], [px - bw * 0.08, py + h * hk * 0.26]], snow, seed + 50 + Math.round(u * 100), a * 0.6); } };
  if (kind === 'beach') {
    sky('#f6a77e', '#f9b98e', '#fbcaa0'); disc(0.68, 0.5, 0.26, '#ffe39a', { opacity: 0.45, rim: false }); disc(0.68, 0.5, 0.15, '#ffd36b');
    band(0.6, '#3a7ca5', 0, 1); band(0.68, '#4b8db6', 0.01, 3, 1, 3); band(0.76, '#3a7ca5', 0.01, 4, 2, 4); band(0.82, '#f3d9a4', 0.05, 2, 0.5, 5);
  } else if (kind === 'peaks') {
    sky('#bfe3ff', '#d2ecff', '#e2f3ff'); peaks(0.78, [[0.2, 0.42, 0.22], [0.5, 0.55, 0.26], [0.82, 0.4, 0.22]], '#8fb0d4', '#ffffff'); peaks(0.86, [[0.34, 0.36, 0.24], [0.7, 0.3, 0.22]], '#5c7fa8', '#eef6ff'); band(0.86, '#4a6b8f', 0.02, 2, 1, 6);
  } else if (kind === 'city') {
    sky('#ffb38a', '#ffc39c', '#ffd2ad'); disc(0.3, 0.58, 0.18, '#ffe39a');
    for (let i = 0; i < 9; i++) { const bw = w * (0.08 + r() * 0.06), bx = X(i / 9) + (r() - 0.5) * 8, bh = h * (0.25 + r() * 0.4); cutRect(g, bx, Y(1) - bh, bw, bh + 6, i % 2 ? '#7b4a72' : '#6b3a63', seed + i, 2, a * 0.6); }
    for (let i = 0; i < 8; i++) { const bw = w * (0.1 + r() * 0.06), bx = X(i / 8) + (r() - 0.5) * 8, bh = h * (0.15 + r() * 0.3); cutRect(g, bx, Y(1) - bh, bw, bh + 6, '#4b3466', seed + 20 + i, 2, a * 0.6); for (let k = 0; k < 3; k++) if (r() < 0.6) cutRect(g, bx + bw * 0.25, Y(1) - bh + h * 0.05 + k * h * 0.07, bw * 0.18, h * 0.035, '#ffd36b', seed + 40 + i * 3 + k, 1, 0.3, { rim: false }); }
  } else if (kind === 'wave') {
    sky('#9be3d4', '#b5ebe0', '#ccf2ea');
    cutShape(g, [[X(-0.05), Y(1.05)], [X(-0.05), Y(0.62)], [X(0.25), Y(0.55)], [X(0.52), Y(0.3)], [X(0.7), Y(0.26)], [X(0.84), Y(0.34)], [X(0.8), Y(0.44)], [X(0.68), Y(0.4)], [X(0.62), Y(0.5)], [X(0.7), Y(0.7)], [X(1.05), Y(0.78)], [X(1.05), Y(1.05)]], '#2f8f8a', seed + 1, a);
    cutShape(g, [[X(0.52), Y(0.3)], [X(0.7), Y(0.26)], [X(0.84), Y(0.34)], [X(0.8), Y(0.44)], [X(0.72), Y(0.34)], [X(0.6), Y(0.36)]], '#ffffff', seed + 2, a * 0.6);
    band(0.78, '#5fc0b0', 0.02, 3, 0, 3); band(0.88, '#2f8f8a', 0.02, 4, 1, 4);
  } else if (kind === 'forest') {
    sky('#cfe8b4', '#dcefc5', '#e7f5d8'); disc(0.78, 0.26, 0.1, '#fff4c2'); band(0.6, '#9cc59a', 0.04, 2, 1, 1); band(0.72, '#7fb77e', 0.04, 3, 2, 2);
    for (let i = 0; i < 7; i++) { const px = X(0.06 + i * 0.15 + (r() - 0.5) * 0.05), ph = h * (0.26 + r() * 0.16), py = Y(0.84); cutShape(g, [[px, py - ph], [px + ph * 0.3, py], [px - ph * 0.3, py]], i % 2 ? '#3f8058' : '#2f6b4f', seed + 10 + i, a * 0.6); }
    band(0.86, '#4f8a4c', 0.02, 2, 0, 3);
  } else if (kind === 'desert') {
    sky('#ffd7a0', '#ffe1b4', '#ffe9c6'); disc(0.26, 0.32, 0.12, '#fff0b8'); band(0.64, '#e8b06a', 0.06, 2, 0.4, 1); band(0.78, '#d18f4a', 0.06, 1.6, 2, 2);
    const cx = X(0.7), cy = Y(0.8), ch = h * 0.34, cw = w * 0.022;
    cutRect(g, cx - cw, cy - ch, cw * 2, ch, '#4f9566', seed + 3, cw, a * 0.5); cutRect(g, cx - cw * 4, cy - ch * 0.7, cw * 1.6, ch * 0.34, '#4f9566', seed + 4, cw, a * 0.4); cutRect(g, cx + cw * 2.4, cy - ch * 0.85, cw * 1.6, ch * 0.4, '#4f9566', seed + 5, cw, a * 0.4);
    band(0.9, '#b9773a', 0.03, 2, 1, 6);
  } else if (kind === 'night') {
    sky('#2d2a4a', '#36305a', '#403668'); for (let i = 0; i < 14; i++) star(g, X(r()), Y(r() * 0.5), h * 0.018, '#fbe9d0', i); disc(0.72, 0.28, 0.12, '#f6e7c1');
    band(0.68, '#4a2d66', 0.05, 2, 0.6, 1); band(0.8, '#33204f', 0.04, 3, 2, 2);
  } else if (kind === 'lake') {
    sky('#ffcf9e', '#ffdcb4', '#ffe7c8'); disc(0.64, 0.4, 0.13, '#fff0b8');
    peaks(0.62, [[0.3, 0.3, 0.2], [0.55, 0.38, 0.22], [0.82, 0.28, 0.2]], '#8a7ab8', null);
    band(0.62, '#4f86b8', 0, 1, 0, 1); band(0.72, '#5f96c6', 0.01, 3, 1, 2); band(0.84, '#4f86b8', 0.01, 4, 2, 3);
    cutShape(g, [[X(-0.05), Y(1.05)], [X(-0.05), Y(0.48)], [X(0.12), Y(0.46)], [X(0.22), Y(0.56)], [X(0.26), Y(1.05)]], '#5a4a6e', seed + 5, a);
  } else if (kind === 'mars') {
    sky('#f2a07b', '#f5b08a', '#f8c19c'); disc(0.74, 0.24, 0.13, '#fbe3c4'); disc(0.88, 0.4, 0.06, '#bcd7f0');
    for (let i = 0; i < 10; i++) star(g, X(r()), Y(r() * 0.35), h * 0.012, '#fff6e0', i, { opacity: 0.8 });
    band(0.6, '#c2462e', 0.08, 3, 0.3, 1); band(0.74, '#a3361f', 0.06, 2.5, 1.4, 2); band(0.88, '#7a2617', 0.04, 2, 2.2, 3);
  }
  return g;
}
// a little paper landscape in a frame of its own size, centred at (x, y)
function picture(parent, kind, x, y, w, h, seed = 1, border = '#fbf6ec') {
  const g = pup(parent, { x, y }, { filter: shadow(2) });
  if (border) cutRect(g, -w / 2 - 12, -h / 2 - 12, w + 24, h + 24, border, seed, 4, 1.2);
  const c = clipRect(g, -w / 2, -h / 2, w, h, 4);
  mini(c, kind, w, h, seed);
  return g;
}
// small paper icons, about 70 px: shapes, cube, frames, brush, notes, burst, mic, snip, lock, key, gpu, laptop, ram, check
function icon(parent, kind, x, y, s = 1) {
  const ic = pup(parent, { x, y, sx: s, sy: s });
  if (kind === 'shapes') { cutCircle(ic, -10, 6, 22, '#ffd36b', 1760, 0.6); cutRect(ic, 2, -22, 34, 34, '#fbf6ec', 1761, 3, 0.6); cutShape(ic, [[-26, 30], [-4, -6], [18, 30]], '#2b2236', 1762, 0.5); }
  if (kind === 'cube') { shape(ic, [[0, -30], [30, -14], [0, 2], [-30, -14]], '#fff0c8'); shape(ic, [[-30, -14], [0, 2], [0, 34], [-30, 18]], '#c9821f'); shape(ic, [[30, -14], [0, 2], [0, 34], [30, 18]], '#a86a14'); }
  if (kind === 'frames') for (let k = 0; k < 3; k++) { cutRect(ic, -30 + k * 10, -26 + k * 12, 44, 32, '#fbf6ec', 1763 + k, 2, 0.5); cutRect(ic, -24 + k * 10, -20 + k * 12, 32, 20, k === 2 ? '#f07a2a' : '#3f8058', 1766 + k, 1, 0.3, { rim: false }); }
  if (kind === 'brush') { cutRect(ic, -6, -34, 12, 40, '#8a5a34', 1769, 3, 0.4); cutRect(ic, -8, 4, 16, 10, '#d9dee6', 1770, 2, 0.3); cutShape(ic, [[-8, 14], [8, 14], [0, 34]], '#ff6a8a', 1771, 0.4); cutCircle(ic, 22, 26, 9, '#ff6a8a', 1772, 0.4); }
  if (kind === 'notes') { cutEllipse(ic, -18, 22, 12, 9, '#fbf6ec', 1773, 0.4, -0.3); cutEllipse(ic, 18, 16, 12, 9, '#fbf6ec', 1774, 0.4, -0.3); cutRect(ic, -9, -26, 6, 48, '#fbf6ec', 1775, 2, 0.3); cutRect(ic, 27, -32, 6, 48, '#fbf6ec', 1776, 2, 0.3); cutShape(ic, [[-9, -26], [33, -32], [33, -22], [-9, -16]], '#fbf6ec', 1777, 0.3); }
  if (kind === 'burst') { const pts = []; for (let k = 0; k < 16; k++) { const a = (k / 16) * TAU, rr = k % 2 ? 16 : 34; pts.push([Math.cos(a) * rr, Math.sin(a) * rr]); } shape(ic, pts, '#ffd36b'); cutCircle(ic, 0, 0, 9, '#c45bb5', 1778, 0.3, { rim: false }); }
  if (kind === 'mic') { cutRect(ic, -14, -34, 28, 46, '#2b2236', 1779, 14, 0.5); for (let k = 0; k < 3; k++) el('line', { x1: -8, y1: -24 + k * 10, x2: 8, y2: -24 + k * 10, stroke: '#7fd6cf', 'stroke-width': 2 }, ic); el('path', { d: 'M -22 4 a 22 22 0 0 0 44 0 M 0 26 L 0 36 M -12 36 L 12 36', stroke: '#fbf6ec', 'stroke-width': 4, fill: 'none', 'stroke-linecap': 'round' }, ic); }
  if (kind === 'snip') { cutEllipse(ic, -18, 18, 12, 9, 'none', 1780, 0.3, 0.5, { stroke: '#fbf6ec', 'stroke-width': 5 }); cutEllipse(ic, 18, 18, 12, 9, 'none', 1781, 0.3, -0.5, { stroke: '#fbf6ec', 'stroke-width': 5 }); el('path', { d: 'M -10 10 L 20 -32 M 10 10 L -20 -32', stroke: '#fbf6ec', 'stroke-width': 5, 'stroke-linecap': 'round' }, ic); }
  if (kind === 'lock') { el('path', { d: 'M -18 0 L -18 -14 A 18 18 0 0 1 18 -14 L 18 0', stroke: '#d9dee6', 'stroke-width': 8, fill: 'none', 'stroke-linecap': 'round' }, ic); cutRect(ic, -28, -2, 56, 44, '#ffb347', 1782, 8, 0.6); cutCircle(ic, 0, 16, 6, '#7a4a14', 1783, 0.3); }
  if (kind === 'key') { cutCircle(ic, -18, 0, 16, '#ffd36b', 1784, 0.5); cutCircle(ic, -18, 0, 6, '#7a4a14', 1785, 0.3, { rim: false }); cutRect(ic, -4, -5, 40, 10, '#ffd36b', 1786, 3, 0.4); cutRect(ic, 22, 4, 6, 12, '#ffd36b', 1787, 1, 0.3); cutRect(ic, 30, 4, 6, 10, '#ffd36b', 1788, 1, 0.3); }
  if (kind === 'gpu') { cutRect(ic, -34, -22, 68, 40, '#2b2236', 1789, 4, 0.6); cutCircle(ic, -16, -2, 11, '#6b6f84', 1790, 0.4); cutCircle(ic, 14, -2, 11, '#6b6f84', 1791, 0.4); for (let k = 0; k < 5; k++) el('rect', { x: -28 + k * 11, y: 18, width: 6, height: 8, fill: '#e9a23b' }, ic); }
  if (kind === 'laptop') { cutRect(ic, -28, -28, 56, 36, '#2b2236', 1792, 3, 0.5); cutRect(ic, -22, -23, 44, 26, '#7fe8ff', 1793, 2, 0.3, { rim: false, opacity: 0.6 }); cutRect(ic, -38, 8, 76, 8, '#4a4868', 1794, 3, 0.4); }
  if (kind === 'ram') { cutRect(ic, -34, -14, 68, 30, '#2f8f8a', 1795, 3, 0.5); for (let k = 0; k < 3; k++) cutRect(ic, -26 + k * 21, -8, 15, 16, '#1f1d2b', 1796 + k, 1, 0.3, { rim: false }); for (let k = 0; k < 7; k++) el('rect', { x: -30 + k * 9, y: 16, width: 5, height: 7, fill: '#e9a23b' }, ic); }
  if (kind === 'check') { cutCircle(ic, 0, 0, 26, '#3ddc84', 1799, 0.6); el('path', { d: 'M -11 1 L -3 9 L 12 -8', stroke: '#0e3a22', 'stroke-width': 5, fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, ic); }
  return ic;
}
// software made of paper, for films about editing or apps: a timeline, a menu, plug-ins, a render that never ends,
// a dialog box, a warning, a sticky note, a file name, a keyframe, a loading wheel (each centred at x, y unless said)
const CLIPCOLS = ['#ee6f57', '#2f8f8a', '#ffb347', '#8f5bff', '#e5533d', '#4a90c2', '#c45bb5'];
const ui = {
  timeline(parent, cx, base, w = 300, floors = 10, seed = 701) {
    const g = pl(parent, base, 2), c = g.__in, r = rng(seed), x0 = cx - w / 2, top = base - floors * 44 - 34;
    cutRect(c, x0 - 12, top, w + 24, base - top + 60, '#262438', seed, 6, 2);
    cutRect(c, x0, top + 8, w, 20, '#3a3852', seed + 1, 3, 1, { rim: false });
    for (let x = x0 + 8, k = 0; x < x0 + w; x += 16, k++) el('line', { x1: x, y1: top + 12, x2: x, y2: top + (k % 4 ? 18 : 25), stroke: '#a9a3c0', 'stroke-width': 2 }, c);
    for (let f = 0; f < floors; f++) { const y = top + 34 + f * 44; let x = x0 + 4; while (x < x0 + w - 24) { const cw = Math.min(28 + r() * 92, x0 + w - 4 - x); cutRect(c, x, y + 4, cw, 34, CLIPCOLS[Math.floor(r() * CLIPCOLS.length)], seed + 10 + f * 13 + Math.round(x), 4, 1); cutRect(c, x + 3, y + 7, cw - 6, 7, '#fbf6ec', seed + 11 + f, 2, 0.3, { rim: false, opacity: 0.35 }); x += cw + 5; } }
    const px = cx + w * 0.12;
    cutRect(c, px - 2.5, top + 4, 5, base - top, '#ff3b3b', seed + 99, 2, 0.4, { rim: false });
    el('path', { d: `M ${px - 11} ${top} L ${px + 11} ${top} L ${px} ${top + 15} Z`, fill: '#ff3b3b' }, c);
    return g;
  },
  menu(parent, cx, top, w = 250, rows = 14, lit = 5, seed = 720) {
    const g = pup(parent, { x: cx, y: top }, { filter: shadow(2) }), r = rng(seed), x0 = -w / 2, h = 54 + rows * 34;
    cutRect(g, x0, 0, w, h, '#efe8dc', seed, 10, 1.6); cutRect(g, x0, 0, w, 40, '#d9d0c2', seed + 1, 10, 1, { rim: false });
    [['#ff5f57', 0], ['#febc2e', 1], ['#28c840', 2]].forEach(([col, i]) => el('circle', { cx: x0 + 22 + i * 20, cy: 20, r: 6, fill: col }, g));
    for (let i = 0; i < rows; i++) { const y = 54 + i * 34, on = i === lit; if (on) cutRect(g, x0 + 8, y - 5, w - 16, 30, '#4a7bd8', seed + 2, 4, 0.8, { rim: false }); cutRect(g, x0 + 22, y + 5, (w - 80) * (0.35 + r() * 0.6), 10, on ? '#ffffff' : '#b9b0a3', seed + 10 + i, 3, 0.5, { rim: false }); if (r() < 0.45 || on) el('path', { d: `M ${x0 + w - 26} ${y + 3} l 8 7 l -8 7 Z`, fill: on ? '#fff' : '#9a9184' }, g); }
    return g;
  },
  plugins(parent, cx, base, seed = 760) {
    const g = pl(parent, base, 2), c = g.__in, r = rng(seed);
    ['#3d6b8a', '#8a3d6b', '#5f7d3a'].forEach((col, i) => {
      const bw = 280 - i * 20, bh = 140, x0 = cx - bw / 2 + (i - 1) * 14, y0 = base - (i + 1) * (bh + 12);
      cutRect(c, x0, y0, bw, bh, col, seed + i, 8, 1.6); cutRect(c, x0, y0, bw, 30, '#1f1d2b', seed + 5 + i, 8, 1, { rim: false, opacity: 0.55 });
      for (let k = 0; k < 3; k++) { const kx = x0 + 40 + k * 60, ky = y0 + 80; cutCircle(c, kx, ky, 22, '#1f1d2b', seed + 10 + k + i * 9, 0.8); cutCircle(c, kx, ky, 16, '#e9e2d4', seed + 13 + k + i * 9, 0.6); const a = r() * TAU; el('line', { x1: kx, y1: ky, x2: (kx + Math.cos(a) * 13).toFixed(1), y2: (ky + Math.sin(a) * 13).toFixed(1), stroke: '#1f1d2b', 'stroke-width': 4, 'stroke-linecap': 'round' }, c); }
      for (let k = 0; k < 2; k++) { const sx = x0 + bw - 52 + k * 26; el('line', { x1: sx, y1: y0 + 44, x2: sx, y2: y0 + bh - 14, stroke: '#1f1d2b', 'stroke-width': 6, 'stroke-linecap': 'round' }, c); cutRect(c, sx - 10, y0 + 50 + r() * 50, 20, 14, '#ffd36b', seed + 30 + k + i * 2, 3, 0.5); }
    });
    return g;
  },
  render(parent, cx, cy, pct = '3%', left = '6 h left', seed = 802) {
    const g = pup(parent, { x: cx, y: cy }, { filter: shadow(2) });
    cutRect(g, -150, -98, 300, 196, '#1f1d2b', seed, 10, 1.8);
    txt(g, 'Rendering…', -124, -46, 30, { font: 'Geist Mono', weight: 400, fill: '#d9d0c2', anchor: 'start', spacing: 0 });
    cutRect(g, -124, -24, 248, 20, '#3a3850', seed + 1, 6, 0.6, { rim: false }); cutRect(g, -124, -24, 10, 20, '#ff5a4a', seed + 2, 4, 0.4, { rim: false });
    g.__pct = txt(g, pct, -124, 68, 66, { fill: '#fbf6ec', anchor: 'start' });
    g.__left = txt(g, left, 124, 64, 26, { font: 'Geist Mono', weight: 400, fill: '#a9a3c0', anchor: 'end', spacing: 0 });
    return g;
  },
  dialog(parent, x, y, label, seed = 2100) {
    const p = pup(parent, { x, y }, { filter: shadow(2) });
    cutRect(p, -150, -80, 300, 160, '#efe8dc', seed, 8, 1.2); cutRect(p, -150, -80, 300, 34, '#d9d0c2', seed + 1, 8, 0.8, { rim: false });
    txt(p, label, -136, -56, 20, { font: 'Geist Mono', weight: 400, fill: '#2b2236', anchor: 'start', spacing: 0 });
    for (let k = 0; k < 3; k++) cutRect(p, -130, -28 + k * 24, 140 + k * 30, 9, '#b9b0a3', seed + 2 + k, 2, 0.3, { rim: false });
    cutRect(p, 40, 44, 90, 26, '#4a7bd8', seed + 5, 6, 0.4); cutRect(p, -60, 44, 90, 26, '#d9d0c2', seed + 6, 6, 0.4);
    return p;
  },
  warn(parent, x, y, label, seed = 2107) {
    const p = pup(parent, { x, y }, { filter: shadow(2) });
    cutRect(p, -170, -50, 340, 100, '#fff0c8', seed, 8, 1.2); cutShape(p, [[-140, 26], [-114, -24], [-88, 26]], '#ffb347', seed + 1, 0.6);
    txt(p, '!', -114, 20, 34, { fill: '#2b2236' }); txt(p, label, -72, 10, 28, { fill: '#2b2236', anchor: 'start' });
    return p;
  },
  sticky(parent, x, y, col = '#ffe27a', seed = 820) {
    const p = pup(parent, { x, y }, { filter: shadow(1) });
    cutRect(p, -42, -42, 84, 84, col, seed, 3, 1);
    for (let k = 0; k < 3; k++) el('path', { d: `M -26 ${-18 + k * 15} q 12 -6 24 0 t 24 0`, stroke: '#b59a3a', 'stroke-width': 3, fill: 'none', 'stroke-linecap': 'round' }, p);
    return p;
  },
  file(parent, x, y, name, seed = 840) {
    const p = pup(parent, { x, y }, { filter: shadow(1) });
    const w = Math.max(220, name.length * 15 + 50);
    cutShape(p, [[-w / 2, -30], [w / 2 - 22, -30], [w / 2, -10], [w / 2, 30], [-w / 2, 30]], '#fbf6ec', seed, 1.2); shape(p, [[w / 2 - 22, -30], [w / 2, -10], [w / 2 - 22, -10]], '#d9d0c2');
    txt(p, name, -w / 2 + 16, 9, 24, { font: 'Geist Mono', weight: 400, fill: '#2b2236', anchor: 'start', spacing: 0 });
    return p;
  },
  keyframe(parent, x, y, col = '#ffd36b', seed = 830) {
    const p = pup(parent, { x, y }, { filter: shadow(1) });
    cutShape(p, [[0, -24], [24, 0], [0, 24], [-24, 0]], col, seed, 0.8); cutShape(p, [[0, -12], [12, 0], [0, 12], [-12, 0]], '#fbf6ec', seed + 1, 0.4, { opacity: 0.5, rim: false });
    return p;
  },
  wheel(parent, x, y, r = 34) {
    const p = pup(parent, { x, y }, { filter: shadow(1) }), spin = pup(p);
    for (let k = 0; k < 8; k++) { const a0 = (k / 8) * TAU, a1 = ((k + 1) / 8) * TAU; shape(spin, [[0, 0], [Math.cos(a0) * r, Math.sin(a0) * r], [Math.cos((a0 + a1) / 2) * (r + 1), Math.sin((a0 + a1) / 2) * (r + 1)], [Math.cos(a1) * r, Math.sin(a1) * r]], CLIPCOLS[k % CLIPCOLS.length]); }
    cutCircle(p, 0, 0, r * 0.26, '#fbf6ec', 850, 0.5);
    LIVE.push((t) => { spin.__s.r = t * 300; put(spin); });
    return p;
  },
};
// falls from above the page and lands at (x, ground) on `at`, with a little bounce
function drop(g, at, ground, rot = 0, fall = 0.62) {
  set(g, { o: 0 }, 0); set(g, { o: 1 }, at - fall);
  tl.fromTo(g.__s, { y: -160, r: rot - 120 }, { y: ground, r: rot, duration: fall, ease: 'power2.in', onUpdate: upd(g) }, at - fall);
  to(g, { y: ground - 18 }, at, 0.12, 'power2.out'); to(g, { y: ground }, at + 0.12, 0.16, 'power2.in');
  sfx(at, 'flick', { gain: -4 });
}
// a labelled box (a module, a feature, a tool): its colour, an icon, a name on a cream plate, a line under it
function labelBox(parent, cx, cy, w, h, col, name, sub = '', iconKind = null, seed = 1740) {
  const m = pup(parent, { x: cx, y: cy }, { filter: shadow(2) });
  cutRect(m, -w / 2, -h / 2, w, h, col, seed, 10, 1.6);
  cutRect(m, -w / 2, h / 2 - 23, w, 23, '#000', seed + 1, 6, 0.8, { rim: false, opacity: 0.18 });
  const left = iconKind ? -w / 2 + 92 : -w / 2 + 16, plateW = w / 2 - 16 - left;
  if (iconKind) icon(m, iconKind, -w / 2 + 47, -h * 0.12, 1);
  cutRect(m, left, -h / 2 + 23, plateW, 58, '#fbf6ec', seed + 2, 6, 0.8);
  const nm = txt(m, name, left + plateW / 2, -h / 2 + 62, 30, { fill: '#2b2236' });
  if (sub) txt(m, sub.toUpperCase(), left + plateW / 2, h / 2 - 39, 18, { font: 'Geist Mono', weight: 400, fill: '#fbf6ec', spacing: 1.5 });
  const fit = () => { const w100 = measure(name, 100, 800, 'Bricolage', -2.5); const s = Math.min(30, (plateW - 26) / (w100 / 100)); nm.setAttribute('font-size', s.toFixed(1)); nm.setAttribute('letter-spacing', (-s * 0.025).toFixed(2)); nm.setAttribute('y', (-h / 2 + 52 + s * 0.35).toFixed(1)); };
  fit(); REFIT.push(fit);
  return m;
}

// ---------------------------------------------------------------- running the film
function run() {
  const CLOCK = { t: 0 };
  const FONTS = ['800 40px "Bricolage"', '700 40px "Bricolage"', '400 40px "Geist Mono"', 'italic 400 40px "Instrument"'];
  let fitted = false;
  const fontsIn = () => !document.fonts || FONTS.every((f) => document.fonts.check(f));
  tl.to(CLOCK, { t: FILM.seconds, duration: FILM.seconds, ease: 'none', onUpdate: () => {
    if (!fitted && fontsIn()) { fitAll(); fitted = true; }
    for (const f of LIVE) f(CLOCK.t);
  } }, 0);
  if (document.fonts) Promise.all(FONTS.map((f) => document.fonts.load(f))).then(() => { fitAll(); fitted = true; for (const f of LIVE) f(CLOCK.t); });
  for (const f of LIVE) f(0);
  // the paper's grain, over everything
  const cv = document.createElement('canvas'); cv.width = Math.round(W / 2); cv.height = Math.round(H / 2);
  const g = cv.getContext('2d'), img = g.createImageData(cv.width, cv.height), r = rng(77);
  for (let i = 0; i < img.data.length; i += 4) { const v = 128 + (r() - 0.5) * 64; img.data[i] = img.data[i + 1] = img.data[i + 2] = v; img.data[i + 3] = 255; }
  g.putImageData(img, 0, 0);
  g.globalAlpha = 0.16; g.lineWidth = 0.7;
  for (let i = 0; i < 1600; i++) { const x = r() * cv.width, y = r() * cv.height, a = r() * TAU, l = 3 + r() * 12; g.strokeStyle = r() < 0.5 ? '#fff' : '#000'; g.beginPath(); g.moveTo(x, y); g.lineTo(x + Math.cos(a) * l, y + Math.sin(a) * l); g.stroke(); }
  g.globalAlpha = 0.07;
  for (let i = 0; i < 46; i++) { const x = r() * cv.width, y = r() * cv.height, rad = 40 + r() * 150, col = r() < 0.5 ? '255,255,255' : '0,0,0'; const gr = g.createRadialGradient(x, y, 0, x, y, rad); gr.addColorStop(0, `rgba(${col},1)`); gr.addColorStop(1, `rgba(${col},0)`); g.fillStyle = gr; g.fillRect(x - rad, y - rad, rad * 2, rad * 2); }
  const grain = document.getElementById('grain');
  if (grain) grain.style.backgroundImage = `url(${cv.toDataURL()})`;
}
