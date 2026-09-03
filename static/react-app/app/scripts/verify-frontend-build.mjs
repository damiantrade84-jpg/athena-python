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

// Aurora Terminal theme markers — proves the current design tokens and
// component layer actually shipped, rather than a stale bundle being served.
//   216 100% 66  → the azure accent (--primary)
//   152 64% 46   → the semantic long/direction token
//   meter-fill   → the score/quality meter component class
const requiredCssMarkers = [
  '216 100% 66',
  '152 64% 46',
  'app-shell-bg',
  'panel-glass',
  'meter-fill',
  // FABLE codex stylesheet (scoped .fbl-* classes) must ship with the bundle.
  'fbl-root',
];
for (const marker of requiredCssMarkers) {
  if (!cssText.includes(marker)) {
    fail(`production CSS missing theme marker: ${marker}`);
  }
}

// eqFill is the shared equity chart's gradient id prefix; the Engine B string
// proves the rebuilt signal card is in the bundle.
const requiredJsMarkers = [
  'EquityAreaChart',
  'eqFill',
  'engine_b_quality_pct_net',
  // AI chart review must ship the two-image browser contract. These property
  // names survive minification and catch a stale five-field production bundle.
  'review_role',
  'entry_screenshot_base64',
  'entry_screenshot_meta',
  // Engine B's ASE overlay must remain visible and explicitly advisory.
  'ASE SUPPORTS',
  'ASE WATCH ALIGNED',
  'supportEligible',
  'no Engine B score or gate change',
  // Latest ASE scan results must survive sidebar panel unmount/remount.
  'aseScanCache',
  // FABLE engine panel and its scan-board snapshot adapter.
  'fable-engine',
  '/api/fable/scan',
];
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
