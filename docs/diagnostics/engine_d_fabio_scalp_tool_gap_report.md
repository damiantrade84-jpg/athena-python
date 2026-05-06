# Engine D Fabio Scalp Tool Gap Report

Date: 2026-05-06

Scope: audit and planning only. No scoring, risk, threshold, or live-execution behavior was changed.

## Evidence Used

- DOCX extraction: `tmp/docs/fabio_valentini_extracted.txt`
- Current Engine D implementation: `scalp_engine.py`
- Current Engine D config: `config.yaml`
- Volume-profile helper: `volume_profile.py`
- Scalp API and execution routes: `athena.py`
- Scalp Lab UI surface: `static/react-app/app/src/components/panels/ScalpLabPanel.tsx`
- Focused test surface: `tests/test_scalp_engine.py`, `tests/test_scalp_fixes.py`, `tests/test_scalp_backtest_rules.py`, `tests/test_scalp_execution.py`
- External fact-check sources:
  - World Cup official historical standings: https://www.worldcupchampionships.com/world-cup-trading-championship-historical-standings
  - World Cup current/version-2 standings: https://www.worldcupchampionships.com/world-cup-trading-championship-standings-version-2
  - World Cup 2024 quarterly finals: https://www.worldcupchampionships.com/2024-quarterly-finals
  - CME University Trading Challenge past challenges: https://www.cmegroup.com/events/university-trading-challenge/past-challenges.html
  - TradingView CVD documentation: https://www.tradingview.com/support/solutions/43000725058-cumulative-volume-delta/

DOCX render/layout note: visual layout, embedded image content, and pagination are not verified in this environment. Text, tables, footnotes/comments, and media inventory were extracted from the DOCX package.

## Fact-Check Summary

Confirmed:

- The official World Cup historical standings list Fabio Valentini at 2023 Q4 Quarterly Futures Day Trading Championship, 2nd, 68.60%, and 2024 Q1, 3rd, 89.50%.
- The same official historical standings list 2024 Q4 Quarterly Futures Day Trading Championship, 2nd, 218.30%.
- The World Cup version-2/current standings list 2025 Q1 Futures Day Trading Championship, 3rd, Fabio Valentini, 169.7%.
- The World Cup version-2/current standings include the limitation that WCC accounts do not necessarily represent all accounts controlled by a competitor and that entrants may trade more than one account.
- TradingView's official CVD documentation describes CVD as an estimate built from intrabar volume and price fluctuations, not true aggressor-side bid/ask tick classification.
- CME University Trading Challenge official past-challenge pages list university/team winners, not Fabio Valentini as an individual CME winner in the checked source.

Not verified:

- Full private trading track record, drawdown, win rate, Sharpe/Calmar, trade-level logs, and non-competition account history.
- The exact 2,000+ trade count and 600+ trades in one quarter.
- Biographical details and platform ownership details beyond the document's sources.
- DeepCharts internal latency/data-feed claims beyond what the document cites.

## What The DOCX Says A Good Scalp Tool Needs

The document repeatedly reduces the methodology to a three-step filter:

1. Market State: decide balance versus imbalance before looking for entries.
2. Location: trade only at meaningful Volume Profile references such as LVN, POC, VAH, VAL, or outside value.
3. Aggression: require order-flow confirmation at the level. The document states this as "no aggression = no trade" and "if even one is missing, you stay flat."

The document also requires:

- Session-conditioned playbooks: Trend Model favored in New York, Mean Reversion favored in London/compressed conditions.
- Profile anchors that depend on context: prior value, impulse leg, or reclaim leg, not only a blind fixed window.
- Data fidelity: true tick-by-tick bid/ask or aggressor-side data for real footprint/CVD/absorption; OHLC or tick-volume proxies must be labeled as approximations.
- Risk discipline: A/B/C sizing, start small, reduce after losses, stop after three losses, tight invalidation, early scratch/breakeven logic when aggression fails.
- Operator visibility: the tool must show why it skipped, watchlisted, or passed a candidate.

## Current ATHENA Fit

### 1. Market State

