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
- **Engine B live + backtest:** `NAKED_ENGINE.style_profiles` (`min_score`, `min_rr`), `ENGINE_B_REGIME_MULTIPLIERS` (score scaling — currently neutralized to 1.0), `zone_multipliers` (structural width), naked checklist gates in `market_structure.py`.
- **Engine D (Scalp Lab):** `SCALP_ENGINE` in `config.yaml` (`MIN_RR`, `MAX_SPREAD_PIPS`, `ZONE_MIN_CONDITIONS`, `WITH_TREND_ONLY`, `BIAS_TIMEFRAME`, candle limits, `AI_GRADING` / `MIN_GRADE_AUTO_EXECUTE`, optional `SCALP_PAIRS`) and core pass/fail logic in `scalp_engine.py` (session filter, zone detection, M5 trigger, momentum, level math).

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

**Four trading engines:**
- **Engine A** — Multi-Factor Quantitative Scoring (MFQS): z-score factor engine + forex rule-based scorer
- **Engine B** — Naked price-action (market structure, zones, BOS/CHoCH, FVG, swing sequence)
- **Engine C** — Consensus layer: blends A + B + AI Vision confirmation into a conviction-tiered signal
- **Engine D (Scalp Lab)** — Standalone M15 structure + M5 tactical scalping (`scalp_engine.py`); not part of A/B/C consensus. Dashboard **Scalp Lab** panel (`static/index.html`): `POST /api/scalp-scan`, manual `POST /api/scalp-execute`.

**Execution:** MT5 for forex/stocks/commodities; Bybit Linear Futures for crypto.

**AI Review:**
- **Marcus Reid (Grok/xAI)** — AI analysis of Engine A + B signals (`EXPERT_PROMPT`), JSON output with grade/verdict/edgeProbability/style_ratings
- **Chart Vision (xAI multimodal)** — chart screenshot analysis (`/api/chart-analysis`) via OpenAI-compatible xAI client (`base_url=https://api.x.ai/v1`) and `VISION_MODEL`. Single/dual/triple TF; structured footer parsed by `_extract_vision_structured()`.
- **Lottery AI** — `POST /api/lottery/ai-analysis`: Grok (xAI) via OpenAI-compatible client; see **Lottery Lab** below.

**API Keys (secrets via env):** `EODHD_KEY`, `XAI_API_KEY` (Marcus Reid, chart vision, signal debate, Engine B AI, Lottery AI), `ANTHROPIC_API_KEY` (news sentiment helpers where configured). xAI-backed routes require the **`openai`** PyPI package (`base_url=https://api.x.ai/v1`).

---

## File Map

| File | Purpose |
|------|---------|
| `athena.py` | Flask app, all API routes, `analyze_pair`, pair lists, `EXPERT_PROMPT`, `_build_signal_message`, `_build_event_risk`, vision prompts | ~8500 lines |
| `vision_prompts.py` | Modular chart-vision prompt builders (`build_system_prompt`, `build_single_prompt`, `build_dual_prompt`, `build_triple_prompt`) with A-E right-edge candle framework |
| `vision_data.py` | Vision dataset schema/helpers (`vision_samples`, `vision_labels`, `vision_predictions`), artifact persistence, metrics aggregation |
| `vision_hybrid.py` | Advisory hybrid chart-vision v2 training/inference (fusion of image + structured features) |
| `chart_renderer.py` | Server-side chart renderer with Engine B overlays (FVG/OB/BOS/CHoCH/SR), pattern labels, configurable `bars_window` + `dpi` |
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
| `forex_scoring.py` | Dedicated forex scorer (rules-based, 0–2.0 scale): trend gate + session + RSI pullback + COT boost |
| `market_structure.py` | Engine B: `NakedEngine`, swing analysis, BOS/CHoCH/FVG/order blocks, shared checklist pass/fail |
| `engine_b_ai.py` | Engine B advisory AI verdict (review only — not a pass/fail gate) |
| `engine_c.py` | Engine C consensus: `ENGINE_C_AB_WEIGHTS` blend, conviction tiers, SL/TP resolution, Vision modifier |
| `confidence_engine.py` | 4-component confidence scoring (indicator agreement, TF alignment, regime fit, liquidity) |
| `indicators.py` | Pure indicator functions (EMA, RSI, MACD, ATR, ADX, BB, Stochastic, Fib, OBV, Squeeze) |
| `regime.py` | Market regime detection: TRENDING / DEVELOPING / RANGING / DEAD_RANGING |
| `risk_engine.py` | Kill switch, drawdown, position sizing, portfolio heat |
| `config.py` | Hardcoded defaults + YAML loader + `_json_safe()` |
| `config.yaml` | All tunable thresholds — edit here, not `config.py` |
| `scalp_engine.py` | Engine D: M15/M5 structural scalping — MT5 data for non-crypto, `fetch_candles`/Binance path for crypto; signals set `mt5_symbol=None` on crypto |
| `ai_schemas.py` | Pydantic schemas for AI JSON output (`EngineAResponse`, `EngineBResponse`, `StyleRating`) |
| `mt5_executor.py` | MetaTrader 5 execution |
| `bybit_executor.py` | Bybit Linear (USDT-M) Futures execution |
| `auto_trader.py` | Autonomous scheduler: scan every 30 min, auto-execute per conviction |
| `ai_learning.py` | Outcome extraction → `learning_log`; factor-level analysis for AI calibration |
| `lottery_engine.py` | Lottery analytics + ticket generation (7 modes) + simulation |
| `lottery_service.py` | DB schema, CSV import, draw history |
| `static/index.html` | Dashboard UI: signals, Engine C tab, backtest, ACM charts | ~2550 lines |

