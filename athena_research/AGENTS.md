# AGENTS.md — Research Lab

Scope: `athena_research/`, `tools/vectorbt_research_lab.py`, `configs/vectorbt_research_lab.yaml`, and related research configs.

## Rules

- Backtests and lab runs are **diagnostic discovery**, not permission to tune live thresholds.
- Do not optimize runs to look profitable; validate indicator and strategy behavior.
- Do not change live scoring gates or `config.yaml` live thresholds unless the user explicitly requests it.
- Prefer short, bounded research jobs; no full matrix unless requested.

## Skills

- `athena-research-lab` — vectorbt lab workflows, calibration, research configs
- `athena-engine-parity` — when comparing lab output to live engine contracts

Parent rules: repo root `AGENTS.md`.
