// The owner's dashboard: one range filter on top scopes every number and chart below it.
import '../styles/tokens.css';
import '../styles/components.css';
import '../styles/app.css';
import '../styles/admin.css';
import '../styles/extras.css'; // the sky, the light/dark switch, the asking card: the same as every other page
import '../styles/light.css';
import { confirms, themeToggle } from '../app/asks.js';
import { PALETTE, fmt, lineChart, columnChart, stackedColumns, hbars, outcomes, heatmap, sparkline, table, legend } from './charts.js';
import { dismiss } from '../app/ui.js';

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const short = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', timeZone: 'UTC' });
let range = 30;
let data = null;

function delta(host, now, before, upIsGood = true) {
  if (!host) return;
  host.replaceChildren();
  if (!before && !now) return;
  const d = before ? ((now - before) / before) * 100 : 100;
  const up = d >= 0;
  host.className = `kpi-delta ${up === upIsGood ? 'good' : 'bad'}`;
  host.textContent = `${up ? '▲' : '▼'} ${Math.abs(Math.round(d))}% vs previous ${range} days`;
}

function kpis(m) {
  const k = m.kpi;
  const set = (key, value) => { const n = $(`[data-kpi="${key}"]`); if (n) n.textContent = value; };
  set('films', fmt(k.films[0]));
  set('visitors', fmt(k.visitors[0]));
  set('signups', fmt(k.signups[0]));
  set('active', fmt(k.active));
  set('rating', k.rating[2] ? `${k.rating[0].toFixed(1)}` : '–');
  set('rating-n', k.rating[2] ? `${k.rating[2]} rating${k.rating[2] === 1 ? '' : 's'} · asked-for match ${k.match.toFixed(1)}/5` : 'no ratings yet');
  set('spend', fmt(k.spend[0], 'usd'));
  set('render', fmt(k.render_min[0], 'min'));
  const total = k.success[0] + k.success[1];
  set('success', total ? `${Math.round((100 * k.success[0]) / total)}%` : '–');
  set('success-n', total ? `${k.success[0]} finished · ${k.success[1]} failed` : 'no finished steps yet');
  delta($('[data-delta="films"]'), k.films[0], k.films[1]);
  delta($('[data-delta="visitors"]'), k.visitors[0], k.visitors[1]);
  delta($('[data-delta="signups"]'), k.signups[0], k.signups[1]);
  delta($('[data-delta="spend"]'), k.spend[0], k.spend[1], false);
  delta($('[data-delta="render"]'), k.render_min[0], k.render_min[1], false);
  const sp = (key, values) => { const n = $(`[data-spark="${key}"]`); if (n) sparkline(n, values); };
  const hero = $('[data-spark="films"]'); if (hero) sparkline(hero, m.series.films, { w: 360, h: 110, area: true });
  sp('visitors', m.series.visitors);
  sp('signups', m.series.signups);
  sp('render', m.series.render_min);
}

