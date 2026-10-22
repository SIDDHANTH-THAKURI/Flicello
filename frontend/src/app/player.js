// The instant preview: an edit played straight from light copies of the clips (studio/preview.py), so a change is
// seen in a second instead of after a render. Two video layers take turns: while one plays its shot, the other loads
// the next shot and waits on its first frame; at the cut they swap. A steady clock drives everything, and the music
// and the playing shot are pulled back to it whenever they drift. It's a preview: honest about the cut, not frame-exact.

const LOOKS = {
  natural: '', vivid: 'saturate(1.2) contrast(1.08)', bright: 'brightness(1.08) saturate(1.2) contrast(1.06)',
  warm: 'sepia(0.12) saturate(1.06)', cool: 'saturate(0.98) hue-rotate(-6deg)', 'teal-orange': 'saturate(1.1) contrast(1.05)',
  film: 'contrast(1.05) saturate(0.9)', faded: 'contrast(0.9) saturate(0.82) brightness(1.04)',
  punchy: 'contrast(1.1) saturate(1.22)', mono: 'grayscale(1) contrast(1.12)',
};
const DRIFT = 0.25; // seconds a playing shot may stray before it's pulled back
const MUSIC_DRIFT = 0.12;

const el = (tag, cls, parent) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (parent) parent.append(n);
  return n;
};
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const ease = (p) => (p < 0.5 ? 2 * p * p : 1 - (-2 * p + 2) ** 2 / 2);
const mmss = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
const dbToGain = (db) => clamp(10 ** ((db || 0) / 20), 0, 1);

// The motion graphics: the very blocks the render uses (vidosuper/motion/blocks), loaded once per page.
const BLOCKS = ['title', 'captions', 'lowerthird', 'transition', 'kinetic', 'callout', 'route', 'accent', 'logo'];
let motionReady = null;
function loadMotion() {
  if (motionReady) return motionReady;
  const script = (src) => new Promise((ok, fail) => {
    const s = el('script', '', document.head);
    s.onload = ok;
    s.onerror = fail;
    s.src = src;
  });
  const fonts = el('link', '', document.head);
  fonts.rel = 'stylesheet';
  fonts.href = '/motion/fonts.css';
  motionReady = script('/motion/motion.js').then(() => Promise.all(BLOCKS.map((b) => script(`/motion/${b}.js`))));
  return motionReady;
}

// source seconds `dt` into a shot (its speed steps added up; past its moving part the last frame holds)
function srcAt(shot, dt) {
  const d = clamp(dt, 0, shot.moving);
  let pos = shot.in;
  const steps = shot.steps;
  for (let k = 0; k < steps.length; k += 1) {
    const a = steps[k][0];
    const b = k + 1 < steps.length ? steps[k + 1][0] : Infinity;
    if (d <= a) break;
    pos += (Math.min(d, b) - a) * steps[k][1];
  }
  return pos;
}
function rateAt(shot, dt) {
  let rate = shot.steps[0][1];
  for (const [at, speed] of shot.steps) if (dt >= at) rate = speed;
  return clamp(rate, 0.0625, 16);
}
function musicAt(m, t) {
  let pos = m.start + t;
  for (const j of m.jumps || []) if (t >= j.at) pos = j.to + (t - j.at);
  return pos;
}

