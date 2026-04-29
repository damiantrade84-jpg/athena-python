# Sentinel Pro v4.0 — Full Factual Audit Report

**Date:** 2025-01-XX
**Auditor:** Cascade (AI), using only cloned reference repos as source of truth
**References used:**
1. `freqtrade_sample_strategy.py` (freqtrade — strategies, indicators, entry/exit)
2. `refs/jesse/jesse/strategies/Strategy.py` (jesse — signal conditions, risk helpers)
3. `refs/vectorbt/tests/test_portfolio.py` (vectorbt — backtesting with fees + slippage)
4. `refs/backtrader/samples/commission-schemes/commission-schemes.py` (backtrader — commissions, live mechanics)

**Scope:** Every `.py` file in the Athena workspace — indicators, scoring, risk engine, executors (MT5 + Bybit), backtesting, auto-trader, config.

---

## ISSUE #1 — CRITICAL: Intraday Backtest Passes Wrong Timeframe to Confluence

### Buggy Code
`athena.py` lines 2068–2075 (intraday backtest loop):
```python
h4i = calc_indicators(h4_window)
h1i = calc_indicators(h1_window)
d1i_ctx = calc_indicators(d1_ctx)        # ← D1 indicators ARE computed
# ...
res = calc_confluence(h4i, h1i, h1i, vr, stoch, pair, "neutral",
                       d1_candles=d1_ctx, h4_candles=h4_window, h1_candles=h1_window,
                       ...)
```

### What's Wrong
`calc_confluence(d1, h4, h1, ...)` expects D1 indicators as the **first** argument. The intraday loop passes `h4i` (H4 indicators) in the D1 slot, and `h1i` in both the H4 and H1 slots. `d1i_ctx` is computed on line 2071 but **never used**.

### Reference Pattern
In `analyze_pair()` (athena.py line 3506), the live path correctly passes:
```python
res = calc_confluence(_cf_d1i, _cf_h4i, _cf_h1i, vr, stoch, ...)
```
The scalp backtest loop (line 2198) also correctly passes `d1i_ctx` as the first arg.

### Consequence
The **D1 Trend Gate** (worth 2.0 points — the heaviest vote) evaluates H4 EMA21/EMA50/EMA200 instead of D1 EMAs. H4 EMAs catch trends faster than D1, so the backtest is **more permissive** than live — it will approve trend-following trades that the live scanner would reject. **Backtest results are optimistically biased and do not match live behavior.**

### Fix
```python
# athena.py line 2075 — change h4i to d1i_ctx, and h1i,h1i to h4i,h1i:
res = calc_confluence(d1i_ctx, h4i, h1i, vr, stoch, pair, "neutral",
                       d1_candles=d1_ctx, h4_candles=h4_window, h1_candles=h1_window,
                       volume_threshold=get_pair_profile(pair).get(
                           "volume_threshold",
                           CONFIG.get("VOLUME_THRESHOLD_BACKTEST", CONFIG["VOLUME_THRESHOLD"])
                       ),
                       bar_time=h4_window[-1].get("time") if h4_window else None)
```

---

## ISSUE #2 — MODERATE: Bollinger Band Uses Population Std Dev (N) Instead of Sample (N-1)

### Buggy Code
`indicators.py` line 130:
```python
sd = math.sqrt(sum((x - mn) ** 2 for x in sl) / p)
```
Same pattern at lines 157, 170, 357 (in `calc_squeeze`, `calc_bb_width_percentile`).

### Reference Pattern
Freqtrade (`freqtrade_sample_strategy.py` line 243):
```python
bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2)
```
`qtpylib.bollinger_bands()` uses `pandas.rolling().std()` which defaults to **ddof=1** (sample standard deviation, divides by N-1). TA-Lib's `BBANDS` also uses sample std (N-1). This is the universal standard for Bollinger Bands in trading.

### Consequence
Population std (÷ N) produces **narrower bands** than sample std (÷ N-1). With period=20, the difference is ~2.5%. This means:
- **BB pullback signals** (`scoring.py` line 364: `bbp < 0.25`) trigger at slightly wrong levels
- **Squeeze detection** (`indicators.py` line 160) triggers slightly **too early** (BB appears narrower)
- Cumulative effect across all BB-based logic

### Fix
```python
# indicators.py — all 4 locations:
sd = math.sqrt(sum((x - mn) ** 2 for x in sl) / (p - 1))
```

---

## ISSUE #3 — MODERATE: Backtest Has No Commission/Fee Modeling

