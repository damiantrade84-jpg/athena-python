# CLAUDE.md - Athena / Sentinel Pro v4

Claude Code quick guide. Detailed operating rules live in [AGENTS.md](AGENTS.md) and [docs/agent-operating-guide.md](docs/agent-operating-guide.md).

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
pytest tests/
pip install -r requirements.txt
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
- `/api/ai/trade-chat` and `ai_tools.py` are read-only. They may inspect signal, engine, Vision, market-intelligence, similar-setup, strategist, and risk-state context only.
- `ai_agent_safety.validate_ai_chat_response()` must keep `read_only=true`, `can_execute=false`, `can_modify_thresholds=false`, and `deterministic_gates_required=true`.
- AI must not preserve `VALID_SETUP` when deterministic gates fail, kill switch is active, guardian is not clean, or RR/spread/fee/freshness/risk data is failed or missing.
- Similar setups with sample size under 20 are insufficient; do not make calibrated probability claims.
- Market intelligence uses existing local/repo sources only; stale or unavailable sources must be surfaced as warnings, not invented.
- Strategist is read-only and advisory; it must not directly block execution unless a future explicit config gate is added and defaults safe.
- Marcus two-stage memo mode is optional and disabled by default; existing single-stage behavior must remain compatible.

## Vision

- Chart Vision and Lottery AI are separate. Do not mix prompts, parsers, ratings, or payloads.
- Preserve footer tokens: `RIGHT EDGE`, `TF ALIGNMENT`, `RATING`, `LEVELS`.
- Structured Vision freshness must not upgrade execution context. Missing or stale timestamps mean `allowed_for_execution_context=false`.

## Verification

Before saying fixed or done, run the smallest relevant compile/test/smoke command and report exactly what passed or was not verified.
