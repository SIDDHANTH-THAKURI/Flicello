// The film page: your film and Cello, side by side. The player with its timeline (each shot or moment a bar on the
// film's sound, a playhead that follows it, tap anywhere to jump), point and say (click the film where something is,
// pick or type what should change there: the words go to Cello with the time and the place), the tabs kept in step
// with whatever opens them, the Cello / Both / Manual modes, and on a phone the conversation as a sheet you pull up.
// The conversation itself is one: Cello's voice lines arrive here from its shell and what is typed here goes to it
// when it is on (see editor.js).

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const mmss = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const framed = (() => { try { return window.top !== window && window.top.location.pathname === '/cello'; } catch { return false; } })();

// ---------------------------------------------------------------- Cello / Both / Manual
function modes(page) {
  const KEY = 'flicello-mode';
  const set = (m, quiet = false) => {
    page.classList.remove('mode-cello', 'mode-both', 'mode-manual');
    page.classList.add(`mode-${m}`);
    $$('.fp-mode [data-mode]', page).forEach((b) => b.setAttribute('aria-pressed', b.dataset.mode === m ? 'true' : 'false'));
    localStorage.setItem(KEY, m);
    if (page.dockShow && !quiet) page.dockShow(m !== 'manual');
  };
  $$('[data-mode]', page).forEach((b) => b.addEventListener('click', () => set(b.dataset.mode)));
  set(['cello', 'both', 'manual'].includes(localStorage.getItem(KEY)) ? localStorage.getItem(KEY) : 'cello', true);
}

// ---------------------------------------------------------------- tabs: opened from buttons, or by Cello pointing at a part
function tabGo(page) {
  const bar = $('[data-tabs="tab"]', page);
  if (!bar) return;
  $$('[data-tab-go]', page).forEach((b) => b.addEventListener('click', () => {
    if (bar.show) bar.show(b.dataset.tabGo);
    bar.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'nearest' });
  }));
  // Cello's reveal() unhides a panel directly: the tab bar follows
  new MutationObserver((list) => {
    for (const m of list) {
      const el = m.target;
      if (el.classList.contains('tabpanel') && !el.hidden && bar.show) {
        const on = $('[role="tab"][aria-selected="true"]', bar);
        if (!on || on.dataset.t !== el.dataset.p) bar.show(el.dataset.p, false);
      }
    }
  }).observe(page, { subtree: true, attributes: true, attributeFilter: ['hidden'] });
}