### Buggy Code
`athena.py` backtest_pair, e.g. line 2023 (swing), 2148 (intraday), 2269 (scalp):
```python
equity_change = result_r * CONFIG["RISK_PCT"] * risk_mult * _vol_adj
```

### Reference Patterns
**Backtrader** (`commission-schemes.py` lines 114-119):
```python
cerebro.broker.setcommission(commission=args.comm, mult=args.mult,
                             margin=args.margin, ...)
```
**Vectorbt** (`test_portfolio.py` lines 2073-2097):
```python
from_signals_both(size=1, fees=[[-0.1, 0., 0.1, 1.]]).order_records
```
Both reference frameworks **explicitly model fees/commissions** in every backtest. Vectorbt tests multiple fee levels including negative (rebates), zero, and positive.

### Consequence
Slippage is modeled (good), but **no round-trip commission** is deducted. For crypto at 0.1% taker fee, over 100 trades the cumulative fee drag is significant. Backtest equity curves are **optimistically inflated**.

### Fix
Add fee deduction per trade:
```python
# After equity_change calculation, add:
_FEE_RT = {"forex": 0.00003, "crypto": 0.002, "commodity": 0.0003, "stock": 0.001, "index": 0.0003}
fee_drag = _FEE_RT.get(_ptype, 0.001) * CONFIG["RISK_PCT"] * risk_mult
equity_change -= fee_drag
```
Apply this in all 3 backtest loops (swing line ~2023, intraday line ~2148, scalp line ~2269).

---

## ISSUE #4 — MODERATE: Bybit Equity Equals Balance (Ignores Unrealized P&L)

### Buggy Code
`bybit_executor.py` lines 212-213:
```python
"balance": total,
"equity": total,   # ← same value as balance!
```

### Reference Pattern
Jesse (`Strategy.py` line 77): Jesse tracks `self.position` with full unrealized P&L awareness. The framework passes **equity** (balance + unrealized) to risk decisions, not just balance.

Athena's `risk_engine.py` line 318:
```python
dd = _current_drawdown(account_equity)
```
This expects **equity** (balance + unrealized P&L) but receives **balance** from Bybit.

### Consequence
Drawdown circuit breaker (`DRAWDOWN_STOP_THRESHOLD=15%`, `DRAWDOWN_REDUCE_THRESHOLD=10%`) **cannot detect unrealized losses**. A position could be deep in the red (-10%) but the drawdown check sees 0% drawdown because `equity == balance`. New trades get approved when they shouldn't be.

### Fix
```python
# bybit_executor.py — bybit_get_account():
balance = exchange.fetch_balance(params={"type": "linear"})
usdt = balance.get("USDT", {})
total = usdt.get("total", 0) or 0
free  = usdt.get("free",  0) or 0
# Equity = total + unrealized P&L from open positions
positions = exchange.fetch_positions(params={"category": "linear", "settleCoin": "USDT"})
unrealized = sum(float(p.get("unrealizedPnl", 0) or 0) for p in (positions or []))
return {
    "exchange": "Bybit",
    "testnet": os.environ.get("BYBIT_TESTNET", "false").lower() in ("true", "1", "yes"),
    "balance": total,
    "equity": total + unrealized,   # ← actual equity
    "freeBalance": free,
    "currency": "USDT",
}
```

---

## ISSUE #5 — MODERATE: Bybit Position Risk Estimate Hardcoded at 2%

### Buggy Code
`bybit_executor.py` lines 235-237:
```python
sl_est = entry * 0.98  # fallback 2% SL estimate if no SL set
notional = size * entry
est_risk = round(notional * 0.02, 2)
```

### What's Wrong
The `risk_amount` fed to `risk_engine._calc_portfolio_heat()` is always 2% of notional, regardless of actual SL distance. But actual SL for crypto can be 3-5% (ATR-based), and the SL value is available in `info.get("stopLoss")` on line 247.

### Consequence
Portfolio heat is **underestimated** when actual SL is wider than 2%, allowing cumulative risk to exceed `MAX_PORTFOLIO_HEAT`. For example, with 3 crypto positions each at 4% actual risk, true heat is 12% but calculated heat is only 6%.

### Fix
Use actual SL when available (already fetched on line 247-248):
```python
sl_val = float(info.get("stopLoss", 0) or 0)
tp_val = float(info.get("takeProfit", 0) or 0)
# Accurate risk from actual SL
if sl_val > 0 and entry > 0:
    est_risk = round(abs(entry - sl_val) * size, 2)
else:
    est_risk = round(notional * 0.02, 2)  # fallback only when no SL set
```

