# CLAUDE.md - Athena / Sentinel Pro v4

Claude Code quick guide. Full operating rules: [docs/agent-operating-guide.md](docs/agent-operating-guide.md). (`AGENTS.md` is Cursor/Codex-only and is omitted from Claude Code context via `.claudeignore`.)

---

## Context Loading Contract

- Claude Code starts with this `CLAUDE.md` project instruction file only.
- `AGENTS.md` is for Cursor/Codex and must not be treated as Claude startup context.
- Claude project skills live under `.claude/skills/<skill-name>/SKILL.md`.
- Old audit docs, task artifacts, historical findings, and archived reports are historical context. Do not load them unless the user explicitly asks for a historical review, full audit comparison, or a named artifact.
- Engine A, Engine B, Engine C, and Engine D are independent unless the task explicitly concerns consensus, blending, or cross-engine coordination.
- Run only tests directly related to the touched behavior. Do not run the full test suite or unrelated engine/UI/backtest tests unless explicitly requested or shared infrastructure was changed.
- Do not read `tasks/`, old audit reports, generated logs, backtest artifacts, or historical skill references at startup. Read them only when the user names them or the current task requires that exact artifact.
- Use subagents, Superpowers, or other workflow helpers only when the task benefits from parallel investigation or a specialized workflow. Do not use them to broaden a small fix into a full audit.

## Core Rules

- Work evidence-first: inspect code, logs, tests, DB paths, or docs before making claims.
- Keep changes minimal and focused. Do not refactor unrelated code.
- Paper-only unless the user explicitly approves otherwise.
- Never weaken risk, freshness, kill switch, RR, spread, fee, broker, guardian, audit, or execution gates.
- Scoring and thresholds are locked unless the user explicitly asks to change them; thresholds belong in `config.yaml`.
- Never import `athena.py` in tests; use `athena_app/` modules.

## Commands

```bash
python athena.py
pip install -r requirements.txt
```

Run targeted tests for the changed behavior, for example:

```bash
pytest path/to/test_file.py -q
pytest path/to/test_file.py::test_function_name -q
```

## Key Paths

- `athena.py` - Flask monolith entry point.
- `athena_app/api/` - modular API routes.
- `scoring.py`, `factor_scoring.py` - Engine A.
- `market_structure.py`, `zone_registry.py`, `engine_b_ai.py` - Engine B.
- `engine_c.py`, `engine_c_ai.py` - Engine C.
- `scalp_engine.py`, `volume_profile.py` - Engine D.
- `execution.py`, `auto_trader.py`, `risk_engine.py`, `mt5_executor.py`, `bybit_executor.py` - high-risk execution path.
- `config.yaml` / `config.py` - config and thresholds.

## AI Agent Boundaries

- All AI is advisory-only: Marcus, Engine B AI, AI review packets, Strategist, market intelligence, Vision, similar setups, and AI Trading Agent chat.
- AI may explain, challenge, downgrade, block, request confirmation, compare evidence, and recommend research.
- AI must not execute trades, approve orders, mutate config/thresholds, change strategy parameters, or bypass deterministic gates.
- `/api/ai/trade-chat` and `ai_tools.py` are read-only.
- `ai_agent_safety.validate_ai_chat_response()` must keep `read_only=true`, `can_execute=false`, `can_modify_thresholds=false`, and `deterministic_gates_required=true`.

## Vision

- Chart Vision and Lottery AI are separate. Do not mix prompts, parsers, ratings, or payloads.
- Preserve footer tokens: `RIGHT EDGE`, `TF ALIGNMENT`, `RATING`, `LEVELS`.
- Structured Vision freshness must not upgrade execution context. Missing or stale timestamps mean `allowed_for_execution_context=false`.

## Verification

Before saying fixed or done, run the smallest relevant compile/test/smoke command for the changed behavior and report exactly what passed or was not verified. If no focused test exists, inspect the changed path and state the exact targeted command that should be added or run.
