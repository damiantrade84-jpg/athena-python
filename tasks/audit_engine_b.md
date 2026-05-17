# PHASE 2 - Engine B Full Audit

Mode: audit-only. No production code/config changes were made by this audit. This file is the requested report artifact.

Verification basis: source inspection with exact line reads. I did not run a live scan, submit an execution request, or run a backtest in this audit pass.

## Required File Inventory

All required files were readable:

- `AGENTS.md`
- `market_structure.py`
- `zone_registry.py`
- `engine_b_ai.py`
- `signal_debate.py`
- `search_engine_b.py`
- `search_engine_b_generic.py`
- `scoring.py`
- `backtest_runner.py`
- `athena_app/services/structure_context.py`
- `config.yaml`

Additional files inspected because they consume or gate Engine B outputs:

- `scanner.py`
- `athena.py`
- `execution.py`
- `risk_engine.py`
- `auto_trader.py`
- `confidence_engine.py`

## Entry Point Inventory

Confirmed Engine B entry points:

- Core Engine B implementation: `market_structure.NakedEngine`, with timeframe resolution in `market_structure.py:383-403`, structure precompute in `market_structure.py:2440-2588`, SL/TP context in `market_structure.py:2864-2954`, output payload in `market_structure.py:3017-3084`, confidence scoring in `market_structure.py:3187-3660`, and final style/regime gate in `market_structure.py:890-907`.
- Full scan overlay path: `scanner.py:1007-1324` resolves Engine B style, builds structure/confidence, and overlays Engine B diagnostics/levels on Engine A scan signals.
- Dedicated naked scan path: `athena.py:6246-7079` scans Engine B directly and only appends passed per-pair signals.
- Analysis/refresh path: `_compute_naked_analysis` in `athena.py:5728-6085`; execution refresh consumes it in `execution.py:700-775`.
- Backtest path: `backtest_runner.backtest_pair_naked` resolves style/profile at `backtest_runner.py:4194-4213`, computes Engine B structure/confidence at `backtest_runner.py:4415-4453`, uses execution levels at `backtest_runner.py:4477-4509`, fills at next candle open at `backtest_runner.py:4538-4547`, and monitors barriers at `backtest_runner.py:4648-4656`.
- AI advisory path: `engine_b_ai.get_engine_b_ai_verdict` in `engine_b_ai.py:472-588`, called from `athena.py:6038-6080`.
- Execution gates: Engine B confirmation checks in `execution.py:408-424` and `risk_engine.py:781-818`; SL/TP side validation in `risk_engine.py:909-928`; RR geometry validation in `risk_engine.py:958-1070`.

## Live Scan Path

Dedicated `/api/scan-naked` path:

- Resolves style/profile and candle timeframes in `athena.py:6366-6384`.
- Rejects insufficient candles in `athena.py:6455-6459`.
- For crypto with Bybit ATR required and signal-feed fallback disabled, rejects when Bybit ATR is unavailable in `athena.py:6467-6479`.
- Rejects invalid ATR in `athena.py:6483-6487`.
- Runs structure for both directions in `athena.py:6616-6632`, skips non-`CLEAR` verdicts in `athena.py:6642-6649`, calculates confidence in `athena.py:6651-6657`, and applies the final Engine B style/regime gate in `athena.py:6675-6680`.
- Rejects failed final gates before signal construction in `athena.py:6696-6707`.
- Sets resolved style/profile fields, including `min_rr`, on the emitted signal in `athena.py:6718-6727`.

Full `/api/scan` overlay path:

- Resolves Engine B profile per pair in `scanner.py:1007-1015`.
- Builds market-state-aware candles in `scanner.py:1021-1143`.
- Handles Bybit ATR unavailability by setting `atr = 0.0` and `engine_b_error = "bybit_atr_unavailable"` when fallback is disabled in `scanner.py:1160-1170`, then only proceeds when `atr > 0` in `scanner.py:1173`.
- Computes Engine B structure and confidence in `scanner.py:1189-1215`.
- Applies top-level Engine B execution levels before final Engine B gate/direction-alignment handling in `scanner.py:1285` and `scanner.py:1309-1319`; this is BUG-B-1.

## Backtest Path

Confirmed current backtest behavior:

