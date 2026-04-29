# Athena Research Lab — AI Review
**Run ID:** `run_20260427_185231_ab6e47`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-27 18:53 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260427_185231_ab6e47 | **Analyst:** Quantitative Research | **Date:** 2026-04-27  
**Key Caveat:** These are discovery results at lowered BT_MIN thresholds (worse than live). Do NOT execute live, copy thresholds to live gates, or treat as production signals. All findings penalized for small samples (<30 trades common), single-symbol focus (no clusters), negative OOS returns (IS works, OOS fails), and gross/net both marginal/negative post-OOS.

## 1. Strategy Family Performance
- **trend_momentum**: WEAK_CANDIDATE (6/540 valid configs, avg net_return +0.0070, avg PF 1.10, avg robustness 0.41; works IS but OOS negative across symbols/TFs; tiny samples on AUD/USD H1).
- **pullback**: REJECT (0 valid, all 48 configs rejected; avg net_return -0.055, low WR 0.14, hurts edge).

## 2. Indicator Helped Most
No indicators show strong help (all pass_rates <10%, avg net_returns negative OOS). **ema_cross**: WEAK_CANDIDATE (4 weak configs, highest PF 1.12 on AUD/USD H1, but <30 trades, single symbol, OOS -0.0123).

## 3. Indicator Hurt Most
**pullback_ema**: REJECT (0/48 pass, avg net_return -0.055, avg SQN -3.35, avg WR 0.14; consistently net-negative even gross).

## 4. Asset Group Performance
**forex**: WEAK_CANDIDATE (only group tested, avg net_return +0.0070, but OOS -0.0128, low robustness 0.41; no multi-asset clusters).

## 5. Symbol Performance
**EUR/USD**: WEAK_CANDIDATE (2 configs, highest net_return +0.0082, WR 0.41, 69 trades; H4 only, OOS -0.0139).  
**AUD/USD**: WEAK_CANDIDATE (4 configs, net_return +0.0063, but tiny samples <30).  
Penalized: Single symbols only, no cross-symbol robustness.

## 6. Timeframe Performance
**H4**: WEAK_CANDIDATE (2 configs on EUR/USD macd_direction, net_return +0.0082, 69 trades).  
**H1**: WEAK_CANDIDATE (4 configs on AUD/USD ema_cross, net_return +0.0063, but <30 trades).  
Penalized: No multi-TF clusters.

## 7. Session Performance
**all**: WEAK_CANDIDATE (only session tested, no granularity; avg net_return +0.0070).

## 8. LONG vs SHORT Performance
**both**: WEAK_CANDIDATE (only direction tested; no long/short split data; avg net_return +0.0070, but OOS fails).

## 9. Setups Collapsed After Fees
No setups gross-profitable but net-negative (all top have gross/net both positive but marginal; penalized for OOS failure instead).  
- **trend_momentum** (ema_cross/macd_direction): Gross +0.006-0.008 vs net similar, but OOS turns negative → REJECT for fee fragility.

## 10. Setups with Too Little Sample Size
Penalized all <30 trades (330/540 configs).  
- **trend_momentum**: NEEDS_MORE_DATA (329 configs <30 trades, e.g., ema_cross slow_period=100/200 variants 10-20 trades).  
- **pullback**: NEEDS_MORE_DATA (1 config <30).

## 11. Engine A Recommendations (EMA/RSI/MACD/ADX Scoring)
Keep core 3-factor (EMA trend, RSI+MACD momentum, ADX gate) but **do not change live forex floor (2.1)**.  
- **Keep**: ADX gate (helps filter in weak ema_cross/macd_direction).  
- **Remove/Demote**: pullback_ema integration (REJECT, hurts score).  
- **Tune**: Increase ADX_min toward 20-25 (weak edge at 0, but tiny samples); test RSI_confirm=True (slight PF lift in ema_cross); shorten fast_period (10 works weakly). NEEDS_MORE_DATA overall (single symbols/TFs, OOS fail). Not trustworthy due to missing multi-symbol data.

## 12. Engine B Recommendations (Price-Action Checklist)
No engine_b_proxy results. **NEEDS_MORE_DATA** (insufficient data; no BOS/FVG/OB setups tested). Keep strict checklist (pass/fail). No changes.

## 13. Engine D Recommendations (VP/OrderFlow Scalping)
No engine_d_proxy or crypto results. **NEEDS_MORE_DATA** (no POC/VAH/CVD data). No changes to grades A-D.

## 14. Next Smallest Useful Test
Expand to 8+ forex symbols (add GBP/USD, USD/JPY, USD/CAD), H1/H4/D1 TFs, trend_momentum family only (focus ema_cross/macd_direction with ADX_min=20+). Reason: Build clusters, hit >30 trades/symbol, check OOS.

## 15. What Should NOT Be Tested Further Right Now
- **pullback** family (all 48 REJECT, no edge even gross).  
- **trend_momentum** with pullback_period/RSI_reclaim (bleeds into pullback_ema, hurts).  
- ema_cross slow_period=100/200 (REJECT/NEEDS_MORE_DATA, low trades/negative).

**Overall Verdict**: Weak signals only (0 STRONG_CANDIDATE). Prioritize data expansion over tuning. Results not trustworthy due to tiny samples, no multi-symbol/TF clusters, consistent OOS decay.

```json
{
  "overall_verdict": "WEAK_CANDIDATE across board; no robust edge, heavy OOS decay, tiny samples/single symbols. NEEDS_MORE_DATA for clusters.",
  "top_candidates": [
    {"strategy": "macd_direction (adx_min=0|fast=12|signal_period=9|slow=26)", "symbol": "EUR/USD", "tf": "H4", "label": "WEAK_CANDIDATE"},
    {"strategy": "ema_cross (adx_min=20|fast_period=10|rsi_confirm=False|slow_period=50)", "symbol": "AUD/USD", "tf": "H1", "label": "WEAK_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "pullback_ema", "reason": "All configs net-negative, low WR, no pass_rate"},
    {"strategy": "ema_cross (slow_period=100/200 variants)", "reason": "Net-negative or <20 trades"}
  ],
  "engine_a": {
    "keep": ["ADX gate (min=20)"],
    "remove_or_demote": ["pullback_ema integration"],
    "tune": ["ADX_min toward 20-25", "RSI_confirm=True", "fast_period=10"],
    "next_tests": ["Multi-symbol H1/H4/D1 on ema_cross/macd_direction"]
  },
  "engine_b": {
    "keep": [],
    "remove_or_demote": [],
    "tune": [],
    "next_tests": ["Add engine_b_proxy (BOS/FVG/OB) on forex H1/H4"]
  },
  "engine_d": {
    "keep": [],
    "remove_or_demote": [],
    "tune": [],
    "next_tests": ["Crypto symbols with VP/OrderFlow proxies"]
  },
  "data_quality_warnings": ["Tiny samples (<30 trades) in 330/540 configs", "No multi-symbol/TF clusters", "Consistent OOS failure (negative returns)"],
  "telemetry_warnings": [],
  "next_tiny_test": {
    "symbols": ["GBP/USD", "USD/JPY", "USD/CAD", "EUR/USD", "AUD/USD"],
    "timeframes": ["H1", "H4", "D1"],
    "strategy_families": ["trend_momentum"],
    "reason": "Build >30 trade samples/symbol, test OOS robustness, focus ema_cross/macd_direction"
  },
  "do_not_do_next": ["pullback family", "trend_momentum pullback_period/RSI_reclaim variants"]
}
```