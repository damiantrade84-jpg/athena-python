---
name: athena-code
description: Evidence-only engineering for the ATHENA live multi-engine trading repository. Use when Codex must audit, debug, patch, review, or extend ATHENA code with real execution risk, especially around execution safety, live/backtest parity, data integrity, market-state integrity, score integrity, risk gates, broker lifecycle, dashboard/API/Telegram truth, or engine separation across Engine A, B, C, and D.
---

# ATHENA Code

## Overview

Work only from verified repository evidence. Read the actual files before making claims, treat comments, configs, and prior discussion as unverified until traced in code, and keep changes minimal because this system has live execution risk.

## Operating Mode

Choose one mode before acting.

- Audit: inspect and report findings only. Do not patch or refactor.
- Surgical Fix: patch only a verified issue. Re-read the target file immediately before editing and preserve existing shapes unless the bug requires change.
- Feature Build: map the current architecture first, extend the correct layer, and preserve engine separation plus monitoring truth.

## Required Workflow

1. Start with `CLAUDE.md`, `config.yaml`, and the exact target files.
2. Trace the real control flow for the paths the task touches: live, scan, execute, auto-trader, monitoring, research/backtest, and operator surfaces.
3. Verify data truth from source to consumer: candle source, confirmed versus forming bars, timeframe alignment, cache use, stale-state behavior, and lookahead leakage.
4. Verify only the affected engines. Do not assume Engine A logic applies to B, C, or D.
5. Verify risk and execution truth end-to-end before changing behavior: `risk_check()`, kill switches, sizing source, SL and TP rules, lifecycle state, DB writes, and broker reconciliation.
6. Verify operator truth end-to-end: DB, API, UI, Telegram, and monitoring must reflect the same underlying state.
7. If the task is a fix or build, make the smallest supported edit and validate it with code inspection, tests, or both.

## Evidence Rules

- Treat every claim as unverified until confirmed from code, tests, logs, DB access paths, or docs.
- Never guess missing behavior. Label it `not verified`.
- Never assume a config key, helper, field, or route is live just because it exists.
- Never silently change scoring, risk, or live execution logic unless the request explicitly requires it and the call path proves the need.
- Separate confirmed findings from hypotheses.

## Reference

Load [references/audit-contract.md](references/audit-contract.md) when you need the ATHENA file map, non-negotiable invariants, audit phases, or the confirmed findings format from the provided spec.
