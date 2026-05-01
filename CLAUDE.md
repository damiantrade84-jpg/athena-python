---
description:
alwaysApply: true
---

# Sentinel Pro v4.0 — Claude Code Instructions

---

## ⚠️ Policy — Scoring Gates (STRICT)

**Do not change anything that alters live or backtest scoring unless the user explicitly instructs it.**

- **Engine A live:** `MIN_CONFLUENCE_CLASS`, `PAIR_PROFILES.min_confluence`, `AUTO_TRADE_MIN_SCORE`, `SCAN_QUANTILE_*`, confluence logic in `scoring.py` / `factor_scoring.py`, `analyze_pair` tiering. Current `config.yaml` does **not** define `MIN_CONFLUENCE_GROUP`; helper support remains in `scoring.py` only for backward-compatible configs that explicitly restore it. Active soft gating keys on the live factor route are `FOREX_ENGINE.session_soft_multiplier`, `FOREX_ENGINE.session_shoulder_multiplier`, `FOREX_ENGINE.session_shoulder_hours`, and `ADX_TREND_MIN_CLASS`.
- **Engine A backtest:** `BT_MIN`, `PAIR_PROFILES.bt_min`, `get_backtest_min_score_threshold`, backtest score gates in `backtest_runner.py`.
- **Engine B live + backtest:** `NAKED_ENGINE.style_profiles` (`min_score`, `min_rr`), `ENGINE_B_REGIME_MULTIPLIERS` (score scaling — TRENDING=0.90, RANGING=0.90, HIGH_VOL=0.85, LOW_VOL=1.15), `zone_multipliers` (structural width), naked checklist gates in `market_structure.py`.
- **Engine D (Scalp Lab):** `SCALP_ENGINE` in `config.yaml` (`MIN_RR`, `MAX_SPREAD_PIPS`, `WITH_TREND_ONLY`, `BIAS_TIMEFRAME`, `M1_CANDLES`, `M15_CANDLES`, `M5_CANDLES`, `H1_CANDLES`, `SESSION_MODE`, `NY_OPEN_SKIP_MINUTES`, `EXECUTION_TIMEFRAME`, `MIN_GRADE_AUTO_EXECUTE`, `BT_ENABLED`, `BT_SESSION_MODE`, `BT_NY_OPEN_SKIP_MINUTES`, `BT_WALK_BARS`, `BT_MAX_CONCURRENT`, `BT_SLIPPAGE_TICKS`, `BT_SCRATCH_ENABLED`, `BT_SCRATCH_BARS`, `BT_SCRATCH_MIN_R`, optional `SCALP_PAIRS`) and core pass/fail logic in `scalp_engine.py` (session filter via `scalp_session_window()`, spread filter, VP build, absorption/CVD/AAA, VWAP, setup classification, HTF bias gate, level math, `ai_quality_grade`).

Cosmetic UI copy is fine. **Do not "tune", "align", or "simplify" thresholds** in passing.

---

## ⚠️ Current Safety State

Athena is currently approved for **controlled paper/demo validation only**.

**Real-money automation is NOT approved.**

Mandatory runtime safety state:
- `PAPER_SOAK.ENABLED: true`
- `REAL_ORDERS_ALLOWED: false`

No change may enable real broker orders unless the user explicitly approves after:
- minimum 1 full trading week of paper/demo logs
- clean candle freshness logs
- no unexplained stale events
- no `ERROR_PATH_MISMATCH`
- no `ERROR_OFFSET_MISMATCH`
- risk sizing reviewed
- execution rejects reviewed
- drawdown reviewed
- manual approval

Any code path that bypasses paper mode, freshness gates, kill switch, or risk_check is a critical bug.

---

## ⚠️ Non-Negotiable Development Rules

- No guessing.
- No fake validation.
- No silent threshold changes.
- No execution safety weakening.
- No real orders while `PAPER_SOAK.ENABLED=true` or `REAL_ORDERS_ALLOWED=false`.
- All strategy changes must be config-gated and default-safe.
- All behavioural changes require tests.
- All diagnostic/audit modes must be report-only unless explicitly approved.
- Risk, freshness, kill-switch, duplicate-position, max-risk, and account-balance gates must never be bypassed.
- AI may not override risk or freshness gates.
- Engine changes must preserve live/backtest/paper parity.

---

## ⚠️ Candle Freshness / Data Integrity Rules

Freshness gate remains mandatory.

**Confirmed-only candle logic:**
- A one-bucket lag behind the current forming candle is allowed only when the path policy is `CONFIRMED_ONLY` and the latest expected confirmed candle is present.
- This must be classified as `CONFIRMED_ONLY_OK`.
- It must not be treated as true stale data.
- True stale means the latest candle is older than the expected confirmed candle.

**Risk gate behaviour:**
- Allow: `OK`, `CONFIRMED_ONLY_OK`
- Block: `TRUE_STALE_1_BUCKET`, `TRUE_STALE_MULTI_BUCKET`, `PROVIDER_STALE`, `PROVIDER_ERROR`, `ERROR_PATH_MISMATCH`, `ERROR_OFFSET_MISMATCH`, `WARNING_ONE_BUCKET_LAG` when policy expects forming/current data

Never disable the freshness gate to fix a signal issue.

---

## ⚠️ H4 Offset / Source Grid Rules

Document the latest validated H4 grids:
- **Binance crypto:** 0h UTC grid, 00/04/08/12/16/20 UTC.
- **MT5 forex/metals/commodities/indices:** 2h H4 grid, 02/06/10/14/18/22 UTC.
- **MT5 US stocks:** 3h session H4 grid, 15/19 UTC observed.
- **MT5 D1 must remain UTC 00:00.**
- Do not apply H4 broker/session offset to D1.
- Do not apply MT5 offsets to Binance crypto.
- H4 alignment must be source/session-aware, not only asset-type based.

**Required diagnostic tools:**
- `tools/live_feed_diagnostics.py`
- `tools/probe_mt5_h4.py`
- `tools/probe_mt5_d1.py`
- `tools/validate_live_feed_matrix.py`

Before paper/demo:
- full H1/H4/D1 matrix must pass for configured symbols.
- every row must show `providerStatus ok`, `policyStatus POLICY_OK`, and `gateDecision ALLOW` or confirmed-only equivalent.

---

## ⚠️ Engine A Rules

Engine A v2 is validated and test-covered.

Do not lower Engine A thresholds without evidence from:
- signal funnel distribution
- near-miss counts
- paper/demo results
- gate-failure breakdown

**Known current state:**
- Engine A is producing A_ONLY signals.
- Engine A threshold was not proven to be too high.
- Engine A `volume_ratio`, `macro_context`, and `intermarket_context` are wired in `factor_scoring.py` Phase 2 (config-gated via `VOLATILITY_SCALER_ENABLED`, macro context from `fetch_macro_context()`, and intermarket from `intermarket_context` feed). These feed into the addon/conviction path, not as standalone factor weights.

Do not silently reweight these factors or bypass their config gates without explicit testing.

---

## ⚠️ Engine B Rules

Engine B is strict by design.

**Document the distinction:**
- `structural_verdict == CLEAR` means data/structure analysis ran successfully.
- `checklist structure_ok` is a tradeability gate and can fail even when `structural_verdict` is CLEAR.

**Engine B must require:**
- structure
- location
- entry trigger
- room/RR
- D1 conflict check
- checklist `confidence.passed == true`

Engine B `passed=True` rows must have empty `hard_fail_reasons`.

**Engine B diagnostics must separate:**
- `hard_fail_reasons`
- `soft_warnings`
- `diagnostic_notes`

**Current validated findings:**
- Engine B raw threshold is not the main bottleneck.
- `confidence.passed false` is the main reason B does not pass often.
- `no_trigger_pattern` often means structure is ready but entry trigger is not present.
- `structural_tp_too_close` often falls back to RR-based TP and is not automatically a bug.
- D1 PD array conflict within configured ATR distance is a safety gate.

Do not weaken Engine B checklist for execution.

**Safe future improvement:**
- expand watchlist visibility only.
- `STRUCTURE_READY_NO_TRIGGER` may become watchlist, not execution.
- D1 conflict watchlist candidates may be shown, not executed.
- next valid target logic may be simulated/report-only before any execution use.

---

## ⚠️ Engine B Crypto Profile

Crypto Engine B has its own execution profile behind safe config gates.

**Current confirmed state:**
- Crypto live prices are working through `/api/prices`.
- Crypto candle data is working through Binance USD-M futures klines.
- Engine B crypto scoring is working.
- Engine B crypto structure detection is working.
- Crypto Target v2 can find real H4/D1 structural targets.
- Crypto Engine B now has its own execution profile.
- Fallback projection is diagnostics-only and must never create a final crypto signal.
- Existing forex, commodities, indices, stocks, Engine A, Engine C, and Live Cockpit behavior must remain unchanged unless explicitly requested.

**Non-Negotiable Rules:**
Do not change these without explicit user approval:
- Engine A scoring logic
- Engine C consensus logic
- Live Cockpit
- live price pollers
- forex/index/stock/commodity Engine B behavior
- default crypto config values
- fallback projection safety rules

Do not force crypto signals by:
- lowering RR blindly
- loosening trigger gates blindly
- allowing fallback projection as a final target
- bypassing structural target requirements
- forcing Engine C consensus when Engine B does not pass

If no crypto signals pass after the crypto profile is enabled, the correct output is:
```
No valid crypto Engine B setup under current market conditions.
```

**Crypto profile config gates (all default to false):**
- `ENGINE_B_CRYPTO_PROFILE_ENABLED` - Master switch for crypto profile
- `ENGINE_B_CRYPTO_TRIGGER_PROFILE_ENABLED` - Crypto-specific M15/M5 trigger detection
- `ENGINE_B_CRYPTO_TARGET_V2_ENABLED` - Crypto Target v2 with structural target search
- `ENGINE_B_CRYPTO_ALLOW_FALLBACK_TARGET_FOR_PASS` - Fallback projection pass policy (default false)
- `ENGINE_B_CRYPTO_REQUIRE_STRUCTURAL_TARGET_FOR_PASS` - Require structural target for final pass

**Crypto profile features:**
- Target v2: H4/D1 liquidity zone search with candidate validation (RR, ATR bounds, path clarity, direction)
- Trigger profile: M15/M5 timeframes with volume ratio, taker buy/sell pressure, displacement ATR
- Location buffer: ATR-based buffer for trend-continuation setups
- Binance futures kline: Includes taker buy/sell volume provenance for orderflow analysis

**Audit and validation:**
- `crypto_engine_b_gate_calibration.py` - Validation-only audit (no tuning recommendations)
- H4 target provenance comparison (A/B/C/D scenarios)
- Audit-only config overrides do not write to config.yaml
- All crypto features are report-only unless explicitly enabled

---

## ⚠️ Engine C Rules

Engine C must not use failed Engine B confirmation.

Engine B can only count in Engine C when:
- `structural_verdict == CLEAR`
- direction is LONG or SHORT
- normalized score exceeds threshold
- `confidence.passed == true`

Engine C must not create aligned/B-only execution from failed Engine B checklist.

**Engine C decision states:**
- `A_ONLY`
- `B_ONLY`
- `ALIGNED`
- `CONFLICT`
- `WATCHLIST`
- `BLOCKED`

Engine C can be starved if Engine B fails checklist. Do not lower Engine C threshold to compensate for missing Engine B confirmation.

---

## ⚠️ Engine D / Scalp Lab Rules

Engine D is working and being called.

The previous issue was UI visibility:
- Scalp Lab only displayed PASS rows.
- Diagnostic Mode now displays skipped/no-pass rows and fail reasons.

**Engine D is strict by design:**
- `MIN_GRADE: B`
- `WITH_TREND_ONLY: true`
- `MIN_RR: 2.0`
- `VP_PROXIMITY_PCT: 0.30`

Do not lower Engine D thresholds yet.

**Engine D should currently be used as:**
- crypto scalp radar
- paper/watchlist only
- diagnostic-first

Crypto has the strongest Engine D data because Binance provides real volume and aggTrade/CVD data.

Forex, commodities, indices, metals, and stocks are microstructure-limited:
- MT5 tick volume is noisy.
- CVD may be synthetic.
- absorption confirmation is less reliable.

Non-crypto Engine D outputs should be treated as watchlist/diagnostic unless later validated by paper evidence.

**Safe future improvement:**
- Grade A/B = valid scalp candidate.
- Grade C with good location + trend + RR = WATCHLIST only.
- Grade D = no setup.
- Do not create execution signals from Grade C without explicit approval.

**Engine D audit logs:**
- `logs/scalp_audit/engine_d_funnel.jsonl`
- `docs/diagnostics/engine_d_scalp_audit.md`

---

## ⚠️ AI Review Rules

AI is now safety-grounded but must remain controlled.

**AI review modes:**
- Marcus Reid: commentary-only
- Engine B AI: commentary-only
- News sentiment: context-only
- Signal Debate: downgrade-only
- Chart Vision: downgrade-only by default

**Mandatory AI safety rules:**
- AI may not bypass `risk_check`.
- AI may not bypass candle freshness gates.
- AI may not bypass paper mode.
- AI may not increase position size.
- AI may not override kill switch.
- AI may not create execution permission by itself.

**Signal Debate:**
- `score_adjustment` must be clamped to `<= 0.0`.
- Debate may reduce score or block.
- Debate may not increase `confluenceScore`.

**Chart Vision:**
- `AI_VISION_CAN_UPGRADE_TRADE` must default to `false`.
- `CONTRADICT` may set `trade=false`.
- `CONFIRM` may set `ai_visual_confirmed=true` and `vision_supports_setup=true`.
- `CONFIRM` must not flip `trade` from `false` to `true` unless `AI_VISION_CAN_UPGRADE_TRADE=true` and all additional safety conditions are explicitly met.

**AI context must include:**
- `candleFetchMeta`
- `dataFreshness`
- candle timestamps
- freshness gate decision
- engine context
- entry/SL/TP/RR where applicable

If freshness/timestamp context is missing, AI review must be `REVIEW_INCOMPLETE` or cautionary.

**AI audit logger:**
- `ai_review_logger.py`
- `logs/ai_review/ai_review_audit.jsonl`

---

## ⚠️ Telegram / Runtime Safety

Telegram conflicts can block diagnostic scans.

**Environment override:**
```
ATHENA_DISABLE_TELEGRAM=1
```

This may be used for local scans/audits to prevent Telegram bot startup.

This override must only disable Telegram startup/polling. It must not affect:
- scanning
- data freshness
- paper mode
- risk checks
- audit logging
- strategy logic

---

## ⚠️ Threshold Audit Rules

Threshold audit is report-only.

Do not lower thresholds based on "few signals" without:
- signal funnel data
- score distributions
- near-miss counts
- fail reason counts
- shadow threshold simulation
- paper/demo results

**Current validated conclusion:**
- Engine A threshold should not be lowered.
- Engine B raw threshold should not be lowered.
- Engine C thresholds should not be lowered.
- Engine B checklist should not be weakened for execution.
- Watchlist expansion is preferred over execution expansion.

**Threshold audit files:**
- `threshold_audit.py`
- `tools/threshold_audit_report.py`
- `docs/diagnostics/threshold_audit_report.md`
- `docs/diagnostics/engine_b_checklist_audit.md`
- `logs/threshold_audit/signal_funnel.jsonl`

---

## ⚠️ Paper Soak Rules

Paper/demo can continue.

Paper soak must log:
- all signals
- watchlists
- blocked signals
- execution decisions
- freshness blocks
- risk blocks
- engine consensus
- paper entries/exits if tracked

