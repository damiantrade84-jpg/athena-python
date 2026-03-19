# Sentinel Pro v4.0 — Claude Code Instructions

## Recent Changes (2026-03-17)

**Engine B (Naked Market Structure):**
- Added `market_structure.py` housing `NakedEngine` for pure price-action evaluation (no indicators).
- Engine looks at D1/H4 swings (HH/HL, LH/LL) and checks H1 for nearest structural Resistance/Support zones using wick/body clusters.
- Added `/api/scan-naked` to run Naked Engine against all active candidate pairs.
- Added `/api/naked-analysis` to run deep Engine B analysis on a single selected pair.

**Dashboard Performance & UI:**
- **Optimization A:** Throttled `run_full_scan` `ThreadPoolExecutor` from 6 workers to 3 to prevent CPU lock-ups.
- **Optimization B:** De-synced the Naked Scan execution loop by intentionally yielding checking blocks with `time.sleep(0.1)` so the Flask web thread remains responsive.
- **Optimization C:** Removed automatic `setTimeout` chart opening payloads from `index.html` after a scan finishes. Charts now Lazy-Load strictly on button clicks, preventing LightweightCharts from seizing the GPU/DOM.
- Added "NAKED GLOBAL SCAN" manual button to UI.
- Added "👁 NAKED SCALP" button to individual signal cards, producing a structural verdict window with a "⚡ QUICK SCALP EXECUTE" button that executes based on Engine B's proprietary SL/TP levels.

**Data Feeds & Infrastructure:**
- **Crypto Live Pricing Migration:** Moved all crypto live price feeds (REST poller and EODHD WebSocket) to a dedicated `BinanceLivePriceWS` thread consuming the `fstream.binance.com/ws/!ticker@arr` stream.
- **API Optimization:** Adjusted EODHD background price poller for non-WS pairs to 21-minute intervals (optimized for delayed stock/index data) grouped in 15-symbol batches.

## Previous Changes (2026-03-16)

**Testing Week Adjustments:**
- `MAX_OPEN_POSITIONS: 20` (was 5) — raised for testing with multiple open trades
- `MAX_CORRELATED_POSITIONS: 10` (was 2) — allow multiple correlated pairs during testing
- Revert both to 5 and 2 before live account activation

**Forex Session & Timezone Fixes:**
- Added `SERVER_TZ_OFFSET_HOURS: 2` config (for GMT+2/SAST) — derives UTC from local system time to fix Windows timezone misconfiguration
- Added Asian session window (00:00–08:00 UTC) to `forex_scoring.py` — forex now trades 24h (was London+NY only)
- Added `FOREX_SESSION_FILTER: true` config to disable session gate if needed

**Scoring & Auto-Trade Threshold Fixes:**
- `AUTO_TRADE_MIN_SCORE` changed from flat 0.75 to **per-class dict** (crypto: 0.80, forex: 0.65, stock: 0.85, commodity: 0.80, index: 0.80)
  - **Why:** factor engine scores 0–3.0, forex engine scores 0–1.0 — same 0.75 threshold was incompatible
- `MIN_CONFLUENCE_CLASS.forex: 0.70` → **0.60** (matches forex_scoring.py scale; 0.70 was over-filtering)
- `BT_MIN.forex: 0.60` kept in sync with MIN_CONFLUENCE_CLASS.forex
- Removed dead config `MIN_FOREX_CONFLUENCE: 0.60` and `MIN_FOREX_BREAKOUT: 0.50` — replaced with comments

**Dashboard Enhancements:**
- Added "Failed Executions" section in Performance tab (shows manual & auto rejections with error reasons)
- Failed manual executions now logged to audit_log with `grade="MANUAL-ERR"` and `error_tag=<reason>`
- New `/api/failed-executions` endpoint returns last 50 failed attempts

---