// ---------------------------------------------------------------- the player, the timeline, point and say
function player(page) {
  const box = $('[data-player]', page);
  if (!box) return;
  const video = $('[data-film-video]', box);
  const play = $('[data-play]', box);
  const tc = $('[data-tc]', box);
  const tip = $('[data-ptip]', box);
  const tl = $('[data-tl]', page);
  const ph = tl && $('[data-ph]', tl);
  const bars = tl ? $$('.mb', tl) : [];
  const rows = $$('.mrow', page);
  const words = $$('[data-words] [data-t]', page);
  let dur = parseFloat(tl?.dataset.duration || '0') || 0;
  const icon = (name) => `<svg class="ico"><use href="#i-${name}"/></svg>`;

  const layout = () => {
    bars.forEach((b) => {
      const a = parseFloat(b.dataset.start);
      const e = parseFloat(b.dataset.end);
      if (!dur) return;
      b.style.left = `${(a / dur) * 100}%`;
      b.style.width = `${Math.max(0.6, ((e - a) / dur) * 100)}%`;
    });
  };
  const sync = () => {
    const t = video.currentTime || 0;
    if (video.duration && Math.abs(video.duration - dur) > 0.3) { dur = video.duration; layout(); }
    tc.textContent = `${mmss(t)} / ${mmss(dur || video.duration || 0)}`;
    if (ph && dur) ph.style.left = `${(t / dur) * 100}%`;
    bars.forEach((b) => b.classList.toggle('cur', t >= parseFloat(b.dataset.start) && t < parseFloat(b.dataset.end)));
    rows.forEach((r) => {
      const a = parseFloat(r.dataset.start);
      const e = parseFloat(r.dataset.end);
      const on = t >= a && t < e;
      r.classList.toggle('cur', on);
      const fill = $('.fill', r);
      if (fill) fill.style.width = on ? `${((t - a) / (e - a)) * 100}%` : (t >= e ? '100%' : '0');
    });
    words.forEach((w) => {
      const a = parseFloat(w.dataset.t);
      w.classList.toggle('past', a < t - 0.4);
      w.classList.toggle('now', a <= t && a > t - 0.6);
    });
  };
  let raf = 0;
  const loop = () => { sync(); raf = video.paused ? 0 : requestAnimationFrame(loop); };
  video.addEventListener('play', () => { play.innerHTML = icon('pause'); if (!raf) raf = requestAnimationFrame(loop); });
  video.addEventListener('pause', () => { play.innerHTML = icon('play'); sync(); });
  ['loadedmetadata', 'seeked', 'timeupdate'].forEach((e) => video.addEventListener(e, sync));
  play.addEventListener('click', (e) => { e.stopPropagation(); if (video.paused) video.play().catch(() => {}); else video.pause(); });
  const seek = (t) => { video.currentTime = Math.max(0, Math.min((video.duration || dur) - 0.05, t)); sync(); };
  if (tl) {
    tl.addEventListener('click', (e) => {
      const r = tl.getBoundingClientRect();
      seek(((e.clientX - r.left) / r.width) * (video.duration || dur));
    });
    wave(tl);
  }
  rows.forEach((r) => r.addEventListener('click', () => { seek(parseFloat(r.dataset.start) + 0.02); video.play().catch(() => {}); }));
  words.forEach((w) => w.addEventListener('click', () => seek(parseFloat(w.dataset.t))));
  $$('.pshape [data-src]', box).forEach((b) => b.addEventListener('click', (e) => {
    e.stopPropagation();
    const t = video.currentTime;
    video.src = b.dataset.src;
    video.addEventListener('loadedmetadata', () => { video.currentTime = t; }, { once: true });
    $$('.pshape [data-src]', box).forEach((x) => x.setAttribute('aria-pressed', x === b ? 'true' : 'false'));
  }));
  layout();
  sync();
  pointAndSay(box, video, () => { if (tip) tip.classList.add('gone'); });
}

async function wave(tl) {
  const cv = $('[data-wave-canvas]', tl);
  if (!cv || !tl.dataset.wave) return;
  let peaks;
  try { peaks = await (await fetch(tl.dataset.wave, { credentials: 'same-origin' })).json(); } catch { return; }
  const draw = () => {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = cv.clientWidth * dpr;
    const h = cv.clientHeight * dpr;
    cv.width = w; cv.height = h;
    const c = cv.getContext('2d');
    c.clearRect(0, 0, w, h);
    const g = c.createLinearGradient(0, 0, w, 0);
    g.addColorStop(0, '#ffb347'); g.addColorStop(0.4, '#ff2e7e'); g.addColorStop(1, '#8f5bff');
    c.fillStyle = g;
    c.globalAlpha = 0.55;
    const n = peaks.length;
    const bw = w / n;
    for (let i = 0; i < n; i += 1) {
      const v = Math.max(0.04, peaks[i]) * h * 0.92;
      c.fillRect(i * bw, (h - v) / 2, Math.max(1, bw * 0.7), v);
    }
  };
  draw();
  new ResizeObserver(draw).observe(cv);
}

