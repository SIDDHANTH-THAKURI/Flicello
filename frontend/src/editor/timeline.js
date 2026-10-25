// The timeline: the film's tracks under one playhead.
// - Graphics: titles, captions, names, drawn things... drag to move them, their ends to change when they come and go.
// - Picture: the shots, with their own frames. Drag an edge to trim (ripple: what follows moves; Alt or ripple off:
//   the neighbour takes it up); drag a shot to another place; tap to select it.
// - Words: what is said, where (cuts snap to them).
// - Sound: each shot's own sound.
// - Music: the song as it plays under the film: its sections, bars and beats, its cuts, its level line (with the music
//   selected: tap the line to add a point, drag one, double-tap to take it away). Drag across it to pick a stretch
//   (to cut out, play again or make again); Shift+drag slides the song against the picture.
// Everything snaps (to cuts, bars, beats, words, graphics and the playhead) unless snapping is off.

import { filmstrip, waveform } from './media.js';

const PAD = 16; // px of air before the film starts
const ROWS = { ruler: 26, gfx: 34, over: 42, pic: 64, words: 18, snd: 38, mus: 70 };
const LABEL = { gfx: 'Graphics', over: 'Over', pic: 'Picture', words: 'Words', snd: 'Sound', mus: 'Music' };
const SNAP_PX = 9;
const MIN_SHOT = 0.25;
const WHAT = { title: 'Title', captions: 'Captions', lowerthird: 'Name', transition: 'Transition', kinetic: 'Beat words',
  callout: 'Callout', route: 'Route', accent: 'Finish', thing: 'Drawn', scene: 'Designed scene', logo: 'Logo' };

const el = (tag, cls, parent) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (parent) parent.append(n);
  return n;
};
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const clock = (t, fine = false) => {
  const s = Math.max(0, t);
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  return `${m}:${(fine ? rest.toFixed(2) : Math.floor(rest).toString()).padStart(fine ? 5 : 2, '0')}`;
};

// song seconds heard at film second t (its slide and cuts), and back again (null: that part of the song isn't heard)
export function songAt(m, t) {
  let pos = m.start + t;
  for (const j of m.jumps || []) if (t >= j.at) pos = j.to + (t - j.at);
  return pos;
}
function pieces(m, total) {
  const out = [];
  let t0 = 0;
  let s0 = m.start;
  for (const j of [...(m.jumps || [])].sort((a, b) => a.at - b.at)) {
    if (j.at <= t0 || j.at >= total) continue;
    out.push({ t0, t1: j.at, s0 });
    t0 = j.at;
    s0 = j.to;
  }
  out.push({ t0, t1: total, s0 });
  return out;
}
export function filmTimes(m, total, songTimes) {
  const out = [];
  for (const p of pieces(m, total)) {
    for (const s of songTimes) {
      const t = p.t0 + (s - p.s0);
      if (t >= p.t0 - 1e-6 && t < p.t1) out.push(t);
    }
  }
  return out.sort((a, b) => a - b);
}

export class Timeline {
  constructor(ed) {
    this.ed = ed;
    this.scroll = document.querySelector('[data-scroll]');
    this.canvas = document.querySelector('[data-canvas]');
    this.labels = document.querySelector('[data-labels]');
    this.pps = 0;
    this.fitted = true;
    this.range = null; // a stretch picked on the music track: { a, b }
    this.scroll.addEventListener('wheel', (e) => this.wheel(e), { passive: false });
    window.addEventListener('resize', () => { if (this.fitted) this.fit(); });
  }

  get v() { return this.ed.view; }

  get total() { return this.v ? this.v.duration : 1; }

  x(t) { return PAD + t * this.pps; }

  at(clientX) {
    const r = this.canvas.getBoundingClientRect();
    return clamp((clientX - r.left - PAD) / this.pps, 0, this.total);
  }

  fit() {
    const w = this.scroll.clientWidth - PAD * 2 - 12;
    this.pps = Math.max(8, w / Math.max(1, this.total));
    this.fitted = true;
    this.render();
  }

  zoom(factor, aroundClientX = null) {
    const r = this.scroll.getBoundingClientRect();
    const cx = aroundClientX ?? r.left + r.width / 2;
    const t = this.at(cx);
    this.pps = clamp(this.pps * factor, 4, 1200);
    this.fitted = false;
    this.render();
    this.scroll.scrollLeft = this.x(t) - (cx - r.left);
  }

