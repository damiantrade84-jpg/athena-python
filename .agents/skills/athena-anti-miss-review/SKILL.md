---
name: athena-anti-miss-review
description: Manual-only shipped-change verification or missed-defect review. Invoke explicitly as $athena-anti-miss-review; never use for ordinary implementation or localized fixes.
---

# Athena anti-miss review

Use only when explicitly invoked for high-cost verification after a change. Do not use for ordinary implementation, explanations, or single-file fixes. Do not chain other skills unless the user names them.

## Workflow

1. Define the exact changed behavior and the smallest relevant path.
2. Inspect the diff, immediate callers/consumers, config keys, and focused tests.
3. Perform one targeted alternate-path search for bypasses, stale fallbacks, duplicate routes, or swallowed errors relevant to the change.
4. For multi-surface work, keep review in one agent by default. Use subagents only when the user explicitly requests parallel review.
5. Stop when the changed path and one meaningful negative case are covered.

## Budget

- No whole-repository search templates.
- No mandatory lane map for a single surface.
- No pytest during read-only review.
- After a requested fix, run one smallest relevant test command.
- No full suites, broad globs, backtest matrices, or unrelated UI/build checks.

## Output

List covered path, confirmed findings, material gaps, and a verdict only when the user asked for one. Do not manufacture findings or broaden scope to avoid `not verified`.
