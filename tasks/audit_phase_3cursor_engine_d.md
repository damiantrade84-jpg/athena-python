# Engine D (Scalp Lab / Volume Profile) — Nuclear-Grade Audit

**Auditor:** Cursor agent
**Scope:** Audit-only (no patches applied during audit).
**Files inspected:** `scalp_engine.py`, `volume_profile.py`, `config.yaml` (SCALP subtree + root `BT_*` keys per scope), `timed_exit_monitor.py` (Engine D bypass), `execution.py` (Engine D path), `eodhd_volume_batch.py`, `eodhd_volume_overlay.py`, `backtest_runner.py` (`backtest_pair_scalp`).
**Evidence:** Parallel reads + repo-wide `grep` / targeted `read_file`; **pytest not run** for this audit deliverable.

---

## Section 1 — Volume Profile Mathematics

### VAH / VAL / POC — fixed-range path (`volume_profile.compute_fixed_range_volume_profile`)

- Total volume: `total_volume = sum(bins)`.
- **POC:** bin index `poc_idx = argmax(volumes)`; POC price = **midpoint** of that bin: `(edges[poc_idx] + edges[poc_idx+1]) / 2`.
- **Value area:** `target_volume = total_volume * clamp(value_area_pct, 0.1, 0.95)`. Expand from POC outward one bin at a time, always adding the side (left vs right) with **larger volume** (ties prefer left). Stop when cumulative ≥ target or no neighbors.
- **VAH / VAL:** `vah = edges[max(included)+1]`, `val = edges[min(included)]` (outer edges of included bin span).
- **70% threshold:** Default `value_area_pct=0.70`; clamped to **[0.1, 0.95]** inside `volume_profile.py`. **Configurable** via callers; `scalp_engine._build_volume_profile` passes `_scalp_cfg_lookup(..., "VP_VALUE_AREA_PCT", ...)` — **not hardcoded** in the production VP builder.

### Trade-bucket path (`compute_bucketed_volume_profile`)

- POC = **price level** (bucket price) with maximum volume.
- Same outward expansion on discrete buckets.
- **LVN:** buckets inside VA span with volume **< poc_vol × lvn_threshold** (default parameter **0.15**).

### Bin size

- Fixed-range: `edges = linspace(session_low, session_high, bins+1)`, default **`bins=64`**, forced **`max(8, bins)`**.
- Driven from **`VP_BINS`** / class maps in `scalp_engine._build_volume_profile`.

### LVN identification

- **Relative fraction of POC bin volume** (`lvn_threshold`), **not** mean bin volume.
- Internal fallback histogram in `_build_volume_profile` uses `lvn_threshold = bins[poc_bin] * lvn_factor` where `lvn_factor` comes from **`VP_LVN_THRESHOLD`** (via `_scalp_cfg_lookup`).

### Profile period / lookback

- Live scan: `vp_lookback = max(20, VP_LOOKBACK_BARS)` (default **50** bars from config).
- Optional **prior completed session** replaces the window when **`VP_SESSION_AWARE`** and `split_completed_sessions` yields **≥20** bars.
- **`_build_volume_profile`** requires **`len(candles) >= 20`** else invalid.
- **Not** parameterized separately per scalp/intraday/swing style keys — single `VP_LOOKBACK_BARS` (+ session-aware branch).

### EODHD volume consumption

- **`scalp_engine._overlay_eodhd_volume_for_scalp`** → runtime fetch → **`eodhd_volume_overlay.overlay_candle_volumes`**: **per-bar** replacement of `vol` on aligned timestamps; resample helpers aggregate volume by **sum** for TF upsampling.
- **`eodhd_volume_batch.py`:** cumulative delayed-quote deltas fed as synthetic ticks — **not** bar-static VP injection.
- Overlay module **does not** implement quote TTL; staleness/lag handled elsewhere (e.g. live V2 **`EODHD_LIVE_V2_MAX_QUOTE_LAG_SEC`**).
- **Stale EODHD volume can produce a VP misaligned with current structure** — operational risk, not automatically rejected at overlay layer alone.

### Tick volume vs real volume (forex / zero-volume candles)

- **`compute_fixed_range_volume_profile`:** session total volume ≤ 0 → **range proxy** path; per candle `vol ≤ 0` → **`high − low`** proxy.
- **`volume_source`** tags: `range_proxy` vs `candle_volume`.
- Scan path: stocks may **`REQUIRE_REAL_VOLUME_FOR_STOCKS`**, **`VP_INVALIDATE_RANGE_PROXY_FOR_STOCKS`**, **`BLOCK_STOCK_VP_ON_EODHD_1H_VOLUME`** — fail-closed variants for equities.

