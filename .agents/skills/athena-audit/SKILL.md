---
name: athena-audit
description: Use for explicit full audit, bug hunt, strict findings report, execution-safety review, live/backtest parity review, producer-to-consumer contract review, or end-to-end trace across Athena engines, execution, UI, feeds, or config. Do not use for small fixes, single-file edits, quick explanations, or routine feature work.
---

# Athena audit

Manual, evidence-first audit. Follow repo `AGENTS.md` safety rules.

## Before starting

1. Restate the user's exact question and scope (engine/surface/files).
2. List files to read and files intentionally skipped.
3. Read `references/invariants.md`.

## Procedure

1. Trace producer → consumer on the real execution path.
2. Check missing, null, false, stale, malformed, empty, and wrong-type cases separately.
3. Verify fail-closed behavior with code evidence, not assumptions.
4. Separate confirmed bugs from suspicious patterns.
5. Propose minimal fixes only after findings; recommend focused regression tests.

## Output per finding

severity (CRITICAL/HIGH/MEDIUM/LOW), file:region, behavior, risk, reproduction path, minimal fix, targeted test.

Label any unchecked mandatory area as `not verified`.

## Boundaries

- Do not patch before findings unless the user requests patch-first work.
- Do not broaden a targeted audit into a whole-repo audit.
- Do not load `tasks/`, old audits, or generated artifacts unless named by the user.
- Do not change thresholds or strategy semantics during an audit unless explicitly requested.
