// Screenshots and frame measurements of the landing page in the local Chrome (headless, real GPU).
//   node tools/shoot.mjs <url> <outdir> [width height]
// For each scroll stop: scroll, render frames by hand (window.__flicello.step), read the WebGL frame back and report
// lit% / blown% / mean, then save a PNG. A hidden page never runs requestAnimationFrame, hence the manual steps.
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';

const [url = 'http://127.0.0.1:8790/', out = 'shots', w = '1600', h = '900'] = process.argv.slice(2);
const W = +w, H = +h;
fs.mkdirSync(out, { recursive: true });

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: 'new',
  args: ['--ignore-gpu-blocklist', '--enable-gpu', '--use-angle=d3d11', '--autoplay-policy=no-user-gesture-required', `--window-size=${W},${H}`],
  defaultViewport: { width: W, height: H, deviceScaleFactor: 1 },
});
const page = await browser.newPage();
const logs = [];
page.on('console', (m) => logs.push(`[${m.type()}] ${m.text()}`));
page.on('pageerror', (e) => logs.push(`[pageerror] ${e.message}`));
await page.goto(url, { waitUntil: 'networkidle0', timeout: 60000 });
await page.waitForFunction(() => window.__flicello && document.getElementById('mosaic').readyState >= 2, { timeout: 30000 });
await new Promise((r) => setTimeout(r, 1500));

const info = await page.evaluate(() => {
  const gl = window.__flicello.gl();
  const ctx = gl && gl.getContext();
  const dbg = ctx && ctx.getExtension('WEBGL_debug_renderer_info');
  return { renderer: dbg ? ctx.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : 'n/a', gl: document.body.dataset.gl, scrollH: document.documentElement.scrollHeight };
});
console.log('renderer:', info.renderer, '| gl:', info.gl, '| page height:', info.scrollH);

// scroll stops: hero, then through each chapter, then the sections below
const stops = (process.env.STOPS || '').split(',').filter(Boolean).map(Number);
const ids = ['#top', '#watch', '#story', '#beat', '#film', '#compare', '#anything', '#you', '#yours', '#start'];
const plan = stops.length ? stops.map((y, i) => ({ name: `y${i}`, y })) : await page.evaluate((ids) => {
  const out = [];
  const vh = window.innerHeight;
  for (const id of ids) {
    const el = document.querySelector(id);
    if (!el) continue;
    const top = el.getBoundingClientRect().top + window.scrollY;
    const hgt = el.offsetHeight;
    if (el.dataset.chapter && +el.dataset.chapter > 0) {
      // entering, pinned at 1/3 and 2/3 and near the end of the action
      for (const f of [0.38, 0.6, 0.82]) out.push({ name: `${id.slice(1)}-${Math.round(f * 100)}`, y: top + (hgt - vh) * f });
    } else out.push({ name: id.slice(1), y: top });
  }
  return out;
}, ids);

const report = [];
for (const stop of plan) {
  await page.evaluate((y) => window.scrollTo(0, y), stop.y);
  await new Promise((r) => setTimeout(r, 450));
  const m = await page.evaluate(() => {
    const f = window.__flicello;
    f.step(8);
    const gl = f.gl();
    if (!gl) return null;
    const ctx = gl.getContext();
    f.step(1);
    const w = ctx.drawingBufferWidth, h = ctx.drawingBufferHeight;
    const px = new Uint8Array(w * h * 4);
    ctx.readPixels(0, 0, w, h, ctx.RGBA, ctx.UNSIGNED_BYTE, px);
    let lit = 0, blown = 0, sum = 0;
    for (let i = 0; i < px.length; i += 4) {
      const v = Math.max(px[i], px[i + 1], px[i + 2]);
      if (v > 12) lit++;
      if (v > 245) blown++;
      sum += (px[i] + px[i + 1] + px[i + 2]) / 3;
    }
    const n = px.length / 4;
    return { lit: +(100 * lit / n).toFixed(1), blown: +(100 * blown / n).toFixed(2), mean: +(sum / n).toFixed(1), morph: +f.state.morph.toFixed(2) };
  });
  const file = path.join(out, `${String(report.length).padStart(2, '0')}-${stop.name}.png`);
  await page.screenshot({ path: file });
  report.push({ stop: stop.name, ...m });
  console.log(stop.name.padEnd(14), JSON.stringify(m));
}
const perf = await page.evaluate(() => { const f = window.__flicello; f.stats(); f.step(60); return f.stats(); });
console.log('ms/frame over 60 hand-stepped frames:', perf.msPerFrame.toFixed(2));
fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify({ info, report, perf, logs }, null, 2));
if (logs.length) console.log('console:\n' + logs.slice(0, 30).join('\n'));
await browser.close();
