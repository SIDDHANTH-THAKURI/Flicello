// Cello's shell: the app lives in a frame and Cello lives over it, so its voice, its ears and its body stay through
// every page. Three parts, kept apart:
// - the body (orb.js): only draws; told its phase, where to look, how loud, the sound's shape;
// - the voice: a live Gemini session in the browser (a one-session token from our server), mic in, voice out,
//   interruptible, resumed if the line drops;
// - the hands: its tools, run here when they move the screen, on our server when they change a film; and a light that
//   reaches from Cello to whatever it is working on, so you see it work.
// The phase is worked out from facts on every frame, never pushed, so it can't go stale.
import { Orb } from './orb.js';
import './cello.css';

const $ = (s, r = document) => r.querySelector(s);
const csrf = $('meta[name="csrf"]').content;
const frame = $('#cello-app');
const root = $('#cello');
const body = $('.cello-body', root);
const orb = new Orb($('#cello-orb'));
const beam = $('#cello-beam');
const caption = $('.cello-caption', root);
const pill = $('.cello-pill', root);
const menu = $('.cello-menu', root);
const typer = $('.cello-type', root);
const WS = 'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained';
const PARTS = { preview: '[data-preview]', draft: '#draft', timeline: '#timeline', styles: '#styles', ready: '#ready',
  exports: '#exports', review: '#review', versions: '.screens', brief: '#editor', people: '#editor .people' };
const TOOL_PART = { edit: 'draft', style: 'styles', fix_score: 'ready', export: 'exports', review_link: 'review',
  make_it: 'draft', discard: 'draft', effect: 'timeline', ai_clip: 'timeline', music: 'preview', step: null };

const S = {
  on: false, ws: null, open: false, ever: false, woke: 0, awaiting: false, acting: 0, muted: false, mic: 0,
  out: 0, handle: sessionStorage.getItem('cello-handle') || '', queue: [], lastSound: 0, since: null, cut: false, inReply: 0, heard: 0,
  greeted: false, retry: 0, aec: true, target: null, targetUntil: 0, pending: [], asked: 0, warned: false,
};

// ---------------------------------------------------------------- the frame: which film, which page
function film() {
  try {
    const m = frame.contentWindow.location.pathname.match(/^\/p\/([^/]+)/);
    return m ? m[1] : null;
  } catch { return null; }
}
function doc() { try { return frame.contentDocument; } catch { return null; } }

frame.addEventListener('load', () => {
  const d = doc();
  if (!d) return;
  history.replaceState(null, '', `/cello?to=${encodeURIComponent(frame.contentWindow.location.pathname + frame.contentWindow.location.search)}`);
  d.addEventListener('pointermove', (e) => { // its eyes follow you around the page
    const r = body.getBoundingClientRect();
    const fr = frame.getBoundingClientRect();
    gazeAt(e.clientX + fr.left, e.clientY + fr.top, r);
  });
  document.title = d.title || 'Flicello';
  d.addEventListener('keydown', (e) => { if (e.key === 'Escape') stop(); });
  d.addEventListener('pointerdown', () => { menu.hidden = true; unlock(); });
  d.addEventListener('click', (e) => { // other sites can't be held in the frame: they open in a tab
    const a = e.target.closest && e.target.closest('a[href]');
    if (a && a.origin !== window.location.origin) { e.preventDefault(); window.open(a.href, '_blank', 'noopener'); }
  }, true);
  S.target = null;
});

function screen() {
  try { return { page: frame.contentWindow.location.pathname, title: doc().title }; } catch { return {}; }
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
  menu.hidden = true;
  typer.hidden = true;
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
    if (!S.open || S.muted || !S.on) return;
    const talking = playing.size > 0;
    if (talking && !S.aec && S.mic < S.out * 2.4) return; // its own voice leaking back in, not them
    const b = new Uint8Array(e.data.pcm);
    let s = '';
    for (let i = 0; i < b.length; i += 1) s += String.fromCharCode(b[i]);
    send({ realtimeInput: { audio: { data: btoa(s), mimeType: 'audio/pcm;rate=16000' } } });
  };
}