- `backtest_pair_naked` resolves the requested style into `resolved_style`/`style_profile` in `backtest_runner.py:4194-4200` and uses that profile for zone, entry, and ATR timeframes in `backtest_runner.py:4211-4213`.
- Structure precompute uses `style=resolved_style` in `backtest_runner.py:4415-4428`.
- Confidence calculation uses the resolved style profile and final `engine_b_confidence_passes(...)` in `backtest_runner.py:4441-4453`.
- Execution levels used for gating come first from `conf_data.execution_sl`/`execution_tp`, with `calc_levels(..., style=resolved_style)` only as fallback in `backtest_runner.py:4477-4501`.
- The actual backtest fill happens at the next entry-timeframe candle open plus slippage in `backtest_runner.py:4538-4547`.
- After the actual fill, the code reuses pre-fill SL/TP and computes distances with `abs(...)` without rechecking side geometry in `backtest_runner.py:4552-4582`; this is BUG-B-4.
- Same-bar TP/SL ordering is not the historical TP-first bias: `_resolve_barrier_exit` uses an open-to-level distance tiebreaker when both are touched in `backtest_runner.py:763-788`.

## Historical Issues Checked

- TP-before-SL bar ordering bias: not confirmed in current `_resolve_barrier_exit`; same-bar TP+SL uses open-to-level distance in `backtest_runner.py:763-788`. A different backtest level-validation issue is confirmed as BUG-B-4.
- `edgeProbability` None bug in Engine B AI scoring path: the successful AI payload validation now coerces missing/None edge probability to `50.0` in `engine_b_ai.py:103-111`, and missing response keys default `edgeProbability` to 50 in `engine_b_ai.py:567-579`. The failure placeholder can still contain `edgeProbability: None` in `athena.py:6065-6071`, but inspected execution paths do not use that field as Engine B approval.
- `style=resolved_style` backtest mismatch: current naked backtest uses `resolved_style` for precompute, fallback level calculation, result metadata, and DB notes in `backtest_runner.py:4194-4213`, `backtest_runner.py:4415-4428`, `backtest_runner.py:4491-4498`, and `backtest_runner.py:4852-4888`.
- `timeframe_alignment` divisor error: current logic uses normalized scores and a `0.85` divisor in `confidence_engine.py:148-176`; `scoring.calc_confluence` passes D1/H4/H1 proxies in `scoring.py:845-856`.
- Engine B AI verdict accidentally treated as execution approval: not confirmed. Engine B AI prompt labels the context advisory and says pass/fail is already checklist-decided in `engine_b_ai.py:394-400`; `_compute_naked_analysis` sets `res["passed"]` from `engine_b_confidence_passes(...)` before attaching AI in `athena.py:5976-5984` and `athena.py:6026-6080`; `auto_trader.py:1213-1230` uses `ai_analysis` for notification fields. BUG-B-6 covers misleading audit logging, not actual execution approval.

## 1. Core Scoring Formula

Confirmed formula:

- `calculate_confidence(...)` defines mandatory gates as structure, location, entry, room/space, RR, and optional macro in `market_structure.py:3187-3202`.
- Style/profile inputs are read in `market_structure.py:3210-3214`.
- Structure gate logic is in `market_structure.py:3231-3244`.
- Location, trigger, breakout, and room inputs are computed in `market_structure.py:3288-3319` and `market_structure.py:3400-3419`.
- Final execution levels and RR gate are computed through `resolve_engine_b_execution_levels(...)` in `market_structure.py:3332-3353`.
- Gate score is the count of passed mandatory gates in `market_structure.py:3425-3461`.
- Bonuses are BOS MTF, OB at zone, volume, optional follow-through, and optional profile points in `market_structure.py:3431-3468` and `market_structure.py:3473-3497`.
- `pct` is `total_score / max_possible` in `market_structure.py:3508`; pass/fail is strict or flexible checklist logic in `market_structure.py:3509-3522`.
- The final style/regime score floor is applied by `engine_b_confidence_passes(...)` in `market_structure.py:890-907`, using `engine_b_min_score_threshold(...)` in `market_structure.py:722-733`.

No confirmed core-formula bug found in the inspected current code.

## 2. BOS and CHoCH Detection

Confirmed behavior:

