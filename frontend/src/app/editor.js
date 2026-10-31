// Your editor, on every step of a film: a message goes without leaving the page, "thinking" shows at once, and the
// reply is swapped in when it lands (the panel comes from the server: /p/{id}/editor). What they are typing survives
// the page reloading itself when a step finishes. Cards and the brief are plain forms: they work without this.
// One conversation: while Cello is on (its shell around this page), what is typed here goes to Cello, and what it
// hears and says arrives here as it happens, in the same thread.

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

// ---------------------------------------------------------------- Cello's voice, in this same thread
const shell = { on: false, you: null, me: null };
function lastThread() {
  const list = document.querySelectorAll('#editor ol.thread');
  return list[list.length - 1] || null;
}
function voiceLine(who, text) {
  const thread = lastThread();
  if (!thread || !text) return;
  const key = who === 'you' ? 'you' : 'me';
  if (!shell[key] || !shell[key].isConnected) {
    if (key === 'you') shell.me = null;
    shell[key] = bubble(key === 'you' ? 'm-you m-voice' : 'm-editor m-voice', ' ');
    thread.append(shell[key]);
  }
  const p = shell[key].querySelector('p');
  p.textContent = `${p.textContent}${text}`.replace(/^\s+/, '');
  const convo = document.querySelector('[data-convo]');
  if (convo) convo.scrollTop = convo.scrollHeight;
}
function stateLine(text) {
  const st = document.querySelector('[data-dock-state]');
  if (st) st.textContent = text;
}
window.addEventListener('message', (e) => {
  if (e.origin !== location.origin || !e.data || e.data.cello !== 'shell') return;
  const m = e.data;
  if (typeof m.on === 'boolean') {
    shell.on = m.on;
    document.body.classList.toggle('cello-on', m.on);
    stateLine(m.on ? 'listening · say anything, or type' : 'your editor · type, or talk to it');
  }
  if (m.line) voiceLine(m.line, m.text);
  if (m.doing) stateLine(`${m.doing}…`);
  if (m.turn === 'done') { shell.you = null; shell.me = null; stateLine('listening · say anything, or type'); }
});

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
      // Enter sends, Shift+Enter is a new line (and nothing is sent mid-composition in an input method)
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
    });
  }
  if (form && area) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const text = area.value.trim();
      if (!text) return;
      if (shell.on && window.top !== window) { // Cello is on: it hears this, and its answer comes back into this thread
        shell.you = null;
        voiceLine('you', text);
        shell.you = null;
        area.value = '';
        sessionStorage.removeItem(keyFor(pid));
        window.top.postMessage({ cello: 'say', text }, location.origin);
        return;
      }
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
  if (box.dataset.thinking === '1') waitForReply(pid);
  const convo = box.querySelector('[data-convo]');
  if (convo) convo.scrollTop = convo.scrollHeight;
  if (area) { // the box grows with what is typed, up to a few lines
    const grow = () => { area.style.height = 'auto'; area.style.height = `${Math.min(140, area.scrollHeight)}px`; };
    area.addEventListener('input', grow);
    grow();
  }
}

export function editor() {
  const pid = document.body.dataset.project;
  if (pid) wire(pid);
}