---

## ISSUE #6 — LOW: Backtest MAX_OPEN Is Dead Code (Sequential Processing)

### Buggy Code
`athena.py` lines 1914, 1924-1925 (swing backtest):
```python
MAX_OPEN = 3
# ...
if open_positions >= MAX_OPEN:
    i += 1; continue
```

### What's Wrong
Trades are processed **sequentially**: evaluate at bar `i`, run forward loop to find exit, then advance `i` to `exit_bar + 1`. The `open_positions` counter increments on line 2028 and decrements on line 2040 within the same iteration. It is **always 0 or 1** — never reaches 3.

### Consequence
The MAX_OPEN=3 cap never triggers. The backtest behaves as single-position despite appearing to support concurrent positions. This is **not harmful** (conservative), but the code is misleading and the feature doesn't work as intended.

### Fix (if concurrent positions desired)
Would require a fundamentally different approach — maintaining a list of open trades and advancing the bar pointer independently. For now, either:
- Remove the dead code to avoid confusion
- Or document that the backtest is single-position

---

## ISSUE #7 — LOW: Incomplete Bar Risk in Live Scanning

### Relevant Code
`athena.py` `analyze_pair()` lines 3456-3465:
```python
d1 = fetch_candles(pair, "D1", CONFIG["D1_CANDLES"])
h4 = fetch_candles(pair, "H4", CONFIG["H4_CANDLES"])
h1 = fetch_candles(pair, "H1", CONFIG["H1_CANDLES"])
# ... immediately computes indicators on these candles
d1i = calc_indicators(d1)
h4i = calc_indicators(h4)
h1i = calc_indicators(h1)
```

### Reference Pattern
Freqtrade (`freqtrade_sample_strategy.py` line 165):
```python
process_only_new_candles = True
```
This ensures indicators are calculated **only on completed (closed) candles**, never on the currently forming bar.

### Status: UNSURE
Whether this is an active bug depends on what `fetch_candles` returns. Some data sources (EODHD EOD, Binance klines) may include the **currently forming bar** whose close/high/low/volume are not final. If so, all indicator values on the last bar are unreliable and may produce signals that disappear by bar close.

I cannot verify this without testing each data source live. The `fetch_candles_live()` path (candle cache from WebSocket) likely includes an in-progress bar from the `CandleBuilder`.

### Recommended Check
Add a guard to drop the last candle if it's not yet closed:
```python
# In analyze_pair(), after fetching candles:
# Drop potentially incomplete current bar
if h1 and len(h1) > 1:
    h1 = h1[:-1]  # use only closed bars for indicator calculation
```
Or verify that each data source only returns closed bars.

---

## ISSUE #8 — LOW: Stochastic Zero-Fill for Warmup Period

### Buggy Code
`indicators.py` lines 290-296:
```python
mapped = [v if v is not None else 0 for v in rawK]
kL = calc_sma(mapped, ks)
```

### What's Wrong
`None` values (warmup period) are replaced with `0` before SMA smoothing. This pulls the smoothed %K artificially low during the warmup. Standard implementations (TA-Lib STOCH, pandas-ta stoch) output NaN for insufficient periods and don't include them in the smoothing window.

### Consequence
**Minimal in practice** — Athena requires 50+ bars minimum, and stochastic warmup is only ~20 bars (14+3+3). By the time the last bar is reached, the zero-fill effect has washed out. Only affects short series.

### Fix (if desired)
Replace zero-fill with proper None propagation:
```python
valid_rawK = [v for v in rawK if v is not None]
kL_valid = calc_sma(valid_rawK, ks)
# Then re-align to original indices with None padding
```

---

## ISSUE #9 — INFO: analyze_pair Session Uses Wall-Clock, Not Bar Time

### Code
`athena.py` line 3584:
```python
"session": get_session(),  # no bar_time argument
```

### What's Wrong
The `session` field in the signal output always reflects the **current wall-clock UTC time**, not the bar's historical timestamp. The **vote** inside `calc_confluence` correctly receives `bar_time` (line 3508 doesn't pass it, but the confluence function falls back to H4 candle time — see scoring.py line 255-256).

### Consequence
Display-only issue. The session badge shown in the UI reflects "now" rather than when the signal's H4 bar formed. The actual scoring vote is unaffected.

### Fix
```python
"session": get_session(h4[-1].get("time") if h4 else None),
```

---

## VERIFIED "NOT A BUG" — Items Checked and Found Correct

