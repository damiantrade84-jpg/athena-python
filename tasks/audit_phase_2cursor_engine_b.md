# Nuclear-grade audit — Engine B (Naked Market Structure / SMC / ICT)

**Scope:** Audit-only (no patches). Sentinel Pro v4.

**Sources inspected:** `market_structure.py`, `zone_registry.py`, `engine_b_ai.py`, `signal_debate.py`, `config.yaml` (Engine B sections + `PAIR_PROFILES` sample), `backtest_runner.py` (Engine B naked + Engine C B branches), `execution.py` (Engine B consumption), `athena_app/services/structure_context.py`, plus verification via `scanner.py`, `athena.py`, `risk_engine.py`, `factor_scoring.py`, `engine_c.py`, and repo-wide grep for `ENGINE_B_FOREX_ADX_MIN` and related keys.

---

## Section 1 — Scoring mathematics

### `max_possible` / percentage

- Not a fixed constant.
- `gate_max_possible = len(gate_confirmations)` (5 or 6 when macro is required).
- `bonus_count = 3 + _profile_points_max` where `_profile_points_max` is 1 if `ENGINE_B_PROFILE_SCORING_ENABLED` else 0.
- `max_possible = gate_max_possible + bonus_count`.
- `pct = round((total_score / max_possible) * 100)` (clamped 0–100).

**Evidence:** `market_structure.py` ~3030–3077.

### `gate_score` / checklist weighting

- `gate_score` is the **count** of true mandatory gates (each worth **1 point**). Not separate YAML weights per legacy checklist label.

**Evidence:** `market_structure.py` ~3030–3032.

### Mapping checklist concepts → gates

Swing sequence, BOS, sweep, FVG overlap, zone proximity, trigger patterns feed **`structure_ok`**, **`location_ok`**, **`entry_ok`**, etc., as **combined booleans**, not separate weighted line items in `pct`.

Bonuses are separate: `bos_mtf`, `ob_at_zone`, `volume_ok`, optional follow-through (`ENGINE_B_FOLLOW_THROUGH`), optional profile points (`ENGINE_B_PROFILE_SCORING_ENABLED`).

**Evidence:** `market_structure.py` ~2799–3007.

### Regime multiplier

- Applied to **`min_score` threshold**, not to raw score: `engine_b_min_score_threshold` multiplies base `min_score` by `_engine_b_regime_gate`, then rounds.
- **`engine_b_confidence_passes`** requires `score >= min_score_scaled` **and** `conf["passed"]`.

**Evidence:** `market_structure.py` ~258–268, ~425–441.

### Arithmetic (HIGH_VOL vs LOW_VOL)

- `min_score_scaled = round(base_min * multiplier)`.
- **HIGH_VOL 0.85** lowers the numeric threshold → **easier** to satisfy `score >= min_score_scaled`.
- **LOW_VOL 1.15** raises it → **harder**.

This matches the file header comment (“HIGH_VOL = easier (lower threshold), LOW_VOL = harder”).

**Evidence:** `market_structure.py` ~14–21, ~235–268.

### Unknown / missing regime

- `_engine_b_regime_gate` uses config map then `ENGINE_B_REGIME_GATE_DEFAULTS.get(regime_key, 1.0)`; empty/unknown regime key → **multiplier 1.0** (neutral).

**Evidence:** `market_structure.py` ~235–255.

### `min_rr` enforcement

- In `calculate_confidence`: `rr_ok = rr >= min_rr` after `resolve_engine_b_execution_levels` (which can extend TP when RR is below `min_rr`).

**Evidence:** `market_structure.py` ~2928–2943, ~838–851 (`resolve_engine_b_execution_levels`).

- Naked backtest also rejects candidates with `rr < style_profile["min_rr"]`.

**Evidence:** `backtest_runner.py` ~3931–3935.

### Profile scoring vs failing gates

- `passed` is purely gate-based (`structure_ok`, `location_ok`, `entry_ok`, `room_ok`, `rr_ok`, optional `macro_ok`). Profile points affect **`score` / `pct`**, not `passed`.

**Evidence:** `market_structure.py` ~3077–3091.

- **`engine_b_confidence_passes`** can still **fail** when `calculate_confidence` returned `passed=True` but `score < min_score_scaled`.

**Evidence:** `market_structure.py` ~425–441.

### Empirical pass-rate by tier

**NOT VERIFIED** — requires logged funnel stats or distribution sampling.

---

## Section 2 — Structure detection mathematics

### BOS

