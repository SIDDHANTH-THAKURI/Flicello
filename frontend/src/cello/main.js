// Cello's shell: the app lives in a frame and Cello lives over it, so its voice, its ears and its body stay through
// every page. Three parts, kept apart:
// - the body (orb.js): only draws; told its phase, where to look, how loud, the sound's shape;
// - the voice: a live Gemini session in the browser (a one-session token from our server), mic in, voice out,
//   interruptible, resumed if the line drops;
// - the hands: its tools, run here when they move the screen, on our server when they change a film; and a light that
//   reaches from Cello to whatever it is working on, so you see it work.
// The phase is worked out from facts on every frame, never pushed, so it can't go stale.
import { Orb } from './orb.js';
import { setupRing } from './ring.js';
import { setupComposer } from './composer.js';
import './bundle.css';

const $ = (s, r = document) => r.querySelector(s);
const csrf = $('meta[name="csrf"]').content;
const OWNER = document.body.dataset.owner === '1';
const frame = $('#cello-app');
const root = $('#cello');
const body = $('.cello-body', root);
const orb = new Orb($('#cello-orb'));
const beam = $('#cello-beam');
const caption = $('.cello-caption', root);
const pill = $('.cello-pill', root);
const WS = 'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained';
const PARTS = { preview: '[data-preview]', draft: '#draft', timeline: '#timeline', styles: '#styles', ready: '#ready',
  exports: '#exports', review: '#review', versions: '.screens', brief: '#editor', people: '#editor .people',
  clips: '#drop', upload: '#drop', story: '.understood, .posters, .looks', shots: '#shots', settings: '#settings-edit',
  music: '#film-music' };
const TOOL_PART = { edit: 'draft', style: 'styles', fix_score: 'ready', export: 'exports', review_link: 'review',
  make_it: 'draft', discard: 'draft', effect: 'timeline', ai_clip: 'timeline', music: 'preview', step: null };

const S = {
  on: false, ws: null, open: false, ever: false, woke: 0, awaiting: false, acting: 0, muted: false, mic: 0,
  out: 0, handle: sessionStorage.getItem('cello-handle') || '', queue: [], lastSound: 0, since: null, cut: false, inReply: 0, heard: 0,
  greeted: false, retry: 0, aec: true, target: null, targetUntil: 0, pending: [], asked: 0, warned: false, blocked: '', dozing: false, sleepSoon: false, hushed: false,
};

// ---------------------------------------------------------------- the frame: which film, which page
function film() {
  try {
    const m = frame.contentWindow.location.pathname.match(/^\/p\/([^/]+)/);
    return m ? m[1] : null;
  } catch { return null; }
}
function doc() { try { return frame.contentDocument; } catch { return null; } }
// the frame sent somewhere (or reloaded), and the next tool held until that page has loaded: a tool that runs on the
// page right after (upload after new_film) must find the new page, not the old one
function go(src) {
  return new Promise((done) => {
    const t = setTimeout(done, 12000);
    frame.addEventListener('load', () => { clearTimeout(t); setTimeout(done, 150); }, { once: true });
    if (src === null) frame.contentWindow.location.reload(); else frame.src = src;
  });
}

function tellPage(msg) { try { frame.contentWindow.postMessage({ cello: 'shell', ...msg }, location.origin); } catch { /* gone */ } }

frame.addEventListener('load', () => {
  const d = doc();
  if (!d) return;
  setTimeout(() => tellPage({ on: S.on }), 50);
  root.classList.toggle('page-docked', !!d.querySelector('[data-dock]')); // the film page has Cello's conversation docked
  if (!d.querySelector('[data-dock]')) root.classList.remove('dock-in'); // (its drawer says when it is open)
  history.replaceState(null, '', `/cello?to=${encodeURIComponent(frame.contentWindow.location.pathname + frame.contentWindow.location.search)}`);
  d.addEventListener('pointermove', (e) => { // its eyes follow you around the page, and it knows what you point at
    const r = body.getBoundingClientRect();
    const fr = frame.getBoundingClientRect();
    cursor.x = e.clientX;
    cursor.y = e.clientY;
    cursor.at = performance.now();
    gazeAt(e.clientX + fr.left, e.clientY + fr.top, r);
  });
  document.title = d.title || 'Flicello';
  d.addEventListener('keydown', (e) => { if (e.key === 'Escape') stop(); });
  d.addEventListener('pointerdown', () => { unlock(); });
  d.addEventListener('click', (e) => { // other sites can't be held in the frame: they open in a tab
    const a = e.target.closest && e.target.closest('a[href]');
    if (a && a.origin !== window.location.origin) { e.preventDefault(); window.open(a.href, '_blank', 'noopener'); }
  }, true);
  S.target = null;
  watchUploads(d);
});

const cursor = { x: -1, y: -1, at: 0 };

// their clips going in: Cello hears each one land (or fail, and why) the moment the page shows it
function watchUploads(d) {
  const list = d.querySelector('#upload-list');
  if (!list) return;
  const told = new WeakSet();
  new MutationObserver(() => {
    for (const li of list.querySelectorAll('li')) {
      if (told.has(li)) continue;
      const name = (li.querySelector('.name')?.textContent || '').trim();
      if (li.classList.contains('ok')) {
        told.add(li);
        hidden(`(Note, not them talking: their clip "${name}" is in. If they asked you to watch it, call step with start now; else tell them it's in.)`);
      } else if (li.classList.contains('off')) {
        told.add(li);
        hidden(`(Note, not them talking: the upload failed: ${name}. Tell them plainly what went wrong; nothing is in.)`);
      }
    }
  }).observe(list, { subtree: true, attributes: true, attributeFilter: ['class'], childList: true });
}

function onPageClips() { // what the page shows of their clips right now
  const d = doc();
  if (!d) return null;
  const inFilm = [...d.querySelectorAll('#clip-list li .name')].map((n) => n.textContent.trim());
  const going = [...d.querySelectorAll('#upload-list li')].map((li) => ({
    file: (li.querySelector('.name')?.textContent || '').trim(),
    state: li.classList.contains('ok') ? 'in' : li.classList.contains('off') ? 'failed' : `uploading ${li.querySelector('.sub:last-of-type')?.textContent || ''}`.trim(),
  }));
  return inFilm.length || going.length ? { clips_in_film: inFilm, uploads: going } : null;
}

// ---------------------------------------------------------------- eyes: what they point at, what is playing
function words(el) { // what a person would call this thing on the page
  if (!el) return '';
  const own = el.getAttribute?.('aria-label') || el.getAttribute?.('title') || el.getAttribute?.('placeholder') || '';
  const text = own || (el.innerText || el.value || '').trim();
  return text.replace(/\s+/g, ' ').slice(0, 120);
}

function videoInfo(d) { // the video on screen: the draft preview or the version, where it is, which shot
  const pv = d.querySelector('[data-preview]');
  if (pv && pv.player) {
    const pl = pv.player;
    const i = pl.shotAt(pl.t);
    return { which: 'the draft preview', at_s: +pl.t.toFixed(2), of_s: +pl.tl.duration.toFixed(2), playing: !!pl.playing,
      shot: i + 1, box: pl.stage.getBoundingClientRect() };
  }
  const v = d.querySelector('.screens video, #draft video, video');
  if (!v) return null;
  const fig = v.closest('[data-shots]');
  let shot = null;
  try {
    const times = JSON.parse(fig?.dataset.shots || '[]');
    for (const [start, n] of times) if (start <= v.currentTime + 1e-3) shot = n;
  } catch { /* no shots */ }
  return { which: 'the film', at_s: +v.currentTime.toFixed(2), of_s: +(v.duration || 0).toFixed(2), playing: !v.paused,
    shot, box: v.getBoundingClientRect() };
}