**Required logs:**
- `logs/paper_soak/signals.jsonl`
- `logs/paper_soak/execution_decisions.jsonl`
- `logs/paper_soak/freshness_blocks.jsonl`
- `logs/paper_soak/risk_blocks.jsonl`
- `logs/paper_soak/engine_consensus.jsonl`

Real-money cannot be considered until:
- 1 full trading week minimum paper soak
- clean diagnostics
- reviewed drawdown
- reviewed execution rejects
- risk sizing verified manually
- no unexpected real order calls
- explicit user approval

---

## ⚠️ Required Validation Commands

After meaningful code changes, run:
```bash
python -m pytest
```

Run `py_compile` on changed Python files.

**For data freshness:**
```bash
python tools/live_feed_diagnostics.py --symbols EURUSD,GBPUSD,XAU/USD,XAG/USD,AAPL,NVDA,MSFT,TSLA,BTCUSDT,ETHUSDT --timeframes H1,H4,D1 --json-output
```

**For MT5 H4:**
```bash
python tools/probe_mt5_h4.py --symbols EUR/USD,XAU/USD,AAPL,NVDA,"S&P 500","NASDAQ-100",WTI Oil --count 10 --json-output
```

**For threshold audit:**
```bash
ATHENA_THRESHOLD_AUDIT=1
ATHENA_DISABLE_TELEGRAM=1
python athena.py scan
python tools/threshold_audit_report.py --input logs/threshold_audit/signal_funnel.jsonl
```

**For paper soak report:**
```bash
python tools/paper_soak_report.py --log-dir logs/paper_soak
```

---

## ⚠️ Current System Status Summary

Current validated status:
- Candle freshness: fixed and policy-aware.
- H4 source/session alignment: fixed.
- MT5 D1 UTC handling: protected.
- Engine A: validated, do not lower threshold yet.
- Engine B: strict by design, do not weaken execution checklist.
- Engine C: must require Engine B checklist pass.
- Engine D: working, diagnostic mode added, do not lower thresholds yet.
- AI review: freshness-grounded, downgrade-only for execution by default.
- Paper/demo: approved.
- Real-money automation: not approved.
- Next priority: paper soak evidence, not more tuning.

---

## ⚠️ Research Lab Scoring Experiment — Engine A Candidate Factors (2026-04-29)

The Research Lab findings have been promoted into **Engine A live/backtest scoring as a reversible experiment**.

**Config gate:** `ENGINE_A_RESEARCH_LAB_FACTORS` in `config.yaml`

Current enabled candidates:
- Forex majors/crosses: `obv_divergence`, `bollinger_touch`, `stochastic_cross`
- Forex exotics: `obv_divergence`, `stochastic_cross`
- Crypto majors/alts/meme: `stochastic_cross`, `chandelier_trend`, `obv_divergence`
- Metals: `aroon_trend`
- Other commodities: `bollinger_touch`

Implementation:
- Code lives in `factor_scoring.py`:
  - `_research_lab_candidate_addon()`
  - `_research_factor_value()`
  - `_research_obv_value()`
  - `_research_stochastic_value()`
  - `_research_chandelier_value()`
  - `_research_bollinger_value()`
  - `_research_aroon_value()`
- Output diagnostics:
  - `factor_scores.research_lab`
  - `research_lab_value`
  - `research_lab_detail`
  - `feed_status.research_lab`
- The factor is bounded by config:
  - `BONUS: 0.15`
  - `PENALTY: -0.10`
  - `MAX_ABS: 0.20`
- It is added through the existing addon/conviction path, not by changing `MIN_CONFLUENCE_CLASS`, `PAIR_PROFILES.min_confluence`, `BT_MIN`, Engine B gates, or execution/risk logic.

**Revert path if paper/backtest results do not improve:**
1. Set `ENGINE_A_RESEARCH_LAB_FACTORS.ENABLED: false` in `config.yaml`.
2. Restart Athena.
3. Optional full code revert: remove the helper functions listed above from `factor_scoring.py` and remove `research_lab` diagnostic fields from `factor_scores` / return payloads.

**Do not expand this experiment** to more groups, higher bonuses, or threshold changes without:
- Research Lab group/zone evidence
- backtest comparison against previous setup
- paper/demo results
- fail-reason review
- explicit user approval

## ⚠️ Research Lab Scoring Experiment — Engine B Candidate Gates (2026-04-29)

The Research Lab Engine B findings have been promoted into **Engine B live/backtest confidence as a reversible experiment**.

**Config gate:** `ENGINE_B_RESEARCH_LAB_FACTORS` in `config.yaml`

Current enabled candidates:
- `commodity_other`: `micro_breakout`, `vwap_deviation`, `cvd_momentum`, `vwap_reclaim`
- `metals`: `micro_breakout`, `vwap_deviation`, `cvd_momentum`, `vwap_reclaim`

Implementation:
- Code lives in `market_structure.py`:
  - `_engine_b_research_lab_candidate_gates()`
  - `_engine_b_micro_breakout_value()`
  - `_engine_b_vwap_deviation_value()`
  - `_engine_b_cvd_momentum_value()`
  - `_engine_b_vwap_reclaim_value()`
- Output diagnostics:
  - `research_lab_detail`
  - `research_lab_entry_upgrade`
  - `research_lab_location_upgrade`
  - `original_trigger_ok`
  - `original_location_ok`
- With `ALLOW_GATE_UPGRADE: true`, Research Lab candidates may satisfy only the matching Engine B checklist gate:
  - `micro_breakout`, `cvd_momentum`, `vwap_reclaim` -> entry/trigger gate
  - `vwap_deviation` -> location gate
- This does **not** change Engine B thresholds, `NAKED_ENGINE.style_profiles.min_score`, `min_rr`, RR math, structural target rules, D1 conflict handling, freshness gates, risk checks, or execution safety.

**Revert path if paper/backtest results do not improve:**
1. Set `ENGINE_B_RESEARCH_LAB_FACTORS.ENABLED: false` in `config.yaml`.
2. Restart Athena.
3. Optional partial revert: set `ENGINE_B_RESEARCH_LAB_FACTORS.ALLOW_GATE_UPGRADE: false` to keep diagnostics while preventing checklist gate upgrades.
4. Optional full code revert: remove the helper functions listed above from `market_structure.py` and remove `research_lab_*` diagnostic fields from `calculate_confidence()`.

**Do not expand this experiment** to more groups, direct RR changes, threshold changes, or execution bypasses without:
- Research Lab group/zone evidence
- retest output for the specific candidate
- backtest comparison against previous setup
- paper/demo review
- fail-reason review
- explicit user approval

---

## ⚠️ UI/API Contract Rules

The UI must display backend contract fields, not legacy placeholders.

**Flask backend returns flat JSON — no `{ data: T }` wrapper:**
- `apiClient.getJson()` / `postJson()` return the response body directly.
- Frontend must **not** extract `.data` from responses.
- Legacy `.data` extraction was removed from `apiClient.ts` during the 2026-04-30 audit.
- If a route needs metadata, it returns it at the top level (e.g., `{ results: [...], meta: {...} }`), not nested under `.data`.

**Engine A UI must use Engine A v2 fields:**
- `factorScores.trend`
- `factorScores.momentum`
- `factorScores.addon`
- `trendCoherence`
- `adxValue`
- `sessionMultiplier`
- `conviction`
- `direction`
- `score/maxScore/threshold`
- fail reasons

Do not reintroduce legacy Engine A vote fields as active scoring indicators unless backend Engine A v2 actually returns and uses them.

**Freshness display must be policy-aware:**
- `CONFIRMED_ONLY_OK` must not be shown as `stale_1_bucket`.
- policy-aware `consistencyStatus` takes priority over raw `stalenessSeverity`.
- raw `stalenessSeverity` may appear only in detailed diagnostics, not as a blocking warning when `gateDecision` is ALLOW.

**Scan responses must include:**
- `payloadVersion`
- `contract` metadata
- `generated_at/run_id` where available
- engineA/engineB/engineC/engineD contract names

UI must warn or fall back safely if `payloadVersion` is missing or old.

**Guardian / Sidebar live state:**
- `useStore.tsx` polls `/api/guardian/status`, `/api/last-scan`, `/api/open-trades-timed`, and `/api/prices` every 5 s.
- Guardian daily loss limit and max open risk come from the live backend config — no hardcoded frontend overrides.
- `SqnBadge.tsx` and `LivePrice.tsx` use `fmtNum()` null guards to prevent crash-on-null.

**Execution payload safety:**
- UI payload must never be trusted as the source of truth for freshness/risk.
- Backend must re-check freshness, risk, kill switch, paper mode, duplicate trades, and signal age.
- UI must not allow WATCHLIST/BLOCKED states to execute.

---

## ⚠️ Data Protection

`audit.db` and `candle_cache.db` contain all live trading history.
- **NEVER** delete, overwrite, or zip these files during updates.
- Run `python backup_db.py` before any major code changes.
- Hardcoded DB/reset/restore safeguards must remain untouched unless user explicitly requests that exact change.

---

## Project Overview

Multi-asset algorithmic trading system built on Flask. Covers forex, crypto, stocks, commodities, indices.

**Four top-level engines:**
- **Engine A v2** — 3-Factor Quantitative Scoring: **Factor 1 (Trend)** multi-TF EMA alignment D1/H4/H1 determines direction only; **Factor 2 (Momentum Quality)** RSI+MACD confirmation sizes conviction 0-1; **Factor 3 (ADX Gate)** hard abort <15, soft 0.65× at 15-25, full ≥25. One asset-class addon: forex=carry z-score, crypto=funding rate, commodity=COT z-score. Unified 0-3.0 scale for ALL asset classes including forex. Session soft multiplier for forex (0.75-1.0×). All implemented in `factor_scoring.py`.
- **Engine B** — Naked price-action (market structure, zones, BOS/CHoCH, FVG, swing sequence)
- **Engine C** — Consensus layer: blends A + B + AI Vision confirmation into a conviction-tiered signal
- **Engine D (Scalp Lab)** - Fabio Valentini VP+OrderFlow scalping (`scalp_engine.py`, `volume_profile.py`). Pipeline: Volume Profile (POC/VAH/VAL/LVN) -> Absorption/CVD/AAA -> VWAP lean -> setup classification (Mean Reversion / Trend Continuation) -> `ai_quality_grade` (A/B/C/D). Execution on M1 (configurable) with NY open cooldown. Session filtering via `scalp_session_window()`. Not part of A/B/C consensus. Dashboard **Scalp Lab** panel: `POST /api/scalp-scan`, `POST /api/scalp-execute`. Backtest: `POST /api/backtest-scalp` -> **Engine D (Scalp VP)** button in backtest panel.

**Execution:** MT5 for forex/stocks/commodities; Bybit Linear Futures for crypto.

**AI Review:**
- **Marcus Reid (Grok/xAI)** — AI analysis of Engine A + B signals (`EXPERT_PROMPT`), JSON output with grade/verdict/edgeProbability/style_ratings
- **Chart Vision (xAI multimodal)** — chart screenshot analysis (`/api/chart-analysis`) via OpenAI-compatible xAI client (`base_url=https://api.x.ai/v1`) and `VISION_MODEL`. Single/dual/triple TF; structured footer parsed by `_extract_vision_structured()`.
- **Lottery AI** — `POST /api/lottery/ai-analysis`: Grok (xAI) via OpenAI-compatible client; see **Lottery Lab** below.

**API Keys (secrets via env):** `EODHD_KEY`, `XAI_API_KEY` (Marcus Reid, chart vision, signal debate, Engine B AI, Lottery AI), `ANTHROPIC_API_KEY` (news sentiment helpers where configured). xAI-backed routes require the **`openai`** PyPI package (`base_url=https://api.x.ai/v1`).

---

## File Map

| File | Purpose |
|------|---------|
| `athena.py` | Flask app, all API routes, `analyze_pair`, pair lists, `EXPERT_PROMPT`, `_build_signal_message`, `_build_event_risk`, vision prompts | ~8500 lines |
| `vision_prompts.py` | Modular chart-vision prompt builders (`build_system_prompt`, `build_single_prompt`, `build_dual_prompt`, `build_triple_prompt`) with A-E right-edge candle framework |
| `vision_data.py` | Vision dataset schema/helpers (`vision_samples`, `vision_labels`, `vision_predictions`), artifact persistence, metrics aggregation |
| `vision_hybrid.py` | Advisory hybrid chart-vision v2 training/inference (fusion of image + structured features) |
| `chart_renderer.py` | Server-side chart renderer with Engine B overlays (FVG/OB/BOS/CHoCH/SR), pattern labels, configurable `bars_window` + `dpi` |
| `candles_cache.py` | TTL candle cache, `fetch_candles` routing (H1→live first where applicable; crypto H4/D1→Binance REST). MT5 sources bypass CandleBuilder and fetch MT5 directly first. |
| `candle_feeds.py` | Live prices, EODHD/Binance WS, `CandleBuilder` for EODHD/Binance live bars, `fetch_candles_live` |
| `eodhd_volume_batch.py` | Live v2 delayed batch volume poller for `ws:False` US stocks/ETFs; injects delta volume into `CandleBuilder.on_tick()` so non-WS stocks build M1/M5/M15/H1/H4 volume bars without per-pair intraday REST churn |
| `athena_runtime.py` | `set_runtime`/`rt()` bindings; `executed_signals` dedupe set |
| `execution.py` | Execution Flask routes (`register_execution_routes`) |
| `scanner.py` | `run_full_scan` and scan pipeline |
| `backtest_runner.py` | Engine A/B/D backtest loops: `backtest_pair`, `backtest_pair_naked`, `backtest_pair_scalp`, `run_full_backtest` |
| `data_feeds.py` | HTTP session, EODHD client, funding/OI helpers |
| `news_sentiment_feed.py` | EODHD news + Claude sentiment; optional scan blend; TTL cache |
| `scoring.py` | Confluence engine, vote weights, signal classification, pair profiles, `get_min_confluence_threshold` |
| `factor_scoring.py` | Engine A v2: 3-factor engine (Trend EMA coherence, Momentum Quality RSI+MACD, ADX Gate) + asset addon (carry/funding/COT). Unified 0-3.0 scale for ALL asset classes. Live Engine A for forex also routes here via `scoring.calc_confluence()`. |
| `forex_scoring.py` | Legacy forex scorer kept for audit, regression, and historical research only. **Not the live Engine A route.** |
| `market_structure.py` | Engine B: `NakedEngine`, swing analysis, BOS/CHoCH/FVG/order blocks, shared checklist pass/fail |
| `engine_b_ai.py` | Engine B advisory AI verdict (review only — not a pass/fail gate) |
| `engine_c.py` | Engine C consensus: `ENGINE_C_AB_WEIGHTS` blend, conviction tiers, SL/TP resolution, Vision modifier |
| `confidence_engine.py` | 4-component confidence scoring (indicator agreement, TF alignment, regime fit, liquidity) + `session_quality` post-multiplier (high=1.0, medium=0.9, low=0.7). Off-hours signals are demoted before Engine C's reliability gate. |
| `indicators.py` | Pure indicator functions (EMA, RSI, MACD, ATR, ADX, BB, Stochastic, Fib, OBV, Squeeze) + Engine D helpers: `calc_vwap`, `detect_absorption`, `calc_cvd`, `detect_range_contraction` |
| `regime.py` | Market regime detection: TRENDING / DEVELOPING / RANGING / DEAD_RANGING |
| `risk_engine.py` | Kill switch, drawdown, position sizing, portfolio heat |
| `config.py` | Hardcoded defaults + YAML loader + `_json_safe()` |
| `config.yaml` | All tunable thresholds — edit here, not `config.py` |
| `scalp_engine.py` | Engine D: Fabio Valentini VP+OrderFlow scalping. Functions: `run_scalp_scan`, `get_scalp_pairs`, `scalp_session_window`, `_build_volume_profile`, `_build_trade_bucket_volume_profile`, `_overlay_eodhd_volume_for_scalp`, `_classify_market_state`, `_locate_price_vs_vp`, `_check_absorption`, `_check_cvd`, `_check_trade_bucket_cvd`, `_check_aaa_sequence`, `_check_vwap_lean`, `_classify_setup`, `calculate_scalp_levels`, `ai_quality_grade`. M1/M5/M15/H1 support. MT5 OHLC/live price for non-crypto; EODHD may overlay volume only. Binance/`fetch_candles` for crypto, with Binance aggTrade buckets preferred for VP/CVD when fresh. |
| `volume_profile.py` | Fixed-range Volume Profile computation: POC, VAH, VAL, LVN levels, session splitting, plus `compute_bucketed_volume_profile()` for price-level trade buckets. Used by `scalp_engine.py`. |
| `athena/microstructure/trade_bucket_store.py` | SQLite-backed Binance aggregate-trade price-bucket store used by Engine D crypto VP/CVD. |
| `tools/preprocess_binance_aggtrades.py` | Preprocess local or downloaded Binance Futures aggTrade ZIP/CSV files into trade buckets for crypto backtests. |
| `tools/audit_eodhd_intraday_volume.py` | Read-only EODHD intraday/EOD volume coverage audit for Athena symbols/timeframes. |
| `tools/audit_eodhd_symbol_coverage.py` | Read-only EODHD exchange symbol-list audit for Athena candidate mappings. |
| `ai_schemas.py` | Pydantic schemas for AI JSON output (`EngineAResponse`, `EngineBResponse`, `StyleRating`) |
| `mt5_executor.py` | MetaTrader 5 execution |
| `bybit_executor.py` | Bybit Linear (USDT-M) Futures execution |
| `auto_trader.py` | Autonomous scheduler: scan every 30 min, auto-execute per conviction |
| `ai_learning.py` | Outcome extraction → `learning_log`; factor-level analysis for AI calibration |
| `lottery_engine.py` | Lottery analytics + ticket generation (7 modes) + simulation |
| `lottery_service.py` | DB schema, CSV import, draw history |
| `static/index.html` | Dashboard UI: signals, Pair Browser, Engine C tab, backtest, ACM charts | ~2550 lines |
| `static/js/features/pair_browser.js` | Pair Browser tab logic: single-pair browsing, Engine A/B/compare actions, and news/intermarket/chart/AI vision sections |