---

## Venues (canonical — execution vs data)

- **Crypto — execution:** **Bybit** USDT-M linear futures only (`bybit_executor.py`). All live crypto **orders** go here (including Engine D `/api/scalp-execute` when `signal.type == "crypto"`). **Binance is not a crypto execution broker in this codebase** — it is market data only.
- **Crypto — market data:** **Binance Futures** WebSocket klines (`candle_feeds.BinanceCandleWS`) → `CandleBuilder.on_kline()` for **M5, M15, H1, H4, D1**, plus REST fallback / merge via `fetch_candles()` when `pair["source"] == "binance"`. Live crypto quote stream: `BinanceLivePriceWS` `!ticker@arr`.
- **Non-crypto — execution and OHLC:** **MT5** only (`mt5_executor.py`, `fetch_mt5()` for candles). Forex, commodities, indices, stocks as configured with `source: mt5`.
- **EODHD — volume overlay (not primary price):** For **stocks, commodities, indices** only, EODHD can **overlay volume** onto existing candles without changing OHLC (`eodhd_volume_overlay.py`, whitelist per symbol/TF). This is separate from MT5 price feeds and separate from crypto (crypto volume comes from Binance bars).

---

## Live Data Feed Routing (LOCKED — do not change without explicit approval)

| Asset class | Candles | Live price |
|---|---|---|
| Forex / Stocks / Commodities / Indices | `fetch_mt5()` only — H1/H4/D1 | `symbol_info_tick()` bid/ask mid |
| Crypto M5/M15/H1/H4/D1 | `BinanceCandleWS` futures `@kline_*` → `CandleBuilder.on_kline()`; REST merge/fallback `binance_futures` | `BinanceLivePriceWS` `!ticker@arr` |
| Crypto (other TFs) | Binance REST via `fetch_binance` / `tf_b` map when `source == binance` | — |
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

### Chart Vision — `/api/chart-analysis` in `athena.py`
- Transport: OpenAI-compatible xAI chat completions client (`https://api.x.ai/v1`).
- Model id: **`VISION_MODEL`** in `config.yaml` (current runtime typically Grok reasoning multimodal model id).
- Temperature: **`AI_VISION_TEMPERATURE`** — default **`0.2`** in `config.py` / yaml (factual observation mode).
- Three modes: single-TF (H4), dual-TF (D1+H4), triple-TF (D1+H4+H1)
- Prompt architecture is modularized in `vision_prompts.py` and used directly by `/api/chart-analysis` (single active assignment per system/single/dual/triple prompt).
- RIGHT EDGE instructions enforce A-E candle reading:
  - A Pattern ID + structure location
  - B Wick analysis (direction/ratio/meaning)
  - C Body conviction
  - D Sequence narrative as first RIGHT EDGE line
  - E Candle rules (engulfing requires volume confirmation; counter-trend + rising volume => `POTENTIAL REVERSAL`)