  wheel(e) {
    if (e.ctrlKey || e.metaKey) { // pinch or Ctrl+wheel: zoom around the pointer
      e.preventDefault();
      this.zoom(Math.exp(-e.deltaY * 0.0022), e.clientX);
    } else if (!e.shiftKey && Math.abs(e.deltaY) > Math.abs(e.deltaX)) { // a mouse wheel scrolls the film sideways
      e.preventDefault();
      this.scroll.scrollLeft += e.deltaY;
    }
  }

  // ---------------------------------------------------------------- where things snap
  marks() {
    const v = this.v;
    const out = [];
    for (const s of this.cuts()) out.push({ t: s, kind: 'cut', label: 'cut' });
    out.push({ t: this.ed.t, kind: 'playhead', label: 'playhead' });
    if (v.music) {
      const bars = new Set(filmTimes(v.music, this.total, v.music.bars).map((t) => t.toFixed(3)));
      for (const t of filmTimes(v.music, this.total, v.music.beats)) {
        const bar = bars.has(t.toFixed(3));
        out.push({ t, kind: bar ? 'bar' : 'beat', label: bar ? 'bar' : 'beat' });
      }
    }
    for (const w of v.words) {
      out.push({ t: w.s, kind: 'word', label: `“${w.w}”` });
      out.push({ t: w.e, kind: 'word', label: `after “${w.w}”` });
    }
    for (const g of v.graphics) {
      out.push({ t: g.start, kind: 'graphic', label: 'graphic' });
      out.push({ t: g.end, kind: 'graphic', label: 'graphic' });
    }
    return out;
  }

  snap(t, skip = () => false) {
    if (!this.ed.snapping) return { t, label: '' };
    let best = null;
    const reach = SNAP_PX / this.pps;
    for (const m of this.marksCache || []) {
      if (skip(m)) continue;
      const d = Math.abs(m.t - t);
      const rank = { cut: 0, playhead: 0, bar: 1, word: 1, graphic: 2, beat: 3 }[m.kind];
      if (d <= reach && (!best || d + rank * 1e-4 < best.d)) best = { d, t: m.t, label: m.label };
    }
    return best ? { t: best.t, label: best.label } : { t, label: '' };
  }

  cuts() { // the film seconds between shots (a blend's middle)
    const s = this.v.shots;
    return [0, ...s.slice(1).map((x, i) => (s[i].end > x.start + 1e-3 ? (x.start + s[i].end) / 2 : x.start)), this.total];
  }

  // ---------------------------------------------------------------- drawing
  render() {
    const v = this.v;
    if (!v) return;
    if (!this.pps) { this.fit(); return; }
    this.marksCache = this.marks();
    const width = Math.ceil(this.x(this.total) + PAD + 120);
    this.canvas.replaceChildren();
    this.canvas.style.width = `${width}px`;
    this.labels.replaceChildren();
    el('div', 'ed-label ruler', this.labels).style.height = `${ROWS.ruler}px`;
    this.rows = {};
    const rows = ['gfx', 'over', 'pic', ...(v.words.length ? ['words'] : []), 'snd', ...(v.music ? ['mus'] : [])];
    const ruler = el('canvas', 'ed-ruler', this.canvas);
    ruler.width = width;
    ruler.height = ROWS.ruler;
    this.drawRuler(ruler);
    this.wireRuler(ruler);
    for (const name of rows) {
      const row = el('div', `ed-row ${name}`, this.canvas);
      row.style.height = `${ROWS[name]}px`;
      this.rows[name] = row;
      row.addEventListener('pointerdown', (e) => { // the empty part of a track: the playhead goes there
        if (e.target !== row) return;
        this.ed.seek(this.snap(this.at(e.clientX), (m) => m.kind === 'playhead').t);
        if (name !== 'mus') this.ed.select(null);
      });
      const label = el('div', `ed-label ${name}`, this.labels);
      label.style.height = `${ROWS[name]}px`;
      label.textContent = LABEL[name];
    }
    this.drawShots();
    this.drawGraphics();
    this.drawOver();
    if (this.rows.words) this.drawWords();
    this.drawSound();
    if (this.rows.mus) this.drawMusic();
    this.head = el('div', 'ed-playhead', this.canvas);
    this.guide = el('div', 'ed-guide', this.canvas);
    this.guide.hidden = true;
    this.setTime(this.ed.t);
    this.showSelection();
  }