---

## Section 2 — Three-Pillar Gate

**Operational enforcement:** With **`STRICT_FABIO_GATE_ENABLED`** (default **true**), `run_scalp_scan` appends failures when **`_engine_d_strict_fabio_shadow`** reports **`strict_fabio_pass`** false.

Reference (`scalp_engine.py` ~1695–1716):

- **Mean reversion:** `market_ok = balance`; `location_ok ∈ {at_vah, at_val, outside_va}`.
- **Trend continuation:** `market_ok = imbalance`; `location_ok = at_lvn`.
- **Trend extension:** `market_ok = imbalance`; `location_ok = outside_va`.
- **`aggression_ok`** = **`aggression_confirmed`** from `_engine_d_aggression_fidelity` (absorption OR aligned CVD OR aligned AAA completion — see `_setup_aggression_confirmed`).
- **All three** must be true; `missing` pillars listed; **`strict_pass = not missing`**.

### Market State pillar

- **`_classify_market_state`:** compares **`balance_ratio`** to **`BALANCE_THRESHOLD`** (default **0.40**).
- **`balance_ratio is None`:** defaults to **`"balance"`** (debug log) — **fail-open toward MR**, not fail-closed.

### Location pillar

- **`_locate_price_vs_vp`:** if **`VP_PROXIMITY_USE_ATR`** true (code default **True**) and **`atr_m15 > 0`**: band = **`ATR_M15 * VP_PROXIMITY_ATR_K`** (class/asset maps supported).
- Else: **`abs(price − level) / level < VP_PROXIMITY_PCT / 100`** (e.g. YAML **0.30 → 0.3%**).
- Uses **`current_price`** from caller (typically **close** on structure bar), not an explicit wick-through test.

### Aggression pillar

- Proxy CVD (candle delta approximation), trade-bucket CVD (crypto), absorption, AAA — alignment rules in `_setup_aggression_confirmed` / `_check_cvd` / `_check_trade_bucket_cvd`.
- When flow data is missing, pillar typically **fails** unless absorption/AAA carries it (strict paths).

### ALL must align / 2-of-3

- Under **`STRICT_FABIO_GATE_ENABLED`**, **no** 2/3 path — single pillar failure yields **`strict_fabio:missing_*`** style failure.
- Without strict Fabio, legacy branches allow neutral CVD etc. at VA extremes — weaker gate.

---

## Section 3 — Grading System

### Grade A / B / C / D

- Implemented in **`ai_quality_grade`** (`scalp_engine.py` ~3140+): composite **0–100** score; thresholds from **`GRADE_THRESHOLDS`** default **`{A:80, B:60, C:40}`**; below C → **D**.
- **`GRADE_THRESHOLDS`** not present in checked-in **`config.yaml`** → **in-code defaults** unless overridden locally.
- **`GRADE_SIZING_ENABLED`** + **`GRADE_SIZE_MAP`** / **`GRADE_*_SIZE_MULT`** drive **`size_multiplier`**.

### Grade D = skip (scan output)

- **`run_scalp_scan`:** `grade == "D"` → **`gate_result = "BLOCKED"`**, **`executable = False`**, **`candidate_status_reason = grade_D_context_only`** (~4298–4302).

### Grade → execution path

- Scan/autopilot consumers should respect **`executable`** and **`gate_result`** (`athena.py` paths referenced in prior grep).
- **`execution.py` `api_scalp_execute`:** **does not** reject Grade D or **`executable: false`** — **manual POST can bypass** (see BUG-D-4).

### Grading inputs vs closed bars

- VP structure defaults **`USE_FORMING_FOR_STRUCTURE: false`**; grading ties to pipeline candles wired in **`run_scalp_scan`**. Residual forming-bar risk if structure forming flag enabled or execution TF uses forming candles for flow features.

---

## Section 4 — Setup Type Detection

### Mean reversion

- **`market_state == "balance"`** and location **`at_vah` / `at_val` / `outside_va`**.
- Direction: VAH → SHORT to POC; VAL → LONG to POC; **`outside_va`** uses **`above_va`** flag.
- Confirmations: strict Fabio requires **`_setup_aggression_confirmed`**; legacy allows VWAP/CVD/absorption combinations.

### Trend continuation

- **`market_state == "imbalance"`** and location set includes **`at_lvn`** (and others unless **`STRICT_TREND_LOCATION_LVN_ONLY`** forces LVN-only failures).
- Direction: AAA complete → direction; else **HTF EMA bias** (`infer_bias_from_ema_stack`, needs **≥200** bars on bias series); else VWAP lean.

