import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const page = readFileSync(new URL('../public/index.html', import.meta.url));

export function createApp() {
  return createServer((req, res) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'");
    const reply = (status, contentType, body) => {
      res.writeHead(status, { 'Content-Type': contentType });
      res.end(req.method === 'HEAD' ? undefined : body);
    };
    const json = (status, body) => reply(status, 'application/json; charset=utf-8', JSON.stringify(body));
    if (!['GET', 'HEAD'].includes(req.method)) {
      res.setHeader('Allow', 'GET, HEAD');
      return json(405, { error: 'method_not_allowed' });
    }
    const path = (req.url ?? '/').split('?')[0];
    if (path === '/') return reply(200, 'text/html; charset=utf-8', page);
    if (path === '/healthz') return json(200, { status: 'ok', service: 'ekt-assistant', stage: 'scaffold' });
    if (path === '/api/status') return json(200, {
      stage: 'scaffold',
      integrations: { catalog: false, stock: false, cart: false, purchaseTerms: false, ai: false },
    });
    return json(404, { error: 'not_found' });
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const port = Number(process.env.PORT ?? 3000);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('PORT must be between 1 and 65535');
  const host = process.env.HOST ?? '127.0.0.1';
  const server = createApp();
  server.listen(port, host, () => console.log(`ekt-assistant listening on ${host}:${port}`));
  for (const signal of ['SIGINT', 'SIGTERM']) {
    process.once(signal, () => {
      server.close(() => process.exit(0));
      setTimeout(() => process.exit(1), 10_000).unref();
    });
  }
}
