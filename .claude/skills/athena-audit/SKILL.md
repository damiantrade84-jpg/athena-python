---
name: athena-audit
disable-model-invocation: true
description: >
  Manual full Athena audit only. Use when explicitly invoked or when the user explicitly
  asks for full-system audit / end-to-end trace.
---

# Athena Audit Methodology

## Before You Start

Manual full-audit skill only. Do not use this for small fixes, quick reviews, or targeted file checks unless the user explicitly invokes the skill or asks for a full-system audit / end-to-end trace.

Do not stop at the happy path. Do not patch before producing a bug list unless explicitly asked. Trace producer-to-consumer contracts. Verify missing, false, null, stale, malformed, and empty payload cases separately.

Do not run full tests unless required by the explicit audit scope.

## Audit Completion Contract

Before calling any audit complete, verify all of the following:

- Every finding has: exact file + line number, reproduction path, severity, and proposed fix.
- Confirmed bugs are separated from suspicious patterns.
- All payload boundary checks are verified, not assumed.
- Fail-closed behavior is proven, not inferred.

## Mandatory Contract Checks

For an explicitly scoped full audit, check:

1. Fail-closed defaults for missing, false, null, empty, malformed, stale, and wrong-type inputs.
2. Payload handoff contracts from scanner/backtest/engine output into execution, monitoring, API payloads, and UI consumers.
3. Boolean presence versus truth, including omitted keys and explicit `False`.
4. Mode dispatch and early returns for config modes and live/backtest toggles.
5. Live versus backtest parity where the explicit audit scope requires it.
6. Execution safety handoff before saying an engine is execution-safe.
7. Negative-case tests or test recommendations for confirmed bugs.

If any check was not performed, label that section `not verified`.

## Severity Labels

- **CRITICAL:** Fail-open gate, execution bypass, or real-money risk.
- **HIGH:** Wrong output in production, silent data corruption, config key ignored.
- **MEDIUM:** Edge case with incorrect behavior, test coverage gap.
- **LOW:** Code quality, dead code, missing log.
