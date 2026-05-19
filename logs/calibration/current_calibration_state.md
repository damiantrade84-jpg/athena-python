# Current calibration state

_Generated: 2026-05-19. Research-only calibration evidence state; no live config, scoring, RR/SL/TP, risk, or execution behavior change applied._

## Scope

This state file covers the Engine A / Engine B calibration evidence workflow requested by `docs/codex_calibration_goal.md`.

## Files inspected

- `tools/calibration_sweep.py`
- `tools/export_calibration_sweep_input.py`
- `tools/export_calibration_events.py`
- `tools/build_threshold_recommendation_report.py`
- `tools/build_master_evidence_report.py`
- `tools/validate_proposed_calibration_overlay.py`
- `logs/calibration/`
- `logs/calibration_diagnostics/calibration_events.jsonl`
- `logs/calibration/sweep_input_from_audit.json`
- `logs/calibration/diagnostics_normalized/diagnostic_events_normalized.json`
- `config.py`
- `config.yaml`
- `scoring.py`
- `factor_scoring.py`
- `forex_scoring.py`
- `market_structure.py`
- `execution.py`
- `risk_engine.py`

## Accepted trade input

Source: `logs/calibration/sweep_input_from_audit.json`

| Metric | Value |
| --- | ---: |
| Closed Engine A/B trades | 466 |
| Engine A closed trades | 276 |
| Engine B closed trades | 190 |
| Date min | 2026-03-29 |
| Date max | 2026-05-19 |
| Contains score fields | true |
| Live config mutation | false |
| Live execution behavior changed | false |

## Rejected diagnostic input

Source: `logs/calibration/diagnostics_normalized/diagnostic_events_normalized.json`

| Metric | Value |
| --- | ---: |
| Diagnostic events | 68 |
| Engine A diagnostics | 10 |
| Engine B diagnostics | 58 |

The normalized diagnostic source exists. `tools/export_calibration_events.py` also exists for converting `logs/calibration_diagnostics/calibration_events.jsonl` when regeneration is needed.

## Sweep presets

`tools/calibration_sweep.py` supports the required research-only presets:

- `engine_a_thresholds`
- `engine_a_adx`
- `engine_a_volatility`
- `engine_b_min_score`
- `engine_b_rr`
- `engine_b_room`
- `engine_b_regime_multiplier`
- `engine_b_fallback_tp`
- `combined_research_only`

## Root sweep outputs

The required root sweep outputs were regenerated with `--input-json logs\calibration\sweep_input_from_audit.json`:

- `logs/calibration/sweeps/engine_a_thresholds/calibration_sweep.csv`
- `logs/calibration/sweeps/engine_a_adx/calibration_sweep.csv`
- `logs/calibration/sweeps/engine_a_volatility/calibration_sweep.csv`
- `logs/calibration/sweeps/engine_b_min_score/calibration_sweep.csv`
- `logs/calibration/sweeps/engine_b_rr/calibration_sweep.csv`
- `logs/calibration/sweeps/engine_b_room/calibration_sweep.csv`
- `logs/calibration/sweeps/engine_b_regime_multiplier/calibration_sweep.csv`
- `logs/calibration/sweeps/engine_b_fallback_tp/calibration_sweep.csv`

Each sweep was run in `input_json` mode, research-only, with `live_config_mutation=false`, `live_execution_behavior_changed=false`, `threshold_auto_tuning=false`, and `ranking_policy=robust_metrics_not_raw_pnl`.

## Recommendation state

Root recommendation artifacts:

- `logs/calibration/threshold_recommendation_matrix.csv`
- `logs/calibration/threshold_recommendation_report.md`
- `logs/calibration/master_evidence_report.md`
- `logs/calibration/group_blocker_matrix.csv`
- `logs/calibration/proposed_config_changes.md`
- `logs/calibration/rejected_calibration_changes.md`
- `logs/calibration/final_calibration_decision_report.md`

The recommendation matrix has 8 threshold family rows, 0 promoted recommendations, and every row has `live_change_allowed=false`.

## Threshold family decisions

| Family | Engine | Accepted n | Diagnostic n | Decision | Evidence strength |
| --- | --- | ---: | ---: | --- | --- |
| `engine_a_thresholds` | ENGINE_A | 276 | 10 | insufficient | insufficient |
| `engine_a_adx` | ENGINE_A | 276 | 10 | no_change | insufficient |
| `engine_a_volatility` | ENGINE_A | 276 | 10 | no_change | insufficient |
| `engine_b_min_score` | ENGINE_B | 190 | 58 | no_change | weak |
| `engine_b_rr` | ENGINE_B | 190 | 58 | no_change | weak |
| `engine_b_room` | ENGINE_B | 190 | 58 | no_change | weak |
| `engine_b_regime_multiplier` | ENGINE_B | 190 | 58 | no_change | weak |
| `engine_b_fallback_tp` | ENGINE_B | 190 | 58 | no_change | weak |

No threshold family has sufficient evidence for live promotion.

## Sample-size warnings

- Engine A diagnostics are below the n>=30 evidence floor: n=10.
- Engine B evidence is weak, not promotable, because the accepted closed-trade export lacks the direct gate metadata needed for RR, room, regime, and fallback TP attribution.
- The sweep outputs include small-sample warnings at row level; small buckets are not promotion evidence.

## Overlay state

`configs/proposed_calibration_overlay.yaml` is the only intended staged file. It is inactive, not auto-imported into live config, and contains diagnostics metadata plus explicit no-change holds. `_proposed_gate_adjustments` is empty.

## Live behavior confirmations

- No Engine A scoring formula change is required or proposed.
- No Engine B scoring formula change is required or proposed.
- No Engine B RR/SL/TP logic change is required or proposed.
- No live execution behavior change is required or proposed.
- No `config.yaml` live default change is required or proposed.
- Tests are safety evidence only; threshold decisions are based on real accepted-trade sweep evidence and rejected diagnostic evidence.
