# Cascade Scan Backend Contract

Cascade Scan is a client-driven review pipeline. The backend runs only the
server-safe stages:

1. Engine A broad scan through the existing `run_full_scan(style, asset_class)`.
2. Deterministic shortlist filtering and ranking.
3. Optional provider-agnostic JSON triage when a caller injects a triage client.

The backend does not automate server-side Vision or place trades. Chart-AI
Vision remains a client action because screenshots and rendered layers are
produced by the chart UI.

## Endpoint

`POST /api/cascade-scan`

Request fields:

- `asset_class`: optional string, default `forex`.
- `style`: optional string, default `auto`.
- `enable_triage`: optional boolean, default `false`.
- `top_n`: optional integer, default `5`.
- `rules`: optional object for Cascade shortlist floors and hard-block regimes.

Busy response:

```json
{
  "success": false,
  "busy": true,
  "error": "Scan already in progress"
}
```

Run response:

```json
{
  "success": true,
  "scanRunId": "uuid4 hex string",
  "assetClass": "forex",
  "universeCount": 0,
  "engineACandidateCount": 0,
  "shortlistCount": 0,
  "triageEnabled": false,
  "candidates": []
}
```

## Candidate Fields

- `symbol`: display symbol or broker symbol for the candidate.
- `analysisTimeframes`: always `["D1", "H4", "H1"]`; Engine A scores these jointly.
- `displayTimeframe`: always `D1/H4/H1`; compact UI summary label.
- `reviewTimeframe`: `H1` for scalp or intraday style, otherwise `H4`.
- `direction`: `LONG` or `SHORT`.
- `confluenceScore`: top-level Engine A confluence score from the scan signal.
- `engineAVerdict`: Engine A verdict or scan tier when present.
- `shortlistRank`: one-based deterministic rank after filtering.
- `shortlistPassed`: `true` for emitted candidates.
- `shortlistReasons`: shortlist pass reasons.
- `shortlistBlockers`: non-hard ranking diagnostics such as low coherence or low source fidelity.
- `rr`: top-level risk/reward value from the scan signal.
- `regime`: scan regime label. `RANGING` is not blocked by default.
- `session`: raw session value for display.
- `sessionQualityScore`: ranking score for session quality. Forex candidates are neutral at `0`.
- `sessionQualityReason`: reason for the session score, e.g. `neutral_for_forex`.
- `freshnessDiagnostics`: top-level `dataFreshness` object from the scan signal.
- `sourceFidelity`: derived diagnostic object:

```json
{
  "score": 0.9,
  "pairSource": "mt5",
  "dataFreshness": "fresh",
  "derived": true
}
```

- `coherenceScore`: nested `factorDiagnostics.trendCoherence.coherence_ratio`, or `0` when unavailable.
- `coherenceReason`: `trendCoherence.coherence_ratio` or `not_available`.
- `triageVerdict`: `FULL_CHART_REVIEW`, `WATCHLIST_ONLY`, `SKIP`, or `null` when triage is off.
- `triageReason`: triage reason, or `null` when triage is off.
- `triageConfidence`: triage confidence, or `null` when triage is off.
- `triagePriority`: optional priority from JSON triage.
- `readyForChartReview`: boolean gate for the client chart-review button.
- `reviewAction`: always `OPEN_CHART_REVIEW` for reviewable candidates.
- `chartAiStatus`: `NOT_RUN` until the client runs chart AI.
- `actionState`: one of `ENGINE_A_ONLY`, `WATCHLIST_ONLY`, `READY_FOR_CHART_REVIEW`, `BLOCKED`.

## Frontend Button Behavior

Open chart:

- Load the candidate `symbol`.
- Use `reviewTimeframe` as the chart timeframe.
- Do not call chart AI automatically.

Review with chart AI:

- Client renders the chart for `symbol` and `reviewTimeframe`.
- Client captures `screenshot_meta` and `renderedLayers`.
- Client calls the existing `/api/chart-analysis` endpoint.
- The server-side Cascade Scan endpoint does not run Vision in a loop.

Re-run fresh ENTRY_NOW review:

- Client performs a fresh chart render and chart-AI review.
- Execution remains gated by the existing deterministic and AI-review execution rules.
- JSON triage is review-only and must never be treated as trade approval.

## Safety Notes

- Cascade Scan is diagnostic and review orchestration only.
- It reuses `run_full_scan` and therefore respects the existing scan lock.
- It does not alter Engine A scoring, Engine B/C/D behavior, SL/TP, sizing, or risk gates.
- Hard data filters are applied only for shortlist eligibility.
- Low source fidelity and low coherence are ranking diagnostics, not hard gates.
- Missing or invalid freshness, candle metadata, or ATR provenance fails closed for shortlist eligibility.
