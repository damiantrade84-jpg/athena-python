---
name: athena-anti-miss-review
description: Use when the user asks for audit, review, verification, shipped-change validation, missed issue detection, regression check, or "make sure nothing was missed". Do not use for implementation, quick explanations, or single-file fixes without a review ask.
---

# Anti-miss review protocol

Follow repo `AGENTS.md` safety rules and `docs/codex-code-review-discipline.md`. Use this skill for reviews where missing a defect is costly.

## Mandatory first pass

Before producing findings, create a **review map**:

| Area | Entry point | Files inspected | Tests inspected | Status |
|---|---|---|---|---|

Statuses: **COVERED**, **PARTIAL**, **NOT REVIEWED**, **BLOCKED**

Do not produce a final pass/fail verdict until the review map exists.

## Required search pass

Run targeted searches before concluding (adapt scope to the task; do not skip the search pass):

```bash
rg -n "TODO|FIXME|HACK|temporary|fallback|hardcoded|except:|pass$|silent|swallow|deprecated|legacy"
rg -n "provider|model|claude|grok|openai|xai|anthropic|hermes|vision|review"
rg -n "threshold|gate|block|kill_switch|guardian|risk|stale|freshness|ATR|RR|SL|TP"
rg -n "Engine A|Engine B|Engine C|Engine D|scalp|workbench|open-and-review|chart"
```

Exclude `refs/` and other vendored trees unless the task requires them.

## Required adversarial pass

After the normal review, perform a second pass assuming the implementation is wrong. Check:

1. Is there another code path that bypasses this fix?
2. Is the UI calling a different endpoint than the patched backend?
3. Is there an old fallback still active?
4. Is there a duplicate config/env key overriding the intended one?
5. Does a test pass while the production route remains broken?
6. Are comments claiming behavior that code does not implement?
7. Are errors swallowed and converted into false success?
8. Does the patch only fix one engine while another engine still uses old logic?

## Final output format

Every audit result must end with:

### Coverage

- Covered:
- Partially covered:
- Not reviewed:
- Blocked:

### Findings

For each finding:

- Severity:
- File/anchor:
- Execution path:
- Why it matters:
- Minimal fix:
- Regression test:

### Verdict

Use only one:

- **PASS**
- **PASS WITH GAPS**
- **FAIL**
- **BLOCKED**

Do not use **PASS** if any required execution path was not inspected.

## Boundaries

- Do not patch during review unless the user requests fix-first work.
- Do not load `tasks/`, old audits, or generated artifacts unless named by the user.
- Current source files and current tests are proof — not memory, prior summaries, or comments alone.
