# Engine B (Naked Market Structure) — Nuclear Audit Report

**Audit Date:** 2026-05-12
**Scope:** market_structure.py, zone_registry.py, engine_b_ai.py, signal_debate.py, backtest_runner.py (Engine B path), execution.py (Engine B consumption), structure_context.py
**Mode:** Audit-only — no patches applied

---

## Files Inspected

| File | Lines | Role |
|------|-------|------|
| market_structure.py | 3322 | Core: BOS, CHoCH, OB, FVG, zones, confidence scoring, regime gate |
| zone_registry.py | 311 | Zone persistence, upsert/merge, prune, SQLite backing |
| engine_b_ai.py | 648 | AI review (advisory only), prompt build, grade validation |
| signal_debate.py | 513 | Bull/Bear/Judge debate for auto-trade gate |
| backtest_runner.py L3504–4392 | ~890 | Engine B standalone backtest loop |
| backtest_runner.py L4398–5056 | ~660 | Engine C consensus backtest (A+B merge) |
| execution.py L118–440 | ~320 | Engine B signal consumption, level extraction, gate checks |
| execution.py L1050–1300 | ~250 | Engine C scan: NakedEngine instantiation, direction loop |
| structure_context.py | 89 | Engine A score modulation from Engine B structural context |
| config.yaml L1800–1900 | ~100 | NAKED_ENGINE.style_profiles, zone_multipliers, score_group_overrides |

---

## Section 1 — Scoring Mathematics

### 1.1 max_score Determination

`calculate_confidence()` builds a **dynamic** `max_possible` from the sum of enabled checklist items + bonus items. It is **not** a fixed constant. The observed value depends on which features are enabled by config and which gates are evaluated.

**Base checklist points** (6 mandatory gates): Each gate awards a fixed number of points when passed. The `max_possible` is the sum of all awardable points — including bonus items (MTF alignment, FVG overlap, OB presence, volume confirmation, etc.).

**Regime scaling** is applied *after* scoring via `engine_b_min_score_threshold()`:

```
base_min × regime_multiplier → rounded to nearest int
```

> **CRITICAL:** The `round()` in `engine_b_min_score_threshold` (L268) means a score floor of 3.0 × 0.85 = 2.55 becomes **3** (rounded up), not 2. This is **fail-safe** (harder threshold). However, 3.0 × 0.90 = 2.70 → rounds to **3** (same as base), making TRENDING/RANGING multipliers effectively no-ops for `min_score=3`. Only LOW_VOLATILITY (1.15 → 3.45 → **3**) and HIGH_VOLATILITY (0.85 → 2.55 → **3**) also resolve to 3 — meaning ALL regime multipliers produce identical outcomes for min_score=3.

### 1.2 Score Percentage

`pct = (score / max_possible) * 100`. This is correctly computed and bounded. The `engine_b_confidence_passes` gate checks both `conf.passed` AND `score >= min_score_scaled`, requiring **both** the checklist pass and the floor.

### 1.3 Engine B AI Score Interaction

Engine B AI (`engine_b_ai.py`) is **advisory only** — it never modifies the `passed` verdict or the `score`. The AI grade, edgeProbability, and riskLevel are attached to the signal payload for operator display but do not flow into execution gates.

**AI ADVISORY CONTRACT CONFIRMED:** `execution_allowed_before_ai=True, execution_allowed_after_ai=True, final_action="advisory"` at engine_b_ai.py L628–630. No gate bypass.

---

## Section 2 — Gate Logic

### 2.1 Six Mandatory Gates

| Gate | Purpose | Fail-Closed? |
|------|---------|:------------:|
| `structure_ok` | BOS or CHoCH confirmed in direction | ✅ |
| `location_ok` | Price near active S/R zone | ✅ |
| `entry_ok` | Trigger pattern detected (rejection, engulfing, etc.) | ✅ |
| `room_ok` | Sufficient distance to opposing zone (ATR-scaled) | ✅ |
| `rr_ok` | Risk:reward meets style minimum | ✅ |
| `macro_ok` | D1 swing alignment (swing-only, config-gated per asset) | ✅ |

All gates default to `False` and must be explicitly set to `True` by evidence. Confirmed fail-closed.

### 2.2 Structure Gate Relaxation (`disable_structure_gate`)

When `ENGINE_B_BT_STRUCTURE_GATE_ENABLED: false`, the BT loop sets `style_profile["disable_structure_gate"] = True`. This propagates into `calculate_confidence`, which skips the structure check when this key is True.

