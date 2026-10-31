// Screenshot each sketch section of a sketches page: node tools/sketch_shot.mjs <file> <outdir>
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
const [file, out] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });
const browser = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: 'new',
  args: ['--ignore-gpu-blocklist', '--enable-gpu', '--use-angle=d3d11'], defaultViewport: { width: 1440, height: 900 } });
const page = await browser.newPage();
await page.goto('file:///' + file.split('\\').join('/'), { waitUntil: 'networkidle0' });
await page.evaluate(() => document.querySelectorAll('.reveal').forEach((e) => e.classList.add('in')));
for (const id of ['s1', 's2', 's3', 's4', 's5', 's6', 's7', 's8', 's9']) {
  const el = await page.$('#' + id);
  await el.scrollIntoView();
  await new Promise((r) => setTimeout(r, 2500));
  await el.screenshot({ path: `${out}/${id}.png` });
}
await browser.close();