## ⚠️ DATA PROTECTION
audit.db and candle_cache.db contain all live trading history.
NEVER delete, overwrite, or zip these files during updates.
Run `python backup_db.py` before any major code changes.
Backups stored in db_backups/ folder — keep last 7 days automatically.
Do not touch any scoring, risk, or trading logic.

## Project Overview
Multi-asset algorithmic trading system: Flask dashboard, confluence-based signal engine, AI analysis (Claude claude-sonnet-4-6), live execution on MT5 (forex/stocks/commodities/indices) and Bybit Linear Futures (crypto LONG+SHORT).

---

## File Map

| File | Purpose | Size |
|------|---------|------|
| `athena.py` | Flask app, scan engine, all API routes, backtest | ~2200 lines — use offset/limit |
| `indicators.py` | Pure indicator functions (EMA, RSI, MACD, ATR, ADX, BB, Stochastic, Weinstein, Fib, OBV, Squeeze) | |
| `scoring.py` | Confluence engine, vote weights, session, pair profiles, signal classification | |
| `factor_scoring.py` | Z-score factor engine — directional (trend, momentum, microstructure, derivatives) + non-directional (trend_strength, volatility, volume, structure). Candle-based microstructure proxies for all asset types. | |
| `confidence_engine.py` | 4-component confidence scoring (indicator agreement, timeframe alignment, regime fit, liquidity) | |
| `feature_normalizer.py` | Rolling z-score, percentile rank, min-max normalization | |
| `config.py` | Hard-coded CONFIG defaults + YAML loader + validation | |
| `config.yaml` | All tunable thresholds — edit this, not config.py | |
| `risk_engine.py` | Risk gateway: kill switch, drawdown, position sizing, portfolio heat | |
| `mt5_executor.py` | MetaTrader 5 execution | |
| `bybit_executor.py` | Bybit Linear Futures execution | |
| `auto_trader.py` | Autonomous scheduler: scan every 30 min, auto-execute | |
| `ai_learning.py` | Outcome extraction → learning_log in audit.db; factor-level analysis for AI calibration | |
| `regime.py` | Market regime detection (TRENDING/DEVELOPING/RANGING/DEAD RANGING) | |
| `carry_feed.py` | Interest rate carry data — FRED static fallback, 1h cooldown on failure, non-blocking | |
| `cot_feed.py` | CFTC Commitment of Traders z-scores — non-blocking, cache-first during scans | |
| `duka_volume.py` | Dukascopy tick volume for forex — non-blocking during scans (returns 1.0 if cache not ready) | |
| `forex_scoring.py` | Dedicated forex scoring engine (rules-based, 0–1 scale) — trend gate + session filter + RSI pullback + COT boost | |
| `auto_trader.py` | Autonomous trade executor with per-class auto-trade thresholds (dict-based, per CONFIG) | |
| `static/index.html` | Dashboard UI: signals, backtest, screener tabs — pair selector populated dynamically from `/api/pairs` | ~1320 lines |
| `tests/test_athena.py` | Main test suite (56+ tests) | |
| `tests/test_factor_scoring.py` | Factor scoring unit tests | |
| `test_indicators.py` | Pure indicator unit tests (imports from `indicators` only) | |
| `audit.db` | SQLite runtime DB — **never commit** (gitignored) | |
| `candle_cache.db` | SQLite candle cache — **never commit** (gitignored) | |
| `*.db` | All `.db` files are gitignored — too large / runtime data | |

---

## Signal Flow (end-to-end)

