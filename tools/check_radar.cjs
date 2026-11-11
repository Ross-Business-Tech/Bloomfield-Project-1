// Interaction checks use only the fictional preview fixture. Run after build_preview.py.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const dist = path.join(root, 'preview-dist');
const out = path.join(root, 'smoke-results');
const snapshot = JSON.parse(fs.readFileSync(path.join(dist, 'api/snapshot.json'), 'utf8'));
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://localhost').pathname;
  const file = path.resolve(dist, '.' + (pathname === '/' ? '/index.html' : pathname));
  if (!file.startsWith(dist + path.sep) || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
  res.setHeader('Content-Type', { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json' }[path.extname(file)] || 'application/octet-stream');
  fs.createReadStream(file).pipe(res);
});
async function main() {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch();
  fs.mkdirSync(out, { recursive: true });
  try {
    for (const width of [1280, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 900 } });
      const page = await context.newPage();
      page.setDefaultTimeout(10000);
      page.setDefaultNavigationTimeout(10000);
      const errors = [];
      page.on('pageerror', (error) => { errors.push(error.message); console.error(error.message); });
      await page.goto(base);
      await page.locator('#tbody tr').first().waitFor();
      assert.equal(await page.locator('#kpis').isVisible(), false, 'numbers should be in Overview');
      await page.screenshot({ path: path.join(out, `radar-${width}-dashboard.png`), fullPage: true });
      const nav = await page.locator('.nav').boundingBox();
      await page.getByRole('link', { name: 'Overview', exact: true }).click();
      await page.locator('.nav a[data-view="overview"][aria-current="page"]').waitFor();
      assert.equal((await page.locator('.nav').boundingBox()).x, nav.x, 'nav shifts to Overview');
      assert.equal(await page.locator('#map').isVisible(), false);
      await page.locator('#kpis.in').waitFor();
      await page.waitForFunction((n) => document.querySelector('#kpis .value').textContent === String(n), snapshot.items.length, { timeout: 10000 });
      await page.locator('#funnel').scrollIntoViewIfNeeded();
      await page.locator('#funnel.in').waitFor();
      await page.waitForFunction((n) => document.querySelector('#funnel .cnt').textContent === Number(n).toLocaleString('en-US'), snapshot.funnel[0].count);
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
      await page.screenshot({ path: path.join(out, `radar-${width}-overview.png`), fullPage: true, animations: 'disabled' });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'page overflows');
      await page.getByRole('link', { name: 'How it works', exact: true }).click();
      await page.locator('.nav a[data-view="how"][aria-current="page"]').waitFor();
      assert.equal((await page.locator('.nav').boundingBox()).x, nav.x, 'nav shifts between pages');
      await page.getByRole('link', { name: 'Overview', exact: true }).click();
      await page.locator('#view-overview:not(.hidden)').waitFor();
      assert.equal(await page.locator('#kpis .value').first().textContent(), String(snapshot.items.length), 'count replays');
      await page.getByRole('link', { name: 'View live deals', exact: true }).click();
      await page.waitForURL('**/#/leads/A');
      await page.locator('#tabs .a[aria-selected="true"]').waitFor();
      await page.locator('#table:not(.hidden)').waitFor();
      const widths = () => page.locator('#table col').evaluateAll((cols) => Object.fromEntries(cols.map((c) => [c.className, c.getBoundingClientRect().width])));
      const a = await widths();
      await page.getByRole('tab', { name: /List B/ }).click();
      const b = await widths();
      for (const key of Object.keys(b).filter((k) => k !== 'c-property')) assert.equal(b[key], a[key], `${key} width changes`);
      assert.equal(await page.locator('#thead').getByText('Buyer', { exact: true }).count(), 0);
      await page.locator('#f-search').fill('no matching fictional property');
      assert.equal(await page.locator('#empty').isVisible(), true);
      await page.getByRole('button', { name: 'Reset', exact: true }).first().click();
      const row = page.locator('#tbody tr').first();
      await row.press('Enter');
      await page.getByRole('dialog').waitFor();
      assert.equal(await page.locator('#p-close').evaluate((n) => document.activeElement === n), true);
      await page.screenshot({ path: path.join(out, `radar-${width}-contact.png`), animations: 'disabled' });
      await page.locator('#p-close').press('Shift+Tab');
      assert.equal(await page.locator('#panel').evaluate((n) => n.contains(document.activeElement)), true, 'focus leaves dialog');
      await page.keyboard.press('Escape');
      assert.equal(await row.evaluate((n) => document.activeElement === n), true, 'focus not restored');
      await page.getByRole('link', { name: 'Overview', exact: true }).click();
      await page.getByRole('link', { name: 'View pre-sale distress', exact: true }).click();
      assert.equal(await page.locator('#tabs .tab.b').getAttribute('aria-selected'), 'true');
      assert.equal(await page.locator('#f-search').inputValue(), '');
      assert.deepEqual(errors, []);
      await context.close();
    }
    const reduced = await browser.newContext({ reducedMotion: 'reduce' });
    const page = await reduced.newPage();
    await page.goto(base + '/#/overview');
    await page.locator('#funnel.in').waitFor();
    assert.equal(await page.locator('#kpis .value').first().textContent(), String(snapshot.items.length));
    assert.equal(await page.locator('.fbar').first().evaluate((n) => getComputedStyle(n).transitionDelay), '0s');
    await reduced.close();
    console.log('Deal Radar interactions passed: desktop, phone, navigation, columns, filters, dialog and reduced motion.');
  } finally { await browser.close(); server.close(); }
}
main().catch((error) => { console.error(error); server.close(); process.exitCode = 1; });
