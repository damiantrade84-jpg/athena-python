# Claude Code (project)

## MCP (.mcp.json)

Optional servers require environment setup before starting Claude Code:

| Server     | Requirement |
|------------|-------------|
| **github** | `GITHUB_PERSONAL_ACCESS_TOKEN` in your environment (classic PAT with repo/workflow scope as needed). Never commit the token. |
| **playwright** | `npx` on PATH; first browser launch may download Playwright browsers (`npx playwright install` if the MCP logs ask for it). |

Enable servers in `.claude/settings.local.json` via `enabledMcpjsonServers` (list includes server names from `.mcp.json`).

## Hooks (.claude/settings.json)

- **PreToolUse**: warns when editing `execution.py`, `risk_engine.py`, `mt5_executor.py`, `bybit_executor.py`, or `auto_trader.py` (notice only).
- **PostToolUse**: runs `python -m pytest <file> -q --tb=short` after edits to `tests/test_*.py` only.

Hook scripts: [tools/claude_hooks/](../tools/claude_hooks/). Restart Claude Code after changing hook config.
