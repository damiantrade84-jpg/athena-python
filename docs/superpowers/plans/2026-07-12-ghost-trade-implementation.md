# Ghost Trade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Ghost Trade engine and fully connected UI with dynamic MT5/Bybit discovery, transparent structural scoring, shadow tracking, and verified demo-only manual/automatic execution.

**Architecture:** A new `ghost_trade` package owns its models, persistence, scanner, scheduler, risk state, and broker adapters. Existing Athena candle/broker/guardian capabilities are injected through narrow runtime adapters; Ghost never imports another engine's scoring or auto-trader. Flask routes and one React panel connect the subsystem while all live-order paths remain fail-closed.

**Tech Stack:** Python 3, Flask, SQLite, pytest, React 19, TypeScript, Vite, Vitest, existing MT5 and Bybit clients.

## Global Constraints

- Committed mode is `SHADOW`; `live_trading_allowed` is always false.
- Ghost Trade remains independent from Engines A/B/C/D, ASE, Engine C, and the existing auto-trader.
- Confirmed scores use closed D1/H4/H1 candles only; M15 forming data is a separate overlay capped at +/-0.10.
- No ML, training, parameter optimization, fixed-lot sizing, or volatility-based risk increases.
- Execution requires verified MT5 demo/contest or Bybit demo/testnet status and fails closed on uncertainty.
- Risk defaults: 0.25% per trade, maximum 0.50% per trade, maximum 2.00% total Ghost risk.
- Structural SL/TP is the only enabled exit model.
- Preserve unrelated dirty-worktree changes and do not run the full repository test suite.

---

### Task 1: Configuration and immutable domain contracts

**Files:**
- Create: `ghost_trade/__init__.py`
- Create: `ghost_trade/config.py`
- Create: `ghost_trade/models.py`
- Modify: `config.py`
- Modify: `config.yaml`
- Test: `tests/ghost_trade/test_config_models.py`

**Interfaces:**
- Produces: `GhostConfig.from_mapping(mapping, environ) -> GhostConfig`
- Produces: enums and frozen dataclasses used by every later task.
- Produces: `load_ghost_config(CONFIG, os.environ) -> GhostConfig`.

- [ ] **Step 1: Write failing configuration/model tests**

Test committed SHADOW defaults, exact environment overrides, invalid numeric/risk bounds, enum parsing, immutable models, score range validation, and forced `live_trading_allowed is False` even when inputs request true.

- [ ] **Step 2: Run the focused test file and confirm red**

Run: `python -m pytest tests/ghost_trade/test_config_models.py -q`

Expected: collection/import failure because `ghost_trade` contracts do not exist.

- [ ] **Step 3: Implement typed configuration and models**

Define `GhostMode`, `Venue`, `AssetGroup`, `Style`, `Direction`, `VolatilityRegime`, `SignalStatus`, `PositionMode`, `PositionStatus`, `DemoVerificationStatus`, and `ExitStrategy`. Define frozen candle, instrument, component, geometry, signal, scan, position, execution, and performance records with explicit JSON serialization. Parse only the six specified environment variables and reject widening risk values.

- [ ] **Step 4: Add safe root configuration defaults**

Add the `ghost_trade` YAML section and config loader integration without changing existing engine keys. The loader must normalize `live_trading_allowed` to false and `demo_auto_enabled` effective state to false on process start.

- [ ] **Step 5: Run the focused tests**

Run: `python -m pytest tests/ghost_trade/test_config_models.py -q`

Expected: all pass.

### Task 2: Canonical symbols and dynamic universe discovery

**Files:**
- Create: `ghost_trade/symbols.py`
- Create: `ghost_trade/universe.py`
- Test: `tests/ghost_trade/test_symbols_universe.py`

**Interfaces:**
- Consumes: `GhostConfig`, `GhostInstrument`, `Venue`, `AssetGroup`.
- Produces: `CanonicalSymbolService.classify(metadata) -> GhostInstrument`.
- Produces: `MT5UniverseProvider.discover() -> UniverseResult`.
- Produces: `BybitUniverseProvider.discover() -> UniverseResult`.

- [ ] **Step 1: Write failing symbol and discovery tests**

Cover MT5 suffix/prefix preservation, metadata-first classification, configurable overrides, unknown-to-other visibility, disabled/expired/selectability states, Bybit active linear USDT filters, spot gating, missing filters, liquidity/history skip reasons, and deterministic ordering.

