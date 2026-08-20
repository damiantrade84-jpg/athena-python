# KIMI Engine

Standalone intraday quant engine — proprietary indicator suite, KIMI Score
composite, risk-managed paper execution, optional Bybit adapter, live terminal UI.

Multi-asset: crypto (Binance/Bybit) plus anything the attached MT5 terminal
offers — forex, metals, indices, energy (broker suffixes like `.s` resolve
automatically). Yahoo Finance is the labeled last-resort fallback.

## Run

```bash
cd kimi_engine
python server.py            # http://127.0.0.1:7100
# or
npm run dev                 # same thing, Kimi Work preview entry
```

## What is inside

| Layer | File | Contents |
|---|---|---|
| Data | `kimi/datafeed.py` | Bybit-first crypto live quotes, Binance crypto candles/fallback quotes, MT5 terminal attach (suffix resolver, tick + D1 change), Yahoo fallback, TTL caches |
| Indicators | `kimi/indicators.py` | Tide (trend), Pulse (momentum), Pressure (signed relvol), Flow (taker imbalance), Vol regime, session VWAP |
| Structure | `kimi/structure.py` | swings, liquidity sweeps, displacements, FVGs |
| Scoring | `kimi/scoring.py` | KIMI Score 0–100, 8 weighted components, grades A+/A/B |
| Signals | `kimi/signals.py` | multi-TF fuse (1h bias / 15m context / 5m entry), SL from swept structure, TP1/TP2 at R multiples |
| Risk | `kimi/risk.py` | 1% fixed-fractional sizing, max positions, daily-loss limit, kill switch — fail closed |
| Broker | `kimi/broker.py` | PaperBroker (fees+slippage, TP1 partial, BE move, persisted) + Bybit v5 adapter |
| Engine | `kimi/engine.py` | scan loop, cooldowns, signal lifecycle, execution routing, entry-TF freshness gate |
| Server | `server.py` | stdlib HTTP + SSE, no dependencies |
| UI | `web/` | dark terminal dashboard, candle chart with sweeps/signal markers |

## Data rules

- Crypto (`*USDT`) live prices come from Bybit linear tickers, with an explicitly labeled Binance fallback. Crypto candles retain Binance-first routing for taker-flow scoring, with Bybit fallback.
- Everything else resolves through the running MetaTrader 5 terminal first,
  then Yahoo. MT5 volume is tick volume; taker-flow scores neutral there.
- The ticker batch is split by asset class — a non-Binance symbol can never
  fail the crypto batch; failed symbols keep their last-known tick.
- MT5 bar times are normalized from the broker clock (ATFX = UTC+3) to UTC —
  inferred from the live tick clock, `KIMI_MT5_BROKER_UTC_OFFSET` fallback.
  Without the shift, weekend/future bars would fool every freshness gate.
- Freshness gate: entry-TF candles older than 2 bars (or future-dated) still
  score in the UI but never emit executable signals.

## Execution modes

- **Host pipeline (default when available; `venue=host`/`auto`)** — routes the
  signal through the exact path the other engines use: canonical candle
  freshness evidence → demo gate → `guardian.pre_trade_check` →
  `risk_engine.risk_check` (host sizing owns volume) →
  `execution_lifecycle.run_managed_execution` → `mt5_execute` (MT5 symbols)
  or `bybit_execute` (USDT crypto). On-broker SL/TP with post-fill reconcile;
  the position is then tracked/closed from the main Athena dashboard or MT5.
  Demo-first: the MT5 account must report trade-mode demo and Bybit must be
  demo/testnet — real accounts need `KIMI_HOST_REAL_ORDERS=1` explicitly.
  The host modules must import cleanly (the repo config must pass its own
  boot safety validation — set `ATHENA_REAL_ORDERS_CONFIRM` when the host
  config requires it). Any failure → unavailable, engine stays paper.
  Disable with `KIMI_HOST_EXEC=0`.
- **Paper (fallback / explicit `venue=paper`)** — deterministic fills at
  market ± slippage, taker fees, TP1 50% partial with breakeven move,
  TP2/SL exits, equity curve persisted to `state/kimi_state.json`.
  Asset-agnostic: works for crypto and MT5 symbols.
- **Native Bybit adapter (`venue=bybit`, crypto USDT only)** — active when
  `KIMI_LIVE_ENABLED=1` and `KIMI_BYBIT_KEY`/`KIMI_BYBIT_SECRET` are set
  (`BYBIT_API_KEY`/`BYBIT_API_SECRET` accepted as aliases). `KIMI_BYBIT_ENV`
  selects `demo` (default), `testnet`, or `live`.

## Config (env)

`KIMI_SYMBOLS`, `KIMI_SIGNAL_MIN_SCORE`, `KIMI_RISK_PER_TRADE_PCT`,
`KIMI_MAX_POSITIONS`, `KIMI_MAX_DAILY_LOSS_PCT`, `KIMI_START_EQUITY`,
`KIMI_PORT`, … see `kimi/config.py`.

## Tests

```bash
python -m pytest tests/ -q        # unit checks (mocked MT5, no terminal needed)
python tests/e2e_check.py         # live data + HTTP API check
python tests/execute_check.py     # scan → execute → mark → close lifecycle
python tests/athena_bridge_check.py  # embedded /kimi routes on a bare Flask app
```
