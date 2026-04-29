# Athena Research Lab — AI Review
**Run ID:** `run_20260427_171336`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-27 17:14 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Analyst:** Quantitative Research Analyst  
**Run ID:** run_20260427_171336  
**Date:** 2026-04-27  
**Scope:** 33 configs across 3 forex symbols (AUD/USD, USD/JPY, NZD/USD), H4 TF, breakout family only. All results use intentionally low BT_MIN thresholds (not for live). Samples penalized (<30 trades common). OOS consistently negative despite IS positive — major robustness penalty. No clusters across symbols/TFs. **No live execution recommended. Do not copy thresholds to live gates.**

## 1. Strategy Family Assessment
- **breakout** (only family tested): WEAK_CANDIDATE  
  Avg net_return 0.0355 (IS), but OOS -0.0245. Avg PF 1.35, WR 0.39, robustness 0.53. Gross/net both positive but tiny edge eroded by OOS failure and fees in marginal cases. 12/33 pass basic filters, but no multi-symbol cluster. Penalized for single TF/session and OOS decay.

## 2. Indicator Helped Most
No indicators qualify as helpful. **session_opening_range** (range_bars=6/12): WEAK_CANDIDATE (best top-ranked, PF up to 1.88 on AUD/USD, 44 trades), but OOS negative and single-symbol bias. Verdict from attribution: HURTS overall (pass_rate 0.5, avg_net 0.0009).

## 3. Indicator Hurt Most
**prev_day_hl**: REJECT (avg_net -0.0249, pass_rate 0.67 but OOS -0.03, hurts net after fees/OOS). Low WR (~0.32), consistent drawdowns.

## 4. Asset Group
**forex**: WEAK_CANDIDATE (only group, avg net 0.0355 IS / -0.0245 OOS, 12 configs). No comparison; not trustworthy due to limited symbols (3) and OOS failure.

## 5. Symbol
**AUD/USD**: WEAK_CANDIDATE (best: avg net 0.0543, WR 0.41, 7 configs). **USD/JPY / NZD/USD**: REJECT (lower net 0.0088/0.0095, OOS worse). Penalized: no robust cluster (AUD/USD dominates 7/12 valid).

## 6. Timeframe
**H4** (only): WEAK_CANDIDATE. No comparison; test more TFs needed.

## 7. Session
**all** (only): WEAK_CANDIDATE. london_breakout/ny_breakout: NEEDS_MORE_DATA (tiny samples). No edge isolation.

## 8. LONG vs SHORT
**both** (only direction tested): WEAK_CANDIDATE. No separation; NEEDS_MORE_DATA for directional bias.

## 9. Setups Collapsed After Fees
None purely gross-profitable but net-negative (all valid have gross/net aligned positive IS). However, penalize marginals like **breakout/prev_day_hl** (net ~0.00-0.02 eroded by fees/OOS across symbols).

## 10. Setups with Too Little Sample
Penalized all <30 trades. List:
- **breakout/london_breakout** (13-19 trades): NEEDS_MORE_DATA
- **breakout/ny_breakout** (0 trades): NEEDS_MORE_DATA
- Marginal: AUD/USD prev_day_hl atr=1.0 (27 trades), NZD/USD session_opening_range (30 trades) — still penalize per rule.

## 11. Engine A (3-factor scoring: EMA/RSI+MACD/ADX)
NEEDS_MORE_DATA — no valid trend_momentum/pullback results in this run. Not trustworthy: zero attribution. **Keep:** Current live floor (2.1). **Remove/Tune:** None. Next: Test EMA coherence gates on H4 forex.

## 12. Engine B (Naked price-action: BOS/FVG/OB etc.)
**breakout** proxies weak overall (WEAK_CANDIDATE family). **Keep:** session_opening_range logic (range_bars=6 strongest proxy). **Remove/Demote:** prev_day_hl (consistent OOS hurt). **Tune:** ATR_expand_min upward (0.5-1.0 variants slightly better PF); add multi-session filter to boost samples. Not trustworthy: single family, OOS fail.

## 13. Engine D (VP/OrderFlow: POC/VAH/CVD etc.)
NEEDS_MORE_DATA — no engine_d_proxy results (crypto-focused, forex run). **Keep/Remove/Tune:** None. Not trustworthy: missing data.

## 14. Next Smallest Useful Test
Tiny mode: Add 3-5 more forex symbols (EUR/USD, GBP/USD, USD/CAD), test H1/M15 TFs alongside H4, same breakout family +1 new (e.g., pullback). Reason: Build symbol/TF clusters, check OOS on fresh data.

## 15. What Should NOT Be Tested Further Right Now?
- **breakout/prev_day_hl**: REJECT (hurts net/OOS across symbols).
- **breakout/ny_breakout**: REJECT (zero trades).
Prioritize clusters over one-offs.

**Overall:** Weak discovery signals in forex H4 breakout (AUD/USD bias), but OOS collapse kills edge. Expand scope before tuning. Data not trustworthy: tiny samples (avg 34 trades), 3 symbols only, no Engine A/B/D separation.

```json
{
  "overall_verdict": "WEAK_CANDIDATE across forex H4 breakout; OOS failure and single-symbol bias prevent promotion. No strong clusters.",
  "top_candidates": [
    {"strategy": "session_opening_range atr_expand_min=0.0|range_bars=6", "symbol": "AUD/USD", "tf": "H4", "label": "WEAK_CANDIDATE"},
    {"strategy": "session_opening_range atr_expand_min=0.5|range_bars=6", "symbol": "AUD/USD", "tf": "H4", "label": "WEAK_CANDIDATE"},
    {"strategy": "prev_day_hl atr_expand_min=1.0", "symbol": "AUD/USD", "tf": "H4", "label": "WEAK_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "prev_day_hl", "reason": "avg_net -0.0249, OOS hurt, gross/net marginal"},
    {"strategy": "ny_breakout", "reason": "0 trades"},
    {"strategy": "london_breakout", "reason": "<20 trades"}
  ],
  "engine_a": {"keep": ["Current live floor 2.1"], "remove_or_demote": [], "tune": [], "next_tests": ["Test EMA/RSI+MACD on H4 forex"]},
  "engine_b": {"keep": ["session_opening_range logic"], "remove_or_demote": ["prev_day_hl"], "tune": ["ATR_expand_min >=0.5", "multi-session filters"], "next_tests": ["H1/M15 breakout proxies"]},
  "engine_d": {"keep": [], "remove_or_demote": [], "tune": [], "next_tests": ["Crypto symbols for VP/OrderFlow"]},
  "data_quality_warnings": ["Tiny samples (<30 trades common)", "Only 3 symbols, single TF/session/direction", "Consistent IS/OOS divergence"],
  "telemetry_warnings": ["Missing IS/OOS details for some NEEDS_MORE_DATA", "MT5 data only — no multi-source"],
  "next_tiny_test": {"symbols": ["EUR/USD", "GBP/USD", "USD/CAD"], "timeframes": ["H1", "M15"], "strategy_families": ["breakout", "pullback"], "reason": "Expand to 6+ symbols/TFs for clusters, check OOS robustness"},
  "do_not_do_next": ["prev_day_hl variants", "ny_breakout (zero trades)"]
}
```