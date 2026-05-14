# ATHENA Audit Contract

This reference is historical full-audit context. Do not load it for normal targeted fixes. Load it only when the user explicitly asks for a historical/full audit comparison, a full-system audit, or this named reference.

The source text for the findings template ends after the `Current code` block header. Any fields beyond that point are not verified and are intentionally not invented here.

## Repo Mental Model

### FOUNDATION / POLICY

- `AGENTS.md` only for Codex startup context
- `config.yaml`

### DATA / MARKET STATE

- `candle_manager.py`
- `candle_feeds.py`
- `candles_cache.py`
- `data_feeds.py`
- `athena_runtime.py`

### ENGINE A - FACTOR / INDICATOR SCORING

- `athena.py`
- `scoring.py`
- `factor_scoring.py`
- `forex_scoring.py`
- `confidence_engine.py`
- `indicators.py`
- `regime.py`
- `feature_normalizer.py`
- `adaptive_weights.py`
- `advisory_thresholds.py`

### ENGINE B - NAKED STRUCTURE

- `market_structure.py`
- `engine_b_ai.py`
- `zone_registry.py`
- `volume_profile.py`

### ENGINE C - CONSENSUS / GOVERNANCE

- `engine_c.py`
- `meta_learner.py`
- `confidence_engine.py`
- `stability_monitor.py`
- `divergence_monitor.py`
- `guardian.py`
- `guardian_routes.py`

### ENGINE D - SCALP

- `scalp_engine.py`

### AI / VISION / REVIEW

- `athena.py`
- `ai_schemas.py`
- `ai_utils.py`
- `engine_b_ai.py`

### RISK / EXECUTION

- `execution.py`
- `risk_engine.py`
- `risk_shield.py`
- `mt5_executor.py`
- `bybit_executor.py`
- `auto_trader.py`
- `execution_lifecycle.py`

### RESEARCH / BACKTEST / CALIBRATION

- `backtest_runner.py`
- `research_metrics.py`
- `calibration.py`
- `divergence_monitor.py`

### UI / TELEGRAM / OPERATOR SURFACES

- `static/index.html`
- `telegram_bot.py`
- `telegram_notify.py`
- `athena.py`

### DATABASE / AUDIT STATE

- `audit.db`
- `audit_log`
- all DB access paths in execution, monitoring, learner, advisory, and UI routes

## Non-Negotiable System Invariants

### 1. Market-state integrity

- all engines participating in one decision must use the correct bar state
- do not mix forming-bar and confirmed-bar logic without explicit design
- do not let lower timeframes peek past higher timeframe decision boundaries

### 2. Live/backtest parity

- same logic must mean the same thing live and historically unless intentionally separated
- no live-only thresholds sneaking into backtest
- no present-day values injected into historical loops

### 3. Risk truth

- `risk_check()` must not be bypassed
- hard rejects must stay hard
- kill switch must apply everywhere
- sizing must come from risk, not executors

### 4. Execution lifecycle integrity

- no orphan child orders
- parent/child trade state must reconcile
- DB state must match broker state as closely as possible

### 5. Engine isolation

- Engine A, B, C, and D must not silently borrow each other's score semantics
- Engine B monitoring must use Engine B score truth
- Engine D must not be mislabeled as Engine A or B

### 6. Operator truth

- dashboard, API, Telegram, DB, and monitoring must all represent the same underlying truth
- no misleading fallback values
- no fake "healthy" status on insufficient data

## Required Audit Method

### Phase 0 - Recon

- read `AGENTS.md` and exact target files
- read `config.yaml`
- read exact target files
- list exact file paths inspected
- map entrypoints, helpers, downstream consumers, DB paths, and UI paths

### Phase 1 - Control flow

Trace:

- live path
- scan path
- execute path
- auto-trader path
- monitoring path
- research/backtest path
- UI/API/Telegram surfaces

### Phase 2 - Data truth

Verify:

- source of candles
- confirmed versus forming bars
- timeframe alignment
- cache behavior
- stale-state behavior
- no lookahead leakage

### Phase 3 - Engine truth

Verify only the engines touched:

- Engine A factor math, weights, and thresholds
- Engine B structure, score, pct, max_possible, and SL/TP
- Engine C blending, veto, conviction, and reliability
- Engine D scalp isolation and routing

### Phase 4 - Risk / execution truth

Verify:

- risk gate
- kill switch
- sizing
- SL width rules
- lifecycle management
- `audit_log` writes
- broker reconciliation

### Phase 5 - Monitoring / operator truth

Verify:

- entry score source
- current score source
- engine labeling
- score scale consistency
- API payload
- UI rendering
- Telegram rendering
- DB semantic consistency

### Phase 6 - Performance

Identify only proven bottlenecks:

- repeated full-array rescans
- repeated indicator recomputation
- hot-path DB churn
- stale cache growth
- blocking network calls inside loops

## Confirmed Findings Format

Use exactly the fields below when the user wants the strict format from the provided spec. Do not invent extra required fields unless the user provides the missing source text.

````md
### BUG/BOTTLENECK [N] - [SEVERITY: CRITICAL / HIGH / MEDIUM / LOW]
**System Area:**
**File:**
**Line(s):**
**Current code:**
```python
# exact code
```
````