function pointing() {
  const d = doc();
  if (!d) return { result: 'The page is not readable right now.' };
  const out = { ...screen() };
  const vid = videoInfo(d);
  if (vid) {
    const { box, ...rest } = vid;
    out.video = rest;
  }
  if (cursor.at && performance.now() - cursor.at < 120000) {
    const el = d.elementFromPoint(cursor.x, cursor.y);
    if (vid && cursor.x >= vid.box.left && cursor.x <= vid.box.right && cursor.y >= vid.box.top && cursor.y <= vid.box.bottom) {
      out.cursor = { on: `${vid.which}, at ${vid.at_s} s${vid.shot ? `, shot ${vid.shot}` : ''}`,
        frame_x: +((cursor.x - vid.box.left) / vid.box.width).toFixed(3), frame_y: +((cursor.y - vid.box.top) / vid.box.height).toFixed(3),
        note: 'frame_x/y: where in the picture they point (0-1 from the top left)' };
    } else if (el) {
      const card = el.closest('[data-shot]');
      const target = el.closest('button, a, label, input, select, textarea, summary, [role=button], h1, h2, h3, li, figure, section') || el;
      out.cursor = { on: words(target) || target.tagName.toLowerCase(), ...(card ? { shot: +card.dataset.shot } : {}) };
    }
  } else {
    out.cursor = { on: 'nothing: their cursor has not been on the page lately' };
  }
  return { result: out };
}

// ---------------------------------------------------------------- hands on the page
function find(d, what, kinds) {
  const want = String(what || '').toLowerCase().trim();
  if (!want) return null;
  const all = [...d.querySelectorAll(kinds)].filter((n) => n.offsetParent !== null || n.getClientRects().length);
  const named = all.map((n) => [n, words(n).toLowerCase() || (n.labels?.[0] && words(n.labels[0]).toLowerCase()) || ''])
    .filter(([, w]) => w);
  return (named.find(([, w]) => w === want) || named.find(([, w]) => w.startsWith(want))
    || named.find(([, w]) => w.includes(want)) || named.find(([, w]) => want.includes(w) && w.length > 3) || [])[0] || null;
}

