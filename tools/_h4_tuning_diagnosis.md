# H4 Indicator TF-Sensitivity Diagnosis

Generated: 2026-07-08 (research pass for Engine B H4 entry experiment)

## Summary

Current indicator periods in `config.yaml` are keyed by **score_group**, not by **entry TF**. When Phase 3 moved entry from H1 to H4, the same bar-count periods were applied on 4× wider candles. This document maps each parameter's effective **time window** on H1 vs H4 and whether it is config-gated.

**Related parity gap (not fixed in this pass):** `backtest_runner.py:5170,6454,6462,6463` call `calc_indicators_with_normalized(candles, asset_type)` without `score_group=`, so Engine B backtests use universal defaults (RSI 14, EMA 21/50/200) unless a research runner injects score_group. The H4 experiment runner patches this for research only.

---

## Config-gated Engine A period tables

### `ENGINE_A_EMA_PERIODS_BY_CLASS` — `config.yaml:793-820`

| Field | Example (forex_majors) | H1 time window | H4 time window | Scope |
|---|---:|---|---|---|
| trend | 26 | 26 hours | 104 hours (~4.3 days) | per score_group |
| momentum | 60 | 60 hours | 240 hours (10 days) | per score_group |
| long | 200 | 200 hours | 800 hours (~33 days) | universal anchor |

Resolver: `factor_scoring.py:250-265` → `engine_a_v3/profile.py:217-228`

### `ENGINE_A_RSI_PERIOD_BY_CLASS` — `config.yaml:822-849`

| Example | H1 window | H4 window | Scope |
|---|---:|---:|---|
| forex_majors = 18 | 18h | 72h | per score_group |
| crypto_btc = 12 | 12h | 48h | per score_group |
| bond_tlt = 21 | 21h | 84h | per score_group |

Resolver: `factor_scoring.py:268-279`

### `ENGINE_A_ATR_ADX_PERIODS_BY_CLASS` — `config.yaml:887+`

| Field | Value | H1 window | H4 window | Scope |
|---|---:|---:|---:|---|
| atr | 14 | 14h | 56h | per score_group (uniform 14) |
| adx | 14 | 14h | 56h | per score_group (uniform 14) |

Resolver: `factor_scoring.py:282-306`. Note: ATR absolute value scales with TF bar range; period 14 on H4 produces wider SL distances in `_full_atr` (`backtest_runner.py:5124-5131`).

### `ENGINE_A_MACD_PARAMS_BY_CLASS` — `config.yaml:851-878`

| Params | Bars | H1 window | H4 window | Scope |
|---|---:|---:|---:|---|
| 12/26/9 | 12 fast | 12h | 48h | deliberately universal (`config.yaml:787`) |

Resolver: `factor_scoring.py:309+`

---

## Engine B structure / trigger parameters

### `ENGINE_B_SWEEP_LOOKBACK_BARS` — `config.yaml:2159`

| Value | H1 window | H4 window | Scope |
|---:|---:|---:|---|
| 5 | 5 hours | 20 hours | global scalar |

Consumer: `market_structure.py:3231-3234` (`_detect_sweep`)

### `ENGINE_B_BOS_LOOKBACK_BARS` — default in code

| Default | H1 window | H4 window | Scope |
|---:|---:|---:|---|
| 5 | 5 hours | 20 hours | config key read at `market_structure.py:2846-2848` |

### `_price_action_trigger` last-3-candles — `market_structure.py:3493-3606`

| Window | H1 | H4 | Scope |
|---|---|---|---|
| 3 bars | 3 hours | 12 hours | hardcoded bar count |

Uses `engulfing_min_body_atr_mult` from `NAKED_ENGINE` (`config.yaml:3416`) — ATR-scaled, partially self-adjusting.

### BOS swing lookback (last 3 peaks/troughs) — `market_structure.py:2809-2810`

| Window | H1 | H4 | Scope |
|---|---|---|---|
| 3 swings | variable (~hours) | variable (~days) | hardcoded in BOS detector |

---

## Backtest loop parameters (partially TF-aware)

| Parameter | Location | H1 | H4 | TF-aware? |
|---|---|---:|---:|---|
| COOLDOWN bars | `backtest_runner.py:5174` | 8 bars = 8h | 2 bars = 8h | **Yes** |
| ATR period in `_full_atr` | `backtest_runner.py:5130` | 14 | 14 | No (hardcoded 14) |
| entry_raw selection | `backtest_runner.py:5119` | candles_h1 | candles_h4 | **Yes** (via style_profile) |

---

## H4 overlay candidate scaling (research-only)

File: `configs/proposed_h4_indicator_overlay.yaml`

| Parameter | Baseline scaling rule | Rationale |
|---|---|---|
| EMA trend | ×0.5 bars | ~same hours on H4 |
| EMA momentum | ×0.25 bars | ~same hours on H4 |
| EMA long | unchanged (200) | structural anchor |
| RSI | ×0.5 bars | ~same hours on H4 |
| ATR/ADX | unchanged (14/14) | Wilder standard; ATR magnitude self-scales |
| MACD | unchanged (12/26/9) | deliberate universal hold |
| Sweep lookback | 5 → 2 bars | ~same hours on H4 (~8h) |

---

## Implication for Phase 3 H4 results

Moving entry TF to H4 without retuning periods:
- Widens effective momentum/trend lookbacks (more lag, fewer triggers)
- Widens sweep/BOS lookback in clock time
- Keeps COOLDOWN at ~8 hours (already TF-normalized)

The H4 experiment tests whether overlay-scaled periods recover signal quality on H4 entry TF beyond the raw TF change alone.
