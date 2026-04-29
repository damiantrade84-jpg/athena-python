# Athena Research Lab — AI Review
**Run ID:** `run_20260427_130708`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-27 13:09 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260427_130708 | **Analyst:** Quantitative Research | **Date:** 2026-04-27  
**Key Caveat:** These are discovery results at lowered BT_MIN thresholds (worse than live). Do NOT recommend live execution, threshold copies, or direct deployment. All findings penalized for tiny samples (<30 trades), single-symbol results, and lack of OOS consistency. No robust clusters found. Total valid: 4 (1.4%), all WEAK_CANDIDATE due to small N and isolation.

## 1. Strategy Family Performance
- **mean_reversion**: WEAK_CANDIDATE (4 configs, avg net_return 0.0313, avg WR 0.6931, avg SQN 0.8032; but tiny N=28.5 avg, single symbols, mixed OOS).
- **breakout**: REJECT (110 configs, consistent net-negative, poor SQN, fails fees/OOS).
- **engine_d_proxy**: REJECT (100 configs, net-negative, low WR<30%, high DD).

## 2. Indicator Helped Most
**rsi_extreme** (period=14, overbought=70/oversold=30): WEAK_CANDIDATE (1/40 configs pass, net_return 0.0524 on BNB/USDT H1, SQN 1.0960; but N=21<30, single symbol).

## 3. Indicator Hurt Most
**prev_day_hl**: REJECT (avg net_return -0.4547, SQN -3.96, WR 13.6%; tiny N, fails OOS).

## 4. Asset Group Performance
**crypto**: WEAK_CANDIDATE (only group tested, avg net 0.0313; but no forex/equity comparison, all weak/single-symbol).

## 5. Symbol Performance
**BNB/USDT**: WEAK_CANDIDATE (net 0.0524, WR 71.4%, SQN 1.096, robustness 0.74; N=21<30, OOS+).
- ADA/USDT: WEAK_CANDIDATE (net 0.0313, N=41>30 but OOS-).
- Others (BTC/SOL): WEAK_CANDIDATE but penalize OOS~0 and N<30.

## 6. Timeframe Performance
**H1**: WEAK_CANDIDATE (net 0.0524 on BNB; N=21).
**M15**: WEAK_CANDIDATE (avg net 0.0243 across 3; but mixed OOS).

## 7. Session Performance
**all**: WEAK_CANDIDATE (only tested; no session splits show edge).

## 8. LONG vs SHORT
**both**: WEAK_CANDIDATE (only direction tested; no long/short split, cannot prefer).

## 9. Setups Collapsed After Fees
None. All gross-profitable setups survive fees (no gross+ / net- cases).

## 10. Setups with Too Little Sample (<30 trades)
Penalized 158 configs:
- **breakout** (63): prev_day_hl, london_breakout, ny_breakout, session_opening_range (many N<10).
- **mean_reversion** (59): bollinger_touch, rsi_extreme (most N<30).
- **engine_d_proxy** (36): vwap_reclaim, micro_breakout, ema_scalp_pullback.

## 11. Engine A Recommendations
NEEDS_MORE_DATA (no trend_momentum/pullback results; all families lack Engine A proxies).  
- **Keep**: N/A.  
- **Remove/Demote**: N/A.  
- **Tune**: Test EMA/RSI+MACD/ADX confluences >2.1 on crypto H1/M15 (direction: raise ADX gate for volatility filter). Not trustworthy: zero valid data.

## 12. Engine B Recommendations
NEEDS_MORE_DATA (no engine_b_proxy; breakout family as proxy fails).  
- **Keep**: N/A.  
- **Remove/Demote**: Reject BOS/CHoCH + FVG/OB in low-sample sessions (london/ny_breakout).  
- **Tune**: Tighten range_bars>12 and atr_expand_min>0.5 for opening_range (direction: stricter location/trigger). Not trustworthy: zero valid.

