# Claude Code project config

## Context hygiene

- Claude Code startup context is `CLAUDE.md`.
- Codex/Cursor startup context is `AGENTS.md`.
- Do not import `AGENTS.md` into `CLAUDE.md` unless you intentionally want shared startup context.
- `.claudeignore` is used only for Claude Code context hygiene. It should not be used as the main source of truth for agent behavior.
- Runtime DBs/logs, generated research artifacts, `tasks/`, old audits, and heavy diagnostics should stay out of default context unless explicitly needed.

## Skills

Claude Code project skills live under:

```text
.claude/skills/<skill-name>/SKILL.md
```

Current project skill:

```text
.claude/skills/athena-audit/SKILL.md
```

Invoke manually with:

```text
/athena-audit
```

The `athena-audit` skill is manual-only and should include:

```yaml
disable-model-invocation: true
```

Do not reference skills that do not exist under `.claude/skills/`.

## Subagents

Claude subagents live under:

```text
.claude/agents/
```

Current subagent:

```text
.claude/agents/execution-safety-reviewer.md
```

Use it only for focused review of execution/risk/broker-path diffs.

## MCP

Optional MCP servers require environment setup before starting Claude Code.

| Server | Requirement |
|---|---|
| `playwright` | `npx` on PATH; run `npx playwright install` if Playwright asks for browser setup. |

Enable local MCP servers in `.claude/settings.local.json` only. Do not commit personal local settings.

## Hooks

Shared hook config lives in:

```text
.claude/settings.json
```

Current hooks:

- `PreToolUse`: warns on edits touching execution/risk/broker-sensitive files.
- `PostToolUse`: runs targeted pytest only when a touched path is a `tests/test_*.py` file.

Hook scripts live under:

```text
tools/claude_hooks/
```

Restart Claude Code after changing hook config.
