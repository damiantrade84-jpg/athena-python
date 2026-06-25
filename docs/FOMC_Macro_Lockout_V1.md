# FOMC Macro-Event Feed & Lockout System — V1 (Phase 12 Report)

Safe, advisory-only macro layer that ingests official Fed FOMC events and blocks/downgrades
**new** trade candidates during FOMC windows. Demo/paper only. No strategy threshold changes,
no new signal direction, no auto-execution enablement, no duplicate FRED client, no paid API.

## Architecture (producer → consumer)

```
Fed FOMC calendar HTML ─┐
Fed monetary-policy RSS ─┼─► macro/*  ─► SQLite (macro_events.db) ─► macro_guard (state machine)
FRED (carry_feed reuse) ─┘                                              │
                                                                       ├─► scan overlay (annotate_signals)
                                                                       ├─► AI review prompt block
                                                                       ├─► /api/macro/* routes
                                                                       └─► UI MacroBadge (header chip)
```

## Files added (`macro/` package)

| File | Role |
|------|------|
| `macro/config.py` | env → config.yaml → default resolution; ET→UTC + DST; caches `(Exception, SystemExit)` so cold/CLI processes degrade to defaults instead of tripping the live-order safety gate |
| `macro/store.py` | idempotent SQLite store (WAL); status-precedence merge, COALESCE enrichment, `created_at` preservation; 20-col schema incl. previous/actual target range + `surprise_bps` |
| `macro/fomc_calendar.py` | parses official calendar HTML → `FOMC_RATE_DECISION` at 14:00 ET (presser 14:30 ET), `time_source='assumed_default'`; fail-safe fetch; CLI `--refresh` |
| `macro/fomc_rss.py` | stdlib XML parse of `press_monetary.xml` → `FOMC_STATEMENT`/`FOMC_MINUTES` (status `RELEASED`); flips same-day scheduled decision → RELEASED; CLI `--poll` |
| `macro/fomc_fred.py` | **reuses** `carry_feed.get_fred_latest_rate` (DFEDTARU/DFEDTARL) to confirm target range + compute `surprise_bps`; best-effort; CLI `--confirm` |
| `macro/macro_guard.py` | time-driven state machine + affected-symbol matrix + AI/UI context; CLI `--state` |
| `macro/scan_integration.py` | annotates scan output (`macroRisk`, `macroBlockNewTrades`, …); escalates existing `majorEventRisk.blocksAutoExecution` for affected symbols under hard lockout |
| `macro/scheduler.py` | daemon thread: daily calendar refresh + adaptive RSS poll (fast in FOMC window) |

## Files edited (minimal, surgical)

- `carry_feed.py` — added single public `get_fred_latest_rate()` accessor (no second FRED path).
- `scanner.py` (~2878) — try/wrapped `annotate_signals(results/watchlist)` after correlation cap.
- `ai_review/prompt_builder.py` — inject `render_macro_prompt_block(...)` into Engine A & B chart prompts.
- `ai_review/macro_context.py` (new) — server-trusted macro block + mandatory FOMC instruction.
- `athena_app/api/routes_macro.py` (new) — `/api/macro/state`, `/api/macro/state?symbol=`, `/api/macro/events`.
- `athena.py` — `register_macro_routes(app)` + scheduler startup hook.
- `config.yaml` — 18 `ATHENA_FOMC_*` keys (all defaults below).
- `static/.../layout/Header.tsx` + `shared/MacroBadge.tsx` (new) — advisory header chip; bundle rebuilt.

## State machine

`NONE → UPCOMING → LOCKOUT → ACTIVE_RELEASE → POST_RELEASE_CAUTION → COMPLETED`

Default windows (config-overridable): pre-lockout 30m, post-lockout 60m, extended caution 120m,
presser pre 15m / post 45m, UPCOMING horizon 1440m. Presser folds into the hard window.
**Blocking** = LOCKOUT / ACTIVE_RELEASE; **downgrade** = those + POST_RELEASE_CAUTION.

## Affected symbols

USD forex pairs, XAU/XAG/USD, **US** indices (allow-list; non-US e.g. DAX explicitly excluded),
BTC/ETH + major USD/USDT crypto (toggle `ATHENA_FOMC_AFFECTED_CRYPTO`), DXY. Unaffected symbols
report global risk but are **never** blocked/downgraded.

## Safety properties (verified)

- Advisory only: never mutates score/SL/TP/sizing/execution. Auto-exec block delegated to the
  existing unchanged `majorEventRisk.blocksAutoExecution` contract.
- Cannot enable auto-execution; AI cannot override hard lockout (prompt forbids execution-ready output).
- Fail-safe: unreachable Fed site / parse failure → degraded-mode log + ingest nothing, never raises
  into scan/trade/UI. Store/guard failures resolve to safe `NONE`.
- Idempotent ingest (re-run updates in place, no duplicates).
- Offline-only tests (no live website dependency).

## Tests — 18 passing (4 files)

`tests/test_fomc_calendar.py` (parse / build / idempotent ingest),
`tests/test_fomc_rss.py` (parse / classify / build / same-day RELEASED flip),
`tests/test_macro_guard.py` (6-state timeline, affected matrix, unaffected-not-blocked, disabled→NONE),
`tests/test_macro_routes.py` (global + symbol state, events nextEvent).

## Operational notes

- Scheduler auto-starts with the app when `ATHENA_FOMC_ENABLED` + `ATHENA_FOMC_SCHEDULER_ENABLED`.
- Manual: `python -m macro.fomc_calendar --refresh` · `python -m macro.fomc_rss --poll` ·
  `python -m macro.fomc_fred --confirm` · `python -m macro.macro_guard --state XAU/USD`.
- DB: repo-root `macro_events.db` (override via `ATHENA_MACRO_DB_PATH`).
- Standalone CLI/scripts need `ATHENA_REAL_ORDERS_CONFIRM` set (read-only; same as test conftest)
  only because cold `config` import runs the live-order safety gate — the macro layer itself
  places no orders.
