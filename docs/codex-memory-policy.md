# Codex memory policy (Athena)

How Codex, Codex CLI, and Cursor should load instructions for this repo — and what to do when stale memory appears.

## Active sources (repo)

| Source | Role |
|--------|------|
| **`AGENTS.md`** (repo root) | Always-on repo rules for Codex/Cursor |
| **`.agents/skills/<name>/SKILL.md`** | On-demand workflows when the task matches |
| **Nested `AGENTS.md`** | Extra scope under `tests/`, `static/react-app/`, `athena_research/` |

**`.codex/config.toml`** in this repo configures MCP servers only — not behavior rules. It must not list `memory.md` or `memories.md` as instruction fallbacks.

## Not active guidance (repo)

| Path | Status |
|------|--------|
| `memory.md`, `memories.md`, `MEMORY.md` at repo root | **Disallowed** — archive under `docs/archive/` if you need to keep notes |
| `docs/archive/*memory*.md` | **Archived** — historical human notes only |
| `AGENT.md` at repo root | **Disallowed** — use `AGENTS.md` only (no duplicate startup file) |
| `CLAUDE.md` / `.claude/skills/` | Claude Code only — not Codex startup context |
| `docs/agent-operating-guide.md` | Deep reference — read on demand, not startup |

If archived memory conflicts with **`AGENTS.md`**, **`AGENTS.md` wins**. Do not infer current architecture from memory files without reading current source.

## Local Codex memory (outside repo)

Codex can inject **generated** memories from the user profile, independent of this repository:

- **`~/.codex/memories/`** (Windows: `%USERPROFILE%\.codex\memories`)

These files are not version-controlled. They may contain outdated project notes. Cleaning or disabling them does not change repo code.

### Windows: inspect local files

```text
explorer %USERPROFILE%\.codex\memories
notepad %USERPROFILE%\.codex\config.toml
```

### Disable memory injection (user-level only)

Edit **`%USERPROFILE%\.codex\config.toml`** (do **not** commit this to the repo):

```toml
[features]
memories = true

[memories]
use_memories = false
generate_memories = false
```

Restart Codex after changing user config.

## Archiving stale repo notes

If you find `memory.md` or `memories.md` at repo root:

1. Move to `docs/archive/memory.archive.md` or `docs/archive/memories.archive.md`.
2. Add a header: archived, not active Codex guidance, superseded by `AGENTS.md`.
3. Remove the root copy.
4. Run `python tools/sync_agent_docs.py`.

## Validation

```bash
python tools/sync_agent_docs.py
```

See also `docs/codex-guidance.md` for the full Codex/Cursor/Claude split.
