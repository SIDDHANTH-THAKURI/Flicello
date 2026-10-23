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
  out: 0, handle: sessionStorage.getItem('cello-handle') || '', queue: [], lastSound: 0, since: Date.now() / 1000,
  greeted: false, retry: 0, aec: true, target: null, targetUntil: 0,
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
  if (S.open) context(`(Note, not them talking: the screen now shows "${d.title}" at ${frame.contentWindow.location.pathname}. Say nothing about it unless it helps.)`);
});

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
let epoch = 0;
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
  epoch += 1;
  for (const s of playing) { try { s.stop(); } catch { /* done */ } }
  playing.clear();
  nextAt = 0;
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
function say(text) { send({ realtimeInput: { text } }); S.awaiting = true; }
function context(text) { send({ clientContent: { turns: [{ role: 'user', parts: [{ text }] }], turnComplete: false } }); }
function hidden(text) { S.queue.push(text); }

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
  ws.onopen = () => {
    ws.send(JSON.stringify({ setup: S.handle ? { sessionResumption: { handle: S.handle } } : {} }));
  };
  ws.onmessage = async (e) => {
    const msg = JSON.parse(typeof e.data === 'string' ? e.data : await e.data.text());
    onMessage(msg);
  };
  ws.onclose = () => {
    S.open = false;
    S.ws = null;
    if (S.on) setTimeout(() => { if (S.on && !S.ws) connect(); }, Math.min(8000, 800 * 2 ** S.retry++));
  };
}

function onMessage(m) {
  if (m.setupComplete) {
    S.open = true;
    S.ever = true;
    S.retry = 0;
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
  if (m.goAway) { try { S.ws.close(); } catch { /* reconnects */ } }
  if (m.toolCall) tools(m.toolCall.functionCalls || []);
  const sc = m.serverContent;
  if (!sc) return;
  if (sc.interrupted) hush();
  for (const p of sc.modelTurn?.parts || []) if (p.inlineData?.data) { S.awaiting = false; play(p.inlineData.data); }
  if (sc.inputTranscription?.text) { line('you', sc.inputTranscription.text); S.awaiting = true; }
  if (sc.outputTranscription?.text) line('me', sc.outputTranscription.text);
  if (sc.turnComplete) S.awaiting = false;
}

// what was just said, briefly, beside the orb (never a chat log)
const said = { you: '', me: '', t: 0 };
function line(who, text) {
  if (performance.now() - said.t > 2500) { said.you = ''; said.me = ''; }
  said[who] = `${said[who]}${text}`.slice(-180);
  if (who === 'me') said.you = said.you.slice(-60);
  said.t = performance.now();
  $('.you', caption).textContent = said.you;
  $('.me', caption).textContent = said.me;
  caption.classList.add('on');
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
  const out = await r.json();
  const ui = out.ui || {};
  if (ui.go) frame.src = ui.go;
  else if (ui.reload || ui.wait) frame.contentWindow.location.reload();
  if (ui.wait) S.watch = true;
  return out;
}

async function openThing(what) {
  const places = { home: '/', films: '/', new: '/new', style: '/style', 'my style': '/style', account: '/account' };
  const key = what.toLowerCase().trim();
  if (places[key]) { frame.src = places[key]; return { result: `Opened ${key}.` }; }
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
  const old = el.style.boxShadow;
  el.style.transition = 'box-shadow .4s ease';
  el.style.boxShadow = '0 0 0 3px rgba(255, 190, 110, .95), 0 0 40px 6px rgba(255, 160, 80, .45)';
  setTimeout(() => { el.style.boxShadow = old; }, ms);
  return true;
}

function drawBeam(now) {
  const g = beam.getContext('2d');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  if (beam.width !== innerWidth * dpr) { beam.width = innerWidth * dpr; beam.height = innerHeight * dpr; }
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, innerWidth, innerHeight);
  if (!S.target || now > S.targetUntil || !S.target.isConnected) return;
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
      const r = await fetch(`/api/cello/events?pid=${pid}&since=${S.since}`, { credentials: 'same-origin' });
      const j = await r.json();
      S.since = j.now;
      if (j.events.length) {
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
  if (!S.queue.length || !S.open || playing.size || S.awaiting || S.acting) return;
  if (S.mic > 0.03 || now - S.lastSound < 1500) return;
  say(S.queue.shift());
}

// ---------------------------------------------------------------- waking, sleeping, moving
async function wake() {
  if (S.on) return;
  S.on = true;
  S.woke = performance.now();
  localStorage.setItem('cello-on', '1');
  audio();
  await ctx.resume();
  try { await ears(); } catch { show('Cello can hear you once the microphone is allowed; you can type meanwhile.'); typer.hidden = false; }
  connect();
}

function sleep() {
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
body.addEventListener('pointerup', () => {
  if (drag && !drag.moved) {
    if (!S.on) wake();
    else menu.hidden = !menu.hidden;
  }
  drag = null;
});
body.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (!S.on) wake(); else menu.hidden = !menu.hidden; } });
function place(fx, fy) {
  fx = Math.max(0.04, Math.min(0.96, fx));
  fy = Math.max(0.06, Math.min(0.94, fy));
  root.style.setProperty('--x', `${fx * 100}vw`);
  root.style.setProperty('--y', `${fy * 100}vh`);
  localStorage.setItem('cello-pos', JSON.stringify([fx, fy]));
}
try { const p = JSON.parse(localStorage.getItem('cello-pos')); if (p) place(p[0], p[1]); } catch { /* default */ }

menu.addEventListener('click', (e) => {
  const what = e.target.dataset.cello;
  menu.hidden = true;
  if (what === 'type') { typer.hidden = false; typer.t.focus(); }
  if (what === 'mute') { S.muted = !S.muted; e.target.textContent = S.muted ? 'Unmute my mic' : 'Mute my mic'; }
  if (what === 'off') turnOff();
});
typer.addEventListener('submit', (e) => {
  e.preventDefault();
  const t = typer.t.value.trim();
  if (!t) return;
  typer.t.value = '';
  line('you', t);
  if (!S.on) wake().then(() => setTimeout(() => say(t), 1500));
  else say(t);
});
window.addEventListener('keydown', (e) => { if (e.key === 'Escape') { hush(); menu.hidden = true; } });

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
  const speaking = playing.size > 0;
  if (outAn) { const b = bands(outAn); S.out = b.reduce((a, x) => a + x, 0) / 8; }
  let phase = 'idle';
  if (!S.on) phase = 'sleep';
  else if (!S.open && (S.ever || now - S.woke > 3500)) phase = 'offline';
  else if (S.acting) phase = 'acting';
  else if (speaking) phase = 'speaking';
  else if (S.awaiting) phase = 'thinking';
  else if (S.mic > 0.025 && !S.muted) phase = 'hearing';
  orb.phase = phase;
  root.dataset.state = phase;
  if (speaking) { orb.bands = bands(outAn); orb.level = Math.min(1, S.out * 2.4); } else if (phase === 'hearing') {
    orb.bands = bands(micAn).map((x) => x * 0.7);
    orb.level = Math.min(1, S.mic * 9);
    if (now > S.targetUntil) orb.look(0, -0.35); // it looks at you while you talk
  } else { orb.bands = new Float32Array(8); orb.level = 0; }
  if (performance.now() - said.t > 3200) caption.classList.remove('on');
  flush(now);
  drawBeam(now);
  orb.resize();
  orb.frame(dt);
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
watch();
if (localStorage.getItem('cello-on') === '1') root.classList.add('ready'); // tap to bring it back (browsers need the tap)