function charts(m) {
  const days = m.days;
  lineChart($('#c-visitors'), { labels: days, values: m.series.visitors, name: 'visitors' });
  table($('#t-visitors'), ['Day', 'Visitors', 'Page views'], days.map((d, i) => [short(d), m.series.visitors[i], m.series.views[i]]));
  columnChart($('#c-signups'), { labels: days, values: m.series.signups, name: 'sign-ups' });
  table($('#t-signups'), ['Day', 'Sign-ups'], days.map((d, i) => [short(d), m.series.signups[i]]));
  columnChart($('#c-films'), { labels: days, values: m.series.films, name: 'films finished' });
  table($('#t-films'), ['Day', 'Films finished', 'Render minutes'], days.map((d, i) => [short(d), m.series.films[i], m.series.render_min[i]]));

  const f = m.funnel;
  const pct = (a, b) => { if (!b) return '0%'; const v = (100 * a) / b; return v < 1 && v > 0 ? `${v.toFixed(1)}%` : `${Math.round(v)}%`; };
  const head = $('#funnel-head');
  head.textContent = `${fmt(f[0].n)} visitors → ${fmt(f[1].n)} new accounts (${pct(f[1].n, f[0].n)} of visitors)`;
  const people = f.slice(1);
  hbars($('#c-funnel'), {
    items: people.map((s, i) => ({ label: s.stage, n: s.n, note: i ? `· ${pct(s.n, people[i - 1].n)}` : '' })),
    colors: PALETTE.ordinal.slice(1),
  });
  table($('#t-funnel'), ['Stage', 'People', 'From the stage before'], f.map((s, i) => [s.stage, s.n, i ? pct(s.n, f[i - 1].n) : '']));

  const series = m.spend.series;
  if (Object.keys(series).length) {
    stackedColumns($('#c-spend'), $('#l-spend'), { labels: days, series, kind: 'usd' });
  } else {
    empty($('#c-spend'), 'No AI spend in this range.'); legend($('#l-spend'), []);
  }
  table($('#t-spend'), ['Day', ...Object.keys(series)], days.map((d, i) => [short(d), ...Object.keys(series).map((k) => fmt(series[k][i], 'usd'))]));

  if (m.outcomes.length) outcomes($('#c-outcomes'), m.outcomes); else empty($('#c-outcomes'), 'No finished steps yet.');
  table($('#t-outcomes'), ['Step', 'Finished', 'Failed'], m.outcomes.map((o) => [o.kind, o.done, o.failed]));

  const hist = m.ratings.hist;
  if (hist.some(Boolean)) columnChart($('#c-ratings'), { labels: hist.map((_, i) => String(i + 1)), values: hist, name: 'ratings', xIsDate: false });
  else empty($('#c-ratings'), 'No ratings in this range.');
  table($('#t-ratings'), ['Rating', 'Films'], hist.map((n, i) => [`${i + 1}/10`, n]));
  const tags = $('#c-tags');
  tags.replaceChildren();
  for (const [t, n] of m.ratings.tags) {
    const chip = document.createElement('span'); chip.className = 'tag-chip';
    const b = document.createElement('b'); b.textContent = String(n);
    chip.append(document.createTextNode(t), b);
    tags.appendChild(chip);
  }
  if (!m.ratings.tags.length) tags.textContent = 'Nothing flagged.';

  heatmap($('#c-cohorts'), m.cohorts);
  table($('#t-cohorts'), ['Joined week', 'People', ...m.cohorts.map((_, k) => (k ? `+${k}` : 'Week 0'))],
    m.cohorts.map((c) => [short(c.week), c.size, ...c.cells.map((v) => (v == null ? '' : `${v}%`))]));

  const tr = m.traffic;
  const totalVisits = m.kpi.visitors[0];
  const bars = (id, items) => (items.length ? hbars($(id), { items, total: totalVisits }) : empty($(id), 'No visits yet.'));
  bars('#c-referrers', tr.referrers);
  bars('#c-pages', tr.pages);
  bars('#c-devices', tr.devices);
  bars('#c-browsers', tr.browsers);
}

function empty(host, text) {
  host.replaceChildren();
  const p = document.createElement('p'); p.className = 'viz-empty'; p.textContent = text; host.appendChild(p);
}

async function load() {
  const root = $('.dash');
  root.classList.add('loading');
  try {
    const r = await fetch(`/admin/api/metrics?days=${range}`, { credentials: 'same-origin' });
    if (!r.ok) throw new Error(String(r.status));
    data = await r.json();
    kpis(data);
    charts(data);
  } catch (err) {
    console.warn('[admin] metrics', err);
  } finally {
    root.classList.remove('loading');
  }
}

