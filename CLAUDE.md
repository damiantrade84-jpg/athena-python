---
description:
alwaysApply: true
---

# Sentinel Pro v4.0 — Claude Code Instructions

---

## ⚠️ Policy — Scoring Gates (STRICT)

**Do not change anything that alters live or backtest scoring unless the user explicitly instructs it.**

- **Engine A live:** `MIN_CONFLUENCE_CLASS`, `MIN_CONFLUENCE_GROUP`, `MIN_FOREX_CONFLUENCE`, `PAIR_PROFILES.min_confluence`, `AUTO_TRADE_MIN_SCORE`, `SCAN_QUANTILE_*`, confluence logic in `scoring.py` / `factor_scoring.py` / `forex_scoring.py`, `analyze_pair` tiering.
- **Engine A backtest:** `BT_MIN`, `PAIR_PROFILES.bt_min`, `get_backtest_min_score_threshold`, backtest score gates in `backtest_runner.py`.
- **Engine B live + backtest:** `NAKED_ENGINE.style_profiles` (`min_score`, `min_rr`), zone multipliers, naked checklist gates in `market_structure.py`.

Cosmetic UI copy is fine. **Do not "tune", "align", or "simplify" thresholds** in passing.

---

## ⚠️ Data Protection

`audit.db` and `candle_cache.db` contain all live trading history.
- **NEVER** delete, overwrite, or zip these files during updates.
- Run `python backup_db.py` before any major code changes.
- Hardcoded DB/reset/restore safeguards must remain untouched unless user explicitly requests that exact change.

---

## Project Overview

Multi-asset algorithmic trading system built on Flask. Covers forex, crypto, stocks, commodities, indices.

**Three trading engines:**
- **Engine A** — Multi-Factor Quantitative Scoring (MFQS): z-score factor engine + forex rule-based scorer
- **Engine B** — Naked price-action (market structure, zones, BOS/CHoCH, FVG, swing sequence)
- **Engine C** — Consensus layer: blends A + B + AI Vision confirmation into a conviction-tiered signal

**Execution:** MT5 for forex/stocks/commodities; Bybit Linear Futures for crypto.

**AI Review:**
- **Marcus Reid (Grok/xAI)** — AI analysis of Engine A + B signals, JSON output with grade/verdict/edgeProbability/style_ratings
- **Claude Vision** — chart screenshot analysis (`/api/chart-analysis`), single/dual/triple TF, structured 7-line footer parsed by `_extract_vision_structured()`

**API Keys required (env only, not yaml):** `EODHD_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`

---

## File Map

