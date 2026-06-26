# Engine A V4 Forex Data-Gate Closeout

## Verdict

**BLOCKED_DATA** — the frozen FOREX research data is insufficient and executable cost data is missing.

## Evidence boundary

Existing MT5 coverage is limited to EURUSD, GBPUSD, USDJPY, and GBPJPY mostly from 2023 through 2026, with fragmented USDCHF coverage. Only D1, H4, and H1 bars are present; M15 and M5 are absent.

The gate is also missing:

- a pinned research store and reproducible dataset manifests;
- historical bid/ask quotes;
- executable rollover or forward-point data;
- verified live/backtest provider parity;
- pre-registered empirical quality thresholds; and
- a frozen `StrategyEmbargoContract`.

These omissions prevent defensible cost accounting, chronological validation, gap embargoes, and reproducible holdout evaluation. Missing or uncertain evidence therefore fails closed.

## Closeout decision

No FOREX V4 strategy implementation or backtest is permitted while this gate remains blocked. No Engine A production proposal, threshold change, live-route change, or execution change is justified.
