---
name: athena-audit
description: >-
  Manual-only full audit, bug hunt, strict findings, execution-safety review,
  live/backtest parity review, producer-to-consumer contract review, or end-to-end trace.
  Do not use for ordinary fixes, routine reviews, explanations, or narrow edits.
disable-model-invocation: true
---

# Athena formal audit

Invoke only when the user explicitly requests `/athena-audit` or the complete formal audit workflow.

1. Define the exact question, surface, path, and material exclusions.
2. Read current source and relevant tests only; do not load Codex instructions, historical audits, plans, logs, generated artifacts, or unrelated engines.
3. Trace the relevant entry point to the requested output or safety boundary.
4. Check malformed/missing/stale cases only where they can affect that path.
5. Separate confirmed defects from unverified suspicion.
6. Do not patch unless requested and do not use a subagent unless the user explicitly asks for parallel review.

No pytest during a read-only audit. After a requested fix, run one smallest relevant test command by default. Never run full suites, broad globs, long backtests, live services, or broker actions unless explicitly requested.

For each confirmed finding, report severity, file/anchor, evidence path, impact, minimal fix, focused regression test, and material areas not verified. Use a coverage map or PASS/FAIL verdict only when the user explicitly requests a formal verdict.