| File | Purpose |
|------|---------|
| `athena.py` | Flask app, all API routes, `analyze_pair`, pair lists, `EXPERT_PROMPT`, `_build_signal_message`, `_build_event_risk`, vision prompts | ~8500 lines |
| `candles_cache.py` | TTL candle cache, `fetch_candles` routing (H1→live first; crypto H4/D1→Binance REST), forex WS bar merge |
| `candle_feeds.py` | Live prices, EODHD/Binance WS, `CandleBuilder` (forex H1 via `on_tick`; crypto H1 via `on_kline`), `fetch_candles_live` |
| `athena_runtime.py` | `set_runtime`/`rt()` bindings; `executed_signals` dedupe set |
| `execution.py` | Execution Flask routes (`register_execution_routes`) |
| `scanner.py` | `run_full_scan` and scan pipeline |
| `backtest_runner.py` | Engine A/B backtest loops |
| `data_feeds.py` | HTTP session, EODHD client, funding/OI helpers |
| `news_sentiment_feed.py` | EODHD news + Claude sentiment; optional scan blend; TTL cache |
| `scoring.py` | Confluence engine, vote weights, signal classification, pair profiles, `get_min_confluence_threshold` |
| `factor_scoring.py` | Z-score factor engine — directional (trend, momentum, microstructure, derivatives) + non-directional (trend_strength, volatility, volume, structure) |
| `forex_scoring.py` | Dedicated forex scorer (rules-based, 0–1 scale): trend gate + session + RSI pullback + COT boost |
| `market_structure.py` | Engine B: `NakedEngine`, swing analysis, BOS/CHoCH/FVG/order blocks, shared checklist pass/fail |
| `engine_b_ai.py` | Engine B advisory AI verdict (review only — not a pass/fail gate) |
| `engine_c.py` | Engine C consensus: `ENGINE_C_AB_WEIGHTS` blend, conviction tiers, SL/TP resolution, Vision modifier |
| `confidence_engine.py` | 4-component confidence scoring (indicator agreement, TF alignment, regime fit, liquidity) |
| `indicators.py` | Pure indicator functions (EMA, RSI, MACD, ATR, ADX, BB, Stochastic, Fib, OBV, Squeeze) |
| `regime.py` | Market regime detection: TRENDING / DEVELOPING / RANGING / DEAD_RANGING |
| `risk_engine.py` | Kill switch, drawdown, position sizing, portfolio heat |
| `config.py` | Hardcoded defaults + YAML loader + `_json_safe()` |
| `config.yaml` | All tunable thresholds — edit here, not `config.py` |
| `scalp_engine.py` | Engine D: M15/M5 structural scalping |
| `ai_schemas.py` | Pydantic schemas for AI JSON output (`EngineAResponse`, `EngineBResponse`, `StyleRating`) |
| `mt5_executor.py` | MetaTrader 5 execution |
| `bybit_executor.py` | Bybit Linear (USDT-M) Futures execution |
| `auto_trader.py` | Autonomous scheduler: scan every 30 min, auto-execute per conviction |
| `ai_learning.py` | Outcome extraction → `learning_log`; factor-level analysis for AI calibration |
| `lottery_engine.py` | Lottery analytics + ticket generation (7 modes) + simulation |
| `lottery_service.py` | DB schema, CSV import, draw history |
| `static/index.html` | Dashboard UI: signals, Engine C tab, backtest, ACM charts | ~2550 lines |

---

## Live Data Feed Routing (LOCKED — do not change without explicit approval)

| Asset class | Candles | Live price |
|---|---|---|
| Forex / Stocks / Commodities / Indices | `fetch_mt5()` only — H1/H4/D1 | `symbol_info_tick()` bid/ask mid |
| Crypto H1 | `BinanceCandleWS` `@kline_1h` → `CandleBuilder.on_kline()` | `BinanceLivePriceWS` `!ticker@arr` |
| Crypto H4/D1 | Binance REST native intervals | — |
| EODHD WS | EODHD-sourced pairs only (JSE disabled pairs etc.) | — |

**Candle depth:** `D1_CANDLES: 1001`, `H4_CANDLES: 1001`, `H1_CANDLES: 1001` → ~1000 closed bars after forming bar is dropped. `fetch_mt5()` requests `limit + 100`.

**Never** route MT5-sourced pairs through CandleBuilder or EODHD REST. **Never** write a stale bar close into `_live_prices` for any MT5 pair.

---

## AI Components

### Marcus Reid (Engine A + B analysis) — `EXPERT_PROMPT` in `athena.py`
- System prompt for Grok/xAI; JSON-only output
- **6 absolute rules:** cite specific data or don't claim it; no "will/guaranteed/definitely"; every claim must reference a factor name/score/weight/level from input
- **Analysis order:** factor diagnostics → trend coherence → regime → nondirectional quality → levels → Engine B cross-check → context color only
- **Critical cross-checks (all mandatory):** momentum divergence, direction flip bug, confidence multiplier < 0.5, trendCoherence < 0.7, nondirectionalScore < 0.5, SL > 2%
- **edgeProbability:** anchored formula `base = score_pct × 0.8` + modifiers
- Outputs: `grade`, `verdict`, `narrative`, `edgeProbability`, `riskLevel`, `style_ratings` (scalp/intraday/swing)