```
run_full_scan(style, asset_class)
  └─ for each active pair (ThreadPoolExecutor, 6 workers)
       └─ analyze_pair(pair, btc_bias, style)
            ├─ fetch_candles(pair, "D1"/"H4"/"H1", limit)   ← TTL cache + live bypass for non-crypto
            ├─ calc_indicators(candles)                       ← returns {snap: {...}} dict
            ├─ calc_confluence(d1i, h4i, h1i, vr, stoch, pair, btc_bias, ...)
            │    ├─ get_pair_vote_weights(pair)               ← merges class weights + pair profile
            │    ├─ votes tallied → bull/bear score
            │    ├─ ranging/counter-trend penalties applied
            │    ├─ classify_signal_setup(direction, entry_mode, squeeze_bonus, atr_breakout, votes)
            │    └─ returns {score, direction, votes, warnings, signalClass, regime, maxScoreOverride}
            ├─ calc_levels(entry, atr, direction, ptype, regime_state)
            └─ returns full signal dict

  └─ _annotate_signal_for_scan(sig, pair, threshold, ...)
  └─ _classify_signal(sig, pair) → tier: "trade" | "watchlist" | "skip"
  └─ apply_correlation_cap(results)
  └─ _json_safe(result)                                       ← strips NaN/Inf before jsonify
```

**Execution path:**
```
api_execute()
  ├─ signal freshness check (SIGNAL_MAX_AGE_SEC)
  ├─ live re-analyze if stale → HTTP 409 if direction flipped
  ├─ price drift >1% → rebase SL/TP offsets
  ├─ _validate_exit_levels(direction, price, sl, tp)
  ├─ risk_check(signal, balance, equity, positions, symbol_info, ...)
  │    ├─ kill switch / drawdown stop check
  │    ├─ portfolio heat check (MAX_PORTFOLIO_HEAT)
  │    ├─ position sizing: base * score_factor * sizing_override * dd_factor
  │    └─ returns RiskApproval(approved, volume, risk_amount, risk_pct, reason)
  └─ mt5_execute(signal, approval) OR bybit_execute(signal, approval)
```

---

## Key Functions

### `calc_confluence(d1, h4, h1, vr, stoch, pair, btc_bias, ...)` — scoring.py
Signature (no `e200s` — removed intentionally):
```python
calc_confluence(d1: dict, h4: dict, h1: dict, vr: float, stoch: dict,
                pair: dict, btc_bias: str,
                d1_candles=None, h4_candles=None, h1_candles=None,
                funding_rate=None, volume_threshold=None, bar_time=None) -> dict
```
- Reads `get_pair_vote_weights(pair)` — merges class-level VOTE_WEIGHTS with pair profile overrides
- 12 vote slots: d1_trend(2.0), h1_ema(1.0), d1_adx(1.0), h4_macd(1.0), h4_oscillator(0.75–1.0), volume(0–1.0), funding(0–1.0), session(0–1.0), h4_fib(0.5–1.0), h1_bb(0.5–1.0), weinstein(0–1.0), divergence(1.0 bonus)
- Session vote adds W_SESS×0.5 to BOTH bull and bear — net directional max is 0.5, not full W_SESS
- `_base_max` subtracts `W_SESS×0.5` so confluencePct is not overstated
- Tie-break: `bull >= bear → LONG` (intentional long bias)
- Returns: `{score, votes, direction, bull, bear, spread, warnings, trendState, weinsteinStage, weinsteinLabel, entryMode, signalClass, regime, fundingRate, maxScoreOverride, adxMomentum, adxSlope}`

### `classify_signal_setup(direction, entry_mode, squeeze_bonus, atr_breakout, votes)` — scoring.py
Uses **structured boolean flags** — NOT string matching on warning text:
```python
# squeeze_bonus and atr_breakout are set at source inside calc_confluence
signal_class = classify_signal_setup(direction, _entry_mode,
                                     squeeze_bonus=_squeeze_bonus,
                                     atr_breakout=_atr_breakout,
                                     votes=v)
```
Returns: `"mean_reversion"` | `"breakout"` | `"trend_pullback"` | `"trend_continuation"`

### `get_pair_profile(pair)` — scoring.py
```python
profiles = CONFIG.get("PAIR_PROFILES", {})
return profiles.get(pair["display"]) or profiles.get(pair["symbol"]) or {}
```

### `get_pair_vote_weights(pair)` — scoring.py
Merges class-level VOTE_WEIGHTS with `disabled_votes` and `weight_overrides` from pair profile.

