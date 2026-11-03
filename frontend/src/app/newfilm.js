// The new film: their words first, examples that show instead of modes, clips dropped anywhere (watched, and a
// suggestion written from what is in them), and recording on the spot. A drop or a take begins the film at once (a
// draft), so what they add has somewhere to go before they press Start, which fills it in.
import { api, uploadFile } from './studio.js';
import { toast } from './ui.js';

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const isVideo = (f) => f.type.startsWith('video/') || /\.(mov|mp4|m4v|mkv|webm|avi|mts|3gp)$/i.test(f.name);

// ---------------------------------------------------------------- the box: a ghost that types ideas, examples that write
function ghost(box, el) {
  const ideas = ['Tell me what you want to make…', 'a talk where my words come alive beside me',
    'our weekend away, on the beat', "a cartoon about my dog's secret life", 'my voice memo, turned into an animation'];
  // gone the moment the box has words or they click into it (never typed under what they're about to write)
  const hide = () => { el.classList.toggle('off', !!box.value || document.activeElement === box || box.dataset.typing === '1'); };
  box.addEventListener('input', hide);
  box.addEventListener('focus', hide);
  box.addEventListener('blur', hide);
  window.addEventListener('pageshow', hide); // a value the browser puts back on Back never fires 'input'
  el.style.transition = 'none'; // at once the first time: never two texts on top of each other
  hide();
  requestAnimationFrame(() => { el.style.transition = ''; });
  if (reduced) { el.textContent = ideas[0]; return; }
  (async () => {
    for (let i = 0; ; i += 1) {
      const s = ideas[i % ideas.length];
      for (let k = 0; k <= s.length; k += 1) { el.textContent = s.slice(0, k); hide(); await sleep(38); }
      await sleep(1700);
      for (let k = s.length; k >= 0; k -= 3) { el.textContent = s.slice(0, k); await sleep(14); }
    }
  })();
}

let typing = 0; // a newer example tapped stops the one still being typed (fast taps never mix two)
async function typeInto(box, text) {
  const mine = ++typing;
  box.dataset.typing = '1';
  box.value = '';
  box.dispatchEvent(new Event('input'));
  if (reduced) { box.value = text; } else {
    for (let k = 1; k <= text.length; k += 1) {
      if (mine !== typing) return;
      box.value = text.slice(0, k);
      await sleep(14);
    }
  }
  if (mine !== typing) return;
  box.dataset.typing = '';
  box.focus();
  box.setSelectionRange(box.value.length, box.value.length);
  box.dispatchEvent(new Event('input'));
}

