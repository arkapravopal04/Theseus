// Smoke test for theseus's viewer.html, used by tests/test_viewer.py.
// Usage: node viewer_check.js <viewer.html> <local three package dir, or "" for the CDN> <screenshot prefix>
const { chromium } = require('playwright');
const path = require('path');
const [page_path, three, out] = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  if (three) {  // serve a local copy of the pinned three.js instead of the CDN (offline test runs)
    await page.route('https://cdn.jsdelivr.net/npm/three@*/**', route => {
      const rel = route.request().url().replace(/^.*three@[^/]+\//, '');
      route.fulfill({ path: path.join(three, rel), contentType: 'application/javascript' });
    });
  }
  await page.goto('file://' + path.resolve(page_path));
  await page.waitForFunction(() => window.theseusReady === true, null, { timeout: 30000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: out + '-diff.png' });
  const views = await page.$$eval('#views button', bs => bs.map(b => b.textContent));
  await page.click('#views button:has-text("Overlay")'); await page.waitForTimeout(300);
  await page.screenshot({ path: out + '-overlay.png' });
  await page.selectOption('#axis', 'X'); await page.fill('#cut', '250'); await page.dispatchEvent('#cut', 'input');
  await page.waitForTimeout(300); await page.screenshot({ path: out + '-section.png' });
  await page.selectOption('#axis', '');
  await page.click('#views button:has-text("Difference")');
  await page.click('li.finding'); await page.waitForTimeout(300);
  await page.screenshot({ path: out + '-finding.png' });
  // measure: click two points near the canvas centre
  await page.click('#measure');
  const box = await page.locator('#stage canvas').boundingBox();
  await page.mouse.click(box.x + box.width * 0.45, box.y + box.height * 0.5);
  await page.mouse.click(box.x + box.width * 0.55, box.y + box.height * 0.55);
  const measured = await page.textContent('#measure-out');
  const findings = await page.$$eval('li.finding', ls => ls.length);
  console.log(JSON.stringify({ views, findings, measured, errors }));
  await browser.close();
})();