- Close-based breach of **prior** structural swing (second-to-last of last three peaks/troughs).
- Scans last `ENGINE_B_BOS_LOOKBACK_BARS` bars; invalidation if subsequent closes cross back.
- Optional volume confirmation when volumes are “eligible” (crypto contract volume by default; tick volume gated by `ENGINE_B_BOS_VOLUME_FOR_TICKVOL`).

**Evidence:** `market_structure.py` ~1079–1176, ~2027–2047.

### CHoCH

- Fast path when `bos_data` present: compares `last_close` to `bos_reference_high/low`.
- Fallback: swing trend-count path (`ENGINE_B_CHOCH_STRICT` toggles transition count).

**Evidence:** `market_structure.py` ~1187–1283.

### Swing highs/lows

- `_swing_cache`: `prominence = atr * 0.8`, `distance=3`.
- Zone pivots in `_find_zones`: `prominence = atr * 1.5`, `distance=5`.

**Evidence:** `market_structure.py` ~896–903, ~953–966.

### FVG

- Three-bar pattern on candle indices `i-1`, `i`, `i+1`.
- Bearish: `prev_low > next_high`; bullish: `prev_high < next_low`.
- Merge consecutive same-type FVGs when `|bar_index difference| ≤ 2`.
- Mitigation when price reaches **50% midpoint** of gap.
- **No minimum gap size** filter in code.

**Evidence:** `market_structure.py` ~1500–1610.

**Note:** Docstring at ~1591–1597 contradicts implementation (bullish/bearish lines swapped vs `_detect_fvg_legacy` / `_detect_fvg_fast`). See BUG-B-2.

### Liquidity sweep

- Requires `len(closes) ≥ 8`.
- Uses structural swing high/low when available; else local max/min of window ~bars 6–15 back from the end.
- Scans **last 5** bars:
  - Bull sweep: `low < ref_low - 0.3 * atr` and `close > ref_low`.
  - Bear sweep: symmetric with `high > ref_high + 0.3 * atr` and `close < ref_high`.
- **No** explicit wick-to-body ratio in this function.

**Evidence:** `market_structure.py` ~1404–1478.

### Zone construction / “quality”

- Zones from peaks/troughs with regime-dependent ATR buffers (`NAKED_ENGINE.zone_multipliers`).
- Each zone carries `volume_strength = min(1.0, zone_vol / avg_volume_20)` — informational, not a separate additive checklist score like Engine B gate points.

**Evidence:** `market_structure.py` ~953–1024.

### `ENGINE_B_USE_FORMING_FOR_STRUCTURE` vs `ENGINE_B_USE_FORMING_FOR_TRIGGER`

- **Independently enforced at candle fetch:** `_compute_naked_analysis` uses `_use_forming_structure` vs `_use_forming_trigger` when building series (`athena.py` ~5745–5771, ~5792–5795).
- **Additional layer:** `analyze_structure` applies `_engine_b_confirmed_only_struct_candles` when `ENGINE_B_STRIP_FORMING_STRUCT` is true — strips forming bars on structural TF regardless of the forming-fetch flags.

**Evidence:** `market_structure.py` ~56–94, ~1982–1987; `athena.py` ~5745–5795.

Trigger paths can still include the forming bar when trigger TF uses `h4_trigger` / `h1_trigger` with `_use_forming_trigger=True`.

---

## Section 3 — Regime multipliers — calibration assessment

- Effective scaled threshold: `round(min_score × multiplier)` using `ENGINE_B_REGIME_MULTIPLIERS` / defaults.

**Evidence:** `market_structure.py` ~14–21, ~235–268; `config.yaml` ~1826–1849, ~2040–2048.

| Regime | Effect on score gate |
|--------|----------------------|
| HIGH_VOL (0.85) | **Lowers** `min_score_scaled` → **easier** |
| LOW_VOL (1.15) | **Raises** `min_score_scaled` → **harder** |

### Scan vs backtest consistency

- Naked backtest applies **`engine_b_confidence_passes`** after `calculate_confidence` (`backtest_runner.py` ~3874–3878).

**Caveat:** Engine C **consensus** backtest path does **not** apply `engine_b_confidence_passes` before `compute_consensus` — see BUG-B-5.

---

## Section 4 — AI review layer

### `engine_b_ai.py`

- Prompt/logging frames output as **advisory**; execution gates are not flipped inside this module.
- Hard failures return `{"error": ...}`.
- Parseable JSON **missing required keys** gets **soft defaults** (e.g. grade **C**, edge 50%) — see BUG-B-8.
- **`ENGINE_B_SCAN_CONFIRMATION_GATE_ENABLED`** is **not** implemented here.

