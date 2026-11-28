/** Serve the synthetic card fixture beside the standalone replay example. */
import { fileURLToPath } from 'node:url';
import { serveReplay as servePreview } from '../../examples/ride-replay/serve.mjs';

export function serveReplay() {
  const fixture = fileURLToPath(new URL('../frontend/map-fixture.html', import.meta.url));
  return servePreview({ fixture });
}