- BOS is close-based over recent structural swings and rejects missing swing context in `market_structure.py:1562-1588`.
- CHoCH can use BOS reference levels when BOS data exists: bearish BOS requires a later close above the prior reference high for bullish CHoCH, and bullish BOS requires a later close below the prior reference low for bearish CHoCH in `market_structure.py:1713-1736`.
- Structure precompute computes BOS on the resolved structure timeframe and D1 BOS separately in `market_structure.py:2537-2562`.
- MTF BOS confirmation is computed from structure and D1 BOS agreement in `market_structure.py:2572`.

No confirmed BOS/CHoCH detection bug found in the inspected current code.

## 3. Order Blocks, FVGs, and Zone Registry

Confirmed behavior:

- Order blocks are the last opposing candle before a BOS, with precomputed BOS bar index preferred when available in `market_structure.py:1780-1808`.
- FVG detection uses a three-candle gap model and tracks mitigation in `market_structure.py:2005-2035`.
- Structure precompute detects FVGs, active FVGs, order blocks, and registry-backed active zones in `market_structure.py:2516-2519` and `market_structure.py:2574-2585`.
- `zone_registry.ZoneRegistry.upsert_zones(...)` normalizes OB/FVG zones, merges matching zones, increments scan count, and persists when enabled in `zone_registry.py:28-60`.
- Mitigation uses edge crossing by direction in `zone_registry.py:62-89`.
- Persistence uses SQLite with `timeout=15.0` and WAL in `zone_registry.py:216-241` and `zone_registry.py:273-335`.

No confirmed OB/FVG/registry logic bug found in the inspected current code.

## 4. Liquidity Sweeps

Confirmed behavior:

- Sweep detection uses swing highs/lows when available, else local extrema from the configured lookback window in `market_structure.py:1903-1934`.
- Structure precompute feeds the latest swing high/low into `_detect_sweep(...)` in `market_structure.py:2539-2550`.
- Sweep participation is returned as `sweep_data` and `liquidity_sweep` in `market_structure.py:3044-3053`.
- Sweep can satisfy entry logic when combined with zone context in `market_structure.py:3392-3398`.

No confirmed liquidity-sweep bug found in the inspected current code.

## 5. Style Profiles and Per-Asset Differentiation

Confirmed behavior:

- Timeframe routing uses `resolve_engine_b_tfs(...)`, documented as the single source of truth for Athena, execution, scanner, and backtest in `market_structure.py:383-403`.
- Current base style profiles are `scalp min_score=5.0/min_rr=1.5`, `intraday min_score=5.0/min_rr=1.5`, and `swing min_score=5.5/min_rr=2.0` in `config.yaml:1777-1801`.
- Regime multipliers are configured in `config.yaml:1992-2000` and consumed by `_engine_b_regime_gate(...)` in `market_structure.py:699-719`.
- Dedicated Engine B scan emits the resolved `min_rr` in `athena.py:6726`.
- Naked backtest uses `resolved_style`, not the raw requested style, at `backtest_runner.py:4194-4213`, `backtest_runner.py:4415-4428`, `backtest_runner.py:4491-4498`, and `backtest_runner.py:4852-4888`.

Confirmed issues in this area: BUG-B-1 and BUG-B-5.

## 6. AI Layer

Confirmed behavior:

- Engine B AI prompt states that news/event context is advisory and pass/fail is already decided by the checklist in `engine_b_ai.py:394-400`.
- `get_engine_b_ai_verdict(...)` returns an error if no API key is configured in `engine_b_ai.py:472-498`.
- AI response key defaults and validation are in `engine_b_ai.py:567-579`; `edgeProbability` is coerced to numeric 0-100 in `engine_b_ai.py:103-111`.
- `_compute_naked_analysis` only runs AI if `force_ai` is true or `AI_ON_DEMAND_ONLY` is false in `athena.py:6026-6028`, and attaches AI output after the Engine B gate has already set `res["passed"]` in `athena.py:5976-5984` and `athena.py:6038-6080`.
- `auto_trader.py:1213-1230` reads `ai_analysis` for Telegram notification fields.
- `signal_debate.run_signal_debate(...)` is a separate auto-trade debate gate, not Engine B AI approval; no direct `engine_b_ai` execution approval path was found in inspected `execution.py` or `auto_trader.py`.

Confirmed issue in this area: BUG-B-6.

## 7. RR, TP, and SL Logic

Confirmed behavior:

