---
name: athena-code
description: Targeted Athena fixes and evidence-based code changes. Use for bounded bug fixes, debugging, and implementation work in exact target files. Do not perform full-system audits unless explicitly requested.
---

# ATHENA Code

## Overview

Use for targeted Athena fixes and evidence-based code changes. Do not perform full-system audits unless explicitly requested.

Work only from verified repository evidence. Read the actual files before making claims, treat comments, configs, and prior discussion as unverified until traced in code, and keep changes minimal because this system has live execution risk.

## Operating Mode

Choose one mode before acting. This skill is not a full-audit skill.

- Surgical Fix: patch only a verified issue. Re-read the target file immediately before editing and preserve existing shapes unless the bug requires change.
- Feature Build: map only the current feature's architecture, extend the correct layer, and preserve engine separation plus monitoring truth.

## Required Workflow

1. Start with `CLAUDE.md` and exact target files.
2. Trace only the real control flow for the touched path and its direct callers/callees.
3. Verify data truth only where the current change depends on it: source, consumer, stale-state behavior, and type/shape contracts.
4. Verify only the affected engines. Do not assume Engine A logic applies to B, C, or D.
5. Verify risk and execution truth end-to-end before changing behavior: `risk_check()`, kill switches, sizing source, SL and TP rules, lifecycle state, DB writes, and broker reconciliation.
6. Verify operator truth end-to-end: DB, API, UI, Telegram, and monitoring must reflect the same underlying state.
7. If the task is a fix or build, make the smallest supported edit and validate it with code inspection, tests, or both.
8. **Execution path edits** — If the change touches `execution.py`, `risk_engine.py`, `auto_trader.py`, `mt5_executor.py`, or `bybit_executor.py`, load and complete `.claude/skills/execution-preflight/SKILL.md` before claiming done or opening a PR.

## Evidence Rules

- Treat every claim as unverified until confirmed from code, tests, logs, DB access paths, or docs.
- Never guess missing behavior. Label it `not verified`.
- Never assume a config key, helper, field, or route is live just because it exists.
- Never silently change scoring, risk, or live execution logic unless the request explicitly requires it and the call path proves the need.
- Separate confirmed findings from hypotheses.
- Do not load historical audit docs unless the user explicitly asks for historical/full audit comparison.
- Run only targeted tests covering changed behavior.

## References

Do not load historical audit references for normal targeted fixes. Use the manual audit skill only when the user explicitly asks for a full audit, historical/full audit comparison, or strict audit finding format.
