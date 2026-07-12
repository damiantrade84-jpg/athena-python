# Ghost Trade Design

## Status and scope

This design adds Ghost Trade as a standalone Athena engine with its own universe, score, signal lifecycle, positions, performance, API, scheduler, and frontend panel. It reuses shared market-data and broker clients only through narrow adapters. It does not read or mutate Engine A, B, C, D, ASE, guardian decisions belonging to other engines, or the existing auto-trader state.

Ghost Trade supports `SHADOW`, `DEMO_MANUAL`, and `DEMO_AUTO`. The committed default is `SHADOW`. Live trading is structurally prohibited: configuration parsing forces `live_trading_allowed` to `false`, adapters reject any non-demo/non-testnet account, and no Ghost API accepts a live override.

Delivery is staged, but the feature is complete only after all stages and acceptance checks pass:

1. SHADOW vertical slice.
2. DEMO_MANUAL with verified broker adapters.
3. DEMO_AUTO with independent portfolio controls.
4. Integration, frontend build, shadow scan, and controlled demo validation.

## Architectural boundaries

Create `ghost_trade/` as the authoritative backend package. It owns all Ghost models and state. Existing modules are consumed through adapters; no Ghost module imports Engine A scoring, Engine B levels, Engine C consensus, Engine D scalp logic, ASE signals, or the existing auto-trader.

The only application-level wiring is:

- `athena.py` constructs a small runtime dependency object and calls `register_ghost_trade_routes(app, runtime)` plus scheduler lifecycle hooks.
- `config.py` parses and validates Ghost configuration and environment overrides.
- `config.yaml` contains safe defaults and group profiles.
- the React application adds one `ghostTrade` panel and its typed API client.

The package is divided by responsibility:

```text
ghost_trade/
  __init__.py
  config.py
  models.py
  symbols.py
  universe.py
  market_data.py
  structure.py
  momentum.py
  volatility.py
  exhaustion.py
  levels.py
  scoring.py
  scanner.py
  eligibility.py
  signal_service.py
  position_sizing.py
  risk.py
  persistence.py
  reconciliation.py
  scheduler.py
  service.py
  api.py
  execution/
    __init__.py
    base.py
    mt5_demo.py
    bybit_demo.py
```

Pure scoring modules accept immutable candle sequences and return typed dataclasses. Broker, database, clock, and candle-provider access is confined to adapters/services so unit tests do not require terminals, credentials, or the network.

## Configuration and safety invariants

The root configuration is:

```yaml
ghost_trade:
  enabled: true
  mode: SHADOW
  live_trading_allowed: false
  demo_auto_enabled: false
  minimum_confirmed_score: 0.35
  minimum_entry_quality: 0.60
  minimum_raw_rr: 1.00
  risk_per_trade: 0.0025
  max_risk_per_trade: 0.005
  max_total_risk: 0.02
  max_open_positions: 8
  scan_interval_seconds: 3600
  signal_cooldown_seconds: 14400
  exit_strategy: STRUCTURAL
```

Environment overrides are limited to `GHOST_TRADE_ENABLED`, `GHOST_TRADE_MODE`, `GHOST_TRADE_DEMO_AUTO_ENABLED`, `GHOST_TRADE_MAX_RISK_PER_TRADE`, `GHOST_TRADE_MAX_TOTAL_RISK`, and `GHOST_TRADE_SCAN_INTERVAL_SECONDS`. Numeric overrides are range-checked. Invalid values fail configuration loading rather than silently widening risk. The normalized config object always sets `live_trading_allowed=False`, regardless of YAML or environment input.

`DEMO_AUTO` requires all of the following at runtime: mode is `DEMO_AUTO`, `demo_auto_enabled` is true, the venue-specific demo verifier passes, the scheduler owns the current scan lease, the signal remains fresh and eligible, and all Ghost portfolio gates pass. Restart initialization always resets the effective auto switch to false. A stored preference may be displayed for operator convenience but cannot reactivate automatic execution without a new explicit operator action and audit record after restart.