### Trend extension (third type)

- **`outside_va`** + **`imbalance`** — **not** listed in module docstring “two setup types” (documentation drift).

### Setup conflict / priority

- **`_classify_setup`** uses sequential **`if` / `return`** — **only one** setup per invocation.

### False setup / POC breach

- **`calculate_scalp_levels`** applies mechanical **TP1** from **SL distance × multiplier**, **`rr_below_min`**, SL clamps when VP contradicts side — structural POC vs mechanical TP handled explicitly (`structure_target_close`, etc.).

---

## Section 5 — Session Filter

### NY open skip

- **`09:30`** America/New_York local, plus **`NY_OPEN_SKIP_MINUTES`** (and **`BT_NY_OPEN_SKIP_MINUTES`** in backtest).

### London cash open skip

- **`_london_cash_open_utc_minute_of_day`:** **08:00 Europe/London** converted to UTC minute-of-day + **`LONDON_OPEN_SKIP_MINUTES`**.
- **YAML comment** referencing **07:00 UTC** may **not** match code (DST-dependent) — see BUG-D-6.

### Session mode tracing

- **`_resolved_normalized_session_mode`:** **`SESSION_MODE`**, **`SESSION_MODE_BY_ASSET`**, **`CRYPTO_SESSION_MODE`**, with **`BT_*`** counterparts when **`backtest=True`**.

### Crypto session filter

- Modes like **`asia_london_ny`** union windows; **gaps** → **`off_hours`** unless mode is **`all`**. This **filters crypto** — intentional per config, but **not** 24/7 unless **`SESSION_MODE`** / **`SESSION_MODE_BY_ASSET`** says **`all`**.

### Backtest vs live

- **`backtest_pair_scalp`** calls **`scalp_session_window(..., backtest=True)`** — session filter **is applied**.
- Default **`BT_SESSION_MODE: all`** vs live **`london_ny`** — **known divergence** (broader BT windows).

---

## Section 6 — Execution Handoff

### `timed_exit_monitor.py` Engine D bypass

- **`engine in ("scalp", "engine d", "scalp_vp")`** after **`.lower()`** — MT5 and Bybit row handlers (~1145–1146, ~1428–1429).

### Signal engine label

- **`run_scalp_scan`** emits **`"engine": "SCALP"`** → lowercases to **`scalp`** — matches bypass tuple.

### TP1 as 1R self-pay

- **`calculate_scalp_levels`** docstring describes mechanical **1R** style target, but **`tp1_r_mult = max(TP1_R_MULT, min_rr_cfg)`** (~3070) couples TP1 distance to **`MIN_RR`** when **`MIN_RR > TP1_R_MULT`** — **not** strictly “1R” (see BUG-D-3).
- Signal **`rr_partial: 1.0`** hardcoded (~4332) can **disagree** with geometry.

### SL placement

- VP boundary ± **buffer** (ATR/asset floors), then optional **ATR SL** widening (`ATR_SL_ENABLED`) — farther invalidation wins.

### Grade D to broker

- **Scan path:** blocked. **Manual `api_scalp_execute`:** **not** blocked in **`execution.py`** (BUG-D-4).

---

## Section 7 — Threshold Calibration Assessment

| Key | Assessment |
|-----|--------------|
| **BALANCE_THRESHOLD** | CALIBRATED — splits regimes; **None → balance** arguably TOO_LOOSE for fail-closed regime detection |
| **STRICT_FABIO_GATE_ENABLED** | CALIBRATED — three-pillar enforcement |
| **VP_VALUE_AREA_PCT / VP_BINS** | CALIBRATED — standard VP knobs |
| **VP_LVN_THRESHOLD** | CALIBRATED — POC-relative LVN |
| **VP_PROXIMITY_PCT / VP_PROXIMITY_ATR_K** | CALIBRATED — ATR proximity avoids huge %-of-price bands on FX |
| **MIN_RR + score_group_overrides** | CALIBRATED — unified RR floor |
| **TP1_R_MULT vs MIN_RR (`max(...)`)** | TOO_STRICT / MISLABELLED — contradicts “1R pay-yourself” semantics |
| **Backtest grade gate vs EXECUTION_MIN_GRADE** | TOO_LOOSE risk — parity gap (BUG-D-2) |
| **BT_SESSION_MODE: all** | OVERKILL vs live — intentional breadth |
| **TRADE_BUCKET_MIN_LEVELS** | CALIBRATED (YAML notes lowered for reliability) |
| **SKIP_CRYPTO_ON_AGGTRADE_UNAVAILABLE** | OVERKILL for diagnostics — skips entire pair |