**Evidence:** `engine_b_ai.py` ~526–542, ~568–576, ~642–647.

### `signal_debate.py`

- Builds LLM debate; outputs `grade`, `allowed`, `score_adjustment` (≤ 0), etc.
- Does **not** read Engine B structure gates; cannot by itself restore a candidate that failed upstream structure logic.
- **`execution.py`** does not reference `signal_debate`; primary consumer is **`auto_trader`** (per codebase grep / prior trace).

### `ENGINE_B_SCAN_CONFIRMATION_GATE_ENABLED: false`

- Implemented in **`scanner.py`**: when true, Engine A **trade** tier demotes to watchlist without `enginesAligned`; autopilot explicitly **does not** follow this flag.

**Evidence:** `scanner.py` ~41–116.

Not dead code while `_apply_engine_b_scan_gate` exists.

---

## Section 5 — Execution handoff

### `ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS: true`

- Implemented in **`scanner.py`**: copies resolved Engine B execution SL/TP onto signal top-level `sl` / `tp1` / `tp2` when flag true.

**Evidence:** `scanner.py` ~88–102.

- **`execution.py`** does not read this flag; structural paths use `_extract_engine_b_execution_levels` / `_apply_engine_b_execution_levels`.

**Evidence:** `execution.py` ~406–439, ~775–776, ~1635–1646.

### `ENGINE_B_BT_STRUCTURE_GATE_ENABLED` vs `ENGINE_B_STRUCTURE_GATE_ENABLED`

- Same mechanism: both set `style_profile["disable_structure_gate"]` when disabled — BT only inside `backtest_pair_naked`; live via `naked_scan_style_profile`.

**Evidence:** `backtest_runner.py` ~3634–3637; `athena.py` ~5600–5603; `config.yaml` ~2124–2132.

They **can diverge** when the two config keys differ.

### `min_rr` vs `risk_engine`

- `calculate_confidence` enforces `min_rr` via resolved execution RR.
- **`risk_check`** applies **`ENGINE_C_EXEC_MIN_RR`** only for **consensus-shaped** signals (`verdict` set **or** `engine` / `components` shape).

**Evidence:** `risk_engine.py` ~835–867.

Structural naked payloads **without** that shape may bypass geometric RR check — see BUG-B-4.

### `structure_context.py`

- Applies a bounded multiplier to **Engine A / forex** scoring from `analyze_structure` output — separate from Engine B’s internal checklist.

**Evidence:** `athena_app/services/structure_context.py` ~6–88.

---

## Section 6 — Threshold calibration assessment

- **`NAKED_ENGINE.style_profiles.min_score`:** Reachable in principle when all mandatory gates pass and bonuses/profile add points; **empirical calibration NOT VERIFIED**.
- **`ENGINE_B_FOREX_ADX_MIN`:** Documented in YAML but **not read** by production Python paths — misleading unless wired or commented as intentional non-use. See BUG-B-1.
- **`ENGINE_B_ROOM_GATE_REQUIRE_DISTANCE`:** When `false`, `room_ok` allows `room_dist is None`; contextual **`_get_min_room_atr`** uses **hardcoded** ladders by asset class/style.

**Evidence:** `market_structure.py` ~2945–2950, ~2901–2916.

---

## Section 7 — Dead code check

| Item | Status |
|------|--------|
| `ENGINE_B_ZONE_PERSISTENCE: false` | Code in `zone_registry.py` runs when **true** — not dead, just off by default. |
| `ENGINE_B_NEWS_CONTEXT_ENABLED` | Consumed in `athena.py` naked AI path — **advisory** context only. |
| `ENGINE_B_FOLLOW_THROUGH` | Consumed in `calculate_confidence`; `ENABLED: false` skips bonus but diagnostics may still run — **not dead**. |
| `ENGINE_B_RESEARCH_LAB_FACTORS` | Implemented; with `ALLOW_GATE_UPGRADE: false`, gate upgrades never apply — mostly diagnostic CPU (see BUG-B-6). Also interacts with Engine A via `factor_scoring.py` cross-cap when both labs enabled. |
| `ENGINE_B_REASON_FOREX_ADX_LOW` | Constant unused in diagnostics — see BUG-B-7. |

---

## BUG-B-[N] — Ranked findings

### BUG-B-1 — `ENGINE_B_FOREX_ADX_MIN` unused in production code