Status: partially implemented.

Confirmed in code:

- `scalp_engine.py` computes Volume Profile and a balance ratio in `_build_volume_profile()` and `_calc_balance_ratio()`.
- `_classify_market_state()` returns `balance` or `imbalance`, with a safer default to `balance` when the balance ratio is unavailable.
- `config.yaml` has Engine D VP controls under `SCALP_ENGINE`, including `VP_ENABLED`, `VP_LOOKBACK_BARS`, `VP_BINS`, `VP_VALUE_AREA_PCT`, and `VP_LVN_THRESHOLD`.

Gap:

- The document expects context-specific anchors such as prior session, impulse leg, and reclaim leg. Current live scan uses `candles_m15[-vp_lookback:]`, a mechanical lookback window. That is not the same as a discretionary impulse/reclaim profile anchor.
- This is not a confirmed bug. It is a confirmed methodology difference.

Action:

- Add a report-only "profile anchor mode" diagnostic first: `fixed_lookback`, `prior_session`, `impulse_leg_candidate`, `reclaim_leg_candidate`.
- Do not switch anchors until backtest and paper evidence show improvement.

### 2. Location

Status: mostly implemented.

Confirmed in code:

- `_locate_price_vs_vp()` classifies `at_vah`, `at_val`, `at_poc`, `at_lvn`, `inside_va`, and `outside_va`.
- ATR-based proximity is supported through `VP_PROXIMITY_ATR_K` in `config.yaml`.
- The scan signal includes VP levels: `vp_poc`, `vp_vah`, `vp_val`, `vp_lvn_count`.

Gap:

- Raw Engine D signals include `vp_volume_source` and `vp_bucket_count`, but `_scalp_ui_signal()` does not forward those fields to the Scalp Lab UI payload.
- The React UI type and detail panel show VP levels, but not whether the VP source was `binance_aggtrade`, `candle_volume`, `range_proxy`, or other proxy.

Action:

- First safe fix should be visibility only: pass `vp_volume_source` and `vp_bucket_count` through the API/UI and add tests.

### 3. Aggression

Status: partially implemented, with important fidelity limits.

Confirmed in code:

- `_check_absorption()` uses `indicators.detect_absorption()` when available and has a fallback.
- `_check_cvd()` computes candle/proxy CVD from `indicators.calc_cvd()` or an internal candle-based approximation.
- `_check_trade_bucket_cvd()` can use Binance aggregate-trade buckets for crypto when fresh.
- `_check_aaa_sequence()` implements Absorption -> Accumulation -> Aggression.
- `_check_vwap_lean()` adds VWAP directional lean.

Gap:

- The document's strongest requirement is true order-flow aggression at the level. ATHENA's non-crypto path is MT5 tick volume, EODHD overlay, or candle/range proxy. That is not verified as true bid/ask footprint or aggressor-side CVD.
- For crypto, Binance aggregate-trade buckets are closer to real trade flow, but still need source/freshness visibility at the operator surface.
- Current config allows neutral CVD at VA extremes via `ALLOW_NEUTRAL_CVD_AT_VA_EXTREME: true`; the code can therefore classify some VA extreme setups as valid without true aggression. This is a confirmed difference from the strict DOCX rule.

Action:

- Add report-only "aggression fidelity" fields before changing gates:
  - `aggression_source`: `binance_aggtrade`, `candle_proxy`, `mt5_tick_proxy`, `range_proxy`, `disabled`, `unknown`
  - `aggression_confirmed`: true/false
  - `strict_fabio_pass`: true/false under a shadow rule that requires real absorption/CVD/AAA/VWAP alignment
- Do not hard-block neutral CVD until shadow data shows the impact on paper funnel quality.

### 4. Setup Playbooks

Status: partially implemented.

Confirmed in code:

- `_classify_setup()` supports mean reversion in balance and trend continuation/extension in imbalance.
- `config.yaml` exposes `SETUP_MEAN_REVERSION` and `SETUP_TREND`.
- `config.yaml` includes session controls: `SESSION_MODE`, `NY_OPEN_SKIP_MINUTES`, `LONDON_OPEN_SKIP_MINUTES`, and `GRADE_SESSIONS`.

