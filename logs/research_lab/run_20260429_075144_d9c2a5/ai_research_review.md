# Athena Research Lab — AI Review
**Run ID:** `run_20260429_075144_d9c2a5`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-29 07:52 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260429_075144_d9c2a5 | **Analyst:** Quantitative Research | **Date:** 2026-04-29  
**Key Caveat:** These are discovery results at lowered BT_MIN thresholds. **Do NOT execute live, copy thresholds to live gates, or deploy without multi-symbol OOS validation.** All findings penalized for tiny samples (<30 trades: 194/304 configs), single-symbol focus (only XAU/USD, XAG/USD), and inconsistent IS/OOS (many positive IS, weak/mixed OOS). No robust clusters across symbols/TFs. Robustness scores mediocre (avg 0.58). Data trustworthy but limited (MT5 source, no missing telemetry noted).

## 1. Strategy Family Performance
- **pullback**: WEAK_CANDIDATE (13/16 pass, highest avg net_return 0.56, PF 2.23; works on XAG/USD D1/H4 but single-symbol, OOS mixed).
- **engine_b_proxy**: WEAK_CANDIDATE (7/73 pass, avg net 0.40; structure_filters subset shows edge but high drawdowns).
- **trend_momentum**: REJECT (12/289 pass but mostly tiny samples <30; ema_cross/alignment weak, high rejects).
- **breakout**: WEAK_CANDIDATE (14/62 pass, low net 0.17; session_opening_range ok but OOS negative).
- **mean_reversion**: REJECT (0/32 pass).

Best family: **pullback** (WEAK_CANDIDATE).

## 2. Indicator Helped Most
**pullback_ema**: WEAK_CANDIDATE (pass_rate 81%, avg_net 0.48, avg_SQN 0.94; 694 trades across configs, consistent PF>2).

## 3. Indicator Hurt Most
**bollinger_touch**: REJECT (0% pass, avg_net -0.29, negative SQN/OOS).

## 4. Asset Group
**commodity**: WEAK_CANDIDATE (only group tested, avg net 0.35, WR 32%; no comparison).

