// One look, everywhere: every page's controls brought to the shared style. Progressive: each control works as plain
// HTML first (a <select>, a checkbox, a form); this makes it look and answer the same on every page.
// - dropdowns: a <select class="input"> or inside .field becomes a menu you can open, type to, and arrow through;
//   the real select stays in the form and keeps the value
// - tab bars: [data-tabs] with [role=tab][data-t] and .tabpanel[data-p]; the open tab is kept in the address
// - switches: button.sw-btn[role=switch] (with data-input naming a hidden input it keeps in step)
// - sliders: --v follows the value, for the filled track
// - file boxes: .filedrop and .drop light up while a file is dragged over
// - checked as you type: input[data-check] says one line under the field (see CHECKS)

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

// ---------------------------------------------------------------- dropdowns
function label(opt) {
  const [main, ...rest] = opt.textContent.split(' · ');
  return { main: main.trim(), sub: rest.join(' · ').trim() };
}

function enhanceSelect(sel) {
  if (sel.dataset.ui || sel.multiple || sel.size > 1 || sel.closest('[data-native]')) return;
  sel.dataset.ui = '1';
  const box = document.createElement('div');
  box.className = 'ui-select';
  box.tabIndex = sel.disabled ? -1 : 0;
  box.setAttribute('role', 'combobox');
  box.setAttribute('aria-haspopup', 'listbox');
  box.setAttribute('aria-expanded', 'false');
  if (sel.getAttribute('aria-label')) box.setAttribute('aria-label', sel.getAttribute('aria-label'));
  const val = document.createElement('span');
  val.className = 'val';
  const car = document.createElement('span');
  car.className = 'car';
  car.textContent = '▾';
  const menu = document.createElement('div');
  menu.className = 'menu';
  menu.setAttribute('role', 'listbox');
  box.append(val, car, menu);
  sel.classList.add('ui-hidden');
  sel.tabIndex = -1;
  sel.after(box);
  if (sel.style.width) box.style.width = sel.style.width;
  if (sel.style.maxWidth) box.style.maxWidth = sel.style.maxWidth;

  let hot = -1;
  const opts = () => [...sel.options].filter((o) => !o.hidden);
  function draw() {
    menu.textContent = '';
    for (const o of opts()) {
      const d = document.createElement('div');
      d.setAttribute('role', 'option');
      d.dataset.v = o.value;
      const { main, sub } = label(o);
      d.textContent = main;
      if (sub) { const s = document.createElement('small'); s.textContent = sub; d.append(s); }
      d.setAttribute('aria-selected', o.selected ? 'true' : 'false');
      if (o.disabled) d.setAttribute('aria-disabled', 'true');
      menu.append(d);
    }
    const cur = sel.options[sel.selectedIndex];
    val.textContent = cur ? label(cur).main : '';
  }
  function open(on) {
    if (sel.disabled) return;
    box.classList.toggle('open', on);
    box.setAttribute('aria-expanded', on ? 'true' : 'false');
    if (on) {
      draw();
      const r = box.getBoundingClientRect();
      box.classList.toggle('up', window.innerHeight - r.bottom < 300 && r.top > 300);
      hot = opts().findIndex((o) => o.selected);
      mark();
    }
  }
  function mark() {
    $$('[role="option"]', menu).forEach((d, i) => d.classList.toggle('hot', i === hot));
    const h = menu.children[hot];
    if (h) h.scrollIntoView({ block: 'nearest' });
  }
  function pick(v) {
    if (sel.value !== v) {
      sel.value = v;
      sel.dispatchEvent(new Event('input', { bubbles: true }));
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    }
    draw();
    open(false);
    box.focus();
  }
  box.addEventListener('click', (e) => {
    const o = e.target.closest('[role="option"]');
    if (o) { if (o.getAttribute('aria-disabled') !== 'true') pick(o.dataset.v); return; }
    open(!box.classList.contains('open'));
  });
  let typed = '';
  let typedAt = 0;
  box.addEventListener('keydown', (e) => {
    const list = opts();
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!box.classList.contains('open')) { open(true); return; }
      hot = Math.max(0, Math.min(list.length - 1, hot + (e.key === 'ArrowDown' ? 1 : -1)));
      mark();
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      if (box.classList.contains('open') && list[hot]) pick(list[hot].value); else open(true);
    } else if (e.key === 'Escape' || e.key === 'Tab') {
      open(false);
    } else if (e.key.length === 1) { // type to jump
      typed = performance.now() - typedAt > 700 ? e.key.toLowerCase() : typed + e.key.toLowerCase();
      typedAt = performance.now();
      const i = list.findIndex((o) => o.textContent.trim().toLowerCase().startsWith(typed));
      if (i >= 0) { if (!box.classList.contains('open')) open(true); hot = i; mark(); }
    }
  });
  document.addEventListener('pointerdown', (e) => { if (!box.contains(e.target)) open(false); });
  sel.addEventListener('change', draw);
  new MutationObserver(draw).observe(sel, { childList: true, subtree: true, attributes: true });
  draw();
}

