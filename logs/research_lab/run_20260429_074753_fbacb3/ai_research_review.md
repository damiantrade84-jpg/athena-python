# Athena Research Lab — AI Review
**Run ID:** `run_20260429_074753_fbacb3`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-29 07:48 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260429_074753_fbacb3 | **Analyst:** Quantitative Research | **Date:** 2026-04-29  
**Key Caveat:** These are discovery results at lowered BT_MIN thresholds. **Do NOT execute live, copy thresholds to live gates, or deploy without multi-symbol OOS validation.** All labels per safety rules. Robustness penalized for single-symbol (BNB/USDT dominance), small samples (<30 trades), and limited data (only 72 rows, 2 symbols, no Engine B/D proxies).

## 1. Strategy Family Performance
- **pullback**: WEAK_CANDIDATE (3/24 configs pass; strong PF>2.0 on BNB/USDT D1 but single-symbol, low WR~34%, hurts globally per attribution; avg net_return 1.50 but only crypto).
- **mean_reversion**: REJECT (45/45 rejected or NEEDS_MORE_DATA; no passers despite high WR in small samples; collapses OOS/net).

## 2. Indicator Helped Most
**bollinger_touch**: STRONG_CANDIDATE (25% pass rate, "HELPS" verdict; viable mean-reversion proxy on BNB/USDT H4/D1 with WR>69%, but small samples 17-42 trades penalize to weak cluster potential).

## 3. Indicator Hurt Most
**pullback_ema**: REJECT (12.5% pass rate, "HURTS" verdict; 19/24 rejected, negative avg SQN -2.63 despite top gross returns on BNB D1; EURUSD H4 fully rejected, no multi-symbol robustness).

## 4. Asset Group
**crypto**: STRONG_CANDIDATE (5/6 valid passers, avg net_return 0.95, PF 2.15; beats stock).  
**stock** (EURUSD mislabel? forex-like): REJECT (1 weak passer, low returns 0.016).

## 5. Symbol
**BNB/USDT**: WEAK_CANDIDATE (5/6 valid, avg net 0.95; penalized heavily for no multi-symbol replication—**all edge isolated here**).  
**EURUSD**: REJECT (all pullback REJECT, mean_reversion NEEDS_MORE_DATA).

## 6. Timeframe
**D1**: STRONG_CANDIDATE (4/6 valid, highest net 1.13, but single-symbol).  
**H4**: WEAK_CANDIDATE (2 valid, high WR 70% but lower returns 0.13, small samples).

## 7. Session
**all**: NEEDS_MORE_DATA (only data; no session splits to evaluate).

## 8. LONG vs SHORT
**both**: NEEDS_MORE_DATA (only direction tested; no split to compare).

## 9. Setups Collapsed After Fees
None (no gross-profitable but net-negative cases; all valid are net-positive).

## 10. Setups with Too Little Sample (<30 trades)
- **mean_reversion** (39 configs): rsi_extreme (all 17+ variants, 4-18 trades), vwap_deviation (4 trades), bollinger_touch (some 17 trades).
- **pullback** (2 configs): pullback_ema period=50 rsi_reclaim=True (18 trades).

## 11. Engine A (EMA/RSI+MACD/ADX)
**Keep:** EMA trend_period=200 + RSI threshold=50 base (powers top pullback_ema).  
**Remove/Demote:** No removals; rsi_reclaim gate unproven (mixed).  
**Tune:** Test pullback_period=20-50 tighter on crypto D1 (boosts PF>2.0); add ADX gate to filter low-momentum; **direction: raise RSI_reclaim selectivity**. Not trustworthy: no IS/OOS split details, single symbol. **No live threshold changes.**

## 12. Engine B (Price-Action Checklist)
NEEDS_MORE_DATA / TELEMETRY_BUG (zero engine_b_proxy results; no BOS/FVG/OB data). Keep all current strict checklist gates unchanged.

## 13. Engine D (VP/OrderFlow Crypto)
NEEDS_MORE_DATA (no engine_d_proxy; BNB/USDT crypto data but no POC/VAH/CVD tests). **Not trustworthy: missing telemetry.** Keep VP params as-is; no tunes.

## 14. Next Smallest Useful Test
**Tiny mode:** pullback_ema (period=20|rsi_reclaim=False/True|rsi=50|trend=200) on 3-5 crypto symbols (BTC/USDT, ETH/USDT, SOL/USDT) across D1/H4. Reason: Validate single-symbol BNB edge into cluster; penalize if fails OOS/multi-symbol.

## 15. What Should NOT Be Tested Further Right Now?
- mean_reversion (rsi_extreme, vwap_deviation): chronic small samples + rejects.  
- pullback_ema on forex/stock (EURUSD): full REJECT, no edge.  
- Single-symbol only (BNB/USDT isolation violates robustness).

**Overall:** Weak isolated edge in Engine A pullback_ema on crypto D1. Prioritize multi-symbol clusters before any engine tweaks. No live actions.

```json
{
  "overall_verdict": "Weak single-symbol edges in pullback_ema (Engine A proxy) on BNB/USDT crypto D1; heavy penalties for no multi-symbol/OOS clusters, small data. Mean-reversion rejected.",
  "top_candidates": [
    {"strategy": "pullback_ema pullback_period=20|rsi_reclaim=False|rsi_threshold=50|trend_period=200", "symbol": "BNB/USDT", "tf": "D1", "label": "STRONG_CANDIDATE"},
    {"strategy": "pullback_ema pullback_period=20|rsi_reclaim=True|rsi_threshold=50|trend_period=200", "symbol": "BNB/USDT", "tf": "D1", "label": "STRONG_CANDIDATE"},
    {"strategy": "bollinger_touch num_std=2.0|period=20", "symbol": "BNB/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "pullback_ema (all EURUSD H4)", "reason": "Full REJECT: negative returns, low PF"},
    {"strategy": "mean_reversion (rsi_extreme, vwap_deviation)", "reason": "Tiny samples + no edge"},
    {"strategy": "pullback (19/24 configs)", "reason": "Global HURTS verdict, single-symbol"}
  ],
  "engine_a": {
    "keep": ["EMA trend_period=200", "RSI threshold=50"],
    "remove_or_demote": [],
    "tune": ["pullback_period=20-50 on crypto D1", "Add ADX momentum filter"],
    "next_tests": ["Multi-crypto symbols D1/H4"]
  },
  "engine_b": {
    "keep": ["All current checklist gates"],
    "remove_or_demote": [],
    "tune": [],
    "next_tests": ["Add engine_b_proxy to discovery"]
  },
  "engine_d": {
    "keep": ["Current VP/POC/VAH params"],
    "remove_or_demote": [],
    "tune": [],
    "next_tests": ["Add engine_d_proxy on crypto"]
  },
  "data_quality_warnings": ["EURUSD asset_class='stock' mismatch (forex?)", "Tiny total rows=72, only 2 symbols"],
  "telemetry_warnings": ["No Engine B/D proxies", "Missing IS/OOS details per config", "No session/direction splits"],
  "next_tiny_test": {
    "symbols": ["BNB/USDT", "BTC/USDT", "ETH/USDT"],
    "timeframes": ["D1", "H4"],
    "strategy_families": ["pullback"],
    "reason": "Cluster-test top pullback_ema beyond single BNB/USDT"
  },
  "do_not_do_next": [
    "mean_reversion families",
    "EURUSD/forex pullback",
    "Single-symbol only"
  ]
}
```