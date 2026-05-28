# AGENTS.md — Tests

Scope: `tests/` and test helpers used by pytest.

## Rules

- **Never import `athena.py`** in tests. Use `athena_app/` modules and fixtures.
- SQLite in tests: WAL mode, 15s timeout where applicable.
- Run **only** tests for touched behavior: `pytest path/to/test_file.py -q` or `::test_name`.
- Do not weaken assertions or change production thresholds to make tests pass.
- If a failure is unrelated to the task, isolate and report it; do not fix broadly.

## Skills

- `athena-test-repair` — focused pytest/import/fixture repair for the current change
- `athena-audit` — only when the user asked for a full audit, not for routine test fixes

Parent rules: repo root `AGENTS.md`.