// click the film: a pin where you pointed, the time, and what to change there
const ASK = ['Make this bigger', 'Take this out', 'Change its colour', 'Move it', 'Hold here longer'];
function where(x, y) {
  const v = y < 0.34 ? 'top' : y > 0.66 ? 'bottom' : 'middle';
  const h = x < 0.34 ? 'left' : x > 0.66 ? 'right' : 'centre';
  return v === 'middle' && h === 'centre' ? 'the centre' : v === 'middle' ? `the ${h}` : `the ${v} ${h === 'centre' ? '' : h}`.trim();
}
function pointAndSay(box, video, used) {
  let pin = null;
  let pop = null;
  const clear = () => { pin?.remove(); pop?.remove(); pin = null; pop = null; };
  box.addEventListener('click', (e) => {
    if (e.target.closest('.pctl, .pshape, .pop')) return;
    used();
    clear();
    video.pause();
    const r = box.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width;
    const y = (e.clientY - r.top) / r.height;
    const t = video.currentTime || 0;
    pin = document.createElement('span');
    pin.className = 'pin';
    pin.style.left = `${x * 100}%`;
    pin.style.top = `${y * 100}%`;
    pop = document.createElement('div');
    pop.className = 'pop';
    pop.style.left = `${Math.min(0.78, Math.max(0.22, x)) * 100}%`;
    pop.style.top = `${Math.min(0.62, y) * 100}%`;
    const small = document.createElement('small');
    small.textContent = `${mmss(t)} · ${where(x, y)}`;
    const chips = document.createElement('div');
    chips.className = 'chips';
    const place = `At ${mmss(t)}, at ${where(x, y)} of the frame (where I pointed): `;
    const talk = (text) => {
      const area = $('#editor textarea');
      if (!area) return;
      area.value = place + (text || '');
      area.dispatchEvent(new Event('input'));
      area.focus();
      area.setSelectionRange(area.value.length, area.value.length);
      if (text) area.form.requestSubmit();
      clear();
      const fp = $('[data-filmpage]');
      fp?.classList.add('dock-open');
      fp?.dockShow?.(true);
    };
    ASK.forEach((a) => {
      const c = document.createElement('button');
      c.type = 'button';
      c.className = 'chip';
      c.textContent = a;
      c.addEventListener('click', (ev) => { ev.stopPropagation(); talk(a.toLowerCase()); });
      chips.append(c);
    });
    const own = document.createElement('button');
    own.type = 'button';
    own.className = 'chip own';
    own.textContent = 'Say it my way…';
    own.addEventListener('click', (ev) => { ev.stopPropagation(); talk(''); });
    chips.append(own);
    pop.append(small, chips);
    box.append(pin, pop);
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') clear(); });
}

// ---------------------------------------------------------------- the dock: Cello's drawer at the window's right edge
// Held to the edge under the top bar, full height. Drag its edge to make it wider (drag it nearly shut and it closes),
// close it to a tab and pull the tab (or tap it, or press Alt+C) to bring it back; its width and whether it is open
// are kept. Cello's shell is told, so its floating orb steps aside while the drawer is open. On a phone the drawer is
// a sheet you pull up instead.
const DOCK = 'flicello-dock';
const MIN_W = 320;
const SHUT_W = 220;
const phone = () => window.matchMedia('(max-width: 860px)').matches;
const maxW = () => Math.min(760, Math.round(window.innerWidth * 0.62));
function dockState() {
  try { const v = JSON.parse(localStorage.getItem(DOCK)); if (v && typeof v.w === 'number') return v; } catch { /* default */ }
  return { open: true, w: 400 };
}

function dock(page) {
  const d = $('[data-dock]', page);
  if (!d) return;
  const html = document.documentElement;
  const tab = $('[data-dock-tab]', page);
  const edge = $('[data-dock-edge]', d);
  const st = dockState();
  const toShell = (msg) => {
    if (framed) window.top.postMessage(msg, location.origin);
    else window.location.href = `/cello?to=${encodeURIComponent(window.location.pathname + window.location.search)}`;
  };
  const tellShell = () => {
    if (!framed) return;
    const open = st.open && !phone();
    window.top.postMessage({ cello: 'dock', open, w: open ? st.w : 0 }, location.origin);
  };
  const top = () => { // the drawer starts under the top bar
    const bar = $('.app-top');
    html.style.setProperty('--dock-top', `${bar ? Math.max(0, bar.getBoundingClientRect().bottom) : 0}px`);
  };
  const width = (w) => {
    st.w = Math.max(MIN_W, Math.min(maxW(), Math.round(w)));
    html.style.setProperty('--dockw', `${st.w}px`);
  };
  const save = () => localStorage.setItem(DOCK, JSON.stringify(st));
  const show = (open, keep = true) => {
    st.open = open;
    html.classList.add('has-dock');
    html.classList.toggle('dock-shut', !open);
    d.inert = !open && !phone();
    if (tab) tab.hidden = open || phone();
    if (keep) save();
    tellShell();
  };
  width(st.w);
  top();
  show(st.open, false);
  page.dockShow = show;

  d.addEventListener('click', (e) => {
    if (e.target.closest('[data-dock-ring]')) toShell({ cello: 'ring' });
    if (e.target.closest('[data-dock-mic]')) toShell({ cello: 'wake' });
    if (e.target.closest('[data-dock-close]')) { show(false); tab?.focus({ preventScroll: true }); }
  });

  // the edge: drag to resize; nearly shut closes it; double-click resets; arrow keys nudge it
  if (edge) {
    let drag = null;
    edge.addEventListener('pointerdown', (e) => {
      if (phone()) return;
      e.preventDefault();
      edge.setPointerCapture(e.pointerId);
      drag = { x0: e.clientX, w0: st.w };
      html.classList.add('dock-dragging');
    });
    edge.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const want = drag.w0 + (drag.x0 - e.clientX);
      html.classList.toggle('dock-closing', want < SHUT_W);
      width(want);
    });
    const end = (e) => {
      if (!drag) return;
      const { x0, w0 } = drag;
      drag = null;
      html.classList.remove('dock-dragging', 'dock-closing');
      if (w0 + (x0 - e.clientX) < SHUT_W) { width(w0); show(false); } else { save(); tellShell(); }
    };
    edge.addEventListener('pointerup', end);
    edge.addEventListener('pointercancel', end);
    edge.addEventListener('dblclick', () => { width(400); save(); tellShell(); });
    edge.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      width(st.w + (e.key === 'ArrowLeft' ? 24 : -24));
      save();
      tellShell();
    });
  }

  // the tab: tap it, or pull it out (the drawer follows the pointer and opens at the width you let go at)
  if (tab) {
    let pull = null;
    tab.addEventListener('pointerdown', (e) => {
      tab.setPointerCapture(e.pointerId);
      pull = { x0: e.clientX, moved: false };
    });
    tab.addEventListener('pointermove', (e) => {
      if (!pull) return;
      if (!pull.moved && pull.x0 - e.clientX > 8) {
        pull.moved = true;
        html.classList.add('dock-dragging');
        width(MIN_W);
        show(true, false);
      }
      if (pull.moved) width(window.innerWidth - e.clientX);
    });
    const done = () => {
      if (!pull) return;
      const { moved } = pull;
      pull = null;
      html.classList.remove('dock-dragging');
      if (moved) { save(); tellShell(); } else show(true);
      $('#editor textarea', d)?.focus({ preventScroll: true });
    };
    tab.addEventListener('pointerup', done);
    tab.addEventListener('pointercancel', done);
    tab.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); show(true); } });
  }

  document.addEventListener('keydown', (e) => {
    if (e.altKey && !e.ctrlKey && !e.metaKey && e.code === 'KeyC') {
      e.preventDefault();
      show(!st.open);
      if (st.open) $('#editor textarea', d)?.focus({ preventScroll: true });
    }
  });
  window.addEventListener('resize', () => { top(); width(st.w); show(st.open, false); });

  // the phone's sheet
  const grab = $('[data-dock-grab]', d);
  if (grab) {
    let y0 = null;
    grab.addEventListener('click', () => page.classList.toggle('dock-open'));
    grab.addEventListener('pointerdown', (e) => { y0 = e.clientY; });
    window.addEventListener('pointerup', (e) => {
      if (y0 === null) return;
      if (y0 - e.clientY > 30) page.classList.add('dock-open');
      if (e.clientY - y0 > 30) page.classList.remove('dock-open');
      y0 = null;
    });
  }
  $('#editor textarea', d)?.addEventListener('focus', () => page.classList.add('dock-open'));
}

export function filmPage() {
  const page = $('[data-filmpage]');
  if (!page) return;
  dock(page);
  modes(page);
  tabGo(page);
  player(page);
  const convo = $('[data-convo]', page);
  if (convo) convo.scrollTop = convo.scrollHeight;
}

export { framed };