### `pair_filter_enabled(pair, filter_name)` — scoring.py
Returns `False` if `filter_name` in pair profile's `disable_filters` list.
Filter names: `weinstein`, `session`, `regime_transition`, `obv`, `funding`, `squeeze`, `mean_revert`, `btc_bias`, `divergence_warning`

### `_json_safe(value)` — config.py
Recursively replaces `float("nan")` / `float("inf")` / `float("-inf")` with `None`.
Applied to ALL scan, backtest, and analyze API responses before `jsonify()`.
Imported in `athena.py` via `from config import _json_safe`.

### `_build_event_risk(pair, ds_ctx, earnings_ctx, closed_exchanges)` — scoring.py
Builds `{hardBlock, reasons, earnings?, dividend?, split?}` risk dict for a pair.

### `_local_to_utc_hour()` — forex_scoring.py
Derives UTC hour from local system time + `SERVER_TZ_OFFSET_HOURS` config. Fixes Windows timezone misconfiguration where `datetime.now(timezone.utc)` returns wrong UTC. User sets offset to match their timezone (SAST = 2, UTC+0 = 0, etc.).

### `_classify_signal(signal, pair)` — scoring.py
Returns `(tier, reason)` where `tier` is `"trade"` | `"watchlist"` | `"skip"`.

### `_validate_exit_levels(direction, entry_price, sl, tp)` — bybit_executor.py
Returns error string or `None`. Called twice: before order placement and after fill.
Post-fill failure triggers emergency market close (1 retry on SL/TP set before emergency close).

### `_max_score_for_pair(pair)` — athena.py
Computes theoretical max score using `get_pair_vote_weights(pair)`, subtracts `W_SESS×0.5` to match `calc_confluence` accounting.

### `fetch_candles(pair, tf, limit)` — athena.py
- Non-crypto: tries `fetch_candles_live()` first; falls back to TTL cache
- Crypto: uses TTL cache only
- Cache TTL keys are **uppercase**: `"H1"`, `"H4"`, `"D1"` (not lowercase)
- TTL: H1=55 min, H4=3h55m, D1=23h
- Pairs with `"ws": False` use REST cache only — no live price tick; negligible latency impact for swing/intraday

### `_build_ticker_map(pairs)` — athena.py (EODHDWSManager)
Routes pairs to EODHD WS endpoints (`us`, `forex`). Skips any pair with `"ws": False`. Crypto pairs are entirely excluded from EODHD and handled natively by `BinanceLivePriceWS` without connection limits.
- EODHD plan cap: **50 tickers total** across all endpoints
- WS allocation (~36): us=17, forex=19 — see comment at `start()` for full ticker list
- Pairs with `"ws": False` still scan, backtest, and execute normally via REST cache
- To re-allocate WS slots: run backtests → query `backtest_results` by SQN → update `"ws"` flags in pair lists

### `/api/pairs` endpoint — athena.py
Returns ALL_PAIRS grouped by asset class for the frontend pair selector. Response: `{groups: {label: [{sym, label, enabled}]}, total, active}`. The backtest dropdown in index.html fetches this on page load — do NOT hardcode pair lists in HTML.

### `_can_execute(signal, cfg)` — auto_trader.py
Gate for auto-trade execution. Reads `AUTO_TRADE_MIN_SCORE` as a **per-class dict** from CONFIG:
```yaml
AUTO_TRADE_MIN_SCORE:
  crypto:    0.80   # factor engine (0–3 scale)
  stock:     0.85   # higher bar for single stocks
  commodity: 0.80
  index:     0.80
  forex:     0.65   # forex engine (0–1 scale)
```
Effective min = `max(AUTO_TRADE_MIN_SCORE[asset_type], MIN_CONFLUENCE_CLASS[asset_type])`. Falls back to crypto value if asset_type missing.
**Why per-class:** factor engine outputs 0–3.0; forex outputs 0–1.0 — same flat threshold was incompatible.

