// What the timeline draws inside its tracks: the clips' own frames (a filmstrip on every shot) and sound as waveforms.
// Frames are read from the clips' light copies by a hidden video per clip, one seek at a time, kept once read, and
// painted as they arrive; nothing waits on them.

const kept = new Map(); // `${src}@${t}` -> a small canvas of that frame
const readers = new Map(); // src -> { video, jobs: [{ t, done }], busy }
const STEP = 0.1; // seconds: frames this close share one picture

function reader(src) {
  let r = readers.get(src);
  if (r) return r;
  const video = document.createElement('video');
  video.muted = true;
  video.preload = 'auto';
  video.playsInline = true;
  video.src = src;
  r = { video, jobs: [], busy: false };
  readers.set(src, r);
  return r;
}

function next(r) {
  if (r.busy || !r.jobs.length) return;
  const job = r.jobs.shift();
  const key = `${r.video.src}@${job.t}`;
  if (kept.has(key)) { job.done(kept.get(key)); next(r); return; }
  r.busy = true;
  const grab = () => {
    const v = r.video;
    const h = 72;
    const w = Math.max(1, Math.round((v.videoWidth / Math.max(1, v.videoHeight)) * h));
    const c = document.createElement('canvas');
    c.width = w;
    c.height = h;
    try { c.getContext('2d').drawImage(v, 0, 0, w, h); } catch { /* not decodable: a blank tile */ }
    kept.set(key, c);
    r.busy = false;
    job.done(c);
    next(r);
  };
  const seek = () => {
    r.video.addEventListener('seeked', grab, { once: true });
    r.video.currentTime = Math.min(job.t, Math.max(0, (r.video.duration || job.t) - 0.05));
  };
  if (r.video.readyState >= 1) seek(); else r.video.addEventListener('loadedmetadata', seek, { once: true });
}

// the frame of a clip's light copy at `t` source seconds (a canvas), when it's read
export function frame(src, t) {
  const at = Math.max(0, Math.round(t / STEP) * STEP);
  const key = `${new URL(src, window.location.href).href}@${at}`;
  if (kept.has(key)) return Promise.resolve(kept.get(key));
  return new Promise((done) => {
    const r = reader(src);
    r.jobs.push({ t: at, done });
    next(r);
  });
}

// a clip's filmstrip sheet (made by the server: its frames in a grid), loaded once
const sheets = new Map();
function sheet(url) {
  if (!sheets.has(url)) {
    sheets.set(url, new Promise((done) => {
      const img = new Image();
      img.onload = () => done(img);
      img.onerror = () => done(null);
      img.src = url;
    }));
  }
  return sheets.get(url);
}

// a shot's filmstrip from its clip's sheet: every tile at once (`lay`: the sheet's fps, columns and tile size);
// without a sheet, frames read one by one from the light copy
export function filmstrip(canvas, src, srcIn, rate, seconds, aspect, lay = null) {
  if (lay) {
    sheet(lay.src).then((img) => {
      if (!img) { filmstripByFrames(canvas, src, srcIn, rate, seconds, aspect); return; }
      if (!canvas.isConnected) return;
      const ctx = canvas.getContext('2d');
      const W = canvas.width;
      const H = canvas.height;
      const tile = Math.max(8, Math.round(H * aspect));
      const n = Math.max(1, Math.ceil(W / tile));
      ctx.fillStyle = 'rgba(0,0,0,0.35)';
      ctx.fillRect(0, 0, W, H);
      for (let k = 0; k < n; k += 1) {
        const into = Math.min(seconds, ((k + 0.5) * tile / W) * seconds);
        const f = Math.min(lay.n - 1, Math.max(0, Math.round((srcIn + into * (rate || 1)) * lay.fps)));
        const sx = (f % lay.cols) * lay.w;
        const sy = Math.floor(f / lay.cols) * lay.h;
        const scale = Math.max(tile / lay.w, H / lay.h); // cover the tile around the frame's centre
        const dw = lay.w * scale;
        const dh = lay.h * scale;
        ctx.save();
        ctx.beginPath();
        ctx.rect(k * tile, 0, tile, H);
        ctx.clip();
        ctx.drawImage(img, sx, sy, lay.w, lay.h, k * tile + (tile - dw) / 2, (H - dh) / 2, dw, dh);
        ctx.restore();
        ctx.fillStyle = 'rgba(0,0,0,0.18)';
        ctx.fillRect(k * tile + tile - 1, 0, 1, H);
      }
    });
    return;
  }
  filmstripByFrames(canvas, src, srcIn, rate, seconds, aspect);
}

function filmstripByFrames(canvas, src, srcIn, rate, seconds, aspect) {
  const ctx = canvas.getContext('2d');
  const W = canvas.width;
  const H = canvas.height;
  const tile = Math.max(8, Math.round(H * aspect));
  const n = Math.max(1, Math.ceil(W / tile));
  ctx.fillStyle = 'rgba(0,0,0,0.35)';
  ctx.fillRect(0, 0, W, H);
  if (!src) return;
  for (let k = 0; k < n; k += 1) {
    const into = Math.min(seconds, ((k + 0.5) * tile / W) * seconds);
    frame(src, srcIn + into * (rate || 1)).then((img) => {
      if (!canvas.isConnected) return;
      const sw = img.width;
      const sh = img.height;
      const scale = Math.max(tile / sw, H / sh); // cover the tile around the frame's centre
      const dw = sw * scale;
      const dh = sh * scale;
      ctx.save();
      ctx.beginPath();
      ctx.rect(k * tile, 0, tile, H);
      ctx.clip();
      ctx.drawImage(img, k * tile + (tile - dw) / 2, (H - dh) / 2, dw, dh);
      ctx.restore();
      ctx.fillStyle = 'rgba(0,0,0,0.18)';
      ctx.fillRect(k * tile + tile - 1, 0, 1, H);
    });
  }
}

// sound as a waveform: `peaks` (0-255, `pps` a second) between source seconds `from` and `to`, across the canvas
export function waveform(canvas, peaks, pps, from, to, color, mapper = null) {
  const ctx = canvas.getContext('2d');
  const W = canvas.width;
  const H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  if (!peaks || !peaks.length) return;
  ctx.fillStyle = color;
  const mid = H / 2;
  for (let x = 0; x < W; x += 1) {
    // the source seconds this column covers (a mapper turns film seconds into song seconds for the music)
    let a = from + ((to - from) * x) / W;
    let b = from + ((to - from) * (x + 1)) / W;
    if (mapper) { a = mapper(a); b = mapper(b); if (a === null || b === null) continue; }
    let i0 = Math.floor(Math.min(a, b) * pps);
    let i1 = Math.ceil(Math.max(a, b) * pps);
    if (i1 <= 0 || i0 >= peaks.length) continue;
    i0 = Math.max(0, i0);
    i1 = Math.min(peaks.length, Math.max(i0 + 1, i1));
    let m = 0;
    for (let i = i0; i < i1; i += 1) if (peaks[i] > m) m = peaks[i];
    const h = Math.max(1, (m / 255) * (H * 0.92));
    ctx.fillRect(x, mid - h / 2, 1, h);
  }
}