class Player {
  constructor(root, tl) {
    this.root = root;
    this.tl = tl;
    this.t = 0;
    this.playing = false;
    root.replaceChildren();
    root.classList.add('pv-ready');
    const stage = el('div', 'pv-stage', root);
    stage.style.aspectRatio = `${tl.w} / ${tl.h}`;
    stage.classList.toggle('tall', tl.h > tl.w);
    this.stage = stage;
    this.layers = [0, 1].map(() => {
      const box = el('div', 'pv-layer', stage);
      const v = el('video', '', box);
      v.playsInline = true;
      v.preload = 'auto';
      v.setAttribute('playsinline', '');
      return { box, v, i: -1 };
    });
    this.flash = el('div', 'pv-flash', stage);
    this.fade = el('div', 'pv-fade', stage);
    this.texts = el('div', 'pv-texts', stage);
    this.gfx = el('div', 'pv-motion', stage);
    this.motion = [];
    if (tl.motion && tl.motion.length) {
      loadMotion().then(() => this.mountMotion()).catch(() => { /* the preview plays without its graphics */ });
    }
    if (tl.letterbox && tl.w / tl.h < tl.letterbox) {
      const bar = Math.max(0, (1 - (tl.w / tl.letterbox) / tl.h) / 2) * 100;
      for (const side of ['top', 'bottom']) el('div', `pv-bar ${side}`, stage).style.height = `${bar}%`;
    }
    this.badge = el('span', 'shot-badge pv-badge', stage);
    const big = el('button', 'pv-big', stage);
    big.type = 'button';
    big.setAttribute('aria-label', 'Play');
    big.addEventListener('click', () => this.toggle());
    this.big = big;
    const bar = el('div', 'pv-controls', root);
    this.btn = el('button', 'pv-play', bar);
    this.btn.type = 'button';
    this.btn.addEventListener('click', () => this.toggle());
    this.scrub = el('input', 'pv-scrub', bar);
    Object.assign(this.scrub, { type: 'range', min: 0, max: tl.duration, step: 0.01, value: 0 });
    this.scrub.setAttribute('aria-label', 'Position');
    this.scrub.addEventListener('input', () => this.seek(parseFloat(this.scrub.value)));
    this.time = el('span', 'pv-time', bar);
    if (tl.music && tl.music.src) {
      this.music = new Audio(tl.music.src);
      this.music.preload = 'auto';
    }
    for (const L of this.layers) L.box.style.filter = LOOKS[tl.look] || ''; // the picture, not the graphics on it
    this.resize = new ResizeObserver(() => this.draw());
    this.resize.observe(stage);
    for (const L of this.layers) L.v.addEventListener('loadedmetadata', () => this.draw());
    this.seek(0);
  }

  // each graphic on a stage of the film's own size in a shadow root of its own (its styles stay its own), scaled
  // down to the preview, so it lays out and moves exactly as in the render
  async mountMotion() {
    const FM = window.FlicelloMotion;
    this.motion = await Promise.all(this.tl.motion.filter((g) => FM.blocks[g.block]).map(async (g) => {
      const host = el('div', 'pv-gfx', this.gfx);
      const shadow = host.attachShadow({ mode: 'open' });
      // its styles in first: the block measures its type as it builds
      await Promise.all(['motion', g.block].map((name) => new Promise((done) => {
        const css = el('link', '', shadow);
        css.rel = 'stylesheet';
        css.onload = done;
        css.onerror = done;
        css.href = `/motion/${name}.css`;
      })));
      const stage = el('div', '', shadow);
      const M = FM.make(stage, g.vars, this.tl.w, this.tl.h);
      FM.blocks[g.block](M);
      return { g, host, stage, M };
    }));
    this.draw();
  }

  shotAt(t) {
    const shots = this.tl.shots;
    let i = 0;
    for (let k = 0; k < shots.length; k += 1) if (shots[k].start <= t + 1e-6) i = k;
    return i;
  }

  load(L, i, at = 0) {
    const shot = this.tl.shots[i];
    L.i = i;
    if (!shot || !shot.src) { L.v.removeAttribute('src'); return; }
    if (!L.v.src.endsWith(shot.src)) L.v.src = shot.src;
    const go = () => { try { L.v.currentTime = srcAt(shot, at); } catch { /* not ready yet */ } };
    if (L.v.readyState >= 1) go(); else L.v.addEventListener('loadedmetadata', go, { once: true });
    L.v.muted = !!shot.mute;
    L.v.volume = dbToGain(shot.gain);
    L.v.playbackRate = rateAt(shot, at);
  }

  // the layer showing shot i, loading it into the other one if needed (the next shot is usually already there)
  layerFor(i, at) {
    let L = this.layers.find((x) => x.i === i);
    if (!L) {
      L = this.layers.find((x) => x !== this.active) || this.layers[0];
      this.load(L, i, at);
    }
    return L;
  }

