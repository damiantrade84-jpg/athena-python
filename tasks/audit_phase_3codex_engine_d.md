# Audit Phase 3 - Codex Engine D Findings

Date: 2026-05-12
Mode: Audit only. No production code patched.
Scope: Engine D / Scalp Lab / Volume Profile in Sentinel Pro v4.

## Files Inspected

- `scalp_engine.py`
- `volume_profile.py`
- `config.yaml` (`SCALP_ENGINE` block and `BT_*` keys)
- `timed_exit_monitor.py` (Engine D bypass path)
- `execution.py` (Engine D signal consumption path)
- `eodhd_volume_batch.py`
- `eodhd_volume_overlay.py`
- `backtest_runner.py` (Engine D path)
- `athena.py` (active monolith Engine D execute and EODHD provider paths, because the modular `execution.py` route is not the only scalp execution path)
- `mt5_executor.py` / `bybit_executor.py` (Engine D executor classification only)
- `candle_feeds.py` (stock cumulative volume injection effect only)

## Commands Run

- `rg -n` searches over Engine D functions, config keys, execution handoff, EODHD, and timed-exit labels.
- `pytest tests/test_eodhd_volume_overlay.py -q` -> 7 passed.
- `pytest tests/test_eodhd_volume_batch.py -q` -> 2 failed, 4 passed. The failures are timestamp-gate test-data failures from stale 2024 quotes against the current max-lag check, not proof that the batcher is correct.

## Verified Mechanics

### Volume Profile Mathematics

`volume_profile.py:19` `compute_bucketed_volume_profile()`:

- POC: bucket with maximum `volume`.
- VAH/VAL: start at POC bucket index, repeatedly add the higher-volume adjacent bucket, left or right, until captured volume reaches `total_volume * value_area_pct`.
- Threshold: `value_area_pct` argument default `0.70`; live path supplies `SCALP_ENGINE.VP_VALUE_AREA_PCT` from `config.yaml:2294`.
- LVN: buckets inside the final value area where `volume < poc_volume * lvn_threshold`; default threshold `0.15`.
- Bucket size: inherited from incoming trade buckets; this function does not create price bins.

`volume_profile.py:275` `compute_fixed_range_volume_profile()`:

- POC: price bin with maximum allocated candle volume.
- Bin size: `(high_max - low_min) / bins`, where `bins` defaults to 64 and is supplied by Engine D from `SCALP_ENGINE.VP_BINS`.
- Candle volume allocation: each candle volume is distributed across overlapped bins by price-range overlap.
- VAH/VAL: same POC-outward higher-neighbor expansion until `total_volume * value_area_pct`.
- This function does not return LVNs; `scalp_engine.py` supplements LVNs with its internal builder.

`scalp_engine.py:1135` `_build_volume_profile()`:

- POC: price bin with maximum allocated volume, returned as the bin center.
- Bin size: `(price_max - price_min) / num_bins`; `num_bins` from `VP_BINS` / `VP_BINS_CLASS`.
- VAH/VAL: POC-outward expansion until `total_vol * va_pct`; `va_pct` from `VP_VALUE_AREA_PCT` / class override.
- LVN: only bins inside `[VAL bin, VAH bin]` where `bin_volume < poc_bin_volume * VP_LVN_THRESHOLD`.
- Minimum profile bars: hard minimum 20 candles in code; configured profile lookback default `VP_LOOKBACK_BARS: 50` at `config.yaml:2284`.

### Three-Pillar Gate

`scalp_engine.py:1359` `_classify_market_state()`:

- Balance condition: `balance_ratio >= BALANCE_THRESHOLD` where default is `0.40`.
- If balance ratio is missing, the function returns `"balance"`.

`scalp_engine.py:1375` `_locate_price_vs_vp()`:

- Price is considered at VAH/VAL/POC/LVN when absolute distance is strictly less than tolerance.
- Tolerance defaults to `price * VP_PROXIMITY_PCT / 100`, but ATR mode uses `atr_m15 * VP_PROXIMITY_ATR_K` when ATR is available.
- Config defaults: `VP_PROXIMITY_PCT: 0.30` at `config.yaml:2306`, `VP_PROXIMITY_ATR_K: 0.50` at `config.yaml:2307`.

`scalp_engine.py:1627` `_engine_d_aggression_fidelity()`:

- Aggression is true when any of absorption, CVD alignment, or AAA alignment is true.
- Strict Fabio pass only counts live Binance aggregate-trade source for strict fidelity; proxy sources are labelled but not equivalent.

