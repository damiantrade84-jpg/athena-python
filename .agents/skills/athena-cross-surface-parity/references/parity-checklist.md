# Athena cross-surface parity checklist

Use when Engine A scoring, chart API, TV Chart UI, backtest, or AI review context must show the **same numeric contract** for the same pair/score_group/timeframe.

**Lesson (TV Chart RSI, 2026):** `price_precision.rsi_period` was sent, EMA periods were consumed on the client, but RSI/ATR/ADX were still computed at hardcoded 14 on server and client. Tests passed because both sides used RSI 14 without `score_group`. **Field presence is not parity.**

---

## Golden rule — closed loop required

For every config-driven value (period, threshold, gate, provider, candle policy), trace this loop and mark each hop **PASS / FAIL / NOT REVIEWED**:

```
config.yaml key
  → resolver (_resolve_* in factor_scoring / scoring / config)
  → producer computation (indicators, scoring, overlay builder)
  → API payload field name + value
  → UI/consumer read (not just type definition)
  → displayed label / legend / AI prompt text
  → regression test asserts per score_group (not universal default)
```

**Stop condition:** If any hop reads a hardcoded literal while an earlier hop resolves per-group, that is **CRITICAL drift** until proven intentional.

---

## Surface map (Engine A lane)

| Invariant | Producer | API / payload | Consumer | Test anchor |
|-----------|----------|---------------|----------|-------------|
| RSI period | `factor_scoring._resolve_rsi_period` | `_format_chart_candles` → `indicator_periods.rsi`, candle `rsi`/`rsi14` | `TVChartPanel` `indicatorPeriods`, `buildChartStudySnapshot` | `test_chart_api_indicator_period_parity.py` |
| EMA periods | `_resolve_ema_periods` | `ema_trend`/`ema_momentum`/`ema_long`, `price_precision.ema_periods` | `emaPeriods` useMemo, chart EMA lines | same + `test_engine_a_indicator_period_parity.py` |
| ATR period / source | Engine A ATR path + `atrDiagnostics` | chart `atr14`, `atr_timeframe`, `atr_provider` | chart legend, parity panel | chart vs signal ATR diagnostics |
| ADX period / source | D1/H4 gate mode per group | chart `adx14` (period 14 today) | study pane label | document intentional divergence |
| Candle policy | confirmed vs forming config | `candle_confirmed_policy`, `confirmed` on rows | client confirmed-only indicator inputs | compare last bar handling |
| VWAP filter | `ENGINE_A_VWAP_FILTER` + crypto gate | `engine_a_vwap_filter_enabled`, candle `vwap` | `showVwapOnChart` in TVChartPanel | `test_vwap_filter_crypto_only_when_enabled` |
| Engine B overlay freshness | `_normalize_engine_b_overlay_payload` | `computed_at`, `overlay_source` | stale badge, `engineBOverlayStatus` | overlay age > threshold |
| Score group | `get_pair_score_group` | `price_precision.score_group` | parity rows, AI context | per-group parametrized tests |

---

## Mandatory adversarial checks

Run before claiming parity or merging chart/scoring changes:

### 1. Hardcoded period grep

```bash
rg -n "rsi\(.*,\s*14\)|calc_rsi\(.*14\)|atr\(.*,\s*14\)|calc_atr\(.*14\)|adx\(.*,\s*14\)|calc_adx\(.*14\)|ema\(.*,\s*21\)|calc_ema\(.*21\)" static/react-app athena_app/api routes_market_data.py indicators.py
```

Flag any hit that is not behind an explicit `indicatorPeriods` / `_resolve_*` / config read.

### 2. “Sent but unused” grep

```bash
rg -n "rsi_period|ema_periods|indicator_periods|score_group|price_precision" static/react-app athena_app/api factor_scoring.py
```

For each field: find **write site** (API) and **read site** (computation or UI). Write without read = FAIL.

### 3. Client fallback audit

In `TVChartPanel.tsx` `buildChartStudySnapshot`:

- `useApiIndicators = true` is good only if server values use correct periods.
- Local `rsi`/`atr`/`adx`/`ema` fallbacks must use `indicatorPeriods` / `emaPeriods`, never literal 14/21/50/200.

### 4. Test honesty audit

Ask for each parity test:

- Does it pass `score_group` on **both** sides?
- Does it use a non-default group (e.g. `forex_majors` RSI 18, `crypto_btc` RSI 12)?
- Would the test still pass if the bug regressed? (Both sides wrong the same way = masked failure)

### 5. Label honesty

UI labels (`RSI14`, `ATR14`, `EMA21`) must match actual computation period. Dynamic labels required when period ≠ default.

---

## Pytest budget

- During parity **review**: greps and source reads are required; **do not run pytest**.
- After a **fix** that changes indicators: run at most **one** parity file — prefer `tests/test_chart_api_indicator_period_parity.py` unless the fix is crypto-specific (`tests/test_engine_a_crypto_chart_parity.py`). Do not run every file in the surface map table.

## Score-group spot checks (minimum)

Parametrize at least these pairs in tests (read or extend one cited file) or manual verification:

| Pair | score_group | asset_type | RSI period (typical) |
|------|-------------|------------|----------------------|
| EURUSD | forex_majors | forex | 18 |
| TRXUSDT | crypto_* | crypto | 12 |
| US500 / SPY tracker | us_indices_trackers | index | 14 (default tier) |

Assert: `chart API last candle rsi` == `calc_indicators_with_normalized(..., score_group=group).snap.rsi`

---

## Live vs backtest vs chart

When touching indicators or scoring:

1. **Live scan** — `scanner.py` → `factor_scoring` / `forex_scoring` path
2. **Backtest** — `backtest_runner.py` / research lab uses `calc_indicators_with_normalized` with or without `score_group`?
3. **Chart API** — `routes_market_data._format_chart_candles`
4. **UI** — `TVChartPanel` study snapshot

All four must be listed in the coverage map. Divergence in any one is a finding.

---

## AI review / Vision lane

When chart feeds AI review:

- `engineBOverlayStatus` / count in `chart_snapshot` (prompt path)
- Server cross-check in `routes_ai_chart_review.py` for empty structure context
- Engine A diagnostics must come from server-trusted fields, not frontend guesses

---

## Finding severity guide

| Severity | Example |
|----------|---------|
| CRITICAL | Chart RSI period ≠ Engine A score group period |
| HIGH | VWAP shown in Engine A filter path but hidden on chart (when filter enabled) |
| MEDIUM | Client duplicate math still used as fallback with wrong period |
| LOW | Label says RSI14 but value is RSI18 (misleading) |
| INFO | Intentional divergence documented (e.g. chart ADX on H4 vs gate on D1) |

---

## Verdict template

```
Coverage map: [surfaces + files + tests]
Closed loops traced: [list each invariant + PASS/FAIL]
Findings: [severity, path, fix, test]
Masked tests: [any test that would pass while bug exists]
Verdict: PASS | PASS WITH GAPS | FAIL | BLOCKED
```

Do not use **PASS** if any CRITICAL closed loop is FAIL or NOT REVIEWED.