All execution requests pass this sequence:

```text
authenticated API/scheduler
  -> persisted signal lookup
  -> stable signal/version check
  -> Ghost eligibility revalidation
  -> demo-account attestation
  -> fresh quote/spread validation
  -> fixed-risk sizing
  -> Ghost duplicate/cooldown/portfolio gates
  -> shared guardian and risk approval
  -> venue demo adapter
  -> persisted execution attempt
  -> broker reconciliation
```

Any missing, stale, malformed, unsupported, or ambiguous input rejects execution with a stable reason code. Exceptions are logged and returned as explicit failures; they never produce an empty success response.

## Domain models and identifiers

Use frozen dataclasses/enums internally and JSON-safe dictionaries at the API boundary. Required enums are `GhostMode`, `Venue`, `AssetGroup`, `Style`, `Direction`, `VolatilityRegime`, `SignalStatus`, `PositionMode`, `PositionStatus`, `DemoVerificationStatus`, and `ExitStrategy`.

`GhostInstrument` stores venue, exact broker symbol, canonical symbol, group/subgroup, base/quote assets, metadata provenance, trading status, price/quantity filters, supported timeframes, and skip reasons. The canonical symbol service uses reliable MT5/Bybit metadata first, then a configurable override map, then conservative parsing. Unknown instruments are classified as `other` and remain visible.

Signal IDs are SHA-256 hashes of engine version, venue, broker symbol, style, decision timestamp, and confirmed-candle timestamps. Scan IDs and execution IDs are UUIDs. Repeated scans of the same confirmed data upsert the same signal rather than creating duplicate execution opportunities.

A `GhostSignal` stores confirmed and live scores separately; all D1/H4/H1/M15 components; candle confirmation timestamps; entry, stop, target, ATR distances, structural R:R, and level provenance; eligibility state and every rejection reason; group/global ranks; data freshness; spread; score version; and source scan ID. `canExecute` is derived server-side and cannot be supplied by a client.

## Universe discovery and classification

### MT5

The MT5 adapter calls the connected terminal's symbol discovery API, retains exact broker symbols, and attempts selection only as a read-only eligibility check. Instruments are rejected from scoring when expired, disabled, not selectable, unsupported by the candle loader, or missing required history. They are still persisted with skip reasons and shown in universe/error views.

Metadata such as path, description, currency base/profit/margin, trade mode, expiration, digits, point, tick size/value, contract size, volume limits, stop level, and freeze level drives classification and sizing. Suffixes and prefixes do not affect the canonical identity.

### Bybit

The Bybit adapter uses the configured shared client to load active instruments by allowed categories. Linear USDT perpetuals are mandatory; spot is included only when the existing execution client supports it safely. It rejects inactive, pre-launch, delisted, filter-less, insufficient-history, and configured low-liquidity instruments. Exchange symbol/filter metadata is retained for rounding and minimum-notional checks.

Universe discovery never submits orders and never calls live market endpoints through an execution-configured production client for demo validation.

## Candle integrity and scoring

The market-data adapter returns a `CandleSet` per instrument/profile with provider identity, requested/received counts, open/close timestamps, freshness state, and confirmed/forming designation. D1, H4, and H1 confirmed scoring always drops the forming candle. M15 may expose a separately labelled live overlay. Timeframes are aligned to one `as_of` timestamp; stale or incomplete higher-timeframe data produces visible rejection reasons.

Intraday uses D1/H4/H1 plus optional M15 overlay. Swing uses D1/H4 with optional H1 refinement. H1 is never labelled scalp.

The initial confirmed model exactly follows the structural-challenger formulas in the product specification:

