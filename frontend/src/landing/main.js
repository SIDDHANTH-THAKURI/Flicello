// Landing page bootstrap: smooth scroll, the WebGL stage (when the device can), the story, and the widgets.
import '../styles/tokens.css';
import '../styles/components.css';
import '../styles/landing.css';
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import Lenis from 'lenis';
import { createBeat } from './audio.js';
import { createStory } from './story.js';
import { compareWidget, bentoWidget, typer, radar } from './widgets.js';

gsap.registerPlugin(ScrollTrigger);

const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const body = document.body;
const video = document.getElementById('mosaic');

function hasWebGL2() {
  try {
    const c = document.createElement('canvas');
    return !!c.getContext('webgl2');
  } catch { return false; }
}

// split a headline's lines into characters for the entrance. Words stay whole (no breaks inside a word), and
// an <em> moves as one piece: its gradient is clipped to its own text, which per-letter boxes would break.
function split(el) {
  const chars = [];
  el.querySelectorAll('.line').forEach((line) => {
    [...line.childNodes].forEach((child) => {
      if (child.nodeType === 3) {
        const frag = document.createDocumentFragment();
        child.textContent.split(/(\s+)/).forEach((part) => {
          if (!part) return;
          if (/^\s+$/.test(part)) { frag.appendChild(document.createTextNode(part)); return; }
          const word = document.createElement('span');
          word.className = 'split-word';
          for (const ch of part) {
            const c = document.createElement('span');
            c.className = 'split-char';
            c.textContent = ch;
            word.appendChild(c);
            chars.push(c);
          }
          frag.appendChild(word);
        });
        line.replaceChild(frag, child);
      } else if (child.nodeType === 1) {
        const wrap = document.createElement('span');
        wrap.className = 'split-word split-char';
        line.replaceChild(wrap, child);
        wrap.appendChild(child);
        chars.push(wrap);
      }
    });
  });
  return chars;
}

function startVideo() {
  const go = () => video.play().catch(() => {});
  go();
  const kick = () => { go(); window.removeEventListener('pointerdown', kick); window.removeEventListener('scroll', kick); };
  window.addEventListener('pointerdown', kick, { passive: true });
  window.addEventListener('scroll', kick, { passive: true });
}

async function main() {
  startVideo();

  // smooth scroll, driven by GSAP's ticker (one clock for everything)
  let lenis = null;
  if (!reduced) {
    lenis = new Lenis({ lerp: 0.085, wheelMultiplier: 0.9, smoothWheel: true });
    lenis.on('scroll', ScrollTrigger.update);
    gsap.ticker.add((t) => lenis.raf(t * 1000));
    gsap.ticker.lagSmoothing(0);
    document.querySelectorAll('a[href^="#"]').forEach((a) => a.addEventListener('click', (e) => {
      const target = document.querySelector(a.getAttribute('href'));
      if (target) { e.preventDefault(); lenis.scrollTo(target, { offset: 0, duration: 1.6 }); }
    }));
  }

  const beat = createBeat();
  const soundButtons = [...document.querySelectorAll('[data-sound]')];
  const setPressed = (on) => soundButtons.forEach((b) => {
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    const label = b.querySelector('.sound-label');
    if (label) label.textContent = on ? 'Sound on' : 'Sound off';
    const cta = b.querySelector('.sound-cta-label');
    if (cta) cta.textContent = on ? 'Sound on: every cut is a beat' : 'Turn the sound on and hear it cut';
  });
  soundButtons.forEach((b) => b.addEventListener('click', () => {
    const next = b.getAttribute('aria-pressed') !== 'true';
    setPressed(next);
    beat.setSound(next);
  }));

  // the stage (lazy: three.js only loads where it can run)
  let stage = null;
  const canvas = document.getElementById('gl');
  if (hasWebGL2()) {
    const { createStage } = await import('./gl/stage.js');
    try {
      stage = createStage(canvas, video);
      body.dataset.gl = 'on';
    } catch (err) {
      console.warn('[flicello] WebGL stage failed: ' + (err && (err.stack || err.message) || err));
      body.dataset.gl = 'off';
    }
  } else {
    body.dataset.gl = 'off';
  }

  const story = stage ? createStory({ stage, beat, reduced }) : null;
  window.addEventListener('pointermove', (e) => stage && stage.setPointer(e.clientX, e.clientY, true), { passive: true });
  document.addEventListener('pointerleave', () => stage && stage.setPointer(0, 0, false));

  const drawCompare = compareWidget(video, reduced);
  const drawBento = bentoWidget(video);
  typer(reduced);
  radar(reduced);

  // one frame of everything
  let frames = 0, ms = 0;
  const frame = () => {
    const t0 = performance.now();
    if (stage && story) stage.render(story.update());
    drawCompare();
    drawBento();
    ms += performance.now() - t0;
    frames++;
  };
  gsap.ticker.add(frame);

  // a way to check the page by hand (hidden tabs never run requestAnimationFrame)
  window.__flicello = {
    step(n = 1) { for (let i = 0; i < n; i++) frame(); },
    stats() { const r = { frames, msPerFrame: frames ? ms / frames : 0 }; frames = 0; ms = 0; return r; },
    get state() { return story && story.state; },
    gl: () => stage && stage.renderer,
  };

  // entrance
  const heroChars = split(document.querySelector('.hero-title'));
  const finaleChars = split(document.querySelector('.finale-title'));
  if (!reduced) {
    gsap.set(heroChars, { yPercent: 115, rotate: 7, opacity: 0 });
    gsap.set(finaleChars, { yPercent: 115, opacity: 0 });
    gsap.to(heroChars, { yPercent: 0, rotate: 0, opacity: 1, duration: 1.15, ease: 'expo.out', stagger: 0.022, delay: 0.15 });
    ScrollTrigger.create({
      trigger: '.finale', start: 'top 70%', once: true,
      onEnter: () => gsap.to(finaleChars, { yPercent: 0, opacity: 1, duration: 1.1, ease: 'expo.out', stagger: 0.02 }),
    });
    gsap.utils.toArray('.pillars article, .bento .b, .anything-head, .compare-copy, .learn-copy, .prompt').forEach((el) => {
      gsap.from(el, { y: 40, opacity: 0, duration: 1.1, ease: 'expo.out', scrollTrigger: { trigger: el, start: 'top 88%', once: true } });
    });
  }
  requestAnimationFrame(() => { body.dataset.state = 'ready'; });
}

main();