Gap:

- The DOCX describes more specific sequencing:
  - Trend Model: out-of-balance displacement, profile impulse leg, pullback to LVN, aggression, execute.
  - Mean Reversion Model: balance, failed breakout, reclaim inside balance, profile reclaim leg, pullback to LVN, aggression.
- Current code does not prove the "second failed breakout" or "reclaim leg profile" sequence as a hard rule in live scan.
- Current `SESSION_FILTER` is `false`, so live scans are not hard restricted by London/New York session unless other code paths enforce it.

Action:

- Keep current behavior unchanged.
- Add diagnostics for which playbook was matched and which sequence components were actually observed:
  - `playbook`: `trend`, `mean_reversion`, `trend_extension`
  - `displacement_confirmed`
  - `failed_breakout_count`
  - `reclaim_confirmed`
  - `profile_anchor_reason`

### 5. Risk And Execution

Status: strongly implemented, with some methodology differences.

Confirmed in code:

- `calculate_scalp_levels()` uses ATR/structure-aware stops and sets TP1 from configured R logic.
- `SCALP_ENGINE` has `ATR_SL_ENABLED`, `ATR_SL_MULT`, `TP1_R_MULT`, `MIN_RR`, `ESTIMATED_FEE_PCT_BY_ASSET`, and `ESTIMATED_SLIPPAGE_PCT_BY_ASSET`.
- Fee guard rejects or watchlists zero-stop, micro-stop, and high-cost setups.
- `ai_quality_grade()` maps A/B/C/D quality to sizing multipliers.
- `run_scalp_scan()` blocks all Engine D scans after configured `MAX_DAILY_LOSSES`.
- `record_scalp_trade_outcome()` updates consecutive losses, total losses, net R, and size-cut state.
- `/api/scalp-execute` re-runs a fresh scan, rejects stale/flipped/non-executable candidates, then calls `risk_check()` with the kill switch and size multiplier.

Gap:

- The DOCX describes stops "beyond aggressive print + 1-2 tick buffer." Current code uses ATR/VP/structure buffers, not verified real aggressive-print stops.
- This is a deliberate mechanical implementation difference and may be safer for this tool, but it is not a faithful footprint-stop clone.

Action:

- Preserve current mechanical levels for safety.
- Add `aggressive_print_stop_available: false/true` as a diagnostic if true footprint data is ever added.

### 6. Operator/API/UI Truth

Status: partially implemented.

Confirmed in code:

- Raw scan output includes `gate_result`, `executable`, `candidate_status`, `fail_reasons`, `soft_warnings`, `fee_guard`, `session_risk_state`, `vp_volume_source`, `cvd_source`, and bucket counts.
- `/api/scalp-scan` returns `diagnostic_summary`, pass/candidate/skip counts, and diagnostic skipped rows.
- Scalp Lab UI displays grade, RR, VP levels, absorption count, CVD direction/slope, fail reasons, soft warnings, and executable state.

Gap:

- `_scalp_ui_signal()` drops `vp_volume_source`, `vp_bucket_count`, `cvd_source`, and `cvd_bucket_count`.
- The UI does not clearly label "true flow" versus "proxy flow".
- The UI header says crypto uses Binance aggTrade buckets where fresh, but individual candidate rows do not prove which source was actually used.

Action:

- Safe first implementation item:
  1. Pass source/bucket fields through `_scalp_ui_signal()`.
  2. Add UI badges: `VP source`, `CVD source`, `bucket count`, `proxy warning`.
  3. Add tests that source fields survive API normalization.

## Recommended Action Plan

### Phase A - No Behavior Change, Visibility Only

1. Add Engine D source/fidelity fields to API/UI:
   - `vp_volume_source`
   - `vp_bucket_count`
   - `cvd_source`
   - `cvd_bucket_count`
   - `aggression_source`
   - `aggression_confirmed`
   - `strict_fabio_pass` as shadow/report-only