| Area | What Was Checked | Result |
|------|-----------------|--------|
| **RSI calculation** | Wilder smoothing formula in `calc_rsi` | ✅ Correct — matches Wilder's original (exponential smoothing with period weighting) |
| **EMA calculation** | Multiplier `k = 2/(p+1)`, SMA seed | ✅ Correct — standard EMA formula |
| **ATR calculation** | True Range + Wilder smoothing | ✅ Correct — matches TA-Lib ATR |
| **MACD calculation** | EMA(12) - EMA(26), signal EMA(9) | ✅ Correct — standard MACD |
| **ADX calculation** | Wilder +DI/-DI + smoothed DX | ✅ Correct structure (minor index offset possible but unverifiable without test data) |
| **Position sizing** | ATR-based SL → risk budget → lots | ✅ Correct — follows standard risk-per-trade formula |
| **Risk engine checks** | Kill switch, daily loss, drawdown, max positions, correlation, signal freshness | ✅ Comprehensive — 8 sequential gates |
| **SL/TP drift rebasing** | Both MT5 and Bybit rebase when drift > 1% | ✅ Correct pattern |
| **Backtest look-ahead** | Entry at bar[i+1].open, SL checked before TP | ✅ Correct — no look-ahead in trade execution |
| **Backtest slippage** | Percentage-based, session-variable for forex | ✅ Reasonable values (1pip forex, 20bps crypto) |
| **Walk-forward split** | 70/30 IS/OOS with SQN comparison | ✅ Correct implementation |
| **Emergency close on SL/TP failure** | Bybit executor rolls back if protective orders fail | ✅ Robust error handling |
| **Bybit SL/TP via trading-stop** | Uses v5 `/position/trading-stop` instead of order types | ✅ Correct for Bybit v5 API |
| **Signal freshness check** | Rejects signals older than SIGNAL_MAX_AGE_SEC | ✅ Correct |
| **Correlation guard** | Cluster-based position limiting | ✅ Correct |

---

## Summary Table

| # | Severity | File | Issue | Impact |
|---|----------|------|-------|--------|
| 1 | **CRITICAL** | `athena.py:2075` | Intraday backtest passes H4 as D1 to confluence | Backtest results don't match live; optimistic bias |
| 2 | **MODERATE** | `indicators.py:130,157,170,357` | BB uses population std (÷N) not sample std (÷N-1) | BB bands ~2.5% too narrow; squeeze/pullback signals offset |
| 3 | **MODERATE** | `athena.py:2023,2148,2269` | Backtest has zero commission/fee modeling | Backtest equity curves overstated |
| 4 | **MODERATE** | `bybit_executor.py:212-213` | Bybit equity = balance (ignores unrealized P&L) | Drawdown circuit breaker blind to open losses |
| 5 | **MODERATE** | `bybit_executor.py:235-237` | Position risk hardcoded at 2% of notional | Portfolio heat underestimated; risk caps bypassed |
| 6 | **LOW** | `athena.py:1914,1924` | Backtest MAX_OPEN is dead code | No harm, but concurrent position cap doesn't work |
| 7 | **LOW/UNSURE** | `athena.py:3456-3465` | Possibly including incomplete (forming) bars | False signals from partial candles (needs verification) |
| 8 | **LOW** | `indicators.py:290` | Stochastic zero-fills warmup Nones | Negligible with 50+ bar minimum |
| 9 | **INFO** | `athena.py:3584` | Session display uses wall-clock not bar time | Display-only; vote scoring unaffected |
| 10 | **CRITICAL** | `athena.py:5035` | SQLite initialized without WAL mode | Concurrent access from auto-trader thread throws `database is locked` |

---

## Fixed Version — Issue #1 (Critical)

```python
# athena.py — intraday backtest loop, around line 2075
# BEFORE (buggy):
res = calc_confluence(h4i, h1i, h1i, vr, stoch, pair, "neutral",
                       d1_candles=d1_ctx, h4_candles=h4_window, h1_candles=h1_window,
                       volume_threshold=get_pair_profile(pair).get(
                           "volume_threshold",
                           CONFIG.get("VOLUME_THRESHOLD_BACKTEST", CONFIG["VOLUME_THRESHOLD"])
                       ),
                       bar_time=h4_window[-1].get("time") if h4_window else None)

# AFTER (fixed):
res = calc_confluence(d1i_ctx, h4i, h1i, vr, stoch, pair, "neutral",
                       d1_candles=d1_ctx, h4_candles=h4_window, h1_candles=h1_window,
                       volume_threshold=get_pair_profile(pair).get(
                           "volume_threshold",
                           CONFIG.get("VOLUME_THRESHOLD_BACKTEST", CONFIG["VOLUME_THRESHOLD"])
                       ),
                       bar_time=h4_window[-1].get("time") if h4_window else None)
```

