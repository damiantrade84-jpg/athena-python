---
name: athena-audit
description: Use for explicit full audit, bug hunt, strict findings report, execution-safety review, live/backtest parity review, producer-to-consumer contract review, or end-to-end trace across Athena engines, execution, UI, feeds, or config. Do not use for small fixes, single-file edits, quick explanations, or routine feature work.
---

# Athena audit

Manual, evidence-first audit. Follow repo `AGENTS.md` safety rules.

## Before starting

1. Restate the user's exact question and scope (engine/surface/files).
2. Read `references/invariants.md` and `docs/codex-code-review-discipline.md`.
3. For verification or "nothing missed" reviews, also follow `.agents/skills/athena-anti-miss-review/SKILL.md`.
4. Build the required **coverage map** before any verdict (see discipline doc).

## Procedure

1. Trace producer → consumer on the real execution path (entry point → output contract).
2. Check missing, null, false, stale, malformed, empty, and wrong-type cases separately.
3. Verify fail-closed behavior with code evidence, not assumptions.
4. Run the **negative-check pass** from `docs/codex-code-review-discipline.md`.
5. Separate confirmed bugs from suspicious patterns.
6. Propose minimal fixes only after findings; recommend focused regression tests.

Do not say "looks good", "no issues found", or "implemented correctly" without traced coverage. If incomplete, say **"Coverage incomplete"** and list missing areas.

## Output per finding

severity (CRITICAL/HIGH/MEDIUM/LOW), file path, function/class/route/component, line anchor, why it is real, expected behavior, minimal fix, regression test required.

Label any unchecked mandatory area as `not verified`.

## Boundaries

- Do not patch before findings unless the user requests patch-first work.
- Do not broaden a targeted audit into a whole-repo audit.
- Do not load `tasks/`, old audits, or generated artifacts unless named by the user.
- Do not change thresholds or strategy semantics during an audit unless explicitly requested.