`scalp_engine.py:1682` `_engine_d_strict_fabio_shadow()`:

- Mean reversion strict gate: market state balance, location at VAH/VAL/outside VA, and aggression confirmed.
- Trend continuation strict gate: market state imbalance, location at LVN, and aggression confirmed.
- Trend extension strict gate: market state imbalance, outside VA, and aggression confirmed.
- A single pillar failure makes `strict_pass` false; there is no verified 2-of-3 pass path in this strict shadow gate.

### Grading System

`scalp_engine.py:3140` `ai_quality_grade()`:

- Grade thresholds are read from `GRADE_THRESHOLDS`, falling back to hardcoded `{A: 80, B: 60, C: 40}` at `scalp_engine.py:3327`.
- `GRADE_THRESHOLDS` is not present in the checked `SCALP_ENGINE` config block.
- Size mapping falls back to configured `SIZE_MULTIPLIER_A/B/C`, with Grade D fallback to 0.
- Live scan blocks Grade D at `scalp_engine.py:4299` by setting `gate_result = "BLOCKED"` and `executable = False`, but all signals are still appended to the result list later.

### Setup Type Detection

`scalp_engine.py:2507` `_classify_setup()`:

- Mean reversion requires balance plus VA extreme/outside-VA location; direction is short above VAH or long below VAL; target is later represented by POC in level calculation.
- Trend continuation requires imbalance plus acceptable pullback location. With strict LVN-only config, non-LVN continuation is rejected.
- Trend direction can come from AAA, HTF bias, or VWAP context, depending on available signals.
- No explicit minimum room-to-POC gate is present in setup detection.
- No explicit false-setup guard was verified for "price already through POC" or "current price and POC in same bin".

### Session Filter

`scalp_engine.py:962` `scalp_session_window()`:

- Live session filter uses `SESSION_FILTER` / `SESSION_MODE`; backtest uses `BT_SESSION_FILTER` / `BT_SESSION_MODE`.
- `BT_SESSION_MODE: "all"` at `config.yaml:2675` means Engine D backtests currently pass all sessions.
- London and New York windows are computed from timezone-aware London/New York local times. London open cooldown and NY open cooldown are applied when the selected mode includes those sessions.
- Crypto can use `asia_london_ny` through `SESSION_MODE_BY_ASSET.crypto`; this intentionally applies session filtering to crypto unless mode is all/disabled.

### Execution Handoff

`timed_exit_monitor.py:1145` and `timed_exit_monitor.py:1428` bypass Engine D only when `engine.lower()` is one of `("scalp", "engine d", "scalp_vp")`.

`execution.py:2003` `api_scalp_execute()`:

- Modular route accepts posted signal payload and does not rerun the scanner.
- It does not re-check `gate_result`, `executable`, Grade D, RR status, or fail reasons before `risk_check()` and `run_managed_execution()`.

`athena.py:9007` active monolith `api_scalp_execute()`:

- Reruns `run_scalp_scan([symbol])`.
- Rejects if `signal.get("gate_result", "PASS") != "PASS"` or `signal.get("executable") is False` at `athena.py:9084`.
- This path is safer than the modular route for Grade D and blocked Engine D signals.

## BUG-D-1 - Crypto Engine D Backtest Uses a Tuple Instead of Candle List

Severity: CRITICAL
File: `backtest_runner.py`
Line: `5131`
What: The crypto Engine D backtest path assigns `m15_raw = _scalp_fetch_candles(pair_dict, "M15", 2000)`. `_scalp_fetch_candles()` returns a tuple, but downstream code expects a list of candle dicts.
Should: Unpack the tuple and use the M15 candle list consistently.
Impact: Crypto Engine D backtests can be empty, malformed, or crash before producing valid parity evidence.
Fix: Minimal concrete fix:

```python
_m15_tuple = _scalp_fetch_candles(pair_dict, "M15", 2000)
m15_raw = _m15_tuple[0] if isinstance(_m15_tuple, tuple) else _m15_tuple
```

Test: Add a crypto Engine D backtest regression using a mocked `_scalp_fetch_candles()` tuple and assert the path normalizes to candle dicts before profile/setup calculation.

## BUG-D-2 - Crypto Backtest Can Use Forming-Bar Data While Non-Crypto Backtest Uses Closed Bars

