# Athena Calibration Final Goal

## Objective

Complete Athena Engine A and Engine B calibration evidence workflow end-to-end.

This goal must:
- verify current calibration tooling,
- fix remaining test/tooling blockers,
- run real threshold-specific sweeps,
- combine accepted trade evidence with rejected diagnostic evidence,
- produce final threshold recommendations only where evidence supports them,
- stage proposed settings only in configs/proposed_calibration_overlay.yaml,
- preserve live behavior.

## Hard Rules

- Read actual current code before editing.
- Do not guess or hallucinate.
- Do not change Engine A scoring formulas.
- Do not change Engine B scoring formulas.
- Do not change Engine B RR/SL/TP logic.
- Do not merge Engine A and Engine B.
- Do not change live execution.
- Do not modify config.yaml live defaults.
- Do not apply proposed settings to production.
- Do not use tests as trading evidence.
- Do not use dry-run/synthetic output as trading evidence.
- Do not recommend thresholds from raw PnL alone.
- Do not recommend thresholds from insufficient sample-size buckets unless marked exploratory.
- Every recommendation must cite evidence file, metric, sample size, and rollback rule.
- If evidence is insufficient, say so.

## Known CLI Notes

calibration_sweep.py may use:
- --engine engine_a
- --engine engine_b
- --engine both
- --output-dir
- --input-json

Do not use --engine ALL unless the CLI supports it.

For Windows pytest temp issues use:

$env:PYTEST_ADDOPTS="--basetemp=C:\dev\athena-python\.pytest_tmp"

## Required Work

1. Inspect current state:
   - tools/calibration_sweep.py
   - calibration export tools
   - tools/validate_proposed_calibration_overlay.py
   - logs/calibration/
   - logs/calibration_diagnostics/calibration_events.jsonl
   - logs/calibration/sweep_input_from_audit.json
   - config.py
   - config.yaml
   - scoring.py
   - factor_scoring.py
   - forex_scoring.py
   - market_structure.py
   - execution.py
   - risk_engine.py

2. Create/update:
   - logs/calibration/current_calibration_state.md

3. Run safety tests:

$env:PYTEST_ADDOPTS="--basetemp=C:\dev\athena-python\.pytest_tmp"
.venv\Scripts\python.exe -m pytest tests -q -k "calibration or diagnostics or engine_b or volatility or research_reference or rr_basis or config"
.venv\Scripts\python.exe -m pytest tests -q -k "engine_a or engine_b or scoring or market_structure or execution"

4. Verify accepted-trade input:
   - logs/calibration/sweep_input_from_audit.json

If missing, export real closed Engine A/B trades from audit.db. Do not fabricate data.

5. Verify rejected diagnostic input:
   - logs/calibration/diagnostics_normalized/diagnostic_events_normalized.json

If missing, convert:
   - logs/calibration_diagnostics/calibration_events.jsonl

Create/update:
   - tools/export_calibration_events.py

6. Confirm calibration_sweep.py supports threshold presets:

Engine A:
- engine_a_thresholds
- engine_a_adx
- engine_a_volatility

Engine B:
- engine_b_min_score
- engine_b_rr
- engine_b_room
- engine_b_regime_multiplier
- engine_b_fallback_tp

Also allow:
- combined_research_only

If missing, add them as research-only presets.

7. Run real threshold sweeps using:
   - logs/calibration/sweep_input_from_audit.json

Commands:

.venv\Scripts\python.exe tools/calibration_sweep.py --engine engine_a --preset engine_a_thresholds --input-json logs\calibration\sweep_input_from_audit.json --output-dir logs\calibration\sweeps\engine_a_thresholds
.venv\Scripts\python.exe tools/calibration_sweep.py --engine engine_a --preset engine_a_adx --input-json logs\calibration\sweep_input_from_audit.json --output-dir logs\calibration\sweeps\engine_a_adx
.venv\Scripts\python.exe tools/calibration_sweep.py --engine engine_a --preset engine_a_volatility --input-json logs\calibration\sweep_input_from_audit.json --output-dir logs\calibration\sweeps\engine_a_volatility
.venv\Scripts\python.exe tools/calibration_sweep.py --engine engine_b --preset engine_b_min_score --input-json logs\calibration\sweep_input_from_audit.json --output-dir logs\calibration\sweeps\engine_b_min_score
.venv\Scripts\python.exe tools/calibration_sweep.py --engine engine_b --preset engine_b_rr --input-json logs\calibration\sweep_input_from_audit.json --output-dir logs\calibration\sweeps\engine_b_rr
.venv\Scripts\python.exe tools/calibration_sweep.py --engine engine_b --preset engine_b_room --input-json logs\calibration\sweep_input_from_audit.json --output-dir logs\calibration\sweeps\engine_b_room
.venv\Scripts\python.exe tools/calibration_sweep.py --engine engine_b --preset engine_b_regime_multiplier --input-json logs\calibration\sweep_input_from_audit.json --output-dir logs\calibration\sweeps\engine_b_regime_multiplier
.venv\Scripts\python.exe tools/calibration_sweep.py --engine engine_b --preset engine_b_fallback_tp --input-json logs\calibration\sweep_input_from_audit.json --output-dir logs\calibration\sweeps\engine_b_fallback_tp

If CLI differs, inspect --help and run equivalent valid commands.

8. Create/update:
   - logs/calibration/threshold_recommendation_report.md
   - logs/calibration/threshold_recommendation_matrix.csv
   - logs/calibration/proposed_config_changes.md
   - logs/calibration/rejected_calibration_changes.md
   - logs/calibration/final_calibration_decision_report.md

9. Create/update proposed overlay only:
   - configs/proposed_calibration_overlay.yaml

Do not modify config.yaml.

Every proposed setting must include:
- config key,
- current value,
- proposed value,
- engine,
- group/asset/regime,
- evidence file,
- metric,
- accepted sample size,
- diagnostic sample size,
- expected effect,
- rollback rule,
- evidence strength,
- overfit risk,
- live_change_allowed=false.

10. Validate overlay:

.venv\Scripts\python.exe tools\validate_proposed_calibration_overlay.py --overlay configs\proposed_calibration_overlay.yaml

11. Final diff check:

git diff -- config.py config.yaml scoring.py factor_scoring.py forex_scoring.py market_structure.py execution.py risk_engine.py
git status

Expected:
- no live config default changes,
- no scoring formula changes,
- no Engine B RR formula changes,
- no execution/risk behavior changes.

## Final Response Required

Include:
- whether threshold evidence is sufficient,
- files read,
- files changed,
- files generated,
- commands run,
- test results,
- overlay validation result,
- accepted trade row count,
- diagnostic row count,
- Engine A/B row counts,
- sample-size warnings,
- threshold recommendations with evidence,
- insufficient-evidence threshold families,
- explicit confirmations:

No Engine A scoring formula changed.
No Engine B scoring formula changed.
No Engine B RR/SL/TP logic changed.
No live execution behavior changed.
No live config defaults changed.
All proposed calibration changes are staged only in configs/proposed_calibration_overlay.yaml.
Tests are safety evidence only; threshold recommendations are based on real sweep/diagnostic evidence.
