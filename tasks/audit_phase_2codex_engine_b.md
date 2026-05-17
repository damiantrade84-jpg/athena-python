# Engine B Nuclear Audit - Codex

Date: 2026-05-12

Scope: Engine B / Naked Market Structure / SMC / ICT audit-only review.

Files read in full before forming findings:

- `market_structure.py`
- `zone_registry.py`
- `engine_b_ai.py`
- `signal_debate.py`
- `backtest_runner.py` Engine B path
- `execution.py` Engine B signal consumption path
- `athena_app/services/structure_context.py`
- `config.yaml` Engine B / NAKED sections requested

Additional producer/consumer paths inspected for verification:

- `scanner.py`
- `athena.py`
- `auto_trader.py`
- `risk_engine.py`
- `config.py`
- `ai_schemas.py`
- `ai_safe_wrappers.py`

No patches were made during the audit. No tests were run because this was audit-only/read-only.

## BUG-B-1 - Quick Execute Rebuilds Generic Levels For Structural Engine B Signals

Severity: HIGH

File: `execution.py:775`

What: `/api/quick-execute` rejects stale or failed Engine B context, but if confirmed Engine B levels are missing, it falls through to `recompute_levels_for_style()` and may continue with generic levels at `execution.py:775-810`.

Should: Structural Engine B execution should fail closed when Engine B execution levels are missing, matching `/api/execute` at `execution.py:1635-1647`.

Impact: A confirmed Engine B setup can execute with non-Engine-B SL/TP, bypassing the structural level contract and style `min_rr` assumptions.

Fix:

```python
if _has_engine_b_context and not _apply_engine_b_execution_levels(sig, engine_b):
    return jsonify({
        "error": "ENGINE_B_LEVELS_UNAVAILABLE: structural execution levels missing",
        "pair": sig.get("pair"),
    }), 409
```

Test: POST `/api/quick-execute` with `is_naked: true`, `passed: true`, no `execution_sl`/`execution_tp`; assert HTTP 409.

## BUG-B-2 - Level Override Can Bypass Engine B Style Min RR

Severity: HIGH

File: `execution.py:812`, `execution.py:1648`, `risk_engine.py:835`

What: `level_override` is applied after Engine B levels in quick and normal execute paths. `_apply_level_override()` validates side/positivity, but not Engine B `min_rr`; `risk_check()` only enforces geometric min RR for consensus-like signals, not standalone `is_naked` Engine B.

Should: Any override on structural Engine B must satisfy the resolved Engine B style `min_rr`.

Impact: Manual/AI level override can reduce RR below Engine B's accepted threshold and still pass risk.

Fix: compute `abs(tp1 - entry) / abs(entry - sl)` after override for Engine B context and reject below resolved `style_profile["min_rr"]`.

Test: Engine B `is_naked` signal with `min_rr=1.5`, override RR 0.8; assert `/api/execute` and `/api/quick-execute` both reject.

## BUG-B-3 - Regime Multipliers Are Rounded Into Non-Effects Or Inconsistent Gates

Severity: MEDIUM

File: `market_structure.py:258`, `config.yaml:2040`

What: threshold is `round(min_score * multiplier)`. For `min_score=3`, HIGH_VOL `3 * 0.85 = 2.55 -> 3`, LOW_VOL `3 * 1.15 = 3.45 -> 3`; both become neutral. For swing `4 * 0.85 = 3.4 -> 3`, `4 * 1.15 = 4.6 -> 5`.

Should: Multipliers should have explicit, documented effective behavior, not banker/binary rounding artifacts.

Impact: Operator calibration is misleading; scalp/intraday regime multipliers do nothing while swing multipliers change gates materially.

Fix: use explicit policy, e.g. `ceil()` for strictness or keep decimal thresholds.

Test: assert effective thresholds for all styles/regimes match the intended table.

## BUG-B-4 - `min_room_atr` Config Overrides Are Ignored

Severity: MEDIUM

File: `market_structure.py:2806`, `market_structure.py:2901`

What: `min_room_atr` is read from the style profile, but the actual room gate uses hardcoded `_get_min_room_atr()` values. Config overrides such as `config.yaml:1908` and `config.yaml:1924` do not drive the gate.

Should: Profile/config `min_room_atr` should be the base policy or explicitly removed.

Impact: Forex exotics and DOGE room calibration does not apply in production scoring.

Fix:

