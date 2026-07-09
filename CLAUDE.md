# CLAUDE.md - Athena / Sentinel Pro v4

Claude Code repo instructions. Keep this file short; use current source as truth.

## Current Status

- Paper/demo by default. Live trading requires explicit user approval.
- Bybit is the primary crypto venue for trade buckets, microstructure, live ticks, levels, and paper execution.
- Binance microstructure is off by default when Bybit is primary. Enable only with `MICROSTRUCTURE_BINANCE_FEEDS_ENABLED: true` or `TRADE_BUCKET_EXCHANGE: binance`.
- Binance candle/live-price paths may still run for Binance-sourced data; this is separate from Binance microstructure.
- MT5 D1 fetch has bounded read-only retry for small stale-history windows. Stale retry results still fail closed at freshness/risk gates.
- ASE is standalone under `athena_ase/`, demo-paper only, and must not reuse Engine A scoring or indicators.

## Core Rules

- Inspect current files before claims or patches. Do not infer from old audits, memory files, or stale docs.
- Keep Engine A, B, C, D, ASE, Research Lab, UI, and execution paths separate unless the task requires integration.
- Engine A and Engine B scoring must not affect each other. Never write one engine's score, pct, or gate state into the other engine's score fields. Cross-engine consensus lives only in explicitly named blend/annotation fields (`combinedConviction`, `engine_b_*`) and must use graded totals (score/max_possible) — never binary gate outputs like `gate_pct`, which are 100 for every passing signal and silently saturate blends and headline scores.
- Do not change thresholds, weights, gates, SL/TP, RR, strategy semantics, or execution safety unless explicitly requested.
- Never bypass risk, freshness, kill switch, execution approval, broker, audit, or deterministic safety checks.
- AI is advisory only and cannot execute, approve orders, mutate config, or override deterministic gates.
- Never read, print, modify, or commit `.env`, secrets, API keys, tokens, or credentials.

## Workflow

- Trace entry point -> producer -> consumer -> tests before editing.
- Make the smallest safe change.
- Run one targeted pytest file or one targeted test case after fixes.
- Do not run full suites, backtest matrices, live trading, broker actions, or long research jobs unless explicitly requested.
- Final answer: summary, files inspected/changed, tests/checks run, and remaining risks or `not verified`.

## High-Risk Files

Treat these as safety-critical: `execution.py`, `risk_engine.py`, `guardian.py`,
`auto_trader.py`, `mt5_executor.py`, `bybit_executor.py`, broker feeds, freshness checks,
and config gates. Prefer fail-closed behavior for missing, stale, malformed, delayed,
duplicated, or ambiguous data.
