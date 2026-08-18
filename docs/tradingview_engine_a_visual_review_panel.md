# TradingView Engine A Visual Review Panel

This is a report-only UI contract for the existing `TVChartPanel.tsx`. It does
not change Engine A scoring, Engine B, Engine D, thresholds, or execution.

## Evidence Note

| Item | Result | Notes |
| --- | --- | --- |
| Existing EMA strings | Replaced | The previous `EMA@tv-basicstudies(20/50/200)` strings are not the study IDs shown by the Advanced Chart widget examples. The widget examples use a JSON `studies` array inside `embed-widget-advanced-chart.js`. |
| EMA | `MAExp@tv-basicstudies` | Used for EMA20, EMA50, and EMA200 with explicit `length` inputs. |
| DEMA | `DoubleEMA@tv-basicstudies` | Available as a supported AdvancedRealTimeChart study ID in the public `react-ts-tradingview-widgets` study list. Kept behind a manual DEMA200 toggle; the preset leaves EMA200 on as the fallback trend filter. |
| ATR | `ATR@tv-basicstudies` | Added as ATR14 for the lower-panel volatility readout. |
| RSI | `RSI@tv-basicstudies` | Added as RSI14 for divergence review only. |
| Custom entry/SL/TP lines | Side panel only | The external TradingView iframe widget is configured by embed JSON and does not expose a local drawing API to this React component. The panel does not claim these levels are plotted. |
| Prior swing levels | Side panel only | Shown only when present in the current signal payload. No synthetic swing levels are created. |

Primary TradingView reference:
`https://www.tradingview.com/widget-docs/widgets/charts/advanced-chart/`

The TradingView tutorial example for Advanced Chart embeds
`embed-widget-advanced-chart.js` and passes widget options as JSON inside the
script tag, including `autosize`, `symbol`, `interval`, and `studies`.

## Timeframe Interpretation (chart vs policy roles)

The chart interval is a **viewing** choice. It is never a policy role and must
never be used to override, correct, or infer one.

| Concern | Authority | Rule |
| --- | --- | --- |
| Which timeframe scored what | Server-supplied `regimeTf` / `biasTf` / `structureTf` / `setupTf` / `triggerTf` (+ `factorDiagnostics.scoringTimeframes`) | The panel and any reviewer read these; a screenshot on another interval proves nothing about them. |
| Advisory execution context | `executionTf` with `executionMode: live_quote` | Production fills are quote-based. `executionTf` is context only. |
| ATR provenance | `atrDiagnostics.atr_tf` | Levels must be sanity-checked against this exact series, not the chart interval. |
| Engine B bias rung | `biasTf` plus `hierarchicalBias` on the policy payload | Under `ENGINE_B_HIERARCHICAL_TF` the bias rung is Daily for intraday (`hierarchicalBias.applied=true`). Daily bias with an H4 structure zone is the designed state, not a defect. |

Engine B hierarchical expectation, for panels that render Engine B context:
HTF bias (Daily primary for intraday; Weekly + Daily for swing) -> MTF
confirmation on the structure/setup rungs -> LTF entry on the trigger rung. The
panel shows the rungs; it does not evaluate them, and it never re-derives bias
from the drawn chart. There is no `W1` rung on the timeframe ladder — the swing
weekly read comes from `ENGINE_B_HIERARCHY.SWING_WEEKLY_ENABLED` (weekly
resampled from D1) inside `engine_b_hierarchy.py`.

## Engine A Cross-Sectional Ranking (when active)

Engine A V3 scores each pair independently (continuous trend / momentum /
location / volume-flow quality) and promotes on an absolute threshold. When
`ENGINE_A_V3_CROSS_SECTIONAL.ENABLED` is on, already-scored pairs are ranked
inside their `score_group` / universe and only the top N (or top percentile) are
promoted to TRADE.

- The result is stamped per signal as `crossSectionalRanking` (also mirrored into
  `factorDiagnostics.crossSectionalRanking`): `enabled`, `applied`, `surface`,
  `groupBy`, `groupKey`, `method`, `topN` / `percentile`, `minScoreFloor`,
  `groupSize`, `eligibleCount`, `cutoff`, `rank`, `eligible`, `accepted`,
  `reason`, `rankingScore`, `tieBreakers`.
- Ranking is a **relative selection layer**: it never rewrites component scores,
  never upgrades WATCH/NO_SIGNAL, and the absolute threshold still applies first.
- A panel should present rank as cohort context (rank / eligibleCount within
  `groupKey`), never as a quality score, and must not re-rank client-side.
- Disabled, missing, or `applied=false` means the previous absolute-threshold
  behavior is in force; render nothing about ranking rather than implying a gap.

## Scope Guard

The implementation is limited to `static/react-app/app/src/components/panels/TVChartPanel.tsx`.
It reads existing Engine A scan payload fields already available to the React
store and renders them in a side panel. It does not import execution modules,
call execution routes, or mutate any Engine A/B/D scoring code.