// live jobs and health, every 5 seconds
async function live() {
  try {
    const r = await fetch('/admin/api/live', { credentials: 'same-origin' });
    if (!r.ok) return;
    const d = await r.json();
    const box = $('#live');
    box.replaceChildren();
    if (!d.jobs.length) {
      const p = document.createElement('p'); p.className = 'viz-empty'; p.textContent = 'Nothing running. All quiet.'; box.appendChild(p);
    }
    for (const j of d.jobs) {
      const row = document.createElement('div'); row.className = `live-row ${j.status}`;
      const who = document.createElement('span'); who.className = 'live-who'; who.textContent = `${j.who || '–'} · ${j.kind}`;
      const msg = document.createElement('span'); msg.className = 'live-msg'; msg.textContent = j.status === 'running' ? (j.message || 'working') : j.status;
      const meter = document.createElement('span'); meter.className = 'live-meter';
      const fill = document.createElement('i'); fill.style.width = `${Math.round((j.progress || 0) * 100)}%`; meter.appendChild(fill);
      const pct = document.createElement('b'); pct.textContent = j.status === 'running' ? `${Math.round((j.progress || 0) * 100)}%` : '';
      row.append(who, msg, meter, pct);
      box.appendChild(row);
    }
    const h = d.health;
    const set = (k, v) => { const n = $(`[data-health="${k}"]`); if (n) n.textContent = v; };
    set('worker', h.worker_seen == null ? 'never seen' : `seen ${Math.round(h.worker_seen)} s ago`);
    set('queue', `${h.running} running · ${h.queued} queued`);
    set('failed', String(h.failed_24h));
    set('disk', `${h.free_gb} GB free`);
    set('cap', `$${h.month_usd.toFixed(2)} of $${Math.round(h.cap_usd)}`);
    const cap = $('[data-health-meter]');
    if (cap) {
      const f = Math.min(1, h.month_usd / Math.max(0.01, h.cap_usd));
      cap.style.width = `${Math.round(f * 100)}%`;
      cap.className = f > 0.9 ? 'bad' : f > 0.7 ? 'warn' : '';
    }
    const dot = $('[data-health-dot]');
    if (dot) dot.className = `pulse-dot ${h.worker_seen != null && h.worker_seen < 60 ? 'ok' : 'down'}`;
  } catch { /* the next tick tries again */ }
}

function controls() {
  $$('[data-range]').forEach((b) => b.addEventListener('click', () => {
    range = +b.dataset.range;
    $$('[data-range]').forEach((x) => x.setAttribute('aria-pressed', x === b ? 'true' : 'false'));
    $$('[data-period]').forEach((x) => { x.textContent = `last ${range} days`; });
    load();
  }));
  $$('[data-table-toggle]').forEach((b) => b.addEventListener('click', () => {
    const card = b.closest('.viz-card');
    const on = card.classList.toggle('show-table');
    b.textContent = on ? 'Chart' : 'Table';
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  }));
  let resize;
  window.addEventListener('resize', () => { clearTimeout(resize); resize = setTimeout(() => data && charts(data), 150); });
  $$('.toast').forEach(dismiss);
  const link = $('#invite-link');
  if (link) {
    const full = window.location.origin + link.dataset.path;
    link.textContent = `${full}  (tap to copy)`;
    link.addEventListener('click', async () => { try { await navigator.clipboard.writeText(full); link.textContent = `${full}  ✓ copied`; } catch { /* by hand */ } });
  }
}

// people: search and show 20 at a time
function people() {
  const tbody = $('.people-list tbody');
  if (!tbody) return;
  const rows = [...tbody.rows];
  const search = $('#people-search');
  const more = $('#people-more');
  let all = false;
  const apply = () => {
    const q = (search.value || '').trim().toLowerCase();
    let shown = 0;
    rows.forEach((r) => {
      const hit = !q || r.textContent.toLowerCase().includes(q);
      const visible = hit && (all || q || shown < 20);
      r.hidden = !visible;
      if (hit) shown++;
    });
    more.hidden = !!q || all || rows.length <= 20;
    more.textContent = `Show all ${rows.length}`;
  };
  search.addEventListener('input', apply);
  more.addEventListener('click', () => { all = true; apply(); });
  apply();
}

themeToggle();
confirms();
controls();
people();
load();
live();
setInterval(live, 5000);