- Structural SL/TP candidates are built from zones/sweeps/fallback RR in `market_structure.py:2864-2922`.
- Final executable SL/TP and RR are resolved in `calculate_confidence(...)` through `resolve_engine_b_execution_levels(...)` in `market_structure.py:3332-3353`.
- Dedicated scan emits SL/TP from `conf_data.execution_sl`/`execution_tp` in `athena.py:6728-6730`.
- Execution applies Engine B levels and rejects missing/invalid structural levels in `execution.py:882-894` and `execution.py:1752-1763`.
- Risk rejects wrong-side SL/TP in `risk_engine.py:909-928`.
- Risk enforces RR geometry for Engine B/consensus/D signals in `risk_engine.py:958-1070`.

Confirmed issues in this area: BUG-B-4 and BUG-B-5.

## 8. Live vs Backtest Parity

Confirmed parity:

- Backtest now uses resolved style/profile and execution-level outputs that match the live Engine B confidence path: `backtest_runner.py:4194-4213`, `backtest_runner.py:4415-4453`, and `backtest_runner.py:4477-4509`.
- Backtest crypto ATR level resolver fails closed when Bybit ATR is required and fallback is disabled in `backtest_runner.py:521-567`.
- Dedicated live scan also fails closed on the same Bybit ATR-unavailable condition in `athena.py:6467-6479`.

Confirmed parity gaps:

- `_compute_naked_analysis(...)`, which is used by execution refresh, reintroduces synthetic ATR after Bybit ATR unavailable; see BUG-B-2.
- Naked backtest does not revalidate SL/TP side after next-bar fill/slippage; see BUG-B-4.
- Full scan can propagate Engine B execution levels before final B gate/alignment; see BUG-B-1.

## 9. Dead Code and Dead Config

Confirmed:

- `search_engine_b.py:1-5` and `search_engine_b_generic.py:1-5` are root-level search helpers that only read `static/index.html` and print matching lines.
- `git grep` found those script names only in task/audit documentation, not in runtime imports.

Confirmed issue in this area: BUG-B-7.

Potential dead local, not reported as a behavior bug:

- `market_structure.py:3364-3369` computes `stop_valid`, but inspected local output and gate logic use `execution_levels_valid`, `rr_ok`, and side-valid execution-level diagnostics instead. I did not find `stop_valid` consumed elsewhere. This is cleanup-level only and not a confirmed behavior defect.

## 10. Threshold Calibration Assessment

No threshold change is recommended from this audit.

Arithmetic checked:

- Base style floors are `5.0`, `5.0`, and `5.5` in `config.yaml:1777-1801`.
- Regime multipliers are `TRENDING=0.90`, `RANGING=0.90`, `HIGH_VOLATILITY=0.85`, `LOW_VOLATILITY=1.15` in `config.yaml:1992-2000`.
- Effective floor is `ceil(base_min * regime_multiplier * 10) / 10` in `market_structure.py:722-733`.
- Therefore current effective base floors are:
  - Scalp/intraday: `4.3` high-volatility, `4.5` trending/ranging, `5.8` low-volatility.
  - Swing: `4.7` high-volatility, `5.0` trending/ranging, `6.4` low-volatility.
- Current max score is at least 9 in normal flexible mode when profile scoring is enabled: 5 mandatory gates plus 3 base bonuses plus 1 profile point, from `market_structure.py:3425-3468` and `config.yaml:1181`.
- Follow-through diagnostics are enabled but bonus application is disabled in `config.yaml:2088-2092`, so the follow-through max bonus is not included unless `ENABLED` is set true.

Assessment:

- The audited thresholds are reachable under the current formula; no arithmetic proof was found that they are unreachable, internally inconsistent, too strict, or too loose.
- Low-volatility swing requires 6.4 points, which means mandatory gates plus meaningful bonus/profile contribution, but this is still reachable with the current max score. No threshold change recommended.

## 11. Ranked Issues by Severity

## BUG-B-1

Severity: HIGH

File: `scanner.py`, `config.yaml`, `risk_engine.py`

Line/function: `_apply_engine_b_scan_levels(...)` at `scanner.py:117-130`; full scan overlay at `scanner.py:1285` and `scanner.py:1309-1319`; trade result append at `scanner.py:1525-1583`; return payload at `scanner.py:1659-1664`; current switches at `config.yaml:2114-2116` and `config.yaml:2133`; live geometry checks at `risk_engine.py:909-928`

