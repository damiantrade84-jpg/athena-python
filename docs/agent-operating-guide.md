# Sentinel Pro v4 — Agent operating guide

**Athena** — Sentinel Pro v4 paper-trading and trading-analysis system: multiple engines, AI review, broker/execution adapters, monitoring, backtesting, UI/API consumers.

**Priorities:** correctness, safety, fail-closed behavior, and reproducibility over speed, cosmetic cleanup, or clever shortcuts.



---

## Context Loading Contract

- Codex and Cursor start with `AGENTS.md` for repository instructions.
- Codex/Cursor start from `AGENTS.md`; Claude Code starts from `CLAUDE.md`. Do not intentionally cross-load these startup files.
- `memory.md`, `memories.md`, and `MEMORY.md` are not active repo instructions. Archive under `docs/archive/` if kept for history. See `docs/codex-memory-policy.md`.
- Local Codex-generated memory (`~/.codex/memories/`) is outside the repo and may be stale; user-level cleanup is separate from git.
- Codex repo skills must live under `.agents/skills/<skill-name>/SKILL.md` for repository skill discovery.
- Codex should not rely on `tools/skills/*` for automatic repo skill discovery.
- Claude Code project skills belong under `.claude/skills/<skill-name>/SKILL.md`.
- Old audit docs, task artifacts, historical findings, and archived reports are historical context. Do not load them unless the user explicitly asks for a historical review, full audit comparison, or a named artifact.
- Engine A, Engine B, Engine C, and Engine D are independent unless the task explicitly concerns consensus, blending, or cross-engine coordination.
- Run only tests directly related to the touched behavior. Do not run the full test suite or unrelated engine/UI/backtest tests unless explicitly requested or shared infrastructure was changed. See **`AGENTS.md` Test & token budget** for the canonical one-file-per-pass rule.
- Do not read `tasks/`, old audit reports, generated logs, backtest artifacts, or historical skill references at startup. Read them only when the user names them or the current task requires that exact artifact.
- Use subagents, Superpowers, or other workflow helpers only when the task benefits from parallel investigation or a specialized workflow and the active tool policy allows it. Do not use them to broaden a small fix into a full audit.

---

## 1. Commands

```bash
# Web app (http://127.0.0.1:5000)
python athena.py

# Targeted tests
pytest path/to/test_file.py -q
pytest path/to/test_file.py::test_function_name -q

# Dependencies (Python 3.11–3.14; .python-version pins 3.14)
pip install -r requirements.txt
```

After recreating `.venv`: run `python tools/check_ws_env.py` to verify WebSocket env.

---

## 2. Repository map

**Entry point:** `athena.py` — monolith Flask app. **Never import this in tests**; use `athena_app/` modules instead.

**App factory:** `app.py` → `athena_legacy.load()` + `execution` healthcheck. `athena_runtime.py` holds shared runtime bindings (`set_runtime` / `rt()`) to break import cycles from the monolith.

### `athena_app/` (modular Flask layer)

- **`api/`** — route blueprints: `routes_scan`, `routes_backtest`, `routes_execution`, `routes_audit`, `routes_live_dashboard`, `routes_lottery`, `routes_market_data`, `routes_broker_status`, `routes_status`
- **`services/`** — `scan_backtest_service`, `candle_service`, `data_freshness`, `market_state`, `engine_b_market_state`, `paper_mode`, `structure_context`
- **`repositories/audit_repo.py`** — audit SQLite writes

### Core engines

- `scoring.py` / `factor_scoring.py` — Engine A factor confluence
- `market_structure.py` / `zone_registry.py` — Engine B SMC/ICT
- `engine_c.py` + `engine_c_ai.py` — Engine C consensus + AI blend
- `engine_b_ai.py` — Engine B AI advisory (**review-only**, never executes)
- `scalp_engine.py` + `volume_profile.py` — Engine D scalp lab
- **`athena_ase/`** — ASE v2.1 (PTIS, Layer 1/2, inference, demo gate); **`ase_cli.py`**; spec **`docs/ASE_v2.1_Implementation_Spec.md`**
- `timed_exit_monitor.py` — exit pipeline for Engine A/B (Engine D bypasses)