### `risk_check(signal, balance, equity, positions, symbol_info, ...)` — risk_engine.py
- Commodity tick defaults: tick=0.01, contract=100, tick_val=1.0 (e.g. gold: 0.01×100)
- Stock/crypto tick defaults: tick=0.01, contract=1, tick_val=0.01
- Forex tick defaults: tick=0.00001, tick_val=1.0
- MT5 `mt5_get_positions()` uses `symbol_info` tick_size/tick_value for accurate risk_amount (not hardcoded)

---

## Pair Profiles (config.yaml)

Per-pair overrides without touching class-level defaults:
```yaml
PAIR_PROFILES:
  XAU/USD:
    disable_filters: [obv, session]
    weight_overrides:
      h4_fib: 1.5
      h1_bb: 0.5
    min_confluence: 5.8
    bt_min: 4.6
  EUR/USD:
    disabled_votes: [volume]
    weight_overrides:
      session: 1.25
```
- `disabled_votes`: zeroes that vote's weight
- `weight_overrides`: sets specific vote weight
- `disable_filters`: disables named filter logic (weinstein warnings, OBV, squeeze detection, etc.)
- `min_confluence`, `bt_min`, `volume_threshold`: per-pair threshold overrides

Valid vote keys: `d1_trend, h1_ema, d1_adx, h4_macd, h4_oscillator, volume, funding, session, h4_fib, h1_bb, weinstein, divergence`
Valid filter keys: `weinstein, session, regime_transition, obv, funding, squeeze, mean_revert, btc_bias, divergence_warning`
`PAIR_PROFILE_VOTES` and `PAIR_PROFILE_FILTERS` constants defined in **config.py**, imported by scoring.py — single source of truth.

---

## Auto-Trader (auto_trader.py)

```
AutoTrader._scheduler_loop() — daemon thread, wakes every 30s
  └─ every AUTO_TRADE_SCAN_INTERVAL_MIN minutes:
       └─ _run_auto_scan()
            ├─ run_scan_fn(style="auto")          ← fresh scan, not cached
            ├─ logs: passed / watchlist / low_score / closed / inactive counts
            ├─ logs: best near-miss if no signals pass
            └─ for each signal in tradeSignals:
                 └─ _can_execute(signal, cfg)
                      ├─ score >= max(AUTO_TRADE_MIN_SCORE, MIN_CONFLUENCE_CLASS[type])
                      ├─ logs: pair, score/maxScore, min threshold, direction
                      └─ session filter (if AUTO_TRADE_SESSIONS configured)
                 └─ _execute_signal(signal, cfg)
                      ├─ risk_check() → RiskApproval
                      ├─ bybit_execute() or mt5_execute()
                      ├─ success → _write_audit() + trades_today++
                      └─ failure → _write_error(signal, error_tag)
```

**Config keys (config.yaml):**
```yaml
AUTO_TRADE_ENABLED: true           # boot-persistent (toggling in UI resets on restart without this)
AUTO_TRADE_MIN_SCORE: 5.5          # floored by MIN_CONFLUENCE_CLASS per asset type
AUTO_TRADE_MAX_DAILY: 3
AUTO_TRADE_MAX_PER_SCAN: 1
AUTO_TRADE_SCAN_INTERVAL_MIN: 30
```

**Diagnosing why no trades fired:**
```sql
SELECT pair, score, direction, error_tag, grade, ts
FROM audit_log WHERE grade LIKE 'AUTO%' ORDER BY ts DESC LIMIT 20;
```
`grade='AUTO-DEMO'` = successful demo trade. `grade='AUTO-ERR-DEMO'` = blocked — see `error_tag`.

---

## Bybit Executor (bybit_executor.py)

