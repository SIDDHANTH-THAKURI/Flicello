// Asking before anything starts work, costs something or deletes (in a card of our own, not the browser's box),
// the light/dark switch, and "Other" choices that open their own words box. The server still checks everything.

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

export function ask(text, yes, danger) {
  return new Promise((resolve) => {
    const veil = document.createElement('div');
    veil.className = 'ask-veil';
    const card = document.createElement('div');
    card.className = 'ask-card';
    card.setAttribute('role', 'alertdialog');
    card.setAttribute('aria-modal', 'true');
    const h = document.createElement('h3');
    h.textContent = danger ? 'Are you sure?' : 'Before it starts';
    const p = document.createElement('p');
    p.textContent = text;
    const row = document.createElement('div');
    row.className = 'ask-row';
    const no = document.createElement('button');
    no.type = 'button'; no.className = 'btn quiet'; no.textContent = 'Not yet';
    const go = document.createElement('button');
    go.type = 'button'; go.className = danger ? 'btn danger-go' : 'btn-sun';
    const label = document.createElement('span');
    label.textContent = yes || 'Yes, go ahead';
    go.appendChild(label);
    row.append(no, go);
    card.append(h, p, row);
    veil.appendChild(card);
    const last = document.activeElement;
    const key = (e) => { if (e.key === 'Escape') close(false); };
    function close(ok) {
      document.removeEventListener('keydown', key);
      veil.remove();
      if (last && last.focus) last.focus();
      resolve(ok);
    }
    veil.addEventListener('click', (e) => { if (e.target === veil) close(false); });
    no.addEventListener('click', () => close(false));
    go.addEventListener('click', () => close(true));
    document.addEventListener('keydown', key);
    document.body.appendChild(veil);
    go.focus();
  });
}

export function confirms() {
  document.addEventListener('submit', async (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement) || form.dataset.asked === '1') return;
    const btn = e.submitter || null;
    const holder = btn && btn.dataset && btn.dataset.confirm ? btn : form;
    if (!holder.dataset.confirm) return;
    // a button with its own action and no question of its own (e.g. 'Polish') doesn't take the form's question
    if (holder === form && btn && btn.getAttribute('formaction')) return;
    if (holder === form && form.dataset.confirmIf) { // only asks when that box has something in it
      const box = form.querySelector(`[name="${form.dataset.confirmIf}"]`);
      if (!box || !box.value.trim()) return;
    }
    e.preventDefault();
    e.stopImmediatePropagation();
    const ok = await ask(holder.dataset.confirm, holder.dataset.confirmYes, 'confirmDanger' in holder.dataset);
    if (!ok) return;
    form.dataset.asked = '1';
    if (btn) form.requestSubmit(btn); else form.requestSubmit();
    setTimeout(() => { delete form.dataset.asked; }, 2000);
  }, true);
}

export function themeToggle() {
  $$('[data-theme-toggle]').forEach((b) => b.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    document.cookie = `theme=${next}; path=/; max-age=31536000; SameSite=Lax`;
    const meta = $('meta[name="theme-color"]');
    if (meta) meta.content = next === 'dark' ? '#06060a' : '#fbf6ee';
    const scheme = $('meta[name="color-scheme"]');
    if (scheme) scheme.content = next;
    b.setAttribute('aria-label', `Switch to ${next === 'dark' ? 'light' : 'dark'} mode`);
  }));
}

export function otherChoices() {
  const sync = () => {
    $$('[data-other-for]').forEach((box) => {
      const picked = document.querySelector(`input[name="${box.dataset.otherFor}"]:checked`);
      const on = !!picked && picked.value === 'other';
      box.hidden = !on;
      box.required = on;
    });
    $$('[data-other-need]').forEach((box) => {
      const picked = document.querySelector(`input[name="${box.dataset.otherNeed}"]:checked`);
      box.required = !!picked && picked.value === 'other';
    });
  };
  document.addEventListener('change', (e) => {
    const t = e.target;
    if (!(t instanceof HTMLInputElement) || t.type !== 'radio') return;
    sync();
    if (t.value === 'other') {
      const box = document.querySelector(`[data-other-for="${t.name}"], [data-other-need="${t.name}"]`);
      if (box) box.focus();
    }
  });
  sync();
}

// a link to a closed section (#settings-edit, #report, ...) opens it and brings it into view
export function openTargets() {
  const open = (id) => {
    const el = id && document.getElementById(id);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (el.tagName !== 'DETAILS') return; // a plain anchor (a heading, the skip link): just go there
    el.open = true;
    const first = el.querySelector('input:not([type=hidden]), textarea, select');
    if (first) setTimeout(() => first.focus({ preventScroll: true }), 350);
  };
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a) return;
    const id = decodeURIComponent(a.getAttribute('href').slice(1));
    const el = id && document.getElementById(id);
    if (!el) return;
    e.preventDefault();
    history.replaceState(null, '', `#${id}`);
    open(id);
  });
  // a section opened before (its #name still in the address after saving) stays closed until asked for
  if (location.hash) {
    const el = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (el && el.tagName === 'DETAILS') history.replaceState(null, '', location.pathname + location.search);
  }
}

// a box shown only for one choice: data-when="length=custom" (any form on the page)
export function showWhen() {
  const sync = () => $$('[data-when]').forEach((el) => {
    const [name, value] = el.dataset.when.split('=');
    const scope = el.closest('form') || document;
    const picked = scope.querySelector(`input[name="${name}"]:checked`);
    const on = !!picked && picked.value === value;
    el.hidden = !on;
    $$('input', el).forEach((i) => { i.required = on; });
  });
  document.addEventListener('change', (e) => {
    sync();
    const t = e.target;
    if (t instanceof HTMLInputElement && t.type === 'radio') {
      const box = (t.closest('form') || document).querySelector(`[data-when="${t.name}=${t.value}"] input`);
      if (box) box.focus();
    }
  });
  sync();
}