### Execution

- `execution.py` — signal execution dispatcher
- `auto_trader.py` — autopilot loop
- `risk_engine.py` — position sizing + risk gates
- `mt5_executor.py` / `bybit_executor.py` — broker adapters

### Data pipeline

- `candle_feeds.py` / `data_feeds.py` — live candle ingestion
- `backtest_candle_cache.py` / `candle_cache.db` — cached OHLCV for backtests
- `cot_feed.py` / `carry_feed.py` — COT + carry enrichment
- `eodhd_volume_batch.py` — EODHD volume (**Engine D only**)

### SQLite (WAL mode, 15s timeout where applicable)

- `audit.db` — trade audit trail (`trades`)
- `candle_cache.db` — OHLCV cache
- `cot.db` / `carry_cache.db`
- `microstructure.db` — order-flow microstructure

### AI layer

- `conductor.py` — deterministic routing (no LLM for routing decisions)
- `ai_contracts.py` — canonical AI packet/context/decision schemas
- `ai_context.py` / `ai_utils.py` — shared helpers and AI review packet builder
- `ai_orchestrator.py` / `ai_similar_setups.py` / `ai_contradiction_detector.py` — advisory packet orchestration, historical analogue scaffold, deterministic contradiction checks
- `ai_tools.py` / `ai_trade_chat.py` / `ai_agent_safety.py` — read-only AI Trading Agent tool registry, chat orchestration, and response safety validation
- `market_intelligence.py` / `pair_context.py` / `ai_strategist.py` — read-only market desk context, pair context, and Strategist summaries
- `ai_conversation_store.py` / `ai_agent_logger.py` — best-effort chat persistence and audit logging; failures must not block chat/review
- `athena_app/api/routes_ai_agent.py` — `/api/ai/trade-chat`, conversation routes, and Strategist routes
- `vision_prompts.py` / `vision_hybrid.py` — chart vision (grok-4.3); **preserve exact footer tokens**
- `lottery_engine.py` — lottery AI (**separate** from chart vision — do not mix)
- `signal_debate.py` — Engine B debate flow
- `news_sentiment_feed.py` — sentiment enrichment

### Config

All thresholds belong in **`config.yaml`** (loaded via `config.py`). Do not hardcode trading thresholds or safety gates in Python.

### Reference code

**`refs/`** — third-party Jesse framework reference; excluded from pytest and not part of shipped product logic.

---

## 3. Safety rules

### Trading safety

Paper-only unless the user **explicitly** states otherwise.

Never bypass or weaken:

- risk gates, freshness gates, kill-switches, execution approval gates
- broker safety checks, score thresholds, RR checks, SL/TP validation
- position/balance validation, stale-data protections, monitor/audit logging

**AI review and AI Trading Agent chat cannot override execution gates.**

Real-money enablement requires **all** of:

1. ≥ 1 week clean paper results  
2. explicit manual user approval  
3. tests proving fail-closed execution behavior  
4. broker adapter checks proving SL/TP and risk preservation  

If any required safety field is missing, stale, malformed, null, false, or ambiguous, the system must **reject by default**.

### Engine and scoring safety

- Scoring is **locked** unless the user asks to change it.
- Do not change Engine A/B/D thresholds unless requested.
- Do not hardcode thresholds, symbols, offsets, scoring constants, or gates in Python — use `config.yaml`/config layer.
- Engine A and Engine B are independent signal engines. Do not make Engine A suppress Engine B, or Engine B suppress Engine A, unless an explicitly named config gate says so. Engine C is the comparison/consensus layer for A vs B agreement, conflict, A-only, and B-only outcomes.

Changes should be:

- config-gated where appropriate  
- default-safe and minimally invasive  
- covered by focused tests  
- explainable with code-path evidence  

