# Claude Code (project)

## Context hygiene

- **`.claudeignore`** (repo root) excludes runtime DBs/logs, research artifacts, `tasks/`, and heavy `docs/` subtrees from default Claude Code context. Track those paths in git normally; ignore is for token/latency only.
- **Agent guides:** root `CLAUDE.md` is generated from [`docs/claude-code-guide.md`](../docs/claude-code-guide.md); edit that file and run `python tools/sync_agent_docs.py`. **`AGENTS.md` is listed in `.claudeignore`** so Claude Code does not ingest it—it is for Cursor/Codex only (`AGENTS.md` mirrors `docs/agent-operating-guide.md`). For full detail in Claude Code, read `docs/agent-operating-guide.md` when required.

## MCP (.mcp.json)

Optional servers require environment setup before starting Claude Code:

| Server        | Requirement |
|---------------|-------------|
| **playwright** | `npx` on PATH; first browser launch may download Playwright browsers (`npx playwright install` if the MCP logs ask for it). |

Enable servers in `.claude/settings.local.json` via `enabledMcpjsonServers` (list includes server names from `.mcp.json`).

## Hooks (.claude/settings.json)

- **PreToolUse**: warns when editing `execution.py`, `risk_engine.py`, `mt5_executor.py`, `bybit_executor.py`, or `auto_trader.py` (notice only).
- **PostToolUse**: invokes [tools/claude_hooks/post_tooluse_pytest_tests.py](../tools/claude_hooks/post_tooluse_pytest_tests.py) on every Write|Edit; the script **suppresses output** unless `file_path` is `tests/test_*.py` (case-insensitive path match), then runs `python -m pytest <file> -q --tb=line --disable-warnings`. On success: a short one-line summary; on failure: at most **50 lines** of stdout/stderr (tail, with an omitted-line prefix when truncated).

Hook scripts: [tools/claude_hooks/](../tools/claude_hooks/). Restart Claude Code after changing hook config.
