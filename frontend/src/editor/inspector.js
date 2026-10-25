// The panel beside the film: what is selected on the timeline, and everything that can be done to it. Each control is
// an exact edit (made as the draft at once); sliders send theirs when they are let go.

import { clock, songAt } from './timeline.js';

const el = (tag, cls, parent, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  if (parent) parent.append(n);
  return n;
};
const BLENDS = [['cut', 'Cut'], ['dissolve', 'Dissolve'], ['fadeblack', 'Dip to black'], ['fadewhite', 'Dip to white'],
  ['smoothleft', 'Slide left'], ['smoothright', 'Slide right'], ['zoomin', 'Zoom through']];
const SPEEDS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4];
const STYLES = { title: ['rise', 'slam', 'mask', 'split'], captions: ['pop', 'karaoke', 'box'],
  transition: ['panels', 'iris', 'bars', 'flash'], callout: ['circle', 'box', 'arrow'],
  accent: ['brackets', 'sweep', 'leak', 'lines'] };
const WHAT = { title: 'Title', captions: 'Captions', lowerthird: 'Name', transition: 'Transition', kinetic: 'Beat words',
  callout: 'Callout', route: 'Route', accent: 'Finish', thing: 'Drawn thing', scene: 'Designed scene' };

function section(root, title) {
  const s = el('section', 'ins-sec', root);
  el('h3', '', s, title);
  return s;
}

function chips(parent, options, current, pick) {
  const row = el('div', 'ins-chips', parent);
  for (const [value, label] of options) {
    const b = el('button', `ins-chip${value === current ? ' on' : ''}`, row, label);
    b.type = 'button';
    b.addEventListener('click', () => { if (value !== current) pick(value); });
  }
  return row;
}

// a slider that shows its value as it moves and sends it when let go
function slider(parent, label, { min, max, step, value, show, send }) {
  const wrap = el('label', 'ins-slider', parent);
  const top = el('span', 'ins-sl-top', wrap);
  el('span', '', top, label);
  const out = el('output', '', top, show(value));
  const input = el('input', '', wrap);
  Object.assign(input, { type: 'range', min, max, step, value });
  input.addEventListener('input', () => { out.textContent = show(parseFloat(input.value)); });
  input.addEventListener('change', () => send(parseFloat(input.value)));
  return input;
}

function button(parent, label, act, cls = '') {
  const b = el('button', `ed-btn small ${cls}`, parent, label);
  b.type = 'button';
  b.addEventListener('click', act);
  return b;
}

export function inspect(ed) {
  const root = document.querySelector('[data-inspector]');
  root.replaceChildren();
  const v = ed.view;
  if (!v) return;
  const sel = ed.sel;
  if (sel && sel.type === 'shot') shotPanel(ed, root, v.shots.find((s) => s.n === sel.n));
  else if (sel && sel.type === 'graphic') graphicPanel(ed, root, v.graphics.find((g) => g.index === sel.index));
  else if (sel && sel.type === 'music' && v.music) musicPanel(ed, root, v.music, sel.range);
  else if (sel && sel.type === 'over') overPanel(ed, root, v.over.find((o) => o.index === sel.index));
  else if (sel && sel.type === 'pick') pickPanel(ed, root, v, sel.index ?? -1);
  else filmPanel(ed, root, v);
}

function filmPanel(ed, root, v) {
  el('h2', 'ins-title', root, v.which === 'draft' ? 'Your draft' : `Version ${v.version}`);
  el('p', 'ins-meta', root, `${clock(v.duration, true)} · ${v.shots.length} shots · ${v.graphics.length} graphics`);
  if (v.said.length) {
    const s = section(root, 'Changed so far');
    const ul = el('ul', 'ins-said', s);
    for (const line of v.said.slice(-12)) el('li', '', ul, line);
  }
  const s = section(root, 'Pick something');
  el('p', 'ins-help', s, 'Tap a shot, a graphic or the music on the timeline to change it. Drag a shot’s edge to trim it, '
    + 'the shot itself to move it; S splits at the playhead.');
  if (v.music) button(s, 'The music', () => ed.select({ type: 'music' }));
}

