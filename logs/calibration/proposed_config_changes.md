# Proposed calibration config changes

_Overlay: `configs/proposed_calibration_overlay.yaml` - inactive, not imported into live config._

## Summary

| Status | Count |
| --- | ---: |
| Diagnostics metadata settings | 2 |
| Threshold gate promotions | 0 |
| Explicit no-change holds | 8 |
| live_change_allowed=true | 0 |

## Proposed Active Settings

| Key | Current | Proposed | Evidence | Metric | Accepted n | Diagnostic n | Strength | Rollback |
| --- | --- | --- | --- | --- | ---: | ---: | --- | --- |
| `CALIBRATION_DIAGNOSTICS_ENABLED` | true | true | `logs/calibration/diagnostics_normalized/diagnostic_events_summary.csv` | diagnostic_n=68; A=10; B=58 | 466 | 68 | weak | set false; archive JSONL |
| `CALIBRATION_DIAGNOSTICS_PATH` | `logs/calibration_diagnostics/calibration_events.jsonl` | same | `config.yaml` | path unchanged | 466 | 68 | weak | n/a |

## Threshold Families

No threshold family is proposed for live config. Evidence is recorded in `logs/calibration/threshold_recommendation_matrix.csv`.

| Family | Decision | Reason |
| --- | --- | --- |
| `engine_a_thresholds` | no promotion | Engine A diagnostic n=10; below n>=30 |
| `engine_a_adx` | no promotion | input-json sweep cannot prove ADX gates without re-simulation |
| `engine_a_volatility` | no promotion | input-json sweep cannot prove volatility scaler without re-simulation |
| `engine_b_min_score` | no promotion | best tested value remains negative expectancy and drops admitted trades by 102 |
| `engine_b_rr` | no promotion | closed-trade export lacks RR gate metadata needed for min_rr recommendation |
| `engine_b_room` | no promotion | closed-trade export lacks room/level metadata |
| `engine_b_regime_multiplier` | no promotion | input-json sweep cannot prove regime multipliers without re-simulation |
| `engine_b_fallback_tp` | no promotion | accepted export has fallback flag populated for 0/466 rows |

## Confirmation

- `config.yaml` was not modified.
- No Engine A/B scoring formula files were modified.
- No Engine B RR/SL/TP logic was modified.
- No execution/risk behavior was modified.
