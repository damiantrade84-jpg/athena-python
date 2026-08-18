# OPUS Engine

An intraday liquidity/auction trading engine. Self-contained: its own
indicators, scoring model, probability calibration, risk model, data adapters
and broker adapters, its own configuration (`opus/opus.yaml`) and its own
SQLite store (`opus_store.sqlite3`). It imports nothing from the rest of this
repository and shares no scoring surface, threshold, gate or execution
semantic with anything else here.

UI: **OPUS Engine** in the sidebar. API: `/api/opus/*`.

---

## The thesis

Intraday edge is not a pattern. It is the joint occurrence of seven things,
and the seventh is the one that usually decides the outcome:

| # | Condition | Measured by |
|---|-----------|-------------|
| 1 | A liquidity event — someone was forced to transact | **LDI** |
| 2 | An efficiency imprint — the move left a void, or was absorbed | **VFI**, **ARC** |
| 3 | A value dislocation — price is away from accepted value | **SAV** |
| 4 | Directional participation confirming the side | **OFP** |
| 5 | A regime where that setup actually pays | **RQS** |
| 6 | A time of day that carries follow-through | **TCI** |
| 7 | **A cost structure that leaves positive expectancy** | **CBI** |

Most intraday systems die on (7). OPUS makes it a hard gate.

---

## Indicators

All six directional indicators return a signed value in `[-1, 1]` (positive
supports long) plus a `confidence` in `[0, 1]` reporting how much data actually
backed the reading. Thin evidence is down-weighted rather than trusted.

**LDI — Liquidity Displacement Index.** When price traded through a level where
stops were resting, did it *stay* there or was it rejected? Those two outcomes
look identical to any oscillator and are opposite trades. LDI separates them by
*retention*: the fraction of the excursion beyond the level that the bar kept by
its close. Pools are weighted by plausible resting liquidity, so equal highs
score far above a lone pivot.

**ARC — Absorption / Rejection Curve.** Effort versus result. A bar that traded
huge volume across a wide range and closed where it started was *absorbed*.
Scores `effort × (1 − conversion) × expansion × close-location`, which isolates
the side that won.

**VFI — Void Field Inefficiency.** Three-bar imbalances treated as a decaying
scalar field rather than binary boxes: each void carries mass by size in sigma,
eroded by how much has traded back through and by age. Yields a directional
bias, a magnet vector, and the specific shelf price is retracing into.

**SAV — Session Anchored Value.** Per-session volume-weighted value with
developing bands. Near value, price drifts *with* value; stretched beyond the
outer band, it reverts. One continuous curve encodes both, so no regime flag is
needed to switch between them.

**KDI — Kinetic Drift Index.** Displacement multiplied by *fractal efficiency*
raised to a punishing exponent, so a move only counts to the extent it went
somewhere directly. Jump ratio (via bipower variation) separates discrete
institutional repricing from a continuous grind.

**OFP — Order Flow Pressure.** Signed flow: bar-derived everywhere, blended
with multi-level book depth imbalance where a live book exists. Divergence
between price extremes and cumulative delta is scored separately.

Two **conditioning** layers, which never create conviction and can only scale
or veto it:

**RQS — Regime Quadrant State.** Volatility (expanding/contracting) × structure
(balanced/imbalanced) → `TREND_DRIVE`, `COILED`, `VOLATILE_RANGE`, `DORMANT`.
Routes which archetypes may fire; a `0.0` multiplier disables one outright.

**TCI — Temporal Conviction Index.** Learns activity and follow-through per
time-of-day bucket from the instrument's own history. Estimates are shrunk
toward neutral by sample count, so a thin bucket cannot earn a large multiplier.

---

## Scoring

```
base        = Σ(wᵢ · confidenceᵢ · alignedᵢ) / Σ(wᵢ · confidenceᵢ)
coherence   = 1 − weighted_dispersion / dispersion_scale
conviction  = base · coherence^exponent · regime_gain · temporal_gain
```

Coherence is the point. A weighted sum lets one indicator at maximum drag a
signal past a threshold while everything else disagrees:

| Readings | Weighted mean | Coherence | Conviction |
|---|---|---|---|
| `{+0.6, +0.6, +0.6}` | 0.60 | high | ~0.60 |
| `{+1.0, +1.0, −0.2}` | 0.60 | low | ~0.35 |

Both score 0.60 additively. Only the first is a trade.

Both directions of every archetype are always scored. Choosing a direction from
a blended sign first would hide genuinely two-sided evidence — precisely when
standing aside is correct.

---

## Setups

Stops are **structural** — beyond the thing that would prove the idea wrong —
then bounded by `min_stop_sigma` / `max_stop_sigma`.

