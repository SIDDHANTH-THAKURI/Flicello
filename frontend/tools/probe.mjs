import puppeteer from 'puppeteer-core';
const browser = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: 'new', defaultViewport: { width: 1440, height: 900 } });
const page = await browser.newPage();
await page.goto('http://127.0.0.1:8766/new', { waitUntil: 'networkidle2' });
await page.click('[data-record="camera"]');
await new Promise((r) => setTimeout(r, 1500));
const out = await page.evaluate(() => {
  const pts = [[251, 450], [570, 248], [895, 450]];
  return pts.map(([x, y]) => { const e = document.elementFromPoint(x, y); const cs = getComputedStyle(e); return `${e.tagName}.${e.className} border=${cs.border} radius=${cs.borderRadius} pressed=${[...document.querySelectorAll('[data-rec-mode] button')].map(b=>b.getAttribute('aria-pressed')).join(',')}`; });
});
console.log(out.join('\n'));
await browser.close();
