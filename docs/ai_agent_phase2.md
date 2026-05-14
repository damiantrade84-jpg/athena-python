# AI Agent Phase 2

Phase 2 extends the advisory AI layer with market intelligence, pair context, structured Vision freshness, strategist summaries, and richer trade-chat responses.

## Market Intelligence

`market_intelligence.get_market_intelligence(symbol, asset_type)` returns a cached `market_intelligence.v1` packet. It uses existing repo/local sources only:

- `event_risk.check_event_risk` for calendar risk when available.
- `cot_feed.get_cot_net/get_cot_z` for COT positioning when configured.
- `data_feeds` public funding helpers for crypto funding when available.
- `pair_context.build_pair_context` for local trade outcomes and pair notes.

Unavailable or stale sources are reported through `source_status`, `warnings`, and `freshness_status`; macro, DXY, VIX, SPX, gold, BTC, and yield fields are not invented when no current local source exists.

## Pair Context

`pair_context.build_pair_context(symbol, asset_type)` is read-only. It can include recent local learning/audit outcomes, COT context, and crypto funding context. Missing DBs/tables return stable warnings instead of raising.

## Vision Freshness

`vision_trade_read.py` parses `vision_trade_read.v1` JSON when present and falls back to the existing footer contract. Missing or stale `chart_timestamp` / `latest_candle_ts` sets `allowed_for_execution_context=false`. The old footer tokens remain unchanged for backward compatibility:

- `RIGHT EDGE`
- `TF ALIGNMENT`
- `SCALP/INTRADAY/SWING RATING`
- `SCALP/INTRADAY/SWING LEVELS`

Chart-analysis now returns `structured_trade_read` and uses a cache key based on symbol, timeframe set, direction, trace id, latest candle timestamp, and image hash when those fields are available. Incomplete keys are marked UI-only.

## Strategist

`ai_strategist.py` provides:

- `strategist_morning_brief()`
- `strategist_pre_trade_check(packet)`
- `weekly_strategy_retrospective()`

These functions are read-only and advisory. They do not execute trades, mutate thresholds, or directly block execution. The weekly retrospective returns `do_not_auto_apply=true`.

## Trade Chat

`POST /api/ai/trade-chat` now returns structured trading-desk sections:

- market read
- trade thesis
- supports
- contradictions
- confirmation needed
- invalidation
- historical analogue summary
- risk warning
- final action
- market intelligence summary
- Vision summary

When market intelligence, Vision, or historical samples are unavailable or insufficient, the response says so rather than inferring missing data.

## Safety Boundaries

AI remains advisory-only. It cannot execute trades, approve orders, change thresholds/config, bypass guardian/freshness/kill-switch/RR/spread/fee/risk gates, or upgrade failed deterministic gates into executable trades. Existing execution and risk paths remain authoritative.

## Known Limitations

Market intelligence is a safe scaffold, not a full macro data feed. Cross-asset fields are explicitly unavailable unless existing local sources provide current data. Strategist outputs are surface-level summaries until wired to richer portfolio/outcome repositories.
