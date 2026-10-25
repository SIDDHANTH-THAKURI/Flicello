// The app bundle: shared behaviour for signed-in screens, sign-in and the legal pages. Everything is progressive:
// pages work without it, this makes them feel alive.
import '../styles/tokens.css';
import '../styles/components.css';
import '../styles/app.css';
import '../styles/studio.css';
import '../styles/extras.css';
import '../styles/light.css';
import { confirms, openTargets, otherChoices, showWhen, themeToggle } from './asks.js';
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

function main() {
  toasts();
  confirms();
  themeToggle();
  otherChoices();
  openTargets();
  showWhen();
  studio();
}
main();
