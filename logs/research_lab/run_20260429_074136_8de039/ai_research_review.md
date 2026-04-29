# Athena Research Lab — AI Review
**Run ID:** `run_20260429_074136_8de039`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-29 07:42 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260429_074136_8de039 | **Analyst:** Quantitative Research | **Date:** 2026-04-29  
**Scope:** 48 configs (mean_reversion family only, crypto symbols, H1/M15 TFs). All discovery thresholds < live. **No live execution or threshold changes recommended.**

## 1. Strategy Family Performance
- **mean_reversion**: WEAK_CANDIDATE (3/48 valid configs pass robustness; avg net_return +0.029 across crypto M15; but <30 trades/symbol, OOS degradation in 2/3 top configs, single TF/symbol clusters).

## 2. Top Helping Indicator
- **bollinger_touch** (num_std=2.5|period=20): WEAK_CANDIDATE (3/12 configs valid; highest pass_rate 0.25; avg SQN 1.16 in tops; net positive but tiny samples ~25 trades; helps M15 crypto).

## 3. Worst Hurting Indicator
- No clear worst (all others NEEDS_MORE_DATA due to <20 trades). **rsi_extreme**: REJECT (0/24 valid; tiny samples 5-16 trades; no edge signal). **vwap_deviation**: REJECT (0/12 valid; tiny samples 7-11 trades).

## 4. Best Asset Group
- **crypto**: WEAK_CANDIDATE (only group tested; avg net_return +0.029, WR 72%; but single-group, no forex/equity comparison; penalize for lack of cross-asset robustness).

## 5. Best Symbol
- **XRP/USDT**: WEAK_CANDIDATE (top net_return +0.0389, WR 73%, PF 1.98; 26 trades M15 bollinger_touch; but single-symbol, OOS -1.02% vs IS +4.96%; <30 trades). Others (BTC/SOL) similar but weaker.

## 6. Best Timeframe
- **M15**: WEAK_CANDIDATE (all 3 valid configs; avg net_return +0.029, sharpe 2.88; H1 all REJECT/NEEDS_MORE_DATA due to tiny samples or losses e.g., BTC H1 bollinger sharpe -2.35).

## 7. Best Session
- **all**: NEEDS_MORE_DATA (only session tested; no session splits; cannot assess).

## 8. LONG vs SHORT Performance
- **both**: WEAK_CANDIDATE (only direction tested; avg WR 72%; no long/short split; cannot assess bias).

## 9. Setups Collapsing After Fees
- None (all 3 valid are gross/net positive: e.g., XRP M15 bollinger net +3.89% vs gross +3.89%; no gross-profitable/net-negative cases).

## 10. Setups with Insufficient Sample
- **rsi_extreme** (all 24 configs: 5-16 trades; NEEDS_MORE_DATA).
- **vwap_deviation** (all 12 configs: 7-11 trades; NEEDS_MORE_DATA).
- H1 bollinger_touch variants (e.g., BTC H1 num_std=2.0: 34 trades but REJECT on losses/OOS fail).

## 11. Engine A Recommendations
- No mean_reversion proxies map to Engine A (trend_momentum/pullback). **NEEDS_MORE_DATA** (zero valid results). Keep current live floor (2.1 forex). Do not tune from this.

## 12. Engine B Recommendations
- No price-action/engine_b_proxy results. **NEEDS_MORE_DATA**. Current strict checklist unchanged.

## 13. Engine D Recommendations
- **vwap_deviation** loosely Engine D-related (VWAP): REJECT (tiny samples, no edge). No VP/POC/CVD results. **NEEDS_MORE_DATA**. No changes to grades/crypto focus.

## 14. Next Smallest Useful Test
- Expand to 10+ crypto symbols (add ETH, ADA, LINK), test M15/H1/M30, include trend_momentum family for Engine A proxy. Reason: Build clusters beyond 3 single-symbol weak signals.

## 15. Do NOT Test Further Right Now
- **rsi_extreme** / **vwap_deviation** (all tiny samples, zero valid). H1 mean_reversion (all REJECT/NEEDS_MORE_DATA, worse metrics vs M15).

**Overall:** Weak M15 crypto mean_reversion edge via bollinger_touch (num_std=2.5); penalize tiny samples/single-TF/symbol. **Not trustworthy** due to <30 trades, OOS decay. Run broader discovery before tuning.

```json
{
  "overall_verdict": "WEAK_CANDIDATE cluster in mean_reversion bollinger_touch (num_std=2.5|period=20) on M15 crypto (BTC/SOL/XRP); penalize tiny samples (<30), single TF/symbol, OOS decay. No engine-specific edges.",
  "top_candidates": [
    {"strategy": "bollinger_touch num_std=2.5|period=20", "symbol": "XRP/USDT", "tf": "M15", "label": "WEAK_CANDIDATE"},
    {"strategy": "bollinger_touch num_std=2.5|period=20", "symbol": "BTC/USDT", "tf": "M15", "label": "WEAK_CANDIDATE"},
    {"strategy": "bollinger_touch num_std=2.5|period=20", "symbol": "SOL/USDT", "tf": "M15", "label": "WEAK_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "bollinger_touch (H1 variants)", "reason": "Losses, poor sharpe/OOS (e.g., BTC H1 sharpe -2.35)"},
    {"strategy": "rsi_extreme (all)", "reason": "Tiny samples <20 trades"},
    {"strategy": "vwap_deviation (all)", "reason": "Tiny samples <20 trades"}
  ],
  "engine_a": {"keep": [], "remove_or_demote": [], "tune": [], "next_tests": ["Add trend_momentum/pullback proxies on M15 crypto"]},
  "engine_b": {"keep": [], "remove_or_demote": [], "tune": [], "next_tests": []},
  "engine_d": {"keep": [], "remove_or_demote": ["vwap_deviation"], "tune": [], "next_tests": ["Test POC/VAH/VAL on M15 crypto"]},
  "data_quality_warnings": ["Tiny samples dominate (36/48 <20 trades); penalize all <30"],
  "telemetry_warnings": ["Missing IS/OOS for NEEDS_MORE_DATA configs; binance_rest only"],
  "next_tiny_test": {"symbols": ["ETH/USDT", "ADA/USDT", "LINK/USDT"], "timeframes": ["M15", "M30"], "strategy_families": ["mean_reversion"], "reason": "Expand symbol/TF cluster for bollinger_touch robustness"},
  "do_not_do_next": ["rsi_extreme", "vwap_deviation", "H1 mean_reversion"]
}
```