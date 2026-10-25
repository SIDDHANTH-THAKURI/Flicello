// The editor: the film on a timeline, changed by hand. Every change is an exact edit (studio/handedit.py), made at
// once as the draft that plays above; undo and redo walk the drafts; Make it renders the draft as a version. What
// Cello and the work agent change shows up here the same way (the page reloads when they do).

import '../styles/tokens.css';
import '../styles/studio.css'; // the player's own styles
import '../styles/light.css';
import './editor.css';
import { keyedAt, Player } from '../app/player.js';
import { clock, Timeline } from './timeline.js';
import { inspect } from './inspector.js';

const $ = (s, root = document) => root.querySelector(s);
const el = (tag, cls, parent, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  if (parent) parent.append(n);
  return n;
};
const csrf = () => ($('meta[name="csrf"]') || {}).content || '';
const KEEP = (pid) => `flicello-editor-${pid}`; // the zoom and the playhead survive a reload

class Editor {
  constructor(pid) {
    this.pid = pid;
    this.view = null;
    this.player = null;
    this.t = 0;
    this.sel = null;
    this.ripple = true;
    this.snapping = true;
    this.busy = false;
    this.reframing = false;
    this.timeline = new Timeline(this);
    this.tools();
    this.keys();
    for (const b of document.querySelectorAll('[data-act]')) b.addEventListener('click', () => this.act(b.dataset.act));
    const kept = JSON.parse(sessionStorage.getItem(KEEP(pid)) || '{}');
    this.t = kept.t || 0;
    this.keptZoom = kept.pps || 0;
    window.addEventListener('beforeunload', () => {
      sessionStorage.setItem(KEEP(pid), JSON.stringify({ t: this.t, pps: this.timeline.fitted ? 0 : this.timeline.pps }));
    });
    this.load();
  }