---

## Venues (canonical — execution vs data)

- **Crypto — execution:** **Bybit** USDT-M linear futures only (`bybit_executor.py`). All live crypto **orders** go here (including Engine D `/api/scalp-execute` when `signal.type == "crypto"`). **Binance is not a crypto execution broker in this codebase** — it is market data only.
- **Crypto - market data:** **Binance Futures** WebSocket klines (`candle_feeds.BinanceCandleWS`) -> `CandleBuilder.on_kline()` for **M5, M15, H1, H4, D1**, plus REST fallback / merge via `fetch_candles()` when `pair["source"] == "binance"`. Live crypto quote stream: `BinanceLivePriceWS` `!ticker@arr`. Engine D crypto orderflow also subscribes to Binance **`aggTrade`** streams via `athena/datafeeds/binance_ws.py` and stores price-level buckets in `athena/microstructure/trade_bucket_store.py`; VP/CVD prefer fresh buckets and fall back to candle volume when buckets are unavailable or stale.
- **Non-crypto — execution and OHLC:** **MT5** only (`mt5_executor.py`, `fetch_mt5()` for candles). Forex, commodities, indices, stocks as configured with `source: mt5`.
- **EODHD - volume support (not primary MT5 OHLC):** For MT5-sourced **US stocks**, CandleBuilder is the preferred live volume path: `ws:True` stocks accumulate from the EODHD US WebSocket, and `ws:False` stocks use `eodhd_volume_batch.py` Live v2 delayed batch polling to inject delta volume into `CandleBuilder.on_tick()` for **M1/M5/M15/H1/H4** without per-pair intraday REST churn. Stock **D1** is seeded by `bulk_update_d1()` and `_fetch_eodhd_volume_only()` now prefers CandleBuilder D1 bars before falling back to per-pair REST. **Forex / commodities / indices** remain on MT5 OHLC plus MT5 tick-volume in the live overlay path (`detail=no_real_volume`). The legacy whitelist module `eodhd_volume_overlay.py` remains on disk and the two background warmers still run: fast loop (M1/M5/M15 for scalp pairs, 60s) and slow loop (H1/H4/D1 for all non-crypto pairs, 900s — uses `scan_candle_limits()` limits to match `analyze_pair()` cache keys). Engine D backtests may still fetch EODHD intraday volume directly. Crypto volume/orderflow comes from Binance bars and aggTrade buckets, not EODHD.

---

## Live Data Feed Routing (LOCKED — do not change without explicit approval)

| Asset class | Candles | Live price |
|---|---|---|
| Forex / Stocks / Commodities / Indices | `fetch_mt5()` primary; Engine D may overlay cached EODHD volume only on M1/M5/M15/H1/H4/D1 without changing OHLC | `symbol_info_tick()` bid/ask mid |
| Crypto M5/M15/H1/H4/D1 | `BinanceCandleWS` futures `@kline_*` → `CandleBuilder.on_kline()`; REST merge/fallback `binance_futures` | `BinanceLivePriceWS` `!ticker@arr` |
| Crypto (other TFs) | Binance REST via `fetch_binance` / `tf_b` map when `source == binance` | — |
| EODHD WS | EODHD-sourced pairs only (JSE disabled pairs etc.) | — |

**Candle depth:** `D1_CANDLES: 1001`, `H4_CANDLES: 1001`, `H1_CANDLES: 1001` → ~1000 closed bars after forming bar is dropped. `fetch_mt5()` requests `limit + 100`.

**Never** route MT5-sourced pairs through CandleBuilder. **Never** replace MT5 OHLC with EODHD OHLC on MT5-sourced pairs; EODHD is volume-only overlay. Live Engine D EODHD volume lookup must remain cache-only to avoid delaying scalping; cold cache falls back to MT5 volume. **Never** write a stale bar close into `_live_prices` for any MT5 pair.

---

## AI Components

### Marcus Reid (Engine A + B analysis) — `EXPERT_PROMPT` in `athena.py`
- System prompt for Grok/xAI; JSON-only output
- **6 absolute rules:** cite specific data or don't claim it; no "will/guaranteed/definitely"; every claim must reference a factor name/score/weight/level from input
- **Analysis order:** factor diagnostics → trend coherence → regime → nondirectional quality → levels → Engine B cross-check → context color only
- **Critical cross-checks (all mandatory):** momentum divergence, direction flip bug, confidence multiplier < 0.5, trendCoherence < 0.7, nondirectionalScore < 0.5, SL > 2%
- **edgeProbability:** anchored formula `base = score_pct × 0.8` + modifiers
- Outputs: `grade`, `verdict`, `narrative`, `edgeProbability`, `riskLevel`, `style_ratings` (scalp/intraday/swing)

### `_build_signal_message` — `athena.py`
Builds the text input sent to Marcus Reid. Sections emitted:
- `=== SIGNAL ===` — pair, direction, score, conviction, regime, style
- `=== ENGINE B (NAKED MARKET STRUCTURE) ===` — reads `signal.get("engine_b") or signal.get("naked_data")` (Engine A uses `"engine_b"`, Engine B scan uses `"naked_data"`)
- `=== FACTOR DIAGNOSTICS ===` — Engine A: per-factor scores/weights (`factorScores`, `factorWeights` camelCase), directional/nondirectional scores, confidence multiplier, trendCoherence, active factors. Engine B: `=== ENGINE B SCORING DIAGNOSTICS ===` (score, bonuses, actionable flag)
- `=== CONFIDENCE ENGINE ===` — `confidenceDetail` components (Engine A only)
- `=== TECHNICALS ===`, `=== LEVELS ===`, `=== WARNINGS ===`, `=== CONTEXT ===`, `=== PORTFOLIO ===`

**Key signal dict keys (camelCase — do not use snake_case):**
- `factorScores`, `factorWeights`, `factorDiagnostics`, `confidenceDetail` (all set in `analyze_pair()`)
- `naked_data` (Engine B scan signals only — contains full naked result)

### Chart Vision — `/api/chart-analysis` in `athena.py`
- Transport: OpenAI-compatible xAI chat completions client (`https://api.x.ai/v1`).
- Model id: **`VISION_MODEL`** in `config.yaml` (current runtime typically Grok reasoning multimodal model id).
- Temperature: **`AI_VISION_TEMPERATURE`** — default **`0.2`** in `config.py` / yaml (factual observation mode).
- Three modes: single-TF (H4), dual-TF (D1+H4), triple-TF (D1+H4+H1)
- Prompt architecture is modularized in `vision_prompts.py` and used directly by `/api/chart-analysis` (single active assignment per system/single/dual/triple prompt).
- RIGHT EDGE instructions enforce A-E candle reading:
  - A Pattern ID + structure location
  - B Wick analysis (direction/ratio/meaning)
  - C Body conviction
  - D Sequence narrative as first RIGHT EDGE line
  - E Candle rules (engulfing requires volume confirmation; counter-trend + rising volume => `POTENTIAL REVERSAL`)
- **Read order (2026-03-31):** Prompts enforce **image-first** for structure and prices; **right edge** = last 5 candles on authoritative TF (single/H4, dual/H4, triple/H1); **then** algorithmic context for cross-check. If chart and context conflict on **structure/direction**, **the image wins**.
- **TRADE SNAPSHOT instrument/TF:** Prefer any **visible chart UI** (watermark, symbol strip, title bar — not only top-left). If the label is still illegible, state exactly *chart label not legible — from request* and use **only** the chart-analysis request metadata for identity: `symbol`, `tf` (single-TF), or the request symbol plus each image’s stated TF role (D1/H4 or D1/H4/H1). Do **not** substitute “algo context” wording for identity, guess alternatives, list candidate pairs, or hedge with likely / maybe / appears / possibly / or. Do not infer instrument from ENGINE A/B text. Fallback is **metadata only**; it does not override visible price action.
- **RIGHT EDGE (2026-03-31):** Model must lead with **interpretation** (momentum, control of last closes, continuation vs pullback vs reversal risk, confirm vs threaten algorithmic LONG/SHORT). Avoid long candle-by-colour play-by-play without meaning; optional one compact oldest→newest fact sentence as evidence.
- **Entry quality rules (2026-04-01):** System prompt rules 9–12 enforce entry positioning assessment (tactical vs chasing into congestion), volatility-regime interaction (low-vol breakdowns flagged), move maturity (late/exhausted entries), and independent RR verification. (Rule 8 is instrument/timeframe metadata-only when chart labels are unreadable.) All three user prompts (single/dual/triple) include an **ENTRY QUALITY** body section between **KEY RISKS** and **FINAL VERDICT**.
- **Body structure:** Concise sections (e.g. TRADE SNAPSHOT, MARKET STRUCTURE, RIGHT EDGE, factors, verdict) plus required machine footer below.
- **STRUCTURED FOOTER (required — do not remove):** Line `RIGHT EDGE: CONFIRMS | REVIEW | POTENTIAL REVERSAL` immediately before `TF ALIGNMENT` + three `*_RATING` + three `*_LEVELS` — parsed by `_extract_vision_structured()` for Engine C conviction modification.
- **Anti-hallucination rules:** ONLY describe what you can ACTUALLY SEE; never invent patterns; cross-reference algo context **after** the visual read; full annotation legend for Engine B elements.
- **Footer parsing:** Parsed by `_extract_vision_structured()` and used by `apply_vision()` to modify Engine C conviction. Removing or rewording parser tokens breaks Engine C Vision integration and UI level extraction.
- **EODHD news (same file, `fetch_news_context`):** Per-pair `/api/news` and `/api/news-word-weights` use `timeout=15` (was 8s) to reduce read timeouts on slow EODHD responses; failures stay non-fatal.
- Advisory hybrid v2 sidecar is available via:
  - `POST /api/vision-sample`
  - `POST /api/vision-label`
  - `POST /api/vision-infer-v2`
  - `GET /api/vision-metrics`
  - `POST /api/vision-train-v2`

---

## Signal Flow

```
run_full_scan(style, asset_class)
  └─ for each active pair (ThreadPoolExecutor, SCAN_MAX_WORKERS workers)
       └─ analyze_pair(pair, btc_bias, style)
            ├─ fetch_candles(pair, "D1"/"H4"/"H1", limit)
            ├─ calc_indicators(candles)  → {snap: {...}}
            ├─ calc_confluence(d1i, h4i, h1i, vr, stoch, pair, ...)
            │    ├─ get_pair_vote_weights(pair)
            │    ├─ votes tallied → bull/bear score
            │    ├─ ranging/counter-trend penalties applied
            │    ├─ classify_signal_setup(...)
            │    └─ returns {score, direction, votes, warnings, signalClass, regime, ...}
            ├─ factor_scoring → {factorScores, factorWeights, factorDiagnostics, confidenceDetail}
            ├─ calc_levels(entry, atr, direction, ptype, style)
            └─ returns full signal dict (camelCase keys)
  └─ _classify_signal → tier: "trade" | "watchlist" | "skip"
  └─ apply_correlation_cap(results)
  └─ _json_safe(result)
```

**Engine B flow:**
```
/api/scan-naked | /api/naked-analysis | /api/compare-engines
  └─ NakedEngine.analyze_structure(d1, h4, h1, price, direction, atr, regime)
  └─ NakedEngine.calculate_confidence(res, price, direction, atr, entry_candles, style_profile)
       └─ checklist: structure, location, trigger/entry, room, RR, macro
  └─ Engine B AI review (advisory only — not a pass/fail gate)
  └─ result stored as "naked_data" on Engine B scan signals
```

**Engine C flow:**
```
POST /api/engine-c-scan {assetClass, style}
  └─ Fetches candles ONCE per pair, shares between Engine A and Engine B via confirmed/forming market-state splits
  └─ analyze_pair(preloaded_candles={D1,H4,H1})   ← Engine A (uses same data as Engine B)
  └─ NakedEngine.analyze_structure() + calculate_confidence()   ← Engine B
  └─ compute_consensus() → ALIGNED / A_ONLY / B_ONLY / CONFLICT / SKIPPED
  └─ ALIGNED results insert into shadow ledger (SHADOW_LEDGER_ENABLED must be true)

Other Engine C paths:
  - /api/compare-engines — manual per-pair A+B+C comparison on demand
  - /api/backtest-consensus — Engine C backtest for a specific pair

In each path the consensus logic is:
  └─ compute_consensus(signal_a, signal_b, confidence_b, regime, entry, atr)
       ├─ normalise_engine_a() → 0–1  (max_score 3.0 for all asset classes)
       ├─ normalise_engine_b() → 0–1  (max_possible from calculate_confidence, default 5.0)
       ├─ ENGINE_C_AB_WEIGHTS regime blend from `engine_c.py`
       │    Current values: TRENDING={A:0.40,B:0.60}, RANGING={A:0.35,B:0.65}
       ├─ resolve_sl() — structural → ATR-clamped → tighter
       ├─ resolve_tp() — structural if RR≥1.5, else ATR
       └─ returns {conviction, tier, sizing_override, sl, tp, rr, ...}

Note: analyze_pair() logs [ANALYZE] warnings when returning None due to empty/insufficient candles
```

/api/engine-c-confirm (Vision overlay):
  └─ apply_vision(consensus, vision_result)
       ├─ CONFIRM + conviction≥0.35 → trade=True
       └─ AVOID/CONTRADICT → trade=False, tier=SKIP
