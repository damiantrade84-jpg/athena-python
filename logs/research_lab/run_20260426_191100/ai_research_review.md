# Athena Research Lab — AI Review
**Run ID:** `run_20260426_191100`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-26 19:13 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260426_191100 | **Analyst:** Quantitative Research | **Date:** 2026-04-27  
**Key Caveat:** These are discovery results at lowered BT_MIN thresholds. **Do NOT execute live, copy thresholds to live gates, or treat as production signals.** All labels per safety rules. Robustness penalized for <30 trades, single-symbol, fee-negative, IS/OOS decay. No telemetry bugs noted; all data from binance_rest/mt5/eodhd (trustworthy sources, no missing data flags).

## 1. Strategy Family Performance
- **pullback**: WEAK_CANDIDATE (highest avg_net_return 0.197 across 15 configs, but low WR 0.264, HURTS per attribution; clusters on XAG/USD H4).
- **mean_reversion**: STRONG_CANDIDATE (bollinger_touch leads tops with 2 STRONG_CANDIDATEs: SOL/USDT H4 39 trades PF=2.44, ETH/USDT H4 24 trades PF=2.76; avg_net 0.153, high WR 0.658).
- **engine_b_proxy**: WEAK_CANDIDATE (structure_filters shows edge on MSFT/XAG/XAU H4, but overall HURTS attribution).
- **breakout**: REJECT (session_opening_range/micro_breakout weak OOS decay).
- **trend_momentum**: REJECT (ema_cross/macd_direction globally HURTS, tiny samples dominate).
- **engine_d_proxy**: WEAK_CANDIDATE (micro_breakout STRONG on XAU H4 86 trades, but HURTS overall).
- **volatility**: REJECT (bb_squeeze/atr_compression low robustness).

## 2. Indicator Helped Most
**bollinger_touch** (mean_reversion): STRONG_CANDIDATE (25% pass_rate, only positive avg_net_return +0.008, tops charts on SOL/ETH H4/NAS100 H4/WTI M15; robust WR>70%, multi-symbol cluster).

## 3. Indicator Hurt Most
**ema_scalp_pullback** (pullback): REJECT (most negative avg_net -0.192, 4% pass_rate, destroys edge).

## 4. Asset Group Performance
- **commodity**: STRONG_CANDIDATE (top net_return 0.1995, PF 1.74; XAG/XAU/WTI leaders).
- **crypto**: WEAK_CANDIDATE (net 0.169, SOL/ETH/BTC strong but OOS -0.021).
- **index**: WEAK_CANDIDATE (NAS100/GER40/US500 modest).
- **stock**: REJECT (low net 0.048, single-symbol risk).
- **forex**: REJECT (near-zero expectancy 0.0007, OOS decay).

## 5. Symbol Performance
**XAG/USD** (commodity): STRONG_CANDIDATE (top net 0.348, 18 configs; pullback/structure_filters edge on H4 despite low WR—high expectancy 0.0113).  
**SOL/USDT**: WEAK_CANDIDATE (net 0.280, WR 0.625; bollinger H4).  
Others (BTC/ETH/XAU/WTI): WEAK_CANDIDATE (decent but single-TF bias).

## 6. Timeframe Performance
**H4**: STRONG_CANDIDATE (top net 0.161, 96 configs, WR 0.430; all top-10 except 2).  
**M15**: WEAK_CANDIDATE (high WR 0.574 but low count 12).  
**H1**: REJECT (OOS collapse -0.034).

## 7. Session Performance
**all**: NEEDS_MORE_DATA (only data; no session splits—untrustworthy for isolation).

## 8. LONG vs SHORT
**both**: NEEDS_MORE_DATA (only 'both' direction tested—no long/short split; cannot compare).

## 9. Setups Collapsed After Fees
None. No gross-profitable but net-negative cases (all penalized pre-report).

## 10. Setups with Too Little Sample (<30 trades penalized)
- **trend_momentum** (1384 configs): REJECT (e.g., ema_cross ETH M15 11-29 trades).
- **engine_b_proxy** (358): REJECT.
- **mean_reversion** (306): NEEDS_MORE_DATA (bollinger_touch some <30).
- **breakout** (296): REJECT.
- **engine_d_proxy** (195): REJECT.
- **volatility** (191): REJECT.
- **pullback** (24): NEEDS_MORE_DATA.

