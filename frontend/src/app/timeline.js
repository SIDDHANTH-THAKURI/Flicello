// The hands-on timeline: the film's shots as blocks as long as they play, the graphics on a track above.
// - Drag a shot onto another place to move it there; drag its right edge to make it longer or shorter (in half beats).
// - Tap a shot for more: remove, swap with the next one, how it plays, a fresh picture.
// - Drag a graphic along to another shot; tap it to take it away.
// Every change becomes a draft at once (the same changes Cello makes from words), and is remembered as what they do
// by hand, so their next films learn it.

const el = (tag, cls, parent) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (parent) parent.append(n);
  return n;
};
const WHAT = { title: 'Title', captions: 'Captions', lowerthird: 'Name', transition: 'Transition', kinetic: 'Beat words',
  callout: 'Callout', route: 'Route', accent: 'Finish', logo: 'Logo' };

async function send(box, ops, said) {
  box.classList.add('busy');
  const r = await fetch(`/api/p/${box.dataset.timeline}/edit`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF': box.dataset.csrf, Accept: 'application/json' },
    body: JSON.stringify({ ops, said }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    box.classList.remove('busy');
    const note = box.querySelector('.tl-note') || el('p', 'help tl-note', box);
    note.textContent = j.detail || 'That change could not be made.';
    return;
  }
  window.location.hash = 'draft';
  window.location.reload(); // the draft panel shows it, playing, in a few seconds
}

function render(box, tl) {
  const body = box.querySelector('.tl-body');
  body.replaceChildren();
  const total = tl.duration || 1;
  const pct = (t) => `${(t / total) * 100}%`;
  // the graphics track
  const gtrack = el('div', 'tl-gtrack', body);
  tl.graphics.forEach((g) => {
    if (g.kind === 'captions') {
      const bar = el('div', 'tl-g captions', gtrack);
      bar.style.left = '0';
      bar.style.width = '100%';
      bar.textContent = 'Captions';
      bar.title = 'Tap to take the captions away';
      bar.addEventListener('click', () => send(box, [{ op: 'captions', shot: 1, off: true }], ['The captions are taken away']));
      return;
    }
    const shot = tl.shots.find((s) => s.n === g.shot);
    if (!shot) return;
    const bar = el('div', `tl-g ${g.kind}`, gtrack);
    const end = Math.min(total, shot.start + (g.beats || 4) * ((shot.end - shot.start) / Math.max(0.5, shot.beats)));
    bar.style.left = pct(shot.start);
    bar.style.width = pct(Math.max(0.4, end - shot.start));
    bar.textContent = `${WHAT[g.kind] || g.kind}${g.text ? `: ${g.text.split('|').join(' → ')}` : ''}`;
    bar.draggable = true;
    bar.title = 'Drag it to another shot; tap to take it away';
    bar.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/graphic', JSON.stringify(g)); });
    bar.addEventListener('click', () => send(box, [{ op: g.kind, shot: g.shot, off: true }], [`The ${WHAT[g.kind] || g.kind} on shot ${g.shot} is taken away`]));
  });
  // the shots
  const track = el('div', 'tl-track', body);
  tl.shots.forEach((s, i) => {
    const b = el('div', `tl-shot kind-${s.kind}`, track);
    b.style.width = pct(s.end - s.start);
    b.draggable = true;
    b.dataset.n = String(s.n);
    if (s.thumb) {
      const img = el('img', '', b);
      img.src = box.dataset.stills + s.thumb;
      img.alt = '';
      img.draggable = false;
    }
    el('span', 'tl-n', b).textContent = String(s.n);
    const grip = el('span', 'tl-grip', b);
    grip.title = 'Drag to make it longer or shorter';
    // drag the edge: half beats
    grip.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const x0 = e.clientX;
      const perBeat = (b.getBoundingClientRect().width) / Math.max(0.5, s.beats);
      const ghost = el('span', 'tl-ghost', b);
      const move = (ev) => {
        const beats = Math.round(((ev.clientX - x0) / perBeat) * 2) / 2;
        ghost.textContent = beats ? `${beats > 0 ? '+' : ''}${beats} beat${Math.abs(beats) === 1 ? '' : 's'}` : '';
        b.style.width = `calc(${pct(s.end - s.start)} + ${beats * perBeat}px)`;
      };
      const up = (ev) => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        const beats = Math.round(((ev.clientX - x0) / perBeat) * 2) / 2;
        ghost.remove();
        if (!beats) { b.style.width = pct(s.end - s.start); return; }
        const op = beats > 0 ? 'longer' : 'shorter';
        send(box, [{ op, shot: s.n, beats: Math.abs(beats) }],
          [`Shot ${s.n} is ${Math.abs(beats)} beat${Math.abs(beats) === 1 ? '' : 's'} ${beats > 0 ? 'longer' : 'shorter'}`]);
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
    });
    // drag the shot onto another: it moves there; a graphic dropped on it moves to it
    b.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/shot', String(s.n)); });
    b.addEventListener('dragover', (e) => { e.preventDefault(); b.classList.add('over'); });
    b.addEventListener('dragleave', () => b.classList.remove('over'));
    b.addEventListener('drop', (e) => {
      e.preventDefault();
      b.classList.remove('over');
      const from = parseInt(e.dataTransfer.getData('text/shot'), 10);
      const g = e.dataTransfer.getData('text/graphic');
      if (g) {
        const graphic = JSON.parse(g);
        if (graphic.shot === s.n) return;
        send(box, [{ op: graphic.kind, shot: graphic.shot, off: true },
          { op: graphic.kind, shot: s.n, text: graphic.text, sub: graphic.sub, style: graphic.style, beats: graphic.beats, want: graphic.want }],
        [`The ${WHAT[graphic.kind] || graphic.kind} moves from shot ${graphic.shot} to shot ${s.n}`]);
      } else if (from && from !== s.n) {
        send(box, [{ op: 'move', shot: from, other: s.n }], [`Shot ${from} moves to where shot ${s.n} is`]);
      }
    });
    // tap: the shot's own changes
    b.addEventListener('click', () => {
      const open = body.querySelector('.tl-menu');
      if (open) open.remove();
      const menu = el('div', 'tl-menu', body);
      el('b', '', menu).textContent = `Shot ${s.n}`;
      const act = (label, ops, said) => {
        const btn = el('button', 'btn quiet', menu);
        btn.type = 'button';
        btn.textContent = label;
        btn.addEventListener('click', () => send(box, ops, [said]));
      };
      act('Remove', [{ op: 'remove', shot: s.n }], `Shot ${s.n} is removed`);
      if (i + 1 < tl.shots.length) act('Swap with the next', [{ op: 'swap', shot: s.n, other: tl.shots[i + 1].n }], `Shots ${s.n} and ${tl.shots[i + 1].n} trade places`);
      act('Something else here', [{ op: 'replace', shot: s.n, want: 'something else that fits' }], `Shot ${s.n} shows something else`);
      act('Slow motion', [{ op: 'speed', shot: s.n, kind: 'hold' }], `Shot ${s.n} plays in slow motion`);
      act('Normal speed', [{ op: 'speed', shot: s.n, kind: 'cut' }], `Shot ${s.n} plays at normal speed`);
      const talk = document.querySelector('#talk-text');
      if (talk) { // effects are Cello's work (minutes, not seconds): asked for in its conversation
        const fx = el('button', 'btn quiet', menu);
        fx.type = 'button';
        fx.textContent = 'A 3D photo';
        fx.addEventListener('click', () => { talk.value = `make shot ${s.n} a 3d photo`; talk.form.requestSubmit(); });
      }
    });
  });
}

export function timeline() {
  const box = document.querySelector('[data-timeline]');
  if (!box) return;
  fetch(`/api/p/${box.dataset.timeline}/timeline?which=${encodeURIComponent(box.dataset.which)}`, { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then((tl) => render(box, tl))
    .catch(() => { box.querySelector('.tl-body').textContent = 'The timeline isn’t available for this version.'; });
}
