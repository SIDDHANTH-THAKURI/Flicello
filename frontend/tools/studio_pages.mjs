// Sign in with an email code and screenshot the studio: films, every film's page, new film, style.
//   node tools/studio_pages.mjs <base url> <outdir> <outbox dir> <email> [width height]
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';

const [base, out, outbox, email, w = '1440', h = '900'] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });
const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: 'new',
  args: ['--ignore-gpu-blocklist', '--enable-gpu', '--use-angle=d3d11', '--autoplay-policy=no-user-gesture-required'],
  defaultViewport: { width: +w, height: +h, deviceScaleFactor: 1 },
});
const page = await browser.newPage();
const logs = [];
page.on('console', (m) => { if (m.type() === 'error') logs.push(`[error] ${m.text()}`); });
page.on('pageerror', (e) => logs.push(`[pageerror] ${e.message}`));
let n = 0;
const shot = async (name, full = true) => {
  await new Promise((r) => setTimeout(r, 1200));
  const file = path.join(out, `${String(n++).padStart(2, '0')}-${name}.png`);
  await page.screenshot({ path: file, fullPage: full });
  console.log('shot', file);
};
await page.goto(`${base}/signin`, { waitUntil: 'networkidle0' });
await page.type('input[name=email]', email);
await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle0' }), page.click('form[action="/auth/email"] button[type=submit]')]);
const files = fs.readdirSync(outbox).filter((f) => f.endsWith('.json')).sort();
const code = JSON.parse(fs.readFileSync(path.join(outbox, files[files.length - 1]), 'utf8')).text.match(/code: (\d{6})/)[1];
await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle0' }), page.type('#code', code)]);
await page.goto(`${base}/`, { waitUntil: 'networkidle0' });
await shot('films');
const links = await page.$$eval('a.film[href^="/p/"]', (as) => as.map((a) => [a.getAttribute('href'), a.querySelector('b').textContent]));
for (const [href, title] of links) {
  await page.goto(base + href, { waitUntil: 'domcontentloaded' });
  await shot(`film-${title.replace(/\W+/g, '-').toLowerCase()}`);
}
await page.goto(`${base}/new`, { waitUntil: 'networkidle0' });
await shot('new');
await page.goto(`${base}/style`, { waitUntil: 'networkidle0' });
await shot('style');
fs.writeFileSync(path.join(out, 'logs.txt'), logs.join('\n'));
if (logs.length) console.log(logs.slice(0, 20).join('\n'));
await browser.close();