- **Read order (2026-03-31):** Prompts enforce **image-first** for structure and prices; **right edge** = last 5 candles on authoritative TF (single/H4, dual/H4, triple/H1); **then** algorithmic context for cross-check. If chart and context conflict on **structure/direction**, **the image wins**.
- **TRADE SNAPSHOT instrument/TF:** Prefer any **visible chart UI** (watermark, symbol strip, title bar — not only top-left). If the label is still illegible, state exactly *chart label not legible — from request* and use **only** the chart-analysis request metadata for identity: `symbol`, `tf` (single-TF), or the request symbol plus each image’s stated TF role (D1/H4 or D1/H4/H1). Do **not** substitute “algo context” wording for identity, guess alternatives, list candidate pairs, or hedge with likely / maybe / appears / possibly / or. Do not infer instrument from ENGINE A/B text. Fallback is **metadata only**; it does not override visible price action.
- **RIGHT EDGE (2026-03-31):** Model must lead with **interpretation** (momentum, control of last closes, continuation vs pullback vs reversal risk, confirm vs threaten algorithmic LONG/SHORT). Avoid long candle-by-colour play-by-play without meaning; optional one compact oldest→newest fact sentence as evidence.
- **Entry quality rules (2026-04-01):** System prompt rules 9–12 enforce entry positioning assessment (tactical vs chasing into congestion), volatility-regime interaction (low-vol breakdowns flagged), move maturity (late/exhausted entries), and independent RR verification. (Rule 8 is instrument/timeframe metadata-only when chart labels are unreadable.) All three user prompts (single/dual/triple) include an **ENTRY QUALITY** body section between **KEY RISKS** and **FINAL VERDICT**.
- **Body structure:** Concise sections (e.g. TRADE SNAPSHOT, MARKET STRUCTURE, RIGHT EDGE, factors, verdict) plus required machine footer below.
- **STRUCTURED FOOTER (required — do not remove):** Line `RIGHT EDGE: CONFIRMS | REVIEW | POTENTIAL REVERSAL` immediately before `TF ALIGNMENT` + three `*_RATING` + three `*_LEVELS` — parsed by `_extract_vision_structured()` for Engine C conviction modification.
- **Anti-hallucination rules:** ONLY describe what you can ACTUALLY SEE; never invent patterns; cross-reference algo context **after** the visual read; full annotation legend for Engine B elements.
- **Footer parsing:** Parsed by `_extract_vision_structured()` and used by `apply_vision()` to modify Engine C conviction. Removing or rewording parser tokens breaks Engine C Vision integration and UI level extraction.
- **EODHD news (same file, `fetch_news_context`):** Per-pair `/api/news` and `/api/news-word-weights` use `timeout=15` (was 8s) to reduce read timeouts on slow EODHD responses; failures stay non-fatal.
- Advisory hybrid v2 sidecar is available via:
  - `POST /api/vision-sample`
  - `POST /api/vision-label`
  - `POST /api/vision-infer-v2`
  - `GET /api/vision-metrics`
  - `POST /api/vision-train-v2`

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
POST /api/engine-c-scan {assetClass, style}
  └─ Fetches candles ONCE per pair, shares between Engine A (full) and Engine B (last bar dropped)
  └─ analyze_pair(preloaded_candles={D1,H4,H1})   ← Engine A (uses same data as Engine B)
  └─ NakedEngine.analyze_structure() + calculate_confidence()   ← Engine B
  └─ compute_consensus() → ALIGNED / A_ONLY / B_ONLY / CONFLICT / SKIPPED
  └─ ALIGNED results insert into shadow ledger (SHADOW_LEDGER_ENABLED must be true)

Other Engine C paths:
  - /api/compare-engines — manual per-pair A+B+C comparison on demand
  - /api/backtest-consensus — Engine C backtest for a specific pair

