---
name: athena-audit
description: >-
  This skill should be used when the user requests a full audit, bug hunt,
  strict findings report, execution-safety review, live/backtest parity review,
  producer-to-consumer contract review, or end-to-end trace of Athena/Sentinel Pro.
  Must be invoked for any task involving systematic code verification across engines,
  safety-gate auditing, cross-engine payload tracing, or fail-closed verification.
  Do not use for small fixes, quick edits, single-file changes, or explanations.
disable-model-invocation: true
---

# Athena Audit Methodology

## Invocation contract

This skill is manual-only.

Use it only when the user explicitly invokes the skill or requests one of:

- full audit
- bug hunt
- end-to-end trace
- strict findings
- execution-safety review
- live/backtest parity review
- producer-to-consumer contract review

Do not use this skill for small fixes, quick edits, simple explanations, or narrow file changes.

## Context loading rules

- Do not load `AGENTS.md`, `.agents/**`, or `.cursor/**`.
- Use `CLAUDE.md` and `.claude/skills/**/SKILL.md` for repo instruction context.
- Read current source files before making claims.
- Do not load `tasks/`, `logs/`, old audits, generated backtest artifacts, historical reports, archived diagnostics, or stale findings unless the user names the artifact or the current audit explicitly requires it.
- Do not rely on historical findings as current truth. Re-verify against current code.
- Do not broaden a targeted audit into a whole-repo audit.
- Do not patch before producing findings unless the user explicitly asks for patch-first work.

## Scope discipline

Before auditing, identify:

1. User's exact question.
2. Engine/surface involved: Engine A, Engine B, Engine C, Engine D, execution, AI review, Vision, Research Lab, UI/API, data feed, config, or tests.
3. Producer-to-consumer path to trace.
4. Files that must be read.
5. Files intentionally not read.

If scope expands, state why.

## Athena invariants

Read `references/invariants.md` for the full list of system invariants. All invariants apply to every audit finding.

## Audit procedure

1. Trace the real execution path end-to-end.
2. Identify every producer and consumer of the payload being audited.
3. Verify missing, false, null, stale, malformed, empty, and wrong-type payload cases separately.
4. Check mode dispatch, early returns, config toggles, live/backtest toggles, and fallback behavior.
5. Verify fail-closed behavior. Do not infer it.
6. Separate confirmed bugs from suspicious patterns.
7. Propose minimal fixes only after evidence is listed.
8. Name **one** regression test per confirmed bug. Run pytest only after a fix — at most one file per fix.

## Test & token budget

- **No pytest during audit phase.** Read test source files for coverage.
- After a fix: `pytest path/to/test_file.py -q` — at most **one** file per fix.
- Never run `pytest tests/`, broad `-k` globs, or full frontend test suites unless the user explicitly requests.
- Scoped audits in `.claude/commands/audit-*.md` inherit this budget.

## Mandatory contract checks

For explicitly scoped full audits, check:

1. Fail-closed defaults for missing, false, null, empty, malformed, stale, and wrong-type inputs.
2. Payload handoff contracts from scanner/backtest/engine output into execution, monitoring, API payloads, and UI consumers.
3. Boolean presence versus truth, including omitted keys and explicit `False`.
4. Mode dispatch and early returns for config modes and live/backtest toggles.
5. Live versus backtest parity where the explicit audit scope requires it.
6. Execution safety handoff before saying an engine is execution-safe.
7. Negative-case tests or test recommendations for confirmed bugs.

If any check was not performed, label it `not verified`.

## Output contract

Every finding must include:

- severity: CRITICAL, HIGH, MEDIUM, LOW
- exact file and line/region
- what the code does
- why it is wrong or risky
- reproduction path or payload path
- minimal recommended fix
- targeted regression test

## Severity labels

- CRITICAL: fail-open gate, execution bypass, real-money risk, or safety blocker.
- HIGH: wrong production output, silent data corruption, config key ignored, live/backtest parity break.
- MEDIUM: edge case with incorrect behavior or missing coverage.
- LOW: code quality, dead code, unclear docs, or low-risk cleanup.