Evidence:

- `_apply_engine_b_scan_levels(...)` writes `sl`, `tp1`, `tp2`, `levelSource`, and `level_source` onto the top-level scan signal in `scanner.py:117-130`.
- Current config enables that behavior with `ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS: true` in `config.yaml:2133`.
- Full scan calls `_apply_engine_b_scan_levels(sig_a, conf_b, res_b)` at `scanner.py:1285`, before final `engine_b_confidence_passes(...)` is applied at `scanner.py:1309-1315`.
- Direction mismatch is handled only after the top-level levels are already written in `scanner.py:1298-1319`.
- Current config does not require Engine B confirmation for Engine A trade tier because `ENGINE_B_SCAN_CONFIRMATION_GATE_ENABLED: false` in `config.yaml:2114-2116`.
- Trade rows are appended by final scan tier in `scanner.py:1525-1583` and returned as `tradeSignals` in `scanner.py:1659-1664`.

What it does:

Full scan can put Engine B execution SL/TP onto an Engine A trade signal before knowing whether the final Engine B style/regime gate passed or whether Engine B direction aligned with Engine A.

What it should do:

Only keep Engine B levels in Engine B diagnostic fields until final Engine B gate and direction alignment pass. Top-level executable SL/TP should remain Engine A levels unless Engine B is confirmed and direction-aligned.

Impact:

Same-direction but non-passing Engine B overlays can become executable levels for an Engine A trade. Opposite-direction B levels should be rejected later by risk side checks in `risk_engine.py:909-928`, but that still creates execution-rejection noise and a scan/execution handoff mismatch.

Fix:

Move top-level `sl`/`tp1`/`tp2` assignment until after `engine_b_confidence_passes(...)` and direction alignment. Keep `engine_b_execution_sl`/`engine_b_execution_tp` as diagnostics for failed/non-aligned B overlays. Add a regression test where Engine B produces levels but final B gate fails and assert top-level Engine A levels are unchanged.

Confidence: CONFIRMED

## BUG-B-2

Severity: HIGH

File: `athena.py`, `execution.py`, `backtest_runner.py`

Line/function: `_compute_naked_analysis(...)` at `athena.py:5884-5913`; dedicated scan Bybit fail-closed at `athena.py:6467-6479`; execution refresh at `execution.py:713-755`; backtest ATR resolver at `backtest_runner.py:521-567`; current ATR config at `config.yaml:127-129`

Evidence:

- Current config requires Bybit levels feed and disables signal-feed fallback for Engine B crypto levels in `config.yaml:127-129`.
- Dedicated Engine B scan rejects crypto Bybit ATR unavailable when fallback is disabled in `athena.py:6467-6479`.
- Backtest level ATR resolution also returns `None, "bybit_unavailable"` when Bybit ATR is unavailable and fallback is disabled in `backtest_runner.py:544-561`.
- `_compute_naked_analysis(...)` sets `atr = 0.0` under the same crypto/Bybit/fallback-disabled condition in `athena.py:5884-5892`.
- Immediately afterward, `_compute_naked_analysis(...)` treats invalid ATR as a warning and creates a synthetic fallback ATR from current price and asset type in `athena.py:5898-5913`.
- Execution refresh calls `compute_naked_analysis(seed, force_ai=False)` in `execution.py:713-719`, then accepts the refreshed context if passed and executable levels exist in `execution.py:729-755`.

What it does:

The analysis/refresh path can convert a configured fail-closed Bybit ATR miss into synthetic ATR-derived Engine B levels.

What it should do:

When Bybit ATR is required and fallback is disabled, `_compute_naked_analysis(...)` should return an explicit error or non-executable result, matching dedicated scan and backtest behavior.

Impact:

Crypto Engine B execution refresh can validate stale structural signals on a different ATR basis than scan/backtest. This is a live/backtest parity gap and a fail-closed violation for configured ATR source policy.

Fix:

Return an error from `_compute_naked_analysis(...)` on crypto Bybit ATR unavailable when `ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK` is false. If synthetic ATR is retained for display-only analysis, mark the result non-executable and make `_refresh_engine_b_execution_context(...)` reject it. Add tests for naked analysis and execution refresh with Bybit ATR unavailable.