In each path the consensus logic is:
  └─ compute_consensus(signal_a, signal_b, confidence_b, regime, entry, atr)
       ├─ normalise_engine_a() → 0–1  (max_score 2.0 for forex, 3.0 for non-forex)
       ├─ normalise_engine_b() → 0–1  (max_possible from calculate_confidence, default 5.0)
       ├─ ENGINE_C_AB_WEIGHTS regime blend: TRENDING={A:0.65,B:0.35}, RANGING={A:0.35,B:0.65}
       ├─ resolve_sl() — structural → ATR-clamped → tighter
       ├─ resolve_tp() — structural if RR≥1.5, else ATR
       └─ returns {conviction, tier, sizing_override, sl, tp, rr, ...}

Note: analyze_pair() logs [ANALYZE] warnings when returning None due to empty/insufficient candles
```

/api/engine-c-confirm (Vision overlay):
  └─ apply_vision(consensus, vision_result)
       ├─ CONFIRM + conviction≥0.35 → trade=True
       └─ AVOID/CONTRADICT → trade=False, tier=SKIP
```

**Scalp Lab / Engine D flow:**
```
Dashboard Scalp Lab (panel-scalp) → runScalpScan()
  POST /api/scalp-scan
    └─ get_scalp_pairs()  ← CONFIG SCALP_ENGINE.SCALP_PAIRS or default universe (MT5 lists + crypto USDT perps)
    └─ scalp_engine.run_scalp_scan(pairs)
         ├─ Session filter (London/NY for MT5; Asia/London/NY windows for crypto)
         ├─ Spread filter (per MAX_SPREAD_PIPS; skipped for crypto)
         ├─ M15 zone (≥ ZONE_MIN_CONDITIONS among S/R, sweep, EMA21, etc.)
         ├─ M5 entry trigger (rejection/engulfing + direction + inside zone)
         ├─ Momentum confirmation (M5 structure break or strong body)
         ├─ H1 bias gate when WITH_TREND_ONLY + BIAS_TIMEFRAME
         ├─ calculate_scalp_levels → SL, TP1 (~MIN_RR), optional TP2 (M15 swing)
         └─ ai_quality_grade — rule-based 0–100 score, A/B/C/D (no external API)
    └─ _scalp_ui_signal() per row → JSON for UI

POST /api/scalp-execute  (symbol + optional client signal for direction match)
  └─ run_scalp_scan([symbol]) — fresh setup required
  └─ risk_check() → mt5_execute() OR bybit_execute() (crypto)
```

Signals use `engine: "SCALP"`; `confluenceScore` is derived from the rule-based `ai_score` for risk sizing compatibility.

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

**Risk engine scope:** Peak equity, drawdown, and daily realized P&L are tracked **per** `signal["type"]` (asset class: `crypto`, `forex`, `stock`, etc.) so Bybit vs MT5 equity is not mixed. `risk_check` enforces `MAX_SL_PCT` per class from the config dict (`config.yaml` / `config.py`).

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
Shared Engine B checklist for live scan, analysis, compare, backtest. Score = checklist count / UI measure. `passed` = naked price-action rules only — not AI-driven. **Note:** `ENGINE_B_REGIME_MULTIPLIERS` (set to 1.0) ensures the `min_score` from UI or `score_group_overrides` is used directly without hidden regime inflation.

### `_naked_scan_style_profile(style, score_group)` — `athena.py`
Resolves effective Engine B thresholds. Logic: Hardcoded defaults → `style_profiles` (global UI) → `score_group_overrides` (per-pair BT MIN). Per-group overrides take priority and are displayed in the **"Per-Group BT MIN Overrides"** table in the dashboard's Engine B panel.

### `_engine_b_regime_label(h4_candles, pair_type, regime_hint)` — `athena.py`
Shared regime resolver for all Engine B paths. Returns: `TRENDING`, `RANGING`, `HIGH_VOLATILITY`, `LOW_VOLATILITY`.

### `_json_safe(value)` — `config.py`
Recursively replaces `NaN`/`Inf` with `None`, normalizes numpy types. Applied before every `jsonify()`.

### `_can_execute(signal, cfg)` — `auto_trader.py`
Live execute gate is **`combinedConviction`** vs `AUTO_TRADE_MIN_CONVICTION` (with alignment discount and meta delta). `AUTO_TRADE_MIN_SCORE` is **informational** in `get_status()` only; keep forex on the **0–2.0** scale alongside `MIN_CONFLUENCE_CLASS.forex` / `MIN_FOREX_CONFLUENCE`.

