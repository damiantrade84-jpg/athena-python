# Athena Research Lab — AI Review
**Run ID:** `run_20260429_075458_22a2d8`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-29 07:55 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

### Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260429_075458_22a2d8 | **Analyst:** Quantitative Research | **Date:** 2026-04-29  
**Key Caveats:** All findings from discovery backtests at lowered BT_MIN thresholds (not live gates). Samples are tiny (<50 trades max, many <30) and limited to 2 index symbols (NAS100, US30). No multi-asset clusters; heavy IS/OOS degradation in many (e.g., pullback_ema OOS negative). Penalizing single-symbol, low-sample results per rules. **NO live execution or threshold copying recommended.** Robustness scores mediocre (avg 0.55). Data trustworthy but sparse (no telemetry bugs noted).

1. **Strategy family that worked best?** mean_reversion **WEAK_CANDIDATE** (highest avg net_return 0.0987, PF 3.26, SQN 1.92 across 4 configs; driven by bollinger_touch on indices H4/H1. Penalized for <30 trades in most, single symbols, no multi-TF cluster). trend_momentum **WEAK_CANDIDATE** (avg net 0.037, ema_alignment shows edge but OOS holds barely). pullback/breakout **REJECT** (low robustness, OOS fails).

2. **Indicator that helped most?** bollinger_touch **WEAK_CANDIDATE** (0.333 pass rate, avg net +0.027, SQN +0.44; 4 weak configs on indices H1/H4, survives fees/OOS decently at num_std=2.0-2.5 period=20. Tiny samples ~22-43 trades).

3. **Indicator that hurt most?** prev_day_hl **REJECT** (avg net -0.232, pass_rate 0, SQN -2.28; consistent loser across configs).

4. **Asset group that worked best?** index **WEAK_CANDIDATE** (only group tested, avg net +0.053, WR 43%, robustness 0.55; no forex/crypto comparison possible).

5. **Symbol that worked best?** US30 **WEAK_CANDIDATE** (higher WR 48%, PF 2.97 vs NAS100; but only 4 configs vs 10, still single-symbol penalty).

6. **Timeframe that worked best?** H4 **WEAK_CANDIDATE** (highest net 0.124, WR 69%, PF 3.91; 3 configs ~34 trades avg, best OOS survival).

7. **Session that worked best?** all **NEEDS_MORE_DATA** (only session tested; no session splits).

8. **LONG or SHORT better overall?** both **WEAK_CANDIDATE** (only direction tested, avg net +0.053; no long/short split).

9. **Setups that collapsed after fees?** None **REJECT** (no gross-profitable/net-negative cases; all valid configs fee-survive).

10. **Setups with too little sample size?** trend_momentum **REJECT** (171 configs <20-30 trades, e.g., ema_cross all <20). engine_b_proxy **REJECT** (40 configs). mean_reversion **NEEDS_MORE_DATA** (37 configs). breakout **NEEDS_MORE_DATA** (34 configs). pullback ok but still low (~30).

11. **Engine A (EMA/RSI+MACD/ADX) keep/remove/tune?**  
   - **Keep:** EMA alignment (3-EMA coherence D1/H4/H1?) with ADX_min>=20 **WEAK_CANDIDATE** (NAS100 M15/H1: PF~1.55-1.88, robustness 0.44-0.71; adds edge over no-ADX).  
   - **Remove/demote:** ema_cross, macd_direction **REJECT** (all hurt: net -0.11 to -0.13, pass_rate 0).  
   - **Tune:** Raise ADX gate directionally (20+ helps vs 0); test RSI_confirm=False (cross variants fail). NEEDS_MORE_DATA multi-symbol (>30 trades). No live threshold changes.

12. **Engine B (price-action BOS/CHoCH/FVG/OB etc.) keep/remove/tune?**  
   - **Keep:** None **REJECT** (engine_b_proxy 72 rejects; ob_bos/structure_filters hurt net -0.15--0.19).  
   - **Remove/demote:** ob_bos, structure_filters, london_breakout, prev_day_hl, ny_breakout **REJECT** (all pass_rate 0, SQN -2 to -4).  
   - **Tune:** N/A – checklist too loose in discovery; tighten BOS/CHoCH location+RR gates. NEEDS_MORE_DATA (tiny samples).

