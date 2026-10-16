// Walk the sign-up like a person would and screenshot each step (headless Chrome, real GPU).
//   node tools/pages.mjs <base url> <outdir> <outbox dir> [width height]
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';

const [base = 'http://127.0.0.1:8790', out = 'pages', outbox = '', w = '1440', h = '900'] = process.argv.slice(2);
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
const shot = async (name, full = false) => {
  await new Promise((r) => setTimeout(r, 900));
  const file = path.join(out, `${String(n++).padStart(2, '0')}-${name}.png`);
  await page.screenshot({ path: file, fullPage: full });
  console.log('shot', file);
};
const latestCode = () => {
  const files = fs.readdirSync(outbox).filter((f) => f.endsWith('.json')).sort();
  const mail = JSON.parse(fs.readFileSync(path.join(outbox, files[files.length - 1]), 'utf8'));
  return mail.text.match(/code: (\d{6})/)[1];
};

await page.goto(`${base}/signin`, { waitUntil: 'networkidle0' });
await shot('signin');
await page.goto(`${base}/signup`, { waitUntil: 'networkidle0' });
await shot('signup');
const email = `ava.${Date.now()}@example.com`;
await page.type('input[name=email]', email);
await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle0' }), page.click('form[action="/auth/email"] button[type=submit]')]);
await shot('code');
if (outbox) {
  const code = latestCode();
  await page.type('#code', code.slice(0, 3));
  await shot('code-typing');
  await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle0' }), page.type('#code', code.slice(3))]);
  await shot('join');
  await page.evaluate(() => { document.querySelector('input[name=display]').value = ''; });
  await page.type('input[name=display]', 'Ava');
  await page.click('input[name=adult]');
  await page.click('input[name=agree]');
  await shot('join-ticked');
  await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle0' }), page.click('form[action="/join"] button[type=submit]')]);
  console.log('after join:', page.url());
  await page.goto(`${base}/account`, { waitUntil: 'networkidle0' });
  await shot('account', true);
}
await page.goto(`${base}/legal`, { waitUntil: 'networkidle0' });
await shot('legal');
await page.goto(`${base}/legal/privacy`, { waitUntil: 'networkidle0' });
await shot('privacy');
await page.evaluate(() => window.scrollTo(0, 2600));
await shot('privacy-scrolled');
fs.writeFileSync(path.join(out, 'logs.txt'), logs.join('\n'));
if (logs.length) console.log(logs.slice(0, 20).join('\n'));
await browser.close();
