# AGENTS.md - Athena / Sentinel Pro v4

Athena is a multi-engine trading analysis and execution-support repo. Default mode is
paper/demo only unless the user explicitly approves live trading.

## Current Status

- Bybit is the primary crypto venue for trade buckets, microstructure, live ticks, levels, and paper execution.
- Binance microstructure feeds are disabled by default when Bybit is primary. Start them only with `MICROSTRUCTURE_BINANCE_FEEDS_ENABLED: true` or when `TRADE_BUCKET_EXCHANGE: binance`.
- Binance kline/live-price support may still run for Binance-sourced candle/price data; do not confuse that with Binance microstructure.
- MT5 D1 fetch has a bounded read-only retry for small stale-history windows. If data remains stale, freshness/risk gates still reject.
- Engine A, B, C, D, ASE, Research Lab, UI, and execution paths stay separate unless the task explicitly requires integration.
- ASE lives under `athena_ase/`, is standalone/demo-paper only, and must not import Engine A scoring or indicators.

## Non-Negotiable

- Read current source before claims or patches. Do not rely on memory, old audits, or generated logs unless the user names them.
- Use the smallest safe diff. No unrelated refactors, thresholds, weights, gate behavior, SL/TP, RR, or strategy changes unless requested.
- Engine A and Engine B scoring must not affect each other. Never write one engine's score, pct, or gate state into the other engine's score fields. Cross-engine consensus lives only in explicitly named blend/annotation fields (`combinedConviction`, `engine_b_*`) and must use graded totals (score/max_possible) — never binary gate outputs like `gate_pct`, which are 100 for every passing signal and silently saturate blends and headline scores.
- Never bypass risk, freshness, kill switch, execution approval, broker, audit, or deterministic safety gates.
- AI is advisory only. It cannot execute, approve orders, mutate config, or override deterministic gates.
- Never read, print, modify, or commit `.env`, secrets, API keys, tokens, or credentials.

## Workflow

- Before editing: identify the real entry path, read producer and consumer code, check current tests, then make the minimal change.
- After editing: run one targeted test file or one targeted test case for the touched behavior.
- Do not run full test suites, backtest matrices, live trading, broker actions, or long research jobs unless explicitly requested.
- Reviews/audits require a coverage map and concrete findings with file/function/line evidence. If coverage is incomplete, say so.

## High-Risk Paths

Use extra caution around `execution.py`, `risk_engine.py`, `guardian.py`, `auto_trader.py`,
`mt5_executor.py`, `bybit_executor.py`, broker feeds, freshness checks, and config gates.
Prefer fail-closed behavior for missing, stale, malformed, delayed, duplicated, or ambiguous data.

## Tests

- Default: `pytest path/to/test_file.py -q` or `pytest path/to/test_file.py::test_name -q`.
- Audits/reviews: inspect tests but do not run pytest until after applying a fix.
- Completion claims require fresh command output.

## Final Response Contract

Always include: summary, files inspected/changed, tests/checks run, and remaining risks or `not verified` areas.