### `/api/chart-analysis` — `athena.py`
Requires `XAI_API_KEY` env var and uses OpenAI-compatible xAI client (`base_url=https://api.x.ai/v1`). `regime` in signal can be dict `{"label":"TRENDING"}` (Engine A) or string `"TRENDING"` (Engine C) — both handled. Context builder is **outside** try/except — keep it simple.

### `/api/scalp-scan` / `/api/scalp-execute` — `athena.py`
See **Scalp Lab / Engine D flow** above. Execute path: **`signal.type == "crypto"` → Bybit**; **else → MT5**. Crypto candles for Engine D come from Binance futures (`fetch_candles` / `CandleBuilder`); orders are **never** sent to Binance.

---

## Scoring Architecture Summary

| Engine | Scorer | Scale | Gate key |
|--------|--------|-------|----------|
| Engine A — non-forex | `factor_scoring.py` z-score factor engine | 0–3.0 | `MIN_CONFLUENCE_CLASS[type]` |
| Engine A — forex | `forex_scoring.py` rules-based | 0–2.0 | `MIN_FOREX_CONFLUENCE` (1.0) + `MIN_CONFLUENCE_CLASS.forex` (1.0) |
| Engine B | `market_structure.py` naked checklist | 0–100 pct | `NAKED_ENGINE.style_profiles.min_score` |
| Engine C | `engine_c.py` A+B blend | 0–1 conviction | `ENGINE_C_AB_WEIGHTS` |
| Engine D (Scalp Lab) | `scalp_engine.py` zones + triggers + rule-based `ai_quality_grade` | 0–100 (`ai_score`), letter `ai_grade` | `SCALP_ENGINE` (`MIN_RR`, spread/session/zone gates, `MIN_GRADE_AUTO_EXECUTE`, etc.) |

**Two different REGIME_WEIGHTS exist — do not confuse:**
- `CONFIG["REGIME_WEIGHTS"]` — adjusts **factor group weights** inside Engine A
- `ENGINE_C_AB_WEIGHTS` in `engine_c.py` — controls **A vs B blend ratio** in Engine C

**`confluencePct` display scaling:** anchored to `get_min_confluence_threshold(pair)` so ~67% = "passing" intent. Engine C uses raw `confluenceScore / maxScore` (not `confluencePct`) for normalization.

---

## 2026-04-08: Forex London breakout / news / Engine B reality (current active guidance)

### Code-verified London breakout behavior

- The forex engine is London-breakout capable, but it is **not** a London-breakout-only bot.
- `forex_scoring.py` contains a dedicated `_london_breakout_score()` path.
- The breakout path measures the Asian range from **00:00-07:00 UTC** using H1 candles.
- Breakout scoring is only active during the first 3 London hours: **07:00-09:00 UTC**.
- The Hurst/trend gate can veto the trend-following path while the London breakout path still runs.
- The forex engine remains a hybrid:
  - trend pullback path
  - London breakout path
  - final score uses the stronger valid path

Use this mental model when reasoning about forex:
- **Engine A = breakout / timing detector**
- **Engine B = structural validator**
- **Engine C = alignment / consensus layer**
- **news / event risk = final safety layer**

Do not describe the current forex engine as a dedicated London-breakout-only system.

### London breakout operator guidance (not a single code-enforced switch)

Treat this as the intended review / execution flow for breakout trades:
1. Build the Asian range (**00:00-07:00 UTC**).
2. Let **Engine A** detect breakout quality around London open.
3. Use **Engine B** to validate whether the breakout is structurally tradable.
4. Let **Engine C** confirm A/B alignment.
5. Apply event-risk / sentiment safety filters last.

Interpret timing like this:
- too early = first wick poke through the range
- best = first confirmed H1 close outside the Asian range, ideally during **07:00-09:00 UTC**
- too late = extended chase after multiple London impulse candles

If the move is already stretched, treat it as continuation / pullback logic, not a fresh London breakout.

### Engine B role in London breakout

Engine B is **not** the London-session clock and should not be documented as the primary breakout detector.

