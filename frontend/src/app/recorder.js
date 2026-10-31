// Record on the spot, with no other app: the camera (front or back), the screen, or a voice memo, in the browser. A
// 3-2-1, a live level, several takes kept to compare; "Use this take" puts it straight into the film (a clip, the
// talk, or the voice). Opened by any [data-record="camera|screen|voice"]; a page says where takes go by setting
// window.flicelloTake = async (file, mode) => {...} (the new-film page makes a draft film first).
import { api, uploadFile } from './studio.js';
import { toast } from './ui.js';

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const mmss = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;

function mimeFor(mode) {
  const kinds = mode === 'voice'
    ? ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus']
    : ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm', 'video/mp4'];
  return kinds.find((k) => window.MediaRecorder && MediaRecorder.isTypeSupported(k)) || '';
}

export function recorder() {
  const dlg = $('#recorder');
  if (!dlg || !navigator.mediaDevices) return;
  const cam = $('[data-cam]', dlg);
  const live = $('[data-cam-video]', dlg);
  const replay = $('[data-take-video]', dlg);
  const vwave = $('[data-vwave]', dlg);
  const level = $$('[data-level] i', dlg);
  const recT = $('[data-rec-t]', dlg);
  const count = $('[data-count]', dlg);
  const note = $('[data-cam-note]', dlg);
  const big = $('[data-bigrec]', dlg);
  const flip = $('[data-flip]', dlg);
  const list = $('[data-take-list]', dlg);
  const use = $('[data-use-take]', dlg);
  const modes = $('[data-rec-mode]', dlg);
  vwave.innerHTML = '<i></i>'.repeat(36);
  const bars = $$('i', vwave);
  const S = { mode: 'camera', stream: null, rec: null, chunks: [], state: 'idle', t0: 0, facing: 'user', takes: [], chosen: -1,
    ctx: null, analyser: null, raf: 0 };

  const say = (text) => { note.textContent = text; note.hidden = !text; };
  function stop() {
    if (S.stream) S.stream.getTracks().forEach((t) => t.stop());
    S.stream = null;
    live.srcObject = null;
    if (S.ctx) { S.ctx.close().catch(() => {}); S.ctx = null; S.analyser = null; }
  }
  async function open(mode) {
    stop();
    S.mode = mode;
    $$('button', modes).forEach((b) => b.setAttribute('aria-pressed', b.dataset.v === mode ? 'true' : 'false'));
    cam.classList.remove('camera', 'screen', 'voice', 'real');
    cam.classList.add(mode);
    flip.hidden = mode !== 'camera';
    replay.hidden = true;
    say('');
    try {
      if (mode === 'camera') {
        S.stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: S.facing, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: { echoCancellation: true, noiseSuppression: true },
        });
      } else if (mode === 'screen') {
        const screen = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 30 }, audio: true });
        let mic = null;
        try { mic = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch { mic = null; }
        S.stream = new MediaStream([...screen.getVideoTracks(), ...(mic ? mic.getAudioTracks() : screen.getAudioTracks())]);
        screen.getVideoTracks()[0].addEventListener('ended', () => { if (S.state === 'rec') big.click(); });
      } else {
        S.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      }
    } catch (err) {
      say(mode === 'screen' ? 'Screen sharing was stopped or declined.'
        : `No ${mode === 'voice' ? 'microphone' : 'camera'} here, or your browser was told no. Allow it in the address bar and try again.`);
      return;
    }
    if (mode !== 'voice') { live.srcObject = S.stream; live.classList.toggle('mirror', mode === 'camera' && S.facing === 'user'); live.play().catch(() => {}); cam.classList.add('real'); }
    if (S.stream.getAudioTracks().length) {
      S.ctx = new AudioContext();
      const src = S.ctx.createMediaStreamSource(new MediaStream(S.stream.getAudioTracks()));
      S.analyser = S.ctx.createAnalyser();
      S.analyser.fftSize = 512;
      src.connect(S.analyser);
    }
  }
  function meter() {
    S.raf = requestAnimationFrame(meter);
    if (S.state === 'rec') recT.textContent = `REC ${mmss((performance.now() - S.t0) / 1000)}`;
    if (!S.analyser) return;
    const buf = new Uint8Array(S.analyser.fftSize);
    S.analyser.getByteTimeDomainData(buf);
    let sum = 0;
    for (const v of buf) sum += ((v - 128) / 128) ** 2;
    const rms = Math.min(1, Math.sqrt(sum / buf.length) * 4);
    level.forEach((b, i) => { b.style.height = `${12 + 88 * Math.min(1, rms * (0.6 + 0.4 * Math.sin(performance.now() / 120 + i)))}%`; });
    if (S.mode === 'voice') {
      const f = new Uint8Array(S.analyser.frequencyBinCount);
      S.analyser.getByteFrequencyData(f);
      bars.forEach((b, i) => { b.style.height = `${6 + 70 * (f[2 + i * 2] / 255)}%`; });
    }
  }
  function drawTakes() {
    list.textContent = '';
    if (!S.takes.length) {
      const p = document.createElement('p');
      p.className = 'hint';
      p.textContent = 'No takes yet. Press the red button: 3, 2, 1, then it records.';
      list.append(p);
    }
    S.takes.forEach((t, i) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = `take${i === S.chosen ? ' sel' : ''}`;
      const thumb = document.createElement('i');
      if (t.poster) thumb.style.backgroundImage = `url(${t.poster})`;
      thumb.classList.toggle('voice', t.mode === 'voice');
      const words = document.createElement('span');
      const b = document.createElement('b');
      b.textContent = `Take ${i + 1}`;
      const sm = document.createElement('small');
      sm.textContent = `${mmss(t.seconds)} · ${{ camera: 'camera', screen: 'screen', voice: 'voice memo' }[t.mode]}`;
      words.append(b, sm);
      row.append(thumb, words);
      row.addEventListener('click', () => { S.chosen = i; drawTakes(); play(t); });
      list.prepend(row);
    });
    use.disabled = S.chosen < 0;
    use.textContent = S.chosen < 0 ? 'Use this take' : `Use take ${S.chosen + 1}`;
  }
  function play(t) {
    replay.hidden = false;
    replay.src = t.url;
    replay.controls = true;
    replay.play().catch(() => {});
  }
  function poster(url) {
    return new Promise((done) => {
      const v = document.createElement('video');
      v.muted = true; v.src = url;
      v.addEventListener('loadeddata', () => { v.currentTime = 0.2; }, { once: true });
      v.addEventListener('seeked', () => {
        try {
          const c = document.createElement('canvas');
          c.width = 140; c.height = 88;
          c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
          done(c.toDataURL('image/jpeg', 0.7));
        } catch { done(''); }
      }, { once: true });
      v.addEventListener('error', () => done(''), { once: true });
    });
  }
  big.addEventListener('click', async () => {
    if (S.state === 'count') return;
    if (S.state === 'rec') {
      S.state = 'idle';
      cam.classList.remove('recording');
      recT.textContent = 'SAVING';
      S.rec.stop();
      return;
    }
    if (!S.stream) await open(S.mode);
    if (!S.stream) return;
    replay.hidden = true;
    replay.pause();
    S.state = 'count';
    for (const n of [3, 2, 1]) {
      count.innerHTML = '';
      const sp = document.createElement('span');
      sp.textContent = n;
      count.append(sp);
      await sleep(850);
    }
    count.textContent = '';
    const type = mimeFor(S.mode);
    S.chunks = [];
    try { S.rec = new MediaRecorder(S.stream, type ? { mimeType: type, videoBitsPerSecond: 8_000_000 } : undefined); } catch (err) {
      S.state = 'idle'; say(`This browser can't record here: ${err.message}`); return;
    }
    S.rec.ondataavailable = (e) => { if (e.data && e.data.size) S.chunks.push(e.data); };
    S.rec.onstop = async () => {
      const blob = new Blob(S.chunks, { type: S.rec.mimeType || type || 'video/webm' });
      const seconds = (performance.now() - S.t0) / 1000;
      const url = URL.createObjectURL(blob);
      const take = { blob, url, seconds, mode: S.mode, poster: S.mode === 'voice' ? '' : await poster(url) };
      S.takes.push(take);
      S.chosen = S.takes.length - 1;
      recT.textContent = 'SAVED';
      drawTakes();
    };
    S.rec.start(1000);
    S.state = 'rec';
    S.t0 = performance.now();
    cam.classList.add('recording');
  });
  flip.addEventListener('click', () => { S.facing = S.facing === 'user' ? 'environment' : 'user'; open('camera'); });
  modes.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-v]');
    if (!b || S.state !== 'idle') return;
    open(b.dataset.v);
  });
  use.addEventListener('click', async () => {
    const t = S.takes[S.chosen];
    if (!t) return;
    use.disabled = true;
    const ext = t.blob.type.includes('mp4') ? 'mp4' : 'webm';
    const file = new File([t.blob], `take-${S.chosen + 1}-${t.mode}.${ext}`, { type: t.blob.type });
    use.textContent = 'Putting it in your film…';
    try {
      if (typeof window.flicelloTake === 'function') await window.flicelloTake(file, t.mode, (f) => { use.textContent = `Sending it · ${Math.round(f * 100)}%`; });
      else throw new Error('There is no film here to put it in.');
      toast(`Take ${S.chosen + 1} is in your film.`);
      close();
    } catch (err) {
      use.disabled = false;
      use.textContent = `Use take ${S.chosen + 1}`;
      toast(err.message);
    }
  });
  function close() {
    if (S.state === 'rec') { S.state = 'idle'; S.rec.stop(); }
    cancelAnimationFrame(S.raf);
    stop();
    replay.pause();
    if (dlg.open) dlg.close();
  }
  $('[data-rec-close]', dlg).addEventListener('click', close);
  dlg.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
  $$('[data-record]').forEach((b) => b.addEventListener('click', () => {
    dlg.showModal();
    drawTakes();
    meter();
    open(b.dataset.record);
  }));
}

// where a take goes on a page that has a film: into it (a clip, or the voice recording of a voice film)
export function takeIntoFilm(pid, kind) {
  window.flicelloTake = async (file, mode, onProgress) => {
    const upKind = mode === 'voice' && kind === 'voice' ? 'song' : 'clip';
    if (mode === 'voice' && kind !== 'voice') throw new Error('A voice memo goes into a voice film: start a new one with it.');
    const ui = { cancelled: false, uploadId: null, set: (f) => onProgress && onProgress(f) };
    await uploadFile(pid, file, upKind, ui);
    setTimeout(() => window.location.reload(), 600);
  };
}

export { api };