Severity: HIGH
File: `backtest_runner.py`
Line: `5131`
What: The crypto path calls `_scalp_fetch_candles()` without the non-crypto `include_forming=False` guard used by `mt5_fetch_scalp_candles()` at `backtest_runner.py:5138`.
Should: Crypto backtest signal generation should use closed bars only, matching the stated Engine D backtest contract.
Impact: Crypto Engine D backtests can contain forming-bar lookahead and overstate signal quality.
Fix: Minimal concrete fix:

```python
m15_raw = _closed_candles_only(m15_raw)
```

where `_closed_candles_only()` removes any candle whose close time is after the simulated signal timestamp.
Test: Seed a last incomplete crypto M15 candle with a decisive VP/aggression change and assert the backtest ignores it.

## BUG-D-3 - Engine D TP1 Is Not the Configured 1R Self-Pay Target

Severity: HIGH
File: `scalp_engine.py`
Lines: `3070-3071`
What: `tp1_r_mult = max(float(cfg.get("TP1_R_MULT", 1.0)), min_rr_cfg)` forces TP1 to at least the configured minimum RR. With config `TP1_R_MULT: 1.0` at `config.yaml:2602` and many `MIN_RR` values above 1.0, TP1 becomes 1.2R, 1.3R, 1.5R, or higher.
Should: TP1 should remain the self-pay target from `TP1_R_MULT`, while RR validation should separately enforce that the full structural or runner target satisfies `MIN_RR`.
Impact: Live Engine D trades may not take partial profit at 1R, contradicting the Engine D monitor/audit expectation that TP1 is the self-pay level.
Fix: Minimal concrete fix:

```python
tp1_r_mult = float(cfg.get("TP1_R_MULT", 1.0))
min_rr_required = min_rr_cfg
```

Then validate `min_rr_required` against the intended full target, not by stretching TP1.
Test: Configure `TP1_R_MULT=1.0`, `MIN_RR=1.5`, entry 100, SL 99, and assert TP1 is 101 while signal RR validation still requires 1.5R elsewhere.

## BUG-D-4 - Modular Engine D Execution Route Can Execute Blocked or Grade D Payloads

Severity: CRITICAL
File: `execution.py`
Lines: `2003`, `2122-2142`
What: The modular `api_scalp_execute()` consumes posted signal data, then calls `risk_check()` and `run_managed_execution()` without checking `gate_result`, `executable`, Grade D, RR failure, or Engine D fail reasons.
Should: Execution should fail closed unless scanner-produced `gate_result == "PASS"`, `executable is True`, grade is above the configured execution minimum, and RR/levels are valid.
Impact: If this route is registered or called directly, a client can hand in a Grade D or blocked signal that reaches broker placement.
Fix: Minimal concrete fix:

```python
if sig.get("gate_result") != "PASS" or sig.get("executable") is not True:
    return jsonify({"ok": False, "error": "Engine D signal not executable"}), 400
if str(sig.get("grade", "")).upper() == "D":
    return jsonify({"ok": False, "error": "Grade D skipped"}), 400
```

Test: Route test posts a Grade D Engine D signal with valid-looking SL/TP and asserts `run_managed_execution()` is not called.

## BUG-D-5 - Modular Engine D Rebase Drops Group-Specific Minimum RR

Severity: HIGH
File: `execution.py`
Lines: `2066-2091`
What: Modular `api_scalp_execute()` recalculates scalp levels with `calculate_scalp_levels()` but does not pass `min_rr_override` or `score_group`. The live scan path uses group-aware min RR.
Should: Rebased execution levels should use the same score group and min RR source as the scanner.
Impact: A pair requiring higher RR by group can be executed with lower generic constraints if the modular route is active.
Fix: Minimal concrete fix:

```python
levels = calculate_scalp_levels(
    sig,
    pair=pair,
    min_rr_override=sig.get("min_rr_required"),
    score_group=sig.get("score_group"),
)
```

Test: Configure a group override `MIN_RR=1.6`, post a signal through modular scalp execute, and assert rebased levels still enforce 1.6R.

## BUG-D-6 - Stock Backtest Volume Acceptance Does Not Match Live EODHD Safety Gates

Severity: HIGH
File: `backtest_runner.py`
Lines: `5348-5352`
What: Backtest accepts stock volume sources including `eodhd_1h` and `ws_tick` as valid. Live scan has stricter stock EODHD safety controls, including `BLOCK_STOCK_VP_ON_EODHD_1H_VOLUME` observed in `scalp_engine.py:3980`.
Should: Backtest should mirror live stock VP blocking and range-proxy invalidation.
Impact: Engine D stock backtests can validate setups that live scan would block, creating live/backtest parity drift.
Fix: Minimal concrete fix:

