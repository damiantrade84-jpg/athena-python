# Engine D (Fabio Valentini Pro Scalper) — Nuclear Audit Report

**Audit Date:** 2026-05-12
**Scope:** scalp_engine.py, volume_profile.py, config.py / config.yaml, execution.py (Engine D path), timed_exit_monitor.py (Engine D bypass), eodhd_volume_overlay.py (via inline overlay calls)
**Mode:** Audit-only — no patches applied

---

## Files Inspected

| File | Lines | Role |
|------|-------|------|
| scalp_engine.py | 4668 | Core: VP building, 3-pillar gate, setup classification, grading, risk levels, scan loop |
| volume_profile.py | 595 | Fixed-range VP (candle), bucketed VP (trade buckets), session splitting |
| config.py | 1589 | CONFIG dict, SCALP_ENGINE YAML-only key, validation |
| execution.py L2003–2150 | ~150 | `api_scalp_execute`: level rebasing, risk_check, broker dispatch |
| timed_exit_monitor.py L1140–1160 | ~20 | Engine D bypass (`engine in ("scalp", "engine d", "scalp_vp")` → return) |

---

## Section 1 — Volume Profile Mathematics

### 1.1 POC Identification — ✅ CORRECT

**Internal histogram** (scalp_engine.py:1201):
```python
poc_bin = max(range(num_bins), key=lambda i: bins[i])
poc = price_min + (poc_bin + 0.5) * bin_size
```

**volume_profile.py** (L395): `poc_idx = int(np.argmax(volumes))` — same algorithm.

**compute_bucketed_volume_profile** (L67): `poc_idx = int(np.argmax(np.asarray(volumes)))` — same on trade data.

All three: POC = midpoint of maximum-volume bin. **Correct.**

### 1.2 Value Area (VAH/VAL) — ✅ CORRECT

All implementations use standard **outward-expansion from POC**:
1. Start with POC bin volume
2. Compare adjacent left vs right bin
3. Include the larger one
4. Repeat until cumulative ≥ `total_vol × va_pct`

`va_pct` is **configurable** via `SCALP_ENGINE.VP_VALUE_AREA_PCT` (YAML-only), defaulting to `0.70`. Clamped to `[0.1, 0.95]` by `_as_value_area_pct`. **Not hardcoded.**

### 1.3 LVN Detection — ✅ CORRECT

Internal histogram (L1224–1228):
```python
lvn_threshold = bins[poc_bin] * lvn_factor  # default 0.30
# bins inside VA with volume < threshold → LVN
```

`compute_bucketed_volume_profile` (L89–94): same algorithm. Configurable via `VP_LVN_THRESHOLD`.

**Note:** `compute_fixed_range_volume_profile` does NOT compute LVN levels. The caller supplements from the internal fallback (L1253–1260). **Defensive code — correct.**

### 1.4 Volume Allocation — ✅ CORRECT

All paths use proportional overlap allocation: candle volume distributed across bins proportional to price overlap. Zero-volume candles use `high - low` range as proxy. Standard VP methodology.

### 1.5 Balance Ratio

`_calc_balance_ratio` (L1327–1356): `(vah - val) / (session_high - session_low)`, clamped `[0.0, 1.0]`.

Fallback when session bounds unavailable:
- ≥2 LVNs → 0.30 (lean imbalance)
- 0–1 LVNs with valid vah/val → 0.55 (lean balance)
- No vah/val → `None` → defaults to "balance" (fail-safe)

---

## Section 2 — Three-Pillar Gate Logic

### 2.1 Pillar 1: Market State + Location — ✅ CORRECT

**Market State** = `_classify_market_state(vp)`:
- `balance_ratio >= 0.40` → "balance"
- `balance_ratio < 0.40` → "imbalance"
- `balance_ratio is None` → "balance" (fail-safe, logged)

**Location** = `_locate_price_vs_vp(price, vp, atr_m15)`:
- ATR-based proximity (default): `atr_m15 × VP_PROXIMITY_ATR_K` (default 0.20)
- Closest-wins tiebreaker eliminates check-order bias (L1425–1441)

### 2.2 Pillar 2: Aggression — ✅ CORRECT WITH OBSERVATIONS

**Absorption** (L1459–1522): High volume (≥ `ABSORPTION_VOL_MULT` × SMA) + small price move (≤ `ABSORPTION_MAX_MOVE_ATR` × ATR). Checked in recent `ABSORPTION_RECENT_BARS` (default 5).

**CVD** (L1525–1586): Crypto tries Binance aggTrade buckets first, falls back to candle approximation. Non-crypto: candle-based buy/sell approximation. Slope over last 6 values determines direction.

**AAA Sequence** (L2330): Absorption → Accumulation → Aggression completion.

**`_setup_aggression_confirmed`** (L2492–2504):
```python
return bool(meaningful_absorption or cvd_aligned or aaa_aligned)
```
**OR gate** — only ONE sub-pillar needs to pass. Intentional per Valentini methodology.

