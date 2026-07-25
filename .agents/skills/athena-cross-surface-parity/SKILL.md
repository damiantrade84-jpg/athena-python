---
name: athena-cross-surface-parity
description: Manual-only parity review for one named config-to-API-to-UI invariant. Invoke explicitly as $athena-cross-surface-parity; never auto-run because a scoring or chart file was touched.
---

# Athena cross-surface parity

Use only when explicitly invoked for a defined parity question. Do not auto-run because a listed file was touched, and do not pair with another skill unless the user names both.

## Workflow

1. Name the exact invariant or field being compared.
2. Trace only its current config/resolver, producer, API payload, consumer, and focused test.
3. Check one non-default group or alternate route when relevant.
4. Search only for hardcoded or duplicate values of the named invariant.
5. Patch minimally if requested.

## Budget

- Load `references/parity-checklist.md` only for a formal full parity audit, not a localized fix.
- No generic coverage map, whole-repo grep, full test suite, or backtest matrix.
- After a fix, run one focused parity test command.

## Output

State the exact drift point, evidence, minimal fix, focused regression test, and any unverified surface.
