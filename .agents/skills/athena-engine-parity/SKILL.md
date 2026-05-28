---
name: athena-engine-parity
description: Use when investigating live-chart, backtest, or scan parity for Engine A, Engine B, Engine C, or Engine D — candle policy, provider routing, data freshness, ATR provenance, scoring drift, overlay contracts, UI chart payloads, or AI review context mismatches. Do not use for execution-gate changes, unrelated Python cleanup, or threshold tuning unless the user explicitly requests it.
---

# Athena engine parity

Verify outputs match intended data source, candle policy, scoring contract, and consumer payloads without changing strategy semantics.

## Steps

1. Identify engine and route (live scan, backtest, API, UI).
2. Trace provider → cache → engine → payload consumer.
3. Compare score/confidence/overlay fields to tests and `config.yaml` sources.
4. Document exact drift point before patching.

## Inspect first

`scanner.py`, `candles_cache.py`, `candle_feeds.py`, engine modules (`forex_scoring.py`, `factor_scoring.py`, `market_structure.py`, `engine_c.py`, `scalp_engine.py`), `routes_market_data.py`, chart UI under `static/react-app/`.

## Boundaries

- No threshold changes unless explicitly requested.
- Do not collapse Engine A/B/C/D responsibilities.
- No long backtest matrix unless requested.
- Every fix needs a regression test and proof of no unintended scoring change.
