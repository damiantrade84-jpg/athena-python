# Athena Research Lab — AI Review
**Run ID:** `run_20260429_073857_9a5fb1`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-29 07:39 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** `run_20260429_073857_9a5fb1` | **Analyst Review Date:** Current | **Mode:** tiny | **Scope:** mean_reversion family only, crypto symbols (ADA/USDT, BNB/USDT, ETH/USDT), M15/H1 TFs  
**Key Caveats:** All findings from intentionally loose BT_MIN discovery thresholds (not for live). Tiny samples dominate (<30 trades heavily penalized). No clusters across symbols/TFs (penalized). No IS/OOS collapse evident but OOS often weaker (e.g., ADA M15 bollinger_touch oos_return -0.0110). Data trustworthy but limited (binance_rest source, no missing telemetry noted). **No live execution or threshold copying recommended.**

## 1. Strategy Family Assessment
- **mean_reversion** (only family tested): WEAK_CANDIDATE  
  Avg net_return 0.0801 across 8 valid configs, WR 0.7320, PF 5.8487, but tiny samples (avg 26.75 trades), symbol-specific (no multi-symbol cluster), mixed OOS (avg 0.0372). Gross/net positive everywhere valid. Prefers robustness → weak due to no scale.

## 2. Top Helping Indicator
**vwap_deviation** (std_threshold=1.5): WEAK_CANDIDATE  
Highest avg_net_return (0.2343), avg_WR 0.8774, avg_SQN 5.6785, avg_oos_return 0.0718. Standout: ADA/USDT H1 (21 trades, PF 23.97, net 0.3671, robustness 0.7347). ETH/USDT M15 similar (20 trades). But <30 trades + 2 symbols → weak, needs cluster.

## 3. Worst Hurting Indicator
**bollinger_touch** (num_std=2.0/2.5, period=20): WEAK_CANDIDATE (borderline REJECT)  
Avg_net_return -0.0138 across configs (some positive like ADA M15 0.0549, but OOS fails e.g., ADA H1 -0.0679). Avg_WR 0.6239, low SQN -0.2017. More trades (avg ~30) but inconsistent OOS + fees erode edge → hurts reliability.

**rsi_extreme** (overbought=70/75, oversold=25/30, period=14): WEAK_CANDIDATE  
Avg_net_return 0.0729, but tiny samples everywhere (<21 trades valid) → NEEDS_MORE_DATA. BNB/USDT H1 (21 trades, net 0.0729) mild promise.

## 4. Asset Group
**crypto**: WEAK_CANDIDATE  
Only group (8 valids): net 0.0801, but tiny mode + 3 symbols only → no broad edge proof.

## 5. Symbol
**ADA/USDT**: WEAK_CANDIDATE  
Best avg_net 0.0914 (5 configs), WR 0.7181. But single-symbol dominance penalized (no cluster). ETH/USDT (0.1015, 1 config) flash but NEEDS_MORE_DATA (20 trades). BNB/USDT (0.0413) weaker.

## 6. Timeframe
**H1**: WEAK_CANDIDATE  
Best avg_net 0.0964 (5 configs), robustness 0.5758. M15 (0.0530, OOS -0.0043 avg) trails.

## 7. Session
**all**: NEEDS_MORE_DATA  
Only tested → no differentiation.

## 8. Direction
**both**: WEAK_CANDIDATE  
Only tested → no long/short split.

## 9. Setups Collapsing After Fees
None. All valid configs gross-profitable + net-positive (e.g., top vwap ADA H1 gross/net both 0.3671). No penalization here.

## 10. Too Little Sample (<30 trades penalized)
33/48 configs (69%):  
- **rsi_extreme** (all 24 configs, e.g., ADA M15/H1 6-19 trades): NEEDS_MORE_DATA  
- **vwap_deviation** (many <20, e.g., ADA M15 16-19, H1 std=2.0 16 trades): NEEDS_MORE_DATA  
- **bollinger_touch** (some ok ~40, but penalized if <30 e.g., ADA M15 std=2.5 22 trades)

## 11. Engine A Recommendations (EMA/RSI/MACD/ADX factors)
No direct trend_momentum/pullback data (all mean_reversion proxies).  
- **Keep:** RSI momentum quality (rsi_extreme shows WR~76% in valids) as contrarian filter.  
- **Remove/Demote:** None definitive.  
- **Tune:** RSI period=14 oversold~30 (mild edge BNB H1); test overbought 70-75 lower for crypto mean-reversion. Avoid BB touch (inconsistent). Do NOT raise live floor from 2.1. VWAP deviation unproven for EMA/ADX gate. NEEDS_MORE_DATA overall.

