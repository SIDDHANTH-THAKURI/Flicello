// Cello's type box, part of Cello: a glass panel joined to the orb with a light running round its edge in Cello's
// state colour (amber listening, violet thinking, rose talking back). It shows your words as it hears them, then what
// it is doing, step by step, then the answer; suggestions come from where the film is, never generic. Type in it any
// time (Enter sends, Shift+Enter a new line), or talk.

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

// what to suggest, from where the film is (the page in the frame says its stage and kind)
function suggestions(where) {
  const { stage, kind, page } = where;
  if (!page.startsWith('/p/')) {
    if (page === '/new') return ['Film me now', 'Start from my clips', 'A cartoon about my dog'];
    if (page === '/style') return ['Drop the car rule', 'Make my brand colour orange', 'What do you know about my taste?'];
    if (page === '/connect') return ['What needs fixing?', 'Fix it for me', 'Is my GPU fast enough?'];
    return ['Start a new film', 'Open my last film', 'What can you do?'];
  }
  if (['clips', 'watching', 'pitching', 'sampling', 'rendering', 'revising', 'reading'].includes(stage)) {
    return ['How long is left?', 'What is it doing right now?', 'Stop it'];
  }
  return {
    setup: kind === 'voice' ? ['Record my voice now', 'What should I say?', 'Make it'] : ['Watch my clips', 'Film me now', 'Build it for me'],
    understood: ['Looks right, carry on', 'You got something wrong', 'Make it shorter'],
    choose: ['Tell me about the first one', 'Something else', 'Build this one'],
    review: ['Make the whole film', 'Change the sample', 'Slower at the start'],
    done: ['What’s left before I can post?', 'Make it calmer', 'Export a square cut'],
    confirm: ['Yes, make it', 'Not quite', 'Show me the draft'],
    failed: ['What went wrong?', 'Try it again', 'Fix it'],
  }[stage] || ['What now?', 'Make it better', 'What can you do?'];
}

export function setupComposer(api) {
  const root = $('.cello-lc');
  const status = $('[data-lc-status]', root);
  const body = $('[data-lc-body]', root);
  const chips = $('[data-lc-chips]', root);
  const send = $('[data-lc-send]', root);
  const mic = $('[data-lc-mic]', root);
  const S = { mode: 'idle', heard: '', reply: '', steps: [], typing: false, shownAt: 0, ta: null, pinned: false };

  function state(s) { // listen | heard | think | done | type
    root.classList.remove('listen', 'heard', 'think', 'done', 'type');
    root.classList.add(s);
    $$('[data-s]', status).forEach((n) => {
      const order = ['listen', 'heard', 'think', 'done'];
      const i = order.indexOf(n.dataset.s);
      const at = order.indexOf(s === 'type' ? 'listen' : s);
      n.classList.toggle('on', i === at);
      n.classList.toggle('ok', i < at);
    });
  }
  function show() {
    root.hidden = false;
    S.shownAt = performance.now();
  }
  function drawChips() {
    chips.replaceChildren(...suggestions(api.where()).map((t) => {
      const c = el('button', 'chip', t);
      c.type = 'button';
      c.addEventListener('click', () => submit(t));
      return c;
    }));
  }
  function submit(text) {
    const t = String(text || '').trim();
    if (!t) return;
    S.typing = false;
    S.heard = t;
    S.reply = '';
    S.steps = [];
    body.replaceChildren(el('div', 'heard', t));
    state('heard');
    show();
    api.say(t);
  }
  function type() { // their own words, typed
    S.typing = true;
    S.pinned = true;
    const ta = el('textarea');
    ta.rows = 2;
    ta.placeholder = 'Type to Cello…';
    ta.setAttribute('aria-label', 'Type to Cello');
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(ta.value); }
      if (e.key === 'Escape') { S.typing = false; S.pinned = false; root.hidden = true; }
    });
    S.ta = ta;
    body.replaceChildren(ta);
    state('type');
    drawChips();
    show();
    setTimeout(() => ta.focus(), 30);
  }
  send.addEventListener('click', () => { if (S.ta && S.typing) submit(S.ta.value); else type(); });
  mic.addEventListener('click', () => api.mute());

  return {
    type,
    typing: () => S.typing && !root.hidden,
    heard(text) { // their words as it hears them
      if (S.typing) return;
      if (S.mode !== 'heard') { S.heard = ''; S.reply = ''; S.steps = []; }
      S.mode = 'heard';
      S.heard += text;
      body.replaceChildren(el('div', 'heard', S.heard.trim()));
      state('heard');
      show();
    },
    step(text) { // what it is doing, step by step
      if (S.typing) return;
      S.mode = 'think';
      if (S.steps[S.steps.length - 1] === text) return;
      S.steps.push(text);
      const list = el('div', 'steps');
      if (S.heard) list.append(el('div', 'heard soft', S.heard.trim()));
      S.steps.slice(-4).forEach((s, i, all) => {
        const row = el('div');
        row.append(el('span', i < all.length - 1 ? 'ok' : 'spin', i < all.length - 1 ? '✓' : ''), el('span', '', s));
        list.append(row);
      });
      body.replaceChildren(list);
      state('think');
      show();
    },
    reply(text) { // the answer, as it says it
      if (S.typing) return;
      if (S.mode !== 'reply') S.reply = '';
      S.mode = 'reply';
      S.reply += text;
      body.replaceChildren(el('div', 'heard', S.reply.trim()));
      state('done');
      show();
    },
    done() { // the turn ended: the answer stays a while, with what to say next
      if (S.typing) return;
      S.mode = 'idle';
      drawChips();
      state('done');
      S.shownAt = performance.now();
    },
    phase(ph, now) { // every frame: the panel follows Cello
      if (root.hidden) return;
      if (S.typing) return;
      if (ph === 'hearing' && S.mode === 'idle') { state('listen'); body.replaceChildren(el('div', 'heard soft', 'Listening…')); S.shownAt = now; }
      if (ph === 'thinking' && S.mode === 'heard') state('think');
      if (ph === 'sleep' && !S.pinned) root.hidden = true;
      if (!S.pinned && ['idle', 'sleep'].includes(ph) && S.mode === 'idle' && now - S.shownAt > 9000) root.hidden = true;
    },
    close() { S.typing = false; S.pinned = false; root.hidden = true; },
  };
}
