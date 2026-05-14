---
name: athena-audit
description: >
  Manual full Athena audit only. Use when explicitly invoked or when the user explicitly
  asks for full-system audit / end-to-end trace.
---

# Athena Audit Methodology

Manual full-audit skill only. Do not use this for small fixes, quick reviews, or targeted file checks unless the user explicitly invokes the skill or asks for a full-system audit / end-to-end trace.

Do not stop at the happy path. Do not patch before producing a bug list unless explicitly asked. Trace producer-to-consumer contracts. Verify missing, false, null, stale, malformed, and empty payload cases separately.

Do not run full tests unless required by the explicit audit scope.

## Audit Completion Contract

- Every finding has exact file + line number, reproduction path, severity, and proposed fix.
- Confirmed bugs are separated from suspicious patterns.
- All payload boundary checks are verified, not assumed.
- Fail-closed behavior is proven, not inferred.

If any check was not performed, label that section `not verified`.
