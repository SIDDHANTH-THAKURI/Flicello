// The DOM pieces below the WebGL story: the raw/edited comparison, the clip cards, the typing prompt and the
// taste radar. They all draw from the same mosaic video, so there is still only one video decoding.
import { gsap } from 'gsap';

const TILE_W = 288, TILE_H = 512;
function tileRect(tile) {
  return { sx: (tile % 4) * TILE_W, sy: Math.floor(tile / 4) * TILE_H, sw: TILE_W, sh: TILE_H };
}
const RAW = 'saturate(0.82) contrast(0.92) brightness(1.03)';
const EDITED = 'contrast(1.2) saturate(1.38) brightness(0.95) sepia(0.12) hue-rotate(-8deg)';

// draw a tile into a canvas with "cover" fit; `zoom` reframes it a little tighter
function cover(ctx, video, tile, w, h, zoom = 1, ox = 0) {
  const { sx, sy, sw, sh } = tileRect(tile);
  const scale = Math.max(w / sw, h / sh) * zoom;
  const dw = sw * scale, dh = sh * scale;
  ctx.drawImage(video, sx, sy, sw, sh, (w - dw) / 2 + ox, (h - dh) / 2, dw, dh);
}

function visible(el, cb) {
  const io = new IntersectionObserver((entries) => entries.forEach((e) => cb(e.isIntersecting)), { rootMargin: '120px' });
  io.observe(el);
}

export function compareWidget(video, reduced) {
  const root = document.querySelector('[data-compare]');
  if (!root) return () => {};
  const canvas = root.querySelector('canvas');
  const handle = root.querySelector('.compare-handle');
  const ctx = canvas.getContext('2d');
  let split = 0.5, shown = false, dragging = false, swept = false;

  const setSplit = (x) => {
    split = Math.min(0.97, Math.max(0.03, x));
    handle.style.left = `${(split * 100).toFixed(2)}%`;
    handle.setAttribute('aria-valuenow', String(Math.round(split * 100)));
  };
  const fromEvent = (e) => {
    const r = root.getBoundingClientRect();
    return (e.clientX - r.left) / r.width;
  };
  root.addEventListener('pointerdown', (e) => { dragging = true; root.setPointerCapture(e.pointerId); setSplit(fromEvent(e)); });
  root.addEventListener('pointermove', (e) => { if (dragging) setSplit(fromEvent(e)); });
  root.addEventListener('pointerup', () => { dragging = false; });
  handle.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') { setSplit(split - 0.05); e.preventDefault(); }
    if (e.key === 'ArrowRight') { setSplit(split + 0.05); e.preventDefault(); }
  });
  visible(root, (v) => {
    shown = v;
    if (v && !swept && !reduced) {
      swept = true;
      const o = { x: 0.5 };
      gsap.timeline({ delay: 0.4 })
        .to(o, { x: 0.18, duration: 0.9, ease: 'power3.inOut', onUpdate: () => setSplit(o.x) })
        .to(o, { x: 0.82, duration: 1.3, ease: 'power3.inOut', onUpdate: () => setSplit(o.x) })
        .to(o, { x: 0.5, duration: 0.8, ease: 'power3.inOut', onUpdate: () => setSplit(o.x) });
    }
  });
  setSplit(0.5);

  const W = canvas.width, H = canvas.height;
  const vg = ctx.createRadialGradient(W / 2, H * 0.45, H * 0.2, W / 2, H * 0.5, H * 0.75);
  vg.addColorStop(0, 'rgba(0,0,0,0)');
  vg.addColorStop(1, 'rgba(0,0,0,0.55)');
  return () => {
    if (!shown || video.readyState < 2) return;
    const cut = Math.round(W * split);
    ctx.save();
    ctx.filter = RAW;
    cover(ctx, video, 4, W, H, 1.0);
    ctx.restore();
    ctx.save();
    ctx.beginPath(); ctx.rect(cut, 0, W - cut, H); ctx.clip();
    ctx.filter = EDITED;
    cover(ctx, video, 4, W, H, 1.12, -W * 0.03);
    ctx.filter = 'none';
    ctx.fillStyle = vg; ctx.fillRect(0, 0, W, H);
    const leak = ctx.createLinearGradient(W, 0, W * 0.4, H * 0.5);
    leak.addColorStop(0, 'rgba(255,140,60,0.28)');
    leak.addColorStop(1, 'rgba(255,140,60,0)');
    ctx.fillStyle = leak; ctx.fillRect(0, 0, W, H);
    ctx.restore();
  };
}

