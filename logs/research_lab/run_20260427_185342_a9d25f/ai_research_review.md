# Athena Research Lab — AI Review
**Run ID:** `run_20260427_185342_a9d25f`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-27 18:54 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260427_185342_a9d25f | **Analyst:** Quantitative Research | **Date:** 2026-04-27  
**Key Caveat:** These are discovery backtests at lowered thresholds (BT_MIN). **Do NOT execute live, copy thresholds to live gates, or deploy without multi-symbol OOS validation.** All findings penalized for small samples (<30 trades common), forex-only, frequent OOS degradation, and lack of clusters. No telemetry bugs detected, but missing Engine B/D data makes results incomplete.

## 1. Strategy Family Assessment
- **trend_momentum**: WEAK_CANDIDATE (24/615 pass robustness; avg net_return +0.053 gross-profitable but OOS-weak at -0.021 avg; works D1 majors but no robust cluster; penalize single-family dominance and tiny samples).

## 2. Indicator Helped Most
- **ema_cross** (adx_min=20|fast=10|rsi=False|slow=50 on EUR/USD D1): WEAK_CANDIDATE (PF 1.90, net +0.132 but only 23 trades; highest PF in top ranks but avg across 540 configs hurts at -0.067 net).

## 3. Indicator Hurt Most
- **ema_cross** overall: REJECT (540 configs, pass_rate 2.6%, avg_net -0.067, avg_SQN -1.81; dominates rejections despite isolated weak winners).

## 4. Asset Group
- **forex**: WEAK_CANDIDATE (only group tested, avg net +0.053 but OOS -0.021; no cross-asset comparison possible — NEEDS_MORE_DATA for crypto/equities).

## 5. Symbol
- **GBP/USD**: WEAK_CANDIDATE (highest avg net +0.081 across 6 configs, WR 41%; multi-TF but OOS -0.035).
- Others (EUR/USD, USD/JPY): WEAK_CANDIDATE but penalize GBP/USD symbol concentration risk.

## 6. Timeframe
- **D1**: WEAK_CANDIDATE (15 configs, highest net +0.075, WR 38%; best IS/OOS but small samples ~33 avg trades).
- H4/H1: REJECT (low net, H1 tiny samples).

## 7. Session
- **all**: NEEDS_MORE_DATA (only session tested; no session filter edge visible).

## 8. LONG vs SHORT
- **both**: WEAK_CANDIDATE (only direction tested; no long/short split — NEEDS_MORE_DATA; no bias detected).

## 9. Setups Collapsed After Fees
- None: No gross-profitable but net-negative cases (all weak candidates survive fees marginally).

## 10. Setups with Too Little Sample
- **trend_momentum**: NEEDS_MORE_DATA (409 configs <20-30 trades; e.g., most H1/ADX=25 variants 16-17 trades — untrustworthy).

## 11. Engine A (EMA/RSI/MACD/ADX)
- **Keep:** Core 3-factor (D1 ema_cross/macd_direction on majors show weak edge).
- **Remove/Demote:** High-ADX gates (25+): REJECT (tiny samples).
- **Tune:** Lower fast EMA (10) + slow=50 on D1; test RSI_confirm=True vs False (neutral); ADX_min=20 slight edge over 0 but penalize OOS drop. **No live threshold changes.**
- Not trustworthy: Single-family, forex-only.

## 12. Engine B (Price-Action Checklist)
- **Keep/Remove/Tune:** NEEDS_MORE_DATA (no engine_b_proxy results; all rejected/insufficient).
- Explicit: Missing data — untrustworthy for BOS/FVG/OB decisions.

## 13. Engine D (VP/OrderFlow, Crypto-Focused)
- **Keep/Remove/Tune:** NEEDS_MORE_DATA (no engine_d_proxy results; all rejected/insufficient).
- Explicit: Missing data — untrustworthy for POC/VAH/CVD.

## 14. Next Smallest Useful Test
- Tiny mode: Add 5+ symbols (e.g., USD/CHF, NZD/USD), H4/D1 only, trend_momentum + pullback families. Reason: Build clusters beyond GBP/EUR D1; validate OOS.

## 15. What Should NOT Be Tested Further Right Now
- **trend_momentum H1**: REJECT (low net +0.006, tiny samples, poor robustness).
- ema_cross slow_period=100/200: REJECT (consistent losses).
- Single-symbol deep dives (e.g., GBP/USD-only).

**Overall:** Weak signals only; no strong edges. Prioritize multi-symbol OOS before Engine changes. Robustness avg 0.47 — data trustworthy but sparse.

```json
{
  "overall_verdict": "WEAK_CANDIDATE: trend_momentum shows marginal D1 forex edge (ema_cross/macd) but penalize small samples, OOS decay, no clusters/B/D data.",
  "top_candidates": [
    {"strategy": "ema_cross (adx_min=20|fast=10|rsi=False|slow=50)", "symbol": "EUR/USD", "tf": "D1", "label": "WEAK_CANDIDATE"},
    {"strategy": "macd_direction (adx_min=20|fast=12|signal=9|slow=26)", "symbol": "GBP/USD", "tf": "D1", "label": "WEAK_CANDIDATE"},
    {"strategy": "ema_cross (adx_min=0|fast=10|rsi=False|slow=50)", "symbol": "GBP/USD", "tf": "D1", "label": "WEAK_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "ema_cross (slow=100/200 variants)", "reason": "Consistent net losses, poor SQN"},
    {"strategy": "trend_momentum H1", "reason": "Tiny samples, low net return"},
    {"strategy": "ema_alignment", "reason": "Low pass_rate 6.7%, hurts net"}
  ],
  "engine_a": {
    "keep": ["D1 ema_cross/macd_direction cores (fast=10/slow=50)"],
    "remove_or_demote": ["ADX_min=25+", "slow_period=100/200"],
    "tune": ["RSI_confirm impact", "ADX 0 vs 20 on majors"],
    "next_tests": ["Add pullback family, 5+ symbols"]
  },
  "engine_b": {
    "keep": [],
    "remove_or_demote": [],
    "tune": [],
    "next_tests": ["Run engine_b_proxy on D1/H4 forex"]
  },
  "engine_d": {
    "keep": [],
    "remove_or_demote": [],
    "tune": [],
    "next_tests": ["Run engine_d_proxy on crypto D1/H4"]
  },
  "data_quality_warnings": ["409 tiny samples (<30 trades)", "OOS often negative despite IS"],
  "telemetry_warnings": [],
  "next_tiny_test": {
    "symbols": ["USD/CHF", "NZD/USD", "AUD/JPY"],
    "timeframes": ["D1", "H4"],
    "strategy_families": ["trend_momentum", "pullback"],
    "reason": "Expand symbols/TF for clusters; include pullback for Engine A breadth"
  },
  "do_not_do_next": [
    "H1-only trend_momentum",
    "Single-symbol deep param sweeps"
  ]
}
```