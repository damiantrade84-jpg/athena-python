# Final calibration decision report

_Generated: 2026-05-19. Research-only close-out; no live config, scoring, RR/SL/TP, execution, or risk behavior changes applied._

## Dataset Summary

| Metric | Value | Source |
| --- | ---: | --- |
| Accepted closed trades | 466 | `logs/calibration/sweep_input_from_audit.json` |
| ENGINE_A accepted | 276 | `logs/calibration/sweep_input_from_audit.json` |
| ENGINE_B accepted | 190 | `logs/calibration/sweep_input_from_audit.json` |
| Diagnostic events | 68 | `logs/calibration/diagnostics_normalized/diagnostic_events_normalized.json` |
| ENGINE_A diagnostics | 10 | `diagnostic_events_summary.csv` |
| ENGINE_B diagnostics | 58 | `diagnostic_events_summary.csv` |
| Threshold family rows | 8 | `threshold_recommendation_matrix.csv` |
| Live threshold recommendations | 0 | `threshold_recommendation_matrix.csv` |

## Decision

No Engine A or Engine B threshold change is supported for live promotion.

`configs/proposed_calibration_overlay.yaml` contains only diagnostics metadata and explicit no-change holds. `_proposed_gate_adjustments` is empty.

## Evidence Results

| Family | Decision | Evidence strength | Reason |
| --- | --- | --- | --- |
| Engine A score thresholds | insufficient | insufficient | Engine A diagnostic n=10; best tested R still negative |
| Engine A ADX | no change | insufficient | input-json rows do not re-simulate ADX gates |
| Engine A volatility | no change | insufficient | input-json rows do not re-simulate volatility factors |
| Engine B min_score | no change | weak | best tested R remains negative and admitted trade count drops by 102 |
| Engine B min_rr | no change | weak | accepted closed trades lack RR gate metadata |
| Engine B min_room_atr | no change | weak | accepted closed trades lack room/level metadata |
| Engine B regime multiplier | no change | weak | input-json rows do not re-simulate regime gates |
| Engine B fallback TP | no change | weak | accepted closed trades lack fallback TP attribution |

## Artifact Index

| Artifact | Path |
| --- | --- |
| Accepted input | `logs/calibration/sweep_input_from_audit.json` |
| Diagnostics normalized | `logs/calibration/diagnostics_normalized/diagnostic_events_normalized.json` |
| Diagnostic summary | `logs/calibration/diagnostics_normalized/diagnostic_events_summary.csv` |
| Sweep outputs | `logs/calibration/sweeps/*/calibration_sweep.csv` |
| Recommendation report | `logs/calibration/threshold_recommendation_report.md` |
| Recommendation matrix | `logs/calibration/threshold_recommendation_matrix.csv` |
| Proposed overlay | `configs/proposed_calibration_overlay.yaml` |
| Proposed changes | `logs/calibration/proposed_config_changes.md` |
| Rejected changes | `logs/calibration/rejected_calibration_changes.md` |

## Required Confirmations

- No Engine A scoring formula changed.
- No Engine B scoring formula changed.
- No Engine B RR/SL/TP logic changed.
- No live execution behavior changed.
- No live config defaults changed.
- All proposed calibration changes are staged only in `configs/proposed_calibration_overlay.yaml`.
- Tests are safety evidence only; threshold recommendations are based on real sweep/diagnostic evidence.