```python
if asset_type == "stock" and cfg.get("BLOCK_STOCK_VP_ON_EODHD_1H_VOLUME") and _bt_volume_source == "eodhd_1h":
    skip_reason = "stock_eodhd_1h_blocked"
    continue
```

Test: Backtest fixture with stock `_bt_volume_source="eodhd_1h"` and config block enabled should skip the signal, matching live behavior.

## BUG-D-7 - LVN Detection Only Searches Inside the Value Area

Severity: MEDIUM
File: `scalp_engine.py`
Lines: `1225-1228`
What: `_build_volume_profile()` only appends LVNs when the bin index is inside the computed value area range. `volume_profile.py` bucketed path applies the same concept inside VA.
Should: If Engine D trend continuation expects pullbacks to LVNs as rejection/acceptance nodes, LVN detection should either search the full profile or the code/config should explicitly document that only in-value-area LVNs are valid.
Impact: Trend continuation can miss legitimate low-volume pullback nodes outside the value area, while still claiming generic LVN logic.
Fix: Minimal concrete fix:

```python
for i, vol in enumerate(bins):
    if vol < bins[poc_bin] * lvn_factor:
        lvn_levels.append(...)
```

or rename/document the behavior as `value_area_lvn_levels`.
Test: Build a profile with a low-volume node just outside VA and assert the expected trend-continuation location classification.

## BUG-D-8 - EODHD LiveV2 First Quote Injects Cumulative Session Volume as One Delta

Severity: HIGH
File: `eodhd_volume_batch.py`
Lines: `205-209`
What: When no prior cumulative volume is known, the batcher sets `delta_vol = cum_vol`. That delta is then injected into CandleBuilder, which accumulates it into all stock timeframes.
Should: First observation should establish a baseline, not inject the full cumulative session volume into the current bar.
Impact: The first warmed stock candle can receive a massive synthetic volume spike, distorting POC/VA/LVN and aggression grading.
Fix: Minimal concrete fix:

```python
if prev_vol is None:
    self._prev_cum_vol[symbol] = cum_vol
    continue
```

Test: Feed first quote cumulative volume 1,000,000 and assert no candle volume is incremented until the next quote delta arrives.

## BUG-D-9 - EODHD Low-Timeframe Cache TTL Can Serve Stale VP Volume for 15 Minutes

Severity: HIGH
File: `athena.py`
Lines: `137`, `1873`
What: `_EODHD_VOLUME_TTL` defines H1/H4/D1 only. Other timeframes fall back to `15 * 60` seconds at `athena.py:1873`.
Should: M1/M5/M15 Engine D volume cache TTL should match scalp structure sensitivity and provider freshness guarantees, or be explicitly blocked when stale.
Impact: Stale EODHD volume can produce a volume profile that does not reflect current market structure for up to 15 minutes.
Fix: Minimal concrete fix:

```python
_EODHD_VOLUME_TTL = {"M1": 60, "M5": 5 * 60, "M15": 5 * 60, "H1": 55 * 60, "H4": 235 * 60, "D1": 23 * 3600}
```

Test: Insert an M15 cached volume set older than configured max age and assert live Engine D marks volume stale and skips/fails closed.

## BUG-D-10 - Backtest EODHD Path Can Use Live CandleBuilder Stock Volume Despite Comment Saying It Skips WS

Severity: MEDIUM
File: `athena.py`
Lines: `1878-1886`, `1920-1979`
What: `_fetch_eodhd_volume_only()` comment says backtest callers pass `cache_only=False` and skip the WS path. The stock CandleBuilder branch is still executed before the `cache_only` return branch.
Should: Backtest volume should come from deterministic historical EODHD/cache data, not live CandleBuilder state.
Impact: Engine D stock backtests can be contaminated by current live session volume and lose reproducibility.
Fix: Minimal concrete fix:

```python
if cache_only and _is_stock_like(pair):
    live_rows = _candlebuilder_rows(...)
```

or add an explicit `live_mode` argument and require it for CandleBuilder volume.
Test: Seed CandleBuilder with live stock volume, run historical stock Engine D backtest, and assert the backtest does not consume live rows.

## BUG-D-11 - Grade Thresholds Are Hardcoded Fallbacks and Missing From Config

