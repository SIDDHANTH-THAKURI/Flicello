// The studio's moving parts: resumable uploads (with a frame from each clip as its thumbnail before it has even
// finished uploading), live progress while a step runs, the rating slider, and the new-film form.
import { toast } from './ui.js';

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const csrf = () => (document.querySelector('meta[name="csrf"]') || {}).content || '';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(method, url, data, isBlob) {
  const opts = { method, headers: { 'X-CSRF': csrf() }, credentials: 'same-origin' };
  if (data !== undefined) {
    if (isBlob) { opts.body = data; opts.headers['Content-Type'] = 'application/octet-stream'; }
    else { opts.body = JSON.stringify(data); opts.headers['Content-Type'] = 'application/json'; }
  }
  const res = await fetch(url, opts);
  let json = {};
  try { json = await res.json(); } catch { /* empty body */ }
  if (!res.ok) { const err = new Error(json.error || `Error ${res.status}`); err.status = res.status; throw err; }
  return json;
}

// a still from the clip itself, made in the browser (nothing is sent anywhere for it)
function thumbFor(file, img) {
  if (!file.type.startsWith('video/') && !/\.(mov|mp4|m4v|mkv|webm)$/i.test(file.name)) return;
  const url = URL.createObjectURL(file);
  const v = document.createElement('video');
  v.muted = true; v.playsInline = true; v.preload = 'metadata'; v.src = url;
  v.addEventListener('loadeddata', () => { v.currentTime = Math.min(1, (v.duration || 2) / 3); }, { once: true });
  v.addEventListener('seeked', () => {
    try {
      const c = document.createElement('canvas');
      const h = 100, w = Math.round(h * (v.videoWidth / Math.max(1, v.videoHeight)));
      c.width = w; c.height = h;
      c.getContext('2d').drawImage(v, 0, 0, w, h);
      img.src = c.toDataURL('image/jpeg', 0.7);
    } catch { /* some codecs (HEVC on some browsers) can't be drawn: keep the blank */ }
    URL.revokeObjectURL(url);
  }, { once: true });
}

function row(list, file) {
  const li = document.createElement('li');
  const img = document.createElement('img');
  img.className = 'thumb'; img.alt = '';
  thumbFor(file, img);
  const name = document.createElement('span');
  name.className = 'name'; name.textContent = file.name;
  const sub = document.createElement('span');
  sub.className = 'sub'; sub.textContent = 'waiting';
  const size = document.createElement('span');
  size.className = 'sub'; size.textContent = `${(file.size / 1e6).toFixed(file.size > 1e8 ? 0 : 1)} MB`;
  const bar = document.createElement('div');
  bar.className = 'bar';
  const fill = document.createElement('span');
  bar.appendChild(fill);
  li.append(img, name, size, sub, bar);
  list.appendChild(li);
  return {
    set(f, text) { fill.style.width = `${Math.round(f * 100)}%`; sub.textContent = text || `${Math.round(f * 100)}%`; },
    ok() { li.classList.add('ok'); sub.textContent = '✓'; },
    fail(msg) { li.classList.add('off'); sub.textContent = ''; name.textContent = `${file.name} · ${msg}`; },
  };
}

let uploading = 0;

// One piece at `offset`, retried through a flaky connection; returns how many bytes the server has.
async function putPiece(id, bytes, offset, ui, frac) {
  let failures = 0;
  for (;;) {
    try {
      return (await api('PUT', `/api/uploads/${id}?offset=${offset}`, bytes, true)).received;
    } catch (err) {
      if (err.status && err.status >= 400 && err.status < 500) throw err; // the server said no: stop
      failures += 1;
      if (failures > 8) throw new Error('connection lost; try again');
      ui.set(frac, 'reconnecting…');
      await sleep(Math.min(30000, 1000 * 2 ** failures));
      try { // the piece may have arrived and only the answer got lost
        const st = await api('GET', `/api/uploads/${id}`);
        if (st.received === offset + bytes.byteLength) return st.received;
      } catch { /* retry */ }
    }
  }
}

async function sendOriginal(pid, file, kind, ui) {
  const start = await api('POST', `/api/p/${pid}/uploads`, { kind, name: file.name, size: file.size });
  const chunk = start.chunk;
  let received = start.received, failures = 0;
  while (received < file.size) {
    const piece = file.slice(received, Math.min(file.size, received + chunk));
    try {
      const r = await api('PUT', `/api/uploads/${start.id}?offset=${received}`, piece, true);
      received = r.received; failures = 0;
      ui.set(received / file.size);
    } catch (err) {
      if (err.status && err.status >= 400 && err.status < 500) throw err; // the server said no: stop
      failures += 1;
      if (failures > 8) throw new Error('connection lost; try again');
      ui.set(received / file.size, 'reconnecting…');
      await sleep(Math.min(30000, 1000 * 2 ** failures));
      try { received = (await api('GET', `/api/uploads/${start.id}`)).received; } catch { /* retry */ }
    }
  }
  ui.set(1, 'checking…');
  return api('POST', `/api/uploads/${start.id}/done`);
}

