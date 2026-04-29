# Athena Research Lab — AI Review
**Run ID:** `run_20260429_072220_0b956c`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-29 07:23 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260429_072220_0b956c | **Analyst:** Quantitative Research | **Date:** 2026-04-29  
**Key Caveat:** These are discovery results at lowered BT_MIN thresholds (worse than live). Do **NOT** execute live, copy thresholds to live gates, or deploy without multi-symbol OOS validation, walk-forward, and live telemetry. All labels per safety rules. Penalized: tiny samples (<30 trades), single-symbol, net-negative, IS>OOS fails (low robustness <0.5), no multi-symbol clusters.

## 1. Strategy Family Performance
- **mean_reversion**: STRONG_CANDIDATE (4 STRONG +13 WEAK in top; highest PF 2.85, WR 72%; clusters on H4 crypto like bollinger_touch across SOL/ADA/ETH/BNB; robust OOS survival).
- **breakout**: WEAK_CANDIDATE (8 passing; good net return 0.263 but lower WR/PF; BTC/ETH H4 prev_day_hl/session_opening_range; robustness ~0.5, OOS weak).
- **trend_momentum**: REJECT (10 weak passing but ema_cross/most hurt globally; low robustness 0.54; ETH H1 macd single-symbol outlier).
- **engine_b_proxy**: WEAK_CANDIDATE (4 weak; structure_filters BNB H4 high trades 191 but low expectancy).
- **pullback**: REJECT (all 60 failed; low WR/robustness).

**Best family overall: mean_reversion (STRONG_CANDIDATE).**

## 2. Indicator Helped Most
**bollinger_touch** (num_std=2.0-2.5|period=20): STRONG_CANDIDATE (pass_rate 56.7%; 17/30 configs passing; avg net +0.099; clusters H4 crypto; high WR 65%; multi-symbol SOL/ADA/ETH/BNB).

## 3. Indicator Hurt Most
**ema_cross** (all params): REJECT (0/540 pass; avg net -0.253; WR 16%; 245 rejects; hurts trend_momentum family; tiny samples penalized).

## 4. Asset Group
**crypto**: STRONG_CANDIDATE (only group tested; 41/1140 pass at 3.6%; avg net +0.180, PF 2.04, robustness 0.57; no comparison).

## 5. Symbol
**SOL/USDT**: STRONG_CANDIDATE (highest net 0.311, WR 61.9%; 5 passing incl. bollinger_touch H4 WEAK; but small cluster—needs multi-symbol confirm).  
**ETH/USDT**: WEAK_CANDIDATE (net 0.237; macd H1 STRONG). Others (ADA/BNB/BTC) WEAK_CANDIDATE or lower.

## 6. Timeframe
**H4**: STRONG_CANDIDATE (24/41 passing; highest net 0.255, trades 67 avg; bollinger/breakout clusters).  
**H1**: WEAK_CANDIDATE (13 passing; net 0.090).  
**M15**: REJECT (tiny samples <30; net 0.024).

## 7. Session
**all**: NEEDS_MORE_DATA (only session tested; no variance).

## 8. LONG vs SHORT
**both**: NEEDS_MORE_DATA (only direction tested; no long/short split).

## 9. Setups Collapsed After Fees
None. All passing are net-positive (gross/net aligned; no gross-profitable/net-negative).

## 10. Setups with Too Little Sample (<30 trades)
Penalized 574 configs:  
- **trend_momentum** (296; e.g., ema_cross SOL M15 14-34 trades).  
- **engine_b_proxy** (107).  
- **mean_reversion** (88).  
- **breakout** (83).  
All labeled REJECT/NEEDS_MORE_DATA.

## 11. Engine A (EMA/RSI+MACD/ADX) Recommendations
- **Keep**: MACD momentum (adx_min=0 H1 ETH STRONG_CANDIDATE; direction quality helps in clusters).  
- **Remove/Demote**: EMA cross/coherence (REJECT; all params hurt). ADX gate (min=0-25 no edge).  
- **Tune**: RSI confirm (mixed; disable in trend_momentum). Slow EMA (50-100 worse than 200? Test). Threshold direction: Raise confluence floor >2.1 live (backtests marginal).  
Not trustworthy single-symbol (ETH outlier).