function onPage(args) {
  const d = doc();
  const w = frame.contentWindow;
  if (!d || !w) return 'The page is not readable right now.';
  const what = String(args.where || '').toLowerCase();
  if (args.do === 'back') { w.history.back(); return 'Went back.'; }
  if (args.do === 'scroll') {
    const h = w.innerHeight * 0.75;
    if (what.includes('top')) w.scrollTo({ top: 0, behavior: 'smooth' });
    else if (what.includes('bottom')) w.scrollTo({ top: d.body.scrollHeight, behavior: 'smooth' });
    else w.scrollBy({ top: what.includes('up') ? -h : h, behavior: 'smooth' });
    return `Scrolled ${what || 'down'}.`;
  }
  if (args.do === 'click') {
    const el = find(d, what, 'button, a, summary, label, [role=button], input[type=submit], input[type=checkbox], input[type=radio]');
    if (!el) return `There is nothing called "${args.where}" to click on this page.`;
    reveal(el);
    if (destroys(el)) { // never pressed for them: Cello shows it, the person presses it
      d.querySelectorAll('[data-cello-pointed]').forEach((n) => n.removeAttribute('data-cello-pointed'));
      el.setAttribute('data-cello-pointed', '');
      point('[data-cello-pointed]', 9000);
      return `Not pressed: "${words(el)}" deletes for good, so only they may press it. It is lit up on screen for `
        + 'them; say so, and never try another way to delete it.';
    }
    if (el.closest('form[data-confirm]')) el.closest('form').removeAttribute('data-confirm'); // they asked by voice: no box to tick
    el.click();
    return `Clicked "${words(el)}".`;
  }
  if (args.do === 'type') {
    const el = findField(d, what, 'input:not([type=hidden]):not([type=file]), textarea') || find(d, what, 'input:not([type=hidden]):not([type=file]), textarea') || d.querySelector('textarea, input[type=text]');
    if (!el) return 'There is no field to type in on this page.';
    if (secret(el)) { // keys and passwords are theirs alone
      reveal(el);
      d.querySelectorAll('[data-cello-pointed]').forEach((x) => x.removeAttribute('data-cello-pointed'));
      el.setAttribute('data-cello-pointed', '');
      point('[data-cello-pointed]', 6000);
      return 'Not typed: that is a key or password box. Only they type keys (never ask them to say one aloud); point them to it.';
    }
    reveal(el);
    el.focus();
    el.value = String(args.text || '');
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return `Typed into "${words(el) || 'the field'}".`;
  }
  if (args.do === 'choose') { // a dropdown's option, by its words
    const sel = findField(d, what, 'select');
    if (!sel) return `There is no dropdown called "${args.where}" here.`;
    const want = String(args.text || '').toLowerCase().trim();
    const opts = [...sel.options];
    const o = opts.find((x) => x.textContent.toLowerCase().trim() === want || x.value.toLowerCase() === want)
      || opts.find((x) => x.textContent.toLowerCase().startsWith(want)) || opts.find((x) => want && x.textContent.toLowerCase().includes(want));
    if (!o) return `"${labelOf(sel)}" has no "${args.text}". Its options: ${opts.map((x) => x.textContent.split(' · ')[0].trim()).join(', ')}.`;
    reveal(sel.dataset.ui && sel.previousElementSibling?.classList.contains('ui-select') ? sel.previousElementSibling : sel);
    sel.value = o.value;
    sel.dispatchEvent(new Event('input', { bubbles: true }));
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    const save = fieldOf(sel).saved_by;
    return `Chose "${o.textContent.split(' · ')[0].trim()}" in "${labelOf(sel)}".${save ? ` Not saved yet: "${save}" saves it.` : ''}`;
  }
  if (args.do === 'set') { // a switch, a tick box or a choice on or off; a colour; a slider
    const el = findField(d, what, 'button[role=switch], input[type=checkbox], input[type=radio], input[type=color], input[type=range]')
      || find(d, what, 'button[role=switch], input[type=checkbox], input[type=radio]');
    if (!el) return `There is no switch, colour or slider called "${args.where}" here.`;
    reveal(el);
    const v = String(args.text || '').trim().toLowerCase();
    if (el.matches('button[role=switch]')) {
      const on = el.getAttribute('aria-checked') === 'true';
      const want = !/^(off|no|false|0)$/.test(v);
      if (on !== want) el.click();
      return `"${labelOf(el) || words(el)}" is ${want ? 'on' : 'off'}.`;
    }
    if (el.type === 'checkbox' || el.type === 'radio') {
      if (destroys(el)) return 'Not ticked: that box agrees to deleting for good; only they may tick it.';
      const want = el.type === 'radio' || !/^(off|no|false|0)$/.test(v);
      if (el.checked !== want) el.click();
      return `"${labelOf(el)}" is ${want ? 'on' : 'off'}.`;
    }
    if (el.type === 'color' && !/^#[0-9a-f]{6}$/i.test(v)) return 'A colour is set as #rrggbb (for example #ff5a36).';
    el.value = v;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    const save = fieldOf(el).saved_by;
    return `"${labelOf(el)}" set to ${el.value}.${save ? ` Not saved yet: "${save}" saves it.` : ''}`;
  }
  return 'Say scroll, click, type, choose, set or back.';
}

// what can't be undone: deleting a film, every film, clips, their style or account. Cello may take them there and
// point; it never presses it (and never takes away the "are you sure?" box on it).
const FOR_GOOD = /\b(delete|erase|wipe|destroy|remove (it |them |this )?for good|permanently|can.?t be undone|cannot be undone)\b/i;
function destroys(el) {
  const form = el.closest('form');
  const said = [words(el), el.getAttribute('formaction') || '', form?.getAttribute('action') || '',
    form?.dataset.confirm || '', el.dataset?.confirm || ''].join(' ');
  return FOR_GOOD.test(said) || !!el.closest('.danger, .danger-zone, details.danger-zone');
}

function reveal(el) { // a part tucked away in the one-screen view, brought up (and every box it sits in opened)
  const d = el.ownerDocument;
  for (let n = el; n && n !== d.body; n = n.parentElement) {
    if (n.tagName === 'DETAILS') n.open = true;
    if (getComputedStyle(n).display === 'none' || n.hidden) { n.hidden = false; n.classList.add('cello-shown'); }
  }
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function upload() {
  let drop = null;
  for (let k = 0; k < 20 && !drop; k++) { // a page still drawing itself: a moment to find its drop place
    const d = doc();
    drop = d && d.readyState !== 'loading' && d.querySelector('#drop, label.drop');
    if (!drop) await new Promise((r) => setTimeout(r, 150));
  }
  if (!drop) {
    return { result: film() ? 'This film is past its clips: its clips are in. (To add more, go back to the clips step.)'
      : 'No film is open: start one with new_film first.' };
  }
  point('upload', 9000);
  drop.classList.add('cello-lit');
  setTimeout(() => drop.classList.remove('cello-lit'), 9000);
  return { result: 'The place for their clips is lit up: they tap it to choose files, or drop them on it. Uploads show '
    + 'progress there; tell them you will watch once they are in.' };
}

function screen() {
  try { return { page: frame.contentWindow.location.pathname, title: doc().title, ...pageMap() }; } catch { return {}; }
}

// ---------------------------------------------------------------- the page, as Cello needs to know it
// Its sections and tabs, and every field a person could fill: its label, kind, what it holds now (a key or password
// only as set / empty, never its value), a dropdown's options, and the button that saves the form it is in. So "make
// the type serif and save" fills the right field and presses the right Save.
const shown = (n) => !!(n.offsetParent !== null || n.getClientRects().length);
function labelOf(el) {
  const d = el.ownerDocument;
  const byFor = el.id && d.querySelector(`label[for="${CSS.escape(el.id)}"]`);
  const own = el.getAttribute('aria-label') || (byFor && words(byFor)) || (el.labels?.[0] && words(el.labels[0]));
  const field = el.closest('.field, label');
  const fromField = field && field.querySelector('span, b') && words(field.querySelector('span, b'));
  return (own || fromField || el.getAttribute('placeholder') || el.name || '').replace(/\s+/g, ' ').slice(0, 80);
}
function secret(el) { return el.type === 'password' || !!el.closest('[data-connect-key]') || /key|token|secret|password/i.test(el.name || ''); }
function fieldOf(el) {
  const f = { label: labelOf(el) };
  if (el.tagName === 'SELECT') {
    f.kind = 'dropdown';
    f.now = el.selectedOptions[0] ? words(el.selectedOptions[0]) : '';
    f.options = [...el.options].map((o) => o.textContent.split(' · ')[0].trim()).slice(0, 20);
  } else if (el.matches('button[role=switch]')) {
    f.kind = 'switch';
    f.now = el.getAttribute('aria-checked') === 'true' ? 'on' : 'off';
  } else if (el.type === 'checkbox' || el.type === 'radio') {
    f.kind = el.type;
    f.now = el.checked ? 'on' : 'off';
  } else if (el.type === 'color' || el.type === 'range') {
    f.kind = el.type === 'color' ? 'colour' : 'slider';
    f.now = el.value;
  } else if (el.type === 'file') {
    f.kind = 'file (only they can pick files: point at it)';
  } else {
    f.kind = el.tagName === 'TEXTAREA' ? 'text box' : 'text';
    f.now = secret(el) ? (el.value ? 'set' : 'empty') : String(el.value || '').slice(0, 80);
    if (secret(el)) f.kind = 'key (only they type keys)';
  }
  const form = el.closest('form');
  const save = form && [...form.querySelectorAll('button[type=submit], button:not([type]), input[type=submit]')].find(shown);
  if (save) f.saved_by = words(save);
  for (let n = el.parentElement; n && n.tagName !== 'MAIN'; n = n.parentElement) { // the nearest titled part it sits in
    const head = n.matches('details') ? n.querySelector(':scope > summary')
      : n.querySelector(':scope > h2, :scope > h3, :scope > h4, :scope > header h2, :scope > header h3, :scope > .card-h');
    const tab = n.matches('.tabpanel[data-p]') && n.ownerDocument.querySelector(`[role=tab][data-t="${n.dataset.p}"]`);
    if (head || tab) { f.in = words(head || tab).replace(/\s*\d+$/, '').slice(0, 60); break; }
  }
  return f;
}
function pageMap() {
  const d = doc();
  if (!d) return {};
  const sections = [...d.querySelectorAll('main h1, main h2, main h3')].filter(shown).map((h) => words(h)).filter(Boolean).slice(0, 20);
  const tabs = [...d.querySelectorAll('[data-tabs]')].filter(shown).map((bar) => ({
    tabs: [...bar.querySelectorAll('[role=tab]')].map((t) => words(t)),
    open: words(bar.querySelector('[role=tab][aria-selected=true]')),
  }));
  const fields = [...d.querySelectorAll('main input:not([type=hidden]), main select, main textarea, main button[role=switch]')]
    .filter((el) => shown(el) || (el.tagName === 'SELECT' && el.dataset.ui)) // a styled dropdown hides its real select
    .filter((el) => !el.closest('[data-dock]')) // their conversation box isn't a field to fill
    .slice(0, 60).map(fieldOf);
  return { sections, ...(tabs.length ? { tabs } : {}), ...(fields.length ? { fields } : {}) };
}
window.celloHands = { look: () => screen(), page: (args) => onPage(args) }; // the same hands, for checks and the console
function findField(d, what, kinds) {
  const want = String(what || '').toLowerCase().trim();
  const all = [...d.querySelectorAll(kinds)].filter((el) => shown(el) || (el.tagName === 'SELECT' && el.dataset.ui));
  const named = all.map((el) => [el, labelOf(el).toLowerCase()]).filter(([, w]) => w);
  return (named.find(([, w]) => w === want) || named.find(([, w]) => w.startsWith(want)) || named.find(([, w]) => w.includes(want))
    || named.find(([, w]) => want.includes(w) && w.length > 2) || [])[0] || null;
}

function gazeAt(x, y, r = body.getBoundingClientRect()) {
  const cx = r.left + r.width / 2;
  const cy = r.top + r.height / 2;
  orb.look((x - cx) / (window.innerWidth / 2), -(y - cy) / (window.innerHeight / 2));
}

// ---------------------------------------------------------------- voice out: 24 kHz PCM, in order, cut on interruption
let ctx = null;
let outGain = null;
let outAn = null;
let micAn = null;
let nextAt = 0;
const playing = new Set();

function audio() {
  if (ctx) return ctx;
  ctx = new AudioContext();
  outGain = ctx.createGain();
  outAn = ctx.createAnalyser();
  outAn.fftSize = 256;
  outGain.connect(outAn);
  outAn.connect(ctx.destination);
  return ctx;
}

function play(b64) {
  if (S.cut) return; // they stopped this answer: the rest of it is dropped
  if (localStorage.getItem('cello-text') === '1') { S.lastSound = performance.now(); return; } // text only: it writes
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const pcm = new Int16Array(bytes.buffer, 0, bytes.length >> 1);
  const buf = ctx.createBuffer(1, pcm.length, 24000);
  const ch = buf.getChannelData(0);
  for (let i = 0; i < pcm.length; i += 1) ch[i] = pcm[i] / 32768;
  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.connect(outGain);
  const lead = 0.06 + (ctx.outputLatency || ctx.baseLatency || 0);
  nextAt = Math.max(nextAt, ctx.currentTime + lead);
  src.start(nextAt);
  nextAt += buf.duration;
  playing.add(src);
  src.onended = () => { playing.delete(src); S.lastSound = performance.now(); };
}

function hush() { // they spoke over it: everything queued goes, at once
  for (const s of playing) { try { s.stop(); } catch { /* done */ } }
  playing.clear();
  nextAt = 0;
}

function stop() { // Esc, or a tap while it talks: this answer ends here
  if (playing.size || S.awaiting) S.cut = true;
  hush();
  S.awaiting = false;
  S.asked = 0;
}

// ---------------------------------------------------------------- ears: the mic at 16 kHz, 40 ms at a time
let micStream = null;
async function ears() {
  if (micStream) return;
  micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  S.aec = !!micStream.getAudioTracks()[0]?.getSettings?.().echoCancellation;
  await ctx.audioWorklet.addModule('/static/cello-mic.js');
  const src = ctx.createMediaStreamSource(micStream);
  const node = new AudioWorkletNode(ctx, 'cello-mic');
  micAn = ctx.createAnalyser();
  micAn.fftSize = 256;
  src.connect(micAn);
  src.connect(node);
  const zero = ctx.createGain(); // the graph only pulls a node that reaches the speakers
  zero.gain.value = 0;
  node.connect(zero);
  zero.connect(ctx.destination);
  node.port.onmessage = (e) => {
    S.mic = e.data.rms;
    if (!S.open || S.muted || (!S.on && !S.dozing)) return;
    const talking = playing.size > 0;
    if (talking && !S.aec && S.mic < S.out * 2.4) return; // its own voice leaking back in, not them
    const b = new Uint8Array(e.data.pcm);
    let s = '';
    for (let i = 0; i < b.length; i += 1) s += String.fromCharCode(b[i]);
    send({ realtimeInput: { audio: { data: btoa(s), mimeType: 'audio/pcm;rate=16000' } } });
  };
}

// ---------------------------------------------------------------- the log: what it heard, said and did, kept
const book = [];
const told = []; // this session's lines, for the ring's "What it did" (book is sent away and emptied)
const turn = { you: '', cello: '' };
function note(who, text) {
  const entry = { at: Date.now() / 1000, who, text: typeof text === 'string' ? text : JSON.stringify(text) };
  book.push(entry);
  told.push(entry);
  if (told.length > 120) told.splice(0, told.length - 120);
  if (book.length > 40) sendBook();
}
const recent = []; // the last few things said, both ways: a restart carries them over
function keep(who, text) {
  recent.push(`${who === 'you' ? 'They' : 'You'}: ${text.slice(0, 400)}`);
  if (recent.length > 8) recent.splice(0, recent.length - 8);
}
function closeTurn() { // what they said and what it answered, each as one line
  if (turn.you.trim()) { note('you', turn.you.trim()); keep('you', turn.you.trim()); }
  if (turn.cello.trim()) { note('cello', turn.cello.trim()); keep('cello', turn.cello.trim()); }
  turn.you = '';
  turn.cello = '';
}
function sendBook() {
  if (!book.length) return;
  const lines = book.splice(0, book.length);
  fetch('/api/cello/log', { method: 'POST', credentials: 'same-origin', keepalive: true,
    headers: { 'Content-Type': 'application/json', 'X-CSRF': csrf }, body: JSON.stringify({ pid: film(), lines }) })
    .catch(() => {});
}
setInterval(sendBook, 8000);
window.addEventListener('pagehide', () => { closeTurn(); sendBook(); });

// ---------------------------------------------------------------- the line to Gemini
function send(msg) { if (S.ws && S.ws.readyState === 1) S.ws.send(JSON.stringify(msg)); }
function say(text) {
  if (!S.open) { S.pending.push(text); return; }
  note(text.startsWith('(Note') ? 'note' : 'you', text);
  if (!text.startsWith('(Note')) S.lastAsk = text;
  send({ realtimeInput: { text } });
  S.awaiting = true;
  S.asked = performance.now();
}
function hidden(text) { S.queue.push(text); }
function unlock() { if (ctx && ctx.state !== 'running') ctx.resume().catch(() => {}); }

async function connect() {
  let tok;
  try {
    const r = await fetch('/api/cello/token', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF': csrf } });
    tok = await r.json();
    if (!r.ok) throw new Error(tok.detail || 'no voice');
  } catch (err) {
    show(`Cello can't speak right now. ${err.message || ''}`);
    setTimeout(() => { if (S.on) connect(); }, Math.min(30000, 2000 * 2 ** S.retry++));
    return;
  }
  // this machine's own voice (Whisper, Claude on their plan, Kokoro) speaks the same messages as Gemini Live
  const ws = tok.local ? new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${tok.url}`)
    : new WebSocket(`${WS}?access_token=${encodeURIComponent(tok.token)}`);
  ws.local = !!tok.local;
  S.ws = ws;
  ws.resumed = !!S.handle;
  ws.onopen = () => {
    ws.send(JSON.stringify({ setup: S.handle && !ws.local ? { sessionResumption: { handle: S.handle } } : {} }));
  };
  ws.onmessage = async (e) => {
    const msg = JSON.parse(typeof e.data === 'string' ? e.data : await e.data.text());
    onMessage(msg);
  };
  ws.onclose = (e) => {
    if (S.ws !== ws) return; // an old line closing after a new one opened
    if (ws.resumed && !ws.ready) { S.handle = ''; sessionStorage.removeItem('cello-handle'); } // that handle has expired
    S.open = false;
    S.ws = null;
    const why = String(e.reason || '').trim();
    if (why) console.warn(`Cello's line closed (${e.code}): ${why}`);
    note('line', `closed ${e.code}${why ? `: ${why}` : ''}${ws.ready ? '' : ' (before it was set up)'}`);
    if (/credit|prepay|billing|quota|exhaust|permission|api key|not enabled|suspended/i.test(why)) {
      // Google won't carry the voice (no credit, no quota): said plainly, and tried again in a few minutes, not in a loop
      S.blocked = OWNER ? (/credit|prepay|billing/i.test(why)
        ? "Cello can't speak: the Gemini API credit has run out. Top it up in Google AI Studio (ai.studio/projects, Billing), then tap Cello."
        : `Cello can't speak: Google says "${why}".`)
        : "Cello's voice isn't available right now. Try again a little later.";
      show(S.blocked, 30000);
      if (S.on) setTimeout(() => { if (S.on && !S.ws) connect(); }, 180000);
      return;
    }
    S.blocked = '';
    if (S.on && !S.warned) { S.warned = true; show('Reconnecting\u2026'); }
    if (S.on) setTimeout(() => { if (S.on && !S.ws) connect(); }, Math.min(8000, 800 * 2 ** S.retry++));
  };
}

function onMessage(m) {
  if (m.setupComplete) {
    if (S.ws) S.ws.ready = true;
    S.open = true;
    S.ever = true;
    S.retry = 0;
    S.warned = false;
    if (S.pending.length) S.greeted = true; // they spoke first: answering them is the greeting
    while (S.pending.length) say(S.pending.shift());
    if (S.carry) { // it restarted itself: no greeting, it picks up where it was
      S.greeted = true;
      hidden(S.carry);
      S.carry = '';
    }
    if (!S.greeted) {
      S.greeted = true;
      const access = { advisor: 'Advisor (you look, point and suggest; they make the changes)', full: 'Full (you do the whole job and tell them after)' }[ring.access()] || 'Co-pilot (small edits at once; ask before anything that takes time)';
      hidden(film() ? `(Note, not them talking: they just woke you while a film is open. Your access: ${access}. Call look, then greet them in a few words and say one useful thing about where the film is.)`
        : `(Note, not them talking: they just woke you. Your access: ${access}. Greet them in a few words; you are ready to work.)`);
    }
  }
  if (m.sessionResumptionUpdate?.resumable && m.sessionResumptionUpdate.newHandle) {
    S.handle = m.sessionResumptionUpdate.newHandle;
    sessionStorage.setItem('cello-handle', S.handle);
  }
  if (m.goAway) { // the line is about to end: a new one opens now and carries on where this one was
    const old = S.ws;
    S.ws = null;
    S.open = false;
    connect();
    setTimeout(() => { try { old.close(); } catch { /* gone */ } }, 1500);
  }
  if (m.cello?.woke) { // its name was said while it rested
    S.dozing = false;
    S.on = true;
    S.woke = performance.now();
    root.classList.remove('dozing');
    chime(true);
    S.awaiting = true;
    S.asked = performance.now();
  }
  if (m.cello?.working && S.awaiting) S.asked = performance.now(); // thinking or using a tool: quiet, not stuck
  if (m.toolCall) tools(m.toolCall.functionCalls || []);
  const sc = m.serverContent;
  if (!sc) return;
  if (sc.interrupted) { hush(); S.cut = false; }
  for (const p of sc.modelTurn?.parts || []) {
    if (p.inlineData?.data) { S.awaiting = false; S.inReply = performance.now(); play(p.inlineData.data); }
  }
  if (sc.inputTranscription?.text) {
    if (turn.cello) closeTurn();
    turn.you += sc.inputTranscription.text;
    S.cut = false;
    if (!said.you) said.me = ''; // a new thing from them: its last answer steps aside
    line('you', sc.inputTranscription.text);
    S.lastAsk = turn.you.trim();
    S.awaiting = true;
    S.asked = performance.now();
  }
  if (sc.outputTranscription?.text) { turn.cello += sc.outputTranscription.text; line('me', sc.outputTranscription.text); }
  if (sc.turnComplete) { comp.done(); tellPage({ turn: 'done' }); }
  if (sc.interrupted) note('event', 'they spoke over it: stopped');
  if (sc.turnComplete) closeTurn();
  if (sc.turnComplete) { S.awaiting = false; S.asked = 0; S.cut = false; S.inReply = 0; }
  if (sc.turnComplete && S.sleepSoon) { S.sleepSoon = false; afterWords(doze); }
  if (sc.inputTranscription?.text) S.hushed = false;
}

function afterWords(fn) { // when what it is saying has finished playing
  const wait = () => (playing.size ? setTimeout(wait, 150) : setTimeout(fn, 250));
  wait();
}

// what was just said, briefly, beside the orb (never a chat log)
const said = { you: '', me: '', t: 0 };
function line(who, text) {
  if (who === 'you') comp.heard(text); else comp.reply(text);
  tellPage({ line: who, text });
  if (performance.now() - said.t > 2500) { said.you = ''; said.me = ''; }
  said[who] = tail(`${said[who]}${text}`, who === 'me' ? 170 : 110);
  if (who === 'me') said.you = tail(said.you, 60);
  said.t = performance.now();
  $('.you', caption).textContent = said.you;
  $('.me', caption).textContent = said.me;
  caption.classList.add('on');
}
function tail(text, n) { // the end of what was said, starting on a whole word
  if (text.length <= n) return text;
  const cut = text.slice(-n);
  const sp = cut.indexOf(' ');
  return `\u2026${sp > 0 && sp < 24 ? cut.slice(sp + 1) : cut}`;
}
function show(text, ms = 4000) {
  pill.textContent = text;
  pill.hidden = false;
  pill.classList.toggle('long', text.length > 60);
  clearTimeout(show.t);
  show.t = setTimeout(() => { pill.hidden = true; }, ms);
}

// ---------------------------------------------------------------- hands
async function tools(calls) {
  const answers = [];
  for (const fc of calls) {
    S.acting += 1;
    const name = fc.name;
    const doing = ({ look: 'Looking', edit: 'Making the changes', style: 'Trying the style', make_it: 'Making it', export: 'Exporting',
      effect: 'Working on the shot', ai_clip: 'Making a clip', music: 'Remaking the music', open: 'Opening', show: 'Showing you',
      play: 'Playing', diagnose: 'Checking what broke', repair: 'Repairing', pointing: 'Looking where you point',
      page: 'On the page', upload: 'Where your clips go', new_film: 'Starting a film', build: 'Building it',
      brief: 'Noting it', find: 'Watching for it' }[name] || 'On it');
    show(doing);
    comp.step(doing);
    tellPage({ doing });
    let result;
    note('tool', `${name} ${JSON.stringify(fc.args || {})}`);
    try {
      result = await run(name, fc.args || {});
    } catch (err) {
      result = { result: `Not done: ${err.message}` };
      note('error', `${name}: ${err.message}`);
    }
    note('result', `${name}: ${JSON.stringify(result.result ?? result).slice(0, 1500)}`);
    S.acting -= 1;
    answers.push({ id: fc.id, name, response: { result: result.result ?? result } });
    if (!String(result.result || '').startsWith('Not done')) orb.nod();
  }
  send({ toolResponse: { functionResponses: answers } });
  S.awaiting = true;
  S.asked = performance.now();
}

async function run(name, args) {
  if (name === 'open') return openThing(String(args.what || ''));
  if (name === 'pointing') return pointing();
  if (name === 'page') return { result: onPage(args) };
  if (name === 'upload') return upload();
  if (name === 'quiet') { stop(); S.hushed = true; return { result: 'Quiet now: say nothing more until they speak to you.' }; }
  if (name === 'sleep') { S.sleepSoon = true; return { result: 'Going to sleep after this turn.' }; }
  if (name === 'show') return { result: point(String(args.part || '').toLowerCase()) ? 'Shown.' : 'That part is not on this page.' };
  if (name === 'play') return { result: drive(args.do, args.seconds) };
  const part = TOOL_PART[name];
  if (part) point(part, 2500);
  const r = await fetch('/api/cello/act', {
    method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF': csrf },
    body: JSON.stringify({ name, args, pid: film() }),
  });
  const out = await r.json().catch(() => ({}));
  if (!r.ok) {
    return { result: r.status === 401 ? 'Not done: they are signed out. Ask them to sign in again.'
      : `Not done: ${out.detail || out.result || `the server said ${r.status}`}` };
  }
  if (name === 'look' && out.result && typeof out.result === 'object') {
    out.result = { ...out.result, ...screen(), ...(onPageClips() ? { on_page: onPageClips() } : {}) };
  }
  const ui = out.ui || {};
  if (ui.go) await go(ui.go);
  else if (ui.reload || ui.wait) await go(null);
  if (ui.wait) S.watch = true;
  return out;
}

async function openThing(what) {
  const places = {
    home: ['/', 'their films'], films: ['/', 'their films'], new: ['/new', 'the page to start a new film'],
    style: ['/style', 'their style page: their brand kit (logo, colours, fonts) and how they like films cut'],
    'my style': ['/style', 'their style page: their brand kit (logo, colours, fonts) and how they like films cut'],
    account: ['/account', 'their account settings'],
  };
  const key = what.toLowerCase().trim();
  if (places[key]) { await go(places[key][0]); return { result: `Opened ${places[key][1]}.` }; }
  if (/^(the )?(editor|timeline)$/.test(key)) {
    if (!film()) return { result: 'No film is open: open one first, then the editor.' };
    await go(`/p/${film()}/edit`);
    return { result: 'Opened the editor: the film on a timeline, to cut by hand.' };
  }
  const r = await fetch('/api/cello/act', { method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF': csrf }, body: JSON.stringify({ name: 'list_films', args: {} }) });
  const films = (await r.json()).result || [];
  const hit = films.find((f) => f.title.toLowerCase() === key) || films.find((f) => f.title.toLowerCase().includes(key));
  if (!hit) return { result: `No film called "${what}". Their films: ${films.map((f) => f.title).slice(0, 8).join(', ')}.` };
  await go(`/p/${hit.id}`);
  return { result: `Opened "${hit.title}" (${hit.stage}).` };
}

function drive(what, seconds) {
  const d = doc();
  const pv = d && d.querySelector('[data-preview]');
  const player = pv && pv.player;
  const video = d && d.querySelector('video');
  if (player) {
    if (what === 'seek') player.seek(Number(seconds) || 0);
    else if (what === 'pause') player.pause();
    else player.play();
    point('preview', 1500);
    return `${what} done on the preview.`;
  }
  if (video) {
    if (what === 'seek') video.currentTime = Number(seconds) || 0;
    else if (what === 'pause') video.pause();
    else video.play().catch(() => {});
    return `${what} done on the video.`;
  }
  return 'There is no video on this page.';
}

// the light that reaches from Cello to what it is working on
function point(part, ms = 3000) {
  const d = doc();
  let el = null;
  try { el = d && d.querySelector(PARTS[part] || part); } catch { el = null; }
  if (!el) return false;
  reveal(el);
  S.target = el;
  S.targetUntil = performance.now() + ms + 600;
  if (!glows.has(el)) glows.set(el, { shadow: el.style.boxShadow, transition: el.style.transition });
  clearTimeout(glows.get(el).t);
  el.style.transition = 'box-shadow .4s ease';
  el.style.boxShadow = '0 0 0 3px rgba(255, 190, 110, .95), 0 0 40px 6px rgba(255, 160, 80, .45)';
  glows.get(el).t = setTimeout(() => {
    const g = glows.get(el);
    el.style.boxShadow = g.shadow;
    setTimeout(() => { el.style.transition = g.transition; }, 450);
    glows.delete(el);
  }, ms);
  return true;
}
const glows = new Map();

function drawBeam(now) {
  const g = beam.getContext('2d');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.round(innerWidth * dpr);
  const h = Math.round(innerHeight * dpr);
  if (beam.width !== w || beam.height !== h) { beam.width = w; beam.height = h; S.drawn = true; }
  const on = S.target && now <= S.targetUntil && S.target.isConnected;
  if (!on && !S.drawn) return; // nothing drawn, nothing to clear
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, innerWidth, innerHeight);
  S.drawn = !!on;
  if (!on) return;
  const t = S.target.getBoundingClientRect();
  const f = frame.getBoundingClientRect();
  const b = body.getBoundingClientRect();
  const x0 = b.left + b.width / 2;
  const y0 = b.top + b.height / 2;
  const x1 = Math.max(f.left, Math.min(f.right, f.left + t.left + t.width / 2));
  const y1 = Math.max(f.top, Math.min(f.bottom, f.top + t.top + Math.min(t.height / 2, 60)));
  gazeAt(x1, y1, b);
  const fade = Math.min(1, (S.targetUntil - now) / 600);
  const cx = (x0 + x1) / 2 + (y1 - y0) * 0.18;
  const cy = (y0 + y1) / 2 - (x1 - x0) * 0.18;
  const grad = g.createLinearGradient(x0, y0, x1, y1);
  grad.addColorStop(0, `rgba(255, 220, 170, ${0.9 * fade})`);
  grad.addColorStop(1, `rgba(255, 140, 90, ${0.15 * fade})`);
  g.strokeStyle = grad;
  g.lineWidth = 2;
  g.shadowColor = 'rgba(255, 170, 100, .9)';
  g.shadowBlur = 14;
  g.setLineDash([2, 10]);
  g.lineDashOffset = -now / 25;
  g.beginPath();
  g.moveTo(x0, y0);
  g.quadraticCurveTo(cx, cy, x1, y1);
  g.stroke();
  g.setLineDash([]);
  for (let k = 0; k < 3; k += 1) { // sparks travelling along it
    const u = ((now / 900) + k / 3) % 1;
    const px = (1 - u) * (1 - u) * x0 + 2 * (1 - u) * u * cx + u * u * x1;
    const py = (1 - u) * (1 - u) * y0 + 2 * (1 - u) * u * cy + u * u * y1;
    g.fillStyle = `rgba(255, 236, 200, ${fade})`;
    g.beginPath();
    g.arc(px, py, 2.6, 0, Math.PI * 2);
    g.fill();
  }
}

// ---------------------------------------------------------------- speaking first: what happens to the film
async function watch() {
  const pid = film();
  if (S.on && S.open && pid) {
    try {
      const r = await fetch(`/api/cello/events?pid=${pid}&since=${S.since ?? 1e12}`, { credentials: 'same-origin' });
      const j = await r.json();
      S.since = j.now;
      if (r.ok && j.events.length) {
        note('event', j.events.join(' | '));
        orb.notice();
        if (S.watch) frame.contentWindow.location.reload();
        S.watch = false;
        hidden(`(Note, not them talking: ${j.events.join('; ')}. Tell them what it means in a sentence and offer the next move; call look if you need to.)`);
      }
    } catch { /* next time */ }
  }
  setTimeout(watch, 4000);
}

// a note is only spoken into a quiet room: not over them, not over itself
function flush(now) {
  if (!S.queue.length || !S.open || playing.size || S.awaiting || S.acting || ctx?.state !== 'running') return;
  if (!S.queuedAt) S.queuedAt = now;
  if ((S.mic > 0.03 || now - S.lastSound < 1500) && now - S.queuedAt < 20000) return;
  S.queuedAt = 0;
  say(S.queue.shift());
}

// ---------------------------------------------------------------- waking, sleeping, moving
function chime(up = true) {
  if (!ctx) return;
  const t = ctx.currentTime + 0.02;
  [[0, up ? 523.25 : 783.99], [0.09, up ? 783.99 : 523.25]].forEach(([d, f]) => {
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = 'sine';
    o.frequency.value = f;
    g.gain.setValueAtTime(0, t + d);
    g.gain.linearRampToValueAtTime(0.06, t + d + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t + d + 0.5);
    o.connect(g);
    g.connect(ctx.destination);
    o.start(t + d);
    o.stop(t + d + 0.55);
  });
}

// a page inside asks for Cello (the new-film page's "Or just tell Cello")
window.addEventListener('message', (e) => {
  if (e.origin !== location.origin || !e.data) return;
  if (e.data.cello === 'wake' && !S.on) wake();
  if (e.data.cello === 'ring') { if (!S.on) wake(); else ring.open(); }
  if (e.data.cello === 'dock') { // the film page's drawer opened or closed: while it is open Cello lives in it
    root.classList.toggle('dock-in', !!e.data.open);
    root.style.setProperty('--dockw', `${Math.max(0, Number(e.data.w) || 0)}px`);
  }
  if (e.data.cello === 'say' && e.data.text) { // typed in the film page's dock while Cello is on
    comp.heard(String(e.data.text));
    if (!S.on) wake();
    say(String(e.data.text));
  }
});

async function wake() {
  if (S.on) return;
  if (S.dozing && S.ws && S.open) {
    S.dozing = false;
    S.on = true;
    S.woke = performance.now();
    root.classList.remove('dozing');
    chime(true);
    send({ cello: { doze: false } });
    return;
  }
  S.on = true;
  S.woke = performance.now();
  localStorage.setItem('cello-on', '1');
  audio();
  await Promise.race([ctx.resume(), new Promise((r) => { setTimeout(r, 300); })]);
  if (ctx.state === 'running') chime(true);
  else show('Cello is back. Tap anywhere so it can speak.');
  try {
    await ears();
  } catch {
    show('Allow the microphone to talk to Cello. Meanwhile you can type to it.');
    comp.type();
  }
  tellPage({ on: true });
  await ring.load();
  if (!ring.access()) await ring.askAccess(); // asked once, on the first hello
  connect();
}

function sleep() {
  chime(false);
  tellPage({ on: false });
  S.on = false;
  S.dozing = false;
  root.classList.remove('dozing');
  hush();
  try { S.ws?.close(); } catch { /* gone */ }
  S.ws = null;
  S.open = false;
  S.greeted = false;
}

// asked to sleep (or tapped to sleep): with this machine's own voice it rests and wakes to its name; otherwise it
// goes fully off, the line closed, until it is tapped
function wakesByName() { return !!(S.ws && S.ws.local) && localStorage.getItem('cello-wake') !== '0'; }
function doze() {
  if (!S.on) return;
  if (!wakesByName()) { sleep(); return; }
  chime(false);
  hush();
  S.on = false;
  S.dozing = true;
  S.awaiting = false;
  root.classList.add('dozing');
  send({ cello: { doze: true } });
  show('Resting. Say \u201cCello\u201d or tap me when you need me.', 5000);
}

function turnOff() {
  sleep();
  localStorage.setItem('cello-on', '0');
  let to = '/';
  try { to = frame.contentWindow.location.pathname + frame.contentWindow.location.search; } catch { /* home */ }
  window.location.href = to;
}

let drag = null;
body.addEventListener('pointerdown', (e) => { drag = { x: e.clientX, y: e.clientY, moved: false }; body.setPointerCapture(e.pointerId); });
body.addEventListener('pointermove', (e) => {
  if (!drag) return;
  if (Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > 6) drag.moved = true;
  if (drag.moved) place(e.clientX / innerWidth, e.clientY / innerHeight);
});
function tap() {
  if (!S.on) wake();
  else if (playing.size) stop();
  else ring.toggle();
}
body.addEventListener('pointerup', () => {
  if (drag && !drag.moved) tap();
  drag = null;
});
body.addEventListener('pointercancel', () => { drag = null; });
body.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); tap(); } });
function place(fx, fy) {
  fx = Math.max(0.04, Math.min(0.96, fx));
  fy = Math.max(0.06, Math.min(0.94, fy));
  root.style.setProperty('--x', `${fx * 100}vw`);
  root.style.setProperty('--y', `${fy * 100}vh`);
  root.classList.toggle('flip', fx < 0.5); // words go on the roomier side
  root.classList.toggle('high', fy < 0.22);
  localStorage.setItem('cello-pos', JSON.stringify([fx, fy]));
}
try { const p = JSON.parse(localStorage.getItem('cello-pos')); if (p) place(p[0], p[1]); } catch { /* default */ }