- [ ] **Step 2: Run the focused test file and confirm red**

Run: `python -m pytest tests/ghost_trade/test_symbols_universe.py -q`

- [ ] **Step 3: Implement canonical classification**

Use MT5 path/description/currency metadata and Bybit base/quote/type metadata before conservative text parsing. Retain exact execution symbols and expose every unknown/skipped instrument with reasons.

- [ ] **Step 4: Implement read-only universe providers**

Inject fake/shared clients, perform no order calls, normalize metadata/filters, and return discovered, eligible, skipped, error, and per-group counts.

- [ ] **Step 5: Run the focused tests**

Run: `python -m pytest tests/ghost_trade/test_symbols_universe.py -q`

### Task 3: Confirmed candles and pure structural scoring

**Files:**
- Create: `ghost_trade/market_data.py`
- Create: `ghost_trade/structure.py`
- Create: `ghost_trade/momentum.py`
- Create: `ghost_trade/volatility.py`
- Create: `ghost_trade/exhaustion.py`
- Create: `ghost_trade/levels.py`
- Create: `ghost_trade/scoring.py`
- Test: `tests/ghost_trade/test_scoring.py`

**Interfaces:**
- Produces: `CandleAdapter.load(instrument, profile, as_of) -> CandleBundle`.
- Produces: `confirmed_fractals(candles, as_of) -> SwingSet`.
- Produces: `score_confirmed(bundle, instrument, config) -> GhostScore`.
- Produces: `score_live_overlay(bundle, confirmed_score) -> LiveOverlay`.

- [ ] **Step 1: Write failing point-in-time/scoring tests**

Cover t+2 fractal availability, bullish/bearish/mixed D1 structure, ROC robust normalization, unavailable volume renormalization, FX tick-volume quality, volatility classes, exhaustion cap, correct-side structural levels and R:R, entry-quality components, score bounds, forming-candle exclusion, as-of alignment, stale/missing data errors, and overlay cap/separation.

- [ ] **Step 2: Run the focused test file and confirm red**

Run: `python -m pytest tests/ghost_trade/test_scoring.py -q`

- [ ] **Step 3: Implement candle integrity and pure features**

Normalize OHLCV, identify/drop forming confirmed-timeframe bars, preserve provider/timestamps, align D1/H4/H1 at one as-of time, and expose M15 forming state separately. Implement every formula and fixed weight/threshold from the approved design without importing production Engine A scoring.

- [ ] **Step 4: Implement geometry, eligibility diagnostics, and live overlay**

Validate structural stop/target level side, confirmation source/time, ATR distances, finite positive risk/room, and broker minimum-distance diagnostics. Clamp the overlay and prevent it from mutating confirmed direction/geometry.

- [ ] **Step 5: Run the focused tests**

Run: `python -m pytest tests/ghost_trade/test_scoring.py -q`

### Task 4: Dedicated persistence, migrations, and signal lifecycle

**Files:**
- Create: `ghost_trade/persistence.py`
- Create: `ghost_trade/signal_service.py`
- Test: `tests/ghost_trade/test_persistence_signals.py`

**Interfaces:**
- Produces: `GhostRepository(db_path)` and transactional `migrate()`.
- Produces: stable `signal_id_for(...)` and idempotent scan/signal upserts.
- Produces: query/filter methods for every API list/detail view.

- [ ] **Step 1: Write failing migration/lifecycle tests**

Cover fresh/upgrade migrations, rollback on failure, deterministic IDs, idempotent upsert, signal-version conflicts, numeric round trips, status transitions, execution reservation uniqueness, filtering/pagination, and isolation from `audit.db` engine tables.

- [ ] **Step 2: Run the focused test file and confirm red**

Run: `python -m pytest tests/ghost_trade/test_persistence_signals.py -q`

- [ ] **Step 3: Implement transactional schema and repository**

Create all nine approved Ghost tables plus schema versioning and indexes. Persist core fields as queryable columns and diagnostics as canonical JSON. Use transactions for scan completion, execution reservation, and position transitions.

- [ ] **Step 4: Implement signal lifecycle service**

Derive stable identifiers from engine/version/venue/symbol/style/confirmed timestamps, preserve all rejection reasons, calculate group/global ranks, and prevent dismissed/stale/version-mismatched execution.