// A clip is first made lighter on this device when that helps a lot (see shrink.js), and sent while it is made;
// anything unusual and the original goes instead.
async function uploadFile(pid, file, kind, ui) {
  if (kind === 'clip') {
    let p = null;
    try {
      const { plan } = await import('./shrink.js');
      ui.set(0, 'checking…');
      p = await plan(file, document.body.dataset.shapes, document.body.dataset.kind);
    } catch { p = null; }
    if (p) {
      let start = null;
      try {
        const { convertAndSend } = await import('./shrink.js');
        start = await api('POST', `/api/p/${pid}/uploads`, { kind, name: `${file.name.replace(/\.[^.]+$/, '')}.mp4`, size: file.size });
        let frac = 0;
        const size = await convertAndSend(p, start.chunk, (bytes, offset) => putPiece(start.id, bytes, offset, ui, frac), (f) => {
          frac = f;
          ui.set(f, `lighter on your device · ${Math.round(f * 100)}%`);
        }, file.size);
        ui.set(1, 'checking…');
        return await api('POST', `/api/uploads/${start.id}/done`, { size });
      } catch (err) {
        console.warn('Flicello: sending the original instead of a lighter copy:', err && err.message);
        if (!start && err.status && err.status >= 400 && err.status < 500) throw err; // refused (limits): same for the original
        if (start) api('POST', `/api/uploads/${start.id}/cancel`).catch(() => {});
        ui.set(0, 'sending the original…');
      }
    }
  }
  return sendOriginal(pid, file, kind, ui);
}

async function uploadAll(pid, files, kind, list) {
  uploading += 1;
  let ok = 0;
  const queue = files.map((file) => ({ file, ui: row(list, file) }));
  const lane = async () => { // two at a time: a slow first file doesn't hold up the rest
    for (let job = queue.shift(); job; job = queue.shift()) {
      try { await uploadFile(pid, job.file, kind, job.ui); job.ui.ok(); ok += 1; } catch (err) { job.ui.fail(err.message); }
    }
  };
  await Promise.all([lane(), lane()]);
  uploading -= 1;
  if (ok && !uploading) setTimeout(() => window.location.reload(), 700);
}

function uploads(pid) {
  const clipInput = $('#clip-input');
  const songInput = $('#song-input');
  const list = $('#upload-list');
  if (clipInput && list) {
    clipInput.addEventListener('change', () => { uploadAll(pid, [...clipInput.files], 'clip', list); clipInput.value = ''; });
    const drop = $('#drop');
    if (drop) {
      ['dragenter', 'dragover'].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add('over'); }));
      ['dragleave', 'drop'].forEach((e) => drop.addEventListener(e, () => drop.classList.remove('over')));
      drop.addEventListener('drop', (ev) => {
        ev.preventDefault();
        const files = [...ev.dataTransfer.files].filter((f) => f.type.startsWith('video/') || /\.(mov|mp4|m4v|mkv|webm)$/i.test(f.name));
        if (files.length) uploadAll(pid, files, 'clip', list);
      });
    }
  }
  if (songInput) {
    songInput.addEventListener('change', () => {
      if (songInput.files[0]) uploadAll(pid, [songInput.files[0]], 'song', $('#song-list'));
      songInput.value = '';
    });
  }
  window.addEventListener('beforeunload', (ev) => { if (uploading) { ev.preventDefault(); ev.returnValue = ''; } });
  $$('[data-delete]').forEach((btn) => btn.addEventListener('click', async () => {
    btn.disabled = true;
    try {
      await api('POST', `/api/p/${pid}/clips/${btn.dataset.delete}/delete`);
      btn.closest('li').remove();
      window.location.reload();
    } catch (err) { btn.disabled = false; toast(err.message); }
  }));
}

