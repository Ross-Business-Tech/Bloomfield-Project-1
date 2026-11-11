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
    for (const width of [1280, 1440, 1920, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 900 } });
      const page = await context.newPage();
      page.setDefaultTimeout(10000);
      page.setDefaultNavigationTimeout(10000);
      const errors = [];
      page.on('pageerror', (error) => { errors.push(error.message); console.error(error.message); });
      await page.goto(base);
      await page.locator('#tbody tr').first().waitFor();
      const frame = () => page.locator('.view:not(.hidden) .page-head').evaluate((n) => {
        const h = n.querySelector('h1'), p = n.querySelector('p'), r = n.getBoundingClientRect();
        return { x: r.x, y: r.y, width: r.width, font: getComputedStyle(h).fontFamily,
          size: getComputedStyle(h).fontSize, line: getComputedStyle(h).lineHeight,
          paragraph: getComputedStyle(p).fontSize };
      });
      const dashboardFrame = await frame();
      const tableFits = async () => {
        if (width < 1280) return;
        assert.equal(await page.locator('.view:not(.hidden) .table-wrap').first().evaluate((n) => n.scrollWidth <= n.clientWidth + 1), true,
          'desktop table requires horizontal scrolling');
      };
      await tableFits();
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
      await page.waitForFunction((n) => document.querySelector('.frow.is-final .cnt').textContent === String(n), snapshot.items.length);
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
      await page.screenshot({ path: path.join(out, `radar-${width}-overview.png`), fullPage: true, animations: 'disabled' });
      assert.deepEqual(await frame(), dashboardFrame, 'Overview frame and typography differ');
      assert.equal(await page.locator('#view-overview a[href^="#/leads/"]').count(), 3, 'list links should appear only once');
      assert.equal(await page.locator('.hero-summary, .list-entry').count(), 0);
      assert.equal(await page.locator('.frow').last().locator('.t').textContent(), 'Shown on Deal Radar');
      const shades = await page.locator('.fbar').evaluateAll((nodes) => nodes.map((n) => Number(getComputedStyle(n).getPropertyValue('--bar-color').match(/\d+/)[0])));
      assert.ok(shades.every((shade, i) => i === 0 || shade < shades[i - 1]), 'funnel colors should deepen from top to bottom');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'page overflows');
      await page.getByRole('link', { name: 'How it works', exact: true }).click();
      await page.locator('.nav a[data-view="how"][aria-current="page"]').waitFor();
      assert.equal((await page.locator('.nav').boundingBox()).x, nav.x, 'nav shifts between pages');
      assert.deepEqual(await frame(), dashboardFrame, 'How it works frame and typography differ');
      await page.screenshot({ path: path.join(out, `radar-${width}-how.png`), fullPage: true });
      await page.getByRole('link', { name: 'Explore', exact: true }).click();
      await page.locator('#view-explore:not(.hidden)').waitFor();
      assert.deepEqual(await frame(), dashboardFrame, 'Explore frame and typography differ');
      await tableFits();
      await page.screenshot({ path: path.join(out, `radar-${width}-explore.png`), fullPage: true });
      await page.getByRole('link', { name: 'Overview', exact: true }).click();
      await page.locator('#kpis.in').waitFor();
      assert.ok(Number(await page.locator('#kpis .value').first().textContent()) < snapshot.items.length, 'count should replay on return');
      await page.waitForFunction((n) => document.querySelector('#kpis .value').textContent === String(n), snapshot.items.length);
      await page.getByRole('link', { name: /^List A:/ }).click();
      await page.waitForURL('**/#/leads/A');
      await page.locator('#tabs .a[aria-selected="true"]').waitFor();
      await page.locator('#table:not(.hidden)').waitFor();
      const widths = () => page.locator('#table col').evaluateAll((cols) => Object.fromEntries(cols.map((c) => [c.className, c.getBoundingClientRect().width])));
      const a = await widths();
      await page.getByRole('tab', { name: /List B/ }).click();
      const b = await widths();
      await tableFits();
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
      await page.getByRole('link', { name: /^List B:/ }).click();
      await page.locator('#tabs .tab.b[aria-selected="true"]').waitFor();
      assert.equal(await page.locator('#tabs .tab.b').getAttribute('aria-selected'), 'true');
      assert.equal(await page.locator('#f-search').inputValue(), '');
      await page.getByRole('link', { name: 'Overview', exact: true }).click();
      await page.getByRole('link', { name: /^List C:/ }).click();
      await page.locator('#tabs .tab.c[aria-selected="true"]').waitFor();
      assert.equal(await page.locator('#tabs .tab.c').getAttribute('aria-selected'), 'true');
      await tableFits();
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
    console.log('Deal Radar interactions passed: equal page frames/type, full desktop tables at 1280/1440/1920, phone, replaying motion, gradient, list links, filters, dialog and reduced motion.');
  } finally { await browser.close(); server.close(); }
}
main().catch((error) => { console.error(error); server.close(); process.exitCode = 1; });