## 12. Engine B Recommendations (PA checklist: BOS/FVG/OB etc.)
No engine_b_proxy data.  
- **Keep/Remove/Tune:** No changes (insufficient data).  
- **Next:** Add mean_reversion proxies to B checklist (e.g., FVG at VWAP dev >1.5 std).

## 13. Engine D Recommendations (VP/OF scalping: POC/VAH/CVD/VWAP, crypto)
VWAP deviation proxy shows crypto H1/M15 promise (ADA/ETH).  
- **Keep:** VWAP as AAA sequence filter (high SQN 5.67 avg).  
- **Remove/Demote:** None.  
- **Tune:** std_threshold=1.5 for grade B/C (21-20 trades edge); test with POC/VAL absorption. Avoid RSI extremes (tiny samples).

## 14. Next Smallest Useful Test
Expand tiny mode: Add 5-7 more crypto symbols (e.g., BTC/SOL/XRP/USDT), test H1/M15 + M30, same mean_reversion family. Focus vwap_deviation std=1.5 + rsi_extreme 70/30. Goal: 50+ trades/config, multi-symbol cluster.

## 15. Do NOT Test Further Right Now
- **rsi_extreme** extreme params (75/25): All <15 trades → REJECT low-sample variants.  
- **bollinger_touch** num_std=2.5: Low expectancy, OOS weak → REJECT until clustered.  
- Single-symbol deep dives (e.g., only ADA): Penalized, run multi-symbol first.

**Overall Verdict:** Weak signals in mean_reversion crypto (VWAP best but tiny/isolated). No strong edges; prioritize scale over tuning. Robustness low (avg 0.5453).

```json
{
  "overall_verdict": "WEAK_CANDIDATE: mean_reversion shows mild crypto edge (VWAP/RSI), but tiny samples + no clusters penalize. NEEDS_MORE_DATA for live relevance.",
  "top_candidates": [
    {"strategy": "vwap_deviation std_threshold=1.5", "symbol": "ADA/USDT", "tf": "H1", "label": "WEAK_CANDIDATE"},
    {"strategy": "vwap_deviation std_threshold=1.5", "symbol": "ETH/USDT", "tf": "M15", "label": "WEAK_CANDIDATE"},
    {"strategy": "rsi_extreme overbought=70|oversold=30|period=14", "symbol": "BNB/USDT", "tf": "H1", "label": "WEAK_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "rsi_extreme (75/25 params)", "reason": "tiny samples <15 trades"},
    {"strategy": "bollinger_touch num_std=2.5", "reason": "negative avg_net_return, poor OOS"}
  ],
  "engine_a": {
    "keep": ["RSI momentum (period=14)"],
    "remove_or_demote": [],
    "tune": ["RSI oversold ~25-30, overbought 70 for crypto mean-reversion"],
    "next_tests": ["Add VWAP dev as ADX proxy on H1 crypto"]
  },
  "engine_b": {
    "keep": [],
    "remove_or_demote": [],
    "tune": [],
    "next_tests": ["Proxy mean_reversion (VWAP/RSI) into FVG/OB checklist"]
  },
  "engine_d": {
    "keep": ["VWAP deviation std=1.5"],
    "remove_or_demote": [],
    "tune": ["Pair with POC/VAH for grade B/C crypto H1/M15"],
    "next_tests": ["CVD absorption at VWAP extremes"]
  },
  "data_quality_warnings": ["Tiny mode: avg 26 trades/config; 69% <30 trades penalized"],
  "telemetry_warnings": [],
  "next_tiny_test": {
    "symbols": ["BTC/USDT", "SOL/USDT", "XRP/USDT", "LINK/USDT", "DOT/USDT"],
    "timeframes": ["H1", "M15", "M30"],
    "strategy_families": ["mean_reversion"],
    "reason": "Build multi-symbol clusters for VWAP/RSI; target 50+ trades/config"
  },
  "do_not_do_next": [
    "rsi_extreme 75/25 params (low sample)",
    "Single-symbol deep dives",
    "bollinger_touch 2.5 std (OOS weak)"
  ]
}
```