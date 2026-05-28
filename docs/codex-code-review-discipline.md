# Codex code review discipline (Athena)

Codex must not perform sampled, surface-level, or summary-only reviews on this repo.

For every audit or code review, build a **coverage map** before giving a verdict.

## Required coverage map

Include in every audit/review output:

- entry points inspected
- caller/callee path traced
- config/env keys inspected
- tests inspected
- UI/API contract inspected (when relevant)
- files explicitly **not** inspected
- assumptions and unknowns

Do not say "looks good", "no issues found", or "implemented correctly" unless the relevant execution path was traced from entry point to output contract.

If coverage is incomplete, say **"Coverage incomplete"** and list the exact missing areas.

## Finding format

Every finding must include:

- severity
- exact file path
- function / class / route / component
- line or nearby anchor
- why it is a real issue
- expected behavior
- minimal fix direction
- regression test required

## Negative-check pass

Every review must include a negative-check pass:

- duplicate provider/model paths
- hardcoded thresholds/gates
- stale fallback logic
- swallowed exceptions
- bypassed risk/guardian checks
- UI/backend contract drift
- tests that assert stale behavior
- dead config keys or env keys

## Engine logic chain

When reviewing Athena engine logic, inspect the full relevant chain:

- data provider/source
- candle policy
- scoring/confidence calculation
- gate/blocker logic
- SL/TP/RR calculation
- payload/output contract
- UI/API/AI review consumer
- tests

## Parallel lane review

For multi-surface audits, spawn one subagent per lane (Engine A, Engine B, Engine D/Scalp Workbench, UI/API contracts, tests/imports). Each lane returns coverage, findings, and not-reviewed areas. Consolidate only after all lanes return. Lane details: `.agents/skills/athena-anti-miss-review/references/review-lanes.md`.

## Source of truth

Do not rely on memory, prior summaries, old audit notes, or comments in code as proof. **Current source files and current tests** are the source of truth.

See also: `AGENTS.md`, `.agents/skills/athena-audit/SKILL.md`, `.agents/skills/athena-anti-miss-review/SKILL.md`, `docs/agent-operating-guide.md`.