13. **Engine D (VP/POC/VAH/CVD/VWAP, crypto) keep/remove/tune?**  
   - **Keep:** vwap_deviation **NEEDS_MORE_DATA** (neutral, no rejects; 94 trades but no metrics).  
   - **Remove/demote:** None directly tested.  
   - **Tune:** N/A – no crypto data here (indices only); test AAA sequence/POC absorption on BTC/ETH. **Not trustworthy due to missing crypto telemetry/data.**

14. **Next smallest useful test to run?** Expand bollinger_touch (num_std=2.0-2.5, period=20) **WEAK_CANDIDATE** to 3-5 more index symbols (e.g., GER40, SPX500) on H4/H1 (10-20 configs, target >50 trades each) – builds on best cluster.

15. **What should NOT be tested further right now?** ema_cross, ob_bos, prev_day_hl, london_breakout/ny_breakout, structure_filters **REJECT** (consistent hurts, no OOS survival, pass_rate 0). Skip engine_b_proxy entirely (72 rejects).

**Overall:** Sparse edges in mean_reversion (bollinger_touch) on index H4 **WEAK_CANDIDATE** cluster; Engine A ema_alignment tunable but single-symbol. Prioritize multi-symbol expansion over one-offs. No strong live signals.

```json
{
  "overall_verdict": "Weak edges in mean_reversion bollinger_touch on index H4/H1; Engine A ema_alignment promising but needs multi-symbol data. Heavy small-sample/single-symbol penalties.",
  "top_candidates": [
    {"strategy": "bollinger_touch", "symbol": "US30", "tf": "H4", "label": "WEAK_CANDIDATE"},
    {"strategy": "ema_alignment", "symbol": "NAS100", "tf": "M15", "label": "WEAK_CANDIDATE"},
    {"strategy": "session_opening_range", "symbol": "NAS100", "tf": "H1", "label": "WEAK_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "ema_cross", "reason": "Consistent net loss (-0.11), pass_rate 0, tiny samples"},
    {"strategy": "ob_bos", "reason": "Hurts net (-0.147), SQN -3.07, all rejects"},
    {"strategy": "prev_day_hl", "reason": "Worst net (-0.232), pass_rate 0"},
    {"strategy": "engine_b_proxy", "reason": "72 rejects, no edge"}
  ],
  "engine_a": {
    "keep": ["ema_alignment (ADX_min>=20)"],
    "remove_or_demote": ["ema_cross", "macd_direction"],
    "tune": ["ADX gate upward (20+)", "RSI_confirm=False variants"],
    "next_tests": ["Multi-symbol H4/M15 ema_alignment (>50 trades)"]
  },
  "engine_b": {
    "keep": [],
    "remove_or_demote": ["ob_bos", "structure_filters", "london_breakout", "prev_day_hl"],
    "tune": [],
    "next_tests": []
  },
  "engine_d": {
    "keep": ["vwap_deviation (neutral)"],
    "remove_or_demote": [],
    "tune": [],
    "next_tests": ["Crypto symbols (BTC/ETH) VP/POC/CVD on H1/H4"]
  },
  "data_quality_warnings": ["Tiny samples dominate (282 <20 trades); limited to 2 index symbols"],
  "telemetry_warnings": ["Missing crypto data for Engine D; no session/direction splits"],
  "next_tiny_test": {
    "symbols": ["US30", "NAS100", "GER40", "SPX500"],
    "timeframes": ["H4", "H1"],
    "strategy_families": ["mean_reversion"],
    "reason": "Expand bollinger_touch cluster for >50 trades confirmation"
  },
  "do_not_do_next": [
    "ema_cross any variants",
    "ob_bos/structure_filters",
    "engine_b_proxy",
    "prev_day_hl/london_breakout"
  ]
}
```