Severity: MEDIUM
File: `scalp_engine.py`
Line: `3327`
What: `ai_quality_grade()` uses `cfg.get("GRADE_THRESHOLDS", {"A": 80, "B": 60, "C": 40})`, but `GRADE_THRESHOLDS` is absent from `SCALP_ENGINE` in `config.yaml`.
Should: Grade boundaries should be config-driven because trading thresholds belong in `config.yaml`.
Impact: Operators cannot calibrate A/B/C/D distribution from config even though Engine D execution depends on grade.
Fix: Minimal concrete fix in config:

```yaml
SCALP_ENGINE:
  GRADE_THRESHOLDS:
    A: 80
    B: 60
    C: 40
```

Test: Override thresholds in a test config and assert `ai_quality_grade()` returns different grade cutoffs without code changes.

## BUG-D-12 - Active Monolith Scalp Execute Can Return Success After Audit Insert Failure

Severity: HIGH
File: `athena.py`
Lines: `9196`, `9226-9227`, `9229-9237`
What: Active monolith `api_scalp_execute()` catches audit insert exceptions, logs a warning, then still returns success after broker execution.
Should: High-risk execution should not report a clean success when audit persistence failed; it should either fail before broker placement or return an explicit degraded state requiring reconciliation.
Impact: A live/paper Engine D fill can exist without an audit row, breaking BE/TP1 tracking, timed-exit bypass reconciliation, dashboard truth, and PnL accounting.
Fix: Minimal concrete fix:

```python
except Exception as audit_exc:
    log.error("[SCALP EXEC] audit_log insert failed after broker execution: %s", audit_exc)
    return jsonify({"ok": False, "error": "order_placed_audit_failed", "result": result}), 500
```

Test: Mock broker placement success and audit DB failure; assert response includes `order_placed_audit_failed` and reconciliation data.

## THRESHOLD ASSESSMENT - ENGINE D

- `VP_LOOKBACK_BARS` - CALIBRATED - Default 50 bars is a reasonable fixed-range M15 profile, but code only hard-fails below 20 bars; stability depends on data source quality.
- `VP_BINS` - CALIBRATED - 64 bins is standard enough for fixed-range VP, with class overrides available.
- `VP_VALUE_AREA_PCT` - CALIBRATED - 0.70 matches conventional value-area calculation and is config-driven.
- `VP_LVN_THRESHOLD` - TOO_STRICT - 15% of POC volume, restricted to inside-VA bins, can miss external LVNs used by trend-continuation pullbacks.
- `VP_PROXIMITY_PCT` - TOO_LOOSE WHEN ATR UNAVAILABLE - 0.30% of price can be wide on high-priced assets; ATR mode mitigates when ATR exists.
- `VP_PROXIMITY_ATR_K` - CALIBRATED - 0.50 ATR is plausible for VAH/VAL tolerance, but still needs distribution validation by asset class.
- `BALANCE_THRESHOLD` - NOT VERIFIED - The live default observed in code is 0.40, but exact config presence and distribution calibration were not fully proven.
- `ABSORPTION_VOL_MULT` - CALIBRATED - Default 2.0 with class overrides up to 2.5 is reasonable for spike detection, but can be sparse on low-volume forex proxies.
- `ABSORPTION_MAX_MOVE_ATR` - TOO_STRICT - 0.30 ATR max move can reject valid aggressive absorption in volatile symbols.
- `ABSORPTION_SMA` - CALIBRATED - 20-bar volume baseline is conventional enough for M15, but source quality is the main risk.
- `CVD_SMOOTH` - CALIBRATED - 5-bar smoothing is plausible for short-horizon order flow.
- `CVD_MIN_SLOPE` - TOO_LOOSE - Default 0.0 treats any positive or negative slope as aligned, increasing proxy-noise sensitivity.
- `AAA_LOOKBACK` - CALIBRATED - 10 bars is plausible for compression/breakout detection.
- `AAA_CONTRACTION_THRESHOLD_*` - CALIBRATED - Class-specific 0.55 to 0.65 ranges are plausible, though not distribution-proven.
- `AAA_BREAKOUT_VOL_MULT` - CALIBRATED - 1.5x volume breakout threshold is reasonable.
- `MIN_RR` and `MIN_RR_GROUP` - CALIBRATED - Group overrides are risk-aware, but modular execution can ignore them during rebasing.
- `ATR_SL_MULT` - CALIBRATED - 1.5 ATR is standard for structural/ATR hybrid stops.
- `BUFFER_ATR_K` - CALIBRATED - 0.25 ATR structural buffer is moderate.
- `TP1_R_MULT` - UNREACHABLE AS CONFIGURED - Config says 1.0R, but code raises it to at least `MIN_RR`.
- `TP1_MAX_RR` - DEAD / NOT EFFECTIVE - Config key exists, but no Engine D code read was found.
- `TREND_EXT_MAX_RR` - DEAD / NOT EFFECTIVE - Config key exists, but no Engine D code read was found.
- `EXECUTION_MIN_GRADE` - CALIBRATED - Current B minimum is sensible for live execution.
- `MIN_GRADE_AUTO_EXECUTE` - CALIBRATED AS FALLBACK - Current B matches `EXECUTION_MIN_GRADE`; divergence would create ambiguity.
- `SIZE_MULTIPLIER_A` - CALIBRATED - Full size for A aligns with grading model.
- `SIZE_MULTIPLIER_B` - CALIBRATED - Half size for B aligns with moderate confidence.
- `SIZE_MULTIPLIER_C` - TOO_LOOSE IF EXECUTABLE - Quarter size for C is okay for watchlist but should not auto-execute under current B minimum.
- `BT_SESSION_MODE` - TOO_LOOSE - Backtest default all sessions diverges from live session filtering.
- `BT_GRADE_SESSION_LIVE_PARITY` - TOO_LOOSE - False permits grade/session scoring drift between live and backtest.
- `BT_CRYPTO_SESSION_MODE` - NOT VERIFIED - Config inherits session handling, but practical crypto 24/7 calibration was not distribution-tested.
- `BT_NY_OPEN_SKIP_MINUTES` - CALIBRATED BUT CURRENTLY BYPASSED - It matches live 30 minutes but has no effect while `BT_SESSION_MODE` is all.
- `BT_VP_LOOKBACK_BARS` - NOT VERIFIED - Fallback path exists, but key was not confirmed in the inspected config block.

