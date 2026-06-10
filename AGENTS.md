# AGENTS.md — Codex / Cursor

Athena / Sentinel Pro v4: multi-engine trading analysis and execution-support. **Paper-only** unless the user explicitly approves live trading.

## Non-negotiable

- Do not guess architecture. Read current source before claims or patches.
- Smallest safe diff. No unrelated refactors, features, or safety layers unless requested.
- Do not change thresholds, scoring weights, gate behavior, SL/TP logic, or strategy semantics unless explicitly requested.
- Keep **Engine A, B, C, D**, Research Lab, UI, and execution paths separate unless the task requires integration.
- **Engine C** owns A/B agreement, conflict, A-only, and B-only. **Engine A and B must not suppress each other.**
- Never bypass risk, freshness, kill-switch, execution-approval, broker, RR, SL/TP, or audit gates.
- AI is advisory only; it cannot execute, approve orders, mutate config, or override deterministic gates.
- Never read, print, modify, or commit `.env`, secrets, API keys, tokens, or credentials.
- Do not load `tasks/`, old audits, generated logs, or backtest artifacts unless the user names them or the task requires them.

## Workflow

**Before:** identify execution path → read involved files → trace producer → consumer → check existing tests → state minimal change.

**After:** run **targeted** tests only → lint/build only if relevant → review diff for unintended threshold or contract changes.

**Tests:** prefer `pytest path/to/test_file.py -q`. Never import `athena.py` in tests. SQLite: WAL mode, 15s timeout.

**Do not run** full test suite, backtest matrix, long research jobs, live trading, or broker actions unless explicitly requested.

## Test & token budget (all agents)

- **Default:** `pytest path/to/test_file.py -q` or `pytest path/to/test_file.py::test_name -q` — at most **one file** per verification pass unless the user explicitly requests more.
- **Never** run `pytest tests/`, broad `-k` globs across modules, full frontend test suites, or backtest matrix unless explicitly requested or shared infrastructure changed.
- **Audits/reviews:** read test source files; **do not run pytest** during the audit phase. Run pytest only after applying a fix, and only the **one** test file that proves that fix.
- **Parity/chart work:** cite parity tests by name; run **one** parity file if indicators changed — not all files in the checklist.
- **Completion claims:** "tests pass" requires fresh output from the targeted command above — not a full-suite run.

## Output contract

Every final response includes: summary, files inspected/changed, tests/checks run, remaining risks or `not verified` areas.

## Code review discipline

No sampled, surface-level, or summary-only audits. Build a **coverage map** before any verdict (entry points, caller/callee path, config/env keys, tests, UI/API contract when relevant, files not inspected, assumptions/unknowns).

Do not say "looks good", "no issues found", or "implemented correctly" without tracing entry point → output contract. If coverage is incomplete, say **"Coverage incomplete"** and list missing files/paths.

Every finding: severity, file path, function/class/route/component, line anchor, why it is real, expected behavior, minimal fix, regression test required. Current source and tests are proof — not memory, old audits, prior summaries, or comments.

Multi-surface audits (only when the user explicitly asks for audit/review/verification): spawn **parallel lane subagents** scoped to the change (Engine A, Engine B, Engine D/Scalp Workbench, UI/API, tests/imports); each returns coverage, findings, and not-reviewed areas; **consolidate only after all return**. Do not spawn all lanes for single-file fixes. Search pass, adversarial pass, and verdict rules: **`athena-anti-miss-review`**. Summary checklist: **`docs/codex-code-review-discipline.md`**. Audit skills inherit the **Test & token budget** above.

## Repo skills (on demand)

Discover under `.agents/skills/<name>/SKILL.md`. Load a skill only when the task matches its description; do not preload all skills.

| Skill | Invoke for |
|-------|------------|
| `athena-audit` | Full audit, bug hunt, strict findings, e2e trace, producer-to-consumer contract review (inherits Test & token budget) |
| `athena-anti-miss-review` | Audit, verification, shipped-change validation, missed-issue detection, regression check, "nothing missed" (inherits Test & token budget) |
| `athena-engine-parity` | Live/backtest or chart parity across engines, candles, ATR, scoring drift, UI payloads |
| `athena-cross-surface-parity` | Closed-loop audit: config → resolver → API → UI → tests; catch sent-but-unused fields, hardcoded period drift, masked parity tests (Engine A ↔ chart ↔ backtest) |
| `athena-research-lab` | `athena_research/`, vectorbt lab, backtest discovery, indicator calibration |
| `athena-ui-chart-review` | React/native chart UI, chart AI review payloads, Vision (not execution) |
| `athena-test-repair` | Targeted pytest repair for touched behavior |
| `athena-risk-execution` | `execution.py`, `risk_engine.py`, `guardian.py`, `auto_trader.py`, broker executors |

## Chart AI review

Engine A playbook (`ai_playbooks/engine_a_playbook.py`) drives indicator/strategy usage; entry-timing downgrades must be evidence-based (price-vs-EMA distance in ATR, RSI/ADX; VWAP extension is crypto-only). Advisory only — never connects to execution.

## Nested scope guides

- `static/react-app/AGENTS.md` — frontend / native chart
- `athena_research/AGENTS.md` — Research Lab
- `tests/AGENTS.md` — pytest conventions

## Memory and instruction source of truth

- **AGENTS.md** is the active Codex/Cursor repo instruction file.
- **`.agents/skills/<name>/SKILL.md`** files are task-specific workflows — load only when the task matches.
- **`memory.md`**, **`memories.md`**, **`MEMORY.md`**, and **`docs/archive/*memory*`** are historical notes only — not active rules.
- Do not treat archived memory files as active guidance. If old memory conflicts with **AGENTS.md**, **AGENTS.md** wins.
- Do not infer current Athena architecture from memory files without inspecting current source files.
- Local Codex-generated memory lives outside the repo (`~/.codex/memories/`). See **`docs/codex-memory-policy.md`** if stale context persists.

## Reference (not startup context)

- `docs/codex-guidance.md` — AGENTS vs skills vs PLANS vs MCP
- `docs/codex-code-review-discipline.md` — coverage map, finding format, negative-check pass
- `docs/codex-memory-policy.md` — repo vs local Codex memory; troubleshooting stale context
- `docs/agent-operating-guide.md` — detailed repo map and safety tables

## Tool boundaries

- **Codex app, Codex CLI, Cursor:** same `AGENTS.md` + `.agents/skills/`; start sessions at repo root. See `docs/codex-guidance.md` (multi-tool setup).
- **Cursor:** also loads `.cursor/rules/*.mdc`; name or `@` a skill file when you want a specific workflow.
- **Codex MCP:** `.codex/config.toml` is for Codex/CLI only — not Cursor MCP.
- **`CLAUDE.md`** / **`.claude/skills/`** are Claude Code only unless maintaining Claude docs.
