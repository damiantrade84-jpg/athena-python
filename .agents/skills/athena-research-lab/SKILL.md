---
name: athena-research-lab
description: Use for athena_research, vectorbt research lab, backtest matrix discovery, indicator calibration, research YAML configs, and diagnostic strategy experiments. Do not use to change live thresholds, execution gates, auto-trader behavior, or production scoring without explicit user approval.
---

# Athena research lab

Diagnostic research only — not live gate tuning.

## Steps

1. Confirm scope under `athena_research/` or `tools/vectorbt_research_lab.py` and named configs.
2. Use bounded runs; avoid full matrix unless requested.
3. Report what was measured vs what would need a separate live change request.

## Boundaries

- Treat backtest results as discovery, not proof for production threshold changes.
- Do not hardcode thresholds in Python; use config layers when changes are explicitly approved.
- Read `athena_research/AGENTS.md` for folder conventions.

## Deliverables

- commands run (bounded),
- artifacts produced,
- findings vs live contract (`not verified` if not compared),
- recommended follow-up tests if code changed.