export function bentoWidget(video) {
  const cards = [...document.querySelectorAll('.b[data-clip]')].map((el) => {
    const canvas = el.querySelector('canvas');
    const item = { el, canvas, ctx: canvas.getContext('2d'), tile: +el.dataset.clip, shown: false, w: 0, h: 0 };
    visible(el, (v) => { item.shown = v; });
    return item;
  });
  const fit = () => cards.forEach((c) => {
    const r = c.el.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    c.w = Math.max(2, Math.round(r.width * dpr));
    c.h = Math.max(2, Math.round(r.height * dpr));
    if (c.canvas.width !== c.w) c.canvas.width = c.w;
    if (c.canvas.height !== c.h) c.canvas.height = c.h;
  });
  fit();
  window.addEventListener('resize', fit);
  return () => {
    if (video.readyState < 2) return;
    for (const c of cards) {
      if (!c.shown) continue;
      c.ctx.filter = c.el.classList.contains('b-soon') ? 'grayscale(0.6) brightness(0.8)' : EDITED;
      cover(c.ctx, video, c.tile, c.w, c.h);
    }
  };
}

export function typer(reduced) {
  const el = document.querySelector('[data-typer]');
  if (!el) return;
  const lines = [
    'Turn our road trip into a 30-second trailer',
    'Every goal from Sunday’s match, on the beat',
    'My sister’s wedding as a two-minute film',
    'Explain my café’s new menu with motion graphics',
    'A slow, golden travel film to this song',
    'Make it feel like the opening of a heist movie',
  ];
  if (reduced) { el.textContent = lines[0]; return; }
  let li = 0;
  const run = async () => {
    for (;;) {
      const text = lines[li % lines.length];
      for (let i = 1; i <= text.length; i++) { el.textContent = text.slice(0, i); await wait(32 + Math.random() * 45); }
      await wait(1700);
      for (let i = text.length; i >= 0; i--) { el.textContent = text.slice(0, i); await wait(14); }
      await wait(300);
      li++;
    }
  };
  let started = false;
  visible(el, (v) => { if (v && !started) { started = true; run(); } });
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

export function radar(reduced) {
  const root = document.querySelector('[data-taste]');
  if (!root) return;
  const svg = root.querySelector('svg');
  const axes = ['pace', 'warmth', 'text', 'drama', 'humour', 'music-led'];
  const R = 120;
  const pt = (i, v) => {
    const a = (Math.PI * 2 * i) / axes.length - Math.PI / 2;
    return [Math.cos(a) * R * v, Math.sin(a) * R * v];
  };
  const grid = svg.querySelector('.radar-grid');
  const labels = svg.querySelector('.radar-labels');
  const NS = 'http://www.w3.org/2000/svg';
  [0.25, 0.5, 0.75, 1].forEach((v) => {
    const poly = document.createElementNS(NS, 'polygon');
    poly.setAttribute('points', axes.map((_, i) => pt(i, v).join(',')).join(' '));
    grid.appendChild(poly);
  });
  axes.forEach((name, i) => {
    const [x, y] = pt(i, 1);
    const line = document.createElementNS(NS, 'line');
    line.setAttribute('x1', '0'); line.setAttribute('y1', '0'); line.setAttribute('x2', String(x)); line.setAttribute('y2', String(y));
    grid.appendChild(line);
    const [lx, ly] = pt(i, 1.2);
    const t = document.createElementNS(NS, 'text');
    t.setAttribute('x', String(lx)); t.setAttribute('y', String(ly + 3)); t.setAttribute('text-anchor', 'middle');
    t.textContent = name;
    labels.appendChild(t);
  });
  const first = [0.5, 0.42, 0.8, 0.55, 0.3, 0.5];
  const later = [0.86, 0.82, 0.22, 0.7, 0.46, 0.9];
  const path = (vals) => 'M' + vals.map((v, i) => pt(i, v).join(',')).join('L') + 'Z';
  const a = svg.querySelector('.radar-a');
  const b = svg.querySelector('.radar-b');
  a.setAttribute('d', path(first));
  b.setAttribute('d', path(first));
  const items = [...root.querySelectorAll('.lessons li')];
  gsap.set(items, { opacity: 0, x: 24 });
  let done = false;
  visible(root, (v) => {
    if (!v || done) return;
    done = true;
    const o = { t: 0 };
    gsap.to(o, {
      t: 1, duration: reduced ? 0 : 2.4, ease: 'power3.inOut', delay: 0.3,
      onUpdate: () => b.setAttribute('d', path(first.map((f, i) => f + (later[i] - f) * o.t))),
    });
    gsap.to(items, { opacity: 1, x: 0, duration: reduced ? 0 : 0.9, ease: 'power3.out', stagger: 0.35, delay: 0.5 });
  });
}
