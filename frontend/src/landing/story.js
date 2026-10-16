// Scroll is the timeline. Each chapter is a tall section with a sticky frame: while it scrolls in, the cards
// morph to its layout; while it is pinned, its action plays (the scan, the pick, the playhead, the phone).
const clamp01 = (x) => Math.min(1, Math.max(0, x));
const smooth = (x) => { x = clamp01(x); return x * x * (3 - 2 * x); };

// chapter progress: 0 when the section's top meets the bottom of the screen, 1 when its bottom does
function progress(el) {
  const r = el.getBoundingClientRect();
  const vh = window.innerHeight;
  const total = r.height;
  return clamp01((vh - r.top) / total);
}

export function createStory({ stage, beat, reduced }) {
  const $ = (s) => document.querySelector(s);
  const chapters = [1, 2, 3, 4].map((k) => document.querySelector(`[data-chapter="${k}"]`));
  const hero = $('[data-chapter="0"]');
  const compare = $('#compare');
  const veil = $('.lp-veil');
  const nav = $('[data-nav]');
  const timecode = $('[data-timecode]');
  const cutsEl = $('[data-cuts]');

  // ---- scan tags: a few kept and a few cut cards get a verdict as the beam passes
  const tagsBox = $('[data-tags]');
  const TAGS_KEEP = ['horizon · level', 'waves · peak at 0:02', 'golden blue', 'bridge · wide', 'motion → left', 'clean light'];
  const TAGS_CUT = ['shaky · cut', 'duplicate · cut', 'out of focus · cut'];
  let tags = [];
  function placeTags() {
    tagsBox.innerHTML = '';
    const L = stage.layouts;
    const flags = L.flag;
    const N = L.N;
    const pick = (kept, n, from) => {
      const out = [];
      const cols = L.wall.cols;
      for (let i = 0; i < N && out.length < n; i++) {
        const col = i % cols, row = Math.floor(i / cols);
        const keep = flags[i * 4] > 0.5;
        // spread the tags over the wall: one per band of columns, away from the edges
        const band = Math.floor((col / cols) * n);
        if (keep === kept && band === out.length && row > 1 && row < L.wall.rows - 2 && ((row * 7 + col * 3 + from) % 5 === 0)) out.push(i);
      }
      return out;
    };
    const keepIdx = pick(true, TAGS_KEEP.length, 0);
    const cutIdx = pick(false, TAGS_CUT.length, 2);
    tags = [
      ...keepIdx.map((i, n) => ({ i, text: TAGS_KEEP[n], cut: false })),
      ...cutIdx.map((i, n) => ({ i, text: TAGS_CUT[n], cut: true })),
    ].map((t) => {
      const el = document.createElement('span');
      el.className = 'tag' + (t.cut ? ' cut' : '');
      el.textContent = t.text;
      tagsBox.appendChild(el);
      return { ...t, el, col: flags[t.i * 4 + 3] };
    });
  }

  // ---- pitches anchored to the constellations
  const pitchEls = [...document.querySelectorAll('[data-pitch]')];
  const counters = [...document.querySelectorAll('[data-count]')];
  let lastWide = null;

  const state = { time: 0, morph: 0, scan: -1, pick: 0, playhead: 0, wave: 0, phone: 0, pulse: 0, shot: 0, beatFrac: 0, titleA: 0 };

  function update() {
    const b = beat.read();
    state.time = b.t;
    state.pulse = reduced ? 0 : b.pulse;
    state.shot = b.beat;
    state.beatFrac = b.frac;
    state.titleA = smooth(1 - ((b.beat % 32) + b.frac - 2.4) / 1.2) * (b.beat % 32 < 4 ? 1 : 0);

    const L = stage.layouts;
    if (L.wide !== lastWide) { lastWide = L.wide; placeTags(); }

    const p = chapters.map(progress);
    const entry = p.map((x) => smooth((x - 0.04) / 0.36));
    const action = p.map((x) => clamp01((x - 0.42) / 0.5));
    const pc = compare ? clamp01((window.innerHeight - compare.getBoundingClientRect().top) / window.innerHeight) : 0;

    let morph = 0;
    for (let k = 0; k < 4; k++) if (entry[k] > 0) morph = k + entry[k];
    if (pc > 0) morph = Math.max(morph, 4 + smooth(pc));
    state.morph = morph;

    // 1 · the scan
    state.scan = action[0] > 0 && morph < 1.6 ? action[0] * 1.08 - 0.03 : (morph >= 1.6 ? 2 : -1);
    const scanned = clamp01(action[0] * 1.08);
    counters.forEach((el) => {
      const target = +el.dataset.count;
      const v = Math.round(target * scanned);
      if (el._v !== v) { el._v = v; el.textContent = v.toLocaleString('en-AU'); }
    });
    const onWall = 1 - smooth(Math.abs(morph - 1) / 0.5);
    for (const t of tags) {
      const s = stage.project(t.i, 'l1');
      const shown = onWall * smooth((state.scan - t.col) / 0.06);
      t.el.style.opacity = shown.toFixed(3);
      t.el.style.transform = `translate(${s.x.toFixed(1)}px, ${(s.y - 26 - (1 - shown) * 10).toFixed(1)}px) translate(-50%, -100%)`;
    }

    // 2 · the pick
    state.pick = smooth((action[1] - 0.25) / 0.45);
    const onStory = 1 - smooth(Math.abs(morph - 2) / 0.45);
    pitchEls.forEach((el, k) => {
      const centre = L.centres[k];
      const chosen = k === 1;
      const target = chosen ? L.front : centre.clone().add(centre.clone().sub(L.front).multiplyScalar(0.35)).setZ(centre.z - 3);
      const at = centre.clone().lerp(target, state.pick);
      const lift = (L.wide ? 2.05 : 1.15) * (chosen ? 1 + state.pick * 0.25 : 1 - state.pick * 0.2);
      const s = stage.projectPoint(at.clone().setY(at.y + lift));
      const fade = onStory * (chosen ? 1 : 1 - state.pick);
      el.style.opacity = fade.toFixed(3);
      el.style.transform = `translate(${s.x.toFixed(1)}px, ${s.y.toFixed(1)}px) translate(-50%, -100%) scale(${(chosen ? 1 + state.pick * 0.15 : 1 - state.pick * 0.1).toFixed(3)})`;
      el.classList.toggle('chosen', chosen && state.pick > 0.5);
    });

    // 3 · the beat
    state.wave = smooth((morph - 2.55) / 0.4) * (1 - smooth((morph - 3.4) / 0.4));
    state.playhead = action[2];
    if (cutsEl) {
      const slots = L.timeline.slots;
      const head = L.timeline.x - L.timeline.w / 2 + L.timeline.w * state.playhead;
      const cuts = slots.filter(([x, w]) => x - w / 2 < head).length;
      if (cutsEl._v !== cuts) { cutsEl._v = cuts; cutsEl.textContent = String(cuts); }
    }

    // 4 · the phone
    state.phone = smooth((morph - 3.35) / 0.6) * (1 - smooth(pc * 1.15));

    // the veil follows the chapter: heavy on the left while copy sits there, low when it moves down
    const veilL = morph < 1.5 ? 0.8 : morph < 2.5 ? 0.42 + Math.abs(morph - 2) * 0.6 : 0.74;
    const veilB = morph > 1.5 && morph < 2.5 ? 0.85 : 0.5;
    veil.style.setProperty('--veil-l', veilL.toFixed(3));
    veil.style.setProperty('--veil-b', veilB.toFixed(3));

    nav.classList.toggle('solid', window.scrollY > 40);
    if (timecode) {
      const frames = Math.floor(window.scrollY * 1.6);
      const ff = frames % 30, ss = Math.floor(frames / 30) % 60, mm = Math.floor(frames / 1800) % 60, hh = Math.floor(frames / 108000);
      const tc = [hh, mm, ss, ff].map((n) => String(n).padStart(2, '0')).join(':');
      if (timecode._v !== tc) { timecode._v = tc; timecode.textContent = tc; }
    }
    void hero;
    return state;
  }

  return { update, state };
}
