# TF / Entry Path Audit Summary

Research diagnostic only. No production changes recommended unless results are
positive, stable, cost-adjusted, low ambiguity, and validated by Athena harness.

## Direct Answers

1. **Bad direction vs bad entry?** Engine B early-adverse rate avg 0.0%; Engine A 0.0%. High early-adverse suggests entry timing dominates over direction.
2. **Does H4 improve bias?** Insufficient H4 vs H1 data.
3. **Does H1/M15 improve execution after H4 bias?** H4→H1 combo not validated.
4. **Static TPs too far?** Engine B avg TP-beyond-MFE rate 0.0%.
5. **Wide SL vs poor entry?** High early-adverse + positive MFE-but-negative-R rates indicate SL being hit after poor entry path, not necessarily SL width alone.
6. **Engine A TF experiment?** H1→H1 avg R=-0.4036 vs H4→H4=-0.2369.
7. **Engine B TF experiment?** Best validated cell: none
8. **Symbols for deeper retuning:** See by-symbol CSV; focus symbols with WATCHLIST verdict and high early-adverse %.
9. **Symbols to block:** Symbols with FAIL verdict, negative avg R, and SQN < 1 across all cells.
10. **VectorBT provisional:** 0 rows in vectorbt CSV (if generated).
11. **Athena harness validated:** 2 cohorts.

## Parameter Intent Map (excerpt)

| Engine | Parameter | Intent |
|--------|-----------|--------|
| A | ENGINE_A_EMA_PERIODS_BY_CLASS.trend | calendar-duration based |
| A | ENGINE_A_EMA_PERIODS_BY_CLASS.momentum | calendar-duration based |
| A | ENGINE_A_EMA_PERIODS_BY_CLASS.long | bar-structure based |
| A | ENGINE_A_RSI_PERIOD_BY_CLASS | calendar-duration based |
| A | ENGINE_A_ATR_ADX_PERIODS_BY_CLASS.atr | volatility based |
| A | ENGINE_A_MACD_PARAMS_BY_CLASS | calendar-duration based |
| A | ENGINE_A_SCORE_GROUP_THRESHOLDS | scoring/threshold based |
| A | ENGINE_A_V3_BACKTEST.MAX_HOLD_BARS | execution based |
| B | ENGINE_B_SWEEP_LOOKBACK_BARS | bar-structure based |
| B | ENGINE_B_BOS_LOOKBACK_BARS | bar-structure based |
| B | NAKED_ENGINE.ob_lookback_bars | bar-structure based |
| B | NAKED_ENGINE.zone_proximity_atr_mult | volatility based |

## Validation Summary

| Engine | Symbol | Signal TF | Entry TF | Profile | Trades | Avg R | SQN | Verdict |
|--------|--------|-----------|----------|---------|--------|-------|-----|---------|
| A | EUR/USD | H1 | H1 | bar_native | 66 | -0.4036 | -3.7805 | FAIL |
| A | EUR/USD | H4 | H4 | bar_native | 19 | -0.2369 | -1.233 | FAIL |
