# AGENTS.md - Athena / Sentinel Pro v4 (Codex / Cursor)

Primary repo-level startup instructions for Codex/Cursor.

# AGENTS.md — Codex Only

This file is for Codex/agent-compatible tooling only.

Claude Code must not read, follow, summarize, edit, or import this file unless the user explicitly asks to maintain AGENTS.md.

Do not duplicate Claude Code instructions here. Claude Code instructions belong in `CLAUDE.md` and `.claude/skills/**/SKILL.md`.

## Core rules


- Never bypass risk gates, freshness checks, kill switches, execution approvals, broker safety checks, RR checks, SL/TP validation, audit logging, or deterministic safety rules.
- AI is advisory only. AI review, Marcus, Vision, Strategist, AI Agent chat, and similar-setup logic cannot execute trades, approve orders, mutate config, or override deterministic gates.
- Engine A, Engine B, Engine C, and Engine D are separate unless the task explicitly concerns consensus, routing, or cross-engine payload handoff.
- Engine A and Engine B must not suppress each other. Engine C owns agreement, conflict, A-only, and B-only comparison.
- Start with the files relevant to the user's request. The repository map below lists common entry points only; inspect additional current source files when needed to verify the real execution path.
- Do not load `tasks/`, old audit reports, generated logs, backtest artifacts, historical findings, or archived diagnostics unless the user names them or the current issue directly requires them.
- Run only targeted tests for the changed behavior. Never run full test suites unless explicitly requested.
- Evidence first. Inspect current source before making claims.
- If unsure, say `not verified` instead of guessing.

## Mandatory skill routing

- Before editing `execution.py`, `risk_engine.py`, `guardian.py`, `auto_trader.py`, `mt5_executor.py`, or `bybit_executor.py`, invoke `$athena-audit` to verify execution safety first.
- If changes affect Engine A/B/C/D scoring or threshold logic, verify live/backtest parity before applying.
- After any edit to safety gates, freshness checks, or kill switches, run targeted tests for the touched behavior.

## Codex skill policy

- Codex repo skills live under `.agents/skills/<skill-name>/SKILL.md`.
- Current installed repo skill: `athena-audit`.
- `athena-audit` is manual-only. Invoke it only for explicit full audit, bug hunt, strict findings, execution-safety review, live/backtest parity review, or end-to-end trace work.
- Do not look for or use skills that do not exist under `.agents/skills/`.
- Codex manual-only invocation is controlled by `.agents/skills/athena-audit/agents/openai.yaml`.

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
- AI/Vision: `ai_agent_safety.py`, native chart AI review modules, provider routers, screenshot/payload builders, browser chart code under `static/`
- AI Agent chat: `ai_trade_chat.py`, `athena_app/api/routes_ai_agent.py`. LLM narrative optional when configured; deterministic decision card, safety flags, and gates remain authoritative.
- Research Lab: `athena_research/`, `tools/vectorbt_research_lab.py`, `configs/vectorbt_research_lab.yaml`
- Backtesting: `backtest_runner.py`, backtest matrix tooling, telemetry/report writers
- Config: `config.py`, `config.yaml`, `configs/`
- Tests: targeted `tests/test_*.py` only for touched behavior

## Chart and providers

- Native chart is the active chart surface for chart and AI review work.
- Do not build new AI review features on the legacy TradingView path.
- Prefer native chart PNG screenshots; TradingView limits drove the move.

## Engine A baseline

- Engine A / live chart parity is the current priority baseline.
- Live chart validity: done; `bar_time` wiring verified end-to-end in a live scan.
- Equity/index session liquidity weighting is not a known fail-open blocker.
- Do not re-open missing `bar_time` unless new regression evidence.

## Chart AI review contract

- Read-only advisory; must not connect to execution.
- Input: native chart PNG + server-trusted Engine A diagnostics assembled on the backend.
- Do not trust the frontend for Engine A score, threshold, ATR, or RR.
- Review payload must include ATR diagnostics, SL/TP/RR, freshness/provider timestamps, and Engine-A-vs-model concordance.
- v1 provider target: Anthropic/Claude; OpenAI scaffold only if explicitly requested.

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
- For this repo, active skill discovery should come from `.agents/skills/` only.

---

# Karpathy Guidelines

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
