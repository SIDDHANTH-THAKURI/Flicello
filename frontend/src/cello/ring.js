// Cello's ring: tap the orb (while it's awake) and its controls open around it, with a spring, a light sweeping to the
// one you point at, live states (green = on), and a panel beside the ring for the ones that need one. Everything here
// also works by voice ("rest for a bit", "take full control", "restart yourself"). And the access levels: asked once,
// on the first hello, changed any time.
import { Orb } from './orb.js';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
function icon(name) {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('class', 'ico');
  const u = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  u.setAttribute('href', `#i-${name}`);
  s.append(u);
  return s;
}

export const LEVELS = [
  { id: 'advisor', name: 'Advisor', bars: 1, says: 'Looks, answers, points and suggests. Changes nothing.',
    flow: [['you', 'make the ball bigger'], ['cello', 'points at it, suggests 1.5×'], ['you', 'make the change yourself']] },
  { id: 'copilot', name: 'Co-pilot', bars: 2, says: 'Small edits at once; a yes before anything that takes time.',
    flow: [['you', 'make the ball bigger'], ['cello', 'shows the change as a draft'], ['you', '“make it”'], ['cello', 'renders the new version']] },
  { id: 'full', name: 'Full access', bars: 3, says: 'Does it all and tells you after. Can work while you are away.',
    flow: [['you', 'make the ball bigger'], ['cello', 'changes, renders, checks'], ['cello', 'tells you: “done, version 4”']] },
];

function accessCards(current, onPick) {
  const wrap = el('div', 'acc');
  LEVELS.forEach((lv) => {
    const card = el('button', `acard${lv.id === (current || 'copilot') ? ' sel' : ''}`);
    card.type = 'button';
    card.dataset.level = lv.id;
    const bars = el('div', 'lvl');
    for (let k = 1; k <= 3; k += 1) bars.append(el('i', k <= lv.bars ? 'f' : ''));
    const h = el('b', 'acc-name', lv.name);
    if (lv.id === 'copilot') h.append(el('span', 'pill', 'recommended'));
    const p = el('p', '', lv.says);
    const flow = el('div', 'flow');
    lv.flow.forEach(([who, what]) => {
      const row = el('div');
      row.append(el('b', who === 'cello' ? 'c' : '', who === 'cello' ? 'Cello' : 'You'), el('span', '', what));
      flow.append(row);
    });
    card.append(bars, h, p, flow);
    card.addEventListener('click', () => {
      $$('.acard', wrap).forEach((c) => c.classList.toggle('sel', c === card));
      onPick(lv.id);
    });
    wrap.append(card);
  });
  let k = 0; // each card plays the same request, so the difference is seen, not read
  const t = setInterval(() => {
    if (!wrap.isConnected) { clearInterval(t); return; }
    const sel = $('.acard.sel', wrap) || $('.acard', wrap);
    const rows = $$('.flow div', sel);
    $$('.flow div', wrap).forEach((r) => r.classList.remove('lit'));
    rows.slice(0, (k % (rows.length + 1)) + 1).forEach((r) => r.classList.add('lit'));
    k += 1;
  }, 900);
  return wrap;
}

function promises() {
  const p = el('div', 'promises');
  [['shield', 'Never deletes anything for good'], ['key', 'Never touches your keys'], ['check', 'Asks before spending paid credit']]
    .forEach(([i, t]) => { const s = el('span'); s.append(icon(i), document.createTextNode(t)); p.append(s); });
  return p;
}

