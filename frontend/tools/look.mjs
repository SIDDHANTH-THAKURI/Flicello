// Screenshot pages of the local studio: node tools/look.mjs <outdir> <width> <height> <path> [<path>...]
// A path may end with #click=<selector> to click something first.
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';

const [out, w, h, ...paths] = process.argv.slice(2);
const base = process.env.BASE || 'http://127.0.0.1:8766';
fs.mkdirSync(out, { recursive: true });
const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: 'new',
  args: ['--ignore-gpu-blocklist', '--enable-gpu', '--use-angle=d3d11', '--autoplay-policy=no-user-gesture-required'],
  defaultViewport: { width: +w, height: +h, deviceScaleFactor: 1 },
});
const page = await browser.newPage();
const logs = [];
page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') logs.push(`[${m.type()}] ${m.text()}`); });
page.on('pageerror', (e) => logs.push(`[pageerror] ${e.message}`));
let n = 0;
for (const p of paths) {
  const [url, ...acts] = p.split('#');
  await page.goto(base + url, { waitUntil: 'networkidle2', timeout: 60000 }).catch((e) => logs.push(`[goto] ${e.message}`));
  for (const a of acts) {
    const i = a.indexOf('='); const k = a.slice(0, i); const v = a.slice(i + 1);
    if (k === 'click') await page.click(v).catch((e) => logs.push(`[click ${v}] ${e.message}`));
    if (k === 'wait') await new Promise((r) => setTimeout(r, +v));
    if (k === 'theme') await page.evaluate((t) => { document.documentElement.dataset.theme = t; }, v);
  }
  await new Promise((r) => setTimeout(r, 1500));
  const file = path.join(out, `${String(n++).padStart(2, '0')}-${url.replace(/\W+/g, '-').replace(/^-|-$/g, '') || 'home'}.png`);
  await page.screenshot({ path: file, fullPage: process.env.FULL !== '0' });
  console.log('shot', file, 'final url', page.url());
}
if (logs.length) console.log(logs.join('\n'));
await browser.close();