- [ ] **Step 5: Run the focused tests**

Run: `python -m pytest tests/ghost_trade/test_persistence_signals.py -q`

### Task 5: SHADOW scanner, positions, performance, and scheduler

**Files:**
- Create: `ghost_trade/eligibility.py`
- Create: `ghost_trade/scanner.py`
- Create: `ghost_trade/reconciliation.py`
- Create: `ghost_trade/scheduler.py`
- Create: `ghost_trade/service.py`
- Test: `tests/ghost_trade/test_shadow_service.py`

**Interfaces:**
- Consumes: universe providers, candle adapter, pure scorer, repository, config, clock.
- Produces: `GhostService.scan(request) -> ScanResult`.
- Produces: shadow position tracking and `performance(filters) -> PerformanceReport`.
- Produces: `GhostScheduler.start()/stop()/trigger_scan()` with a single scan lease.

- [ ] **Step 1: Write failing SHADOW service tests**

Cover all-instrument visibility, scan counts/errors, explicit skip reasons, one-scan lease/409 state, independent cache keys, no execution adapter calls, shadow entry/SL/TP lifecycle, structural-only exit, performance partitions/concentration, and scheduler close-time/fallback behavior.

- [ ] **Step 2: Run the focused test file and confirm red**

Run: `python -m pytest tests/ghost_trade/test_shadow_service.py -q`

- [ ] **Step 3: Implement scanner and eligibility**

Discover dynamically, load confirmed bundles, score each eligible instrument, persist neutral/ineligible/error outcomes, rank within group/global universe, and expose visibility separately from execution eligibility.

- [ ] **Step 4: Implement shadow tracking, performance, and scheduler**

Use structural levels only, never call brokers, update hypothetical positions from market data, derive Ghost-only metrics, serialize scans with a lease, and keep automatic execution disabled.

- [ ] **Step 5: Run the focused tests**

Run: `python -m pytest tests/ghost_trade/test_shadow_service.py -q`

### Task 6: Authenticated Flask API and application wiring

**Files:**
- Create: `ghost_trade/api.py`
- Modify: `athena.py`
- Test: `tests/ghost_trade/test_api.py`
- Test: `tests/ghost_trade/test_engine_isolation.py`

**Interfaces:**
- Produces: `register_ghost_trade_routes(app, runtime) -> GhostService`.
- Exposes every approved `/api/ghost-trade/*` route with stable status/error schemas.

- [ ] **Step 1: Write failing API and isolation tests**

Cover health/config safe allowlist, live-enable rejection, universe, scan/scan conflict, filters/details/dismiss, positions/history/performance/groups, SHADOW execute rejection, missing/stale IDs, explicit provider errors, and unchanged existing Engine A/B/C/D/ASE route registration/state.

- [ ] **Step 2: Run the focused API file and confirm red**

Run: `python -m pytest tests/ghost_trade/test_api.py -q`

- [ ] **Step 3: Implement route handlers and registrar**

Use injected service/runtime dependencies, JSON validation, server-derived `canExecute`, safe error-to-status mapping, and existing global Athena authentication/rate limiting. Do not swallow errors into empty lists.

- [ ] **Step 4: Add minimal application wiring**

Register after Flask/auth setup, construct the dedicated database path and adapters, and provide explicit scheduler lifecycle without changing existing scan/auto-trader wiring.

- [ ] **Step 5: Run API and isolation tests**

Run: `python -m pytest tests/ghost_trade/test_api.py tests/ghost_trade/test_engine_isolation.py -q`

### Task 7: Fully connected React panel

**Files:**
- Create: `static/react-app/app/src/features/ghostTrade/types.ts`
- Create: `static/react-app/app/src/features/ghostTrade/api.ts`
- Create: `static/react-app/app/src/features/ghostTrade/useGhostTrade.ts`
- Create: `static/react-app/app/src/features/ghostTrade/GhostSafetyBanner.tsx`
- Create: `static/react-app/app/src/features/ghostTrade/GhostGroupSection.tsx`
- Create: `static/react-app/app/src/features/ghostTrade/GhostSignalCard.tsx`
- Create: `static/react-app/app/src/features/ghostTrade/GhostSignalDrawer.tsx`
- Create: `static/react-app/app/src/features/ghostTrade/GhostPositions.tsx`
- Create: `static/react-app/app/src/features/ghostTrade/GhostPerformance.tsx`
- Create: `static/react-app/app/src/components/panels/GhostTradePanel.tsx`
- Modify: `static/react-app/app/src/types/index.ts`
- Modify: `static/react-app/app/src/pages/Home.tsx`
- Modify: `static/react-app/app/src/components/layout/Sidebar.tsx`
- Test: `static/react-app/app/src/features/ghostTrade/GhostTradePanel.test.tsx`

