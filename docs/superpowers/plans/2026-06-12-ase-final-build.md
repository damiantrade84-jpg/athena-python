# ASE Final Build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ASE the standalone demo/paper decision engine across the configured universe, with complete training artifacts, one runtime inference path, a fail-closed execution bridge, an operational React panel, and source-backed build reports.

**Architecture:** Keep Layer 1 and PTIS intact. Replace the Phase 2-4 promotion workflow with deterministic per-family/horizon training and active runtime artifact discovery, but preserve repository safety authority: families that cannot train or fail mandatory evidence gates remain FLAT-only, and every execution must pass the demo gate, guardian, `risk_check()`, kill switch, freshness checks, and existing low-level broker safeguards. ASE never routes through Engine C and imports no Engine A-D modules.

**Tech Stack:** Python 3.11+, pandas/NumPy, scikit-learn HGB/isotonic models, Parquet/SQLite, Flask, React/TypeScript/Vite, pytest.

---

## Instruction Precedence

- The attached `ASE FINAL BUILD` replaces earlier Phase 2-4 workflow requirements.
- Active `AGENTS.md` safety rules still govern implementation.
- No live trading is enabled. MT5 must be demo and Bybit must be testnet.
- Failed/insufficient families remain FLAT-only; validation results are never rewritten or bypassed.
- Existing dirty-worktree changes are preserved and integrated, not reverted.

### Task 1: Deterministic Training And `train-all`

**Files:**
- Modify: `ase_cli.py`
- Modify: `athena_research/ase/train.py`
- Modify: `athena_ase/models/meta.py`
- Modify: `athena_ase/models/calibrate.py`
- Modify: `athena_ase/models/quantile_heads.py`
- Modify: `athena_ase/registry/artifacts.py`
- Modify: `athena_ase/artifacts/loader.py`
- Create: `athena_research/ase/training_report.py`
- Test: `tests/test_calibration.py`
- Test: `tests/test_quantile_heads.py`
- Test: `tests/test_artifacts.py`
- Test: `tests/test_feature_schema.py`

- [ ] Add failing tests proving chronological 80/20 split, calibration fitted only on the final 15% of the training slice, five quantile heads per target, crossing correction, fixed classifier parameters, and manifest hash verification.
- [ ] Run each named test file individually and confirm the new assertions fail for the missing behavior.
- [ ] Replace grid search in the final-build path with fixed HGB parameters and deterministic categorical encoding.
- [ ] Train core and enriched variants only when their required features are available and verified.
- [ ] Fit quantile regressors for `net_R`, `MAE_R`, `MFE_R`, and `hold_bars`.
- [ ] Select `thr_family` from the chronological eval slice with at least 40 retained trades; otherwise use `0.10` and persist an explicit fallback flag.
- [ ] Write complete manifests and `reports/ase_training_report.md`.
- [ ] Add `ase_cli.py train-all` that iterates the five families and supported horizons, records failures, and never aborts successful families because another family is insufficient.
- [ ] Re-run the four focused test files individually.

### Task 2: Runtime Replacement And Legacy Bypass

**Files:**
- Modify: `athena_ase/runtime/scan.py`
- Modify: `athena_ase/inference/predict.py`
- Modify: `athena_ase/contracts.py`
- Modify: `athena_ase/runtime/health.py`
- Modify: `engine_a_legacy_guard.py`
- Modify: Engine A entry points identified by the runtime coverage pass
- Modify: `athena_app/api/routes_ase.py`
- Test: `tests/test_decision_rule.py`
- Test: `tests/test_missing_feeds.py`
- Test: `tests/test_contract_aliases.py`
- Test: `tests/test_legacy_bypass.py`

- [ ] Add failing tests proving all configured instruments return rows for both supported horizons, runtime thresholds come only from manifests, corrupt artifacts return ERROR, and Engine A entry points always raise for ASE-owned families.
- [ ] Run each named test file individually and confirm the missing behavior.
- [ ] Remove promotion-state gating from ASE runtime decisions while retaining drift WATCH-max and mandatory validation/health metadata.
- [ ] Make scan, chart review context, and execution consume the same `predict_batch()` output.
- [ ] Remove Engine C normalization/routing from ASE runtime paths; keep compatibility aliases only for UI/review consumers.
- [ ] Keep insufficient/untrained family rows visible as FLAT or ERROR with explicit blockers.
- [ ] Re-run the four focused test files individually.