### `_build_signal_message` — `athena.py`
Builds the text input sent to Marcus Reid. Sections emitted:
- `=== SIGNAL ===` — pair, direction, score, conviction, regime, style
- `=== ENGINE B (NAKED MARKET STRUCTURE) ===` — reads `signal.get("engine_b") or signal.get("naked_data")` (Engine A uses `"engine_b"`, Engine B scan uses `"naked_data"`)
- `=== FACTOR DIAGNOSTICS ===` — Engine A: per-factor scores/weights (`factorScores`, `factorWeights` camelCase), directional/nondirectional scores, confidence multiplier, trendCoherence, active factors. Engine B: `=== ENGINE B SCORING DIAGNOSTICS ===` (score, bonuses, actionable flag)
- `=== CONFIDENCE ENGINE ===` — `confidenceDetail` components (Engine A only)
- `=== TECHNICALS ===`, `=== LEVELS ===`, `=== WARNINGS ===`, `=== CONTEXT ===`, `=== PORTFOLIO ===`

**Key signal dict keys (camelCase — do not use snake_case):**
- `factorScores`, `factorWeights`, `factorDiagnostics`, `confidenceDetail` (all set in `analyze_pair()`)
- `naked_data` (Engine B scan signals only — contains full naked result)

### Claude Vision — `/api/chart-analysis` in `athena.py`
- Model: `claude-opus-4-6`, temperature: `0.2` (low — factual observation mode)
- Config key: `AI_VISION_TEMPERATURE: 0.2`
- Three modes: single-TF (H4), dual-TF (D1+H4), triple-TF (D1+H4+H1)
- **Read order (2026-03-31):** Prompts enforce **image-first**: read chart(s); instrument + timeframe from **chart top-left** (no guessing from request); **right edge** = last 5 candles on authoritative TF (single/H4, dual/H4, triple/H1); **then** algorithmic context for cross-check. If chart and context conflict, **the image wins**. Prefer prices from chart axis/overlays; use context numbers only when the same level is unreadable on the image.
- **RIGHT EDGE (2026-03-31):** Model must lead with **interpretation** (momentum, control of last closes, continuation vs pullback vs reversal risk, confirm vs threaten algorithmic LONG/SHORT). Avoid long candle-by-colour play-by-play without meaning; optional one compact oldest→newest fact sentence as evidence.
- **Body structure:** Concise sections (e.g. TRADE SNAPSHOT, MARKET STRUCTURE, RIGHT EDGE, factors, verdict) plus required machine footer below.
- **STRUCTURED FOOTER (required — do not remove):** Line `RIGHT EDGE: CONFIRMS | REVIEW | POTENTIAL REVERSAL` immediately before `TF ALIGNMENT` + three `*_RATING` + three `*_LEVELS` — parsed by `_extract_vision_structured()` for Engine C conviction modification.
- **Anti-hallucination rules:** ONLY describe what you can ACTUALLY SEE; never invent patterns; cross-reference algo context **after** the visual read; full annotation legend for Engine B elements.
- **Footer parsing:** Parsed by `_extract_vision_structured()` and used by `apply_vision()` to modify Engine C conviction. Removing or rewording parser tokens breaks Engine C Vision integration and UI level extraction.
- **EODHD news (same file, `fetch_news_context`):** Per-pair `/api/news` and `/api/news-word-weights` use `timeout=15` (was 8s) to reduce read timeouts on slow EODHD responses; failures stay non-fatal.

---

## Signal Flow

```
run_full_scan(style, asset_class)
  └─ for each active pair (ThreadPoolExecutor, SCAN_MAX_WORKERS workers)
       └─ analyze_pair(pair, btc_bias, style)
            ├─ fetch_candles(pair, "D1"/"H4"/"H1", limit)
            ├─ calc_indicators(candles)  → {snap: {...}}
            ├─ calc_confluence(d1i, h4i, h1i, vr, stoch, pair, ...)
            │    ├─ get_pair_vote_weights(pair)
            │    ├─ votes tallied → bull/bear score
            │    ├─ ranging/counter-trend penalties applied
            │    ├─ classify_signal_setup(...)
            │    └─ returns {score, direction, votes, warnings, signalClass, regime, ...}
            ├─ factor_scoring → {factorScores, factorWeights, factorDiagnostics, confidenceDetail}
            ├─ calc_levels(entry, atr, direction, ptype, style)
            └─ returns full signal dict (camelCase keys)
  └─ _classify_signal → tier: "trade" | "watchlist" | "skip"
  └─ apply_correlation_cap(results)
  └─ _json_safe(result)
```

