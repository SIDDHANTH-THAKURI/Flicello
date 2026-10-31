// My style: the brand seen as it is set (a live end card follows the name, type, colour and logo), and rules switched
// off, removed to the bin or brought back in place.
import { toast } from './ui.js';

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const csrf = () => (document.querySelector('meta[name="csrf"]') || {}).content || '';

function brand() {
  const form = $('[data-brand-form]');
  if (!form) return;
  const card = $('[data-endcard]');
  const hidden = $('[data-brand-colour]', form);
  const hex = $('[data-brand-hex]', form);
  const pick = $('[data-brand-pick]', form);
  const name = $('[data-brand-name]', form);
  const font = $('[data-brand-font]', form);
  const logo = $('[data-brand-logo]', form);
  const ecName = $('[data-ec-name]', card);
  const ecLogo = $('[data-ec-logo]', card);
  const setColour = (c) => {
    hidden.value = c;
    hex.textContent = c;
    pick.value = c;
    card.style.setProperty('--brand', c);
    $$('.swatches .sw[data-c]', form).forEach((s) => {
      const on = s.dataset.c.toLowerCase() === c.toLowerCase();
      s.classList.toggle('sel', on);
      s.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  };
  $$('.swatches .sw[data-c]', form).forEach((s) => {
    s.style.background = s.dataset.c;
    s.addEventListener('click', () => setColour(s.dataset.c));
  });
  pick.addEventListener('input', () => setColour(pick.value));
  setColour(card.dataset.colour || hidden.value);
  const initial = () => (name.value.trim() || 'F').slice(0, 1).toUpperCase();
  name.addEventListener('input', () => {
    ecName.textContent = name.value.trim() || 'Your name';
    if (!ecLogo.querySelector('img')) ecLogo.textContent = initial();
  });
  font.addEventListener('change', () => {
    card.classList.remove('font-display', 'font-serif', 'font-mono');
    card.classList.add(`font-${font.value}`);
  });
  logo.addEventListener('change', () => {
    const f = logo.files && logo.files[0];
    if (!f || !f.type.startsWith('image/')) return;
    const img = document.createElement('img');
    img.alt = '';
    img.src = URL.createObjectURL(f);
    ecLogo.textContent = '';
    ecLogo.append(img);
  });
}

function answersTab() {
  const next = $('[data-answers-next]');
  if (!next) return;
  document.addEventListener('tab', (e) => {
    if (e.detail === 'cut' || e.detail === 'music') next.value = `/style?tab=${e.detail}`;
  });
}

async function rule(id, op) {
  const r = await fetch('/api/style/rule', {
    method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF': csrf() },
    body: JSON.stringify({ rule: id, op }),
  });
  if (!r.ok) throw new Error(`Error ${r.status}`);
  return r.json();
}

function rules() {
  const list = $('[data-rules]');
  if (!list) return;
  const count = $('[data-rules-n]');
  const bin = $('[data-rule-bin]');
  const binN = $('[data-bin-n]');
  const tally = (st) => {
    if (count) count.textContent = st.on;
    if (binN) binN.textContent = st.bin;
    if (bin) bin.hidden = !st.bin;
  };
  list.addEventListener('flip', async (e) => {
    const row = e.target.closest('[data-rule]');
    if (!row) return;
    row.classList.toggle('off', !e.detail);
    try { tally(await rule(row.dataset.rule, e.detail ? 'on' : 'off')); toast(e.detail ? 'Followed again.' : 'Switched off: kept, not followed.'); }
    catch (err) { toast(err.message); }
  });
  list.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-rule-drop]');
    if (!b) return;
    const row = b.closest('[data-rule]');
    const text = row.querySelector('.rule-text').textContent;
    try {
      tally(await rule(row.dataset.rule, 'drop'));
      row.remove();
      const back = document.createElement('div');
      back.className = 'trow off';
      back.dataset.binned = row.dataset.rule;
      const t = document.createElement('span');
      t.textContent = text;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn sm';
      btn.dataset.ruleRestore = '';
      btn.textContent = 'Bring it back';
      back.append(t, btn);
      bin.querySelector('summary').after(back);
      toast('Removed: it waits in the bin for 30 days.');
    } catch (err) { toast(err.message); }
  });
  if (bin) bin.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-rule-restore]');
    if (!b) return;
    const row = b.closest('[data-binned]');
    try {
      await rule(row.dataset.binned, 'restore');
      window.location.assign('/style?tab=rules&msg=' + encodeURIComponent("It's back."));
    } catch (err) { toast(err.message); }
  });
}

export function stylePage() {
  brand();
  answersTab();
  rules();
}