export function setupRing(api) {
  const box = $('.cello-ring');
  const ring = $('[data-ring]', box);
  const sweep = $('.sweep', ring);
  const title = $('[data-ring-title]', box);
  const small = $('[data-ring-small]', box);
  const sayWhat = $('[data-ring-say]', box);
  const panel = $('[data-ring-panel]', box);
  const ringOrb = new Orb($('#ring-orb'));
  let settings = {};
  let raf = 0;

  async function load() {
    try {
      const r = await fetch('/api/cello/settings', { credentials: 'same-origin' });
      if (r.ok) settings = await r.json();
    } catch { /* offline: the ring still works */ }
    api.S.access = settings.access || '';
    return settings;
  }
  async function save(body) {
    const r = await fetch('/api/cello/settings', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF': api.csrf }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || `Error ${r.status}`);
    await load();
    return j;
  }
  const textOnly = () => localStorage.getItem('cello-text') === '1';

  // ---------------------------------------------------------------- the panels beside the ring
  function choiceList(items, current, onPick) {
    const list = el('div', 'choices-list');
    items.forEach((it) => {
      const c = el('button', `choice${it.id === current ? ' sel' : ''}`);
      c.type = 'button';
      c.append(el('span', 'r'));
      const t = el('span');
      t.append(el('b', '', it.name), el('small', '', it.says));
      c.append(t);
      if (it.disabled) c.disabled = true;
      c.addEventListener('click', async () => {
        $$('.choice', list).forEach((x) => x.classList.toggle('sel', x === c));
        try { await onPick(it.id); } catch (err) { api.show(err.message); }
      });
      list.append(c);
    });
    return list;
  }
  const PANELS = {
    access: () => {
      const b = el('div');
      b.append(accessCards(settings.access, async (id) => {
        await save({ access: id });
        api.hidden(`(Note, not them talking: they set your access to ${id}. ${id === 'advisor' ? 'From now on you look, point and suggest; they make changes themselves.' : id === 'full' ? 'From now on you do the whole job and tell them after (still ask before paid credit).' : 'Small edits at once; ask before anything that takes time.'} Acknowledge it in a few words.)`);
      }), promises());
      return ['Access · how much Cello may do', b];
    },
    voice: () => {
      const current = textOnly() ? 'text' : (settings.voice_chosen === 'auto' ? settings.voice : settings.voice_chosen) || settings.voice;
      const items = [
        { id: 'local', name: 'This computer', says: 'Private: Whisper hears, Kokoro speaks, Claude on your plan thinks. About a second to answer on a GPU.', disabled: !settings.local_voice },
        { id: 'gemini', name: 'Gemini Live', says: 'One speech model, quickest replies; needs your Gemini key and sends audio to Google.', disabled: !settings.gemini },
        { id: 'text', name: 'Text only', says: 'Cello writes its answers; nothing is spoken out loud.' },
      ];
      const b = el('div');
      b.append(choiceList(items, current, async (id) => {
        if (id === 'text') { localStorage.setItem('cello-text', '1'); api.show('Text only: Cello writes, nothing is spoken.'); return; }
        localStorage.removeItem('cello-text');
        if (settings.owner) {
          await save({ voice: id });
          api.show('Voice chosen. Restart Cello (in this ring) to hear it.', 5000);
        }
      }));
      if (!settings.owner) b.append(el('p', 'hint', 'Only the owner of this computer changes the voice.'));
      if (settings.local_voice && settings.owner) { // this machine's voice: how fast it speaks, from the next sentence
        const row = el('label', 'speed-row');
        const name = el('span', '', 'Speaking speed');
        const val = el('b', '', `${Number(settings.speed || 1).toFixed(2).replace(/0$/, '')}×`);
        const range = document.createElement('input');
        range.type = 'range';
        range.className = 'ui';
        range.min = '0.7';
        range.max = '1.5';
        range.step = '0.05';
        range.value = String(settings.speed || 1);
        range.setAttribute('aria-label', 'Speaking speed');
        const paint = () => {
          range.style.setProperty('--v', `${((range.value - 0.7) / 0.8) * 100}%`);
          val.textContent = `${Number(range.value).toFixed(2).replace(/0$/, '')}×`;
        };
        range.addEventListener('input', paint);
        range.addEventListener('change', async () => {
          try { await save({ speed: Number(range.value) }); settings.speed = Number(range.value); } catch (err) { api.show(err.message); }
        });
        paint();
        row.append(name, range, val);
        b.append(row);
      }
      return ['Voice · who speaks and hears', b];
    },
    model: () => {
      const b = el('div');
      b.append(choiceList((settings.models || []).map((m) => ({ ...m, disabled: !settings.owner })), settings.model, async (id) => {
        await save({ model: id });
        api.show('Model chosen. Restart Cello (in this ring) to think with it.', 5000);
      }));
      b.append(el('p', 'hint', 'This is the model Cello talks with. Real work (editing, designing, watching footage) has its own model per job: Connect, Models.'));
      return ['Model · what Cello thinks with', b];
    },
    did: () => {
      const b = el('div', 'log');
      const lines = api.book().slice(-24).reverse();
      if (!lines.length) b.append(el('p', 'hint', 'Nothing yet this time. Everything it hears, says and does is listed here.'));
      lines.forEach((ln) => {
        const row = el('div', 'logline');
        const at = new Date(ln.at * 1000);
        row.append(el('span', '', `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`),
          el('span', `who-${ln.who}`, `${{ you: 'You', cello: 'Cello', tool: 'Did', result: '→', note: 'Note', event: 'Event', error: 'Error', line: 'Line' }[ln.who] || ln.who}: ${ln.text}`.slice(0, 220)));
        b.append(row);
      });
      return ['What it did · this session', b];
    },
    restart: () => {
      const b = el('div');
      b.append(el('p', '', 'Restarts Cello’s line and its mind. The page and your film stay exactly where they are.'));
      const go = el('button', 'btn sun', 'Restart Cello now');
      go.type = 'button';
      go.addEventListener('click', () => { close(); api.restart(); });
      b.append(go);
      return ['Restart', b];
    },
    help: () => {
      const b = el('div', 'help-list');
      [['Edit by talking', '“cut shot 4”, “make the stars gold”, “hold the end two seconds longer”'],
        ['Point and say', 'click the film where something is, then say what to change there'],
        ['Make things', '“start a new film from these clips”, “record me”, “make it for TikTok”'],
        ['Look after the film', '“what’s left before I can post?”, “export a square cut”, “a review link for my client”'],
        ['Control me', '“rest for a bit”, “take full control”, “check with me first”, “restart yourself”']]
        .forEach(([h, t]) => { const r = el('div', 'logline'); r.append(el('b', '', h), el('span', '', t)); b.append(r); });
      return ['What can Cello do?', b];
    },
  };
  function openPanel(id) {
    const make = PANELS[id];
    if (!make) { panel.hidden = true; return; }
    const [h, body] = make();
    panel.replaceChildren(el('h5', '', h), body);
    panel.hidden = false;
  }

  // ---------------------------------------------------------------- the controls around the orb
  const CONTROLS = [
    { id: 'type', label: 'Type', icon: 'keys', say: 'let me type', on: () => api.typing(), act: () => { close(); api.type(); } },
    { id: 'mic', label: 'Mic', icon: 'mic', say: 'stop listening', on: () => !api.S.muted, act: () => api.mute() },
    { id: 'wake', label: 'Wake on name', icon: 'ear', say: 'wake when I say your name', on: () => localStorage.getItem('cello-wake') !== '0',
      act: () => { const on = localStorage.getItem('cello-wake') === '0'; localStorage.setItem('cello-wake', on ? '1' : '0'); api.show(on ? 'Say “Cello” any time to wake me.' : 'Only a tap wakes me now.'); } },
    { id: 'rest', label: 'Rest', icon: 'moon', say: 'rest for a bit', act: () => { close(); api.doze(); } },
    { id: 'access', label: 'Access', icon: 'shield', say: 'take full control', on: () => true, panel: true },
    { id: 'voice', label: 'Voice', icon: 'wave', say: 'just write, don’t speak', on: () => !textOnly(), panel: true },
    { id: 'model', label: 'Model', icon: 'brain', say: 'think harder', panel: true },
    { id: 'record', label: 'Record', icon: 'rec', say: 'record me', act: () => { close(); api.record(); } },
    { id: 'theme', label: 'Day / Night', icon: 'sun', say: 'make it dark', act: () => api.theme() },
    { id: 'did', label: 'What it did', icon: 'list', say: 'what did you just do?', panel: true },
    { id: 'stopwork', label: 'Stop the work', icon: 'stop', say: 'stop that', act: () => { api.stopWork(); } },
    { id: 'restart', label: 'Restart', icon: 'restart', say: 'restart yourself', panel: true },
    { id: 'help', label: 'What can you do?', icon: 'help', say: 'what can you do?', panel: true },
    { id: 'off', label: 'Turn off', icon: 'power', say: 'turn yourself off', danger: true, act: () => api.turnOff() },
  ];
  const buttons = CONTROLS.map((c, i) => {
    const a = (i / CONTROLS.length) * Math.PI * 2 - Math.PI / 2;
    const b = el('button', `rb${c.danger ? ' danger' : ''}`);
    b.type = 'button';
    b.dataset.id = c.id;
    const disc = el('span', 'disc');
    disc.append(icon(c.icon));
    if (c.on) disc.append(el('span', 'state'));
    b.append(disc, el('span', 'lbl', c.label));
    b.style.left = `${50 + Math.cos(a) * 40}%`;
    b.style.top = `${50 + Math.sin(a) * 40}%`;
    b.style.setProperty('--d', `${i * 0.025}s`);
    b.addEventListener('pointerenter', () => point(c, a));
    b.addEventListener('focus', () => point(c, a));
    b.addEventListener('click', () => {
      $$('.rb', ring).forEach((x) => x.classList.toggle('sel', x === b));
      if (c.panel) openPanel(c.id); else panel.hidden = true;
      if (c.act) c.act();
      paint();
    });
    ring.append(b);
    return [c, b];
  });
  function point(c, a) {
    sweep.style.setProperty('--a', `${(a * 180) / Math.PI + 90}deg`);
    sweep.classList.add('on');
    sayWhat.textContent = `“${c.say}”`;
  }
  function paint() {
    buttons.forEach(([c, b]) => { if (c.on) b.classList.toggle('on', !!c.on()); });
    const ph = api.orb.phase;
    title.textContent = { idle: 'Listening', hearing: 'Hearing you', thinking: 'Thinking', acting: 'Working',
      speaking: 'Talking', sleep: 'Resting', offline: 'Reconnecting' }[ph] || 'Here';
    const lv = LEVELS.find((l) => l.id === (settings.access || 'copilot'));
    const model = (settings.models || []).find((m) => m.id === settings.model);
    small.textContent = [lv ? lv.name : 'Co-pilot', model ? model.name : '', textOnly() ? 'text only' : (settings.voice === 'local' ? 'local voice' : settings.voice === 'gemini' ? 'Gemini voice' : '')]
      .filter(Boolean).join(' · ');
  }
  let last = performance.now();
  function frame(now) {
    const dt = (now - last) / 1000;
    last = now;
    ringOrb.phase = api.orb.phase === 'sleep' ? 'idle' : api.orb.phase;
    ringOrb.bands = api.orb.bands;
    ringOrb.level = api.orb.level;
    ringOrb.resize();
    ringOrb.frame(dt);
    if (Math.round(now / 400) !== Math.round((now - dt * 1000) / 400)) paint();
    raf = requestAnimationFrame(frame);
  }
  function open() {
    load().then(paint);
    box.hidden = false;
    requestAnimationFrame(() => ring.classList.remove('closed'));
    panel.hidden = true;
    sayWhat.textContent = '“rest for a bit”';
    paint();
    last = performance.now();
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(frame);
    const first = $('.rb', ring);
    if (first) first.focus({ preventScroll: true });
  }
  function close() {
    ring.classList.add('closed');
    sweep.classList.remove('on');
    setTimeout(() => { if (ring.classList.contains('closed')) { box.hidden = true; cancelAnimationFrame(raf); } }, 380);
  }
  $('[data-ring-close]', box).addEventListener('click', close);
  $('#ring-orb').addEventListener('click', close);
  box.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } });

  // ---------------------------------------------------------------- asked once, on the first hello
  function askAccess() {
    return new Promise((done) => {
      const card = $('.cello-hello');
      const holder = $('[data-hello-cards]', card);
      holder.replaceChildren(accessCards('copilot', async (id) => {
        try { await save({ access: id }); } catch { /* kept as co-pilot */ }
        card.hidden = true;
        done(id);
      }), promises());
      card.hidden = false;
      $('[data-hello-later]', card).onclick = async () => {
        try { await save({ access: 'copilot' }); } catch { /* co-pilot stays the default */ }
        card.hidden = true;
        done('copilot');
      };
    });
  }

  load();
  return { open, close, toggle: () => (box.hidden ? open() : close()), isOpen: () => !box.hidden, askAccess, load,
    access: () => settings.access || '' };
}