- **Severity:** MEDIUM
- **File:** `config.yaml`
- **Line:** ~2086–2088
- **What:** Key is documented as meaningful for Engine B forex while **no runtime `.py` reads it** (grep: `config.py` registry + tests only).
- **Should:** Wire into diagnostics/gating **or** remove/neutralize ops implications.
- **Impact:** Operators may believe ADX floor gates Engine B when **`structure_ok` explicitly does not use `ENGINE_B_FOREX_ADX_MIN`** (`market_structure.py` ~2840–2842).
- **Fix:** Minimal: read key where ADX is surfaced and append diagnostic code **or** amend YAML to state “not enforced.”
- **Test:** Assert either no gate reads the key **or** diagnostics emit expected code when ADX &lt; configured floor.

### BUG-B-2 — FVG docstring contradicts implementation

- **Severity:** LOW
- **File:** `market_structure.py`
- **Line:** ~1591–1597 vs ~1508–1538 / ~1559–1587
- **What:** Docstring swaps bullish/bearish inequalities vs `_detect_fvg_legacy` / `_detect_fvg_fast`.
- **Should:** Doc matches code (`bearish`: `prev_low > next_high`; `bullish`: `prev_high < next_low`).
- **Impact:** Wrong mental model / audit confusion.
- **Fix:** Align docstring.
- **Test:** Synthetic three-bar cases per type.

### BUG-B-3 — `space_ok` vs `passed` semantics diverge

- **Severity:** MEDIUM
- **File:** `market_structure.py`
- **Line:** ~2990 vs ~3084–3089
- **What:** `space_ok = room_ok or rr_ok` exported in payload; **`passed` requires both `room_ok` and `rr_ok`**.
- **Should:** Single coherent contract for consumers.
- **Impact:** Dashboards/tools may treat `space_ok` as sufficient when checklist still fails room.
- **Fix:** Remove/rename `space_ok` **or** implement intentional OR-gate behind config + tests.
- **Test:** `room_ok=False`, `rr_ok=True` → document `passed` vs `space_ok`.

### BUG-B-4 — `risk_check` checklist + geometric RR can skip structural payloads

- **Severity:** HIGH
- **File:** `risk_engine.py`
- **Line:** ~701–723, ~835–867
- **What:** Engine B checklist proof and **`ENGINE_C_EXEC_MIN_RR`** apply only when signal matches consensus-shaped predicates (`verdict` / `engine` / `components`).
- **Should:** Defense-in-depth for all structural Engine B executions **or** invariant that API always attaches `verdict` + checklist fields.
- **Impact:** Thin/hand-built naked payloads might bypass **`ENGINE_B_CHECKLIST_MISSING`** / geometric RR while passing `_engine_b_context_confirmed` (`execution.py` ~387–401).
- **Fix:** Extend `_is_consensus_execution_signal` for `is_naked` / structural markers **or** reject incomplete payloads before `risk_check`.
- **Test:** `risk_check` with `is_naked=True`, minimal dict, no `verdict` → expect reject.

### BUG-B-5 — Engine C backtest omits `engine_b_confidence_passes`

- **Severity:** HIGH
- **File:** `backtest_runner.py`
- **Line:** ~4725–4765 (consensus ~4760–4765)
- **What:** Live Engine C scan uses **`_engine_c_accepts_engine_b`** → **`engine_b_confidence_passes`** (`execution.py` ~118–131). Consensus **backtest** feeds `compute_consensus` without that wrapper.
- **Should:** Same score-floor semantics as live scan.
- **Impact:** BT/live mismatch when `calculate_confidence["passed"]` is true but `score < min_score_scaled`.
- **Fix:** Call `engine_b_confidence_passes` before consensus **or** fold score floor into `passed` inside `calculate_confidence`.
- **Test:** Fixture: gates pass, score below scaled floor → BT must reject like scan.

### BUG-B-6 — Research lab ENABLED but upgrades hard-disabled by default

- **Severity:** MEDIUM
- **File:** `market_structure.py` / `config.yaml`
- **Line:** ~645–652 / ~1771–1774
- **What:** Factors run when `ENGINE_B_RESEARCH_LAB_FACTORS.ENABLED` is true, but `entry_ok`/`location_ok` outputs are forced false unless **`ALLOW_GATE_UPGRADE`** is true (default **false**). Gate merge at ~2973–2977 never fires.
- **Should:** Config semantics match behavior (short-circuit or rename ENABLED).
- **Impact:** Misleading ops + wasted computation.
- **Fix:** Skip factor evaluation when upgrades disabled **or** document “diagnostics only.”
- **Test:** ENABLED true, ALLOW false → assert no gate flips vs baseline without heavy calls (if optimized).