  drawRuler(c) {
    const ctx = c.getContext('2d');
    const css = getComputedStyle(document.body);
    ctx.clearRect(0, 0, c.width, c.height);
    const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60];
    const step = steps.find((s) => s * this.pps >= 64) || 60;
    ctx.font = `11px ${css.getPropertyValue('--f-mono') || 'monospace'}`;
    ctx.fillStyle = css.getPropertyValue('--muted') || '#999';
    ctx.strokeStyle = css.getPropertyValue('--line-2') || '#555';
    for (let t = 0; t <= this.total + 1e-6; t += step / 4) {
      const major = Math.abs(t / step - Math.round(t / step)) < 1e-6;
      const x = Math.round(this.x(t)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(x, c.height);
      ctx.lineTo(x, c.height - (major ? 10 : 4));
      ctx.stroke();
      if (major) ctx.fillText(step < 1 ? clock(t, true) : clock(t), x + 3, 12);
    }
    if (this.v.music) { // the beat, along the ruler's foot
      ctx.fillStyle = css.getPropertyValue('--amber') || '#fb4';
      for (const t of filmTimes(this.v.music, this.total, this.v.music.beats)) ctx.fillRect(Math.round(this.x(t)), c.height - 2, 1, 2);
    }
  }

  wireRuler(c) {
    c.addEventListener('pointerdown', (e) => {
      c.setPointerCapture(e.pointerId);
      const go = (ev) => this.ed.seek(this.snap(this.at(ev.clientX), (m) => m.kind === 'playhead').t);
      go(e);
      const move = (ev) => go(ev);
      const up = () => { c.removeEventListener('pointermove', move); c.removeEventListener('pointerup', up); };
      c.addEventListener('pointermove', move);
      c.addEventListener('pointerup', up);
    });
  }

  setTime(t) {
    if (!this.head) return;
    this.head.style.transform = `translateX(${this.x(t)}px)`;
    if (this.ed.player && this.ed.player.playing) { // keep it in view while it plays
      const left = this.scroll.scrollLeft;
      const right = left + this.scroll.clientWidth;
      const x = this.x(t);
      if (x > right - 40 || x < left) this.scroll.scrollLeft = x - 60;
    }
  }

  showGuide(t, label) {
    if (!label) { this.guide.hidden = true; return; }
    this.guide.hidden = false;
    this.guide.style.transform = `translateX(${this.x(t)}px)`;
    this.guide.dataset.label = label;
  }

  showSelection() {
    const sel = this.ed.sel;
    this.canvas.querySelectorAll('.on').forEach((n) => n.classList.remove('on'));
    if (!sel) return;
    const q = sel.type === 'shot' ? `[data-shot="${sel.n}"]` : sel.type === 'graphic' ? `[data-graphic="${sel.index}"]`
      : sel.type === 'over' ? `[data-over="${sel.index}"]` : sel.type === 'music' ? '.ed-music' : null;
    if (q) this.canvas.querySelectorAll(q).forEach((n) => n.classList.add('on'));
    if (this.rows && this.rows.mus) this.drawLevels();
  }

  // ---------------------------------------------------------------- picture
  drawShots() {
    const row = this.rows.pic;
    const cuts = this.cuts();
    const aspect = this.v.w / this.v.h;
    this.v.shots.forEach((s, i) => {
      const a = cuts[i];
      const b = cuts[i + 1];
      const box = el('div', `ed-shot kind-${s.kind}`, row);
      box.dataset.shot = String(s.n);
      box.style.left = `${this.x(a)}px`;
      box.style.width = `${Math.max(2, (b - a) * this.pps)}px`;
      const strip = el('canvas', 'ed-strip', box);
      strip.width = Math.max(2, Math.round((b - a) * this.pps));
      strip.height = ROWS.pic - 6;
      filmstrip(strip, s.copy, s.src_in + Math.max(0, a - s.start) * (s.rate || 1), s.rate, b - a, aspect,
        (this.v.sprites || {})[s.clip]);
      const tag = el('span', 'ed-n', box);
      tag.textContent = String(s.n);
      const badges = [];
      if (s.rate && Math.abs(s.rate - 1) > 0.01) badges.push(`${+s.rate.toFixed(2)}x`);
      if (s.mute) badges.push('muted');
      if (s.kind !== 'cut') badges.push({ hold: 'slow', hit: 'hit', ramp: 'ramp', stutter: `×${s.count}` }[s.kind] || s.kind);
      if (badges.length) el('span', 'ed-badges', box).textContent = badges.join(' · ');
      for (const k of s.keys || []) { // its keyframes
        const at = s.start + k[0];
        if (at < a - 1e-3 || at > b + 1e-3) continue;
        const d = el('span', 'ed-key', box);
        d.style.left = `${(at - a) * this.pps - 5}px`;
        d.title = `Keyframe at ${clock(at, true)}: ${k[1].toFixed(2)}x`;
      }
      if (s.blend && s.blend !== 'cut' && i > 0) {
        const bow = el('span', 'ed-blend', row);
        bow.style.left = `${this.x(a) - 9}px`;
        bow.title = `${s.blend} into shot ${s.n}`;
      }
      for (const side of ['l', 'r']) {
        const h = el('span', `ed-h ${side}`, box);
        h.title = side === 'l' ? 'Drag: trim its start' : 'Drag: trim its end';
        h.addEventListener('pointerdown', (e) => this.trimDrag(e, s, i, side === 'l' ? 'start' : 'end', box));
      }
      box.addEventListener('pointerdown', (e) => { if (!e.target.classList.contains('ed-h')) this.shotDrag(e, s, i, box); });
    });
  }

