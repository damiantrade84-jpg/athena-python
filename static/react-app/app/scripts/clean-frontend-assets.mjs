import { readdirSync, unlinkSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const assetsDir = join(dirname(fileURLToPath(import.meta.url)), '../../../assets');

const indexBundlePattern = /^index-.*\.(js|css)$/;
const lazyChunkPattern = /^(?!index-)[A-Za-z0-9_-]+-[A-Za-z0-9_-]+\.js$/;

let removedIndex = 0;
let removedLazy = 0;

for (const name of readdirSync(assetsDir)) {
  const fullPath = join(assetsDir, name);

  if (indexBundlePattern.test(name)) {
    unlinkSync(fullPath);
    removedIndex += 1;
    continue;
  }

  if (lazyChunkPattern.test(name)) {
    unlinkSync(fullPath);
    removedLazy += 1;
  }
}

console.log(
  `clean-frontend-assets: removed ${removedIndex} stale index bundle(s), ` +
    `${removedLazy} lazy chunk(s) from static/assets`,
);