**Interfaces:**
- Consumes: typed API schemas from Task 6.
- Produces: independent `ghostTrade` navigation panel and authoritative mutation refresh behavior.

- [ ] **Step 1: Write failing frontend tests**

Cover navigation/panel render, group summaries/sections, list/card sorting and filters, confirmed/live split, top-down diagnostics, detail drawer, SHADOW hiding execution, verified/unverified demo actions, explicit auto confirmation, positions/performance, loading/provider/API errors, and mutation refresh.

- [ ] **Step 2: Run the focused frontend test and confirm red**

Run from `static/react-app/app`: `npm test -- src/features/ghostTrade/GhostTradePanel.test.tsx`

- [ ] **Step 3: Implement typed feature client and panel components**

Keep Ghost state outside global Engine signals/positions. Render every required field/rejection reason and exact safety banner states. Disable duplicate submissions and refresh server-authoritative state after mutations.

- [ ] **Step 4: Wire navigation and panel map**

Add `ghostTrade` to `PanelId`, Sidebar primary navigation, and Home mapping without changing existing panel IDs.

- [ ] **Step 5: Run the focused frontend test**

Run from `static/react-app/app`: `npm test -- src/features/ghostTrade/GhostTradePanel.test.tsx`

### Task 8: Fixed-risk sizing and Ghost portfolio controls

**Files:**
- Create: `ghost_trade/position_sizing.py`
- Create: `ghost_trade/risk.py`
- Test: `tests/ghost_trade/test_risk_sizing.py`

**Interfaces:**
- Produces: `size_mt5(request, account, symbol_info, conversion) -> SizeResult`.
- Produces: `size_bybit(request, account, filters) -> SizeResult`.
- Produces: `GhostRiskService.evaluate(signal, open_positions) -> GateResult`.

- [ ] **Step 1: Write failing sizing/risk tests**

Cover MT5 tick/contract/currency conversion, round-down volume steps/min/max, Bybit step/notional, missing metadata rejection, 0.25/0.50/2.00 percent bounds, no volatility increase, duplicate direction, repeated signal ID, cooldown, maximum positions, signed group exposure diagnostics, and other-engine position non-blocking.

- [ ] **Step 2: Run the focused file and confirm red**

Run: `python -m pytest tests/ghost_trade/test_risk_sizing.py -q`

- [ ] **Step 3: Implement deterministic sizing and gates**

Reject uncertainty, round down, preserve fixed risk, and return transparent calculations/reason codes. Ghost caps count only Ghost positions while shared guardian/risk approval remains mandatory later.

- [ ] **Step 4: Run the focused tests**

Run: `python -m pytest tests/ghost_trade/test_risk_sizing.py -q`

### Task 9: Verified demo-only provider adapters and reconciliation

**Files:**
- Create: `ghost_trade/execution/__init__.py`
- Create: `ghost_trade/execution/base.py`
- Create: `ghost_trade/execution/mt5_demo.py`
- Create: `ghost_trade/execution/bybit_demo.py`
- Modify: `ghost_trade/service.py`
- Modify: `ghost_trade/api.py`
- Test: `tests/ghost_trade/test_demo_execution.py`
- Test: `tests/ghost_trade/test_reconciliation.py`

**Interfaces:**
- Produces: `DemoExecutionAdapter.attest()`, `submit()`, `close()`, and `reconcile()`.
- Consumes: persisted signal reservation, sizing, Ghost gates, shared guardian/risk approval, existing executor functions.

- [ ] **Step 1: Write failing demo-attestation/execution tests**

Cover MT5 demo/contest verification, real/unknown rejection before `order_send`, Bybit demo/testnet non-production endpoint verification, production/unknown rejection before `create_order`, exact broker symbol, side price, spread/stops/freeze, fill mode, quantity rounding, hedge index, reduce-only close, bounded SL/TP handling, response persistence, partial fills, and no production fallback.

- [ ] **Step 2: Run the focused execution file and confirm red**