function shotPanel(ed, root, s) {
  if (!s) { filmPanel(ed, root, ed.view); return; }
  el('h2', 'ins-title', root, `Shot ${s.n}`);
  el('p', 'ins-meta', root, `${clock(s.start, true)} – ${clock(s.end, true)} · ${(s.end - s.start).toFixed(2)} s · ${s.short}`);
  const cut = section(root, 'Cut');
  const row = el('div', 'ins-row', cut);
  const inside = ed.t > s.start + 0.25 && ed.t < s.end - 0.25;
  const split = button(row, 'Split at playhead', () => ed.edit([{ op: 'split', shot: s.n, seconds: +(ed.t - s.start).toFixed(3) }]));
  split.disabled = !inside;
  split.title = inside ? 'S' : 'Put the playhead inside this shot first';
  button(row, 'Duplicate', () => ed.edit([{ op: 'duplicate', shot: s.n }]));
  button(row, 'Delete', () => ed.edit([{ op: 'delete', shot: s.n, ripple: ed.ripple }]), 'warn');
  const order = el('div', 'ins-row', cut);
  const back = button(order, '← Earlier', () => ed.edit([{ op: 'move', shot: s.n, to: s.n - 1 }]));
  back.disabled = s.n === 1;
  const fwd = button(order, 'Later →', () => ed.edit([{ op: 'move', shot: s.n, to: s.n + 1 }]));
  fwd.disabled = s.n === ed.view.shots.length;
  const spd = section(root, 'Speed');
  const rate = s.rate || 1;
  chips(spd, SPEEDS.map((x) => [x, `${x}x`]), SPEEDS.find((x) => Math.abs(x - rate) < 1e-3),
    (x) => ed.edit([{ op: 'speed', shot: s.n, factor: x, ripple: ed.ripple }]));
  if (s.kind === 'ramp') el('p', 'ins-help', spd, 'It speeds up, then slows down. Picking a speed plays it evenly instead.');
  const snd = section(root, 'Its own sound');
  const mute = el('label', 'ins-check', snd);
  const box = el('input', '', mute);
  box.type = 'checkbox';
  box.checked = !s.mute && s.sound;
  el('span', '', mute, 'Heard');
  box.addEventListener('change', () => ed.edit([{ op: 'volume', shot: s.n, mute: !box.checked }]));
  slider(snd, 'Level', { min: -30, max: 12, step: 0.5, value: s.gain_db || 0, show: (x) => `${x > 0 ? '+' : ''}${x} dB`,
    send: (x) => ed.edit([{ op: 'volume', shot: s.n, gain_db: x }]) });
  const fr = section(root, 'Framing');
  slider(fr, 'Punch in', { min: 1, max: 2.5, step: 0.05, value: s.zoom || 1, show: (x) => `${x.toFixed(2)}x`,
    send: (x) => ed.edit([{ op: 'frame', shot: s.n, zoom: x }]) });
  slider(fr, 'Move in the shot', { min: -0.4, max: 0.4, step: 0.02, value: s.push || 0,
    show: (x) => (Math.abs(x) < 0.005 ? 'still' : x > 0 ? `push in ${Math.round(x * 100)}%` : `pull out ${Math.round(-x * 100)}%`),
    send: (x) => ed.edit([{ op: 'frame', shot: s.n, push: x }]) });
  if (ed.view.h > ed.view.w) {
    slider(fr, 'Where it sits (left · right)', { min: 0, max: 1, step: 0.01, value: s.frame_x ?? 0.5,
      show: (x) => `${Math.round(x * 100)}%`, send: (x) => ed.edit([{ op: 'frame', shot: s.n, frame_x: x }]) });
  }
  if ((s.zoom || 1) > 1.001) {
    slider(fr, 'Punch-in centre (left · right)', { min: 0, max: 1, step: 0.01, value: s.zoom_center[0],
      show: (x) => `${Math.round(x * 100)}%`, send: (x) => ed.edit([{ op: 'frame', shot: s.n, zoom_center: [x, s.zoom_center[1]] }]) });
  }
  keysSection(ed, root, s);
  const flip = el('label', 'ins-check', fr);
  const fb = el('input', '', flip);
  fb.type = 'checkbox';
  fb.checked = s.flip;
  el('span', '', flip, 'Mirrored');
  fb.addEventListener('change', () => ed.edit([{ op: 'frame', shot: s.n, flip: fb.checked }]));
  if (s.n > 1) {
    const bl = section(root, 'Comes in with');
    chips(bl, BLENDS, s.blend, (x) => ed.edit([{ op: 'blend', shot: s.n, style: x, seconds: s.blend_seconds || 0.5 }]));
    if (s.blend !== 'cut') {
      slider(bl, 'Length', { min: 0.1, max: 2, step: 0.05, value: s.blend_seconds || 0.5, show: (x) => `${x.toFixed(2)} s`,
        send: (x) => ed.edit([{ op: 'blend', shot: s.n, style: s.blend, seconds: x }]) });
    }
  }
}

