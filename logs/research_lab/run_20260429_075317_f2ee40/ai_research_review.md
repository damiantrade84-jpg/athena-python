# Athena Research Lab — AI Review
**Run ID:** `run_20260429_075317_f2ee40`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-29 07:54 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Analyst:** Quantitative Research Analyst  
**Run ID:** run_20260429_075317_f2ee40  
**Date:** 2026-04-29  
**Scope:** 456 configs across 2 commodity symbols (XAG/USD, XAU/USD), multiple families, H1/H4/M15 TFs. All 'both' direction, 'all' session. Robustness-filtered to 27 valid (5.9%). **No STRONG_CANDIDATEs.** All top results are single-symbol (penalized). OOS often negative (penalized). Samples marginal (30-50 trades, penalized). Gross profitable but many net-weak after fees. **Do NOT execute live or copy BT_MIN thresholds to live gates (e.g., Engine A forex floor 2.1).**

## 1. Strategy Family Performance
- **engine_b_proxy**: WEAK_CANDIDATE (best avg net_return 0.4151, 4/68 passed; structure_filters like FVG+strong_close show edge on H4 XAG/USD, PF>1.9, but single-symbol/OOS-weak).
- **pullback**: WEAK_CANDIDATE (avg net 0.3651, 7/41 passed; pullback_ema on H4 XAG/USD, PF~2.4, but low WR~20-30%, single-symbol).
- **breakout**: REJECT (avg net 0.2318, 6/95 passed; session_opening_range marginal, high DD, OOS collapse).
- **trend_momentum**: REJECT (avg net 0.2219, 9/237 passed; ema_cross/macd_direction hurt globally, tiny samples dominate).
- **mean_reversion**: NEEDS_MORE_DATA (1/47 passed, M15 only, tiny sample).

**Prefer clusters:** No multi-symbol clusters; all single-symbol (XAG/USD bias).

## 2. Indicator Helped Most
**structure_filters (FVG + strong_close_pct=0.7-0.8)**: WEAK_CANDIDATE (top on H4 XAG/USD/XAU, PF>1.9-3.7, WR~50%; Engine B proxy). pullback_ema (RSI reclaim/threshold=50, trend=200) close second (PF~2.4).

## 3. Indicator Hurt Most
**prev_day_hl**: REJECT (avg net -0.1706, 0/18 passed, high DD). ema_cross (Engine A core): REJECT (avg net -0.0712, 5/216 weak passes, globally hurts PF<0.5 on M15).

## 4. Asset Group
**commodity**: WEAK_CANDIDATE (only group, avg net 0.2825, but single-class bias; no forex/crypto comparison).

