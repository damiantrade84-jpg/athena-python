# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

# Commands

```bash
# Run the web app (http://127.0.0.1:5000)
python athena.py

# Run all tests
pytest tests/

# Run a single test file
pytest tests/test_scalp_engine.py

# Run a single test by name
pytest tests/test_scalp_engine.py::test_function_name -v

# Install dependencies (Python 3.11–3.13; .python-version pins 3.13)
pip install -r requirements.txt
```

> After recreating `.venv`: run `python tools/check_ws_env.py` to verify WebSocket env.

---

# Module Architecture

**Entry point:** `athena.py` — monolith Flask app. Never import this in tests; use `athena_app/` modules instead.

**App factory:** `app.py` → calls `athena_legacy.load()` + wires `execution` healthcheck. `athena_runtime.py` holds shared runtime bindings (`set_runtime` / `rt()`) to break import cycles from the monolith.

**`athena_app/` package** (modular Flask layer):
- `api/` — route blueprints: `routes_scan`, `routes_backtest`, `routes_execution`, `routes_audit`, `routes_live_dashboard`, `routes_lottery`, `routes_market_data`, `routes_broker_status`, `routes_status`
- `services/` — business logic: `scan_backtest_service`, `candle_service`, `data_freshness`, `market_state`, `engine_b_market_state`, `paper_mode`, `structure_context`
- `repositories/audit_repo.py` — audit SQLite writes

**Core engine files:**
- `scoring.py` / `factor_scoring.py` — Engine A factor confluence
- `market_structure.py` / `zone_registry.py` — Engine B SMC/ICT
- `engine_c.py` + `engine_c_ai.py` — Engine C consensus + AI blend
- `engine_b_ai.py` — Engine B AI advisory (review-only, never executes)
- `scalp_engine.py` + `volume_profile.py` — Engine D scalp lab
- `timed_exit_monitor.py` — exit pipeline for Engine A/B (Engine D bypasses)

**Execution:**
- `execution.py` — signal execution dispatcher
- `auto_trader.py` — autopilot loop
- `risk_engine.py` — position sizing + risk gates
- `mt5_executor.py` / `bybit_executor.py` — broker adapters

**Data pipeline:**
- `candle_feeds.py` / `data_feeds.py` — live candle ingestion
- `backtest_candle_cache.py` / `candle_cache.db` — cached OHLCV for backtests
- `cot_feed.py` / `carry_feed.py` — COT + carry enrichment
- `eodhd_volume_batch.py` — EODHD volume (Engine D only)

**SQLite databases (WAL mode, 15s timeout):**
- `audit.db` — trade audit trail (key table: `trades`)
- `candle_cache.db` — OHLCV cache
- `cot.db` / `carry_cache.db` — COT + carry data
- `microstructure.db` — order-flow microstructure

**AI layer:**
- `conductor.py` — deterministic AI routing (no LLM calls for routing decisions)
- `ai_context.py` / `ai_utils.py` — shared AI helpers
- `vision_prompts.py` / `vision_hybrid.py` — chart vision (grok-4.3); preserve exact footer tokens
- `lottery_engine.py` — lottery AI (separate from chart vision — do not mix)
- `signal_debate.py` — Engine B signal debate flow
- `news_sentiment_feed.py` — sentiment enrichment

**Config:** All thresholds live in `config.yaml` (never hardcode in Python). Loaded via `config.py`.

**`refs/`** — third-party Jesse framework reference; excluded from pytest and not part of the project.

---

# Sentinel Pro v4 — Claude Brief

**Safety:** Paper only. Never bypass risk/freshness/kill-switch. AI cannot override gates. No real orders without 1 week clean paper + manual approval.

**Scoring:** Locked. Do not change Engine A/B/D thresholds unless user requests. No hardcode in Python — use `config.yaml`.

**Dev:** No guessing. All changes config-gated, default-safe, with tests. Never import `athena.py` in tests. SQLite: WAL mode, 15s timeout.

**AI:** Engine B AI review-only. Preserve exact vision footer tokens. Chart Vision and Lottery AI are separate — do not mix.

**Data:** Freshness gate mandatory. H4 offsets: Binance 0h, MT5 forex 2h, MT5 stocks 3h. D1 = UTC 00:00. MT5 → `fetch_mt5()`, EODHD volume-only for Engine D.

---

# Mandatory Audit Contract Checks

For every audit, do not stop at the intended happy path. Trace the contract from producer to final consumer and prove how the system behaves when required fields are missing, false, stale, or malformed.

Audit runs must explicitly check:
- **Fail-closed defaults:** If a gate, confirmation, freshness check, score pass, RR pass, or execution approval field is absent, verify the code rejects by default. Flag any helper that returns `True`, `trade`, `passed`, or `execute` from missing data.
- **Payload handoff contracts:** Trace scanner/backtest/engine output into `execution.py`, `auto_trader.py`, `risk_engine.py`, broker executors, monitors, API payloads, and UI consumers. Confirm required fields are always present at each boundary.
- **Boolean presence vs truth:** Check code that uses `"key" in payload`, `payload.get(...)`, fallback `{}`, or default `True`. Verify omission, explicit `False`, `None`, and empty dict/list behavior separately.
- **Mode dispatch and early returns:** For config modes such as `tp_mode`, backtest/live toggles, and structure-gate switches, prove which branches are skipped by early returns and whether suppressed branches are intentional.
- **Live vs backtest parity:** Compare the exact SL/TP, ATR source, score group, session, volume, and feed paths used by live/paper execution against backtests. Call out intentional divergence separately from bugs.
- **Execution safety handoff:** Before saying an engine is safe, inspect the execution guard, level preservation, broker adapter, monitor, and audit/log write path. Engine-internal correctness is not enough.
- **Negative-case tests:** Recommend or add focused tests for omitted required flags, failed confirmations, stale candles, zero/invalid ATR, missing broker symbols, missing execution levels, and rejected broker SL/TP updates.