// keyframes: the framing at moments of the shot (zoom and where it looks), eased between; set by dragging and
// scrolling on the picture at the playhead, or here
function keysSection(ed, root, s) {
  const sec = section(root, 'Keyframes');
  const keys = (s.keys || []).map((k) => [...k]);
  const fps = ed.view.fps || 30;
  const into = ed.t - s.start;
  const inside = into >= 0 && ed.t < s.end;
  const here = keys.findIndex((k) => Math.abs(k[0] - into) < 1 / fps);
  const send = (next) => ed.edit([{ op: 'keys', shot: s.n, keys: next }]);
  const reframe = el('label', 'ins-check', sec);
  const rb = el('input', '', reframe);
  rb.type = 'checkbox';
  rb.checked = ed.reframing;
  el('span', '', reframe, 'Reframe on the picture (drag it, scroll to zoom)');
  rb.addEventListener('change', () => ed.setReframing(rb.checked));
  if (keys.length) {
    for (const [k, key] of keys.entries()) {
      const row = el('div', 'ins-row', sec);
      const go = button(row, `${clock(key[0], true)} · ${key[1].toFixed(2)}x`, () => ed.seek(s.start + key[0] + 0.001), k === here ? 'on' : '');
      go.title = 'Go to this keyframe';
      button(row, '×', () => send(keys.filter((_, j) => j !== k)), 'icon');
    }
  }
  if (here >= 0) {
    const key = keys[here];
    const set = (i, x) => { const next = keys.map((k) => [...k]); next[here][i] = x; send(next); };
    slider(sec, 'Zoom', { min: 1, max: 3, step: 0.05, value: key[1], show: (x) => `${x.toFixed(2)}x`, send: (x) => set(1, x) });
    slider(sec, 'Looks at (left · right)', { min: 0, max: 1, step: 0.01, value: key[2], show: (x) => `${Math.round(x * 100)}%`, send: (x) => set(2, x) });
    slider(sec, 'Looks at (top · bottom)', { min: 0, max: 1, step: 0.01, value: key[3], show: (x) => `${Math.round(x * 100)}%`, send: (x) => set(3, x) });
  } else {
    const add = button(sec, 'Add a keyframe at the playhead', () => {
      const now = ed.keyedNow(s) || [1, 0.5, 0.5];
      send([...keys, [+into.toFixed(3), ...now]].sort((a, b) => a[0] - b[0]));
    });
    add.disabled = !inside;
    if (!inside) add.title = 'Put the playhead inside this shot first';
  }
  if (keys.length > 1) el('p', 'ins-help', sec, 'Between keyframes the picture moves smoothly: a slow push, a pan to someone, a reveal.');
}

