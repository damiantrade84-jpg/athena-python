# Commodity H4 Consolidated Residual-Gap Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce family-aware, exhaustive residual-gap classifications, deterministic admissibility gates, and fail-closed strategy embargo masks for six commodity H4 datasets.

**Architecture:** Add a separate consolidated reviewer that consumes existing freeze/forensic evidence without changing the legacy classifier. Add an independent gate evaluator and embargo module, then expose a read-only CLI that writes immutable versioned artifacts.

**Tech Stack:** Python 3.13, dataclasses, pandas, JSON, pytest, existing MT5 read-only adapters and freeze stores.

## Global Constraints

- Research-data work only; no strategies, backtests, live routes, thresholds, execution, TradingView, or raw-bar edits.
- Preserve all prior reviews/manifests, including superseded development runs, and write corrected `commodity_h4_residual_gap_review_v3` artifacts.
- Provider missing percentage is strictly less than `0.5%` against reliable-era expected bars.
- Isolation means at most 6 missing H4 bars per event, at least 20 calendar days between events, and strictly less than `2.0%` in every rolling 90-day expected-session window.
- Candidate-mask generation requires an explicit validated `commodity_gap_embargo_contract_v1`; no defaults.

---

### Task 1: Classification and family policy

**Files:**
- Create: `athena_research/commodity_data_audit/consolidated_gap_review.py`
- Create: `tests/test_commodity_consolidated_gap_review.py`

**Interfaces:**
- Produces: `ResidualGapClass`, `FamilyClosurePolicy`, `ResidualGapEvidence`, `classify_residual_event(evidence) -> ResidualGapClass`

- [ ] Add failing tests for Nat Gas independent closure, WTI/Brent peer-confirmed closure, corruption/acquisition precedence, and conflicting evidence becoming unresolved.
- [ ] Run `pytest tests/test_commodity_consolidated_gap_review.py -q` and confirm failures are caused by missing interfaces.
- [ ] Implement the six-value enum, explicit family policies, complete evidence model, and exactly-one classification function.
- [ ] Run the same test file and confirm the classification tests pass.

### Task 2: Deterministic admissibility and gates

**Files:**
- Modify: `athena_research/commodity_data_audit/consolidated_gap_review.py`
- Modify: `tests/test_commodity_consolidated_gap_review.py`

**Interfaces:**
- Produces: `GapAdmissibilityMetrics`, `evaluate_gap_gate(events, expected_timestamps) -> GapGateResult`

- [ ] Add failing tests proving an isolated provider gap below strict `0.5%` clears with embargo, equality at `0.5%` blocks, more than 6 bars blocks, less than 20-day separation blocks, rolling 90-day rate at or above `2.0%` blocks, corruption blocks, and unresolved blocks.
- [ ] Implement precise numerator/denominator recording, stable expected-session calculations, isolation metrics, and six-status gate precedence.
- [ ] Run `pytest tests/test_commodity_consolidated_gap_review.py -q` and confirm gate tests pass.

### Task 3: Required strategy embargo contract

**Files:**
- Create: `athena_research/commodity_data_audit/gap_embargo.py`
- Modify: `tests/test_commodity_consolidated_gap_review.py`

**Interfaces:**
- Produces: `StrategyEmbargoContract`, `build_gap_exclusion_mask(candidate_timestamps, provider_gaps, contract, run_context) -> GapExclusionMask`

- [ ] Add failing tests for missing contract, invalid integer values, feature-lookback crossing, entry/label/holding crossing, post-gap rewarm, stable versioned run hash, and input bars remaining byte-for-byte equivalent.
- [ ] Implement validation with no defaults, interval construction, reason-coded mask output, and stable hash inputs including all four contract values.
- [ ] Run `pytest tests/test_commodity_consolidated_gap_review.py -q` and confirm embargo tests pass.

### Task 4: Consolidated evidence runner and immutable artifacts

**Files:**
- Modify: `athena_research/commodity_data_audit/consolidated_gap_review.py`
- Create: `tools/review_commodity_residual_gaps.py`
- Modify: `tests/test_commodity_consolidated_gap_review.py`

**Interfaces:**
- Consumes: existing raw chunks, QA-v3 coverage, MT5 subject/D1 refetch, family peers, chunk provenance, and reopening corruption metadata.
- Produces: immutable `commodity_h4_residual_gap_review_v3` JSON per symbol and a consolidated summary.

- [ ] Add a failing orchestration test with fake read-only MT5 evidence proving every event is exported once, peers cannot override defects/corruption, no self-peer is created, and conflicting artifact rewrites fail.
- [ ] Implement the read-only runner, source hashes, evidence serialization, aggregate counts, gate output, and CLI nonzero behavior when any symbol is blocked.
- [ ] Run `pytest tests/test_commodity_consolidated_gap_review.py -q` and confirm the full file passes.

### Task 5: Generate six reviews and verify scope

**Files:**
- Create: `logs/commodity_data_audit/forensics/consolidated_v3/XPT_USD/H4.residual_gap_review.commodity_h4_residual_gap_review_v3.json`
- Create: `logs/commodity_data_audit/forensics/consolidated_v3/XPD_USD/H4.residual_gap_review.commodity_h4_residual_gap_review_v3.json`
- Create: `logs/commodity_data_audit/forensics/consolidated_v3/WTI_Oil/H4.residual_gap_review.commodity_h4_residual_gap_review_v3.json`
- Create: `logs/commodity_data_audit/forensics/consolidated_v3/Brent_Oil/H4.residual_gap_review.commodity_h4_residual_gap_review_v3.json`
- Create: `logs/commodity_data_audit/forensics/consolidated_v3/Nat_Gas/H4.residual_gap_review.commodity_h4_residual_gap_review_v3.json`
- Create: `logs/commodity_data_audit/forensics/consolidated_v3/Gasoline/H4.residual_gap_review.commodity_h4_residual_gap_review_v3.json`
- Create: `logs/commodity_data_audit/forensics/consolidated_v3/commodity_h4_residual_gap_summary_v3.json`

**Interfaces:**
- Consumes: the six existing H4 freezes and direct read-only MT5 evidence.
- Produces: final classifications, admissibility metrics, and gates; no candidate mask until a strategy contract is supplied.

- [ ] Run the consolidated CLI for XPT/USD, XPD/USD, WTI Oil, Brent Oil, Nat Gas, and Gasoline.
- [ ] Validate artifact hashes, exact classification cardinality, missing-percentage arithmetic, and preserved prior artifact hashes.
- [ ] Confirm no files under `logs/commodity_data_audit/raw/`, Engine A/B/D, execution, TradingView, live routes, or prior review/manifest paths changed.
- [ ] Report the consolidated gate table and explicitly block trend-specialist work unless all eight clusters are `CLEAR_ON_FREEZE` or `CLEAR_WITH_GAP_EMBARGO`.