Run: `python -m pytest tests/ghost_trade/test_demo_execution.py -q`

- [ ] **Step 3: Implement fail-closed adapters and manual execution**

Attest on every request, log redacted verification, reserve idempotently, obtain shared deterministic approval, delegate only to shared clients, persist full outcomes, and expose manual execute/close/close-all only for verified demo positions.

- [ ] **Step 4: Implement broker reconciliation**

Match only persisted Ghost venue IDs/tags, track partial/rejected/cancelled/unsafe states, never adopt unrelated positions, and expose mismatch/close-only recovery state.

- [ ] **Step 5: Run execution and reconciliation tests**

Run: `python -m pytest tests/ghost_trade/test_demo_execution.py tests/ghost_trade/test_reconciliation.py -q`

### Task 10: Independent DEMO_AUTO lifecycle

**Files:**
- Modify: `ghost_trade/scheduler.py`
- Modify: `ghost_trade/service.py`
- Modify: `ghost_trade/api.py`
- Modify: `static/react-app/app/src/features/ghostTrade/useGhostTrade.ts`
- Modify: `static/react-app/app/src/components/panels/GhostTradePanel.tsx`
- Test: `tests/ghost_trade/test_demo_auto.py`
- Test: `static/react-app/app/src/features/ghostTrade/GhostTradeAuto.test.tsx`

**Interfaces:**
- Produces: audited `set_demo_auto_enabled(enabled, operator_context)` and committed-scan consumer.
- Guarantees: restart effective state false and no unverified broker request.

- [ ] **Step 1: Write failing backend/frontend auto tests**

Cover explicit toggle, restart-off, mode mismatch, account verification loss, scan-transaction ordering, duplicate/cooldown/risk gates, scheduler lease ownership, bounded retries, audit records, frontend confirmation, and server-authoritative reset.

- [ ] **Step 2: Run focused auto tests and confirm red**

Run: `python -m pytest tests/ghost_trade/test_demo_auto.py -q`

Run from `static/react-app/app`: `npm test -- src/features/ghostTrade/GhostTradeAuto.test.tsx`

- [ ] **Step 3: Implement independent auto consumer and UI control**

Consume only committed eligible Ghost signals, revalidate every gate/attestation, never call existing auto-trader, stop on verification/provider failure, and record every decision.

- [ ] **Step 4: Run focused auto tests**

Repeat the two commands from Step 2; expected all pass.

### Task 11: Final focused validation and acceptance audit

**Files:**
- Modify only if validation exposes Ghost defects.
- Output: runtime Ghost database/log evidence and completion inventory; no generated secrets.

**Interfaces:**
- Verifies all acceptance criteria against code/tests/runtime evidence.

- [ ] **Step 1: Run focused Ghost backend tests**

Run: `python -m pytest tests/ghost_trade -q`

Expected: all Ghost tests pass; do not run the full repository suite.

- [ ] **Step 2: Run focused Ghost frontend tests**

Run from `static/react-app/app`: `npm test -- src/features/ghostTrade`

Expected: all Ghost frontend tests pass.

- [ ] **Step 3: Run the frontend production build**

Run from `static/react-app/app`: `npm run build`

Expected: TypeScript and Vite build pass and the verified static manifest references the new bundle.

- [ ] **Step 4: Start backend in SHADOW and run one complete scan**

Use the repository's normal backend start command with Ghost committed defaults. Query health, universe, scan, signals, groups, positions, and performance. Record MT5/Bybit discovered/eligible/scored/skipped counts, group breakdowns, top three per group, and exact skip reasons.

- [ ] **Step 5: Prove SHADOW and engine isolation**

Verify no Ghost execution attempts/broker calls occurred, all existing engine status/routes still load, Ghost score/state remains separate, and auto-trader fields are unchanged.

- [ ] **Step 6: Validate demo safety**

Always run mocked verified/unverified provider integration tests. If actual connected accounts are positively verified as demo/testnet, perform one operator-authorized minimum-size Ghost demo execution per available provider and close/reconcile it. Otherwise report real demo validation as unavailable; never test on an unknown/real account.

- [ ] **Step 7: Complete requirement-by-requirement audit**

Inventory exact files, migrations, routes, components, config/env keys, runtime counts/signals, test/build commands, shadow proof, demo verification states, skips, limitations, and start command. Do not claim completion for any criterion lacking direct evidence.