**Engine B flow:**
```
/api/scan-naked | /api/naked-analysis | /api/compare-engines
  └─ NakedEngine.analyze_structure(d1, h4, h1, price, direction, atr, regime)
  └─ NakedEngine.calculate_confidence(res, price, direction, atr, entry_candles, style_profile)
       └─ checklist: structure, location, trigger/entry, room, RR, macro
  └─ Engine B AI review (advisory only — not a pass/fail gate)
  └─ result stored as "naked_data" on Engine B scan signals
```

**Engine C flow:**
```
/api/engine-c-scan
  └─ analyze_pair()          ← Engine A
  └─ NakedEngine.analyze_structure() + calculate_confidence()   ← Engine B
  └─ compute_consensus(signal_a, signal_b, confidence_b, regime, entry, atr)
       ├─ normalise_engine_a() → 0–1
       ├─ normalise_engine_b() → 0–1
       ├─ ENGINE_C_AB_WEIGHTS regime blend: TRENDING={A:0.65,B:0.35}, RANGING={A:0.35,B:0.65}
       ├─ resolve_sl() — structural → ATR-clamped → tighter
       ├─ resolve_tp() — structural if RR≥1.5, else ATR
       └─ returns {conviction, tier, sizing_override, sl, tp, rr, ...}

/api/engine-c-confirm
  └─ apply_vision(consensus, vision_result)
       ├─ CONFIRM + conviction≥0.35 → trade=True
       └─ AVOID/CONTRADICT → trade=False, tier=SKIP
```

**Execution path:**
```
api_execute()
  ├─ signal freshness check (SIGNAL_MAX_AGE_SEC)
  ├─ live re-analyze if stale → HTTP 409 if direction flipped
  ├─ price drift >1% → rebase SL/TP
  ├─ _validate_exit_levels()
  ├─ risk_check() → RiskApproval (kill switch, heat, sizing)
  └─ mt5_execute() OR bybit_execute()
```

---

## Key Functions

### `calc_confluence(d1, h4, h1, vr, stoch, pair, btc_bias, ...)` — `scoring.py`
12 vote slots: `d1_trend(2.0)`, `h1_ema(1.0)`, `d1_adx(1.0)`, `h4_macd(1.0)`, `h4_oscillator(0.75–1.0)`, `volume(0–1.0)`, `funding(0–1.0)`, `session(0–1.0)`, `h4_fib(0.5–1.0)`, `h1_bb(0.5–1.0)`, `weinstein(0–1.0)`, `divergence(1.0 bonus)`
- Session vote adds `W_SESS×0.5` to BOTH sides; `_base_max` subtracts `W_SESS×0.5` to prevent overstatement
- Tie-break: `bull >= bear → LONG` (intentional long bias)
- Returns: `{score, votes, direction, signalClass, regime, factor_scores, factor_weights, factorDiagnostics, confidenceDetail, ...}`

### `get_min_confluence_threshold(pair)` — `scoring.py`
Priority: `PAIR_PROFILES[pair].min_confluence` → `MIN_CONFLUENCE_GROUP[type][score_group]` → `MIN_CONFLUENCE_CLASS[type]`

### `_max_score_for_pair(pair)` — `athena.py`
Theoretical max score using `get_pair_vote_weights(pair)`, minus `W_SESS×0.5`.

### `fetch_candles(pair, tf, limit)` — `candles_cache.py`
- H1 (non-polygon): `CandleBuilder` first if bars ≥ min, else TTL then REST
- Crypto H4/D1: Binance REST native intervals
- MT5 sources: `fetch_mt5()` for all TFs
- Cache TTL keys: **uppercase** `"H1"/"H4"/"D1"` — lowercase misses cache
- TTL: H1=55 min, H4=3h55m, D1=23h