## DEAD CODE - ENGINE D

Confirmed or strongly indicated unused/non-operative items from inspected Engine D scope:

- `scalp_engine.py:930` `is_valid_session()` - no production Engine D call found in bounded search; `scalp_session_window()` is the active session helper.
- `config.yaml:2256` `WATCHLIST_GRADE_C_ENABLED` - no Engine D read found in inspected code; current executable/watchlist logic is controlled by grade minimum and gate result.
- `config.yaml` `WATCHLIST_REQUIRES_TREND` - no Engine D read found in inspected code.
- `config.yaml` `WATCHLIST_MIN_RR` - no Engine D read found in inspected code.
- `config.yaml` `WATCHLIST_LOCATION_REQUIRED` - no Engine D read found in inspected code.
- `config.yaml:2604` `TP1_MAX_RR` - no Engine D read found; TP1 is not capped by this key.
- `config.yaml:2606` `TREND_EXT_MAX_RR` - no Engine D read found; trend-extension TP1 is not capped by this key.
- `config.yaml` `ZONE_MIN_CONDITIONS` - no Engine D read found in inspected code.
- `config.yaml` `ZONE_CACHE_TTL_HOURS` - no Engine D read found in inspected code.
- `config.yaml` `BT_COMMISSION_PER_SIDE` - no Engine D backtest read found in inspected path.
- `config.yaml:2199` `SCALP_ENGINE.enabled` - observed as funnel metadata only, not as a hard Engine D scan/execution gate in inspected path.
- `volume_profile.py` `classify_profile_interaction()` - not dead globally because `market_structure.py` references it, but it was not verified as part of the Engine D production scan/execution path.
- `config.yaml:1330` `EODHD_COMMODITY_TICKERS` - consumed by EODHD volume overlay commodity mapping, including Engine D volume overlay for commodity symbols.

## NOT VERIFIED

- Auto-trader Engine D handoff was not fully traced in this audit artifact. The active manual/API execute paths were traced, but a complete `auto_trader.py` producer-to-broker Engine D path remains NOT VERIFIED.
- UI/operator surfaces for every Engine D fail reason were not fully traced. Scanner funnel payloads were inspected, but dashboard rendering parity remains NOT VERIFIED.
- Broker adapter SL/TP precision for every supported asset class was not fully revalidated. Only Engine D classification/bypass logic in `mt5_executor.py` and `bybit_executor.py` was checked.
- Statistical calibration of thresholds was not proven from a live distribution sample. Threshold judgments above are code/config assessments, not a market-data backtest distribution study.
- `BT_*` key inventory was based on the inspected config and bounded searches; any dynamically assembled config access outside the searched paths remains NOT VERIFIED.
