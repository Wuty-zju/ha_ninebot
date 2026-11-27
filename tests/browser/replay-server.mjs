/** Serve only the synthetic history fixture and the bundled card assets. */
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, sep } from 'node:path';
const root = fileURLToPath(new URL('../../', import.meta.url));
const assets = resolve(root, 'custom_components/ninebot/frontend');

export async function serveReplay() {
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://localhost');
      let path;
      if (url.pathname === '/') path = resolve(root, 'tests/frontend/map-fixture.html');
      else if (url.pathname.startsWith('/ninebot-static/')) {
        path = resolve(assets, url.pathname.slice('/ninebot-static/'.length));
        if (!path.startsWith(assets + sep)) throw new Error('Not an asset');
      } else throw new Error('Not a fixture');
      const bytes = await readFile(path);
      const type = path.endsWith('.js') ? 'application/javascript' : path.endsWith('.css') ? 'text/css' :
        path.endsWith('.png') ? 'image/png' : 'text/html';
      response.writeHead(200, { 'Content-Type': type }); response.end(bytes);
    } catch { response.writeHead(404); response.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { server, origin: `http://127.0.0.1:${server.address().port}` };
}