| Archetype | Entry | Stop | Target |
|---|---|---|---|
| `SWEEP_RECLAIM` | limit at the reclaimed pool | beyond the sweep wick | opposing pool, else session value |
| `DISPLACEMENT_CONTINUATION` | limit at the void midpoint | impulse origin, else void edge | opposing pool |
| `VALUE_FADE` | market at the band | beyond the excursion extreme | session VWAP |
| `VACUUM_BREAK` | stop beyond the coil | opposite side of the coil | measured move, capped at resting liquidity |

`min_stop_sigma` exists for an economic reason, not a cosmetic one: cost in R is
*inverse* to stop distance, so a stop inside the instrument's own noise hands
most of the risk budget to the spread before the idea can work.

---

## Probability and expectancy

Conviction is an ordinal score, not a probability. OPUS learns the mapping from
its own realised outcomes:

1. **Triple-barrier labelling** — every signal resolves at its target, its stop
   or a time limit. A bar containing *both* barriers resolves to the **stop**:
   intrabar sequence is unobservable, and assuming the target would inflate
   every measured hit rate exactly where it is least safe to assume.
2. **Shrunk logistic** — `P = σ(a·conviction + b)` per archetype × asset class,
   L2-regularised (separable small samples otherwise diverge to certainty) and
   shrunk toward a conservative prior by sample count. A bucket where higher
   conviction predicted *worse* outcomes falls back to the prior rather than
   inverting sizing.
3. **The decisive gate**:

```
cost_r = (spread + 2·commission + 2·slippage) / stop_distance
E[R]   = p · RR − (1 − p) − cost_r          # must clear min_expectancy_r
```

Both sides of the round trip are charged. A missing quote is charged the
slippage allowance, never treated as free.

**Validation.** Deflated Sharpe over stored outcomes corrects for selection
bias across the archetype × class grid — the maximum of N noise trials is large
by construction. Reported by default (`VALIDATION.enforce: false`); set
`enforce: true` to block unvalidated archetypes from promoting.

---

## Safety

- **Decision and readiness are separate.** `TRADE` says the idea is good;
  `READY` says it can be acted on now. A stale quote blocks the second without
  touching the first.
- **Gates fail closed.** A gate that cannot evaluate its input returns False.
  No quote means the spread and age gates *fail*, not pass.
- **Live needs two independent switches**: `MODE: live` in config **and** the
  `OPUS_LIVE_CONFIRM` environment variable. Neither arms live alone.
- **Synthetic data can never reach a live broker.**
- **Signals are re-validated at submit time**, re-derived from a fresh scan —
  never from a client-supplied payload.
- **Deduplication** on signal id (keyed to the trigger bar), plus portfolio
  caps: concurrent positions, per symbol, correlation group, cooldown, daily.
- Credentials come from the environment only (`OPUS_BYBIT_KEY`,
  `OPUS_BYBIT_SECRET`) and are never written to config or returned in a payload.

---

## API

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/opus/status` | engine identity, mode, universe, store stats |
| GET | `/api/opus/config` | resolved configuration (contains no secrets) |
| POST | `/api/opus/scan` | run a scan; `{symbols?, equity?, includeBlocked?}` |
| GET | `/api/opus/signals` | recently stored signals |
| POST | `/api/opus/execute` | submit one signal; `{signalId, symbol, live?, units?}` |
| GET | `/api/opus/orders` | order history |
| GET | `/api/opus/account` | broker account state |
| POST | `/api/opus/resolve` | label matured signals, refit calibration |
| GET | `/api/opus/calibration` | calibration and evidence per bucket |
| GET | `/api/opus/indicators/<symbol>` | full indicator detail for one symbol |

---

## Venues

| Venue | Data | Live broker |
|---|---|---|
| `mt5` | MetaTrader 5 (own handshake, suffix-tolerant symbol resolution) | yes |
| `bybit` | v5 public REST (kline / tickers / orderbook) | yes |
| `binance` | public REST | data only |
| `synthetic` | deterministic demo feed | never |

MT5 reports tick count rather than traded size for FX; that is flagged on the
candles so ARC and OFP discount it instead of treating a tick count as volume.

---

## Operating it

```bash
# run the test suite
./.venv/Scripts/python.exe -m pytest tests/opus/ -q
```

To try it without a broker, set every universe entry's `venue` to `synthetic`
in `opus/opus.local.yaml`.

The learning loop needs `POST /api/opus/resolve` to run periodically — until
signals are labelled, every probability is the prior. Signals are only resolved
once their full time barrier has elapsed, so a live trade is never mislabelled
as a time-barrier loss.

**Calibration is invalidated by model changes.** Changing indicator maths or
conviction weighting means bumping `SCORE_MODEL_VERSION` in `opus/version.py`;
stored samples scored under a different model are excluded from fits rather
than silently mixed.

Override anything in `opus/opus.local.yaml` (deep-merged, git-ignored) rather
than editing `opus.yaml`.