## 12. Engine B (Price-Action Checklist: BOS/CHoCH/FVG/OB/Swing/Location/Trigger/RR) Recommendations
- **Keep**: Structure filters (fvg_detection=False|strong_close_pct=0.8 BNB H4 WEAK_CANDIDATE; high trades 191).  
- **Remove/Demote**: OB/BOS (REJECT; 0 pass, net -0.299).  
- **Tune**: FVG detection (disable improves); strong_close_pct (0.8 helps). Zone logic: Add RR room filter.  
Weak clusters; needs multi-symbol.

## 13. Engine D (VP/OF: POC/VAH/VAL/Absorption/CVD/VWAP/AAA) Recommendations
- **Keep**: VWAP deviation (std_threshold=1.5 ADA H1 WEAK_CANDIDATE; sqn 6.65 outlier).  
- **Remove/Demote**: None direct (no VP/POC/CVD tested).  
- **Tune**: Grade A/B/C/D thresholds (test crypto H1/H4). VP params untested—add.  
Crypto-focused; promising but NEEDS_MORE_DATA (1 weak).

## 14. Next Smallest Useful Test
Multi-symbol H4 mean_reversion bollinger_touch (num_std=2.0-2.5|period=20) on 10+ cryptos (add XRP/DOGE/LINK); split long/short + sessions (London/NY); 2-3x data window for OOS. Reason: Validate cluster robustness.

## 15. What Should NOT Be Tested Further Right Now
- ema_cross/pullback_ema (global rejects; no edge).  
- M15 all families (tiny samples).  
- Single-symbol outliers (e.g., ADA vwap).  
- NY/London breakout (0 pass). Wait for H4 cluster confirm.

**Overall:** Focus mean_reversion H4 crypto clusters. No live changes.

```json
{
  "overall_verdict": "mean_reversion H4 crypto (bollinger_touch) shows strongest cluster edge; trend_momentum/ema_cross globally weak. Prioritize multi-symbol validation.",
  "top_candidates": [
    {"strategy": "bollinger_touch (num_std=2.0|period=20)", "symbol": "SOL/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "bollinger_touch (num_std=2.5|period=20)", "symbol": "ADA/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "macd_direction (adx_min=0|fast=12|signal=9|slow=26)", "symbol": "ETH/USDT", "tf": "H1", "label": "STRONG_CANDIDATE"},
    {"strategy": "bollinger_touch (num_std=2.0|period=20)", "symbol": "BNB/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "bollinger_touch (num_std=2.5|period=20)", "symbol": "ETH/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "ema_cross", "reason": "0/540 pass; avg net -0.253; low WR 16%"},
    {"strategy": "ob_bos", "reason": "0/120 pass; avg net -0.299"},
    {"strategy": "pullback_ema", "reason": "0/60 pass; avg net -0.181"}
  ],
  "engine_a": {
    "keep": ["MACD direction (adx_min=0)"],
    "remove_or_demote": ["EMA cross/coherence", "ADX gate (min=0-25)"],
    "tune": ["RSI confirm (disable)", "Slow EMA periods (test 50-200)"],
    "next_tests": ["Multi-symbol H1 MACD OOS"]
  },
  "engine_b": {
    "keep": ["Structure filters (fvg=False|strong_close=0.8)"],
    "remove_or_demote": ["OB/BOS"],
    "tune": ["FVG detection (disable)", "Zone RR logic"],
    "next_tests": ["Multi-symbol H4 structure_filters"]
  },
  "engine_d": {
    "keep": ["VWAP deviation (std=1.5)"],
    "remove_or_demote": [],
    "tune": ["Grade A/B/C/D thresholds", "Add POC/VAH/VAL params"],
    "next_tests": ["Crypto H1/H4 VP+OF full"]
  },
  "data_quality_warnings": ["Only crypto/binance_rest; no forex/stocks comparison"],
  "telemetry_warnings": [],
  "next_tiny_test": {
    "symbols": ["SOL/USDT", "ETH/USDT", "ADA/USDT", "BNB/USDT", "BTC/USDT", "XRP/USDT"],
    "timeframes": ["H4"],
    "strategy_families": ["mean_reversion"],
    "reason": "Validate bollinger_touch cluster; add 1-2 new symbols/sessions"
  },
  "do_not_do_next": [
    "M15 timeframes",
    "ema_cross params",
    "Single-symbol deep dives",
    "NY/London breakout"
  ]
}
```