---
name: athena-audit
description: Use for explicit full audit, bug hunt, strict findings report, execution-safety review, live/backtest parity review, producer-to-consumer contract review, or end-to-end trace across Athena engines, execution, UI, feeds, or config. Do not use for small fixes, single-file edits, quick explanations, or routine feature work.
---

# Athena audit

Manual, evidence-first audit. Follow repo `AGENTS.md` safety rules.

## Before starting

1. Restate the user's exact question and scope (engine/surface/files).
2. Read `references/invariants.md` and `docs/codex-code-review-discipline.md`.
3. For verification or "nothing missed" reviews, follow `.agents/skills/athena-anti-miss-review/SKILL.md` (review map, search pass, adversarial pass, parallel lanes when multi-surface).
4. Build the required **coverage map** before any verdict.

## Procedure

1. Trace producer → consumer on the real execution path (entry point → output contract).
2. For multi-engine or cross-surface scope, spawn **parallel lane subagents** per `athena-anti-miss-review/references/review-lanes.md`; consolidate only after all return.
3. Check missing, null, false, stale, malformed, empty, and wrong-type cases separately.
4. Verify fail-closed behavior with code evidence, not assumptions.
5. Run the **negative-check pass** from `docs/codex-code-review-discipline.md`.
6. Separate confirmed bugs from suspicious patterns.
7. Propose minimal fixes only after findings; recommend focused regression tests.

Do not say "looks good", "no issues found", or "implemented correctly" without traced coverage. If incomplete, say **"Coverage incomplete"** and list missing files/paths/lanes.

## Engine chain (when in scope)

Inspect with current source evidence: provider/source → candle policy → scoring/confidence → gates → SL/TP/RR → payload → UI/API/AI consumer → tests.

## Output per finding

severity (CRITICAL/HIGH/MEDIUM/LOW), file path, function/class/route/component, line anchor, why it is real, expected behavior, minimal fix, regression test required.

Label any unchecked mandatory area as `not verified`. End with Coverage / Findings / Verdict when using anti-miss protocol (**PASS** only if required paths inspected).

## Boundaries

- Do not patch before findings unless the user requests patch-first work.
- Do not broaden a targeted audit into a whole-repo audit.
- Do not load `tasks/`, old audits, or generated artifacts unless named by the user.
- Do not change thresholds or strategy semantics during an audit unless explicitly requested.
- Current source and tests are proof — not memory, prior summaries, or comments alone.
