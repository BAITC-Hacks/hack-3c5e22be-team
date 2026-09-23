import test from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../src/server.js';

test('deployment smoke: page, health, integration status and disabled mutations', async (t) => {
  const server = createApp();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const base = `http://127.0.0.1:${server.address().port}`;
  const page = await fetch(base);
  assert.equal(page.status, 200);
  assert.match(await page.text(), /каталог и корзина пока не подключены/);
  assert.match(page.headers.get('content-security-policy'), /frame-ancestors 'none'/);
  const health = await fetch(`${base}/healthz`);
  assert.equal(health.status, 200);
  assert.equal((await health.json()).status, 'ok');
  const status = await (await fetch(`${base}/api/status`)).json();
  assert.ok(Object.values(status.integrations).every((value) => value === false));
  const mutation = await fetch(`${base}/api/cart`, { method: 'POST', body: '{}' });
  assert.equal(mutation.status, 405);
  assert.equal((await fetch(`${base}/missing`)).status, 404);
  const head = await fetch(`${base}/healthz`, { method: 'HEAD' });
  assert.equal(head.status, 200);
  assert.equal(await head.text(), '');
});