function overPanel(ed, root, o) {
  if (!o) { filmPanel(ed, root, ed.view); return; }
  el('h2', 'ins-title', root, `Laid over: ${o.short}`);
  el('p', 'ins-meta', root, `${clock(o.start, true)} – ${clock(o.end, true)} · from ${clock(o.src_in, true)} of ${o.short}`);
  el('p', 'ins-help', root, 'Its picture covers the film’s for this stretch; the film’s own sound goes on under it.');
  const shot = section(root, 'The shot');
  const row = el('div', 'ins-row', shot);
  button(row, 'Another shot…', () => ed.select({ type: 'pick', index: o.index }));
  button(row, 'Take it away', () => ed.edit([{ op: 'over', index: o.index, remove: true }]), 'warn');
  const spd = section(root, 'Speed');
  chips(spd, SPEEDS.map((x) => [x, `${x}x`]), SPEEDS.find((x) => Math.abs(x - (o.rate || 1)) < 1e-3),
    (x) => ed.edit([{ op: 'over', index: o.index, factor: x }]));
  const fr = section(root, 'Framing');
  slider(fr, 'Punch in', { min: 1, max: 2.5, step: 0.05, value: o.zoom || 1, show: (x) => `${x.toFixed(2)}x`,
    send: (x) => ed.edit([{ op: 'over', index: o.index, zoom: x }]) });
  if (ed.view.h > ed.view.w) {
    slider(fr, 'Where it sits (left · right)', { min: 0, max: 1, step: 0.01, value: o.frame_x ?? 0.5,
      show: (x) => `${Math.round(x * 100)}%`, send: (x) => ed.edit([{ op: 'over', index: o.index, frame_x: x }]) });
  }
}

// the film's footage, moment by moment, to lay over the picture at the playhead (or to swap into a laid-over shot)
function pickPanel(ed, root, v, index) {
  el('h2', 'ins-title', root, index >= 0 ? 'Another shot' : 'Lay a shot over');
  el('p', 'ins-meta', root, index >= 0 ? 'Pick what it shows instead' : `At ${clock(ed.t, true)}: pick a moment of your footage`);
  for (const f of v.footage) {
    const sec = section(root, `${f.short} · ${f.seconds.toFixed(1)} s`);
    const moments = f.moments.length ? f.moments : [{ start: 0, end: f.seconds, what: 'The whole clip' }];
    for (const m of moments) {
      const card = el('button', 'ins-moment', sec);
      card.type = 'button';
      const thumb = el('canvas', '', card);
      thumb.width = 96;
      thumb.height = 54;
      const lay = (v.sprites || {})[f.clip];
      if (lay) drawThumb(thumb, lay, (m.start + m.end) / 2);
      el('span', '', card, `${clock(m.start, true)}–${clock(m.end, true)} · ${m.what}`);
      card.addEventListener('click', () => {
        const old = index >= 0 ? v.over.find((o) => o.index === index) : null;
        const seconds = old ? old.end - old.start : Math.min(3, Math.max(0.8, m.end - m.start));
        const at = old ? old.start : ed.t;
        ed.edit([{ op: 'over', index, clip: f.short, src_in: m.start, at: +at.toFixed(3), until: +(at + seconds).toFixed(3) }]);
      });
    }
  }
}

const thumbs = new Map();
function drawThumb(canvas, lay, t) {
  const go = (img) => {
    if (!img || !canvas.isConnected) return;
    const f = Math.min(lay.n - 1, Math.max(0, Math.round(t * lay.fps)));
    const ctx = canvas.getContext('2d');
    const scale = Math.max(canvas.width / lay.w, canvas.height / lay.h);
    ctx.drawImage(img, (f % lay.cols) * lay.w, Math.floor(f / lay.cols) * lay.h, lay.w, lay.h,
      (canvas.width - lay.w * scale) / 2, (canvas.height - lay.h * scale) / 2, lay.w * scale, lay.h * scale);
  };
  if (!thumbs.has(lay.src)) {
    thumbs.set(lay.src, new Promise((done) => {
      const img = new Image();
      img.onload = () => done(img);
      img.onerror = () => done(null);
      img.src = lay.src;
    }));
  }
  thumbs.get(lay.src).then(go);
}

