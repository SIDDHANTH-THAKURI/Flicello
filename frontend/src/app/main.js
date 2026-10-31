// The app bundle: shared behaviour for signed-in screens, sign-in and the legal pages. Everything is progressive:
// pages work without it, this makes them feel alive.
import './bundle.css';
import { confirms, openTargets, otherChoices, showWhen, themeToggle } from './asks.js';
import { studio } from './studio.js';
import { controls } from './controls.js';
import { stylePage } from './stylepage.js';
import { newFilmPage } from './newfilm.js';
import { recorder, takeIntoFilm } from './recorder.js';
import { filmPage } from './filmpage.js';
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
  controls();
  stylePage();
  newFilmPage();
  filmPage();
  recorder();
  const film = document.body.dataset.project; // a take recorded on a film's page goes into that film
  if (film && document.body.dataset.stage === 'setup') takeIntoFilm(film, document.body.dataset.kind);
}
main();