  seek(t) {
    this.t = clamp(t, 0, this.tl.duration);
    const i = this.shotAt(this.t);
    const shot = this.tl.shots[i];
    this.active = this.layers[0];
    this.load(this.layers[0], i, this.t - shot.start);
    if (i + 1 < this.tl.shots.length) this.load(this.layers[1], i + 1, 0);
    if (this.music) { try { this.music.currentTime = musicAt(this.tl.music, this.t); } catch { /* later */ } }
    if (this.playing) this.layers[0].v.play().catch(() => {});
    this.draw();
  }

  toggle() { if (this.playing) this.pause(); else this.play(); }

  play() {
    if (this.t >= this.tl.duration - 0.05) this.seek(0);
    this.playing = true;
    this.clock = performance.now() - this.t * 1000;
    this.root.classList.add('playing');
    this.active.v.play().catch(() => {});
    if (this.music) {
      this.music.currentTime = musicAt(this.tl.music, this.t);
      this.music.play().catch(() => {});
    }
    const tick = () => {
      if (!this.playing) return;
      this.t = (performance.now() - this.clock) / 1000;
      if (this.t >= this.tl.duration) { this.t = this.tl.duration; this.pause(); this.draw(); return; }
      this.step();
      this.draw();
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  pause() {
    this.playing = false;
    cancelAnimationFrame(this.raf);
    this.root.classList.remove('playing');
    for (const L of this.layers) L.v.pause();
    if (this.music) this.music.pause();
  }

  // keep the right shots playing, in time, with the next one parked on its first frame
  step() {
    const shots = this.tl.shots;
    const i = this.shotAt(this.t);
    const shot = shots[i];
    let L = this.layerFor(i, this.t - shot.start);
    if (L !== this.active) {
      const old = this.active;
      this.active = L;
      L.v.play().catch(() => {});
      const prev = shots[i - 1];
      const overlapping = prev && this.t < prev.end;
      if (!overlapping) old.v.pause();
    }
    const dt = this.t - shot.start;
    if (dt <= shot.moving) {
      const want = srcAt(shot, dt);
      if (Math.abs(L.v.currentTime - want) > DRIFT && L.v.readyState >= 2) L.v.currentTime = want;
      L.v.playbackRate = rateAt(shot, dt);
    } else if (!L.v.paused) L.v.pause(); // a freeze: the last frame holds
    const other = this.layers.find((x) => x !== L);
    if (other.i >= 0 && other.i < i && this.t >= shots[other.i].end && !other.v.paused) other.v.pause(); // its overlap is over
    const next = shots[i + 1];
    if (next && other.i !== i + 1 && !(shots[i - 1] && this.t < shots[i - 1].end && other.i === i - 1)) this.load(other, i + 1, 0);
    if (this.music && !this.music.paused) {
      const want = musicAt(this.tl.music, this.t);
      if (Math.abs(this.music.currentTime - want) > MUSIC_DRIFT) this.music.currentTime = want;
      const m = this.tl.music;
      const fin = m.fade_in ? clamp(this.t / m.fade_in, 0, 1) : 1;
      const fout = m.fade_out ? clamp((this.tl.duration - this.t) / m.fade_out, 0, 1) : 1;
      this.music.volume = dbToGain(m.gain) * fin * fout;
    }
  }

  // place one layer's picture: cover the frame around the shot's centre, with its zoom, push, flip and hits
  place(L, shot, dt, alpha) {
    const v = L.v;
    const W = this.stage.clientWidth;
    const H = this.stage.clientHeight;
    L.box.style.opacity = String(alpha);
    if (!v.videoWidth || !W) return;
    const vw = v.videoWidth;
    const vh = v.videoHeight;
    const fit = shot.framing === 'fit' || shot.framing === 'blur_pad';
    const base = fit ? Math.min(W / vw, H / vh) : Math.max(W / vw, H / vh);
    const p = clamp(dt / Math.max(0.01, shot.end - shot.start), 0, 1);
    let z = shot.zoom || 1;
    if (shot.push > 0) z *= 1 + shot.push * ease(p);
    else if (shot.push < 0) z *= 1 + -shot.push * (1 - ease(p));
    let shake = 0;
    for (const f of shot.fx) {
      const into = dt - f.at;
      if (into < 0 || into > f.dur) continue;
      const decay = 1 - into / f.dur;
      if (f.type === 'pulse') z *= 1 + 0.06 * f.k * decay;
      if (f.type === 'shake') shake = Math.max(shake, 14 * f.k * decay);
    }
    const s = base * z;
    const [cx, cy] = (shot.zoom || 1) > 1.001 ? shot.zc : shot.fc;
    let ox = W / 2 - cx * vw * s;
    let oy = H / 2 - cy * vh * s;
    if (!fit) {
      ox = clamp(ox, W - vw * s, 0);
      oy = clamp(oy, H - vh * s, 0);
    } else {
      ox = (W - vw * s) / 2;
      oy = (H - vh * s) / 2;
    }
    if (shake) {
      ox += Math.sin(performance.now() / 23) * shake;
      oy += Math.cos(performance.now() / 29) * shake * 0.6;
    }
    v.style.width = `${vw}px`;
    v.style.height = `${vh}px`;
    v.style.transform = shot.flip ? `translate(${ox + vw * s}px, ${oy}px) scale(${-s}, ${s})` : `translate(${ox}px, ${oy}px) scale(${s})`;
    v.style.filter = shot.look ? LOOKS[shot.look] || '' : '';
  }

  draw() {
    const tl = this.tl;
    const shots = tl.shots;
    const t = this.t;
    const i = this.shotAt(t);
    const shot = shots[i];
    const L = this.active || this.layers[0];
    const prev = shots[i - 1];
    let alpha = 1;
    let dip = 0;
    if (prev && t < prev.end && shot.trans.dur > 0) { // a transition: both shots on screen
      const k = clamp((t - shot.start) / shot.trans.dur, 0, 1);
      const P = this.layers.find((x) => x.i === i - 1);
      if (shot.trans.type === 'fadeblack' || shot.trans.type === 'fadewhite') {
        dip = 1 - Math.abs(2 * k - 1);
        this.fade.style.background = shot.trans.type === 'fadewhite' ? '#fff' : '#000';
        if (P && k < 0.5) { this.place(P, prev, t - prev.start, 1); alpha = 0; } else if (P) P.box.style.opacity = '0';
      } else if (P) {
        this.place(P, prev, t - prev.start, 1);
        alpha = k;
      }
      L.box.style.zIndex = '2';
      if (P) P.box.style.zIndex = '1';
    } else {
      for (const x of this.layers) if (x !== L) x.box.style.opacity = '0';
      L.box.style.zIndex = '2';
    }
    this.place(L, shot, t - shot.start, alpha);
    // hits: a flash
    let flash = 0;
    for (const f of shot.fx) {
      const into = t - shot.start - f.at;
      if (f.type === 'flash' && into >= 0 && into <= f.dur) flash = Math.max(flash, 0.85 * Math.min(1, f.k) * (1 - into / f.dur));
    }
    this.flash.style.opacity = String(flash);
    // the film's own fade in and out, and dips through black or white
    const fin = tl.fade_in ? clamp(1 - t / tl.fade_in, 0, 1) : 0;
    const fout = tl.fade_out ? clamp(1 - (tl.duration - t) / tl.fade_out, 0, 1) : 0;
    if (!dip) this.fade.style.background = '#000';
    this.fade.style.opacity = String(Math.max(fin, fout, dip));
    // texts
    const on = tl.texts.filter((x) => t >= x.start && t < x.end);
    if (on.map((x) => x.text).join('|') !== this.textKey) {
      this.textKey = on.map((x) => x.text).join('|');
      this.texts.replaceChildren(...on.map((x) => {
        const d = el('div', `pv-text ${x.position} ${x.size} ${x.style}`);
        d.textContent = x.text;
        return d;
      }));
    }
    for (const [k, x] of on.entries()) {
      const d = this.texts.children[k];
      if (!d) continue;
      const f = x.fade || 0;
      d.style.opacity = String(f ? clamp(Math.min((t - x.start) / f, (x.end - t) / f), 0, 1) : 1);
    }
    // the motion graphics, at this moment of their own clocks (hidden, not removed: they keep their layout)
    const k = this.stage.clientWidth / tl.w;
    for (const m of this.motion) {
      const on = t >= m.g.start && t < m.g.end;
      m.host.style.visibility = on ? 'visible' : 'hidden';
      if (!on) continue;
      m.stage.style.transform = `scale(${k})`;
      m.M.seek(t - m.g.start);
    }
    this.badge.textContent = `Shot ${shot.n}`;
    this.scrub.value = String(t);
    this.time.textContent = `${mmss(t)} / ${mmss(tl.duration)}`;
    this.btn.textContent = this.playing ? 'Pause' : 'Play';
    this.btn.setAttribute('aria-label', this.playing ? 'Pause' : 'Play');
  }
}

async function mount(root, tries = 0) {
  const pid = root.dataset.preview;
  const which = root.dataset.which || 'draft';
  let r;
  try {
    r = await fetch(`/api/p/${pid}/preview?which=${encodeURIComponent(which)}`, { credentials: 'same-origin' });
  } catch {
    r = null;
  }
  if (!r || !r.ok) {
    root.textContent = 'The instant preview isn’t available for this one.';
    return;
  }
  const tl = await r.json();
  if (tl.missing && tl.missing.length && tries < 60) { // the clips' light copies are being made: wait for them
    root.textContent = 'Making quick previews of your clips…';
    setTimeout(() => mount(root, tries + 1), 4000);
    return;
  }
  root.player = new Player(root, tl);
}

export function previews() {
  document.querySelectorAll('[data-preview]').forEach((root) => { mount(root); });
}

// The edit-style cards: each style's own title, moving in a loop over a still from their film, in the style's grade.
export function stylePreviews() {
  const cards = [...document.querySelectorAll('[data-style-preview]')];
  if (!cards.length) return;
  loadMotion().then(() => {
    const FM = window.FlicelloMotion;
    const live = cards.map((card) => {
      const tall = card.classList.contains('tall');
      const W = tall ? 540 : 960;
      const H = tall ? 960 : 540;
      if (card.dataset.still) {
        const img = el('img', 'style-still', card);
        img.src = card.dataset.still;
        img.alt = '';
        img.style.filter = LOOKS[JSON.parse(card.dataset.stylePreview).look] || '';
      }
      const host = el('div', 'style-gfx', card);
      const shadow = host.attachShadow({ mode: 'open' });
      return Promise.all(['motion', 'title'].map((name) => new Promise((done) => {
        const css = el('link', '', shadow);
        css.rel = 'stylesheet';
        css.onload = done;
        css.onerror = done;
        css.href = `/motion/${name}.css`;
      }))).then(() => {
        const stage = el('div', '', shadow);
        const vars = { ...JSON.parse(card.dataset.stylePreview), y: 0.45, size: tall ? 0.075 : 0.12, duration: 3.2, fps: 30 };
        const M = FM.make(stage, vars, W, H);
        FM.blocks.title(M);
        return { card, stage, M, W };
      });
    });
    Promise.all(live).then((items) => {
      const t0 = performance.now();
      const tick = (now) => {
        items.forEach((it, k) => { // each its own moment, so the gallery is never blank at once
          if (!it.card.isConnected) return;
          const t = ((now - t0) / 1000 + k * 0.55) % 3.6; // the title's whole life, then a beat of nothing, again
          it.stage.style.transform = `scale(${it.card.clientWidth / it.W})`;
          it.M.seek(Math.min(t, 3.2));
        });
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
  }).catch(() => { /* the cards work without their moving sample */ });
}
