// Small SVG charts for the owner's dashboard, built to the dataviz rules: thin marks (bars <= 24px, 4px rounded data
// ends, square at the baseline), 2px lines, hairline solid grid, 2px surface gaps between touching fills, one axis,
// legends for 2+ series, text in text tokens (never the series colour), a crosshair or per-mark tooltip on every
// chart, and a table twin for each. Labels are data: always textContent, never innerHTML.
const NS = 'http://www.w3.org/2000/svg';

export const PALETTE = {
  // validated: reference dark categorical slots on the panel surface #101016 (all six checks pass)
  series: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'],
  accent: '#ff2e7e',              // single-series charts: the brand rose (5.4:1 on the surface)
  // ordinal (funnel): one hue, validated with --ordinal; first stage lightest
  ordinal: ['#ffa7bc', '#ff779e', '#eb5485', '#cf386d', '#b21557', '#900042'],
  // sequential (heatmap): one hue, dark = low, light = high on the dark surface
  seq: ['#5a0027', '#790037', '#990047', '#b61b59', '#cf386d', '#e85082', '#ff6a97', '#ff96b1', '#ffbccb'],
  good: '#0ca30c', critical: '#d03b3b',
  surface: '#101016', grid: '#1f1f28', axis: '#2c2c38', muted: '#8b8996', text2: '#c3c0cc', text1: '#f4f1ea',
};

const el = (name, attrs = {}, parent) => {
  const n = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  if (parent) parent.appendChild(n);
  return n;
};
const fmt = (v, kind) => {
  if (v == null || Number.isNaN(v)) return '–';
  if (kind === 'usd') return `$${v < 10 ? v.toFixed(2) : Math.round(v).toLocaleString('en-AU')}`;
  if (kind === 'min') return `${v < 10 ? v.toFixed(1) : Math.round(v).toLocaleString('en-AU')} min`;
  if (kind === 'pct') return `${Math.round(v)}%`;
  if (v >= 10000) return `${(v / 1000).toFixed(1)}K`;
  return Math.round(v * 100) / 100 === Math.round(v) ? Math.round(v).toLocaleString('en-AU') : v.toFixed(1);
};
export { fmt };
const short = (iso) => { const d = new Date(`${iso}T00:00:00Z`); return d.toLocaleDateString('en-AU', { day: 'numeric', month: 'short', timeZone: 'UTC' }); };

