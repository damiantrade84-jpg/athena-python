---
name: athena-risk-execution
description: Manual-only deep review of a named execution, risk, freshness, kill-switch, sizing, SL/TP, or broker-path change. Invoke explicitly as $athena-risk-execution; root safety rules cover ordinary edits.
---

# Athena risk and execution

Use only when explicitly invoked for a deep execution-path review. Root `AGENTS.md` safety rules still apply to every ordinary edit of execution files.

Review only the modified path: signal entry → relevant risk/freshness/kill gate → sizing or SL/TP branch → broker/paper handoff → response handling.

Confirm where applicable:

- missing, stale, null, false, empty, malformed, or wrong-type safety fields fail closed;
- SL/TP direction, precision, tighten-only rules, and broker constraints remain valid;
- paper/live separation, kill switch, freshness, and deterministic approval remain reachable;
- AI cannot approve or execute.

Do not invoke audit or anti-miss skills unless the user names them. After a requested fix, run one focused test command by default; a second is allowed only for a distinct changed safety boundary.
