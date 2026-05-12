# AGENTS.md — Sentinel Pro v4 (Cursor / Codex)

**Audience:** Cursor agents and Codex. **Canonical:** [`docs/agent-operating-guide.md`](docs/agent-operating-guide.md). This file duplicates the canonical guide for tooling that reads **AGENTS.md** at repo root.

---

# Sentinel Pro v4 — Agent operating guide

**Athena** — Sentinel Pro v4 paper-trading and trading-analysis system: multiple engines, AI review, broker/execution adapters, monitoring, backtesting, UI/API consumers.

**Priorities:** correctness, safety, fail-closed behavior, and reproducibility over speed, cosmetic cleanup, or clever shortcuts.

**Engine deep dives:** `docs/engines-reference.md` (summary + pointers to audits/diagnostics).

**Shared structure:** `AGENTS.md` (Cursor/Codex) and `CLAUDE.md` (Claude Code) mirror this document’s section order.

---

## 1. Commands

```bash
# Web app (http://127.0.0.1:5000)
python athena.py

# Tests
pytest tests/
pytest tests/test_scalp_engine.py -v
pytest tests/test_scalp_engine.py::test_function_name -v

# Dependencies (Python 3.11–3.13; .python-version pins 3.13)
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
- `ai_context.py` / `ai_utils.py` — shared helpers
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

**AI review cannot override execution gates.**

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
| **AI** | Engine B AI advisory only; Vision footer tokens immutable; Lottery ≠ Vision |
| **Data** | Freshness mandatory; H4 offsets: Binance 0h, MT5 forex 2h, MT5 stocks 3h; D1 @ UTC 00:00; `fetch_mt5()` for MT5; EODHD **volume-only** for Engine D |

### Test safety

- Never import `athena.py` in tests.
- SQLite: WAL + 15s timeout where applicable; avoid brittle tests on global DB state.

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

Engine B AI is **review-only**. AI must not override gates, risk/freshness checks, approve execution without required fields, mix Vision and Lottery contracts, or mutate thresholds unless requested.

**Chart Vision** vs **Lottery AI** are separate: do not mix prompts, parsers, tokens, ratings, or payloads.

**Vision footer tokens (exact):** `RIGHT EDGE`, `TF ALIGNMENT`, `RATING`, `LEVELS`

---

## 7. Audits — operating contract

### Critical agent behavior

**Audit / review / debug / bug-finding:**

- Do not stop at the happy path or after the first issue.
- Do not patch before producing a bug list unless explicitly asked.
- Trace producer → consumer contracts; verify missing/false/null/stale/malformed/empty cases separately.
- Treat missing safety fields as critical until proven fail-closed.
- Separate confirmed bugs from suspicious patterns; label gaps `not verified`.
- Do not call a path “safe” without execution guard, broker adapter, monitor, and audit/log path.
- Prefer evidence: code paths, tests, logs, repro steps. If a test cannot run, say why and name the test.

**Implementation / fixes:**

- Smallest safe change; preserve behavior unless that behavior is the bug.
- No hardcoded trading logic; add/update focused tests; run when possible; summarize before/after and residual risk.

### Audit completion checklist

An audit is complete only when the response includes:

1. Files inspected  
2. Functions/classes inspected  
3. Execution paths traced  
4. Commands/tests run  
5. Areas **not verified**  
6. Ranked bug list **with evidence**  
7. Recommended or added negative-case tests  

Per bug/suspected bug include: severity, file/function, producer, consumer, trigger, actual vs expected behavior, impact, minimal fix, test to prove.

Never say **no bugs** unless inspected scope **and** skipped scope are explicit.

### Mandatory audit contract checks

Trace end-to-end when required fields are **missing**, **false**, **null**, **stale**, **malformed**, **empty**, **wrong type**, **delayed**, or **partially populated**.

**1. Fail-closed defaults** — Reject-by-default unless proven otherwise for gates, confirmations, freshness, scores, RR, structure, AI review, execution approval, broker symbol, SL/TP, ATR, session, kill-switch, risk config, positions, balances. Flag helpers that return `True` / `trade` / `passed` / `execute` / `approved` / `allow` / `valid` / `safe` from absent or bad data.

**2. Payload handoff** — Trace scanner/backtest/engine/consensus outputs into `execution.py`, `auto_trader.py`, `risk_engine.py`, brokers, monitors, audit/API/UI.

**3. Boolean presence vs truth** — Scrutinize `in`-checks, `.get(...)`, `.get(..., True)`, `{}`/`[]` fallbacks; verify omitted key vs `False`/`None`/empty/type/stale separately.

**4. Mode dispatch & early returns** — `tp_mode`, backtest/live/paper, structure gates, AI toggles, engine enablement, sessions, overrides, fallback thresholds.

**5. Live vs backtest parity** — SL/TP/ATR, score group, session, volume, feeds, freshness, RR, broker mapping, rounding, spreads/slippage, approval fields, sizing, monitors.

**6. Execution safety handoff** — Producer → scoring → consensus → guard → risk → levels → broker → responses → monitors → audit/log → UI/API.

**7. Negative-case tests** — Omitted flags, failed confirmations, stale candles, bad ATR, missing symbol/levels, rejected SL/TP updates, fills/orders lifecycle, duplicates, restart recovery, malformed payloads, kill-switch, disabled engine, paper/live mismatch.

Label any unchecked area **`not verified`**.

### Primary inspection order

**Execution safety:** scanner → engine scores → Engine C/trust → approval payload → `execution.py` → `auto_trader.py` → `risk_engine.py` → broker → monitor → audit/log → API/UI.

**Backtest/live parity:** BT signal + levels → live signal + levels → ATR/score/session/volume/feed → broker precision → monitor/audit.

**AI/vision:** prompt → payload → parser → footer/ratings/levels → review gate → execution handoff → logs/audit.

---

## 8. Engines & scoring

### Engine A — Factor confluence (primary)

- **Scoring:** `final_score` 0.0–3.0; directional `trend_score`; nondirectional `mom_quality`.
- **Thresholds:** profile override → pair/group YAML → 3-tier fallback.
- **Factors:** BTC bias (conditional on correlation), OI (crypto), intermarket confirmation.
- **Config:** `ENGINE_A`, `ENGINE_A_RESEARCH_LAB_FACTORS`, `ENGINE_A_MEAN_REVERSION`

**Audit concerns:** normalization, missing score group, threshold source drift, profile/override misuse, permissive fallback, BTC/OI misuse, live/BT mismatch.

### Engine B — Naked structure (SMC/ICT)

- **Scoring:** % of max; regime-gated thresholds.
- **Regime multipliers:** TRENDING 0.90, RANGING 0.90, HIGH_VOL 0.85, LOW_VOL 1.15.
- **Checklist:** swings, BOS, sweeps, FVG overlap, zone/trigger quality.
- **Styles:** scalp H1, intraday H4, swing D1 — each `min_score` + `min_rr`.
- **Config:** `NAKED_ENGINE.style_profiles`, `NAKED_MAX_DAILY`, `ENGINE_B_REGIME_MULTIPLIERS`

**Audit concerns:** AI review mistaken for approval, missing profile passes, regime math, RR mismatch, structure gate skipped by early return, live/BT mismatch, incomplete payload confirmations.

### Engine C — Consensus (A vs B)

- **Outputs:** calibrated probability, trust (`trust_a` / `trust_b` / `trust_both` / `trust_neither`), weights `{"A": x, "B": y}` summing to 1.0, conviction (`UPGRADE`/`NEUTRAL`/`DOWNGRADE`), decision state (trade, tier, sizing override, disagreement diagnosis).

**Audit concerns:** default-pass trust, weights ≠ 1, trades without proof, `trust_neither` still trading, bad conviction upgrades, sizing bypass, unlogged A/B mismatch, missing diagnosis in audit path.

### Engine D — Scalp lab (VP / order flow)

- Fabio Valentini VP + OF: balance/imbalance, VAL/VAH/POC/LVN.
- **Setups:** mean reversion (VA extreme → POC), trend continuation (pullback to LVN).
- **Grades:** A/B/C/D.
- **Three pillars:** market state + location + aggression — **all** must align (when strict mode applies per config).
- **Sessions:** NY open skip, London cash open, modes NY/London/Asia/All (per config/asset rules).
- **Config:** `SCALP_ENGINE`, `BT_*`

**Audit concerns:** missing pillar passes, grade D trades, session skip ignored, mixed volume sources, EODHD beyond volume-only role, live/BT mismatch, bad/missing POC or VA levels, aggression defaulting “on”.

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

- Review **`tasks/lessons.md`** for project-specific patterns.
- Review **`tasks/todo.md`** for open work.

### Planning & execution (Claude/planning loop)

- Use **plan mode** for non-trivial work (3+ steps or architecture).
- Re-plan if assumptions break or safety is at risk.
- Use **subagents** for parallel exploration; one focused task per subagent.
- After **user corrections**, append patterns to `tasks/lessons.md`.
- **Verify before done:** tests, logs, staff-engineer bar.
- For non-trivial edits, ask whether a cleaner design exists; avoid hacky fixes.
- **Autonomy:** fix reported bugs/CI using evidence; avoid hand-holding.

### Task files (when non-trivial or requested)

- **`tasks/todo.md`:** checkable items, completed markers, short review section.
- **`tasks/lessons.md`:** concise, actionable rules after corrections.

Task files **do not** replace running tests or proofs.

### Audit mode (explicit asks to audit/review/find bugs)

1. Inspect first; don’t patch unless asked.  
2. Producer→consumer map.  
3. Fail-closed behavior.  
4. Presence vs truth.  
5. Mode dispatch / early returns.  
6. Parity where relevant.  
7. Execution handoff.  
8. Ranked findings.  
9. Negative tests.  
10. Then fixes.  

Don’t stall on preamble — read code and run safe commands.

### Implementation mode

Short plan when non-trivial; smallest safe diff; config-gated; tests; summarize risk.

### When to stop and re-plan

Conflicting tests, execution-safety risk, architecture surprises, missing files/env failures, fixes that weaken gates or change locked scoring.

### Task tracking nuance

Use `tasks/todo.md` / `tasks/lessons.md` when work is large or the user wants durable tracking — not as a substitute for verification.

---

## 12. Verification

Before claiming **done / fixed / passing**, verify with the **smallest** relevant command.

If commands are unknown: check `README`, `pyproject.toml`, `pytest.ini`, `package.json`, CI config — then infer.

**Python example:**

```bash
pytest path/to/test_file.py -q
```

---

## 13. Backtest Analysis

### Key files

- `backtest_runner.py` — main orchestrator, engine-specific run logic
- `backtest.py` — legacy harness
- `backtest_candle_cache.py` — OHLCV cache (`candle_cache.db`)
- `run_backtest.py` — CLI runner
- `research_validation.py` — strategy validation layer

### Before interpreting results

Always verify:

- candle source is the same for signal generation and fill simulation
- signal was scored on a closed bar (`iloc[-2]`), not a forming bar (`iloc[-1]`)
- timeframe key in engine output matches what `execution.py` expects — known mismatch for swing-style forex (D1 vs H4 key) was fixed; confirm fix is present before trusting results
- `AUTO_TRADE_MIN_SCORE` is a dead config key — verify which threshold gate is actually being tested
- signal count vs trade count are consistent; large divergence indicates fill logic bug or RR filter too aggressive

### Engine A backtest checklist

- Hit rate near 50% across 200+ samples = noise, not edge; do not claim directional edge without chi-squared test
- `combinedConviction` structurally caps Engine A-only signals below the auto-trade gate; verify this is not the cause of zero auto-trades before investigating elsewhere
- Structure-first entry model: Engine B structural confirmation (BOS or CHoCH in direction) is required alongside the Engine A score gate; verify both gates are present in `backtest_runner.py`
- Forming-bar lookahead: confirm signal bar is `iloc[-2]` throughout

### Engine C backtest checklist

- Timeout rate above 10%: inspect `_monitor_fill_index` bisect call — the type mismatch between float price and list-of-dicts was a confirmed bug; verify the fix is applied before drawing conclusions
- `trust_neither` rate above 40%: signal quality issue upstream in Engine A or B, not an Engine C problem
- Weight sum must equal 1.0 at every decision; flag if not enforced

### Statistical validity gates

- 200 closed trades minimum for directional hit-rate conclusions
- 500 trades minimum for Sharpe/expectancy claims
- Always split long vs short hit rates; aggregated can mask directional bias
- Required report fields: trades, win_rate, avg_r, expectancy, max_drawdown_r, profit_factor

### Debugging workflow

1. Run with verbose/debug flag; count and categorize SKIP / NO_FILL / TIMEOUT entries
2. Dump first 10 signal records; verify field presence and types
3. Check candle alignment: signal bar close timestamp vs fill bar open timestamp
4. For Engine A: dump `factor_score_detail` for 5 sample signals; confirm normalization sum equals raw weight sum
5. For Engine C: confirm weight output sums to 1.0 and trust verdict is always one of the four valid states

---

## 14. Engine A — Structure-First Entry Redesign

### Context

Engine A's scorer has been confirmed as statistically near-random for directional hit-rate.
The approved fix is a structure-first entry model: Engine B structural confirmation is
required alongside (not instead of) the existing Engine A score gate.

### Design contract

A signal is a valid entry candidate only when ALL of the following pass:

1. Engine B structural confirmation: BOS or CHoCH in the correct direction, from `market_structure.py` / `zone_registry.py`
2. Structural recency: BOS/CHoCH within N candles of signal bar (configurable, default 5)
3. Direction agreement: Engine A `trend_score` direction matches Engine B BOS/CHoCH direction
4. Engine A score gate: `final_score >= threshold` (existing, unchanged)

The structure gate must be evaluated before the score gate. If structure fails, the signal is skipped — fail closed.

### Implementation target

The structure gate is added as a pre-filter in the signal loop inside `backtest_runner.py`:

- structure check runs first
- if structure check fails, `continue` — do not score
- score gate runs second
- only signals passing both gates are recorded

### Config gate

Add under `ENGINE_A` in `config.yaml`:

```yaml
ENGINE_A:
  structure_first_entry:
    enabled: true
    lookback_bars: 5
    require_bos: true
    require_choch: false
```

`enabled: false` must reproduce the original near-random baseline exactly.

### Verification checklist

Before marking this implementation complete:

- backtest with gate enabled vs disabled produces measurably different hit-rate
- `enabled: false` reproduces the near-random baseline
- no forming-bar lookahead in the structure check
- structure check uses closed bars only (`iloc[-2]`)
- direction mapping between Engine A `trend_score` and Engine B BOS/CHoCH direction is consistent and tested
- config key is respected; hardcoding is not acceptable
- focused test covers: gate enabled + structure fails = no signal, gate enabled + structure passes + score fails = no signal, gate enabled + both pass = signal recorded

---

## Maintaining root copies (`AGENTS.md`, `CLAUDE.md`)

Shared rules live in **`docs/agent-operating-guide.md`** (this document). After edits, regenerate root copies:

```bash
python tools/sync_agent_docs.py
```

---

*End of shared operating guide.*