### 2.3 Pillar 3: VWAP Lean — ✅ CORRECT

Session VWAP from M15 candles; price above = LONG lean, below = SHORT lean.

### 2.4 Setup Classification — ✅ CORRECT

| Setup | Market State | Location | Required Confirmations |
|-------|-------------|----------|----------------------|
| Mean Reversion (extended) | balance | outside_va | Aggression confirmed + CVD not opposing |
| Mean Reversion (at VA) | balance | at_vah / at_val | Aggression confirmed + CVD not opposing |
| Trend Continuation | imbalance | at_lvn (strict) | AAA/HTF bias/VWAP + CVD not hard-veto |
| Trend Extension | imbalance | outside_va | Aggression confirmed + HTF bias aligned |

**Strict Fabio Gate** (`STRICT_FABIO_GATE_ENABLED`, default `True`):
- Trend continuation requires `at_lvn` location only
- All setups require `_setup_aggression_confirmed()` to pass
- Crypto strict mode requires real aggTrade buckets for VP and CVD

---

## Section 3 — Data Sources & Fidelity

### 3.1 Volume Source Hierarchy

| Asset Type | VP Source | CVD Source |
|-----------|----------|-----------|
| Crypto | Binance aggTrade buckets → candle fallback | Binance aggTrade buckets → candle fallback |
| Forex/Commodity/Index | MT5 tick-volume + EODHD overlay → range proxy | Candle approximation |
| Stock | MT5 tick-volume + EODHD overlay (required) | Candle approximation |

### 3.2 EODHD Volume Overlay — ✅ WELL-IMPLEMENTED

`_overlay_eodhd_volume_for_scalp` (L339–405):
- Config-gated for live/backtest separately
- Clear volume source tags (`ws_tick`, `eodhd_1m`, `eodhd_1h`, `mt5_tick`)
- `REQUIRE_REAL_VOLUME_FOR_STOCKS` / `REQUIRE_REAL_VOLUME_FOR_FOREX` / `REQUIRE_REAL_VOLUME_FOR_COMMODITY` gates

### 3.3 Data Fidelity Tagging — ✅ EXCELLENT

`_engine_d_data_fidelity()` produces VP/CVD/absorption fidelity reports with proxy detection flags. Visible in signal and funnel diagnostics.

### 3.4 Freshness Checks — ✅ COMPREHENSIVE

- `_scalp_candles_fresh()`: candle age vs `MARKET_CANDLE_MAX_AGE_SEC` (default 900s) + one bar-length buffer
- Session-boundary check: 12-hour gap detection for non-crypto
- MT5 tick freshness: `mt5_market_open_state()` checks tick age vs `MARKET_TICK_MAX_AGE_SEC` (default 900s)

---

## Section 4 — Execution Path

### 4.1 Risk Check Enforcement — ✅ CORRECT, NEVER BYPASSED

`api_scalp_execute` (execution.py:2003–2150):

1. **Level rebasing**: SL/TP recalculated at current broker mid-price
2. **RR validation**: if rebased RR < min → execution blocked
3. **`risk_check()`**: called at L2122 with full context — **never bypassed**
4. **`_guardian_pre_trade()`**: additional pre-trade checks
5. **`run_managed_execution()`**: dispatches to MT5 or Bybit

Kill switch passed via `_r.kill_switch()`. Any approval failure → HTTP 400. **Fail-closed.**

### 4.2 Timed Exit Monitor Bypass — ✅ CORRECT

`timed_exit_monitor.py:1145`:
```python
if engine in ("scalp", "engine d", "scalp_vp"):
    return
```
Engine D trades bypass timed exit monitor (TP/SL managed by broker). Does not affect Engine A/B `style=scalp` trades.

---

## Section 5 — Configuration Architecture

### 5.1 Config Loading

`SCALP_ENGINE` is in `_KNOWN_YAML_ONLY_KEYS` (config.py:1066) with **no hardcoded default**. Every `CONFIG.get("SCALP_ENGINE", {})` falls back to `{}`. All inline defaults are explicitly provided via `.get(key, default)` calls.

### 5.2 `_scalp_cfg_lookup` — ✅ WELL-DESIGNED

(L699–717): 3-tier resolution: score-group override → asset-class override → base key with default. Enables per-group and per-asset tuning without code changes.

### 5.3 Session Risk State — ✅ THREAD-SAFE

(L62–116): Protected by `_session_state_lock`. Resets at UTC midnight. Consecutive-loss halving (≥2 losses → `×0.5`). +2R daily cap (`min(current, 0.5)` — caps, does not increase).

---

## Ranked Bug List