2. Add focused tests for `_scalp_ui_signal()` and Scalp Lab payload shape.
3. Run:
   - `python -m py_compile scalp_engine.py athena.py`
   - `python -m pytest tests/test_scalp_engine.py tests/test_scalp_execution.py -q --basetemp=.pytest_local_tmp/engine_d_fabio_visibility`

### Phase B - Shadow Diagnostics Before Gate Changes

1. Add a strict Fabio shadow evaluator that does not affect execution:
   - requires market state + location + aggression
   - treats neutral CVD as not confirmed
   - labels candle/MT5/range CVD as proxy
2. Log/report mismatch:
   - `current_pass=true, strict_fabio_pass=false`
   - `current_watchlist=true, strict_fabio_pass=false`
   - `strict_fabio_pass=true, current_no_setup=true`
3. Use `logs/scalp_audit/engine_d_funnel.jsonl` and Scalp Lab diagnostics to evaluate impact.

### Phase C - Data Fidelity Upgrade

1. Crypto:
   - Verify Binance aggTrade bucket freshness and bucket counts in live diagnostics.
   - Consider config-gated execution preference for real trade buckets only, but only after paper evidence.
2. Non-crypto:
   - Keep MT5/EODHD/tick-volume output explicitly marked as proxy.
   - Do not claim true footprint/CVD until a true bid/ask tick feed is integrated and tested.
3. Add a future connector only if a real feed source is available:
   - aggressor side
   - timestamp
   - price
   - size
   - bid/ask or trade side

### Phase D - Strategy Fidelity Research

1. Add report-only profile-anchor diagnostics:
   - fixed lookback
   - prior session
   - impulse leg candidate
   - reclaim leg candidate
2. Backtest/profile compare in Research Lab.
3. Keep default live behavior unchanged until paper evidence proves a better anchor.

## Bottom Line

ATHENA already has the skeleton of a serious scalping tool: Volume Profile, market-state/location/aggression pipeline, grading, fee guard, ATR/1R levels, daily risk state, fresh-scan execution, and diagnostics.

The biggest confirmed gaps are not "more indicators." They are:

1. Data-source truth is not visible enough in the operator UI.
2. Non-crypto aggression is proxy-based, not verified true footprint/order flow.
3. Current profile anchoring is mechanical last-N M15 bars, not the full impulse/reclaim-leg logic described in the document.
4. Current defaults can allow neutral-CVD VA extreme setups, which is looser than the strict "no aggression = no trade" document rule.

Safest next implementation is Phase A only: source/fidelity visibility with tests, no gate changes.

## Phase A Implementation Result - 2026-05-06

Implemented the safe visibility slice only:

- `scalp_engine.py` now adds report-only aggression fidelity fields:
  - `aggression_source`
  - `aggression_source_raw`
  - `aggression_source_is_proxy`
  - `aggression_confirmed`
  - `strict_fabio_pass`
  - `aggression_components`
- `athena.py` now preserves VP/CVD source and bucket fields through `_scalp_ui_signal()`.
- `static/react-app/app/src/components/panels/ScalpLabPanel.tsx` now displays VP source, VP buckets, CVD source, CVD buckets, proxy/true-flow labels, aggression confirmation, and strict Fabio shadow status.
- `tests/test_scalp_engine.py` covers proxy candle flow versus Binance aggTrade flow.
- `tests/test_scalp_execution.py` covers API normalization of the new fidelity fields.

Confirmed not changed:

- Engine D scoring.
- Engine D risk checks.
- Engine D thresholds.
- Paper/live execution decisions.
- Existing PASS/WATCHLIST/BLOCK gate logic.

Validation:

- `python -m pytest tests/test_scalp_engine.py::test_aggression_fidelity_marks_proxy_flow_as_not_strict tests/test_scalp_engine.py::test_aggression_fidelity_marks_binance_trade_flow_as_strict -q --basetemp=.pytest_local_tmp\engine_d_phase_a_green1` passed (`2 passed`).
- `python -m pytest tests/test_scalp_execution.py::test_scalp_ui_signal_preserves_flow_fidelity_fields -q --basetemp=.pytest_local_tmp\engine_d_phase_a_green2` passed (`1 passed`).
- `python -m py_compile scalp_engine.py athena.py` passed.
- `.\node_modules\.bin\tsc.cmd -b --noEmit` passed from `static/react-app/app`.
- `python -m pytest tests/test_scalp_engine.py tests/test_scalp_execution.py -q --basetemp=.pytest_local_tmp\engine_d_phase_a_focused` passed (`112 passed`).