```
bybit_execute(signal, approval)
  ├─ _get_exchange()               ← singleton; calls load_time_difference() to fix retCode 10002
  ├─ fetch live price (ask/bid)
  ├─ rebase SL/TP if price drift >1% from signal price
  ├─ _validate_exit_levels()       ← pre-fill check, returns error string or None
  ├─ market order placement
  ├─ post-fill _validate_exit_levels() → emergency close if invalid (no retry here)
  ├─ _set_trading_stop()           ← 2 attempts (1 retry, 2s sleep) before emergency close
  └─ returns {success, ticket, volume, entryPrice, direction, sl, tp, ...}
```
- `adjustForTimeDifference: True` and `recvWindow: 10000` set in exchange options
- Emergency close uses `reduceOnly: True, positionIdx: 0`

---

## Backtest (athena.py — backtest_pair)

- Swing: walks D1 bars, max hold 20 bars → TIMEOUT if neither SL nor TP hit
- Intraday: walks H4 bars, max hold 12 bars → TIMEOUT if not hit
- TIMEOUT is force-closed at last forward bar close, P&L calculated as actual R-multiple (capped ±5R) and labelled TIMEOUT (not a loss)
- `open_positions` counter gates `MAX_OPEN=3` concurrent positions — incremented on entry, decremented on exit (not on OPEN/TIMEOUT)
- `bt_min` per class (config.yaml `BT_MIN`), overridable per pair via `PAIR_PROFILES`
- No `e200s` computed in backtest loops — variable was dead after `calc_confluence` signature change

---

## Scan Funnel (what the numbers mean)

```
{'total': 96, 'active': 28, 'inactive_pair': 68, 'closed_exchange': 15,
 'low_score': 61, 'passed': 1, 'watchlist': 16, 'dead_ranging': 4}
```
- `total` = ALL_PAIRS count
- `active` = enabled pairs scanned
- `inactive_pair` = total - active (disabled, never scored)
- `closed_exchange` = pairs with open exchange flagged closed at scan time (JSE / US pre-open)
- `low_score` = diagnostic code count (NOT unique pairs — one pair can have multiple codes)
- `passed` = signals in `tradeSignals` (tier="trade"), ready for auto-execution
- `watchlist` = near-miss signals (score within 1.0 of threshold, not DEAD RANGING)

---

## Audit DB Schema (audit.db)

### `audit_log` table
Key columns: `ts, pair, score, direction, trend, grade, edge_prob, risk, style, asset_class, score_pct, max_score, votes_json, warnings_json, weinstein, trend_state, adx_pct, btc_bias, session_name, regime, entry_price, sl, tp, volume, risk_amount, risk_pct, ticket, exit_price, exit_time, pnl, r_multiple, exit_reason, holding_period_hours, error_tag, fee_cost, factors_json`

`fee_cost` (REAL) — actual exchange commission paid, captured from `bybit_execute()` → `order["fee"]["cost"]`. NULL for MT5 trades (not available via API).

`factors_json` (TEXT) — JSON blob `{scores, weights, disabled, regime}` from `compute_factor_scores()`. Written on every AI analysis, execution, and webhook entry. Used by `ai_learning.py` to analyze factor reliability across winning/losing trades.

### `backtest_results` table
Populated after every `backtest_pair()` run. Columns: `id, run_date, pair, asset_type, engine, trades, win_rate, profit_factor, expectancy, sqn, sharpe, sortino, is_score, oos_score, max_dd_pct, bt_min, atr_source, notes`

- `engine`: `"forex_scoring"` for forex pairs, `"factor_scoring"` for all others
- `atr_source`: `"D1_ATR"` for non-crypto, `"H4_ATR"` for crypto
- Index: `idx_bt_pair (pair, run_date)`

**Backtest history API endpoints:**
- `GET /api/backtest-history` — last 500 results, newest first
- `GET /api/backtest-history/<pair_name>` — last 50 for a specific pair (URL-encode slashes: `XAU%2FUSD`)
- `GET /api/backtest-best` — most recent run per pair, ordered by SQN desc

Schema auto-migrated on startup — adding a new column: add it to both `CREATE TABLE` and the migration list in `_init_audit_db()`.