## 5. Symbol
**XAG/USD**: WEAK_CANDIDATE (30 configs, net 0.40 > XAU/USD's 0.26; penalize single-symbol, needs multi-asset cluster).  
**XAU/USD**: NEEDS_MORE_DATA (16 configs, lower net; many tiny samples).

## 6. Timeframe
**D1**: WEAK_CANDIDATE (23 configs, net 0.38 > H4's 0.33; higher robustness 0.61).  
**H4**: WEAK_CANDIDATE (similar WR, but OOS weaker).

## 7. Session
**all**: WEAK_CANDIDATE (only tested; no session splits).

## 8. LONG vs SHORT
**both**: WEAK_CANDIDATE (only direction tested; no bias detected).

## 9. Setups Collapsed After Fees
None. All passing configs are net-positive (gross-profitable survive fees).

## 10. Setups with Too Little Sample (<30 trades)
- **trend_momentum** (125 configs, e.g., ema_cross all <20 trades: NEEDS_MORE_DATA).
- **engine_b_proxy** (32 configs).
- **mean_reversion** (26 configs).
- **breakout** (10 configs).
- **pullback** (1 config).

## 11. Engine A (Trend/Momentum Scoring: EMA/RSI/MACD/ADX)
**Keep:** EMA trend coherence (D1/H4/H1 alignment via ema_alignment: WEAK_CANDIDATE, avg_net 0.41).  
**Remove/Demote:** ema_cross (tiny samples, low pass_rate 3.5%: REJECT); macd_direction (hurts net -0.05: REJECT).  
**Tune:** Increase ADX gate (>20 helps PF in survivors); test RSI confirm=False (fewer but higher-quality trades). No live threshold changes.  
**Status:** Weak overall (high rejects); NEEDS_MORE_DATA on >5 symbols.

## 12. Engine B (Price-Action Checklist: BOS/FVG/OB/Swings)
**Keep:** structure_filters (fvg_detection=True/False + strong_close_pct=0.7-0.8: WEAK_CANDIDATE, 7 passes, WR~41-51%).  
**Remove/Demote:** None explicit, but proxy shows edge only in high-sample configs.  
**Tune:** Prioritize strong_close_pct>=0.8 and FVG=False (better OOS); add location/room gates for pullback synergy.  
**Status:** Promising proxy (WEAK_CANDIDATE); validate full checklist.

## 13. Engine D (VP/OrderFlow: POC/VAH/VAL/CVD/VWAP)
No results (crypto-focused, commodity data only). **NEEDS_MORE_DATA**. No changes; telemetry ok but irrelevant symbols.

## 14. Next Smallest Useful Test
Test **pullback_ema** (top params: pullback_period=20/50|rsi_reclaim=False|rsi_threshold=50|trend_period=200) on 3+ forex majors (EURUSD, GBPUSD, USDJPY) D1/H4. Reason: Builds cluster around best family/symbol/TF; low compute (reuse MT5 data).

## 15. What Should NOT Be Tested Further Right Now
- **bollinger_touch**, **prev_day_hl**, **macd_direction** (consistent hurts/rejects).  
- **mean_reversion** family (0 passes).  
- D1-only ema_cross (tiny samples, no edge).

**Overall Verdict:** Weak edges in pullback_ema on XAG/USD D1/H4 (WEAK_CANDIDATE cluster); Engine A needs tuning, B proxy viable. Prioritize multi-symbol validation. No live actions.

```json
{
  "overall_verdict": "Weak pullback edges on commodities; no strong candidates. Prioritize multi-symbol clusters.",
  "top_candidates": [
    {"strategy": "pullback_ema (pullback_period=20|rsi_reclaim=False|rsi_threshold=50|trend_period=200)", "symbol": "XAG/USD", "tf": "D1", "label": "WEAK_CANDIDATE"},
    {"strategy": "pullback_ema (pullback_period=50|rsi_reclaim=False|rsi_threshold=50|trend_period=200)", "symbol": "XAU/USD", "tf": "D1", "label": "WEAK_CANDIDATE"},
    {"strategy": "structure_filters (fvg_detection=False|strong_close_pct=0.8)", "symbol": "XAG/USD", "tf": "D1", "label": "WEAK_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "bollinger_touch", "reason": "0% pass, negative net/SQN"},
    {"strategy": "mean_reversion", "reason": "0 passes"},
    {"strategy": "ema_cross (all params)", "reason": "Tiny samples <30, low pass_rate"},
    {"strategy": "macd_direction", "reason": "Hurts net_return"}
  ],
  "engine_a": {
    "keep": ["ema_alignment (adx_min=0|ema_long=200|ema_mid=50|ema_short=20)"],
    "remove_or_demote": ["ema_cross", "macd_direction"],
    "tune": ["ADX gate >20", "RSI confirm=False"],
    "next_tests": ["Test on forex majors D1/H4"]
  },
  "engine_b": {
    "keep": ["structure_filters (strong_close_pct>=0.8)"],
    "remove_or_demote": [],
    "tune": ["FVG=False priority", "Add pullback location synergy"],
    "next_tests": ["Full checklist on XAG/USD + forex"]
  },
  "engine_d": {
    "keep": [],
    "remove_or_demote": [],
    "tune": [],
    "next_tests": ["Crypto symbols (BTC/ETH) with VP params"]
  },
  "data_quality_warnings": ["194/304 tiny samples <30 trades", "Only 2 symbols (penalized)"],
  "telemetry_warnings": [],
  "next_tiny_test": {
    "symbols": ["EURUSD", "GBPUSD", "USDJPY"],
    "timeframes": ["D1", "H4"],
    "strategy_families": ["pullback"],
    "reason": "Validate top pullback_ema on forex for cluster"
  },
  "do_not_do_next": [
    "bollinger_touch",
    "mean_reversion",
    "prev_day_hl heavy tuning"
  ]
}
```