### BUG-B-7 — `ENGINE_B_REASON_FOREX_ADX_LOW` never emitted

- **Severity:** LOW
- **File:** `market_structure.py`
- **Line:** ~174
- **What:** Constant defined; never appended to `engine_b_diagnostics.reason_codes` (~3119–3135).
- **Should:** Emit **or** delete.
- **Impact:** Dead observability hook.
- **Fix:** Wire when forex ADX below configured floor **or** remove symbol.

### BUG-B-8 — `engine_b_ai` soft-defaults on partial parsed JSON

- **Severity:** MEDIUM (advisory integrity)
- **File:** `engine_b_ai.py`
- **Line:** ~568–576
- **What:** Missing keys filled with middling defaults instead of hard error.
- **Should:** Fail closed into `error` **or** explicit invalid flag for UI.
- **Impact:** Advisory output may look authoritative when model omitted fields.
- **Fix:** Strict validation post-parse.
- **Test:** Partial JSON → `error` or explicit policy marker.

---

## THRESHOLD ASSESSMENT — ENGINE B

| Key | Verdict | Justification |
|-----|---------|----------------|
| `NAKED_ENGINE.style_profiles.*.min_score` | CALIBRATED (partial) | Reachable when gates pass + bonuses; empirical pass-rate **NOT VERIFIED**. |
| `NAKED_ENGINE.score_group_overrides.*` | NOT VERIFIED | Per-group overrides; no empirical pass in this audit. |
| `ENGINE_B_REGIME_MULTIPLIERS` HIGH_VOL | TOO_LOOSE (score gate) | Lowers scaled threshold vs base. |
| `ENGINE_B_REGIME_MULTIPLIERS` LOW_VOL | STRICTER | Raises scaled threshold (matches code comments). |
| `ENGINE_B_FOREX_ADX_MIN` | OVERKILL / misleading | Unused in production `.py`; ops may assume enforcement. |
| `ENGINE_B_ROOM_GATE_REQUIRE_DISTANCE` | Mixed | Toggle changes None-distance handling; `_get_min_room_atr` hardcoded. |
| `ENGINE_C_EXEC_MIN_RR` | Context-dependent | Only consensus-shaped executions in `risk_check`. |

---

## DEAD CODE — ENGINE B

| Item | Location | Notes |
|------|-----------|------|
| `ENGINE_B_REASON_FOREX_ADX_LOW` | `market_structure.py` ~174 | Never referenced in diagnostics assembly. |
| `ENGINE_B_FOREX_ADX_MIN` value | `config.yaml` | Unused by runtime Python beyond registry/tests. |
| Possibly unreachable `RuntimeError` | `engine_b_ai.py` ~94–96 | Defensive branch after retry loop — likely unreachable in normal flow. |

---

## NOT VERIFIED

- Empirical score distributions / pass rates per style tier.
- Every production HTTP/UI payload for naked execution (whether `verdict` / `components` always present).
- Entire `PAIR_PROFILES` YAML for rare nested Engine B keys (sampled region showed Engine A-style keys only).
- `timed_exit_monitor.py` / broker adapters for Engine B lifecycle (out of scope).
- Full statistical calibration of `score_group_overrides`.

---

## Files / functions inspected (audit trail)

| File | Focus |
|------|--------|
| `market_structure.py` | Scoring, gates, BOS/CHoCH/sweep/FVG/zones, regime threshold, execution level resolution, research lab gates |
| `zone_registry.py` | Persistence (`ENGINE_B_ZONE_PERSISTENCE`), merge/strength |
| `engine_b_ai.py` | Advisory merge, failures, defaults |
| `signal_debate.py` | Output contract, no structure override |
| `config.yaml` | `NAKED_ENGINE`, `ENGINE_B_*`, `ENGINE_B_REGIME_MULTIPLIERS`, `PAIR_PROFILES` sample |
| `backtest_runner.py` | `backtest_pair_naked`, Engine C B branch |
| `execution.py` | `_engine_c_accepts_engine_b`, structural B levels, confirmation |
| `scanner.py` | Scan confirmation gate, scan-level SL/TP |
| `athena.py` | `naked_scan_style_profile`, forming flags, naked analysis |
| `risk_engine.py` | `risk_check` Engine B checklist + RR |
| `factor_scoring.py` | Cross-engine research cap |
| `engine_c.py` | Consensus SL/TP branch using `confidence_b["passed"]` |
| `structure_context.py` | Engine A structural adjustment |

---

*End of audit document.*
