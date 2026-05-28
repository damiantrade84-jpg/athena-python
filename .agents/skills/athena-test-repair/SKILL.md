---
name: athena-test-repair
description: Use when fixing pytest failures, import errors, fixture setup, or test isolation issues tied to the current change. Do not use for full audit, changing production thresholds to green tests, or running the entire test suite unless the user explicitly requests it.
---

# Athena test repair

Focused test fixes for the task at hand.

## Rules

- Never import `athena.py` in tests.
- Fix tests to match intended production contract; do not weaken safety assertions.
- Run only affected files: `pytest path/to/test.py -q` or `::test_name`.
- SQLite: WAL, 15s timeout in new DB helpers.

## Steps

1. Reproduce with the smallest failing command.
2. Read the failing test and the production module under test.
3. Patch minimally; add a regression test when fixing a real bug.
4. Report unrelated failures separately without broad fixes.

Read `tests/AGENTS.md` for folder conventions.
