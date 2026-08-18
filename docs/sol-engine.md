# SOL Engine

SOL is a standalone, deterministic intraday trading system. Its name expands to
**Session-Oriented Liquidity**. It uses venue-native closed candles, produces an
auditable 100-point score, and keeps setup quality separate from execution
permission.

SOL does not claim a profitable edge merely because the implementation, replay,
or broker bridge works. The checked-in research state is `UNVALIDATED`; paper and
attested demo operation are available, while real-money execution is disabled by
default.

## Research basis

The design uses a small number of robust, causally computable ideas rather than a
large indicator search:

- Intraday volatility and liquidity are strongly periodic, so volatility must be
  interpreted relative to its recent local distribution rather than through one
  fixed price-distance threshold. See Andersen and Bollerslev,
  [Intraday Periodicity and Volatility Persistence in Financial Markets](https://doi.org/10.1016/S0927-5398(97)00004-2).
- Short-horizon continuation and reversal can coexist because temporary liquidity
  imbalances and bid-ask effects operate on different horizons. See Heston,
  Korajczyk, and Sadka,
  [Intraday Patterns in the Cross-section of Stock Returns](https://doi.org/10.1111/j.1540-6261.2010.01573.x).
- Configuration search can manufacture impressive historical results. SOL therefore
  ships fixed parameters, reports insufficient samples, uses closed-prefix replay,
  and resolves ambiguous same-bar stop/target hits against the strategy. See Bailey,
  Borwein, López de Prado, and Zhu,
  [The Effects of Backtest Overfitting on Out-of-Sample Performance](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659).

The research above motivates the measurement and validation discipline. It does
not prove that SOL's exact rules have positive expectancy.

## Venue and candle contract

SOL prices each instrument from its execution venue:

- Non-crypto instruments: MetaTrader 5 terminal candles and ticks.
- Crypto perpetuals: Bybit V5 linear-market candles and top-of-book ticker.

MetaTrader documents bar index zero as the current bar in
[`copy_rates_from_pos`](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrompos_py).
Bybit documents that the close of an unclosed kline is only the latest traded price
in [Get Kline](https://bybit-exchange.github.io/docs/v5/market/kline). SOL therefore
removes an explicitly unconfirmed row and independently rejects any bar whose
scheduled close is after the point-in-time evaluation clock.

A scan freezes one causal evaluation clock across every instrument. If a fetch
crosses the next bucket boundary, the newly opened post-snapshot bar is dropped
and counted separately; only a timestamp beyond the actual receipt clock is
classified as corrupt future data.

Each series is normalized and checked for:

- finite, positive, internally consistent OHLC values;
- duplicate timestamps;
- future and forming bars;
- minimum history;
- last-closed-bar freshness; and
- explicit venue, timeframe, bar-count, and volume-source provenance.

Malformed, missing, stale, future, or ambiguous data fails closed.

## Intraday stack

| Role | Timeframe | Purpose |
|---|---:|---|
| Context | H4 | Robust directional orbit and path persistence |
| Value | H1 | Pullback/extension location around robust equilibrium |
| Setup | M15 | Liquidity event and invalidation geometry |
| Trigger | M5 | Closed-bar displacement and micro-break confirmation |

The role timeframes are distinct and configuration-validated. A faster candle does
not replace a missing slower layer.

## Native indicators

### Robust price orbit

The orbit center is the median typical price over the configured lookback. Its
radius is `1.4826 × MAD`, floored at `0.10 × ATR`. The current location is:

```text
orbit_z = (close - median_typical_price) / max(1.4826 * MAD, 0.10 * ATR)
```

Direction is the sign of the change between two adjacent median windows,
normalized by ATR. Orbit quality combines absolute normalized slope (68%) with
path efficiency (32%).

### Liquidity event

SOL recognizes exactly two event families:

1. `SWEEP_RECLAIM`: price trades beyond a prior 20-bar extreme, closes back through
   it, has a qualifying rejection wick, and remains valid through the latest closed
   setup bar.
2. `COMPRESSION_RELEASE`: six-bar median true range contracts relative to the prior
   30-bar distribution, then a body-efficient close breaks the preceding 12-bar
   range.

The event strength records excursion, wick or body efficiency, compression ratio,
break distance, event age, boundary, and invalidation extreme.

### Displacement pulse

The trigger pulse measures signed three-bar net movement divided by summed true
range, latest-bar body efficiency, and a six-bar micro-break. The direction and
minimum strength must agree with the context direction.

### Participation impulse

Participation compares the median log volume of the latest three bars with a robust
30-bar baseline. MT5 tick volume is treated only as within-series activity; it is
never described as centralized traded volume. Missing volume contributes zero
points and is labelled unavailable.

## Score and hard gates

| Component | Points |
|---|---:|
| Regime orbit | 20 |
| Value location | 15 |
| Liquidity event | 25 |
| Displacement | 20 |
| Participation | 10 |
| Execution geometry | 10 |

Default classification is:

- `READY`: score at least 74 and every deterministic gate passes;
- `WATCH`: score at least 58 but one or more setup gates remain incomplete; or
- `BLOCKED`: inadequate score, invalid data, or invalid geometry.

Score cannot override these gates:

- all four closed series are present and fresh;
- H4 direction resolves;
- H1 is not overextended and has no strong opposite orbit;
- an aligned M15 liquidity event exists;
- the M5 displacement pulse is aligned and strong enough; and
- stop, target, ATR distance, and minimum live risk/reward are valid.

The executable identity is tied to the originating M15 liquidity event rather
than each later M5 refresh. A setup can mature from WATCH to READY, but a pending
or successful execution cannot be duplicated merely because the trigger remains
aligned on the next closed bar.

## Levels

The entry reference is the last closed M5 price. The stop sits beyond the setup
event and recent M15 structure with an ATR buffer. Stop width must remain within the
configured ATR interval. The target begins at fixed risk multiple and is pulled in
front of the nearest opposing M15 liquidity when one exists. If that wall reduces
live risk/reward below the configured minimum, the setup is not executable.

Stops and targets are immutable during broker attestation. SOL never parallel-shifts
structural levels to make a moved quote fit.

## Execution lifecycle

Execution is a separate state transition:

```text
persisted READY signal
  -> reserve idempotency key
  -> fresh executable bid/ask
  -> quote age, spread, drift, and live-RR gates
  -> kill switch, account, positions, guardian, and risk approval
  -> paper ledger or venue order
  -> immutable execution result
```

Paper mode records a simulated fill and sends no order. Demo mode requires an
attested demo account. Bybit's demo environment is an isolated account and supports
order placement, as documented in
[Bybit Demo Trading Service](https://bybit-exchange.github.io/docs/v5/demo).
Bybit also notes that market orders are protected through IOC price limits and may
cancel when liquidity is insufficient in
[Place Order](https://bybit-exchange.github.io/docs/v5/order/create-order).

Real-money mode requires all of the following:

- SOL `live_enabled: true`;
- research status `VALIDATED` when the validation gate is enabled;
- the application-wide live and real-order controls;
- the server-side real-order confirmation environment value;
- a real-account attestation; and
- every quote, guardian, risk, sizing, and broker gate.

Real-money mode is disabled in the checked-in configuration.

## Causal replay

The dashboard can run a bounded single-instrument diagnostic replay. At every M5
decision time, each higher timeframe is sliced to bars whose scheduled close is no
later than that time. Outcomes use future M5 bars only after the signal. When a bar
touches both stop and target, the stop is assigned first.

This replay is a diagnostic, not promotion evidence. Before changing research
status to `VALIDATED`, run a documented basket study across market groups with:

- frozen venue provenance and trading-cost assumptions;
- chronological train, validation, and untouched holdout periods;
- enough signals per group and direction;
- spread, slippage, fees, swaps/funding, and rejected-order modeling;
- parameter-trial accounting and multiple-testing correction; and
- paper/demo forward observation through different sessions and volatility regimes.

## API and UI

The deployed panel is available from **SOL Engine** in the application sidebar.
It exposes:

- asynchronous all-group or selected-group scans;
- score attribution and exact gate status;
- candle provenance and closed-bar timestamps;
- quote preview/attestation;
- paper, demo, and gated live execution choices;
- causal replay diagnostics; and
- execution history.

API routes use the `/api/sol/` namespace:

```text
GET  /api/sol/health
GET  /api/sol/config
POST /api/sol/scan
GET  /api/sol/scan/current
GET  /api/sol/signals
GET  /api/sol/signals/<signal_id>
POST /api/sol/signals/<signal_id>/preview
POST /api/sol/signals/<signal_id>/execute
GET  /api/sol/executions
POST /api/sol/replay
```