function progress(pid) {
  const body = document.body;
  const bar = $('#bar');
  const meter = $('#meter');
  const pct = (f) => `${(Math.max(0, Math.min(1, f)) * 100).toFixed(1)}%`;
  if (bar && bar.dataset.progress) bar.style.width = pct(parseFloat(bar.dataset.progress));
  if (body.dataset.busy !== '1') return;
  const stage = body.dataset.stage;
  const msg = $('#job-msg');
  const eta = $('#job-eta');
  const list = $('#steps');
  let misses = 0;
  // What the bar shows: never less than before. While a step runs it creeps on slowly between updates (towards where
  // the time left says it should be, never more than 4% past the last real value), so a long step never looks frozen;
  // while the video waits for its turn nothing is happening, so nothing creeps and the track shows it is waiting.
  let shown = bar ? parseFloat(bar.dataset.progress || '0') || 0 : 0;
  let real = shown;
  let waiting = meter ? meter.classList.contains('waiting') : false;
  let left = null; // seconds left as shown, counted down every second
  let lastTick = Date.now();
  const paint = () => {
    if (bar) bar.style.width = pct(shown);
    if (meter) meter.classList.toggle('waiting', waiting);
  };
  const say = () => {
    if (!eta) return;
    eta.textContent = left === null ? '' :
      `${left < 45 ? 'under a minute' : left < 90 ? 'about a minute' : `about ${Math.round(left / 60)} min`} left`;
  };
  const ago = (n) => (n < 60 ? `${n} s` : n < 3600 ? `${Math.floor(n / 60)} min ${String(n % 60).padStart(2, '0')} s`
    : `${Math.floor(n / 3600)} h ${Math.floor((n % 3600) / 60)} min`);
  let steps = [];
  let stepsAt = Date.now();
  const drawSteps = () => {
    if (!list) return;
    const grow = Math.round((Date.now() - stepsAt) / 1000);
    list.replaceChildren(...steps.map((st) => {
      const li = document.createElement('li');
      li.className = st.now ? 'now' : 'done';
      const m = document.createElement('span');
      m.textContent = st.m;
      const t = document.createElement('time');
      t.textContent = ago(st.s + (st.now ? grow : 0)); // the running step's time keeps counting between updates
      li.append(m, t);
      return li;
    }));
  };
  setInterval(() => {
    const dt = (Date.now() - lastTick) / 1000;
    lastTick = Date.now();
    if (left !== null) left = Math.max(20, left - dt);
    if (!waiting) {
      const rate = left ? (1 - shown) / left : 0.002;
      shown = Math.min(Math.max(shown, real) + rate * dt * 0.5, real + 0.04, 0.985);
    }
    paint();
    say();
    drawSteps();
  }, 1000);
  const poll = async () => {
    try {
      const st = await api('GET', `/api/p/${pid}/status`);
      misses = 0;
      if (st.stage !== stage || !st.busy) { window.location.reload(); return; }
      const started = waiting && !st.queued; // its turn came (also after a step was started again): from the real value
      waiting = !!st.queued;
      if (started) { real = st.progress || 0; shown = real; }
      else if (st.progress !== null) { real = Math.max(real, st.progress); shown = Math.max(shown, real); }
      paint();
      if (msg) msg.textContent = st.queued ? (st.queued_note || 'Waiting for its turn…') : (st.message || 'Working…');
      const s = st.eta;
      if (s === null || s === undefined) left = null;
      else if (left === null || Math.abs(s - left) > Math.max(30, 0.35 * left)) left = s; // big news: take it
      else left = 0.8 * left + 0.2 * s; // small news: ease towards it, no jumping
      say();
      steps = st.steps || [];
      stepsAt = Date.now();
      drawSteps();
    } catch (err) {
      misses += 1;
      if (err.status === 401) { window.location.href = '/signin'; return; }
    }
    setTimeout(poll, misses ? Math.min(20000, 2500 * misses) : 2500);
  };
  setTimeout(poll, 1500);
}

function rating() {
  const liked = $('#liked');
  const out = $('#liked-out');
  if (liked && out) liked.addEventListener('input', () => { out.firstChild.textContent = liked.value; });
}

function newFilm() {
  const form = $('#new-form');
  if (!form) return;
  const apply = () => {
    const kind = (form.querySelector('input[name="kind"]:checked') || {}).value || 'montage';
    $$('[data-kinds]', form).forEach((el) => { el.hidden = !el.dataset.kinds.split(' ').includes(kind); });
  };
  $$('input[name="kind"]', form).forEach((r) => r.addEventListener('change', apply));
  apply();
  // fields shown only for one choice (data-when="length=custom")
  const when = () => $$('[data-when]', form).forEach((el) => {
    const [name, value] = el.dataset.when.split('=');
    const on = (form.querySelector(`input[name="${name}"]:checked`) || {}).value === value;
    el.hidden = !on;
    if (on) { const first = el.querySelector('input'); if (first && document.activeElement?.name === name) first.focus(); }
  });
  form.addEventListener('change', when);
  when();
  // what the improvise slider means, in words, as it moves
  const slider = $('#improvise');
  const says = $('#improvise-says');
  if (slider && says) {
    const steps = JSON.parse(says.dataset.says);
    const show = () => { says.textContent = (steps.find(([top]) => Number(slider.value) <= top) || steps[steps.length - 1])[1]; };
    slider.addEventListener('input', show);
    show();
  }
}

function inviteLink() {
  const link = $('#invite-link');
  if (!link) return;
  const full = window.location.origin + link.dataset.path;
  link.textContent = `${full}  (tap to copy)`;
  link.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(full); link.textContent = `${full}  ✓ copied`; } catch { /* select by hand */ }
  });
}

function busyForms() {
  $$('form[data-busy-text]').forEach((form) => form.addEventListener('submit', () => {
    const btn = form.querySelector('button[type=submit]');
    if (btn) { btn.textContent = form.dataset.busyText; btn.disabled = true; }
  }));
}

function swatches() {  // a look's colours (set from script: the page allows no inline styles)
  $$('.sw[data-c]').forEach((el) => {
    if (/^#[0-9a-f]{6}$/i.test(el.dataset.c)) el.style.background = el.dataset.c;
  });
}

export function studio() {
  const pid = document.body.dataset.project;
  if (pid) { uploads(pid); progress(pid); }
  swatches();
  rating();
  newFilm();
  inviteLink();
  busyForms();
}