- confirmed five-bar fractals become visible only after `t+2` closes;
- D1 direction uses three ordered confirmed swing highs and lows, displacement/ATR, persistence, and structural-event freshness;
- H4 momentum uses robust-scaled ROC(10) and signed participation pressure, renormalizing when volume is unavailable;
- direction confidence is clamped `0.55 * structure + 0.45 * momentum`, with fixed `+/-0.25` direction thresholds;
- H1 structural stop/target geometry uses confirmed swings and validates level side, finite risk, target room, broker distances, and confirmation time;
- entry quality uses the specified 0.40/0.25/0.20/0.15 R:R, room, pullback, and trigger weights;
- H4 volatility regimes and penalties use the specified 50-bar percentiles;
- contextual exhaustion is capped at 0.30;
- confirmed score is `clip(abs(direction_confidence) * entry_quality - volatility_penalty - exhaustion_penalty, 0, 1)`.

The live overlay is a separate object computed from the active execution-timeframe candle. Its adjustment is clamped to `[-0.10, +0.10]`, and `displayScore=clip(confirmedScore+liveAdjustment, 0, 1)`. It cannot alter confirmed components, direction, structural stop/target, stored eligibility, or an already persisted signal identity. Before execution, the service revalidates price-dependent room/spread and broker constraints without rescoring confirmed history.

All instruments produce either a score payload or explicit data/classification errors. Neutral and ineligible signals remain visible.

## Eligibility, risk, and sizing

Signal visibility is independent of execution eligibility. Initial eligibility uses the configured floors, fresh data, valid structural geometry, acceptable spread, venue demo verification, duplicate/cooldown checks, and available Ghost risk. Structural is the only enabled exit strategy.

Risk is fixed per trade at 0.25% of verified demo equity by default, capped at 0.50%; total Ghost risk is capped at 2.00%. No volatility regime increases risk. Position sizing uses broker tick/contract/quantity metadata, rounds down to permitted steps, and rejects uncertain conversions, zero/negative stop value, minimum-notional failures, or unavailable account-currency conversion.

Ghost risk state counts only Ghost positions for its own caps. Existing positions from other engines are not blocked or modified; same-symbol conflicts are displayed diagnostically. The shared guardian/risk layer is still consulted before any broker request and may veto Ghost execution.

## Demo execution adapters

### MT5 adapter

Before every order, the adapter verifies terminal connectivity, account data, and `trade_mode` against MT5 demo/contest constants. Unknown or real modes reject. It logs server, redacted account identifier, mode, and attestation result without secrets. It selects the exact broker symbol, reads the fresh bid/ask, validates spread, stop/freeze distance, trade mode, tick size, and volume bounds, then delegates a pre-approved request to the shared MT5 executor. Unsupported fill modes may use the executor's bounded supported-mode fallback. Broker response, retcode, order/position IDs, and accepted SL/TP are persisted. Post-entry SL/TP modification is allowed only when required by the verified demo broker and failure triggers an explicit unsafe-position reconciliation state.

### Bybit adapter

Before every order, the adapter verifies that the shared client is configured for testnet or Bybit demo trading and that resolved API URLs are non-production. Unknown endpoint/account state rejects. It validates category, filters, position mode/index, price/quantity rounding, and minimum notional; delegates to the shared Bybit executor; persists order/position IDs and partial-fill state; and uses reduce-only close requests. It never falls back to production.

Manual close and close-all routes operate only on persisted Ghost demo positions, re-attest the venue, require reduce-only/position-specific semantics, and record every result. Shadow closes are local state transitions only.

## Persistence and reconciliation

Use a dedicated SQLite database under Athena's runtime data directory, not Engine A/B audit tables. Schema migrations are versioned and transactional. Tables are:

- `ghost_schema_version`
- `ghost_scan_runs`
- `ghost_instruments`
- `ghost_signals`
- `ghost_signal_components`
- `ghost_execution_attempts`
- `ghost_positions`
- `ghost_position_events`
- `ghost_closed_trades`
- `ghost_runtime_settings`

Indexes cover canonical symbol, venue/broker symbol, scan time, signal status/score/group, open position identity, and execution idempotency keys. JSON diagnostics are stored alongside queryable core columns. Monetary/price values are serialized without lossy frontend rounding.

