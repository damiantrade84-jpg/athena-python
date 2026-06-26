# Engine A V4 Crypto Data-Gate Closeout

## Verdict

**BLOCKED_DATA** — frozen CRYPTO research data is insufficient and venue-specific execution-cost evidence is missing.

## Frozen evidence currently available

- Venue and instrument: Bybit linear perpetuals only.
- Symbols: BTC, ETH, SOL, XRP, and DOGE.
- Timeframes: D1, H4, and H1 only.
- Approximate coverage: 2024-05-12 through 2026-05-30.
- Funding and open-interest series exist, but their point-in-time alignment with the frozen candles is not proven.

The preferred universe cannot qualify because BNB, ADA, and LINK are not present in the frozen store. M15 and M5 data are also absent.

## Missing gate evidence

- pinned Crypto research manifests;
- a content-addressed frozen research store;
- confirmed/provisional capture-state tagging;
- historical bid/ask quotes or spread series;
- venue-specific fee and slippage assumptions supported by evidence;
- contract and sizing metadata;
- Crypto exchange outage and gap review; and
- a provider-parity manifest.

Missing or uncertain evidence fails closed. The gate must remain `BLOCKED_DATA`; no Crypto V4 strategy implementation or backtest is permitted.

## Confirmed provider defect

`athena_research.data_loader` defaults Crypto research acquisition to Binance futures, while the existing frozen and live-aligned research evidence uses Bybit linear perpetuals. Binance and Bybit data are not interchangeable, so this mismatch must be reported rather than silently accepted.

This is future Crypto research data-pipeline work. It is not repaired in this closeout and does not justify a production, live-route, threshold, TradingView, or execution change.

## Closeout decision

No strategy implementation is permitted while the data gate remains blocked. No Engine A production proposal or other production/live change is justified.