**Failed Execution Logging (as of 2026-03-16):**
- Manual executions rejected by `risk_check()` now log to `audit_log` with `grade="MANUAL-ERR"` and `error_tag=<reason>`
- Auto-trade failures already logged with `grade="AUTO-ERR-*"`
- Query failed executions: `WHERE grade LIKE '%-ERR%'` — shows both manual and auto rejections with reason
- New `/api/failed-executions` endpoint returns last 50 failed attempts
- Dashboard "Performance" tab now shows "Failed Executions" section with time, pair, direction, score, source, and reason

---

## Python Environment

- **Python 3.14.3** — always use `py` (not `python` or `python3`); on Linux use `python3`
- **Run tests**: `py -m pytest tests/ test_indicators.py -v` (Windows) / `python3 -m pytest tests/ test_indicators.py -v` (Linux)
- **Platform**: Windows 11, bash shell (dev); Linux also supported

---

## Claude Code Usage

This project uses **Claude Code** (CLI) as the primary AI coding assistant. To start a session:

```bash
# From the project directory:
claude
```

**Preferred workflow:**
- Use Claude Code CLI (not Windsurf or other IDEs) for all code changes — context is preserved across sessions via `CLAUDE.md` and the memory system at `~/.claude/projects/`
- To paste long logs or output: use a file (`log.txt`) and ask Claude to read it, rather than pasting directly
- To share a server log: save to a temp file and say "read log.txt"
- Claude Code sessions auto-summarize context so long projects are not lost

**Key commands:**
- `/help` — list all slash commands
- `/model` — switch model (Opus 4.6 for complex tasks, Sonnet 4.6 for speed)
- `/clear` — clear context window (does NOT lose CLAUDE.md memory)
- `Ctrl+C` — interrupt a running tool without exiting

---

## Hard Rules

1. Never bypass `risk_check()` for any execution
2. Never hardcode thresholds in Python — use `config.yaml`
3. Never import from `athena.py` in unit tests — use `indicators` or `scoring` directly
4. Never commit `*.db` files — all SQLite databases are gitignored (runtime/market data, too large)
5. Never add `e200s` back to `calc_confluence()` — it was intentionally removed
6. Never use string matching on warning text for classification — use structured flags
7. `PAIR_PROFILE_VOTES` / `PAIR_PROFILE_FILTERS` live in `config.py` only — do not redefine in scoring.py
8. Cache TTL dict keys are uppercase `"H1"/"H4"/"D1"` — lowercase misses the cache
9. `ALL_PAIRS = FOREX_PAIRS + COMMODITY_PAIRS + INDEX_PAIRS + US_STOCK_PAIRS + ETF_PAIRS + JSE_PAIRS + CRYPTO_PAIRS` — JSE_PAIRS must stay in this concatenation
10. Never hardcode pair counts or pair lists in `static/index.html` — the backtest selector fetches `/api/pairs` dynamically
11. `CandleBuilder.seed()` and `bulk_update_d1()` skip `enabled:False` pairs — do not remove these checks
12. `_resolve_scan_style(requested_style, pair)` — use this for per-pair style resolution in `run_full_scan()`; do not rename or replace with ad-hoc functions
13. Non-blocking I/O during scans: `carry_feed`, `cot_feed`, `duka_volume` must never block the scan thread — return cached/neutral values if data not ready
14. Always use `timeout=1.0` and `PRAGMA journal_mode=WAL` for `sqlite3.connect` to avoid `database is locked` concurrency errors
15. `"ws": False` on a pair dict opts it out of WS subscription only — scan/backtest/execute are unaffected. Default is `True` (backward-compatible). EODHD plan cap is 50 tickers total; do not add `ws:True` pairs without removing others first
16. BybitWS (`athena/datafeeds/bybit_ws.py`): `ping_interval=None` in `websockets.connect()` is required — disables library-level keepalive and lets app-level `{"op":"ping"}` handling prevent 1011 keepalive errors
