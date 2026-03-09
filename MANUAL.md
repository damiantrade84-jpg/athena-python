# Athena Pro v3.1 — Operational Manual

> Last updated: 2026-03-09 | Version 3.1 | Pepperstone Demo + Bybit Demo

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Dashboard Overview](#2-dashboard-overview)
3. [Running a Scan & Reading Signals](#3-running-a-scan--reading-signals)
4. [AI Analysis](#4-ai-analysis)
5. [Manual Trade Execution](#5-manual-trade-execution)
6. [Monitoring Trades on MetaTrader 5](#6-monitoring-trades-on-metatrader-5)
7. [Monitoring Trades on Bybit](#7-monitoring-trades-on-bybit)
8. [TEST Mode](#8-test-mode)
9. [AUTO-TRADE Bot](#9-auto-trade-bot)
10. [Risk Engine Reference](#10-risk-engine-reference)
11. [What Works on Pepperstone](#11-what-works-on-pepperstone)
12. [Troubleshooting](#12-troubleshooting)
13. [Configuration Reference](#13-configuration-reference)

---

## 1. Quick Start

### Prerequisites

Before starting Athena, ensure the following are running:

| Requirement | Status check |
|-------------|-------------|
| MetaTrader 5 terminal | Open and logged into Pepperstone Demo |
| Internet connection | Required for EODHD data + Bybit API |
| Python environment | `venv` or `.venv` in project folder |

### Starting the App

**Option A — Double-click:**
```
start_athena.bat
```

**Option B — Command line:**
```bash
cd C:\Users\damia\OneDrive\Desktop\athena-python
py athena.py
```

**Single scan then exit (CLI mode):**
```bash
py athena.py --scan
```

### Dashboard
Once started, open your browser and go to:
```
http://localhost:5000
```

### Startup Log — What to Expect
```
[INFO] ATHENA PRO v3.1 - Python Edition
[INFO] Pairs: 28 active / 70 total
[INFO] [WS] WebSocket manager started
[INFO] [MT5] Connected — Account: XXXXXXXX | Balance: $XX,XXX
[INFO] [BYBIT] Bybit Linear Futures DEMO connected
[INFO] http://localhost:5000
```

If you see `[WARNING] Running in degraded mode` — missing API keys (CRYPTOPANIC, FINNHUB). These are optional; the tool still works.

---

## 2. Dashboard Overview

### Tabs

| Tab | Purpose |
|-----|---------|
| **SIGNALS** | Main screen — trade signals, watchlist, open positions |
| **BACKTEST** | Historical strategy testing for any pair |
| **SCREENER** | Discover new momentum stocks via EODHD |
| **PERFORMANCE** | P&L metrics, auto-trade log, AI learning stats |

### Status Badges (Top Right)

| Badge | Meaning |
|-------|---------|
| `MT5: ON` (green) | MetaTrader 5 connected and account loaded |
| `MT5: OFF` (red) | MT5 terminal not running or not logged in |
| `Bybit: ON` (green) | Bybit API connected (demo or live) |
| `Bybit: OFF` (red) | Bybit API keys missing or invalid |
| Price tickers | Live prices from EODHD WebSocket |

### Toggles

| Toggle | What It Does |
|--------|-------------|
| **TEST** | Lowers score thresholds by 3 pts; enables Force Execute on all signals |
| **AUTO** | Enables auto-trading bot (scans every 30 min, auto-executes qualifying signals) |

### Open Positions Panel
Appears at the top of SIGNALS when you have open trades. Updates every ~10 seconds. Shows pair, direction, entry, live P&L, SL, and TP for each position.

---

## 3. Running a Scan & Reading Signals

### How to Scan
Click the **SCAN** button on the SIGNALS tab. A full scan takes ~30 seconds and covers all active pairs (forex, crypto, commodities, indices, JSE stocks, US stocks).

### Signal Tiers

| Tier | Meaning |
|------|---------|
| **TRADE** | Score ≥ threshold — high-confidence signal, execute button shown |
| **WATCHLIST** | Score below threshold but interesting — monitor only |
| **SKIP** | Score too low or blocked — shown in diagnostics only |

### Reading a Signal Card

| Field | Meaning |
|-------|---------|
| **Pair** | Asset being traded (e.g. EUR/USD, BTC/USDT) |
| **Direction** | LONG (buy) or SHORT (sell) |
| **Score** | Confluence score out of max (e.g. 8.5/10) — higher = stronger signal |
| **Regime** | Market state: TRENDING, RANGING, DEVELOPING, BREAKOUT |
| **Entry** | Suggested entry price |
| **SL** | Stop-loss level — where you accept being wrong |
| **TP1** | First take-profit target (primary) |
| **TP2** | Second take-profit target (extended) |
| **ATR** | Average True Range — current volatility measure |
| **ADX** | Trend strength (>25 = trending, <20 = ranging) |
| **Session** | Active trading session at scan time |

### Scan Funnel (bottom of scan results)
```
Total: 70 pairs scanned
Active: 28 enabled
Passed: 4 → TRADE signals
Watchlist: 8 → monitoring
Low score: 15 → filtered out
Closed exchange: 2 → US market closed
Counter-trend: 1 → blocked (against trend)
```

---

## 4. AI Analysis

### How to Trigger
Click **Analyze** on any signal card. Claude AI will evaluate the signal and return a grade within ~5 seconds.

### Grades

| Grade | Meaning | Action |
|-------|---------|--------|
| **A+** | Exceptional setup, full conviction | Execute at full size |
| **A** | Strong setup, minor concerns | Execute at normal size |
| **B** | Decent setup, some risk | Execute at half size or skip |
| **C** | Weak setup, significant concerns | Skip or quarter size max |
| **F** | Do not trade | Do not execute |

### What AI Evaluates
- D1 trend alignment and Weinstein stage
- H4 MACD and oscillator confirmation
- Volume signature relative to recent average
- Session timing and liquidity
- Funding rate (crypto) or dividend risk (stocks)
- Recent learning data from past trades on this pair

### Position Sizing Recommendations
AI may suggest: `Full`, `Normal (0.75x)`, `Half`, or `Quarter` — this is applied automatically when you click Execute after analysis.

---

## 5. Manual Trade Execution

### Step-by-Step

1. **Run scan** — click SCAN button
2. **Find signal** — look for TRADE tier cards (green border)
3. **Analyze** — click Analyze to get AI grade (optional but recommended)
4. **Review** — check grade, narrative, and suggested position size
5. **Execute** — click the **EXECUTE LONG** or **EXECUTE SHORT** button

### What Happens After You Click Execute

1. Signal freshness checked (must be < 5 minutes old)
2. MT5 or Bybit connection verified
3. Risk engine runs all checks (see Section 10)
4. If approved: order sent to broker
5. If rejected: error message shown with rejection reason

### Confirmation Modal Fields

| Field | Meaning |
|-------|---------|
| Entry price | Live price at moment of execution |
| Stop Loss | Price where trade closes at a loss |
| Take Profit | Price where trade closes at a profit |
| Volume | Lot size calculated from your risk settings |
| Risk Amount | Dollar amount at risk on this trade |
| Risk % | Percentage of account balance at risk |
| Portfolio Heat | Total risk after this trade opens |

### After Execution
- Trade appears immediately in the **Open Positions panel** on the dashboard
- Trade also visible in **MT5 Trade tab** or **Bybit Positions tab**
- Audit log entry written to database

---

## 6. Monitoring Trades on MetaTrader 5

### Where to Find Open Trades

In MT5 terminal, click the **Trade** tab (bottom panel). This shows all currently open positions.

**Athena trades are identifiable by:**
- **Magic number:** `240601`
- **Comment field:** `Athena|{pair}|Score:{score}` e.g. `Athena|EUR/USD|Score:8.5`

### Trade Tab vs Orders Tab

| Tab | What You See |
|-----|-------------|
| **Trade** | Open positions — your active trades with live P&L |
| **Orders** | Pending orders — waiting to trigger at a future price |

Athena sends **market orders** which fill immediately. They go straight to the **Trade tab** as open positions. You will **not** see them in the Orders tab (unless Athena placed a TP2 pending limit).

### Reading a Position Row

| Column | Meaning |
|--------|---------|
| Symbol | Instrument (e.g. EURUSD) |
| Type | Buy or Sell |
| Volume | Lot size |
| Price (open) | Entry price |
| S/L | Stop-loss level |
| T/P | Take-profit level |
| Price (current) | Live price |
| Profit | Unrealized P&L in account currency |

### SL and TP on MT5 Positions
SL and TP are attached **directly to the position** — they are not separate orders. When price hits the TP or SL, MT5 closes the trade automatically. You will see the SL and T/P columns populated on the position row.

### Closing a Trade Manually
Right-click the position → **Close Position** → confirm. This overrides SL/TP and closes immediately at market price.

### Modifying SL/TP Manually
Right-click the position → **Modify or Delete Order** → change S/L or T/P values → **Modify**.

---

## 7. Monitoring Trades on Bybit

### Where to Find Open Trades

On Bybit (web or app):
- Go to **Derivatives** → **USDT Perpetual**
- Click the **Positions** tab

### Positions Tab vs Orders Tab

| Tab | What You See |
|-----|-------------|
| **Positions** | Open positions — your active trades (Athena trades appear here) |
| **Orders** | Pending/unfilled orders — Athena does NOT use this for SL/TP |

Athena sends market orders to Bybit, which fill instantly and show as **Positions**, not Orders.

### Where SL and TP Appear on Bybit
SL and TP are set as **position-level stops** (not as separate orders). On the Positions tab, you will see:
- An **SL** price label on the position row
- A **TP** price label on the position row

These are the exact levels Athena calculated. When price hits them, Bybit closes the position automatically.

### Reading a Bybit Position

| Field | Meaning |
|-------|---------|
| Contract | e.g. BTCUSDT |
| Side | Long or Short |
| Size | Quantity in base asset (e.g. 0.5 BTC) |
| Entry Price | Average fill price |
| Mark Price | Current fair value price |
| Liq. Price | Liquidation price (avoid letting trade reach here) |
| Unrealized PnL | Current profit/loss in USDT |
| SL | Stop-loss price |
| TP | Take-profit price |
| Funding | Next funding rate and time |

### Funding Rate
Every 8 hours, a funding payment occurs between longs and shorts. If funding is positive, longs pay shorts. Monitor this on long-duration crypto holds — large negative funding can eat into profit.

### Demo Mode Indicator
If you configured `BYBIT_DEMO=true`, the Bybit interface shows a **Demo** badge next to the account balance. All trades are paper trades — no real money moves.

### Closing a Bybit Trade Manually
On the Positions tab, click **Close** next to the position → choose **Market Close** → confirm. This closes at current mark price.

### Modifying SL/TP on Bybit
Click the pencil icon next to the SL or TP value on the position row → enter new price → confirm.

---

## 8. TEST Mode

### What It Does
- **Lowers score thresholds by 3 points** — signals that normally wouldn't appear as TRADE tier now show
- **Enables Force Execute** on watchlist signals — allows executing lower-confidence signals for testing
- **Removes duplicate execute guard** — same signal can be executed multiple times
- **Tags audit entries** as `AUTO-DEMO` for distinction from live trades

### When to Use
- Testing MT5 execution on demo account to verify orders go through
- Testing Bybit execution flow end-to-end
- Checking that SL/TP are correctly attached to positions
- Verifying new pairs/symbols work before enabling live

### How to Activate
Click the **TEST** toggle button on the dashboard. The button turns yellow and a banner appears:
```
TEST MODE ACTIVE — Score thresholds lowered, force-execute enabled on all signals
```

### Force Execute Button
In TEST mode, watchlist signals (normally not executable) show an orange **FORCE EXECUTE** button. Clicking this sends the trade through the full risk engine (all checks still apply) but bypasses the score threshold gate.

### How to Deactivate
Click **TEST** toggle again. Button returns to normal, banner disappears, thresholds restore.

> **Important:** Always turn off TEST mode after testing. It should never be left on during normal trading sessions.

---

## 9. AUTO-TRADE Bot

### What It Does
Runs a background scheduler that automatically scans the market every 30 minutes and executes the highest-scoring qualifying signal without any manual input.

### Enabling / Disabling
Click the **AUTO** toggle on the dashboard. When enabled:
- A banner shows the next scan time, trades today, and last execution
- The bot fires an immediate first scan within ~30 seconds

### How It Selects Trades

1. Scans all active pairs every 30 minutes
2. Filters signals by minimum score (default: 7.0)
3. Checks session window (e.g. forex only during London/NY hours)
4. Checks daily trade cap (default: max 3 per day)
5. Selects highest-scoring signal
6. Runs full risk engine checks
7. Executes via MT5 or Bybit

### Session Windows (UTC)

| Session | Hours (UTC) | Asset Classes |
|---------|-------------|---------------|
| London | 07:00–16:00 | Forex |
| New York | 13:00–21:00 | Forex |
| London/NY Overlap | 13:00–16:00 | Forex (peak) |
| JSE | 07:00–15:00 | JSE Stocks |
| US Regular | 14:30–21:00 | US Stocks |
| Always | 24/7 | Crypto, Commodities, Indices |

### AUTO-TRADE LOG
In the **Performance** tab → AUTO-TRADE LOG section. Shows last 30 auto-trade attempts:

| Column | Meaning |
|--------|---------|
| Time | UTC timestamp of attempt |
| Pair | Asset attempted |
| Direction | LONG or SHORT |
| Score | Confluence score |
| Result | Success or rejection |
| Error Tag | Reason for failure (if any) |

### Error Tags

| Tag | Meaning |
|-----|---------|
| `SYMBOL_NOT_ON_BROKER` | Symbol exists in Athena but not on your MT5 broker |
| `MT5_NOT_CONNECTED` | MT5 terminal was not running when scan fired |
| `BYBIT_NOT_CONNECTED` | Bybit API not connected |
| `RISK:CORRELATED_CLUSTER_FULL` | Already 2 positions in same corr. cluster |
| `RISK:STALE_SIGNAL` | Signal aged out before execution (>5 min) |
| `RISK:MAX_POSITIONS_REACHED` | Already at max open positions (5) |
| `RISK:DRAWDOWN_CIRCUIT_BREAKER` | Account drawdown ≥ 15% |
| `RISK:PORTFOLIO_HEAT_EXCEEDED` | Total risk would exceed 6% |
| `EXEC:MARKET_CLOSED` | Broker shows 0 price — market not open |
| `EXEC:INVALID_SL` | SL/TP validation failed (wrong side of entry) |
| `EXCEPTION:...` | Unexpected error — check logs for detail |

---

## 10. Risk Engine Reference

Every trade — manual or auto — passes through the risk engine before execution. The checks run in this order:

| # | Check | Rejection Code | Condition |
|---|-------|----------------|-----------|
| 1 | Kill Switch | `KILL_SWITCH_ACTIVE` | Kill switch is ON |
| 2 | Daily Loss | `DAILY_LOSS_LIMIT` | Day's losses ≥ 5% of account |
| 3 | Signal Age | `STALE_SIGNAL` | Signal is older than 5 minutes |
| 4 | Timestamp | `UNPARSEABLE_TIMESTAMP` | Signal timestamp is malformed |
| 5 | Drawdown | `DRAWDOWN_CIRCUIT_BREAKER` | Equity drawdown ≥ 15% from peak |
| 6 | Max Positions | `MAX_POSITIONS_REACHED` | 5 or more trades already open |
| 7 | Correlation | `CORRELATED_CLUSTER_FULL` | 2+ positions in same cluster |
| 8 | Volume Calc | `ZERO_VOLUME` | Calculated lot size rounds to 0 |
| 9 | Risk Per Trade | `RISK_TOO_HIGH` | Trade would risk > 3% of account |
| 10 | Portfolio Heat | `PORTFOLIO_HEAT_EXCEEDED` | Total risk would exceed 6% |

### Rejection Reason Explanations

**ZERO_VOLUME**
Calculated position size was 0 after all scaling. Usually means:
- SL distance is very wide relative to account size
- Score scaling factor reduced size below minimum
- Symbol info from broker has unusual contract specs

**CORRELATED_CLUSTER_FULL**
You already have 2 positions in the same correlation cluster. Clusters include:
- `forex_usd`: EUR/USD, GBP/USD, AUD/USD, NZD/USD, USD/CHF, USD/CAD, USD/ZAR etc.
- `defi`: SOL, AVAX, LINK, BNB, ETH, INJ, NEAR
- `metals`: XAU/USD, XAG/USD, GLD
- `jse`: all JSE stocks
- `us_tech`: AAPL, TSLA, NVDA, MSFT, AMZN, META, GOOG
- `ai_crypto`: FET, RENDER

**STALE_SIGNAL**
The signal was generated more than 5 minutes ago. Re-run the scan to get a fresh signal.

**DRAWDOWN_CIRCUIT_BREAKER**
Account equity has fallen 15% or more from its highest recorded value. All trading is halted. The account needs to recover before new trades are allowed.

**PORTFOLIO_HEAT_EXCEEDED**
The total risk across all open positions plus this new trade would exceed 6% of account balance. Close some existing positions first.

**RISK_TOO_HIGH**
This single trade would risk more than 3% of account balance. The SL distance is too wide for your account size and current 1% risk setting.

### Drawdown Behaviour

| Drawdown | Effect |
|----------|--------|
| < 10% | Normal trading, full position sizes |
| 10–15% | **Position sizes halved** automatically |
| ≥ 15% | **All trading stopped** (circuit breaker) |

---

## 11. What Works on Pepperstone

### Supported ✓

| Asset Class | Examples | Notes |
|-------------|---------|-------|
| Forex | EUR/USD, GBP/USD, USD/JPY, AUD/USD etc. | Full support, live prices |
| Commodities | XAU/USD (gold), XAG/USD (silver), WTI Oil | Full support |
| Indices | FTSE 100 (UK100), S&P 500 (US500), Dow Jones (US30) | Full support |
| US Stocks CFD | AAPL, TSLA, NVDA, MSFT, AMZN, META, GOOG, JPM etc. | When US market is open |
| Crypto | All pairs via Bybit (not MT5) | Bybit handles crypto |

### Needs Setup ⚠️

| Asset | Issue | Fix |
|-------|-------|-----|
| Nasdaq (USTEC) | Symbol may not be in Market Watch | MT5 → View → Market Watch → right-click → Symbols → search USTEC → Add |
| WTI Oil | May appear as USOUSD or WTIUSD | Check Market Watch for exact name |

### Not Available on Pepperstone ✗

| Asset | Reason | Alternative |
|-------|--------|------------|
| JSE Stocks (Prosus, Naspers, etc.) | Pepperstone doesn't carry JSE equities | GT247, Khwezi Trade (both MT5-compatible) |

---

## 12. Troubleshooting

### MT5 Not Connected
**Symptom:** `MT5: OFF` badge, execute returns "MT5 not connected"

**Fixes:**
1. Open MetaTrader 5 terminal
2. Log in to your Pepperstone Demo account
3. Wait for the terminal to fully load (green bar at bottom right)
4. Restart Athena — it will auto-connect on startup

**Check logs for:**
```
[MT5] Connected to terminal
[MT5] Account: XXXXXXXX | Balance: $XX,XXX
```

---

### Bybit Not Connected
**Symptom:** `Bybit: OFF` badge, crypto execute returns "Bybit not connected"

**Fixes:**
1. Check `.env` file has `BYBIT_API_KEY` and `BYBIT_API_SECRET` set
2. Verify API key has **trading permissions** on Bybit
3. If using demo: ensure `BYBIT_DEMO=true` in `.env`
4. Restart Athena

**Check logs for:**
```
[BYBIT] Bybit Linear Futures DEMO connected
```

---

### ZERO_VOLUME Rejection
**Symptom:** `REJECTED by risk engine: ZERO_VOLUME`

**Causes & Fixes:**

| Cause | Fix |
|-------|-----|
| SL distance too wide for account size | Tighten the SL or increase account balance |
| Score scaling reduced size to 0 | Signal score too low — wait for better signal |
| Symbol not on broker → symbol_info=None | See "Symbol not on broker" below |
| MT5 returned zero tick value (market closed) | Wait for market to open, try again |

---

### Symbol Not on Broker
**Symptom:** `Symbol 'PRX' not available on your MT5 broker`

**Cause:** The symbol exists in Athena's watchlist but your broker (Pepperstone) doesn't carry it.

**Fix:** For JSE stocks, use a JSE-capable broker. For indices like USTEC:
1. Open MT5
2. Go to View → Market Watch
3. Right-click → Symbols
4. Search for the symbol (try `USTEC`, `NAS100`, `USTEC.cash`)
5. Click Add
6. The symbol will now appear in Market Watch and Athena can use it

---

### Stale Signal
**Symptom:** `REJECTED: signal is 312s old (max 300s)`

**Cause:** More than 5 minutes passed between the scan and clicking Execute.

**Fix:** Re-run the scan to get a fresh signal, then execute immediately.

---

### Dashboard Not Updating (Laptop Sleep)
**Symptom:** Prices frozen, positions not refreshing, auto-trader not scanning

**Cause:** When your laptop sleeps, Windows suspends the Python process and all network connections drop.

**Fixes:**
- **Prevent sleep:** Windows Settings → Power & Sleep → set Sleep to "Never" while trading
- **After waking:** Refresh the browser (F5). If Athena process died, run `start_athena.bat` again
- **Permanent fix:** Run Athena on a VPS or cloud server that stays on 24/7

---

### Scan Lock (Concurrent Scan Error)
**Symptom:** `Scan already in progress — please wait`

**Cause:** A previous scan is still running (scans take ~30 seconds).

**Fix:** Wait 30–60 seconds and try again. If it persists, restart Athena.

---

### Correlated Cluster Full
**Symptom:** `REJECTED: 2 positions in 'forex_usd' cluster (max 2)`

**Cause:** You already have the maximum allowed positions in that correlation group.

**Fix:** Either:
- Wait for one position to close
- Close one position manually
- Increase `MAX_CORRELATED_POSITIONS` in `config.yaml` (not recommended — adds correlation risk)

---

### Force Execute Not Showing
**Symptom:** Watchlist signals show no execute button

**Cause:** TEST mode is OFF. Force Execute only appears in TEST mode.

**Fix:** Click the **TEST** toggle to enable test mode, then re-scan.

---

### Trade Went to Position (Not Orders Tab) — This Is Normal
Athena sends **market orders** which fill instantly. Filled trades appear in the **Positions/Trade tab**, not the Orders tab. This is correct behavior.

---

## 13. Configuration Reference

Edit `config.yaml` in the project folder to override any default setting. Changes take effect on restart.

### Risk Settings

```yaml
RISK_PCT: 0.01                    # Base risk per trade (1% of account)
MAX_PORTFOLIO_HEAT: 0.06          # Max total open risk (6%)
MAX_OPEN_POSITIONS: 5             # Max concurrent trades
MAX_CORRELATED_POSITIONS: 2       # Max per correlation cluster
MAX_RISK_PER_TRADE: 0.03          # Hard cap per trade (3%)
DRAWDOWN_REDUCE_THRESHOLD: 0.10   # Halve sizes at 10% drawdown
DRAWDOWN_STOP_THRESHOLD: 0.15     # Stop all trading at 15% drawdown
SIGNAL_MAX_AGE_SEC: 300           # Reject signals older than 5 minutes
```

### Execution Settings

```yaml
EXECUTION_ENABLED: true           # Master execution switch
AUTO_EXECUTE: false               # Auto-execute after AI grade (manual only when false)
AUTO_EXECUTE_MIN_SCORE: 8.0       # Minimum score for auto-execute
AUTO_EXECUTE_MIN_GRADE: "B"       # Minimum AI grade for auto-execute
```

### AUTO-TRADE Bot Settings

```yaml
AUTO_TRADE_ENABLED: false         # Start bot on launch (false = manual toggle only)
AUTO_TRADE_MIN_SCORE: 7.0         # Minimum confluence score to auto-execute
AUTO_TRADE_MAX_DAILY: 3           # Max auto-trades per calendar day (UTC)
AUTO_TRADE_MAX_PER_SCAN: 1        # Max executions per single scan
AUTO_TRADE_SIZING_OVERRIDE: 1.0   # Sizing multiplier (1.0 = full live size)
AUTO_TRADE_SCAN_INTERVAL_MIN: 30  # Minutes between automatic scans
AUTO_TRADE_SESSIONS:
  forex:     ["london", "new_york", "london_ny_overlap"]
  crypto:    ["always"]
  stock:     ["jse", "us_regular"]
  commodity: ["always"]
  index:     ["always"]
```

### Per-Asset-Class Thresholds

```yaml
MIN_CONFLUENCE_CLASS:
  crypto: 5.0
  forex: 5.0
  commodity: 5.0
  stock: 5.5
  index: 5.0

ADX_TREND_MIN_CLASS:
  crypto: 20
  forex: 22
  commodity: 25
  stock: 25
  index: 25
```

### AI Settings

```yaml
ANTHROPIC_MODEL: "claude-sonnet-4-6"   # AI model for analysis
LEARNING_ENABLED: true                  # Extract learning from closed trades
LEARNING_MIN_TRADES: 5                  # Min trades before learning injected
LEARNING_LOOKBACK_DAYS: 90             # Days of history for learning context
META_ANALYSIS_ENABLED: true            # Weekly meta-analysis via Claude
```

---

## Quick Reference Card

### Normal Trading Session
```
1. Open MT5 → Log in to demo
2. Run start_athena.bat
3. Open http://localhost:5000
4. Click SCAN (wait ~30s)
5. Click signal card → Analyze
6. Review AI grade
7. Click EXECUTE
8. Monitor in Positions panel
```

### Demo/Testing Session
```
1. Same as above steps 1-4
2. Enable TEST mode (yellow toggle)
3. Force Execute on any signal
4. Verify trade in MT5 Trade tab or Bybit Positions
5. Disable TEST mode when done
```

### Auto-Trading Session
```
1. Ensure MT5 + Bybit connected
2. Click AUTO toggle
3. First scan fires in ~30 seconds
4. Bot scans every 30 minutes
5. Check Performance → AUTO-TRADE LOG for results
6. Click AUTO again to stop
```

### Emergency Stop
```
API: POST http://localhost:5000/api/killswitch {"action":"on"}
Effect: Blocks ALL new scans and executions immediately
Open positions: NOT affected — close manually in MT5/Bybit
```