Reconciliation polls only verified demo/testnet accounts, matches using persisted venue IDs and Ghost order tags, records fills/rejections/cancellations/SL/TP/close events, and never adopts unrelated broker positions as Ghost positions. Missing broker positions become explicit reconciliation mismatches until resolved. Shadow positions update from confirmed/live market data without broker calls.

Performance derives only from closed Ghost trades and exposes sample size, mean/median R, win rate, profit factor, drawdown, long/short, group, source, score band, regime, R:R bucket, symbols, concentration, and shadow/demo partitions.

## Scheduler and concurrency

`GhostScheduler` is independent from Athena's auto-trader. It has a single-process scan lease, one active scan at a time, bounded worker count, per-provider throttling, and a stop event. Confirmed scans run after timeframe closes plus a configurable periodic fallback; overlays may refresh more often without recomputing D1/H4 history. Cache keys include venue, broker symbol, timeframe, and confirmed close time.

Manual scans return `409 scan_already_running` when the lease is held. DEMO_AUTO consumes persisted eligible signals only after a scan transaction commits. A restart never resumes automatic execution until the explicit runtime toggle is enabled again.

## API design

Create `ghost_trade/api.py` using Athena's `add_url_rule` registration pattern. Routes inherit the existing authentication and rate limiting in `athena.py`; state-changing routes also require JSON content, stable signal/position IDs, and server-side revalidation.

The exact routes are:

```text
GET  /api/ghost-trade/health
GET  /api/ghost-trade/config
PUT  /api/ghost-trade/config
GET  /api/ghost-trade/universe
POST /api/ghost-trade/scan
GET  /api/ghost-trade/scans/current
GET  /api/ghost-trade/signals
GET  /api/ghost-trade/signals/<signal_id>
POST /api/ghost-trade/signals/<signal_id>/execute-demo
POST /api/ghost-trade/signals/<signal_id>/dismiss
GET  /api/ghost-trade/positions
GET  /api/ghost-trade/positions/history
POST /api/ghost-trade/positions/<position_id>/close
POST /api/ghost-trade/positions/close-all
GET  /api/ghost-trade/performance
GET  /api/ghost-trade/groups
```

List responses include data plus pagination/filter metadata and surfaced provider errors. Filters match the specification. Config updates expose only a safe allowlist; attempts to enable live trading or exceed hard risk bounds return `400`. Execution safety failures return `403`, conflicts/idempotency failures return `409`, provider unavailability returns `503`, and validation failures return `400`.

## Frontend design

Add `ghostTrade` to `PanelId`, the Home panel map, and primary Sidebar navigation. `GhostTradePanel.tsx` owns the page layout; typed hooks/API functions live under `src/features/ghostTrade/`. No Ghost state is merged into global Engine signals/positions.

The panel contains:

- a header with mode, venue verification, scan timing/counts, eligible signals, open positions/risk, Scan Now, and the independent auto toggle;
- a persistent safety banner with exactly one unambiguous verification state per venue/mode;
- group summary/navigation for Forex, Crypto, Metals, Energy, Other Commodities, Indices, Equities, and Other;
- sortable card/list views within each group;
- a signal drawer with component diagnostics, trade geometry, decision trace, missing inputs, and mode-appropriate actions;
- separate open shadow/demo positions;
- separate Ghost performance.

Cards show canonical/exact symbol, venue, group/style, direction, confirmed/live/display scores, execution eligibility, entry/structural levels/R:R, regime, freshness, spread, age, D1/H4/H1/M15 component rows, group/global rank, and all rejection reasons. Score labels come from configured bands and are never described as probabilities.

The UI hides Execute Demo in SHADOW and when verification fails. Buttons remain disabled while requests are pending, display server error codes, and refresh authoritative state after mutation. DEMO_AUTO requires an explicit confirmation dialog describing demo-only status and resets visually from the server's post-restart false state.

## Error handling and observability