### `NakedEngine.calculate_confidence(...)` — `market_structure.py`
Shared Engine B checklist for live scan, analysis, compare, backtest. Score = checklist count / UI measure. `passed` = naked price-action rules only — not AI-driven.

### `_engine_b_regime_label(h4_candles, pair_type, regime_hint)` — `athena.py`
Shared regime resolver for all Engine B paths. Returns: `TRENDING`, `RANGING`, `HIGH_VOLATILITY`, `LOW_VOLATILITY`.

### `_json_safe(value)` — `config.py`
Recursively replaces `NaN`/`Inf` with `None`, normalizes numpy types. Applied before every `jsonify()`.

### `_can_execute(signal, cfg)` — `auto_trader.py`
Effective min = `max(AUTO_TRADE_MIN_SCORE[type], MIN_CONFLUENCE_CLASS[type])`. `AUTO_TRADE_MIN_SCORE` is a **per-class dict** (crypto=0.80, forex=0.65, stock=0.85, etc.).

### `/api/chart-analysis` — `athena.py`
Requires `ANTHROPIC_API_KEY` env var. `regime` in signal can be dict `{"label":"TRENDING"}` (Engine A) or string `"TRENDING"` (Engine C) — both handled. Context builder is **outside** try/except — keep it simple.

---

## Scoring Architecture Summary

| Engine | Scorer | Scale | Gate key |
|--------|--------|-------|----------|
| Engine A — non-forex | `factor_scoring.py` z-score factor engine | 0–3.0 | `MIN_CONFLUENCE_CLASS[type]` |
| Engine A — forex | `forex_scoring.py` rules-based | 0–1.0 | `MIN_FOREX_CONFLUENCE` |
| Engine B | `market_structure.py` naked checklist | 0–100 pct | `NAKED_ENGINE.style_profiles.min_score` |
| Engine C | `engine_c.py` A+B blend | 0–1 conviction | `ENGINE_C_AB_WEIGHTS` |

**Two different REGIME_WEIGHTS exist — do not confuse:**
- `CONFIG["REGIME_WEIGHTS"]` — adjusts **factor group weights** inside Engine A
- `ENGINE_C_AB_WEIGHTS` in `engine_c.py` — controls **A vs B blend ratio** in Engine C

**`confluencePct` display scaling:** anchored to `get_min_confluence_threshold(pair)` so ~67% = "passing" intent. Engine C uses raw `confluenceScore / maxScore` (not `confluencePct`) for normalization.

---

## Pair Profiles (`config.yaml`)

```yaml
PAIR_PROFILES:
  XAU/USD:
    disable_filters: [obv, session]
    weight_overrides: {h4_fib: 1.5, h1_bb: 0.5}
    min_confluence: 5.8
    bt_min: 4.6
  EUR/USD:
    disabled_votes: [volume]
    weight_overrides: {session: 1.25}
```
Valid vote keys: `d1_trend, h1_ema, d1_adx, h4_macd, h4_oscillator, volume, funding, session, h4_fib, h1_bb, weinstein, divergence`
Valid filter keys: `weinstein, session, regime_transition, obv, funding, squeeze, mean_revert, btc_bias, divergence_warning`
`PAIR_PROFILE_VOTES` and `PAIR_PROFILE_FILTERS` constants live in `config.py` only.

---

## Auto-Trader (`auto_trader.py`)

- Daemon thread wakes every 30s, scans every `AUTO_TRADE_SCAN_INTERVAL_MIN` minutes
- Gate: `score >= max(AUTO_TRADE_MIN_SCORE[type], MIN_CONFLUENCE_CLASS[type])`
- Diagnosing: `SELECT pair, score, direction, error_tag, grade FROM audit_log WHERE grade LIKE 'AUTO%' ORDER BY ts DESC LIMIT 20`
- `grade='AUTO-DEMO'` = success. `grade='AUTO-ERR-DEMO'` = blocked (see `error_tag`)

---

## Bybit Executor (`bybit_executor.py`)

