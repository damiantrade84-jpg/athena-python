# Codex agent guidance

How Athena repo instructions are split for **Codex (app)**, **Codex CLI**, **Cursor**, and Claude Code.

## Layers

| Layer | Path | Role |
|-------|------|------|
| Always-on rules | `AGENTS.md` | Repo-wide safety, workflow, engine boundaries, output contract |
| Manual workflows | `.agents/skills/<name>/SKILL.md` | Explicit audits, parity, research, UI review, test repair, and execution review |
| Large plans | `PLANS.md` (optional, repo root) | Multi-step refactors or audit plans; not loaded by default |
| Tool access | `.codex/config.toml`, MCP servers | Databases, browser, docs — **not** behavior rules (Codex/CLI only) |
| Memory policy | `docs/codex-memory-policy.md` | Repo vs `~/.codex/memories/`; archive rules — read when stale context appears |
| Formal audit discipline | `docs/codex-code-review-discipline.md` | Read only for explicitly requested formal audits or strict verdicts |
| Deep reference | `docs/agent-operating-guide.md` | Repo map, engine tables, Vision tokens — read when needed, not at startup |

**Not instruction files:** `memory.md`, `memories.md`, `MEMORY.md`, or `docs/archive/*memory*` — archive only. See `docs/codex-memory-policy.md`.

## Multi-tool setup (Codex app, Codex CLI, Cursor)

Use the **same repo files** in all three. Different models do not change which files apply — only how reliably they follow them.

### What loads automatically

| Surface | Always-on | On-demand skills | Extra |
|---------|-----------|------------------|-------|
| **Codex app** | `AGENTS.md` (+ nested `AGENTS.md` by cwd) | `.agents/skills/` metadata at session start; all Athena skills require explicit `$skill` invocation | `.codex/config.toml` MCP |
| **Codex CLI** | Same as app | Same | Same; start from repo root: `cd` repo → `codex` |
| **Cursor** | `AGENTS.md` + `.cursor/rules/*.mdc` | Name skill in prompt or `@` skill file (do not assume auto-invoke) | Cursor MCP is separate from `.codex/config.toml` |

Skills are discovered at **`$REPO_ROOT/.agents/skills/<name>/SKILL.md`** (no extra path setting required). Validate after changes:

```bash
python tools/sync_agent_docs.py
```

### Session checklist

1. Open **`c:\dev\athena-python`** (repo root), not a random subfolder.
2. Rely on **`AGENTS.md`** for safety and workflow — do not paste long prompts per model.
3. Skills are manual-only. Invoke one explicitly with `$skill-name` only when its full workflow is needed.
4. Do **not** load `docs/agent-operating-guide.md` at startup; cite a section or `@` it only when needed.
5. For multi-session work, add **`PLANS.md`** at repo root and reference it explicitly (`Follow PLANS.md step 2 only`).

### Cursor-specific

- **`.cursor/rules/`** add guardrails on top of `AGENTS.md`; they do not replace it.
- Prefer **`@.agents/skills/<name>/SKILL.md`** when you want Codex-style skill discipline in Cursor.
- Working under `tests/`, `static/react-app/`, or `athena_research/`? Nested **`AGENTS.md`** in those folders applies additional scope rules.

### Codex CLI / app verification

In a new thread: *List repo skills you see and their descriptions.* — expect all eight Athena skills, all explicit-only.

### Claude Code (if used in this repo)

- Startup: **`CLAUDE.md`** + **`.claude/skills/`** only.
- Do not cross-load **`AGENTS.md`** in Claude sessions unless maintaining Codex docs.
- Only **`athena-audit`** is required under `.claude/skills/` today; copy other `.agents/skills/` folders to `.claude/` only if you want Claude parity.

## Codex vs Claude vs Cursor (summary)

- **Codex app / CLI / Cursor:** `AGENTS.md` + `.agents/skills/`
- **Claude Code:** `CLAUDE.md` + `.claude/skills/`
- **Cursor-only:** `.cursor/rules/*.mdc`

## Skills

Skills live at `.agents/skills/<skill-name>/SKILL.md`.

Each skill has YAML frontmatter with **`name`** and **`description`** only, plus `agents/openai.yaml` with `allow_implicit_invocation: false`. Skills must be invoked explicitly; descriptions document scope and boundaries.

Long domain detail belongs in `references/` under the skill folder, not in root `AGENTS.md`.

| Skill | Use when |
|-------|----------|
| `athena-audit` | Full audit, bug hunt, strict findings, e2e trace |
| `athena-anti-miss-review` | Verification, shipped-change validation, missed-issue detection, regression check, parallel lane reviews |
| `athena-engine-parity` | Live/backtest/chart/engine payload parity |
| `athena-cross-surface-parity` | Config → API → UI closed-loop parity |
| `athena-research-lab` | Research Lab, vectorbt, calibration |
| `athena-ui-chart-review` | React/native chart, Vision/review UI |
| `athena-test-repair` | Focused pytest repair |
| `athena-risk-execution` | Execution, risk, guardian, brokers |

## PLANS.md

Optional at repo root only when the user requests a durable plan or the work is a migration, architecture change, new subsystem, significant refactor, or multi-stage change spanning more than five production files. **Not auto-loaded.**

## agent-operating-guide.md

~20 KiB reference — **not startup context.** Use section-scoped reads only (e.g. Engine B table, Vision tokens). Trim over time by moving stable detail into skill `references/`.

## Validation

```bash
python tools/sync_agent_docs.py
```

## Maintaining docs

- Keep **`AGENTS.md`** under ~8 KiB: rules and pointers only.
- Update **`docs/agent-operating-guide.md`** when the repo map or invariant tables change.
- Add or extend a **skill** when a workflow is repeatable; do not grow `AGENTS.md` with audit checklists or engine scoring tables.