### Sentinel brief (Claude/Code summary)

| Area | Rule |
|------|------|
| **Safety** | Paper only; never bypass risk/freshness/kill-switch; AI cannot override gates |
| **Scoring** | Locked; thresholds only via user request → `config.yaml` |
| **Dev** | No guessing; default-safe tests; never import `athena.py` in tests; SQLite WAL + 15s timeout |
| **AI** | Advisory only; AI Agent/chat/tools cannot execute, mutate config, or override deterministic gates; Vision footer tokens immutable; Lottery ≠ Vision |
| **Data** | Freshness mandatory; H4 offsets: Binance 0h, MT5 forex 2h, MT5 stocks 3h; D1 @ UTC 00:00; `fetch_mt5()` for MT5; EODHD **volume-only** for Engine D |

### Test safety

- Never import `athena.py` in tests.
- SQLite: WAL + 15s timeout where applicable; avoid brittle tests on global DB state.
- Run only tests directly related to the touched behavior.
- Do not run `pytest tests/`, unrelated engine suites, broad UI builds, broad backtest batches, or unrelated smoke suites unless explicitly requested or shared infrastructure was changed.
- If no focused test exists, prefer a narrow import/compile/smoke check for the touched module and state what remains not verified.

---

## 4. Development invariants

- No guessing; find root causes; no temporary fixes.
- No cosmetic rewrites during bug fixes; keep scope minimal.
- Use config instead of hardcoding.
- Add tests for safety-relevant bugs.
- Prefer simple, explicit code; preserve public contracts unless the contract is the bug.

---

## 5. Data freshness & sources

Freshness gate is **mandatory**.

**H4/session offsets:**

- Binance H4: `0h`
- MT5 forex H4: `2h`
- MT5 stocks H4: `3h`
- D1 candles: UTC `00:00`

**Sources:**

- MT5 data → `fetch_mt5()`
- EODHD → **volume-only** for Engine D
- Do not silently mix feeds or let stale fallback pass execution safety

---

## 6. AI boundaries

All ATHENA AI is advisory-only. Engine B AI, Marcus review, AI review packets, Strategist, market intelligence, Vision, similar setups, and AI Trading Agent chat may explain, challenge, downgrade, block, request confirmation, compare evidence, and recommend research. They must not execute trades, approve orders, mutate config/thresholds, change strategy parameters, or bypass guardian, freshness, kill switch, RR, spread, fee, broker, or risk gates.

The AI Agent stack is a tool-using desk assistant, not an execution layer:

- `AIReviewPacket` is the canonical review context; missing fields must be recorded, not invented.
- `/api/ai/trade-chat` and AI tools are read-only. Tool calls may read signal, engine, Vision, market-intelligence, similar-setup, strategist, and risk-state context only.
- `validate_ai_chat_response()` must force `read_only=true`, `can_execute=false`, `can_modify_thresholds=false`, and `deterministic_gates_required=true`.
- AI must never return or preserve `VALID_SETUP` when deterministic gates fail, kill switch is active, guardian is not clean, RR/spread/fee/freshness data is failed or missing, or the packet says the setup is blocked.
- Similar-setup samples under 20 are `insufficient`; do not make calibrated probability claims from insufficient history.
- Market intelligence and pair context use existing repo/local sources only; unavailable or stale sources must be surfaced as warnings, not filled with generic macro opinions.
- Strategist functions are read-only and must not directly block execution unless an explicit future config gate is added and defaults safe.
- Conversation persistence and AI logging are best-effort; DB/log failures must not block signal generation or chat responses.
- Marcus two-stage memo mode is optional and disabled by default; when disabled, existing single-stage structured review behavior must remain compatible.

**Chart Vision** vs **Lottery AI** are separate: do not mix prompts, parsers, tokens, ratings, or payloads. Structured Vision may add `vision_trade_read.v1`, but execution-adjacent use must honor freshness policy and `allowed_for_execution_context=false` for missing/stale timestamps.