// ---------------------------------------------------------------- the line to Gemini
function send(msg) { if (S.ws && S.ws.readyState === 1) S.ws.send(JSON.stringify(msg)); }
function say(text) {
  if (!S.open) { S.pending.push(text); return; }
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
  const ws = new WebSocket(`${WS}?access_token=${encodeURIComponent(tok.token)}`);
  S.ws = ws;
  ws.resumed = !!S.handle;
  ws.onopen = () => {
    ws.send(JSON.stringify({ setup: S.handle ? { sessionResumption: { handle: S.handle } } : {} }));
  };
  ws.onmessage = async (e) => {
    const msg = JSON.parse(typeof e.data === 'string' ? e.data : await e.data.text());
    onMessage(msg);
  };
  ws.onclose = () => {
    if (S.ws !== ws) return; // an old line closing after a new one opened
    if (ws.resumed && !ws.ready) { S.handle = ''; sessionStorage.removeItem('cello-handle'); } // that handle has expired
    S.open = false;
    S.ws = null;
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
    if (!S.greeted) {
      S.greeted = true;
      hidden(film() ? '(Note, not them talking: they just woke you while a film is open. Call look, then greet them in a few words and say one useful thing about where the film is.)'
        : '(Note, not them talking: they just woke you. Greet them in a few words; you are ready to work.)');
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
  if (m.toolCall) tools(m.toolCall.functionCalls || []);
  const sc = m.serverContent;
  if (!sc) return;
  if (sc.interrupted) { hush(); S.cut = false; }
  for (const p of sc.modelTurn?.parts || []) {
    if (p.inlineData?.data) { S.awaiting = false; S.inReply = performance.now(); play(p.inlineData.data); }
  }
  if (sc.inputTranscription?.text) {
    S.cut = false;
    if (!said.you) said.me = ''; // a new thing from them: its last answer steps aside
    line('you', sc.inputTranscription.text);
    S.awaiting = true;
    S.asked = performance.now();
  }
  if (sc.outputTranscription?.text) line('me', sc.outputTranscription.text);
  if (sc.turnComplete) { S.awaiting = false; S.asked = 0; S.cut = false; S.inReply = 0; }
}

// what was just said, briefly, beside the orb (never a chat log)
const said = { you: '', me: '', t: 0 };
function line(who, text) {
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
function show(text) {
  pill.textContent = text;
  pill.hidden = false;
  clearTimeout(show.t);
  show.t = setTimeout(() => { pill.hidden = true; }, 4000);
}

// ---------------------------------------------------------------- hands
async function tools(calls) {
  const answers = [];
  for (const fc of calls) {
    S.acting += 1;
    const name = fc.name;
    show({ look: 'Looking', edit: 'Making the changes', style: 'Trying the style', make_it: 'Making it', export: 'Exporting',
      effect: 'Working on the shot', ai_clip: 'Making a clip', music: 'Remaking the music', open: 'Opening', show: 'Showing you',
      play: 'Playing', diagnose: 'Checking what broke', repair: 'Repairing' }[name] || 'On it');
    let result;
    try {
      result = await run(name, fc.args || {});
    } catch (err) {
      result = { result: `Not done: ${err.message}` };
    }
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
  if (name === 'look' && out.result && typeof out.result === 'object') out.result = { ...out.result, ...screen() };
  const ui = out.ui || {};
  if (ui.go) frame.src = ui.go;
  else if (ui.reload || ui.wait) frame.contentWindow.location.reload();
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
  if (places[key]) { frame.src = places[key][0]; return { result: `Opened ${places[key][1]}.` }; }
  const r = await fetch('/api/cello/act', { method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF': csrf }, body: JSON.stringify({ name: 'list_films', args: {} }) });
  const films = (await r.json()).result || [];
  const hit = films.find((f) => f.title.toLowerCase() === key) || films.find((f) => f.title.toLowerCase().includes(key));
  if (!hit) return { result: `No film called "${what}". Their films: ${films.map((f) => f.title).slice(0, 8).join(', ')}.` };
  frame.src = `/p/${hit.id}`;
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
  const el = d && d.querySelector(PARTS[part] || part);
  if (!el) return false;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
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

async function wake() {
  if (S.on) return;
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
    typer.hidden = false;
    typer.t.focus();
  }
  connect();
}

function sleep() {
  chime(false);
  S.on = false;
  hush();
  try { S.ws?.close(); } catch { /* gone */ }
  S.ws = null;
  S.open = false;
  S.greeted = false;
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
  else menu.hidden = !menu.hidden;
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

menu.addEventListener('click', (e) => {
  const what = e.target.dataset.cello;
  menu.hidden = true;
  if (what === 'type') { typer.hidden = false; typer.t.focus(); }
  if (what === 'mute') {
    S.muted = !S.muted;
    e.target.textContent = S.muted ? 'Unmute my mic' : 'Mute my mic';
    root.classList.toggle('muted', S.muted);
    show(S.muted ? 'Mic off: Cello isn\u2019t listening' : 'Listening again');
  }
  if (what === 'off') turnOff();
});
typer.addEventListener('submit', (e) => {
  e.preventDefault();
  const t = typer.t.value.trim();
  if (!t) return;
  typer.t.value = '';
  typer.hidden = true; // said; Cello answers out loud
  line('you', t);
  if (!S.on) wake();
  say(t);
});
window.addEventListener('keydown', (e) => { if (e.key === 'Escape') stop(); });
typer.t.addEventListener('blur', () => { if (!typer.t.value.trim()) setTimeout(() => { typer.hidden = true; }, 150); });

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
  if (speaking) { orb.bands = outBands; orb.level = Math.min(1, S.out * 2.4); } else if (phase === 'hearing') {
    orb.bands = bands(micAn).map((x) => x * 0.7);
    orb.level = Math.min(1, S.mic * 9);
    if (now > S.targetUntil) orb.look(0, -0.35); // it looks at you while you talk
  } else { orb.bands = new Float32Array(8); orb.level = 0; }
  if (!speaking && now - said.t > 3200) caption.classList.remove('on');
  if (S.awaiting && !S.acting && !speaking && S.asked && now - S.asked > 25000) {
    S.awaiting = false;
    S.asked = 0;
    show('I lost that one. Say it again?');
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