## Phase B Implementation Result - 2026-05-06

Implemented the safe shadow-diagnostics slice only:

- `scalp_engine.py` now adds `_engine_d_strict_fabio_shadow()` as a report-only three-pillar evaluator.
- The strict shadow evaluator requires:
  - market-state alignment (`balance` for mean reversion, `imbalance` for trend setups)
  - valid strict location (`at_vah`/`at_val`/`outside_va` for mean reversion, `at_lvn` for trend continuation, `outside_va` for trend extension)
  - strict aggression from true Binance aggTrade flow via the Phase A aggression-fidelity check
- Raw Engine D signals now include:
  - `strict_fabio_reason`
  - `strict_fabio_missing_pillars`
  - `strict_fabio_pillars`
  - `current_vs_strict_status`
- `_scalp_ui_signal()` now preserves the strict shadow fields.
- Scalp Lab now displays strict reason, missing pillars, and current-vs-strict status.

Confirmed not changed:

- Engine D scoring.
- Engine D risk checks.
- Engine D thresholds.
- Paper/live execution decisions.
- Existing PASS/WATCHLIST/BLOCK gate logic.

Important validation result:

- `tests/test_scalp_engine.py::test_run_scalp_scan_does_not_block_close_structure_target` proves an existing Engine D `PASS` remains `PASS` and executable while being shadow-labelled `current_pass_strict_fail` because the strict Fabio aggression pillar is missing.

Validation:

- Initial red tests failed as expected due missing `_engine_d_strict_fabio_shadow`, missing API passthrough fields, and missing signal shadow fields.
- Test collection was initially blocked by an existing dirty `config.yaml` addition: `BACKTEST_USE_BT_MIN_THRESHOLDS: true`. `config.py` explicitly rejects that key at startup, so it was removed to restore the repo's fatal config invariant before validation.
- `python -m pytest tests/test_scalp_engine.py::test_strict_fabio_shadow_flags_current_pass_with_proxy_aggression tests/test_scalp_engine.py::test_strict_fabio_shadow_passes_when_all_three_pillars_align -q --basetemp=.pytest_local_tmp\engine_d_phase_b_green1` passed (`2 passed`).
- `python -m pytest tests/test_scalp_execution.py::test_scalp_ui_signal_preserves_flow_fidelity_fields -q --basetemp=.pytest_local_tmp\engine_d_phase_b_green2` passed (`1 passed`).
- `python -m pytest tests/test_scalp_engine.py::test_run_scalp_scan_does_not_block_close_structure_target -q --basetemp=.pytest_local_tmp\engine_d_phase_b_green3` passed (`1 passed`).
- `python -m py_compile config.py scalp_engine.py athena.py tests\test_scalp_engine.py tests\test_scalp_execution.py` passed.
- `.\node_modules\.bin\tsc.cmd -b --noEmit` passed from `static/react-app/app`.
- `python -m pytest tests/test_scalp_engine.py tests/test_scalp_execution.py -q --basetemp=.pytest_local_tmp\engine_d_phase_b_focused` passed (`114 passed`).
- `python -m pytest tests/test_scalp_engine.py tests/test_scalp_execution.py tests/test_scalp_fixes.py tests/test_scalp_backtest_rules.py -q --basetemp=.pytest_local_tmp\engine_d_phase_b_adjacent` passed (`132 passed`).
- `python -m pytest tests/test_stage4_hardening.py tests/test_scoring_group_routing.py -q --basetemp=.pytest_local_tmp\engine_d_phase_b_config` passed (`30 passed`).
- `python -m pytest tests/test_vectorbt_research_lab.py -q --basetemp=.pytest_local_tmp\engine_d_phase_b_vectorbt_fresh` passed (`65 passed`, two pandas `FutureWarning`s).
- Full repo validation was not green: `python -m pytest -q --basetemp=.pytest_local_tmp\engine_d_phase_b_all` completed with `1164 passed, 51 failed, 2 warnings`. The failures were broad and outside the Engine D Phase B slice, including legacy static HTML assertions, extracted-route expectations still pointed at `athena.py`, audit repo schema drift, auto-trader debate trace-id expectations, MT5 monkeypatch/module-shape failures, and research-lab no-live-import checks after full-suite import pollution.