export function selects(root = document) {
  $$('select.input, .field select, select.ui', root).forEach(enhanceSelect);
}

// ---------------------------------------------------------------- tab bars
export function tabs(root = document) {
  $$('[data-tabs]', root).forEach((bar) => {
    if (bar.dataset.ready) return;
    bar.dataset.ready = '1';
    const name = bar.dataset.tabs;
    const scope = bar.closest('[data-tab-scope]') || document;
    const panels = () => $$(`.tabpanel[data-of="${name}"], .tabpanel[data-p]`, scope).filter((p) => !p.dataset.of || p.dataset.of === name);
    function show(t, keep = true) {
      const tabsIn = $$('[role="tab"]', bar);
      if (!tabsIn.some((b) => b.dataset.t === t)) return;
      tabsIn.forEach((b) => {
        const on = b.dataset.t === t;
        b.setAttribute('aria-selected', on ? 'true' : 'false');
        b.tabIndex = on ? 0 : -1;
      });
      panels().forEach((p) => { p.hidden = p.dataset.p !== t; });
      if (keep && bar.dataset.keep !== 'no') {
        const u = new URL(window.location.href);
        u.searchParams.set(name, t);
        history.replaceState(history.state, '', u);
      }
      bar.dispatchEvent(new CustomEvent('tab', { detail: t, bubbles: true }));
    }
    bar.addEventListener('click', (e) => {
      const b = e.target.closest('[role="tab"]');
      if (b && b.dataset.t) { e.preventDefault(); show(b.dataset.t); }
    });
    bar.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      const list = $$('[role="tab"]', bar);
      const i = list.indexOf(document.activeElement);
      if (i < 0) return;
      const next = list[(i + (e.key === 'ArrowRight' ? 1 : list.length - 1)) % list.length];
      next.focus();
      show(next.dataset.t);
    });
    bar.show = show;
    // the address's tab wins, then the one the page marked, then the first that needs them, then the first
    const asked = new URLSearchParams(window.location.search).get(name);
    const marked = $('[role="tab"][aria-selected="true"]', bar);
    const needs = $('[role="tab"][data-needs]', bar);
    const first = $('[role="tab"]', bar);
    const pick = (asked && $(`[role="tab"][data-t="${CSS.escape(asked)}"]`, bar)) || marked || needs || first;
    if (pick) show(pick.dataset.t, false);
  });
}