// ---------------------------------------------------------------- the tiny moving examples
const ORB = [[255, 179, 71], [255, 106, 61], [255, 46, 126], [143, 91, 255]];
function fit(cv) {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = Math.round(cv.clientWidth * dpr);
  const h = Math.round(cv.clientHeight * dpr);
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
}
function drawMini(cv, now) {
  fit(cv);
  const c = cv.getContext('2d');
  const W = cv.width;
  const H = cv.height;
  if (!W || !H) return;
  const kind = cv.dataset.kind;
  if (kind === 'words') { // them, talking; the thing they describe is born beside them and glows on them
    let g = c.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, '#1a1325'); g.addColorStop(1, '#0b0810');
    c.fillStyle = g; c.fillRect(0, 0, W, H);
    const ph = (now * 0.32) % 1;
    const born = Math.min(1, Math.max(0, (ph - 0.15) / 0.12));
    const pop = born < 1 ? born * (1.25 - 0.25 * born) : 1;
    const bx = W * 0.66 + Math.sin(now * 1.3) * W * 0.03;
    const by = H * 0.42 + Math.cos(now * 1.7) * H * 0.03;
    const r = H * 0.13 * pop;
    if (r > 0) {
      const halo = c.createRadialGradient(bx, by, 0, bx, by, r * 3.2);
      halo.addColorStop(0, 'rgba(255,170,90,.45)'); halo.addColorStop(1, 'rgba(255,90,60,0)');
      c.fillStyle = halo; c.fillRect(0, 0, W, H);
      g = c.createRadialGradient(bx - r * 0.35, by - r * 0.35, r * 0.1, bx, by, r);
      g.addColorStop(0, '#fff6e0'); g.addColorStop(0.45, '#ffb347'); g.addColorStop(1, '#ff4a2e');
      c.fillStyle = g; c.beginPath(); c.arc(bx, by, r, 0, Math.PI * 2); c.fill();
    }
    // the person: a soft silhouette, lit on the side the light is
    const px = W * 0.3;
    g = c.createLinearGradient(px - W * 0.12, 0, px + W * 0.12, 0);
    g.addColorStop(0, '#2c2338'); g.addColorStop(1, born ? '#6b4a52' : '#3a2f48');
    c.fillStyle = g;
    c.beginPath(); c.ellipse(px, H * 1.02, W * 0.17, H * 0.36, 0, Math.PI, 0); c.fill();
    c.beginPath(); c.arc(px, H * 0.47, H * 0.13, 0, Math.PI * 2); c.fill();
    return;
  }
  if (kind === 'trip') {
    const beat = now * 2.2;
    const k = Math.floor(beat) % 4;
    const f = beat % 1;
    const pals = [['#ffb26b', '#d9468a'], ['#3fb7d6', '#1d3d7a'], ['#8bd17c', '#2b6b4b'], ['#ffd36b', '#b4231a']];
    const g = c.createLinearGradient(0, 0, W, H);
    g.addColorStop(0, pals[k][0]); g.addColorStop(1, pals[k][1]);
    c.save(); c.translate(W / 2, H / 2); const z = 1.12 - f * 0.1; c.scale(z, z); c.translate(-W / 2, -H / 2);
    c.fillStyle = g; c.fillRect(0, 0, W, H);
    c.fillStyle = 'rgba(0,0,0,.25)'; c.beginPath(); c.moveTo(0, H * 0.75);
    for (let x = 0; x <= W; x += W / 8) c.lineTo(x, H * (0.6 + 0.12 * Math.sin((x / W) * 7 + k)));
    c.lineTo(W, H); c.lineTo(0, H); c.fill();
    c.restore();
    c.fillStyle = `rgba(255,255,255,${(1 - f) * 0.35})`; c.fillRect(0, 0, W, H);
    for (let i = 0; i < 16; i += 1) {
      const on = i === Math.floor(beat * 2) % 16;
      c.fillStyle = on ? '#fff' : 'rgba(255,255,255,.4)';
      c.fillRect(W * (0.08 + i * 0.054), H * 0.88, W * 0.03, on ? H * 0.06 : H * 0.025);
    }
    return;
  }
  if (kind === 'toon') {
    let g = c.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, '#ffd7a8'); g.addColorStop(1, '#ff9e7a');
    c.fillStyle = g; c.fillRect(0, 0, W, H);
    c.fillStyle = '#fff3c4'; c.beginPath(); c.arc(W * 0.8, H * 0.22, H * 0.11, 0, Math.PI * 2); c.fill();
    c.fillStyle = '#5aa35a'; c.beginPath(); c.ellipse(W * 0.5, H * 1.08, W * 0.75, H * 0.42, 0, 0, Math.PI * 2); c.fill();
    const hop = Math.abs(Math.sin(now * 4)) * H * 0.1;
    const x = W * (0.3 + 0.4 * ((now * 0.12) % 1));
    c.fillStyle = 'rgba(0,0,0,.18)'; c.beginPath(); c.ellipse(x, H * 0.74, W * 0.05 * (1 - (hop / H) * 2), H * 0.015, 0, 0, Math.PI * 2); c.fill();
    c.fillStyle = '#ff6a3d'; c.beginPath(); c.roundRect(x - W * 0.04, H * 0.5 - hop, W * 0.08, H * 0.22, W * 0.03); c.fill();
    c.fillStyle = '#ffd9b8'; c.beginPath(); c.arc(x, H * 0.45 - hop, W * 0.045, 0, Math.PI * 2); c.fill();
    c.fillStyle = '#3a2e47'; c.beginPath(); c.arc(x + W * 0.015, H * 0.44 - hop, W * 0.007, 0, Math.PI * 2); c.fill();
    for (let i = 0; i < 3; i += 1) {
      const a = now * 2 + i * 2.1;
      c.fillStyle = ['#8f5bff', '#ff2e7e', '#3fb7d6'][i];
      c.save(); c.translate(x + Math.cos(a) * W * 0.12, H * 0.4 - hop + Math.sin(a) * H * 0.1); c.rotate(a);
      c.fillRect(-W * 0.012, -W * 0.012, W * 0.024, W * 0.024); c.restore();
    }
    return;
  }
  // voice: a voice memo's bars gather into a designed shape
  c.fillStyle = '#100c18'; c.fillRect(0, 0, W, H);
  const ph = (now * 0.35) % 1;
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
  const cl = (t) => Math.max(0, Math.min(1, t));
  const k = ease(cl((ph - 0.35) / 0.3)) * (1 - ease(cl((ph - 0.85) / 0.15)));
  for (let i = 0; i < 24; i += 1) {
    const v = 0.2 + 0.8 * Math.abs(Math.sin(i * 0.7 + now * 5) * Math.sin(i * 0.23 + now * 1.3));
    const bx = W * (0.12 + i * 0.032);
    const bh = H * 0.4 * v;
    const a = (i / 24) * Math.PI * 2;
    const x = bx + (W / 2 + Math.cos(a) * H * 0.22 - bx) * k;
    const y = H / 2 + Math.sin(a) * H * 0.22 * k;
    const h = bh + (H * 0.04 - bh) * k;
    const [r, gg, b] = ORB[i % 4];
    c.fillStyle = `rgba(${r},${gg},${b},.95)`;
    c.beginPath(); c.roundRect(x - W * 0.01, y - h / 2, W * 0.02, h, W * 0.01); c.fill();
  }
}