// clean axis ticks: 0, then round steps
function ticks(max, count = 4) {
  if (max <= 0) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const top = Math.ceil(max / step) * step;
  const out = [];
  for (let v = 0; v <= top + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}

// one tooltip for the whole page
let tip;
function tooltip() {
  if (tip) return tip;
  tip = document.createElement('div');
  tip.className = 'viz-tip';
  tip.setAttribute('role', 'status');
  document.body.appendChild(tip);
  return tip;
}
function showTip(x, y, title, rows) {
  const t = tooltip();
  t.replaceChildren();
  const h = document.createElement('div'); h.className = 'viz-tip-title'; h.textContent = title; t.appendChild(h);
  for (const r of rows) {
    const row = document.createElement('div'); row.className = 'viz-tip-row';
    const key = document.createElement('span'); key.className = 'viz-key'; key.style.background = r.color || PALETTE.accent;
    const v = document.createElement('b'); v.textContent = r.value;
    const l = document.createElement('span'); l.textContent = r.label || '';
    row.append(key, v, l); t.appendChild(row);
  }
  t.classList.add('on');
  const w = t.offsetWidth, hgt = t.offsetHeight;
  t.style.left = `${Math.min(window.innerWidth - w - 12, Math.max(12, x + 14))}px`;
  t.style.top = `${Math.max(12, y - hgt - 12)}px`;
}
const hideTip = () => tip && tip.classList.remove('on');

function frame(host, height) {
  host.replaceChildren();
  const w = Math.max(240, host.clientWidth);
  const svg = el('svg', { viewBox: `0 0 ${w} ${height}`, width: w, height, role: 'img' }, host);
  return { svg, w, h: height };
}

function yAxis(svg, { left, top, width, height, max, kind }) {
  const t = ticks(max);
  const top_ = t[t.length - 1] || 1;
  const g = el('g', { class: 'viz-axis' }, svg);
  for (const v of t) {
    const y = top + height - (v / top_) * height;
    el('line', { x1: left, x2: left + width, y1: y, y2: y, stroke: v === 0 ? PALETTE.axis : PALETTE.grid, 'stroke-width': 1 }, g);
    const label = el('text', { x: left - 8, y: y + 4, 'text-anchor': 'end', class: 'viz-tick' }, g);
    label.textContent = fmt(v, kind);
  }
  return top_;
}

function xLabels(svg, labels, xs, y) {
  const g = el('g', {}, svg);
  const every = Math.ceil(labels.length / 6);
  const keep = labels.map((_, i) => i % every === 0);
  const last = labels.length - 1;
  // the last day always gets a label; the regular one before it gives way if they'd touch
  for (let i = last - 1; i >= 0; i--) { if (keep[i]) { if (xs[last] - xs[i] < 56) keep[i] = false; break; } }
  keep[last] = true;
  labels.forEach((d, i) => {
    if (!keep[i]) return;
    const t = el('text', { x: xs[i], y, 'text-anchor': i === last ? 'end' : 'middle', class: 'viz-tick' }, g);
    t.textContent = short(d);
  });
}

// ---------------------------------------------------------------- line (+ soft area) with a crosshair
export function lineChart(host, { labels, values, kind, color = PALETTE.accent, name }) {
  const { svg, w, h } = frame(host, 220);
  const L = 48, R = 16, T = 14, B = 28;
  const pw = w - L - R, ph = h - T - B;
  const top = yAxis(svg, { left: L, top: T, width: pw, height: ph, max: Math.max(...values, 1), kind });
  const xs = values.map((_, i) => L + (values.length === 1 ? pw / 2 : (i / (values.length - 1)) * pw));
  const ys = values.map((v) => T + ph - (v / top) * ph);
  const d = xs.map((x, i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${ys[i].toFixed(1)}`).join('');
  el('path', { d: `${d}L${xs[xs.length - 1]},${T + ph}L${xs[0]},${T + ph}Z`, fill: color, 'fill-opacity': 0.1 }, svg);
  el('path', { d, fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, svg);
  const last = values.length - 1;
  el('circle', { cx: xs[last], cy: ys[last], r: 4, fill: color, stroke: PALETTE.surface, 'stroke-width': 2 }, svg);
  const endLabel = el('text', { x: xs[last] - 8, y: ys[last] - 10, 'text-anchor': 'end', class: 'viz-label' }, svg);
  endLabel.textContent = fmt(values[last], kind);
  xLabels(svg, labels, xs, h - 8);
  crosshair(svg, { xs, top: T, height: ph, onAt: (i, ev) => showTip(ev.clientX, ev.clientY, short(labels[i]), [{ value: fmt(values[i], kind), label: name, color }]) });
}

function crosshair(svg, { xs, top, height, onAt }) {
  const line = el('line', { y1: top, y2: top + height, stroke: PALETTE.text2, 'stroke-width': 1, opacity: 0 }, svg);
  const hit = el('rect', { x: xs[0] - 12, y: top, width: xs[xs.length - 1] - xs[0] + 24, height, fill: 'transparent' }, svg);
  const at = (ev) => {
    const r = svg.getBoundingClientRect();
    const x = ((ev.clientX - r.left) / r.width) * svg.viewBox.baseVal.width;
    let best = 0;
    xs.forEach((v, i) => { if (Math.abs(v - x) < Math.abs(xs[best] - x)) best = i; });
    line.setAttribute('x1', xs[best]); line.setAttribute('x2', xs[best]); line.setAttribute('opacity', 0.5);
    onAt(best, ev);
  };
  hit.addEventListener('pointermove', at);
  hit.addEventListener('pointerleave', () => { line.setAttribute('opacity', 0); hideTip(); });
}

// a column with a 4px rounded data end and a square baseline
function column(parent, x, y, w, h, color, extra = {}) {
  if (h <= 0.5) return null;
  const r = Math.min(4, w / 2, h);
  const d = `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  return el('path', { d, fill: color, ...extra }, parent);
}

// ---------------------------------------------------------------- columns (one series), crosshair readout
export function columnChart(host, { labels, values, kind, color = PALETTE.accent, name, xIsDate = true }) {
  const { svg, w, h } = frame(host, 220);
  const L = 48, R = 12, T = 14, B = 28;
  const pw = w - L - R, ph = h - T - B;
  const top = yAxis(svg, { left: L, top: T, width: pw, height: ph, max: Math.max(...values, 1), kind });
  const band = pw / values.length;
  const bw = Math.min(24, Math.max(2, band - 2));
  const xs = values.map((_, i) => L + band * i + band / 2);
  values.forEach((v, i) => column(svg, xs[i] - bw / 2, T + ph - (v / top) * ph, bw, (v / top) * ph, color));
  if (xIsDate) xLabels(svg, labels, xs, h - 8);
  else labels.forEach((l, i) => { const t = el('text', { x: xs[i], y: h - 8, 'text-anchor': 'middle', class: 'viz-tick' }, svg); t.textContent = l; });
  const maxI = values.indexOf(Math.max(...values));
  if (values[maxI] > 0) {
    const lab = el('text', { x: xs[maxI], y: T + ph - (values[maxI] / top) * ph - 6, 'text-anchor': 'middle', class: 'viz-label' }, svg);
    lab.textContent = fmt(values[maxI], kind);
  }
  crosshair(svg, { xs, top: T, height: ph, onAt: (i, ev) => showTip(ev.clientX, ev.clientY, xIsDate ? short(labels[i]) : String(labels[i]), [{ value: fmt(values[i], kind), label: name, color }]) });
}

// ---------------------------------------------------------------- stacked columns (categorical), legend + readout
export function stackedColumns(host, legendHost, { labels, series, kind }) {
  const names = Object.keys(series);
  const colors = Object.fromEntries(names.map((n, i) => [n, PALETTE.series[i]]));
  const { svg, w, h } = frame(host, 240);
  const L = 52, R = 12, T = 14, B = 28;
  const pw = w - L - R, ph = h - T - B;
  const totals = labels.map((_, i) => names.reduce((a, n) => a + (series[n][i] || 0), 0));
  const top = yAxis(svg, { left: L, top: T, width: pw, height: ph, max: Math.max(...totals, kind === 'usd' ? 0.01 : 1), kind });
  const band = pw / labels.length;
  const bw = Math.min(24, Math.max(2, band - 2));
  const xs = labels.map((_, i) => L + band * i + band / 2);
  labels.forEach((_, i) => {
    let y = T + ph;
    names.forEach((n, k) => {
      const v = series[n][i] || 0;
      if (!v) return;
      const hh = (v / top) * ph;
      const isTop = names.slice(k + 1).every((m) => !(series[m][i] || 0));
      const gap = 2;
      if (isTop) column(svg, xs[i] - bw / 2, y - hh, bw, Math.max(0, hh - (y < T + ph ? gap : 0)), colors[n]);
      else el('rect', { x: xs[i] - bw / 2, y: y - hh + (y < T + ph ? 0 : 0), width: bw, height: Math.max(0, hh - gap), fill: colors[n] }, svg);
      y -= hh;
    });
  });
  xLabels(svg, labels, xs, h - 8);
  crosshair(svg, { xs, top: T, height: ph, onAt: (i, ev) => showTip(ev.clientX, ev.clientY, short(labels[i]),
    [{ value: fmt(totals[i], kind), label: 'total', color: PALETTE.text2 }, ...names.map((n) => ({ value: fmt(series[n][i] || 0, kind), label: n, color: colors[n] }))]) });
  legend(legendHost, names.map((n) => ({ label: n, color: colors[n], shape: 'rect' })));
}

export function legend(host, items) {
  if (!host) return;
  host.replaceChildren();
  if (items.length < 2) return;
  for (const it of items) {
    const s = document.createElement('span');
    s.className = 'viz-legend-item';
    const key = document.createElement('i');
    key.className = it.shape === 'line' ? 'viz-key line' : 'viz-key';
    key.style.background = it.color;
    const t = document.createElement('span');
    t.textContent = it.label;
    s.append(key, t);
    host.appendChild(s);
  }
}

// ---------------------------------------------------------------- horizontal bars (one series or ordinal)
export function hbars(host, { items, colors, kind, total }) {
  host.replaceChildren();
  const max = Math.max(...items.map((x) => x.n), 1);
  const list = document.createElement('div');
  list.className = 'viz-hbars';
  items.forEach((it, i) => {
    const row = document.createElement('div');
    row.className = 'viz-hbar';
    row.tabIndex = 0;
    const label = document.createElement('span');
    label.className = 'viz-hbar-label';
    label.textContent = it.label;
    const track = document.createElement('span');
    track.className = 'viz-hbar-track';
    const bar = document.createElement('span');
    bar.className = 'viz-hbar-fill';
    bar.style.width = `${Math.max(it.n ? 1.5 : 0, (it.n / max) * 100)}%`;
    bar.style.background = Array.isArray(colors) ? colors[Math.min(i, colors.length - 1)] : (colors || PALETTE.accent);
    track.appendChild(bar);
    const value = document.createElement('b');
    value.className = 'viz-hbar-value';
    value.textContent = fmt(it.n, kind) + (it.note ? ` ${it.note}` : '');
    row.append(label, track, value);
    const show = (ev) => {
      const r = row.getBoundingClientRect();
      const rows = [{ value: fmt(it.n, kind), label: it.label, color: bar.style.background }];
      if (total) rows.push({ value: `${Math.round((100 * it.n) / Math.max(1, total))}%`, label: 'of all', color: PALETTE.text2 });
      showTip(ev.clientX || r.right, ev.clientY || r.top, it.label, rows);
    };
    row.addEventListener('pointermove', show);
    row.addEventListener('focus', show);
    row.addEventListener('pointerleave', hideTip);
    row.addEventListener('blur', hideTip);
    list.appendChild(row);
  });
  host.appendChild(list);
}

// ---------------------------------------------------------------- done vs failed per step (status colours + labels)
export function outcomes(host, rows) {
  host.replaceChildren();
  const max = Math.max(...rows.map((r) => r.done + r.failed), 1);
  const list = document.createElement('div');
  list.className = 'viz-hbars';
  for (const r of rows) {
    const row = document.createElement('div');
    row.className = 'viz-hbar';
    row.tabIndex = 0;
    const label = document.createElement('span'); label.className = 'viz-hbar-label'; label.textContent = r.kind;
    const track = document.createElement('span'); track.className = 'viz-hbar-track split';
    const ok = document.createElement('span'); ok.className = 'viz-hbar-fill'; ok.style.width = `${(r.done / max) * 100}%`; ok.style.background = PALETTE.good;
    const bad = document.createElement('span'); bad.className = 'viz-hbar-fill'; bad.style.width = `${(r.failed / max) * 100}%`; bad.style.background = PALETTE.critical;
    track.append(ok, bad);
    const value = document.createElement('b'); value.className = 'viz-hbar-value';
    const rate = r.done + r.failed ? Math.round((100 * r.done) / (r.done + r.failed)) : 0;
    value.textContent = `✓ ${r.done} · ✕ ${r.failed} · ${rate}%`;
    row.append(label, track, value);
    const show = (ev) => showTip(ev.clientX || 0, ev.clientY || 0, r.kind, [
      { value: String(r.done), label: 'finished', color: PALETTE.good }, { value: String(r.failed), label: 'failed', color: PALETTE.critical }]);
    row.addEventListener('pointermove', show); row.addEventListener('focus', show);
    row.addEventListener('pointerleave', hideTip); row.addEventListener('blur', hideTip);
    list.appendChild(row);
  }
  host.appendChild(list);
}

// ---------------------------------------------------------------- retention heatmap (sequential, one hue)
export function heatmap(host, cohorts) {
  host.replaceChildren();
  const table = document.createElement('div');
  table.className = 'viz-heat';
  const weeks = Math.max(...cohorts.map((c) => c.cells.length), 1);
  const head = document.createElement('div'); head.className = 'viz-heat-row head';
  const corner = document.createElement('span'); corner.textContent = 'Joined week'; head.appendChild(corner);
  const n = document.createElement('span'); n.textContent = 'People'; head.appendChild(n);
  for (let k = 0; k < weeks; k++) { const s = document.createElement('span'); s.textContent = k === 0 ? 'Week 0' : `+${k}`; head.appendChild(s); }
  table.appendChild(head);
  for (const c of cohorts) {
    const row = document.createElement('div'); row.className = 'viz-heat-row';
    const wk = document.createElement('span'); wk.className = 'viz-heat-week'; wk.textContent = short(c.week); row.appendChild(wk);
    const size = document.createElement('span'); size.className = 'viz-heat-size'; size.textContent = String(c.size); row.appendChild(size);
    for (let k = 0; k < weeks; k++) {
      const cell = document.createElement('span');
      cell.className = 'viz-heat-cell';
      const v = c.cells[k];
      if (v == null || !c.size) { cell.classList.add('none'); row.appendChild(cell); continue; }
      const step = Math.min(PALETTE.seq.length - 1, Math.round((v / 100) * (PALETTE.seq.length - 1)));
      cell.style.background = PALETTE.seq[step];
      cell.textContent = `${v}%`;
      cell.style.color = step >= 5 ? '#1a0610' : '#fff3f6';
      cell.tabIndex = 0;
      const show = (ev) => showTip(ev.clientX || 0, ev.clientY || 0, `Joined week of ${short(c.week)}`, [{ value: `${v}%`, label: k === 0 ? 'active in their first week' : `active ${k} week${k > 1 ? 's' : ''} later`, color: PALETTE.seq[step] }]);
      cell.addEventListener('pointermove', show); cell.addEventListener('focus', show);
      cell.addEventListener('pointerleave', hideTip); cell.addEventListener('blur', hideTip);
      row.appendChild(cell);
    }
    table.appendChild(row);
  }
  host.appendChild(table);
}

// ---------------------------------------------------------------- sparkline (de-emphasis, current period in the accent)
export function sparkline(host, values, { w = 120, h = 32, area = false } = {}) {
  host.replaceChildren();
  const svg = el('svg', { viewBox: `0 0 ${w} ${h}`, width: '100%', height: h, preserveAspectRatio: 'none', 'aria-hidden': 'true' }, host);
  const max = Math.max(...values, 1);
  const pts = values.map((v, i) => [(i / Math.max(1, values.length - 1)) * (w - 6) + 3, h - 4 - (v / max) * (h - 8)]);
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('');
  if (area) el('path', { d: `${d}L${pts[pts.length - 1][0]},${h}L${pts[0][0]},${h}Z`, fill: PALETTE.accent, 'fill-opacity': 0.1 }, svg);
  el('path', { d, fill: 'none', stroke: area ? PALETTE.accent : PALETTE.muted, 'stroke-width': area ? 2 : 1.5, 'stroke-linejoin': 'round', 'vector-effect': 'non-scaling-stroke' }, svg);
  if (!area) {
    const last = pts[pts.length - 1];
    el('circle', { cx: last[0], cy: last[1], r: 3, fill: PALETTE.accent, stroke: PALETTE.surface, 'stroke-width': 2 }, svg);
  }
}

// ---------------------------------------------------------------- table twin of a chart
export function table(host, columns, rows) {
  host.replaceChildren();
  const t = document.createElement('table');
  t.className = 'viz-table';
  const hr = document.createElement('tr');
  columns.forEach((c) => { const th = document.createElement('th'); th.textContent = c; hr.appendChild(th); });
  const thead = document.createElement('thead'); thead.appendChild(hr); t.appendChild(thead);
  const tb = document.createElement('tbody');
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    r.forEach((v) => { const td = document.createElement('td'); td.textContent = v; tr.appendChild(td); });
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  host.appendChild(t);
}
