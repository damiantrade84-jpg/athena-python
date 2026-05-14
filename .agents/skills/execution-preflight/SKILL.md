---
name: execution-preflight
description: >
  Pre-commit mental checklist for changes that touch execution, risk, or broker adapters
  in ATHENA. Use when editing execution.py, risk_engine.py, auto_trader.py, mt5_executor.py,
  bybit_executor.py, or closely coupled sizing/gate paths — before claiming work complete or
  opening a PR.
---

# Execution preflight

Use this **after** implementing a change and **before** declaring done, reviewing, or merging — only when the diff touches live execution risk surfaces.

## When to load

- Any edit to: `execution.py`, `risk_engine.py`, `auto_trader.py`, `mt5_executor.py`, `bybit_executor.py`, or wiring that changes how orders, SL/TP, sizing, or kill-switches behave.

## Checklist (all must pass or be explicitly called out)

1. **Scoring lock** — No Engine A/B/D threshold or weight changes unless the user explicitly requested them; no hardcoded trading constants (use `config.yaml`).
2. **Fail-closed** — Missing/stale/ambiguous safety fields still reject; no new default that approves execution from absent data.
3. **Gates** — Risk gates, freshness, kill-switch, execution approval, and audit/logging paths are not bypassed or weakened.
4. **SL/TP and sizing** — Broker-facing levels and position sizing preserve existing invariants (tighten-only where applicable; no silent widening without config intent).
5. **Paper-first** — No real-money enablement or implicit live toggles; repository remains paper-safe by default.
6. **Parity** — If the change affects live paths, note any intentional backtest vs live divergence; otherwise flag for parity check.
7. **Tests** — Run the smallest relevant `pytest` set (use `run-test-subset` skill for `execution`, `risk`, `bybit`, `auto_trader` keywords) and mention failures or gaps.

## Output

State **PASS** with evidence (files + checks), or **BLOCKED** with concrete items still failing.

## Reference

Cross-check invariants in [athena-code/references/audit-contract.md](../athena-code/references/audit-contract.md) and the full methodology in [athena-audit/SKILL.md](../athena-audit/SKILL.md) when depth is needed.