```

**Pair Browser workflow:**
```
Dashboard Pair Browser tab (panel-pair-browser)
  └─ loadPairBrowserPairs() → GET /api/pairs
  └─ selectPairBrowserPair(symbol)
  └─ Engine A button → POST /api/pair-scan
       └─ Runs analyze_pair() for one selected symbol without requiring a full-universe scan first
  └─ Engine B button → POST /api/naked-analysis
       └─ Uses Engine A direction when Pair Browser direction mode is AUTO; otherwise uses operator-selected LONG/SHORT
  └─ Compare button → POST /api/compare-engines
  └─ News section → POST /api/news-sentiment
  └─ Intermarket section → GET /api/intermarket-matrix
       └─ Read-only context when the selected Engine A signal has no attached intermarketConfirmation payload
  └─ AI Vision button → POST /api/chart-analysis (server_render=true)
  └─ Full Chart button → openAcm(symbol, tf, fallbackSig)
  └─ TV Chart button → openTVChart(display, "240")
```

`POST /api/pair-scan` is the dedicated single-pair Engine A browse route for the Pair Browser tab. Keep it single-pair only; do not repurpose it for full scan orchestration or scoring changes.

**Scalp Lab / Engine D flow:**
```
Dashboard Scalp Lab (panel-scalp) → runScalpScan()
  POST /api/scalp-scan
    └─ get_scalp_pairs()  ← CONFIG SCALP_ENGINE.SCALP_PAIRS or active pairs (MT5 + Binance crypto)
    └─ scalp_engine.run_scalp_scan(pairs)
         ├─ Session filter: scalp_session_window() - supports NY open cooldown (NY_OPEN_SKIP_MINUTES)
         ├─ Spread filter: check_spread() per MAX_SPREAD_PIPS - skipped for crypto
         ├─ MT5 candles: mt5_fetch_scalp_candles(M15, M5, M1, bias_tf)
         │  Crypto candles: _scalp_fetch_candles via fetch_candles/Binance
         ├─ Execution TF: M1 by default (EXECUTION_TIMEFRAME), M5 fallback
         ├─ Pillar 1 - Volume Profile: _build_volume_profile(M15) → POC/VAH/VAL/LVN
         │    _classify_market_state() → "balance" | "imbalance"
         │    _locate_price_vs_vp() → location vs levels
         ├─ Pillar 2 - Aggression (execution TF candles):
         │    _check_absorption() → detected, count, bars (uses indicators.detect_absorption)
         │    _check_cvd() → direction, cvd_slope (uses indicators.calc_cvd)
         │    _check_aaa_sequence() → complete (Absorption→Accumulation→Aggression)
         ├─ Pillar 3 - VWAP: _check_vwap_lean(M15) → lean direction (uses indicators.calc_vwap)
         ├─ HTF bias: infer_bias_from_ema_stack(bias_tf candles) — EMA 21/50/200 stack
         ├─ _classify_setup() → direction, setup_type, reasons, valid
         ├─ HTF bias gate: skip if direction != htf_bias (when WITH_TREND_ONLY)
         ├─ calculate_scalp_levels() → SL (VP-based), TP1 (MIN_RR), TP2, sl_method, rr
         └─ ai_quality_grade() → score 0–100, grade A/B/C/D, size_multiplier, reasons
              Grade gate: skip grade D (MIN_GRADE=C) or C+D (MIN_GRADE=B)

Signal dict keys: pair, display, mt5_symbol, direction, price (entry), sl, tp1, tp2, rr1,
  zone_type (setup_type), zone_conditions, trigger_type, momentum_method,
  vp_poc, vp_vah, vp_val, vp_lvn_count, market_state,
  absorption_count, cvd_direction, cvd_slope, aaa_complete,
  vwap, htf_bias, htf_bias_tf, spread_pips, session,
  execution_tf, context_tf, structure_tf,
  ai_score, ai_grade, ai_reasons, size_multiplier,
  confluenceScore (=ai_score/100 for risk sizing), engine="SCALP"

POST /api/scalp-execute  (pair + signal)
  └─ risk_check() → mt5_execute() OR bybit_execute() (crypto when signal.type=="crypto")

POST /api/backtest-scalp  (pair)  ← Engine D backtest
  - backtest_pair_scalp() in backtest_runner.py
       |- Fetches M15 bars (BT_WALK_BARS), walks forward in chunks
       |- Session filter: scalp_session_window(backtest=True) - respects BT_SESSION_MODE/BT_NY_OPEN_SKIP_MINUTES
       |- Builds VP per window, runs full pipeline per bar
       |- Tracks best_favorable_r for early scratch decisions
       |- Resolves SL/TP1 hits intrabar (_resolve_barrier_exit)
       |- Scratch exit: if BT_SCRATCH_ENABLED and no early follow-through after BT_SCRATCH_BARS
       |- Saves to backtest_results (engine="scalp_vp", bt_min=min_rr)
       - Returns standard result dict + scalp_analysis breakdown
            (trigger/setup/grade counts with WR and avg_r per bucket)
  UI: renderScalpBtSingle() - grade-coloured stats + VP analysis block + trade table
```

UI card fields used: `display||pair`, `market_state`, `zone_type`, `ai_grade`, `ai_score`, `size_multiplier`, `vp_vah`, `vp_poc`, `vp_val`, `aaa_complete`, `absorption_count`, `cvd_direction`, `htf_bias`, `htf_bias_tf`, `session`, `price`, `sl`, `tp1`, `rr1`, `spread_pips`, `sl_method`, `ai_reasons`.

**Execution path:**
```
api_execute()
  ├─ signal freshness check (SIGNAL_MAX_AGE_SEC)
  ├─ live re-analyze if stale → HTTP 409 if direction flipped
  ├─ price drift >1% → rebase SL/TP
  ├─ _validate_exit_levels()
  ├─ risk_check() → RiskApproval (kill switch, heat, sizing)
  └─ mt5_execute() OR bybit_execute()
```

**Risk engine scope:** Peak equity, drawdown, and daily realized P&L are tracked **per** `signal["type"]` (asset class: `crypto`, `forex`, `stock`, etc.) so Bybit vs MT5 equity is not mixed. `risk_check` enforces `MAX_SL_PCT` per class from the config dict (`config.yaml` / `config.py`).

---

## Current Engine A Contract (2026-04-22)

- Live route: `athena.py analyze_pair() -> scoring.calc_confluence() -> factor_scoring.compute_factor_scores()` for **all** asset classes, including forex.
- Score scale: Engine A uses a unified **0-3.0** scale. `athena.py._max_score_for_pair()` returns `3.0` for every asset class.
- Live threshold priority in the current config: `PAIR_PROFILES[pair].min_confluence -> MIN_CONFLUENCE_CLASS[type] -> MIN_CONFLUENCE`. Current `config.yaml` does not define `MIN_CONFLUENCE_GROUP`.
- Current live forex floor: `MIN_CONFLUENCE_CLASS.forex = 2.1`; `AUTO_TRADE_MIN_SCORE.forex = 2.1`.
- Backtest threshold chain when `BACKTEST_USE_BT_MIN_THRESHOLDS` or `RESEARCH_MODE` is true: `PAIR_PROFILES[pair].bt_min -> BT_MIN[type]`. Current `config.yaml` does not define `BT_MIN_GROUP`.
- Score groups (`forex_majors`, `forex_crosses`, etc.) still exist as metadata/routing helpers, but they do **not** change live `factor_scoring.py` output by themselves.
- Research guardrail: keep live Engine A production alpha small and orthogonal. The current production core is trend + momentum quality + ADX gate + one asset addon. Do not reintroduce Hurst, FVG/Fib/SMC bonuses, or pair-specific weight tuning into live Engine A without fresh out-of-sample evidence.

### Research basis for the live Engine A guardrails

- Trend/momentum persistence: Moskowitz, Ooi, Pedersen (2012) and Hurst, Ooi, Pedersen (2017)
- FX-specific momentum/carry evidence: Menkhoff et al. (2012)
- FX microstructure relevance: Evans and Lyons (1999/2005)
- Model-stability warning: Rossi (2013)
- Overfitting warning: Bailey et al. (2014)

## Key Functions

### `calc_confluence(d1, h4, h1, vr, stoch, pair, ...)` — `scoring.py`
Calls `compute_factor_scores()` from `factor_scoring.py` (Engine A v2). Returns legacy-compatible dict with `{score, votes, direction, signalClass, regime, factor_scores, factor_weights, factorDiagnostics, confidenceDetail, ...}`. Factor scores: `trend` (±3.0), `momentum` (0-1), `addon` (-0.15/0/+0.30). Final score 0-3.0.

### `get_min_confluence_threshold(pair)` — `scoring.py`
Priority in the current config: `PAIR_PROFILES[pair].min_confluence` → `MIN_CONFLUENCE_CLASS[type]` → `MIN_CONFLUENCE`. Backward-compatible support for `MIN_CONFLUENCE_GROUP[type][score_group]` still exists in `scoring.py`, but it is inactive unless the key is explicitly restored in config.

### `_max_score_for_pair(pair)` — `athena.py`
Returns 3.0 for all asset classes (Engine A v2 unified scale). No per-class fork.

### `fetch_candles(pair, tf, limit)` — `candles_cache.py`
- H1 (eligible live feeds only): `CandleBuilder` first if bars ≥ min, else TTL then REST
- Crypto H4/D1: Binance REST native intervals
- MT5 sources: `fetch_mt5()` for all TFs
- Cache TTL keys: **uppercase** `"H1"/"H4"/"D1"` — lowercase misses cache
- TTL: H1=55 min, H4=3h55m, D1=23h

### `NakedEngine.calculate_confidence(...)` — `market_structure.py`
Shared Engine B checklist for live scan, analysis, compare, backtest. Score = checklist count / UI measure. `passed` = naked price-action rules only — not AI-driven. **Note:** `ENGINE_B_REGIME_MULTIPLIERS` (TRENDING=0.90, RANGING=0.90, HIGH_VOL=0.85, LOW_VOL=1.15) — easier in chop/vol, harder in flat. `engine_b_confidence_passes` uses `passed` boolean only (no score threshold double-jeopardy). **`ENGINE_B_PROFILE_SCORING_ENABLED: true`** — volume profile (POC/VAH/VAL) bonus scoring is active. Crypto uses Binance kline bar volume (`vol = k[5]`); non-crypto uses EODHD real volume overlaid on MT5 candles. `compute_fixed_range_volume_profile()` has a range-proxy fallback for any pair with zero-volume bars.

**Engine B Fixes Applied (2026-04-30):**
- FIX 1: D1 penalty applies to `total_score` only; `gate_score` stays integer.
- FIX 2: Regime multipliers fixed — easier in RANGING/HIGH_VOL, harder in LOW_VOL.
- FIX 3: Crypto trigger profile no longer bricks all crypto when disabled.
- FIX 4: `max_possible` is dynamic (`gate_count + bonus_count`).
- FIX 5: `engine_b_confidence_passes` uses `passed` boolean only (no score threshold).
- FIX 6: ADX removed from forex `structure_ok`; drives regime classification instead.
- FIX 7: Contextual `min_room_atr` — crypto=0.15, scalp=0.20, RR≥2=0.20, BOS=0.25.
- FIX 8: Internal diagnostics (`structural_verdict_clear`, `target_v2`, `path`) are warnings, not gates.
- FIX 9: Absorption entry fallback added to `entry_ok`.
- FIX 10: Per-gate failure histogram for monitoring.

### `_naked_scan_style_profile(style, score_group)` — `athena.py`
Resolves effective Engine B thresholds. Logic: Hardcoded defaults → `style_profiles` (global UI) → `score_group_overrides` (per-pair BT MIN). Per-group overrides take priority and are displayed in the **"Per-Group BT MIN Overrides"** table in the dashboard's Engine B panel.

### `_engine_b_regime_label(h4_candles, pair_type, regime_hint)` — `athena.py`
Shared regime resolver for all Engine B paths. Returns: `TRENDING`, `RANGING`, `HIGH_VOLATILITY`, `LOW_VOLATILITY`.

### `_json_safe(value)` — `config.py`
Recursively replaces `NaN`/`Inf` with `None`, normalizes numpy types. Applied before every `jsonify()`.

### `_can_execute(signal, cfg)` — `auto_trader.py`
Live execute gate is **`combinedConviction`** vs `AUTO_TRADE_MIN_CONVICTION` (with alignment discount and meta delta). `AUTO_TRADE_MIN_SCORE` is **informational** in `get_status()` only. All asset classes now use 0–3.0 scale — `AUTO_TRADE_MIN_SCORE.forex` should be aligned to `MIN_CONFLUENCE_CLASS.forex` on the 0–3.0 scale.

### `/api/chart-analysis` — `athena.py`
Requires `XAI_API_KEY` env var and uses OpenAI-compatible xAI client (`base_url=https://api.x.ai/v1`). `regime` in signal can be dict `{"label":"TRENDING"}` (Engine A) or string `"TRENDING"` (Engine C) — both handled. Context builder is **outside** try/except — keep it simple.

**Engine C Vision payload fix (2026-04-30):** When Engine C consensus calls chart-analysis, `conviction` (0–1 scale) is now sent as `confluenceScore` scaled to 0–100 with `maxScore: 100`, plus the raw `conviction` field preserved. Previously the backend prompt showed `score=0.72/3.0` (misleadingly weak) instead of `score=72/100` (strong consensus). Fixed in `static/index.html` lines 11344–11468 and `_acmBuildChartAiSignalPayload()`.

### `/api/scalp-scan` / `/api/scalp-execute` — `athena.py`
See **Scalp Lab / Engine D flow** above. Execute path: **`signal.type == "crypto"` → Bybit**; **else → MT5**. Crypto candles for Engine D come from Binance futures (`fetch_candles` / `CandleBuilder`); orders are **never** sent to Binance.

---

## Scoring Architecture Summary

| Engine | Scorer | Scale | Gate key |
|--------|--------|-------|----------|
| Engine A — all assets | `factor_scoring.py` v2: 3-factor (Trend+Momentum+ADX) + addon | 0–3.0 | `MIN_CONFLUENCE_CLASS[type]` |
| Engine B | `market_structure.py` naked checklist | 0–100 pct | `NAKED_ENGINE.style_profiles.min_score` |
| Engine C | `engine_c.py` A+B blend | 0–1 conviction | `ENGINE_C_AB_WEIGHTS` |
| Engine D (Scalp Lab) | `scalp_engine.py` VP+OrderFlow: VP build → absorption/CVD/AAA → VWAP → `_classify_setup` → `ai_quality_grade` | 0–100 (`ai_score`), letter `ai_grade` A/B/C/D, `size_multiplier` (1.0/0.5/0.25) | `SCALP_ENGINE` (`MIN_RR`, `MAX_SPREAD_PIPS`, `MIN_GRADE`, `WITH_TREND_ONLY`, `BIAS_TIMEFRAME`) |

**Regime in Engine A v2:** Regime is detected and smoothed (`REGIME_SMOOTHING_BARS`) but does **not** modify factor weights — it is informational/diagnostic only. Only `ENGINE_C_AB_WEIGHTS` in `engine_c.py` uses regime (to control A vs B blend ratio in Engine C).

**`confluencePct` display scaling:** anchored to `get_min_confluence_threshold(pair)` so ~67% = "passing" intent. Engine C uses raw `confluenceScore / maxScore` (not `confluencePct`) for normalization. Engine A v2 max is 3.0 for all asset classes; `FOREX_ENGINE_A_MAX_SCORE` in `intermarket.py` updated to 3.0.

**`getConfluencePct()` engine detection (2026-04-30):** Added early returns for Engine C (`data.conviction` → ×100) and Engine D (`data.ai_score` + `ai_grade` → direct) so signals render correctly if they ever enter shared grids. Engine C/D signals currently live in separate tabs, but this is defensive.

