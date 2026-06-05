# Candle Understanding Layer

Diagnostic-first candle context for Athena AI review and Engine D advisory payloads.

## Principles

- Candlestick patterns are **weak standalone signals**. Context gates their usefulness.
- Read order: **regime → location → last 3 anatomy → SMC (sweep/BOS/FVG/OB) → volume/effort → directional view**.
- RSI is a weak confirmer only, not a hard gate.
- All contributions remain **report-only** until Athena validates n≥30 per asset class and SQN>2.0.

## Modules

- `athena_ai/price_action_facts.py` — atomic deterministic facts (anatomy, sweep, FVG fill, OB/FVG confluence, effort-vs-result, regime gate).
- `athena_ai/candle_understanding.py` — orchestrator + report-only score block.

## Config (default-safe)

| Key | Default | Meaning |
|-----|---------|---------|
| `CANDLE_UNDERSTANDING_ENABLED` | `true` | Emit diagnostics |
| `CANDLE_UNDERSTANDING_REPORT_ONLY` | `true` | No live scoring impact |
| `CANDLE_UNDERSTANDING_LIVE_WEIGHT` | `0.0` | Effective score multiplier into engines |
| `ENGINE_A_CANDLE_TIMEFRAME_CONFIDENCE` | D1=1.0 … M1=0.2 | Engine A discounts noisy LTF candles |
| `ENGINE_D_CANDLE_EXECUTION_CONFIDENCE` | M5/M3=1.0 … | Engine D does not over-penalize execution TFs |

## Engine A vs Engine D timeframe confidence

Engine A chart review uses `ENGINE_A_CANDLE_TIMEFRAME_CONFIDENCE` (M15 discounted vs D1 full).

Engine D scalp workbench uses `ENGINE_D_CANDLE_EXECUTION_CONFIDENCE` (M5/M3 at full weight for execution context).

## Volatility regime multipliers

`ENGINE_A_VOLATILITY_REGIME_MULTIPLIERS` exists in config but is **neutral at 1.00** (report-only phase). The candle regime gate can become a consumer of these values after validation.

## Safety

- No execution path, no risk sizing, no threshold changes by default.
- Sweep requires a **named liquidity pool** + reclaim; random wicks are not sweeps.
- Suppressed directions still emit raw anatomy and diagnostics.