If any of these checks were not performed, label that part of the audit as "not verified".

---

# Engines & Scoring

## Engine A — Factor Confluence (Primary)
- **Scoring:** `final_score` 0.0–3.0 (normalized indicator confluence)
- **Directional score:** Trend component (trend_score)
- **Nondirectional score:** Momentum quality (mom_quality)
- **Thresholds:** Profile override, pair/group YAML, then 3-tier fallback
- **Key factors:** BTC bias (conditional on correlation), OI context for crypto, intermarket confirmation
- **Config keys:** `ENGINE_A`, `ENGINE_A_RESEARCH_LAB_FACTORS`, `ENGINE_A_MEAN_REVERSION`

## Engine B — Naked Market Structure (SMC/ICT)
- **Scoring:** Score/max_score (%), regime-gated thresholds
- **Regime multipliers:** TRENDING=0.90, RANGING=0.90, HIGH_VOL=0.85, LOW_VOL=1.15
- **Checklist:** Swing sequence, BOS, liquidity sweeps, FVG overlap, zone quality, trigger quality
- **Styles:** scalp (H1), intraday (H4), swing (D1) — each with min_score + min_rr
- **Config keys:** `NAKED_ENGINE.style_profiles`, `NAKED_MAX_DAILY`, `ENGINE_B_REGIME_MULTIPLIERS`

## Engine C — Consensus Engine (A vs B Trust)
- **Purpose:** Compare Engine A and B signals, resolve conflicts
- **Scoring:** Calibrated probability, trust verdict (trust_a/trust_b/trust_both/trust_neither)
- **Weight recommendation:** {"A": x, "B": y} summing to 1.0
- **Conviction modifier:** Categorical (UPGRADE/NEUTRAL/DOWNGRADE) mapped to float
- **Decision states:** trade boolean, tier, sizing_override, disagreement_diagnosis

## Engine D — Scalp Lab (Volume Profile)
- **Methodology:** Fabio Valentini VP + Order Flow (balance/imbalance, VAL/VAH/POC/LVN)
- **Setup types:** Mean Reversion (price at VA extreme → target POC) / Trend Continuation (pullback to LVN)
- **Grading:** A (full) / B (half) / C (quarter) / D (skip)
- **Three-pillar gate:** Market State + Location + Aggression (ALL must align)
- **Session filter:** NY open skip, London cash open, session mode (NY/London/Asia/All)
- **Config keys:** `SCALP_ENGINE`, `BT_*` (backtest params)

## Vision (Chart Analysis)
- **Input:** Chart screenshots (H4/H1/D1) + algorithmic context
- **Output:** RIGHT EDGE status (CONFIRMS/REVIEW/POTENTIAL REVERSAL), TF alignment, per-style ratings
- **Model:** VISION_MODEL (grok-4.3), 800–1100 tokens, temperature from AITemperatureConfig
- **Parser contract:** Exact footer tokens required — `RIGHT EDGE`, `TF ALIGNMENT`, `RATING`, `LEVELS`

---

# Exit Pipeline (Engine A / B)

`timed_exit_monitor.py` owns trade-management for Engine A/B. Engine D bypasses (`engine in {scalp, engine d, scalp_vp}` early-returns) — manages TP1/SL natively at broker.

**Mode dispatch (`TIMED_EXIT.tp_mode`):**
- `trailing_atr` (default): chandelier ATR trail only. Lock + timed-close branches suppressed via early-return.
- `fixed`: legacy lock + timed-close pipeline. Set to roll back without code changes.

**Per-style `trail_activation_r`:** verify from current `config.yaml` before citing. Checked-in baseline is `{scalp: 0.7, intraday: 1.0, swing: 1.5}` R. Trail arms when `current_r >= activation_r`. Scalar form accepted for back-compat.

**Defaults (locked unless user requests):**
- `intraday`/`swing` `timed_close_enabled: false` — never close on timer alone.
- `scalp.profit_lock_enabled: false` — lock no longer clips winners.
- `trail_indicator_confirm: true` — RSI/MACD must agree before `TRAIL_CLOSE`.
- `timer_tightens_trail: false`, `timer_tighten_factor: 0.6` — timer does not tighten trail unless explicitly enabled.

**Broker-enforced:** SL ratchets via `mt5_move_sl_to_breakeven` / `bybit_move_sl_to_breakeven` on each tick once armed. Tightens-only via `_protective_sl_tightens`.

**Failure mode:** chandelier fetch failure holds previous `_trail_state` + WARN — no silent regression.

**Persistence:** `timed_exit_state` SQLite (WAL, 15s). Hydrated on first `_run_check`. Stable key `(venue, audit_id)` — survives `ticket=0` / reissue.

**Exit tags (`exit_reason`):** `TRAIL_CLOSE` / `TIMED_CLOSE` / `LOCK_SL_HIT`. Set via `_mark_timed_close(reason=...)`.

**State machine:** `_evaluate_trail()` returns `{action: none|ratchet|close}` — single source of truth.

---

# Workflow Orchestration

## 1. Plan Node Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

## 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One tack per subagent for focused execution

## 3. Self-Improvement Loop
- After ANY correction from the user: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

## 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

## 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes – don't over-engineer
- Challenge your own work before presenting it

## 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests – then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## 7. Task Management
1. **Plan First**: Write plan to `tasks/todo.md` with checkable items
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review section to `tasks/todo.md`
6. **Capture Lessons**: Update `tasks/lessons.md` after corrections

## 8. Core Principles
- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.
