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
  if (box.dataset.thinking === '1') waitForReply(pid);
}

export function editor() {
  const pid = document.body.dataset.project;
  if (pid) wire(pid);
}