---

## Section 8 — Dead Code & Config Gaps

### Functions / paths

| Item | Location | Notes |
|------|----------|--------|
| **`is_valid_session`** | `scalp_engine.py` ~930–959 | **Unused** in production; only tests/monkeypatch reference **`scalp_session_window`** as active API |

### Config keys

| Key | Notes |
|-----|--------|
| **`GRADE_THRESHOLDS`** | Absent from checked-in **`config.yaml`** — grading uses **defaults in code** |
| **`VP_PROXIMITY_USE_ATR`** | Absent from **`config.yaml`** — **always** code default **`True`** |

### `classify_profile_interaction` (`volume_profile.py`)

- **Not** Engine D consumer path — used from **`market_structure.py`**.

### `EODHD_COMMODITY_TICKERS`

- Read by **`eodhd_volume_overlay.eodhd_commodity_ticker_for_pair`** (~160–171) for **commodity** ticker resolution — **not** Engine-D-exclusive.

### Large commented blocks

- **`scalp_engine.py`** ~4668 lines — **not** exhaustively inventoried for commented dead logic in this audit.

---

## Ranked Bug List

### BUG-D-1 — Global `run_scalp_scan` session pre-gate ignores commodity/index/stock reality

- **Severity:** CRITICAL
- **File:** `scalp_engine.py`
- **Line:** 3606–3620
- **What:** Before the per-pair loop, aborts entire scan when **`scalp_session_window("forex")`** and **`scalp_session_window("crypto")`** are **both** false.
- **Should:** Do not use forex∧crypto OR as global veto for **all** asset types; rely on **`scalp_session_window(asset_type)`** per pair (~3692) or extend OR to relevant asset families.
- **Impact:** Windows where forex **and** crypto session filters are false may **suppress** scanning for **stocks, indices, commodities** that could still be valid under **`SESSION_MODE_BY_ASSET`**.
- **Fix:** Remove global gate or gate per asset class consistently.
- **Test:** Fixture time: forex+crypto false, **`scalp_session_window("stock")`** true → stock pair still scanned.

### BUG-D-2 — Backtest min grade ignores `EXECUTION_MIN_GRADE`

- **Severity:** HIGH
- **File:** `backtest_runner.py`
- **Line:** 5114–5116, 5389–5403
- **What:** `_min_grade_str` uses **`MIN_GRADE_AUTO_EXECUTE` / `MIN_GRADE`** default **`"C"`**, never **`EXECUTION_MIN_GRADE`**. Comment claims mirror of live; live uses **`_scalp_execution_min_grade`**.
- **Should:** Use **`_scalp_execution_min_grade(cfg)`** for `_min_grade_idx`.
- **Impact:** **Live vs backtest** executable grade floor **diverges** when operators set **`EXECUTION_MIN_GRADE`** without redundant **`MIN_GRADE_*`**.
- **Fix:** Import and call **`_scalp_execution_min_grade`**.
- **Test:** **`EXECUTION_MIN_GRADE: "A"`**, omit **`MIN_GRADE_*`** → BT must reject grades below A.

### BUG-D-3 — Mechanical TP1 uses `max(TP1_R_MULT, MIN_RR)`, not literal 1R; `rr_partial` stale

- **Severity:** HIGH
- **File:** `scalp_engine.py`
- **Line:** 3070–3072; signal ~4332 (`rr_partial`)
- **What:** **`tp1_r_mult = max(float(cfg.get("TP1_R_MULT", 1.0)), min_rr_cfg)`** forces TP1 distance ≥ **MIN_RR × SL** when **`MIN_RR > TP1_R_MULT`**. Payload still sets **`rr_partial: 1.0`**.
- **Should:** Product-defined: either true **1R** TP1 with **MIN_RR** enforced separately on structure/validation, or rename fields/comments to **“max(1R, MIN_RR)”** and emit accurate **`rr_partial`**.
- **Impact:** Operators expecting **Fabio 1R pay-yourself** at **`TP1_R_MULT=1`** get **≥ MIN_RR** mechanical exits when **`MIN_RR` > 1**.
- **Fix:** Decouple TP1 R-multiple from **`MIN_RR`** (explicit separate gates).
- **Test:** **`MIN_RR=1.2`**, **`TP1_R_MULT=1.0`** → assert TP1 distance matches intended contract.

### BUG-D-4 — Manual scalp execution does not enforce Grade D / `executable`

