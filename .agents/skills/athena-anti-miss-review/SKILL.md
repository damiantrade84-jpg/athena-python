---
name: athena-anti-miss-review
description: Use when the user asks for audit, review, verification, shipped-change validation, missed issue detection, regression check, or "make sure nothing was missed". Do not use for implementation, quick explanations, or single-file fixes without a review ask.
---

# Anti-miss review protocol

Follow repo `AGENTS.md` safety rules, **Test & token budget**, and `docs/codex-code-review-discipline.md`. Use this skill for reviews where missing a defect is costly.

## Mandatory first pass

Before producing findings, create a **review map**:

| Area | Entry point | Files inspected | Tests inspected | Status |
|---|---|---|---|---|

Statuses: **COVERED**, **PARTIAL**, **NOT REVIEWED**, **BLOCKED**

Do not produce a final pass/fail verdict until the review map exists.

## Parallel lane review (multi-surface audits)

When the change touches more than one engine, UI/API, or test surface, **spawn one subagent per in-scope lane only** (run lanes in parallel when the tool supports it). Do not spawn all five lanes for single-file fixes.

1. **Engine A** — factor/forex scoring, Engine A scan payloads
2. **Engine B** — market structure, zones, Engine B overlays
3. **Engine D / Scalp Workbench** — scalp engine, workbench UI/API
4. **UI / API contracts** — routes, React consumers, review payloads
5. **Tests / imports** — **read** test files and map coverage; forbidden `athena.py` imports, stale assertions. **Do not run pytest** during review — run only after a fix, one file per fix.

Lane entry points and chain expectations: **`references/review-lanes.md`**.

Each subagent returns **coverage**, **findings**, and **not-reviewed areas**. **Consolidate only after all lanes return** (or are BLOCKED with reason). Merge maps, run global search/adversarial passes (`rg`, not pytest), then emit final output.

For single-lane scoped reviews, one reviewer may cover the lane — still fill the review map and list NOT REVIEWED explicitly.

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

1. Another code path bypasses this fix?
2. UI calls a different endpoint than the patched backend?
3. Old fallback still active?
4. Duplicate config/env key overrides the intended one?
5. Test passes while production route remains broken?
6. Comments claim behavior code does not implement?
7. Errors swallowed and converted into false success?
8. Patch fixes one engine while another still uses old logic?

Also run the **negative-check pass** from `docs/codex-code-review-discipline.md` (duplicate provider/model paths, hardcoded thresholds/gates, stale fallbacks, swallowed exceptions, bypassed risk/guardian checks, UI/backend drift, stale tests, dead config/env keys).

## Engine logic chain (per lane)

When a lane includes engine logic, inspect the full chain with current source evidence:

- data provider/source
- candle policy
- scoring/confidence calculation
- gate/blocker logic
- SL/TP/RR calculation
- payload/output contract
- UI/API/AI review consumer
- tests

## Source of truth

Do not use memory files, prior summaries, old audit notes, or comments alone as proof. **Current source files and current tests** are the source of truth.

If coverage is incomplete, state **"Coverage incomplete"** and list missing files/paths/lanes.

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

Do not use **PASS** if any required execution path or required lane was not inspected.

## Boundaries

- Do not patch during review unless the user requests fix-first work.
- Do not load `tasks/`, old audits, or generated artifacts unless named by the user.
- Do not change thresholds or strategy semantics during a review unless explicitly requested.
- **No pytest during review/audit phase.** Run pytest only after applying a fix — at most **one** test file per fix (`pytest path/to/test_file.py -q`).
- Do not run `pytest tests/`, broad `-k` globs across modules, or full frontend test suites unless the user explicitly requests.