function graphicPanel(ed, root, g) {
  if (!g) { filmPanel(ed, root, ed.view); return; }
  el('h2', 'ins-title', root, WHAT[g.kind] || g.kind);
  el('p', 'ins-meta', root, `${clock(g.start, true)} – ${clock(g.end, true)}${g.drawn ? '' : ' · not drawn in this draft'}`);
  if (g.kind === 'scene') {
    el('p', 'ins-help', root, 'A designed scene is part of the picture where it was designed. Ask Cello to design it again elsewhere.');
    return;
  }
  const words = section(root, 'Words');
  if (!['captions', 'transition', 'accent'].includes(g.kind)) {
    const t = el('input', 'ins-text', words);
    t.value = g.text;
    t.placeholder = g.kind === 'route' ? 'Sydney|Wollongong|Kiama' : 'Its words';
    t.addEventListener('change', () => ed.edit([{ op: 'graphic', index: g.index, text: t.value }]));
    if (['title', 'lowerthird'].includes(g.kind)) {
      const sub = el('input', 'ins-text', words);
      sub.value = g.sub;
      sub.placeholder = 'A line under it (optional)';
      sub.addEventListener('change', () => ed.edit([{ op: 'graphic', index: g.index, sub: sub.value }]));
    }
  } else if (g.kind === 'captions') {
    el('p', 'ins-help', words, 'What is said, word by word, from their own voices.');
  }
  if (STYLES[g.kind]) {
    chips(words, STYLES[g.kind].map((x) => [x, x[0].toUpperCase() + x.slice(1)]), g.style || STYLES[g.kind][0],
      (x) => ed.edit([{ op: 'graphic', index: g.index, style: x }]));
  }
  if (['title', 'captions', 'lowerthird', 'kinetic', 'callout'].includes(g.kind)) {
    const look = section(root, 'Size and place');
    slider(look, 'Size', { min: 0.4, max: 3, step: 0.05, value: g.scale || 1, show: (x) => `${Math.round(x * 100)}%`,
      send: (x) => ed.edit([{ op: 'graphic', index: g.index, scale: x }]) });
    if (g.kind !== 'callout') {
      chips(look, [['auto', 'Auto'], ['top', 'Top'], ['middle', 'Middle'], ['bottom', 'Bottom']], g.place || 'auto',
        (x) => ed.edit([{ op: 'graphic', index: g.index, place: x }]));
    }
  }
  const when = section(root, 'When');
  const row = el('div', 'ins-row', when);
  const at = el('input', 'ins-num', row);
  Object.assign(at, { type: 'number', step: 0.05, min: 0, value: g.start.toFixed(2) });
  el('span', 'ins-dash', row, 'to');
  const until = el('input', 'ins-num', row);
  Object.assign(until, { type: 'number', step: 0.05, min: 0, value: g.end.toFixed(2) });
  const send = () => ed.edit([{ op: 'graphic', index: g.index, at: parseFloat(at.value), until: parseFloat(until.value) }]);
  at.addEventListener('change', send);
  until.addEventListener('change', send);
  const nowRow = el('div', 'ins-row', when);
  button(nowRow, 'Start at playhead', () => ed.edit([{ op: 'graphic', index: g.index, at: +ed.t.toFixed(3), until: +(ed.t + (g.end - g.start)).toFixed(3) }]));
  button(nowRow, 'End at playhead', () => ed.edit([{ op: 'graphic', index: g.index, at: g.start, until: +ed.t.toFixed(3) }]));
  const gone = section(root, '');
  button(gone, 'Take it away', () => ed.edit([{ op: 'graphic', index: g.index, remove: true }]), 'warn');
}

