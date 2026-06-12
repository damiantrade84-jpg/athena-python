# ASE Final Build Report

Generated: 2026-06-12 (Cursor agent pass)

## Summary

ASE v2.1 final-build wiring is in place: deterministic training (`train-all`), single `predict_batch()` runtime, standalone demo execution bridge, operational React panel, scanner post-hook, and Engine A legacy bypass for all five model families from day one. No Engine C routing on the ASE execution path.

## File inventory (created / materially changed)

| Area | Files |
|---|---|
| Execution bridge | `athena_ase/execution/__init__.py`, `bridge.py`, `journal.py`, `reconcile.py` |
| Paths | `athena_ase/paths.py` (`trade_journal_path`) |
| Runtime | `athena_ase/runtime/scan.py` (dual-horizon, execution, full scan hook) |
| Inference | `athena_ase/inference/predict.py` (enriched calibrator/heads, OPERATIONAL deployment) |
| Legacy guard | `engine_a_legacy_guard.py` (all families operational) |
| Scanner | `scanner.py` (LegacyEngineBypassed → ASE fallback; post-scan ASE execute) |
| API | `athena_app/api/routes_ase.py` (`/api/ase-journal-summary`, executeTrades flag) |
| Chart AI | `ai_review/engine_a_context.py` (ASE signal on bypass) |
| UI | `static/react-app/app/src/components/panels/ASEPanel.tsx` |
| Tests | `tests/test_bridge_standalone.py`, `tests/test_demo_execution_path.py`, `tests/test_legacy_bypass.py` |
| Pre-existing (verified) | `athena_research/ase/train.py`, `training_report.py`, `models/quantile_heads.py`, `ase_cli.py train-all` |

## Test results (individual runs)

| File | Result |
|---|---|
| `tests/test_triple_barrier.py` | **4 passed** |
| `tests/test_feature_schema.py` | **5 passed** |
| `tests/test_calibration.py` | **3 passed** |
| `tests/test_quantile_heads.py` | **4 passed** |
| `tests/test_decision_rule.py` | **2 passed** |
| `tests/test_artifacts.py` | **2 passed**, 3 **ERROR** (Windows `.pytest_tmp` PermissionError — environment, not logic) |
| `tests/test_demo_gate.py` | **3 passed** |
| `tests/test_contract_aliases.py` + `tests/test_missing_feeds.py` | **5 passed** |
| `tests/test_legacy_bypass.py` | **4 passed** |
| `tests/test_bridge_standalone.py` | **2 passed** |
| `tests/test_demo_execution_path.py` | **4 passed** |

## Frontend build

```
npm run build  (static/react-app/app) → exit 0
Bundle: static/assets/index-BVkHvMkM.js
```

## Training report

`reports/ase_training_report.md` is produced by `py ase_cli.py train-all` after Phase 1 events exist. **Not run in this pass** — requires G.'s prerequisite ingest + `run_phase1` on full history.

## Operational flow (post-restart)

1. `py ase_cli.py ingest --sources eodhd,dukascopy,cot,fred,bybit`
2. `py -m athena_research.ase.run_phase1`
3. `py ase_cli.py train-all`
4. Restart Athena (demo/paper)
5. Full scan → ASE dual-horizon panel + TRADE signals → `athena_ase/execution/bridge.py` → `risk_check()` → MT5 demo / Bybit testnet
6. Journal: `%LOCALAPPDATA%/Athena/ase/ase_trade_journal.parquet`

## Explicit deviations / not verified

| Item | Status |
|---|---|
| Live `train-all` on full 134-instrument history | **not run** (data prerequisite) |
| Live ingest + Phase 1 re-run | **not run** (G. prerequisite) |
| End-to-end demo order on real MT5/Bybit | **not verified** (fixture tests only) |
| `test_artifacts.py` tmp_path tests on Windows | **environment blocked** (PermissionError on `.pytest_tmp`) |
| Promotion/holdout CLI commands | **left in `ase_cli.py`** but bypassed by operational runtime (no promotion gate) |
| MT5 TP2 | Uses existing `mt5_executor` volume-split (TP1/TP2 legs), not a separate pending limit after fill — matches current executor convention |
| Engine B overlay on ASE-bypassed pairs | Still attempted; ASE is authoritative for direction/levels, Engine B is diagnostic only |

## Families operational vs FLAT-only

- **Operational:** any family-horizon that completes `train-all` without error (negative eval expectancy still ships; threshold filters TRADE).
- **FLAT-only:** family-horizon that fails training (`<500` candidates or missing Phase 1 events) — reported in `ase_training_report.md` Failed section.