function minis() {
  const list = $$('canvas.mini');
  if (!list.length) return;
  const seen = new Map();
  const io = new IntersectionObserver((es) => es.forEach((e) => seen.set(e.target, e.isIntersecting)));
  list.forEach((m) => io.observe(m));
  const tick = (t) => {
    list.forEach((m) => { if (seen.get(m)) drawMini(m, reduced ? 2.4 : t / 1000); });
    if (!reduced) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

// ---------------------------------------------------------------- the draft: begun by the first drop or take
const state = { pid: '', kind: '', making: null };
// an example they tapped and never changed is not what their clips are about
const examples = () => new Set($$('.ex[data-say]').map((x) => x.dataset.say.trim()));
const untouchedExample = (box) => !!box && examples().has(box.value.trim());
async function draft(source) {
  const input = $('[data-draft]');
  if (input && input.value) { state.pid = input.value; return state.pid; }
  if (state.making) return state.making;
  const box = $('[data-say-box]');
  const describe = box && !untouchedExample(box) ? box.value : '';
  state.making = api('POST', '/api/new/draft', { source, describe }).then((r) => {
    state.pid = r.pid;
    state.kind = r.kind;
    document.body.dataset.kind = r.kind;
    document.body.dataset.shapes = r.shapes;
    if (input) input.value = r.pid;
    // this page is now this film's start: Back from the film comes here with its words and clips, not to a blank page
    history.replaceState(history.state, '', `/new?film=${r.pid}`);
    return r.pid;
  });
  try { return await state.making; } finally { state.making = null; }
}

// ---------------------------------------------------------------- clips dropped anywhere on the page
function clips() {
  const zone = $('[data-dropzone]');
  if (!zone) return;
  const strip = $('[data-strip]', zone);
  const text = $('[data-drop-text]', zone);
  const input = $('[data-drop-input]', zone);
  const sug = $('[data-suggest]');
  const sugText = $('[data-suggest-text]');
  const sugFrom = $('[data-suggest-from]');
  let landed = 0;
  let going = 0;
  const tile = (file) => {
    const t = document.createElement('i');
    t.title = file.name;
    const bar = document.createElement('b');
    t.append(bar);
    if (isVideo(file)) { // a frame from the clip itself, made here (nothing is sent anywhere for it)
      const url = URL.createObjectURL(file);
      const v = document.createElement('video');
      v.muted = true; v.playsInline = true; v.preload = 'metadata'; v.src = url;
      v.addEventListener('loadeddata', () => { v.currentTime = Math.min(1, (v.duration || 2) / 3); }, { once: true });
      v.addEventListener('seeked', () => {
        try {
          const c = document.createElement('canvas');
          c.width = 76; c.height = 104;
          const ctx = c.getContext('2d');
          const s = Math.max(c.width / v.videoWidth, c.height / v.videoHeight);
          ctx.drawImage(v, (c.width - v.videoWidth * s) / 2, (c.height - v.videoHeight * s) / 2, v.videoWidth * s, v.videoHeight * s);
          t.style.backgroundImage = `url(${c.toDataURL('image/jpeg', 0.7)})`;
        } catch { /* no frame: the tile stays a colour */ }
        v.removeAttribute('src'); v.load(); URL.revokeObjectURL(url);
      }, { once: true });
    }
    strip.append(t);
    return {
      cancelled: false,
      uploadId: null,
      set(f) { bar.style.width = `${Math.round(f * 100)}%`; },
      ok() { t.classList.add('ok'); },
      fail(msg) { t.classList.add('off'); t.title = `${file.name}: ${msg}`; },
    };
  };
  const say = (s) => { text.textContent = s; };
  async function suggest() {
    if (!state.pid || !landed) return;
    sug.hidden = false;
    sugFrom.textContent = `From the ${landed} clip${landed === 1 ? '' : 's'} you just dropped`;
    sugText.textContent = '';
    sugText.classList.add('thinking');
    zone.classList.add('scan');
    try {
      const r = await api('POST', `/api/p/${state.pid}/suggest`);
      sugText.classList.remove('thinking');
      if (!r.text) { sug.hidden = true; return; }
      for (const w of r.text.split(' ')) { sugText.textContent += (sugText.textContent ? ' ' : '') + w; await sleep(reduced ? 0 : 45); }
    } catch { sug.hidden = true; } finally { zone.classList.remove('scan'); }
  }
  async function add(files) {
    const vids = files.filter(isVideo);
    if (!vids.length) { toast('Those aren’t video files: drop clips (mp4, mov and more).'); return; }
    going += vids.length;
    const box = $('[data-say-box]');
    if (untouchedExample(box)) { box.value = ''; box.dispatchEvent(new Event('input')); } // their clips lead, not our example
    say(`${vids.length} clip${vids.length === 1 ? '' : 's'} going in…`);
    let pid;
    try { pid = await draft('clips'); } catch (err) { say(err.message); going -= vids.length; return; }
    const jobs = vids.map((f) => ({ f, ui: tile(f) }));
    const lane = async () => {
      for (let j = jobs.shift(); j; j = jobs.shift()) {
        try { await uploadFile(pid, j.f, 'clip', j.ui); j.ui.ok(); landed += 1; } catch (err) { j.ui.fail(err.message); toast(`${j.f.name}: ${err.message}`); }
        going -= 1;
        say(going ? `${landed} in, ${going} still going in…` : `${landed} clip${landed === 1 ? '' : 's'} in. Watching them…`);
      }
    };
    await Promise.all([lane(), lane()]);
    if (!going && !landed && !$('[data-drop-text]').dataset.kept) { // nothing got in: no empty film left behind
      await api('POST', '/api/new/discard', { pid }).catch(() => {});
      state.pid = ''; const input = $('[data-draft]'); if (input) input.value = '';
      history.replaceState(history.state, '', '/new');
      say('None of those got in. Try again, or drop other clips.');
      return;
    }
    if (!going) {
      await suggest();
      say(`${landed} clip${landed === 1 ? '' : 's'} in your film. Drop more, or press Start.`);
    }
  }
  input.addEventListener('change', () => { add([...input.files]); input.value = ''; });
  const page = $('[data-newfilm]');
  page.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('over'); });
  page.addEventListener('dragleave', (e) => { if (!page.contains(e.relatedTarget)) zone.classList.remove('over'); });
  page.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('over');
    const files = [...(e.dataTransfer?.files || [])];
    if (files.length) add(files);
  });
  $('[data-suggest-use]').addEventListener('click', () => typeInto($('[data-say-box]'), sugText.textContent));
}

