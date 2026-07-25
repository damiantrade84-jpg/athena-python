---
name: athena-audit
description: Manual-only full audit, bug hunt, strict findings, execution-safety review, or end-to-end trace. Invoke explicitly as $athena-audit; never use for ordinary fixes or routine reviews.
---

# Athena formal audit

Use only when explicitly invoked. Do not invoke or chain another skill unless the user explicitly names it.

## Scope

1. State the exact question, in-scope surface, and files or execution path to verify.
2. Read current source and relevant tests only. Do not load historical audits, plans, logs, generated artifacts, or unrelated engines.
3. Expand scope only when evidence shows another path is necessary.

## Procedure

- Trace the relevant entry point to the requested output or safety boundary.
- Check missing, false, null, stale, malformed, empty, and wrong-type cases only where they can affect that path.
- Separate confirmed defects from unverified suspicion.
- Do not patch unless the user requested fixes.
- Do not spawn subagents unless the user explicitly requested a multi-lane audit.

## Verification budget

- No pytest during a read-only audit.
- After a requested fix, run one smallest relevant test command by default.
- Never run a full suite, broad grep inventory, backtest matrix, live service, or broker action unless explicitly requested.

## Output

For each confirmed finding: severity, file/anchor, evidence path, impact, minimal fix, and one focused regression test recommendation. State material areas not verified. Use a coverage map or PASS/FAIL verdict only when the user explicitly requested a formal audit verdict.
