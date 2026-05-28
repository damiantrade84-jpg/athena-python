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

## Output contract

Every final response includes: summary, files inspected/changed, tests/checks run, remaining risks or `not verified` areas.

## Repo skills (on demand)

Discover under `.agents/skills/<name>/SKILL.md`. Load a skill only when the task matches its description; do not preload all skills.

| Skill | Invoke for |
|-------|------------|
| `athena-audit` | Full audit, bug hunt, strict findings, e2e trace, producer-to-consumer contract review |
| `athena-engine-parity` | Live/backtest or chart parity across engines, candles, ATR, scoring drift, UI payloads |
| `athena-research-lab` | `athena_research/`, vectorbt lab, backtest discovery, indicator calibration |
| `athena-ui-chart-review` | React/native chart UI, chart AI review payloads, Vision (not execution) |
| `athena-test-repair` | Targeted pytest repair for touched behavior |
| `athena-risk-execution` | `execution.py`, `risk_engine.py`, `guardian.py`, `auto_trader.py`, broker executors |

## Nested scope guides

- `static/react-app/AGENTS.md` — frontend / native chart
- `athena_research/AGENTS.md` — Research Lab
- `tests/AGENTS.md` — pytest conventions

## Reference (not startup context)

- `docs/codex-guidance.md` — AGENTS vs skills vs PLANS vs MCP
- `docs/agent-operating-guide.md` — detailed repo map and safety tables

## Tool boundaries

- **Codex app, Codex CLI, Cursor:** same `AGENTS.md` + `.agents/skills/`; start sessions at repo root. See `docs/codex-guidance.md` (multi-tool setup).
- **Cursor:** also loads `.cursor/rules/*.mdc`; name or `@` a skill file when you want a specific workflow.
- **Codex MCP:** `.codex/config.toml` is for Codex/CLI only — not Cursor MCP.
- **`CLAUDE.md`** / **`.claude/skills/`** are Claude Code only unless maintaining Claude docs.
