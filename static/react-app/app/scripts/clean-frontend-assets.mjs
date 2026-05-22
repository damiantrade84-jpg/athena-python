import { readdirSync, unlinkSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const assetsDir = join(dirname(fileURLToPath(import.meta.url)), '../../../assets');
const stalePattern = /^index-.*\.(js|css)$/;

let removed = 0;
for (const name of readdirSync(assetsDir)) {
  if (!stalePattern.test(name)) continue;
  unlinkSync(join(assetsDir, name));
  removed += 1;
}

console.log(`clean-frontend-assets: removed ${removed} stale bundle file(s) from static/assets`);
