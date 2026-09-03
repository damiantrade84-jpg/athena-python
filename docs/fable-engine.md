# FABLE Engine

FABLE is a standalone, deterministic intraday trading engine. Its name is the
whole idea: every market is read as a **story told by liquidity**, and the
engine only acts when the story is coherent from beginning to end.

FABLE shares no scoring, indicators, thresholds or timeframe roles with Engine
A, Engine B, Engine D, ASE, SOL, GROK, OPUS, KIMI or OX Alpha. It consumes the
shared venue candles (MT5 terminal for broker instruments, Bybit V5 linear for
crypto), the shared live quote sources, the shared advisory feeds (carry, COT,
volatility skew, Bybit funding, event risk) and the shared demo-gated execution
chain (`guardian.pre_trade_check` -> `risk_engine.risk_check` -> MT5/Bybit
executor). Nothing else is borrowed.

The checked-in research status is `UNVALIDATED`. Paper and attested demo
execution are available; live execution stays locked until the status is
`VALIDATED` and the server confirmation token is present.

## The five acts

| Act | Name | Timeframe | What it measures |
| --- | --- | --- | --- |
| I | **Draw** | D1 / H4 | Dealing range and premium/discount, H4 swing-sequence bias, trend efficiency, the external liquidity pool price is drawn towards |
| II | **Raid** | M15 vs H1/H4/session pools | A sweep through a resting pool that closed back inside: depth, reclaim strength, pool strength, recency, participation |
| III | **Shift** | M15 | Displacement after the raid that closes through the pre-raid swing: leg travel, largest body, imbalance left behind, speed |
| IV | **Return** | M15 | Price coming back into the fair value gap (or order block) inside the optimal-trade-entry band of the displacement leg |
| V | **Chorus** | mixed | Session window, volatility regime, participation, carry, positioning, vol skew, funding: advisory voices that agree or dissent |

Pools are built from H4 and H1 fractal swings (equal highs/lows merged into
stronger pools), previous-day and previous-week highs/lows measured on the New
York calendar, then de-duplicated. A raid may take up to three bars; a sweep
deeper than 2.5 ATR is a breakout, not a raid. A shift is invalidated the moment
a close returns beyond the raid extreme.

## Coherence

Each act yields a quality in `[0, 1]`. The acts are fused with a **weighted
geometric mean** (default weights 22 / 20 / 22 / 18 / 18) onto a 0–100
*coherence* score. Because the mean is geometric, one weak act drags the whole
story down instead of being averaged away; the `quality_floor` keeps the log
defined.

Tiers grade coherence for display only: `LEGEND >= 80`, `SAGA >= 64`,
`TALE >= 50`, otherwise `SKETCH`.

## Decisions and gates

Deterministic gates decide whether the story is tellable at all: closed-bar
data and freshness per timeframe, ATR sanity, event blackout (when the shared
event-risk feed is enabled), the institutional session window (forex, index and
commodity only), stop geometry (0.35–3.5 ATR beyond the raid extreme, wider caps
for crypto, commodities and indices) and reward geometry (RR >= 1.5 to the leg
high, the next pool, or the external draw). While the return is still pending
the plan is measured from the imbalance edge price would enter at, not from the
current price.

| Decision | Meaning |
| --- | --- |
| `EXECUTE` | price is inside the imbalance, every gate passed, coherence >= 64 |
| `STAGE` | the narrative is complete but price has not returned yet (potential >= 64), or coherence sits between 50 and 64 |
| `OBSERVE` | no raid, no shift, no imbalance, the return failed, or coherence < 50 |
| `VOID` | a gate failed; the payload carries `voidReasons` |

Only `EXECUTE` can be sealed. The signal id is a hash of the raid and shift
bars, so the same narrative keeps one id across re-scans and the execution
ledger allows one live reservation per narrative.

## Execution ("sealing")

