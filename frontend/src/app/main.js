// The app bundle: shared behaviour for signed-in screens, sign-in and the legal pages. Everything is progressive:
// pages work without it, this makes them feel alive.
import '../styles/tokens.css';
import '../styles/components.css';
import '../styles/app.css';
import '../styles/studio.css';
import { footageWall } from './wall.js';
import { passkeySignIn, passkeyAdd, passkeysSupported } from './passkeys.js';
import { studio } from './studio.js';
import { toast, dismiss } from './ui.js';

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
export const csrf = () => (document.querySelector('meta[name="csrf"]') || {}).content || '';

// ---- flash messages arrive as a toast and leave on their own
function toasts() {
  $$('.toast').forEach(dismiss);
}

// ---- the six code boxes: type, paste or autofill; it submits itself on the sixth digit
function codeBoxes() {
  const box = $('[data-code]');
  if (!box) return;
  const input = $('input', box);
  const cells = $$('span', box);
  const draw = () => {
    const v = input.value.replace(/\D/g, '').slice(0, 6);
    if (input.value !== v) input.value = v;
    cells.forEach((c, i) => {
      c.textContent = v[i] || '';
      c.classList.toggle('filled', !!v[i]);
      c.classList.toggle('on', document.activeElement === input && i === Math.min(v.length, 5));
    });
    if (v.length === 6 && !box.dataset.sent) {
      box.dataset.sent = '1';
      setTimeout(() => input.form.requestSubmit(), 120);
    }
    if (v.length < 6) delete box.dataset.sent;
  };
  ['input', 'focus', 'blur', 'keyup'].forEach((e) => input.addEventListener(e, draw));
  if (box.dataset.error) { box.classList.add('shake'); input.value = ''; }
  input.focus();
  draw();
}

// ---- passkeys
function passkeys() {
  const signIn = $('[data-passkey-signin]');
  if (signIn) {
    if (!passkeysSupported()) signIn.hidden = true;
    signIn.addEventListener('click', async () => {
      signIn.disabled = true;
      try {
        const next = await passkeySignIn(signIn.dataset.next || '/');
        window.location.assign(next);
      } catch (err) {
        if (err && err.name !== 'NotAllowedError' && err.name !== 'AbortError') toast(err.message || 'That didn’t work. Please try again.');
      } finally { signIn.disabled = false; }
    });
  }
  const add = $('[data-passkey-add]');
  if (add) {
    if (!passkeysSupported()) { add.disabled = true; add.textContent = 'Passkeys aren’t supported in this browser'; }
    add.addEventListener('click', async () => {
      add.disabled = true;
      try {
        await passkeyAdd(csrf(), deviceLabel());
        window.location.assign('/account?msg=' + encodeURIComponent('Passkey added. Next time, sign in with one tap.'));
      } catch (err) {
        if (err && err.name !== 'NotAllowedError' && err.name !== 'AbortError') toast(err.message || 'That didn’t work. Please try again.');
        add.disabled = false;
      }
    });
  }
}
function deviceLabel() {
  const ua = navigator.userAgent;
  const os = /iPhone|iPad/.test(ua) ? 'iPhone' : /Android/.test(ua) ? 'Android' : /Mac/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows' : 'This device';
  const br = /Edg\//.test(ua) ? 'Edge' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : /Firefox\//.test(ua) ? 'Firefox' : '';
  return br ? `${os} · ${br}` : os;
}

// ---- legal pages: the contents list follows your reading
function legalToc() {
  const links = $$('.doc-toc a[href^="#"]');
  if (!links.length) return;
  const map = new Map(links.map((a) => [decodeURIComponent(a.getAttribute('href').slice(1)), a]));
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      links.forEach((a) => a.classList.remove('on'));
      const a = map.get(e.target.id);
      if (a) a.classList.add('on');
    });
  }, { rootMargin: '-20% 0px -70% 0px' });
  $$('.prose h2[id]').forEach((h) => io.observe(h));
}

// ---- dangerous buttons ask first (the server still checks)
function confirms() {
  $$('form[data-confirm]').forEach((f) => f.addEventListener('submit', (e) => {
    if (!window.confirm(f.dataset.confirm)) e.preventDefault();
  }));
}

function main() {
  toasts();
  codeBoxes();
  passkeys();
  legalToc();
  confirms();
  studio();
  const wall = $('.auth-stage canvas');
  if (wall) footageWall(wall, $('#auth-video'), reduced);
}
main();