This flag exists in backtest only (`backtest_runner.py` L3634–3637). There is no corresponding live scan path that sets `disable_structure_gate`. BT experiments with this flag will show inflated trade counts that cannot be replicated live. This is **by design** (experiment-only toggle).

### 2.3 Room Gate

The room gate (`ROOM_GATE_REQUIRE_DISTANCE`) checks that the opposing zone is far enough from entry to sustain the R:R target. Distance threshold = `min_room_atr × ATR`. If the opposing zone is closer than this, the trade is rejected. No findings.

### 2.4 Macro Gate

Controlled per-asset via `require_macro_align` in config.yaml style_profiles. Currently:

- Forex: false
- Crypto: false
- Stock: false
- Index: false
- Commodity: **true** (only asset class with macro gate active for swing)

Macro alignment is disabled for all asset classes except commodity swing. This is intentional config.

---

## Section 3 — Zone Registry

### 3.1 SQLite Compliance

**BUG-001 — ZONE_REGISTRY_NO_WAL_NO_TIMEOUT (Severity: HIGH)**

`zone_registry.py` uses `sqlite3.connect(self._db_path)` with **no timeout** and **no WAL mode pragma** (L211, L233, L281). Per AGENTS.md rules ("Database Concurrency: Always use timeout=1.0 and PRAGMA journal_mode=WAL"), this violates the project's SQLite safety contract.

- **Impact:** Under concurrent read/write from multiple scan threads, the zone DB can lock up with `database is locked` errors. The in-memory `_lock` (threading.Lock) protects the Python dict, but does NOT protect the SQLite operations which happen inside `_persist_locked`, `_load_from_db`, and `_ensure_schema`.
- **Trigger:** Enabled only when `ENGINE_B_ZONE_PERSISTENCE: true`. If persistence is disabled (default appears false), this is dormant.
- **Fix:** Add `PRAGMA journal_mode=WAL` and `timeout=15.0` to all `sqlite3.connect()` calls.

### 3.2 Delete-All-Then-Insert Pattern

**BUG-004 — ZONE_PERSISTENCE_FULL_TABLE_DELETE (Severity: MEDIUM)**

`_persist_locked` (L259–292) does:

```python
conn.execute("DELETE FROM zones")
conn.executemany("INSERT INTO zones ...", flat_rows)
conn.commit()
```

Every upsert/mark_mitigated call that touches the DB deletes ALL rows and re-inserts the full in-memory state. A crash between DELETE and COMMIT loses all persisted zone data. Without WAL mode, this is a full table lock for the duration.

- **Mitigation:** The in-memory `_zones` dict is the authoritative source; the DB is a cold-start recovery mechanism. Data loss on crash means zones are rebuilt on next scan cycle. Not catastrophic, but violates durability expectation of "persistence."
- **Fix:** Use UPSERT (INSERT OR REPLACE) with a composite primary key.

### 3.3 Zone Pruning Logic

`prune_old_zones` removes zones that are both stale AND untouched (`scan_count <= 1` and not mitigated). Zones that were scanned more than once or were mitigated are kept regardless of age.

**BUG-007 — PRUNE_KEEPS_MITIGATED_FOREVER (Severity: LOW)**

Mitigated zones are never pruned. Over months of operation, the zone dict and DB can grow unboundedly with mitigated entries. No impact on correctness (`get_active_zones` filters them), but memory/storage grows.

- **Fix:** Add a max-age prune for mitigated zones (e.g., 30 days).

### 3.4 `asset_type` Field

**BUG-005 — ZONE_ASSET_TYPE_LOST_ON_RESTART (Severity: LOW)**

