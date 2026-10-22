// Your editor, on every step of a film: a message goes without leaving the page, "thinking" shows at once, and the
// reply is swapped in when it lands (the panel comes from the server: /p/{id}/editor). What they are typing survives
// the page reloading itself when a step finishes. Cards and the brief are plain forms: they work without this.

const $ = (s, root = document) => root.querySelector(s);
const keyFor = (pid) => `flicello-talk-${pid}`;

function bubble(cls, text) {
  const li = document.createElement('li');
  li.className = `m ${cls}`;
  if (text) {
    const p = document.createElement('p');
    p.textContent = text;
    li.append(p);
  } else {
    const dots = document.createElement('span');
    dots.className = 'dots';
    dots.setAttribute('role', 'status');
    dots.setAttribute('aria-label', 'Your editor is thinking');
    dots.append(document.createElement('i'), document.createElement('i'), document.createElement('i'));
    li.append(dots);
  }
  return li;
}

async function swap(pid) {
  const r = await fetch(`/p/${pid}/editor`, { credentials: 'same-origin', headers: { Accept: 'text/html' } });
  if (r.status === 401) { window.location.href = '/signin'; return false; }
  if (!r.ok) throw new Error(String(r.status));
  const tmp = document.createElement('div');
  tmp.innerHTML = await r.text();
  const fresh = $('#editor', tmp);
  const box = $('#editor');
  if (!fresh || !box) return false;
  box.replaceWith(fresh);
  wire(pid);
  return fresh.dataset.thinking === '1';
}

let polling = false; // one wait at a time: a swapped-in panel that is still thinking joins the wait already running

function waitForReply(pid) {
  if (polling || !$('#editor')) return;
  polling = true;
  let tries = 0;
  const tick = async () => {
    tries += 1;
    let still = true;
    try {
      still = await swap(pid);
    } catch { /* the next try */ }
    if (still && tries < 240) setTimeout(tick, tries < 30 ? 1500 : 4000);
    else polling = false;
  };
  setTimeout(tick, 1500);
}

function wire(pid) {
  const box = $('#editor');
  if (!box) return;
  const form = $('form[data-talk]', box);
  const area = form ? $('textarea', form) : null;
  const send = form ? $('button[type="submit"]', form) : null;
  if (area) {
    const kept = sessionStorage.getItem(keyFor(pid));
    if (kept && !area.value && !area.disabled) area.value = kept;
    area.addEventListener('input', () => sessionStorage.setItem(keyFor(pid), area.value));
    area.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); form.requestSubmit(); }
    });
  }
  if (form && area) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const text = area.value.trim();
      if (!text) return;
      const data = new FormData(form);
      const list = box.querySelectorAll('ol.thread');
      const thread = list[list.length - 1];
      const mine = bubble('m-you', text);
      const wait = bubble('m-editor m-thinking', '');
      thread?.append(mine, wait);
      area.value = '';
      area.disabled = true;
      if (send) send.disabled = true;
      try {
        const r = await fetch(form.action, { method: 'POST', body: data, credentials: 'same-origin',
          headers: { Accept: 'application/json' } });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || 'That message could not be sent. Try again.');
        }
        sessionStorage.removeItem(keyFor(pid));
        waitForReply(pid);
      } catch (err) {
        mine.remove();
        wait.remove();
        area.disabled = false;
        if (send) send.disabled = false;
        area.value = text;
        let note = $('.composer-note', box);
        if (!note) {
          note = document.createElement('p');
          note.className = 'help composer-note';
          note.setAttribute('role', 'alert');
          form.after(note);
        }
        note.textContent = err.message;
      }
    });
  }
  voice(box, form, area, pid);
  if (box.dataset.thinking === '1') waitForReply(pid);
}

// ---------------------------------------------------------------- talking to Cello out loud
// The browser hears them (its own speech recognition) and reads Cello's replies aloud (its own voices). Push the mic to
// say one thing; hands-free keeps listening after each reply, as a conversation.
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const HANDS_FREE = 'cello-hands-free';
let spokenLast = '';

function lastReply(box) {
  const replies = [...box.querySelectorAll('li.m-editor:not(.m-thinking)')];
  const li = replies[replies.length - 1];
  return li ? (li.querySelector('.m-text, p') || li).textContent.trim() : '';
}

function say(text, then) {
  if (!('speechSynthesis' in window) || !text) { if (then) then(); return; }
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text.slice(0, 600));
  u.rate = 1.05;
  u.onend = () => { if (then) then(); };
  u.onerror = () => { if (then) then(); };
  window.speechSynthesis.speak(u);
}

function listen(form, area, mic, auto) {
  if (!Recognition || !area || area.disabled) return;
  const rec = new Recognition();
  rec.lang = navigator.language || 'en-US';
  rec.interimResults = true;
  rec.continuous = false;
  let heard = '';
  mic.classList.add('on');
  mic.setAttribute('aria-pressed', 'true');
  rec.onresult = (e) => {
    heard = [...e.results].map((r) => r[0].transcript).join(' ');
    area.value = heard;
  };
  rec.onerror = () => { mic.classList.remove('on'); mic.setAttribute('aria-pressed', 'false'); };
  rec.onend = () => {
    mic.classList.remove('on');
    mic.setAttribute('aria-pressed', 'false');
    if (heard.trim()) form.requestSubmit(); // said it: it goes, as if typed
    else if (auto && localStorage.getItem(HANDS_FREE) === '1') setTimeout(() => listen(form, area, mic, true), 600);
  };
  rec.start();
}

function voice(box, form, area, pid) {
  if (!form || !area || !Recognition || form.querySelector('.mic')) return;
  const mic = document.createElement('button');
  mic.type = 'button';
  mic.className = 'mic';
  mic.title = 'Say it';
  mic.setAttribute('aria-label', 'Say it to Cello');
  mic.setAttribute('aria-pressed', 'false');
  form.insertBefore(mic, form.querySelector('button[type="submit"]'));
  mic.addEventListener('click', () => listen(form, area, mic, false));
  const free = document.createElement('label');
  free.className = 'hands-free';
  const tick = document.createElement('input');
  tick.type = 'checkbox';
  tick.checked = localStorage.getItem(HANDS_FREE) === '1';
  free.append(tick, document.createTextNode(' Hands-free'));
  form.after(free);
  tick.addEventListener('change', () => {
    localStorage.setItem(HANDS_FREE, tick.checked ? '1' : '0');
    if (tick.checked) listen(form, area, mic, true);
    else if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  });
  // hands-free: a new reply is read out, then it listens again
  const reply = lastReply(box);
  if (localStorage.getItem(HANDS_FREE) === '1' && box.dataset.thinking !== '1' && reply && reply !== spokenLast) {
    const first = !spokenLast;
    spokenLast = reply;
    if (!first) say(reply, () => listen(form, area, mic, true));
  } else if (!spokenLast) {
    spokenLast = reply;
  }
}

export function editor() {
  const pid = document.body.dataset.project;
  if (pid) wire(pid);
}
