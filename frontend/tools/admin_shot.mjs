// Sign in as the owner (email code) and screenshot the dashboard, with a tooltip shown and the 90-day view.
//   node tools/admin_shot.mjs <base> <outdir> <outbox> <email>
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';

const [base, out, outbox, email] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });
const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: 'new',
  args: ['--ignore-gpu-blocklist', '--enable-gpu', '--use-angle=d3d11'], defaultViewport: { width: 1440, height: 900, deviceScaleFactor: 1 },
});
const page = await browser.newPage();
const logs = [];
page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') logs.push(`[${m.type()}] ${m.text()}`); });
page.on('pageerror', (e) => logs.push(`[pageerror] ${e.message}`));
await page.goto(`${base}/signin`, { waitUntil: 'networkidle0' });
await page.type('input[name=email]', email);
await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle0' }), page.click('form[action="/auth/email"] button[type=submit]')]);
const files = fs.readdirSync(outbox).filter((f) => f.endsWith('.json')).sort();
const code = JSON.parse(fs.readFileSync(path.join(outbox, files[files.length - 1]), 'utf8')).text.match(/code: (\d{6})/)[1];
await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle0' }), page.type('#code', code)]);
await page.goto(`${base}/admin`, { waitUntil: 'networkidle0' });
await new Promise((r) => setTimeout(r, 1800));
await page.screenshot({ path: path.join(out, '00-admin-full.png'), fullPage: true });
await page.$eval('#c-visitors', (n) => n.scrollIntoView({ block: 'center' }));
await new Promise((r) => setTimeout(r, 300));
const box = await page.$eval('#c-visitors svg', (s) => { const r = s.getBoundingClientRect(); return { x: r.x + r.width * 0.7, y: r.y + r.height * 0.5 }; });
await page.mouse.move(box.x, box.y);
await new Promise((r) => setTimeout(r, 400));
await page.screenshot({ path: path.join(out, '01-tooltip.png') });
await page.click('[data-range="90"]');
await new Promise((r) => setTimeout(r, 1500));
await page.screenshot({ path: path.join(out, '02-90days.png'), fullPage: true });
fs.writeFileSync(path.join(out, 'logs.txt'), logs.join('\n'));
console.log(logs.slice(0, 15).join('\n') || 'no console errors');
await browser.close();
