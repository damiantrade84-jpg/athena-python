---
name: athena-ui-chart-review
description: Manual-only review of a named chart UI, overlay, Vision, or review-payload contract. Invoke explicitly as $athena-ui-chart-review; never auto-run for ordinary frontend edits.
---

# Athena UI and chart review

Use only when explicitly invoked for a named UI component, API route, overlay, Vision payload, or chart-review contract. Do not load parity or anti-miss skills unless the user names them.

- Trace only the relevant API route/payload builder and React consumer.
- Server-assembled score, threshold, ATR, RR, and diagnostics remain authoritative.
- Chart AI/Vision is advisory and must not connect to execution.
- Preserve Engine B and Engine D surface boundaries and exact parser tokens when those files are in scope.
- Avoid legacy TradingView work unless the task explicitly targets it.

After a requested fix, run one targeted frontend check that best verifies the change. Do not automatically run typecheck, tests, and build together, and do not run unrelated UI suites.