**Vision footer tokens (exact):** `RIGHT EDGE`, `TF ALIGNMENT`, `RATING`, `LEVELS`

---

## 7. Skills and audit mode

Detailed repeatable workflows live in repo skills. See `docs/codex-guidance.md` for how layers fit together.

- Codex discovers repo skills under `.agents/skills/<skill-name>/SKILL.md`.
- Claude Code discovers project skills under `.claude/skills/<skill-name>/SKILL.md`.
- Codex repo skills: `athena-audit`, `athena-engine-parity`, `athena-research-lab`, `athena-ui-chart-review`, `athena-test-repair`, `athena-risk-execution`.
- `athena-audit` is manual-only for explicit full audit, bug hunt, strict findings, execution-safety review, live/backtest parity review, producer-to-consumer contract review, or end-to-end trace work.
- Do not reference or search for skills that do not exist in the current repo skill folders.

---

## 8. Engines & scoring

### Engine A — Factor confluence (primary)

- **Scoring:** `final_score` 0.0–3.0; directional `trend_score`; nondirectional `mom_quality`.
- **Thresholds:** profile override → pair/group YAML → 3-tier fallback.
- **Factors:** BTC bias (conditional on correlation), OI (crypto), intermarket confirmation.
- **Config:** `ENGINE_A`, `ENGINE_A_RESEARCH_LAB_FACTORS`, `ENGINE_A_MEAN_REVERSION`
- **Boundary:** Engine A should score factor confluence on its own evidence. It may expose diagnostics for Engine B context, but it should not hide valid Engine B structures or require Engine B confirmation unless a specific config-gated feature requires that behavior.

**Audit concerns:** normalization, missing score group, threshold source drift, profile/override misuse, permissive fallback, BTC/OI misuse, live/BT mismatch.

### Engine B — Naked structure (SMC/ICT)

- **Scoring:** % of max; regime-gated thresholds.
- **Regime multipliers:** TRENDING 0.90, RANGING 0.90, HIGH_VOL 0.85, LOW_VOL 1.15.
- **Checklist:** swings, BOS, sweeps, FVG overlap, zone/trigger quality.
- **Styles:** scalp H1, intraday H4, swing D1 — each `min_score` + `min_rr`.
- **Config:** `NAKED_ENGINE.style_profiles`, `NAKED_MAX_DAILY`, `ENGINE_B_REGIME_MULTIPLIERS`
- **Boundary:** Engine B should score naked market structure on its own BOS/CHoCH/OB/FVG/liquidity evidence. It should not be discarded solely because Engine A is below threshold or pointing elsewhere; surface B-only or B-vs-A conflict to Engine C / scan diagnostics when config allows.

**Audit concerns:** AI review mistaken for approval, missing profile passes, regime math, RR mismatch, structure gate skipped by early return, live/BT mismatch, incomplete payload confirmations.

### Engine C — Consensus (A vs B)

- **Outputs:** calibrated probability, trust (`trust_a` / `trust_b` / `trust_both` / `trust_neither`), weights `{"A": x, "B": y}` summing to 1.0, conviction (`UPGRADE`/`NEUTRAL`/`DOWNGRADE`), decision state (trade, tier, sizing override, disagreement diagnosis).
- **Boundary:** Engine C owns comparison between Engine A and Engine B. A/B agreement, conflict, A-only, and B-only states should be decided here or in explicit scan-only surfacing helpers, not by silently letting one engine erase the other upstream.

**Audit concerns:** default-pass trust, weights ≠ 1, trades without proof, `trust_neither` still trading, bad conviction upgrades, sizing bypass, unlogged A/B mismatch, missing diagnosis in audit path.

### Engine D — Scalp lab (VP / order flow)

- Fabio Valentini VP + OF: balance/imbalance, VAL/VAH/POC/LVN.
- **Setups:** mean reversion (VA extreme → POC), trend continuation (pullback to LVN).
- **Grades:** A/B/C/D.
- **Three pillars:** market state + location + aggression — **all** must align (when strict mode applies per config).
- **Sessions:** NY open skip, London cash open, modes NY/London/Asia/All (per config/asset rules).
- **Config:** `SCALP_ENGINE`, `BT_*`