Bybit Linear (USDT-M) Futures, 1x leverage, ISOLATED margin. LONG = market buy, SHORT = market sell.
- SL = `stop_market`, TP = `take_profit_market`
- `adjustForTimeDifference: True`, `recvWindow: 10000` (fixes retCode 10002)
- Post-fill `_validate_exit_levels()` → emergency close if invalid (no retry)
- `_set_trading_stop()` — 2 attempts, 2s sleep, then emergency close
- Env: `BYBIT_API_KEY`, `BYBIT_API_SECRET`, `BYBIT_TESTNET=true`

---

## Audit DB Schema (`audit.db`)

### `audit_log`
Key columns: `ts, pair, score, direction, grade, edge_prob, risk, style, asset_class, score_pct, max_score, votes_json, warnings_json, regime, entry_price, sl, tp, volume, risk_amount, ticket, exit_price, pnl, r_multiple, exit_reason, holding_period_hours, error_tag, fee_cost, factors_json`

- `factors_json` — `{scores, weights, disabled, regime}` from `compute_factor_scores()`. Used by `ai_learning.py`.
- `fee_cost` — Bybit commission. NULL for MT5.

### `backtest_results`
Columns: `id, run_date, pair, asset_type, engine, trades, win_rate, profit_factor, expectancy, sqn, sharpe, sortino, is_score, oos_score, max_dd_pct, bt_min, atr_source, notes`
- `engine`: `"forex_scoring"` | `"factor_scoring"` | `"naked_engine"`
- Endpoints: `GET /api/backtest-history`, `/api/backtest-history/<pair>`, `/api/backtest-best`

Schema auto-migrated on startup. To add a column: add to both `CREATE TABLE` and migration list in `_init_audit_db()`.

---

## Debugging Playbook

**Scoring/confluence complaints require tracing ALL layers:**
1. Data feed → candle cache → `fetch_candles` → `analyze_pair` → response JSON fields → UI formula → `/api/candles` chart
2. Use `confluenceScore`, `maxScore`, `get_min_confluence_threshold(pair)` together — never `confluencePct` alone
3. EMA mismatch: verify same `limit`, same forming-bar drop, same venue (crypto: futures WS for H1)
4. Engine A vs Engine B disagreement is **by design** — different inputs, gates, scales

**Common traps:**
- `confluencePct` optical illusion: display is threshold-anchored, NOT `score/maxScore` — `20fd03a`
- `_json_safe()` must be called before every `jsonify()` (NaN/Inf crash)
- `cache TTL keys must be uppercase "H1"/"H4"/"D1"`
- Engine C `regime` can be string or dict — handle both
- `confidenceDetail` lives on the signal dict (`analyze_pair` sets it); `naked_data` holds the Engine B naked result for Engine B scan signals

---

## Candle Windows

- **Engine A scan:** `D1=1001`, `H4=1001`, `H1=1001` → ~1000 closed bars after forming-bar drop
- **Dashboard `/api/candles`:** `limit=1000` — EMA 21/50/200 right-edge values match scan when limits align
- **Minimums after drop:** `len(d1) >= 220`, `len(h4) >= 50`, `len(h1) >= 50` or pair is skipped

---

## Backtest

### Engine A (`backtest_pair`)
- Swing: D1 bars, max hold 20 bars → TIMEOUT (force-closed at last bar close, signed R, labelled TIMEOUT)
- Intraday: H4 bars, max hold 12 bars
- `MAX_OPEN=3` concurrent positions

### Engine B (`backtest_pair_naked`)
- Hold: `scalp=12`, `intraday=24`, `swing=60` H4 bars
- Enforces same `min_score`, `passed`, `min_rr` as live scan
- 2-bar H4 cooldown after exit bar
- Uses `_engine_b_regime_label()` for zone multipliers

---

## Lottery Lab

**Games:** `lotto` (6/52+bonus), `powerball` (5/50+bonus), `daily_lotto` (5/36)
**Generator modes:** `pure_random · hot_bias · cold_bias · overdue_bias · balanced_mix · pair_bias · anti_crowd`
**Critical invariants:**
- `simulate_generator()` uses incremental counters — never revert to full-history rescan per draw (quadratic regression)
- `generate_tickets()` requires `rules`, `universe`, `main_numbers` initialised before `if draw_context is not None`
- Per-ticket retry cap: `max(25, min(400, len(main_numbers) * 4))` — never remove
- New games must be added to both `LOTTERY_GAME_RULES` (`lottery_engine.py`) AND `LOTTERY_GAME_SPECS` (`lottery_service.py`)