## 5. Symbol
**XAG/USD**: WEAK_CANDIDATE (18/27 valid, avg net 0.3604 > XAU/USD's 0.1268; H4 edge). **XAU/USD**: REJECT (9 valid, weaker OOS/net).

**Penalize:** Single-symbol dependence.

## 6. Timeframe
**H4**: STRONGEST_SIGNAL_HERE (23/27 valid, avg net 0.3296, WR 32%; all tops). **H1/M15**: REJECT (low WR/net, tiny samples).

## 7. Session
**all**: NEEDS_MORE_DATA (only tested; no session splits).

## 8. LONG vs SHORT
**both**: NEEDS_MORE_DATA (no directional split; all 'both').

## 9. Setups Collapsed After Fees
None explicitly gross-profitable/net-negative (all valid survived fees per robustness). But many marginal (e.g., trend_momentum ema_cross gross ~0 but net -0.05 to -0.13). **No fee-killers flagged.**

## 10. Too Little Sample (<30 trades)
- **trend_momentum**: 174/237 (74%; ema_cross dominates tiny samples).
- **engine_b_proxy**: 44/68 (65%).
- **mean_reversion**: 39/47 (83%).
- **breakout**: 35/95 (37%; session_opening_range often 20-27).
**Total: 292/456 (64%).** Penalize all NEEDS_MORE_DATA.

## 11. Engine A (3-factor: EMA coherence, RSI+MACD, ADX)
**Keep:** ADX gate (min=0-25 helps weak ema_cross/macd_direction on H4 XAG).  
**Remove/Demote:** ema_cross (10/20/50 fast/slow hurts M15, net -0.1; low WR<25%). macd_direction marginal (H4 only).  
**Tune:** Raise ADX_min >20 (filters noise); test RSI_confirm=False (weak edge); extend slow_period>100 (fewer but better trades). **No threshold changes—backtest-only.** Best: macd_direction adx_min=0 H4 XAG (WEAK_CANDIDATE). **Not trustworthy: M15 data sparse.**

## 12. Engine B (Price-action: BOS/CHoCH, FVG, OB, etc.)
**Keep:** FVG detection + strong_close_pct=0.7-0.8 (WEAK_CANDIDATE proxy, WR~50%, H4 commodities).  
**Remove/Demote:** ob_bos (0% pass, net -0.04).  
**Tune:** Location/room RR gates stricter (high DD in tops); add swing sequence filter. **structure_filters** shows checklist potential but single-symbol. No BOS/CHoCH direct wins.

## 13. Engine D (VP/OrderFlow: POC/VAH/VAL, CVD, etc.)
**NEEDS_MORE_DATA / TELEMETRY_BUG**: No crypto symbols/timeframes tested (D crypto-focused). No vwap_deviation/rsi_extreme edge (neutral/tiny). **Missing data—untrustworthy.**

## 14. Next Smallest Useful Test
Add 2-3 forex majors (EUR/USD, GBP/USD) + 1 crypto (BTC/USD) on H4 only, engine_b_proxy + pullback families (FVG/pullback_ema params from tops). 20-30 configs. Reason: Test multi-symbol clusters, forex relevance for Engine A.

## 15. What Should NOT Be Tested Further Right Now
- M15 (tiny samples, hurts).  
- Single-symbol (XAG/USD bias).  
- prev_day_hl, london/ny_breakout, ob_bos (consistent hurts).  
- trend_momentum ema_cross <50-period (net negative).  
- mean_reversion (no edge).

**Overall:** Weak commodity H4 signals (engine_b_proxy/pullback). No robust clusters/OOS. Prioritize multi-asset validation. **No live actions.**

```json
{
  "overall_verdict": "Weak signals on H4 commodities (XAG/USD bias). engine_b_proxy + pullback show promise but single-symbol/OOS penalized. No strong edges; expand symbols.",
  "top_candidates": [
    {"strategy": "structure_filters (fvg_detection=True|strong_close_pct=0.7)", "symbol": "XAG/USD", "tf": "H4", "label": "WEAK_CANDIDATE"},
    {"strategy": "pullback_ema (pullback_period=20|rsi_reclaim=True|rsi_threshold=50|trend_period=200)", "symbol": "XAG/USD", "tf": "H4", "label": "WEAK_CANDIDATE"},
    {"strategy": "structure_filters (fvg_detection=True|strong_close_pct=0.7)", "symbol": "XAU/USD", "tf": "H4", "label": "WEAK_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "ema_cross", "reason": "Globally hurts (low WR/PF, net negative)"},
    {"strategy": "prev_day_hl", "reason": "Consistent high DD/net loss"},
    {"strategy": "ob_bos", "reason": "0% pass rate"},
    {"strategy": "breakout (non-session_opening)", "reason": "OOS collapse"}
  ],
  "engine_a": {
    "keep": ["ADX gate (min=0-20)"],
    "remove_or_demote": ["ema_cross (short periods)"],
    "tune": ["RSI_confirm=False", "slow_period>100", "ADX_min>20"],
    "next_tests": ["H4 forex majors + macd_direction"]
  },
  "engine_b": {
    "keep": ["FVG + strong_close_pct=0.7-0.8"],
    "remove_or_demote": ["ob_bos"],
    "tune": ["Stricter RR/location gates"],
    "next_tests": ["Multi-symbol structure_filters"]
  },
  "engine_d": {
    "keep": [],
    "remove_or_demote": [],
    "tune": [],
    "next_tests": ["Crypto symbols (BTC/USD H4 VP params)"]
  },
  "data_quality_warnings": [
    "292/456 tiny samples (<30 trades)",
    "Only 2 symbols (XAG/USD bias)",
    "No directional/session splits",
    "Commodity-only (no forex/crypto)"
  ],
  "telemetry_warnings": [
    "No Engine D crypto data",
    "Sparse M15/H1",
    "OOS often negative (IS/OOS fail)"
  ],
  "next_tiny_test": {
    "symbols": ["EUR/USD", "GBP/USD", "BTC/USD"],
    "timeframes": ["H4"],
    "strategy_families": ["engine_b_proxy", "pullback"],
    "reason": "Multi-asset cluster test for top weak candidates (20-30 configs)"
  },
  "do_not_do_next": [
    "M15 tests",
    "Single-symbol (XAG/USD only)",
    "prev_day_hl/london_breakout/ob_bos",
    "trend_momentum ema_cross <50-period"
  ]
}
```