# Athena Research Lab — AI Review
**Run ID:** `run_20260427_163414`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-27 16:35 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260427_163414 | **Analyst:** Quantitative Research | **Date:** 2026-04-27  
**Scope:** 72 configs on engine_b_proxy (ob_bos + structure_filters) across crypto/commodity, H4 TF, 'all' session, 'both' direction.  
**Key Caveats:** Tiny mode (low BT_MIN thresholds). All samples small (<165 trades max). No Engine A/D data. Results from binance_rest (crypto) + mt5 (XAU). **Do NOT execute live or copy thresholds to live gates.** Penalized: tiny samples (<30 trades), single-symbol, OOS failure, no multi-symbol clusters.

## 1. Strategy Family Performance
- **engine_b_proxy** (structure_filters): WEAK_CANDIDATE (3 valid configs, avg net_return 0.282, but single-symbol XAU/ETH, small samples 24-165, mixed OOS).
- **engine_b_proxy** (ob_bos): REJECT (48 configs, avg net -0.0467, tiny samples <20, hurts edge).

## 2. Indicator Helped Most
structure_filters (fvg_detection=True|strong_close_pct=0.7 on XAU/USD H4): WEAK_CANDIDATE (32 trades, WR 53.1%, PF 4.09, net 0.4594, SQN 2.28). Borderline sample; OOS positive but tiny.

## 3. Indicator Hurt Most
ob_bos (all params): REJECT (avg WR 27.7%, PF <1, net -0.0467, SQN -1.21, 594 trades but fragmented <20 per config).

## 4. Asset Group Worked Best
- **commodity** (XAU/USD): WEAK_CANDIDATE (avg WR 51.6%, net 0.395, PF 3.84, 28 trades avg).
- **crypto**: WEAK_CANDIDATE (ETH/BTC, avg net 0.055 but OOS -0.10 on ETH; ob_bos NEEDS_MORE_DATA).

## 5. Symbol Worked Best
- **XAU/USD**: WEAK_CANDIDATE (2 configs, avg net 0.395, WR 51.6%, robustness 0.44).
- **ETH/USDT**: WEAK_CANDIDATE (1 config, net 0.055 but OOS -0.10 → penalize).
- **BTC/USDT**: NEEDS_MORE_DATA/REJECT (all <20 trades).

## 6. Timeframe Worked Best
- **H4**: WEAK_CANDIDATE (only TF tested, avg net 0.282, but no cluster).

## 7. Session Worked Best
- **all**: WEAK_CANDIDATE (only session tested).

## 8. LONG vs SHORT
- **both**: WEAK_CANDIDATE (only direction tested; no LONG/SHORT split).

## 9. Setups Collapsed After Fees
None identified (all valid configs gross-profitable + net-positive, e.g., XAU structure_filters gross/net both >0.3). ob_bos gross/net nan or negative → already REJECT.

## 10. Setups with Too Little Sample
- engine_b_proxy/ob_bos: 46 configs (<20 trades, e.g., BTC/ETH swing_period=5/10).
- engine_b_proxy/structure_filters: Some <30 (e.g., BTC fvg=True|strong_close=0.8: 19 trades → NEEDS_MORE_DATA).

## 11. Engine A Recommendations
NEEDS_MORE_DATA (no results). Not trustworthy due to missing data. Keep current live floor (2.1). Do not tune/remove based on this.

## 12. Engine B Recommendations
- **Keep:** structure_filters logic (strong_close_pct=0.7-0.8 shows weak edge on XAU H4).
- **Remove/Demote:** ob_bos (all REJECT; no edge, hurts PF/WR).
- **Tune:** Test structure_filters fvg_detection=True + strong_close_pct=0.7-0.8 on more symbols (expand beyond XAU); add multi-TF check. Checklist gates too loose in discovery.

## 13. Engine D Recommendations
NEEDS_MORE_DATA (no results). Not trustworthy due to missing data. No changes.

## 14. Next Smallest Useful Test
Expand structure_filters (fvg=True|strong_close=0.7) to 4+ symbols (add EUR/USD, GBP/USD forex + more crypto), H1/H4 TFs, 'london/ny' sessions. Target >50 trades/config.

## 15. What Should NOT Be Tested Further Right Now
ob_bos (all REJECT; consistently tiny/negative across BTC/ETH/XAU). engine_b_proxy on BTC H4 (<20 trades, no edge).

**Overall:** Weak signals on structure_filters XAU H4 only. No robust clusters. Prioritize multi-symbol validation before Engine tweaks. **No live execution.**

```json
{
  "overall_verdict": "WEAK_CANDIDATE on engine_b_proxy/structure_filters (XAU H4 only); ob_bos REJECT; no Engine A/D data.",
  "top_candidates": [
    {"strategy": "structure_filters fvg_detection=True|strong_close_pct=0.7", "symbol": "XAU/USD", "tf": "H4", "label": "WEAK_CANDIDATE"},
    {"strategy": "structure_filters fvg_detection=True|strong_close_pct=0.8", "symbol": "XAU/USD", "tf": "H4", "label": "WEAK_CANDIDATE"},
    {"strategy": "structure_filters fvg_detection=False|strong_close_pct=0.8", "symbol": "ETH/USDT", "tf": "H4", "label": "WEAK_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "ob_bos (all params)", "reason": "tiny samples, negative net/PF, hurts edge"},
    {"strategy": "structure_filters (BTC configs)", "reason": "REJECT/NEEDS_MORE_DATA, negative returns"}
  ],
  "engine_a": {"keep": ["current live floor 2.1"], "remove_or_demote": [], "tune": [], "next_tests": ["add trend_momentum/pullback families"]},
  "engine_b": {"keep": ["structure_filters logic"], "remove_or_demote": ["ob_bos"], "tune": ["strong_close_pct=0.7-0.8, fvg_detection=True"], "next_tests": ["multi-symbol H1/H4"]},
  "engine_d": {"keep": [], "remove_or_demote": [], "tune": [], "next_tests": ["add engine_d_proxy on crypto"]},
  "data_quality_warnings": ["Small samples dominate (<30 trades in 50+ configs)", "Single-symbol bias (XAU/ETH)"],
  "telemetry_warnings": ["Mixed sources (binance_rest/mt5); no OOS consistency in crypto"],
  "next_tiny_test": {"symbols": ["XAU/USD", "EUR/USD", "GBP/USD", "ETH/USDT"], "timeframes": ["H1", "H4"], "strategy_families": ["engine_b_proxy"], "reason": "Validate structure_filters edge across forex/crypto"},
  "do_not_do_next": ["ob_bos any params", "BTC/USDT H4 only"]
}
```