```python
_effective_min_room_atr = float(
    profile.get("min_room_atr", _get_min_room_atr(rr, bool(res.get("bos_confirmed")), asset_type_lower, exec_style))
)
```

Test: set `min_room_atr=1.2`, build room distance 0.8 ATR; assert `room_ok=False`.

## BUG-B-5 - `ENGINE_B_FOREX_ADX_MIN` Is Dead As A Gate

Severity: MEDIUM

File: `config.yaml:2088`, `market_structure.py:2840`

What: config documents an ADX floor, but `calculate_confidence()` only derives `_adx_derived_regime`; it never reads `ENGINE_B_FOREX_ADX_MIN` or blocks low ADX.

Should: Either enforce the floor or rename/comment it as diagnostic-only.

Impact: Operators can tune `ENGINE_B_FOREX_ADX_MIN: 12` expecting execution impact, but it does nothing.

Fix: read the key and add a diagnostic-only name, or gate `structure_ok` if that is intended.

Test: forex ADX below configured floor should produce expected gate behavior.

## BUG-B-6 - Follow-Through Bonus Can Distort Score Denominator When Enabled

Severity: MEDIUM

File: `market_structure.py:3010`, `market_structure.py:3035`

What: `ENGINE_B_FOLLOW_THROUGH.ENABLED` adds bonus/penalty to `total_score`, but `max_possible` remains `gate_max + 3 + profile`.

Should: Any enabled scoring factor must be included in max score or kept diagnostics-only.

Impact: If enabled, raw scores and percent calibration drift; a marginal setup can cross `min_score` due to a factor absent from the denominator.

Fix: include `MAX_BONUS` in `max_possible` when enabled, or never apply it to `total_score`.

Test: enable follow-through, assert `max_possible` increases and percent remains mathematically consistent.

## BUG-B-7 - Confirmed-Only Structure Helper Fails Open

Severity: MEDIUM

File: `market_structure.py:56`

What: `_engine_b_confirmed_only_struct_candles()` returns original candles when pair context is missing, confirmed bars are under minimum, or `split_market_state()` throws.

Should: With confirmed-only structure policy enabled, failure to prove confirmed bars should fail closed or emit a hard diagnostic.

Impact: Direct callers can leak forming bars into BOS/CHoCH despite `ENGINE_B_USE_FORMING_FOR_STRUCTURE: false`. Current traced API callers pass pair context, so live leakage is not verified.

Fix: return confirmed subset when available; otherwise return `[]` plus reason.

Test: monkeypatch `split_market_state()` to raise; assert structure analysis does not use last forming candle.

## BUG-B-8 - Zone Persistence SQLite Path Violates Repo DB Safety Rules

Severity: MEDIUM

File: `zone_registry.py:211`, `zone_registry.py:281`

What: when `ENGINE_B_ZONE_PERSISTENCE` is enabled, SQLite connections use no timeout/WAL and persistence rewrites via `DELETE FROM zones` then insert all rows.

Should: Use WAL and timeout, and avoid full-table rewrites where possible.

Impact: Concurrent scans can lock or lose zone state if persistence is enabled. Currently dormant because `config.yaml:2056` is false.

Fix: `sqlite3.connect(..., timeout=15.0)` plus `PRAGMA journal_mode=WAL`; upsert rows transactionally.

Test: concurrent zone upserts with persistence enabled should not throw lock errors.

## BUG-B-9 - Zone TTL Config Is Misplaced For ZoneRegistry

Severity: LOW

File: `zone_registry.py:108`, `config.yaml:2388`

What: `ZoneRegistry.prune_old_zones()` reads root `ZONE_CACHE_TTL_HOURS`, but YAML defines it under `SCALP_ENGINE`.

Should: Read the actual configured path or move the key to root.

Impact: crypto 72h zone TTL is ignored; default 168h applies.

Fix:

```python
ttl_config = (
    config.CONFIG.get("ZONE_CACHE_TTL_HOURS")
    or (config.CONFIG.get("SCALP_ENGINE", {}) or {}).get("ZONE_CACHE_TTL_HOURS", {})
)
```

Test: crypto zone older than 72h prunes without passing explicit `max_age_hours`.

## BUG-B-10 - FVG Detection Has No Minimum Gap Filter

Severity: LOW

File: `market_structure.py:1500`, `market_structure.py:1544`