---

## Engine A v2 / Forex / Engine B reality (current active guidance)

### Engine A v2 forex behavior

- Forex now routes through `factor_scoring.py` (Engine A v2) — **`forex_scoring.py` is no longer active**.
- Engine A v2 uses multi-TF EMA coherence for direction, RSI+MACD momentum quality, ADX gate, and carry z-score addon for forex.
- Session soft multiplier (0.75–1.0×) replaces the old hard session block.
- London breakout path (`_london_breakout_score`) and Hurst gate are **removed** — Engine B handles structural breakout validation instead.

Use this mental model when reasoning about forex:
- **Engine A = trend quality detector** (3-factor: EMA coherence + RSI/MACD confirmation + ADX gate + asset addon)
- **Engine B = structural validator** (naked price action: BOS/CHoCH/FVG/OB/swing sequence)
- **Engine C = alignment / consensus layer**
- **news / event risk = final safety layer**

Do not describe the current forex engine as a dedicated London-breakout-only system.

### London breakout operator guidance (not a single code-enforced switch)

Treat this as the intended review / execution flow for breakout trades:
1. Build the Asian range (**00:00-07:00 UTC**).
2. Let **Engine A** detect breakout quality around London open.
3. Use **Engine B** to validate whether the breakout is structurally tradable.
4. Let **Engine C** confirm A/B alignment.
5. Apply event-risk / sentiment safety filters last.

Interpret timing like this:
- too early = first wick poke through the range
- best = first confirmed H1 close outside the Asian range, ideally during **07:00-09:00 UTC**
- too late = extended chase after multiple London impulse candles

If the move is already stretched, treat it as continuation / pullback logic, not a fresh London breakout.

### Engine B role in London breakout

Engine B is **not** the London-session clock and should not be documented as the primary breakout detector.

Use Engine B as a structure / execution-quality filter:
- did price break structure or only wick the range?
- is the breakout or continuation candle strong enough?
- is there room to target?
- is RR still valid after SL placement?
- is price already too extended from the break area?

Correct role split:
- **Engine A finds the breakout event**
- **Engine B checks whether the breakout is structurally tradable**
- **Engine C checks whether A and B align**

### News toggle and news architecture

The UI news toggle only controls `NEWS_SENTIMENT_CONFLUENCE_ENABLED`.

It does **not** automatically disable:
- `SENTIMENT_GATE_ENABLED`
- `EVENT_RISK_ENABLED`

So current behavior is:
- news toggle ON = news sentiment can nudge scan / confluence score
- news toggle OFF = no news score blend
- sentiment / event safety blockers can still remain active for auto-trading

Current repo reality has **3 separate news / event layers**:
1. background news context for UI / AI / narrative use
2. execution safety gates (`SENTIMENT_GATE_ENABLED`, `EVENT_RISK_ENABLED`)
3. optional scan / confluence blend via `NEWS_SENTIMENT_CONFLUENCE_ENABLED`

Do not describe the current repo as having one unified news engine.

### News blend scoring contract

When news sentiment confluence is enabled, score blending must respect the Engine A v2 score contract:
- **All asset classes (including forex) = 0–3.0** — `maxScoreOverride` is 3.0 for every path.
- `NEWS_SENTIMENT_SCORE_IMPACT` delta scales from 3.0 max. Do not use the old 2.0 forex cap.

### Historical forex threshold snapshot (superseded by Current Engine A Contract above)

Historical 2026-04 migration notes used a lower forex floor during earlier scale-conversion work.
Do not use those legacy values for live decisions.
Use the **Current Engine A Contract (2026-04-22)** section above for the verified live forex floor and score-scale rules.

---
## Pair Profiles (config.yaml) - current example shapes

Current live Engine A guidance:
- `min_confluence` and `bt_min` are the pair-profile fields that materially affect score gating on the active factor route.
- disable_filters remains live for filter gates.
- Do not assume `weight_overrides` change `factor_scoring.py` unless you trace a live call path that proves it.

```yaml
PAIR_PROFILES:
  XAU/USD:
    disable_filters: [obv, session]
    min_confluence: 1.05
    bt_min: 1.80
  USD/CHF:
    disable_filters: [obv]
```

Valid vote keys: `d1_trend, h1_ema, d1_adx, h4_macd, h4_oscillator, volume, funding, session, h4_fib, h1_bb, weinstein, divergence`
Valid filter keys: `weinstein, session, regime_transition, obv, funding, squeeze, mean_revert, btc_bias, divergence_warning`
`PAIR_PROFILE_VOTES` and `PAIR_PROFILE_FILTERS` constants live in `config.py` only.

---

## Auto-Trader (`auto_trader.py`)

- Daemon thread wakes every 30s, scans every `AUTO_TRADE_SCAN_INTERVAL_MIN` minutes
- Execute gate: **`combinedConviction`** ≥ `AUTO_TRADE_MIN_CONVICTION` (adjusted for alignment / meta). `AUTO_TRADE_MIN_SCORE` is status-only; current forex informational scale is **0-3.0**, aligned to `MIN_CONFLUENCE_CLASS.forex`.
- Diagnosing: `SELECT pair, score, direction, error_tag, grade FROM audit_log WHERE grade LIKE 'AUTO%' ORDER BY ts DESC LIMIT 20`
- `grade='AUTO-DEMO'` = success. `grade='AUTO-ERR-DEMO'` = blocked (see `error_tag`)

---

## Bybit Executor (`bybit_executor.py`)

Bybit Linear (USDT-M) Futures, 1x leverage, ISOLATED margin. LONG = market buy, SHORT = market sell.
- SL = `stop_market`, TP = `take_profit_market`
- `adjustForTimeDifference: True`, `recvWindow: 10000` (fixes retCode 10002)
- Post-fill `_validate_exit_levels()` → emergency close if invalid (no retry)
- `_set_trading_stop()` — 2 attempts, 2s sleep, then emergency close
- Env: `BYBIT_API_KEY`, `BYBIT_API_SECRET`, `BYBIT_TESTNET=true`

---

## Audit DB Schema (`audit.db`)

### `audit_log`
Key columns: `ts, pair, score, direction, grade, edge_prob, risk, style, asset_class, score_pct, max_score, votes_json, warnings_json, regime, entry_price, sl, tp, volume, risk_amount, ticket, exit_price, pnl, r_multiple, exit_reason, holding_period_hours, error_tag, fee_cost, factors_json`

- `factors_json` — `{scores, weights, disabled, regime}` from `compute_factor_scores()`. Used by `ai_learning.py`.
- `fee_cost` — Bybit commission. NULL for MT5.
- `exit_reason` — outcome classification written by `_update_trade_outcome()` in `athena.py`. Current live values include `SL_HIT`, `TP_HIT`, `MANUAL_CLOSE`, and `TIMED_CLOSE`.
- Timed closes are pre-marked by `timed_exit_monitor.py` and preserved by `_update_trade_outcome()` so completed timed exits remain attributable in `audit_log` and `/api/performance`.
- `/api/open-trades-timed` and `timed_exit_monitor.py` match live positions to audit rows by exact ticket first, then by `pair + direction + entry_price` fallback. This covers split MT5 legs and Bybit positions where the live position identifier does not match the audit ticket 1:1.

### `backtest_results`
Columns: `id, run_date, pair, asset_type, engine, trades, win_rate, profit_factor, expectancy, sqn, sharpe, sortino, is_score, oos_score, max_dd_pct, bt_min, atr_source, notes`
- `engine`: `"forex_scoring"` | `"factor_scoring"` | `"naked_engine"` | `"scalp_vp"`
- `bt_min`: the effective minimum threshold used for that run (Engine D stores `min_rr`)
- Endpoints: `GET /api/backtest-history`, `/api/backtest-history/<pair>`, `/api/backtest-best`

Schema auto-migrated on startup. To add a column: add to both `CREATE TABLE` and migration list in `_init_audit_db()`.

---

## Debugging Playbook

**Scoring/confluence complaints require tracing ALL layers:**
1. Data feed → candle cache → `fetch_candles` → `analyze_pair` → response JSON fields → UI formula → `/api/candles` chart
2. Use `confluenceScore`, `maxScore`, `get_min_confluence_threshold(pair)` together — never `confluencePct` alone
3. EMA mismatch: verify same `limit`, same forming-bar drop, same venue (crypto: futures WS for H1)
4. Engine A vs Engine B disagreement is **by design** — different inputs, gates, scales

**Common traps:**
- `confluencePct` optical illusion: display is threshold-anchored, NOT `score/maxScore` — `20fd03a`
- `_json_safe()` must be called before every `jsonify()` (NaN/Inf crash)
- `cache TTL keys must be uppercase "H1"/"H4"/"D1"`
- Engine C `regime` can be string or dict — handle both
- `confidenceDetail` lives on the signal dict (`analyze_pair` sets it); `naked_data` holds the Engine B naked result for Engine B scan signals
- `[NAKED-DBG]` logs in `athena.py` show specific checklist failures (e.g., `fails=[struct,loc,trigger,rr=0.8]`) for immediate diagnostic visibility.
- `[ANALYZE]` warnings in `athena.py` log when `analyze_pair()` returns None due to empty/insufficient candles — check console for D1/H4/H1 bar counts.

---

## Candle Windows

- **Engine A scan:** `D1=1001`, `H4=1001`, `H1=1001` → ~1000 closed bars after forming-bar drop
- **Dashboard `/api/candles`:** `limit=1000` — EMA 21/50/200 right-edge values match scan when limits align
- **Minimums after drop:** `len(d1) >= 220`, `len(h4) >= 50`, `len(h1) >= 50` or pair is skipped

---

## Backtest

### Engine A (`backtest_pair`)
- Swing: D1 bars, max hold 20 bars → TIMEOUT (force-closed at last bar close, signed R, labelled TIMEOUT)
- Intraday: H4 bars, max hold 12 bars
- `MAX_OPEN=3` concurrent positions

### Engine B (`backtest_pair_naked`)
- Hold: `scalp=12`, `intraday=24`, `swing=60` H4 bars
- Enforces same `min_score`, `passed`, `min_rr` as live scan
- 2-bar H4 cooldown after exit bar
- Uses `_engine_b_regime_label()` for zone multipliers

### Engine D (`backtest_pair_scalp`) — `POST /api/backtest-scalp`
- Walks forward over M15 bars (`BT_WALK_BARS` from config, default 2000)
- Session filter: `scalp_session_window(backtest=True)` respects `BT_SESSION_MODE`/`BT_NY_OPEN_SKIP_MINUTES`
- Per bar: builds VP over a rolling lookback window, runs full VP→Absorption/CVD/AAA→VWAP→classify pipeline
- Grade gate applied (skips D, or C+D if MIN_GRADE=B); HTF bias gate applied
- Tracks `best_favorable_r` for early scratch decisions
- Exit: `_resolve_barrier_exit()` intrabar SL/TP1 resolution; scratch exit if `BT_SCRATCH_ENABLED` and no follow-through after `BT_SCRATCH_BARS`
- Slippage: `BT_SLIPPAGE_TICKS` applied to entry
- DB: saved as `engine="scalp_vp"`, `bt_min=min_rr`
- Response includes `scalp_analysis` dict: per-trigger (absorption/cvd_shift/rejection), per-setup (mean_reversion/trend), per-grade (grade_A/B/C) breakdowns with `count`, `wr`, `avg_r`
- UI: **⚡ Engine D (Scalp VP)** button in backtest panel → `renderScalpBtSingle()` renderer

---

## Advisory thresholds (backtest + live suggestions)

- **Module:** [advisory_thresholds.py](advisory_thresholds.py) — builds suggestions from latest `backtest_results` per (pair, engine) and closed `audit_log` trades.
- **No auto-apply:** `BT_MIN`, `MIN_CONFLUENCE_CLASS`, and `NAKED_ENGINE.style_profiles.min_score` change **only** after **POST** `/api/advisory-thresholds/<rec_id>/approve` (human action). GET `/api/advisory-thresholds` is read-only for gates.
- **Audit:** Approvals/rejections stored in `advisory_threshold_actions` in `audit.db`.

## Lottery Lab

**Games:** `lotto` (6/58+bonus), `powerball` (5/50+bonus), `daily_lotto` (5/36)
**Generator modes:** `pure_random · hot_bias · cold_bias · overdue_bias · balanced_mix · pair_bias · anti_crowd`

**Lottery AI:** `POST /api/lottery/ai-analysis` uses **Grok (xAI)** only — not Anthropic. Set **`XAI_API_KEY`** in env; **`openai`** package with **`base_url=https://api.x.ai/v1`**; model from **`LOTTERY_AI_MODEL`** (optional) or **`XAI_MODEL`** in `config.yaml`. Response JSON includes **`model`** echo.

**Critical invariants:**
- `simulate_generator()` uses incremental counters — never revert to full-history rescan per draw (quadratic regression)
- `generate_tickets()` requires `rules`, `universe`, `main_numbers` initialised before `if draw_context is not None`
- Per-ticket retry cap: `max(25, min(400, len(main_numbers) * 4))` — never remove
- New games must be added to both `LOTTERY_GAME_RULES` (`lottery_engine.py`) AND `LOTTERY_GAME_SPECS` (`lottery_service.py`)
- `LOTTERY_GAME_RULES["lotto"]` uses `main_max=58, bonus_max=58` (SA Lotto 1–58 pool since 21 Sep 2025) — do not revert to 52 or 55

---

## EODHD WebSocket Allocation

- Plan cap: **50 tickers total** across all endpoints
- Current WS: ~17 US stocks/ETFs + ~28 forex/commodities/indices = ~45
- `"ws": False` on a pair = opts out of WS only; scan/backtest/execute unaffected
- To reallocate WS slots: run backtests → query `backtest_results` by SQN → update `"ws"` flags

---

## Python Environment

- Virtualenv: `.\.venv\Scripts\python.exe`
- Tests: `.\.venv\Scripts\python.exe -m pytest tests/ test_indicators.py -v`
- Platform: Windows 11 (dev); Linux also supported

---

## DECISIONS

## 2026-04-22: Engine A v2 contract audit - current verified contract

This section **supersedes older threshold notes below** when they conflict with the live code.

**Verified current contract:**
- Engine A live route: `athena.py analyze_pair() -> scoring.calc_confluence() -> factor_scoring.compute_factor_scores()`
- Engine A live scale: `0-3.0` for all asset classes
- Current live threshold priority: `PAIR_PROFILES.min_confluence -> MIN_CONFLUENCE_CLASS[type] -> MIN_CONFLUENCE`
- Current live forex floor: `MIN_CONFLUENCE_CLASS.forex = 2.1`; `AUTO_TRADE_MIN_SCORE.forex = 2.1`
- Current `config.yaml` intentionally omits `MIN_CONFLUENCE_GROUP` and `BT_MIN_GROUP`; helper support remains only for backward-compatible configs that explicitly restore them

**Audit-driven cleanup completed this session:**
- `divergence_monitor.py` now replays the shared live factor path for forex instead of treating `compute_forex_score()` as the live route
- `forex_scoring.py` now labels itself legacy/reference-only at the module level
- `tests/test_scoring_group_routing.py` now asserts the current class-level forex threshold behavior and shared divergence replay path
- `tests/test_factor_group_overrides.py` now guards the small-core contract: `PAIR_PROFILES.weight_overrides` and `score_group` metadata do not change live `factor_scoring.py` output by themselves
- Targeted regression suite after the audit: `31 passed`

**Research guardrail carried into the repo contract:**
- Keep live Engine A production alpha small: trend + momentum quality + ADX gate + one asset addon
- Do not reintroduce Hurst, FVG/Fib/SMC bonuses, subgroup multipliers, or pair-specific weight tuning into the live factor route without fresh out-of-sample evidence

