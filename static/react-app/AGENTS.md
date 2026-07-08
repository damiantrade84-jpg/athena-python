# AGENTS.md — React / native chart UI

Scope: `static/react-app/` and chart-related API consumers.

## Prod UI (Flask :5000)

1. `cd static/react-app/app && npm run build`
2. Restart Flask if already running (optional; static files are read from disk)
3. Hard refresh browser (Ctrl+Shift+R)
4. Verify: View Source → `meta name="athena-frontend-build"` + `/static/assets/index-<hash>.js`

Flask serves `static/index.html` — **not** `static/react-app/dist/`. Source edits without `npm run build` will **not** appear in prod.

## Rules

- Native chart is the active surface for chart and AI review work. Do not build new review features on legacy TradingView paths.
- Engine B overlays: TV Chart tab. Scalp Workbench: Engine D only.
- **ASE panel** (`ASEPanel.tsx`): SHADOW/DEMO badge per family; server `/api/ase-scan` payloads authoritative — do not wire ASE to execution from the frontend.
- Do not trust the browser for Engine A score, threshold, ATR, or RR — server-assembled payloads are authoritative.
- Chart AI review is read-only advisory; no execution wiring from the frontend.
- Prefer targeted frontend typecheck/build for touched packages; do not run unrelated UI suites unless requested.

## Skills

- `athena-ui-chart-review` — chart UI, Vision/review payloads, overlay contracts
- `athena-engine-parity` — when the bug is live chart vs engine payload mismatch
- `athena-cross-surface-parity` — before chart indicator changes: trace `price_precision` / `indicator_periods` from API → `TVChartPanel` computation → labels → per-group tests

Parent rules: repo root `AGENTS.md`.