Confidence: CONFIRMED

## BUG-B-3

Severity: HIGH

File: `athena.py`, `execution.py`, `risk_engine.py`

Line/function: direction default in `_compute_naked_analysis(...)` at `athena.py:5734-5736`; execution refresh at `execution.py:713-775`; quick execute refresh trigger at `execution.py:825-834`; execute refresh trigger at `execution.py:1666-1674`; risk direction validation at `risk_engine.py:733-737`

Evidence:

- `_compute_naked_analysis(...)` reads `direction = str(sig.get("direction", "LONG")).upper()` and resets malformed directions to `LONG` in `athena.py:5734-5736`.
- `_refresh_engine_b_execution_context(...)` calls that function for stale Engine B execution context in `execution.py:713-719`.
- Direction mismatch rejection only runs when `original_direction` is present and different from refreshed direction in `execution.py:721-727`.
- Refresh writes the refreshed direction back into the executable signal in `execution.py:762-775`.
- Quick execute triggers this refresh for stale or missing-price Engine B context in `execution.py:825-834`; `/api/execute` triggers it for stale Engine B context in `execution.py:1666-1674`.
- Risk would normally reject missing/malformed direction in `risk_engine.py:733-737`, but refresh can rewrite the missing/malformed value to `LONG` before risk sees it.

What it does:

A stale structural Engine B execution payload with missing/null/malformed direction can be refreshed as `LONG`.

What it should do:

Execution refresh should reject missing or malformed original direction before calling `_compute_naked_analysis(...)`. Analysis defaults may be acceptable for display-only tooling, but not for execution refresh.

Impact:

Execution refresh can invent trade direction instead of failing closed on a required safety-critical field.

Fix:

In `_refresh_engine_b_execution_context(...)`, require `original_direction in {"LONG", "SHORT"}` before refresh. Optionally add an execution-mode parameter to `_compute_naked_analysis(...)` so invalid direction returns an error. Add quick-execute and execute regression tests for stale Engine B payloads with missing/malformed direction.

Confidence: CONFIRMED

## BUG-B-4

Severity: HIGH

File: `backtest_runner.py`, `risk_engine.py`

Line/function: `backtest_pair_naked` pre-fill levels at `backtest_runner.py:4477-4509`; next-bar fill at `backtest_runner.py:4538-4547`; post-fill level reuse at `backtest_runner.py:4552-4582`; barrier monitoring at `backtest_runner.py:4648-4656`; live side validation at `risk_engine.py:909-928`

Evidence:

- Backtest candidate levels are selected before actual fill in `backtest_runner.py:4477-4509`.
- Actual entry is moved to the next entry-timeframe candle open plus slippage in `backtest_runner.py:4538-4547`.
- After that fill, the backtest reuses `best["sl"]` and `best["tp"]`, computes `_sl_dist` and `_tp_dist` with `abs(...)`, and only rejects `target_rr <= 0` or below min RR in `backtest_runner.py:4552-4582`.
- There is no post-fill side check equivalent to live `risk_engine.py:914-928`.
- The reused levels are then passed to `_resolve_barrier_exit(...)` in `backtest_runner.py:4648-4656`.

What it does:

If the next open/slippage gaps through a precomputed stop or target, the backtest can continue with wrong-side SL/TP geometry because absolute distances hide the side violation.

What it should do:

After actual fill/slippage, enforce `LONG: sl < entry < tp` and `SHORT: tp < entry < sl`, or recompute Engine B execution levels at the actual fill and reject if invalid.

Impact:

Backtest results can include trades live risk would reject, distorting Engine B expectancy, win/loss classification, and live/backtest parity on gap bars.

Fix:

Add post-fill side validation before max-SL and barrier monitoring. Add a regression test with a valid pre-fill long candidate where next open gaps below the stop or above the target and assert no trade is recorded.

Confidence: CONFIRMED

## BUG-B-5

Severity: MEDIUM

File: `execution.py`, `risk_engine.py`, `config.yaml`, `athena.py`

Line/function: `_apply_level_override(...)` at `execution.py:289-360`; override application at `execution.py:927-934` and `execution.py:1765-1772`; risk Engine B min RR extraction at `risk_engine.py:958-975`; risk RR check at `risk_engine.py:994-1070`; dedicated scan `min_rr` emission at `athena.py:6726`; style `min_rr` config at `config.yaml:1777-1801`