`POST /api/fable/signals/<id>/preview` attests the live quote: contract and
decision, signal age, narrative-bar age (<= 2 M15 buckets), kill switch, venue
and direction, quote integrity and age, spread in bps, drift against the scan
close in ATR, and live geometry. The stop is immutable; if the live entry no
longer clears the minimum RR to the first target, the external draw (`target2`)
is the only permitted fallback.

`POST /api/fable/signals/<id>/execute` with `{mode, idempotencyKey}`:

- `paper` records a synthetic fill inside `fable_engine.db`; nothing reaches a broker.
- `demo` attests a demo account, then runs `guardian.pre_trade_check`,
  `risk_engine.risk_check(execution_context="fable_engine")`, verifies the
  immutable levels survived, downsizes to `risk_fraction` (0.25 % of equity by
  default, capped at 1 %) and sends through `mt5_execute` / `bybit_execute`.
- `live` requires `live_enabled`, `EXECUTOR_MODE=live`, `REAL_ORDERS_ALLOWED`,
  `research_status=VALIDATED` and `ATHENA_REAL_ORDERS_CONFIRM`.

`follow_global_executor_mode: true` makes demo the default whenever
`EXECUTOR_MODE` is `demo`. Successful broker fills are read by
`engine_attribution.py` from `fable_executions`, so the Trades panel labels
FABLE positions without an `audit_log` row (and therefore without the
timed-exit monitor adopting them).

## Surfaces

| Route | Purpose |
| --- | --- |
| `GET /api/fable/health` | engine state, session clock, window schedule, thresholds, capabilities |
| `GET /api/fable/accounts` | redacted MT5 / Bybit account state |
| `GET /api/fable/config` | merged configuration |
| `GET /api/fable/universe` | enabled pairs and their venue |
| `POST /api/fable/scan` | start a scan (`assetTypes`, `symbols`) |
| `GET /api/fable/scan/current` | scan progress and tallies |
| `GET /api/fable/signals` | latest completed scan (`decisions`, `asset_types`, `limit`) |
| `GET /api/fable/signals/<id>` | one story |
| `GET /api/fable/signals/<id>/chart` | fresh closed M15 candles plus pools, raid, shift, imbalance and levels |
| `POST /api/fable/signals/<id>/preview` | live quote attestation |
| `POST /api/fable/signals/<id>/execute` | seal the story |
| `GET /api/fable/executions` | the ledger |
| `GET /api/fable/positions` | open broker positions claimed by FABLE fills |
| `POST /api/fable/chronicle` | causal closed-prefix replay for one symbol |

The React panel (`FableEnginePanel.tsx`, styles in `styles/fable.css`, all
classes prefixed `fbl-`) is the *codex*: a frontispiece with the session
ribbon, the story cards with a coherence ring and five-act glyph strip, the
manuscript with the narrative paragraph, the acts and their evidence, levels,
the M15 story chart, the seal controls, the ledger, open stories and the
chronicle.

## Configuration

Defaults live in `fable_engine/defaults.yaml`; the root `FABLE_ENGINE` mapping
in `config.yaml` / `config.local.yaml` is deep-merged on top and validated at
load time (weights must sum to 100, risk fraction <= 1 %, live requires
validated research, and so on).

## Chronicle

`POST /api/fable/chronicle` walks the narrative series one closed bar at a time,
evaluates the same scorer on each prefix, fills every `EXECUTE` at the next
bar's open and resolves a bar that touches both stop and target as a loss. A
sample below `minimum_trades_for_evidence` (30) is reported as
`INSUFFICIENT_SAMPLE`. The chronicle checks the implementation; it is not proof
of edge.

## Tests

`tests/test_fable_engine.py` covers configuration, closed-bar normalisation,
swings, gaps, raid and shift detection, coherence, the full story fixture
(EXECUTE with levels, STAGE without the return, VOID on stale data, event
blackout and session window), the chronicle, the execution coordinator (quote
attestation, spread, staleness, drift, kill switch, paper idempotency, demo
attestation, guardian and risk routing, live lock) and the Flask routes. Run it
with:

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_fable_engine.py -q -p no:cacheprovider
```