## Phase C and D Implementation Result - 2026-05-06

Implemented the safe diagnostics/reporting slice only:

- `scalp_engine.py` now emits `data_fidelity` diagnostics for VP, CVD, absorption, and aggression:
  - labels real Binance aggregate-trade bucket usage versus candle/range/tick-volume proxies
  - preserves VP/CVD bucket counts and proxy booleans
  - marks absorption as candle-volume/tick-volume proxy unless true order-flow evidence exists
  - keeps the fields report-only in signal payloads and funnel `diagnostic_notes`
- `scalp_engine.py` now emits `profile_anchor_shadow` diagnostics:
  - reports the active/current profile anchor mode
  - reports the fixed-lookback window metadata (`bars`, start/end, high/low, volume)
  - adds report-only candidates for prior UTC session, impulse leg, and reclaim leg when supportable from current M15 candles
- `athena.py` now preserves the new Phase C/D fields through `_scalp_ui_signal()`.
- `static/react-app/app/src/components/panels/ScalpLabPanel.tsx` now displays data-fidelity and profile-anchor diagnostics in the Engine D detail panel.
- `tests/test_scalp_engine.py` covers source-fidelity classification, profile-anchor shadow candidates, and confirms an existing Engine D `PASS` remains `PASS`.
- `tests/test_scalp_execution.py` covers API normalization of the new diagnostics fields.

Confirmed not changed:

- Engine D scoring.
- Engine D risk checks.
- Engine D thresholds.
- Engine D gate decisions.
- Paper/live execution behavior.
- `config.yaml`.

Validation:

- `python -m py_compile scalp_engine.py athena.py tests\test_scalp_engine.py tests\test_scalp_execution.py` passed.
- `git diff --check -- scalp_engine.py athena.py static/react-app/app/src/components/panels/ScalpLabPanel.tsx tests/test_scalp_engine.py tests/test_scalp_execution.py docs/diagnostics/engine_d_fabio_scalp_tool_gap_report.md` passed, with only existing LF-to-CRLF warnings.
- `python -m pytest tests/test_scalp_engine.py tests/test_scalp_execution.py -q --basetemp=.pytest_local_tmp\engine_d_phase_cd` passed (`116 passed`).
- `.\node_modules\.bin\tsc.cmd -b --noEmit` passed from `static/react-app/app`.

Remaining risk:

- Prior-session, impulse-leg, and reclaim-leg anchors are heuristics for visibility only. They are not evidence that those anchors improve performance, and they are not used for scoring, gating, risk, or execution.

## Final Integration Validation - 2026-05-06

Confirmed after Phase C/D landed:

- Baseline stale tests and order-pollution failures were stabilized without changing trading/scoring/risk behavior.
- Full repo pytest validation passed: `python -m pytest -q --basetemp=.pytest_local_tmp\baseline_full_after_order_fixes` (`1218 passed`, two pandas `FutureWarning`s).
- TypeScript validation passed: `.\node_modules\.bin\tsc.cmd -b --noEmit` from `static/react-app/app`.
- Phase C/D focused validation passed before integration: `python -m pytest tests/test_scalp_engine.py tests/test_scalp_execution.py -q --basetemp=.pytest_local_tmp\engine_d_phase_cd` (`116 passed`).

Confirmed still not changed:

- Engine D scoring.
- Engine D thresholds.
- Engine D risk checks.
- Engine D gate decisions.
- Paper/live execution behavior.
- `config.yaml`.
