# Commodity H4 Trend Continuation V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the research preflight and produce an evidence-backed terminal verdict without crossing the Phase 0 data gate.

**Architecture:** A research-only module reads the authoritative gate and verifies frozen H4/D1 artifact presence and hashes. It returns a deterministic report and stops with `BLOCKED_DATA` before any indicator, signal, cost, or performance code when required D1 data is absent.

**Tech Stack:** Python 3.13 standard library, pytest, existing commodity audit JSON artifacts.

---

### Task 1: Preflight contract tests

**Files:**
- Create: `tests/test_commodity_trend_v1.py`

- [ ] Write tests using a temporary eight-cluster gate and artifact tree that assert eight-cluster loading, XPD exclusion, missing-D1 failure, deterministic report hashing, unchanged-raw enforcement, and no production imports.
- [ ] Run `py -3.13 -m pytest tests/test_commodity_trend_v1.py -q` and confirm import failure before implementation.

### Task 2: Research-only preflight implementation

**Files:**
- Create: `athena_research/commodity_trend_v1/__init__.py`
- Create: `athena_research/commodity_trend_v1/preflight.py`
- Create: `athena_research/commodity_trend_v1/run_preflight.py`

- [ ] Define the exact eligible universe and terminal verdict enum.
- [ ] Load and validate the authoritative gate schema, qualifying count, success flag, raw-unchanged flags, accepted gates, and XPD exclusion.
- [ ] Resolve each review plus normalized H4/D1 file and manifest under an explicit repository root.
- [ ] Hash present files, record missing artifacts, and return `BLOCKED_DATA` without invoking strategy code.
- [ ] Serialize stable JSON, derive the run hash without self-reference, and write only a new content-addressed output.
- [ ] Run `py -3.13 -m pytest tests/test_commodity_trend_v1.py -q` and confirm all tests pass.

### Task 3: Frozen repository preflight

**Files:**
- Create ignored artifact: `logs/commodity_trend_v1/preflight/<run-hash>.json`

- [ ] Run `py -3.13 -m athena_research.commodity_trend_v1.run_preflight` once against the authoritative gate.
- [ ] Confirm the report identifies the seven missing D1 series and has primary verdict `BLOCKED_DATA`.
- [ ] Confirm `git diff` contains no raw artifacts, production modules, or unrelated files.

### Task 4: Completion verification

- [ ] Re-run `py -3.13 -m pytest tests/test_commodity_trend_v1.py -q` as fresh evidence.
- [ ] Inspect `git status --short` and the preflight artifact; report unimplemented phases as intentionally blocked, not as tested or completed.
