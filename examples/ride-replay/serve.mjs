/** Serve the local example and its static frontend dependencies on loopback. */
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const preview = fileURLToPath(new URL('./', import.meta.url));
const frontend = fileURLToPath(new URL('../../custom_components/ninebot/frontend/', import.meta.url));
const files = new Set(['index.html', 'replay.css', 'replay.mjs', 'synthetic-archive.mjs']);
const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.png': 'image/png' };
const policy = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; " +
  "connect-src 'none'; base-uri 'none'; form-action 'none'";

export async function serveReplay({ port = 0, fixture = null } = {}) {
  const server = createServer(async (request, response) => {
    try {
      const { pathname } = new URL(request.url, 'http://127.0.0.1');
      if (request.method !== 'GET' && request.method !== 'HEAD') {
        response.writeHead(405, { Allow: 'GET, HEAD' }).end();
        return;
      }
      let path;
      if (pathname === '/' && fixture) path = fixture;
      else if (pathname.startsWith('/ninebot-static/')) {
        path = resolve(frontend, pathname.slice('/ninebot-static/'.length));
        if (!path.startsWith(resolve(frontend) + sep)) throw new Error('Unknown asset');
      } else {
        const name = pathname === '/' || pathname === '/replay/' ? 'index.html' : pathname.slice('/replay/'.length);
        if (!(pathname === '/' || pathname.startsWith('/replay/')) || !files.has(name)) {
          throw new Error('Unknown preview page');
        }
        path = resolve(preview, name);
      }
      const bytes = await readFile(path);
      const headers = { 'Content-Type': types[extname(path)] ?? 'application/octet-stream',
        'X-Content-Type-Options': 'nosniff' };
      if (path === resolve(preview, 'index.html')) headers['Content-Security-Policy'] = policy;
      response.writeHead(200, headers).end(request.method === 'HEAD' ? undefined : bytes);
    } catch {
      response.writeHead(404).end();
    }
  });
  await new Promise((ready, fail) => {
    server.once('error', fail);
    server.listen(port, '127.0.0.1', ready);
  });
  return { server, origin: `http://127.0.0.1:${server.address().port}` };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const { origin } = await serveReplay({ port: Number(process.argv[2] ?? 8765) });
  console.log(`Ninebot ride replay · ${origin}/replay/`);
}