// ---------------------------------------------------------------- dictation, where the browser can hear
function dictate() {
  const btn = $('[data-dictate]');
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!btn || !Rec) return;
  btn.hidden = false;
  const box = $('[data-say-box]');
  let rec = null;
  btn.addEventListener('click', () => {
    if (rec) { rec.stop(); return; }
    rec = new Rec();
    rec.interimResults = true;
    rec.lang = document.documentElement.lang || 'en';
    const before = box.value ? `${box.value.trim()} ` : '';
    btn.classList.add('live');
    rec.onresult = (e) => { box.value = before + [...e.results].map((r) => r[0].transcript).join(''); box.dispatchEvent(new Event('input')); };
    rec.onend = () => { btn.classList.remove('live'); rec = null; };
    rec.onerror = () => { btn.classList.remove('live'); rec = null; };
    rec.start();
  });
}

export function newFilmPage() {
  const page = $('[data-newfilm]');
  if (!page) return;
  const box = $('[data-say-box]');
  ghost(box, $('[data-ghost]'));
  $$('.ex[data-say]').forEach((x) => x.addEventListener('click', () => {
    typeInto(box, x.dataset.say);
    box.closest('.say').scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'center' });
  }));
  box.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) box.form.requestSubmit(); });
  minis();
  clips();
  dictate();
  // a take recorded here begins the film (a talk from the camera, a voice film from a voice memo) and goes into it
  window.flicelloTake = async (file, mode, onProgress) => {
    if (mode === 'voice' && state.kind && state.kind !== 'voice') throw new Error('A voice memo starts a voice film; these clips are a film from footage. Start a new film for the memo.');
    const pid = await draft(mode);
    const ui = { cancelled: false, uploadId: null, set: (f) => onProgress && onProgress(f) };
    await uploadFile(pid, file, mode === 'voice' ? 'song' : 'clip', ui);
    const text = $('[data-drop-text]');
    if (text) text.textContent = mode === 'voice' ? 'Your voice memo is in. Say what it’s about, then press Start.'
      : `Your ${mode === 'screen' ? 'screen recording' : 'take'} is in. Say what it’s for, then press Start.`;
  };
}

export { draft };
