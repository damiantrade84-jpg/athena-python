import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const staticRoot = join(dirname(fileURLToPath(import.meta.url)), '../../..');
const indexHtmlPath = join(staticRoot, 'index.html');
const assetsDir = join(staticRoot, 'assets');

function fail(message) {
  console.error(`verify-frontend-build: ${message}`);
  process.exit(1);
}

function readIndexHtml() {
  if (!existsSync(indexHtmlPath)) {
    fail('static/index.html is missing');
  }
  return readFileSync(indexHtmlPath, 'utf8');
}

function activeBundleRefs(html) {
  const jsRefs = [...html.matchAll(/src="\/static\/assets\/([^"]+\.js)"/g)].map((m) => m[1]);
  const cssRefs = [...html.matchAll(/href="\/static\/assets\/([^"]+\.css)"/g)].map((m) => m[1]);
  if (jsRefs.length === 0) fail('static/index.html does not reference a JS bundle');
  if (cssRefs.length === 0) fail('static/index.html does not reference a CSS bundle');
  return { jsRefs, cssRefs, allRefs: [...jsRefs, ...cssRefs] };
}

function readBundleText(ref) {
  const path = join(assetsDir, ref);
  if (!existsSync(path)) {
    fail(`static/index.html references missing asset: assets/${ref}`);
  }
  return readFileSync(path, 'utf8');
}

const html = readIndexHtml();

const buildMeta = html.match(/<meta name="athena-frontend-build" content="([^"]+)"\s*\/?>/);
if (!buildMeta) {
  fail('static/index.html is missing athena-frontend-build meta tag');
}
if (!/^\d{4}-\d{2}-\d{2}T/.test(buildMeta[1])) {
  fail(`athena-frontend-build meta is not ISO format: ${buildMeta[1]}`);
}

const { jsRefs, cssRefs, allRefs } = activeBundleRefs(html);

const jsText = jsRefs.map(readBundleText).join('\n');
const cssText = cssRefs.map(readBundleText).join('\n');

const requiredCssMarkers = ['258 90% 68', 'app-shell-bg', 'panel-glass'];
for (const marker of requiredCssMarkers) {
  if (!cssText.includes(marker)) {
    fail(`production CSS missing Aurora marker: ${marker}`);
  }
}

const requiredJsMarkers = ['EquityAreaChart', 'dashEquityStroke'];
for (const marker of requiredJsMarkers) {
  if (!jsText.includes(marker)) {
    fail(`production JS missing redesign marker: ${marker}`);
  }
}

const indexOnDisk = new Set(
  readdirSync(assetsDir).filter((name) => /^index-.*\.(js|css)$/.test(name)),
);
const expectedIndex = new Set(allRefs.filter((ref) => /^index-/.test(ref)));
const extraIndex = [...indexOnDisk].filter((name) => !expectedIndex.has(name));
const missingIndex = [...expectedIndex].filter((name) => !indexOnDisk.has(name));
if (extraIndex.length > 0 || missingIndex.length > 0) {
  fail(`index bundle mismatch — extra=${extraIndex.join(', ') || 'none'}, missing=${missingIndex.join(', ') || 'none'}`);
}

const lazyChunkRefs = new Set();
for (const match of jsText.matchAll(/([A-Za-z0-9_-]+-[A-Za-z0-9_-]+\.js)/g)) {
  const candidate = match[1];
  if (!/^index-/.test(candidate)) {
    lazyChunkRefs.add(candidate);
  }
}

for (const chunk of lazyChunkRefs) {
  if (!existsSync(join(assetsDir, chunk))) {
    fail(`main bundle references missing lazy chunk: assets/${chunk}`);
  }
}

const lazyOnDisk = readdirSync(assetsDir).filter((name) => /^(?!index-)[A-Za-z0-9_-]+-[A-Za-z0-9_-]+\.js$/.test(name));
const orphanLazy = lazyOnDisk.filter((name) => !lazyChunkRefs.has(name));
if (orphanLazy.length > 0) {
  fail(`orphaned lazy chunk(s) on disk: ${orphanLazy.join(', ')}`);
}

console.log(
  `verify-frontend-build: ok — build=${buildMeta[1]}, ` +
    `js=${jsRefs.join(', ')}, css=${cssRefs.join(', ')}, lazy=${[...lazyChunkRefs].join(', ') || 'none'}`,
);