| # | Severity | File | Finding | Description |
|---|----------|------|---------|-------------|
| 1 | **MEDIUM** | scalp_engine.py L2560–2567 vs L2710–2714 | BUG-010 | CVD proxy veto asymmetry: mean reversion hard-vetoes CVD proxy conflict even when absorption confirms, while trend continuation allows absorption/VWAP override. Inconsistent treatment of same data quality issue. |
| 2 | **MEDIUM** | scalp_engine.py L720–734 | BUG-011 | `_as_fraction` silently converts values >1.0 to `v/100`. Values like `1.5` become `0.015` (almost certainly unintended). Values between 1.0–2.0 exclusive produce sub-2% fractions with no warning. |
| 3 | **LOW** | volume_profile.py L275–428 | BUG-012 | `compute_fixed_range_volume_profile` does not emit `lvn_levels` or `distribution`. Caller supplements from internal fallback, causing double VP computation. Performance cost, not correctness. |
| 4 | **LOW** | scalp_engine.py L1369–1371 | BUG-013 | Balance ratio `None` defaults to "balance". Potentially blocks valid trend continuation setups when session bounds unavailable (session transitions). Fail-safe by design, documented. |
| 5 | **LOW** | scalp_engine.py L4420 | BUG-014 | Signal emits `"engine": "SCALP"` (uppercase) but timed_exit bypass checks lowercase `"scalp"`. Engine field is lowered at consumption site but inconsistency is a maintenance hazard. |

---

## Recommended Fixes (Priority Order)

### BUG-010 — CVD Proxy Veto Asymmetry (MEDIUM)

In `_classify_setup`, align the CVD proxy override logic: if absorption confirms and CVD source is proxy, downgrade to advisory (grade reduction) consistently across mean reversion AND trend continuation. Currently mean reversion treats proxy conflict as hard veto unless `CVD_PROXY_HARD_VETO=False`, while trend continuation allows absorption/VWAP override.

### BUG-011 — `_as_fraction` Silent Conversion (MEDIUM)

Add a `log.warning` when auto-converting `v > 1.0` to `v / 100.0`, or restrict auto-conversion to `v >= 10.0` to avoid ambiguity with values like 1.5. Alternatively, document the expected input ranges in config comments.

### BUG-012 — Double VP Computation (LOW)

Add LVN detection to `compute_fixed_range_volume_profile` using the same `poc_vol * lvn_threshold` formula. Eliminate the supplemental internal profile call.

---

## Recommended Negative-Case Tests

| # | Test Name | Target | Purpose |
|---|-----------|--------|---------|
| 1 | `test_cvd_proxy_veto_mean_reversion_with_absorption` | scalp_engine.py | CVD proxy opposing + absorption confirms → verify behavior with `CVD_PROXY_HARD_VETO=True` vs `False` |
| 2 | `test_grade_d_blocks_execution` | scalp_engine.py | All pillars valid but quality score <30 → `gate_result=BLOCKED`, `executable=False` |
| 3 | `test_fee_guard_micro_stop` | scalp_engine.py | SL distance < `ENGINE_D_MIN_STOP_PCT` → `fee_guard_micro_stop` in fail_reasons |
| 4 | `test_stale_candles_rejected` | scalp_engine.py | M15 candles older than `MARKET_CANDLE_MAX_AGE_SEC` + bar length → skip |
| 5 | `test_crypto_strict_no_aggtrade_skips` | scalp_engine.py | Strict gate + no trade buckets → skip (not watchlist) |
| 6 | `test_balance_ratio_none_defaults_balance` | scalp_engine.py | VP without session bounds → "balance" market state |
| 7 | `test_risk_check_never_bypassed` | execution.py | Signal with missing `sl`/`price` → `risk_check()` rejects |
| 8 | `test_prior_session_freshness_reject` | scalp_engine.py | Candles age >12h for forex → `PRIOR_SESSION_DATA` rejection |
| 9 | `test_as_fraction_edge_cases` | scalp_engine.py | `VP_VALUE_AREA_PCT: 1.5` → 0.015; `VP_VALUE_AREA_PCT: 70` → 0.70 |

---

## Areas NOT Verified

| Area | Reason |
|------|--------|
| `backtest_runner.py` Engine D path | Not re-read in this session; prior session confirmed it exists |
| Engine D unit test coverage | Test directory not inspected |
| `eodhd_volume_batch.py` internals | Deferred; overlay consumption path verified via `_overlay_eodhd_volume_for_scalp` |
| `scalp_audit.py` funnel diagnostics | Import-guarded; report-only module not critical path |
| Engine D → Telegram notification path | Not in scope |
| Live auto-trader → Engine D execution end-to-end | Requires `auto_trader.py` inspection |

---

## Audit Completion Checklist

- [x] Files inspected: 5 (listed above)
- [x] Functions/classes inspected: 25+ (listed in Section 1 header)
- [x] Execution paths traced: scan → VP → 3-pillar gate → classify → grade → signal → risk_check → broker
- [x] Commands/tests run: None (audit-only mode)
- [x] Areas not verified: Listed above
- [x] Ranked bug list with evidence: 5 findings (2 medium, 3 low)
- [x] Recommended negative-case tests: 9 tests listed
