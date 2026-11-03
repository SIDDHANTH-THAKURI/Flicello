// Opens a film page in the renderer's own Chrome and reports what a render can't: script errors, whether the
// timeline is there, the scenes' windows and the sounds the kit logged (window.__events).
//     node probe.mjs <index.html> <chrome executable>
import { pathToFileURL } from 'node:url';
import puppeteer from 'puppeteer-core';

const [file, chrome] = process.argv.slice(2);
const browser = await puppeteer.launch({ executablePath: chrome, headless: 'shell',
  args: ['--allow-file-access-from-files', '--disable-gpu', '--no-sandbox'] });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String((e && e.message) || e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(pathToFileURL(file).href, { waitUntil: 'load', timeout: 60000 });
  await page.evaluate(() => (document.fonts ? document.fonts.ready.then(() => true) : true));
  await new Promise((r) => setTimeout(r, 400));
  const out = await page.evaluate(() => {
    const tl = window.__timelines && window.__timelines.main;
    return { timeline: !!tl, duration: tl ? tl.duration() : 0, events: window.__events || [], scenes: window.__scenes || {} };
  });
  console.log(JSON.stringify({ errors, ...out }));
} finally {
  await browser.close();
}
