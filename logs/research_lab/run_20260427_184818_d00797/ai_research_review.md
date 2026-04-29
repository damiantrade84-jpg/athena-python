# Athena Research Lab — AI Review
**Run ID:** `run_20260427_184818_d00797`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-27 18:48 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260427_184818_d00797 | **Analyst Review Date:** Current | **Scope:** Breakout family (H4 forex only)  
**Key Caveats:** Tiny mode (44 configs, 12 valid). All samples <70 trades — penalize heavily (<30 flagged). No clusters across symbols/TFs/sessions. OOS decay common (avg oos_return -0.0179). BT_MIN lower than live — do NOT copy thresholds. No live execution recommended.

## 1. Strategy Family
- **breakout**: WEAK_CANDIDATE (avg net_return +0.0341, PF 1.34 across 12 valid; top configs PF>1.7 but OOS negative, small samples 27-56 trades, GBP/USD fails entirely).

## 2. Indicator Helped Most
- **prev_day_hl**: WEAK_CANDIDATE (50% pass_rate, avg net +0.0001, works marginally on AUD/USD/USDJPY; Sharpe/SQN low ~0.3).
- Others neutral/weak.

## 3. Indicator Hurt Most
- **london_breakout**: REJECT (0% pass, avg net -0.0185, low WR 0.31, negative expectancy).
- **session_opening_range**: REJECT (37.5% pass, avg net -0.0060 despite high PF in AUD/USD; OOS collapse).

## 4. Asset Group
- **forex**: WEAK_CANDIDATE (only group tested; avg net +0.0341 but single-asset bias, OOS decay).

## 5. Symbol
- **AUD/USD**: WEAK_CANDIDATE (best avg net +0.0543, WR 0.41, 7 configs; penalize single-symbol).
- **EUR/USD**: NEEDS_MORE_DATA (50 trades avg, breakeven).
- **USD/JPY**: WEAK_CANDIDATE (marginal +0.0088 net).
- **GBP/USD**: REJECT (all negative net/expectancy).

## 6. Timeframe
- **H4**: WEAK_CANDIDATE (only TF; avg net +0.0341 but no cross-TF robustness).

## 7. Session
- **all**: NEEDS_MORE_DATA (only session; no session splits show edge).

## 8. LONG or SHORT
- **both**: NEEDS_MORE_DATA (only direction tested; no long/short split).

## 9. Setups Collapsed After Fees
- None (all valid configs gross-profitable survive fees; net_return tracks gross closely).

## 10. Setups Too Little Sample
- **breakout** (14 configs): REJECT (e.g., london_breakout 13-26 trades, ny_breakout 0 trades — <30 threshold penalty).

## 11. Engine A (EMA/RSI/MACD/ADX)
- No valid results (breakout is PA, not quant scoring).  
- **Keep:** All current (MIN_CONFLUENCE_CLASS.forex=2.1 unchanged).  
- **Remove/Demote:** None.  
- **Tune:** None from this run — NEEDS_MORE_DATA (test trend_momentum/pullback families).  
- Not trustworthy: missing data.

## 12. Engine B (Naked PA: BOS/FVG/OB etc.)
- Breakout proxies tested (prev_day_hl/session ranges).  
- **Keep:** prev_day_hl logic as weak filter (atr_expand_min=0.5-1.0 survives fees on AUD/USD).  
- **Remove/Demote:** london_breakout/ny_breakout (zero edge/samples). Demote session_opening_range (OOS fails).  
- **Tune:** Tighten atr_expand_min >=0.5 (improves PF marginally); add symbol gate (block GBP/USD); test range_bars=6 only.  
- Penalize: Single-symbol (AUD/USD), OOS decay.

## 13. Engine D (VP/OrderFlow: POC/VAH etc.)
- No valid results (forex/crypto mismatch).  
- **Keep/Remove/Tune:** None — NEEDS_MORE_DATA.  
- Not trustworthy: missing data (crypto-focused).

## 14. Next Smallest Useful Test
- Tiny mode: Add 4 symbols (NZD/USD, USD/CAD, GBP/JPY, XAU/USD), H1/M15 TFs, breakout + pullback families. Reason: Test cross-symbol clusters, lower TFs for scalping proxy.

## 15. What Should NOT Be Tested Further Right Now?
- **breakout on GBP/USD**: REJECT (all 10 configs negative net/expectancy).  
- **london_breakout/ny_breakout**: REJECT (low samples, consistent losses).  
- Single-symbol deep dives (e.g., AUD/USD only).

**Overall:** Weak forex H4 breakout edge (AUD/USD session_opening_range prev_day_hl) but no robust clusters/OOS. Prioritize multi-symbol expansion. No live changes.

```json
{
  "overall_verdict": "WEAK_CANDIDATE: Marginal breakout edge on AUD/USD H4 (PF>1.7 top configs) but heavy penalties (small samples<56, OOS decay, GBP/USD fails, no multi-symbol/TF clusters). No STRONG_CANDIDATE.",
  "top_candidates": [
    {"strategy": "session_opening_range (atr_expand_min=0.0|0.5, range_bars=6)", "symbol": "AUD/USD", "tf": "H4", "label": "WEAK_CANDIDATE"},
    {"strategy": "prev_day_hl (atr_expand_min=0.0|0.5)", "symbol": "AUD/USD", "tf": "H4", "label": "WEAK_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "london_breakout", "reason": "Negative net_return/WR, low samples"},
    {"strategy": "session_opening_range (range_bars=12)", "reason": "OOS collapse"},
    {"strategy": "All GBP/USD breakout", "reason": "Gross/net negative"}
  ],
  "engine_a": {"keep": [], "remove_or_demote": [], "tune": [], "next_tests": ["Add trend_momentum/pullback families on H4"]},
  "engine_b": {"keep": ["prev_day_hl (atr_expand_min>=0.5)"], "remove_or_demote": ["london_breakout", "ny_breakout"], "tune": ["Symbol gate: block GBP/USD; range_bars=6 priority"], "next_tests": ["Test on H1/M15, more majors"]},
  "engine_d": {"keep": [], "remove_or_demote": [], "tune": [], "next_tests": ["Crypto symbols (BTC/ETH) for VP proxies"]},
  "data_quality_warnings": ["Tiny samples (max 56 trades, 14<30)", "OOS decay (avg -0.0179)", "No fee-killers but low expectancy"],
  "telemetry_warnings": ["Missing Engine A/B/D proxies", "MT5 data only — no cross-source", "No long/short split"],
  "next_tiny_test": {"symbols": ["NZD/USD", "USD/CAD", "GBP/JPY", "XAU/USD"], "timeframes": ["H1", "M15"], "strategy_families": ["breakout", "pullback"], "reason": "Build cross-symbol/TF clusters; avoid single-symbol bias"},
  "do_not_do_next": ["breakout GBP/USD", "london/ny_breakout deep tune"]
}
```