**Audit concerns:** missing pillar passes, grade D trades, session skip ignored, mixed volume sources, EODHD beyond volume-only role, live/BT mismatch, bad/missing POC or VA levels, aggression defaulting “on”.

### ASE — Adaptive Specialist Engine v2.1 (greenfield)

- **Package:** `athena_ase/` — separate from Engine A/B/C/D; no legacy factor scoring imports.
- **Layers:** Layer 1 deterministic signals → candidates; Layer 2 pooled per-family meta-model (HGB + isotonic + quantile heads) filters only.
- **Data:** PTIS point-in-time store (`athena_ase/data/ptis.py`); features use `asof()` only.
- **Universe:** 134 instruments via `athena_ase/universe.py` (derived from `ALL_PAIRS`).
- **Inference:** `athena_ase/inference/predict.py` → `predict_batch()` — **only** path for scan/shadow/demo/parity.
- **Safety:** `athena_ase/gates/demo_only.py` (no override); sizing in `risk_engine`; **does not block Engine A** or route through Engine C.
- **Deployment:** SHADOW journal + manual `ase_cli promote` → DEMO execution eligibility; ASE panel + `/api/ase-*` + standalone backtest (`/api/backtest-ase`, `/api/backtest-ase-all`).
- **Spec / CLI:** `docs/ASE_v2.1_Implementation_Spec.md`, `ase_cli.py` (`train`, `validate`, `freeze`, `holdout-eval`, `promote`, `demote`, `shadow-report`, `drift-report`).
- **Research:** `athena_research/ase/` (Phase 1 backtest, walk-forward, train, parity).
- **UI:** `ASEPanel.tsx`, `/api/ase-scan`.

**Audit concerns:** second inference path, demo gate bypass, training on holdout, automatic promotion, Engine A threshold reuse in ASE features, weakened PROVISIONAL/holdout gates.

---

## 9. Vision / chart analysis

**Input:** screenshots + H4/H1/D1 + algorithmic context.

**Output:** RIGHT EDGE status, TF alignment, per-style ratings, levels.

**Model:** `VISION_MODEL` (grok-4.3), ~800–1100 tokens, temperature from `AITemperatureConfig`.

**Parser:** exact footer tokens; do not rename/reorder unless explicitly requested.

**Audit concerns:** permissive parser, malformed output passing, missing levels, REVIEW treated as confirm, Vision mixed with Lottery, AI overriding gates, wrong TF for ratings, parser defaulting to pass/trade.

---

## 10. Exit pipeline (Engine A / B)

`timed_exit_monitor.py` owns trade management for Engine A/B. Engine D bypasses (`engine in {scalp, engine d, scalp_vp}` early-returns) — TP1/SL at broker.

**`TIMED_EXIT.tp_mode`:**

- `trailing_atr` (default): chandelier ATR trail; lock + timed-close suppressed via early-return when configured accordingly.
- `fixed`: legacy lock + timed-close (config rollback path).

**`trail_activation_r`:** confirm in live `config.yaml` before citing; checked-in baseline often `{scalp: 0.7, intraday: 1.0, swing: 1.5}` R; arms when `current_r >= activation_r`; scalar accepted for back-compat.

**Typical defaults (change only on user request):** `intraday`/`swing` `timed_close_enabled: false`; `scalp.profit_lock_enabled: false`; `trail_indicator_confirm: true`; `timer_tightens_trail: false` with `timer_tighten_factor: 0.6`.

**Broker:** SL ratchets via MT5/Bybit BE/trail helpers; tightens-only via `_protective_sl_tightens`.

**Resilience:** chandelier fetch failure should hold prior `_trail_state` and warn.

**Persistence:** `timed_exit_state` SQLite; stable key `(venue, audit_id)`.

