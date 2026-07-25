---
name: athena-engine-parity
description: Manual-only investigation of one named live, scan, backtest, chart, or payload parity issue. Invoke explicitly as $athena-engine-parity; never use for unrelated fixes or threshold tuning.
---

# Athena engine parity

Use only when explicitly invoked for a named engine, route, and parity question. Do not inspect every engine or load another skill unless the user explicitly requests it.

## Workflow

1. Identify one engine and one route: live scan, backtest, API, UI, or AI context.
2. Trace only the relevant provider/cache, candle policy, engine calculation, payload, and consumer.
3. Inspect only config keys and tests that control the suspected drift.
4. Check one alternate route or fallback if evidence shows it can bypass the intended path.
5. Document the precise drift before patching.

## Budget

- No default repository-wide grep, lane map, subagents, or full engine chain outside the named issue.
- No pytest during read-only investigation.
- After a fix, run one smallest relevant test command.
- No threshold changes, full suites, or backtest matrices unless explicitly requested.

## Output

Report evidence, expected versus actual behavior, minimal fix, focused regression test, and material unverified areas.
