# H4 Indicator Tuning Experiment Verdict

Generated: 2026-07-08T12:10:42.183560+00:00

Single-variable test: H4 entry TF held constant; only indicator periods differ between variants.

| Pair | Score group | H1-on-H4 SL% | H4-tuned SL% | SL delta | H1-on-H4 avg R | H4-tuned avg R | R delta | Trades (base/tuned) | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| AAPL | us_stock_single | 46.03 | 44.44 | -1.6 | -0.041 | -0.019 | +0.022 | 63/63 | mixed / inconclusive on this pair sample |
| BTC/USDT | crypto_btc | 42.54 | 45.31 | +2.8 | -0.086 | -0.102 | -0.016 | 134/128 | mixed / inconclusive on this pair sample |
| EUR/GBP | forex_crosses | 47.01 | 43.08 | -3.9 | -0.303 | -0.249 | +0.054 | 134/130 | H4 tuning improved SL rate without hurting R |
| EUR/USD | forex_majors | 42.64 | 40.77 | -1.9 | -0.061 | -0.038 | +0.023 | 129/130 | mixed / inconclusive on this pair sample |
| SOL/USDT | crypto_alt_majors | 49.38 | 48.12 | -1.3 | -0.094 | -0.075 | +0.019 | 162/160 | no clear effect — TF change dominates |
| SPY | us_indices_trackers | 55.77 | 55.77 | +0.0 | -0.05 | -0.062 | -0.012 | 52/52 | no clear effect — TF change dominates |
| USD/ZAR | forex_exotics | 46.67 | 44.63 | -2.0 | -0.038 | -0.007 | +0.031 | 120/121 | mixed / inconclusive on this pair sample |
| WTI Oil | energy_oil | 37.96 | 38.24 | +0.3 | -0.022 | -0.034 | -0.012 | 137/136 | no clear effect — TF change dominates |
| XAU/USD | precious_trackers | 33.06 | 33.05 | -0.0 | 0.05 | 0.081 | +0.031 | 121/118 | mixed / inconclusive on this pair sample |

## Per-group inference (one pair per group — indicative only)

### crypto_alt_majors
mixed on this single-pair sample — not conclusive for the group
- SOL/USDT: no clear effect — TF change dominates (SL delta -1.3pp, R delta +0.019)

### crypto_btc
mixed on this single-pair sample — not conclusive for the group
- BTC/USDT: mixed / inconclusive on this pair sample (SL delta +2.8pp, R delta -0.016)

### energy_oil
mixed on this single-pair sample — not conclusive for the group
- WTI Oil: no clear effect — TF change dominates (SL delta +0.3pp, R delta -0.012)

### forex_crosses
sufficient signal to justify a full-group backtest
- EUR/GBP: H4 tuning improved SL rate without hurting R (SL delta -3.9pp, R delta +0.054)

### forex_exotics
mixed on this single-pair sample — not conclusive for the group
- USD/ZAR: mixed / inconclusive on this pair sample (SL delta -2.0pp, R delta +0.031)

### forex_majors
mixed on this single-pair sample — not conclusive for the group
- EUR/USD: mixed / inconclusive on this pair sample (SL delta -1.9pp, R delta +0.023)

### precious_trackers
mixed on this single-pair sample — not conclusive for the group
- XAU/USD: mixed / inconclusive on this pair sample (SL delta -0.0pp, R delta +0.031)

### us_indices_trackers
mixed on this single-pair sample — not conclusive for the group
- SPY: no clear effect — TF change dominates (SL delta +0.0pp, R delta -0.012)

### us_stock_single
mixed on this single-pair sample — not conclusive for the group
- AAPL: mixed / inconclusive on this pair sample (SL delta -1.6pp, R delta +0.022)

## Notes

- Overlay file: `configs/proposed_h4_indicator_overlay.yaml` (NOT imported by live config).
- Activate overlay only via `ATHENA_BT_H4_OVERLAY=proposed_h4_indicator_overlay` inside the experiment runner.
- Do NOT promote overlay values to `config.yaml` from this single-pair-per-group run.