## 2026-04-22: Engine A v2 threshold audit — stale hardcoded values purged, factor_scoring tunables wired

Historical note only. If any value here conflicts with the **Current Engine A Contract (2026-04-22)** section above, the current-contract section is the source of truth.

**Audit scope:** Searched all scoring, backtest, advisory, and test files for hardcoded thresholds that conflicted with `config.yaml` or referenced the deprecated 0–2.0 forex scale.

**Production code fixes:**
- `factor_scoring.py` — 10 hardcoded module constants now read from `CONFIG` with safe fallbacks. New config.yaml keys: `FACTOR_ADX_HARD_FAIL`, `FACTOR_ADX_SOFT_MULT`, `FACTOR_SESSION_CORE_MULT`, `FACTOR_SESSION_SHOULDER_MULT`, `FACTOR_SESSION_OFF_MULT`, `FACTOR_MOMENTUM_WEIGHT`, `FACTOR_ADDON_WEIGHT`, `FACTOR_BASE_WEIGHT`, `FACTOR_CONVICTION_FLOOR`. All preserve existing default values — **no scoring behavior change unless config.yaml is edited**.
- `advisory_thresholds.py` — `_ENGINE_A_BY_ASSET["forex"]` changed from `"forex_scoring"` → `"factor_scoring"` (Engine A v2 unified). `_BT_LIMITS` and `_LIVE_LIMITS` upper bounds raised from 2.0 → 3.0 scale. Advisory dashboard was silently clamping proposed thresholds to old ceiling.
- `config.py` — Fallback defaults aligned with the then-current `config.yaml`: `BT_MIN`, `BT_MIN_GROUP`, `MIN_CONFLUENCE_CLASS` (forex 1.0→1.20), `MIN_CONFLUENCE_GROUP` (forex_majors 0.85→1.05, forex_exotics 1.05→1.30). Historical note only; current live config no longer defines group thresholds.

**Test fixes (forex max_score 2.0 → 3.0):**
- `test_crit_fixes.py` — CRIT-002 assertions updated to 3.0
- `test_market_specific_contracts.py` — `_max_score_for_pair` forex contract updated to 3.0
- `test_engine_c_meta.py` — `normalise_engine_a` forex test signals updated to maxScore 3.0
- `test_news_sentiment_feed.py` — news blend test updated to maxScoreOverride 3.0 + corrected delta
- `test_scoring_group_routing.py` — forex_exotics threshold assertion updated 1.05 → 1.30

**Historical `CLAUDE.md` fixes in that session:**
- The then-current forex threshold note was updated from `MIN_CONFLUENCE_CLASS.forex` 1.0 to **1.20**, and `AUTO_TRADE_MIN_SCORE.forex` 1.0 to **1.20**
- Historical "2026-04-17" section: forex floor updated from "scale 0–2.0" → "scale **0–3.0**"
- Historical "2026-04-07" section: the then-current contract note was updated from 0–2.0 / 1.0 → 0–3.0 / 1.20

**Verified correct (no change needed):**
- `scoring.py` `get_score_threshold()` — reads all thresholds from CONFIG at runtime ✓
- `backtest_runner.py` — uses `_rt().max_score_for_pair()` → `athena.py._max_score_for_pair()` → 3.0 ✓
- `intermarket.py` `FOREX_ENGINE_A_MAX_SCORE = 3.0` ✓
- `athena.py` `_max_score_for_pair()` returns 3.0 for all asset classes ✓
- `athena.py` `maxScoreOverride` defaults to 3.0 ✓

**Note:** Engine A v2 scorer output remains statistically uncorrelated with trade outcome (2026-04-18 finding). These fixes correct stale metadata/limits but do not improve WR/PF. Entry-logic redesign is the next branch.

---

## 2026-04-21: Confidence session multiplier + legacy forex_scoring research branch + BE fix

**`confidence_engine.py` — session quality multiplier:**
`compute_confidence()` now accepts `session_quality: Optional[str]` and post-multiplies confidence:
- `"high"` (London Open, London-NY overlap) → **1.00** (no change)
- `"medium"` (London solo, NY solo) → **0.90**
- `"low"` (Asian, off-hours) → **0.70**

Called from `scoring.py` via `get_session(bar_time)["quality"]`. Off-hours Engine A signals have lower confidence → Engine C's reliability gate (`confidence >= 0.60`) naturally demotes them. Does not affect scoring gates directly.

**`forex_scoring.py` — counter-trend SHORT gate:**
Two config-driven penalty cases under `FOREX_ENGINE.counter_trend_short_gate`:
- **Case A** (`bo_counter_trend_mult: 0.70`): London breakout SHORT (`bo_dir="SHORT"`) while EMA stack is LONG (`trend_dir="LONG"`) and Hurst is trending → `bo_final × 0.70`. Prevents fading a confirmed bullish trend on the first London reversal wick.
- **Case B** (`trend_high_hurst_short_mult: 0.80`): Trend-path SHORT with `_hurst > high_hurst_threshold (0.65)` → `trend_score × 0.80`. Mature downtrend is already extended; penalise late-SHORT entries.
Both penalties are additive (can both apply). Result stored in `result.components["counter_trend_short_applied"]` and `"counter_trend_case"`. Config key `enabled: true`; set `false` to bypass.

**`timed_exit_monitor.py` — breakeven closing at 0.00R fix:**
Root cause: `profit > 0` armed BE on any positive P&L (even $0.01); `mt5_move_sl_to_breakeven` placed SL exactly at entry; spread immediately stopped the trade out at 0.00R.

Two-part fix (both config-driven under `TIMED_EXIT`):
1. **`breakeven_min_profit_r: 0.20`** — BE only arms when `profit >= risk_amount × 0.20`. For a $100 risk trade that's $20 minimum. Falls back to $0.01 floor for rows without `risk_amount`.
2. **`breakeven_buffer_r: 0.05`** — BE SL placed at `entry + sl_dist × 5%` (LONG) or `entry − sl_dist × 5%` (SHORT). On a 20-pip SL that's 1 pip above entry — ensures close price > 0.00R. Applied to both MT5 and Bybit handlers.

The "immediate close" path (`close_min <= be_min` case) retains `profit > 0` unchanged — that path banks profit immediately rather than setting a deferred BE stop.

---

## 2026-04-18: Engine A scorer-is-noise confirmation (factor-level probe)

**Conclusion:** Engine A's scorer output is statistically uncorrelated with trade outcome at both the aggregate and per-factor level. No single-factor polarity flip will rescue WR. Entry-logic redesign is the next branch, not gate/weight tuning.

**Chain of evidence this session:**
1. `diagnose_score_direction.py` (n=265 closed audit rows): Spearman(score, R) = **−0.018**. Score quintile WR is **inverted** — Q5 (highest score) WR 30.2% < Q1 37.7%. Directional hit-rate **42.3%** (worse than coin-flip).
2. Factor-level probe via `diagnose_factors.py` showed only 41/265 audit rows had populated `factors_json` (15.5% coverage) — most historical rows pre-dated the audit-writer fix. Too sparse to trust any per-factor correlation.
3. **camelCase `factors_json` bug found and fixed** at `athena.py:4788` and `athena.py:6050`. Both audit-write sites used `sig.get("factor_scores")` (snake_case) but the signal dict exposes `factorScores` (CLAUDE.md Hard Rule 23). New rows now populate correctly.
4. Built `instrumented_backtest.py` (Engine A backtest with factor sidecar JSONL) + `analyze_factor_dump.py` (Pearson per factor vs R). Additive fields-only edit to `backtest_runner.py` (three `trades.append` sites: swing/intraday/scalp) — logic unchanged.
5. Ran across BTC/USDT, ETH/USDT, EUR/USD intraday → **190 factor-populated trades**. Killed mid-run; sample adequate.

**Factor-level findings (n=190, pooled; intraday):**
- **No factor crosses |ρ|≥0.20.** GBP/USD smoke (n=32) had `fvg_bonus`/`fvg_overlap` at −0.447 but this washed to −0.091 when pooled with EUR/USD — pure small-sample noise.
- Strongest negative: crypto `trend` factor ρ=−0.175 (mean signed value −1.35). Heaviest weight in `factor_scoring.py`. Suggestive but below threshold — needs a bigger crypto sample to confirm, and even at −0.175 it's not a polarity bug.
- Strongest positive: crypto `volume_strength` ρ=+0.130, forex `liquidity_sweep` ρ=+0.097. Weak.
- Verdict matches `diagnose_score_direction.py`: **SCORER-IS-NOISE branch**, not DIRECTION-IS-RANDOM. A polarity flip on any single factor will not rescue WR.

**What was NOT the problem (ruled out this session + prior sessions):**
- BT gate level (2.3× range, flat WR across gates)
- Hurst gate (within noise — measurement revert on 2026-04-18)
- Context propagation (funding/OI 100%/99.8% coverage)
- External-audit `pairMaxScore=3.0` forex claim (stale JSON from 2026-04-05, pre-scale-fix)
- External-audit `bisect` lookahead claim (all sites use `bisect_left`)
- Individual factor sign inversion

**Open next steps (in priority order):**
1. **LONG/SHORT split** on the 190-trade dump — 42.3% dir hit-rate may be one-sided (scorer right on LONG, broken on SHORT, or vice versa). `analyze_factor_dump.py` needs a `--split direction` flag.
2. **Regime split** on same dump — scorer may work in TRENDING and fail in RANGING.
3. **If neither split isolates the issue:** escalate to entry-timing redesign discussion with explicit scope approval. Options include: feature engineering (new factors from orderflow/swing structure), model replacement (ML-ranked entry instead of linear weight), or style pruning (drop whichever style contributes most to random portion).

**Code changes this session (non-scoring):**
- `scoring.py:179-186` — fixed BT fall-through trap where LIVE `MIN_CONFLUENCE_GROUP` bypassed class-level `BT_MIN` when `BACKTEST_USE_BT_MIN_THRESHOLDS=true` (prior session bug).
- `athena.py:4788-4795` + `athena.py:6050-6057` — fixed camelCase `factors_json` bug.
- `athena.py` `_update_trade_outcome` — added |R|>50 sanity clamp + asset-typed fallback gate (prevents the −44554R EUR/CHF corruption from contaminating aggregates).
- `forex_scoring.py` — added `BACKTEST_DISABLE_HURST_GATE` opt-in measurement flag (reversible, backtest-only).
- `config.yaml:355` — `BACKTEST_DISABLE_HURST_GATE: false` (measurement complete, reverted).
- `backtest_runner.py` — three `trades.append` sites now carry `factors` + `factor_weights` for sidecar analysis. Additive only; scoring logic untouched.

**New read-only diagnostic scripts (no writes, `mode=ro` sqlite):**
- `diagnose_score_direction.py` — Spearman / quintile WR / directional hit-rate
- `diagnose_factors.py` — per-factor Pearson correlation (audit_log-based; low coverage pre-fix)
- `diagnose_style.py` — WR/SL/TP/avgR breakdown by (class, style)
- `probe_bt_context.py` — BTC/USDT funding/OI coverage probe
- `instrumented_backtest.py` — runs `backtest_pair` across a pair universe, dumps `factor_dump.jsonl` sidecar (gitignored)
- `analyze_factor_dump.py` — per-factor Pearson(factor, R), splits by class and style

---

## 2026-04-17: Engine A edge root-cause investigation (read-only, diagnostic)

**Conclusion: the Engine A edge loss is NOT the BT score gate.** Three backtest runs on same pair set at different gates produced essentially identical WR:

| Pair | Gate 1.4 (live) | Gate ~0.94 (old BT) | Gate 2.15 (p75) |
|------|------|------|------|
| BTC/USDT | 349 trades, WR 30.1%, PF 0.63 | 411 trades, WR 29.7%, PF 0.58 | 172 trades, WR 29.7%, PF 0.71 |
| GBP/USD | — | 199 trades, WR 36.2%, PF 0.80 | 84 trades, WR 31.0%, PF 0.70 |

WR is flat across a 2.3× gate range. Raising `BT_MIN` cannot rescue Engine A.

**Code-path trap found — `scoring.py:179-182`:** `get_score_threshold` has an **unconditional** fall-through to LIVE `MIN_CONFLUENCE_GROUP` BEFORE reaching class-level `BT_MIN`. So if `BT_MIN_GROUP` is empty or missing a subgroup, the BT lookup silently uses the LIVE group value and `BT_MIN` is never read. To make `BT_MIN` class-level actually bind, **every** subgroup in `MIN_CONFLUENCE_GROUP` must have a matching entry in `BT_MIN_GROUP`. This is a policy-locked scoring file — document the trap but do **not** refactor without explicit user request.

**Config changes this session (BT-only, live gates untouched):**
- `BACKTEST_USE_BT_MIN_THRESHOLDS: false → true`
- `BT_MIN` raised to class-level p75 of observed live Engine A scores: crypto 2.15, commodity 1.80, forex 1.20, stock 1.10, index 1.17
- `BT_MIN_GROUP` repopulated with p75 per subgroup (prevents the `scoring.py:179-182` LIVE fallback from binding)
- `PAIR_PROFILES.XAU/USD.bt_min` and `XAG/USD.bt_min` raised 0.65 → 1.80 (were bypassing class gate)

**Real Engine A findings from `diagnose_edge.py` (read-only, n=266 closed trades):**
- Score Q1→Q4 WR is **not monotonic** — factor scoring doesn't discriminate winners from losers at any threshold
- SL_HIT share: **crypto 56.2%, forex 50.8%** — half the trades never reach TP
- WR by direction: roughly symmetric (no LONG/SHORT tie-break bias)

**SL-mechanics findings from `diagnose_exits.py`:**
- Median SL-hit hold time: crypto 3.67h, forex 5.71h, stock 3.35h, commodity 3.12h — stopped out within a single H4 bar
- TIGHT SL tercile SL_HIT rate: forex **77.3%**, crypto **66.7%** (vs MID/WIDE terciles much lower)
- Current `STYLE_ATR_MULTS` (`config.py:155-186`) are already at industry benchmarks (scalp 1.0-1.2×, intraday 1.5-2.0×, swing 1.8×) — **not globally "too tight"**. Next hypothesis to separate: style mix (scalp dragging) vs entry timing (chasing extremes).

**Data-integrity bug found — not an edge issue but pollutes ALL aggregates:**
EUR/CHF row `ts=2026-03-16T08:18:14` has `r_multiple=-44554.48` on a normal -$106.54 loss. True math:
- Risk: `|0.90389 - 0.90945| = 0.00556`
- Adverse: `|0.90389 - 0.90584| = 0.00195`
- Actual R: **-0.35** (a completely normal loser)

Bug lives in `_update_trade_outcome` in `athena.py` — `risk_amount` likely recorded in wrong units (pip distance vs dollar risk). A single row at -44554R skews every WR/PF/expectancy average across `/api/performance`, `ai_learning.py`, `advisory_thresholds.py`. **Must be fixed before trusting any `audit_log` stats.**

**New read-only diagnostic scripts (`mode=ro` sqlite, no writes):**
- `diagnose_edge.py` — WR by score quartile, exit-reason mix, WR by direction per asset class
- `diagnose_exits.py` — time-to-SL, SL% terciles, SL_HIT by regime, anomaly row dump