**Exit reasons:** e.g. `TRAIL_CLOSE`, `TIMED_CLOSE`, `LOCK_SL_HIT` via `_mark_timed_close(reason=...)`.

**Trail evaluation:** `_evaluate_trail()` → `{action: none|ratchet|close}`.

---

## 11. Workflow & task tracking

### Session start

- Start from the active tool’s root instruction file and the user's exact request:
  - Codex/Cursor: `AGENTS.md`
  - Claude Code: `CLAUDE.md`
- Do not review `tasks/lessons.md`, `tasks/todo.md`, old audits, generated logs, or backtest artifacts by default.
- Open historical/task files only when the user names them or they are directly needed to verify the current issue.

### Planning & execution (Claude/planning loop)

- Use **plan mode** for non-trivial work (3+ steps or architecture).
- Re-plan if assumptions break or safety is at risk.
- Use **subagents** or Superpowers only when the task benefits from parallel exploration or a specialized workflow, and keep each delegated task focused on the current request.
- After **user corrections**, append patterns to `tasks/lessons.md`.
- **Verify before done:** tests, logs, staff-engineer bar.
- For non-trivial edits, ask whether a cleaner design exists; avoid hacky fixes.
- **Autonomy:** fix reported bugs/CI using evidence; avoid hand-holding.

### Task files (only when needed)

- Use `tasks/todo.md` only when the user requests durable tracking or the task genuinely needs a persistent checklist.
- Use `tasks/lessons.md` only after a user correction creates a reusable repository rule.

Task files are not startup context and do not replace running targeted tests or proofs.

### Audit mode (explicit asks to audit/review/find bugs)

Use the manual audit skill only when the user explicitly asks for an audit, bug hunt, full trace, or strict finding format. Keep the root guide limited to scope and safety rules; the audit `SKILL.md` owns detailed audit procedure and checks.

All audits and code reviews must follow **`docs/codex-code-review-discipline.md`**: build a coverage map before any verdict, no summary-only reviews, run the negative-check pass, and say **"Coverage incomplete"** with missing areas when proof is insufficient.

For audit, verification, shipped-change validation, or "make sure nothing was missed" asks, invoke **`.agents/skills/athena-anti-miss-review/SKILL.md`** (review map, parallel lanes when multi-surface and explicitly scoped, required search pass, adversarial pass, structured verdict). Parallel lanes are for explicit audit asks only — not single-file fixes. Lane definitions: **`references/review-lanes.md`** under that skill. No pytest during audit phase; see **`AGENTS.md` Test & token budget**.

### Implementation mode

Short plan when non-trivial; smallest safe diff; config-gated where needed; targeted tests only; summarize risk.

### When to stop and re-plan

Conflicting tests, execution-safety risk, architecture surprises, missing files/env failures, fixes that weaken gates or change locked scoring.

### Task tracking nuance

Use `tasks/todo.md` / `tasks/lessons.md` only when work is large or the user wants durable tracking, not as default context and not as a substitute for verification.

---

## 12. Verification

Before claiming **done / fixed / passing**, verify with the **smallest** relevant command.

If commands are unknown: check `README`, `pyproject.toml`, `pytest.ini`, `package.json`, CI config — then infer.

**Python example:**

```bash
pytest path/to/test_file.py -q
```

---

## Maintaining root copies (`AGENTS.md`, `CLAUDE.md`)

Shared operating guidance lives in **`docs/agent-operating-guide.md`**. Root **`AGENTS.md`** and **`CLAUDE.md`** are maintained separately as short startup guides.

Root **`CLAUDE.md`** is maintained directly as the Claude Code startup guide. Root **`AGENTS.md`** is maintained separately for Codex/Cursor.

After edits to startup files, skills, or this guide, validate the instruction layout:

```bash
python tools/sync_agent_docs.py
```

See also `docs/codex-guidance.md` for AGENTS vs skills vs PLANS vs MCP.

---

*End of shared operating guide.*


