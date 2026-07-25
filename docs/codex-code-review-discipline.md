# Codex formal audit discipline — Athena

This document applies only when the user explicitly invokes a formal audit/review skill or requests a strict end-to-end verdict. It does not apply to ordinary implementation, localized bug fixes, explanations, or routine diff checks.

## Routine review

For a normal fix or localized review, inspect the changed code, its immediate caller/consumer, and the smallest relevant test. Do not create a coverage map, spawn subagents, run generic search templates, or inspect unrelated engines/surfaces.

## Formal audit scope

Before a formal verdict, record:

- exact question and in-scope path;
- files and tests inspected;
- material path intentionally excluded;
- assumptions or blockers that affect the verdict.

A table is optional unless the user requests one. Do not broaden the audit merely to fill a template.

## Findings

Each confirmed finding should include severity, file/anchor, evidence path, impact, minimal fix, and one focused regression test recommendation. Separate confirmed defects from suspicious but unverified patterns.

## Negative check

Perform only checks relevant to the named path, such as an alternate route, stale fallback, duplicate config key, swallowed error, bypassed safety gate, or producer/consumer drift. Do not run a repository-wide checklist by default.

## Parallel review

Use one reviewer by default. Spawn subagents only when the user explicitly requests parallel review and the scope contains independent surfaces that cannot be reviewed efficiently in one path.

## Test budget

No pytest during a read-only audit. After a requested fix, run one smallest relevant test command by default. Never run a full suite, broad `-k` search, multi-file batch, frontend suite, backtest matrix, live service, or broker action unless explicitly requested.

Use `not verified` for material gaps. Issue PASS/FAIL only when the user explicitly asked for a formal verdict.