**Open next steps for next session (in priority order):**
1. **Write `diagnose_style.py`** (read-only) — break down SL_HIT / TP_HIT / WR / avg_r by **style** (scalp / intraday / swing) within each class. Separates two hypotheses: "scalp style is broken" vs "entry timing is broken across all styles." If scalp SL_HIT is 80% and swing is 40%, the decision is to drop or tighten scalp. If WR is flat across styles, entry timing is the real fix.
2. **Fix `_update_trade_outcome` r_multiple bookkeeping bug** in `athena.py`. First dump all rows where `abs(r_multiple) > 10` to measure pollution scope, then patch the risk-amount unit handling. Re-compute WR/PF/expectancy aggregates after fix.
3. **Only after #1+#2:** decide on a scoring-adjacent change (drop/tighten scalp, widen SL style-specifically, or accept that Engine A entry timing needs deeper redesign). Any of these touch policy-locked scoring files — require explicit user approval.

**Branch state:** `claude/review-codebase-RjgKT` ahead of `main` with BT p75 diagnostic config + 2 read-only diagnostic scripts. **Live gates (`MIN_CONFLUENCE_*`) untouched** per locked-scoring policy. Revert BT chain by flipping `BACKTEST_USE_BT_MIN_THRESHOLDS: true → false`.

---

## 2026-04-17: Engine A forex soft-gating + Engine D gate tightening

**Engine A forex (Option B — session/ADX softening, breakout window extension):**

Diagnostic (`diagnose_engine_a_forex.py`) showed only **3/24 UTC hours** produced signals under ideal bull conditions because of stacked hard gates (session=strict + ADX hard floor 25 + Hurst veto + 3-hour London breakout window). Applied three reversible config-level softenings in `config.yaml` `FOREX_ENGINE`:

- `session_mode: "strict"` → **`"soft"`** (shoulder hours partially active, not blocked)
- `trend_gate_adx_soft_enabled: false` → **`true`** (ADX below hard floor scales confidence, not veto)
- New keys `london_breakout_window_lo: 7`, `london_breakout_window_hi: 11` — extend breakout path from 07:00–09:00 to **07:00–11:00 UTC**. `forex_scoring._london_breakout_score()` reads these (with safe fallback to the old `[_LONDON_OPEN[0], _LONDON_OPEN[0]+2]` window if CONFIG missing)

**No scoring gate, weight, or threshold changes.** These are **soft-gate** flips — live scoring logic untouched per locked policy. Revert by flipping any of the three keys back.

**Historical Engine A forex class floor at that time:** `MIN_CONFLUENCE_CLASS.forex = 1.20`, scale **0–3.0** (Engine A v2 unified). For current live values, use the **Current Engine A Contract (2026-04-22)** section above.

**Engine D (Scalp Lab) tightening:**

1. **`config.py` FACTOR_MIN_DIRECTIONAL_CRYPTO drift fix:** hardcoded default was 0.20, `config.yaml` was 0.15 → aligned to **0.15**. Runtime value unchanged when yaml present; now consistent when yaml is stripped/replaced.
2. **Crypto trade-bucket VP fallback logging** (`scalp_engine.py` ~line 2060): when Binance aggTrade buckets are stale/unavailable and VP falls back to candle volume, emit `log.info("[SCALP-VP] trade_bucket fallback: %s reason=%s — using candle VP", ...)`. Lets us measure real fallback rate without changing behavior.
3. **`SCALP_ENGINE.MIN_GRADE: "C" → "B"`** in `config.yaml`. Scan gate now filters grade C (0.25× size) setups. `MIN_GRADE_AUTO_EXECUTE` was already "B"; this aligns the scan display to the auto-execute floor.
4. **`MT5_ABSORPTION_MIN_COUNT: 2`** new config key + `_classify_setup(asset_type=...)` parameter. For non-crypto (MT5) pairs, mean-reversion one-of-three confluence now requires `absorption.count >= 2` to count as "absorption present" — MT5 tick-volume absorption on a single bar is too noisy. Crypto (Binance real bid/ask trade volume) unchanged. Both callers — `scalp_engine.py` live scan and `backtest_runner.py` scalp BT — pass `asset_type`.

**Non-forex threshold verification (no change):** Analytical verification under variance sqrt-scaling + `_SCORE_SCALE=2.11` stretch confirms current `MIN_CONFLUENCE_CLASS` (crypto 1.20, stock 1.10, commodity 1.10, index 1.05) are reachable with 2–3 aligned factors at z≥1.5. Did **not** change any non-forex thresholds this session — previous regressions were candle-data issues, not gate level.

---

## 2026-04-13: Engine B forex intraday D1 override removed

**Problem:** All Engine B scan paths (standalone `/api/scan-naked`, Engine C `/api/engine-c-scan`, `scanner.py`, and the `simulate_trade` backtest wrapper) contained logic that silently upgraded forex intraday style to swing when `ENGINE_B_FOREX_STRUCTURE_TF = "D1"`. This meant:
- An explicit **intraday** scan on any forex pair used **swing** thresholds: `min_score=5.0` (needs 5/5 checklist), `zone_tf=D1`, `entry_tf=H4`
- Intraday should use: `min_score=4.0` (needs 4/5), `zone_tf=H4`, `entry_tf=H1`
- Result: forex pairs that would pass intraday gates were rejected and surfaced as WATCHLIST in Engine C

**Fix:** Removed the intraday→swing upgrade blocks from:
- `execution.py` (Engine C scan)
- `athena.py` `/api/scan-naked` and `/api/naked-analysis`
- `scanner.py` (Engine A+B combined scan)
- `market_structure.py` `simulate_trade` — entry candle selection now uses `style_profile["entry_tf"]` instead of the D1 check

**`ENGINE_B_FOREX_STRUCTURE_TF`:** The config key still exists and still controls `analyze_structure` internal logic (D1 swing detection, BOS/CHoCH analysis) — that is correct and untouched. The only thing removed is the code that **upgraded the style profile thresholds** based on this config.

**`backtest_runner.py`:** Retains the upgrade but only for `requested_style in ("auto", "naked")` — explicit intraday backtests are unaffected.

**Intraday forex Engine B TFs (current):** `zone_tf=H4`, `entry_tf=H1`, `atr_tf=H4`, `min_score=4.0`.

---

## 2026-04-05: Engine A threshold recalibration

**Root cause:** `MIN_CONFLUENCE_CLASS` gates for stocks (1.55), ETFs (1.45), commodities (1.40), and indices (1.35) were above the scoring formula ceiling.

The multiplicative formula with binary trend factor produces:

- **Maximum achievable score:** 1.42 (all factors at theoretical peak)
- **Typical good signal:** 0.79

Previous gates made it structurally impossible for stocks/ETFs to pass, and commodities/indices could only pass under rare perfect conditions.

**New gates (`config.yaml`):**

- stock: **1.10** (was 1.55)
- commodity: **1.10** (was 1.40)
- index: **1.05** (was 1.35)
- crypto: **1.20** (was 1.40)
- forex: see historical **2026-04-07** decision below (superseded by the current 1.0 live class floor)

These are calibrated at ~75–85% of maximum achievable to allow strong signals through while maintaining selectivity. Backtest **30+ trades per pair** before further adjustment.

---

## 2026-04-05: Engine A score scaling fix

**Problem:** The multiplicative formula `dir_score × quality_mult × dir_conf` produced a maximum `final_score` of ~1.42 instead of the documented 0–3.0 range. Root cause: the trend factor is binary (max ±1.0) and has the highest weight (2.0), so the weighted average of directional factors is structurally capped at ~1.4 regardless of how strong momentum/derivatives/carry are.

This made `MIN_CONFLUENCE_CLASS` gates unreachable:

- stock (1.55): impossible — max was 1.42
- commodity (1.40): only at theoretical perfect conditions
- index (1.35): only at theoretical perfect conditions

**Fix:** Added `_SCORE_SCALE = 2.11` in `factor_scoring.py` line ~1169 to stretch output to the documented 0–3.0 range. Now:

- A score of 1.5 = 50% of theoretical maximum
- A score of 2.0 = 67% of theoretical maximum
- Gates at 1.35–1.55 are achievable with strong (but not perfect) signals

**Impact:** All Engine A backtests before this fix used the unscaled 0–1.42 range. Re-run backtests after applying to get valid results. No threshold changes needed.

---

## 2026-04-07: Forex scoring scale fix (0–2.0)

**Problem:** Forex scoring had two `min(1.0, …)` caps compressing the score range. True max raw was ~1.97, but any score above 1.0 displayed as **1.0**. An intermediate fix used `_FOREX_SCORE_SCALE = 0.507` into 0–1.0 and lowered gates to **0.50** — that collapsed trade counts (e.g. USD/CHF) because the threshold drop did not fully compensate (needed raw ≥ ~0.99 vs old ≥ ~0.80).

**Final fix:**

1. Removed caps on `trend_score` and on the multiplicative `final_score` (no 0.507 scaling).
2. **0–2.0** display scale: `result.final_score` capped at **2.0** (matches achievable max ~1.97).
3. Historical thresholds at that point included a forex class gate of **1.60** (~80% of 2.0, similar selectivity to the old **0.80** on 0-1.0). **`BT_MIN.forex`** was **1.50**; **`BT_MIN_GROUP`** forex was **1.50 / 1.55 / 1.65**; **`MIN_CONFLUENCE_GROUP`** forex was aligned to the same ladder.
4. **`advisory_thresholds.py`:** `_BT_LIMITS.forex` → **(0.80, 1.90)**; `_LIVE_LIMITS.forex` → **(1.00, 2.00)**.
5. **`maxScoreOverride` / Engine C forex path:** **2.0** in `athena.py` and `backtest_runner.py`; **`normalize_engine_a`** treats **`max_score ≤ 2.01`** like forex for the A-side floor. Engine C **conviction** calibration / `record_signal_event` stay **`max_score=1.0`** (0–1 normalized).
6. Historical fallback sync at that point: **`config.py` fallbacks** and **`AUTO_TRADE_MIN_SCORE.forex`** were aligned to **1.60** (informational; matched the then-current class gate on 0-2.0).

**Score mapping (illustrative):**

| Old (capped 0–1) | New (0–2.0) | Meaning |
|-------------------|-------------|---------|
| 0.80 gate | 1.60 gate | ~same selectivity |
| 1.00 (cap) | 1.97 | True max visible |

**Expected at that time:** Trade counts closer to the pre-0.507 fix baseline; elite scores **1.8-2.0** separable from good **1.4-1.6**. Re-run forex backtests.

---

## 2026-04-07: Forex threshold correction (too high) — historical / superseded again later

**Problem:** After the 0–2.0 scale fix, forex thresholds remained at **80% of max** (1.60), which was unreachable without rare SMC bonuses (FVG overlap + liquidity sweep). Typical good forex signals scored 0.8–1.1, so no signals passed.

**Fix:** Lowered forex thresholds to match other asset classes (~47-52% of max):

| Threshold | Old | New | % of max (2.0) |
|---|---|---|---|
| `MIN_CONFLUENCE_CLASS.forex` | 1.60 | **0.95** | 48% |
| `forex_majors` | 1.50 | **0.85** | 43% |
| `forex_crosses` | 1.55 | **0.95** | 48% |
| `forex_exotics` | 1.65 | **1.05** | 53% |
| `BT_MIN.forex` | 1.50 | **0.85** | 43% |
| `BT_MIN_GROUP` forex | 1.50-1.65 | **0.75-0.95** | 38-48% |

**Impact at that time:** Forex signals now appear in main scan. BT_MIN thresholds scaled proportionally to maintain BT/live ratio.

Historical active contract at that time (now superseded):
- Engine A forex scale was **0–3.0** at that point in the migration
- `MIN_CONFLUENCE_CLASS.forex` was **1.20** at that point
- `AUTO_TRADE_MIN_SCORE.forex` was **1.20** and remained informational/status-only at that point
- For the current live contract, use the **Current Engine A Contract (2026-04-22)** section above

---

## 2026-04-07: H1 preload regression fix

**Problem:** The Engine C double-fetch fix added `if h1 is not None:` in `analyze_pair()`. Empty list `[]` from `fetch_candles` satisfied this, bypassing `candle_manager` which has WebSocket data. Result: forex pairs got empty H1 → `analyze_pair()` returned None → no signals.

**Fix:** Changed to truthiness check `if h1:` so empty/missing H1 falls through to `candle_manager` as before.

---

## 2026-04-07: Engine C double-fetch elimination

**Problem:** `api_engine_c_scan()` fetched candles for Engine B, then `analyze_pair()` fetched them again internally. This caused:
- 6 API calls per pair instead of 3 (EODHD rate limits)
- Different H1 data between Engine A and B (different Hurst exponent → different forex scores)
- Higher latency and cost

**Fix:** Restructured per-pair loop:
1. Fetch candles ONCE per TF (`_tf_map_full` stores full bars, `_tf_map` stores last-bar-dropped)
2. Pass `{D1, H4, H1}` from `_tf_map_full` to `analyze_pair(preloaded_candles=...)`
3. Use `_tf_map` for Engine B (last bar dropped as expected)

**Result:** ~40% reduction in EODHD API calls per pair, identical candle data between engines.

---

## 2026-04-16: Engine A MT5 Direct Fetch (TTL Cache Bypass)

**Problem:** The candles_cache TTL (H1=55min, H4=235min, D1=23h) caused Engine A to use stale data for MT5-sourced pairs. CandleBuilder WS was also overriding MT5 OHLC for forex/commodity/index pairs, creating data inconsistencies.

**Fix:** Modified `candles_cache.py`:
1. Excluded MT5 pairs from CandleBuilder WS routing (line 364-365)
2. Bypass TTL cache entirely for MT5 pairs (lines 479-495) — MT5 IPC is fast (1-2ms)
3. Removed MT5+WS merge logic for forex/commodity/index

**Result:** Engine A/B non-crypto pairs now fetch fresh MT5 data on every scan. Crypto pairs still use Binance WS/REST via candles_cache.

**Engine D (Scalp Lab):** Unaffected — already uses `mt5_fetch_scalp_candles()` directly.

**Volume source badge:** Shows "mt5_tick" for forex/commodity/index (correct — OTC markets have no real exchange volume) and "binance_ws" for crypto (correct — Binance WebSocket data).

---

## 2026-04-14: Engine D (Scalp Lab) — Full VP+OrderFlow rebuild

**Previous implementation:** Zone-based M15 structure + M5 trigger system (`detect_m15_zone`, `detect_m5_trigger`, `confirm_momentum`). Signal keys: `entry`, `rr`, `zone_desc`, `trigger_desc`, `risk_level`.

**New implementation:** Fabio Valentini Pro Scalper methodology — three-pillar orderflow system.

**New files/modules:**
- `volume_profile.py` — fixed-range VP computation (POC, VAH, VAL, LVN)
- `indicators.py` — appended: `calc_vwap`, `detect_absorption`, `calc_cvd`, `detect_range_contraction`
- `scalp_engine.py` — fully replaced (~2450 lines as of 2026-04-23)
- `backtest_runner.py` — `backtest_pair_scalp()` inserted before `run_full_backtest`
- `athena.py` — `/api/backtest-scalp` route added; backtest import updated
- `tests/test_scalp_engine.py` — fully replaced to match new API

**Pipeline:** VP (M15) → Absorption/CVD/AAA (M5) → VWAP lean (M15) → HTF bias (H1 EMA stack) → `_classify_setup` → `calculate_scalp_levels` → `ai_quality_grade`

**Signal shape change (breaking — UI updated same session):**

| Old key | New key | Notes |
|---|---|---|
| `s.entry` | `s.price` | entry price |
| `s.rr` | `s.rr1` | R:R ratio |
| `s.symbol` | `s.display \|\| s.pair` | `symbol` is null for MT5 pairs |
| `s.zone_desc` | `s.zone_type` + `s.zone_conditions` | VP-based |
| `s.trigger_desc` | `s.trigger_type` + `s.momentum_method` | orderflow-based |
| `s.risk_level` | `s.ai_grade` (A/B/C/D) | grade drives card border colour |
| — | `s.vp_poc`, `s.vp_vah`, `s.vp_val` | VP levels displayed on card |
| — | `s.absorption_count`, `s.cvd_direction`, `s.aaa_complete` | confluence pills |
| — | `s.size_multiplier` (1.0/0.5/0.25) | position sizing by grade |
| — | `s.htf_bias`, `s.htf_bias_tf` | H1 EMA stack direction |