window.addEventListener('keydown', (e) => { if (e.key === 'Escape') stop(); });

// ---------------------------------------------------------------- every frame: the phase from the facts
const bins = new Uint8Array(128);
function bands(an) {
  const out = new Float32Array(8);
  if (!an) return out;
  an.getByteFrequencyData(bins);
  const edges = [1, 2, 4, 6, 9, 14, 22, 34, 52];
  for (let i = 0; i < 8; i += 1) {
    let s = 0;
    for (let k = edges[i]; k < edges[i + 1]; k += 1) s += bins[k];
    out[i] = s / (edges[i + 1] - edges[i]) / 255;
  }
  return out;
}

let last = performance.now();
function tick(now) {
  const dt = (now - last) / 1000;
  last = now;
  const speaking = playing.size > 0 || (S.inReply && !S.cut && now - S.lastSound < 450);
  const outBands = bands(outAn);
  S.out = outBands.reduce((a, x) => a + x, 0) / 8;
  let phase = 'idle';
  if (!S.on) phase = 'sleep';
  else if (!S.open && (S.ever || now - S.woke > 9000)) phase = 'offline'; // a first connect gets a fair chance
  else if (S.acting) phase = 'acting';
  else if (speaking) phase = 'speaking';
  else if (S.awaiting) phase = 'thinking';
  else if (!S.muted && (S.mic > 0.025 || now - S.heard < 450)) phase = 'hearing';
  if (S.mic > 0.025) S.heard = now;
  orb.phase = phase;
  root.dataset.state = phase;
  comp.phase(phase, now);
  if (speaking) { orb.bands = outBands; orb.level = Math.min(1, S.out * 2.4); } else if (phase === 'hearing') {
    orb.bands = bands(micAn).map((x) => x * 0.7);
    orb.level = Math.min(1, S.mic * 9);
    if (now > S.targetUntil) orb.look(0, -0.35); // it looks at you while you talk
  } else { orb.bands = new Float32Array(8); orb.level = 0; }
  if (!speaking && now - said.t > 3200) caption.classList.remove('on');
  if (S.awaiting && !S.acting && !speaking && S.asked && now - S.asked > 30000) {
    S.awaiting = false;
    S.asked = 0;
    selfRestart();
  }
  flush(now);
  drawBeam(now);
  orb.resize();
  orb.frame(dt);
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
watch();
if (localStorage.getItem('cello-on') === '1') { // it was on: it comes back by itself if it may listen without asking
  root.classList.add('ready');
  navigator.permissions?.query({ name: 'microphone' }).then((p) => { if (p.state === 'granted') wake(); }).catch(() => {});
}
window.addEventListener('pointerdown', unlock, true);
window.addEventListener('keydown', unlock, true);

// ---------------------------------------------------------------- Cello's hands: the desk beside it, the question card
// What its work sessions are doing (the request, live progress, a stop), what they finished, the film's choices; and
// any question to the person as a card of options, answered by a tap or by voice (Cello asks it out loud too).
const desk = $('.cello-desk', root);
const deskTab = $('[data-desk-open]', root);
const askCard = $('.cello-ask', root);
const seenAsk = { id: '', later: '' }; // later: a question they put off (it waits behind Cello's work tab)
let folded = sessionStorage.getItem('cello-desk') === 'folded';

function ago(t, now) {
  const s = Math.max(0, Math.round(now - t));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s` : `${Math.floor(s / 3600)}h`;
}
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

async function answer(id, text, picked) {
  askCard.classList.add('sending');
  try {
    const r = await fetch('/api/work/answer', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF': csrf }, body: JSON.stringify({ id, text }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
    askCard.hidden = true;
    note('you', `[card] ${text}`);
    if (picked) hidden(`(Note, not them talking: on the card they picked "${text}". Acknowledge it in a few words; the answer is already passed on.)`);
  } catch (err) {
    show(`That answer didn't go through: ${err.message}`);
  } finally {
    askCard.classList.remove('sending');
  }
}