What: any `prev_low > next_high` or `prev_high < next_low` becomes an FVG; there is no ATR/tick minimum.

Should: Micro-gaps should be filtered with configurable min gap size if FVG overlap is used as signal context.

Impact: noisy FVGs can mark zones as overlapping and influence diagnostics/context.

Fix: add `NAKED_ENGINE.fvg_min_gap_atr_mult` and skip gaps below threshold.

Test: gap of `0.01 ATR` should not create active FVG when min is `0.05`.

## Threshold Assessment - Engine B

`NAKED_ENGINE.style_profiles.scalp.min_score=3.0` - TOO_LOOSE. Checklist pass already requires 5 mandatory gates, so the score floor is normally non-binding.

`NAKED_ENGINE.style_profiles.intraday.min_score=3.0` - TOO_LOOSE. Same non-binding math as scalp.

`NAKED_ENGINE.style_profiles.swing.min_score=4.0` - TOO_LOOSE except LOW_VOL. A passed 5-gate setup clears 4; LOW_VOL rounds to 5 and can bind.

`ENGINE_B_REGIME_MULTIPLIERS.HIGH_VOLATILITY=0.85` - TOO_LOOSE/NEUTRALIZED. Applied to threshold, so it lowers the gate; rounding neutralizes it for min_score 3.

`ENGINE_B_REGIME_MULTIPLIERS.LOW_VOLATILITY=1.15` - TOO_STRICT/NEUTRALIZED. Applied to threshold, so it raises the gate; rounding neutralizes it for min_score 3 but raises swing 4 to 5.

`NAKED_ENGINE.*.min_rr` - CALIBRATED MECHANICALLY / PERFORMANCE NOT VERIFIED. Enforced in confidence and backtest; not re-enforced after execution level override for standalone Engine B.

`ENGINE_B_PROFILE_SCORING_ENABLED=true` - CALIBRATED WITH CAVEAT. `max_possible` includes +1 and score can add up to +1; it cannot override `passed=False`, but can help raw score floor.

`ENGINE_B_FOREX_ADX_MIN=12` - DEAD. Diagnostic comments are accurate; no gate reads it.

`ENGINE_B_ROOM_GATE_REQUIRE_DISTANCE=true` - OVERKILL/HARDCODED. Fails closed on unknown distance, but effective distance thresholds ignore configured `min_room_atr`.

## Dead Code - Engine B

`ENGINE_B_FOREX_ADX_MIN` - `config.yaml:2088`; production Engine B does not read it as a floor.

`NAKED_ENGINE.score_group_overrides.*.min_room_atr` - examples at `config.yaml:1908`; read value is not used by room gate.

`ZONE_CACHE_TTL_HOURS` for Engine B registry - configured under `config.yaml:2388`, but consumer reads root at `zone_registry.py:108`.

`ZoneRegistry.mark_mitigated(atr)` parameter - `zone_registry.py:62`; immediately discarded at line 69.

`PAIR_PROFILES` Engine B entries - NOT PRESENT in inspected `config.yaml:1657` block; current entries are Engine A factor profile fields.

## Verified Non-Bugs / Clarifications

`max_possible` is dynamic: mandatory gates 5 or 6 plus 3 bonuses plus optional profile point, from `market_structure.py:3030`.

A signal can reach 100% if all mandatory gates, three bonuses, and profile point pass. With follow-through enabled it can exceed the modeled denominator but `pct` is capped at 100.

BOS is close-based against prior swing, scanning the configured last 5 bars by default, not wick-only: `market_structure.py:1126`.

CHoCH is close-based when closes exist; BOS reference levels are used when BOS context exists: `market_structure.py:1220`.

Engine B AI is advisory-only in the inspected path; failures become `ai_analysis` grade `N/A`, not approval: `athena.py:5991`.

Signal debate is an auto-trader gate, not a structure-gate override; it can block or downgrade, not repair a failed Engine B checklist: `auto_trader.py:821`.

`ENGINE_B_SCAN_CONFIRMATION_GATE_ENABLED=false` means scanner does not demote Engine A trade-tier rows when `enginesAligned` is false; enabling it applies demotion at `scanner.py:1101`.

## Not Verified

Empirical pass-rate distribution from live/backtest data was not verified. This audit did not run backtests or query production logs.

Broker order placement and monitor/audit persistence beyond the `execution.py -> risk_engine.py` boundary were not fully verified.

UI rendering was not verified.
