# Threshold recommendation report

_Generated: 2026-05-19 16:23 UTC. Research-only; no live config or execution changes._

## Dataset

| Metric | Value |
| --- | --- |
| Accepted closed trades | 466 |
| ENGINE_A accepted | 276 |
| ENGINE_B accepted | 190 |
| Diagnostic events | 68 |
| ENGINE_A diagnostics | 10 |
| ENGINE_B diagnostics | 58 |
| Recommendation rows | 8 |
| Exploratory recommendations | 0 |
| No-change rows | 7 |
| Insufficient rows | 1 |

## Recommendations

No threshold family is approved for live promotion. Rows marked `exploratory_only`, if any, remain `live_change_allowed=false`.

| Family | Engine | Current | Recommended | Accepted n | Diagnostic n | Baseline R | Best R | Evidence | Decision | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| engine_a_thresholds | ENGINE_A | 1.5 | none | 276 | 10 | -0.098235 | -0.075905 | insufficient | insufficient | sample size below threshold for accepted or diagnostic evidence |
| engine_a_adx | ENGINE_A | {"ADX_TREND_MIN_CLASS": {"commodity": 25, "crypto": 18, "crypto_alt_majors": 18, "crypto_btc": 18, "crypto_doge": 20, "crypto_eth": 18, "crypto_other": 20, "forex": 20, "forex_crosses": 20, "forex_exotics": 18, "forex_majors": 20, "forex_other": 20, "index": 25, "nat_gas": 27, "softs": 25, "stock": 25}, "FACTOR_ADX_HARD_FAIL_CLASS": {"commodity": 10, "crypto": 10, "crypto_alt_majors": 10, "crypto_btc": 10, "crypto_doge": 10, "crypto_eth": 10, "crypto_other": 12, "forex": 10, "forex_crosses": 10, "forex_exotics": 9, "forex_majors": 10, "forex_other": 10, "index": 10, "nat_gas": 12, "softs": 10, "stock": 10}} | none | 276 | 10 | -0.098235 | -0.078271 | insufficient | no_change | input-json sweep cannot prove this family because required per-trade gate fields are absent or not re-simulated |
| engine_a_volatility | ENGINE_A | {"commodity": {"high": 0.015, "low": 0.003}, "crypto": {"high": 0.04, "low": 0.01}, "energy_oil": {"high": 0.015, "low": 0.003}, "forex": {"high": 0.0025, "low": 0.0005}, "index": {"high": 0.01, "low": 0.002}, "precious_trackers": {"high": 0.015, "low": 0.003}, "stock": {"high": 0.02, "low": 0.005}} | none | 276 | 10 | -0.098235 | -0.078271 | insufficient | no_change | input-json sweep cannot prove this family because required per-trade gate fields are absent or not re-simulated |
| engine_b_min_score | ENGINE_B | 4.0 | none | 190 | 58 | -0.030806 | -0.024483 | weak | no_change | best tested value does not clear expectancy/trade-count/diagnostic gates |
| engine_b_rr | ENGINE_B | 1.5 | none | 190 | 58 | -0.030806 | -0.024483 | weak | no_change | input-json sweep cannot prove this family because required per-trade gate fields are absent or not re-simulated |
| engine_b_room | ENGINE_B | null | none | 190 | 58 | -0.030806 | -0.024483 | weak | no_change | input-json sweep cannot prove this family because required per-trade gate fields are absent or not re-simulated |
| engine_b_regime_multiplier | ENGINE_B | {"HIGH_VOLATILITY": 0.85, "LOW_VOLATILITY": 0.9, "RANGING": 0.9, "TRENDING": 0.9} | none | 190 | 58 | -0.030806 | -0.024483 | weak | no_change | input-json sweep cannot prove this family because required per-trade gate fields are absent or not re-simulated |
| engine_b_fallback_tp | ENGINE_B | true | none | 190 | 58 | -0.030806 | -0.024483 | weak | no_change | input-json sweep cannot prove this family because required per-trade gate fields are absent or not re-simulated |

## Insufficient Evidence Families

- `engine_a_thresholds`: sample size below threshold for accepted or diagnostic evidence (accepted_n=276, diagnostic_n=10, evidence=insufficient).
- `engine_a_adx`: input-json sweep cannot prove this family because required per-trade gate fields are absent or not re-simulated (accepted_n=276, diagnostic_n=10, evidence=insufficient).
- `engine_a_volatility`: input-json sweep cannot prove this family because required per-trade gate fields are absent or not re-simulated (accepted_n=276, diagnostic_n=10, evidence=insufficient).
- `engine_b_min_score`: best tested value does not clear expectancy/trade-count/diagnostic gates (accepted_n=190, diagnostic_n=58, evidence=weak).
- `engine_b_rr`: input-json sweep cannot prove this family because required per-trade gate fields are absent or not re-simulated (accepted_n=190, diagnostic_n=58, evidence=weak).
- `engine_b_room`: input-json sweep cannot prove this family because required per-trade gate fields are absent or not re-simulated (accepted_n=190, diagnostic_n=58, evidence=weak).
- `engine_b_regime_multiplier`: input-json sweep cannot prove this family because required per-trade gate fields are absent or not re-simulated (accepted_n=190, diagnostic_n=58, evidence=weak).
- `engine_b_fallback_tp`: input-json sweep cannot prove this family because required per-trade gate fields are absent or not re-simulated (accepted_n=190, diagnostic_n=58, evidence=weak).

## Artifact Inputs

- `logs/calibration/sweep_input_from_audit.json`
- `logs/calibration/diagnostics_normalized/diagnostic_events_normalized.json`
- `logs/calibration/master_evidence_report.md`
- `logs/calibration/group_blocker_matrix.csv`
- `logs/calibration/controlled_experiment_plan.md`
- `logs/calibration/sweeps/*/calibration_sweep.csv`

## Policy

- `live_change_allowed=false` for every row.
- Rankings use expectancy / robust metadata, not raw PnL alone.
- Tests are safety evidence only; threshold decisions use real input-json sweeps and diagnostics.