- **Severity:** MEDIUM
- **File:** `execution.py` (`api_scalp_execute`)
- **Line:** ~2006–2142
- **What:** No check on **`ai_grade == "D"`** or **`executable`** before **`risk_check` / `run_managed_execution`**.
- **Should:** Reject unless explicit audited override flag.
- **Impact:** Crafted POST could reach broker adapter with **context-only** setups.
- **Fix:** **`if str(sig.get("ai_grade")) == "D" or sig.get("executable") is False: return 400`** (policy-tunable).
- **Test:** POST Grade-D payload → HTTP 400.

### BUG-D-5 — `balance_ratio is None` forces `"balance"` market state

- **Severity:** MEDIUM
- **File:** `scalp_engine.py`
- **Line:** 1369–1371
- **What:** Missing ratio → **`balance`** (MR-friendly).
- **Should:** Fail-closed **`unknown`** / skip when strict mode demands VP integrity.
- **Impact:** Trend/imbalance regimes may be **misclassified** toward MR when VP metadata incomplete.
- **Fix:** Skip or alternate regime detector when **`br is None`** under strict config.
- **Test:** VP without session bounds / heuristic → expected skip vs default balance.

### BUG-D-6 — YAML London open comment vs code (08:00 London)

- **Severity:** LOW
- **File:** `config.yaml` (comment) vs `scalp_engine.py` ~50–56
- **What:** Comment cites **07:00 UTC** style wording; code anchors **08:00 Europe/London** → UTC varies with DST.
- **Should:** Align documentation to implemented anchor.
- **Impact:** Operator confusion only.
- **Fix:** Correct YAML comment.

### BUG-D-7 — Diagnostic `strict_fabio_pass` key collision / overwrite

- **Severity:** LOW
- **File:** `scalp_engine.py` (`_engine_d_aggression_fidelity` vs `_engine_d_strict_fabio_shadow` funnel updates)
- **What:** Same key name for different semantics; later **`dict.update`** overwrites.
- **Should:** Distinct keys (**e.g.** `crypto_aggtrade_strict` vs `pillar_strict`).
- **Impact:** Misleading diagnostics/UI.
- **Fix:** Rename keys in fidelity vs shadow payloads.

### BUG-D-8 — `is_valid_session` dead in production

- **Severity:** LOW
- **File:** `scalp_engine.py` ~930–959
- **What:** No production callers — **`scalp_session_window`** supersedes.
- **Should:** Remove or formally deprecate.
- **Impact:** Maintenance noise.

---

## THRESHOLD ASSESSMENT — ENGINE D

See Section 7 table (each key: CALIBRATED / TOO_STRICT / TOO_LOOSE / UNREACHABLE / OVERKILL + one-line justification).

---

## DEAD CODE — ENGINE D

See Section 8 tables (`is_valid_session`, absent **`GRADE_THRESHOLDS`** / **`VP_PROXIMITY_USE_ATR`** in checked-in YAML, `classify_profile_interaction` consumer note, **`EODHD_COMMODITY_TICKERS`** reader).

---

## NOT VERIFIED

- Full **`auto_trader`** Engine D scheduling beyond **`_signal_engine`** snippet
- Complete **`risk_engine.risk_check`** behavior for scalp payloads (grep: no `grade`/`scalp` hits in `risk_engine.py`)
- **`scanner.py`** → UI field parity for Engine D
- Exhaustive **`SCALP_ENGINE`** key-by-key consumption audit
- Full commented-block inventory in **`scalp_engine.py`**
- Line-by-line **`eodhd_volume_batch.py`** (summary-level review only)

---

## Appendix — Key Code References

### Global session pre-gate (`scalp_engine.py`)

```text
3606:3620 — sessions = get_current_sessions(); mt5_session_ok = scalp_session_window("forex");
            crypto_session_ok = scalp_session_window("crypto"); abort if both false
3692:3697 — per-pair session_ok = scalp_session_window(asset_type)
```

### TP1 R-mult (`scalp_engine.py`)

```text
3070:3072 — tp1_r_mult = max(TP1_R_MULT, min_rr_cfg); tp1 from sl_distance * tp1_r_mult
```

### Backtest grade gate (`backtest_runner.py`)

```text
5114:5116 — _min_grade_str from MIN_GRADE_AUTO_EXECUTE / MIN_GRADE / default "C"
5389:5403 — pre-trade grade skip uses _min_grade_idx
```

### Timed exit bypass (`timed_exit_monitor.py`)

```text
1145:1146, 1428:1429 — if engine in ("scalp", "engine d", "scalp_vp"): return
```

---

*End of audit document.*
