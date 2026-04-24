# Paper Soak Validation

This checklist is for paper/demo operation only. It does not approve real-money automation.

## Paper/Demo Start Requirements

Paper/demo can start only after:
- Full live feed matrix passes validation: `python tools/validate_live_feed_matrix.py --input docs/diagnostics/latest_live_feed_snapshot.json`
- No missing H1/H4/D1 rows for required symbols (EURUSD, GBPUSD, BTCUSDT, ETHUSDT)
- All gate decisions are ALLOW for tested market data
- PAPER_SOAK.ENABLED is true in config.yaml
- REAL_ORDERS_ALLOWED is false in config.yaml
- All required candle policies show POLICY_OK status
- No ERROR_PATH_MISMATCH or ERROR_OFFSET_MISMATCH
- No WARNING_ONE_BUCKET_LAG (genuine stale, not intentional confirmed-only lag)

## Real-Money Automation Requirements

Real-money can only be considered after:
- Minimum 1 full trading week of paper/demo logs (not 2 weeks - reduced for faster validation)
- No unexplained stale candle events
- No ERROR_PATH_MISMATCH in logs
- No ERROR_OFFSET_MISMATCH in logs
- No unexpected real order calls in paper mode (verify no broker API calls)
- Risk sizing verified manually
- Execution rejects reviewed
- Drawdown reviewed
- Manual approval

## Run Live Feed Diagnostics

Start Athena in demo mode, then call:

```bash
curl "http://127.0.0.1:5000/api/live-feed-diagnostics?symbols=EUR/USD,GBP/USD,BTC/USDT,ETH/USDT&timeframes=H1,H4,D1"
```

No trades are placed by this endpoint. The response field `tradesPlaced` must be `0`.

## Fresh Candle Output

For each symbol/timeframe, verify:

- `providerStatus` is `ok`.
- `stalenessSeverity` is `fresh`.
- `bucketLag` is `0`.
- `hasCurrentBucket` is `true`.
- `lastBarEpoch` is not null.
- `expectedCurrentBucketEpoch` matches the active bucket.
- `confirmedCount` is greater than `0`.

## MT5 Forex H4 Offset

For forex H4 with `FOREX_H4_RESAMPLE_OFFSET_HOURS: 1`, expected active H4 buckets are:

```text
01:00, 05:00, 09:00, 13:00, 17:00, 21:00 UTC
```

In diagnostics, forex H4 should show:

- `usesOffset: true`
- `offsetHours: 1.0`
- `expectedCurrentBucketIso` on the 01/05/09/13/17/21 UTC grid

## Binance Crypto H4/D1

For BTCUSDT and ETHUSDT:

- Binance REST rows should show `source` as Binance or Binance futures.
- H4 buckets should stay on the UTC 00/04/08/12/16/20 grid.
- D1 buckets should stay at 00:00 UTC.
- `usesOffset` must be `false`.
- WS rows with `source: binance_ws` should appear when CandleBuilder has kline data.

## Stale Warning

A stale diagnostic warning looks like:

```json
{
  "timeframe": "H4",
  "bucketLag": 1,
  "hasCurrentBucket": false,
  "stalenessSeverity": "stale_1_bucket"
}
```

Scanner signals may still display, but warnings should include `DATA_FRESHNESS`.

## Execution Block

With `DATA_FRESHNESS_GATES.BLOCK_EXECUTION_ON_STALE: true`, execution should be rejected before broker order placement. Expected reason shape:

```text
STALE_DATA_BLOCK:H4:stale_1_bucket
```

For path mismatches:

```text
STALE_DATA_BLOCK:H4:error_path_mismatch
```

## Logs To Capture

Capture these during the soak:

- `/api/live-feed-diagnostics` output for EUR/USD, GBP/USD, BTC/USDT, ETH/USDT.
- `/api/feed-health` output.
- Scanner logs around candle source and `DATA_FRESHNESS` warnings.
- Execution reject logs with `STALE_DATA_BLOCK`, risk rejection, duplicate rejection, and broker rejection.
- MT5 terminal connection status and broker symbol names.
- Binance WS startup logs showing `1h`, `4h`, and `1d` kline subscriptions.

## Minimum Soak Period

Run at least 1 full trading week in paper/demo before considering any real-money review. Include at least one forex market open, one New York session, one Asian/off-session period, and one crypto weekend.

## Required Metrics

Record these daily and for the full soak:

- Number of scans.
- Number of signals.
- Number of blocked stale-data signals.
- Number of executed paper trades.
- Win rate.
- Average R.
- Max drawdown.
- Average slippage estimate.
- Feed errors.
- Rejected executions.
- Engine A/B/C/D agreement rate.

Real-money automation remains blocked until the soak data, feed diagnostics, execution rejects, and account-risk logs are reviewed.