Every scan has a scan ID; every log line carries scan/signal/execution identifiers where applicable. Structured logs cover discovery, classification, candles, freshness, scoring, skipped instruments, eligibility, demo attestation, risk/sizing, submission, broker response, and reconciliation. Secrets and candle arrays are never logged.

Stable error codes cover provider disconnection, failed demo verification, missing/stale history, unsupported/disabled instruments, invalid levels, spread, sizing, duplicate/cooldown/risk, order rejection/partial fill, reconciliation mismatch, and scan concurrency. API responses include human-readable messages without exposing credentials or raw provider internals.

## Test and validation strategy

Backend unit tests cover every scoring invariant, canonical/group mapping, confirmed/forming separation, overlay cap, sizing/rounding, demo rejection, duplicate/cooldown/portfolio gates, persistence, and reconciliation. API tests use Flask's test client and mocked provider/adapters for every route and safety branch. Integration tests exercise discovery-to-signal and signal-to-mocked-demo lifecycles without live endpoints.

Frontend Vitest tests cover navigation, group/card/detail rendering, all safety modes, filters/sorting, actions, positions/performance, loading/error states, and auto-toggle confirmation. The production build must pass.

Final validation runs focused Ghost backend tests, focused Ghost frontend tests, and the frontend production build. The backend starts in SHADOW, discovers connected MT5/Bybit universes, performs one scan, reports counts/groups/top signals/skip reasons, and proves zero order submissions through execution-attempt records and adapter spies/logs. Existing engine route smoke tests verify they still load. A real demo/testnet order is attempted only when the account is positively verified; otherwise the completion report records demo validation as unavailable and uses the mocked verified path.

## Delivery milestones and completion evidence

### Milestone 1: SHADOW vertical slice

Complete config, models, universe, candles, scoring, levels, persistence, scan service, shadow tracking, read/write APIs that cannot execute, grouped UI, and focused tests. Evidence is a SHADOW scan with discovered/scored/skipped counts and no execution attempts.

### Milestone 2: DEMO_MANUAL

Complete attestation, sizing, risk, provider adapters, manual execute/close, persistence/reconciliation, UI actions, and mocked verified/unverified integration tests. Evidence includes real-account rejection and a mocked demo lifecycle. A real demo validation is conditional on credentials and connected account state.

### Milestone 3: DEMO_AUTO

Complete independent scheduler consumption, explicit default-off toggle, cooldown/duplicate/portfolio controls, restart behavior, auto audit trail, and tests. Evidence proves restart-off and that unverified providers never receive an order request.

### Milestone 4: final validation

Run the specified focused suites/build/SHADOW scan, verify existing engines load, verify no SHADOW orders, optionally perform one minimum-size verified demo order per available provider, and produce the requested completion inventory. The feature is not complete if backend/frontend are disconnected, any route/action is a placeholder, live-account rejection is untested, or any acceptance criterion lacks evidence.

## High-risk review

- Risk: an order reaches a live account. Mitigation: forced-false live config, per-request venue attestation, endpoint verification, no fallback, shared risk approval, and real/unverified-account rejection tests.
- Risk: Ghost mutates another engine or auto-trader. Mitigation: independent package/database/scheduler/UI state, narrow application wiring, and regression smoke tests.
- Risk: stale/forming data becomes confirmed evidence. Mitigation: typed candle states, as-of alignment, explicit forming-bar drop, timestamps in signal identity, and point-in-time tests.
- Risk: incorrect sizing exceeds intended risk. Mitigation: broker metadata, round-down rules, hard percentage caps, rejection on uncertain conversion/filter data, and provider-specific sizing tests.
- Risk: duplicate or repeated orders. Mitigation: deterministic signal IDs, execution idempotency keys, transactionally reserved attempts, cooldown/open-position gates, and reconciliation tests.
- Risk: partial fill or missing protective levels creates unmanaged exposure. Mitigation: explicit unsafe/reconciliation states, bounded protective-level handling, visible alerts, close-only recovery actions, and mocked partial-fill tests.