function drawAsk(w) {
  deskTab.classList.toggle('waiting', !!w && seenAsk.later === w.id);
  if (!w) { askCard.hidden = true; seenAsk.id = ''; seenAsk.later = ''; return; }
  if (seenAsk.later === w.id) { askCard.hidden = true; deskTab.hidden = false; deskTab.textContent = 'A question is waiting'; return; }
  if (seenAsk.id === w.id && !askCard.hidden) return;
  seenAsk.id = w.id;
  $('.ask-lead', askCard).textContent = w.say || '';
  $('.ask-q', askCard).textContent = w.question || '';
  const box = $('.ask-options', askCard);
  box.replaceChildren();
  (w.options || []).forEach((o, k) => {
    const b = el('button', k === 0 ? 'ask-opt best' : 'ask-opt', o);
    b.type = 'button';
    b.addEventListener('click', () => answer(w.id, o, true));
    box.append(b);
  });
  const own = $('.ask-own', askCard);
  own.onsubmit = (e) => { e.preventDefault(); const t = own.t.value.trim(); if (t) { own.t.value = ''; answer(w.id, t, true); } };
  askCard.hidden = false;
  orb.notice();
}

function drawDesk(state) {
  const now = state.now;
  const work = state.work || [];
  const live = work.filter((w) => w.status === 'running' || w.status === 'queued');
  const asking = work.find((w) => w.status === 'asking');
  const done = work.filter((w) => ['done', 'failed'].includes(w.status) && w.kind !== 'system').slice(0, 3);
  const nowBox = $('.desk-now', desk);
  nowBox.replaceChildren();
  live.forEach((w) => {
    const item = el('div', `desk-task ${w.status}`);
    item.append(el('p', 'desk-what', w.kind === 'scene' ? `Designing a scene: ${w.request}` : w.request));
    item.append(el('p', 'desk-progress', w.status === 'queued' ? 'Next, after the current task' : (w.progress || 'Thinking…')));
    const foot = el('p', 'desk-foot', w.status === 'queued' ? '' : `working ${ago(w.created, now)}`);
    if (w.kind === 'agent') {
      const stop = el('button', 'desk-stop', 'Stop');
      stop.type = 'button';
      stop.addEventListener('click', async () => {
        await fetch('/api/work/stop', { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRF': csrf }, body: JSON.stringify({ id: w.id }) });
        hidden('(Note, not them talking: they stopped the task in progress. Say it is stopped, in a few words.)');
      });
      foot.append(stop);
    }
    item.append(foot);
    nowBox.append(item);
  });
  nowBox.hidden = !live.length;
  const doneBox = $('.desk-done', desk);
  const list = $('ul', doneBox);
  list.replaceChildren(...done.map((w) => el('li', w.status, w.say || w.request)));
  doneBox.hidden = !done.length;
  const choiceBox = $('.desk-choices', desk);
  const dl = $('dl', choiceBox);
  dl.replaceChildren();
  (state.choices || []).forEach((c) => { dl.append(el('dt', '', c.what), el('dd', '', c.value)); });
  choiceBox.hidden = !(state.choices || []).length;
  const anything = live.length || done.length || (state.choices || []).length;
  // on the film page the film's facts are in its tabs and the conversation is docked: the desk opens only while its
  // hands are working
  const fold = folded || (root.classList.contains('page-docked') && !live.length);
  desk.hidden = !anything || fold;
  deskTab.hidden = (!anything || !fold) && !seenAsk.later; // a question put off keeps its tab
  root.classList.toggle('desk-on', !desk.hidden);
  drawAsk(asking);
  S.handsBusy = live.some((w) => w.status === 'running');
}

$('[data-desk-fold]', desk).addEventListener('click', () => { folded = true; sessionStorage.setItem('cello-desk', 'folded'); desk.hidden = true; deskTab.hidden = false; });
deskTab.addEventListener('click', () => {
  if (seenAsk.later) { // the question they put off comes back
    seenAsk.later = '';
    seenAsk.id = '';
    deskTab.classList.remove('waiting');
    deskTab.textContent = 'Cello’s work';
  }
  folded = false; sessionStorage.removeItem('cello-desk'); deskTab.hidden = true; desk.hidden = false;
});
$('[data-ask-later]', askCard).addEventListener('click', () => { // not now: the work behind it waits for the answer
  seenAsk.later = seenAsk.id;
  askCard.hidden = true;
  deskTab.hidden = false;
  deskTab.classList.add('waiting');
  deskTab.textContent = 'A question is waiting';
  show('It waits behind “A question is waiting”. The work it belongs to waits too.', 5000);
});

// what Cello's hands did on the screen (a film opened, a draft on its way), shown as it happens, voice on or off
const hands = { since: 0, wait: '' };
async function deskLoop() {
  try {
    const pid = film() || '';
    const r = await fetch(`/api/work/state?pid=${pid}&since=${hands.since || 1e12}`, { credentials: 'same-origin' });
    if (r.ok) {
      const j = await r.json();
      hands.since = j.now;
      drawDesk(j);
      for (const ui of j.ui || []) {
        if (ui.go) frame.src = ui.go;
        else if (ui.reload && (!ui.pid || ui.pid === pid)) frame.contentWindow.location.reload();
        if (ui.wait) hands.wait = ui.pid || pid; // shown once it's made
      }
      if (hands.wait && hands.wait === pid && !j.busy) {
        hands.wait = '';
        frame.contentWindow.location.reload();
      }
    }
  } catch { /* next time */ }
  setTimeout(deskLoop, 2500);
}
deskLoop();

// ---------------------------------------------------------------- the ring around the orb, and the type box
function mute() {
  S.muted = !S.muted;
  root.classList.toggle('muted', S.muted);
  show(S.muted ? 'Mic off: Cello isn\u2019t listening' : 'Listening again');
}
// no word, no tool and no sign of thought for 30 s after they asked: its line or its mind is stuck. It restarts
// itself quietly (a fresh line and mind; the page, the film and the work running stay) and the new mind gets the last
// few turns and what was still unanswered, so it carries on instead of greeting. Once in two minutes at most: a
// second stall soon after is said plainly instead of looping.
function selfRestart() {
  const now = performance.now();
  if (S.restartedAt && now - S.restartedAt < 120000) { show('I\u2019m stuck on that one. Say it again, or restart me from the ring.'); return; }
  S.restartedAt = now;
  note('event', 'no reply for 30 s: restarted itself');
  closeTurn();
  const before = recent.slice(-6).join('\n');
  const ask = (S.lastAsk || '').trim();
  S.carry = '(Note, not them talking: you were restarted because your last reply never came; the page, the film and '
    + `any work running are as they were. ${before ? `Just before:\n${before}\n` : ''}`
    + `${ask ? `Still unanswered, answer it now: "${ask.slice(0, 600)}". ` : ''}Start with "I lost my place, I'm back," then carry on. Don't greet.)`;
  show('I lost my place, I\u2019m back.', 4000);
  restart(true);
}
function restart(quiet = false) { // a fresh line and a fresh mind; the page and the film stay
  if (!quiet) show('Restarting\u2026');
  sleep();
  S.handle = '';
  sessionStorage.removeItem('cello-handle');
  S.greeted = false;
  setTimeout(wake, 600);
}
async function stopWork() {
  stop();
  try {
    const r = await fetch(`/api/work/state?pid=${film() || ''}&since=1e12`, { credentials: 'same-origin' });
    const j = await r.json();
    const live = (j.work || []).filter((w) => w.status === 'running' || w.status === 'queued' || w.status === 'asking');
    for (const w of live) {
      await fetch('/api/work/stop', { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF': csrf }, body: JSON.stringify({ id: w.id }) });
    }
    show(live.length ? `Stopped ${live.length === 1 ? 'the task' : `${live.length} tasks`}.` : 'Nothing was running.');
  } catch { show('Couldn\u2019t reach the work to stop it.'); }
}
async function record() {
  let d = doc();
  let b = d && d.querySelector('[data-record]');
  if (!b) { await go('/new'); d = doc(); b = d && d.querySelector('[data-record="camera"]'); }
  if (b) b.click(); else show('Recording opens from a film or the new-film page.');
}
function theme() {
  const t = doc() && doc().querySelector('[data-theme-toggle]');
  if (t) t.click();
}
function where() {
  try {
    const d = doc();
    return { stage: d?.body?.dataset.stage || '', kind: d?.body?.dataset.kind || '', page: frame.contentWindow.location.pathname };
  } catch { return { stage: '', kind: '', page: '/' }; }
}
const comp = setupComposer({ say: (t) => { if (!S.on) wake(); say(t); }, mute, where });
const ring = setupRing({ S, orb, csrf, show, hidden, doze, turnOff, restart, stopWork, record, theme, mute,
  type: () => comp.type(), typing: () => comp.typing(), book: () => told });
