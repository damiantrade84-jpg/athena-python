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

## Scope Guard

The implementation is limited to `static/react-app/app/src/components/panels/TVChartPanel.tsx`.
It reads existing Engine A scan payload fields already available to the React
store and renders them in a side panel. It does not import execution modules,
call execution routes, or mutate any Engine A/B/D scoring code.
