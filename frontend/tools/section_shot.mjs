// Screenshot one part of a page at several screen sizes, to check a layout after copy or CSS changes.
//   node tools/section_shot.mjs <url> <selector> <outdir> 1440x900 1024x768 390x844
// For each size: scroll the element into view, let the reveal animations finish, save <outdir>/<name>-<size>.png and
// report the element's size, anything inside it wider than its box, and whether the page scrolls sideways.
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';

const [url, selector, out = 'shots', ...sizes] = process.argv.slice(2);
if (!url || !selector) {
  console.log('usage: node tools/section_shot.mjs <url> <selector> <outdir> [WxH ...]');
  process.exit(1);
}
fs.mkdirSync(out, { recursive: true });
const name = selector.replace(/[^a-z0-9]+/gi, '') || 'section';
const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: 'new',
  args: ['--ignore-gpu-blocklist', '--enable-gpu', '--use-angle=d3d11'],
});
for (const size of sizes.length ? sizes : ['1440x900']) {
  const [w, h] = size.split('x').map(Number);
  const page = await browser.newPage();
  await page.setViewport({ width: w, height: h, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(url, { waitUntil: 'networkidle0', timeout: 60000 });
  const el = await page.$(selector);
  if (!el) {
    console.log(size.padEnd(10), 'not found:', selector);
    await page.close();
    continue;
  }
  // walk down through the element so every scroll-triggered reveal fires, then come back to its top
  const span = await page.evaluate((s) => {
    const r = document.querySelector(s).getBoundingClientRect();
    return { top: r.top + window.scrollY, height: r.height };
  }, selector);
  for (let y = span.top; y < span.top + span.height; y += h * 0.5) {
    await page.evaluate((y) => window.scrollTo(0, y), y);
    await new Promise((r) => setTimeout(r, 300));
  }
  await page.evaluate((y) => window.scrollTo(0, y), span.top);
  await new Promise((r) => setTimeout(r, 1400));
  const facts = await page.evaluate((s) => {
    const root = document.querySelector(s);
    const box = root.getBoundingClientRect();
    const wide = [...root.querySelectorAll('*')].filter((n) => n.getBoundingClientRect().right > box.right + 1).length;
    return { w: Math.round(box.width), h: Math.round(box.height), wide,
             sideways: document.documentElement.scrollWidth > document.documentElement.clientWidth };
  }, selector);
  const file = path.join(out, `${name}-${size}.png`);
  await el.screenshot({ path: file });
  console.log(size.padEnd(10), `${facts.w}x${facts.h}`, `| wider than the box: ${facts.wide}`,
              `| page scrolls sideways: ${facts.sideways}`, errors.length ? `| errors: ${errors.join(' / ')}` : '');
  await page.close();
}
await browser.close();