`asset_type` is stored per-zone in `upsert_zones` but **not** persisted or loaded from DB. The `asset_type` field is not in the `zones` table schema (L214–228). `upsert_zones` sets `zone["asset_type"]` on the in-memory dict (L48), and `prune_old_zones` uses `zone.get("asset_type", "unknown")` to look up TTL config. After a restart, all zones will fall back to `"unknown"` TTL (defaulting to forex's 168h).

- **Fix:** Add `asset_type TEXT DEFAULT 'unknown'` column to schema and persist/load it.

---

## Section 4 — AI Integration (engine_b_ai.py)

### 4.1 Safety Contract

- AI review is **advisory only**. No gates are modified.
- Missing/malformed AI fields default to safe values: grade `C`, edgeProbability `50`, riskLevel `Medium`.
- Structured output (Pydantic `EngineBResponse`) is attempted first; falls back to `json_object` legacy.
- Retry with exponential backoff on transient failures.

No critical findings. AI integration is correctly isolated.

### 4.2 Prompt Injection Surface

The AI prompt (L526–543) includes user-provided signal data (pair name, prices, structural results). Since this flows to xAI API only (not executed locally), the injection surface is limited to model manipulation. Grade validation (L197–202) and value clamping ensure malicious AI outputs are sanitized.

### 4.3 Signal Debate (signal_debate.py)

- `FORCE_DEBATE_DOWNGRADE_ONLY` ensures debate can only **reduce** scores, never boost them (L54–60).
- `DEBATE_FAILURE_DEFAULTS_TO_BLOCK` defaults to block on error — fail-closed.
- `_signal_max_score` correctly handles Engine D (1.0) vs Engine A (3.0) scales.

When both Bull/Bear LLM responses fail to parse, the fallback conviction is `5/10` (neutral). The Judge then outputs `PASS` (its error fallback), which **blocks** execution (`allowed = grade in ("STRONG_GO", "WEAK_GO")`). End result is fail-closed despite neutral defaults. **Confirmed safe.**

---

## Section 5 — Backtest Parity

### 5.1 Engine B Standalone Backtest

| Check | Status |
|-------|--------|
| Signal generation parity (same `analyze_structure` + `calculate_confidence` + `engine_b_confidence_passes`) | ✅ |
| Entry timing (signal at bar `i`, fill at `entry_raw[i+1]` open) | ✅ |
| Forming bar (uses `candles[:-1]`, walks closed bars only) | ✅ |
| ATR resolution (precomputed O(1) with proper TF alignment) | ✅ |
| SL/TP source (uses `conf_data.execution_sl/tp`, calc_levels fallback) | ✅ |

**BUG-006 — BT_DUPLICATE_EXIT_CHECK (Severity: LOW)**

The BT exit loop (L4072–4173) has **two** barrier-checking passes for each bar. First, `_resolve_barrier_exit()` is called at L4074. If it doesn't find a hit, the loop *then* manually checks TP/SL *again* at L4133–4172. These are **redundant** — `_resolve_barrier_exit` already handles LONG/SHORT SL/TP detection.

The duplication is not harmful (the first check breaks on hit, so the manual check only runs on "none" bars), but the manual check includes its own BE logic that could theoretically diverge from the centralized barrier resolver.

- **Fix:** Remove the inline manual TP/SL/BE check, keep only `_resolve_barrier_exit`.

### 5.2 Engine C Backtest

**BUG-002 — ENGINE_C_BT_MFE_MAE_NEVER_POPULATED (Severity: MEDIUM)**

The Engine C BT loop (L4857–4867) initializes MFE/MAE tracking variables (`max_favorable_excursion_r`, etc.) but **never updates them** inside the exit monitoring loop (L4869–4897). The exit loop uses `_resolve_barrier_exit` + inline BE check but omits the per-bar R tracking that Engine B BT correctly implements at L4098–4131.

- **Impact:** Engine C BT trade records always show `max_favorable_excursion_r: 0.0`, `max_adverse_excursion_r: 0.0`, `highest_r_seen: 0.0`, `lowest_r_seen: 0.0`, `bars_to_mfe: None`, `bars_to_mae: None`. Any downstream analytics (calibration, meta-learner) that relies on these fields for Engine C will get zeroes.
- **Fix:** Copy the per-bar R tracking loop from Engine B BT (L4098–4131) into the Engine C exit loop.

---

## Section 6 — Execution Handoff

### 6.1 Engine B Level Extraction

`_extract_engine_b_execution_levels` (execution.py L406–439) searches through four candidate sources in order: `naked_data`, `engine_b`, standalone engine_b dict, and finally the signal itself. It validates both SL and TP are positive floats before accepting.

**Confirmed fail-closed**: Returns `None` if no valid levels found, and `_apply_engine_b_execution_levels` returns `False`, triggering the `recompute_levels_for_style` fallback.

### 6.2 Engine B Confirmation Gate

`_engine_b_context_confirmed` (execution.py L387–403):

- If signal has Engine B context but no `passed` field → returns `False` (fail-closed) ✅
- If `enginesAligned` is present → uses that boolean ✅
- Final fallback: `return False` ✅

### 6.3 Stale Signal Rejection

Signals with Engine B context that are older than `SIGNAL_MAX_AGE_SEC / 2` or missing price return HTTP 409 `ENGINE_B_REFRESH_REQUIRED` (L720–726). Non-Engine-B signals get a `analyze_pair` refresh attempt.

Engine B stale signals get a hard 409 rejection with no refresh attempt — the operator must retrigger from the scanner. This is more conservative than Engine A (which tries to refresh). Intentional design: structural analysis is expensive and context-sensitive.

### 6.4 Engine C Scan (execution.py)

The Engine C scan (L1089–1300+) instantiates a fresh `NakedEngine()`, iterates both LONG/SHORT directions, selects the best by checklist score, and gates through `_engine_c_accepts_engine_b` (which calls `engine_b_confidence_passes`).

The direction loop keeps TWO tracking pairs: `sig_b_best/conf_b_best` (gated passed) and `sig_b_candidate_best/conf_b_candidate_best` (best regardless of gate). This allows Engine C to see *what* Engine B thought even if the gate failed, for diagnostic/logging purposes. The actual consensus call uses the gated version. Safe by design.

---

## Section 7 — Structure Context Service

`apply_structure_context_to_score` (structure_context.py L6–88):

- Only applies when `structural_verdict == "CLEAR"`.
- Multiplier is clamped to `[0.85, 1.20]`.
- Direction opposition gives `-0.08` (penalty), alignment gives `+0.04` (bonus).
- Max bonus = zone_proximity(0.08) + ob(0.05) + fvg(0.05) + sweep(0.04) + aligned(0.04) = **0.26** → clamped to **1.20** multiplier.
- Max penalty = opposed direction = **-0.08** → multiplier = **0.92**.

When Engine B reports CLEAR + zone + OB + FVG + sweep + aligned direction, the multiplier is always 1.20 (capped). This gives Engine A a free 20% score boost from structural alignment, which could push borderline signals over threshold. This is by design but should be monitored for inflation.

---

## Section 8 — Config Consistency

### 8.1 style_profiles vs Hardcoded Defaults

The config.yaml style_profiles (L1825–1862) match the expected shape. `config.py` L923 provides Python-side defaults. No drift detected.

### 8.2 Regime Multipliers

Per AGENTS.md: `TRENDING 0.90, RANGING 0.90, HIGH_VOL 0.85, LOW_VOL 1.15`. The code defaults at market_structure.py L16–21 match exactly. Config-level `ENGINE_B_REGIME_MULTIPLIERS` can override, with per-asset nesting supported.

---

## Ranked Bug List

| # | Severity | File | Finding ID | Description |
|---|----------|------|------------|-------------|
| 1 | **HIGH** | zone_registry.py | BUG-001 | No WAL mode, no timeout on SQLite connects. DB locking under concurrent scan threads when persistence enabled. |
| 2 | **MEDIUM** | backtest_runner.py L4857–4897 | BUG-002 | Engine C BT never populates MFE/MAE/bars_to_mfe/mae tracking variables. All Engine C trade diagnostics show zeroes. |
| 3 | **MEDIUM** | market_structure.py L258–268 | BUG-003 | `round()` on regime-scaled min_score makes regime multipliers a no-op for min_score=3. All regimes resolve to identical floor for scalp/intraday. |
| 4 | **MEDIUM** | zone_registry.py L259–292 | BUG-004 | DELETE-all + INSERT-all persistence pattern. Zone data loss on mid-transaction crash; excessive I/O for single-zone updates. |
| 5 | **LOW** | zone_registry.py L214–228 | BUG-005 | `asset_type` not in DB schema. Per-asset TTL uses wrong default ("unknown") after cold restart. |
| 6 | **LOW** | backtest_runner.py L4072–4173 | BUG-006 | Duplicate barrier exit check (centralized `_resolve_barrier_exit` + inline manual). Potential for divergence. |
| 7 | **LOW** | zone_registry.py L96–98 | BUG-007 | Mitigated zones never pruned. Unbounded memory growth over months. |
| 8 | **INFO** | market_structure.py L24–53 | INFO-001 | Asian session skip uses UTC hour 22–07. Correct for UTC, but MT5 SAST bars may need offset consideration. |
| 9 | **INFO** | execution.py L720–726 | INFO-002 | Engine B stale signals get hard 409 (no refresh) vs Engine A (refresh attempt). Intentional — shorter execution window for Engine B signals. |

---

## Recommended Fixes (Priority Order)

### BUG-001 — Zone Registry SQLite Safety (HIGH)

Add WAL mode and timeout to all `sqlite3.connect()` calls in `zone_registry.py`:

```python
# In _ensure_schema, _load_from_db, _persist_locked:
with sqlite3.connect(self._db_path, timeout=15.0) as conn:
    conn.execute("PRAGMA journal_mode=WAL")
    # ... existing logic
```

### BUG-002 — Engine C BT MFE/MAE (MEDIUM)

Copy the per-bar R tracking from Engine B BT (L4098–4131) into the Engine C exit loop (after L4897). The tracking loop measures `bar_r_high`, `bar_r_low`, `bar_r_close` and updates `max_favorable_excursion_r`, `max_adverse_excursion_r`, `bars_to_mfe`, `bars_to_mae`, `highest_r_seen`, `lowest_r_seen`, `price_never_reached_tp`, `price_never_reached_sl`.

### BUG-003 — Regime Scaling No-Op (MEDIUM)

Replace `round(scaled)` with `round(scaled, 1)` in `engine_b_min_score_threshold` to preserve regime differentiation at one decimal place. This changes the score comparison from integer to float, which is compatible since `score` from `calculate_confidence` is already a float.

Alternatively, use `math.ceil(scaled * 10) / 10` for a fail-safe (always rounds up) approach.

### BUG-004 — Zone Persistence Pattern (MEDIUM)

Replace DELETE+INSERT-all with an UPSERT approach using a composite primary key `(symbol, timeframe, type, direction, bottom)`. This eliminates the crash-window data loss risk and reduces I/O.

---

## Negative-Case Tests Recommended

| # | Test Name | Target File | Purpose |
|---|-----------|-------------|---------|
| 1 | `test_regime_scaling_noop_for_min_score_3` | market_structure.py | Prove that regime multipliers ≠ 1.0 produce identical `engine_b_min_score_threshold` when base=3 |
| 2 | `test_zone_registry_wal_and_timeout` | zone_registry.py | Verify SQLite connects use WAL + timeout after fix |
| 3 | `test_zone_asset_type_survives_restart` | zone_registry.py | Persist→reload→prune round-trip with asset_type |
| 4 | `test_engine_c_bt_mfe_mae_populated` | backtest_runner.py | Run Engine C BT and assert MFE/MAE fields are non-zero for at least some trades |
| 5 | `test_zone_persistence_crash_recovery` | zone_registry.py | Kill process between DELETE and COMMIT, verify recovery |
| 6 | `test_engine_b_ai_never_modifies_passed` | engine_b_ai.py | Confirm `passed` field is unchanged after AI review |
| 7 | `test_debate_both_parse_fail_blocks` | signal_debate.py | Both Bull/Bear return parse errors → Judge PASS → `allowed=False` |
| 8 | `test_structure_gate_disable_live_absent` | execution.py | Confirm `disable_structure_gate` is never set in any live scan path |

---

## Areas NOT Verified

| Area | Reason |
|------|--------|
| `calculate_confidence` full checklist point allocation math | 800+ lines; verified gating structure and fail-closed defaults but not every point value |
| `analyze_structure` BOS/CHoCH detection algorithm correctness | Algorithmic review of swing detection not audited for mathematical correctness |
| `_determine_independent_direction` weighting accuracy | Advisory-only output; weight calibration not verified |
| Engine B → Telegram notification path | Not in scope files |
| `timed_exit_monitor.py` Engine B exit pipeline | Not in scope |
| Vision/chart analysis Engine B interaction | Not present in scope files |
| Live auto-trader → Engine B execution end-to-end | Requires `auto_trader.py` inspection (out of scope) |
| `STYLE_ATR_MULTS` per-asset-class calibration | Referenced in `resolve_engine_b_asset_class` but actual multiplier values not fully traced |

---

## Audit Completion Checklist

- [x] Files inspected: 10 (listed above)
- [x] Functions/classes inspected: NakedEngine, ZoneRegistry, engine_b_confidence_passes, engine_b_min_score_threshold, _engine_b_regime_gate, resolve_engine_b_asset_class, _extract_engine_b_execution_levels, _engine_b_context_confirmed, apply_structure_context_to_score, run_signal_debate, get_engine_b_ai_verdict, build_engine_b_signal_message, backtest_pair_naked, backtest_pair_consensus
- [x] Execution paths traced: signal generation → confidence gate → execution level extraction → broker handoff; BT signal → fill → exit
- [x] Commands/tests run: None (audit-only mode)
- [x] Areas not verified: Listed above
- [x] Ranked bug list with evidence: 9 findings
- [x] Recommended negative-case tests: 8 tests listed