// ---------------------------------------------------------------- switches, sliders, file boxes
export function switches(root = document) {
  $$('button.sw-btn[role="switch"]', root).forEach((b) => {
    if (b.dataset.ready) return;
    b.dataset.ready = '1';
    const input = b.dataset.input ? document.getElementById(b.dataset.input) : null;
    b.addEventListener('click', () => {
      const on = b.getAttribute('aria-checked') !== 'true';
      b.setAttribute('aria-checked', on ? 'true' : 'false');
      if (input) { input.value = on ? (b.dataset.on || '1') : (b.dataset.off || '0'); input.dispatchEvent(new Event('change', { bubbles: true })); }
      b.dispatchEvent(new CustomEvent('flip', { detail: on, bubbles: true }));
      if (b.dataset.submit !== undefined && b.form) b.form.requestSubmit();
    });
  });
}

export function sliders(root = document) {
  $$('input[type="range"].range, input[type="range"].ui, input[type="range"].slide', root).forEach((r) => {
    const set = () => {
      const min = +r.min || 0;
      const max = +r.max || 100;
      r.style.setProperty('--v', `${((+r.value - min) / (max - min || 1)) * 100}%`);
    };
    set();
    r.addEventListener('input', set);
  });
}

export function fileboxes(root = document) {
  $$('.filedrop, label.drop', root).forEach((z) => {
    if (z.dataset.dropReady) return;
    z.dataset.dropReady = '1';
    ['dragenter', 'dragover'].forEach((t) => z.addEventListener(t, () => z.classList.add('over')));
    ['dragleave', 'drop'].forEach((t) => z.addEventListener(t, () => z.classList.remove('over')));
    const input = $('input[type="file"]', z);
    const name = $('[data-name]', z);
    const thumb = $('.thumb', z);
    if (input && (name || thumb)) {
      input.addEventListener('change', () => {
        const f = input.files && input.files[0];
        if (!f) return;
        if (name) name.textContent = f.name;
        if (thumb && f.type.startsWith('image/')) {
          const img = document.createElement('img');
          img.alt = '';
          img.src = URL.createObjectURL(f);
          thumb.textContent = '';
          thumb.append(img);
        }
      });
    }
  });
}

// ---------------------------------------------------------------- checked as you type
const CHECKS = {
  // a key typed in two halves with a colon (Higgsfield shows it that way): both halves, nothing else
  pair: (v) => (!v ? ['', 'The whole key, both halves, with the colon.']
    : /^\S{8,}:\S{8,}$/.test(v) ? ['good', 'Looks right: both halves are there.']
      : v.includes(':') ? ['err', 'One half looks short: paste the whole key again.'] : ['err', 'Missing the colon: it is id:secret, both halves.']),
  key: (v) => (!v ? ['', ''] : /\s/.test(v.trim()) ? ['err', 'There is a space in it: paste just the key.']
    : v.trim().length < 20 ? ['err', 'That looks too short for a key.'] : ['good', 'Looks like a key.']),
  email: (v) => (!v ? ['', ''] : /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v) ? ['good', ''] : ['err', 'That doesn’t look like an email address.']),
  hex: (v) => (!v ? ['', ''] : /^#?[0-9a-f]{6}$/i.test(v) ? ['good', ''] : ['err', 'A colour like #ff5a36.']),
};

export function checked(root = document) {
  $$('input[data-check]', root).forEach((i) => {
    if (i.dataset.ready) return;
    i.dataset.ready = '1';
    const rule = CHECKS[i.dataset.check];
    if (!rule) return;
    let hint = i.parentElement.querySelector('.hint');
    if (!hint) { hint = document.createElement('span'); hint.className = 'hint'; i.after(hint); }
    const base = hint.textContent;
    const run = () => {
      const [cls, say] = rule(i.value.trim());
      i.classList.toggle('err', cls === 'err');
      i.classList.toggle('good', cls === 'good');
      hint.className = `hint ${cls}`.trim();
      hint.textContent = say || base;
    };
    i.addEventListener('input', run);
    if (i.value) run();
  });
}

export function controls(root = document) {
  selects(root);
  tabs(root);
  switches(root);
  sliders(root);
  fileboxes(root);
  checked(root);
}