Use Engine B as a structure / execution-quality filter:
- did price break structure or only wick the range?
- is the breakout or continuation candle strong enough?
- is there room to target?
- is RR still valid after SL placement?
- is price already too extended from the break area?

Correct role split:
- **Engine A finds the breakout event**
- **Engine B checks whether the breakout is structurally tradable**
- **Engine C checks whether A and B align**

### News toggle and news architecture

The UI news toggle only controls `NEWS_SENTIMENT_CONFLUENCE_ENABLED`.

It does **not** automatically disable:
- `SENTIMENT_GATE_ENABLED`
- `EVENT_RISK_ENABLED`

So current behavior is:
- news toggle ON = news sentiment can nudge scan / confluence score
- news toggle OFF = no news score blend
- sentiment / event safety blockers can still remain active for auto-trading

Current repo reality has **3 separate news / event layers**:
1. background news context for UI / AI / narrative use
2. execution safety gates (`SENTIMENT_GATE_ENABLED`, `EVENT_RISK_ENABLED`)
3. optional scan / confluence blend via `NEWS_SENTIMENT_CONFLUENCE_ENABLED`

Do not describe the current repo as having one unified news engine.

### News blend scoring contract

When news sentiment confluence is enabled, score blending must respect the Engine A score contract for the current path:
- forex Engine A = **0-2.0**
- non-forex Engine A = **0-3.0**

Any news delta must scale from the correct `maxScoreOverride`. Do not assume all Engine A paths share one raw max.

### Current active forex thresholds

- Engine A forex scale = **0-2.0**
- `MIN_FOREX_CONFLUENCE` = **1.0**
- `MIN_CONFLUENCE_CLASS.forex` = **1.0**
- `AUTO_TRADE_MIN_SCORE.forex` remains informational / status-only and should stay aligned to the active forex class floor

Historical threshold notes may remain below, but they must stay clearly labeled historical / superseded.

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
- Execute gate: **`combinedConviction`** ≥ `AUTO_TRADE_MIN_CONVICTION` (adjusted for alignment / meta). `AUTO_TRADE_MIN_SCORE` is status-only; align per-class scales (forex **0–2.0**).
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
- `[NAKED-DBG]` logs in `athena.py` show specific checklist failures (e.g., `fails=[struct,loc,trigger,rr=0.8]`) for immediate diagnostic visibility.
- `[ANALYZE]` warnings in `athena.py` log when `analyze_pair()` returns None due to empty/insufficient candles — check console for D1/H4/H1 bar counts.

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

## Advisory thresholds (backtest + live suggestions)

- **Module:** [advisory_thresholds.py](advisory_thresholds.py) — builds suggestions from latest `backtest_results` per (pair, engine) and closed `audit_log` trades.
- **No auto-apply:** `BT_MIN`, `MIN_CONFLUENCE_CLASS`, and `NAKED_ENGINE.style_profiles.min_score` change **only** after **POST** `/api/advisory-thresholds/<rec_id>/approve` (human action). GET `/api/advisory-thresholds` is read-only for gates.
- **Audit:** Approvals/rejections stored in `advisory_threshold_actions` in `audit.db`.

## Lottery Lab

**Games:** `lotto` (6/58+bonus), `powerball` (5/50+bonus), `daily_lotto` (5/36)
**Generator modes:** `pure_random · hot_bias · cold_bias · overdue_bias · balanced_mix · pair_bias · anti_crowd`

**Lottery AI:** `POST /api/lottery/ai-analysis` uses **Grok (xAI)** only — not Anthropic. Set **`XAI_API_KEY`** in env; **`openai`** package with **`base_url=https://api.x.ai/v1`**; model from **`LOTTERY_AI_MODEL`** (optional) or **`XAI_MODEL`** in `config.yaml`. Response JSON includes **`model`** echo.

**Critical invariants:**
- `simulate_generator()` uses incremental counters — never revert to full-history rescan per draw (quadratic regression)
- `generate_tickets()` requires `rules`, `universe`, `main_numbers` initialised before `if draw_context is not None`
- Per-ticket retry cap: `max(25, min(400, len(main_numbers) * 4))` — never remove
- New games must be added to both `LOTTERY_GAME_RULES` (`lottery_engine.py`) AND `LOTTERY_GAME_SPECS` (`lottery_service.py`)
- `LOTTERY_GAME_RULES["lotto"]` uses `main_max=58, bonus_max=58` (SA Lotto 1–58 pool since 21 Sep 2025) — do not revert to 52 or 55

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

