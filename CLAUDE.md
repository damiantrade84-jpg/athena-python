# CLAUDE.md - Athena / Sentinel Pro v4 (Claude Code)

Primary repo-level startup instructions for Claude Code.

Do not intentionally load `AGENTS.md`. Codex/Cursor use `AGENTS.md`.

## Core rules

- Paper-only unless the user explicitly approves live trading.
- Never bypass risk gates, freshness checks, kill switches, execution approvals, broker safety checks, RR checks, SL/TP validation, audit logging, or deterministic safety rules.
- AI is advisory only. AI review, Marcus, Vision, Strategist, AI Agent chat, and similar-setup logic cannot execute trades, approve orders, mutate config, or override deterministic gates.
- Engine A, Engine B, Engine C, and Engine D are separate unless the task explicitly concerns consensus, routing, or cross-engine payload handoff.
- Engine A and Engine B must not suppress each other. Engine C owns agreement, conflict, A-only, and B-only comparison.
- Start with the files relevant to the user's request. The repository map below lists common entry points only; inspect additional current source files when needed to verify the real execution path.
- Do not load `tasks/`, old audit reports, generated logs, backtest artifacts, historical findings, or archived diagnostics unless the user names them or the current issue directly requires them.
- Run only targeted tests for the changed behavior. Never run full test suites unless explicitly requested.
- Minimal changes only. Do not refactor unrelated code.
- Evidence first. Inspect current source before making claims.
- If unsure, say `not verified` instead of guessing.

## Mandatory skill routing

- Before editing `execution.py`, `risk_engine.py`, `guardian.py`, `auto_trader.py`, `mt5_executor.py`, or `bybit_executor.py`, invoke `/athena-audit` to verify execution safety first.
- If changes affect Engine A/B/C/D scoring or threshold logic, verify live/backtest parity before applying.
- After any edit to safety gates, freshness checks, or kill switches, run targeted tests for the touched behavior.

## Claude Code skill policy

- Claude Code project skills live under `.claude/skills/<skill-name>/SKILL.md`.
- Current installed repo skill: `athena-audit`.
- Invoke manually with `/athena-audit` only for explicit full audit, bug hunt, strict findings, execution-safety review, live/backtest parity review, or end-to-end trace work.
- Do not look for or use skills that do not exist under `.claude/skills/`.
- The `athena-audit` skill must include `disable-model-invocation: true` in Claude frontmatter.

## Repository map

Common entry points only. This is not a complete allowlist.

- App/routes: `athena.py`
- Scanner: `scanner.py`
- Engine A: `scoring.py`, `factor_scoring.py`, `forex_scoring.py`
- Engine B: `market_structure.py`, `engine_b_ai.py`
- Engine C: `engine_c.py`, `engine_c_ai.py`
- Engine D: `scalp_engine.py`
- Execution: `execution.py`, `auto_trader.py`, `risk_engine.py`, `guardian.py`, `mt5_executor.py`, `bybit_executor.py`
- Data/candles: `candles_cache.py`, `candle_feeds.py`, provider-specific feed modules
- AI/Vision: `ai_agent_safety.py`, AI review modules, chart/Vision payload builders, browser chart code under `static/`
- AI Agent chat: `ai_trade_chat.py`, `athena_app/api/routes_ai_agent.py`. The chat answer narrative may use an LLM when an AI API key is configured; the deterministic decision card, safety flags, and gates remain authoritative and are never altered by the LLM. With no API key the chat falls back to the deterministic answer.
- Research Lab: `athena_research/`, `tools/vectorbt_research_lab.py`, `configs/vectorbt_research_lab.yaml`
- Backtesting: `backtest_runner.py`, backtest matrix tooling, telemetry/report writers
- Config: `config.py`, `config.yaml`, `configs/`
- Tests: targeted `tests/test_*.py` only for touched behavior

## Safe workflow

1. Restate the exact user request in operational terms.
2. Identify the relevant engine/surface and current source files.
3. Trace producer-to-consumer behavior before editing.
4. Apply the smallest safe patch.
5. Run the smallest relevant compile/test command.
6. Report what changed, what passed, and what was not verified.

## Detailed reference

Use `docs/agent-operating-guide.md` only when the task requires fuller repository operating rules. Do not load it by default for small edits.

- Do not use user-profile Codex skills or memory skills for this repo unless explicitly requested.
- For this repo, active skill discovery should come from `.claude/skills/` only.
