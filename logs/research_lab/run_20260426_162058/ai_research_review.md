# Athena Research Lab — AI Review
**Run ID:** `run_20260426_162058`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-26 16:41 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Analyst:** Quantitative Research  
**Run ID:** run_20260426_162058  
**Date:** 2026-04-26  
**Key Caveat:** These are discovery backtests at lowered BT_MIN thresholds (worse than live). **Do NOT execute live, copy thresholds to live gates, or deploy without forward validation.** Many results untrustworthy due to tiny samples (<30 trades penalized), single-symbol focus, and frequent IS/OOS decay (penalized). No telemetry bugs noted, but mt5/binance_rest data sources lack full OOS consistency checks. Robustness scores ~0.48-0.52 average; prefer clusters over one-offs.

## 1. Strategy Family Performance
- **mean_reversion**: WEAK_CANDIDATE (7 valid configs, best avg net_return 0.125, high WR 65.7%; cluster on bollinger_touch crypto/index, but small samples 24-39 trades, OOS mixed).
- **breakout**: WEAK_CANDIDATE (18 valid, net 0.120; multi-symbol crypto/commodity H4, but frequent OOS negative e.g. BTC/USDT -0.123).
- **trend_momentum**: REJECT (4 valid net 0.115, but 488 rejects/363 tiny samples; ema_cross universally fails).
- **pullback**: REJECT (7 valid net 0.060, low WR 23%; OOS decay).

**Best family overall: mean_reversion (WEAK_CANDIDATE).** No STRONG_CANDIDATE families due to lack of multi-symbol/timeframe clusters.

## 2. Indicator Helped Most
**bollinger_touch (mean_reversion)**: WEAK_CANDIDATE (highest pass_rate 29%, avg WR 56%; top ETH/USDT H4 STRONG_CANDIDATE at 75% WR, but 24 trades and single-symbol penalization).

No strong helpers; all attribution "HURTS" or NEUTRAL.

## 3. Indicator Hurt Most
**ema_cross (trend_momentum)**: REJECT (0% pass_rate, 432 configs avg net -0.113, tiny samples everywhere e.g. EUR/USD 9-23 trades; gross/net same but unprofitable).

## 4. Asset Group Best
**crypto**: STRONG_CANDIDATE (net 0.174, 17 configs; BTC/ETH clusters H4/H1, highest PF 1.47).  
Commodity/forex/index: WEAK_CANDIDATE (lower net, forex near-zero).

## 5. Symbol Best
**BTC/USDT**: WEAK_CANDIDATE (net 0.214, 8 configs; breakout H4 cluster, but OOS -0.065 penalization).  
ETH/USDT close (net 0.138). Others single/one-off: REJECT/NEEDS_MORE_DATA.