function musicPanel(ed, root, m, range) {
  const edit = { slide: 0, gain_db: 0, fade_in: null, fade_out: null, duck_db: null, points: [], jumps: [], ...(m.edit || {}) };
  el('h2', 'ins-title', root, 'The music');
  el('p', 'ins-meta', root, `${Math.round(m.tempo)} beats a minute · the song is ${clock(m.duration, true)}`);
  if (range) {
    const r = section(root, `${clock(range.a, true)} – ${clock(range.b, true)} of the film`);
    const sa = songAt(m, range.a);
    const sb = songAt(m, range.b);
    const row = el('div', 'ins-row', r);
    const jumpsBut = (lo, hi) => edit.jumps.filter((j) => j.at < lo - 1e-3 || j.at >= hi - 1e-3); // the stretch's own go
    button(row, 'Cut this out', () => ed.editMusic({ jumps: [...jumpsBut(range.a, range.b), { at: +range.a.toFixed(3), to: +sb.toFixed(3) }] }));
    button(row, 'Play it again', () => ed.editMusic({ jumps: [...edit.jumps.filter((j) => Math.abs(j.at - range.b) > 1e-3), { at: +range.b.toFixed(3), to: +sa.toFixed(3) }] }));
    if (m.remake) {
      const want = el('input', 'ins-text', r);
      want.placeholder = 'Make it again: calmer, with strings…';
      const go = button(r, 'Make this part again', () => ed.remakeMusic(sa, sb, want.value));
      go.title = 'Writes this stretch of the made track again (the rest kept note for note) and makes a new version: a few minutes';
    }
    el('p', 'ins-help', r, 'Song cuts land best on a bar: the stretch snaps to bars and beats as you drag.');
  }
  const lv = section(root, 'Level');
  slider(lv, 'Music level', { min: -30, max: 12, step: 0.5, value: edit.gain_db, show: (x) => `${x > 0 ? '+' : ''}${x} dB`,
    send: (x) => ed.editMusic({ gain_db: x }) });
  slider(lv, 'Dips under the voice', { min: -30, max: 0, step: 1, value: edit.duck_db ?? m.duck_db,
    show: (x) => (x === 0 ? 'no dip' : `${x} dB`), send: (x) => ed.editMusic({ duck_db: x }) });
  el('p', 'ins-help', lv, 'With the music selected, tap its line on the timeline to add a level point; drag one; double-tap to take it away.');
  const fd = section(root, 'Fades');
  slider(fd, 'Fade in', { min: 0, max: 5, step: 0.1, value: edit.fade_in ?? m.fade_in, show: (x) => `${x.toFixed(1)} s`,
    send: (x) => ed.editMusic({ fade_in: x }) });
  slider(fd, 'Fade out', { min: 0, max: 8, step: 0.1, value: edit.fade_out ?? m.fade_out, show: (x) => `${x.toFixed(1)} s`,
    send: (x) => ed.editMusic({ fade_out: x }) });
  const sl = section(root, 'Against the picture');
  slider(sl, 'Slide the song', { min: -4, max: 4, step: 0.01, value: edit.slide, show: (x) => (Math.abs(x) < 0.005 ? 'as cut'
    : `${Math.abs(x).toFixed(2)} s ${x > 0 ? 'later' : 'earlier'}`), send: (x) => ed.editMusic({ slide: x }) });
  el('p', 'ins-help', sl, 'Or Shift+drag the music on the timeline.');
  if (edit.jumps.length) {
    const cuts = section(root, 'Cuts in the song');
    for (const j of edit.jumps) {
      const row = el('div', 'ins-row', cuts);
      el('span', 'ins-cut', row, `${clock(j.at, true)} → song ${clock(j.to, true)}`);
      button(row, '×', () => ed.editMusic({ jumps: edit.jumps.filter((x) => x !== j) }), 'icon');
    }
  }
  const reset = section(root, '');
  button(reset, 'Music as it was cut', () => ed.editMusic({ slide: 0, gain_db: 0, fade_in: null, fade_out: null, duck_db: null,
    points: [], jumps: [] }, true));
}