## DECISIONS

## 2026-04-05: Engine A threshold recalibration

**Root cause:** `MIN_CONFLUENCE_CLASS` gates for stocks (1.55), ETFs (1.45), commodities (1.40), and indices (1.35) were above the scoring formula ceiling.

The multiplicative formula with binary trend factor produces:

- **Maximum achievable score:** 1.42 (all factors at theoretical peak)
- **Typical good signal:** 0.79

Previous gates made it structurally impossible for stocks/ETFs to pass, and commodities/indices could only pass under rare perfect conditions.

**New gates (`config.yaml`):**

- stock: **1.10** (was 1.55)
- commodity: **1.10** (was 1.40)
- index: **1.05** (was 1.35)
- crypto: **1.20** (was 1.40)
- forex: see historical **2026-04-07** decision below (superseded by the current 1.0 live class floor)

These are calibrated at ~75–85% of maximum achievable to allow strong signals through while maintaining selectivity. Backtest **30+ trades per pair** before further adjustment.

---

## 2026-04-05: Engine A score scaling fix

**Problem:** The multiplicative formula `dir_score × quality_mult × dir_conf` produced a maximum `final_score` of ~1.42 instead of the documented 0–3.0 range. Root cause: the trend factor is binary (max ±1.0) and has the highest weight (2.0), so the weighted average of directional factors is structurally capped at ~1.4 regardless of how strong momentum/derivatives/carry are.

This made `MIN_CONFLUENCE_CLASS` gates unreachable:

- stock (1.55): impossible — max was 1.42
- commodity (1.40): only at theoretical perfect conditions
- index (1.35): only at theoretical perfect conditions

**Fix:** Added `_SCORE_SCALE = 2.11` in `factor_scoring.py` line ~1169 to stretch output to the documented 0–3.0 range. Now:

- A score of 1.5 = 50% of theoretical maximum
- A score of 2.0 = 67% of theoretical maximum
- Gates at 1.35–1.55 are achievable with strong (but not perfect) signals

**Impact:** All Engine A backtests before this fix used the unscaled 0–1.42 range. Re-run backtests after applying to get valid results. No threshold changes needed.

---

## 2026-04-07: Forex scoring scale fix (0–2.0)

**Problem:** Forex scoring had two `min(1.0, …)` caps compressing the score range. True max raw was ~1.97, but any score above 1.0 displayed as **1.0**. An intermediate fix used `_FOREX_SCORE_SCALE = 0.507` into 0–1.0 and lowered gates to **0.50** — that collapsed trade counts (e.g. USD/CHF) because the threshold drop did not fully compensate (needed raw ≥ ~0.99 vs old ≥ ~0.80).

**Final fix:**

1. Removed caps on `trend_score` and on the multiplicative `final_score` (no 0.507 scaling).
2. **0–2.0** display scale: `result.final_score` capped at **2.0** (matches achievable max ~1.97).
3. Historical thresholds at that point: **`MIN_CONFLUENCE_CLASS.forex`** and **`MIN_FOREX_CONFLUENCE`** ? **1.60** (~80% of 2.0, similar selectivity to old **0.80** on 0?1.0). **`BT_MIN.forex`** ? **1.50**; **`BT_MIN_GROUP`** forex ? **1.50 / 1.55 / 1.65**; **`MIN_CONFLUENCE_GROUP`** forex aligned to the same ladder.
4. **`advisory_thresholds.py`:** `_BT_LIMITS.forex` → **(0.80, 1.90)**; `_LIVE_LIMITS.forex` → **(1.00, 2.00)**.
5. **`maxScoreOverride` / Engine C forex path:** **2.0** in `athena.py` and `backtest_runner.py`; **`normalize_engine_a`** treats **`max_score ≤ 2.01`** like forex for the A-side floor. Engine C **conviction** calibration / `record_signal_event` stay **`max_score=1.0`** (0–1 normalized).
6. Historical fallback sync at that point: **`config.py` fallbacks** and **`AUTO_TRADE_MIN_SCORE.forex`** ? **1.60** (informational; matched the then-current class gate on 0?2.0).

