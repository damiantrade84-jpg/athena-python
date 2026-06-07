---
name: athena-cross-surface-parity
description: Use before merging or reviewing changes that touch Engine A scoring, chart candle indicators, TV Chart UI, backtest indicator paths, or AI chart review payloads — to catch "field sent but not used", hardcoded period drift, masked parity tests, and producer→consumer contract breaks across config, server API, React client, and pytest. Invoke for parity audit, cross-surface verification, "chart matches Engine A", or preventing RSI/EMA/ATR period mismatches.
---

# Athena cross-surface parity

Catch **contract drift** across config → resolver → server → API → UI → tests. Complements `athena-engine-parity` (investigation) and `athena-anti-miss-review` (general audits).

**Full checklist:** `references/parity-checklist.md` — load and follow step-by-step.

## When to invoke (mandatory)

Invoke this skill when the task touches **any** of:

- `factor_scoring.py`, `forex_scoring.py`, `scoring.py`, `indicators.py`
- `athena_app/api/routes_market_data.py` (candles, overlays, `price_precision`)
- `static/react-app/**/TVChartPanel.tsx` or chart indicator helpers
- `tests/test_engine_a_*`, `tests/test_*chart*`
- `config.yaml` keys under `ENGINE_A_*_PERIOD`, `ENGINE_A_VWAP_FILTER`, candle policy

Also invoke when the user says: parity, chart matches scoring, period mismatch, "looks wired", or before shipping chart/scoring fixes.

## Workflow

1. **Build closed-loop map** — one row per invariant (RSI period, EMA periods, ATR, VWAP gate, overlay freshness, score_group). See checklist table.
2. **Trace producer → consumer** with file evidence at each hop. **Do not stop at "field exists in interface".**
3. **Run adversarial greps** from checklist (hardcoded 14/21, sent-but-unused fields).
4. **Audit tests** — would they pass if both sides were wrong the same way? Add per-`score_group` assertions if missing.
5. **Compare live / backtest / chart** paths when indicators change.
6. **Emit verdict** using checklist template. CRITICAL drift → **FAIL**, not "looks good".

## Non-negotiable rules

- **Field presence ≠ behavior.** `rsi_period` in `price_precision` proves nothing until chart computation and tests use it.
- **Parametrize non-default groups** in regression tests (`forex_majors` 18, `crypto` 12).
- **Dynamic UI labels** must match computed periods.
- **No threshold or scoring semantic changes** unless user explicitly requests.
- Current source + current tests are proof — not memory or old audit notes.

## Output

Per finding: severity, closed-loop hop that broke, file/anchor, expected vs actual, minimal fix, regression test name.

End with: Coverage map, masked-test risks, Verdict (**PASS** / **PASS WITH GAPS** / **FAIL** / **BLOCKED**).

## Boundaries

- Paper-only; no execution-gate weakening.
- Do not run full test suite or backtest matrix unless requested.
- Pair with `athena-ui-chart-review` for Vision/prompt-only work; pair with `athena-engine-parity` for provider/candle/ATR provenance deep dives.