---

## EODHD WebSocket Allocation

- Plan cap: **50 tickers total** across all endpoints
- Current WS: ~17 US stocks/ETFs + ~28 forex/commodities/indices = ~45
- `"ws": False` on a pair = opts out of WS only; scan/backtest/execute unaffected
- To reallocate WS slots: run backtests → query `backtest_results` by SQN → update `"ws"` flags

---

## Python Environment

- Virtualenv: `.\.venv\Scripts\python.exe`
- Tests: `.\.venv\Scripts\python.exe -m pytest tests/ test_indicators.py -v`
- Platform: Windows 11 (dev); Linux also supported

---

## Hard Rules

1. Never bypass `risk_check()` for any execution
2. Never hardcode thresholds in Python — use `config.yaml`
3. Never import from `athena.py` in unit tests — use `indicators` or `scoring` directly
4. Never commit `*.db` files — gitignored (runtime/market data)
5. Never add `e200s` back to `calc_confluence()` — intentionally removed
6. Never use string matching on warning text for signal classification — use structured flags
7. `PAIR_PROFILE_VOTES` / `PAIR_PROFILE_FILTERS` live in `config.py` only
8. Cache TTL dict keys are **uppercase** `"H1"/"H4"/"D1"`
9. `ALL_PAIRS = FOREX_PAIRS + COMMODITY_PAIRS + INDEX_PAIRS + US_STOCK_PAIRS + ETF_PAIRS + JSE_PAIRS + CRYPTO_PAIRS` — JSE_PAIRS must stay in this concatenation
10. Never hardcode pair lists in `static/index.html` — backtest selector fetches `/api/pairs` dynamically
11. `CandleBuilder.seed()` and `bulk_update_d1()` skip `enabled:False` pairs — do not remove these checks
12. `_resolve_scan_style(requested_style, pair)` — use for per-pair style resolution; do not replace
13. Non-blocking I/O during scans: `carry_feed`, `cot_feed`, `duka_volume` must never block the scan thread
14. SQLite: `PRAGMA journal_mode=WAL`, `timeout=15.0`, explicit commits
15. `"ws": False` opts out of WS only — EODHD plan cap is 50 tickers; do not add `ws:True` without removing others
16. BybitWS: `ping_interval=None` in `websockets.connect()` required — disables library keepalive, prevents 1011 errors
17. Engine B AI is **review-only** — do not reintroduce AI as a pass/fail gate without explicit user request
18. Scoring/confluence/candle complaints require tracing data → engine → API fields → dashboard display — never conclude "engine is wrong" from UI alone
19. Feed routing is **locked** — MT5 sources use `fetch_mt5()` only; no CandleBuilder/EODHD REST for MT5 pairs; no stale bar close into `_live_prices`
20. Scoring gates are **locked** — do not modify thresholds, weights, or gate logic unless user explicitly requests it
21. `_build_signal_message` reads `"engine_b"` first then `"naked_data"` for ENGINE B section (Engine A signals use `"engine_b"`, Engine B scan signals use `"naked_data"`)
22. Vision structured footer: preserve machine-readable lines — `RIGHT EDGE: CONFIRMS|REVIEW|POTENTIAL REVERSAL` (line immediately before `TF ALIGNMENT`) plus `TF ALIGNMENT` + 3× `RATING` + 3× `LEVELS` in single/dual/triple modes — required by `_extract_vision_structured()` parser; do not reword tokens
23. `confidenceDetail` and `factorDiagnostics` keys are camelCase on the signal dict — do not use snake_case when reading from signal
24. Lottery Lab — never bypass `_normalize_game()` before any DB or analytics call
25. Lottery Lab — `simulate_generator()` incremental counters must never revert to full-history rescan per draw
