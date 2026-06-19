# Industrial Metal Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Qualify the first viable industrial-metal replacement in the frozen Copper, Aluminium, Nickel, Zinc order and finalize the authoritative commodity gate at eight clusters.

**Architecture:** Extend existing registries and the consolidated reviewer with an explicit industrial-metals policy. Add a sequential research-only runner that acquires, QAs, reviews, and finalizes one candidate at a time, preserving failures and stopping on the first clear gate.

**Tech Stack:** Python 3.13, MetaTrader5 read-only adapter, immutable JSON freeze store, pytest.

## Global Constraints

- Candidate order is exactly Copper, Aluminium, Nickel, Zinc.
- Acquire only H4 and D1 for 2014-01-01 through 2026-06-19 with one pinned UTC `as_of` per candidate.
- No raw edits, interpolation, forming-bar promotion, strategy work, backtests, live routes, thresholds, execution, or TradingView changes.
- Stop at the first `CLEAR_ON_FREEZE` or `CLEAR_WITH_GAP_EMBARGO`.

---

### Task 1: Registry, family policy, and finalizer contract

**Files:**
- Modify: `athena_research/commodity_data_audit/freeze_registry.py`
- Modify: `athena_research/commodity_data_audit/consolidated_gap_review.py`
- Modify: `tools/finalize_commodity_data_gate.py`
- Create: `tests/test_industrial_metal_replacement.py`

**Interfaces:**
- Produces: `INDUSTRIAL_METAL_CANDIDATE_ORDER`, exact aliases, industrial family policies, and finalizer replacement selection.

- [ ] Add failing tests for frozen order, exact aliases, cluster mapping, no automatic independent closure, and finalizer stop/count semantics.
- [ ] Run `pytest tests/test_industrial_metal_replacement.py -q` and confirm the contract is absent.
- [ ] Implement the minimal registry, policy, and finalizer changes.
- [ ] Run the focused test file and confirm it passes.

### Task 2: Sequential acquisition and quality runner

**Files:**
- Create: `tools/complete_industrial_metal_data_gate.py`
- Modify: `tests/test_industrial_metal_replacement.py`

**Interfaces:**
- Consumes: direct MT5 copy-rates, freeze/QA/review APIs, candidate order.
- Produces: per-candidate immutable artifacts, nonzero failure status, and immediate stop on first clear candidate.

- [ ] Add failing tests using injected acquisition/QA callbacks to prove candidate order, one shared `as_of` for H4/D1, preserved failures, immediate stop, and all-failed nonzero status.
- [ ] Implement the runner with per-candidate exception isolation and no strategy contract.
- [ ] Run the focused test file and confirm it passes.

### Task 3: Execute candidate loop and finalize

**Files:**
- Create only versioned raw, normalized, QA, forensic, and authoritative gate artifacts for attempted candidates.

**Interfaces:**
- Produces: selected replacement or four explicit failures, plus authoritative eight-cluster gate output.

- [ ] Run focused tests.
- [ ] Execute `py -3.13 tools/complete_industrial_metal_data_gate.py`.
- [ ] Stop after the first clear candidate; otherwise retain all four blocked results.
- [ ] Run `py -3.13 tools/finalize_commodity_data_gate.py` and verify exact count, hashes, and selected replacement.
- [ ] Confirm final diff excludes raw artifacts, strategies, engines, execution, thresholds, TradingView, live routes, and unrelated user files.
