---
name: athena-audit
description: >
  Full nuclear-grade audit methodology for Athena/Sentinel Pro. Use this skill whenever
  the user asks for an audit, code review, bug-finding task, safety review, contract check,
  or any "trace the full pipeline" analysis. Also trigger for "find all bugs", "check if
  X is correct", "verify the handoff from engine to execution", or any producer-to-consumer
  tracing task. Do not skip this skill for any audit-type request — even quick ones benefit
  from the fail-closed and payload contract checks.
---

# Athena Audit Methodology

## Before You Start

Do not stop at the happy path. Do not patch before producing a bug list unless explicitly asked. Trace producer-to-consumer contracts. Verify missing, false, null, stale, malformed, and empty payload cases separately.

## Audit Completion Contract

Before calling any audit complete, verify all of the following:

- Every finding has: exact file + line number, reproduction path, severity, and proposed fix
- Confirmed bugs are separated from suspicious patterns
- All payload boundary checks are verified (not assumed)
- Fail-closed behavior is proven, not inferred

## Mandatory Contract Checks

For every audit, run ALL of these:

### 1. Fail-Closed Defaults
If a gate, confirmation, freshness check, score pass, RR pass, or approval field is absent, verify the code rejects by default.
- Flag any helper returning `True`, `trade`, `passed`, or `execute` from missing data
- Check: missing key, explicit `False`, `None`, empty dict/list — each separately

### 2. Payload Handoff Contracts
Trace scanner/backtest/engine output into:
- `execution.py` → `auto_trader.py` → `risk_engine.py` → broker executors → monitors → API payloads → UI consumers
- Confirm required fields are always present at each boundary
- Confirm field names and types are preserved across boundaries (timeframe key, score group, style key)

### 3. Boolean Presence vs Truth
Check code using `"key" in payload`, `payload.get(...)`, fallback `{}`, or default `True`.
Verify omission, explicit `False`, `None`, and empty dict/list behavior separately.

### 4. Mode Dispatch and Early Returns
For config modes (`tp_mode`, backtest/live toggles, structure-gate switches):
- Prove which branches are skipped by early returns
- Verify suppressed branches are intentional, not accidental

### 5. Live vs Backtest Parity
Compare exact SL/TP, ATR source, score group, session, volume, and feed paths used by live/paper execution against backtests. Call out intentional divergence separately from bugs.

### 6. Execution Safety Handoff
Before saying an engine is safe, inspect:
- Execution guard → level preservation → broker adapter → monitor → audit/log write path
- Engine-internal correctness is not enough

### 7. Negative-Case Tests
Recommend or add focused tests for:
- Omitted required flags
- Failed confirmations
- Stale candles
- Zero/invalid ATR
- Missing broker symbols
- Missing execution levels
- Rejected broker SL/TP updates

If any check was not performed, label that section "not verified".

## Severity Labels

- **CRITICAL:** Fail-open gate, execution bypass, or real-money risk
- **HIGH:** Wrong output in production, silent data corruption, config key ignored
- **MEDIUM:** Edge case with incorrect behavior, test coverage gap
- **LOW:** Code quality, dead code, missing log