Evidence:

- `_apply_level_override(...)` validates side geometry but does not validate RR against Engine B style `min_rr` in `execution.py:289-360`.
- Quick execute and execute apply overrides before risk check in `execution.py:927-934` and `execution.py:1765-1772`.
- `risk_engine.py:958-975` only raises Engine B `_min_exec_rr` from top-level `engine_b_min_rr`, `min_rr`, `required_rr`, or `engine_b_status.min_rr`; otherwise it falls back to `ENGINE_C_EXEC_MIN_RR`/`1.0`.
- Current Engine B style `min_rr` is 1.5 for scalp/intraday and 2.0 for swing in `config.yaml:1777-1801`.
- Dedicated scan emits `min_rr` in `athena.py:6726`, but `_compute_naked_analysis(...)`/execution refresh does not set a top-level `min_rr` in `execution.py:762-775`.

What it does:

For structural Engine B execution payloads that lack top-level min-RR metadata, a level override can reduce RR below the resolved Engine B style min RR but above the risk engine fallback of 1.0.

What it should do:

Every Engine B execution path should carry a resolved `min_rr`, and level overrides should be validated against it before risk. If resolved min RR is absent for an Engine B structural signal, fail closed or resolve it from style/profile.

Impact:

This is not the normal dedicated scan payload because that emits `min_rr`. The risk is stale refresh/manual/API payloads plus level override, where the execution RR contract can be weaker than the Engine B style gate.

Fix:

Set `engine_b_min_rr` or `min_rr` during `_refresh_engine_b_execution_context(...)` and require it for structural Engine B signals. Extend `_apply_level_override(...)` or the post-override risk path to reject overrides with RR below resolved Engine B min RR. Add tests for Engine B override RR of 1.2 under intraday min RR 1.5.

Confidence: CONFIRMED

## BUG-B-6

Severity: LOW

File: `engine_b_ai.py`

Line/function: Engine B AI prompt at `engine_b_ai.py:394-400`; AI audit logging at `engine_b_ai.py:598-630`

Evidence:

- The prompt states the context is advisory only and Engine B pass/fail is already decided by the price-action checklist in `engine_b_ai.py:394-400`.
- Successful Engine B AI review logging writes `execution_allowed_before_ai=True`, `execution_allowed_after_ai=True`, and `final_action="advisory"` in `engine_b_ai.py:598-630`.

What it does:

The audit row labels advisory reviews with execution-allowed booleans even though the AI review is not an execution approval gate.

What it should do:

Audit logging should mark execution permission fields as not applicable for advisory-only Engine B AI reviews, or include a separate advisory-only marker that downstream reports cannot confuse with approval.

Impact:

No inspected execution path uses this to approve a trade. The impact is audit/reporting truth: later consumers can misread AI advisory rows as if Engine B AI granted execution permission.

Fix:

Keep `final_action="advisory"` and change the permission fields to `None`/not-applicable if schema allows, or add explicit advisory-only fields and update consumers.

Confidence: CONFIRMED

## BUG-B-7

Severity: LOW

File: `search_engine_b.py`, `search_engine_b_generic.py`

Line/function: entire files, `search_engine_b.py:1-5` and `search_engine_b_generic.py:1-5`

Evidence:

- `search_engine_b.py:1-5` only opens `static/index.html` and prints lines containing `engine_b_overlay` or `engine_b_verdict`.
- `search_engine_b_generic.py:1-5` only opens `static/index.html` and prints lines containing `engine b`.
- `git grep` found these script names only in task/audit documentation, not runtime imports.

What it does:

Leaves one-off search helpers in the repository root.

What it should do:

Remove them or move them under a documented tooling/scratch location if still useful.

Impact:

No runtime impact found. Maintenance/noise issue only.

Fix:

Delete both scripts or move them under `tools/` with names and usage docs.

Confidence: CONFIRMED

## Not Verified

- No live broker/execution request was submitted.
- No live scan was run.
- No backtest was run.
- Frontend/UI behavior for `signal["executable"] = False` naked-scan rows was not inspected.
- The subagent import attempt reported a config safety fatal for real-order mode; this report does not depend on bypassing that guard.
