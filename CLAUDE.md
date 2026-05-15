# CLAUDE.md - Athena / Sentinel Pro v4

Claude Code quick guide. Full operating rules: [docs/agent-operating-guide.md](docs/agent-operating-guide.md). (`AGENTS.md` is Cursor/Codex-only and is omitted from Claude Code context via `.claudeignore`.)

# CLAUDE.md - Athena / Sentinel Pro (Claude Code)

**This file is the ONLY startup context for Claude Code.**
Do NOT load `AGENTS.md` — it is for Cursor/Codex only.

## Core Rules (Always Follow)


- **Never bypass** risk gates, freshness checks, kill-switches, execution approvals, or safety rules.
- AI is **advisory only** — cannot execute trades, approve orders, modify config, or bypass deterministic gates.
- Use **only current files** listed in the Repository Map below.
- Run **only targeted tests** for the changed behavior. Never run full test suites unless explicitly requested.
- Do **not** load `tasks/`, old audit reports, logs, backtest artifacts, or historical findings unless the user names them.
- Engines (A, B, C, D) are independent unless the task requires consensus or cross-engine work.
- **Minimal changes** — only modify what was asked. No unrelated refactoring.
- **Evidence-first** — inspect code before making claims.

## Repository Map (Current)

**Entry:** `athena.py` (Flask monolith — never import in tests)

**Key Modules:**
- Engine A: `scoring.py`, `factor_scoring.py`
- Engine B: `market_structure.py`, `engine_b_ai.py`
- Engine C: `engine_c.py`, `engine_c_ai.py`
- Engine D: `scalp_engine.py`
- Execution: `execution.py`, `auto_trader.py`, `risk_engine.py`, `mt5_executor.py`, `bybit_executor.py`
- Config: `config.yaml` (all thresholds live here)

**AI Safety:**
- All AI components are read-only advisory.
- `ai_agent_safety.validate_ai_chat_response()` must enforce `read_only=true`, `can_execute=false`.

## Before You Finish Any Task

1. Run the smallest relevant test or compile command for the changed code.
2. Report exactly what passed or was not verified.
3. State the next command the user should run if needed.

## When in Doubt

Read the detailed reference: `docs/agent-operating-guide.md`

---

**Last synced:** May 2026
**Purpose:** Keep Claude fast, safe, and grounded in current code only.