## 11. Engine A (3-factor: EMA/RSI+MACD/ADX)
- **Keep**: MACD momentum (STRONG_CANDIDATE on ETH H1/H4, 54-56 trades PF>1.4).
- **Remove/Demote**: EMA cross (REJECT, HURTS -0.132 net, tiny samples); RSI confirm (no lift).
- **Tune**: Raise ADX gate >20 (filters noise, e.g., adx_min=20 MACD holds); test EMA coherence D1/H4 only (H1 volatile).
- No live threshold changes.

## 12. Engine B (Price-action: BOS/CHoCH/FVG/OB/swing)
- **Keep**: structure_filters (WEAK_CANDIDATE on MSFT/XAG/XAU H4, 32-58 trades PF>1.5).
- **Remove/Demote**: FVG detection (weak lift); OB/BOS (0 strong, HURTS).
- **Tune**: Strong_close_pct=0.7 gate (consistent in tops); add location/room filter for commodities.
- Checklist too strict—relax for H4 discovery only.

## 13. Engine D (VP/OrderFlow: POC/VAH/VAL/Absorption/CVD/VWAP scalping)
- **Keep**: micro_breakout range_bars=3 (STRONG_CANDIDATE XAU H4 86 trades); atr_sl_mult=0.5-1.0.
- **Remove/Demote**: AAA sequence (untested/failed proxies).
- **Tune**: Fee_guard_r=0.5 (survives); VP grades A/B for XAU/XAG (crypto focus weak); lower range_bars for H4.
- Crypto bias confirmed weak.

## 14. Next Smallest Useful Test
Expand **bollinger_touch** (num_std=2.0-2.5|period=20) on commodity/index H4 (XAG/SOL/NAS100/WTI) + 2 more symbols (e.g., GBP/USD, NVDA) for cluster confirmation (target >50 trades/symbol, IS/OOS check).

## 15. What Should NOT Be Tested Further Right Now
- ema_cross (1728 configs, all HURTS/tiny).
- Single-symbol M15 trend_momentum (e.g., ETH ema_cross <30 trades).
- Forex H1 (OOS decay).
- Long-only/short-only without 'both' baseline.

**Overall:** Focus mean_reversion/bollinger on H4 commodities (STRONG_CANDIDATE cluster). Trend/breakout families REJECT—overfitted.

```json
{
  "overall_verdict": "Prioritize mean_reversion (bollinger_touch) on H4 commodities; demote trend_momentum/breakout globally.",
  "top_candidates": [
    {"strategy": "bollinger_touch", "symbol": "SOL/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "bollinger_touch", "symbol": "ETH/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "structure_filters", "symbol": "MSFT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "micro_breakout", "symbol": "XAU/USD", "tf": "H4", "label": "STRONG_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "ema_cross", "reason": "HURTS globally, tiny samples"},
    {"strategy": "ema_scalp_pullback", "reason": "Most negative net_return"},
    {"strategy": "ob_bos", "reason": "0 strong, HURTS"}
  ],
  "engine_a": {
    "keep": ["macd_direction (adx_min=0-20)"],
    "remove_or_demote": ["ema_cross", "rsi_confirm"],
    "tune": ["ADX >20", "EMA D1/H4 coherence"],
    "next_tests": ["MACD on commodity H4"]
  },
  "engine_b": {
    "keep": ["structure_filters (strong_close_pct=0.7)"],
    "remove_or_demote": ["fvg_detection", "ob_bos"],
    "tune": ["Add commodity location filter"],
    "next_tests": ["H4 structure on XAG/XAU"]
  },
  "engine_d": {
    "keep": ["micro_breakout range_bars=3 atr_sl=0.5-1.0"],
    "remove_or_demote": ["AAA sequence proxies"],
    "tune": ["VP grades A/B for XAU", "fee_guard_r=0.5"],
    "next_tests": ["XAU/XAG H4 expansion"]
  },
  "data_quality_warnings": [],
  "telemetry_warnings": [],
  "next_tiny_test": {
    "symbols": ["XAG/USD", "SOL/USDT", "NAS100", "WTI Oil"],
    "timeframes": ["H4"],
    "strategy_families": ["mean_reversion"],
    "reason": "Confirm bollinger_touch cluster >50 trades/symbol"
  },
  "do_not_do_next": [
    "ema_cross any TF",
    "M15 trend_momentum single-symbol",
    "Forex H1"
  ]
}
```