## 13. Engine D Recommendations
REJECT (engine_d_proxy: 100 configs net-negative, WR<30%, high DD; e.g., vwap_reclaim/micro_breakout fail fees/OOS).  
- **Keep**: N/A.  
- **Remove/Demote**: vwap_reclaim (band_std/atr_sl_mult all hurt), ema_scalp_pullback, micro_breakout.  
- **Tune**: Raise VP grade to B+ (POC/VAH/VAL), add CVD absorption filter (direction: higher AAA sequence reqs for crypto M15). Not trustworthy: telemetry shows binance_rest gaps, tiny N.

## 14. Next Smallest Useful Test
Expand **mean_reversion** (rsi_extreme/bollinger_touch) to 10+ crypto symbols (add ETH/ XRP/DOGE) on H1/M15, both directions, all sessions. Target N>50 per config. Reason: Only family with edge, test for clusters.

## 15. What Should NOT Be Tested Further Right Now?
- **engine_d_proxy** (VP/OrderFlow scalps: no edge, hurts SQN/net).
- **breakout** (session/prev_day/london/ny: consistent loser post-fees).
- Single-symbol one-offs without OOS lift.

**Overall:** Weak mean_reversion signals in crypto (no STRONG_CANDIDATE). Prioritize data expansion over deployment. Robustness low (avg 0.51).

```json
{
  "overall_verdict": "Weak mean_reversion edge in crypto (4 WEAK_CANDIDATE, tiny N/single symbols); reject breakout/engine_d_proxy. NEEDS_MORE_DATA for engines.",
  "top_candidates": [
    {"strategy": "rsi_extreme", "symbol": "BNB/USDT", "tf": "H1", "label": "WEAK_CANDIDATE"},
    {"strategy": "bollinger_touch", "symbol": "ADA/USDT", "tf": "M15", "label": "WEAK_CANDIDATE"},
    {"strategy": "bollinger_touch", "symbol": "BTC/USDT", "tf": "M15", "label": "WEAK_CANDIDATE"},
    {"strategy": "bollinger_touch", "symbol": "SOL/USDT", "tf": "M15", "label": "WEAK_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "prev_day_hl", "reason": "worst net_return -0.4547, tiny N"},
    {"strategy": "ema_scalp_pullback", "reason": "net -0.2599, low WR"},
    {"strategy": "micro_breakout", "reason": "net -0.1253, fails OOS"},
    {"strategy": "vwap_reclaim", "reason": "net -0.1079, low WR"},
    {"strategy": "session_opening_range", "reason": "net -0.0978, poor SQN"},
    {"strategy": "london_breakout", "reason": "net -0.1252, tiny N"},
    {"strategy": "ny_breakout", "reason": "net -0.1419, poor SQN"}
  ],
  "engine_a": {
    "keep": [],
    "remove_or_demote": [],
    "tune": ["raise ADX gate for crypto volatility"],
    "next_tests": ["test EMA/RSI+MACD confluences >2.1 on H1/M15 crypto"]
  },
  "engine_b": {
    "keep": [],
    "remove_or_demote": ["BOS/CHoCH + FVG/OB in london/ny sessions"],
    "tune": ["tighten range_bars>12, atr_expand_min>0.5"],
    "next_tests": []
  },
  "engine_d": {
    "keep": [],
    "remove_or_demote": ["vwap_reclaim", "ema_scalp_pullback", "micro_breakout"],
    "tune": ["raise to VP grade B+, add CVD filter"],
    "next_tests": []
  },
  "data_quality_warnings": ["158 tiny samples <30 trades", "single symbols only (no clusters)", "mixed OOS (e.g. ADA/SOL negative)"],
  "telemetry_warnings": ["binance_rest gaps in engine_d_proxy", "no IS/OOS split reliability for most"],
  "next_tiny_test": {
    "symbols": ["ETH/USDT", "XRP/USDT", "DOGE/USDT"],
    "timeframes": ["H1", "M15"],
    "strategy_families": ["mean_reversion"],
    "reason": "expand top family for N>50 clusters"
  },
  "do_not_do_next": ["engine_d_proxy", "breakout", "session_opening_range", "prev_day_hl"]
}
```