  async call(method, path, body) {
    const r = await fetch(path, {
      method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF': csrf(), Accept: 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (r.status === 401) { window.location.href = '/signin'; throw new Error('signed out'); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || `The server said ${r.status}.`);
    return j;
  }

  async load() {
    try {
      this.show(await this.call('GET', `/api/p/${this.pid}/editor`));
    } catch (e) {
      this.status(e.message);
      $('[data-player]').textContent = e.message;
    }
  }

  // a new view from the server: the timeline, the panel and the player all follow it
  show(view) {
    const first = !this.view;
    this.view = view;
    if (first && this.keptZoom) { this.timeline.pps = this.keptZoom; this.timeline.fitted = false; }
    if (this.sel && this.sel.type === 'shot' && !view.shots.some((s) => s.n === this.sel.n)) this.sel = null;
    if (this.sel && this.sel.type === 'graphic' && !view.graphics.some((g) => g.index === this.sel.index)) this.sel = null;
    if (this.sel && this.sel.type === 'over' && !view.over.some((o) => o.index === this.sel.index)) this.sel = null;
    if (this.sel && this.sel.type === 'pick') this.sel = null; // picked: back to what it made
    this.t = Math.min(this.t, Math.max(0, view.duration - 0.01));
    const root = $('[data-player]');
    const was = this.player;
    const playing = was && was.playing;
    if (was) was.destroy();
    this.player = new Player(root, view.play);
    root.player = this.player; // Cello's play tool drives it
    this.player.onTick = (t) => { this.t = t; this.timeline.setTime(t); this.clock(); };
    this.player.seek(this.t);
    if (playing) this.player.play();
    this.reframeLayer();
    if (this.timeline.fitted || !this.timeline.pps) this.timeline.fit(); else this.timeline.render();
    inspect(this);
    this.header();
    if (view.busy) this.watchBusy();
  }

  header() {
    const v = this.view;
    const n = v.said.length;
    this.status(v.which === 'draft' ? `Draft on version ${v.version} · ${n} change${n === 1 ? '' : 's'}`
      : `Version ${v.version} · nothing changed yet`);
    const set = (act, on) => { const b = $(`[data-act="${act}"]`); if (b) b.disabled = !on || this.busy; };
    set('undo', v.undo);
    set('redo', v.redo);
    set('discard', v.which === 'draft');
    set('make', v.which === 'draft');
  }

  status(text) { $('[data-status]').textContent = text; }

  toast(text, bad = false) {
    const box = $('[data-toasts]');
    const t = el('div', `ed-toast${bad ? ' bad' : ''}`, box, text);
    setTimeout(() => t.classList.add('out'), bad ? 6000 : 3200);
    setTimeout(() => t.remove(), bad ? 6600 : 3800);
  }

  setBusy(on) {
    this.busy = on;
    document.body.classList.toggle('ed-busy', on);
    if (this.view) this.header();
  }

  // exact edits: made at once as the draft; the server answers with the new view. Edits made while one is on its way
  // (a drag right after a scroll) wait their turn and go together.
  async edit(ops, said) {
    if (this.busy) {
      this.queued = { ops: [...((this.queued && this.queued.ops) || []), ...ops], said };
      return;
    }
    this.setBusy(true);
    try {
      const view = await this.call('POST', `/api/p/${this.pid}/editor`, { ops, said });
      this.show(view);
      const last = view.said[view.said.length - 1];
      if (last) this.toast(last);
    } catch (e) {
      this.toast(e.message, true);
      this.timeline.render(); // back to how it is
    } finally {
      this.setBusy(false);
      const next = this.queued;
      this.queued = null;
      if (next) this.edit(next.ops, next.said);
    }
  }

  // the music's edit, with these parts changed (`whole`: exactly this)
  editMusic(part, whole = false) {
    const m = this.view.music;
    if (!m) return;
    const now = { slide: 0, gain_db: 0, fade_in: null, fade_out: null, duck_db: null, points: [], jumps: [], ...(m.edit || {}) };
    this.edit([{ op: 'music', music: whole ? part : { ...now, ...part } }]);
  }

  async remakeMusic(start, end, want) {
    if (!want.trim()) { this.toast('Say how this part should sound.', true); return; }
    try {
      await this.call('POST', `/api/p/${this.pid}/editor/remake`, { start, end, want });
      this.toast('Writing that part of the music again; the film is made again on it (a few minutes).');
      this.watchBusy();
    } catch (e) {
      this.toast(e.message, true);
    }
  }

  async act(what) {
    if (what === 'keys') { $('[data-keys-dialog]').showModal(); return; }
    if (this.busy) return;
    this.setBusy(true);
    try {
      const out = await this.call('POST', `/api/p/${this.pid}/editor/${what}`);
      if (what === 'make') {
        this.toast('Making it: the draft is being rendered as a new version.');
        this.watchBusy();
      } else {
        this.show(out);
      }
    } catch (e) {
      this.toast(e.message, true);
    } finally {
      this.setBusy(false);
    }
  }

  // a step running on the film (a render, music made again): wait, then show what it made
  watchBusy() {
    if (this.watching) return;
    this.watching = true;
    document.body.classList.add('ed-making');
    this.status('Making it…');
    const tick = async () => {
      try {
        const st = await this.call('GET', `/api/p/${this.pid}/status`);
        if (st.job) { this.status(`Making it… ${st.message || ''}`.trim()); setTimeout(tick, 2500); return; }
      } catch { setTimeout(tick, 4000); return; }
      this.watching = false;
      document.body.classList.remove('ed-making');
      this.load();
    };
    setTimeout(tick, 2000);
  }

  select(sel) {
    this.sel = sel;
    this.timeline.showSelection();
    inspect(this);
    this.reframeLayer();
  }

  // ---------------------------------------------------------------- reframing on the picture (keyframes)
  setReframing(on) {
    this.reframing = on;
    this.reframeLayer();
  }

  // the framing [zoom, x, y] the selected shot's keys give at the playhead (null: it has none)
  keyedNow(s) { return keyedAt(s.keys, this.t - s.start); }

  reframeLayer() {
    const stage = document.querySelector('.ed-stage .pv-stage');
    if (!stage) return;
    let layer = stage.querySelector('.ed-reframe');
    const shot = this.sel && this.sel.type === 'shot' && this.view && this.view.shots.find((s) => s.n === this.sel.n);
    if (!this.reframing || !shot) { if (layer) layer.remove(); return; }
    if (!layer) {
      layer = el('div', 'ed-reframe', stage);
      el('span', 'ed-reframe-tip', layer, 'Drag to move · scroll to zoom · at the playhead');
    }
    layer.onpointerdown = (e) => this.reframeDrag(e, shot, stage);
    layer.onwheel = (e) => this.reframeWheel(e, shot);
  }

  // the key at the playhead (made there if there isn't one), changed by `change`, shown at once, sent when done
  reframeKey(shot, change) {
    const fps = this.view.fps || 30;
    const into = Math.min(shot.end - shot.start, Math.max(0, this.t - shot.start));
    const live = this.player.tl.shots.find((x) => x.n === shot.n); // the framing as it is now (still being sent too)
    const keys = ((live && live.keys) || shot.keys || []).map((k) => [...k]);
    let k = keys.findIndex((x) => Math.abs(x[0] - into) < 1 / fps);
    if (k < 0) {
      keys.push([+into.toFixed(3), ...(keyedAt(keys, into) || [1, 0.5, 0.5])]);
      keys.sort((a, b) => a[0] - b[0]);
      k = keys.findIndex((x) => Math.abs(x[0] - into) < 1e-6);
    }
    change(keys[k]);
    keys[k][1] = Math.min(3, Math.max(1, keys[k][1]));
    keys[k][2] = Math.min(1, Math.max(0, keys[k][2]));
    keys[k][3] = Math.min(1, Math.max(0, keys[k][3]));
    if (live) live.keys = keys; // the preview, live
    this.player.draw();
    return keys;
  }

  reframeDrag(e, shot, stage) {
    e.preventDefault();
    const W = stage.clientWidth;
    const H = stage.clientHeight;
    let last = [e.clientX, e.clientY];
    let keys = null;
    const move = (ev) => {
      const dx = ev.clientX - last[0];
      const dy = ev.clientY - last[1];
      last = [ev.clientX, ev.clientY];
      keys = this.reframeKey(shot, (k) => { k[2] -= dx / (W * k[1]); k[3] -= dy / (H * k[1]); });
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      if (keys) this.edit([{ op: 'keys', shot: shot.n, keys: keys.map((k) => k.map((x) => +x.toFixed(4))) }]);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  reframeWheel(e, shot) {
    e.preventDefault();
    const keys = this.reframeKey(shot, (k) => { k[1] *= Math.exp(-e.deltaY * 0.0015); });
    clearTimeout(this.wheelSend);
    this.wheelSend = setTimeout(() => this.edit([{ op: 'keys', shot: shot.n, keys: keys.map((k) => k.map((x) => +x.toFixed(4))) }]), 450);
  }

  seek(t) {
    this.t = Math.max(0, Math.min(t, this.view ? this.view.duration - 0.01 : 0));
    if (this.player) this.player.seek(this.t);
    this.timeline.setTime(this.t);
    this.clock();
  }

  seekIfOutside(s) { if (this.t < s.start || this.t >= s.end) this.seek(s.start + 0.01); }

  clock() {
    const c = $('[data-clock]');
    if (c && this.view) c.textContent = `${clock(this.t, true)} / ${clock(this.view.duration, true)}`;
  }

  shotAtPlayhead() {
    return this.view.shots.find((s) => this.t >= s.start && this.t < s.end) || this.view.shots[this.view.shots.length - 1];
  }

  // ---------------------------------------------------------------- the tool row
  tools() {
    const bar = $('[data-tools]');
    const toggle = (label, title, get, set) => {
      const b = el('button', 'ed-tool toggle', bar, label);
      b.type = 'button';
      b.title = title;
      const draw = () => b.classList.toggle('on', get());
      b.addEventListener('click', () => { set(!get()); draw(); });
      draw();
      return { draw };
    };
    this.rippleTool = toggle('Ripple', 'Ripple (R): what follows a trim or a delete moves with it. Off: the neighbour takes it up.',
      () => this.ripple, (x) => { this.ripple = x; });
    this.snapTool = toggle('Snap', 'Snap (N): to cuts, bars, beats, words, graphics and the playhead',
      () => this.snapping, (x) => { this.snapping = x; });
    el('span', 'ed-sep', bar);
    const tool = (label, title, act) => {
      const b = el('button', 'ed-tool', bar, label);
      b.type = 'button';
      b.title = title;
      b.addEventListener('click', act);
    };
    tool('Split', 'Split the shot at the playhead (S)', () => this.split());
    tool('Delete', 'Delete what is selected (Delete)', () => this.remove());
    tool('Lay over', 'Lay a shot over the picture at the playhead (B-roll, a cutaway): the sound goes on under it (O)',
      () => this.select({ type: 'pick', index: -1 }));
    el('span', 'ed-sep', bar);
    tool('−', 'Zoom out (−)', () => this.timeline.zoom(1 / 1.5));
    tool('+', 'Zoom in (+)', () => this.timeline.zoom(1.5));
    tool('Whole film', 'Fit the whole film (0)', () => this.timeline.fit());
    el('span', 'ed-gap', bar);
    el('span', 'ed-clock mono', bar).dataset.clock = '';
  }

  split() {
    const s = this.shotAtPlayhead();
    if (!s) return;
    const into = this.t - s.start;
    if (into < 0.25 || s.end - this.t < 0.25) { this.toast('Put the playhead inside a shot (not right at its ends) to split it.'); return; }
    this.edit([{ op: 'split', shot: s.n, seconds: +into.toFixed(3) }]);
  }

  remove() {
    const sel = this.sel;
    if (!sel) { this.toast('Select a shot or a graphic first.'); return; }
    if (sel.type === 'shot') this.edit([{ op: 'delete', shot: sel.n, ripple: this.ripple }]);
    else if (sel.type === 'graphic') this.edit([{ op: 'graphic', index: sel.index, remove: true }]);
    else if (sel.type === 'over') this.edit([{ op: 'over', index: sel.index, remove: true }]);
  }

  trimTo(edge) { // Q / W: the shot under the playhead loses everything before (or after) it
    const s = this.shotAtPlayhead();
    if (!s) return;
    const d = edge === 'start' ? -(this.t - s.start) : -(s.end - this.t);
    if (Math.abs(d) < 0.02 || (s.end - s.start) + d < 0.25) return;
    this.edit([{ op: 'trim', shot: s.n, edge, seconds: +d.toFixed(3), ripple: this.ripple }]);
    if (edge === 'start' && this.ripple) this.t = s.start;
  }

  // ---------------------------------------------------------------- keys
  keys() {
    window.addEventListener('keydown', (e) => {
      if (e.target.closest('input, textarea, select, dialog')) return;
      const k = e.key;
      const mod = e.ctrlKey || e.metaKey;
      const fps = (this.view && this.view.fps) || 30;
      const p = this.player;
      if (mod && k.toLowerCase() === 'z') { e.preventDefault(); this.act(e.shiftKey ? 'redo' : 'undo'); return; }
      if (mod && k.toLowerCase() === 'y') { e.preventDefault(); this.act('redo'); return; }
      if (mod) return;
      if (k === ' ' || k === 'k' || k === 'K') { e.preventDefault(); if (p) { if (k === ' ') p.toggle(); else p.pause(); } return; }
      if (k === 'l' || k === 'L') { if (p) p.play(); return; }
      if (k === 'j' || k === 'J') { if (p) p.pause(); this.seek(this.t - 2); return; }
      if (k === 'ArrowLeft' || k === 'ArrowRight') {
        e.preventDefault();
        const dir = k === 'ArrowRight' ? 1 : -1;
        if (e.shiftKey && this.view) {
          const cuts = this.timeline.cuts();
          const to = dir > 0 ? cuts.find((c) => c > this.t + 1e-3) : [...cuts].reverse().find((c) => c < this.t - 1e-3);
          if (to !== undefined) this.seek(to);
        } else this.seek(this.t + dir / fps);
        return;
      }
      if (k === 'Home') { this.seek(0); return; }
      if (k === 'End' && this.view) { this.seek(this.view.duration); return; }
      if (k === 's' || k === 'S') { this.split(); return; }
      if (k === 'o' || k === 'O') { this.select({ type: 'pick', index: -1 }); return; }
      if (k === 'q' || k === 'Q') { this.trimTo('start'); return; }
      if (k === 'w' || k === 'W') { this.trimTo('end'); return; }
      if (k === 'Delete' || k === 'Backspace') { e.preventDefault(); this.remove(); return; }
      if (k === '+' || k === '=') { this.timeline.zoom(1.5); return; }
      if (k === '-' || k === '_') { this.timeline.zoom(1 / 1.5); return; }
      if (k === '0') { this.timeline.fit(); return; }
      if (k === 'r' || k === 'R') { this.ripple = !this.ripple; this.rippleTool.draw(); this.toast(this.ripple ? 'Ripple on' : 'Ripple off: the neighbour takes up a trim'); return; }
      if (k === 'n' || k === 'N') { this.snapping = !this.snapping; this.snapTool.draw(); this.toast(this.snapping ? 'Snapping on' : 'Snapping off'); return; }
      if (k === 'Escape') { this.timeline.range = null; this.timeline.drawRange(); this.select(null); return; }
      if (k === '?') { $('[data-keys-dialog]').showModal(); }
    });
  }
}

const page = document.querySelector('[data-editor]');
if (page) window.editor = new Editor(page.dataset.editor);