**Backtest result column fix:** `eval_threshold` → `bt_min` (column name in `backtest_results` table — was mismatched causing `table backtest_results has no column named eval_threshold` error).

**UI changes (`static/index.html`):**
- Scalp Lab panel subtitle: "M15 Structure + M5 Tactical" → "VP + OrderFlow (Fabio Valentini)"
- `buildScalpCard()` fully rewritten for new signal shape
- `renderScalpSignals()` keyed by `display||pair` not `symbol`
- Backtest panel: **⚡ Engine D (Scalp VP)** button (purple gradient) added
- `selectBacktestEngine('D')` → routes to `/api/backtest-scalp`
- `renderScalpBtSingle()` added — shows VP analysis block (trigger/setup/grade breakdowns), equity curve, trade table with scalp-specific columns

---

## 2026-04-14: Engine D M1 execution + session filtering + scratch exits

**M1 execution support:**
- Added `M1_CANDLES` config and `EXECUTION_TIMEFRAME` (default "M1", fallback "M5")
- `mt5_fetch_scalp_candles()` now supports M1 timeframe
- Execution/aggression pillars run on M1 candles for precise entry timing
- Signal includes `execution_tf`, `context_tf` (M5), `structure_tf` (M15)

**Session filtering overhaul:**
- Replaced `is_valid_session()` with `scalp_session_window()` function
- Config-driven `SESSION_MODE` (live) and `BT_SESSION_MODE` (backtest)
- NY open cooldown: `NY_OPEN_SKIP_MINUTES` / `BT_NY_OPEN_SKIP_MINUTES` (default 30)
- Returns `(allowed, reason)` where reason can be "NY_OPEN_COOLDOWN"

**Backtest enhancements:**
- Session filter applied in walk-forward loop via `scalp_session_window(backtest=True)`
- Scratch exit logic: `BT_SCRATCH_ENABLED`, `BT_SCRATCH_BARS`, `BT_SCRATCH_MIN_R`
- Tracks `best_favorable_r` during walk-forward; exits if no early follow-through
- `active_exit_indices` tracking replaces simple `open_positions` counter
- Trade records include `session` field from session window

**Size multiplier support:**
- `ai_quality_grade()` reads explicit `GRADE_A/B/C/D_SIZE_MULT` config keys
- Backtest and live execution pass `sizing_override` to `risk_check()`

**Config additions:**
```yaml
SCALP_ENGINE:
  M1_CANDLES: 300
  SESSION_MODE: "new_york"
  NY_OPEN_SKIP_MINUTES: 30
  EXECUTION_TIMEFRAME: "M1"
  MIN_GRADE_AUTO_EXECUTE: "C"  # overrides MIN_GRADE for execution
  BT_SESSION_MODE: "new_york"
  BT_NY_OPEN_SKIP_MINUTES: 30
  BT_SCRATCH_ENABLED: true
  BT_SCRATCH_BARS: 3
  BT_SCRATCH_MIN_R: 0.10
```

---

## 2026-04-23: Engine A, B, and D bug fixes

### Engine A (`factor_scoring.py`, `scoring.py`, `confidence_engine.py`) — 6 fixes

1. **Module-level constants frozen at import (`factor_scoring.py`):** 10 module-level constants (`_ADX_SOFT_MULT`, `_MOMENTUM_WEIGHT`, etc.) were evaluated once at import time using `CONFIG.get()`. Any config reload after startup left them stale. Fixed by replacing each module-level read with a lazy inline `CONFIG.get("FACTOR_*", default)` at call time. No scoring behavior change — values are identical to existing `config.yaml` defaults.

2. **`_ADDON_AGAINST` floored to 0.0 in `addon_norm` (`factor_scoring.py`):** The negative-penalty branch `_ADDON_AGAINST` was defined but then clamped to `max(0.0, addon_raw)`, making all negative addon scores zero. Fixed: removed the floor so penalties flow through as intended.

3. **Dead `_ADX_SOFT_FULL` constant removed (`factor_scoring.py`):** Constant was declared but never referenced. Removed.

4. **`detect_div` RSI window comparison (`scoring.py`):** The RSI divergence check compared `pr[-1]` (single last bar) against the prior window peak. Changed to compare the **final third** vs **middle third** of the lookback window — more robust zone-vs-zone divergence detection.

5. **`timeframe_alignment` divisor wrong for 0-3 scale (`confidence_engine.py`):** Divisor was `2.0` (old forex 0-2.0 scale). Changed to `1.5` to match the Engine A v2 unified 0-3.0 scale.

6. **`_mad()` even-length uses single middle value (`confidence_engine.py`):** For even-length deviation lists, took only `devs[n//2 - 1]` (lower middle). Fixed to average both middle values `(devs[n//2 - 1] + devs[n//2]) / 2`.

### Engine D (`scalp_engine.py`) — 6 fixes

1. **`time` module clobbered by `datetime.time` import [CRITICAL]:** `import time` on line 27 was overwritten by `from datetime import datetime, time, timezone` on line 29. All `time.time()` calls in `_build_trade_bucket_volume_profile` and `_check_trade_bucket_cvd` crashed with `TypeError: 'type' object is not callable`. Fixed: `import time as _time`; all call sites updated to `_time.time()`.

2. **`_locate_price_vs_vp` check-order tiebreak bias [MEDIUM]:** Sequential `if _near(vah) / if _near(val) / if _near(poc)` calls meant VAH always won even if POC was nearer. Replaced with candidate-collection + `min(distance)` so the actual closest level is returned.

3. **`_check_trade_bucket_cvd` slope measured in price-space, not time [MEDIUM]:** Rows were sorted by `price_bucket` before computing `slope = sum(deltas[-3:]) - sum(deltas[:3])`. In a rising market high-price bins always have more buy delta — a structural false positive. Fixed: sort by `last_ts` (falling back to `price_bucket` when unavailable) so slope reflects temporal order flow.

4. **`size_cut_active` one-way latch [MEDIUM]:** Once `net_r_today >= 2.0`, `size_cut_active` was set to `True` and only reset at UTC midnight. After subsequent losses brought `net_r_today` back below 2R, the cut remained active permanently. Fixed: `size_cut_active` now recomputed from `net_r_today >= 2.0` on every `record_scalp_trade_outcome()` call — deactivates correctly.

5. **`_calc_balance_ratio` returns `0.5` when `session_high/low` both missing [LOW]:** The constant 0.5 fallback equals the `BALANCE_THRESHOLD` (0.40 default), classifying all such VPs as `balance`. Fixed: when session bounds are absent, estimate total range from `(vah - val) / 0.8` (20% headroom) so the ratio is still meaningful.

6. **`infer_bias_from_ema_stack` blocks pullback entries [LOW]:** Condition `last_close >= ema21` for LONG bias meant price had to be above EMA21 to get a bullish bias — exactly the opposite of what Engine D targets (pullback below EMA21 in uptrend). Removed the price-position gate; bias is now determined by EMA stack order (`ema21 > ema50 > ema200`) only.

**Commits:** `bd5cb65` (Engine D), `e52d7eb` (Engine A/B + config + tests)

---

## 2026-04-30: Engine A + Engine B Fixes

### Engine A (`factor_scoring.py`, `scoring.py`, `athena.py`) — 5 fixes

1. **Floor Volatility Scaler at 1.0 for Volatile Tier (`factor_scoring.py`):** High ATR was reducing scores for volatile-tier assets (crypto, nat_gas) via `vol_scaler` (down to 0.85) on top of their already-higher 2.0 threshold. Fixed: `vol_scaler = max(1.0, vol_scaler)` for volatile tier so volatility is treated as opportunity, not penalty.

2. **BTC Bias Conditional on Correlation (`scoring.py`):** Altcoins with 0.3-0.5 correlation to BTC were getting penalized (-15%) for moving independently. Fixed: `_get_30d_correlation()` stub added; BTC bias only applies when `btc_corr > 0.80` (high: ±5%), `0.50-0.80` (moderate: ±3%), `< 0.50` (no effect).

3. **Recalibrate Cost/Funding Penalty Sensitivity (`factor_scoring.py`):** `0.005%` funding was triggering max penalty (`min(0.15, abs(fr)*100)`). Fixed: only penalize funding > 0.01% per 8h (`min(0.10, fr*5)`); normal funding = 0 penalty; negative funding > 0.01% gives boost. Forex carry uses raw `get_carry_differential()` with ±2% annual thresholds.

4. **News Sentiment Guard Rails (`athena.py`):** News sentiment could rescue weak setups or kill good ones without bounds. Fixed: `MAX_NEWS_IMPACT = 0.30` cap; if `base_score < threshold * 0.8`, only negative adjustments apply; audit fields `news_adjustment` and `pre_news_score` added.

5. **Single-Vote Trend Weight Scaling (`factor_scoring.py`):** When only one timeframe had EMA data, `_tf_coverage` was `1/3` regardless of which TF. Fixed: `active_votes == 1` scales coverage by relative weight: `(1/3) * (dominant_weight / 0.50)` so D1-only > H4-only > H1-only.

### Engine B (`market_structure.py`, `config.py`, `config.yaml`) — 10 fixes

1. **D1 Penalty Applies to total_score Only:** `gate_score` (integer count) was being modified by `d1_penalty`, making it non-integer. Fixed: penalty subtracted from `total_score` only; `gate_score` stays pure integer.

2. **Fix Backwards Regime Multipliers:** RANGING=1.15 and HIGH_VOL=1.20 made gates harder in choppiest markets. Fixed: TRENDING=0.90, RANGING=0.90, HIGH_VOL=0.85, LOW_VOL=1.15.

3. **Remove Crypto Trigger from Gate Check:** `crypto_trigger_profile_enabled` in `all(crypto_gates.values())` bricked all crypto if False. Fixed: trigger profile status is diagnostic only; `passed` uses market-condition gates only.

4. **Dynamic max_possible:** Hardcoded `6 + 3 + profile` assumed 6 gates. Fixed: `gate_count + bonus_count` so scalp (5 gates) → max 8, swing (6 gates) → max 9.

5. **Eliminate Double Jeopardy:** `passed AND gate_score >= min_score_scaled` was tautological/impossible. Fixed: `engine_b_confidence_passes` uses `passed` boolean only; score/pct used for sizing/UI.

6. **Remove ADX from Forex structure_ok:** ADX < 25 blocked pristine SMC entries. Fixed: ADX drives regime classification (≥30=TRENDING, ≥20=NORMAL, <20=RANGING); `structure_ok` depends on market structure only.

7. **Contextual Room Gate:** Fixed `min_room_atr = 0.35` blocked tight breaker blocks. Fixed: crypto=0.15, scalp=0.20, RR≥2=0.20, BOS confirmed=0.25, default=0.35.

8. **Internal Diagnostics → Assertions:** `structural_verdict_clear`, `target_v2`, `path` were trading gates. Fixed: logged as warnings (code health); trading gates reduced to market conditions only.

9. **Absorption Entry Fallback:** `entry_ok` had ~40% success rate. Fixed: added `absorption_confirmed and location_at_extreme` as new mean-reversion entry path.

10. **Per-Gate Failure Histogram:** Added `_engine_b_gate_failures` dict + `_log_gate_failure()` for observability.

---

## Hard Rules

1. Never bypass `risk_check()` for any execution
2. Never hardcode thresholds in Python — use `config.yaml`
3. Never import from `athena.py` in unit tests — use `indicators` or `scoring` directly
4. Never commit `*.db` files — gitignored (runtime/market data)
5. Never add `e200s` back to `calc_confluence()` — intentionally removed
6. Never use string matching on warning text for signal classification — use structured flags
7. `PAIR_PROFILE_VOTES` / `PAIR_PROFILE_FILTERS` live in `config.py` only
8. Cache TTL dict keys are **uppercase** `"H1"/"H4"/"D1"`
9. `ALL_PAIRS = FOREX_PAIRS + COMMODITY_PAIRS + INDEX_PAIRS + US_STOCK_PAIRS + ETF_PAIRS + JSE_PAIRS + CRYPTO_PAIRS` — JSE_PAIRS must stay in this concatenation
10. Never hardcode pair lists in `static/index.html` — backtest selector fetches `/api/pairs` dynamically
11. `CandleBuilder.seed()` and `bulk_update_d1()` skip `enabled:False` pairs — do not remove these checks
12. `_resolve_scan_style(requested_style, pair)` — use for per-pair style resolution; do not replace
13. Non-blocking I/O during scans: `carry_feed`, `cot_feed`, `duka_volume` must never block the scan thread
14. SQLite: `PRAGMA journal_mode=WAL`, `timeout=15.0`, explicit commits
15. `"ws": False` opts out of WS only — EODHD plan cap is 50 tickers; do not add `ws:True` without removing others
16. BybitWS: `ping_interval=None` in `websockets.connect()` required — disables library keepalive, prevents 1011 errors
17. Engine B AI is **review-only** — do not reintroduce AI as a pass/fail gate without explicit user request
18. Scoring/confluence/candle complaints require tracing data → engine → API fields → dashboard display — never conclude "engine is wrong" from UI alone
19. Feed routing is **locked** - MT5 sources use `fetch_mt5()` for OHLC/live price; EODHD may overlay volume only and live Engine D must use cache-only EODHD volume lookups; no CandleBuilder for MT5 pairs; no stale bar close into `_live_prices`.
20. Scoring gates are **locked** — do not modify thresholds, weights, or gate logic unless user explicitly requests it
21. `_build_signal_message` reads `"engine_b"` first then `"naked_data"` for ENGINE B section (Engine A signals use `"engine_b"`, Engine B scan signals use `"naked_data"`)
22. Vision structured footer: preserve machine-readable lines — `RIGHT EDGE: CONFIRMS|REVIEW|POTENTIAL REVERSAL` (line immediately before `TF ALIGNMENT`) plus `TF ALIGNMENT` + 3× `RATING` + 3× `LEVELS` in single/dual/triple modes — required by `_extract_vision_structured()` parser; do not reword tokens
23. `confidenceDetail` and `factorDiagnostics` keys are camelCase on the signal dict — do not use snake_case when reading from signal
24. Lottery Lab — never bypass `_normalize_game()` before any DB or analytics call
25. Lottery Lab — `simulate_generator()` incremental counters must never revert to full-history rescan per draw
26. **Chart Vision vs Lottery AI:** both are xAI-backed via OpenAI-compatible SDK (`api.x.ai`) but use different prompts/routes. `/api/chart-analysis` uses `VISION_MODEL`; `/api/lottery/ai-analysis` uses `LOTTERY_AI_MODEL` or `XAI_MODEL`. Do not mix prompts, parser contracts, or route payload schemas between them.
27. **Scalp Lab (Engine D)** is a separate pipeline (`scalp_engine.py`, `volume_profile.py`, `/api/scalp-scan`, `/api/scalp-execute`, `/api/backtest-scalp`) — not produced by `analyze_pair()` and not blended in Engine C. Signal keys use `price` (not `entry`), `rr1` (not `rr`), `display||pair` (not `symbol` — `symbol` is null for MT5 pairs). UI card and `_scalpSignalsById` must both key by `display||pair||symbol`.
28. **Vision ENTRY QUALITY section:** Must appear between **KEY RISKS** and **FINAL VERDICT** in all three prompt modes (single/dual/triple). System prompt rules **9–12** (entry positioning, volatility-regime interaction, move maturity, RR reality check) are mandatory — do not remove or weaken.