## 6. Timeframe Best
**H4**: STRONG_CANDIDATE (net 0.139, 24 configs > H1's 0.049; most top ranks). H1: WEAK_CANDIDATE.

## 7. Session Best
**all**: NEEDS_MORE_DATA (only data; no session splits, untrustworthy without breakdown).

## 8. LONG vs SHORT
**both**: NEEDS_MORE_DATA (only direction tested; no long/short split, cannot compare).

## 9. Setups Collapsed After Fees
None. All valid configs gross-profitable imply net-positive (net_return provided, no gross>0/net<0 cases noted).

## 10. Setups with Too Little Sample (<30 trades penalized)
- **trend_momentum** (363 configs, e.g. ema_cross EUR/USD 9-23 trades): REJECT.
- **mean_reversion** (74 configs, e.g. bollinger_touch tops at 24-39): NEEDS_MORE_DATA.
- **breakout** (58 configs): NEEDS_MORE_DATA.
Full: 495/768 tiny.

## 11. Engine A (EMA/RSI+MACD/ADX) Recommendations
- **Keep**: MACD momentum (macd_direction adx_min=0 ETH/USDT H1 STRONG_CANDIDATE, 55 trades WR49% PF1.56 robustness 0.69; OOS+).
- **Remove/Demote**: EMA trend coherence (ema_cross REJECT everywhere, hurts confluence score).
- **Tune**: ADX gate lower (0 best vs 20/25; test RSI+MACD quality threshold up for momentum). No live threshold changes—validate OOS multi-symbol first.
Not trustworthy: forex tiny samples.

## 12. Engine B (Naked PA: BOS/CHoCH/FVG/OB/swing/location/trigger/RR) Recommendations
- **Keep**: None (no engine_b_proxy; breakout/pullback proxies WEAK_CANDIDATE but OOS fail).
- **Remove/Demote**: Session_opening_range/prev_day_hl (hurt attribution, OOS-).
- **Tune**: RR/room gates stricter (low WR); location (PDH/PDL) for H4 crypto only. NEEDS_MORE_DATA—no valid proxies.

## 13. Engine D (VP/OF: POC/VAH/VAL/Absorption/CVD/VWAP/AAA; Crypto) Recommendations
- **Keep/Remove/Tune**: None (no engine_d_proxy results; crypto H4 shows promise but no VP data). NEEDS_MORE_DATA—missing telemetry.

## 14. Next Smallest Useful Test
Expand top WEAK_CANDIDATE clusters: H4 mean_reversion bollinger_touch on 2-3 more crypto (e.g. SOL/USDT, BNB/USDT) + 1 forex (GBP/USD); add long/short split. Tiny mode, 4 symbols max.

## 15. What Should NOT Be Tested Further Right Now
- ema_cross (universal REJECT, hurts).
- H1 trend_momentum (OOS decay, low net).
- Single-symbol forex (EUR/USD tiny/unprofitable).
- london_breakout/ny_breakout (0% pass, hurts).

**Overall:** Weak discovery (4.7% valid, no robust clusters). Focus crypto H4 mean_reversion/breakout for validation. **No live actions.**

```json
{
  "overall_verdict": "WEAK_OVERALL: Crypto H4 mean_reversion/breakout shows promise but lacks multi-symbol OOS clusters; heavy penalization for tiny samples/single-symbols.",
  "top_candidates": [
    {"strategy": "bollinger_touch", "symbol": "ETH/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "macd_direction", "symbol": "ETH/USDT", "tf": "H1", "label": "STRONG_CANDIDATE"},
    {"strategy": "prev_day_hl", "symbol": "XAU/USD", "tf": "H4", "label": "STRONG_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "ema_cross", "reason": "0% pass_rate, tiny samples, net negative"},
    {"strategy": "london_breakout", "reason": "0% pass_rate, hurts attribution"},
    {"strategy": "pullback_ema", "reason": "Low WR, OOS decay"}
  ],
  "engine_a": {
    "keep": ["MACD momentum (adx_min=0)"],
    "remove_or_demote": ["EMA cross/trend coherence"],
    "tune": ["ADX gate lower (test 0-20); RSI+MACD quality thresholds"],
    "next_tests": ["Multi-symbol H4 macd_direction OOS validation"]
  },
  "engine_b": {
    "keep": [],
    "remove_or_demote": ["session_opening_range", "prev_day_hl"],
    "tune": ["RR/location gates stricter for H4 crypto"],
    "next_tests": ["Add engine_b_proxy to breakout/pullback"]
  },
  "engine_d": {
    "keep": [],
    "remove_or_demote": [],
    "tune": [],
    "next_tests": ["Crypto H4 VP/OF proxies (POC/VAH/CVD)"]
  },
  "data_quality_warnings": ["495/768 tiny samples <30 trades", "Frequent IS win / OOS loss"],
  "telemetry_warnings": ["No Engine B/D proxies; mt5/binance_rest OOS unverified"],
  "next_tiny_test": {
    "symbols": ["ETH/USDT", "BTC/USDT", "SOL/USDT", "GBP/USD"],
    "timeframes": ["H4"],
    "strategy_families": ["mean_reversion", "breakout"],
    "reason": "Expand top crypto H4 clusters with long/short split"
  },
  "do_not_do_next": [
    "ema_cross any variant",
    "H1 trend_momentum",
    "Single-symbol forex"
  ]
}
```