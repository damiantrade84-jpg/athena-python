---
name: athena-risk-execution
description: Use before or while changing execution.py, auto_trader.py, risk_engine.py, guardian.py, mt5_executor.py, bybit_executor.py, or helpers directly on the execution path — gates, sizing, SL/TP, paper/live separation, kill switch, freshness, and broker handoff. Do not use for Engine A/B/C/D scoring logic unless execution payload consumption is in scope. Do not use to enable live trading or weaken gates.
---

# Athena risk and execution

High-risk path. Paper-only unless the user explicitly approves live.

## Control path

signal/autopilot entry → risk gates → sizing → SL/TP construction → broker call → response → monitor/audit

## Verify

1. Fail-closed on missing, stale, null, false, empty, or malformed safety fields.
2. SL/TP direction, precision, tighten-only rules, broker constraints.
3. Kill switch and freshness reachable on modified branches.
4. AI cannot approve or execute; deterministic gates remain authoritative.
5. Name **one** targeted test per changed branch; suggest a concrete test name if missing. Run only that file after patch (`pytest path/to/test_file.py -q`).

## Before editing

Consider invoking `athena-audit` when the user asked for execution-safety review or strict findings, not for small mechanical fixes. For shipped-change verification on execution paths, use `athena-anti-miss-review` (coverage map, search/adversarial pass, no PASS without traced gates).

Align with `AGENTS.md` and `references/invariants.md` under `athena-audit` when checking invariants.

Optional Codex subagent: `.codex/agents/execution-safety-reviewer.toml` for diff-only review after patches.
