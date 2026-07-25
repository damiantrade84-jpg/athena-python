---
name: athena-test-repair
description: Manual-only focused repair of a named pytest failure, import error, fixture, or isolation problem. Invoke explicitly as $athena-test-repair; never run broad test suites.
---

# Athena test repair

Use only when explicitly invoked for a named failing test, import error, fixture issue, or isolation problem.

1. Reproduce with the smallest failing test case.
2. Read that test and the directly tested production path.
3. Patch minimally; do not weaken assertions or change production thresholds merely to make tests green.
4. Run the same smallest command once after the patch.
5. Report unrelated failures without expanding scope.

Never run the full suite or a multi-file batch unless the user explicitly requests it. Do not import `athena.py` in tests. Use WAL mode and a 15-second timeout for new SQLite test helpers where applicable.