**Score mapping (illustrative):**

| Old (capped 0–1) | New (0–2.0) | Meaning |
|-------------------|-------------|---------|
| 0.80 gate | 1.60 gate | ~same selectivity |
| 1.00 (cap) | 1.97 | True max visible |

**Expected at that time:** Trade counts closer to pre?0.507 fix; elite scores **1.8?2.0** separable from good **1.4?1.6**. Re-run forex backtests.

---

## 2026-04-07: Forex threshold correction (too high) ? historical / superseded again later

**Problem:** After the 0–2.0 scale fix, forex thresholds remained at **80% of max** (1.60), which was unreachable without rare SMC bonuses (FVG overlap + liquidity sweep). Typical good forex signals scored 0.8–1.1, so no signals passed.

**Fix:** Lowered forex thresholds to match other asset classes (~47-52% of max):

| Threshold | Old | New | % of max (2.0) |
|---|---|---|---|
| `MIN_CONFLUENCE_CLASS.forex` | 1.60 | **0.95** | 48% |
| `forex_majors` | 1.50 | **0.85** | 43% |
| `forex_crosses` | 1.55 | **0.95** | 48% |
| `forex_exotics` | 1.65 | **1.05** | 53% |
| `BT_MIN.forex` | 1.50 | **0.85** | 43% |
| `BT_MIN_GROUP` forex | 1.50-1.65 | **0.75-0.95** | 38-48% |

**Impact at that time:** Forex signals now appear in main scan. BT_MIN thresholds scaled proportionally to maintain BT/live ratio.

Current active contract:
- Engine A forex scale is **0?2.0**
- `MIN_CONFLUENCE_CLASS.forex` is **1.0**
- `MIN_FOREX_CONFLUENCE` is **1.0**
- `AUTO_TRADE_MIN_SCORE.forex` is **1.0** and remains informational/status-only

---

## 2026-04-07: H1 preload regression fix

**Problem:** The Engine C double-fetch fix added `if h1 is not None:` in `analyze_pair()`. Empty list `[]` from `fetch_candles` satisfied this, bypassing `candle_manager` which has WebSocket data. Result: forex pairs got empty H1 → `analyze_pair()` returned None → no signals.

**Fix:** Changed to truthiness check `if h1:` so empty/missing H1 falls through to `candle_manager` as before.

---

## 2026-04-07: Engine C double-fetch elimination

**Problem:** `api_engine_c_scan()` fetched candles for Engine B, then `analyze_pair()` fetched them again internally. This caused:
- 6 API calls per pair instead of 3 (EODHD rate limits)
- Different H1 data between Engine A and B (different Hurst exponent → different forex scores)
- Higher latency and cost

**Fix:** Restructured per-pair loop:
1. Fetch candles ONCE per TF (`_tf_map_full` stores full bars, `_tf_map` stores last-bar-dropped)
2. Pass `{D1, H4, H1}` from `_tf_map_full` to `analyze_pair(preloaded_candles=...)`
3. Use `_tf_map` for Engine B (last bar dropped as expected)

**Result:** ~40% reduction in EODHD API calls per pair, identical candle data between engines.

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
26. **Chart Vision vs Lottery AI:** both are xAI-backed via OpenAI-compatible SDK (`api.x.ai`) but use different prompts/routes. `/api/chart-analysis` uses `VISION_MODEL`; `/api/lottery/ai-analysis` uses `LOTTERY_AI_MODEL` or `XAI_MODEL`. Do not mix prompts, parser contracts, or route payload schemas between them.
27. **Scalp Lab (Engine D)** is a separate pipeline (`scalp_engine.py`, `/api/scalp-scan`, `/api/scalp-execute`) — not produced by `analyze_pair()` and not blended in Engine C unless you explicitly add that integration.
28. **Vision ENTRY QUALITY section:** Must appear between **KEY RISKS** and **FINAL VERDICT** in all three prompt modes (single/dual/triple). System prompt rules **9–12** (entry positioning, volatility-regime interaction, move maturity, RR reality check) are mandatory — do not remove or weaken.