## Fixed Version — Issue #2 (BB Std Dev)

```python
# indicators.py — calc_bb, line 130
# BEFORE:
sd = math.sqrt(sum((x - mn) ** 2 for x in sl) / p)
# AFTER:
sd = math.sqrt(sum((x - mn) ** 2 for x in sl) / (p - 1))

# Same fix at lines 157, 170 (calc_squeeze) and 357 (calc_bb_width_percentile)
```

## Fixed Version — Issue #4 (Bybit Equity)

```python
# bybit_executor.py — bybit_get_account()
def bybit_get_account() -> dict | None:
    """Get Bybit futures wallet balance (USDT) with actual equity."""
    exchange = _get_exchange()
    if not exchange:
        return None
    try:
        balance = exchange.fetch_balance(params={"type": "linear"})
        usdt = balance.get("USDT", {})
        total = usdt.get("total", 0) or 0
        free  = usdt.get("free",  0) or 0
        # Calculate actual equity including unrealized P&L
        positions = exchange.fetch_positions(params={"category": "linear", "settleCoin": "USDT"})
        unrealized = sum(float(p.get("unrealizedPnl", 0) or 0) for p in (positions or []))
        return {
            "exchange": "Bybit",
            "testnet": os.environ.get("BYBIT_TESTNET", "false").lower() in ("true", "1", "yes"),
            "balance": total,
            "equity": total + unrealized,
            "freeBalance": free,
            "currency": "USDT",
        }
    except Exception as e:
        log.error(f"[BYBIT] Failed to fetch balance: {e}")
        return None
```

## Fixed Version — Issue #5 (Bybit Risk Estimate)

```python
# bybit_executor.py — inside bybit_get_positions(), replace lines 235-237:
# BEFORE:
sl_est = entry * 0.98
notional = size * entry
est_risk = round(notional * 0.02, 2)

# AFTER:
notional = size * entry
sl_val = float(info.get("stopLoss", 0) or 0)
if sl_val > 0 and entry > 0:
    est_risk = round(abs(entry - sl_val) * size, 2)
else:
    est_risk = round(notional * 0.02, 2)  # fallback when no SL set
```

---

## ISSUE #10 — CRITICAL: SQLite Concurrency Flaw (`database is locked`)

### Buggy Code
`athena.py` line 5035 (`_init_audit_db`):
```python
def _init_audit_db(db_path: str) -> None:
    """Create audit table if it doesn't exist..."""
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE IF NOT EXISTS audit_log...")
```
`ai_learning.py` line 67 (`init_learning_db`):
```python
def init_learning_db(db_path: str) -> None:
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE IF NOT EXISTS learning_log...")
```

### What's Wrong
The SQLite databases are initialized without enabling `PRAGMA journal_mode=WAL`. The default SQLite journal mode locks the entire database file during a write, preventing any other thread from reading.

### Consequence
The `auto_trader.py` runs on a dedicated background thread and attempts to `INSERT` trades into `audit.db`. Meanwhile, the live scanner runs `run_full_scan` utilizing a `ThreadPoolExecutor` with 6 worker threads, all of which call `risk_engine.py -> _adaptive_risk_pct()`. This function opens a read connection to `audit.db` to calculate Kelly fractions.
If any thread attempts to read while another thread is writing (or vice versa), the reader/writer will fail instantly with a fatal `sqlite3.OperationalError: database is locked` because no connection `timeout` was provided. This will cause scan failures or prevent auto-trades from being recorded.

### Fix
Enable WAL (Write-Ahead Logging) on initialization and add a timeout for concurrent connections:
```python
# athena.py
def _init_audit_db(db_path: str) -> None:
    con = sqlite3.connect(db_path, timeout=15.0)
    con.execute("PRAGMA journal_mode=WAL")
    # ...

# ai_learning.py
def init_learning_db(db_path: str) -> None:
    con = sqlite3.connect(db_path, timeout=15.0)
    con.execute("PRAGMA journal_mode=WAL")
    # ...

# risk_engine.py
def _adaptive_risk_pct(asset_type: str, regime: str = "") -> float:
    # ...
    con = sqlite3.connect(db_path, timeout=15.0)
    # ...
```
