---
name: athena-engine-parity
description: Use when investigating live-chart, backtest, or scan parity for Engine A, Engine B, Engine C, or Engine D — candle policy, provider routing, data freshness, ATR provenance, scoring drift, overlay contracts, UI chart payloads, or AI review context mismatches. Do not use for execution-gate changes, unrelated Python cleanup, or threshold tuning unless the user explicitly requests it.
---

# Athena engine parity

Verify outputs match intended data source, candle policy, scoring contract, and consumer payloads without changing strategy semantics.

## Review discipline

Follow `docs/codex-code-review-discipline.md`. Build a **coverage map** before any parity verdict. For shipped-change or "nothing missed" parity checks, also follow `.agents/skills/athena-anti-miss-review/SKILL.md`.

Do not claim parity without tracing the full chain: provider/source → candle policy → scoring/confidence → gates → SL/TP/RR → payload → UI/API consumer → tests. If incomplete, say **"Coverage incomplete"** and list missing paths.

## Steps

1. Identify engine and route (live scan, backtest, API, UI).
2. Trace provider → cache → engine → payload consumer (caller/callee with file evidence).
3. Inspect relevant `config.yaml` keys and env routing — do not assume from comments.
4. Compare score/confidence/overlay fields to tests and config sources.
5. Run targeted `rg` for duplicate paths, stale fallbacks, hardcoded thresholds, and UI/backend field drift in scope.
6. Adversarial pass: assume drift exists — check alternate routes, old fallbacks, engine-only fixes.
7. Document exact drift point before patching.

## Inspect first

`scanner.py`, `candles_cache.py`, `candle_feeds.py`, engine modules (`forex_scoring.py`, `factor_scoring.py`, `market_structure.py`, `engine_c.py`, `scalp_engine.py`), `routes_market_data.py`, chart UI under `static/react-app/`.

Multi-engine parity: use parallel lanes in `athena-anti-miss-review/references/review-lanes.md`.

## Output

Per finding: severity, file/anchor, execution path, expected vs actual, minimal fix, regression test. End with Coverage and Verdict (**PASS** / **PASS WITH GAPS** / **FAIL** / **BLOCKED**) when user asked for verification.

## Boundaries

- No threshold changes unless explicitly requested.
- Do not collapse Engine A/B/C/D responsibilities.
- No long backtest matrix unless requested.
- Every fix needs a regression test and proof of no unintended scoring change.
- Current source and tests are proof — not memory or old audit notes.