### Task 3: Standalone Demo Execution Bridge

**Files:**
- Create: `athena_ase/execution/__init__.py`
- Create: `athena_ase/execution/bridge.py`
- Create: `athena_ase/execution/journal.py`
- Create: `athena_ase/execution/reconcile.py`
- Modify: `athena_ase/runtime/scan.py`
- Modify: `athena_app/api/routes_ase.py`
- Test: `tests/test_bridge_standalone.py`
- Test: `tests/test_demo_execution_path.py`
- Test: `tests/test_demo_gate.py`

- [ ] Add failing import-graph tests prohibiting Engine A-D and Engine C imports from `athena_ase/`.
- [ ] Add a fixture-driven execution test proving the exact order: demo gate -> account/positions retrieval -> guardian -> `risk_check()` -> broker executor.
- [ ] Test kill-switch rejection, unknown positions rejection, missing freshness rejection, RR rejection, non-demo rejection, TP2 propagation, and max-hold closure.
- [ ] Implement an `ASESignal` execution-envelope adapter containing current timestamp, asset type, pair, levels, score aliases, and attested candle freshness.
- [ ] Inject account, positions, kill-switch, symbol-info, guardian, risk, executor, and close callables so tests cannot contact brokers.
- [ ] Call existing `mt5_execute()` or `bybit_execute()` only with an approved `RiskApproval`.
- [ ] Implement append-only Parquet journal rows for emitted signals and execution outcomes with atomic rewrite/locking.
- [ ] Implement a refresh-loop method that closes ASE-owned positions after `maxHoldBars`, with demo gate and ownership checks.
- [ ] Re-run the three focused test files individually.

### Task 4: Operational API And React Panel

**Files:**
- Modify: `athena_app/api/routes_ase.py`
- Modify: `static/react-app/app/src/types/athena.ts`
- Modify: `static/react-app/app/src/components/panels/ASEPanel.tsx`
- Modify: `static/react-app/app/src/components/layout/Sidebar.tsx`
- Modify: chart-AI review payload builder identified by the UI lane
- Test: relevant existing ASE/UI contract test file selected from lane findings

- [ ] Add failing API/UI contract assertions for family metrics, journal realized R, model health, quantile band, operational status, and full AI review context.
- [ ] Replace SHADOW/promotion wording and controls with operational/demo wording.
- [ ] Render decision chip, direction, expectedNetR, calibrated probability, q10-q90 return band, entry/SL/TP1/TP2, horizon, primary signals, route, version, and health.
- [ ] Add family cards sourced from the training report and trade journal.
- [ ] Keep execution status explicit and never display failed execution as successful.
- [ ] Run the focused contract test file.
- [ ] Run `npm run build` in `static/react-app/app`.

### Task 5: Reports, Training, Restart, And Verification

**Files:**
- Create: `reports/ase_final_build_report.md`
- Generate: `reports/ase_training_report.md`

- [ ] Run the user-requested ingest sources without printing secrets.
- [ ] Run the bounded/full Phase 1 command only after estimating data volume and runtime.
- [ ] Run `py ase_cli.py train-all` and capture per-family/horizon results.
- [ ] Run each required test file individually per repository policy.
- [ ] Verify artifact hashes and active version discovery.
- [ ] Restart Athena in demo/paper mode without supplying the live-order confirmation token.
- [ ] Verify `/api/ase-health`, `/api/ase-scan`, journal endpoints, and one fixture-only execution route.
- [ ] Write `reports/ase_final_build_report.md` with file inventory, exact test outputs, training summary, frontend build status, operational families, FLAT-only families, and explicit deviations.
- [ ] Run final anti-miss and execution-safety reviews and resolve all high/critical findings.