  trimDrag(e, s, i, edge, box) {
    e.preventDefault();
    e.stopPropagation();
    this.ed.select({ type: 'shot', n: s.n });
    const ripple = this.ed.ripple !== e.altKey;
    const cuts = this.cuts();
    const edgeT = edge === 'end' ? cuts[i + 1] : cuts[i];
    const len = s.end - s.start;
    const nb = this.v.shots[edge === 'end' ? i + 1 : i - 1];
    // how far it may go: the shot's own frames, the shortest a shot may be, and (rolling) its neighbour's
    let lo;
    let hi;
    if (edge === 'end') {
      lo = -(len - MIN_SHOT);
      hi = s.rate ? s.room_end : 0;
      if (!ripple && nb) { hi = Math.min(hi, nb.end - nb.start - MIN_SHOT); lo = Math.max(lo, -(nb.rate ? nb.room_start : 0)); }
    } else {
      lo = -(len - MIN_SHOT); // its start later (shorter)
      hi = s.rate ? s.room_start : 0; // its start earlier (longer)
      if (!ripple && nb) hi = Math.min(hi, nb.end - nb.start - MIN_SHOT);
    }
    const x0 = e.clientX;
    const left0 = parseFloat(box.style.left);
    const width0 = parseFloat(box.style.width);
    const tip = el('span', 'ed-tip', box);
    let d = 0;
    const move = (ev) => {
      let to = edgeT + (ev.clientX - x0) / this.pps;
      const snapped = this.snap(to, (m) => m.kind === 'cut' && Math.abs(m.t - edgeT) < 1e-3);
      to = snapped.t;
      d = edge === 'end' ? to - edgeT : edgeT - to; // + longer
      d = clamp(d, lo, hi);
      const shown = edge === 'end' ? edgeT + d : edgeT - d;
      this.showGuide(shown, snapped.label && Math.abs(shown - snapped.t) < 1e-6 ? snapped.label : '');
      if (edge === 'end') box.style.width = `${Math.max(2, width0 + d * this.pps)}px`;
      else { box.style.left = `${left0 - d * this.pps}px`; box.style.width = `${Math.max(2, width0 + d * this.pps)}px`; }
      tip.textContent = `${d >= 0 ? '+' : '−'}${Math.abs(d).toFixed(2)} s${d >= hi - 1e-6 && d > 0 ? ' · clip ends' : ''}`;
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      this.showGuide(0, '');
      tip.remove();
      if (Math.abs(d) < 1 / (this.v.fps || 30)) { box.style.left = `${left0}px`; box.style.width = `${width0}px`; return; }
      this.ed.edit([{ op: 'trim', shot: s.n, edge, seconds: +d.toFixed(3), ripple }]);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  shotDrag(e, s, i, box) {
    e.preventDefault();
    const x0 = e.clientX;
    let moving = false;
    let slot = null;
    const marker = el('div', 'ed-insert', this.rows.pic);
    marker.hidden = true;
    const cuts = this.cuts();
    const move = (ev) => {
      if (!moving && Math.abs(ev.clientX - x0) < 5) return;
      moving = true;
      box.classList.add('lifted');
      box.style.transform = `translateX(${ev.clientX - x0}px)`;
      const t = this.at(ev.clientX);
      let k = 0; // the gap it would go into: before shot k+1
      while (k < this.v.shots.length && t > (cuts[k] + cuts[k + 1]) / 2) k += 1;
      slot = k;
      marker.hidden = k === i || k === i + 1;
      marker.style.left = `${this.x(cuts[k]) - 2}px`;
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      marker.remove();
      box.classList.remove('lifted');
      box.style.transform = '';
      if (!moving) { this.ed.select({ type: 'shot', n: s.n }); this.ed.seekIfOutside(s); return; }
      if (slot === null || slot === i || slot === i + 1) return;
      const to = slot < i ? slot + 1 : slot; // where it lands, numbered as after the move
      this.ed.edit([{ op: 'move', shot: s.n, to }]);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  // ---------------------------------------------------------------- graphics
  drawGraphics() {
    const row = this.rows.gfx;
    const lanes = []; // overlapping graphics sit on lanes, two to a row
    const sorted = [...this.v.graphics].sort((a, b) => a.start - b.start);
    const laneOf = new Map();
    for (const g of sorted) {
      let lane = lanes.findIndex((end) => end <= g.start + 1e-3);
      if (lane < 0) { lane = lanes.length; lanes.push(0); }
      lanes[lane] = g.end;
      laneOf.set(g.index, lane);
    }
    const two = lanes.length > 1;
    for (const g of sorted) {
      const lane = laneOf.get(g.index);
      const pill = el('div', `ed-g kind-${g.kind}${g.drawn ? '' : ' undrawn'}`, row);
      pill.dataset.graphic = String(g.index);
      pill.style.left = `${this.x(g.start)}px`;
      pill.style.width = `${Math.max(6, (g.end - g.start) * this.pps)}px`;
      pill.style.top = `${3 + (two ? (lane % 2) * 14 : 0)}px`;
      pill.style.height = two ? '13px' : '26px';
      pill.textContent = `${WHAT[g.kind] || g.kind}${g.text ? `: ${g.text.split('|').join(' → ')}` : ''}`;
      pill.title = g.drawn ? `${pill.textContent} · drag to move it, its ends to change when it comes and goes`
        : `${pill.textContent} · not drawn in this draft (see the changes list)`;
      if (g.kind === 'scene') continue; // a designed scene sits where it was designed
      for (const side of ['l', 'r']) {
        const h = el('span', `ed-h ${side}`, pill);
        h.addEventListener('pointerdown', (e) => this.graphicDrag(e, g, pill, side));
      }
      pill.addEventListener('pointerdown', (e) => { if (!e.target.classList.contains('ed-h')) this.graphicDrag(e, g, pill, 'move'); });
    }
  }

  graphicDrag(e, g, pill, how) {
    e.preventDefault();
    e.stopPropagation();
    this.ed.select({ type: 'graphic', index: g.index });
    const x0 = e.clientX;
    let a = g.start;
    let b = g.end;
    let moved = false;
    const skip = (m) => m.kind === 'graphic' && (Math.abs(m.t - g.start) < 1e-3 || Math.abs(m.t - g.end) < 1e-3);
    const move = (ev) => {
      const dt = (ev.clientX - x0) / this.pps;
      if (!moved && Math.abs(ev.clientX - x0) < 4) return;
      moved = true;
      let label = '';
      if (how === 'move') {
        const s = this.snap(g.start + dt, skip);
        a = clamp(s.t, 0, this.total - (g.end - g.start));
        b = a + (g.end - g.start);
        label = s.label;
      } else if (how === 'l') {
        const s = this.snap(g.start + dt, skip);
        a = clamp(s.t, 0, b - 0.3);
        label = s.label;
      } else {
        const s = this.snap(g.end + dt, skip);
        b = clamp(s.t, a + 0.3, this.total);
        label = s.label;
      }
      this.showGuide(how === 'r' ? b : a, label);
      pill.style.left = `${this.x(a)}px`;
      pill.style.width = `${Math.max(6, (b - a) * this.pps)}px`;
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      this.showGuide(0, '');
      if (!moved) { this.ed.seek(g.start); return; }
      this.ed.edit([{ op: 'graphic', index: g.index, at: +a.toFixed(3), until: +b.toFixed(3) }]);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  // ---------------------------------------------------------------- what is laid over the picture (B-roll)
  drawOver() {
    const row = this.rows.over;
    const aspect = this.v.w / this.v.h;
    if (!this.v.over.length) {
      const hint = el('span', 'ed-hint', row);
      hint.textContent = 'Lay a shot over the picture here (O): B-roll, a cutaway; the sound goes on under it';
      hint.style.left = `${this.x(0) + 8}px`;
    }
    for (const o of this.v.over) {
      const box = el('div', 'ed-over', row);
      box.dataset.over = String(o.index);
      box.style.left = `${this.x(o.start)}px`;
      box.style.width = `${Math.max(6, (o.end - o.start) * this.pps)}px`;
      const strip = el('canvas', 'ed-strip', box);
      strip.width = Math.max(2, Math.round((o.end - o.start) * this.pps));
      strip.height = ROWS.over - 6;
      filmstrip(strip, o.copy, o.src_in, o.rate, o.end - o.start, aspect, (this.v.sprites || {})[o.clip]);
      el('span', 'ed-n', box).textContent = o.short;
      for (const side of ['l', 'r']) {
        const h = el('span', `ed-h ${side}`, box);
        h.addEventListener('pointerdown', (e) => this.overDrag(e, o, box, side));
      }
      box.addEventListener('pointerdown', (e) => { if (!e.target.classList.contains('ed-h')) this.overDrag(e, o, box, 'move'); });
    }
  }

  overDrag(e, o, box, how) {
    e.preventDefault();
    e.stopPropagation();
    this.ed.select({ type: 'over', index: o.index });
    const x0 = e.clientX;
    let a = o.start;
    let b = o.end;
    let moved = false;
    const skip = (m) => Math.abs(m.t - o.start) < 1e-3 || Math.abs(m.t - o.end) < 1e-3;
    const move = (ev) => {
      if (!moved && Math.abs(ev.clientX - x0) < 4) return;
      moved = true;
      const dt = (ev.clientX - x0) / this.pps;
      let label = '';
      if (how === 'move') {
        const s = this.snap(o.start + dt, skip);
        a = clamp(s.t, 0, this.total - (o.end - o.start));
        b = a + (o.end - o.start);
        label = s.label;
      } else if (how === 'l') {
        const s = this.snap(o.start + dt, skip);
        a = clamp(s.t, Math.max(0, o.start - o.src_in / (o.rate || 1)), b - 0.3); // no earlier than its clip begins
        label = s.label;
      } else {
        const s = this.snap(o.end + dt, skip);
        b = clamp(s.t, a + 0.3, this.total);
        label = s.label;
      }
      this.showGuide(how === 'r' ? b : a, label);
      box.style.left = `${this.x(a)}px`;
      box.style.width = `${Math.max(6, (b - a) * this.pps)}px`;
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      this.showGuide(0, '');
      if (!moved) { this.ed.seek(o.start + 0.01); return; }
      const src = how === 'l' ? o.src_in + (a - o.start) * (o.rate || 1) : o.src_in; // its frames stay where they were
      this.ed.edit([{ op: 'over', index: o.index, at: +a.toFixed(3), until: +b.toFixed(3), src_in: +src.toFixed(3) }]);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  // ---------------------------------------------------------------- words and sound
  drawWords() {
    const row = this.rows.words;
    let last = -Infinity;
    for (const w of this.v.words) {
      const x = this.x(w.s);
      const tick = el('span', 'ed-w', row);
      tick.style.left = `${x}px`;
      tick.style.width = `${Math.max(1, (w.e - w.s) * this.pps)}px`;
      if (x - last > 34) { tick.textContent = w.w; last = x + w.w.length * 6; }
      tick.title = w.w;
    }
  }

  drawSound() {
    const row = this.rows.snd;
    const cuts = this.cuts();
    const css = getComputedStyle(document.body);
    this.v.shots.forEach((s, i) => {
      const a = cuts[i];
      const b = cuts[i + 1];
      const box = el('div', `ed-snd${s.sound && !s.mute ? '' : ' quiet'}`, row);
      box.dataset.shot = String(s.n);
      box.style.left = `${this.x(a)}px`;
      box.style.width = `${Math.max(2, (b - a) * this.pps)}px`;
      const c = el('canvas', '', box);
      c.width = Math.max(2, Math.round((b - a) * this.pps));
      c.height = ROWS.snd - 6;
      const peaks = this.v.sounds[s.clip];
      const from = s.src_in + Math.max(0, a - s.start) * (s.rate || 1);
      waveform(c, peaks, this.v.peaks_per_second, from, from + (b - a) * (s.rate || 1),
        s.sound && !s.mute ? (css.getPropertyValue('--keep') || '#7cf3c4') : (css.getPropertyValue('--faint') || '#666'));
      if (s.gain_db) el('span', 'ed-badges', box).textContent = `${s.gain_db > 0 ? '+' : ''}${s.gain_db} dB`;
      box.addEventListener('pointerdown', () => { this.ed.select({ type: 'shot', n: s.n, part: 'sound' }); });
    });
  }

  // ---------------------------------------------------------------- music
  drawMusic() {
    const m = this.v.music;
    const row = this.rows.mus;
    const css = getComputedStyle(document.body);
    const box = el('div', 'ed-music', row);
    box.style.left = `${this.x(0)}px`;
    box.style.width = `${this.total * this.pps}px`;
    // its sections, where they fall in the film
    for (const p of pieces(m, this.total)) {
      for (const sec of m.sections) {
        const a = p.t0 + (sec.start - p.s0);
        const b = p.t0 + (sec.end - p.s0);
        const lo = Math.max(a, p.t0);
        const hi = Math.min(b, p.t1);
        if (hi - lo < 0.05) continue;
        const band = el('span', `ed-sec e${Math.min(4, Math.round(sec.energy * 4))}`, box);
        band.style.left = `${(lo) * this.pps}px`;
        band.style.width = `${(hi - lo) * this.pps}px`;
        if ((hi - lo) * this.pps > 40) band.textContent = sec.label;
      }
    }
    const c = el('canvas', 'ed-wave', box);
    c.width = Math.max(2, Math.round(this.total * this.pps));
    c.height = ROWS.mus - 4;
    waveform(c, m.peaks, this.v.peaks_per_second, 0, this.total, css.getPropertyValue('--amber') || '#fb4',
      (t) => songAt(m, Math.min(this.total - 1e-6, t)));
    const ctx = c.getContext('2d');
    const bars = new Set(filmTimes(m, this.total, m.bars).map((t) => t.toFixed(3)));
    for (const t of filmTimes(m, this.total, m.beats)) {
      const bar = bars.has(t.toFixed(3));
      ctx.fillStyle = bar ? 'rgba(255,255,255,0.32)' : 'rgba(255,255,255,0.10)';
      ctx.fillRect(Math.round(t * this.pps), 0, 1, bar ? c.height : c.height * 0.3);
    }
    for (const p of pieces(m, this.total)) { // where the song runs out under the picture: silence, said
      const end = p.t0 + (m.duration - p.s0);
      if (end >= p.t1 - 0.05) continue;
      const gap = el('span', 'ed-silent', box);
      gap.style.left = `${Math.max(p.t0, end) * this.pps}px`;
      gap.style.width = `${(p.t1 - Math.max(p.t0, end)) * this.pps}px`;
      if ((p.t1 - Math.max(p.t0, end)) * this.pps > 70) gap.textContent = 'the song has ended';
    }
    for (const j of m.jumps) { // the song's cuts
      const seam = el('span', 'ed-seam', box);
      seam.style.left = `${j.at * this.pps}px`;
      seam.title = `The song jumps here to ${clock(j.to)}`;
    }
    this.levels = el('canvas', 'ed-levels', box);
    this.levels.width = c.width;
    this.levels.height = c.height;
    if (this.range) this.drawRange();
    box.addEventListener('pointerdown', (e) => this.musicDown(e, box));
    box.addEventListener('dblclick', (e) => this.levelRemove(e));
    this.drawLevels();
  }

  levelPoints() {
    const edit = this.v.music.edit;
    return (edit && edit.points) ? edit.points.map((p) => ({ ...p })) : [];
  }

  levelY(db) { // 0 dB near the top, -30 dB at the foot
    const H = this.levels.height;
    return 6 + ((clamp(db, -30, 6) - 6) / -36) * (H - 12);
  }

  drawLevels(points = null) {
    if (!this.levels) return;
    const c = this.levels;
    const ctx = c.getContext('2d');
    ctx.clearRect(0, 0, c.width, c.height);
    const on = this.ed.sel && this.ed.sel.type === 'music';
    const pts = points || this.levelPoints();
    if (!on && !pts.length) return;
    const css = getComputedStyle(document.body);
    ctx.strokeStyle = on ? (css.getPropertyValue('--paper') || '#fff') : 'rgba(255,255,255,0.45)';
    ctx.lineWidth = on ? 1.6 : 1;
    ctx.beginPath();
    const ys = (t) => this.levelY(pts.length ? levelAt(pts, t) : 0);
    ctx.moveTo(0, ys(0));
    for (const p of pts) ctx.lineTo(p.t * this.pps, this.levelY(p.db));
    ctx.lineTo(c.width, ys(this.total));
    ctx.stroke();
    if (on) {
      for (const p of pts) {
        ctx.beginPath();
        ctx.arc(p.t * this.pps, this.levelY(p.db), 4.5, 0, Math.PI * 2);
        ctx.fillStyle = css.getPropertyValue('--amber') || '#fb4';
        ctx.fill();
      }
    }
  }

  musicDown(e, box) {
    e.preventDefault();
    const m = this.v.music;
    const r = box.getBoundingClientRect();
    const t0 = clamp((e.clientX - r.left) / this.pps, 0, this.total);
    const y = e.clientY - r.top;
    const selected = this.ed.sel && this.ed.sel.type === 'music';
    const pts = this.levelPoints();
    // a level point under the pointer (music selected): drag it
    const hit = selected ? pts.findIndex((p) => Math.abs(p.t * this.pps - (e.clientX - r.left)) < 7
      && Math.abs(this.levelY(p.db) - y) < 7) : -1;
    if (hit >= 0 || (selected && Math.abs(this.levelY(pts.length ? levelAt(pts, t0) : 0) - y) < 6)) {
      let k = hit;
      if (k < 0) { pts.push({ t: t0, db: pts.length ? levelAt(pts, t0) : 0 }); pts.sort((a, b) => a.t - b.t); k = pts.findIndex((p) => p.t === t0); }
      const move = (ev) => {
        const t = clamp((ev.clientX - r.left) / this.pps, 0, this.total);
        const db = clamp(((ev.clientY - r.top - 6) / (this.levels.height - 12)) * -36 + 6, -30, 6);
        pts[k] = { t, db: Math.round(db * 2) / 2 };
        this.drawLevels(pts);
        this.ed.status(`Music level ${pts[k].db > 0 ? '+' : ''}${pts[k].db} dB at ${clock(t, true)}`);
      };
      const up = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        this.ed.editMusic({ points: pts.sort((a, b) => a.t - b.t).map((p) => ({ t: +p.t.toFixed(3), db: p.db })) });
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      return;
    }
    const x0 = e.clientX;
    const slide = e.shiftKey;
    let moved = false;
    const move = (ev) => {
      if (!moved && Math.abs(ev.clientX - x0) < 4) return;
      moved = true;
      if (slide) { // the song, earlier or later against the picture
        const d = (ev.clientX - x0) / this.pps;
        box.style.transform = `translateX(${d * this.pps}px)`;
        this.ed.status(`The song ${d >= 0 ? 'later' : 'earlier'} by ${Math.abs(d).toFixed(2)} s`);
        return;
      }
      const a = this.snap(t0, () => false).t;
      const b = this.snap(clamp((ev.clientX - r.left) / this.pps, 0, this.total), () => false).t;
      this.range = { a: Math.min(a, b), b: Math.max(a, b) };
      this.drawRange();
    };
    const up = (ev) => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      if (!moved) { this.range = null; this.drawRange(); this.ed.select({ type: 'music' }); return; }
      if (slide) {
        const d = (ev.clientX - x0) / this.pps;
        box.style.transform = '';
        const edit = this.v.music.edit || {};
        this.ed.editMusic({ slide: +((edit.slide || 0) + d).toFixed(3) }); // dragged right: the song plays later
        return;
      }
      if (this.range && this.range.b - this.range.a < 0.2) this.range = null;
      this.drawRange();
      this.ed.select({ type: 'music', range: this.range });
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  levelRemove(e) {
    if (!(this.ed.sel && this.ed.sel.type === 'music')) return;
    const r = this.levels.getBoundingClientRect();
    const pts = this.levelPoints();
    const k = pts.findIndex((p) => Math.abs(p.t * this.pps - (e.clientX - r.left)) < 8);
    if (k < 0) return;
    pts.splice(k, 1);
    this.ed.editMusic({ points: pts });
  }

  drawRange() {
    const row = this.rows && this.rows.mus;
    if (!row) return;
    row.querySelectorAll('.ed-range').forEach((n) => n.remove());
    if (!this.range) return;
    const band = el('div', 'ed-range', row);
    band.style.left = `${this.x(this.range.a)}px`;
    band.style.width = `${(this.range.b - this.range.a) * this.pps}px`;
    band.dataset.label = `${clock(this.range.a, true)} – ${clock(this.range.b, true)}`;
  }
}

function levelAt(points, t) {
  if (!points.length) return 0;
  if (t <= points[0].t) return points[0].db;
  for (let k = 1; k < points.length; k += 1) {
    const a = points[k - 1];
    const b = points[k];
    if (t <= b.t) return a.db + ((b.db - a.db) * (t - a.t)) / Math.max(1e-6, b.t - a.t);
  }
  return points[points.length - 1].db;
}
